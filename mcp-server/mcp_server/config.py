"""Configuration loaded from environment variables.

Centralizing this here makes it easy to reason about allowlists and credentials
in one place. Everything here is read at import time after `load_dotenv()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # Jira
    jira_base_url: str
    jira_email: str
    jira_api_token: str
    jira_project_key: str

    # Bitbucket
    bitbucket_workspace: str
    bitbucket_repo_slug: str
    bitbucket_username: str
    bitbucket_app_password: str

    # Notifications
    slack_webhook_url: str
    google_chat_webhook_url: str

    # Webhook + OpenClaw
    webhook_secret: str
    openclaw_gateway_url: str
    openclaw_api_key: str

    # Allowlists / policy
    allowed_repo_slug: str
    allowed_directory: str
    pr_destination_branch: str
    default_source_branch: str

    # Logging
    log_level: str

    # Derived constants
    blocked_branches: tuple[str, ...] = field(
        default=("main", "master")
    )
    blocked_branch_prefixes: tuple[str, ...] = field(
        default=("release/",)
    )
    branch_name_regex: str = r"^fix/[A-Z]+-[0-9]+-[a-z0-9-]+$"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        jira_base_url=_require("JIRA_BASE_URL").rstrip("/"),
        jira_email=_require("JIRA_EMAIL"),
        jira_api_token=_require("JIRA_API_TOKEN"),
        jira_project_key=_require("JIRA_PROJECT_KEY"),
        bitbucket_workspace=_require("BITBUCKET_WORKSPACE"),
        bitbucket_repo_slug=_require("BITBUCKET_REPO_SLUG"),
        bitbucket_username=_require("BITBUCKET_USERNAME"),
        bitbucket_app_password=_require("BITBUCKET_APP_PASSWORD"),
        slack_webhook_url=_optional("SLACK_WEBHOOK_URL"),
        google_chat_webhook_url=_optional("GOOGLE_CHAT_WEBHOOK_URL"),
        webhook_secret=_require("WEBHOOK_SECRET"),
        openclaw_gateway_url=_optional("OPENCLAW_GATEWAY_URL", "http://openclaw:8080").rstrip("/"),
        openclaw_api_key=_optional("OPENCLAW_API_KEY"),
        allowed_repo_slug=_optional("ALLOWED_REPO_SLUG", "ai-bug-resolver-test"),
        allowed_directory=_optional("ALLOWED_DIRECTORY", "src/allowed-folder/"),
        pr_destination_branch=_optional("PR_DESTINATION_BRANCH", "develop"),
        default_source_branch=_optional("DEFAULT_SOURCE_BRANCH", "develop"),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
