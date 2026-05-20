"""Server / monitoring probes — strictly READ-ONLY.

No SSH and no shell commands are exposed in the MVP. Every probe goes either:
  * over HTTP (status, resources, logs) — host must be on the
    `ALLOWED_MONITORING_DOMAINS` allowlist enforced by `guardrails_infra`; or
  * via a single TCP socket connect (services) — port must be on the static
    `_DEFAULT_ALLOWED_PORTS` allowlist in `config_infra.py`.

If we ever need SSH-based checks, gate them behind a strict command allowlist
(no shell, no pipes) and a separate guardrail layer — do NOT extend these
tools to pass arbitrary commands. This module's contract is "investigate, not
mutate."
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..guardrails_infra import InfraGuardrailError, get_infra_guardrails
from ..logging import get_logger

log = get_logger("tools.server")

_HTTP_BODY_TRUNCATE = 8_000  # bytes
_LOG_DEFAULT_LIMIT = 100
_LOG_MAX_LIMIT = 1_000


# ---------- Input models ----------------------------------------------------


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ServerCheckStatusInput(_StrictModel):
    url: str = Field(..., min_length=1, max_length=2048)


class ServerCheckResourcesInput(_StrictModel):
    monitoring_url: str = Field(..., min_length=1, max_length=2048)


class ServerCheckServicesInput(_StrictModel):
    host: str = Field(..., min_length=1, max_length=255)
    ports: list[int] = Field(..., min_length=1, max_length=32)


class ServerReadLogsInput(_StrictModel):
    logs_url: str = Field(..., min_length=1, max_length=2048)
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=_LOG_DEFAULT_LIMIT, ge=1, le=_LOG_MAX_LIMIT)


# ---------- Helpers ---------------------------------------------------------


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


def _truncate(text: str, limit: int = _HTTP_BODY_TRUNCATE) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


# ---------- Tool: check status ---------------------------------------------


async def server_check_status(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "server_check_status"
    start = time.perf_counter()
    try:
        payload = ServerCheckStatusInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_infra_guardrails()
    try:
        url = guards.enforce_allowed_url(payload.url)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", url=url)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        ok = 200 <= resp.status_code < 400
        result = {
            "ok": True,
            "url": url,
            "reachable": True,
            "status_code": resp.status_code,
            "response_time_ms": elapsed_ms,
            "healthy": ok,
        }
        log.info(tool=tool, event="success", status=resp.status_code, elapsed_ms=elapsed_ms)
        return result
    except httpx.TimeoutException:
        log.warning(tool=tool, event="timeout", url=url)
        return {"ok": True, "url": url, "reachable": False, "reason": "timeout"}
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return {"ok": True, "url": url, "reachable": False, "reason": "connection_error"}
    finally:
        # Keep this log statement for outer timing reference.
        _ = int((time.perf_counter() - start) * 1000)


# ---------- Tool: check resources ------------------------------------------


async def server_check_resources(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "server_check_resources"
    start = time.perf_counter()
    try:
        payload = ServerCheckResourcesInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_infra_guardrails()
    try:
        url = guards.enforce_allowed_url(payload.monitoring_url)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", url=url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return _structured_error(
                "monitoring_endpoint_error",
                f"Monitoring endpoint returned {resp.status_code}",
                status_code=resp.status_code,
            )
        body_text = resp.text
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach monitoring endpoint")

    # Try JSON first for typed responses; fall back to text for /metrics-style.
    content_type = resp.headers.get("content-type", "")
    parsed: dict[str, Any] | None = None
    if "json" in content_type.lower():
        try:
            parsed = resp.json()
        except Exception:
            parsed = None

    body, truncated = _truncate(body_text)
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {
        "ok": True,
        "monitoring_url": url,
        "content_type": content_type,
        "data": parsed,            # may be None for non-JSON
        "raw_body": body,          # always present, possibly truncated
        "truncated": truncated,
    }


# ---------- Tool: check services -------------------------------------------


async def _probe_port(host: str, port: int, timeout: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        _ = reader  # silence linter
        return {"port": port, "reachable": True, "latency_ms": elapsed_ms}
    except TimeoutError:
        return {"port": port, "reachable": False, "reason": "timeout"}
    except OSError as exc:
        return {"port": port, "reachable": False, "reason": f"connect_error: {exc.__class__.__name__}"}


async def server_check_services(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "server_check_services"
    start = time.perf_counter()
    try:
        payload = ServerCheckServicesInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_infra_guardrails()
    try:
        host = guards.enforce_allowed_host(payload.host)
        guards.enforce_allowed_ports(payload.ports)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", host=host, ports=payload.ports)
    results = await asyncio.gather(
        *(_probe_port(host, p, timeout=5.0) for p in payload.ports)
    )
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {"ok": True, "host": host, "results": results}


# ---------- Tool: read logs ------------------------------------------------


async def server_read_logs(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "server_read_logs"
    start = time.perf_counter()
    try:
        payload = ServerReadLogsInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_infra_guardrails()
    try:
        url = guards.enforce_allowed_url(payload.logs_url)
        query = guards.sanitize_log_query(payload.query)
    except InfraGuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", url=url, limit=payload.limit)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"query": query, "limit": payload.limit})
        if resp.status_code >= 400:
            return _structured_error(
                "logs_endpoint_error",
                f"Logs endpoint returned {resp.status_code}",
                status_code=resp.status_code,
            )
        body_text = resp.text
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach logs endpoint")

    content_type = resp.headers.get("content-type", "")
    parsed: Any = None
    if "json" in content_type.lower():
        try:
            parsed = resp.json()
        except Exception:
            parsed = None

    body, truncated = _truncate(body_text)
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {
        "ok": True,
        "logs_url": url,
        "query": query,
        "limit": payload.limit,
        "content_type": content_type,
        "data": parsed,
        "raw_body": body,
        "truncated": truncated,
    }


__all__ = [
    "ServerCheckResourcesInput",
    "ServerCheckServicesInput",
    "ServerCheckStatusInput",
    "ServerReadLogsInput",
    "server_check_resources",
    "server_check_services",
    "server_check_status",
    "server_read_logs",
]
