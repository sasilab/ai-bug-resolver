# Adding a new MCP tool

This guide walks through the **complete** path for adding a new tool. Every
tool in this project follows the same eight steps. If you skip any of them
your tool will fall short on validation, observability, or security.

We'll use a running example: a hypothetical `pagerduty_create_incident` tool
for a future "on-call automation" use case.

## 0. Decide which use case it belongs to

- **Extending an existing use case?** Add the tool to the matching module
  (e.g. another bug-resolver tool goes in `mcp_server/tools/bitbucket.py`
  or its own bug-resolver file).
- **Starting a new use case?** Create a parallel set of modules:
  `mcp_server/config_<name>.py`, `mcp_server/guardrails_<name>.py`,
  `openclaw-config/system-prompt-<name>.md`, etc. See the "Adding a new
  use case" recipe at the bottom of `CLAUDE.md`.

## 1. Write the tool function

Create or extend a module in `mcp_server/tools/`. Follow the template below.

```python
# mcp_server/tools/pagerduty.py

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config_oncall import get_oncall_settings        # 1. config module
from ..guardrails_oncall import (                      # 2. guardrails
    OncallGuardrailError,
    get_oncall_guardrails,
)
from ..logging import get_logger                       # 3. shared logger

log = get_logger("tools.pagerduty")


class PagerdutyCreateIncidentInput(BaseModel):
    """Strict, no-extra-fields input model. Reject everything we don't expect."""

    model_config = ConfigDict(strict=True, extra="forbid")

    service_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    urgency: str = Field(..., pattern=r"^(high|low)$")


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Every tool returns this shape on failure. Never raise to the caller."""
    return {"ok": False, "error": code, "message": message, **extra}


async def pagerduty_create_incident(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "pagerduty_create_incident"
    start = time.perf_counter()

    # ---- input validation -------------------------------------------------
    try:
        payload = PagerdutyCreateIncidentInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    # ---- guardrails -------------------------------------------------------
    guards = get_oncall_guardrails()
    try:
        guards.enforce_allowed_service(payload.service_id)
    except OncallGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    # ---- external call (httpx, async, wrapped) ----------------------------
    log.info(tool=tool, event="invoke", service_id=payload.service_id)
    settings = get_oncall_settings()
    url = f"{settings.pagerduty_base_url}/incidents"
    headers = {"Authorization": f"Token token={settings.pagerduty_api_key}"}
    body = {
        "incident": {
            "type": "incident",
            "title": payload.title,
            "service": {"id": payload.service_id, "type": "service_reference"},
            "urgency": payload.urgency,
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            return _structured_error(
                "pagerduty_api_error",
                f"PagerDuty returned {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json()
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach PagerDuty")

    # ---- success ---------------------------------------------------------
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {
        "ok": True,
        "incident_id": (data.get("incident") or {}).get("id"),
        "html_url": (data.get("incident") or {}).get("html_url"),
    }


__all__ = ["PagerdutyCreateIncidentInput", "pagerduty_create_incident"]
```

### Hard rules

- **Strict pydantic** (`strict=True, extra="forbid"`). Don't accept unknown
  fields — they're either bugs or attempts to smuggle data.
- **No tracebacks ever escape.** Catch broad `Exception` for input
  validation, narrow `httpx.HTTPError` for I/O. Return `_structured_error`.
- **Log on every invocation** with `tool=...`, `event=...`. Always log
  `event="invoke"` before the external call and `event="success"` (with
  `duration_ms`) on completion.
- **Use httpx.AsyncClient** for HTTP. Never `requests`, never `urllib`.
- **Guardrails first, network second.** If you can reject the call without
  hitting the network, do it.

## 2. Add guardrails

If your tool exposes a new attack surface (a new domain, a new resource ID
pattern, a new query language), add a guardrail. Don't enforce policy in
prompts — agents can be cajoled, code cannot.

Open or create `mcp_server/guardrails_<use_case>.py`:

```python
class OncallGuardrailError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self):
        return {"error": "guardrail_violation", "code": self.code, "message": self.message}


@dataclass(frozen=True)
class OncallGuardrails:
    settings: OncallSettings

    def enforce_allowed_service(self, service_id: str) -> None:
        if service_id not in self.settings.allowed_service_ids:
            raise OncallGuardrailError(
                "service_not_allowed",
                f"PagerDuty service {service_id!r} is not on the allowlist.",
            )
```

Pattern:

- Each policy gets a method named `enforce_<thing>` (raises) or
  `validate_<thing>` (raises and returns the normalized value).
- Each raise uses a **stable string code** like `service_not_allowed` —
  these become part of the public API (callers branch on them).
- No I/O in guardrails. Pure functions of `(payload, settings)`.

## 3. Register the tool in `server.py`

`mcp_server/server.py` keeps two dicts:

```python
_TOOL_REGISTRY: dict[str, tuple[Any, type]] = {
    # ----- Use Case 1: Bug resolution --------------------------------------
    "jira_get_issue": (jira_get_issue, JiraGetIssueInput),
    ...
    # ----- Use Case 2: Infrastructure RCA ----------------------------------
    ...
    # ----- Use Case 3: On-call automation ---------------------------------- ← add here
    "pagerduty_create_incident": (pagerduty_create_incident, PagerdutyCreateIncidentInput),
}

_DESCRIPTIONS: dict[str, str] = {
    ...
    "pagerduty_create_incident": (
        "Create a PagerDuty incident on an allowlisted service. urgency must be 'high' or 'low'."
    ),
}
```

- Use a **descriptive tool name** in lowercase snake_case.
- The description appears verbatim to the agent — tell it any non-obvious
  constraint (allowlists, required formats) right here.
- Add a `# Use Case N: <name>` divider so future readers see the grouping.

## 4. Write tests

Create `mcp-server/tests/test_<name>.py`. **Set required env vars at the
top of the file, before any `mcp_server` import** — `lru_cache` on
`get_*_settings()` snapshots the env at first call:

```python
from __future__ import annotations

import os

os.environ.setdefault("PAGERDUTY_BASE_URL", "https://api.pagerduty.com")
os.environ.setdefault("PAGERDUTY_API_KEY", "fake-token")
os.environ.setdefault("ALLOWED_PAGERDUTY_SERVICES", "P123ABC,P456DEF")

import httpx                              # noqa: E402
import pytest                             # noqa: E402
import respx                              # noqa: E402

from mcp_server import config_oncall      # noqa: E402
from mcp_server.tools.pagerduty import (  # noqa: E402
    pagerduty_create_incident,
)

config_oncall.reset_oncall_settings_cache()


# ---- guardrail rejection ---------------------------------------------------

async def test_rejects_non_allowlisted_service():
    result = await pagerduty_create_incident({
        "service_id": "PNOTONLIST",
        "title": "DB latency spike",
        "urgency": "high",
    })
    assert result["ok"] is False
    assert result["error"] == "service_not_allowed"


# ---- input validation ------------------------------------------------------

async def test_rejects_invalid_urgency():
    result = await pagerduty_create_incident({
        "service_id": "P123ABC",
        "title": "x",
        "urgency": "urgent",  # not in pattern
    })
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


# ---- happy path (HTTP mocked with respx) ----------------------------------

@respx.mock
async def test_creates_incident():
    respx.post("https://api.pagerduty.com/incidents").mock(
        return_value=httpx.Response(
            201,
            json={"incident": {"id": "PINCIDENT1", "html_url": "https://x/incidents/PINCIDENT1"}},
        )
    )
    result = await pagerduty_create_incident({
        "service_id": "P123ABC",
        "title": "DB latency spike",
        "urgency": "high",
    })
    assert result["ok"] is True
    assert result["incident_id"] == "PINCIDENT1"
```

**Cover four scenarios at minimum:**

1. Input validation rejects malformed input (`invalid_input`).
2. Guardrail rejects out-of-policy input (whatever your `code` is).
3. Upstream HTTP error returns a structured error (`*_api_error`).
4. Happy path returns the expected shape.

For socket-based tools, use `monkeypatch` against `asyncio.open_connection`
(see `tests/test_server.py` for the pattern). For FastAPI webhooks, use
`fastapi.testclient.TestClient` (see `tests/test_jenkins.py`).

Run them:

```bash
cd mcp-server
uv run pytest -q tests/test_<name>.py
uv run pytest -q                  # full suite — must stay green
```

## 5. Update `.env.example`

Append a section with a comment header:

```dotenv
# =============================================================================
# On-call automation (Use Case 3) — optional
# =============================================================================
PAGERDUTY_BASE_URL=https://api.pagerduty.com
PAGERDUTY_API_KEY=your-pagerduty-api-token
ALLOWED_PAGERDUTY_SERVICES=PXXXX,PYYYY
```

Only document **placeholder** values, never real ones. The matching real
values live in your local `.env`, which is gitignored.

## 6. Update `README.md`

Add a per-use-case section after the existing Use Case 2 ("Infrastructure
RCA"):

- Architecture overview (a couple of lines + arrow diagram).
- The new tools at a glance (table form).
- Webhook configuration if the new use case is webhook-driven.
- Notes on what's read-only vs. what mutates.

## 7. Update the OpenClaw tool policy

If the new tool should be callable by an existing agent, add it to that
agent's `openclaw-config/tool-policy*.json` allowlist. If it belongs to a
new agent, create a fresh `tool-policy-<name>.json` and a matching
`system-prompt-<name>.md` describing the workflow.

Tool policies are **deny-by-default**. Never leave a wildcard in `allow`.

## 8. Lint and re-run the suite

```bash
cd mcp-server
uv run ruff check . --fix
uv run pytest -q
```

Open a PR per `CONTRIBUTING.md`.

## Checklist

- [ ] Tool function with strict pydantic input model
- [ ] Structured error dicts (never raises to caller)
- [ ] structlog `tool=` + `event=` on invoke/success/error
- [ ] Guardrail(s) called before any network I/O
- [ ] Registered in `server.py` under the right use-case group, with description
- [ ] Tests: input validation, guardrail, HTTP error, happy path
- [ ] `.env.example` updated with placeholders
- [ ] `README.md` updated
- [ ] OpenClaw tool policy updated (or new one created)
- [ ] `ruff check` passes, full `pytest -q` passes
