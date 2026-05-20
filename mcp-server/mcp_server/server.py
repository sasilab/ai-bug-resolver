"""MCP server entrypoint — exposes Jira, Bitbucket, and notification tools.

Runs over stdio for direct MCP clients. The webhook handler (webhook.py) is a
separate HTTP service that triggers OpenClaw — they are deployed together in
the same container.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .logging import configure_logging, get_logger
from .tools.bitbucket import (
    BitbucketCommitFileInput,
    BitbucketCreateBranchInput,
    BitbucketCreatePRInput,
    BitbucketListFilesInput,
    BitbucketReadFileInput,
    bitbucket_commit_file,
    bitbucket_create_branch,
    bitbucket_create_pr,
    bitbucket_list_files,
    bitbucket_read_file,
)
from .tools.jira import JiraGetIssueInput, jira_get_issue
from .tools.notification import SendNotificationInput, send_notification

configure_logging()
log = get_logger("server")

app: Server = Server("ai-bug-resolver-mcp")


_TOOL_REGISTRY: dict[str, tuple[Any, type]] = {
    "jira_get_issue": (jira_get_issue, JiraGetIssueInput),
    "bitbucket_list_files": (bitbucket_list_files, BitbucketListFilesInput),
    "bitbucket_read_file": (bitbucket_read_file, BitbucketReadFileInput),
    "bitbucket_create_branch": (bitbucket_create_branch, BitbucketCreateBranchInput),
    "bitbucket_commit_file": (bitbucket_commit_file, BitbucketCommitFileInput),
    "bitbucket_create_pr": (bitbucket_create_pr, BitbucketCreatePRInput),
    "send_notification": (send_notification, SendNotificationInput),
}


_DESCRIPTIONS: dict[str, str] = {
    "jira_get_issue": (
        "Fetch a Jira issue's title, description, priority, labels, reporter, and comments. "
        "Only the configured Jira project is permitted."
    ),
    "bitbucket_list_files": (
        "List files in a directory of the allowed Bitbucket repository. "
        "directory_path MUST start with 'src/allowed-folder/'."
    ),
    "bitbucket_read_file": (
        "Read the text contents of a single file. "
        "file_path MUST start with 'src/allowed-folder/'."
    ),
    "bitbucket_create_branch": (
        "Create a new branch from a non-protected source branch. "
        "branch_name MUST match 'fix/<JIRA-KEY>-<short-description>'."
    ),
    "bitbucket_commit_file": (
        "Commit a single file to a non-protected branch. "
        "file_path MUST start with 'src/allowed-folder/'."
    ),
    "bitbucket_create_pr": (
        "Open a pull request. Destination is ALWAYS 'develop' — main/master are rejected."
    ),
    "send_notification": (
        "Send a formatted message to Slack or Google Chat via an https webhook URL."
    ),
}


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools: list[Tool] = []
    for name, (_, model) in _TOOL_REGISTRY.items():
        tools.append(
            Tool(
                name=name,
                description=_DESCRIPTIONS[name],
                inputSchema=model.model_json_schema(),
            )
        )
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    import json

    handler_entry = _TOOL_REGISTRY.get(name)
    if handler_entry is None:
        log.warning(event="unknown_tool", tool=name)
        result = {"ok": False, "error": "unknown_tool", "message": f"Unknown tool {name!r}"}
    else:
        handler, _model = handler_entry
        try:
            result = await handler(arguments or {})
        except Exception as exc:
            # Defense-in-depth: tools should already return structured errors,
            # but if one ever raises we must NOT leak the traceback.
            log.error(event="tool_exception", tool=name, error=str(exc))
            result = {
                "ok": False,
                "error": "internal_error",
                "message": "Tool raised an unexpected exception",
            }
    return [TextContent(type="text", text=json.dumps(result))]


async def _run() -> None:
    log.info(event="startup", transport="stdio")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
