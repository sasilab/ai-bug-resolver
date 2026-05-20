"""Set required env vars before mcp_server modules import.

`mcp_server.config.get_settings()` raises on missing vars — give the tests a
deterministic, fake configuration that doesn't touch any real services.
"""

from __future__ import annotations

import os

_FAKE_ENV = {
    "JIRA_BASE_URL": "https://example.atlassian.net",
    "JIRA_EMAIL": "bot@example.com",
    "JIRA_API_TOKEN": "fake-jira-token",
    "JIRA_PROJECT_KEY": "BUG",
    "BITBUCKET_WORKSPACE": "test-workspace",
    "BITBUCKET_REPO_SLUG": "ai-bug-resolver-test",
    "BITBUCKET_USERNAME": "bot",
    "BITBUCKET_APP_PASSWORD": "fake-bitbucket-password",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/x/y/z",
    "GOOGLE_CHAT_WEBHOOK_URL": "",
    "WEBHOOK_SECRET": "test-secret-12345678",
    "OPENCLAW_GATEWAY_URL": "http://openclaw:8080",
    "OPENCLAW_API_KEY": "",
    "ALLOWED_REPO_SLUG": "ai-bug-resolver-test",
    "ALLOWED_DIRECTORY": "src/allowed-folder/",
    "PR_DESTINATION_BRANCH": "develop",
    "DEFAULT_SOURCE_BRANCH": "develop",
    "LOG_LEVEL": "WARNING",
}

for k, v in _FAKE_ENV.items():
    os.environ.setdefault(k, v)
