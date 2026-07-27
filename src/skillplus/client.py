from __future__ import annotations

import time
from typing import Any

import httpx

from .errors import SkillPlusError
from .types import (
    AgentRun,
    AiCategoryResult,
    AiFalsePositive,
    AiReport,
    PlatformListing,
    QueryResult,
    ScanAck,
    ScanIssue,
    ScanReport,
    ScanSummary,
    SupplyChainHit,
    SupplyChainReport,
)

DEFAULT_BASE_URL = "https://skillplus.xyz"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2
_RETRY_STATUSES = frozenset({429, 502, 503})


class SkillPlus:
    """Client for the hosted SkillPlus API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "skillplus-python/0.2.0",
            },
        )

    def __enter__(self) -> SkillPlus:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def query(
        self,
        repo_url: str,
        *,
        skill_path: str | None = None,
        scan_if_missing: bool = False,
        wait: bool = False,
        wait_interval: float = 5.0,
        wait_timeout: float = 600.0,
        preferred_provider: str | None = None,
    ) -> QueryResult:
        """Is there a report? Binary answer: ``found`` (report attached) or
        ``not_found`` (with ``scanning`` telling you whether one is on the way).

        - ``scan_if_missing=True`` — queue a scan when nothing exists, still
          return immediately.
        - ``wait=True`` — block until a completed report exists (scanning
          first if needed) and return ``found``; raises on failure/timeout.
        """

        if wait:
            return self._wait_for_completed(
                repo_url,
                skill_path=skill_path,
                preferred_provider=preferred_provider,
                prior=None,
                interval=wait_interval,
                timeout=wait_timeout,
            )
        payload = self._raw_query(
            repo_url, skill_path=skill_path,
            scan_if_missing=scan_if_missing, preferred_provider=preferred_provider,
        )
        return _map_server_state(payload)

    def scan(
        self,
        repo_url: str,
        *,
        skill_path: str | None = None,
        preferred_provider: str | None = None,
        force: bool = False,
        wait: bool = False,
        wait_interval: float = 5.0,
        wait_timeout: float = 600.0,
    ) -> ScanAck | ScanReport:
        """Trigger a scan.

        - Default: fire-and-forget — returns a :class:`ScanAck` immediately
          (``accepted=False`` means deduplicated: already scanning, or a
          fresh report exists and ``force`` was not set).
        - ``wait=True`` — block until the resulting report completes and
          return the report itself.
        """

        if wait:
            prior: ScanReport | None = None
            if force:
                current = self.query(repo_url, skill_path=skill_path)
                prior = current.report if current.status == "found" else None
            self._raw_scan(
                repo_url, skill_path=skill_path,
                preferred_provider=preferred_provider, force=force,
            )
            found = self._wait_for_completed(
                repo_url,
                skill_path=skill_path,
                preferred_provider=preferred_provider,
                prior=prior,
                interval=wait_interval,
                timeout=wait_timeout,
            )
            assert found.report is not None
            return found.report

        payload = self._raw_scan(
            repo_url, skill_path=skill_path,
            preferred_provider=preferred_provider, force=force,
        )
        status = payload.get("status")
        message = payload.get("message") or ""
        if status == "queued":
            return ScanAck(accepted=True, scanning=True, message=message or "Scan queued")
        if status == "running":
            return ScanAck(accepted=False, scanning=True, message=message or "Scan already in progress")
        if status == "found":
            return ScanAck(
                accepted=False, scanning=False,
                message="A fresh report already exists (pass force=True to re-scan)",
            )
        raise SkillPlusError(message or "Scan failed", detail=status)

    def _raw_query(
        self, repo_url: str, *, skill_path: str | None,
        scan_if_missing: bool, preferred_provider: str | None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/sdk/query",
            json={
                "repo_url": repo_url,
                "skill_path": skill_path,
                "scan_if_missing": scan_if_missing,
                "preferred_provider": preferred_provider,
            },
        )

    def _raw_scan(
        self, repo_url: str, *, skill_path: str | None,
        preferred_provider: str | None, force: bool,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/sdk/scan",
            json={
                "repo_url": repo_url,
                "skill_path": skill_path,
                "preferred_provider": preferred_provider,
                "force": force,
            },
        )

    def _wait_for_completed(
        self,
        repo_url: str,
        *,
        skill_path: str | None,
        preferred_provider: str | None,
        prior: ScanReport | None,
        interval: float,
        timeout: float,
    ) -> QueryResult:
        """Poll until a completed report exists; a ``prior`` report (from a
        forced re-scan) must be superseded before we accept an answer."""

        deadline = time.monotonic() + timeout
        while True:
            payload = self._raw_query(
                repo_url, skill_path=skill_path,
                scan_if_missing=True, preferred_provider=preferred_provider,
            )
            status = payload.get("status")
            if status == "failed":
                raise SkillPlusError(
                    f"Scan failed: {payload.get('message') or ''}", detail="failed"
                )
            report_payload = payload.get("report")
            if (
                status == "found"
                and report_payload
                and report_payload.get("status") == "completed"
                and (prior is None or report_payload.get("scanned_at") != prior.scanned_at)
            ):
                return _map_server_state(payload)
            if time.monotonic() + interval > deadline:
                raise SkillPlusError(
                    f"Timed out waiting for report (last status: {status})",
                    detail=status,
                )
            time.sleep(interval)

    def get_report(self, scan_id: str) -> ScanReport:
        """Retrieve structured report data by scan ID."""

        payload = self._request_json("GET", f"/api/report/{scan_id}")
        return _parse_report(payload)

    def get_badge(self, scan_id: str) -> str:
        """Fetch the badge SVG for a report."""

        return self._request_text("GET", f"/api/report/{scan_id}/badge.svg")

    def get_badge_url(self, scan_id: str) -> str:
        """Return the public badge URL for a report."""

        return f"{self._base_url}/api/report/{scan_id}/badge.svg"

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SkillPlusError("SkillPlus returned invalid JSON", status_code=response.status_code) from exc

        if not isinstance(payload, dict):
            raise SkillPlusError(
                "SkillPlus returned an unexpected response",
                status_code=response.status_code,
                detail=payload,
            )
        return payload

    def _request_text(self, method: str, path: str, **kwargs: Any) -> str:
        return self._request(method, path, **kwargs).text

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(max(float(retry_after), 0.0), 60.0)
                except ValueError:
                    pass
        return min(2.0**attempt, 10.0)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay(None, attempt))
                    continue
                raise SkillPlusError(str(exc), detail=exc) from exc
            if response.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(self._retry_delay(response, attempt))
                continue
            break
        assert response is not None

        if response.is_error:
            detail: object | None = None
            message = response.reason_phrase
            try:
                detail = response.json()
                if isinstance(detail, dict):
                    message = str(detail.get("detail") or detail.get("message") or message)
            except ValueError:
                detail = response.text or None
                if response.text:
                    message = response.text

            raise SkillPlusError(message, status_code=response.status_code, detail=detail)

        return response


def _map_server_state(payload: dict[str, Any]) -> QueryResult:
    """Fold the server's five-state machine onto the binary query contract."""
    status = payload.get("status")
    if status == "found" and payload.get("report"):
        return QueryResult(
            status="found",
            message=payload.get("message") or "Report found",
            report=_parse_report(payload["report"]),
            scanning=False,
        )
    return QueryResult(
        status="not_found",
        message=payload.get("message") or "Report not found",
        report=None,
        scanning=status in ("queued", "running"),
    )


def _parse_report(payload: dict[str, Any]) -> ScanReport:
    return ScanReport(
        scan_id=payload["scan_id"],
        skill_name=payload["skill_name"],
        source=payload["source"],
        skill_path=payload["skill_path"],
        tree_sha=payload["tree_sha"],
        rule_version=payload["rule_version"],
        valid_skill=payload["valid_skill"],
        rating=payload["rating"],
        verdict=payload.get("verdict")
        or {"low": "safe", "critical": "high"}.get(payload["rating"], payload["rating"]),
        scanned_at=payload["scanned_at"],
        cached=payload["cached"],
        scan_duration_ms=payload["scan_duration_ms"],
        summary=_parse_summary(payload["summary"]),
        issues=[_parse_issue(issue) for issue in payload.get("issues", [])],
        report_url=payload["report_url"],
        badge_url=payload["badge_url"],
        blacklisted=bool(payload.get("blacklisted")),
        whitelisted=bool(payload.get("whitelisted")),
        status=payload["status"],
        ai_report=_parse_ai_report(payload["ai_report"]) if payload.get("ai_report") else None,
        external_links=payload.get("external_links") or {},
        platform_listings=[
            PlatformListing(
                platform=listing["platform"],
                url=listing["url"],
                installs=listing["installs"],
            )
            for listing in payload.get("platform_listings", [])
        ],
        supply_chain=_parse_supply_chain(payload.get("supply_chain")),
    )


def _parse_supply_chain(payload: dict[str, Any] | None) -> SupplyChainReport | None:
    if not payload:
        return None
    return SupplyChainReport(
        dependency_count=payload.get("dependency_count") or 0,
        endpoint_count=payload.get("endpoint_count") or 0,
        blacklist_hits=[
            SupplyChainHit(
                kind=hit["kind"],
                ecosystem=hit.get("ecosystem"),
                name=hit["name"],
                version_spec=hit.get("version_spec") or {},
                reason=hit["reason"],
                severity=hit["severity"],
                reference_url=hit.get("reference_url"),
                match_strength=hit["match_strength"],
                matched_version=hit.get("matched_version"),
            )
            for hit in payload.get("blacklist_hits", [])
        ],
    )


def _parse_summary(payload: dict[str, Any]) -> ScanSummary:
    return ScanSummary(
        total_issues=payload["total_issues"],
        danger=payload["danger"],
        warning=payload["warning"],
        info=payload["info"],
        files_scanned=payload["files_scanned"],
        by_category=payload.get("by_category") or {},
    )


def _parse_issue(payload: dict[str, Any]) -> ScanIssue:
    return ScanIssue(
        rule_id=payload["rule_id"],
        severity=payload["severity"],
        severity_normalized=payload.get("severity_normalized")
        or {"danger": "high", "warning": "medium", "info": "low"}.get(
            payload["severity"], payload["severity"]
        ),
        category=payload["category"],
        message=payload["message"],
        file=payload.get("file"),
        line=payload.get("line"),
        snippet=payload.get("snippet"),
        domain=payload.get("domain"),
    )


def _parse_ai_report(payload: dict[str, Any]) -> AiReport:
    return AiReport(
        status=payload["status"],
        risk_level=payload.get("risk_level"),
        refined_rating=payload.get("refined_rating"),
        false_positives=[
            AiFalsePositive(
                rule_id=false_positive["rule_id"],
                file=false_positive["file"],
                reason=false_positive["reason"],
            )
            for false_positive in payload.get("false_positives", [])
        ],
        analysis=[_parse_ai_category(item) for item in payload.get("analysis", [])],
        summary=payload.get("summary"),
        categories_checked=[
            _parse_ai_category(item) for item in payload.get("categories_checked", [])
        ],
        tokens_used=payload.get("tokens_used"),
        duration_ms=payload.get("duration_ms"),
        error_message=payload.get("error_message"),
        agents=(
            [
                AgentRun(
                    agent=run["agent"],
                    status=run["status"],
                    findings=run.get("findings") or [],
                    summary=run.get("summary"),
                    tokens_used=run.get("tokens_used"),
                    duration_ms=run.get("duration_ms"),
                    error_message=run.get("error_message"),
                )
                for run in payload["agents"]
            ]
            if payload.get("agents") is not None
            else None
        ),
    )


def _parse_ai_category(payload: dict[str, Any]) -> AiCategoryResult:
    return AiCategoryResult(
        category=payload["category"],
        status=payload["status"],
        severity=payload["severity"],
        title=payload["title"],
        description=payload["description"],
        evidence=payload.get("evidence") or [],
        confidence=payload.get("confidence") or 0.0,
    )
