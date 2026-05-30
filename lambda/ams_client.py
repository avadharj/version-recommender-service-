from __future__ import annotations

import logging
from typing import Any

import boto3
import requests
from exceptions import AMSUnavailableError, ModelNotFoundException
from models import Candidate, DepSnapshot, FrameworkInfo
from requests_aws4auth import AWS4Auth  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_MAX_READY_VERSIONS = 200
_PAGE_LIMIT = 200


class AMSClient:
    def __init__(
        self,
        endpoint_url: str,
        region: str = "us-east-1",
        connect_timeout: float = 3.0,
        read_timeout: float = 5.0,
    ) -> None:
        raw_creds = boto3.Session().get_credentials()
        assert raw_creds is not None, "No AWS credentials found"
        creds = raw_creds.get_frozen_credentials()
        self._auth = AWS4Auth(
            creds.access_key,
            creds.secret_key,
            region,
            "execute-api",
            session_token=creds.token,
        )
        self._endpoint = endpoint_url.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)

    def list_ready_versions(self, model_name: str) -> list[Candidate]:
        """
        Two-phase fetch.

        Phase 1: Page through ListVersions to collect (version, createdAt) for all READY
                 versions. Cap at _MAX_READY_VERSIONS most-recent before Phase 2.
        Phase 2: Call GetVersion per READY entry to obtain the full depSnapshot.
        """
        ready: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            items, page_token = self._list_page(model_name, page_token)
            for item in items:
                if item.get("status") == "READY":
                    ready.append(item)
            if page_token is None:
                break

        if len(ready) > _MAX_READY_VERSIONS:
            logger.warning(
                "Model %r has %d READY versions; truncating to %d most recent",
                model_name,
                len(ready),
                _MAX_READY_VERSIONS,
            )
            ready.sort(
                key=lambda v: v.get("createdAt", ""),
                reverse=True,
            )
            ready = ready[:_MAX_READY_VERSIONS]

        return [self._get_version(model_name, item["version"]) for item in ready]

    def _list_page(
        self, model_name: str, page_token: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch one page of sparse version entries."""
        params: dict[str, str | int] = {"limit": _PAGE_LIMIT}
        if page_token is not None:
            params["pageToken"] = page_token

        url = f"{self._endpoint}/models/{model_name}/versions"
        try:
            resp = requests.get(url, params=params, auth=self._auth, timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            raise AMSUnavailableError(str(exc)) from exc

        if resp.status_code == 404:
            raise ModelNotFoundException(f"Model '{model_name}' not found")
        if resp.status_code >= 500:
            raise AMSUnavailableError(f"AMS returned {resp.status_code} for {url}")

        body: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = body.get("versions", [])
        next_token: str | None = body.get("nextPageToken") or None
        return items, next_token

    def _get_version(self, model_name: str, version: str) -> Candidate:
        """Fetch a single full version object (includes depSnapshot)."""
        url = f"{self._endpoint}/models/{model_name}/versions/{version}"
        try:
            resp = requests.get(url, auth=self._auth, timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            raise AMSUnavailableError(str(exc)) from exc

        if resp.status_code >= 500:
            raise AMSUnavailableError(f"AMS returned {resp.status_code} for {url}")

        return self._parse_candidate(resp.json())

    @staticmethod
    def _parse_candidate(raw: dict[str, Any]) -> Candidate:
        """Parse a full GetVersion response → Candidate."""
        dep: dict[str, Any] = raw["depSnapshot"]
        fw: dict[str, Any] = dep["framework"]
        return Candidate(
            version=raw["version"],
            dep_snapshot=DepSnapshot(
                python_version=dep["pythonVersion"],
                framework=FrameworkInfo(name=fw["name"], version=fw["version"]),
                os=dep["os"],
                cuda_version=dep.get("cudaVersion"),
            ),
            created_at=raw["createdAt"],
        )
