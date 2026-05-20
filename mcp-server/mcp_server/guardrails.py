"""Centralized guardrails enforced before any external API call.

Every guard raises `GuardrailError` with a structured reason. Tools translate
these into structured error dicts — they MUST NOT leak tracebacks to callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, get_settings


class GuardrailError(Exception):
    """Raised when a request violates a security/policy rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": "guardrail_violation", "code": self.code, "message": self.message}


@dataclass(frozen=True)
class Guardrails:
    settings: Settings

    # ---- branch policy ---------------------------------------------------

    def _is_blocked_branch(self, branch: str) -> bool:
        if branch in self.settings.blocked_branches:
            return True
        return any(branch.startswith(p) for p in self.settings.blocked_branch_prefixes)

    def reject_blocked_source_branch(self, branch: str) -> None:
        if self._is_blocked_branch(branch):
            raise GuardrailError(
                "blocked_source_branch",
                f"Source branch {branch!r} is not allowed. Pick a non-protected branch.",
            )

    def reject_blocked_target_branch(self, branch: str) -> None:
        if self._is_blocked_branch(branch):
            raise GuardrailError(
                "blocked_target_branch",
                f"Cannot commit to protected branch {branch!r}.",
            )

    def reject_blocked_pr_destination(self, destination: str) -> None:
        # PR destination must always be the configured destination (default: develop)
        # and may never be main/master.
        if destination in ("main", "master"):
            raise GuardrailError(
                "blocked_pr_destination",
                f"PR destination {destination!r} is not allowed.",
            )
        if destination != self.settings.pr_destination_branch:
            raise GuardrailError(
                "invalid_pr_destination",
                f"PR destination must be {self.settings.pr_destination_branch!r}, got {destination!r}.",
            )

    def validate_branch_name(self, branch_name: str) -> None:
        # Block protected names outright (in case regex was bypassed somehow).
        if self._is_blocked_branch(branch_name):
            raise GuardrailError(
                "blocked_branch_name",
                f"Branch name {branch_name!r} targets a protected branch.",
            )
        if not re.match(self.settings.branch_name_regex, branch_name):
            raise GuardrailError(
                "invalid_branch_name",
                "Branch name must match pattern fix/JIRA-KEY-short-description "
                "(e.g. fix/BUG-123-null-pointer).",
            )

    # ---- path policy -----------------------------------------------------

    def _normalize_path(self, raw: str) -> str:
        # Strip leading slashes, collapse repeated slashes; reject parent traversal.
        if not raw or raw.strip() == "":
            raise GuardrailError("empty_path", "Path must not be empty.")
        # Normalize backslashes (Windows-pasted paths) to forward slashes.
        normalized = raw.replace("\\", "/").lstrip("/")
        if ".." in normalized.split("/"):
            raise GuardrailError(
                "path_traversal",
                "Path traversal segments (..) are not allowed.",
            )
        return normalized

    def enforce_allowed_path(self, path: str) -> str:
        normalized = self._normalize_path(path)
        allowed = self.settings.allowed_directory
        if not normalized.startswith(allowed):
            raise GuardrailError(
                "path_not_allowed",
                f"Path must start with {allowed!r}. Got {normalized!r}.",
            )
        return normalized

    # ---- repository policy ----------------------------------------------

    def enforce_allowed_repo(self, repo_slug: str) -> None:
        if repo_slug != self.settings.allowed_repo_slug:
            raise GuardrailError(
                "repo_not_allowed",
                f"Repository {repo_slug!r} is not on the allowlist.",
            )


def get_guardrails() -> Guardrails:
    return Guardrails(settings=get_settings())
