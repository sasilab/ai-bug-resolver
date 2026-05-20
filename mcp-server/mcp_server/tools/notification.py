"""Slack / Google Chat notification tool."""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ..logging import get_logger

log = get_logger("tools.notification")


class SendNotificationInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    channel_type: Literal["slack", "google_chat"]
    webhook_url: HttpUrl
    message: str = Field(..., min_length=1, max_length=10_000)


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


def _format_payload(channel_type: str, message: str) -> dict[str, Any]:
    if channel_type == "slack":
        return {"text": message}
    # Google Chat
    return {"text": message}


async def send_notification(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "send_notification"
    start = time.perf_counter()
    try:
        payload = SendNotificationInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    # Only allow https webhooks — reject http to avoid credential leakage.
    url_str = str(payload.webhook_url)
    if not url_str.startswith("https://"):
        return _structured_error("insecure_webhook", "Webhook URL must use https://")

    log.info(tool=tool, event="invoke", channel_type=payload.channel_type)
    body = _format_payload(payload.channel_type, payload.message)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url_str, json=body)
        if resp.status_code >= 400:
            return _structured_error(
                "webhook_error",
                f"Webhook returned {resp.status_code}",
                status_code=resp.status_code,
            )
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to deliver notification")

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {"ok": True, "channel_type": payload.channel_type}


__all__ = ["SendNotificationInput", "send_notification"]
