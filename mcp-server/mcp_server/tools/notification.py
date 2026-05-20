"""Slack / Google Chat notification tools.

Two tools live here:

- ``send_notification`` — plain-text post to Slack or Google Chat. Used by
  the bug-resolver agent.
- ``gchat_send_report`` — structured RCA card (Google Chat Card v2) used by
  the infrastructure RCA agent.
"""

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


# ---------------------------------------------------------------------------
# gchat_send_report — structured RCA card for the infrastructure use case
# ---------------------------------------------------------------------------


_CONFIDENCE_COLOR = {
    "high": "#1E8E3E",    # green
    "medium": "#F9AB00",  # amber
    "low": "#D93025",     # red
}


class GchatSendReportInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    webhook_url: HttpUrl
    title: str = Field(..., min_length=1, max_length=255)
    what_failed: str = Field(..., min_length=1, max_length=4000)
    root_cause: str = Field(..., min_length=1, max_length=4000)
    affected_services: list[str] = Field(default_factory=list, max_length=64)
    proposed_fix: str = Field(..., min_length=1, max_length=4000)
    confidence_level: Literal["high", "medium", "low"]


def _build_rca_card(payload: GchatSendReportInput) -> dict[str, Any]:
    services_text = (
        ", ".join(payload.affected_services) if payload.affected_services else "_none reported_"
    )
    color = _CONFIDENCE_COLOR[payload.confidence_level]
    header_subtitle = f"Confidence: {payload.confidence_level.upper()}"
    return {
        "cardsV2": [
            {
                "cardId": "rca-report",
                "card": {
                    "header": {
                        "title": payload.title,
                        "subtitle": header_subtitle,
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "header": "What failed",
                            "widgets": [{"textParagraph": {"text": payload.what_failed}}],
                        },
                        {
                            "header": "Root cause",
                            "widgets": [{"textParagraph": {"text": payload.root_cause}}],
                        },
                        {
                            "header": "Affected services",
                            "widgets": [{"textParagraph": {"text": services_text}}],
                        },
                        {
                            "header": "Proposed fix",
                            "widgets": [{"textParagraph": {"text": payload.proposed_fix}}],
                        },
                        {
                            "header": "Status",
                            "widgets": [
                                {
                                    "decoratedText": {
                                        "topLabel": "Confidence",
                                        "text": f"<font color=\"{color}\"><b>{payload.confidence_level.upper()}</b></font>",
                                    }
                                }
                            ],
                        },
                    ],
                },
            }
        ]
    }


async def gchat_send_report(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "gchat_send_report"
    start = time.perf_counter()
    try:
        payload = GchatSendReportInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    url_str = str(payload.webhook_url)
    if not url_str.startswith("https://"):
        return _structured_error("insecure_webhook", "Webhook URL must use https://")

    log.info(tool=tool, event="invoke", confidence=payload.confidence_level)
    card = _build_rca_card(payload)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url_str, json=card)
        if resp.status_code >= 400:
            return _structured_error(
                "webhook_error",
                f"Webhook returned {resp.status_code}",
                status_code=resp.status_code,
            )
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to deliver RCA report")

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", duration_ms=duration_ms)
    return {"ok": True, "confidence": payload.confidence_level}


__all__ = [
    "GchatSendReportInput",
    "SendNotificationInput",
    "gchat_send_report",
    "send_notification",
]
