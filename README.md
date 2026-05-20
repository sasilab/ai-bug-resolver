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

## Documentation

- [CLAUDE.md](CLAUDE.md) — full project context for AI coding agents (and humans). Start here if you want to extend the project.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture, component responsibilities, and per-use-case flow diagrams.
- [docs/ADDING-TOOLS.md](docs/ADDING-TOOLS.md) — step-by-step recipe for adding a new MCP tool (with a worked example).
- [docs/SECURITY.md](docs/SECURITY.md) — consolidated security model, hardening checklist, and OpenClaw upgrade policy.
- [CONTRIBUTING.md](CONTRIBUTING.md) — fork → setup → test → PR workflow.

## Prerequisites

You'll need four command-line tools installed before you can run anything in
this repo. Follow the instructions for your operating system — each tool
takes only a minute or two.

### 1. Python 3.12+

The MCP server is written in Python. We need version **3.12 or newer**.

- **Windows** — Download the installer from
  <https://www.python.org/downloads/> and run it. **Important:** tick the
  **"Add python.exe to PATH"** checkbox on the first screen of the
  installer — without it, the `python` command won't work in your terminal.
- **macOS** — Install with Homebrew:

  ```bash
  brew install python@3.12
  ```

- **Linux (Debian/Ubuntu)** —

  ```bash
  sudo apt update && sudo apt install python3.12
  ```

### 2. uv (Python package manager)

`uv` is a fast, modern replacement for `pip` and `venv`. It manages the
project's virtual environment and dependencies in one tool.

- **Windows (PowerShell)** —

  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- **macOS / Linux** —

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

After installation you may need to open a fresh terminal so `uv` is on your
PATH.

### 3. Docker Desktop

Docker Desktop runs the full stack (MCP server + OpenClaw) in containers.
**You only need Docker if you want to run the full stack** — you can still
run tests and the standalone MCP server without it (see
["Running Without Docker"](#running-without-docker) below).

- **Windows** — Install from
  <https://docs.docker.com/desktop/setup/install/windows-install/>.
  Docker Desktop on Windows requires **WSL2** — the installer will prompt
  you to enable it if it isn't already.
- **macOS** — Install from
  <https://docs.docker.com/desktop/setup/install/mac-install/>.
- **Linux** — Install from
  <https://docs.docker.com/desktop/setup/install/linux/>.

After install, **open Docker Desktop and wait for it to finish starting**
(the whale icon in your menu/tray turns from animated to steady) before
running any `docker` commands. Then verify:

```bash
docker --version
docker compose version
```

### 4. Git

Used to clone this repository and to push changes back to your fork.

- **Windows** — Download from <https://git-scm.com/downloads/win>.
- **macOS** — Run the Xcode command-line tools installer:

  ```bash
  xcode-select --install
  ```

- **Linux (Debian/Ubuntu)** —

  ```bash
  sudo apt install git
  ```

### Quick Verify

Open a new terminal and run all five commands:

```bash
python --version
uv --version
docker --version
docker compose version
git --version
```

If all five commands return version numbers, you're ready to proceed.

> On some systems Python is installed as `python3` instead of `python` —
> if `python --version` errors but `python3 --version` works, just use
> `python3` everywhere below.

### Accounts and credentials

You also need accounts (or read-only test credentials) for:

- **Jira Cloud** — email + API token, one read-only project
- **Bitbucket Cloud** — bot account with App Password, scoped to one test repo
- **Slack incoming webhook** (or Google Chat webhook)
- **OpenClaw v2026.4.22+**
- **An LLM provider key** (Anthropic or OpenAI) for the agent

These all go in your local `.env` file (see [Setup](#setup) below).

## Running Without Docker

Docker is only required for the full **OpenClaw + MCP server** stack. If you
just want to develop, run the test suite, or hit the MCP server's HTTP
endpoints manually, you can skip Docker entirely:

```bash
cd mcp-server
uv sync
uv run pytest -q
uv run uvicorn mcp_server.webhook:app --host 127.0.0.1 --port 8000
```

That gives you the webhook handler on `http://127.0.0.1:8000` and a passing
test suite — no containers needed. Install Docker later when you're ready to
bring up OpenClaw alongside the MCP server.

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

## Use Case 2: Infrastructure RCA

In addition to bug resolution, this MCP server exposes a second toolset for
**root-cause analysis of infrastructure incidents** — failed Jenkins builds
or alerts that a service is down. The infra agent is **strictly read-only**:
it investigates Jenkins, hits health endpoints, probes service ports, and
queries a log API, then posts a structured RCA card to Google Chat. It
**never** SSHes, runs shell commands, or mutates any system.

### Architecture

```
Jenkins build fails ─► mcp-server (/webhook/jenkins) ─► OpenClaw ─► mcp-server (stdio MCP)
                                                                          │
                                              Jenkins API • health URLs • metrics URLs • logs API • Google Chat
```

The Jenkins webhook runs on `mcp_server.webhook_jenkins:app` (port `8001`
by default) — separate from the Jira webhook so the two use cases can be
deployed independently. Both apps share the same MCP server process.

### Infra tools at a glance

| Tool                      | What it does                                                    |
| ------------------------- | --------------------------------------------------------------- |
| `jenkins_get_build_info`  | Build result, duration, timestamp, parameters                   |
| `jenkins_get_build_log`   | Console log — auto-truncated to the **last 500 lines**          |
| `server_check_status`     | HTTP GET on an allowlisted URL → status code + response time    |
| `server_check_resources`  | HTTP GET on a monitoring URL (e.g. `/metrics`)                  |
| `server_check_services`   | TCP socket connect (5s timeout) to allowlisted ports            |
| `server_read_logs`        | HTTP GET on a logs API with sanitized query string              |
| `gchat_send_report`       | Structured RCA card (Google Chat Card v2)                       |

### Monitoring endpoints you need

The agent only investigates — it doesn't install anything. You provide:

- A **health endpoint** per service (any URL that returns 2xx when healthy,
  e.g. `/healthz`).
- A **metrics endpoint** per host — node_exporter on `:9100/metrics`, a
  custom JSON endpoint, or anything an HTTP GET can read.
- A **log aggregation API** (Loki, Elasticsearch, or any HTTP API that
  accepts a `query` + `limit` parameter and returns JSON or text).

Then list every hostname the agent may probe in `ALLOWED_MONITORING_DOMAINS`
(comma-separated). Anything not in the list is rejected by the guardrail
**before** any HTTP call is made.

### Configuring the Jenkins webhook

Install the Jenkins **Notification plugin** (or **Generic Webhook Trigger**)
and configure your pipeline:

- **URL** — `https://<your-public-tunnel>/webhook/jenkins?secret=<JENKINS_WEBHOOK_SECRET>`
- **Event** — Job Completed (the handler will skip anything where
  `result == "SUCCESS"`)
- **Content** — JSON; the handler accepts both the Notification plugin's
  `{"name": "...", "build": {"number": N, "result": "..."}}` shape and the
  Generic Webhook Trigger's flat `{"job_name": "...", "build_number": N, "result": "..."}`.

The handler validates the `?secret=` query parameter with `hmac.compare_digest`
and only dispatches **failed** builds to OpenClaw.

### Read-only by design

This is worth repeating because it shapes every architectural choice:

- **No SSH.** All server inspection is HTTP or TCP-connect — there is no
  channel for executing arbitrary commands on a host.
- **No shell.** OpenClaw runs with `shell.enabled = false` in
  `tool-policy-infra.json`.
- **No mutation.** The agent's `proposed_fix` is text in an RCA card. A
  human reads it and decides whether to act.
- **Allowlists everywhere.** Ports are restricted to a fixed set of common
  service ports (80, 443, 8080, 8443, 3000, 5000, 5432, 3306, 6379, 27017,
  9090, 9100). Hosts are restricted to `ALLOWED_MONITORING_DOMAINS`. Log
  queries are rejected if they contain shell metacharacters.
- **Jenkins job-name validation.** The job-name is checked against a strict
  regex (no `..`, no spaces, no shell metacharacters) to prevent path
  traversal in the Jenkins API URL.

If we ever need SSH-based checks, they go behind a separate, strict command
allowlist (no shell, no pipes, no arguments outside an enum) — they will
**not** be added to the existing tools.

### Example Google Chat RCA card

`gchat_send_report` sends a Google Chat Card v2 with five sections:

```json
{
  "cardsV2": [{
    "card": {
      "header": {
        "title": "RCA: deploy-api build #42 failed",
        "subtitle": "Confidence: MEDIUM"
      },
      "sections": [
        {"header": "What failed",        "widgets": [{"textParagraph": {"text": "Build #42 of deploy-api failed at the migrate step."}}]},
        {"header": "Root cause",         "widgets": [{"textParagraph": {"text": "Postgres on db.example.com:5432 was unreachable."}}]},
        {"header": "Affected services",  "widgets": [{"textParagraph": {"text": "deploy-api, postgres"}}]},
        {"header": "Proposed fix",       "widgets": [{"textParagraph": {"text": "Verify postgres connectivity; check security group; consider rollback."}}]},
        {"header": "Status",             "widgets": [{"decoratedText": {"topLabel": "Confidence", "text": "<font color=\"#F9AB00\"><b>MEDIUM</b></font>"}}]}
      ]
    }
  }]
}
```

The confidence color is green (`#1E8E3E`) for high, amber (`#F9AB00`) for
medium, red (`#D93025`) for low.

### Running just the Jenkins webhook locally

```bash
cd mcp-server
uv run uvicorn mcp_server.webhook_jenkins:app --host 127.0.0.1 --port 8001
```

POST to `http://127.0.0.1:8001/webhook/jenkins?secret=<JENKINS_WEBHOOK_SECRET>`
with a Jenkins-shaped payload to exercise it.

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
