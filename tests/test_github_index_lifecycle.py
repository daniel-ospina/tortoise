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
from tortoise.indexer.github_indexer import GitHubIndexer
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


# ── Quota honest-fail (Task 4) ────────────────────────────────────

def test_quota_honest_fail(client, tmp_path, monkeypatch):
    """A team AT the points cap: the job fails honestly with the quota
    message (402-equivalent) — zero writes, never a silent overshoot."""
    # provision with max_points=0 → any point write is at/over cap
    _provision(client.db_path, team_id=client.team_id, max_points=0)
    t = MockGitHubTransport(issues=[gh_issue(1)])

    async def _fake_get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=t)
        return self._client

    monkeypatch.setattr(GitHubIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/github", json={"org": "acme"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    body = _poll_until(client.tc, job_id, "failed")
    assert "limit reached" in body["error"]
    assert body["points_created"] == 0


# ── Single-flight (T2-P2 + cycle-3 P1-3) ──────────────────────────

def test_in_flight_single_flight_reuses(provisioned, mock_github):
    """A `started` job for the team is REUSED — its job_id is returned
    (guard-check FIRST)."""
    team_id = provisioned.team_id
    job_id = _start_index_job(team_id)
    assert _INDEX_JOBS[job_id]["status"] == "started"
    r = provisioned.tc.post("/v1/index/github", json={"org": "acme"})
    assert r.status_code == 200
    assert r.json()["job_id"] == job_id, "in-flight job must be reused"
    # poll the reused job to completion (single point set)
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["points_created"] == 2


def test_stuck_started_evicted(provisioned, mock_github):
    """A `started` entry older than the 30-min TTL is presumed-dead and
    evicted — a hung run never bricks the team."""
    from tortoise.hosted_api import _INDEX_JOB_TTL_S
    team_id = provisioned.team_id
    stale = _start_index_job(team_id)
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
    job_id = _start_index_job(team_id)
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
    assert body["points_created"] == 2  # 2 issues × 1 statement


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
