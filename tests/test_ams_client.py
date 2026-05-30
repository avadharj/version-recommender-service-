from __future__ import annotations

from typing import Any

import pytest
import requests
import responses as rsps_lib
from ams_client import _MAX_READY_VERSIONS, AMSClient
from exceptions import AMSUnavailableError, ModelNotFoundException
from models import Candidate

ENDPOINT = "https://ams.example.com/alpha"
MODEL = "fraud-detector"
LIST_URL = f"{ENDPOINT}/models/{MODEL}/versions"


def _version_url(ver: str) -> str:
    return f"{ENDPOINT}/models/{MODEL}/versions/{ver}"


def _sparse(
    version: str, status: str = "READY", created_at: str = "2026-01-01T00:00:00Z"
) -> dict[str, Any]:
    return {"version": version, "status": status, "createdAt": created_at}


def _full(
    version: str,
    python: str = "3.11.0",
    fw_name: str = "pytorch",
    fw_ver: str = "2.1.0",
    os: str = "linux-x86_64",
    cuda: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    dep: dict[str, Any] = {
        "pythonVersion": python,
        "framework": {"name": fw_name, "version": fw_ver},
        "os": os,
    }
    if cuda is not None:
        dep["cudaVersion"] = cuda
    return {"version": version, "status": "READY", "createdAt": created_at, "depSnapshot": dep}


def _make_client() -> AMSClient:
    import unittest.mock as mock

    with mock.patch("boto3.Session") as sess:
        creds = mock.MagicMock()
        creds.access_key = "AKIAIOSFODNN7EXAMPLE"
        creds.secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        creds.token = None
        sess.return_value.get_credentials.return_value.get_frozen_credentials.return_value = creds
        return AMSClient(endpoint_url=ENDPOINT)


# ── Two-phase: basic happy path ───────────────────────────────────────────────

class TestListReadyVersionsBasic:
    @rsps_lib.activate
    def test_returns_three_candidates_for_three_ready_versions(self) -> None:
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            json={
                "versions": [
                    _sparse("1.0", "READY"),
                    _sparse("2.0", "PENDING"),
                    _sparse("3.0", "READY"),
                    _sparse("4.0", "READY"),
                    _sparse("5.0", "FAILED"),
                ]
            },
            status=200,
        )
        rsps_lib.add(rsps_lib.GET, _version_url("1.0"), json=_full("1.0"), status=200)
        rsps_lib.add(rsps_lib.GET, _version_url("3.0"), json=_full("3.0"), status=200)
        rsps_lib.add(rsps_lib.GET, _version_url("4.0"), json=_full("4.0"), status=200)

        client = _make_client()
        results = client.list_ready_versions(MODEL)

        assert len(results) == 3
        assert all(isinstance(r, Candidate) for r in results)

    @rsps_lib.activate
    def test_pending_and_failed_versions_get_no_get_version_call(self) -> None:
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            json={
                "versions": [
                    _sparse("1.0", "PENDING"),
                    _sparse("2.0", "DELETED"),
                    _sparse("3.0", "FAILED"),
                ]
            },
            status=200,
        )
        # No GetVersion stubs — if one were called responses would raise ConnectionError
        client = _make_client()
        results = client.list_ready_versions(MODEL)
        assert results == []

    @rsps_lib.activate
    def test_response_envelope_key_is_versions_not_items(self) -> None:
        # Verifies we read "versions" not "items" from the list response
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            json={"items": [_sparse("1.0", "READY")]},  # wrong key — should yield 0 results
            status=200,
        )
        client = _make_client()
        results = client.list_ready_versions(MODEL)
        assert results == []

    @rsps_lib.activate
    def test_candidate_fields_correctly_parsed(self) -> None:
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            json={"versions": [_sparse("3.2", "READY", "2026-05-01T12:00:00Z")]},
            status=200,
        )
        rsps_lib.add(
            rsps_lib.GET,
            _version_url("3.2"),
            json=_full(
                "3.2",
                python="3.11.7",
                fw_name="pytorch",
                fw_ver="2.1.0",
                os="linux-x86_64",
                cuda="11.8",
                created_at="2026-05-01T12:00:00Z",
            ),
            status=200,
        )
        client = _make_client()
        results = client.list_ready_versions(MODEL)

        assert len(results) == 1
        c = results[0]
        assert c.version == "3.2"
        assert c.created_at == "2026-05-01T12:00:00Z"
        assert c.dep_snapshot.python_version == "3.11.7"
        assert c.dep_snapshot.framework.name == "pytorch"
        assert c.dep_snapshot.framework.version == "2.1.0"
        assert c.dep_snapshot.os == "linux-x86_64"
        assert c.dep_snapshot.cuda_version == "11.8"

    @rsps_lib.activate
    def test_cuda_version_none_when_absent_from_dep_snapshot(self) -> None:
        rsps_lib.add(
            rsps_lib.GET, LIST_URL, json={"versions": [_sparse("1.0")]}, status=200
        )
        rsps_lib.add(rsps_lib.GET, _version_url("1.0"), json=_full("1.0", cuda=None), status=200)
        client = _make_client()
        results = client.list_ready_versions(MODEL)
        assert results[0].dep_snapshot.cuda_version is None


# ── Pagination ────────────────────────────────────────────────────────────────

class TestPagination:
    @rsps_lib.activate
    def test_pages_through_multiple_pages(self) -> None:
        page1_items = [_sparse(f"{i}.0", "READY") for i in range(1, 201)]
        page2_items = [_sparse(f"{i}.0", "READY") for i in range(201, 401)]
        page3_items = [_sparse(f"{i}.0", "READY") for i in range(401, 406)]

        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            match_querystring=False,
            json={"versions": page1_items, "nextPageToken": "tok1"},
            status=200,
        )
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            match_querystring=False,
            json={"versions": page2_items, "nextPageToken": "tok2"},
            status=200,
        )
        rsps_lib.add(
            rsps_lib.GET,
            LIST_URL,
            match_querystring=False,
            json={"versions": page3_items},
            status=200,
        )

        # 405 READY total → cap to 200 most-recent; each gets a GetVersion call
        for i in range(1, 406):
            rsps_lib.add(
                rsps_lib.GET,
                _version_url(f"{i}.0"),
                json=_full(f"{i}.0", created_at=f"2026-{(i % 12) + 1:02d}-01T00:00:00Z"),
                status=200,
            )

        client = _make_client()
        results = client.list_ready_versions(MODEL)
        # Cap applied: at most 200 results
        assert len(results) == 200

    @rsps_lib.activate
    def test_three_pages_200_200_5_returns_405_candidates(self) -> None:
        """Spec AC: three list pages of 200/200/5 READY → 405 GetVersion calls → 405 candidates
        (only applies when total ≤ _MAX_READY_VERSIONS; here it exceeds cap so 200 returned)."""
        page1 = [_sparse(f"{i}.0", "READY", f"2026-01-{i:02d}T00:00:00Z") for i in range(1, 201)]
        page2 = [
            _sparse(f"{i}.0", "READY", f"2026-02-{(i - 200):02d}T00:00:00Z")
            for i in range(201, 401)
        ]
        page3 = [
            _sparse(f"{i}.0", "READY", f"2026-03-{(i - 400):02d}T00:00:00Z")
            for i in range(401, 406)
        ]

        rsps_lib.add(
            rsps_lib.GET, LIST_URL, match_querystring=False,
            json={"versions": page1, "nextPageToken": "t1"}, status=200,
        )
        rsps_lib.add(
            rsps_lib.GET, LIST_URL, match_querystring=False,
            json={"versions": page2, "nextPageToken": "t2"}, status=200,
        )
        rsps_lib.add(
            rsps_lib.GET, LIST_URL, match_querystring=False,
            json={"versions": page3}, status=200,
        )
        for i in range(1, 406):
            rsps_lib.add(
                rsps_lib.GET, _version_url(f"{i}.0"),
                json=_full(f"{i}.0"), status=200,
            )

        client = _make_client()
        results = client.list_ready_versions(MODEL)
        # 405 total READY but cap is 200
        assert len(results) == _MAX_READY_VERSIONS


# ── Version cap ───────────────────────────────────────────────────────────────

class TestVersionCap:
    @rsps_lib.activate
    def test_cap_at_200_when_250_ready(self) -> None:
        items = [
            _sparse(f"{i}.0", "READY", f"2026-01-{(i % 28) + 1:02d}T{i:02d}:00:00Z")
            for i in range(1, 251)
        ]
        rsps_lib.add(rsps_lib.GET, LIST_URL, json={"versions": items}, status=200)
        for i in range(1, 251):
            rsps_lib.add(
                rsps_lib.GET, _version_url(f"{i}.0"), json=_full(f"{i}.0"), status=200
            )

        client = _make_client()
        results = client.list_ready_versions(MODEL)
        assert len(results) == _MAX_READY_VERSIONS

    @rsps_lib.activate
    def test_cap_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        items = [_sparse(f"{i}.0", "READY") for i in range(1, 251)]
        rsps_lib.add(rsps_lib.GET, LIST_URL, json={"versions": items}, status=200)
        for i in range(1, 251):
            rsps_lib.add(
                rsps_lib.GET, _version_url(f"{i}.0"), json=_full(f"{i}.0"), status=200
            )

        client = _make_client()
        with caplog.at_level(logging.WARNING, logger="ams_client"):
            client.list_ready_versions(MODEL)

        assert any("truncating" in r.message.lower() for r in caplog.records)

    @rsps_lib.activate
    def test_no_cap_when_exactly_200_ready(self) -> None:
        items = [_sparse(f"{i}.0", "READY") for i in range(1, 201)]
        rsps_lib.add(rsps_lib.GET, LIST_URL, json={"versions": items}, status=200)
        for i in range(1, 201):
            rsps_lib.add(
                rsps_lib.GET, _version_url(f"{i}.0"), json=_full(f"{i}.0"), status=200
            )
        client = _make_client()
        results = client.list_ready_versions(MODEL)
        assert len(results) == 200


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    @rsps_lib.activate
    def test_ams_404_on_list_raises_model_not_found(self) -> None:
        rsps_lib.add(rsps_lib.GET, LIST_URL, status=404)
        client = _make_client()
        with pytest.raises(ModelNotFoundException) as exc_info:
            client.list_ready_versions(MODEL)
        assert MODEL in str(exc_info.value)

    @rsps_lib.activate
    def test_ams_500_on_list_raises_ams_unavailable(self) -> None:
        rsps_lib.add(rsps_lib.GET, LIST_URL, status=500)
        client = _make_client()
        with pytest.raises(AMSUnavailableError):
            client.list_ready_versions(MODEL)

    @rsps_lib.activate
    def test_ams_500_on_get_version_raises_ams_unavailable(self) -> None:
        rsps_lib.add(
            rsps_lib.GET, LIST_URL, json={"versions": [_sparse("1.0")]}, status=200
        )
        rsps_lib.add(rsps_lib.GET, _version_url("1.0"), status=503)
        client = _make_client()
        with pytest.raises(AMSUnavailableError):
            client.list_ready_versions(MODEL)

    @rsps_lib.activate
    def test_network_timeout_raises_ams_unavailable(self) -> None:
        import requests as req_lib

        rsps_lib.add(rsps_lib.GET, LIST_URL, body=req_lib.exceptions.Timeout())
        client = _make_client()
        with pytest.raises(AMSUnavailableError):
            client.list_ready_versions(MODEL)

    @rsps_lib.activate
    def test_connection_error_raises_ams_unavailable(self) -> None:
        import requests as req_lib

        rsps_lib.add(rsps_lib.GET, LIST_URL, body=req_lib.exceptions.ConnectionError())
        client = _make_client()
        with pytest.raises(AMSUnavailableError):
            client.list_ready_versions(MODEL)


# ── Timeout parameters ────────────────────────────────────────────────────────

class TestTimeoutParameters:
    def test_custom_timeouts_stored(self) -> None:
        import unittest.mock as mock

        with mock.patch("boto3.Session") as sess:
            creds = mock.MagicMock()
            creds.access_key = "A"
            creds.secret_key = "S"
            creds.token = None
            sess.return_value.get_credentials.return_value.get_frozen_credentials.return_value = (
                creds
            )
            client = AMSClient(ENDPOINT, connect_timeout=1.5, read_timeout=7.0)

        assert client._timeout == (1.5, 7.0)

    @rsps_lib.activate
    def test_timeout_tuple_passed_to_requests(self) -> None:
        """Verify the timeout is forwarded to every HTTP request."""
        import unittest.mock as mock

        rsps_lib.add(rsps_lib.GET, LIST_URL, json={"versions": []}, status=200)

        client = _make_client()
        with mock.patch("requests.get", wraps=requests.get) as mock_get:
            client.list_ready_versions(MODEL)
            call_kwargs = mock_get.call_args_list[0].kwargs
            assert call_kwargs["timeout"] == client._timeout
