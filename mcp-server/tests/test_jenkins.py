"""Tests for jenkins tools, the Jenkins webhook handler, and gchat_send_report.

Set infra env vars at module top BEFORE importing mcp_server modules so the
`config_infra` cache is built with our test values.
"""

from __future__ import annotations

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
from fastapi.testclient import TestClient

from mcp_server import config_infra
from mcp_server.tools.jenkins import (
    jenkins_get_build_info,
    jenkins_get_build_log,
)
from mcp_server.tools.notification import gchat_send_report

# Reset the lru_cache so the freshly-set env vars take effect.
config_infra.reset_infra_settings_cache()


# ---- jenkins_get_build_info -------------------------------------------------


async def test_jenkins_get_build_info_rejects_invalid_input():
    result = await jenkins_get_build_info({"job_name": "", "build_number": 1})
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


async def test_jenkins_get_build_info_rejects_path_traversal():
    result = await jenkins_get_build_info(
        {"job_name": "../etc/passwd", "build_number": 1}
    )
    assert result["ok"] is False
    assert result["error"] in {"invalid_job_name", "job_name_traversal"}


async def test_jenkins_get_build_info_rejects_metachars_in_name():
    result = await jenkins_get_build_info(
        {"job_name": "deploy;rm-rf", "build_number": 1}
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_job_name"


@respx.mock
async def test_jenkins_get_build_info_happy_path():
    respx.get("https://ci.example.com/job/deploy-api/42/api/json").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "FAILURE",
                "duration": 12345,
                "timestamp": 1716220800000,
                "url": "https://ci.example.com/job/deploy-api/42/",
                "actions": [
                    {"parameters": [{"name": "ENV", "value": "prod"}]},
                ],
            },
        )
    )
    result = await jenkins_get_build_info({"job_name": "deploy-api", "build_number": 42})
    assert result["ok"] is True
    assert result["result"] == "FAILURE"
    assert result["parameters"] == {"ENV": "prod"}


# ---- jenkins_get_build_log --------------------------------------------------


@respx.mock
async def test_jenkins_get_build_log_truncates_to_last_500_lines():
    huge_log = "\n".join(f"line {i}" for i in range(1, 1001))
    respx.get("https://ci.example.com/job/deploy-api/42/consoleText").mock(
        return_value=httpx.Response(200, text=huge_log)
    )
    result = await jenkins_get_build_log({"job_name": "deploy-api", "build_number": 42})
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["lines_returned"] == 500
    # Last line of the original should be present.
    assert result["console_text"].splitlines()[-1] == "line 1000"
    # And the very first lines should have been dropped.
    assert "line 1" not in result["console_text"].splitlines()


@respx.mock
async def test_jenkins_get_build_log_short_log_not_truncated():
    small_log = "build started\nbuild failed"
    respx.get("https://ci.example.com/job/deploy-api/42/consoleText").mock(
        return_value=httpx.Response(200, text=small_log)
    )
    result = await jenkins_get_build_log({"job_name": "deploy-api", "build_number": 42})
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["lines_returned"] == 2


# ---- gchat_send_report ------------------------------------------------------


async def test_gchat_send_report_rejects_http_url():
    result = await gchat_send_report(
        {
            "webhook_url": "http://chat.googleapis.com/whatever",
            "title": "RCA: disk full",
            "what_failed": "x",
            "root_cause": "y",
            "affected_services": ["app"],
            "proposed_fix": "z",
            "confidence_level": "high",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "insecure_webhook"


async def test_gchat_send_report_rejects_bad_confidence():
    result = await gchat_send_report(
        {
            "webhook_url": "https://chat.googleapis.com/whatever",
            "title": "RCA",
            "what_failed": "x",
            "root_cause": "y",
            "affected_services": ["app"],
            "proposed_fix": "z",
            "confidence_level": "definite",  # not in literal
        }
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_input"


@respx.mock
async def test_gchat_send_report_happy_path_sends_card_v2():
    captured: dict = {}

    def _capture(request):
        captured["body"] = request.content
        return httpx.Response(200)

    respx.post("https://chat.googleapis.com/whatever").mock(side_effect=_capture)
    result = await gchat_send_report(
        {
            "webhook_url": "https://chat.googleapis.com/whatever",
            "title": "RCA: deploy-api build failed",
            "what_failed": "build #42 failed at the migrate step",
            "root_cause": "DB host unreachable",
            "affected_services": ["deploy-api", "postgres"],
            "proposed_fix": "investigate postgres connectivity",
            "confidence_level": "medium",
        }
    )
    assert result["ok"] is True
    assert result["confidence"] == "medium"
    assert b"cardsV2" in captured["body"]


# ---- Jenkins webhook handler ------------------------------------------------


def test_jenkins_webhook_rejects_bad_secret():
    from mcp_server.webhook_jenkins import app

    client = TestClient(app)
    resp = client.post(
        "/webhook/jenkins?secret=wrong-secret-12345",
        json={"name": "job", "build": {"number": 1, "result": "FAILURE"}},
    )
    assert resp.status_code == 401


def test_jenkins_webhook_skips_successful_build():
    from mcp_server.webhook_jenkins import app

    client = TestClient(app)
    resp = client.post(
        "/webhook/jenkins?secret=fake-webhook-secret-12345678",
        json={"name": "deploy-api", "build": {"number": 7, "result": "SUCCESS"}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_jenkins_webhook_accepts_failed_build():
    from mcp_server.webhook_jenkins import app

    client = TestClient(app)
    resp = client.post(
        "/webhook/jenkins?secret=fake-webhook-secret-12345678",
        json={"name": "deploy-api", "build": {"number": 7, "result": "FAILURE"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["job_name"] == "deploy-api"
    assert body["build_number"] == 7


def test_jenkins_webhook_skips_missing_fields():
    from mcp_server.webhook_jenkins import app

    client = TestClient(app)
    resp = client.post(
        "/webhook/jenkins?secret=fake-webhook-secret-12345678",
        json={"foo": "bar"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


_ = pytest
