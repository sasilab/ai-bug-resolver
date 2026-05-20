# Contributing

Thanks for considering a contribution! This is a small POC, so we keep the
workflow simple.

## 1. Fork and clone

1. Click **Fork** on the repository page.
2. Clone your fork:

   ```bash
   git clone https://github.com/<your-username>/ai-bug-resolver.git
   cd ai-bug-resolver
   ```

3. Add the upstream remote so you can keep your fork in sync:

   ```bash
   git remote add upstream https://github.com/<original-owner>/ai-bug-resolver.git
   ```

## 2. Set up your dev environment

You need **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

```bash
cd mcp-server
uv sync --extra dev
```

`uv sync` creates `mcp-server/.venv/` and installs both runtime and dev
dependencies (pytest, ruff, respx).

Copy the env template — never commit your real `.env`:

```bash
cp .env.example .env
# edit .env with placeholder or test credentials
```

## 3. Run the tests

```bash
cd mcp-server
uv run pytest -q
```

All tests must pass before you open a PR. If you add a new tool or guardrail,
add a corresponding test in `mcp-server/tests/`.

## 4. Code style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
cd mcp-server
uv run ruff check .          # lint
uv run ruff check . --fix    # auto-fix what's safe
uv run ruff format .         # format
```

CI will reject changes that don't pass `ruff check`.

## 5. Submit a pull request

1. Create a branch off `develop`:

   ```bash
   git checkout -b fix/short-description develop
   ```

2. Make focused commits. Keep each PR small — one logical change per PR.
3. Push to your fork and open a PR against `develop` on the upstream repo.
4. In the PR description, include:
   - What the change does and **why**.
   - How you tested it (`uv run pytest -q` output is fine for unit tests).
   - Any new env vars or guardrails.

A maintainer will review and either merge, request changes, or ask questions.

## Security: never commit secrets

- Never commit a real `.env`, API token, App Password, webhook URL, or
  OpenClaw API key. `.gitignore` already blocks `.env*` — leave it that way.
- If you accidentally commit a secret, rotate it immediately and force-push
  a cleaned history; then tell us in the PR so we can audit.
- Real credentials only belong in your local `.env` or in your CI provider's
  secrets manager.

## Reporting bugs

Open a GitHub issue with:

- What you expected to happen.
- What actually happened (include logs with secrets redacted).
- Steps to reproduce.
- Versions: Python, `uv`, Docker, and the commit SHA you're on.

Thanks again — we appreciate the help!
