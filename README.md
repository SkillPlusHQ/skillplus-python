<!-- Absolute URL on purpose: PyPI renders this README on its own domain, where a
     relative path would 404. -->
<img src="https://skillplus.xyz/banner.png" alt="SkillPlus" width="100%">

# SkillPlus Python SDK

Official Python SDK for the hosted [SkillPlus](https://skillplus.xyz) API.

SkillPlus provides security intelligence for AI skills and agent-facing software, helping developers, teams, and platforms scan, understand, and trust AI skills before installation, approval, or integration.

## Install

```bash
pip install skillplus
```

Requires Python 3.10+.

## Quick start

`query` gives a binary answer: is there a report, or not.

```python
from skillplus import SkillPlus

client = SkillPlus(api_key="skp_...")

result = client.query("https://www.skills.sh/vercel-labs/skills/find-skills")
# GitHub repositories work the same way: "https://github.com/owner/repo"

if result.status == "found":
    print(result.report.verdict)  # "safe" | "medium" | "high" | "unknown" — UI labels: Safe / Caution / High Risk / Unrated
else:
    print("no report yet", "(scan in progress)" if result.scanning else "")
```

Prefer `verdict` over the legacy `rating` field — it folds historical values
onto the current three-tier scale.

## The three integration patterns

**1. Show a report (frontend page)** — pure read, instant:

```python
result = client.query(url)          # found | not_found, never blocks
```

**2. Scan in the background (batch pipelines)** — fire-and-forget, or wait:

```python
ack = client.scan(url)              # returns immediately: ack.accepted / ack.scanning
report = client.scan(url, wait=True)   # blocks until the report completes, returns it
```

**3. Query-and-scan (skill detail pages)** — if it was never scanned, scan it:

```python
result = client.query(url, scan_if_missing=True)   # triggers a scan, still returns immediately
result = client.query(url, wait=True)              # scans if needed AND waits for the report
```

`wait=True` implies `scan_if_missing`; a failing scan raises `SkillPlusError`
(errors travel on the exception channel, never as a status value).

## Client options

```python
client = SkillPlus(
    api_key="skp_...",
    base_url="https://skillplus.xyz",  # staging / self-hosted override
    timeout=30.0,                      # per-request timeout (seconds)
    max_retries=2,                     # retries on 429/502/503, honoring Retry-After
)
```

Waiting calls accept `wait_interval` (default 5 s) and `wait_timeout`
(default 600 s).

## Context manager

```python
from skillplus import SkillPlus

with SkillPlus(api_key="skp_...") as client:
    ack = client.scan("https://www.skills.sh/vercel-labs/skills/find-skills")
    print(ack.accepted)
```

## Methods

| Method | Use case |
|---|---|
| `query(url, ...)` | Binary check: `found` (report attached) or `not_found`. Options: `scan_if_missing`, `wait`. |
| `scan(url, ...)` | Trigger a scan: fire-and-forget ack, or `wait=True` for the finished report. `force=True` re-scans fresh reports. |
| `get_report(scan_id)` | Retrieve a report by id: verdict, findings, AI audit, supply-chain snapshot. |
| `get_badge(scan_id)` | Fetch the badge SVG for embedding. |
| `get_badge_url(scan_id)` | Generate a badge URL for READMEs, marketplaces, or internal portals. |
| `get_report_page_url(scan_id)` | The report page a person can read. The `report_url` field is the API endpoint and returns JSON — use this for links you show a human. |

## Error handling

```python
from skillplus import SkillPlus, SkillPlusError

client = SkillPlus(api_key="skp_...")

try:
    result = client.query("https://www.skills.sh/vercel-labs/skills/find-skills")
except SkillPlusError as error:
    print(error.status_code)
    print(error.message)
```

## Development

```bash
uv sync --extra dev
uv run pytest
uv build
```
