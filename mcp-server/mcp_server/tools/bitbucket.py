"""Bitbucket Cloud REST API v2.0 tools."""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..guardrails import GuardrailError, get_guardrails
from ..logging import get_logger

log = get_logger("tools.bitbucket")

API_ROOT = "https://api.bitbucket.org/2.0"


# ---------- Input models -----------------------------------------------------


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class BitbucketListFilesInput(_StrictModel):
    repo_slug: str = Field(..., min_length=1, max_length=128)
    branch: str = Field(..., min_length=1, max_length=128)
    directory_path: str = Field(..., min_length=1, max_length=512)


class BitbucketReadFileInput(_StrictModel):
    repo_slug: str = Field(..., min_length=1, max_length=128)
    branch: str = Field(..., min_length=1, max_length=128)
    file_path: str = Field(..., min_length=1, max_length=512)


class BitbucketCreateBranchInput(_StrictModel):
    repo_slug: str = Field(..., min_length=1, max_length=128)
    branch_name: str = Field(..., min_length=1, max_length=128)
    source_branch: str = Field(..., min_length=1, max_length=128)


class BitbucketCommitFileInput(_StrictModel):
    repo_slug: str = Field(..., min_length=1, max_length=128)
    branch: str = Field(..., min_length=1, max_length=128)
    file_path: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=0, max_length=1_000_000)
    commit_message: str = Field(..., min_length=1, max_length=2000)


class BitbucketCreatePRInput(_StrictModel):
    repo_slug: str = Field(..., min_length=1, max_length=128)
    source_branch: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=0, max_length=20_000)


# ---------- Helpers ----------------------------------------------------------


def _structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


def _auth_header() -> dict[str, str]:
    s = get_settings()
    token = base64.b64encode(f"{s.bitbucket_username}:{s.bitbucket_app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _repo_url(repo_slug: str) -> str:
    s = get_settings()
    return f"{API_ROOT}/repositories/{s.bitbucket_workspace}/{repo_slug}"


# ---------- Tool: list files -------------------------------------------------


async def bitbucket_list_files(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "bitbucket_list_files"
    start = time.perf_counter()
    try:
        payload = BitbucketListFilesInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_guardrails()
    try:
        guards.enforce_allowed_repo(payload.repo_slug)
        normalized = guards.enforce_allowed_path(payload.directory_path)
    except GuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", repo_slug=payload.repo_slug, path=normalized)

    url = f"{_repo_url(payload.repo_slug)}/src/{payload.branch}/{normalized}"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_auth_header()) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return _structured_error("not_found", "Directory not found", path=normalized)
        if resp.status_code >= 400:
            return _structured_error(
                "bitbucket_api_error",
                f"Bitbucket returned {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json()
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Bitbucket API")

    entries = [
        {
            "path": entry.get("path"),
            "type": entry.get("type"),
            "size": entry.get("size"),
        }
        for entry in data.get("values", [])
    ]
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", count=len(entries), duration_ms=duration_ms)
    return {"ok": True, "directory_path": normalized, "branch": payload.branch, "entries": entries}


# ---------- Tool: read file --------------------------------------------------


async def bitbucket_read_file(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "bitbucket_read_file"
    start = time.perf_counter()
    try:
        payload = BitbucketReadFileInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_guardrails()
    try:
        guards.enforce_allowed_repo(payload.repo_slug)
        normalized = guards.enforce_allowed_path(payload.file_path)
    except GuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", repo_slug=payload.repo_slug, path=normalized)

    url = f"{_repo_url(payload.repo_slug)}/src/{payload.branch}/{normalized}"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_auth_header()) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return _structured_error("not_found", "File not found", path=normalized)
        if resp.status_code >= 400:
            return _structured_error(
                "bitbucket_api_error",
                f"Bitbucket returned {resp.status_code}",
                status_code=resp.status_code,
            )
        content = resp.text
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Bitbucket API")

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(tool=tool, event="success", bytes=len(content), duration_ms=duration_ms)
    return {
        "ok": True,
        "file_path": normalized,
        "branch": payload.branch,
        "content": content,
    }


# ---------- Tool: create branch ----------------------------------------------


async def bitbucket_create_branch(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "bitbucket_create_branch"
    start = time.perf_counter()
    try:
        payload = BitbucketCreateBranchInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_guardrails()
    try:
        guards.enforce_allowed_repo(payload.repo_slug)
        guards.reject_blocked_source_branch(payload.source_branch)
        guards.validate_branch_name(payload.branch_name)
    except GuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(
        tool=tool,
        event="invoke",
        branch_name=payload.branch_name,
        source_branch=payload.source_branch,
    )

    # Resolve source branch HEAD commit, then create a new branch ref pointing to it.
    base_url = _repo_url(payload.repo_slug)
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_auth_header()) as client:
            ref_resp = await client.get(
                f"{base_url}/refs/branches/{payload.source_branch}"
            )
            if ref_resp.status_code == 404:
                return _structured_error(
                    "source_branch_not_found",
                    f"Source branch {payload.source_branch!r} does not exist",
                )
            if ref_resp.status_code >= 400:
                return _structured_error(
                    "bitbucket_api_error",
                    f"Bitbucket returned {ref_resp.status_code}",
                    status_code=ref_resp.status_code,
                )
            source_hash = (ref_resp.json().get("target") or {}).get("hash")
            if not source_hash:
                return _structured_error("missing_commit_hash", "Could not resolve source branch hash")

            create_resp = await client.post(
                f"{base_url}/refs/branches",
                json={"name": payload.branch_name, "target": {"hash": source_hash}},
            )
            if create_resp.status_code in (200, 201):
                duration_ms = int((time.perf_counter() - start) * 1000)
                log.info(
                    tool=tool,
                    event="success",
                    branch_name=payload.branch_name,
                    duration_ms=duration_ms,
                )
                return {
                    "ok": True,
                    "branch_name": payload.branch_name,
                    "source_branch": payload.source_branch,
                    "source_commit": source_hash,
                }
            return _structured_error(
                "bitbucket_api_error",
                f"Branch creation failed with {create_resp.status_code}",
                status_code=create_resp.status_code,
                detail=create_resp.text[:500],
            )
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Bitbucket API")


# ---------- Tool: commit file ------------------------------------------------


async def bitbucket_commit_file(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "bitbucket_commit_file"
    start = time.perf_counter()
    try:
        payload = BitbucketCommitFileInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    guards = get_guardrails()
    try:
        guards.enforce_allowed_repo(payload.repo_slug)
        guards.reject_blocked_target_branch(payload.branch)
        normalized = guards.enforce_allowed_path(payload.file_path)
    except GuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(tool=tool, event="invoke", branch=payload.branch, path=normalized)

    url = f"{_repo_url(payload.repo_slug)}/src"
    # Single-file commit: multipart form with field name == file path.
    files = {normalized: (normalized, payload.content)}
    data = {
        "branch": payload.branch,
        "message": payload.commit_message,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_auth_header()) as client:
            resp = await client.post(url, data=data, files=files)
        if resp.status_code in (200, 201):
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.info(tool=tool, event="success", branch=payload.branch, duration_ms=duration_ms)
            return {
                "ok": True,
                "branch": payload.branch,
                "file_path": normalized,
                "commit_message": payload.commit_message,
            }
        return _structured_error(
            "bitbucket_api_error",
            f"Commit failed with {resp.status_code}",
            status_code=resp.status_code,
            detail=resp.text[:500],
        )
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Bitbucket API")


# ---------- Tool: create PR --------------------------------------------------


async def bitbucket_create_pr(raw_input: dict[str, Any]) -> dict[str, Any]:
    tool = "bitbucket_create_pr"
    start = time.perf_counter()
    try:
        payload = BitbucketCreatePRInput.model_validate(raw_input)
    except Exception as exc:
        log.warning(tool=tool, event="invalid_input", error=str(exc))
        return _structured_error("invalid_input", str(exc))

    settings = get_settings()
    guards = get_guardrails()
    destination = settings.pr_destination_branch  # always 'develop' per CLAUDE.md
    try:
        guards.enforce_allowed_repo(payload.repo_slug)
        guards.reject_blocked_source_branch(payload.source_branch)
        guards.reject_blocked_pr_destination(destination)
    except GuardrailError as exc:
        log.warning(tool=tool, event="guardrail", **exc.to_dict())
        return _structured_error(exc.code, exc.message)

    log.info(
        tool=tool,
        event="invoke",
        source_branch=payload.source_branch,
        destination=destination,
    )

    url = f"{_repo_url(payload.repo_slug)}/pullrequests"
    body = {
        "title": payload.title,
        "description": payload.description,
        "source": {"branch": {"name": payload.source_branch}},
        "destination": {"branch": {"name": destination}},
        "close_source_branch": False,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=_auth_header()) as client:
            resp = await client.post(url, json=body)
        if resp.status_code in (200, 201):
            data = resp.json()
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.info(
                tool=tool,
                event="success",
                pr_id=data.get("id"),
                duration_ms=duration_ms,
            )
            return {
                "ok": True,
                "pr_id": data.get("id"),
                "pr_url": (data.get("links") or {}).get("html", {}).get("href"),
                "source_branch": payload.source_branch,
                "destination_branch": destination,
            }
        return _structured_error(
            "bitbucket_api_error",
            f"PR creation failed with {resp.status_code}",
            status_code=resp.status_code,
            detail=resp.text[:500],
        )
    except httpx.HTTPError as exc:
        log.error(tool=tool, event="http_error", error=str(exc))
        return _structured_error("http_error", "Failed to reach Bitbucket API")


__all__ = [
    "BitbucketCommitFileInput",
    "BitbucketCreateBranchInput",
    "BitbucketCreatePRInput",
    "BitbucketListFilesInput",
    "BitbucketReadFileInput",
    "bitbucket_commit_file",
    "bitbucket_create_branch",
    "bitbucket_create_pr",
    "bitbucket_list_files",
    "bitbucket_read_file",
]
