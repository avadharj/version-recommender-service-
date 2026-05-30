from __future__ import annotations

import pytest
from exceptions import (
    AMSUnavailableError,
    CacheError,
    ModelNotFoundException,
    NoCompatibleVersionError,
    RecommenderError,
    ValidationError,
)


class TestExceptionHierarchy:
    def test_model_not_found_is_recommender_error(self) -> None:
        assert isinstance(ModelNotFoundException(), RecommenderError)

    def test_no_compatible_version_is_recommender_error(self) -> None:
        assert isinstance(NoCompatibleVersionError(reason="model_empty"), RecommenderError)

    def test_ams_unavailable_is_recommender_error(self) -> None:
        assert isinstance(AMSUnavailableError(), RecommenderError)

    def test_validation_error_is_recommender_error(self) -> None:
        assert isinstance(ValidationError(), RecommenderError)

    def test_cache_error_is_recommender_error(self) -> None:
        assert isinstance(CacheError(), RecommenderError)

    def test_all_exceptions_catchable_as_base(self) -> None:
        for exc in [
            ModelNotFoundException(),
            NoCompatibleVersionError(reason="model_empty"),
            AMSUnavailableError(),
            ValidationError(),
            CacheError(),
        ]:
            with pytest.raises(RecommenderError):
                raise exc


class TestNoCompatibleVersionError:
    def test_carries_reason_attribute(self) -> None:
        exc = NoCompatibleVersionError(reason="all_disqualified")
        assert exc.reason == "all_disqualified"

    def test_reason_model_empty(self) -> None:
        exc = NoCompatibleVersionError(reason="model_empty")
        assert exc.reason == "model_empty"

    def test_reason_no_ready_versions(self) -> None:
        exc = NoCompatibleVersionError(reason="no_ready_versions")
        assert exc.reason == "no_ready_versions"


class TestCacheError:
    def test_is_subclass_of_recommender_error(self) -> None:
        assert issubclass(CacheError, RecommenderError)
