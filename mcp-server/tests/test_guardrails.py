"""Unit tests for guardrails — branch blocklist, path allowlist, branch regex."""

from __future__ import annotations

import pytest

from mcp_server.guardrails import GuardrailError, get_guardrails


@pytest.fixture
def guards():
    return get_guardrails()


# ---- branch blocklist -------------------------------------------------------


@pytest.mark.parametrize("branch", ["main", "master", "release/1.0", "release/hotfix"])
def test_blocked_source_branch_rejected(guards, branch):
    with pytest.raises(GuardrailError) as exc:
        guards.reject_blocked_source_branch(branch)
    assert exc.value.code == "blocked_source_branch"


@pytest.mark.parametrize("branch", ["develop", "feature/foo", "fix/BUG-1-bar"])
def test_allowed_source_branch_passes(guards, branch):
    guards.reject_blocked_source_branch(branch)  # must not raise


@pytest.mark.parametrize("branch", ["main", "master", "release/1.2.3"])
def test_blocked_target_branch_rejected(guards, branch):
    with pytest.raises(GuardrailError) as exc:
        guards.reject_blocked_target_branch(branch)
    assert exc.value.code == "blocked_target_branch"


def test_pr_destination_must_be_develop(guards):
    guards.reject_blocked_pr_destination("develop")  # ok
    with pytest.raises(GuardrailError) as exc_main:
        guards.reject_blocked_pr_destination("main")
    assert exc_main.value.code == "blocked_pr_destination"
    with pytest.raises(GuardrailError) as exc_other:
        guards.reject_blocked_pr_destination("staging")
    assert exc_other.value.code == "invalid_pr_destination"


# ---- branch name regex ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "fix/BUG-123-null-pointer",
        "fix/AI-1-add-retry",
        "fix/PROJ-9999-the-thing-that-broke",
    ],
)
def test_valid_branch_names_accepted(guards, name):
    guards.validate_branch_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "feature/BUG-1-x",          # wrong prefix
        "fix/bug-1-x",              # lowercase project key
        "fix/BUG-1",                # missing description
        "fix/BUG-1-Has-Caps",       # uppercase in description
        "fix/BUG-1-trailing/",      # trailing slash
        "main",                     # protected name
        "fix/BUG-1- ",              # trailing space
        "fix/BUG-1-bad_underscore", # underscores not allowed
    ],
)
def test_invalid_branch_names_rejected(guards, name):
    with pytest.raises(GuardrailError):
        guards.validate_branch_name(name)


# ---- path allowlist ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/allowed-folder/foo.py",
        "src/allowed-folder/sub/dir/bar.py",
        "/src/allowed-folder/foo.py",            # leading slash normalized
        "src\\allowed-folder\\windows.py",       # backslashes normalized
    ],
)
def test_allowed_paths_accepted(guards, path):
    normalized = guards.enforce_allowed_path(path)
    assert normalized.startswith("src/allowed-folder/")


@pytest.mark.parametrize(
    "path",
    [
        "src/other-folder/foo.py",
        "etc/passwd",
        "../escape.py",
        "src/allowed-folder/../../escape.py",
        "",
    ],
)
def test_disallowed_paths_rejected(guards, path):
    with pytest.raises(GuardrailError):
        guards.enforce_allowed_path(path)


# ---- repo allowlist ---------------------------------------------------------


def test_allowed_repo_accepted(guards):
    guards.enforce_allowed_repo("ai-bug-resolver-test")


def test_other_repo_rejected(guards):
    with pytest.raises(GuardrailError) as exc:
        guards.enforce_allowed_repo("some-other-repo")
    assert exc.value.code == "repo_not_allowed"
