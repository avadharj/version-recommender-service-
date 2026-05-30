from __future__ import annotations

import pytest
from exceptions import NoCompatibleVersionError
from models import Candidate, DepSnapshot, EnvSnapshot, FrameworkInfo
from scorer import ScoreEngine

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fw(name: str = "pytorch", version: str = "2.1.0") -> FrameworkInfo:
    return FrameworkInfo(name=name, version=version)


def _dep(
    python: str = "3.11.7",
    fw_name: str = "pytorch",
    fw_ver: str = "2.1.0",
    cuda: str | None = None,
    os: str = "linux-x86_64",
    created_at: str = "2026-01-01T00:00:00Z",
    version: str = "1.0",
) -> Candidate:
    return Candidate(
        version=version,
        dep_snapshot=DepSnapshot(
            python_version=python,
            framework=FrameworkInfo(name=fw_name, version=fw_ver),
            os=os,
            cuda_version=cuda,
        ),
        created_at=created_at,
    )


def _env(
    python: str = "3.11.7",
    fw_name: str = "pytorch",
    fw_ver: str = "2.1.0",
    cuda: str | None = None,
    os: str = "linux-x86_64",
) -> EnvSnapshot:
    return EnvSnapshot(
        python_version=python,
        framework=FrameworkInfo(name=fw_name, version=fw_ver),
        cuda_version=cuda,
        os=os,
    )


engine = ScoreEngine()


# ── _parse_major_minor ────────────────────────────────────────────────────────

class TestParseMajorMinor:
    def test_three_part_version(self) -> None:
        assert ScoreEngine._parse_major_minor("3.11.7") == (3, 11)

    def test_two_part_version(self) -> None:
        assert ScoreEngine._parse_major_minor("2.1") == (2, 1)

    def test_single_digit_parts(self) -> None:
        assert ScoreEngine._parse_major_minor("3.0.0") == (3, 0)

    def test_raises_on_single_segment(self) -> None:
        with pytest.raises(ValueError):
            ScoreEngine._parse_major_minor("3")

    def test_raises_on_non_numeric(self) -> None:
        with pytest.raises(ValueError):
            ScoreEngine._parse_major_minor("abc.def")

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ValueError):
            ScoreEngine._parse_major_minor("")


# ── _score_python ─────────────────────────────────────────────────────────────

class TestScorePython:
    def test_same_major_minor_returns_35(self) -> None:
        assert ScoreEngine._score_python("3.11.7", "3.11.2") == 35.0

    def test_same_major_different_minor_returns_21(self) -> None:
        assert ScoreEngine._score_python("3.11.7", "3.10.4") == 21.0

    def test_different_major_returns_0(self) -> None:
        assert ScoreEngine._score_python("3.11.7", "2.7.18") == 0.0

    def test_exact_two_part_match(self) -> None:
        assert ScoreEngine._score_python("3.11", "3.11") == 35.0

    def test_minor_zero_match(self) -> None:
        assert ScoreEngine._score_python("3.0.0", "3.0.1") == 35.0


# ── _score_framework ──────────────────────────────────────────────────────────

class TestScoreFramework:
    def test_exact_major_minor_returns_35(self) -> None:
        result = ScoreEngine._score_framework(_fw("pytorch", "2.1.0"), _fw("pytorch", "2.1.2"))
        assert result == 35.0

    def test_same_major_different_minor_returns_24_5(self) -> None:
        result = ScoreEngine._score_framework(_fw("pytorch", "2.1.0"), _fw("pytorch", "2.2.0"))
        assert result == 24.5

    def test_different_major_returns_7(self) -> None:
        result = ScoreEngine._score_framework(_fw("pytorch", "2.1.0"), _fw("pytorch", "3.0.0"))
        assert result == 7.0

    def test_different_framework_name_returns_none(self) -> None:
        result = ScoreEngine._score_framework(_fw("pytorch", "2.1.0"), _fw("tensorflow", "2.13.0"))
        assert result is None

    def test_sklearn_same_version(self) -> None:
        result = ScoreEngine._score_framework(_fw("sklearn", "1.3.0"), _fw("sklearn", "1.3.2"))
        assert result == 35.0


# ── _score_cuda ───────────────────────────────────────────────────────────────

class TestScoreCuda:
    def test_both_none_returns_20(self) -> None:
        assert ScoreEngine._score_cuda(None, None) == 20.0

    def test_same_cuda_major_returns_18(self) -> None:
        assert ScoreEngine._score_cuda("11.8", "11.6") == 18.0

    def test_different_cuda_major_returns_10(self) -> None:
        assert ScoreEngine._score_cuda("11.8", "12.1") == 10.0

    def test_env_cpu_dep_gpu_returns_8(self) -> None:
        assert ScoreEngine._score_cuda(None, "11.8") == 8.0

    def test_env_gpu_dep_cpu_returns_8(self) -> None:
        assert ScoreEngine._score_cuda("11.8", None) == 8.0

    def test_major_only_cuda_string(self) -> None:
        assert ScoreEngine._score_cuda("11", "11") == 18.0

    def test_major_only_vs_full_same_major(self) -> None:
        assert ScoreEngine._score_cuda("12", "12.4") == 18.0


# ── _score_os ─────────────────────────────────────────────────────────────────

class TestScoreOs:
    def test_same_family_different_arch_returns_10(self) -> None:
        assert ScoreEngine._score_os("linux-x86_64", "linux-aarch64") == 10.0

    def test_different_family_returns_5(self) -> None:
        assert ScoreEngine._score_os("linux-x86_64", "darwin-arm64") == 5.0

    def test_windows_same_family(self) -> None:
        assert ScoreEngine._score_os("windows-amd64", "windows-x86_64") == 10.0

    def test_linux_vs_windows(self) -> None:
        assert ScoreEngine._score_os("linux-x86_64", "windows-amd64") == 5.0


# ── _extract_os_family ────────────────────────────────────────────────────────

class TestExtractOsFamily:
    def test_linux(self) -> None:
        assert ScoreEngine._extract_os_family("linux-x86_64") == "linux"

    def test_darwin(self) -> None:
        assert ScoreEngine._extract_os_family("darwin-arm64") == "darwin"

    def test_windows(self) -> None:
        assert ScoreEngine._extract_os_family("windows-amd64") == "windows"


# ── score_all ─────────────────────────────────────────────────────────────────

class TestScoreAll:
    def test_perfect_match_scores_100(self) -> None:
        env = _env()
        candidates = [_dep()]
        results = engine.score_all(env, candidates)
        assert len(results) == 1
        assert results[0].score == 100.0
        assert not results[0].disqualified

    def test_framework_mismatch_is_disqualified(self) -> None:
        env = _env(fw_name="pytorch")
        candidates = [_dep(fw_name="tensorflow")]
        results = engine.score_all(env, candidates)
        assert results[0].disqualified is True
        assert results[0].disqualification_reason == "framework_mismatch"

    def test_disqualified_entries_included_in_results(self) -> None:
        env = _env(fw_name="pytorch")
        candidates = [_dep(fw_name="tensorflow"), _dep(fw_name="pytorch")]
        results = engine.score_all(env, candidates)
        assert len(results) == 2
        disqualified = [r for r in results if r.disqualified]
        eligible = [r for r in results if not r.disqualified]
        assert len(disqualified) == 1
        assert len(eligible) == 1

    def test_empty_candidates_returns_empty(self) -> None:
        results = engine.score_all(_env(), [])
        assert results == []

    def test_breakdown_keys_present(self) -> None:
        results = engine.score_all(_env(), [_dep()])
        assert set(results[0].breakdown.keys()) == {"python", "framework", "cuda", "os"}

    def test_example_from_spec(self) -> None:
        # env: Python 3.11, PyTorch 2.1, CPU, linux-x86_64
        # v3.2: Python 3.11 (+35), PyTorch 2.1 (+35), CPU (+20), linux (+10) = 100.0
        # v2.1: Python 3.11 (+35), PyTorch 2.2 (+24.5), CPU (+20), linux (+10) = 89.5
        # v1.8: Python 3.10 (+21), PyTorch 2.1 (+35), CPU (+20), linux (+10) = 86.0
        # v4.0: Python 3.11 (+35), PyTorch 3.0 (+7), CPU (+20), darwin (+5) = 67.0
        env = _env(python="3.11.0", fw_ver="2.1.0")
        candidates = [
            _dep(version="3.2", python="3.11.0", fw_ver="2.1.0", os="linux-x86_64"),
            _dep(version="2.1", python="3.11.0", fw_ver="2.2.0", os="linux-x86_64"),
            _dep(version="1.8", python="3.10.0", fw_ver="2.1.0", os="linux-x86_64"),
            _dep(version="4.0", python="3.11.0", fw_ver="3.0.0", os="darwin-arm64"),
        ]
        results = engine.score_all(env, candidates)
        by_version = {r.candidate.version: r.score for r in results}
        assert by_version["3.2"] == 100.0
        assert by_version["2.1"] == 89.5
        assert by_version["1.8"] == 86.0
        assert by_version["4.0"] == 67.0


# ── recommend ─────────────────────────────────────────────────────────────────

class TestRecommend:
    def test_empty_list_raises_model_empty(self) -> None:
        with pytest.raises(NoCompatibleVersionError) as exc_info:
            engine.recommend(_env(), [])
        assert exc_info.value.reason == "model_empty"

    def test_all_disqualified_raises_all_disqualified(self) -> None:
        candidates = [_dep(fw_name="tensorflow"), _dep(fw_name="tensorflow", version="2.0")]
        with pytest.raises(NoCompatibleVersionError) as exc_info:
            engine.recommend(_env(fw_name="pytorch"), candidates)
        assert exc_info.value.reason == "all_disqualified"

    def test_returns_highest_scoring_candidate(self) -> None:
        env = _env()
        candidates = [
            _dep(version="1.0", python="3.10.0"),   # 21 + 35 + 20 + 10 = 86
            _dep(version="2.0", python="3.11.0"),   # 35 + 35 + 20 + 10 = 100
        ]
        rec = engine.recommend(env, candidates)
        assert rec.recommended_version == "2.0"
        assert rec.score == 100.0

    def test_tiebreaker_newer_created_at_wins(self) -> None:
        env = _env()
        candidates = [
            _dep(version="old", python="3.11.0", created_at="2026-01-01T00:00:00Z"),
            _dep(version="new", python="3.11.0", created_at="2026-06-01T00:00:00Z"),
        ]
        rec = engine.recommend(env, candidates)
        assert rec.recommended_version == "new"

    def test_tiebreaker_older_loses(self) -> None:
        env = _env()
        candidates = [
            _dep(version="newer", python="3.11.0", created_at="2026-06-01T00:00:00Z"),
            _dep(version="older", python="3.11.0", created_at="2025-01-01T00:00:00Z"),
        ]
        rec = engine.recommend(env, candidates)
        assert rec.recommended_version == "newer"

    def test_disqualified_excluded_from_recommendation(self) -> None:
        env = _env(fw_name="pytorch")
        candidates = [
            _dep(version="bad", fw_name="tensorflow"),
            _dep(version="good", fw_name="pytorch"),
        ]
        rec = engine.recommend(env, candidates)
        assert rec.recommended_version == "good"

    def test_candidates_evaluated_counts_only_eligible(self) -> None:
        env = _env(fw_name="pytorch")
        candidates = [
            _dep(version="bad", fw_name="tensorflow"),
            _dep(version="good1", fw_name="pytorch"),
            _dep(version="good2", fw_name="pytorch", python="3.10.0"),
        ]
        rec = engine.recommend(env, candidates)
        assert rec.candidates_evaluated == 2

    def test_explanation_is_deterministic(self) -> None:
        env = _env()
        candidates = [_dep()]
        rec1 = engine.recommend(env, candidates)
        rec2 = engine.recommend(env, candidates)
        assert rec1.explanation == rec2.explanation

    def test_explanation_exact_match_format(self) -> None:
        env = _env(python="3.11.0", fw_name="pytorch", fw_ver="2.1.0")
        candidates = [_dep(python="3.11.0", fw_ver="2.1.0", version="3.2")]
        rec = engine.recommend(env, candidates)
        assert rec.explanation == (
            "v3.2 is the best match: exact Python 3.11, exact Pytorch 2.1, "
            "CPU environment, same OS (linux)"
        )

    def test_cached_defaults_false(self) -> None:
        rec = engine.recommend(_env(), [_dep()])
        assert rec.cached is False

    def test_explanation_python_closest_match(self) -> None:
        env = _env(python="3.11.0")
        candidates = [_dep(python="3.10.0", version="1.0")]
        rec = engine.recommend(env, candidates)
        assert "Python 3.10 (closest match)" in rec.explanation

    def test_explanation_framework_closest_match(self) -> None:
        env = _env(fw_ver="2.1.0")
        candidates = [_dep(fw_ver="2.2.0", version="1.0")]
        rec = engine.recommend(env, candidates)
        assert "Pytorch 2.2 (closest match)" in rec.explanation

    def test_explanation_cuda_present(self) -> None:
        env = _env(cuda="11.8")
        candidates = [_dep(cuda="11.6", version="1.0")]
        rec = engine.recommend(env, candidates)
        assert "CUDA 11.6" in rec.explanation

    def test_explanation_mixed_cpu_gpu(self) -> None:
        env = _env(cuda=None)
        candidates = [_dep(cuda="11.8", version="1.0")]
        rec = engine.recommend(env, candidates)
        assert "mixed CPU/GPU" in rec.explanation

    def test_explanation_different_os(self) -> None:
        env = _env(os="linux-x86_64")
        candidates = [_dep(os="darwin-arm64", version="1.0")]
        rec = engine.recommend(env, candidates)
        assert "different OS (darwin)" in rec.explanation
