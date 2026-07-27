from __future__ import annotations

import copy

import pytest
import respx
from httpx import Response

from skillplus import SkillPlus, SkillPlusError

from test_client import REPORT_PAYLOAD


def _report(**overrides):
    payload = copy.deepcopy(REPORT_PAYLOAD)
    payload.update(overrides)
    return payload


SUPPLY_CHAIN = {
    "dependency_count": 12,
    "endpoint_count": 3,
    "blacklist_hits": [
        {
            "kind": "package",
            "ecosystem": "pypi",
            "name": "litellm",
            "version_spec": {"exact": ["1.82.7", "1.82.8"]},
            "reason": "known malicious release",
            "severity": "critical",
            "reference_url": "https://example.com/advisory",
            "match_strength": "exact",
            "matched_version": "1.82.7",
        }
    ],
}


@respx.mock
def test_supply_chain_parsed() -> None:
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(200, json=_report(supply_chain=SUPPLY_CHAIN))
    )
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")

    assert report.supply_chain is not None
    assert report.supply_chain.dependency_count == 12
    assert report.supply_chain.endpoint_count == 3
    hit = report.supply_chain.blacklist_hits[0]
    assert hit.name == "litellm"
    assert hit.ecosystem == "pypi"
    assert hit.matched_version == "1.82.7"
    assert hit.version_spec == {"exact": ["1.82.7", "1.82.8"]}


@respx.mock
def test_supply_chain_absent_is_none() -> None:
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(200, json=_report())
    )
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")
    assert report.supply_chain is None


@respx.mock
def test_agent_runs_parsed() -> None:
    payload = _report()
    payload["ai_report"]["agents"] = [
        {
            "agent": "supply_chain",
            "status": "completed",
            "findings": [{"category": "SUPPLY_CHAIN", "severity": "high"}],
            "summary": "one poisoned dependency",
            "tokens_used": 1234,
            "duration_ms": 4000,
            "error_message": None,
        }
    ]
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(200, json=payload)
    )
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")

    assert report.ai_report is not None
    assert report.ai_report.agents is not None
    assert report.ai_report.agents[0].agent == "supply_chain"
    assert report.ai_report.agents[0].findings[0]["severity"] == "high"


@respx.mock
def test_v3_severity_passthrough() -> None:
    payload = _report()
    payload["issues"][0]["severity"] = "high"
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(200, json=payload)
    )
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")
    assert report.issues[0].severity == "high"
    assert report.issues[0].severity_normalized == "high"


@respx.mock
def test_base_url_override() -> None:
    respx.get("https://staging.example.com/api/report/scan_123").mock(
        return_value=Response(200, json=_report())
    )
    with SkillPlus(api_key="skp_test", base_url="https://staging.example.com/") as client:
        report = client.get_report("scan_123")
        assert client.get_badge_url("scan_123") == (
            "https://staging.example.com/api/report/scan_123/badge.svg"
        )
    assert report.scan_id == "scan_123"


@respx.mock
def test_retry_on_429_with_retry_after() -> None:
    route = respx.get("https://skillplus.xyz/api/report/scan_123")
    route.side_effect = [
        Response(429, headers={"Retry-After": "0"}, json={"detail": "Rate limit exceeded"}),
        Response(200, json=_report()),
    ]
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")
    assert report.scan_id == "scan_123"
    assert route.call_count == 2


@respx.mock
def test_no_retry_when_disabled() -> None:
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(429, headers={"Retry-After": "0"}, json={"detail": "Rate limit exceeded"})
    )
    with SkillPlus(api_key="skp_test", max_retries=0) as client:
        with pytest.raises(SkillPlusError) as excinfo:
            client.get_report("scan_123")
    assert excinfo.value.status_code == 429


@respx.mock
def test_query_wait_polls_until_completed_v3() -> None:
    queued = {"status": "queued", "message": "Scan queued", "scan_id": None, "poll_url": None}
    found = {
        "status": "found",
        "message": "Report found",
        "scan_id": "scan_123",
        "poll_url": "/api/scan/status/scan_123",
        "report": _report(),
    }
    route = respx.post("https://skillplus.xyz/api/sdk/query")
    route.side_effect = [Response(200, json=queued), Response(200, json=found)]

    with SkillPlus(api_key="skp_test") as client:
        result = client.query("https://github.com/owner/repo", wait=True, wait_interval=0.01)

    assert result.report is not None and result.report.scan_id == "scan_123"
    assert route.call_count == 2


@respx.mock
def test_query_wait_times_out_v3() -> None:
    queued = {"status": "queued", "message": "Scan queued", "scan_id": None, "poll_url": None}
    respx.post("https://skillplus.xyz/api/sdk/query").mock(
        return_value=Response(200, json=queued)
    )
    with SkillPlus(api_key="skp_test") as client:
        with pytest.raises(SkillPlusError) as excinfo:
            client.query("https://github.com/owner/repo", wait=True, wait_interval=0.01, wait_timeout=0.02)
    assert "Timed out" in str(excinfo.value)


@respx.mock
def test_verdict_fallback_from_legacy_rating() -> None:
    respx.get("https://skillplus.xyz/api/report/scan_123").mock(
        return_value=Response(200, json=_report(rating="low"))
    )
    with SkillPlus(api_key="skp_test") as client:
        report = client.get_report("scan_123")
    assert report.rating == "low"
    assert report.verdict == "safe"
