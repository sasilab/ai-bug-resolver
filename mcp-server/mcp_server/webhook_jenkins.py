"""HTTP webhook handler invoked by Jenkins on build completion.

Receives Jenkins build payloads (configurable via the "Notification" or
"Generic Webhook Trigger" plugins), validates a shared secret, filters to
failed builds, and dispatches the build coordinates to OpenClaw's Gateway.

Kept in a separate module from ``webhook.py`` so the two use cases evolve
independently. Both apps can be served by a single uvicorn process by mounting
this app under ``/jenkins`` (see README).
"""

from __future__ import annotations

import hmac
from typing import Any

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .config_infra import get_infra_settings
from .logging import configure_logging, get_logger

configure_logging()
log = get_logger("webhook_jenkins")

app = FastAPI(title="AI Bug Resolver — Jenkins Webhook", version="0.1.0")


def _extract_build(payload: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """Best-effort extraction of (job_name, build_number, result).

    Supports the official Jenkins Notification plugin shape (``build`` block)
    and the Generic Webhook Trigger shape (top-level fields).
    """
    if "build" in payload and isinstance(payload["build"], dict):
        build = payload["build"]
        return (
            payload.get("name") or payload.get("job_name"),
            build.get("number"),
            (build.get("status") or build.get("result")),
        )
    return (
        payload.get("job_name") or payload.get("name"),
        payload.get("build_number") or payload.get("number"),
        payload.get("result") or payload.get("status"),
    )


async def _trigger_openclaw_infra(job_name: str, build_number: int) -> None:
    settings = get_settings()
    url = f"{settings.openclaw_gateway_url}/v1/runs"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.openclaw_api_key:
        headers["Authorization"] = f"Bearer {settings.openclaw_api_key}"
    body = {
        "agent": "infra-rca",
        "input": {"job_name": job_name, "build_number": build_number},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            log.error(
                event="openclaw_trigger_failed",
                job_name=job_name,
                build_number=build_number,
                status_code=resp.status_code,
                body=resp.text[:500],
            )
            return
        log.info(
            event="openclaw_triggered",
            job_name=job_name,
            build_number=build_number,
            status_code=resp.status_code,
        )
    except httpx.HTTPError as exc:
        log.error(
            event="openclaw_http_error",
            job_name=job_name,
            build_number=build_number,
            error=str(exc),
        )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/jenkins")
async def jenkins_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: str = Query(..., min_length=8, max_length=256),
) -> JSONResponse:
    infra = get_infra_settings()
    if not infra.jenkins_webhook_secret:
        log.error(event="webhook_secret_missing")
        raise HTTPException(status_code=500, detail="JENKINS_WEBHOOK_SECRET is not configured")
    if not hmac.compare_digest(secret, infra.jenkins_webhook_secret):
        log.warning(event="webhook_bad_secret")
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body") from None

    job_name, build_number, result = _extract_build(payload)
    if not job_name or build_number is None:
        log.info(event="webhook_skipped", reason="missing job_name/build_number")
        return JSONResponse(
            {"status": "skipped", "reason": "missing job_name/build_number"},
            status_code=200,
        )

    if not isinstance(build_number, int):
        try:
            build_number = int(build_number)
        except (TypeError, ValueError):
            log.info(event="webhook_skipped", reason="non-integer build_number")
            return JSONResponse(
                {"status": "skipped", "reason": "non-integer build_number"},
                status_code=200,
            )

    # Only investigate failed builds. Treat anything other than SUCCESS as a
    # failure worth analyzing — covers FAILURE, UNSTABLE, ABORTED, NOT_BUILT.
    normalized_result = (result or "").upper()
    if normalized_result == "SUCCESS":
        log.info(event="webhook_skipped", reason="build_succeeded", job_name=job_name)
        return JSONResponse(
            {"status": "skipped", "reason": "build succeeded"}, status_code=200
        )

    log.info(
        event="webhook_accepted",
        job_name=job_name,
        build_number=build_number,
        result=normalized_result or "<unknown>",
    )
    background_tasks.add_task(_trigger_openclaw_infra, job_name, build_number)
    return JSONResponse(
        {"status": "accepted", "job_name": job_name, "build_number": build_number},
        status_code=200,
    )


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "mcp_server.webhook_jenkins:app",
        host="0.0.0.0",
        port=8001,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
