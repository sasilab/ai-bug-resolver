# AI Bug Resolver — CLAUDE.md

This file is read by Claude Code (and other AI coding assistants) at the start
of every session. It is the single best place to learn what this project is,
how it's wired together, and how to safely extend it.

## Project Overview

A multi-use **AI ops toolkit**. Two use cases share one custom MCP server:

1. **Bug resolution** — A Jira "Bug" ticket fires a webhook → OpenClaw analyzes
   the issue → opens a Bitbucket branch, commits a fix, creates a PR against
   `develop` → notifies Slack.
2. **Infrastructure RCA** — A Jenkins build failure (or server-down alert)
   fires a webhook → OpenClaw investigates via read-only HTTP/TCP probes →
   posts a structured RCA card to Google Chat.

Both flows run inside the same Docker Compose stack, share the same MCP server
process, and obey the same security model (guardrails enforced in code, not in
prompts).

## Architecture

- **OpenClaw** is the AI agent — local-first, MCP-native, open-source. It is
  the *only* component that calls the MCP server.
- **One custom MCP server** (Python, stdio transport) exposes every tool for
  both use cases. Tools are grouped by use case in `server.py`.
- **Two FastAPI webhook handlers** run alongside the MCP server in the same
  container, on different ports (`8000` for Jira, `8001` for Jenkins). They
  validate a shared secret, filter events, and hand off to OpenClaw's
  Gateway API as a background task.
- **No n8n, no Zapier, no third-party glue.** OpenClaw handles orchestration
  itself by following the system prompts.
- **Docker Compose** runs the full stack with hardened defaults (read-only
  rootfs, no-new-privileges, all caps dropped, localhost-only ports).

```
Jira webhook   ─► mcp-server:8000 (FastAPI) ─┐
Jenkins webhook ─► mcp-server:8001 (FastAPI) ─┤
                                              ├─► OpenClaw ─► mcp-server (stdio MCP)
                                              │                    │
                                              │     Jira • Bitbucket • Slack
                                              │     Jenkins • health/metrics URLs • logs API • Google Chat
```

## Project Structure

```
ai-bug-resolver/
├── mcp-server/
│   ├── pyproject.toml                   # uv-managed deps (mcp, pydantic, httpx, structlog, fastapi)
│   ├── mcp_server/
│   │   ├── server.py                    # MCP stdio server; registers Use-Case-1 + Use-Case-2 tool groups
│   │   ├── webhook.py                   # Jira webhook (FastAPI, port 8000)
│   │   ├── webhook_jenkins.py           # Jenkins webhook (FastAPI, port 8001)
│   │   ├── config.py                    # Bug-resolver env config (Jira/Bitbucket/Slack)
│   │   ├── config_infra.py              # Infra-RCA env config (Jenkins, monitoring domains, Gchat)
│   │   ├── guardrails.py                # Bug-resolver: branch blocklist, path allowlist, traversal protection
│   │   ├── guardrails_infra.py          # Infra-RCA: port allowlist, domain allowlist, query sanitization
│   │   ├── logging.py                   # structlog JSON setup with deep secret redaction
│   │   └── tools/
│   │       ├── jira.py                  # jira_get_issue (Jira Cloud REST v3, ADF→text)
│   │       ├── bitbucket.py             # list_files, read_file, create_branch, commit_file, create_pr
│   │       ├── jenkins.py               # build info + console log (last 500 lines)
│   │       ├── server.py                # health checks, resource checks, port probes, log reading — NO SSH
│   │       └── notification.py          # send_notification (Slack/Gchat plain) + gchat_send_report (RCA card)
│   └── tests/
│       ├── conftest.py                  # Sets fake env vars for the bug-resolver tests
│       ├── test_guardrails.py           # Parametrized bug-resolver guardrail tests
│       ├── test_tools.py                # Bug-resolver tool tests (respx mocked HTTP)
│       ├── test_jenkins.py              # Jenkins tools + webhook + gchat_send_report tests
│       └── test_server.py               # Server probe tools + infra guardrails (respx + monkeypatch)
├── openclaw-config/
│   ├── openclaw.json                    # OpenClaw config: model + MCP server URL (HTTP transport)
│   ├── system-prompt.md                 # Bug-resolver agent workflow + hard rules
│   ├── system-prompt-infra.md           # Infra-RCA agent workflow + hard rules
│   ├── tool-policy.json                 # Bug-resolver: only 7 bug-resolver tools allowed
│   └── tool-policy-infra.json           # Infra-RCA: only 7 infra tools allowed
├── docs/
│   ├── ARCHITECTURE.md                  # Diagrams + component responsibilities
│   ├── ADDING-TOOLS.md                  # How to add a new MCP tool, step by step
│   └── SECURITY.md                      # Consolidated security model + hardening checklist
├── docker-compose.yml                   # mcp-server + openclaw, hardened
├── Dockerfile                           # python:3.12-slim, uv, non-root user
├── .env.example                         # All required + optional env vars
├── .gitignore
├── .dockerignore
├── LICENSE                              # MIT
├── README.md                            # Quick-start, prerequisites, per-use-case docs
├── CONTRIBUTING.md                      # Fork → setup → test → PR workflow
└── CLAUDE.md                            # This file
```

## Key Commands

```bash
# Install dependencies into .venv (Python 3.12+, uv >= 0.4)
cd mcp-server
uv sync                          # runtime only
uv sync --extra dev              # + pytest, respx, ruff

# Run the full test suite (must stay green at 125+ tests)
uv run pytest -q

# Lint and format
uv run ruff check .
uv run ruff check . --fix        # safe autofixes
uv run ruff format .

# Run a single webhook handler standalone (no Docker)
uv run uvicorn mcp_server.webhook:app          --host 127.0.0.1 --port 8000
uv run uvicorn mcp_server.webhook_jenkins:app  --host 127.0.0.1 --port 8001

# Run the MCP server over stdio (what OpenClaw would launch)
uv run python -m mcp_server.server

# Full stack
docker compose up --build
```

## Code Patterns

- **Every tool** uses a `pydantic.BaseModel(strict=True, extra="forbid")` input
  model. Validation failures return `{"ok": False, "error": "invalid_input", ...}`
  — they never raise to the caller.
- **Every tool** logs via `structlog` with `tool=...`, `event=...`, and
  redacts secret-shaped keys (`api_token`, `password`, `webhook_secret`, ...).
- **Every tool** wraps HTTP calls in `try / except httpx.HTTPError`, returning
  a structured error dict. Tracebacks **never** leak.
- **Guardrails are enforced in code** (`guardrails.py`, `guardrails_infra.py`),
  not in the system prompt. The agent cannot bypass them by being asked nicely.
- **Secrets** are read from environment variables via `python-dotenv` →
  `config.py` / `config_infra.py`. Never hardcode.
- **Tests** mock external HTTP with `respx` (`@respx.mock`) and external
  sockets with `monkeypatch` against `asyncio.open_connection`. They run
  fully offline.
- **Two webhook apps** live in `webhook.py` and `webhook_jenkins.py` — they
  share the MCP server process but each binds to its own port. Don't merge
  them: keeping them separate means one use case can be deployed without the
  other.
- **`server.py`** (the MCP entrypoint) registers tools in two grouped dicts
  `_TOOL_REGISTRY` and `_DESCRIPTIONS` — keep the `# Use Case N` comments
  intact when you add to either.

## Known Caveats

- **Strict shell-metachar guardrail blocks LogQL/Lucene syntax.**
  `guardrails_infra.py::_SHELL_METACHARS` rejects `(`, `)`, `[`, `]`, `{`,
  `}`, `|`, `&`, `;`, `$`, `` ` ``, `>`, `<`, `\`, `!`, newlines. That's
  intentional defense-in-depth but it means real LogQL (`{app=foo} |= "error"`)
  is rejected. The infra agent must phrase queries in plain `key=value`
  form. If you need LogQL/Lucene, narrow the set — don't remove it entirely.
- **No SSH in `tools/server.py`.** Every probe is HTTP or TCP-connect. If we
  ever need SSH, gate it behind a separate command allowlist (enum-only
  arguments, no shell interpolation) — do **not** extend the existing tools.
- **Jenkins webhook accepts two payload shapes:** the official "Notification
  plugin" (`{"name": "...", "build": {...}}`) and the "Generic Webhook
  Trigger" plugin (flat top-level fields). `_extract_build` normalizes both.
- **Build log truncation is last-N-lines.** `jenkins_get_build_log` returns
  the trailing `max_log_lines` (default 500) — the start of the log is
  dropped. That's fine for failures (interesting bit is at the end) but
  surprising for successful long builds.
- **`config_infra.py` does not `_require` anything.** All infra env vars have
  defaults (empty strings / empty tuples) so import never fails. Missing vars
  surface as `{"ok": False, "error": "jenkins_not_configured"}` etc. at
  tool-call time. This lets the bug-resolver tests run without infra env.
- **PR destination is always `develop`** for the bug resolver — `main` /
  `master` are hard-rejected. Branches named `release/*` are also blocked
  from being committed to or branched from.

## Security Model

| Layer        | Hardening                                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| Docker       | `read_only` rootfs, `no-new-privileges`, all caps dropped, ports bound to `127.0.0.1` only.            |
| OpenClaw     | Deny-by-default tool policies (`tool-policy*.json`); no shell, no browser, no filesystem, no third-party skills. |
| MCP server   | pydantic input validation; guardrails reject bad branches, paths, repos, ports, hosts, queries.        |
| Webhooks     | Shared-secret query param verified with `hmac.compare_digest`; only configured event types accepted.   |
| Credentials  | Env vars only, never hardcoded; `structlog` processor redacts secret-shaped keys in JSON logs.         |
| Bug resolver | Never branch off, commit to, or open PRs against `main`/`master`/`release/*`. PR destination locked to `develop`. |
| Infra RCA    | No SSH. No shell. No mutation. All probes are read-only and constrained by port + domain allowlists.  |

Full hardening checklist: [docs/SECURITY.md](docs/SECURITY.md).

## How to Add a New Use Case

When this becomes a third (or fourth) use case, follow the existing pattern:

1. **Tools** — Add new files under `mcp_server/tools/` (e.g.
   `mcp_server/tools/<name>.py`). Each tool: pydantic input model,
   structlog logging, structured error dicts, guardrail calls.
2. **Guardrails** — Create `mcp_server/guardrails_<name>.py`. Keep it
   separate from the existing guardrail modules so policies don't bleed.
3. **Config** — Create `mcp_server/config_<name>.py`. Use lazy defaults
   (no `_require`) so the new module is independently optional.
4. **Register tools** — In `mcp_server/server.py`, add a new
   `# Use Case N: <name>` group inside `_TOOL_REGISTRY` and `_DESCRIPTIONS`.
   Don't reorder or rename existing entries.
5. **Webhook (if needed)** — Create `mcp_server/webhook_<name>.py` on its
   own port. Mirror the secret-validated FastAPI pattern from `webhook.py`.
6. **OpenClaw config** — Create `openclaw-config/system-prompt-<name>.md`
   and `openclaw-config/tool-policy-<name>.json`. Tool policy must
   deny-by-default.
7. **Env vars** — Append a new section to `.env.example` with `# Use Case N`
   header. Document each var.
8. **Tests** — Add `mcp-server/tests/test_<name>.py`. Set required env vars
   at module top *before* importing `mcp_server` modules (see existing
   `test_jenkins.py` for the pattern). Cover guardrails (parametrized) and
   tool happy paths (respx).
9. **Docs** — Update `README.md` ("Use Case N: ..." section) and add the
   new files to the file map in this CLAUDE.md.

## Further Reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Diagrams of both flows and component responsibilities.
- [docs/ADDING-TOOLS.md](docs/ADDING-TOOLS.md) — Step-by-step recipe for adding a new MCP tool.
- [docs/SECURITY.md](docs/SECURITY.md) — Consolidated security model, hardening checklist, and OpenClaw upgrade policy.
- [CONTRIBUTING.md](CONTRIBUTING.md) — Fork → setup → test → PR workflow.
- [README.md](README.md) — User-facing quickstart and per-use-case configuration.
