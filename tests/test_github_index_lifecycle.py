"""#1725 Slice 0 — GitHub index lifecycle tests (Tasks 2/3/4/5/7).

Hosted-API level (TestClient + real SDK on a temp store): quota honest-fail,
per-team single-flight (reuse + TTL eviction), ONE-repo bounded first run,
auto-index-after-connect, re-poll endpoint, cursor + legacy-backfill marker
persistence, state-key registration, and the historical observation-dedup
script (dry-run + opt-in merge).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import sys
import time
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests._github_mock import MockGitHubTransport, gh_issue
from tortoise.hosted_api import (
    _ALLOWED_STATE_KEYS,
    _INDEX_JOBS,
    _ONBOARDING_DEFAULT_STATE,
    _start_index_job,
    app,
)
from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer
from tortoise.sdk import TortoiseSDK

_GRAPH_SCRIPTS = str(Path(__file__).resolve().parent.parent / "graph-scripts")
if _GRAPH_SCRIPTS not in sys.path:
    sys.path.insert(0, _GRAPH_SCRIPTS)

TEAM_ID = "test-team-1"


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Deterministic, fast embeddings (optional everywhere — None is fine)."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


def _provision(db_path: str, *, team_id: str = TEAM_ID,
               max_points: int | None = 10000) -> None:
    """Provision a Team node (mirrors hosted provision_tenant shape) with an
    encrypted GitHub token + org — on the SAME temp store the TestClient
    fixture redirects all SDK construction to (the per-path registry graph)."""
    reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
    from tortoise.crypto import encrypt_token
    reg_sdk._get_registry().query(
        "CREATE (t:Team {id:$id, name:$name, tier:'free', "
        "max_users:1, max_graphs:1, max_api_keys:2, "
        "max_points:$mp})",
        params={"id": team_id, "name": team_id, "mp": max_points},
    )
    reg_sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) "
        "SET t.github_token_enc=$tok, t.github_org=$org",
        params={"id": team_id,
                "tok": encrypt_token("fake-token"), "org": "acme"},
    )
    reg_sdk.close()


@pytest.fixture
def client(tmp_path):
    """TestClient with all SDK construction redirected to a temp store.

    The team_id is UNIQUE PER TEST: _make_sdk(namespace=team_id) mints a
    TEST-PREFIXED graph name (test_<id>_tortoise) that the URI redirect
    honors VERBATIM — a fixed id would share ONE server graph across the
    whole session and pollute every later test's walk."""
    import uuid
    db_path = str(tmp_path / "lifecycle.db")
    team_id = f"test-team-{uuid.uuid4().hex[:10]}"
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # ALWAYS redirect to THIS fixture's store — on the embedded lane
        # hosted_api._make_sdk passes db_path="/data/tortoise.db"
        # positionally; honoring it would silently split the store.
        kw.pop("db_path", None)
        orig_init(self, db_path=db_path, namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": team_id, "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1,
        "max_points": 10000,
    }
    _INDEX_JOBS.clear()

    from types import SimpleNamespace
    ctx = SimpleNamespace(tc=None, team_id=team_id, db_path=db_path)
    with TestClient(app) as tc:
        ctx.tc = tc
        yield ctx
        # Drain background index jobs spawned during THIS test's requests
        # while __init__ is still bound to THIS fixture's store — a stale
        # task surviving portal teardown would otherwise rebind to the next
        # test's patched __init__ and pollute its graph (single-flight +
        # module-global _INDEX_JOBS make the tasks hard to cancel by id).
        _drain_jobs(tc)
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init


@pytest.fixture
def provisioned(client, tmp_path):
    """A provisioned team (Team node + encrypted GitHub token + org)."""
    _provision(client.db_path, team_id=client.team_id)
    return client


@pytest.fixture
def mock_github(monkeypatch):
    """Route the real GitHubIndexer's fetch layer through the mock REST."""
    transport = MockGitHubTransport(
        issues=[gh_issue(1), gh_issue(2)],
        repos=["acme/repo1", "acme/repo2"])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    return transport


def _load_dedup_script():
    """Load graph-scripts/1714_dedup_observation.py (hyphen-free, dotted
    path cannot import a leading-digit module name)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dedup_observation",
        os.path.join(_GRAPH_SCRIPTS, "1714_dedup_observation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wait_for(predicate, timeout_s: float = 8.0):
    """Poll for a fire-and-forget side effect (background _run_indexing task
    on the TestClient's portal loop). Raises AssertionError on timeout."""
    deadline = time.time() + timeout_s
    while not predicate():
        if time.time() > deadline:
            raise AssertionError("timed out waiting for background job")
        time.sleep(0.02)


def _drain_jobs(client, timeout_s: float = 5.0):
    """Wait for this fixture's background index jobs to reach a terminal
    state (or time out) while its SDK-construction patch is still live.

    TestClient's portal only runs background tasks while it is servicing a
    request — the drain pumps the loop with cheap requests so spawned
    _run_indexing tasks settle on THIS fixture's store instead of leaking
    into the next test's patched __init__."""
    deadline = time.time() + timeout_s
    while _INDEX_JOBS and time.time() < deadline:
        if all(j.get("status") in ("completed", "failed")
               for j in _INDEX_JOBS.values()):
            return
        with suppress(Exception):
            client.get("/v1/onboarding/state")  # pump the portal loop
        time.sleep(0.02)


def _poll_until(client, job_id: str, status: str, timeout_s: float = 8.0):
    deadline = time.time() + timeout_s
    while True:
        r = client.get(f"/v1/index/github/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == status:
            return body
        if time.time() > deadline:
            raise AssertionError(f"job {job_id} not {status}: {body}")
        time.sleep(0.02)


# ── State-key registration (cycle-3 P1-2) ─────────────────────────

def test_state_keys_registered():
    """github_index_cursor + github_legacy_backfill_done must be registered
    in BOTH live default-state dicts + _ALLOWED_STATE_KEYS + the PATCH model
    — an unregistered key is silently dropped by _update_onboarding_state."""
    import tortoise.hosted_api as ha
    for key in ("github_index_cursor", "github_legacy_backfill_done"):
        assert key in _ONBOARDING_DEFAULT_STATE
        assert key in _ALLOWED_STATE_KEYS
        assert key in ha.DEFAULT_ONBOARDING_STATE
    # PATCH model fields (the live OnboardingStatePatchRequest)
    assert "github_index_cursor" in ha.OnboardingStatePatchRequest.model_fields
    assert "github_legacy_backfill_done" in ha.OnboardingStatePatchRequest.model_fields


def test_state_keys_survive_patch_roundtrip(client, provisioned):
    """A PATCH writing the cursor key round-trips (the allowlist filter must
    not drop it)."""
    r = client.tc.patch("/v1/onboarding/state",
                        json={"github_index_cursor": {"acme/repo1": {"updated_at": "x", "number": 1}}})
    assert r.status_code == 200
    body = r.json()
    assert body["onboarding"]["github_index_cursor"] == {
        "acme/repo1": {"updated_at": "x", "number": 1}}


# ── Single-flight (T2-P2 + cycle-3 P1-3) ──────────────────────────

def test_in_flight_single_flight_reuses(provisioned, mock_github, monkeypatch):
    """A `started` job for the team is REUSED — its job_id is returned
    (guard-check FIRST), and the reused POST spawns NO second run — two
    concurrent POSTs produce EXACTLY ONE _run_indexing execution (P1-1:
    single-flight dedupes the RUN, not just the entry)."""
    _team_id = provisioned.team_id
    calls = []
    import asyncio as _asyncio

    import tortoise.hosted_api as ha
    orig_run = ha._run_indexing

    async def _counting(*args, **kw):
        calls.append(args)
        # keep the run IN-FLIGHT across both POSTs (the mock walk is
        # millisecond-fast — without the hold the portal settles it before
        # the second POST and the entry turns terminal)
        await _asyncio.sleep(0.2)
        await orig_run(*args, **kw)

    monkeypatch.setattr(ha, "_run_indexing", _counting)
    r1 = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    assert r1.status_code == 200
    job_id = r1.json()["job_id"]
    assert _INDEX_JOBS[job_id]["status"] == "started"
    r2 = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    assert r2.status_code == 200
    assert r2.json()["job_id"] == job_id, "in-flight job must be reused"
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["points_created"] == 0  # object-only (#1844)
    assert body["events_minted"] == 2   # 2 issues × 1 created event
    assert len(calls) == 1, "concurrent POSTs must spawn exactly ONE run"


def test_stuck_started_evicted(provisioned, mock_github):
    """A `started` entry older than the 30-min TTL is presumed-dead and
    evicted — a hung run never bricks the team."""
    from tortoise.hosted_api import _INDEX_JOB_TTL_S
    team_id = provisioned.team_id
    stale, _ = _start_index_job(team_id)
    _INDEX_JOBS[stale]["started_at"] = time.time() - _INDEX_JOB_TTL_S - 10
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    assert r.status_code == 200
    new_job = r.json()["job_id"]
    assert new_job != stale
    assert stale not in _INDEX_JOBS, "stale started entry must be evicted"
    _poll_until(provisioned.tc, new_job, "completed")  # settle the spawned task


def test_terminal_jobs_evicted_on_enqueue(provisioned, mock_github):
    """Terminal (completed) entries for the team are cleared on the next
    enqueue (T1-P14)."""
    team_id = provisioned.team_id
    job_id, _ = _start_index_job(team_id)
    _INDEX_JOBS[job_id]["status"] = "completed"
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    new_job = r.json()["job_id"]
    assert new_job != job_id
    assert job_id not in _INDEX_JOBS
    _poll_until(provisioned.tc, new_job, "completed")  # settle the spawned task


# ── ONE-repo bounded first run (P2-4) ─────────────────────────────

def test_first_run_single_repo(provisioned, mock_github):
    """First-run scope = ONE repo regardless of org size; job status reports
    repos_total vs repos_processed (the honest 'index more' affordance)."""
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 1
    assert body["repos_processed"] == 1
    # only the first resolved repo was walked (acme/repo1)
    assert body["points_created"] == 0  # object-only (#1844): no statement points
    assert body["events_minted"] == 2   # 2 issues × 1 created event


def test_second_run_full_org_with_cursors(provisioned, mock_github):
    """After the first run (github_indexed=True), a re-poll walks ALL repos
    with their persisted cursors."""
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    r = provisioned.tc.post("/v1/index/github/re-poll")
    job_id = r.json()["job_id"]
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 2
    assert body["repos_processed"] == 2
    # the diff re-run produced 0 new points (cursor-stopped walk)
    assert body["points_created"] == 0
    assert body["events_minted"] == 0


# ── Auto-index after connect (Task 5) ─────────────────────────────

def test_auto_index_after_connect(provisioned, mock_github, monkeypatch):
    """Connect fires the first index (quota-gated, ONE repo). The Team node
    must pre-exist for the callback's token MATCH (provisioned fixture)."""
    from tortoise.hosted_api import _GITHUB_STATES
    _GITHUB_STATES["test-state"] = {"team_id": provisioned.team_id, "org": "acme",
                                    "created_at": time.time()}
    async def _fake_exchange(code):
        return "oauth-token"
    monkeypatch.setattr("tortoise.hosted_api._exchange_github_token", _fake_exchange)
    r = provisioned.tc.get("/v1/onboarding/github/callback?code=abc&state=test-state",
                           follow_redirects=False)
    assert r.status_code == 302
    # an index job was enqueued for the team
    jobs = [j for j in _INDEX_JOBS.values() if j.get("team_id") == provisioned.team_id]
    assert jobs, "auto-index job must be enqueued after connect"
    job_id = next(jid for jid, j in _INDEX_JOBS.items()
                  if j.get("team_id") == provisioned.team_id)
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_total"] == 1


def test_re_poll_requires_connection(client):
    r = client.tc.post("/v1/index/github/re-poll")
    assert r.status_code == 400
    assert "GitHub not connected" in r.json()["detail"]


def test_re_poll_scoped_repo(provisioned, monkeypatch):
    """#1845: re-poll accepts an optional {repos} scope and forwards it to
    the run — org is still read server-side from the stored credentials (the
    client never supplies it). The legacy single {repo} field is equivalent
    to a one-item list."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"  # settle the drain

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = provisioned.tc.post("/v1/index/github/re-poll", json={"repo": "repo2"})
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    _wait_for(lambda: seen == [("acme", ["repo2"])])


def test_re_poll_multi_repo_scope(provisioned, monkeypatch):
    """#1845: re-poll with a {repos} LIST forwards exactly those repos (org
    still read server-side)."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = provisioned.tc.post("/v1/index/github/re-poll",
                            json={"repos": ["repo1", "repo2"]})
    assert r.status_code == 200
    _wait_for(lambda: seen == [("acme", ["repo1", "repo2"])])


def test_re_poll_empty_repos_is_full_org(provisioned, monkeypatch):
    """#1845: an EMPTY repos list is the full-org diff (repos=None), not a
    scoped walk of nothing."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = provisioned.tc.post("/v1/index/github/re-poll", json={"repos": []})
    assert r.status_code == 200
    _wait_for(lambda: seen == [("acme", None)])


def test_re_poll_invalid_repo_400(provisioned, monkeypatch):
    """#1845 (review P1): a malicious repo scope must be rejected (400) —
    the org boundary is server-side and the repo must stay a SHORT name.
    Dot-segment traversal ("../victimorg/x") and query/whitespace junk are
    not valid short repo names and must never reach the GitHub URL path."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    for bad in ("../victimorg/secret", "a/b?q=1", "repo name", "../.."):
        r = provisioned.tc.post("/v1/index/github/re-poll", json={"repo": bad})
        assert r.status_code == 400, f"repo={bad!r} should 400"
        assert "Invalid repo name" in r.json()["detail"]
        r2 = provisioned.tc.post("/v1/index/github/re-poll",
                                 json={"repos": [bad]})
        assert r2.status_code == 400, f"repos=[{bad!r}] should 400"
    assert seen == [], "no job must be spawned for an invalid repo"


def test_re_poll_whitespace_repo_is_full_org(provisioned, monkeypatch):
    """#1845 (review P2): a whitespace-only repo scope collapses to None
    (full-org diff), never a scoped walk of a junk name."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = provisioned.tc.post("/v1/index/github/re-poll", json={"repo": "   "})
    assert r.status_code == 200
    _wait_for(lambda: seen == [("acme", None)])


def test_re_poll_no_repo_is_full_org(provisioned, monkeypatch):
    """#1845: re-poll WITHOUT a body keeps the full-org diff (repos=None) —
    backward compatible with the pre-selector flow."""
    import tortoise.hosted_api as ha
    seen = []

    async def _capture(job_id, team_id, org, repos):
        seen.append((org, repos))
        ha._INDEX_JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(ha, "_run_indexing", _capture)
    r = provisioned.tc.post("/v1/index/github/re-poll")
    assert r.status_code == 200
    _wait_for(lambda: seen == [("acme", None)])


# ── Cursor + backfill marker persistence (Tasks 2/3) ──────────────

def test_cursor_and_backfill_marker_persisted(provisioned, mock_github):
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    state = provisioned.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_indexed"] is True
    assert state["github_legacy_backfill_done"] is True
    cursor = state["github_index_cursor"]
    assert cursor and "acme/repo1" in cursor
    assert cursor["acme/repo1"]["updated_at"]
    assert "number" in cursor["acme/repo1"]


# ── P2 (PR #1792): marker / 404 / quota-break honesty ────────────────

def test_backfill_marker_set_only_on_success(client, tmp_path, monkeypatch):
    """P2: github_legacy_backfill_done is set ONLY when the backfill
    SUCCEEDS — a transient failure must not skip the one-time migration
    forever (the job still completes; the backfill re-runs next time)."""
    _provision(client.db_path, team_id=client.team_id)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    fail_backfill = {"fail": True}

    def _flaky_backfill(self, proj):
        if fail_backfill["fail"]:
            raise RuntimeError("backfill boom")
        return 0

    monkeypatch.setattr(GitHubIndexer, "backfill_legacy_closed",
                        _flaky_backfill)
    r = client.tc.post("/v1/index/github", json={"org": "acme"})
    _poll_until(client.tc, r.json()["job_id"], "completed")
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_legacy_backfill_done"] is False, \
        "marker must stay UNSET when the backfill raises"
    # heal the backfill → the next run re-runs it and sets the marker
    fail_backfill["fail"] = False
    r2 = client.tc.post("/v1/index/github/re-poll")
    _poll_until(client.tc, r2.json()["job_id"], "completed")
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_legacy_backfill_done"] is True


def test_resolve_repos_404_fails_job(client, tmp_path, monkeypatch):
    """P2: an org that 404s on BOTH orgs/ and users/ must FAIL the job
    with a readable error — never silently complete with 0 points +
    github_indexed=True."""
    _provision(client.db_path, team_id=client.team_id)
    t = MockGitHubTransport(issues=[], resolve_repos_404=True)

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(client.tc, job_id, "failed")
    assert "not found" in body["error"] or "no access" in body["error"]
    assert body["status"] == "failed"
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert not state["github_indexed"], \
        "a 0-repo 404 failure must leave github_indexed UNSET — the next " \
        "run keeps the ONE-repo bounded first-run pacing (P2, PR #1792)"


def test_issue_ingest_no_longer_consumes_points_quota(client, tmp_path,
                                                      monkeypatch):
    """#1844: issue ingest is OBJECT-ONLY — no statement Points are written,
    so the points quota gate never fires on a github index run (a 0-point-cap
    team still ingests issues fine; the old points-quota preflight would have
    FAILED this job). Pre-change: each issue consumed one statement point, so
    max_points=1 let exactly one issue through and stamped the cursor
    truncated on the quota break."""
    _provision(client.db_path, team_id=client.team_id, max_points=0)
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/github", json={"org": "acme"})
    job_id = r.json()["job_id"]
    body = _poll_until(client.tc, job_id, "completed")
    assert body["quota_hit"] is False, \
        "object-only ingest must not trip the points quota"
    assert body["points_created"] == 0
    assert body["events_minted"] == 2, "both issues' lifecycle events minted"
    assert body["repos_processed"] == 1
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    cursor = state["github_index_cursor"]["acme/repo1"]
    assert "truncated" not in cursor, \
        "a quota-free run ends with a clean window (no DRAIN flag)"


def test_resolve_repos_failure_preserves_persisted_cursors(client, tmp_path,
                                                            monkeypatch):
    """P2: a PRE-WALK failure (resolve_repos raising) must not WIPE
    previously persisted cursors — the finally persists the loaded state back
    (a `cursors={}` blind write would silently drop the resume point).
    (Formerly the points-quota preflight failure; that gate is removed in
    #1844 — the object-only job writes zero points — so the pre-walk failure
    surface is now resolve_repos.)"""
    _provision(client.db_path, team_id=client.team_id, max_points=10000)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    # seed persisted cursors + indexed state
    client.tc.patch("/v1/onboarding/state", json={
        "github_index_cursor": {
            "acme/repo1": {"updated_at": "2026-07-19T12:00:00Z",
                            "number": 7}},
        "github_indexed": True})
    # break the org resolution → the pre-walk resolve_repos fails
    async def _boom(self, org):
        raise GitHubFetchError("org not found")

    monkeypatch.setattr(GitHubIndexer, "resolve_repos", _boom)
    r = client.tc.post("/v1/index/github", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "failed")
    assert "org not found" in body["error"]
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_index_cursor"] == {
        "acme/repo1": {"updated_at": "2026-07-19T12:00:00Z",
                        "number": 7}}, \
        "pre-walk failure must not wipe persisted cursors"
    assert state["github_indexed"] is True, \
        "an already-indexed team must NOT be downgraded to first-run by a " \
        "0-repo pre-walk failure (github_indexed only flips on progress)"


def test_owner_token_eviction_aborts_stale_run(client, tmp_path, monkeypatch):
    """P2: a stale run whose entry was TTL-evicted / replaced (owner token
    gone) must settle SILENTLY — no KeyError on status writes, no
    resurrection of the entry (pre-fix: `_INDEX_JOBS[job_id].update` raised
    KeyError on the missing entry)."""
    _provision(client.db_path, team_id=client.team_id)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    from tortoise.hosted_api import _INDEX_JOB_OWNERS, _run_indexing
    stale_job, _ = _start_index_job(client.team_id)
    # entry + owner gone — as after a TTL eviction + replacement by a newer
    # run (the stale coroutine still holds its old job_id)
    _INDEX_JOBS.pop(stale_job, None)
    _INDEX_JOB_OWNERS.pop(stale_job, None)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _run_indexing(stale_job, client.team_id, "acme", None))
    finally:
        loop.close()
    # the stale run settled without resurrecting its entry (ownership-gated
    # status writes were no-ops) and without raising
    assert stale_job not in _INDEX_JOBS
    assert stale_job not in _INDEX_JOB_OWNERS


# ── Historical observation-dedup script (Task 7) ──────────────────

@pytest.fixture
def dedup_sdk(tmp_path):
    store = TortoiseSDK(str(tmp_path / "dedup.db"))
    yield store
    store.close()


def test_dedup_script_dry_run(dedup_sdk):
    """Dry-run reports the duplicate observation→statement pairing with NO
    writes (deliver-or-defer default)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    sdk.create_point("observation", "old unkeyed claim",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     github_state="open", source="github")
    sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                     externalId="github:issue:acme/repo1#1",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     extractedFrom="https://github.com/acme/repo1/issues/1",
                     source="github")
    report = script.dry_run_report(proj)
    assert report["observations_to_supersede"] == 1
    assert report["duplicate_urls"] == 1
    url = "https://github.com/acme/repo1/issues/1"
    assert url in report["pairs"]
    # dry-run performed NO writes
    assert proj.g.query("MATCH ()-[r:CORRECTS]->() RETURN count(r)").result_set[0][0] == 0


def test_dedup_script_opt_in_merge(dedup_sdk):
    """Opt-in merge supersedes the duplicate observation into its statement
    twin (CORRECTS + terminal observation, statement stays current)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    obs = sdk.create_point("observation", "old unkeyed claim",
                           github_url="https://github.com/acme/repo1/issues/1",
                           github_repo="acme/repo1", github_number=1,
                           github_state="open", source="github")
    stmt = sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                            externalId="github:issue:acme/repo1#1",
                            github_url="https://github.com/acme/repo1/issues/1",
                            github_repo="acme/repo1", github_number=1,
                            extractedFrom="https://github.com/acme/repo1/issues/1",
                            source="github")
    report = script.merge_duplicates(sdk, proj, dry_run=False)
    assert report["merged"] == 1
    rows = proj.g.query(
        "MATCH (a:Point {id:$new})-[:CORRECTS]->(b:Point {id:$old}) "
        "RETURN b.status, b.outdated",
        params={"new": stmt["id"], "old": obs["id"]},
    ).result_set
    assert rows and rows[0][0] == "superseded" and rows[0][1] is True


def test_dedup_script_leave_as_is_by_default(dedup_sdk):
    """The script's DEFAULT mode is dry-run — no writes even when duplicates
    exist (the recorded deliver-or-defer decision)."""
    script = _load_dedup_script()

    proj = dedup_sdk._get_proj()
    sdk = dedup_sdk
    sdk.create_point("observation", "old unkeyed claim",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     github_state="open", source="github")
    sdk.create_point("statement", "[acme/repo1] Issue 1\n\nbody",
                     externalId="github:issue:acme/repo1#1",
                     github_url="https://github.com/acme/repo1/issues/1",
                     github_repo="acme/repo1", github_number=1,
                     extractedFrom="https://github.com/acme/repo1/issues/1",
                     source="github")
    report = script.merge_duplicates(sdk, proj, dry_run=True)
    assert report["dry_run"] is True
    assert report["merged"] == 0
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 2


def test_dedup_script_resolve_uri_honors_explicit_uri(monkeypatch):
    """P2 (PR #1792): an EXPLICIT --uri equal to the default constant is
    honored verbatim — the old constant-comparison silently rerouted it to
    the embedded DB (the operator's explicit target was ignored)."""
    script = _load_dedup_script()
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # no URI anywhere → embedded default path (not a URI)
    embedded = script._resolve_uri("")
    assert embedded and not embedded.startswith("docker://")
    # explicit URI equal to the constant → honored verbatim
    assert script._resolve_uri(script.DEFAULT_URI) == script.DEFAULT_URI
    # explicit --uri wins over env
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@localhost:9999/other")
    assert script._resolve_uri(script.DEFAULT_URI) == script.DEFAULT_URI
    # env-only → env wins
    assert script._resolve_uri("") == "docker://:x@localhost:9999/other"
