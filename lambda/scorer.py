from __future__ import annotations

import re
from datetime import datetime

from exceptions import NoCompatibleVersionError
from models import (
    Candidate,
    EnvSnapshot,
    FrameworkInfo,
    Recommendation,
    ScoredCandidate,
)


class ScoreEngine:

    def score_all(
        self,
        env: EnvSnapshot,
        candidates: list[Candidate],
    ) -> list[ScoredCandidate]:
        """Score all candidates. Disqualified entries included with disqualified=True."""
        results: list[ScoredCandidate] = []
        for candidate in candidates:
            dep = candidate.dep_snapshot
            fw_score = self._score_framework(env.framework, dep.framework)
            if fw_score is None:
                results.append(
                    ScoredCandidate(
                        candidate=candidate,
                        score=0.0,
                        breakdown={},
                        disqualified=True,
                        disqualification_reason="framework_mismatch",
                    )
                )
                continue

            python_score = self._score_python(env.python_version, dep.python_version)
            cuda_score = self._score_cuda(env.cuda_version, dep.cuda_version)
            os_score = self._score_os(env.os, dep.os)
            total = python_score + fw_score + cuda_score + os_score

            results.append(
                ScoredCandidate(
                    candidate=candidate,
                    score=total,
                    breakdown={
                        "python": python_score,
                        "framework": fw_score,
                        "cuda": cuda_score,
                        "os": os_score,
                    },
                )
            )
        return results

    def recommend(
        self,
        env: EnvSnapshot,
        candidates: list[Candidate],
    ) -> Recommendation:
        """Return the top non-disqualified candidate. Raises NoCompatibleVersionError if none."""
        if not candidates:
            raise NoCompatibleVersionError(reason="model_empty")

        scored = self.score_all(env, candidates)
        eligible = [s for s in scored if not s.disqualified]

        if not eligible:
            raise NoCompatibleVersionError(reason="all_disqualified")

        winner = max(eligible, key=lambda s: (round(s.score, 2), self._created_at_dt(s.candidate)))

        return Recommendation(
            recommended_version=winner.candidate.version,
            score=winner.score,
            explanation=self._explanation(env, winner),
            candidates_evaluated=len(eligible),
        )

    # ── Dimension scorers ─────────────────────────────────────────────────────

    @staticmethod
    def _score_python(env_version: str, dep_version: str) -> float:
        """Returns 35.0, 21.0, or 0.0."""
        env_maj, env_min = ScoreEngine._parse_major_minor(env_version)
        dep_maj, dep_min = ScoreEngine._parse_major_minor(dep_version)
        if env_maj != dep_maj:
            return 0.0
        if env_min != dep_min:
            return 21.0
        return 35.0

    @staticmethod
    def _score_framework(env_fw: FrameworkInfo, dep_fw: FrameworkInfo) -> float | None:
        """Returns float or None (None = disqualified — different framework name)."""
        if env_fw.name != dep_fw.name:
            return None
        env_maj, env_min = ScoreEngine._parse_major_minor(env_fw.version)
        dep_maj, dep_min = ScoreEngine._parse_major_minor(dep_fw.version)
        if env_maj != dep_maj:
            return 7.0
        if env_min != dep_min:
            return 24.5
        return 35.0

    @staticmethod
    def _score_cuda(env_cuda: str | None, dep_cuda: str | None) -> float:
        """Returns 20.0, 18.0, 10.0, or 8.0."""
        if env_cuda is None and dep_cuda is None:
            return 20.0
        if env_cuda is None or dep_cuda is None:
            return 8.0
        env_major = int(env_cuda.split(".")[0])
        dep_major = int(dep_cuda.split(".")[0])
        if env_major == dep_major:
            return 18.0
        return 10.0

    @staticmethod
    def _score_os(env_os: str, dep_os: str) -> float:
        """Returns 10.0 or 5.0."""
        if ScoreEngine._extract_os_family(env_os) == ScoreEngine._extract_os_family(dep_os):
            return 10.0
        return 5.0

    @staticmethod
    def _extract_os_family(os_string: str) -> str:
        """'linux-x86_64' → 'linux', 'darwin-arm64' → 'darwin'."""
        return os_string.split("-")[0]

    @staticmethod
    def _parse_major_minor(version_string: str) -> tuple[int, int]:
        """'3.11.7' → (3, 11). '2.1' → (2, 1). Raises ValueError on bad input."""
        parts = re.split(r"[.\-]", version_string)
        if len(parts) < 2:
            raise ValueError(f"Cannot parse major.minor from: {version_string!r}")
        try:
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            raise ValueError(f"Non-numeric version segments in: {version_string!r}")

    @staticmethod
    def _explanation(env: EnvSnapshot, winner: ScoredCandidate) -> str:
        """Build a deterministic human-readable explanation string."""
        dep = winner.candidate.dep_snapshot
        parts: list[str] = []

        env_maj, env_min = ScoreEngine._parse_major_minor(env.python_version)
        dep_maj, dep_min = ScoreEngine._parse_major_minor(dep.python_version)
        if env_maj == dep_maj and env_min == dep_min:
            parts.append(f"exact Python {env_maj}.{env_min}")
        else:
            parts.append(f"Python {dep_maj}.{dep_min} (closest match)")

        fw_env_maj, fw_env_min = ScoreEngine._parse_major_minor(env.framework.version)
        fw_dep_maj, fw_dep_min = ScoreEngine._parse_major_minor(dep.framework.version)
        if fw_env_maj == fw_dep_maj and fw_env_min == fw_dep_min:
            parts.append(f"exact {env.framework.name.capitalize()} {fw_env_maj}.{fw_env_min}")
        else:
            parts.append(
                f"{dep.framework.name.capitalize()} {fw_dep_maj}.{fw_dep_min} (closest match)"
            )

        if env.cuda_version is None and dep.cuda_version is None:
            parts.append("CPU environment")
        elif env.cuda_version is not None and dep.cuda_version is not None:
            parts.append(f"CUDA {dep.cuda_version}")
        else:
            parts.append("mixed CPU/GPU")

        env_family = ScoreEngine._extract_os_family(env.os)
        dep_family = ScoreEngine._extract_os_family(dep.os)
        if env_family == dep_family:
            parts.append(f"same OS ({dep_family})")
        else:
            parts.append(f"different OS ({dep_family})")

        return f"v{winner.candidate.version} is the best match: {', '.join(parts)}"

    @staticmethod
    def _created_at_dt(candidate: Candidate) -> datetime:
        ts = candidate.created_at.rstrip("Z") + "+00:00"
        return datetime.fromisoformat(ts)
