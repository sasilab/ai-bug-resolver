"""Infrastructure RCA configuration loaded from environment variables.

Kept separate from `config.py` (bug-resolver settings) so the two use cases can
evolve independently. All fields use safe defaults — nothing is `_require`'d,
because the infra stack is optional; missing env vars surface as guardrail
errors at tool-call time, not import errors at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


# Common service ports allowed for socket connectivity checks. Anything not in
# this set is rejected by guardrails_infra.enforce_allowed_port.
_DEFAULT_ALLOWED_PORTS: tuple[int, ...] = (
    80,     # http
    443,    # https
    8080,   # alt http
    8443,   # alt https
    3000,   # common app port (node, grafana)
    5000,   # common app port (flask)
    5432,   # postgres
    3306,   # mysql
    6379,   # redis
    27017,  # mongodb
    9090,   # prometheus
    9100,   # node_exporter
)


def _csv_to_tuple(raw: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class InfraSettings:
    # Jenkins
    jenkins_url: str
    jenkins_username: str
    jenkins_api_token: str
    jenkins_webhook_secret: str

    # Google Chat
    gchat_webhook_url: str

    # Allowlists
    allowed_monitoring_domains: tuple[str, ...]
    allowed_ports: tuple[int, ...] = field(default=_DEFAULT_ALLOWED_PORTS)

    # Log fetch limits
    max_log_lines: int = 500
    max_log_query_chars: int = 1000


@lru_cache(maxsize=1)
def get_infra_settings() -> InfraSettings:
    return InfraSettings(
        jenkins_url=os.getenv("JENKINS_URL", "").rstrip("/"),
        jenkins_username=os.getenv("JENKINS_USERNAME", ""),
        jenkins_api_token=os.getenv("JENKINS_API_TOKEN", ""),
        jenkins_webhook_secret=os.getenv("JENKINS_WEBHOOK_SECRET", ""),
        gchat_webhook_url=os.getenv("GCHAT_WEBHOOK_URL", ""),
        allowed_monitoring_domains=_csv_to_tuple(os.getenv("ALLOWED_MONITORING_DOMAINS", "")),
    )


def reset_infra_settings_cache() -> None:
    """Test helper: clear the lru_cache so a test can re-load env vars."""
    get_infra_settings.cache_clear()
