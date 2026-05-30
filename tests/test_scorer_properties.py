from __future__ import annotations

import pytest
from exceptions import NoCompatibleVersionError
from hypothesis import given, settings
from hypothesis import strategies as st
from models import Candidate, DepSnapshot, EnvSnapshot, FrameworkInfo
from scorer import ScoreEngine

engine = ScoreEngine()

# ── Strategies ────────────────────────────────────────────────────────────────

_FRAMEWORK_NAMES = st.sampled_from(["pytorch", "tensorflow", "sklearn", "huggingface"])

_valid_version = st.from_regex(r"\d+\.\d+(\.\d+)?", fullmatch=True)

_malformed_version = st.text(min_size=1).filter(
    lambda s: "." not in s or not all(p.isdigit() for p in s.split(".")[:2])
)


def _framework_st(
    name_st: st.SearchStrategy[str] = _FRAMEWORK_NAMES,
) -> st.SearchStrategy[FrameworkInfo]:
    return st.builds(FrameworkInfo, name=name_st, version=_valid_version)


def _env_snapshot_st() -> st.SearchStrategy[EnvSnapshot]:
    return st.builds(
        EnvSnapshot,
        python_version=_valid_version,
        framework=_framework_st(),
        cuda_version=st.one_of(st.none(), _valid_version),
        os=st.sampled_from(["linux-x86_64", "linux-aarch64", "darwin-arm64", "windows-amd64"]),
    )


def _candidate_st(
    fw_name_st: st.SearchStrategy[str] = _FRAMEWORK_NAMES,
) -> st.SearchStrategy[Candidate]:
    dep = st.builds(
        DepSnapshot,
        python_version=_valid_version,
        framework=_framework_st(fw_name_st),
        cuda_version=st.one_of(st.none(), _valid_version),
        os=st.sampled_from(["linux-x86_64", "linux-aarch64", "darwin-arm64", "windows-amd64"]),
    )
    return st.builds(
        Candidate,
        version=st.from_regex(r"\d+\.\d+", fullmatch=True),
        dep_snapshot=dep,
        created_at=st.datetimes(timezones=st.just(__import__("datetime").timezone.utc)).map(
            lambda dt: dt.isoformat().replace("+00:00", "Z")
        ),
    )


# ── No panics on malformed version strings ────────────────────────────────────

class TestMalformedVersionHandling:
    @given(bad=_malformed_version)
    def test_parse_major_minor_raises_value_error_not_attribute_error(self, bad: str) -> None:
        """_parse_major_minor must raise ValueError (never AttributeError or TypeError)."""
        with pytest.raises(ValueError):
            ScoreEngine._parse_major_minor(bad)

    @given(bad=_malformed_version)
    def test_score_python_raises_value_error_on_bad_env(self, bad: str) -> None:
        with pytest.raises(ValueError):
            ScoreEngine._score_python(bad, "3.11.0")

    @given(bad=_malformed_version)
    def test_score_python_raises_value_error_on_bad_dep(self, bad: str) -> None:
        with pytest.raises(ValueError):
            ScoreEngine._score_python("3.11.0", bad)

    @given(bad=_malformed_version)
    def test_score_framework_raises_value_error_on_bad_env_version(self, bad: str) -> None:
        env_fw = FrameworkInfo(name="pytorch", version=bad)
        dep_fw = FrameworkInfo(name="pytorch", version="2.1.0")
        with pytest.raises(ValueError):
            ScoreEngine._score_framework(env_fw, dep_fw)

    @given(bad=_malformed_version)
    def test_score_framework_raises_value_error_on_bad_dep_version(self, bad: str) -> None:
        env_fw = FrameworkInfo(name="pytorch", version="2.1.0")
        dep_fw = FrameworkInfo(name="pytorch", version=bad)
        with pytest.raises(ValueError):
            ScoreEngine._score_framework(env_fw, dep_fw)


# ── Score bounds ──────────────────────────────────────────────────────────────

class TestScoreBounds:
    @given(env=_env_snapshot_st(), candidate=_candidate_st())
    @settings(max_examples=10_000)
    def test_total_score_in_0_to_100(self, env: EnvSnapshot, candidate: Candidate) -> None:
        results = engine.score_all(env, [candidate])
        assert len(results) == 1
        sc = results[0]
        if not sc.disqualified:
            assert 0.0 <= sc.score <= 100.0

    @given(env=_env_snapshot_st(), candidate=_candidate_st())
    @settings(max_examples=10_000)
    def test_dimension_scores_within_max(self, env: EnvSnapshot, candidate: Candidate) -> None:
        results = engine.score_all(env, [candidate])
        sc = results[0]
        if not sc.disqualified:
            assert sc.breakdown["python"] in {0.0, 21.0, 35.0}
            assert sc.breakdown["framework"] in {7.0, 24.5, 35.0}
            assert sc.breakdown["cuda"] in {8.0, 10.0, 18.0, 20.0}
            assert sc.breakdown["os"] in {5.0, 10.0}

    @given(env=_env_snapshot_st(), candidate=_candidate_st())
    @settings(max_examples=10_000)
    def test_disqualified_score_is_zero(self, env: EnvSnapshot, candidate: Candidate) -> None:
        results = engine.score_all(env, [candidate])
        sc = results[0]
        if sc.disqualified:
            assert sc.score == 0.0


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    @given(env=_env_snapshot_st(), candidates=st.lists(_candidate_st(), min_size=1, max_size=5))
    @settings(max_examples=1_000)
    def test_score_all_is_deterministic(
        self, env: EnvSnapshot, candidates: list[Candidate]
    ) -> None:
        result1 = engine.score_all(env, candidates)
        result2 = engine.score_all(env, candidates)
        assert len(result1) == len(result2)
        for a, b in zip(result1, result2):
            assert a.score == b.score
            assert a.disqualified == b.disqualified
            assert a.breakdown == b.breakdown

    @given(
        env=_env_snapshot_st(),
        candidates=st.lists(
            _candidate_st(fw_name_st=st.just("pytorch")),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=1_000)
    def test_recommend_is_deterministic(
        self, env: EnvSnapshot, candidates: list[Candidate]
    ) -> None:
        # Force same framework to ensure at least one eligible candidate most of the time
        same_fw_env = EnvSnapshot(
            python_version=env.python_version,
            framework=FrameworkInfo(name="pytorch", version=env.framework.version),
            cuda_version=env.cuda_version,
            os=env.os,
        )
        try:
            rec1 = engine.recommend(same_fw_env, candidates)
            rec2 = engine.recommend(same_fw_env, candidates)
            assert rec1.recommended_version == rec2.recommended_version
            assert rec1.score == rec2.score
            assert rec1.explanation == rec2.explanation
        except NoCompatibleVersionError:
            pass  # valid outcome — all disqualified or empty


# ── Tiebreaker transitivity ───────────────────────────────────────────────────

class TestTiebreakerTransitivity:
    @given(
        ts_a=st.datetimes(timezones=st.just(__import__("datetime").timezone.utc)),
        ts_b=st.datetimes(timezones=st.just(__import__("datetime").timezone.utc)),
        ts_c=st.datetimes(timezones=st.just(__import__("datetime").timezone.utc)),
    )
    def test_tiebreaker_is_transitive(self, ts_a, ts_b, ts_c) -> None:  # type: ignore[no-untyped-def]
        """If A > B and B > C by created_at, then A > C."""
        import datetime

        def _iso(dt: datetime.datetime) -> str:
            return dt.isoformat().replace("+00:00", "Z")

        def _cand(ts: datetime.datetime, version: str) -> Candidate:
            return Candidate(
                version=version,
                dep_snapshot=DepSnapshot(
                    python_version="3.11.0",
                    framework=FrameworkInfo(name="pytorch", version="2.1.0"),
                    os="linux-x86_64",
                ),
                created_at=_iso(ts),
            )

        env = EnvSnapshot(
            python_version="3.11.0",
            framework=FrameworkInfo(name="pytorch", version="2.1.0"),
            os="linux-x86_64",
        )
        cand_a = _cand(ts_a, "a")
        cand_b = _cand(ts_b, "b")
        cand_c = _cand(ts_c, "c")

        # Determine all versions with the max timestamp (may be multiple on ties)
        winner_ts = max(ts_a, ts_b, ts_c)
        ts_list = [(ts_a, "a"), (ts_b, "b"), (ts_c, "c")]
        possible_winners = {v for ts, v in ts_list if ts == winner_ts}

        # Full set — winner must be one of the candidates with the latest timestamp
        rec_abc = engine.recommend(env, [cand_a, cand_b, cand_c])
        assert rec_abc.recommended_version in possible_winners

        # Pairwise: A vs B
        rec_ab = engine.recommend(env, [cand_a, cand_b])
        ab_possible = {v for ts, v in [(ts_a, "a"), (ts_b, "b")] if ts == max(ts_a, ts_b)}
        assert rec_ab.recommended_version in ab_possible

        # Pairwise: B vs C
        rec_bc = engine.recommend(env, [cand_b, cand_c])
        bc_possible = {v for ts, v in [(ts_b, "b"), (ts_c, "c")] if ts == max(ts_b, ts_c)}
        assert rec_bc.recommended_version in bc_possible
