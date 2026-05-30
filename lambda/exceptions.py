from __future__ import annotations


class RecommenderError(Exception):
    """Base exception for all recommender service errors."""


class ModelNotFoundException(RecommenderError):
    """AMS returned 404 — model does not exist."""


class NoCompatibleVersionError(RecommenderError):
    """All candidates were disqualified or no candidates exist."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AMSUnavailableError(RecommenderError):
    """AMS returned 5xx or timed out."""


class ValidationError(RecommenderError):
    """Bad EnvSnapshot supplied by the SDK caller."""


class CacheError(RecommenderError):
    """Non-fatal cache failure — caught and ignored by the handler."""
