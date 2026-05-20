# Security

This document consolidates everything you need to know to operate the AI
Bug Resolver POC safely. Read it once before deploying, and again whenever
you add a new tool or use case.

The threat model is straightforward: **the LLM is not trusted**. We assume
prompt injection, jailbreaks, and well-meaning hallucinations are all
inevitable. Every dangerous action is gated behind a Python guardrail in
the MCP server — not behind a prompt-level instruction. Prompts shape
behavior; code enforces it.

## OpenClaw hardening checklist

The OpenClaw container runs the AI agent. It is the largest blast radius
in the stack, so it gets the strictest hardening.

- [ ] **Docker — read-only rootfs.** `read_only: true` in
      `docker-compose.yml`. The agent cannot persist anywhere except the
      mounted workspace volume and `/tmp` (tmpfs, capped at 64 MB).
- [ ] **Docker — no-new-privileges.** `security_opt: [no-new-privileges:true]`.
      The container cannot escalate via setuid binaries.
- [ ] **Docker — all caps dropped.** `cap_drop: [ALL]`. No `CAP_NET_RAW`,
      no `CAP_SYS_ADMIN`. The agent cannot raw-socket or `mount`.
- [ ] **Network — localhost only.** Ports are bound `127.0.0.1:<port>` on
      the host, never `0.0.0.0`. Expose via VPN or a single reverse-proxied
      tunnel — never directly to the public internet.
- [ ] **Tool policy — deny by default.** Each `tool-policy*.json` lists
      *exactly* the MCP tools that agent may call. `default: deny`. No
      wildcards in `allow`. The `deny` list is `["*"]`.
- [ ] **No shell, browser, or filesystem.** In every tool policy:

      ```json
      "shell":      {"enabled": false},
      "browser":    {"enabled": false},
      "filesystem": {"enabled": false, "workspace_only": true, "allowed_paths": []}
      ```

- [ ] **No third-party OpenClaw skills.** Only our custom MCP server is
      registered. `skills.third_party: false`.
- [ ] **Bounded runtime.** `runtime.max_iterations: 25`,
      `max_runtime_seconds: 300`, `fail_closed: true`. A runaway agent
      stops itself.
- [ ] **One mounted config dir, read-only.** `./openclaw-config:/config:ro`
      is the only host path the container sees. No source-code mounts.
- [ ] **Workspace is a named volume, ephemeral.** `openclaw-workspace`
      survives restarts but contains no secrets and can be deleted safely.

## MCP server guardrails

The MCP server is the only thing OpenClaw talks to. Every tool routes
through code in `guardrails.py` (bug resolver) or `guardrails_infra.py`
(infra RCA) before any external HTTP or socket call.

| Policy                                                      | Module              | Enforced by                            |
| ----------------------------------------------------------- | ------------------- | -------------------------------------- |
| No branches off `main`/`master`/`release/*`                 | `guardrails.py`     | `reject_blocked_source_branch`         |
| No commits to `main`/`master`/`release/*`                   | `guardrails.py`     | `reject_blocked_target_branch`         |
| PR destination locked to `develop`                          | `guardrails.py`     | `reject_blocked_pr_destination`        |
| Branch name must match `^fix/[A-Z]+-[0-9]+-[a-z0-9-]+$`     | `guardrails.py`     | `validate_branch_name`                 |
| File paths must start with `src/allowed-folder/`            | `guardrails.py`     | `enforce_allowed_path`                 |
| No path traversal (`..`)                                    | `guardrails.py`     | `enforce_allowed_path`                 |
| Only the configured Bitbucket repo                          | `guardrails.py`     | `enforce_allowed_repo`                 |
| Only the configured Jira project                            | `tools/jira.py`     | inline check after model validation    |
| TCP probes: ports must be in the static allowlist           | `guardrails_infra.py` | `enforce_allowed_ports`              |
| HTTP probes: host must be in `ALLOWED_MONITORING_DOMAINS`   | `guardrails_infra.py` | `enforce_allowed_url` / `_host`      |
| Log queries: reject shell metacharacters                    | `guardrails_infra.py` | `sanitize_log_query`                 |
| Jenkins job names: strict regex, no `..`                    | `guardrails_infra.py` | `validate_jenkins_job_name`          |

Every guardrail violation returns `{"ok": false, "error": "<stable-code>", ...}`
to the agent. The agent is instructed (in the system prompt) to surface
these errors in its final report rather than retry. **The code does not
trust the agent to honor that** — it just keeps refusing.

## Credential management

- **Never hardcode credentials.** Every secret comes from an environment
  variable read at startup by `mcp_server/config.py` or
  `mcp_server/config_infra.py`.
- **`.env` is gitignored** (`.env*` pattern). Only `.env.example` —
  containing placeholders only — is committed.
- **Bot accounts with minimum scope.**
  - Bitbucket: a dedicated bot user with App Password scoped to the single
    test repository. No org-wide tokens.
  - Jira: an API token with read-only access to one project. No tokens
    that can edit or transition issues.
  - Jenkins: an API token scoped to the relevant folder if your Jenkins
    supports folder-level RBAC; otherwise a read-only user.
  - Slack / Google Chat: webhook URLs, which act as their own bearer.
    Rotate them if they leak.
- **Logging redaction.** `mcp_server/logging.py::_redact_secrets` replaces
  any key whose lowercase name matches `api_token`, `token`, `password`,
  `app_password`, `webhook_secret`, `authorization`, `auth`, `secret`, or
  `api_key` with `"***"` in the JSON log output. This applies recursively
  to nested dicts and lists.
- **If a credential leaks, rotate first, then audit.** Don't try to scrub
  logs after the fact — they're already gone.

## Network exposure

The Docker Compose stack only binds ports on `127.0.0.1`. To let Jira /
Jenkins reach your webhook handlers, expose them through one of:

- **A reverse proxy (nginx, Caddy)** with TLS termination, auth, and rate
  limiting. Recommended for permanent deployments.
- **Cloudflare Tunnel / ngrok / Tailscale Funnel.** Acceptable for the
  POC. Make sure the tunnel itself requires auth where supported.
- **An IP allowlist** at the proxy if you know the source CIDRs (Atlassian
  publishes its webhook IP ranges).

The `?secret=` query parameter on each webhook is an additional layer, not
a substitute for transport-level controls. **Both** should be in place.

## Webhook secrets

- Generate with a CSPRNG: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Minimum 32 random characters, ideally 48+.
- Distinct per webhook (`WEBHOOK_SECRET` ≠ `JENKINS_WEBHOOK_SECRET`).
- Compared with `hmac.compare_digest` to prevent timing attacks.

## OpenClaw upgrade policy

OpenClaw is the trust-critical component — a vulnerability in its
sandboxing or tool-policy enforcement could let prompt-injected output
escalate to real-world side effects.

- **Pin to a known-good tag** in `docker-compose.yml`
  (`openclaw/desktop:2026.4.22` at the time of this commit).
- **Subscribe to OpenClaw security advisories** via their GitHub releases
  feed.
- **Re-pin and redeploy promptly when a security release lands.** Treat
  it like any other dependency CVE.
- **Test the tool-policy enforcement after every upgrade.** Run a known
  out-of-policy request (e.g. ask the agent to invoke a non-allowlisted
  tool) and confirm it gets denied at the policy layer, not just refused
  by the model.

Reference: <https://docs.openclaw.ai/gateway/security>

## Known CVE history of OpenClaw

OpenClaw's CVE history is short but worth tracking — the project has
shipped quick fixes for prompt-injection-driven tool misuse and sandbox
escapes in the past. Specific advisories are published on the
[OpenClaw GitHub Security Advisories
page](https://docs.openclaw.ai/gateway/security). When pinning a version,
cross-check against the advisory list to make sure you are not on a
version with a published, unpatched issue.

The cost of keeping OpenClaw current is small (`docker compose pull` +
restart). The cost of *not* keeping it current is open-ended.

## Hard rules — never break

These exist in both source comments and system prompts. The MCP server
enforces them regardless of what the agent does.

- **Bug resolver — never push to or branch off `main`, `master`, or
  `release/*`.** Always target `develop`.
- **Bug resolver — never write outside `src/allowed-folder/`.**
- **Bug resolver — single-file commits only.** One branch, one file, one
  PR per run.
- **Infra RCA — never execute commands on a server.** No SSH. No shell.
  No `system()`. The agent recommends fixes; humans run them.
- **Infra RCA — never touch a host outside `ALLOWED_MONITORING_DOMAINS`.**
- **Both use cases — secrets stay in env vars.** No logging, no echoing,
  no including them in commit messages or PR descriptions.
- **Both use cases — every external call goes through a guardrail.** If
  you are adding a new tool and find yourself about to skip the guardrail
  step "just this once", you are introducing a CVE.

## Reporting a vulnerability

If you find a security issue, **do not open a public GitHub issue.** Open
a private security advisory on the repository (Security tab → Report a
vulnerability) so we can triage and roll out a fix before disclosure.
