# AI Bug Resolver — POC

## Project Overview
An AI-powered Jira bug resolution automation using OpenClaw + custom MCP server.
Flow: Jira webhook → OpenClaw → MCP Server (Jira + Bitbucket + Slack tools) → creates branch, commits fix, opens PR, sends notification.

## Tech Stack
- Python 3.12+ for the MCP server
- uv for package management and virtual environment
- mcp SDK (pip install mcp) for MCP protocol
- pydantic for input validation
- httpx for async HTTP calls to Jira/Bitbucket/Slack APIs
- Docker + docker-compose for all services
- OpenClaw (v2026.4.22+) as the AI agent
- Bitbucket Cloud REST API v2.0
- Jira Cloud REST API v3
- Slack Incoming Webhooks

## Architecture Rules
- ONE custom MCP server exposing ALL tools (Jira + Bitbucket + Slack)
- OpenClaw connects to this single MCP server
- OpenClaw is the ONLY component that calls the MCP server
- No n8n — OpenClaw handles orchestration directly
- All credentials in environment variables, never hardcoded
- All API calls go through the MCP server, never direct from OpenClaw
- Use uv for dependency management, .venv for virtual environment
- pyproject.toml for project config, NOT requirements.txt

## Security Rules — NON-NEGOTIABLE
- OpenClaw runs in Docker with: read_only filesystem, no-new-privileges, localhost-only ports
- MCP server must enforce path allowlists (only /src/allowed-folder/)
- MCP server must REJECT any branch targeting main, master, or release/*
- MCP server must REJECT any commit to main, master, or release/*
- MCP server must REJECT any PR targeting main or master
- Bitbucket bot account with minimal permissions (test repo only)
- Jira token with read-only access to single project
- No third-party OpenClaw skills — only our custom MCP server
- OpenClaw tool policy: strict, no shell access, no browser, no filesystem beyond workspace

## Code Style
- Python 3.12+ with type hints everywhere
- pydantic models for all tool inputs — strict validation
- All API calls wrapped in try/except returning structured error dicts (never tracebacks)
- Every tool must log: timestamp, tool name, input params (redacted secrets), success/failure, duration
- Use structlog for structured JSON logging
- Use httpx.AsyncClient for all HTTP calls
- Use .env.example with placeholder values, never real credentials
- Ruff for linting and formatting

## MVP Scope
- Only one Jira project
- Only one Bitbucket test repo
- Only /src/allowed-folder/ is readable/writable
- Single file commits only
- Branch naming: fix/JIRA-KEY-short-description
- PR target: develop branch only