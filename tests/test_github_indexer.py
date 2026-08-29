"""Tests for the GitHub indexer — fetch+diff + lifecycle writes (#499, #1725).

REWRITTEN (Slice 0): the FakeSDK is DELETED — these tests drive the REAL
embedded SDK (per-test tmp-path store; server-redirected on the docker
lane) with a mock GitHub REST transport, and assert the falsification
targets of the plan:

- #1844 OBJECT-ONLY: issues → Object + lifecycle Events + Subjects, NO
  statement Points (the 1:1 issue↔statement write is removed from the
  default ingest path; the machinery is dormant for #1843)
- re-run ⇒ 0 new nodes of ANY kind (objects/events/subjects)
- close ⇒ Event `github.issue.closed` + Object.status=completed; reopen ⇒
  status open, no CORRECTS
- first ingest of an already-closed issue ⇒ `-created` only
- one-time legacy `-closed` backfill: no double-mint on fresh first-runs
- `observation` NEVER written (removed kind, ONTOLOGY §5)
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from datetime import UTC

import httpx
import pytest

from tests._embedded import _wipe_or
from tests._github_mock import MockGitHubTransport, gh_issue, gh_pr
from tortoise import github_map
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


# ── Phase 2: object-only writes (#1844) ──────────────────────────

def test_index_creates_objects_and_events_only(sdk):
    """#1844: issues → Object + lifecycle Event + Subjects, NO statement
    Points (the 1:1 statement write is gone from the default ingest path;
    the mapper helper stays dormant for #1843)."""
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    stats = _run(_indexer(t), sdk)
    assert stats["points_created"] == 0
    assert stats["statements_superseded"] == 0
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (n:Point) RETURN n.pointKind").result_set
    assert rows == [], "the default ingest path must write NO statement points"
    # Object + Event + Subject nodes materialized (the object-only path).
    # Subject count is scoped to the mapper's github-user:* nodes — the
    # projection also mints an Event-derived Subject per lifecycle event.
    assert proj.g.query("MATCH (o:Object) RETURN count(o)").result_set[0][0] == 2
    assert proj.g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
    rows = proj.g.query("MATCH (s:Subject) RETURN s.id").result_set
    assert sorted(r[0] for r in rows
                  if str(r[0]).startswith("github-user:")) == [
        "github-user:user1", "github-user:user2"]


def test_rerun_zero_new_nodes(sdk):
    """Falsification (a) + #1844: unchanged re-run ⇒ 0 NEW nodes of any
    kind — no statement points (never written) and no duplicate
    objects/events/subjects."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    stats1 = _run(_indexer(t), sdk)
    assert stats1["points_created"] == 0
    proj = sdk._get_proj()

    def _counts():
        return (
            proj.g.query("MATCH (n:Object) RETURN count(n)").result_set[0][0],
            proj.g.query("MATCH (n:Event) RETURN count(n)").result_set[0][0],
            proj.g.query("MATCH (n:Subject) RETURN count(n)").result_set[0][0],
            proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0],
        )

    before = _counts()
    stats2 = _run(_indexer(t), sdk)
    assert stats2["points_created"] == 0
    assert stats2["statements_superseded"] == 0
    assert _counts() == before, "re-run must create 0 new nodes of any kind"


# ── Lifecycle decision table ──────────────────────────────────────

def test_close_event_and_object_status(sdk):
    """close ⇒ Event github.issue.closed + Object.status=completed; the
    #1844 object-only path writes NO statement points (nothing to mutate,
    no CORRECTS)."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
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
    # Object.status projection ONLY — no points exist to mutate
    status = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.status"
    ).result_set[0][0]
    assert status == "completed"
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0


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
    # object-only (#1844): no statement points exist, so reopen can never
    # CORRECTS anything
    assert proj.g.query("MATCH ()-[r:CORRECTS]->() RETURN count(r)").result_set[0][0] == 0
    # re-poll after the reopen lifecycle transition: the diff sees no new
    # state (still open), so it must mint ZERO new events — the transition
    # events are NOT double-minted (no `-reopened-2`/`-closed-2`).
    stats = _run(_indexer(t), sdk)
    assert stats["events_minted"] == 0
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventKind"
    ).result_set
    assert sorted(r[0] for r in rows) == [
        "github.issue.closed", "github.issue.open", "github.issue.reopened"]


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


# ── P1-2/P1-3 (PR #1792): frozen creation record + monotonic transitions ─

def test_created_event_frozen_across_close_reopen(sdk):
    """P1-2: the `-created` node is FROZEN at first observation — its
    eventKind/endedAt stay byte-stable across close/reopen/re-poll cycles.
    Pre-fix: _event_plain_merge's last-writer-wins (ON MATCH SET e += props)
    rewrote the creation record with the CURRENT state on every re-run
    (open→close flipped the kind + added endedAt; reopen flipped it back)."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)  # open → -created (open kind, no endedAt)
    proj = sdk._get_proj()

    def _created():
        return tuple(proj.g.query(
            "MATCH (e:Event {eventId:'github-issue-acme/repo1-1-created'}) "
            "RETURN e.eventKind, e.endedAt").result_set[0])

    assert _created() == ("github.issue.open", None)
    # close → -closed minted; the -created record must stay open-kind + no
    # endedAt (the creation record is truth-of-first-observation)
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                 updated_at="2026-07-20T08:00:00Z")]
    _run(_indexer(t), sdk)
    assert _created() == ("github.issue.open", None), \
        "-created must be frozen after close"
    # reopen → still frozen
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="open", closed_at=None,
                 updated_at="2026-07-21T08:00:00Z")]
    _run(_indexer(t), sdk)
    assert _created() == ("github.issue.open", None), \
        "-created must be frozen after reopen"
    # re-poll the same state → still byte-identical
    _run(_indexer(t), sdk)
    assert _created() == ("github.issue.open", None), \
        "-created must be frozen across re-polls"


def test_repeated_transitions_mint_monotonic_ids(sdk):
    """P1-3: close→reopen→close mints `-closed` AND `-closed-2` (distinct
    nodes) — the event timeline keeps BOTH closes; the final close is never
    lost to a MERGE-overwrite of the first close node."""
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                 updated_at="2026-07-20T08:00:00Z")]
    _run(_indexer(t), sdk)  # close #1 → -closed
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="open", closed_at=None,
                 updated_at="2026-07-21T08:00:00Z")]
    _run(_indexer(t), sdk)  # reopen → -reopened
    t.issues_by_repo["acme/repo1"] = [
        gh_issue(1, state="closed", closed_at="2026-07-22T08:00:00Z",
                 updated_at="2026-07-22T08:00:00Z")]
    _run(_indexer(t), sdk)  # close #2 → -closed-2
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId IN "
        "['github-issue-acme/repo1-1-closed', "
        " 'github-issue-acme/repo1-1-closed-2', "
        " 'github-issue-acme/repo1-1-reopened'] "
        "RETURN e.eventId, e.endedAt ORDER BY e.eventId"
    ).result_set
    assert [tuple(r) for r in rows] == [
        ("github-issue-acme/repo1-1-closed", "2026-07-20T08:00:00Z"),
        ("github-issue-acme/repo1-1-closed-2", "2026-07-22T08:00:00Z"),
        ("github-issue-acme/repo1-1-reopened", None),
    ], "both closes must exist with distinct monotonic ids"
    # Object.status projection reflects the FINAL close
    status = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.status"
    ).result_set[0][0]
    assert status == "completed"


# ── P1-5 (PR #1792): indexer is a routing pass-through ───────────────

def test_indexer_preserves_connector_routing_props(sdk):
    """P1-5: an indexer re-run must NOT clobber the connector's
    label-derived routing on the shared Object. Pre-fix: issue_to_object
    emitted default routing props (routed_team '', routed_role
    'product-implementer', …) and the projection's _persist_extra_props
    (`SET n += extra`) overwrote the connector's values on every run."""
    proj = sdk._get_proj()
    # simulate the connector's ingest: ObjectRegistered + routing SET
    obj = github_map.issue_to_object(
        gh_issue(1), "acme/repo1",
        routed_team="epistemic-team", routed_role="implementer",
        routed_product="acme", complexity="complex", ux_rating="medium")
    proj.apply(obj)
    proj.g.query(
        "MATCH (o:Object {id:$id}) "
        "SET o.routed_team=$t, o.routed_role=$r, o.routed_product=$p, "
        "o.complexity=$c, o.ux_rating=$u",
        params={"id": obj["id"], "t": "epistemic-team",
                "r": "implementer", "p": "acme",
                "c": "complex", "u": "medium"})
    # indexer re-run on the SAME issue (default routing — pure pass-through)
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    rows = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.routed_team, o.routed_role, o.routed_product, "
        "       o.complexity, o.ux_rating"
    ).result_set[0]
    assert tuple(rows) == ("epistemic-team", "implementer", "acme",
                           "complex", "medium"), \
        "indexer re-run must preserve the connector's routing props"


# ── #1895: second-buffered ASC flush — boundary advances, no loss ──────

def test_second_block_spanning_pages_drains_and_advances_boundary(sdk):
    """#1895: a same-second block spanning pages (production shape: 1425
    items at one second + 460 newer across pages) must drain ACROSS capped
    runs with the boundary advancing per run, and must never lose low
    numbers. Pre-fix: run 1 processed page 5's boundary items high-to-low
    (a non-prefix set {1386..1425}) and minted {S, 1425, truncated}; run 2
    (DRAIN) skipped #1..#1385 forever (loss) and froze at 0 processed."""
    S = "2026-08-18T02:49:35Z"
    S1 = "2026-08-18T02:49:36Z"
    issues = [gh_issue(n, updated_at=S1) for n in range(1426, 1886)]
    issues += [gh_issue(n, updated_at=S) for n in range(1, 1426)]
    t = MockGitHubTransport(issues=issues, page_size=100)  # 19 pages
    # run 1: 460 newer + 40 boundary-lowest → {S, 40, truncated}
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["processed"] == 500
    assert stats1["cursor"] == {"updated_at": S, "number": 40,
                                 "truncated": True}
    assert stats1["issues_beyond_window"] > 0
    # runs 2-3: DRAIN drains 500 more each; boundary number strictly advances
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 500
    assert stats2["cursor"] == {"updated_at": S, "number": 540,
                                 "truncated": True}
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=500)
    assert stats3["processed"] == 500
    assert stats3["cursor"] == {"updated_at": S, "number": 1040,
                                 "truncated": True}
    # run 4: the boundary block tail (385) drains WITHOUT a cap cut → clean
    stats4 = _run(_indexer(t), sdk, cursor=stats3["cursor"], cap=500)
    assert stats4["processed"] == 385
    assert stats4["cursor"] == {"updated_at": S, "number": 1425}  # truncated gone
    # run 5 (DIFF): boundary-second items are probed, NOT re-minted — exact-once
    stats5 = _run(_indexer(t), sdk, cursor=stats4["cursor"], cap=500)
    assert stats5["events_minted"] == 0
    assert stats5["cursor"]["updated_at"] == S1  # boundary advanced past S
    # (steady-state probe: the DIFF window [S−1s,∞) re-probes the boundary
    # window — the 460 processed are the NEWER S1 block (idempotent probes,
    # 0 mints); the S block is entirely skipped (all numbers <= 1425);
    # documented §Follow-ups)
    # NO loss: every number, including the lows the pre-fix code skipped
    proj = sdk._get_proj()
    assert proj.g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 1885
    for n in (1, 40, 41, 1385, 1386, 1425, 1426, 1885):
        rows = proj.g.query(
            "MATCH (o:Object {id:$oid}) RETURN count(o)",
            params={"oid": f"github-issue-acme/repo1-{n}"}).result_set
        assert int(rows[0][0]) == 1, f"issue #{n} must be indexed (no loss)"


def test_truncated_clears_on_zero_processed_drain(sdk):
    """#1895: an exact-cap-multiple run (500 new items, cap 500) stamps
    truncated; the NEXT run (DRAIN) processes 0 and must mint a CLEAN
    boundary cursor so the run after exits DRAIN. Pre-fix: the 0-processed
    run left `last is None` → cursor untouched → truncated forever."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 501)],
        page_size=100)
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["cursor"] == {"updated_at": S, "number": 500,
                                 "truncated": True}
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 0
    assert stats2["cursor"] == {"updated_at": S, "number": 500}  # clean
    # run 3 is DIFF (its first issues request carries `since`), not DRAIN —
    # and stays exact-once
    n_before = len(t.issue_query_params())
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=500)
    assert stats3["processed"] == 0
    assert any("since" in p for p in t.issue_query_params()[n_before:]), \
        "a clean cursor must exit DRAIN (the next run uses since)"
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 500


def test_stuck_truncated_cursor_clears_on_empty_drain(sdk):
    """#1895: the exact production freeze shape — a truncated cursor whose
    backlog is FULLY indexed (the drain walk skips everything). The run
    must mint a clean cursor (not stay truncated forever) so re-polls exit
    DRAIN and stop re-walking the full stream."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(issues=[gh_issue(1, updated_at=S),
                                    gh_issue(2, updated_at=S)])
    _run(_indexer(t), sdk)  # index both
    stuck = {"updated_at": S, "number": 2, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck)
    assert stats["processed"] == 0
    assert stats["cursor"] == {"updated_at": S, "number": 2}  # truncated gone


def test_quota_break_at_item_zero_keeps_truncated(sdk):
    """#1895 (scope-verify): the truncated-clear guard `not quota_hit` is
    load-bearing. A quota break BEFORE the first item (processed=0,
    quota_hit=True, last=None) must NOT mint a clean cursor — the deferred
    backlog (items OLDER than the boundary second) would then be missed by
    the since-bounded DIFF walk → permanent silent loss. Pre-fix-adjacent:
    without the guard this test fails (cursor loses truncated)."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(issues=[gh_issue(1, updated_at=S),
                                    gh_issue(2, updated_at=S)])
    from tortoise.quota import QuotaExceededError

    def _quota_check():
        raise QuotaExceededError("limit reached (test)")

    stuck = {"updated_at": S, "number": 1, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck, quota_check=_quota_check)
    assert stats["processed"] == 0
    assert stats["quota_hit"] is True
    assert stats["cursor"]["truncated"] is True, \
        "a quota break is not a clean end — truncated must persist"
    # GUARD test (green pre- AND post-fix): passes on the current code
    # too — it only fails if an implementation writes the truncated-clear
    # WITHOUT the `not stats["quota_hit"]` guard (a quota break with 0
    # processed must keep truncated: the deferred older backlog would be
    # missed by a since-bounded DIFF walk).


def test_within_second_order_independent_advance(sdk):
    """#1895: the ASC flush is independent of the within-second tie order.
    250 items at ONE second (page_size 100, cap 100) under the mock's
    deterministic within-second shuffle (seed=1895 — an ARBITRARY tie
    order): the boundary advances 100→200→250 (clean) across capped runs
    and ALL 250 Objects index. The pre-fix per-page re-sort sliced the
    shuffled stream by page and minted non-prefix cursors (verified: 154
    numbers lost on this seed → census fails)."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 251)],
        page_size=100, shuffle_within_second=True, seed=1895)
    stats1 = _run(_indexer(t), sdk, cap=100)
    assert stats1["processed"] == 100
    assert stats1["cursor"] == {"updated_at": S, "number": 100,
                                 "truncated": True}
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=100)
    assert stats2["processed"] == 100
    assert stats2["cursor"] == {"updated_at": S, "number": 200,
                                 "truncated": True}
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=100)
    assert stats3["processed"] == 50
    assert stats3["cursor"] == {"updated_at": S, "number": 250}  # clean
    # all 250 indexed — the prefix invariant holds under an arbitrary
    # within-second tie order (no low numbers lost)
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 250


# ── P1-4 (PR #1792): DRAIN-mode backlog drain ────────────────────────

def test_drain_mode_drains_backlog_across_runs(sdk):
    """P1-4: a >2×cap multi-second multi-page backlog FULLY drains across
    DRAIN-mode runs — already-processed items (updated AFTER the cursor
    second) are skipped so the cap is spent on NEW backlog only. Pre-fix:
    the drain refetched from the top and re-processed items counted toward
    the cap, so a >2×cap backlog oscillated between two boundary seconds
    forever and the tail was never indexed."""
    from datetime import datetime, timedelta
    base = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
    issues = []
    for i in range(600):
        second = base - timedelta(seconds=i // 50)  # 12 distinct seconds
        issues.append(gh_issue(
            i + 1,
            updated_at=second.isoformat().replace("+00:00", "Z")))
    t = MockGitHubTransport(issues=issues, page_size=100)  # 6 pages
    # run 1: cap 500 → 500 processed, cursor stamped truncated
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["processed"] == 500
    assert stats1["cursor"]["truncated"] is True
    # run 2 (DRAIN): skips the 500 already-processed items, drains the
    # remaining 100 (the pre-fix oscillation never reached them)
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 100
    assert stats2["points_created"] == 0  # object-only (#1844): no statement points
    assert stats2["cursor"].get("truncated") is None  # clean window end
    # the FULL backlog is indexed across the two runs (Objects + Events)
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 600
    assert sdk._get_proj().g.query(
        "MATCH (e:Event) RETURN count(e)").result_set[0][0] == 600
    # run 3 (DIFF): idempotent — nothing NEW (re-fetched boundary items
    # are probes, zero writes)
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=500)
    assert stats3["points_created"] == 0
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 600


# ── P2 (PR #1792): indexer-level honesty ─────────────────────────────

def test_all_projection_failure_keeps_truncated(sdk):
    """#1895 (code-review P1, PR #1989): a run that FETCHES items but
    processes 0 because every fetched item failed projection is NOT a
    clean drain — the truncated-clear must NOT fire. total_fetched > 0
    with processed == 0 means the backlog is deferred by errors, not
    drained: clearing `truncated` would exit DRAIN into a since-bounded
    DIFF walk whose window (cursor.updated_at - 1s) misses the deferred
    older backlog → permanent silent loss (the same hazard the
    `not quota_hit` guard protects against). Pre-fix: cursor cleared to
    {S, 2} clean; post-fix: stays {S, 2, truncated} so the next run
    re-DRAINs and retries the backlog once projection heals."""
    S = "2026-08-18T02:49:35Z"
    S_old = "2026-08-18T02:49:33Z"  # 2s older — outside a since=S-1s DIFF window
    # #3 at S_old is the deferred backlog: NOT skipped by the DRAIN
    # boundary skip (older second), fetched, then fails projection.
    t = MockGitHubTransport(
        issues=[gh_issue(1, updated_at=S), gh_issue(2, updated_at=S),
                gh_issue(3, updated_at=S_old)],
        page_size=100)
    indexer = _indexer(t)

    def _boom(sdk, proj, repo, issue):
        raise ValueError("projection failure (test)")
    indexer._project_issue = _boom

    stuck = {"updated_at": S, "number": 2, "truncated": True}
    stats = _run(indexer, sdk, cursor=stuck)
    assert stats["processed"] == 0
    assert stats["total_fetched"] == 1  # #3 fetched (not skipped) but failed
    assert len(stats["errors"]) == 1
    assert stats["cleared_truncated"] is False
    assert stats["cursor"] == {"updated_at": S, "number": 2,
                                "truncated": True}, \
        "an all-projection-failure run is not a clean drain — " \
        "truncated must persist so the next run re-DRAINs the backlog"


def test_resolve_repos_404_raises(sdk):
    """P2: an org that 404s on BOTH orgs/ and users/ raises
    GitHubFetchError — the job fails honestly, never a silent 0-point
    complete with github_indexed=True."""
    t = MockGitHubTransport(issues=[], resolve_repos_404=True)
    indexer = _indexer(t)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(GitHubFetchError) as ei:
            loop.run_until_complete(indexer.resolve_repos("acme"))
        assert "not found" in str(ei.value) or "no access" in str(ei.value)
    finally:
        loop.close()


def test_resolve_repos_falls_back_to_user_repos(sdk):
    """#1845: when orgs/ and users/ repo lookups 404 (unknown org / the
    legacy team_id-as-org bug), resolve_repos falls back to the token's OWN
    repos (/user/repos), BOUNDED to the token login's namespace (review
    P2-1: never widens across the org boundary) — the selector lists what
    the token can see, and org-wide walks still resolve. Only when
    /user/repos ALSO fails does it raise (the 404-raises test above covers
    that)."""
    import httpx

    from tortoise.indexer.github_indexer import GitHubIndexer

    seen: list[str] = []

    class _FallbackTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            url = str(request.url)
            seen.append(url)
            if "/user" in url and "/repos" not in url:
                return httpx.Response(200, json={"login": "acme-user"},
                                      request=request)
            if "/user/repos" in url:
                # includes a repo from ANOTHER owner — must be filtered out
                # (review P2-1 org-boundary integrity)
                return httpx.Response(
                    200, json=[{"full_name": "acme-user/repo1"},
                               {"full_name": "acme-user/repo2"},
                               {"full_name": "other-org/victim"}],
                    request=request)
            return httpx.Response(404, json={}, request=request)

    indexer = GitHubIndexer("fake-token",
                            httpx_client=httpx.AsyncClient(
                                transport=_FallbackTransport()))
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        repos = loop.run_until_complete(indexer.resolve_repos("ghost-org"))
        assert repos == ["acme-user/repo1", "acme-user/repo2"]
        assert any("/user/repos" in u for u in seen), \
            "the fallback must hit /user/repos after the org/user 404s"
        assert all("other-org" not in r for r in repos), \
            "other-org repos must be filtered (P2-1 boundary)"
    finally:
        loop.run_until_complete(indexer._close())
        loop.close()


def test_list_branches_returns_names(sdk):
    """#1845: list_branches returns the branch names for a repo (used by
    the docs per-repo branch picker)."""
    import httpx

    from tortoise.indexer.github_indexer import GitHubIndexer

    class _BranchesTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            url = str(request.url)
            assert "/branches" in url
            return httpx.Response(
                200,
                json=[{"name": "main"}, {"name": "dev"},
                      {"name": "feature/x"}],
                request=request)

    indexer = GitHubIndexer("fake-token",
                            httpx_client=httpx.AsyncClient(
                                transport=_BranchesTransport()))
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        branches = loop.run_until_complete(
            indexer.list_branches("acme/repo1"))
        assert branches == ["main", "dev", "feature/x"]
    finally:
        loop.run_until_complete(indexer._close())
        loop.close()


def test_quota_break_stamps_truncated_cursor(sdk):
    """P2: a quota-interrupted run stamps the cursor `truncated` so the
    next run stays in DRAIN mode — the unprocessed tail is never silently
    dropped (a quota break is not a clean window end)."""
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    from tortoise.quota import QuotaExceededError
    calls = {"n": 0}

    def _quota_check():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise QuotaExceededError("limit reached (test)")

    stats = _run(_indexer(t), sdk, quota_check=_quota_check)
    assert stats["quota_hit"] is True
    assert stats["processed"] == 1
    assert stats["cursor"]["truncated"] is True


def test_quota_check_error_re_raised(sdk):
    """P2: a QuotaCheckError (fail-closed infra failure) is RE-RAISED in
    the per-item catch — never swallowed as a quota hit."""
    t = MockGitHubTransport(issues=[gh_issue(1), gh_issue(2)])
    from tortoise.quota import QuotaCheckError

    def _boom():
        raise QuotaCheckError("counting failed")

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(QuotaCheckError):
            loop.run_until_complete(
                _indexer(t).index_repo(sdk, "acme/repo1", quota_check=_boom))
    finally:
        loop.close()


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
    assert sdk._get_proj().g.query("MATCH (n:Object) RETURN count(n)").result_set[0][0] == 2
    # run 3: nothing left — the boundary was indexed exactly once each
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"])
    assert stats3["processed"] == 0
    assert sdk._get_proj().g.query("MATCH (n:Object) RETURN count(n)").result_set[0][0] == 2


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
    proj = sdk._get_proj()
    assert proj.g.query("MATCH (n:Object) RETURN count(n)").result_set[0][0] == 0
    assert proj.g.query("MATCH (n:Event) RETURN count(n)").result_set[0][0] == 0
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0


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
    assert stats["points_created"] == 0  # object-only (#1844): no statement points
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'github-pr-acme/repo1-7'}) RETURN e.eventKind"
    ).result_set
    assert [r[0] for r in rows] == ["github.pr.open"]


def test_resolve_repos_404_raises_without_fallback(sdk):
    """#1845 review (deep bug scan): the org-wide WALK path passes
    allow_user_fallback=False — a 404 org RAISES (fail honestly) instead of
    silently walking the token user's personal repos. The selector keeps
    the fallback (default True)."""
    import httpx

    from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            # /user would succeed, but the walk path must not use it
            return httpx.Response(404, json={}, request=request)

    indexer = GitHubIndexer("fake-token",
                            httpx_client=httpx.AsyncClient(
                                transport=_Transport()))
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(GitHubFetchError):
            loop.run_until_complete(
                indexer.resolve_repos("ghost-org", allow_user_fallback=False))
    finally:
        loop.run_until_complete(indexer._close())
        loop.close()
