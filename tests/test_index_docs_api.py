"""#1726 Slice 1 — hosted docs index job + Document-aware quota tests (Task 9).

Hosted-API level (TestClient + real SDK on a temp store): POST /v1/index/docs
mirrors /v1/index/github (kind-scoped per-team single-flight, cross-team poll
404), the derived-constant documents gate (402 at cap where the points gate
would NOT fire; transcript excluded; NULL-kind docs COUNT), unset-base
fail-closed, and the ``github_docs_indexed`` state-key registration.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import time
from contextlib import suppress
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from tests._github_docs_mock import MockGitHubDocsTransport, gh_docs_entry
from tortoise.hosted_api import (
    _ALLOWED_STATE_KEYS,
    _INDEX_JOBS,
    _ONBOARDING_DEFAULT_STATE,
    app,
)
from tortoise.indexer.github_docs import GitHubDocsIndexer
from tortoise.quota import QuotaExceededError, count_team_usage
from tortoise.sdk import TortoiseSDK

TEAM_A = "test-docs-team-a"


def _mk_files(*paths: str) -> tuple[list[dict], dict]:
    """docs/ tree entries + registered blobs for the given rel paths."""
    entries = [gh_docs_entry(p) for p in paths]
    blobs = {}
    for e in entries:
        blobs[e["sha"]] = (f"# {e['path']}\ncontent\n").encode()
    return entries, blobs


def _docs_tree(*, sha: str, entries: list[dict],
               repo: str = "acme/repo1") -> dict:
    return {repo: {"main": {"sha": sha, "entries": entries}}}


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Deterministic, fast embeddings (optional everywhere — None is fine)."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


def _provision(db_path: str, *, team_id: str = TEAM_A,
               max_points: int | None = 10000) -> None:
    """Provision a Team node (mirrors hosted provision_tenant shape) with an
    encrypted GitHub token + org on the SAME temp store the TestClient
    fixture redirects all SDK construction to."""
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
    """TestClient with all SDK construction redirected to a temp store
    (unique team per test — the URI redirect honors the test-prefixed graph
    names verbatim)."""
    import uuid
    db_path = str(tmp_path / "docs-api.db")
    team_id = f"test-docs-{uuid.uuid4().hex[:10]}"
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
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
        _drain_jobs(tc)
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init


@pytest.fixture
def provisioned(client, tmp_path):
    _provision(client.db_path, team_id=client.team_id)
    return client


@pytest.fixture
def ingest_base(tmp_path, monkeypatch):
    """A server-owned sandbox for staged docs (set before the job runs)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    return base


@pytest.fixture
def mock_github(monkeypatch, ingest_base):
    """Route the docs fetcher's fetch layer through the mock Contents API.
    Both resolved repos serve the same docs tree (multi-repo walk)."""
    entries, blobs = _mk_files("docs/README.md", "docs/guides/setup.md")
    transport = MockGitHubDocsTransport(
        repos=["acme/repo1", "acme/repo2"],
        trees={
            "acme/repo1": {"main": {"sha": "tree-v1", "entries": entries}},
            "acme/repo2": {"main": {"sha": "tree-v1", "entries": entries}},
        },
        blobs=blobs)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    return transport


def _team_sdk(client) -> TortoiseSDK:
    """Team-scoped SDK on the SAME temp store (namespace = team_id)."""
    return TortoiseSDK(db_path=client.db_path, namespace=client.team_id)


def _seed_documents(client, n: int, *, kind: str | None = "brief") -> None:
    """Seed n Document nodes in the team graph (NULL kind when None)."""
    sdk = _team_sdk(client)
    for i in range(n):
        if kind is None:
            sdk._get_proj().g.query(
                "CREATE (d:Document {id:$id, title:$title})",
                params={"id": f"doc_seed_{i}", "title": f"seed {i}"})
        else:
            sdk._get_proj().g.query(
                "CREATE (d:Document {id:$id, title:$title, documentKind:$dk})",
                params={"id": f"doc_seed_{i}", "title": f"seed {i}",
                        "dk": kind})
    sdk.close()


def _docs_count(client) -> int:
    return count_team_usage(client.team_id, "documents",
                            sdk=_team_sdk(client))


def _wait_for(predicate, timeout_s: float = 8.0):
    deadline = time.time() + timeout_s
    while not predicate():
        if time.time() > deadline:
            raise AssertionError("timed out waiting for background job")
        time.sleep(0.02)


def _drain_jobs(client, timeout_s: float = 5.0):
    deadline = time.time() + timeout_s
    while _INDEX_JOBS and time.time() < deadline:
        if all(j.get("status") in ("completed", "failed")
               for j in _INDEX_JOBS.values()):
            return
        with suppress(Exception):
            client.get("/v1/onboarding/state")
        time.sleep(0.02)


def _poll_until(client, job_id: str, status: str, timeout_s: float = 10.0):
    deadline = time.time() + timeout_s
    while True:
        r = client.get(f"/v1/index/docs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == status:
            return body
        if time.time() > deadline:
            raise AssertionError(f"docs job {job_id} not {status}: {body}")
        time.sleep(0.02)


# ── state-key registration ───────────────────────────────────────

def test_github_docs_indexed_state_key_registered():
    """github_docs_indexed must be registered in BOTH live default-state
    dicts + _ALLOWED_STATE_KEYS + the PATCH model — an unregistered key is
    silently dropped by _update_onboarding_state (cycle-3 P1-2)."""
    import tortoise.hosted_api as ha
    assert "github_docs_indexed" in _ONBOARDING_DEFAULT_STATE
    assert "github_docs_indexed" in _ALLOWED_STATE_KEYS
    assert "github_docs_indexed" in ha.DEFAULT_ONBOARDING_STATE
    assert "github_docs_indexed" in ha.OnboardingStatePatchRequest.model_fields
    # #1894: the docs last-indexed timestamp rides the same registration
    # table (allowlist-filtered like every state key).
    assert "github_docs_indexed_at" in _ONBOARDING_DEFAULT_STATE
    assert "github_docs_indexed_at" in _ALLOWED_STATE_KEYS
    assert "github_docs_indexed_at" in ha.DEFAULT_ONBOARDING_STATE
    assert "github_docs_indexed_at" in ha.OnboardingStatePatchRequest.model_fields


def test_github_docs_indexed_round_trip(client, provisioned):
    """The key round-trips through the PATCH surface (allowlist filter must
    not drop it) and flips True after a successful docs job."""
    r = client.tc.patch("/v1/onboarding/state",
                        json={"github_docs_indexed": True})
    assert r.status_code == 200
    assert r.json()["onboarding"]["github_docs_indexed"] is True
    # and through the defaults when unset
    r = client.tc.get("/v1/onboarding/state")
    assert "github_docs_indexed" in r.json()["onboarding"]


# ── job poll + ingestion ─────────────────────────────────────────

def test_docs_job_poll_completed(provisioned, mock_github, ingest_base):
    """POST /v1/index/docs → job poll → completed with honest counts: the
    staged docs are ingested by the deterministic corpus pipeline (Sources
    only — no claim extraction). Repos resolve via the Contents-API mock
    (both repos serve the same docs tree)."""
    r = provisioned.tc.post("/v1/index/docs", json={"org": "acme"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["documents_indexed"] == 4  # 2 md files × 2 resolved repos
    assert body["blobs_fetched"] == 4
    assert body["repos_processed"] == 2
    assert body["repos_total"] == 2
    state = provisioned.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_docs_indexed"] is True
    # #1894: completion stamps the last-indexed timestamp. Strict ISO parse
    # (fromisoformat rejects malformed strings, not just epoch floats).
    assert state["github_docs_indexed_at"], "github_docs_indexed_at must be stamped on completion"
    datetime.fromisoformat(state["github_docs_indexed_at"])  # ISO format
    assert _docs_count(provisioned) == 4


def test_docs_live_progress_written_during_walk(provisioned, mock_github,
                                                ingest_base, monkeypatch):
    """#1894: the docs job poll surfaces LIVE per-repo progress mid-walk —
    a placement regression in the SINGLE-BRANCH (default walk) increment
    site must fail the suite (terminal-only coverage would miss it; the
    all-branches increment site is pinned by
    test_docs_live_progress_all_branches_site). Pump-aware: portal runs
    background tasks only while servicing a request; the hold is a
    loop-friendly asyncio.sleep, never a barrier."""
    import asyncio as _asyncio

    orig_walk = GitHubDocsIndexer.walk_repo

    async def _slow_repo2(self, team_id, repo, *, branch="main"):
        if repo.endswith("/repo2"):
            await _asyncio.sleep(1.0)  # loop-friendly hold on the 2nd repo
        return await orig_walk(self, team_id, repo, branch=branch)

    monkeypatch.setattr(GitHubDocsIndexer, "walk_repo", _slow_repo2)
    r = provisioned.tc.post("/v1/index/docs", json={"org": "acme"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Pump + observe via the PUBLIC poll endpoint (the dashboard's surface) —
    # the portal advances the walk while servicing the GET.
    mid = {"seen": False, "repos_processed": 0, "progress": 0,
           "repos_total": 0}
    deadline = time.time() + 12.0
    while time.time() < deadline:
        rp = provisioned.tc.get(f"/v1/index/docs/{job_id}")
        body = rp.json()
        if (body.get("repos_processed", 0) >= 1
                and 0 < body.get("progress", 0) < 100):
            mid["seen"] = True
            mid["repos_processed"] = body["repos_processed"]
            mid["progress"] = body["progress"]
            mid["repos_total"] = body.get("repos_total", 0)
            break
        time.sleep(0.02)

    assert mid["seen"], (
        "docs mid-walk live progress never observed — _job() must write "
        "repos_processed/progress after each repo in the single-branch site")
    assert mid["repos_processed"] == 1
    assert mid["repos_total"] == 2
    assert mid["progress"] == 50  # 1 of 2 repos

    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["repos_processed"] == 2
    assert body["repos_total"] == 2


def test_docs_live_progress_all_branches_site(provisioned, mock_github,
                                              ingest_base, monkeypatch):
    """#1894: the docs 'all branches' increment site (scope_branch == 'all')
    also writes LIVE per-repo progress — a placement regression in THAT site
    must fail the suite. Two repos scoped branch='all' so the first repo's
    increment lands mid-walk (progress 50) before the second repo's slow
    branch walk completes. Pump-aware (portal advances while servicing the
    poll GET)."""
    import asyncio as _asyncio

    entries_main, blobs_main = _mk_files("docs/README.md")
    entries_dev, blobs_dev = _mk_files("docs/DEV.md")
    mock_github.trees["acme/repo1"] = {
        "main": {"sha": "tree-main", "entries": entries_main},
        "dev": {"sha": "tree-dev", "entries": entries_dev},
    }
    mock_github.trees["acme/repo2"] = {
        "main": {"sha": "tree2-main", "entries": entries_main},
        "dev": {"sha": "tree2-dev", "entries": entries_dev},
    }
    mock_github.blobs.update(blobs_main)
    mock_github.blobs.update(blobs_dev)

    orig_walk = GitHubDocsIndexer.walk_repo

    async def _slow_repo2(self, team_id, repo, *, branch="main"):
        if repo.endswith("/repo2"):
            await _asyncio.sleep(1.0)  # loop-friendly hold on the 2nd repo
        return await orig_walk(self, team_id, repo, branch=branch)

    monkeypatch.setattr(GitHubDocsIndexer, "walk_repo", _slow_repo2)
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme",
        "repos": [{"repo": "repo1", "branch": "all"},
                   {"repo": "repo2", "branch": "all"}]})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    mid = {"seen": False, "repos_processed": 0, "progress": 0,
           "repos_total": 0}
    deadline = time.time() + 12.0
    while time.time() < deadline:
        rp = provisioned.tc.get(f"/v1/index/docs/{job_id}")
        body = rp.json()
        if (body.get("repos_processed", 0) >= 1
                and 0 < body.get("progress", 0) < 100):
            mid["seen"] = True
            mid["repos_processed"] = body["repos_processed"]
            mid["progress"] = body["progress"]
            mid["repos_total"] = body.get("repos_total", 0)
            break
        time.sleep(0.02)

    assert mid["seen"], (
        "all-branches site mid-walk progress never observed — _job() must "
        "write repos_processed/progress after the scope_branch=='all' "
        "increment")
    assert mid["repos_processed"] == 1  # repo1 fully walked (both branches)
    assert mid["repos_total"] == 2
    assert mid["progress"] == 50

    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["status"] == "completed"
    assert body["repos_processed"] == 2
    assert _docs_count(provisioned) == 4  # 2 files x 2 repos (branch-qualified)


def test_docs_job_single_repo_and_unchanged_rerun_zero_new(
        provisioned, mock_github, ingest_base):
    """Explicit repo scope; an unchanged re-run ingests 0 NEW documents
    (falsification (f) at the job level)."""
    r = provisioned.tc.post("/v1/index/docs",
                            json={"org": "acme", "repo": "repo1"})
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["documents_indexed"] == 2
    assert _docs_count(provisioned) == 2

    r2 = provisioned.tc.post("/v1/index/docs",
                             json={"org": "acme", "repo": "repo1"})
    body2 = _poll_until(provisioned.tc, r2.json()["job_id"], "completed")
    assert body2["blobs_fetched"] == 0  # tree-by-sha short-circuit
    assert body2["documents_indexed"] == 0
    assert body2["documents_skipped"] == 2  # unchanged re-run skips both files
    assert _docs_count(provisioned) == 2, \
        "unchanged re-ingest must add 0 new Document nodes (falsification (f))"


def test_docs_job_requires_github_connection(client, ingest_base):
    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    assert r.status_code == 400
    assert "GitHub not connected" in r.json()["detail"]


def test_docs_job_unresolvable_org_fails(provisioned, ingest_base,
                                         monkeypatch):
    """An org whose repo resolution 404s (org not found / no access) fails
    the job honestly — 0 documents indexed, 0 graph writes, no
    github_docs_indexed state flip (P2, PR #1792)."""
    transport = MockGitHubDocsTransport(repos=["acme/repo1"],
                                        resolve_404=True)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    r = provisioned.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(provisioned.tc, r.json()["job_id"], "failed")
    assert body["documents_indexed"] == 0
    assert _docs_count(provisioned) == 0
    assert body["error"], "failed job must report a readable error"
    assert "not found or no access" in body["error"]
    state = provisioned.tc.get(
        "/v1/onboarding/state").json()["onboarding"]
    assert not state.get("github_docs_indexed"), \
        "an unresolved org is not progress — the state key must not flip"
    # #1894 parity: a 0-progress failure must NOT stamp the timestamp either
    # (same repos_processed>0 branch as the bool) — no fabricated
    # "Indexed · <time>" for a failed team.
    assert not state.get("github_docs_indexed_at"), \
        "an unresolved org must leave github_docs_indexed_at UNSET (parity " \
        "with the bool — no stamp on zero progress)"


def test_docs_job_token_undecryptable(client, ingest_base):
    """A garbage (non-Fernet) github_token_enc fails the job fast with an
    honest error — no fetches, no writes."""
    _provision(client.db_path, team_id=client.team_id)
    reg_sdk = TortoiseSDK(db_path=client.db_path, namespace="registry")
    reg_sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.github_token_enc=$tok",
        params={"id": client.team_id,
                "tok": "garbage-not-a-fernet-token"})
    reg_sdk.close()
    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "failed")
    assert "Token undecryptable" in body["error"]
    assert _docs_count(client) == 0


def test_docs_job_midwalk_quota_hit(provisioned, mock_github, ingest_base,
                                    monkeypatch):
    """The per-repo DOCUMENTS gate (Fix 3) bounds the overshoot to ONE
    repo's docs, not the whole org: the 3rd enforce_team_limit call
    (repo2's pre-ingest check) raises → repo1's 2 docs are ingested, repo2's
    are not, quota_hit is reported honestly."""
    import tortoise.quota as quota_mod
    real_enforce = quota_mod.enforce_team_limit
    calls = {"n": 0}

    def _counting_enforce(limits, resource, *, sdk=None):
        calls["n"] += 1
        if calls["n"] == 3:  # preflight=1, repo1 pre-ingest=2, repo2 = 3
            raise QuotaExceededError("documents limit reached (test)")
        return real_enforce(limits, resource, sdk=sdk)

    monkeypatch.setattr(quota_mod, "enforce_team_limit", _counting_enforce)
    r = provisioned.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["quota_hit"] is True
    assert body["documents_indexed"] == 2, \
        "only repo1's docs are ingested — the overshoot is ONE repo, not the org"
    assert _docs_count(provisioned) == 2
    # #1894 parity policy: a quota-PARTIAL run with >=1 repo processed is
    # "made progress" — the bool AND the timestamp both stamp (the docs
    # DOCUMENTS gate runs AFTER the increment, so repo2 IS counted).
    state = provisioned.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert state["github_docs_indexed"] is True
    assert state["github_docs_indexed_at"], \
        "quota-partial runs stamp github_docs_indexed_at (parity with the bool)"


# ── documents gate (derived-constant cap) ────────────────────────

def test_402_at_document_cap_points_gate_would_not_fire(
        client, tmp_path, monkeypatch, ingest_base):
    """The docs job gates on the DOCUMENTS resource — a team over the points
    cap (2 non-episodic points, max_points=1) whose documents count is under
    the derived cap runs fine; at the documents cap the job 402s with the
    documents message (the points gate would NOT have fired — it is vacuous
    for Documents)."""
    _provision(client.db_path, team_id=client.team_id, max_points=1)
    entries, blobs = _mk_files("docs/README.md")
    transport = MockGitHubDocsTransport(
        repos=["acme/repo1"],
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    # 2 non-episodic points → OVER the points cap (max_points=1) — the
    # points gate WOULD 402, but the docs job must not use it.
    sdk = _team_sdk(client)
    sdk.create_point("statement", "claim one")
    sdk.create_point("statement", "claim two")
    sdk.close()

    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "completed")
    assert body["documents_indexed"] == 1, \
        "points gate must NOT fire for docs — the documents gate is the gate"
    assert _docs_count(client) == 1

    # now drive the documents count to the derived cap (max_points=1 →
    # max_documents=10): the next job 402s with the documents message
    _seed_documents(client, 9)
    assert _docs_count(client) == 10
    r2 = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body2 = _poll_until(client.tc, r2.json()["job_id"], "failed")
    assert "documents limit reached" in body2["error"]
    assert body2["documents_indexed"] == 0
    assert _docs_count(client) == 10, "no writes past the cap"


def test_transcript_not_counted_docs_cap(client, tmp_path, monkeypatch,
                                         ingest_base):
    """Session transcripts (documentKind='transcript') do NOT consume the
    docs cap (T2-P2a): 9 brief docs + 1 transcript = 9 counted < 10 → the
    job runs (a session-captured Document never 402s the docs gate)."""
    _provision(client.db_path, team_id=client.team_id, max_points=1)
    _seed_documents(client, 9, kind="brief")
    _seed_documents(client, 1, kind="transcript")
    entries, blobs = _mk_files("docs/README.md")
    transport = MockGitHubDocsTransport(
        repos=["acme/repo1"],
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "completed")
    assert body["documents_indexed"] == 1
    # the transcript did not count toward the cap (9 counted, not 10)
    assert _docs_count(client) == 10  # 9 brief + 1 transcript + 1 new


def test_null_kind_doc_counts(client, tmp_path, monkeypatch, ingest_base):
    """NULL-kind Documents COUNT toward the cap — no leak (a frontmatter-
    less docs-endpoint doc is a NULL-kind doc and counts)."""
    _provision(client.db_path, team_id=client.team_id, max_points=1)
    _seed_documents(client, 10, kind=None)
    entries, blobs = _mk_files("docs/README.md")
    transport = MockGitHubDocsTransport(
        repos=["acme/repo1"],
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "failed")
    assert "documents limit reached" in body["error"], \
        "NULL-kind docs COUNT — the discriminator is COALESCE(documentKind,'') != 'transcript'"


# ── fail-closed sandbox ──────────────────────────────────────────

def test_unset_base_fails_closed(client, tmp_path, monkeypatch):
    """TORTOISE_INGEST_BASE_DIR unset ⇒ honest job failure, NO writes
    (fail-closed — the endpoint is tenant-reachable; the ingest_dir_is_safe
    'any absolute path when unset' leniency does not apply here)."""
    _provision(client.db_path, team_id=client.team_id)
    monkeypatch.delenv("TORTOISE_INGEST_BASE_DIR", raising=False)
    entries, blobs = _mk_files("docs/README.md")
    transport = MockGitHubDocsTransport(
        repos=["acme/repo1"],
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    r = client.tc.post("/v1/index/docs", json={"org": "acme"})
    body = _poll_until(client.tc, r.json()["job_id"], "failed")
    assert "TORTOISE_INGEST_BASE_DIR" in body["error"]
    assert body["documents_indexed"] == 0
    assert _docs_count(client) == 0
    state = client.tc.get("/v1/onboarding/state").json()["onboarding"]
    assert not state.get("github_docs_indexed"), \
        "no writes, no state flip on an unset sandbox"
    # #1894 parity: no progress → no stamp (see the 404-org failure test).
    assert not state.get("github_docs_indexed_at"), \
        "an unset sandbox must leave github_docs_indexed_at UNSET (no stamp " \
        "on zero progress)"


# ── cross-team isolation + kind-scoped single-flight ─────────────

def test_cross_team_job_poll_404(provisioned, mock_github, ingest_base):
    """Team B polling team A's docs job_id ⇒ 404 (team-scoped _INDEX_JOBS
    isolation, T1-P2/P2)."""
    r = provisioned.tc.post("/v1/index/docs", json={"org": "acme"})
    job_id = r.json()["job_id"]
    # simulate team B: same app, different dependency-override identity
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "some-other-team", "tier": "free", "key_id": "k2",
        "max_users": 1, "max_graphs": 1, "max_teams": 1, "max_points": 10000,
    }
    rb = provisioned.tc.get(f"/v1/index/docs/{job_id}")
    assert rb.status_code == 404
    # team A still polls fine
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": provisioned.team_id, "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1, "max_points": 10000,
    }
    body = _poll_until(provisioned.tc, job_id, "completed")
    assert body["status"] == "completed"


def test_docs_single_flight_kind_scoped(provisioned, mock_github, ingest_base,
                                        monkeypatch):
    """The docs single-flight is KIND-SCOPED: an in-flight github job does
    NOT block a docs job (different kinds), and a second docs POST reuses
    the in-flight docs job."""
    from tortoise.hosted_api import _start_index_job
    gh_job, gh_new = _start_index_job(provisioned.team_id, kind="github")
    assert gh_new is True
    docs_job, docs_new = _start_index_job(provisioned.team_id, kind="docs")
    assert docs_new is True, "a github in-flight job must not block the docs job"
    assert docs_job != gh_job
    # a second docs POST reuses the in-flight docs job
    docs_job2, docs_new2 = _start_index_job(provisioned.team_id, kind="docs")
    assert docs_new2 is False
    assert docs_job2 == docs_job
    # github-reuse direction: a second github job reuses the in-flight one
    gh_job2, gh_new2 = _start_index_job(provisioned.team_id, kind="github")
    assert gh_new2 is False
    assert gh_job2 == gh_job
    # and the github job is untouched by the docs reuse
    assert gh_job in _INDEX_JOBS
    # mark the directly-created entries terminal so the fixture teardown's
    # _drain_jobs exits immediately instead of spinning the full 5s
    for jid in (gh_job, docs_job):
        if jid in _INDEX_JOBS:
            _INDEX_JOBS[jid]["status"] = "completed"


# ── #1845: multi-repo + per-repo branch scope ────────────────────


def test_docs_job_multi_repo_scope(provisioned, mock_github, ingest_base):
    """#1845: a {repos: [...]} list scopes the walk to EXACTLY those repos
    (each default branch), instead of the org-wide resolve."""
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1"}]})
    assert r.status_code == 200
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["repos_processed"] == 1
    assert body["repos_total"] == 1
    assert body["documents_indexed"] == 2  # repo1's 2 md files only
    assert _docs_count(provisioned) == 2


def test_docs_job_invalid_repo_scope_400(provisioned, mock_github,
                                         ingest_base):
    """#1845 (review P1 parity): a malicious repo short name inside the
    scope list must be rejected (400) before reaching the GitHub URL."""
    for bad in ("../victimorg/x", "a/b?q=1", "repo name"):
        r = provisioned.tc.post("/v1/index/docs", json={
            "org": "acme", "repos": [{"repo": bad}]})
        assert r.status_code == 400, f"repo={bad!r} should 400"


def test_docs_job_specific_branch_scope(provisioned, mock_github,
                                        ingest_base):
    """#1845: a repo scope with a SPECIFIC branch walks that branch (the\n    docs mock serves a 'dev' tree for repo1)."""
    entries, blobs = _mk_files("docs/DEV.md")
    mock_github.trees["acme/repo1"]["dev"] = {
        "sha": "tree-dev", "entries": entries}
    mock_github.blobs.update(blobs)
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1", "branch": "dev"}]})
    assert r.status_code == 200
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["status"] == "completed"
    assert body["documents_indexed"] == 1  # only docs/DEV.md
    assert _docs_count(provisioned) == 1


def test_docs_job_all_branches_scope(provisioned, mock_github, ingest_base):
    """#1845: branch \"all\" walks EVERY branch of the repo — branch-qualified\n    corpus keeps doc ids BRANCH-UNIQUE (main + dev trees both staged)."""
    entries_main, blobs_main = _mk_files("docs/README.md")
    entries_dev, blobs_dev = _mk_files("docs/DEV.md")
    mock_github.trees["acme/repo1"] = {
        "main": {"sha": "tree-main", "entries": entries_main},
        "dev": {"sha": "tree-dev", "entries": entries_dev},
    }
    mock_github.blobs.update(blobs_main)
    mock_github.blobs.update(blobs_dev)
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1", "branch": "all"}]})
    assert r.status_code == 200
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["status"] == "completed"
    # 1 file on main + 1 file on dev — branch-unique doc ids, no collision
    assert body["documents_indexed"] == 2
    assert _docs_count(provisioned) == 2


def test_docs_job_invalid_branch_scope_400(provisioned, mock_github,
                                           ingest_base):
    """#1845 review P1-1: a malicious branch inside a scope must be rejected
    (400) — dot-segment traversal would escape the repo/org boundary via
    httpx URL normalization. Git refs can never contain '..', so the
    rejection is lossless."""
    for bad in ("../../victimorg/git/trees/main", "..", "//", "a b",
                "feature/x/../.."):
        r = provisioned.tc.post("/v1/index/docs", json={
            "org": "acme", "repos": [{"repo": "repo1", "branch": bad}]})
        assert r.status_code == 400, f"branch={bad!r} should 400"


def test_docs_job_default_then_all_no_duplicates(
        provisioned, mock_github, ingest_base):
    """#1845 review P2-2: the default walk and an 'all branches' re-index of
    the SAME repo must NOT duplicate Documents. walk_repo branch-qualifies
    by the RESOLVED branch (always), so the default main walk and the
    'all' main walk share the SAME doc ids (main content under main/), and
    dev content is additive under dev/."""
    entries_main, blobs_main = _mk_files("docs/README.md")
    entries_dev, blobs_dev = _mk_files("docs/DEV.md")
    mock_github.trees["acme/repo1"] = {
        "main": {"sha": "tree-main", "entries": entries_main},
        "dev": {"sha": "tree-dev", "entries": entries_dev},
    }
    mock_github.blobs.update(blobs_main)
    mock_github.blobs.update(blobs_dev)

    # 1) default walk of repo1 (main) — 1 doc
    r1 = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1"}]})
    body1 = _poll_until(provisioned.tc, r1.json()["job_id"], "completed")
    assert body1["documents_indexed"] == 1

    # 2) 'all branches' — main (same doc id, 0 NEW) + dev (1 NEW)
    r2 = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1", "branch": "all"}]})
    body2 = _poll_until(provisioned.tc, r2.json()["job_id"], "completed")
    assert body2["status"] == "completed"
    # main's README is branch-qualified under main/ — SAME doc id as the
    # default walk (which also resolved main), so it dedups; dev adds 1.
    assert body2["documents_indexed"] == 1, \
        "only the new dev doc should index (main dedups)"
    assert _docs_count(provisioned) == 2, \
        "main + dev = 2 unique docs, no duplicates from the re-index"


def test_docs_job_empty_branch_means_default(provisioned, mock_github,
                                             ingest_base):
    """#1845 (review): a scope with branch '' (the client's default
    contract) is the DEFAULT walk — not a 400 and not a scoped junk branch."""
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1", "branch": ""}]})
    assert r.status_code == 200
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["status"] == "completed"
    assert body["documents_indexed"] == 2  # repo1's main tree (default walk)


def test_docs_job_non_string_branch_400(provisioned, mock_github,
                                        ingest_base):
    """#1845 (review): a non-string branch (e.g. a number) must 400, never
    500 — the branch value reaches a GitHub URL path and must be typed."""
    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1", "branch": 5}]})
    assert r.status_code == 400, "non-string branch should 400, not 500"


def test_legacy_unqualified_corpus_cleaned(provisioned, mock_github,
                                           ingest_base):
    """#1845 review (deep bug scan): a pre-#1845 UNQUALIFIED corpus
    ({owner}/{repo}/docs/... + .manifest/{owner}/{name}.json) is removed on
    the first new-layout walk — it would otherwise be ingested under the new
    branch-qualified tree, duplicating every doc (same content, two ids)."""
    from tortoise.indexer.github_docs import GitHubDocsIndexer
    team_root = GitHubDocsIndexer.team_root(provisioned.team_id)
    legacy_docs = team_root / "acme" / "repo1" / "docs"
    legacy_docs.mkdir(parents=True, exist_ok=True)
    (legacy_docs / "README.md").write_text("# legacy\n")
    legacy_manifest = team_root / ".manifest" / "acme" / "repo1.json"
    legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest.write_text('{"tree_sha":"legacy","branch":"main"}')

    r = provisioned.tc.post("/v1/index/docs", json={
        "org": "acme", "repos": [{"repo": "repo1"}]})
    body = _poll_until(provisioned.tc, r.json()["job_id"], "completed")
    assert body["status"] == "completed"
    assert not legacy_docs.exists(), \
        "the legacy unqualified docs/ dir must be removed"
    assert not legacy_manifest.exists(), \
        "the legacy unqualified manifest must be removed"
