from __future__ import annotations

import dataclasses

from models import (
    Candidate,
    DepSnapshot,
    EnvSnapshot,
    FrameworkInfo,
    Recommendation,
    ScoredCandidate,
)


def _framework() -> FrameworkInfo:
    return FrameworkInfo(name="pytorch", version="2.1.0")


def _dep_snapshot(cuda: str | None = None) -> DepSnapshot:
    return DepSnapshot(
        python_version="3.11.7",
        framework=_framework(),
        os="linux-x86_64",
        cuda_version=cuda,
    )


def _candidate() -> Candidate:
    return Candidate(
        version="3.2",
        dep_snapshot=_dep_snapshot(),
        created_at="2026-01-01T00:00:00Z",
    )


# ── FrameworkInfo ──────────────────────────────────────────────────────────────

class TestFrameworkInfo:
    def test_fields_accessible(self) -> None:
        fw = _framework()
        assert fw.name == "pytorch"
        assert fw.version == "2.1.0"

    def test_round_trip_asdict(self) -> None:
        fw = _framework()
        d = dataclasses.asdict(fw)
        restored = FrameworkInfo(**d)
        assert restored == fw


# ── EnvSnapshot ───────────────────────────────────────────────────────────────

class TestEnvSnapshot:
    def test_fields_accessible(self) -> None:
        env = EnvSnapshot(
            python_version="3.11.7",
            framework=_framework(),
            os="linux-x86_64",
        )
        assert env.python_version == "3.11.7"
        assert env.cuda_version is None

    def test_cuda_version_defaults_to_none(self) -> None:
        env = EnvSnapshot(
            python_version="3.11.7",
            framework=_framework(),
            os="linux-x86_64",
        )
        assert env.cuda_version is None

    def test_cuda_version_set(self) -> None:
        env = EnvSnapshot(
            python_version="3.11.7",
            framework=_framework(),
            os="linux-x86_64",
            cuda_version="11.8",
        )
        assert env.cuda_version == "11.8"

    def test_round_trip_asdict(self) -> None:
        env = EnvSnapshot(
            python_version="3.11.7",
            framework=_framework(),
            os="linux-x86_64",
            cuda_version="11.8",
        )
        d = dataclasses.asdict(env)
        restored = EnvSnapshot(
            python_version=d["python_version"],
            framework=FrameworkInfo(**d["framework"]),
            os=d["os"],
            cuda_version=d["cuda_version"],
        )
        assert restored == env


# ── DepSnapshot ───────────────────────────────────────────────────────────────

class TestDepSnapshot:
    def test_cuda_version_none_safe(self) -> None:
        dep = _dep_snapshot(cuda=None)
        assert dep.cuda_version is None

    def test_cuda_version_set(self) -> None:
        dep = _dep_snapshot(cuda="12.1")
        assert dep.cuda_version == "12.1"

    def test_round_trip_asdict(self) -> None:
        dep = _dep_snapshot(cuda="11.8")
        d = dataclasses.asdict(dep)
        restored = DepSnapshot(
            python_version=d["python_version"],
            framework=FrameworkInfo(**d["framework"]),
            os=d["os"],
            cuda_version=d["cuda_version"],
        )
        assert restored == dep

    def test_env_and_dep_are_distinct_types(self) -> None:
        dep = _dep_snapshot()
        env = EnvSnapshot(
            python_version=dep.python_version,
            framework=dep.framework,
            os=dep.os,
        )
        assert type(dep) is not type(env)  # type: ignore[comparison-overlap]


# ── Candidate ─────────────────────────────────────────────────────────────────

class TestCandidate:
    def test_fields_accessible(self) -> None:
        c = _candidate()
        assert c.version == "3.2"
        assert c.created_at == "2026-01-01T00:00:00Z"

    def test_dep_snapshot_cuda_version_none_safe(self) -> None:
        c = Candidate(
            version="1.0",
            dep_snapshot=_dep_snapshot(cuda=None),
            created_at="2026-01-01T00:00:00Z",
        )
        assert c.dep_snapshot.cuda_version is None

    def test_round_trip_asdict(self) -> None:
        c = _candidate()
        d = dataclasses.asdict(c)
        restored = Candidate(
            version=d["version"],
            dep_snapshot=DepSnapshot(
                python_version=d["dep_snapshot"]["python_version"],
                framework=FrameworkInfo(**d["dep_snapshot"]["framework"]),
                os=d["dep_snapshot"]["os"],
                cuda_version=d["dep_snapshot"]["cuda_version"],
            ),
            created_at=d["created_at"],
        )
        assert restored == c


# ── ScoredCandidate ───────────────────────────────────────────────────────────

class TestScoredCandidate:
    def test_defaults(self) -> None:
        sc = ScoredCandidate(
            candidate=_candidate(),
            score=89.5,
            breakdown={"python": 35.0, "framework": 24.5, "cuda": 20.0, "os": 10.0},
        )
        assert sc.disqualified is False
        assert sc.disqualification_reason is None

    def test_disqualified_flag(self) -> None:
        sc = ScoredCandidate(
            candidate=_candidate(),
            score=0.0,
            breakdown={},
            disqualified=True,
            disqualification_reason="framework_mismatch",
        )
        assert sc.disqualified is True
        assert sc.disqualification_reason == "framework_mismatch"

    def test_round_trip_asdict(self) -> None:
        sc = ScoredCandidate(
            candidate=_candidate(),
            score=100.0,
            breakdown={"python": 35.0, "framework": 35.0, "cuda": 20.0, "os": 10.0},
        )
        d = dataclasses.asdict(sc)
        assert d["score"] == 100.0
        assert d["breakdown"]["python"] == 35.0
        assert d["disqualified"] is False


# ── Recommendation ────────────────────────────────────────────────────────────

class TestRecommendation:
    def test_defaults(self) -> None:
        r = Recommendation(
            recommended_version="3.2",
            score=100.0,
            explanation="exact match",
            candidates_evaluated=4,
        )
        assert r.cached is False

    def test_cached_flag(self) -> None:
        r = Recommendation(
            recommended_version="3.2",
            score=100.0,
            explanation="exact match",
            candidates_evaluated=4,
            cached=True,
        )
        assert r.cached is True

    def test_round_trip_asdict(self) -> None:
        r = Recommendation(
            recommended_version="3.2",
            score=89.5,
            explanation="close match",
            candidates_evaluated=3,
            cached=False,
        )
        d = dataclasses.asdict(r)
        restored = Recommendation(**d)
        assert restored == r
