"""HTTP webhook handler invoked by Jira.

Receives `jira:issue_created` events, validates a shared secret, and dispatches
the issue key to OpenClaw's Gateway API. Returns 200 immediately — the actual
bug resolution runs asynchronously inside OpenClaw.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .logging import configure_logging, get_logger

configure_logging()
log = get_logger("webhook")

app = FastAPI(title="AI Bug Resolver Webhook", version="0.1.0")


def _is_bug_created(payload: dict[str, Any]) -> tuple[bool, str | None]:
    event = payload.get("webhookEvent")
    issue = payload.get("issue") or {}
    issue_type = ((issue.get("fields") or {}).get("issuetype") or {}).get("name")
    if event != "jira:issue_created":
        return False, f"ignored event {event!r}"
    if (issue_type or "").lower() != "bug":
        return False, f"ignored issue type {issue_type!r}"
    return True, None


async def _trigger_openclaw(issue_key: str) -> None:
    settings = get_settings()
    url = f"{settings.openclaw_gateway_url}/v1/runs"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.openclaw_api_key:
        headers["Authorization"] = f"Bearer {settings.openclaw_api_key}"
    body = {
        "agent": "bug-resolver",
        "input": {"issue_key": issue_key},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            log.error(
                event="openclaw_trigger_failed",
                issue_key=issue_key,
                status_code=resp.status_code,
                body=resp.text[:500],
            )
            return
        log.info(event="openclaw_triggered", issue_key=issue_key, status_code=resp.status_code)
    except httpx.HTTPError as exc:
        log.error(event="openclaw_http_error", issue_key=issue_key, error=str(exc))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/jira")
async def jira_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: str = Query(..., min_length=8, max_length=256),
) -> JSONResponse:
    settings = get_settings()
    if not hmac.compare_digest(secret, settings.webhook_secret):
        log.warning(event="webhook_bad_secret")
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body") from None

    accepted, reason = _is_bug_created(payload)
    if not accepted:
        log.info(event="webhook_skipped", reason=reason)
        return JSONResponse({"status": "skipped", "reason": reason}, status_code=200)

    issue = payload.get("issue") or {}
    issue_key = issue.get("key")
    if not isinstance(issue_key, str) or not issue_key:
        raise HTTPException(status_code=400, detail="missing issue.key")

    # Enforce MVP scope: only one Jira project allowed.
    if not issue_key.startswith(f"{settings.jira_project_key}-"):
        log.warning(event="webhook_wrong_project", issue_key=issue_key)
        return JSONResponse(
            {"status": "skipped", "reason": "issue outside allowed project"},
            status_code=200,
        )

    log.info(event="webhook_accepted", issue_key=issue_key)
    background_tasks.add_task(_trigger_openclaw, issue_key)
    return JSONResponse({"status": "accepted", "issue_key": issue_key}, status_code=200)


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "mcp_server.webhook:app",
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()


# Silence unused-import warnings for asyncio (used implicitly by FastAPI/uvicorn).
_ = asyncio
