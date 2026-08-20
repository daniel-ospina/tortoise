"""Tests for GitHub connector — issue/PR mapping + webhook + polling."""
from __future__ import annotations  # noqa: I001

import json
import subprocess as sp

import pytest  # noqa: F401
from tortoise.connectors.github import GitHubConnector, _verify_sig

from tests._embedded import wipe  # noqa: E402, RUF100


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
    # #1155: canonical issue-Event id — matches the entity-path producer so
    # both converge on ONE Event node per issue (see test_producers_one_event).
    assert ev["eventId"] == "github-issue-test/repo-42-created"
    assert ev["eventKind"] == "github.issue.closed"
    assert ev["subject"] == "issue:test/repo#42"
    # #1155: object references the pm:issue Object by name (produces wiring).
    assert ev["object"] == "test/repo#42"
    assert ev["startedAt"] == "2026-07-10T10:00:00Z"
    assert ev["endedAt"] == "2026-07-19T12:00:00Z"


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
    assert ev["eventId"] == "github-issue-org/r-5-created"


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
            "html_url": "...",
        },
    })
    assert ev["eventKind"] == "github.pr.merged"


def test_webhook_unknown_event_returns_none():
    gh = GitHubConnector(config={"repo": "org/r"})
    assert gh._webhook_to_event("push", {}) is None


# ── Signature verification ────────────────────────────────────────

def test_verify_sig_valid():
    secret = b"mysecret"
    body = b'{"test":true}'
    import hmac, hashlib  # noqa: E401, I001
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
            assert False, "expected HTTP 500"  # noqa: B011
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except urllib.error.URLError:
            assert False, "handler must respond with 500, not drop the connection"  # noqa: B011
    finally:
        gh.stop_webhook()


# ── #1155: two-producer Event id collision (poll vs entity path) ─────

def test_producers_share_event_id():
    """#1155: the poll/webhook producer and the entity producer must mint the
    SAME deterministic Event id for the same issue — one issue → one Event."""
    gh = GitHubConnector(config={"repo": "test/repo"})
    issue = {
        "number": 42,
        "title": "Fix login bug",
        "state": "open",
        "createdAt": "2026-07-10T10:00:00Z",
        "closedAt": "",
        "url": "https://github.com/test/repo/issues/42",
        "labels": [{"name": "complexity:micro"}],
        "assignees": [],
        "author": {"login": "alice"},
        "milestone": None,
    }
    poll_ev = gh._issue_to_event(issue)
    entities = gh._issue_to_entities(issue)
    assert poll_ev is not None and entities is not None
    assert poll_ev["eventId"] == entities["event"]["eventId"]
    assert poll_ev["eventId"] == "github-issue-test/repo-42-created"
    # both producers point the produces edge at the SAME Object name — the
    # pm:issue Object ({repo}#{number}) — never a stub.
    assert poll_ev["object"] == entities["event"]["object"] == "test/repo#42"


def test_ingest_one_event_per_issue(shared_proj, monkeypatch):
    """#1155 regression: running the full ingest (poll path + entity path)
    must yield exactly ONE Event node per issue — no colliding ids, no
    double-counting — and the Event must produce the real pm:issue Object.

    Pre-fix: 2 Event nodes (`github-issue-{repo}-{n}` from the poll path,
    `github-issue-{repo}-{n}-created` from the entity path), the poll-path
    Event id colliding with the pm:issue Object id string.
    """
    proj = shared_proj
    if proj is None:
        return
    wipe(proj)

    issues = [{
        "number": 42, "title": "Fix login bug", "state": "open",
        "createdAt": "2026-07-10T10:00:00Z", "closedAt": "",
        "url": "https://github.com/test/repo/issues/42",
        "labels": [{"name": "complexity:micro"}],
        "assignees": [{"login": "bob"}],
        "author": {"login": "alice"}, "milestone": None,
    }]
    prs = [{
        "number": 7, "title": "Add auth module", "state": "open",
        "createdAt": "2026-07-15T08:00:00Z", "closedAt": "", "mergedAt": "",
        "url": "https://github.com/test/repo/pull/7",
    }]

    def fake_run(args, **kwargs):
        argv = list(args)
        if "pr" in argv:
            payload = prs
        elif "labels" in argv:  # poll_raw_issues (entity path)
            payload = issues
        else:  # poll issues (legacy poll path)
            payload = issues
        return sp.CompletedProcess(
            args=args, returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(sp, "run", fake_run)

    gh = GitHubConnector(config={"repo": "test/repo"})
    gh.ingest(proj)

    # Exactly ONE Event node for the issue, with the canonical id.
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-test/repo-42' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert sorted(tuple(r) for r in rows) == [
        ("github-issue-test/repo-42-created", "pm:cardCreated"),
    ]

    # No Event carries the legacy poll-path id (which collided with the
    # Object id string).
    assert proj.g.query(
        "MATCH (e:Event {eventId:'github-issue-test/repo-42'}) RETURN e"
    ).result_set == []

    # id-space is unambiguous: only the Object carries id
    # `github-issue-test/repo-42` (pre-fix: Event + Object both matched).
    rows = proj.g.query(
        "MATCH (n {id:'github-issue-test/repo-42'}) RETURN labels(n)[0]"
    ).result_set
    assert [r[0] for r in rows] == ["Object"]

    # produces edge lands on the REAL pm:issue Object (name {repo}#{number}),
    # not a name-stub with a random id.
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'github-issue-test/repo-42-created'})"
        "-[:produces]->(o:Object) RETURN o.name, o.objectKind, o.id"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("test/repo#42", "pm:issue", "github-issue-test/repo-42"),
    ]

    # aboutSubject wiring still resolves against the real Object.
    rows = proj.g.query(
        "MATCH (o:Object {id:'github-issue-test/repo-42'})"
        "-[:aboutSubject]->(s:Subject) RETURN s.id ORDER BY s.id"
    ).result_set
    assert [r[0] for r in rows] == ["github-user:alice", "github-user:bob"]

    # PR events (entity path does not cover PRs) still land.
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-pr-test/repo' "
        "RETURN e.eventId"
    ).result_set
    assert [r[0] for r in rows] == ["github-pr-test/repo-7"]

    # Idempotent: re-ingesting converges — still one Event + one Object.
    gh.ingest(proj)
    assert len(proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-test/repo-42' "
        "RETURN e").result_set) == 1
    assert len(proj.g.query(
        "MATCH (o:Object {id:'github-issue-test/repo-42'}) RETURN o"
    ).result_set) == 1


# ── Routing config (#1395) ────────────────────────────────────────

def test_routing_packaged_default_loads():
    """The packaged default (tortoise/config/routing.yaml) is the fallback."""
    gh = GitHubConnector(config={"repo": "daniel-ospina/tortoise"})
    assert gh._routing, "packaged routing default must load"
    repo_routing = gh._routing.get("repo_routing", {})
    assert repo_routing.get("daniel-ospina/tortoise", {}).get("default_team") == "epistemic-team"


def test_routing_env_override(monkeypatch, tmp_path):
    """TORTOISE_ROUTING_CONFIG points at a user file that wins."""
    custom = tmp_path / "custom-routing.yaml"
    custom.write_text(
        "repo_routing:\n  acme/product:\n    product: acme\n    default_team: acme-team\n"
    )
    monkeypatch.setenv("TORTOISE_ROUTING_CONFIG", str(custom))
    gh = GitHubConnector(config={"repo": "acme/product"})
    assert gh._routing["repo_routing"]["acme/product"]["default_team"] == "acme-team"


def test_routing_env_override_missing_file_falls_back(monkeypatch):
    """Missing override file → packaged default, no crash."""
    monkeypatch.setenv("TORTOISE_ROUTING_CONFIG", "/nonexistent/routing.yaml")
    gh = GitHubConnector(config={"repo": "daniel-ospina/tortoise"})
    assert gh._routing, "must fall back to the packaged default"
    assert gh._routing["repo_routing"]["daniel-ospina/tortoise"]["default_team"] == "epistemic-team"


def test_routing_env_override_invalid_yaml_falls_back(monkeypatch, tmp_path):
    """Invalid YAML in the override file → packaged default, no crash."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("repo_routing: [unclosed")
    monkeypatch.setenv("TORTOISE_ROUTING_CONFIG", str(bad))
    gh = GitHubConnector(config={"repo": "daniel-ospina/tortoise"})
    assert gh._routing, "must fall back on invalid user file"
    assert gh._routing["repo_routing"]["daniel-ospina/tortoise"]["default_team"] == "epistemic-team"


def test_routing_route_issue_unknown_repo_ask_human():
    """attribution_fallback: ask_human — unknown repo → no team (human decision)."""
    gh = GitHubConnector(config={"repo": "some/unknown-repo"})
    assert gh._routing["attribution_fallback"] == "ask_human", "fallback pinned"
    route = gh._route_issue([])
    assert route.get("team") in ("", None), f"unknown repo must not get a team, got {route}"
    assert route.get("role") == "product-implementer"


def test_routing_env_override_empty_dict_wins(monkeypatch, tmp_path):
    """An explicit {} override is a VALID 'no routing' choice — not masked."""
    empty = tmp_path / "empty-routing.yaml"
    empty.write_text("{}\n")
    monkeypatch.setenv("TORTOISE_ROUTING_CONFIG", str(empty))
    gh = GitHubConnector(config={"repo": "daniel-ospina/tortoise"})
    assert gh._routing == {}, "empty-dict override must win over the packaged default"
