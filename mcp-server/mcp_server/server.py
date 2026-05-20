"""MCP server entrypoint — exposes tools for two use cases:

- **Bug resolution** — Jira, Bitbucket, plain notifications.
- **Infrastructure RCA** — Jenkins, server/monitoring probes, Google Chat
  structured reports.

Runs over stdio for direct MCP clients. The webhook handlers (``webhook.py``
for Jira, ``webhook_jenkins.py`` for Jenkins) are separate HTTP services that
trigger OpenClaw — they are deployed alongside the MCP server.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .logging import configure_logging, get_logger

# --- Use Case 1: Bug resolution tools --------------------------------------
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

# --- Use Case 2: Infrastructure RCA tools ----------------------------------
from .tools.jenkins import (
    JenkinsGetBuildInfoInput,
    JenkinsGetBuildLogInput,
    jenkins_get_build_info,
    jenkins_get_build_log,
)
from .tools.jira import JiraGetIssueInput, jira_get_issue
from .tools.notification import (
    GchatSendReportInput,
    SendNotificationInput,
    gchat_send_report,
    send_notification,
)
from .tools.server import (
    ServerCheckResourcesInput,
    ServerCheckServicesInput,
    ServerCheckStatusInput,
    ServerReadLogsInput,
    server_check_resources,
    server_check_services,
    server_check_status,
    server_read_logs,
)

configure_logging()
log = get_logger("server")

app: Server = Server("ai-bug-resolver-mcp")


_TOOL_REGISTRY: dict[str, tuple[Any, type]] = {
    # ----- Use Case 1: Bug resolution --------------------------------------
    "jira_get_issue": (jira_get_issue, JiraGetIssueInput),
    "bitbucket_list_files": (bitbucket_list_files, BitbucketListFilesInput),
    "bitbucket_read_file": (bitbucket_read_file, BitbucketReadFileInput),
    "bitbucket_create_branch": (bitbucket_create_branch, BitbucketCreateBranchInput),
    "bitbucket_commit_file": (bitbucket_commit_file, BitbucketCommitFileInput),
    "bitbucket_create_pr": (bitbucket_create_pr, BitbucketCreatePRInput),
    "send_notification": (send_notification, SendNotificationInput),
    # ----- Use Case 2: Infrastructure RCA ----------------------------------
    "jenkins_get_build_info": (jenkins_get_build_info, JenkinsGetBuildInfoInput),
    "jenkins_get_build_log": (jenkins_get_build_log, JenkinsGetBuildLogInput),
    "server_check_status": (server_check_status, ServerCheckStatusInput),
    "server_check_resources": (server_check_resources, ServerCheckResourcesInput),
    "server_check_services": (server_check_services, ServerCheckServicesInput),
    "server_read_logs": (server_read_logs, ServerReadLogsInput),
    "gchat_send_report": (gchat_send_report, GchatSendReportInput),
}


_DESCRIPTIONS: dict[str, str] = {
    # ----- Use Case 1: Bug resolution --------------------------------------
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
    # ----- Use Case 2: Infrastructure RCA ----------------------------------
    "jenkins_get_build_info": (
        "Fetch metadata about a specific Jenkins build (result, duration, timestamp, parameters)."
    ),
    "jenkins_get_build_log": (
        "Fetch the console log for a Jenkins build. Returns the last 500 lines to stay within "
        "context limits."
    ),
    "server_check_status": (
        "Issue an HTTP GET against a health-check URL and report status code + response time. "
        "The host must be on the ALLOWED_MONITORING_DOMAINS allowlist."
    ),
    "server_check_resources": (
        "Fetch a lightweight monitoring endpoint (e.g. node_exporter /metrics) and return the "
        "raw body. Host must be on the monitoring allowlist."
    ),
    "server_check_services": (
        "TCP-connect to a list of ports on an allowed host with a 5s timeout. Ports must be on "
        "the static port allowlist (HTTP/HTTPS/DB/cache/metrics ports only)."
    ),
    "server_read_logs": (
        "Query a log aggregation API (e.g. Loki). Query string is rejected if it contains shell "
        "metacharacters. The endpoint host must be on the monitoring allowlist."
    ),
    "gchat_send_report": (
        "Post a structured RCA card to Google Chat: What Failed, Root Cause, Affected Services, "
        "Proposed Fix, Confidence Level."
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
