"""Tool-level tests: verify each tool validates inputs and rejects bad input.

These tests focus on the validation + guardrail path. The actual HTTP calls
are mocked with `respx` where end-to-end execution is exercised.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_server.tools.bitbucket import (
    bitbucket_commit_file,
    bitbucket_create_branch,
    bitbucket_create_pr,
    bitbucket_list_files,
    bitbucket_read_file,
)
from mcp_server.tools.jira import jira_get_issue
from mcp_server.tools.notification import send_notification

# ---- jira_get_issue ---------------------------------------------------------


async def test_jira_get_issue_rejects_invalid_input():
    result = await jira_get_issue({"issue_key": "not-a-key"})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


async def test_jira_get_issue_rejects_other_project():
    result = await jira_get_issue({"issue_key": "OTHER-1"})
    assert result["ok"] is False
    assert result["error"] == "project_not_allowed"


@respx.mock
async def test_jira_get_issue_happy_path():
    respx.get("https://example.atlassian.net/rest/api/3/issue/BUG-123").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "BUG-123",
                "fields": {
                    "summary": "Null pointer in checkout",
                    "description": {
                        "type": "doc",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Crashes when cart is empty."}]}
                        ],
                    },
                    "priority": {"name": "High"},
                    "labels": ["checkout"],
                    "reporter": {"displayName": "Ada", "emailAddress": "ada@example.com"},
                    "comment": {"comments": []},
                },
            },
        )
    )
    result = await jira_get_issue({"issue_key": "BUG-123"})
    assert result["ok"] is True
    assert result["title"] == "Null pointer in checkout"
    assert "Crashes when cart is empty" in result["description"]
    assert result["priority"] == "High"
    assert result["labels"] == ["checkout"]


# ---- bitbucket_list_files / read_file --------------------------------------


async def test_list_files_rejects_disallowed_path():
    result = await bitbucket_list_files(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch": "develop",
            "directory_path": "etc/secrets",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "path_not_allowed"


async def test_list_files_rejects_wrong_repo():
    result = await bitbucket_list_files(
        {
            "repo_slug": "some-other-repo",
            "branch": "develop",
            "directory_path": "src/allowed-folder/",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "repo_not_allowed"


async def test_read_file_rejects_traversal():
    result = await bitbucket_read_file(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch": "develop",
            "file_path": "src/allowed-folder/../../etc/passwd",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "path_traversal"


# ---- bitbucket_create_branch -----------------------------------------------


async def test_create_branch_rejects_main_source():
    result = await bitbucket_create_branch(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch_name": "fix/BUG-1-foo",
            "source_branch": "main",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "blocked_source_branch"


async def test_create_branch_rejects_bad_name():
    result = await bitbucket_create_branch(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch_name": "feature/whatever",
            "source_branch": "develop",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_branch_name"


# ---- bitbucket_commit_file -------------------------------------------------


async def test_commit_file_rejects_main_branch():
    result = await bitbucket_commit_file(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch": "main",
            "file_path": "src/allowed-folder/foo.py",
            "content": "x = 1",
            "commit_message": "fix: x",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "blocked_target_branch"


async def test_commit_file_rejects_release_branch():
    result = await bitbucket_commit_file(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch": "release/1.0",
            "file_path": "src/allowed-folder/foo.py",
            "content": "x = 1",
            "commit_message": "fix: x",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "blocked_target_branch"


async def test_commit_file_rejects_disallowed_path():
    result = await bitbucket_commit_file(
        {
            "repo_slug": "ai-bug-resolver-test",
            "branch": "fix/BUG-1-foo",
            "file_path": "src/other/foo.py",
            "content": "x = 1",
            "commit_message": "fix: x",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "path_not_allowed"


# ---- bitbucket_create_pr ---------------------------------------------------


async def test_create_pr_rejects_main_source():
    result = await bitbucket_create_pr(
        {
            "repo_slug": "ai-bug-resolver-test",
            "source_branch": "main",
            "title": "x",
            "description": "y",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "blocked_source_branch"


@respx.mock
async def test_create_pr_happy_path():
    respx.post(
        "https://api.bitbucket.org/2.0/repositories/test-workspace/ai-bug-resolver-test/pullrequests"
    ).mock(
        return_value=httpx.Response(
            201,
            json={"id": 42, "links": {"html": {"href": "https://bitbucket.org/test/pr/42"}}},
        )
    )
    result = await bitbucket_create_pr(
        {
            "repo_slug": "ai-bug-resolver-test",
            "source_branch": "fix/BUG-1-x",
            "title": "BUG-1: fix",
            "description": "details",
        }
    )
    assert result["ok"] is True
    assert result["pr_id"] == 42
    assert result["destination_branch"] == "develop"


# ---- send_notification ------------------------------------------------------


async def test_notification_rejects_http_url():
    result = await send_notification(
        {
            "channel_type": "slack",
            "webhook_url": "http://insecure.example.com/hook",
            "message": "hello",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "insecure_webhook"


async def test_notification_rejects_unknown_channel():
    result = await send_notification(
        {
            "channel_type": "discord",
            "webhook_url": "https://example.com/hook",
            "message": "hello",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


@respx.mock
async def test_notification_happy_path():
    respx.post("https://hooks.slack.com/services/abc/def/ghi").mock(
        return_value=httpx.Response(200)
    )
    result = await send_notification(
        {
            "channel_type": "slack",
            "webhook_url": "https://hooks.slack.com/services/abc/def/ghi",
            "message": "PR opened",
        }
    )
    assert result["ok"] is True


# Avoid pytest collecting fixtures lacking event loops with default mode.
_ = pytest
