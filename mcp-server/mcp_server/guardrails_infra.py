"""Guardrails for the infrastructure RCA tools.

These complement (do not replace) `guardrails.py`. The bug-resolver guardrails
are about Git/Jira/Bitbucket policy; these are about server/monitoring access:

- Port allowlist for socket connectivity checks.
- Domain allowlist for HTTP probes / monitoring / logs endpoints.
- Shell-metacharacter rejection for log queries.
- Strict job-name validation for Jenkins API paths (no traversal).

Every guard raises `InfraGuardrailError` with a structured reason. Tools
translate these to `{"ok": False, ...}` — tracebacks NEVER reach the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .config_infra import InfraSettings, get_infra_settings


class InfraGuardrailError(Exception):
    """Raised when an infra-RCA request violates a security/policy rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": "guardrail_violation", "code": self.code, "message": self.message}


# Characters we consider shell metacharacters. We reject any of these inside a
# log query string. This is conservative — log query languages (LogQL, Lucene)
# don't need backticks, semicolons, redirects, or process-substitution glyphs.
_SHELL_METACHARS = set("`;|&$><()[]{}\\!\n\r")

# Jenkins job names may live in folder paths like "Folder/SubFolder/job-name".
# We allow letters, digits, dot, dash, underscore, and forward slash for
# folder separators — and explicitly reject ".." anywhere.
_JENKINS_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class InfraGuardrails:
    settings: InfraSettings

    # ---- port allowlist --------------------------------------------------

    def enforce_allowed_port(self, port: int) -> None:
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise InfraGuardrailError("invalid_port", f"Port {port!r} is not a valid TCP port.")
        if port not in self.settings.allowed_ports:
            raise InfraGuardrailError(
                "port_not_allowed",
                f"Port {port} is not on the allowlist {self.settings.allowed_ports}.",
            )

    def enforce_allowed_ports(self, ports: list[int]) -> None:
        if not ports:
            raise InfraGuardrailError("empty_ports", "ports list must not be empty.")
        if len(ports) > 32:
            raise InfraGuardrailError("too_many_ports", "Cannot check more than 32 ports per call.")
        for p in ports:
            self.enforce_allowed_port(p)

    # ---- host / URL allowlist -------------------------------------------

    def _host_in_allowlist(self, host: str) -> bool:
        host = (host or "").lower()
        if not host:
            return False
        return host in self.settings.allowed_monitoring_domains

    def enforce_allowed_host(self, host: str) -> str:
        normalized = (host or "").strip().lower()
        if not normalized:
            raise InfraGuardrailError("empty_host", "host must not be empty.")
        if not self._host_in_allowlist(normalized):
            raise InfraGuardrailError(
                "host_not_allowed",
                f"Host {normalized!r} is not on the monitoring allowlist.",
            )
        return normalized

    def enforce_allowed_url(self, url: str) -> str:
        if not url:
            raise InfraGuardrailError("empty_url", "url must not be empty.")
        parsed = urlparse(str(url))
        if parsed.scheme not in ("http", "https"):
            raise InfraGuardrailError(
                "invalid_scheme",
                f"URL scheme {parsed.scheme!r} not allowed. Use http or https.",
            )
        # https-only enforcement is recommended in production but `http` is
        # permitted here so internal-only metrics endpoints can be probed.
        host = parsed.hostname or ""
        if not self._host_in_allowlist(host):
            raise InfraGuardrailError(
                "host_not_allowed",
                f"Host {host!r} is not on the monitoring allowlist.",
            )
        return str(url)

    # ---- log-query sanitization -----------------------------------------

    def sanitize_log_query(self, query: str) -> str:
        if query is None:
            raise InfraGuardrailError("empty_query", "query must not be empty.")
        if len(query) > self.settings.max_log_query_chars:
            raise InfraGuardrailError(
                "query_too_long",
                f"query exceeds {self.settings.max_log_query_chars} chars.",
            )
        bad = sorted({c for c in query if c in _SHELL_METACHARS})
        if bad:
            raise InfraGuardrailError(
                "query_metacharacter",
                f"query contains disallowed characters: {bad!r}",
            )
        return query

    # ---- Jenkins job-name validation ------------------------------------

    def validate_jenkins_job_name(self, job_name: str) -> str:
        if not job_name:
            raise InfraGuardrailError("empty_job_name", "job_name must not be empty.")
        if ".." in job_name.split("/"):
            raise InfraGuardrailError(
                "job_name_traversal", "job_name must not contain '..' segments."
            )
        if not _JENKINS_JOB_NAME_RE.match(job_name):
            raise InfraGuardrailError(
                "invalid_job_name",
                "job_name may only contain letters, digits, '.', '-', '_', '/'.",
            )
        return job_name


def get_infra_guardrails() -> InfraGuardrails:
    return InfraGuardrails(settings=get_infra_settings())
