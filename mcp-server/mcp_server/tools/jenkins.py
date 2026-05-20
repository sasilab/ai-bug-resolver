"""Jenkins build introspection tools (read-only)."""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config_infra import get_infra_settings
from ..guardrails_infra import InfraGuardrailError, get_infra_guardrails
from ..logging import get_logger

log = get_logger("tools.jenkins")


# ---------- Input models ----------------------------------------------------


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class JenkinsGetBuildInfoInput(_StrictModel):
    job_name: str = Field(..., min_length=1, max_length=255)
    build_number: int = Field(..., gt=0, le=10_000_000)


class JenkinsGetBuildLogInput(_StrictModel):
    job_name: str = Field(..., min_length=1, max_length=255)
    build_number: int = Field(..., gt=0, le=10_000_000)


# ---------- Helpers ---------------------------------------------------------


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


def _auth_header() -> dict[str, str]:
    s = get_infra_settings()
    if not s.jenkins_username or not s.jenkins_api_token:
        return {}
    token = base64.b64encode(f"{s.jenkins_username}:{s.jenkins_api_token}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _require_jenkins_url() -> str | None:
    return get_infra_settings().jenkins_url or None


def _tail_lines(text: str, n: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])


# ---------- Tool: get build info -------------------------------------------


async def jenkins_get_build_info(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "jenkins_get_build_info"
    start = time.perf_counter()
    try:
        payload = JenkinsGetBuildInfoInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    base_url = _require_jenkins_url()
    if not base_url:
        return _structured_error("jenkins_not_configured", "JENKINS_URL is not set.")

    guards = get_infra_guardrails()
    try:
        job_name = guards.validate_jenkins_job_name(payload.job_name)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", job_name=job_name, build=payload.build_number)

    url = f"{base_url}/job/{job_name}/{payload.build_number}/api/json"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_auth_header()) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return _structured_error("not_found", "Build not found")
        if resp.status_code >= 400:
            return _structured_error(
                "jenkins_api_error",
                f"Jenkins returned {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json()
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Jenkins API")

    # Pull build parameters out of the actions[] block if present.
    parameters: dict[str, Any] = {}
    for action in data.get("actions") or []:
        for p in action.get("parameters") or []:
            name = p.get("name")
            if name:
                parameters[name] = p.get("value")

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {
        "ok": True,
        "job_name": job_name,
        "build_number": payload.build_number,
        "result": data.get("result"),
        "duration": data.get("duration"),
        "timestamp": data.get("timestamp"),
        "url": data.get("url"),
        "parameters": parameters,
    }


# ---------- Tool: get build log --------------------------------------------


async def jenkins_get_build_log(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "jenkins_get_build_log"
    start = time.perf_counter()
    try:
        payload = JenkinsGetBuildLogInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    base_url = _require_jenkins_url()
    if not base_url:
        return _structured_error("jenkins_not_configured", "JENKINS_URL is not set.")

    guards = get_infra_guardrails()
    try:
        job_name = guards.validate_jenkins_job_name(payload.job_name)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    max_lines = get_infra_settings().max_log_lines
    log.info(
        tool=tool, event="invoke", job_name=job_name, build=payload.build_number, max_lines=max_lines
    )

    url = f"{base_url}/job/{job_name}/{payload.build_number}/consoleText"
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_auth_header()) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return _structured_error("not_found", "Build log not found")
        if resp.status_code >= 400:
            return _structured_error(
                "jenkins_api_error",
                f"Jenkins returned {resp.status_code}",
                status_code=resp.status_code,
            )
        full = resp.text
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Jenkins API")

    truncated = _tail_lines(full, max_lines)
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(
        tool=tool,
        event="success",
        full_bytes=len(full),
        returned_bytes=len(truncated),
        duration_ms=duration_ms,
    )
    return {
        "ok": True,
        "job_name": job_name,
        "build_number": payload.build_number,
        "lines_returned": len(truncated.splitlines()),
        "truncated": len(full.splitlines()) > max_lines,
        "console_text": truncated,
    }


__all__ = [
    "JenkinsGetBuildInfoInput",
    "JenkinsGetBuildLogInput",
    "jenkins_get_build_info",
    "jenkins_get_build_log",
]
