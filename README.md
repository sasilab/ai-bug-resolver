# AI Bug Resolver — POC

AI-powered Jira bug resolution automation. A Jira webhook fires when a Bug
issue is created, which triggers OpenClaw via a small webhook handler.
OpenClaw — armed with one custom MCP server that exposes Jira, Bitbucket, and
Slack tools — reads the issue, explores a sandboxed folder in the test repo,
proposes a fix, opens a PR against `develop`, and posts a Slack summary.

```
Jira webhook ─► mcp-server (FastAPI) ─► OpenClaw ─► mcp-server (stdio MCP)
                                                       │
                                          Jira • Bitbucket • Slack
```

Everything routes through the MCP server. OpenClaw never calls external APIs
directly, and the MCP server enforces every security guardrail on the way out.

## Prerequisites

- Python **3.12+**
- [`uv`](https://docs.astral.sh/uv/) (single binary; see install command below)
- Docker + Docker Compose
- Accounts and credentials for:
  - Jira Cloud (email + API token, one read-only project)
  - Bitbucket Cloud (bot account with App Password, scoped to one test repo)
  - Slack incoming webhook (or Google Chat webhook)
  - OpenClaw v2026.4.22+
  - An LLM provider key (Anthropic or OpenAI) for the agent

Install `uv`:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

```powershell
# 1. Clone or open this repo, then copy the env template
copy .env.example .env
# Edit .env and fill in real credentials (never commit it).

# 2. Install Python deps into ./mcp-server/.venv
cd mcp-server
uv sync --extra dev

# 3. Lint + run tests
uv run ruff check .
uv run pytest -q
```

## Running locally (without Docker)

```powershell
cd mcp-server

# Webhook (the HTTP service Jira hits)
uv run python -m mcp_server.webhook

# MCP server over stdio (what OpenClaw would launch as a child process)
uv run python -m mcp_server.server
```

The webhook listens on `http://127.0.0.1:8000/webhook/jira`.

## Running everything with Docker

```bash
docker compose up --build
```

This starts:

- **mcp-server** on `127.0.0.1:8000` (Jira webhook + FastAPI health endpoint).
  Bound to localhost only — put it behind a tunnel (ngrok, Cloudflare Tunnel,
  nginx reverse proxy) for Jira to reach it.
- **openclaw** on `127.0.0.1:8080`. Runs with `read_only` rootfs,
  `no-new-privileges`, all capabilities dropped, and only the
  `openclaw-config/` directory mounted read-only.

Both containers share an internal bridge network; OpenClaw reaches the MCP
server at `http://mcp-server:8000`.

## Configuring the Jira webhook

In Jira Cloud, create a webhook with:

- **URL** — `https://<your-public-tunnel>/webhook/jira?secret=<WEBHOOK_SECRET>`
- **Events** — _Issue created_ only
- **JQL filter** — `project = BUG AND issuetype = Bug` (matches `JIRA_PROJECT_KEY`)

The handler rejects anything that isn't a Bug create event, and re-validates
the project key before triggering OpenClaw.

## Testing tools individually

Each tool is an async function returning a structured dict — easy to drive
from a REPL.

```powershell
cd mcp-server
uv run python
```

```python
import asyncio
from mcp_server.tools.jira import jira_get_issue
from mcp_server.tools.bitbucket import bitbucket_list_files

asyncio.run(jira_get_issue({"issue_key": "BUG-1"}))
asyncio.run(bitbucket_list_files({
    "repo_slug": "ai-bug-resolver-test",
    "branch": "develop",
    "directory_path": "src/allowed-folder/",
}))
```

Errors come back as `{"ok": False, "error": "<code>", "message": "..."}` —
tracebacks never leak to the caller.

## Test suite

```powershell
cd mcp-server
uv run pytest -q
```

Tests cover:

- **`test_guardrails.py`** — branch blocklist (`main`, `master`, `release/*`),
  path allowlist, branch-name regex, repo allowlist.
- **`test_tools.py`** — input validation, guardrail enforcement, and the
  happy path for Jira / Bitbucket PR creation / Slack notification (HTTP
  calls are mocked with `respx`).

## Security guardrails (enforced in `mcp_server/guardrails.py`)

| Rule                                                          | Enforced by                            |
| ------------------------------------------------------------- | -------------------------------------- |
| No branches off `main`/`master`/`release/*`                   | `reject_blocked_source_branch`         |
| No commits to `main`/`master`/`release/*`                     | `reject_blocked_target_branch`         |
| PR destination must be `develop` (never `main`/`master`)      | `reject_blocked_pr_destination`        |
| Branch name must match `fix/<PROJ>-<NUM>-<kebab-description>` | `validate_branch_name`                 |
| Files must live under `src/allowed-folder/`                   | `enforce_allowed_path`                 |
| No path traversal (`..`)                                      | `enforce_allowed_path`                 |
| Only the configured repo                                      | `enforce_allowed_repo`                 |
| Webhook secrets/credentials redacted from logs                | `mcp_server/logging.py::_redact_secrets` |

A guardrail violation returns `{"ok": False, "error": "<code>", ...}` — the
agent is expected to surface it in its final report rather than retry.

## Folder structure

```
ai-bug-resolver/
├── mcp-server/
│   ├── pyproject.toml
│   ├── mcp_server/
│   │   ├── __init__.py
│   │   ├── server.py          # MCP stdio server, registers all tools
│   │   ├── webhook.py         # FastAPI webhook handler for Jira
│   │   ├── config.py          # Settings from env vars
│   │   ├── guardrails.py      # Centralized security rules
│   │   ├── logging.py         # structlog JSON setup with secret redaction
│   │   └── tools/
│   │       ├── jira.py
│   │       ├── bitbucket.py
│   │       └── notification.py
│   └── tests/
│       ├── conftest.py
│       ├── test_guardrails.py
│       └── test_tools.py
├── openclaw-config/
│   ├── openclaw.json          # OpenClaw config: model + MCP server URL
│   ├── system-prompt.md       # Bug-resolution workflow instructions
│   └── tool-policy.json       # strict: only our MCP tools
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── CLAUDE.md
└── README.md
```

## MVP scope

- One Jira project (`JIRA_PROJECT_KEY`)
- One Bitbucket test repo (`ai-bug-resolver-test`)
- One allowed folder (`src/allowed-folder/`)
- Single-file commits only
- Branch naming: `fix/<JIRA-KEY>-<short-kebab-description>`
- PR destination: always `develop`
