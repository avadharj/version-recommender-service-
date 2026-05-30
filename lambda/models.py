from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FrameworkInfo:
    name: str
    version: str


@dataclass
class EnvSnapshot:
    python_version: str
    framework: FrameworkInfo
    os: str
    cuda_version: str | None = None


@dataclass
class DepSnapshot:
    """Mirrors the AMS DepSnapshot shape; only the fields needed for scoring."""
    python_version: str
    framework: FrameworkInfo
    os: str
    cuda_version: str | None = None


@dataclass
class Candidate:
    version: str
    dep_snapshot: DepSnapshot
    created_at: str


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float
    breakdown: dict[str, float]
    disqualified: bool = False
    disqualification_reason: str | None = None


@dataclass
class Recommendation:
    recommended_version: str
    score: float
    explanation: str
    candidates_evaluated: int
    cached: bool = False
