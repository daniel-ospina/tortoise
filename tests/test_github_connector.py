"""Tests for GitHub connector — issue/PR mapping + webhook + polling."""
from __future__ import annotations

import json
import pytest
from tortoise.connectors.github import GitHubConnector, _verify_sig


# ── Issue mapping ─────────────────────────────────────────────────

def test_issue_to_event_closed():
    gh = GitHubConnector(config={"repo": "test/repo", "state": "closed", "limit": 5})
    issue = {
        "number": 42,
        "title": "Fix login bug",
        "state": "closed",
        "createdAt": "2026-07-10T10:00:00Z",
        "closedAt": "2026-07-19T12:00:00Z",
        "url": "https://github.com/test/repo/issues/42",
    }
    ev = gh._issue_to_event(issue)
    assert ev is not None
    assert ev["type"] == "EventRecorded"
    assert ev["eventId"] == "github-issue-test/repo-42"
    assert ev["eventKind"] == "github.issue.closed"
    assert ev["subject"] == "issue:test/repo#42"
    assert ev["object"] == "Fix login bug"
    assert ev["startedAt"] == "2026-07-10T10:00:00Z"
    assert ev["endedAt"] == "2026-07-19T12:00:00Z"
    # #388: source metadata — per-entity url + kind (projection materializes
    # a Source node from these)
    assert ev["source"] == "github:test/repo"
    assert ev["sourceUrl"] == "https://github.com/test/repo/issues/42"
    assert ev["sourceKind"] == "github_issue"


def test_issue_to_event_open():
    gh = GitHubConnector(config={"repo": "test/repo"})
    issue = {
        "number": 1,
        "title": "New feature",
        "state": "open",
        "createdAt": "2026-07-19T09:00:00Z",
        "closedAt": "",
        "url": "...",
    }
    ev = gh._issue_to_event(issue)
    assert ev["eventKind"] == "github.issue.open"
    assert ev["endedAt"] is None
    assert ev["sourceKind"] == "github_issue"
    assert ev["sourceUrl"] == "..."


def test_issue_to_event_skips_empty_title():
    gh = GitHubConnector(config={"repo": "test/repo"})
    issue = {"number": 1, "title": "", "url": "..."}
    assert gh._issue_to_event(issue) is None


# ── PR mapping ────────────────────────────────────────────────────

def test_pr_to_event_open():
    gh = GitHubConnector(config={"repo": "test/repo"})
    pr = {
        "number": 7,
        "title": "Add auth module",
        "state": "open",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "",
        "mergedAt": "",
        "url": "...",
    }
    ev = gh._pr_to_event(pr)
    assert ev["eventId"] == "github-pr-test/repo-7"
    assert ev["eventKind"] == "github.pr.open"
    assert ev["endedAt"] is None
    # #388: PR events carry their OWN kind (was mislabeled github_issue) + url
    assert ev["sourceKind"] == "github_pr"
    assert ev["sourceUrl"] == "..."


def test_pr_to_event_merged():
    gh = GitHubConnector(config={"repo": "test/repo"})
    pr = {
        "number": 7,
        "title": "Add auth module",
        "state": "closed",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "2026-07-16T10:00:00Z",
        "mergedAt": "2026-07-16T10:00:00Z",
        "url": "...",
    }
    ev = gh._pr_to_event(pr)
    assert ev["eventKind"] == "github.pr.merged"
    assert ev["endedAt"] == "2026-07-16T10:00:00Z"
    assert ev["sourceKind"] == "github_pr"


def test_pr_to_event_closed_unmerged():
    gh = GitHubConnector(config={"repo": "test/repo"})
    pr = {
        "number": 7,
        "title": "Rejected idea",
        "state": "closed",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "2026-07-16T10:00:00Z",
        "mergedAt": "",
        "url": "...",
    }
    ev = gh._pr_to_event(pr)
    assert ev["eventKind"] == "github.pr.closed"


# ── Webhook → event mapping ───────────────────────────────────────

def test_webhook_issue_opened():
    gh = GitHubConnector(config={"repo": "org/r"})
    ev = gh._webhook_to_event("issues", {
        "action": "opened",
        "issue": {
            "number": 5,
            "title": "Webhook test",
            "created_at": "2026-07-19T01:00:00Z",
            "closed_at": None,
            "html_url": "https://gh/org/r/issues/5",
        },
    })
    assert ev["eventKind"] == "github.issue.open"
    assert ev["eventId"] == "github-issue-org/r-5"
    # #388: webhook html_url flows through to sourceUrl
    assert ev["sourceUrl"] == "https://gh/org/r/issues/5"
    assert ev["sourceKind"] == "github_issue"


def test_webhook_issue_closed():
    gh = GitHubConnector(config={"repo": "org/r"})
    ev = gh._webhook_to_event("issues", {
        "action": "closed",
        "issue": {
            "number": 5,
            "title": "Done",
            "created_at": "2026-07-18T00:00:00Z",
            "closed_at": "2026-07-19T02:00:00Z",
            "html_url": "...",
        },
    })
    assert ev["eventKind"] == "github.issue.closed"
    assert ev["endedAt"] == "2026-07-19T02:00:00Z"


def test_webhook_pr_merged():
    gh = GitHubConnector(config={"repo": "org/r"})
    ev = gh._webhook_to_event("pull_request", {
        "action": "closed",
        "pull_request": {
            "number": 10, "title": "Merged PR",
            "created_at": "2026-07-17T00:00:00Z",
            "closed_at": "2026-07-18T00:00:00Z",
            "merged_at": "2026-07-18T00:00:00Z",
            "html_url": "https://gh/org/r/pull/10",
        },
    })
    assert ev["eventKind"] == "github.pr.merged"
    assert ev["sourceKind"] == "github_pr"
    assert ev["sourceUrl"] == "https://gh/org/r/pull/10"


def test_webhook_unknown_event_returns_none():
    gh = GitHubConnector(config={"repo": "org/r"})
    assert gh._webhook_to_event("push", {}) is None


# ── Signature verification ────────────────────────────────────────

def test_verify_sig_valid():
    secret = b"mysecret"
    body = b'{"test":true}'
    import hmac, hashlib
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert _verify_sig(secret, f"sha256={sig}", body)


def test_verify_sig_invalid():
    assert not _verify_sig(b"mysecret", "sha256=bad", b'{}')
    assert not _verify_sig(b"mysecret", "", b'{}')


# ── Polling (mocked) ──────────────────────────────────────────────

def test_empty_repo_returns_empty():
    gh = GitHubConnector(config={"repo": ""})
    assert gh.poll() == []


def test_poll_calls_gh_cli(monkeypatch):
    """Verify poll() calls `gh` CLI for both issues and PRs."""
    import subprocess as sp
    calls = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        return sp.CompletedProcess(args=args, returncode=0, stdout="[]")

    monkeypatch.setattr(sp, "run", fake_run)

    gh = GitHubConnector(config={"repo": "org/repo", "state": "all", "limit": 50})
    result = gh.poll()

    # Should have called: gh issue list + gh pr list
    assert len(calls) == 2
    issue_cmd, pr_cmd = calls

    assert issue_cmd[0] == "gh"
    assert "issue" in issue_cmd
    assert "--repo" in issue_cmd
    assert "org/repo" in issue_cmd
    assert "--state" in issue_cmd and "all" in issue_cmd

    assert pr_cmd[0] == "gh"
    assert "pr" in pr_cmd
    assert "--repo" in pr_cmd
    assert "org/repo" in pr_cmd

    assert result == []


# ── Webhook server start/stop ─────────────────────────────────────

def test_webhook_disabled_when_port_zero():
    gh = GitHubConnector(config={"repo": "test/r", "webhook_port": 0})
    port = gh.start_webhook()
    assert port == 0
    assert gh._server is None


def test_webhook_server_lifecycle():
    """Start webhook server, verify it responds, then stop."""
    import urllib.request
    gh = GitHubConnector(config={
        "repo": "test/r",
        "webhook_port": 18998,
        "webhook_secret": "",
    })
    port = gh.start_webhook()
    assert port == 18998

    try:
        # Send a valid webhook payload
        payload = json.dumps({
            "action": "opened",
            "issue": {
                "number": 99, "title": "Test webhook",
                "created_at": "2026-07-19T00:00:00Z",
                "closed_at": None, "html_url": "...",
            },
        }).encode()
        req = urllib.request.Request(
            "http://localhost:18998/",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issues",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
    finally:
        gh.stop_webhook()


# ── #331: webhook double-start resource leak + swallowed exceptions ──

def test_webhook_start_is_idempotent():
    """#331: starting an already-running webhook must be a no-op — the
    second call must NOT bind a new socket (pre-fix: Address already in
    use / orphaned first server + thread leak)."""
    import urllib.request
    gh = GitHubConnector(config={
        "repo": "test/r",
        "webhook_port": 18996,
        "webhook_secret": "",
    })
    port1 = gh.start_webhook()
    server1 = gh._server
    port2 = gh.start_webhook()  # double-start — must be idempotent
    assert port1 == port2
    assert gh._server is server1, "second start must not replace the server"

    # Original socket must still be serving
    payload = json.dumps({
        "action": "opened",
        "issue": {"number": 2, "title": "t",
                  "created_at": "2026-07-19T00:00:00Z",
                  "closed_at": None, "html_url": ""},
    }).encode()
    req = urllib.request.Request(
        f"http://localhost:{port1}/", data=payload,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "issues"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
    gh.stop_webhook()

    # stop + restart must work (socket fully released)
    port3 = gh.start_webhook()
    assert port3 == port1
    gh.stop_webhook()


def test_webhook_processing_error_returns_500():
    """#331: an exception while processing a webhook must be LOGGED and
    answered with HTTP 500 — not silently dropped (client sees a closed
    connection and retries blindly)."""
    import urllib.error
    import urllib.request

    class _BoomAPI:
        def get_proj(self):
            raise RuntimeError("graph down")

    gh = GitHubConnector(config={
        "repo": "test/r",
        "webhook_port": 18997,
        "webhook_secret": "",
    })
    gh.api = _BoomAPI()
    port = gh.start_webhook()
    try:
        payload = json.dumps({
            "action": "opened",
            "issue": {"number": 1, "title": "t",
                      "created_at": "2026-07-19T00:00:00Z",
                      "closed_at": None, "html_url": ""},
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/", data=payload,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "issues"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTP 500"
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except urllib.error.URLError:
            assert False, "handler must respond with 500, not drop the connection"
    finally:
        gh.stop_webhook()
