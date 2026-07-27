from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

Rating: TypeAlias = Literal["safe", "low", "medium", "high", "unknown"]
# Rating system v3: three-tier verdict (low→safe, critical→high). Prefer over Rating.
Verdict: TypeAlias = Literal["safe", "medium", "high", "unknown"]
# Raw rule-issue severity. v3 scans emit high/medium/low; reports scanned
# before v3 may still carry danger/warning/info. Prefer SeverityNormalized.
Severity: TypeAlias = Literal[
    "high", "medium", "low", "critical", "danger", "warning", "info", "safe"
]
# Rating system v3: unified finding severity. Prefer over Severity.
SeverityNormalized: TypeAlias = Literal["high", "medium", "low", "safe"]
IssueCategory: TypeAlias = Literal[
    "url",
    "code",
    "prompt",
    "structure",
    "persistence",
    "credential",
    "supply_chain",
    "propagation",
]
# query() is binary: a report exists or it does not.
QueryStatus: TypeAlias = Literal["found", "not_found"]
AiReportStatus: TypeAlias = Literal["pending", "processing", "completed", "failed"]


@dataclass(slots=True)
class ScanIssue:
    rule_id: str
    severity: Severity  # deprecated: prefer severity_normalized
    severity_normalized: SeverityNormalized
    category: IssueCategory
    message: str
    file: str | None = None
    line: int | None = None
    snippet: str | None = None
    domain: str | None = None


@dataclass(slots=True)
class ScanSummary:
    total_issues: int
    danger: int
    warning: int
    info: int
    files_scanned: int
    by_category: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AiFalsePositive:
    rule_id: str
    file: str
    reason: str


@dataclass(slots=True)
class AiCategoryResult:
    category: str
    status: str
    severity: str
    title: str
    description: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(slots=True)
class AgentRun:
    """One agent's run in the v2 multi-agent pipeline (present on get_report)."""

    agent: str
    status: str
    findings: list[dict] = field(default_factory=list)
    summary: str | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    error_message: str | None = None


@dataclass(slots=True)
class AiReport:
    status: AiReportStatus
    risk_level: str | None
    refined_rating: Rating | None
    false_positives: list[AiFalsePositive] = field(default_factory=list)
    analysis: list[AiCategoryResult] = field(default_factory=list)
    summary: str | None = None
    categories_checked: list[AiCategoryResult] = field(default_factory=list)
    tokens_used: int | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    # Per-agent detail (multi-agent pipeline); None when not exposed.
    agents: list[AgentRun] | None = None


@dataclass(slots=True)
class SupplyChainHit:
    """One supply-chain blacklist hit (a dependency/domain known to be poisoned)."""

    kind: str
    ecosystem: str | None
    name: str
    version_spec: dict
    reason: str
    severity: str
    reference_url: str | None
    match_strength: str
    matched_version: str | None = None


@dataclass(slots=True)
class SupplyChainReport:
    """Supply-chain section of a report. Any blacklist hit floors the verdict
    to high unless the skill is admin-whitelisted."""

    dependency_count: int = 0
    endpoint_count: int = 0
    blacklist_hits: list[SupplyChainHit] = field(default_factory=list)


@dataclass(slots=True)
class PlatformListing:
    platform: str
    url: str
    installs: int


@dataclass(slots=True)
class ScanReport:
    scan_id: str
    skill_name: str
    source: str
    skill_path: str
    tree_sha: str
    rule_version: str
    valid_skill: bool
    rating: Rating  # deprecated: prefer verdict
    verdict: Verdict
    scanned_at: str
    cached: bool
    scan_duration_ms: int
    summary: ScanSummary
    issues: list[ScanIssue]
    report_url: str
    badge_url: str
    blacklisted: bool
    whitelisted: bool
    status: str
    ai_report: AiReport | None = None
    external_links: dict[str, str] = field(default_factory=dict)
    platform_listings: list[PlatformListing] = field(default_factory=list)
    # Supply-chain snapshot; None when the section is unavailable.
    supply_chain: SupplyChainReport | None = None


@dataclass(slots=True)
class QueryResult:
    """Binary answer: ``found`` (report attached) or ``not_found``.

    ``scanning`` is a courtesy flag on not_found: True means a scan for this
    skill is queued or running right now (check back shortly)."""

    status: QueryStatus
    message: str
    report: ScanReport | None = None
    scanning: bool = False


@dataclass(slots=True)
class ScanAck:
    """Acknowledgement of a fire-and-forget scan() call.

    ``accepted`` is True only when THIS call queued a new scan; False means
    it was deduplicated (already scanning, or a fresh report exists and
    ``force`` was not set)."""

    accepted: bool
    scanning: bool
    message: str
