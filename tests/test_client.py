from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from skillplus import SkillPlus, SkillPlusError


REPORT_PAYLOAD = {
    "scan_id": "scan_123",
    "skill_name": "example",
    "source": "github:owner/repo",
    "skill_path": "skills/example",
    "tree_sha": "abc123",
    "rule_version": "rules-v1",
    "valid_skill": True,
    "rating": "safe",
    "scanned_at": "2026-06-10T00:00:00Z",
    "cached": True,
    "scan_duration_ms": 1200,
    "summary": {
        "total_issues": 1,
        "danger": 0,
        "warning": 1,
        "info": 0,
        "files_scanned": 4,
        "by_category": {"url": 1},
    },
    "issues": [
        {
            "rule_id": "external-url",
            "severity": "warning",
            "category": "url",
            "message": "External URL found",
            "file": "SKILL.md",
            "line": 12,
            "snippet": "https://example.com",
            "domain": "example.com",
        }
    ],
    "report_url": "https://skillplus.xyz/report/scan_123",
    "badge_url": "https://skillplus.xyz/api/report/scan_123/badge.svg",
    "blacklisted": False,
    "whitelisted": False,
    "status": "completed",
    "ai_report": {
        "status": "completed",
        "risk_level": "low",
        "refined_rating": "safe",
        "false_positives": [
            {"rule_id": "external-url", "file": "SKILL.md", "reason": "Documentation link"}
        ],
        "analysis": [
            {
                "category": "Command Execution",
                "status": "pass",
                "severity": "safe",
                "title": "No command execution",
                "description": "No risky command execution was found.",
                "evidence": ["No shell usage"],
                "confidence": 0.98,
            }
        ],
        "summary": "No meaningful risk found.",
        "categories_checked": [],
        "tokens_used": 500,
        "duration_ms": 800,
        "error_message": None,
    },
    "external_links": {"website": "https://example.com"},
    "platform_listings": [
        {"platform": "skills.sh", "url": "https://skills.sh/owner/repo/example", "installs": 10}
    ],
}


SERVER_QUEUED = {"status": "queued", "message": "Scan queued", "scan_id": None, "poll_url": None}
SERVER_RUNNING = {"status": "running", "message": "Scan already in progress", "scan_id": "s1", "poll_url": "/x"}
SERVER_NOT_FOUND = {"status": "not_found", "message": "Report not found", "scan_id": None, "poll_url": None}
SERVER_FAILED = {"status": "failed", "message": "boom", "scan_id": None, "poll_url": None}


def server_found(report=None):
    return {
        "status": "found",
        "message": "Report found",
        "scan_id": "scan_123",
        "poll_url": "/api/scan/status/scan_123",
        "report": report or REPORT_PAYLOAD,
    }


@respx.mock
def test_query_sends_auth_and_request_body() -> None:
    route = respx.post("https://skillplus.xyz/api/sdk/query").mock(
        return_value=Response(200, json=server_found())
    )
    with SkillPlus(api_key="skp_test") as client:
        result = client.query(
            "https://github.com/owner/repo",
            skill_path="skills/example",
            scan_if_missing=True,
            preferred_provider="github",
        )
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer skp_test"
    assert json.loads(request.read()) == {
        "repo_url": "https://github.com/owner/repo",
        "skill_path": "skills/example",
        "scan_if_missing": True,
        "preferred_provider": "github",
    }
    assert result.status == "found"
    assert result.scanning is False
    assert result.report is not None
    assert result.report.scan_id == "scan_123"
    assert result.report.summary.total_issues == 1
    assert result.report.issues[0].rule_id == "external-url"


@respx.mock
def test_query_folds_in_progress_onto_not_found_scanning() -> None:
    for server, scanning in ((SERVER_QUEUED, True), (SERVER_RUNNING, True),
                             (SERVER_NOT_FOUND, False), (SERVER_FAILED, False)):
        respx.post("https://skillplus.xyz/api/sdk/query").mock(
            return_value=Response(200, json=server)
        )
        with SkillPlus(api_key="skp_test") as client:
            result = client.query("https://skills.sh/o/r/s")
        assert result.status == "not_found"
        assert result.report is None
        assert result.scanning is scanning


@respx.mock
def test_query_wait_polls_until_completed() -> None:
    route = respx.post("https://skillplus.xyz/api/sdk/query")
    route.side_effect = [
        Response(200, json=SERVER_QUEUED),
        Response(200, json=SERVER_RUNNING),
        Response(200, json=server_found()),
    ]
    with SkillPlus(api_key="skp_test") as client:
        result = client.query("https://skills.sh/o/r/s", wait=True, wait_interval=0.01)
    assert result.status == "found"
    assert result.report is not None
    assert route.call_count == 3


@respx.mock
def test_query_wait_raises_on_scan_failure() -> None:
    route = respx.post("https://skillplus.xyz/api/sdk/query")
    route.side_effect = [Response(200, json=SERVER_QUEUED), Response(200, json=SERVER_FAILED)]
    with SkillPlus(api_key="skp_test") as client:
        with pytest.raises(SkillPlusError) as excinfo:
            client.query("https://skills.sh/o/r/s", wait=True, wait_interval=0.01)
    assert "failed" in str(excinfo.value).lower()


@respx.mock
def test_query_wait_times_out() -> None:
    respx.post("https://skillplus.xyz/api/sdk/query").mock(
        return_value=Response(200, json=SERVER_QUEUED)
    )
    with SkillPlus(api_key="skp_test") as client:
        with pytest.raises(SkillPlusError) as excinfo:
            client.query("https://skills.sh/o/r/s", wait=True, wait_interval=0.01, wait_timeout=0.02)
    assert "Timed out" in str(excinfo.value)


@respx.mock
def test_scan_ack_semantics() -> None:
    for server, accepted, scanning in ((SERVER_QUEUED, True, True),
                                       (SERVER_RUNNING, False, True),
                                       (server_found(), False, False)):
        respx.post("https://skillplus.xyz/api/sdk/scan").mock(
            return_value=Response(200, json=server)
        )
        with SkillPlus(api_key="skp_test") as client:
            ack = client.scan("https://skills.sh/o/r/s")
        assert (ack.accepted, ack.scanning) == (accepted, scanning)


@respx.mock
def test_scan_wait_triggers_then_returns_report() -> None:
    scan_route = respx.post("https://skillplus.xyz/api/sdk/scan").mock(
        return_value=Response(200, json=SERVER_QUEUED)
    )
    query_route = respx.post("https://skillplus.xyz/api/sdk/query")
    query_route.side_effect = [Response(200, json=SERVER_RUNNING), Response(200, json=server_found())]
    with SkillPlus(api_key="skp_test") as client:
        report = client.scan("https://skills.sh/o/r/s", wait=True, wait_interval=0.01)
    assert report.scan_id == "scan_123"
    assert scan_route.call_count == 1
    assert query_route.call_count == 2


@respx.mock
def test_scan_wait_force_waits_for_superseding_report() -> None:
    import copy
    old = copy.deepcopy(REPORT_PAYLOAD)
    new = copy.deepcopy(REPORT_PAYLOAD)
    new["scanned_at"] = "2026-07-18T12:00:00Z"
    respx.post("https://skillplus.xyz/api/sdk/scan").mock(
        return_value=Response(200, json=SERVER_QUEUED)
    )
    query_route = respx.post("https://skillplus.xyz/api/sdk/query")
    query_route.side_effect = [
        Response(200, json=server_found(old)),   # prior capture
        Response(200, json=server_found(old)),   # still the old report
        Response(200, json=server_found(new)),   # rescanned
    ]
    with SkillPlus(api_key="skp_test") as client:
        report = client.scan("https://skills.sh/o/r/s", force=True, wait=True, wait_interval=0.01)
    assert report.scanned_at == "2026-07-18T12:00:00Z"
    assert query_route.call_count == 3


@respx.mock
def test_http_error_raises_skillplus_error() -> None:
    respx.post("https://skillplus.xyz/api/sdk/query").mock(
        return_value=Response(401, json={"detail": "Invalid API key"})
    )
    with SkillPlus(api_key="skp_bad") as client:
        with pytest.raises(SkillPlusError) as excinfo:
            client.query("https://skills.sh/o/r/s")
    assert excinfo.value.status_code == 401
    assert "Invalid API key" in str(excinfo.value)


@respx.mock
def test_get_badge_and_badge_url() -> None:
    respx.get("https://skillplus.xyz/api/report/scan_123/badge.svg").mock(
        return_value=Response(200, text="<svg/>")
    )
    with SkillPlus(api_key="skp_test") as client:
        assert client.get_badge("scan_123") == "<svg/>"
        assert client.get_badge_url("scan_123") == "https://skillplus.xyz/api/report/scan_123/badge.svg"
