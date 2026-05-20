# Architecture

This document describes how the AI Bug Resolver POC is wired together. Two
use cases — bug resolution and infrastructure RCA — share one custom MCP
server, one OpenClaw deployment, and one Docker Compose stack.

## Overall layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  External                                                                     │
│  ─────────                                                                    │
│  Jira Cloud      Bitbucket Cloud      Jenkins      Slack      Google Chat    │
│      │                  │                │            ▲            ▲          │
│      │ webhook          │ REST           │ webhook    │ webhook    │ webhook  │
│      ▼                  │                ▼            │            │          │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  Localhost (Docker host)                                              │   │
│  │  ───────────────────────                                              │   │
│  │                                                                       │   │
│  │   127.0.0.1:8000 ──► FastAPI: /webhook/jira  ──┐                      │   │
│  │   127.0.0.1:8001 ──► FastAPI: /webhook/jenkins ┤                      │   │
│  │                                                ▼                      │   │
│  │                                          ┌──────────┐                 │   │
│  │                                          │ OpenClaw │                 │   │
│  │                                          │  (8080)  │                 │   │
│  │                                          └────┬─────┘                 │   │
│  │                                               │ stdio MCP             │   │
│  │                                               ▼                       │   │
│  │                                          ┌──────────┐                 │   │
│  │                                          │   MCP    │ ──► Jira API    │   │
│  │                                          │  server  │ ──► Bitbucket   │   │
│  │                                          │          │ ──► Jenkins     │   │
│  │                                          │          │ ──► health URLs │   │
│  │                                          │          │ ──► metrics URLs│   │
│  │                                          │          │ ──► logs API    │   │
│  │                                          │          │ ──► Slack       │   │
│  │                                          │          │ ──► Google Chat │   │
│  │                                          └──────────┘                 │   │
│  │                                                                       │   │
│  │  All MCP-server outbound calls pass through guardrails first.        │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Use Case 1 — Bug resolution

```
Jira issue:Bug created
        │
        ▼
POST /webhook/jira?secret=...      (mcp-server, FastAPI, port 8000)
        │
        ├─ secret validated with hmac.compare_digest
        ├─ event filtered (only jira:issue_created on Bug type)
        └─ project key matches JIRA_PROJECT_KEY?
                │
                ▼  background task
        POST /v1/runs  (OpenClaw gateway)
                │
                ▼
        OpenClaw runs the "bug-resolver" agent:
          1. jira_get_issue          ──► Jira Cloud REST v3
          2. bitbucket_list_files    ──► Bitbucket Cloud REST v2
          3. bitbucket_read_file     ──► (one or more files in src/allowed-folder/)
          4. <internal analysis>
          5. bitbucket_create_branch ──► from develop, named fix/<KEY>-<desc>
          6. bitbucket_commit_file   ──► single file under src/allowed-folder/
          7. bitbucket_create_pr     ──► destination is develop (never main)
          8. send_notification       ──► Slack webhook
                │
                ▼
        Structured JSON report returned to the gateway.
```

Every step goes through `mcp_server.guardrails.Guardrails` before any
external HTTP call:

- branch name must match `^fix/[A-Z]+-[0-9]+-[a-z0-9-]+$`
- source/target branch may not be `main`, `master`, or `release/*`
- path must start with `src/allowed-folder/` and contain no `..` segments
- repo must equal `ALLOWED_REPO_SLUG`
- PR destination is locked to the configured `PR_DESTINATION_BRANCH` (`develop`)

## Use Case 2 — Infrastructure RCA

```
Jenkins build completes
        │
        ▼
POST /webhook/jenkins?secret=...   (mcp-server, FastAPI, port 8001)
        │
        ├─ secret validated with hmac.compare_digest
        ├─ payload normalized (Notification plugin OR Generic Webhook Trigger shape)
        └─ result != "SUCCESS"?
                │
                ▼  background task
        POST /v1/runs  (OpenClaw gateway, agent = "infra-rca")
                │
                ▼
        OpenClaw runs the "infra-rca" agent:
          1. jenkins_get_build_info  ──► Jenkins REST API
          2. jenkins_get_build_log   ──► /consoleText (last 500 lines)
          3. server_check_status     ──► HTTP GET on allowlisted health URL
          4. server_check_resources  ──► HTTP GET on allowlisted metrics URL
          5. server_check_services   ──► asyncio.open_connection (TCP, 5s timeout)
          6. server_read_logs        ──► HTTP GET on allowlisted logs API
          7. <internal analysis>
          8. gchat_send_report       ──► Google Chat Card v2 webhook
                │
                ▼
        Structured RCA JSON returned to the gateway.
```

Every step goes through `mcp_server.guardrails_infra.InfraGuardrails`:

- ports must be in the static allowlist (80, 443, 8080, 8443, 3000, 5000,
  5432, 3306, 6379, 27017, 9090, 9100)
- host (for HTTP probes and socket connects) must be in
  `ALLOWED_MONITORING_DOMAINS`
- log query strings are rejected if they contain shell metacharacters
- Jenkins job-name must match `^[A-Za-z0-9._/-]+$` with no `..` segments

## Component responsibilities

| Component                       | Responsibility                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------- |
| `mcp_server/server.py`          | Registers every tool, exposes them over stdio MCP. Catches stray exceptions defensively.      |
| `mcp_server/webhook.py`         | FastAPI app that receives Jira events, validates them, dispatches to OpenClaw.                |
| `mcp_server/webhook_jenkins.py` | FastAPI app that receives Jenkins events, filters to failures, dispatches to OpenClaw.        |
| `mcp_server/tools/*.py`         | One module per external system; each tool is async, returns structured dicts, logs via structlog. |
| `mcp_server/guardrails*.py`     | Pure-Python policy checks. No I/O. Tools call them before any HTTP/socket work.                |
| `mcp_server/config*.py`         | Loads env vars at import time via `python-dotenv` + `lru_cache`. Settings are frozen.          |
| `mcp_server/logging.py`         | structlog JSON output with a processor that redacts secret-shaped keys.                       |
| OpenClaw container              | The AI agent. Executes system prompts, calls MCP tools, returns reports.                      |

## MCP server ↔ external APIs

| Tool                       | Calls                                                              | Auth                              |
| -------------------------- | ------------------------------------------------------------------ | --------------------------------- |
| `jira_get_issue`           | `GET /rest/api/3/issue/{key}`                                      | Basic (email + API token)         |
| `bitbucket_list_files`     | `GET /repositories/{ws}/{repo}/src/{branch}/{path}`                | Basic (username + app password)   |
| `bitbucket_read_file`      | `GET /repositories/{ws}/{repo}/src/{branch}/{path}`                | Basic (username + app password)   |
| `bitbucket_create_branch`  | `POST /repositories/.../refs/branches`                             | Basic (username + app password)   |
| `bitbucket_commit_file`    | `POST /repositories/.../src` (multipart)                           | Basic (username + app password)   |
| `bitbucket_create_pr`      | `POST /repositories/.../pullrequests`                              | Basic (username + app password)   |
| `send_notification`        | `POST <slack-or-gchat-webhook-url>`                                | Webhook URL is the auth           |
| `jenkins_get_build_info`   | `GET /job/{name}/{n}/api/json`                                     | Basic (username + API token)      |
| `jenkins_get_build_log`    | `GET /job/{name}/{n}/consoleText`                                  | Basic (username + API token)      |
| `server_check_status`      | `GET <allowlisted url>`                                            | None (or whatever the URL needs)  |
| `server_check_resources`   | `GET <allowlisted monitoring url>`                                 | None (or token in URL)            |
| `server_check_services`    | `asyncio.open_connection(host, port)` (TCP only)                   | n/a                               |
| `server_read_logs`         | `GET <allowlisted logs url>?query=&limit=`                         | None (or token in URL)            |
| `gchat_send_report`        | `POST <google-chat-webhook>` with Card v2 body                     | Webhook URL is the auth           |

## Security boundaries

```
┌─ Public internet ─────────────────────────────────────────────────────┐
│  Jira / Bitbucket / Jenkins / Slack / Google Chat                    │
└──┬───────────────────────────────────────────────────────────────────┘
   │ (outbound only, HTTPS, app-password / API-token / webhook URL)
   ▼
┌─ mcp-server container ────────────────────────────────────────────────┐
│  • Non-root user (uid 10001)                                          │
│  • read_only is NOT set on mcp-server (it needs to write logs / tmp), │
│    but no host paths are bind-mounted except .env                     │
│  • cap_drop: ALL, no-new-privileges:true                              │
│  • Ports bound to 127.0.0.1 only — never 0.0.0.0 on the host          │
│  • Inbound: /webhook/jira (8000), /webhook/jenkins (8001)             │
│  • Outbound: only the APIs in the table above                         │
└──┬───────────────────────────────────────────────────────────────────┘
   │ internal docker network only
   ▼
┌─ openclaw container ──────────────────────────────────────────────────┐
│  • read_only rootfs                                                   │
│  • tmpfs at /tmp (64 MB)                                              │
│  • cap_drop: ALL, no-new-privileges:true                              │
│  • Only openclaw-config mounted (read-only); workspace is a named vol │
│  • Tool policy: deny-by-default. Only MCP tools enumerated in         │
│    tool-policy*.json. No shell. No browser. No filesystem outside     │
│    the workspace volume.                                              │
└───────────────────────────────────────────────────────────────────────┘
```

## What runs where

| In Docker (managed here)                | External (you provide)                |
| --------------------------------------- | ------------------------------------- |
| `mcp-server` (Python, FastAPI, stdio)   | Jira Cloud tenant + project           |
| `openclaw` (AI agent)                   | Bitbucket Cloud workspace + test repo |
|                                         | Jenkins server                        |
|                                         | Slack workspace + incoming webhook    |
|                                         | Google Chat space + webhook URL       |
|                                         | A public tunnel to your webhook ports |
|                                         | (ngrok, Cloudflare Tunnel, nginx)     |

## Why two webhook apps instead of one

Each use case has its own:

- payload shape and parsing logic,
- secret (`WEBHOOK_SECRET` vs. `JENKINS_WEBHOOK_SECRET`),
- "what to skip" logic (Bug-only filter vs. success-filter),
- consumer agent inside OpenClaw (`bug-resolver` vs. `infra-rca`).

Keeping them in separate FastAPI apps on separate ports means **one use case
can be disabled by simply not running its uvicorn process**, with zero
changes to the other.
