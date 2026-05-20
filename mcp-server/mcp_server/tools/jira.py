"""Jira Cloud REST API v3 tools."""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..guardrails import GuardrailError
from ..logging import get_logger

log = get_logger("tools.jira")


# ---------- Input models -----------------------------------------------------


class JiraGetIssueInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    issue_key: str = Field(
        ...,
        description="Jira issue key, e.g. BUG-123",
        pattern=r"^[A-Z][A-Z0-9_]+-[0-9]+$",
        min_length=3,
        max_length=64,
    )


# ---------- Helpers ----------------------------------------------------------


def _adf_to_text(node: Any) -> str:
    """Convert Atlassian Document Format (ADF) JSON to plain text (best effort)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "text":
            return node.get("text", "")
        if node_type == "hardBreak":
            return "\n"
        content = node.get("content", [])
        text = _adf_to_text(content)
        if node_type in ("paragraph", "heading", "listItem", "blockquote"):
            return text + "\n"
        return text
    return ""


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


# ---------- Tool -------------------------------------------------------------


async def jira_get_issue(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Fetch a Jira issue's summary, description, priority, labels, reporter, comments."""
    tool = "jira_get_issue"
    start = time.perf_counter()

    try:
        payload = JiraGetIssueInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    settings = get_settings()

    # MVP scope: only one Jira project. Enforce here.
    if not payload.issue_key.startswith(f"{settings.jira_project_key}-"):
        err = _structured_error(
            "project_not_allowed",
            f"Only project {settings.jira_project_key!r} is allowed.",
        )
        log.warning(tool=tool, event="guardrail", **err)
        return err

    log.info(tool=tool, event="invoke", issue_key=payload.issue_key)

    url = f"{settings.jira_base_url}/rest/api/3/issue/{payload.issue_key}"
    params = {"fields": "summary,description,priority,labels,reporter,comment"}
    auth = (settings.jira_email, settings.jira_api_token)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, auth=auth, params=params)
        if resp.status_code == 404:
            return _structured_error("not_found", f"Issue {payload.issue_key} not found")
        if resp.status_code >= 400:
            return _structured_error(
                "jira_api_error",
                f"Jira returned {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json()
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Jira API")

    fields = data.get("fields", {}) or {}
    reporter = fields.get("reporter") or {}
    priority = fields.get("priority") or {}
    comments_block = (fields.get("comment") or {}).get("comments") or []
    comments = [
        {
            "author": (c.get("author") or {}).get("displayName"),
            "created": c.get("created"),
            "body": _adf_to_text(c.get("body")).strip(),
        }
        for c in comments_block
    ]

    result = {
        "ok": True,
        "issue_key": data.get("key"),
        "title": fields.get("summary"),
        "description": _adf_to_text(fields.get("description")).strip(),
        "priority": priority.get("name"),
        "labels": fields.get("labels") or [],
        "reporter": reporter.get("displayName"),
        "reporter_email": reporter.get("emailAddress"),
        "comments": comments,
    }

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", issue_key=payload.issue_key, duration_ms=duration_ms)
    return result


# Re-export the GuardrailError so server.py can catch it uniformly.
__all__ = ["GuardrailError", "JiraGetIssueInput", "jira_get_issue"]
