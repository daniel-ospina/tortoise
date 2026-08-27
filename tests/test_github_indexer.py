"""Tests for the GitHub indexer — fetch+diff + lifecycle writes (#499, #1725).

REWRITTEN (Slice 0): the FakeSDK is DELETED — these tests drive the REAL
embedded SDK (per-test tmp-path store; server-redirected on the docker
lane) with a mock GitHub REST transport, and assert the falsification
targets of the plan:

- re-run ⇒ 0 new nodes + `updatedAt` byte-unchanged (P2-1 two-phase write)
- edit ⇒ supersede (CORRECTS, bi-temporal validFrom/validTo)
- close ⇒ Event `github.issue.closed` + Object.status=completed, NO point
  mutation; reopen ⇒ status open, no CORRECTS
- first ingest of an already-closed issue ⇒ `-created` only
- edit→supersede→revert ⇒ v3 current, no error (P1-2)
- one-time legacy `-closed` backfill: no double-mint on fresh first-runs
- `observation` NEVER written (removed kind, ONTOLOGY §5)
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import httpx
import pytest

from tests._embedded import _wipe_or
from tests._github_mock import MockGitHubTransport, gh_issue, gh_pr
from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Short-circuit the embedding model (bge-small cold load ~60s+) —
    embeddings are OPTIONAL everywhere (return-None on failure), so a
    deterministic None keeps the indexer tests fast and hermetic."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


@pytest.fixture(scope="session")
def sdk(tmp_path_factory):
    """ONE session-scoped real SDK (the plan's real-SDK + _wipe_or pattern):
    a per-test redislite server costs ~4s of teardown each (the #1005 leak
    driver the shared-projection conversion exists to avoid); a single store
    + per-test _wipe_or is hermetic and fast on both lanes."""
    from tortoise.sdk import TortoiseSDK
    store = TortoiseSDK(str(tmp_path_factory.mktemp("idx") / "idx.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean_graph(sdk):
    """Per-test hermeticity: wipe the shared graph before each test."""
    _wipe_or(sdk._get_proj())


def _indexer(transport: MockGitHubTransport) -> GitHubIndexer:
    client = httpx.AsyncClient(transport=transport)
    return GitHubIndexer("fake-token", httpx_client=client)


def _run(indexer: GitHubIndexer, sdk, repo: str = "acme/repo1", **kw) -> dict:
    """Run the indexer against a fresh event loop (hermetic per call)."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(indexer.index_repo(sdk, repo, **kw))
    finally:
        loop.close()


# ── Phase 1: fetch params ─────────────────────────────────────────

def test_fetch_pins_sort_updated_desc(sdk):
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    _run(_indexer(t), sdk)
    params = t.issue_query_params()
    assert params, "an issues request must have been made"
    assert params[0]["sort"] == "updated"
    assert params[0]["direction"] == "desc"
    assert params[0]["state"] == "all"


# ── Phase 2: statement writes ─────────────────────────────────────

def test_index_creates_statement_points_not_observations(sdk):
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 2
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN n.pointKind"
    ).result_set
    kinds = {r[0] for r in rows}
    assert kinds == {"statement"}
    assert "observation" not in kinds
    # statement props contract: externalId present, github_state absent
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "RETURN n.github_state, n.extractedFrom"
    ).result_set
    assert rows and rows[0][0] is None
    assert rows[0][1] == "https://github.com/acme/repo1/issues/1"


def test_rerun_zero_new_nodes_and_no_updatedat_churn(sdk):
    """Falsification (a) + P2-1: unchanged re-run ⇒ 0 new nodes AND the
    existing statement's updatedAt is byte-unchanged."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    stats1 = _run(_indexer(t), sdk)
    assert stats1["points_created"] == 1
    proj = sdk._get_proj()
    updated_before = proj.g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "RETURN n.updatedAt"
    ).result_set[0][0]
    stats2 = _run(_indexer(t), sdk)
    assert stats2["points_created"] == 0
    assert stats2["statements_superseded"] == 0
    assert sdk._get_proj().g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 1
    updated_after = proj.g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "RETURN n.updatedAt"
    ).result_set[0][0]
    assert updated_after == updated_before, "updatedAt must be byte-unchanged on re-run"


def test_edit_supersedes_with_corrects_and_bitemporal(sdk):
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    # edit: same issue, changed body
    t.issues_by_repo["acme/repo1"] = [gh_issue(1, body="The bug moved to the lexer.")]
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 1
    assert stats["statements_superseded"] == 1
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point {pointKind:'statement'}) "
        "WHERE n.externalId = 'github:issue:acme/repo1#1' "
        "RETURN n.id, n.status ORDER BY n.createdAt"
    ).result_set
    assert len(rows) == 2
    v1_id, v1_status = rows[0]
    v2_id, v2_status = rows[1]
    assert v1_id.endswith("_1") and v2_id.endswith("_2")
    assert v1_status == "superseded"
    assert v2_status not in ("superseded", "retracted", "archived"), \
        "the successor must be the current (non-terminal) statement"
    # CORRECTS edge: (v2)-[:CORRECTS]->(v1)
    rows = proj.g.query(
        "MATCH (a:Point {id:$new})-[:CORRECTS]->(b:Point {id:$old}) "
        "RETURN b.validTo, a.validFrom, b.outdated",
        params={"new": v2_id, "old": v1_id},
    ).result_set
    assert rows, "CORRECTS edge missing"
    valid_to, valid_from, outdated = rows[0]
    assert valid_to is not None and valid_from is not None
    assert outdated is True


def test_edit_supersede_revert_v3_current(sdk):
    """P1-2: edit→supersede→revert ⇒ v3 current, no error — a revert NEVER
    reuses the superseded v1 id."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    edited = gh_issue(1, body="Edited body.")
    t.issues_by_repo["acme/repo1"] = [edited]
    _run(_indexer(t), sdk)
    # revert to the original content
    t.issues_by_repo["acme/repo1"] = [gh_issue(1)]
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 1
    assert stats["statements_superseded"] == 1
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.externalId = 'github:issue:acme/repo1#1' "
        "RETURN n.id, n.status ORDER BY n.createdAt"
    ).result_set
    assert [r[1] for r in rows] == ["superseded", "superseded",
                                    "draft"], "v3 is current (draft, non-terminal)"
    assert [r[0][-1] for r in rows] == ["1", "2", "3"], "v3 must be current"
    # current truth = v3 (content-identical to v1 but a fresh id)
    current = proj.g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "WHERE n.status IS NULL OR NOT (n.status IN ['superseded','retracted','archived']) "
        "RETURN n.id"
    ).result_set
    assert [r[0] for r in current] == [rows[2][0]]


# ── Lifecycle decision table ──────────────────────────────────────

def test_close_event_no_point_mutation(sdk):
    """close ⇒ Event github.issue.closed + Object.status=completed; the
    statement point is content/status-UNTOUCHED (no CORRECTS, no mutation)."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    stmt_before = proj.g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "RETURN n.id, n.content, n.status, n.updatedAt"
    ).result_set[0]
    # close the issue
    closed = gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                      updated_at="2026-07-20T08:00:00Z")
    t.issues_by_repo["acme/repo1"] = [closed]
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 0
    assert stats["statements_superseded"] == 0
    # Event minted
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId ENDS WITH '-closed' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("github-issue-acme/repo1-1-closed", "github.issue.closed")]
    # Object.status projection ONLY — point untouched
    status = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.status"
    ).result_set[0][0]
    assert status == "completed"
    stmt_after = proj.g.query(
        "MATCH (n:Point {externalId:'github:issue:acme/repo1#1'}) "
        "RETURN n.id, n.content, n.status, n.updatedAt"
    ).result_set[0]
    assert stmt_after == stmt_before, "close must NOT mutate the statement point"


def test_reopen_status_open_no_corrects(sdk):
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                 updated_at="2026-07-20T08:00:00Z")]
    _run(_indexer(t), sdk)
    # reopen
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="open", closed_at=None, updated_at="2026-07-21T08:00:00Z")]
    stats = _run(_indexer(t), sdk)
    assert stats["statements_superseded"] == 0
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId ENDS WITH '-reopened' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("github-issue-acme/repo1-1-reopened", "github.issue.reopened")]
    status = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.status"
    ).result_set[0][0]
    assert status == "in_progress"
    # reopen must never CORRECTS a statement point
    assert proj.g.query("MATCH ()-[r:CORRECTS]->() RETURN count(r)").result_set[0][0] == 0


def test_first_ingest_closed_issue_created_only(sdk):
    """First ingest of an already-closed issue ⇒ `-created` ONLY (kind
    github.issue.closed) — no `-closed` minted by the normal diff."""
    t = MockGitHubTransport(issues=[
        gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                 updated_at="2026-07-20T08:00:00Z")])
    stats = _run(_indexer(t), sdk)
    assert stats["events_minted"] == 1
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("github-issue-acme/repo1-1-created", "github.issue.closed")]
    # re-run: still no `-closed` (normal diff never mints it for
    # closed-without-`-closed` — the one-time backfill is the only source)
    _run(_indexer(t), sdk)
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventId"
    ).result_set
    assert len(rows) == 1


# ── Legacy `-closed` backfill (T1-P1 + T2-P3) ─────────────────────

def test_backfill_no_double_mint_on_fresh_first_run(sdk):
    """T2-P3: on a FRESH first run the one-time backfill (scans PRE-EXISTING
    events only, before the walk mints anything) finds nothing → 0 minted;
    the freshly-minted `-created`(closed-kind) events are never
    double-minted — the normal diff re-run emits no `-closed` (the marker
    set after the first pass makes the backfill one-shot in _run_indexing)."""
    t = MockGitHubTransport(issues=[
        gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                 updated_at="2026-07-20T08:00:00Z")])
    indexer = _indexer(t)
    proj = sdk._get_proj()
    # backfill runs BEFORE the walk mints anything — pre-existing scan: 0
    assert indexer.backfill_legacy_closed(proj) == 0
    _run(_indexer(t), sdk)
    # re-run the walk (the post-first-pass re-poll) — still ONE event:
    # the normal diff never mints `-closed` for closed-without-`-closed`
    # (fresh indexer per run, as in production — the walk closes its client)
    _run(_indexer(t), sdk)
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventId"
    ).result_set
    assert [r[0] for r in rows] == ["github-issue-acme/repo1-1-created"]


def test_backfill_mints_closed_for_legacy_created(sdk):
    """T1-P1: a pre-existing `-created` event with closed kind + endedAt (a
    legacy graph minted before the fix) gets its `-closed` event."""
    proj = sdk._get_proj()
    proj.apply({
        "type": "EventRecorded",
        "eventId": "github-issue-acme/repo1-1-created",
        "eventKind": "github.issue.closed",
        "subject": "issue:acme/repo1#1",
        "object": "acme/repo1#1",
        "startedAt": "2026-07-10T10:00:00Z",
        "endedAt": "2026-07-20T08:00:00Z",
        "source": "github:acme/repo1",
        "sourceKind": "github_issue",
        "participants": [],
    })
    indexer = _indexer(MockGitHubTransport(issues=[]))
    assert indexer.backfill_legacy_closed(proj) == 1
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId ENDS WITH '-closed' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("github-issue-acme/repo1-1-closed", "github.issue.closed")]
    # idempotent — re-running the backfill mints nothing new
    assert indexer.backfill_legacy_closed(proj) == 0


# ── Cursor semantics (T2-P4 / P1-3) ───────────────────────────────

def test_cursor_persists_and_stops_walk(sdk):
    """The composite cursor is returned and a second run with it stops the
    walk — no new nodes."""
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    stats1 = _run(_indexer(t), sdk)
    # the walk ascends within a second → cursor pins the HIGHEST processed
    # number at the boundary second (T2-P4 exact-once tiebreak)
    assert stats1["cursor"] == {"updated_at": "2026-07-19T12:00:00Z", "number": 2}
    assert stats1["issues_beyond_window"] == 0
    t.issues_by_repo["acme/repo1"] = []
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"])
    assert stats2["points_created"] == 0
    assert stats2["processed"] == 0


def test_cursor_same_second_boundary(sdk):
    """T2-P4: items sharing the cursor's updated_at second with numbers ≤
    cursor.number are skipped — indexed exactly once across two runs."""
    # Both issues updated in the SAME second; run 1 processes only #1 (cap=1)
    t = MockGitHubTransport(issues=[
        gh_issue(1, updated_at="2026-07-19T12:00:00Z"),
        gh_issue(2, updated_at="2026-07-19T12:00:00Z"),
    ])
    # run 1: cap=1 → exactly ONE of the same-second issues processed (the
    # fetch's within-second order is number-desc in the mock; whichever one
    # lands first, the composite cursor pins its (updated_at, number)).
    stats1 = _run(_indexer(t), sdk, cap=1)
    assert stats1["processed"] == 1
    assert stats1["cursor"]["updated_at"] == "2026-07-19T12:00:00Z"
    # run 2 with the cursor: the OTHER same-second issue must be processed;
    # the cursor-pinned one (same second, ≤ number) must be skipped.
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=1)
    assert stats2["processed"] == 1
    assert sdk._get_proj().g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 2
    # run 3: nothing left — the boundary was indexed exactly once each
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"])
    assert stats3["processed"] == 0
    assert sdk._get_proj().g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 2


def test_truncation_reports_issues_beyond_window(sdk):
    """P1-3: with a cap below the total, job status reports N issues beyond
    the window (rel=\"last\" estimate) and the run is honest."""
    t = MockGitHubTransport(issues=[gh_issue(i) for i in range(1, 11)],
                            link_rel_last_page=2)  # 2 pages × 100 ⇒ est 200
    stats = _run(_indexer(t), sdk, cap=3)
    assert stats["processed"] == 3
    assert stats["issues_beyond_window"] > 0
    assert stats["total_fetched"] == 3


def test_mid_walk_401_honest_fail(sdk):
    """T1-P13: a 401 mid-walk ⇒ GitHubFetchError raised (the caller marks
    the job failed with the readable error); the cursor is never advanced
    past unprocessed items — no partial writes past the failure."""
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)],
                            failures=[(401, 1)])
    with pytest.raises(GitHubFetchError) as ei:
        _run(_indexer(t), sdk)
    assert "401" in str(ei.value)
    assert "auth failed" in str(ei.value).lower()
    # nothing was written (the walk failed on the first request)
    assert sdk._get_proj().g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0


def test_fetch_error_raises_for_transport(monkeypatch):
    """429s exhaust bounded retries → GitHubFetchError, never a silent
    partial walk (backoff sleeps short-circuited for the test)."""
    import asyncio

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    t = MockGitHubTransport(issues=[gh_issue(1)], failures=[(429, 10)])
    indexer = _indexer(t)
    loop = asyncio.new_event_loop()

    async def _go():
        client = await indexer._get_client()
        with pytest.raises(GitHubFetchError):
            await indexer._fetch_items(client, "acme/repo1", None, 500)

    try:
        loop.run_until_complete(_go())
    finally:
        loop.close()


# ── PRs ───────────────────────────────────────────────────────────

def test_pr_events_minted(sdk):
    t = MockGitHubTransport(issues=[gh_issue(1), gh_pr(7)])
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 1  # PRs get events only, no statements
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'github-pr-acme/repo1-7'}) RETURN e.eventKind"
    ).result_set
    assert [r[0] for r in rows] == ["github.pr.open"]
