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
import re

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from datetime import UTC
from pathlib import Path

import httpx
import pytest

from tests._embedded import _wipe_or
from tests._github_mock import MockGitHubTransport, gh_issue, gh_pr
from tortoise import github_map
from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer
from tortoise.log import EventLog
from tortoise.projection import _replay_write_scope  # #7951 §17.1: replay folds need the scope


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


# ── Liveness progress beats (#3539 §D) ───────────────────────────

def test_index_repo_beats_progress_during_walk(sdk):
    """#3539 §D: ``index_repo`` beats ``on_progress`` DURING the walk.

    The caller's single-flight claim is kept alive by REAL progress, so the
    walk must beat (a) on every completed GitHub API call — the network-yield
    boundary, where a long paginated fetch spends its time — and (b) every
    ``_PROGRESS_EVERY_ITEMS`` processed items, because the projection loop is
    synchronous and would otherwise go silent for a whole repo.
    """
    from tortoise.indexer.github_indexer import _PROGRESS_EVERY_ITEMS
    n = _PROGRESS_EVERY_ITEMS * 2 + 1
    t = MockGitHubTransport(issues=[gh_issue(i) for i in range(1, n + 1)],
                            page_size=_PROGRESS_EVERY_ITEMS)
    beats: list[int] = []
    stats = _run(_indexer(t), sdk, on_progress=lambda: beats.append(1))
    assert stats["processed"] == n
    pages = len(t.issue_query_params())
    assert pages >= 3, "the walk must paginate — per-page beats are the point"
    # ≥ one beat per completed API call + one per _PROGRESS_EVERY_ITEMS items
    assert len(beats) >= pages + n // _PROGRESS_EVERY_ITEMS, (
        "the walk must beat per API call AND every _PROGRESS_EVERY_ITEMS "
        f"items (saw {len(beats)} beats over {pages} pages / {n} items)")


def test_index_repo_beats_across_retry_backoff(sdk):
    """#3539 §D: a bounded retry/backoff wait is a LIVE run waiting.

    The beat after each backoff keeps the claim from going stale across a
    long ``Retry-After``, so a rate-limited-but-healthy walk is never
    presumed dead and duplicated."""
    t = MockGitHubTransport(issues=[gh_issue(1)], failures=[(500, 1)])
    beats: list[int] = []
    stats = _run(_indexer(t), sdk, on_progress=lambda: beats.append(1))
    assert stats["processed"] == 1
    # the injected 500 forces one backoff sleep — WITHOUT its beat the run
    # records only the one successful response (1 beat), so a long
    # rate-limited wait could go stale mid-run.
    assert len(beats) >= 2, (
        "a retry backoff must beat — a waiting run is not a dead run")


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
    """close ⇒ Event github.issue.closed + the Object's POSITION advanced to the
    terminal stage; the #1844 object-only path writes NO statement points
    (nothing to mutate, no CORRECTS).

    ⛔ #13143 (S0): the connector close fold writes the POSITION axis now
    (`pipelineStageId='done'`, `pipelineSlot='terminal'`, `isClosed=True`) and
    NOT the lifecycle word this test used to assert (`status='completed'`, which
    the four-word axis cannot hold). `status` stays NULL on purpose — a progress
    event is not a lifecycle ending (design §3.2) — and that half is asserted
    too, so a future re-introduction of a lifecycle write here is caught.
    """
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
    # Object POSITION projection ONLY — no points exist to mutate. The status
    # word is asserted NULL: S0 made this fold a STAGE move, never a lifecycle
    # write.
    status, stage, slot, closed = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.status, o.pipelineStageId, o.pipelineSlot, o.isClosed"
    ).result_set[0]
    assert (stage, slot, closed) == ("done", "terminal", True), (
        "the close event did not advance the Object to the terminal stage")
    assert status is None, (
        "a connector close folded a LIFECYCLE word — S0 moved this fold to the "
        "position axis, and `status` is the four-word lifecycle axis")
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
    status, stage, slot, closed = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.status, o.pipelineStageId, o.pipelineSlot, o.isClosed"
    ).result_set[0]
    # S0: the reopen fold moves the POSITION back to the active slot and clears
    # the durable `isClosed` latch (a terminal lifecycle ending could not be
    # cleared at all, which is why the closing fold targets a terminal STAGE).
    assert (stage, slot, closed) == ("in_progress", "active", False), (
        "the reopen event did not move the Object back to the active stage")
    assert status is None, (
        "a connector reopen folded a LIFECYCLE word onto the four-word axis")
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
    # The Object's POSITION reflects the FINAL close (S0: the terminal stage
    # plus the durable latch, not a lifecycle word).
    stage, slot, closed, status = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.pipelineStageId, o.pipelineSlot, o.isClosed, o.status"
    ).result_set[0]
    assert (stage, slot, closed) == ("done", "terminal", True), (
        "the second close did not leave the Object terminal")
    assert status is None, (
        "a connector close folded a LIFECYCLE word onto the four-word axis")


def test_a_row_whose_position_the_fold_cannot_resolve_is_never_read_as_open(sdk):
    """#13222 round-2 review, the residual arm (P1): `cross-pipeline` and
    `unresolvable-current-stage`.

    `apply_stage_fold` refuses two rows the earlier pins do not cover, and BOTH are
    one supported PUBLIC write away:

      * `cross-pipeline` — `update_knowledge(id, pipelineStageKey='venture:deal')`
        stores a foreign key beside the id, and the fold will not read that id as
        another pipeline's stage;
      * `unresolvable-current-stage` — `update_knowledge(id, pipelineStageId='nope')`
        stores an id the declaration does not contain, and the fold fails closed
        rather than rewrite `isClosed` on a row it cannot place.

    In both the fold writes NOTHING, so a reader that answered "open" minted a
    `-closed` (then `-closed-2`, …) on every poll FOR EVER — measured on the
    merged head: `1/1/1` on both shapes. The read now answers `None` ("cannot
    place this row"), which is loop-free: `prev_state is None` gates the
    transition arm off and the `-created` mint is guarded by `_event_in_graph`.
    That is the same answer `planned_occurrence.plan_position` gives these two
    refusals — it treats them as unresolvable and refuses to call such a row
    open/upcoming.

    ⚠️ The trade, stated: a genuinely closing issue whose row carries a broken
    position records no `-closed`. That is bounded and inspectable; an unbounded
    mint loop on the journal is neither.

    ⛔ AND THE ORDER IS THE FIX (#13288 F1/F3, the marker-carrying subset below).
    A guard with the right answer in the wrong place only holds for the rows that
    reach it: `is_position_ended` short-circuits on `bool(is_closed)`
    (`live.py:154`) and never consults the slot, so while this guard sat BELOW the
    polarity arms a row carrying a truthy `isClosed` *and* a broken position
    answered `"closed"` — and against an OPEN issue that mints `-reopened`,
    `-reopened-2`, … on every poll, for ever (`[1,1,1]` measured at head
    `52dd2a6`). Such a row is one supported write sequence away: close the issue
    normally (the fold writes `isClosed=true` + `done`), then
    `update_knowledge(id, pipelineStageId='nope')`. The two marker-free shapes
    above cannot observe that: they answer `None` through the trailing
    `status is None` arm, so they stay green under the arm-order mutation. The
    loop below is the pin for the ORDER.
    """
    oid = "github-issue-acme/repo1-1"
    shapes = {
        "a foreign pipelineStageKey (cross-pipeline)": (
            "pipelineStageKey", "venture:deal"),
        "a pipelineStageId the declaration does not contain": (
            "pipelineStageId", "nope"),
    }
    for why, (prop, value) in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        # through the PUBLIC door — the shape a caller can actually create
        sdk.update_knowledge(oid, **{prop: value})
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="closed", closed_at="2026-07-20T08:00:00Z",
            updated_at="2026-07-20T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == [0, 0, 0], (
            f"{why}: the fold refuses this row, so the read must not call it open "
            f"— every poll minted another close ({mints})")

    # ⛔ THE MARKER-CARRYING SUBSET — the arm-order pin (#13288 F1, F3). Every poll
    # here is an OPEN issue, which is the direction a `"closed"` answer loops in,
    # and the row is built by the reachable sequence rather than by hand.
    for why, (prop, value) in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        # 1. a REAL close, through the fold: `isClosed=true` + the terminal stage.
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="closed", closed_at="2026-07-20T08:00:00Z",
            updated_at="2026-07-20T08:00:00Z")]
        assert _run(_indexer(t), sdk)["events_minted"] == 1
        # 2. the break, through the same PUBLIC door the shape is reachable by.
        sdk.update_knowledge(oid, **{prop: value})
        closed_marker, stage = proj.g.query(
            "MATCH (o:Object {id:$id}) RETURN o.isClosed, o.pipelineStageId",
            params={"id": oid}).result_set[0]
        assert closed_marker and stage, (
            "the fixture cannot show the marker-carrying shape: it needs a truthy "
            f"isClosed AND a stage id (got {closed_marker!r}, {stage!r})")
        # 3. GitHub says OPEN again — the state the issue is in for its whole life
        # and the one in which `"closed"` mints a reopen on EVERY poll.
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="open", updated_at="2026-07-22T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == [0, 0, 0], (
            f"{why}, with a truthy `isClosed`: the fold cannot place this row, so "
            f"the read must not call it CLOSED — every poll minted another reopen "
            f"({mints}); the unresolvable guard was shadowed by the position arm")

    # …and the CONTROL, on the same reachable sequence: an `isClosed=true` row the
    # fold CAN place must still be READ as closed, so a guard that answered `None`
    # for everything cannot pass the loop above.
    proj = sdk._get_proj()
    proj.g.query("MATCH (o:Object) DETACH DELETE o")
    proj.g.query("MATCH (e:Event) DETACH DELETE e")
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-22T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [1, 0, 0], (
        "a placeable `isClosed=true` row must still be READ as closed and record "
        f"its reopen exactly once ({mints}) — `None` for EVERYTHING is not the fix")


def test_a_FALSY_isClosed_with_NO_position_still_withholds_the_open_word(sdk):
    """The STATUS-word path to `"open"` had no pin — the gap this one closes.

    `fall_through` becomes `"open"` two ways — a stage/slot PROP is present, or the
    legacy `status` word maps to it through `_STATUS_TO_STATE` (`in_progress`,
    `live`, `open`). Every other falsy pin exercises the first way or a
    non-`"open"` word, so a narrowing of the arm to position-carrying rows survived
    the whole suite while restoring the measured defect for this one.

    A position-less row is ordinary: a first ingest that records no stage fold leaves
    none, and a pre-S0 row may never have had any. It is not live either (the
    Integer-`0` marker fails `coalesce(n.isClosed, false) = false`), so the close
    fold is never dispatched for it and `"open"` mints a `-closed` on every poll of a
    CLOSED issue — the same unbounded journal growth, reached without a stage prop.
    """
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    # The word and the marker through the PUBLIC DOOR; the row carries no position.
    sdk.update_knowledge(oid, status="live", isClosed=0)
    proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) "
                 "REMOVE o.pipelineStageId, o.pipelineSlot")
    stored = proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) RETURN o.status, o.isClosed, "
        "typeof(o.isClosed), o.pipelineStageId, o.pipelineSlot").result_set[0]
    # PREMISE PIN, not coverage: a door or a projection that started coercing the
    # marker, dropping the word, or recording a position would leave the pin below
    # passing for a shape it is not about — so the precondition is asserted, never
    # assumed. The diff adds READS only, so these can not vary under a mutation of
    # it and must not be counted as guards (#13326 F2, agent-infra#3442 rule 3).
    assert stored[1] == 0 and stored[2] == "Integer", (
        f"the door did not store the uncoerced integer this pin is about: {stored!r}")
    assert stored[0] == "live", (
        f"the word this pin is about (one that maps to `open`) was not stored: {stored!r}")
    assert stored[3] is None and stored[4] is None, (
        f"the precondition is not the POSITION-LESS shape: {stored!r}")
    # GitHub CLOSED over the row: the withheld `"open"` is what made this loop.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    kinds = sorted(r[0] for r in proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventKind").result_set)
    assert mints == [0, 0, 0], (
        "a position-less row with a falsy marker was read as OPEN from its status "
        f"word, so a CLOSED issue minted a close per poll ({mints}); events {kinds}")
    assert "github.issue.closed" not in kinds, (
        f"a false close was minted through the status-word path: {kinds}")
    # …and the reopen direction records nothing either — `None` is the answer, not
    # `"closed"`: this row has no `"closed"` reading to preserve.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    assert [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)] == [0, 0], (
        "a falsy-marker row with no position and no `closed` word must record no "
        "transition in either direction")


def test_a_FALSY_non_boolean_isClosed_does_not_mint_a_close_every_poll(sdk):
    """#13326 (measured on `origin/main` `54682177bc`), the half that had no pin: `isClosed=0`.

    ⚠️ Attribution corrected (#13326 review P3): this half was NOT #13288 round-2 `F1`
    — that finding is "the position arm shadows the unresolvable guard, and the new
    comment says it cannot", and no #13288 comment mentions `isClosed=0`, `Integer`,
    or a falsy non-boolean marker. The closest prior record is #13289's review P3
    ("a falsy non-boolean `outdated` (`0`, `''`) reads live in Python but terminal in
    Cypher — RECORDED, NOT FIXED") — the same excluded middle one FIELD over; the
    `isClosed` field itself was found for #13326.

    `create_object(..., isClosed=1)` is documented at the truthy pin BELOW
    (`test_a_truthy_non_boolean_isClosed_does_not_mint_a_close_every_poll`); the
    FALSY half is reachable through EITHER props door — there is no partition to
    justify (#13326 review, second lane: `create_object("x", objectKind=
    "core:issue", isClosed=0)` stores `['x', 0, 'Integer']` too, measured). Every
    props door stores the marker VERBATIM and none coerces; the `update_knowledge`
    spelling below is the one this pin uses (measured on the merged head:
    `typeof(o.isClosed)` → `['Integer']`). The two sides then disagree
    irreconcilably: this module reads it by `bool()` (→ live), while the fold's
    live-holder probe compares `coalesce(n.isClosed, false) = false` in Cypher,
    where `0 = false` is FALSE under FalkorDB's strict types (measured:
    `RETURN 0 = false, typeof(0)` → `[False, 'Integer']`; vendor-documented, and
    `toBoolean(0)` is the explicit coercion). So the row has NO live holder, and no
    CLOSING fold is dispatched for it — the marker is never repaired from the close
    side. (Trigger-reach is a separate fact and the module docstring states it: a
    `github.issue.reopened` fold reached by NAME DOES rewrite the marker, which is
    exactly why the arm keeps a `"closed"` answer wherever the position or a legacy
    `status` word already records an end.)

    Measured pre-fix on the merged head, four polls of a CLOSED issue: mints
    `[1, 1, 1, 1]`, four permanent `-closed` Events, the row byte-identical. The
    read now WITHHOLDS THE `"open"` ANSWER for a falsy non-boolean marker on a
    position that records no end — it answers `None` where the answer would
    otherwise be `"open"`, which gates the transition arm off. It does NOT answer
    `None` everywhere (#13326 review P2): where the position records an end, or a
    legacy `status` word already says closed, the `"closed"` answer is kept, because
    that is the reading the reopen fold reaches and REPAIRS.

    ⛔ The `[0,0,0,0]` polls alone do NOT test that answer (#13326 F2): the
    reading this arm REJECTS, `"closed"`, mints nothing there either, because
    GitHub says closed and `issue_to_event` needs a change. The discriminator at
    the end — a REOPEN over the same row — is what separates them.
    """
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    # The PUBLIC DOOR, exactly as a caller reaches it — not a raw query.
    sdk.update_knowledge("github-issue-acme/repo1-1", isClosed=0)
    stored = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.isClosed, typeof(o.isClosed), o.pipelineSlot"
    ).result_set[0]
    assert stored[0] == 0 and stored[1] == "Integer", (
        "the door did not store the uncoerced integer this pin is about: "
        f"{stored!r}")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(4)]
    kinds = sorted(r[0] for r in proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventKind").result_set)
    row_after = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "RETURN o.isClosed, o.pipelineSlot").result_set[0]
    assert mints == [0, 0, 0, 0], (
        "`isClosed=0` has no live holder, so no fold can ever run on it — but it "
        f"was read as OPEN and every poll minted another close ({mints}); events "
        f"now {kinds}")
    assert kinds == ["github.issue.open"], f"a false close was minted: {kinds}"
    # PREMISE PIN, not coverage: the diff adds a READ, and a read never writes, so
    # this row can not vary under any mutation of it. It states the cost of the
    # `None` answer honestly — the marker really is left unrepaired — and must not
    # be counted as a guard (#13326 F2, agent-infra#3442 rule 3).
    assert row_after == [0, stored[2]], (
        f"the row must be untouched by the polls: {row_after!r}")
    # ⛔ THE DISCRIMINATOR (#13326 F2). Every assertion above is byte-identical under
    # the answer this arm REJECTS — `return "closed"` — so on its own the pin cannot
    # fault the decision it defends. The transition arm is gated on
    # `prev_state is not None`, so a REOPEN separates the two: `None` mints nothing,
    # while `"closed"` mints exactly one `-reopened` (that is the reading
    # `issue_to_event` acts on). This is the same observation the truthy pin uses.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [0, 0], (
        "the falsy marker was READ as a state instead of as unplaceable: a reopen "
        f"over it minted ({mints}) — the `\"closed\"` answer this arm rejects")


def test_a_FALSY_isClosed_on_an_ENDED_position_still_records_its_reopen(sdk):
    """#13326 review F1 (P1): the falsy arm must not SHADOW the position arm.

    `done` is a terminal slot, so the ordinary shape of any closed issue's row is
    `isClosed=True` + `done`/`terminal`; one `update_knowledge(id, isClosed=0)` —
    the same public door the sibling pin uses — turns it into a falsy marker on an
    ENDED position. Answering `None` for that shape (i.e. placing the falsy arm
    ABOVE the position arm — `is_position_ended`) costs the row its entire remaining
    close/reopen history: `prev_state=None` gates the transition arm off on every
    poll, so a reopen mints nothing, no fold runs, the marker is never rewritten
    to a real boolean, and the row never becomes reachable again.

    The position axis is the determinant of ended-ness (§6), and it is also the
    axis the fold WRITES and can therefore reconcile — so the ended reading wins
    here and the marker is repaired by the fold, exactly as it is for the truthy
    half. This pin fails on the arm order #13326 found (arm above the position
    arm) and passes on both the pre-#13326 reader and this one: the narrowing is
    required to PRESERVE this history, not to fix the measured bug.
    """
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    # Close it through the normal fold — the shape every closed issue has.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1
    # …then plant the verbatim integer through the PUBLIC DOOR.
    sdk.update_knowledge("github-issue-acme/repo1-1", isClosed=0)
    stored = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.isClosed, "
        "typeof(o.isClosed), o.pipelineStageId, o.pipelineSlot"
    ).result_set[0]
    assert stored[0] == 0 and stored[1] == "Integer", (
        f"the door did not store the uncoerced integer this pin is about: {stored!r}")
    assert tuple(stored[2:]) == ("done", "terminal"), (
        "the precondition is not the shape this pin is about (an ENDED position "
        f"carrying a falsy marker): {stored!r}")
    # THE ENDED POSITION WINS: the row is READ as closed, so a closed issue over it
    # mints nothing (the falsy arm answering `None` would also mint nothing — see
    # the discriminator below for the observation that separates them).
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [0, 0], (
        f"a reopen/close was minted over an already-ended position ({mints})")
    # ⛔ THE DISCRIMINATOR: the reopen after it must be RECORDED (one mint). Under
    # `prev_state=None` — the answer a "falsy marker is always unplaceable" arm
    # gives — the transition arm is gated off and this is 0.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1, (
        "the row stopped recording transitions once a falsy marker landed on an "
        "ENDED position — its close/reopen history is over (the falsy arm shadowed "
        "the position arm)")
    # …and the fold REPAIRED the marker, which is what re-arms the row for ever.
    repaired = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.isClosed, "
        "typeof(o.isClosed), o.pipelineSlot"
    ).result_set[0]
    assert repaired[1] == "Boolean" and repaired[0] is False, (
        f"the fold did not repair the marker to a real boolean: {repaired!r}")
    assert repaired[2] == "active", (
        f"the reopen must leave a live position: {repaired!r}")
    assert [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)] == [0, 0], (
        "the repaired row must be settled — nothing further to record")


def test_a_FALSY_isClosed_on_a_TERMINAL_BY_ID_row_still_records_its_reopen(sdk):
    """#13326 review F3 (P2): the arm order is load-bearing, and the DECLARED selector had no shape.

    `test_a_FALSY_isClosed_on_an_ENDED_position_still_records_its_reopen` plants its
    marker on a row whose BOTH axis props read terminal (`("done", "terminal")`), so
    the `pipelineSlot` prop alone already answers ended and an arm placed ABOVE the
    position read passes every one of its assertions. The row this pin adds is the
    one only the DECLARED selector covers: the slot PROP is stale (`active`) while
    the stage DECLARATION by id is terminal — `apply_stage_fold` resolves the stage
    by id, so the fold sees an ended position, while the `pipelineSlot` prop alone
    says not-ended. Under this head the reopen mints `1`; under the falsy arm placed
    above the position read it answers `None`, the transition arm is gated off, and
    it mints `0`.

    Honest scope (#13289 merge): the collapsed `position_ended` predicate means the
    "arm inserted between two position arms" mutant no longer exists as a shape, so
    this pin's arm-order half now coincides with the sibling pin's — what it adds is
    the DECLARED-over-PROP precedence, which no other test exercises.
    """
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    # Close it through the normal fold — the shape every closed issue has.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1
    # …then plant terminal-BY-ID + a STALE live slot prop + the verbatim integer,
    # all through the PUBLIC DOOR.
    sdk.update_knowledge("github-issue-acme/repo1-1",
                         pipelineStageId="done", pipelineSlot="active", isClosed=0)
    proj = sdk._get_proj()
    stored = proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) RETURN o.pipelineStageId, "
        "o.pipelineSlot, o.isClosed, typeof(o.isClosed)").result_set[0]
    assert tuple(stored) == ("done", "active", 0, "Integer"), (
        "the precondition is not the shape this pin is about (terminal by id, a "
        f"stale live slot prop, a falsy marker): {stored!r}")
    # THE ENDED DECLARATION WINS: the row is READ as closed, so the settled close
    # mints nothing per poll.
    assert [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)] == [0, 0], (
        "a close was minted per poll over a position ended BY ID")
    # ⛔ THE DISCRIMINATOR (the gap this pin closes): with the falsy arm placed ABOVE
    # the position read, this row answers `None` — the `pipelineSlot` prop alone said
    # not-ended — and this is 0.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1, (
        "the terminal-by-id row stopped recording transitions once a falsy marker "
        "landed on it — the falsy arm was placed above the position read")


def test_a_FALSY_isClosed_on_a_LEGACY_status_row_still_records_its_reopen(sdk):
    """#13326 review (round 3) F1 (P2): the arm must withhold `"open"` ONLY.

    A POSITION-LESS legacy row (`status='completed'`, no `pipelineStageId` /
    `pipelineSlot` — the shape a pre-S0 binary left) that also carries the verbatim
    integer marker reaches neither the position test nor the wider-term arm:
    `'completed'` is in neither `commit_ops.OBJECT_TERMINAL_STATUSES`
    (`commit_ops.py:88-89`) nor `live.TERMINAL_EXCLUDED_STATUSES`
    (`live.py:97-115`). Before this PR it fell through to `_STATUS_TO_STATE` and
    read `"closed"` — and CONVERGED, because that reading IS the one the reopen
    fold reaches and repairs: a GitHub reopen mints `-reopened`,
    `_reopen_targets_closed` is True for an Integer-`0` row exactly as for a
    truthy one (one registered incarnation, no live holder), an absent `cur_id`
    skips `apply_stage_fold`'s identity guards, and
    `stage_move_refusal(None, …, is_closed=bool(0)=False, reopen=True)` admits the
    move and writes `isClosed=False` plus a live slot (`pipeline.py:397-435`).

    An arm answering `None` for EVERY falsy marker drops that reopen for ever —
    no mint ⇒ no fold ⇒ no repair, every poll — which is exactly the silent false
    negative `_row_is_frozen`'s arm documents for `status='completed'` +
    `outdated` + `isClosed=false`. So the arm withholds only the `"open"` answer,
    and this pin is the observation that separates the two arms: it is RED under
    the unconditional-`None` arm (0 mints) and GREEN here (1 mint + a repaired
    marker).
    """
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    # The MARKER through the PUBLIC DOOR (the verbatim integer this PR is about);
    # the legacy `status` word by a RAW write, because today's door REFUSES it —
    # "a position word written as a status" (`lifecycle_vocabulary.py:313`) — so
    # this shape exists only on a row a pre-S0 binary wrote. Raw graph writes are
    # the legitimate simulation of legacy data (`live.py:100-108`, #2901). No
    # pipeline props: that is what makes the row position-less.
    sdk.update_knowledge(oid, isClosed=0)
    proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) "
                 "SET o.status='completed' "
                 "REMOVE o.pipelineStageId, o.pipelineSlot")
    stored = proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) RETURN o.status, o.isClosed, "
        "typeof(o.isClosed), o.pipelineStageId, o.pipelineSlot").result_set[0]
    assert stored[1] == 0 and stored[2] == "Integer", (
        f"the door did not store the uncoerced integer this pin is about: {stored!r}")
    assert stored[0] == "completed", (
        f"the legacy word this pin is about was not stored: {stored!r}")
    assert stored[3] is None and stored[4] is None, (
        f"the precondition is not the POSITION-LESS legacy shape: {stored!r}")
    # GitHub CLOSED over a row read "closed": nothing to record, no loop.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    assert [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)] == [0, 0], (
        "a transition was minted over a legacy row read `closed`")
    # ⛔ THE DISCRIMINATOR: the reopen this reading makes reachable must be
    # RECORDED (one mint) and must repair the marker — the unconditional-`None`
    # arm gates the transition off and mints 0.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1, (
        "the falsy arm swallowed the `closed` answer a legacy status word gives a "
        "position-less row, so the reopen the fold WOULD apply was dropped for ever")
    repaired = proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) RETURN o.isClosed, typeof(o.isClosed), "
        "o.pipelineSlot").result_set[0]
    assert repaired[1] == "Boolean" and repaired[0] is False, (
        f"the fold did not repair the marker to a real boolean: {repaired!r}")
    assert repaired[2] == "active", (
        f"the reopen must leave a live position: {repaired!r}")
    assert [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)] == [0, 0], (
        "the repaired row must be settled — nothing further to record")


def test_a_row_the_fold_will_refuse_is_never_read_as_open(sdk):
    """#13222 review round 2, finding 1 (P1): reading a SUBSET of the fold's decision.

    Round 1 fixed the re-spelling of the ended-ness test (`isClosed is True` vs the
    shared `bool()`). But the fold refuses to move a row for MORE reasons than "the
    position records an end": `_fold_object_stage` also refuses when the LIFECYCLE
    word is terminal (the status latch), and `apply_stage_fold` resolves the current
    stage from the DECLARATION by id, so a row whose `pipelineSlot` PROP was never
    written is still terminal by id. Each of those read as OPEN here while the fold
    wrote nothing — the same mint-per-poll loop one guard over (measured pre-fix:
    `1/1/1` on every shape below, the row never moving, one permanent Event per poll
    in the journal, and `-closed-N` without bound).

    The read now composes the FOLD's guard set rather than a re-spelling of part of
    it: the LATCHED lifecycle word (`_status_is_latched` → `None`), the unresolvable
    position (`_declared_stage_slot` → `None`), and the position axis itself for
    everything else, resolved from the declaration as `apply_stage_fold` resolves
    the current stage.
    """
    oid = "github-issue-acme/repo1-1"
    shapes = {
        "a terminal LIFECYCLE word with a live position": (
            "SET o.status='deprecated', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        "a terminal STAGE with no slot prop written": (
            "SET o.pipelineStageId='done', o.pipelineSlot=null, o.isClosed=null"),
        "a legacy `outdated` flag with a live position": (
            # ⚠️ NOT a fold REFUSAL: `outdated` is absent from the fold's set
            # (`commit_ops.OBJECT_TERMINAL_STATUSES`), so the fold does not refuse
            # this row — the CLOSE fold is never DISPATCHED for it (no live holder,
            # read at `entities.py:6364-6365`). It belongs in this dict because the read must
            # not call it OPEN, which is the dict's real contract.
            "SET o.outdated=true, o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        # ⛔ NOT a member: a truthy NON-boolean `isClosed` marker. It already read
        # "closed" on pre-fix `main` (`is_position_ended` reads the marker by
        # `bool()`), so no mutation of this diff can redden its row here — it pins
        # nothing (#13288 review F2, re-measured by this PR's review). The shape is
        # pinned where it DOES discriminate, in the OPEN direction:
        # `test_a_truthy_non_boolean_isClosed_does_not_mint_a_close_every_poll`.
    }
    for why, mutate in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="closed", closed_at="2026-07-20T08:00:00Z",
            updated_at="2026-07-20T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == [0, 0, 0], (
            f"{why}: the row was read as OPEN, so every poll minted another close "
            f"({mints}) while no fold can close it")
        # …and the CONTROL, on the same fixture: a row the fold WOULD move mints
        # exactly one close and then stops. Without this, a reader that never
        # answers "closed" at all would pass the loop above.
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) SET o.status=null, "
                     "o.outdated=null, "
                     "o.isClosed=false, o.pipelineStageId='in_progress', "
                     "o.pipelineSlot='active'")
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="open", updated_at="2026-07-24T08:00:00Z")]
        # Back to open — and NOTHING is minted here, which is the control's premise
        # rather than a side effect to gloss over: the reset above leaves the row at
        # a LIVE position (`isClosed=false`, `in_progress`/`active`) and GitHub says
        # `open`, so the two states AGREE and `github_map.issue_to_event` returns
        # `None` (`github_map.py:311-318`). Round-4 review finding 4: the previous
        # comment here claimed this line "mints -reopened", the opposite of what the
        # fixture makes true. Pinned rather than described.
        assert _run(_indexer(t), sdk)["events_minted"] == 0, (
            f"{why}: the reset row and GitHub's `open` agree, so this poll must mint "
            "nothing — if it minted, the control below is not measuring a close")
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="closed", closed_at="2026-07-25T08:00:00Z",
            updated_at="2026-07-25T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
        assert mints == [1, 0], (
            f"{why}: the control did not mint exactly one close then stop ({mints})")


def test_a_latched_row_never_mints_a_reopen_on_every_poll(sdk):
    """#13288 review F1 (P1): `closed` for a fold-REFUSED row loops the OPEN direction.

    The mirror of the pin above. `_fold_object_stage` returns BEFORE
    `apply_stage_fold` when the Object's `status` is in the fold's OWN latch set
    (`entities.py:6458`, `excluded = _terminal_object_statuses()` at `:6124` =
    `commit_ops.OBJECT_TERMINAL_STATUSES`), and while the word is SET no fold path
    clears it (`_STAGE_CLEAR_PROPS` is the pending trio only) — ⚠️ not "for ever": a
    coherent un-supersede does clear `superseded` (`sdk.py:56529-56538`), and this
    read resumes then because it derives from the stored row. So while the word is
    set the row cannot be moved in either direction, and a reader answering
    `"closed"` for it therefore
    disagreed with GitHub on EVERY poll of an OPEN issue — the same unbounded-journal
    class this change exists to remove, one state over. Measured against the
    pre-fix reader (this dict, at head `f64fd15fa1b`): `1/1/1` on every shape below,
    i.e. `-reopened`, `-reopened-2`, `-reopened-3`, … one Event per poll, for ever.

    ⛔ The latch is answered BEFORE the position arm, deliberately, and shape 4 is
    why: a latched row whose position is ALSO ended loops identically, because the
    status latch is tested first IN THE FOLD and `reopen` never reaches
    `apply_stage_fold`'s position guard. Answering `None` for a latch row only
    "when the position is not itself ended" leaves shape 4 minting for ever.

    `None` is the answer for a LATCHED row because the fold can never reconcile it,
    so no non-`None` value can be right: `"closed"` mints a reopen every poll (this
    loop) and `"open"` mints a close every poll (the pin above). The trade is
    symmetric and stated: a latched row records neither `-closed` nor `-reopened`.

    The `outdated` FLAG and the legacy `outdated` WORD are NOT in the fold's latch set
    (`excluded` is `OBJECT_TERMINAL_STATUSES`, which has no `outdated` member), and the
    two folds refuse them ASYMMETRICALLY — so this pin covers only their LIVE-position
    shape, where both directions must stay silent: such a row is not LIVE
    (`_terminal_excluded` is the currency predicate) and a CLOSING fold reaches an
    Object by NAME through the live-holder read, so the close fold never runs on it
    and answering `"open"` loops in the CLOSE direction (measured on this fixture:
    `1/1/1`). ⛔ It is NOT a uniform freeze: the REOPEN fold takes
    `_reopen_targets_closed` and DOES apply, so once the position is ENDED the row
    answers `"closed"` and records that reopen exactly once. That half is pinned by
    `test_an_outdated_row_answers_the_position_axis`, and a `None` for both
    directions would silently drop a reopen the projection would have applied
    (#13289 round-4 finding 2).
    """
    oid = "github-issue-acme/repo1-1"
    shapes = {
        # the public door `update_knowledge(id, status="archived")` normalises to
        # `deprecated` — the shape this PR's own body measures.
        "the lifecycle word `update_knowledge(status=…)` writes": (
            "SET o.status='deprecated', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        "an `archived` word with a live position": (
            "SET o.status='archived', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        "a LATCH word AND an ended position": (
            "SET o.status='deprecated', o.pipelineStageId='done', "
            "o.pipelineSlot='terminal', o.isClosed=true"),
        # ⚠️ These are NOT latch terms — the fold's set has no `outdated` member
        # (see the docstring above). They are here because the CLOSE fold can never
        # be DISPATCHED for such a row (no live holder), so the read must not call
        # it OPEN either.
        "a legacy `outdated` FLAG with a live position": (
            "SET o.outdated=true, o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        "a legacy `outdated` STATUS word with a live position": (
            "SET o.status='outdated', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
    }
    bad = {}
    for why, mutate in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        # The issue is OPEN, which is the state it is in for its whole life — the
        # direction every pre-existing pin leaves untested.
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        if mints != [0, 0, 0]:
            bad[why] = mints
        # …and the CLOSE direction on the SAME frozen row (#13288 review F2). This
        # is where the `outdated` flag is now pinned, having left the
        # "fold REFUSES this" dict it did not belong in: `None` means NO transition
        # is recordable in EITHER direction, so the mirror claim has to be observed
        # too — answering `"open"` here loops with `-closed` instead.
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="closed", closed_at="2026-07-20T08:00:00Z",
            updated_at="2026-07-20T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        if mints != [0, 0, 0]:
            bad[f"{why} (CLOSE direction)"] = mints
    assert not bad, (
        "the row was read as CLOSED while the fold cannot reconcile it, so every "
        f"poll minted another reopen — or as OPEN, minting a close: {bad}")

    # …and the CONTROL: deviating to `None` for EVERYTHING would also pass the loop
    # above, so a row with NO latch term must still mint its close exactly once — and
    # a reopen after it, which is impossible unless the row was READ as closed.
    proj = sdk._get_proj()
    proj.g.query("MATCH (o:Object) DETACH DELETE o")
    proj.g.query("MATCH (e:Event) DETACH DELETE e")
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) SET o.status=null, "
                 "o.outdated=null, o.pipelineStageId='in_progress', "
                 "o.pipelineSlot='active', o.isClosed=false")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [1, 0, 0], (
        "a reconcilable row must still record its first close exactly once "
        f"({mints}) — `None` for EVERYTHING is not the fix")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-22T08:00:00Z")]
    assert _run(_indexer(t), sdk)["events_minted"] == 1, (
        "the row was not READ as closed: a reopen after it minted nothing")


def test_a_FROZEN_row_mints_no_transition_in_either_direction(sdk):
    """#13289 review F1 (P1, regression): the reader's predicate became a SUPERSET.

    `test_a_row_the_fold_will_refuse_is_never_read_as_open` above pins only the
    CLOSED direction, and every shape in it fixes GitHub's state to `closed`. The
    mirror — GitHub says `open` for a row the projection refuses to move — is
    where the fix started looping: the read answered `"closed"` from a LIFECYCLE
    term the fold refuses in BOTH directions while it is set — not one "no writer
    can clear" (a coherent un-supersede DOES clear `superseded`,
    `sdk.py:56529-56538`) — so each poll minted another `-reopened`
    (`_next_transition_id` suffixes without bound: `-reopened`, `-reopened-2`, …)
    plus one journal record, for an event the projection then refused
    (`entities.py:6458`).

    A LATCHED row now answers `None` — this module's "no opinion" — so no
    transition is minted in EITHER direction, the only stable answer while the
    projection refuses to move the row.

    ⛔ Only the LATCHED word is covered here. The WIDER terms (`status='outdated'`,
    the legacy `outdated=true` flag) are NOT frozen by the fold: they answer from the
    POSITION where the row HAS one — `"closed"` once that position is ENDED, `None`
    while it is LIVE (nothing could ever close it) — and, where the row has NO
    position, `"closed"` unless the word maps to `open`, which is withheld as `None`.
    The cases are enumerated once, on `_object_state_to_issue_state`'s docstring;
    ⛔ NOT "per DIRECTION — the POSITION where the row HAS one, the WORD where it has
    none", which is one case short in BOTH halves. Round-4 finding 2 found that
    answering `None` for them silently dropped a reopen the projection WOULD apply.
    All three legs are `test_an_outdated_row_answers_the_position_axis` (the position
    legs) and `test_a_POSITIONLESS_wide_term_row_answers_one_direction_only` (the
    position-less one) below.
    """
    oid = "github-issue-acme/repo1-1"
    frozen = {
        "a terminal LIFECYCLE word (what the `archived` door stores)": (
            "SET o.status='deprecated', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=false"),
        # ⛔ THE SHAPE THAT DISCRIMINATES `_status_is_latched` (#13289 review
        # round 5, P2). Both rows above carry a LIVE position, where the
        # `_row_is_frozen` arm answers `None` too — so with arm 1 DELETED, or with
        # it merely MOVED BELOW the position test, every other assertion in this
        # file still passed and the unbounded `-reopened` loop for a latched word
        # at a `terminal` position was unpinned. Measured: arm 1 removed ->
        # `None` becomes `closed`, and GitHub `open` mints `-reopened` per poll.
        "a terminal LIFECYCLE word at an ENDED position": (
            "SET o.status='deprecated', o.pipelineStageId='done', "
            "o.pipelineSlot='terminal', o.isClosed=true"),
    }
    for why, mutate in frozen.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        assert GitHubIndexer._object_state_to_issue_state(proj, oid) is None, (
            f"{why}: a FROZEN row must answer 'no opinion', not a state — any "
            "state it answers is one no writer can ever change")
        # GitHub reports the issue OPEN: the direction that used to loop.
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="open", closed_at=None,
            updated_at="2026-07-24T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == [0, 0, 0], (
            f"{why}: the frozen row was diffed against a state it can never "
            f"reach, so every poll minted another `-reopened` ({mints}) for an "
            "event the projection refuses to fold")
        assert proj.g.query(
            "MATCH (e:Event) WHERE e.eventId CONTAINS '-reopened' "
            "RETURN e.eventId").result_set == [], (
            f"{why}: a `-reopened` was minted against a frozen row")
    # …and the CONTROL, on the same fixture with the frozen terms cleared: the
    # read has a REAL opinion again and the transition mints exactly once. Without
    # this, a reader that answered `None` for everything would pass the loop above.
    proj = sdk._get_proj()
    proj.g.query("MATCH (o:Object) DETACH DELETE o")
    proj.g.query("MATCH (e:Event) DETACH DELETE e")
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "open"
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-25T08:00:00Z",
        updated_at="2026-07-25T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [1, 0], (
        f"the control did not mint exactly one close then stop ({mints})")


def test_an_outdated_row_answers_the_position_axis(sdk):
    """#13289 round-4 finding 2: the two folds refuse ASYMMETRICALLY on `outdated`.

    `_fold_object_stage`'s latch is `commit_ops.OBJECT_TERMINAL_STATUSES`
    (`projection/entities.py:6458`), which has no `outdated` member — but the two
    folds that consume these transitions are dispatched under DIFFERENT rules, so
    neither single answer serves both directions:

    * the CLOSE fold resolves its target through `live_name_holder_ids`, which
      applies the CURRENCY predicate — and `outdated` is in it
      (`live.py` `TERMINAL_EXCLUDED_STATUSES` + the flag conjunct), so such a row has
      NO live holder and the close fold is NEVER dispatched. Nothing can close it, so
      any state this read answers about it re-fires once per poll, without bound;
    * the REOPEN fold takes `_reopen_targets_closed` (`entities.py:6389-6392`), which
      bypasses the live-holder requirement for a single registered incarnation — so it
      IS dispatched, and it APPLIES, because the latch is that narrower set.

    So the answer is taken from the POSITION where the row HAS one: `None` while the
    position is live (nothing could ever close it), the position-derived state once
    it is ended (so the reopen the projection WOULD apply is actually minted). A
    POSITION-LESS row is the same asymmetry one dimension down — and it is NOT simply
    "the WORD": the word is answered only where it says `closed`. Where it maps to
    `open` (`_STATUS_TO_STATE`: `in_progress`, `live`, `open`) the answer is `None`,
    withheld, because a close against a row with no live holder re-fires for ever;
    where it maps to NOTHING (`outdated`, or a NULL status carrying the flag) it is
    `"closed"`, since only a reopen can be minted there and that fold applies. That
    third leg is the subject of
    `test_a_POSITIONLESS_wide_term_row_answers_one_direction_only` below (the
    `status='in_progress'` + flag shape, asserted `None` against a closed issue).
    Answering `None` for both — the round-3 shape — made every assertion about the
    closed direction pass while silently DROPPING that reopen: measured `[0, 0]` and
    the row left at `done`/`terminal`/`isClosed=true`.
    """
    oid = "github-issue-acme/repo1-1"

    def _fresh():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        return proj, t

    # (a) + (b) A LIVE position on an `outdated` row can never be closed — the close
    # fold is never dispatched — so BOTH GitHub directions must mint nothing. The
    # CLOSED direction is the unbounded mint the wide term exists to stop; the OPEN
    # direction is the "accidentally quiet" one that must stay quiet.
    for why, mutate in {
        "status='outdated' (terminal to the READER, not latched by the fold)":
            "SET o.status='outdated'",
        "the legacy `outdated=true` flag": "SET o.outdated=true",
    }.items():
        for gh_state in ("open", "closed"):
            proj, t = _fresh()
            proj.g.query(
                f"MATCH (o:Object {{id:'{oid}'}}) {mutate}, "
                "o.pipelineStageId='in_progress', o.pipelineSlot='active', "
                "o.isClosed=false")
            t.issues_by_repo["acme/repo1"] = [gh_issue(
                1, state=gh_state,
                closed_at=("2026-07-20T08:00:00Z" if gh_state == "closed"
                           else None),
                updated_at="2026-07-24T08:00:00Z")]
            mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
            assert mints == [0, 0, 0], (
                f"{why}, GitHub {gh_state}, live position: nothing can ever move "
                f"this row (no live holder ⇒ the close fold is never dispatched), "
                f"so a state here re-fires every poll ({mints})")

    # (c) An ENDED position on the same row: the reopen fold IS dispatched and DOES
    # apply, so the transition must be minted — exactly once, and the row must reach
    # the live position. This is the direction the round-3 arm dropped.
    for why, mutate in {
        "status='outdated'": "SET o.status='outdated', ",
        "the legacy `outdated=true` flag": "SET o.outdated=true, ",
    }.items():
        proj, t = _fresh()
        proj.g.query(
            f"MATCH (o:Object {{id:'{oid}'}}) {mutate}"
            "o.pipelineStageId='done', o.pipelineSlot='terminal', o.isClosed=true")
        assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "closed", (
            f"{why}: an ended position is the axis the fold can move, so the read "
            "must answer it — answering 'no opinion' drops the reopen below")
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="open", closed_at=None, updated_at="2026-07-24T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
        assert mints == [1, 0], (
            f"{why}: GitHub reopened a row the projection WOULD move, so exactly "
            f"one `-reopened` must be minted and then stop ({mints})")
        stage, slot, closed = proj.g.query(
            f"MATCH (o:Object {{id:'{oid}'}}) RETURN o.pipelineStageId, "
            "o.pipelineSlot, o.isClosed").result_set[0]
        assert (stage, slot, closed) == ("in_progress", "active", False), (
            f"{why}: the reopen did not reach the row — measured "
            f"{stage!r}/{slot!r}/{closed!r}, so the fold did NOT apply and the "
            "mint above is itself a loop")


def test_a_POSITIONLESS_wide_term_row_answers_one_direction_only(sdk):
    """#13289 review rounds 6-7: the position-less row is the SAME asymmetry.

    Gating the wide arm on "the row has a position" fixed `status='completed'` and
    put the per-poll loop back for `status='in_progress'`: with no pipeline props
    the read fell through to `_STATUS_TO_STATE` and answered `"open"`, so a GitHub
    close minted `-closed` on every poll against a row whose close fold can never be
    dispatched (no live holder — `resolve_fold_name` → `live._terminal_excluded`,
    which carries the `outdated` conjunct; `entities.py:6388-6392`).

    The rule is per-direction. Withhold ONLY where the word says `open`; any other
    word — including one OUTSIDE `_STATUS_TO_STATE` (`outdated`, or a NULL status
    carrying the flag) — can only mint a REOPEN, which the fold applies, so it is
    answered `"closed"`. ⚠️ `!= "closed"` conflated "says open" with "says nothing"
    and dropped those reopens for ever.

    ⛔ The `mints` assertions drive the GITHUB state each shape can actually get
    wrong: the open-word row against a CLOSED issue (the only direction in which
    its loop is observable — against an open issue it mints nothing under either
    reader, so a shared "open" fixture could not discriminate).
    """
    oid = "github-issue-acme/repo1-1"
    closed_issue = dict(state="closed", closed_at="2026-07-26T08:00:00Z",
                        updated_at="2026-07-26T08:00:00Z")
    open_issue = dict(state="open", updated_at="2026-07-26T08:00:00Z")
    shapes = {
        # (mutate, expected read, github state, expected mints)
        # ── the word says closed: a GitHub reopen is recordable and folds ──
        "a legacy pre-S0 CLOSED row + the legacy flag": (
            "SET o.status='completed', o.outdated=true, o.pipelineStageId=null, "
            "o.pipelineSlot=null, o.isClosed=null", "closed", open_issue, [1, 0, 0]),
        # ── a word OUTSIDE the map: still only a reopen can fold ──
        "a row whose word is not in _STATUS_TO_STATE + the legacy flag": (
            "SET o.status='outdated', o.outdated=true, o.pipelineStageId=null, "
            "o.pipelineSlot=null, o.isClosed=null", "closed", open_issue, [1, 0, 0]),
        # ── round-7 review P2: a STORED FALSY `isClosed` is NOT a position. By this
        # point `is_position_ended` has answered for any TRUTHY marker, so `False`
        # and `null` carry the same position answer — the `is_closed is not None`
        # conjunct treated them differently, turned this row's `"closed"` into
        # `None`, and dropped the reopen the fold applies. ──
        "a legacy pre-S0 CLOSED row + the flag + a stored FALSY isClosed": (
            "SET o.status='completed', o.outdated=true, o.pipelineStageId=null, "
            "o.pipelineSlot=null, o.isClosed=false", "closed", open_issue, [1, 0, 0]),
        # ── the word says OPEN: a close could never be dispatched, so withhold ──
        "a legacy OPEN row + the legacy flag": (
            "SET o.status='in_progress', o.outdated=true, o.pipelineStageId=null, "
            "o.pipelineSlot=null, o.isClosed=null", None, closed_issue, [0, 0, 0]),
    }
    for why, (mutate, expected, gh_state, expected_mints) in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        got = GitHubIndexer._object_state_to_issue_state(proj, oid)
        assert got == expected, (
            f"{why}: expected {expected!r} from a position-less row carrying a wide "
            f"lifecycle term, got {got!r}")
        t.issues_by_repo["acme/repo1"] = [gh_issue(1, **gh_state)]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == expected_mints, (
            f"{why}: expected {expected_mints} against a GitHub "
            f"{gh_state['state']!r} issue, got {mints}")


def test_an_EMPTY_stored_position_prop_does_not_count_as_a_position(sdk):
    """#13289 review round 8 (P2): `''` is not a position, so it must not withhold.

    The wide arm gated on `stage_id is not None or slot is not None`, which counts a
    stored EMPTY prop as a position. `apply_stage_fold` resolves the current stage
    with `if cur_id:` (`pipeline.py:490`) and never reads `pipelineSlot` at all, so
    an empty prop is no evidence the fold can see — the row is still position-less
    and the word answer `"closed"` is the safe one (a reopen folds, measured).

    Reachable: `update_knowledge(id, pipelineStageId='')` and
    `update_knowledge(id, pipelineSlot='')` are both admitted and stored verbatim.
    """
    oid = "github-issue-acme/repo1-1"
    shapes = {
        "an EMPTY `pipelineStageId`": (
            "SET o.status='completed', o.outdated=true, o.pipelineStageId='', "
            "o.pipelineSlot=null, o.isClosed=false"),
        "an EMPTY `pipelineSlot`": (
            "SET o.status='completed', o.outdated=true, o.pipelineStageId=null, "
            "o.pipelineSlot='', o.isClosed=false"),
    }
    for why, mutate in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        got = GitHubIndexer._object_state_to_issue_state(proj, oid)
        assert got == "closed", (
            f"{why}: an empty stored prop is not a position the fold can see, so the "
            f"word answer must stand; got {got!r} — the reopen is dropped for ever")
        t.issues_by_repo["acme/repo1"] = [gh_issue(
            1, state="open", updated_at="2026-07-26T08:00:00Z")]
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        assert mints == [1, 0, 0], (
            f"{why}: the reopen must be recorded exactly once then converge ({mints})")
        stage, slot = proj.g.query(
            f"MATCH (o:Object {{id:'{oid}'}}) "
            "RETURN o.pipelineStageId, o.pipelineSlot").result_set[0]
        assert (stage, slot) == ("in_progress", "active"), (
            f"{why}: the fold did not actually move the row, so 'one mint then "
            f"converge' is untested ({stage!r}, {slot!r})")


def test_a_stale_pipelineSlot_prop_does_not_outvote_the_declaration(sdk):
    """#13289 review F1, second instance: the fold NEVER reads `pipelineSlot`.

    `apply_stage_fold` resolves the row's current stage by ID through the
    pipeline's own declaration (`pipeline.py:496`: `pipe.by_id(cur_id)`), so a
    stale or partially-written `pipelineSlot` PROP describes a different object
    than the one the fold acts on. OR-ing the prop unconditionally into the
    ended-ness answer called a row closed that the fold would happily move — the
    same mint-per-poll loop, in the reopen direction.
    """
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageId='in_progress', "
        "o.pipelineSlot='terminal', o.isClosed=false")
    assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "open", (
        "the stale `pipelineSlot` prop outvoted the DECLARATION the fold uses")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-24T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [0, 0, 0], (
        f"an OPEN issue on an OPEN row minted a transition ({mints})")


def test_a_FALSY_stageId_is_not_treated_as_unresolvable(sdk):
    """#13289 review round 5 (P1): the arm must mirror the fold's TRUTHINESS test.

    `apply_stage_fold` resolves the current stage with `cur_id = cur.get("stageId")`
    then `if cur_id:` (`pipeline.py:490`) — an EMPTY/falsy `stageId` is SKIPPED, not
    refused, so its guards are bypassed and the move is ADMITTED. `_declared_stage_slot`
    returns `None` for it (no stage carries that id), so an `is not None` test on the
    "position unresolvable" arm answered "no opinion" for a row the fold would have
    moved — and the transition was dropped for ever. `main` answered "open", minted
    the close, and the fold applied it: that is the regression this pins.

    Reachable through a supported write: `_sanitize_props` keeps `''`, so
    `update_knowledge(id, pipelineStageId='')` leaves the row in this shape.
    """
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageId='', "
        "o.pipelineSlot=null, o.isClosed=false")
    assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "open", (
        "an EMPTY `stageId` is skipped by the fold, not refused — answering 'no "
        "opinion' drops a transition the fold would apply")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [1, 0], (
        f"the close was not recorded exactly once then converged ({mints})")


def test_a_non_scalar_stored_status_does_not_crash_the_reader(sdk):
    """#13289 review F6 (P1, crash): a stored ARRAY `status` must not raise.

    Both lifecycle helpers were `frozenset` membership tests, and membership
    HASHES the value — so a stored ARRAY status raised `TypeError: unhashable
    type`. That value is reachable through a PRE-FIX SDK, a raw Cypher producer or a
    JSONL replay of either (`sdk.py:56377-56379`); it is NOT reachable through
    `update_knowledge` any more — the lifecycle doors refuse a non-string status at
    intake (`lifecycle_vocabulary.py:279-286`, reached from `_read_caller_status`),
    so `update_knowledge(id, status=['live'])` raises rather than storing. The row
    persists anyway, and the FOLD tolerates it because its own `excluded` is a LIST
    (an `==` scan, no hash).

    A raise here is not a quiet skip: `_project_issue` runs BEFORE the Object
    write and the exception lands in the per-issue handler, so that issue is
    dropped on EVERY poll while the run still reports success — one cryptic
    `repo#N: unhashable type: 'list'` per poll. Both helpers now hash only a
    string, so a non-scalar status is simply not a vocabulary word and the read
    falls through to the position axis.
    """
    from tortoise.indexer.github_indexer import (
        _row_is_frozen,
        _status_is_latched,
    )

    # Unit level: neither helper may raise on a non-scalar value.
    for shape in (["live"], {"a": 1}):
        assert _status_is_latched(shape) is False, (
            f"{shape!r} was latched, or raised")
        assert _row_is_frozen(shape, None) is False, (
            f"{shape!r} was frozen, or raised")
    # …and the scalar semantics are unchanged by the guard.
    assert _status_is_latched("deprecated") is True
    assert _status_is_latched("outdated") is False
    assert _row_is_frozen("outdated", None) is True
    assert _row_is_frozen(None, True) is True

    # End to end: the read answers from the position axis and the run reports no
    # per-issue error.
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.status=['live'], "
        "o.pipelineStageId='in_progress', o.pipelineSlot='active', "
        "o.isClosed=false")
    assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "open", (
        "a stored ARRAY status was not read from the position axis")
    stats = _run(_indexer(t), sdk)
    assert not stats.get("errors"), (
        f"a stored ARRAY status raised into the per-issue handler: "
        f"{stats.get('errors')}")


def test_a_pack_keyed_stage_is_not_resolved_against_the_core_declaration(sdk):
    """#13289 review F2 (P2): absent key and unrecognised key are DIFFERENT questions.

    `_declared_stage_slot`'s docstring promises a pack key is *"stated at the call
    site rather than guessed"*, and the helper states the two questions separately.
    ⚠️ MEASURED: that split is a CLARIFICATION, not a behaviour fix — the pre-fix
    line was, verbatim,
    `_PIPELINE_DECLS.get(str(pipeline_key or CORE_WORK_ITEM_PIPELINE_KEY))`, which
    already passed a PRESENT key through verbatim and returned `None` for an
    unrecognised one, so no row was ever read against CORE's declaration. This test
    therefore pins the CONTRACT (an unrecognised key gets no opinion; an absent one
    resolves core) and cannot redden on the pre-fix code — it is a pin of a rule
    that survived, not of a bug that was fixed. The declaration arm's real
    discriminators are the stale-prop and the armed rows below.

    Absent key → core (the fold resolves core too, `cur_key` falsy).
    Present-but-unrecognised key → no opinion on this arm. ⛔ And the caller does
    NOT fall back to the stored `pipelineSlot` prop when a TRUTHY `stageId` is
    present — that is F4's correction to F2, and the last block below is why: with
    the prop consulted, a foreign key + `pipelineSlot='terminal'` reads "closed"
    and mints a `-reopened` on every poll (`[1,1,1]`) while the connector fold
    refuses the key.
    """
    from tortoise.indexer.github_indexer import _declared_stage_slot
    from tortoise.pipeline import CORE_WORK_ITEM_PIPELINE_KEY

    # ABSENT key: the connector's own declaration, exactly as the fold resolves it.
    assert _declared_stage_slot(None, "done") == "terminal"
    assert _declared_stage_slot("", "done") == "terminal"
    assert _declared_stage_slot(CORE_WORK_ITEM_PIPELINE_KEY, "done") == "terminal"
    # PRESENT but UNRECOGNISED: no guessing. `done` collides with a CORE id whose
    # slot is `terminal`; a pack may declare it in any other slot.
    assert _declared_stage_slot("venture:deal", "done") is None, (
        "an unrecognised pipeline key was resolved against CORE — the guess the "
        "docstring forbids")
    # …and end-to-end: that row is not called ended, so an OPEN issue on it mints
    # nothing — and it minted nothing BEFORE the split either: the pre-fix helper
    # returned `None` for this key, and the pre-fix reader's own
    # `_key not in _PIPELINE_DECLS` guard (`4c4fd7c90f~1:1250-1253`) did the same.
    # ⚠️ That guard answers `None` for the CLOSED fixture below too (its `stageId` is
    # PRESENT and its key is foreign, so the guard is reached), so `[0, 0, 0]` there
    # as well. The shape that DOES loop is that same fixture on the baseline WITHOUT
    # the declaration arm, where the read falls through to
    # `if stage_id is not None or slot is not None: return "open"`.
    oid = "github-issue-acme/repo1-1"
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageKey='venture:deal', "
        "o.pipelineStageId='done', o.pipelineSlot=null, o.isClosed=false")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-24T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [0, 0, 0], (
        f"a pack-keyed row was read against CORE's declaration and looped ({mints})")
    # ⛔ …and the direction that actually FIRES (#13289 F4). The assertion above
    # cannot see the loop: with the issue OPEN, a reader answering `open` agrees
    # with GitHub and mints nothing by accident. Fetch it CLOSED — a frozen row the
    # fold refuses (`cross-pipeline`) never moves, so a reader answering `open`
    # mints a `-closed` on EVERY poll, for ever:
    #   measured WITHOUT the declaration arm — F4's own regression shape, never
    #   `4c4fd7c90f~1`: mints=[1, 1, 1], events=['github-issue-acme/repo1-1-closed']
    # The position is UNRESOLVABLE (`declared_slot is None` — unrecognised key) and
    # the prop never resolved (`pipelineSlot=null`), so no fold has moved this row
    # and none will: the answer is `None` ("no opinion"), exactly as
    # `_row_is_frozen` answers for the lifecycle half. Answering "closed" instead
    # is NOT the repair — it loops in the other direction (the mirror of F1).
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-25T08:00:00Z",
        updated_at="2026-07-25T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [0, 0, 0], (
        f"a frozen pack-keyed row was read as OPEN, so every poll minted another "
        f"close while the fold refused to move it ({mints})")
    minted = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId ENDS WITH '-closed' "
        "OR e.eventId ENDS WITH '-reopened' RETURN e.eventId").result_set
    assert [tuple(r) for r in minted] == [], (
        f"a transition was minted for a row the fold cannot move "
        f"({[tuple(r) for r in minted]})")
    # The second refusal, `unresolvable-current-stage`: a stage id absent from the
    # declaration it resolves against (no key → CORE). One supported public write
    # (`update_knowledge(id, pipelineStageId='nope')`), same frozen shape, same
    # loop — measured `[1, 1, 1]` before the arm.
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageKey=null, "
        "o.pipelineStageId='nope', o.pipelineSlot=null, o.isClosed=false")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-27T08:00:00Z",
        updated_at="2026-07-27T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [0, 0, 0], (
        f"an unresolvable stage id was read as OPEN and looped ({mints})")
    # …and the CONTROL that keeps the arm from swallowing resolvable rows. A
    # RESOLVABLE position is untouched by the arm: a keyless row at a live stage
    # still mints exactly one close and then stops.
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageKey=null, "
        "o.pipelineStageId='in_progress', o.pipelineSlot='active', "
        "o.isClosed=false")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-28T08:00:00Z")]
    _run(_indexer(t), sdk)  # back to open
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-29T08:00:00Z",
        updated_at="2026-07-29T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [1, 0], (
        f"the control did not mint exactly one close then stop ({mints}) — the "
        f"`None` arm must not swallow a row whose position DOES resolve")
    # ⛔ And the STALE-PROP mirror (#13289 F4): F2's prop fallback is right for a row
    # a PACK fold owns, but no pack fold can address an indexed issue Object
    # (no pack declares a `pipeline:` for `pm:issue`, which is what
    # `github_map.py:274` writes), so the connector fold is the only fold
    # this row will ever see — and it refuses the foreign key. Nothing clears that
    # prop, so answering from it freezes the row in the OTHER direction: below, the
    # prop says `terminal` while GitHub says OPEN, and the reader answering "closed"
    # mints a `-reopened` on EVERY poll (measured pre-fix: `[1, 1, 1]`). The arm must
    # therefore precede the fallback.
    proj.g.query(
        f"MATCH (o:Object {{id:'{oid}'}}) SET o.pipelineStageKey='venture:deal', "
        "o.pipelineStageId='released', o.pipelineSlot='terminal', o.isClosed=false")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", updated_at="2026-07-30T08:00:00Z")]
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
    assert mints == [0, 0, 0], (
        f"a frozen row with a stale `terminal` prop was read as CLOSED, so every "
        f"poll minted another reopen while the connector fold refused the foreign "
        f"key ({mints})")


def test_a_row_whose_position_the_fold_cannot_place_is_never_read_as_closed_either(sdk):
    """#13288 review round 3, finding 1 — the one refusal family the round-3 fix left.

    `apply_stage_fold` refuses a row it cannot PLACE before it ever consults the
    position guard: `cross-pipeline` when the stored `pipelineStageKey` names
    another pipeline, `unresolvable-current-stage` when the id is absent from its
    declaration. Both fire BEFORE `stage_move_refusal`, so on a row whose position
    is ENDED the reopen arm never runs, `isClosed` is never cleared, and the row
    stays "closed" for ever — the same permanent disagreement with GitHub as a
    latched row, reached through a different guard.

    The `None` answer for these two refusals already existed, but the arm sat BELOW
    the position arms, so it was reachable only while the position was still LIVE —
    the direction its own pin
    (`test_a_row_whose_position_the_fold_cannot_resolve_is_never_read_as_open`)
    measures. On an OPEN issue whose position is already ended it read "closed" and
    minted `-reopened`, `-reopened-2`, … once per poll (measured `1/1/1` here), and
    both shapes are one supported public write away on a closed issue:
    `update_knowledge(id, pipelineStageId='nope')` /
    `(id, pipelineStageKey='venture:deal')`.

    The arm is now answered BEFORE the position arms: "the fold cannot place this
    row" outranks "the position records an end", because a position is only
    reconcilable when the fold can place it. ⚠️ The trade, unchanged in kind: such a
    row records no `-closed` either. Bounded and inspectable; an unbounded mint loop
    is neither.
    """
    oid = "github-issue-acme/repo1-1"
    shapes = {
        "a pipelineStageId the declaration does not contain, with an ENDED position": (
            "SET o.pipelineStageId='nope', o.pipelineStageKey=null, "
            "o.pipelineSlot='terminal', o.isClosed=true"),
        "a foreign pipelineStageKey, with an ENDED position": (
            "SET o.pipelineStageKey='venture:deal', o.pipelineStageId='in_progress', "
            "o.pipelineSlot='active', o.isClosed=true"),
    }
    bad = {}
    for why, mutate in shapes.items():
        proj = sdk._get_proj()
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        t = MockGitHubTransport(issues=[gh_issue(1)])   # the issue stays OPEN
        _run(_indexer(t), sdk)
        proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) {mutate}")
        # The answer itself: `[0,0,0]` alone also holds for a reader that answered
        # "open", which is `main`'s pre-PR behaviour, so the PARK has to be pinned.
        if GitHubIndexer._object_state_to_issue_state(proj, oid) is not None:
            bad[why] = "read as a definite state"
            continue
        mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(3)]
        if mints != [0, 0, 0]:
            bad[why] = mints
    assert not bad, (
        "the fold cannot place this row, so every poll minted another reopen on an "
        f"OPEN issue, or the read asserted a state: {bad}")

    # …and the CONTROL, on the same fixture: a row whose position is ENDED and
    # PLACEABLE must still read "closed" and mint exactly one reopen (the fold
    # applies it and clears `isClosed`), then stop. `None` for everything is not
    # the fix, and neither is parking the position axis itself.
    proj = sdk._get_proj()
    proj.g.query("MATCH (o:Object) DETACH DELETE o")
    proj.g.query("MATCH (e:Event) DETACH DELETE e")
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj.g.query(f"MATCH (o:Object {{id:'{oid}'}}) SET o.status=null, "
                 "o.outdated=null, o.pipelineStageKey=null, o.isClosed=1, "
                 "o.pipelineStageId='in_progress', o.pipelineSlot='active'")
    assert GitHubIndexer._object_state_to_issue_state(proj, oid) == "closed", (
        "a PLACEABLE position that records an end must still read closed")
    mints = [_run(_indexer(t), sdk)["events_minted"] for _ in range(2)]
    assert mints == [1, 0], (
        "the reconcilable control must mint exactly one reopen and then stop "
        f"({mints})")


def test_a_truthy_non_boolean_isClosed_does_not_mint_a_close_every_poll(sdk):
    """#13143 review F1: the state diff must read `isClosed` the way the FOLD does.

    `isClosed` is stored VERBATIM by the public doors — `create_object(...,
    isClosed=1)` stores `1` — and `advance_stage` reads it with `bool()`. A diff
    that read it with `is True` therefore disagreed with the fold acting on the
    same row: truthy-but-not-True read as OPEN here while the fold refused with
    `terminal-guard`, so EVERY poll minted another `-closed` into the journal
    (measured pre-fix: poll1/2/3 each `events_minted=1`) and the graph said the
    issue was open for ever. The read now goes through `live.is_position_ended`,
    the same predicate the fold and every other position read use.
    """
    t = MockGitHubTransport(issues=[gh_issue(1)])
    _run(_indexer(t), sdk)
    proj = sdk._get_proj()
    # A row the fold has already ended, with the marker written by a door that
    # stores it uncoerced (`1`, not `True`) and the stage left non-terminal.
    proj.g.query(
        "MATCH (o:Object {id:'github-issue-acme/repo1-1'}) "
        "SET o.isClosed = 1, o.pipelineStageId = 'in_progress', "
        "    o.pipelineSlot = 'active'")
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="closed", closed_at="2026-07-20T08:00:00Z",
        updated_at="2026-07-20T08:00:00Z")]
    stats = _run(_indexer(t), sdk)
    assert stats["events_minted"] == 0, (
        "a row whose `isClosed` is a truthy NON-boolean was read as OPEN, so "
        "the diff minted a close for a close the graph already records")
    # …and the same answer on the next poll: one mint per RUN would still grow
    # the journal without bound, so the count must stay 0 (not "one, once").
    _run(_indexer(t), sdk)
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId STARTS WITH 'github-issue-acme/repo1-1' "
        "RETURN e.eventKind"
    ).result_set
    assert sorted(r[0] for r in rows) == ["github.issue.open"], (
        f"a repeated false close was minted: {sorted(r[0] for r in rows)}")
    # ⛔ THE DISCRIMINATOR (round-2 finding P2). `events_minted == 0` alone cannot
    # tell "the row was read as CLOSED" from "the row was not read at all"
    # (`prev_state is None`, the pre-PR bug) — both mint nothing, so that half
    # passed on the pre-PR reader. A REOPEN can only be minted when
    # `prev_state == "closed"` (the transition arm is gated on `prev_state is not
    # None`), so it is the observation that separates the two readings.
    t.issues_by_repo["acme/repo1"] = [gh_issue(
        1, state="open", closed_at=None, updated_at="2026-07-23T08:00:00Z")]
    stats = _run(_indexer(t), sdk)
    assert stats["events_minted"] == 1, (
        "the row was not READ as closed: a reopen after it minted nothing, which "
        "is what the pre-fix reader (prev_state=None, 'first ingest') does")
    reopened = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId ENDS WITH '-reopened' "
        "RETURN e.eventId, e.eventKind"
    ).result_set
    assert [tuple(r) for r in reopened] == [
        ("github-issue-acme/repo1-1-reopened", "github.issue.reopened")]


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


def test_exact_cap_multiple_mints_clean_and_next_run_is_diff(sdk):
    """#1895 (+ review round 2): an exact-cap-multiple run (500 items, cap
    500) fully drains the stream in ONE run — the walk-end flush must NOT
    stamp a spurious `truncated` (the final block is completely consumed:
    nothing is deferred). Pre-review: run 1 minted {S, 500, truncated} and
    run 2 wasted a full DRAIN re-walk processing 0 before healing; now run 1
    is clean {S, 500} and the next run goes straight to DIFF (since-bounded,
    0 processed, exact-once). The truncated-clear heal path itself remains
    covered by test_stuck_truncated_cursor_clears_on_empty_drain."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 501)],
        page_size=100)
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["processed"] == 500
    assert stats1["cursor"] == {"updated_at": S, "number": 500}  # CLEAN, no truncated
    assert stats1["cleared_truncated"] is False
    # run 2 is DIFF (its first issues request carries `since`), not DRAIN —
    # and stays exact-once (0 new mints)
    n_before = len(t.issue_query_params())
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 0
    assert any("since" in p for p in t.issue_query_params()[n_before:]), \
        "a clean cursor must exit DRAIN (the next run uses since)"
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 500


def test_truncated_clears_on_zero_processed_drain(sdk):
    """#1895: a truncated cursor whose deferred backlog is FULLY drained
    (a 0-processed DRAIN run that did NOT cap/quota-cut and whose walk
    reached the stream end) must mint a CLEAN boundary cursor so the run
    after exits DRAIN. Pre-fix: the 0-processed run left `last is None` →
    cursor untouched → truncated forever (production freeze)."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 401)],
        page_size=100)
    # A LEGACY truncated cursor whose backlog is already fully indexed: the
    # DRAIN walk skips everything, processes 0, and must clear truncated.
    _run(_indexer(t), sdk)  # index all 400
    stuck = {"updated_at": S, "number": 400, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck, cap=500)
    assert stats["processed"] == 0
    assert stats["cleared_truncated"] is True
    assert stats["cursor"] == {"updated_at": S, "number": 400,
                                "gap_audit_required": True}, \
        "the heal mints a CLEAN boundary cursor (truncated gone) + durable " \
        "gap-audit marker"
    # run 3 is DIFF (its first issues request carries `since`), not DRAIN
    n_before = len(t.issue_query_params())
    stats3 = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=500)
    assert stats3["processed"] == 0
    assert any("since" in p for p in t.issue_query_params()[n_before:]), \
        "a clean cursor must exit DRAIN (the next run uses since)"
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 400


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
    assert stats["cleared_truncated"] is True
    assert stats["cursor"] == {"updated_at": S, "number": 2,
                                "gap_audit_required": True}, \
        "truncated gone + durable heal marker"


def test_premature_walk_end_keeps_truncated(sdk):
    """#1989 review round 2 (P1, walk-completeness guard): a DRAIN walk
    that ends PREMATURELY — the final fetched page's Link omits rel="next"
    but still carries rel="last" (the API claims more pages exist: a
    truncated Link header / gateway 200-with-partial-body) — must NOT fire
    the truncated-clear. `total_fetched == 0` alone does not prove the
    backlog is drained; clearing on a walk that did not reach the stream
    end would exit DRAIN into a since-bounded DIFF whose window misses the
    un-fetched older backlog → permanent silent loss (the removed safety
    check pre-#1895: the cursor stayed truncated and re-DRAINed)."""
    S = "2026-08-18T02:49:35Z"
    # 3 items @ S + 1 deferred backlog item at an OLDER second (page 2).
    # Page 1 carries rel="last" (claiming page 2 exists) but NO rel="next"
    # (premature end) — the walk stops after page 1 with 0 processed.
    t = MockGitHubTransport(
        issues=[gh_issue(1, updated_at=S), gh_issue(2, updated_at=S),
                gh_issue(3, updated_at=S),
                gh_issue(4, updated_at="2026-08-18T02:49:33Z")],
        page_size=3, drop_next_after_page=1)
    stuck = {"updated_at": S, "number": 3, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck)
    assert stats["processed"] == 0
    assert stats["total_fetched"] == 0
    assert stats["cleared_truncated"] is False, \
        "a premature walk end (rel=last still present, no rel=next) must " \
        "NOT clear truncated — the backlog was not provably drained"
    assert stats["cursor"]["truncated"] is True
    # the deferred backlog item was never reached — it must NOT be silently
    # skipped by a since-bounded DIFF the clear would have enabled
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$oid}) RETURN count(o)",
        params={"oid": "github-issue-acme/repo1-4"}).result_set[0][0] == 0


def test_premature_walk_end_exact_cap_mints_truncated(sdk):
    """#1989 review round 3 (P1, mint-path walk-completeness guard): the
    exact-cap-at-final-block no-truncation exemption must NOT apply when
    the walk ended PREMATURELY. 500 items @ S fill the cap exactly on the
    final block, but the final page's Link carries rel="last" without
    rel="next" (truncated header / gateway partial body) — 100 OLDER items
    @ S−2s sit beyond the walk's reach. Pre-fix (round 2): the exemption
    fired (no processable items remained IN the buffered block) → clean
    {S, 500} → the next since-bounded DIFF (window S−1s) never fetches the
    S−2s backlog → permanent silent loss. Post-fix: the exemption requires
    a true stream end, so the run stamps {S, 500, truncated} and the next
    DRAIN (after the gateway recovers) indexes the hidden backlog."""
    S = "2026-08-18T02:49:35Z"
    S_old = "2026-08-18T02:49:33Z"  # 2s older — outside a since=S−1s probe
    issues = [gh_issue(n, updated_at=S) for n in range(1, 501)]
    issues += [gh_issue(n, updated_at=S_old) for n in range(501, 601)]
    t = MockGitHubTransport(issues=issues, page_size=100,
                            drop_next_after_page=5)
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["processed"] == 500
    assert stats1["cursor"] == {"updated_at": S, "number": 500,
                                 "truncated": True}, \
        "an exact-cap-at-final-block on a PREMATURE walk must stamp " \
        "truncated (the older backlog was never fetched)"
    assert stats1["total_fetched"] == 500
    # gateway recovers → next run is DRAIN (truncated) and reaches the
    # hidden older backlog
    t.drop_next_after_page = None
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 100
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 600, \
        "the hidden S−2s backlog must be indexed by the post-recovery DRAIN"


def test_premature_walk_end_uncapped_mints_truncated(sdk):
    """#1989 review round 3 (P1, mint-path walk-completeness guard): an
    UNCAPPED multi-second walk that ends PREMATURELY must mint a
    TRUNCATED cursor, not a clean newest-second one. 300 items @ S are
    processed but the final page's Link keeps rel="last" without
    rel="next" — 100 older items @ S−2s were never fetched. Pre-fix
    (round 2): `walk_complete` only gated the 0-processed clear, so this
    run minted clean {S, 300} → the next since-bounded DIFF (window S−1s)
    misses the S−2s backlog forever (silent loss). Post-fix: `not
    walk_complete` is a truncation condition on the mint path — {S, 300,
    truncated} — and the post-recovery DRAIN indexes the hidden items."""
    S = "2026-08-18T02:49:35Z"
    S_old = "2026-08-18T02:49:33Z"
    issues = [gh_issue(n, updated_at=S) for n in range(1, 301)]
    issues += [gh_issue(n, updated_at=S_old) for n in range(301, 401)]
    t = MockGitHubTransport(issues=issues, page_size=100,
                            drop_next_after_page=3)
    stats1 = _run(_indexer(t), sdk, cap=500)  # uncapped (300 < 500)
    assert stats1["processed"] == 300
    assert stats1["cursor"] == {"updated_at": S, "number": 300,
                                 "truncated": True}, \
        "an uncapped PREMATURE walk must stamp truncated, not mint clean"
    t.drop_next_after_page = None
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 100
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 400, \
        "the hidden S−2s backlog must be indexed by the post-recovery DRAIN"


def test_newest_second_mint_on_multisecond_drain(sdk):
    """#1989 review round 2 (P2): an UNCAPPED run that processes items
    spanning multiple seconds must mint the boundary at the NEWEST
    processed second (the composite cursor then expresses the full newer
    window as processed), not at the oldest appended item. Pre-fix: run 1
    minted {S_old, 200} → the next DIFF since-window re-probed and
    idempotently re-projected the whole newer S block on every poll.
    Post-fix: {S_new, 100} — the next DIFF skips the newer block entirely
    (0 mints, 0 re-projection; S_old lies OUTSIDE the since window since
    the seconds are non-adjacent)."""
    S_new = "2026-08-18T02:49:36Z"
    S_old = "2026-08-18T02:49:34Z"  # 2s older — outside the −1s probe window
    issues = [gh_issue(n, updated_at=S_new) for n in range(1, 101)]
    issues += [gh_issue(n, updated_at=S_old) for n in range(101, 201)]
    t = MockGitHubTransport(issues=issues, page_size=100)
    stats1 = _run(_indexer(t), sdk, cap=500)  # uncapped (200 < 500)
    assert stats1["processed"] == 200
    assert stats1["cursor"] == {"updated_at": S_new, "number": 100}, \
        "an uncapped multi-second run mints at the NEWEST second"
    # next DIFF: since = S_new − 1s → S_old is outside the window; the
    # newer block is skipped wholesale (≤ cursor number) → 0 processed, 0
    # mints, no loss
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 0
    assert stats2["events_minted"] == 0
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 200
    for n in (1, 100, 101, 200):
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$oid}) RETURN count(o)",
            params={"oid": f"github-issue-acme/repo1-{n}"}).result_set
        assert int(rows[0][0]) == 1, f"issue #{n} must be indexed (no loss)"


def test_shuffle_with_since_window_pagination_is_consistent(sdk):
    """#1989 review round 2 (P2): the mock's within-second shuffle must
    stay CONSISTENT across page boundaries on a since-bounded (DIFF) walk.
    The mock's Link next-URLs now carry the original query params
    (including `since`), mirroring real GitHub — so page 2+ shuffle the
    SAME filtered base list as page 1 (previously page 1 filtered while
    page 2+ did not, which could silently skip/duplicate items across the
    boundary under shuffle). The indexer's DIFF probe on a shuffled,
    paginated, since-bounded stream must be idempotent with 0 loss."""
    S = "2026-08-18T02:49:35Z"
    S_old = "2026-08-18T02:49:33Z"  # 2s older — outside a since=S−1s probe
    issues = [gh_issue(n, updated_at=S) for n in range(1, 251)]
    # #1989 review round 3 (P2 falsifiability): 50 items OLDER than the
    # probe's since window (S−1s) make the mock's `since` filter BITE —
    # pre-fix the page-1 base (filtered 250) and the page-2+ base
    # (unfiltered 300) shuffled differently and could skip/duplicate items
    # across the page boundary. Post-fix all pages filter the same 250 base.
    issues += [gh_issue(n, updated_at=S_old) for n in range(251, 301)]
    t = MockGitHubTransport(issues=issues, page_size=100,
                            shuffle_within_second=True, seed=7)
    stats1 = _run(_indexer(t), sdk, cap=500)  # index all 300
    assert stats1["processed"] == 300
    # DIFF probe with a since window (clean cursor) on the shuffled stream
    n_before = len(t.issue_query_params())
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["events_minted"] == 0, "exact-once — 0 new mints"
    assert stats2["processed"] == 0
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 300, \
        "shuffle + since + pagination must not skip or duplicate items"
    # the older backlog stays untouched (outside the since window, 0 mints)
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$oid}) RETURN count(o)",
        params={"oid": "github-issue-acme/repo1-251"}).result_set[0][0] == 1
    # every page request in the probe walk carried `since` (carried through
    # the mock's pagination URLs)
    new_queries = t.issue_query_params()[n_before:]
    assert new_queries and all("since" in p for p in new_queries), \
        "DIFF probe pages must all carry the since window"



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


# ── #1990: DIFF-mode boundary invariant (probe oscillation) ──────────

def test_diff_probe_on_dense_boundary_repo_does_not_restamp_truncated(sdk):
    """#1990: the post-drain steady state on a DENSE-BOUNDARY repo (the
    pre-boundary second holds more items than the cap — production: 1425 >
    500) must poll without re-stamping `truncated`, without regressing the
    cursor, and without re-walking the full stream.

    Shape (scaled down from the production 1425@S + 460@S1 to keep the test
    cheap — the property that matters is pre-boundary second > cap while the
    NEWEST second's block still fits the cap): 100 items at S, 30 newer at
    S1, cap 40, page_size 10.

    Pre-#1990 the DIFF window `[U−1s, ∞)` re-probed the S block with
    `_inside_cursor(drain=False)` (which skips only `== U and number <= N`),
    consumed the whole cap, minted `{S, 40, truncated}` — a cursor REGRESSED
    to the older second — and walked all 13 pages: the verified lossless-but-
    churning cycle (production: {S+1,1885} → {S,500,t} → {S,1000,t} →
    {S,1425} → {S+1,1885} → …, `truncated` re-stamped on 2 of every 4 polls
    with every poll re-walking the stream). Post-#1990 the probe skips the
    pre-boundary second wholesale and the walk stops as soon as the stream
    descends below the boundary second (everything below it is provably
    indexed — the boundary invariant)."""
    S = "2026-08-18T02:49:35Z"
    S1 = "2026-08-18T02:49:36Z"
    issues = [gh_issue(n, updated_at=S1) for n in range(101, 131)]   # 30
    issues += [gh_issue(n, updated_at=S) for n in range(1, 101)]    # 100
    t = MockGitHubTransport(issues=issues, page_size=10)
    # the drain: 10@S cap-cut → 50 → 90 → clean 100@S → the DIFF run that
    # advances to the newest second
    stats = _run(_indexer(t), sdk, cap=40)
    assert stats["cursor"] == {"updated_at": S, "number": 10,
                              "truncated": True}
    for expected in (50, 90):
        stats = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=40)
        assert stats["cursor"] == {"updated_at": S, "number": expected,
                                   "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=40)
    assert stats["cursor"] == {"updated_at": S, "number": 100}  # drained
    stats = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=40)
    assert stats["cursor"] == {"updated_at": S1, "number": 130}, \
        "precondition: the DIFF run advanced to the newest second"
    # the steady-state poll
    n_before = len(t.issue_query_params())
    probe = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=40)
    pages = len(t.issue_query_params()) - n_before
    assert probe["processed"] == 0, \
        "the pre-boundary S block must be skipped wholesale (0 cap spent)"
    assert probe["cursor"] == {"updated_at": S1, "number": 130}, \
        "the boundary must NOT regress to S and `truncated` must NOT be " \
        "re-stamped (pre-#1990: {S, 40, truncated})"
    assert probe["events_minted"] == 0, "exact-once: the probe mints nothing"
    assert probe["issues_beyond_window"] == 0, \
        "a caught-up repo report must not claim a deferred backlog"
    # fetch bound: the walk stops on the page that first carries the
    # descending second (30 boundary-second items / page_size 10 → 4 pages),
    # NOT at the stream end (pre-#1990: all 13 pages)
    assert pages == 4, f"the DIFF walk must stop at the boundary (got {pages})"
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 130


def test_diff_after_drain_cursor_does_not_pingpong_into_drain(sdk):
    """#1990: a DRAIN completion mints the clean cursor at the newest second
    it PROCESSED, which can be OLDER than the previous boundary — the next
    DIFF run therefore legitimately re-probes everything newer (idempotently,
    up to the cap). That probe must end CLEAN.

    Shape: 600 items over 12 seconds (50/sec), cap 500 = exactly 10 seconds,
    so the drain's deferred backlog (#501..#600) lives at T10/T11 while the
    cap cut sits at T9. Pre-#1990 the probe's cap fell exactly at the T9
    block's last item and was treated as a truncation → `{T9, 500,
    truncated}` → the next DRAIN re-processed the T10/T11 backlog and minted
    `{T10, 550}` again: the DIFF↔DRAIN ping-pong repeated FOREVER on this
    shape, re-walking the full stream and re-stamping `truncated` on every
    poll. Post-#1990 the cut is clean (the readable region ends after the T9
    block), so the cursor stabilises at the newest second."""
    from datetime import datetime, timedelta
    base = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
    issues = []
    for i in range(600):
        second = base - timedelta(seconds=i // 50)  # 12 distinct seconds
        issues.append(gh_issue(
            i + 1,
            updated_at=second.isoformat().replace("+00:00", "Z")))
    t = MockGitHubTransport(issues=issues, page_size=100)  # 6 pages
    stats = _run(_indexer(t), sdk, cap=500)
    assert stats["cursor"] == {"updated_at": "2026-07-19T11:59:51Z",
                              "number": 500, "truncated": True}
    # the DRAIN run drains the deferred backlog and mints a CLEAN cursor at
    # the newest second it processed (T10) — one second OLDER than the cut
    stats = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=500)
    assert stats["processed"] == 100
    assert stats["cursor"] == {"updated_at": "2026-07-19T11:59:50Z",
                               "number": 550}, \
        "precondition: the drain is clean but the boundary sits at T10"
    # poll 1 (DIFF): the legitimately-readable newer window (T0..T9) is
    # re-probed idempotently (500 probes = the whole cap) — and the cap cut
    # landing at the T9 block's last item must NOT be a truncation
    probe = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=500)
    assert probe["processed"] == 500
    assert probe["events_minted"] == 0, "exact-once — all probes idempotent"
    assert probe["cursor"] == {"updated_at": "2026-07-19T12:00:00Z",
                               "number": 50}, \
        "the probe must end CLEAN at the newest second (pre-#1990: " \
        "{T9, 500, truncated} → the next DRAIN ping-pongs back to T10)"
    # polls 2-3: stable — 0 processed, no truncated, cursor unchanged
    for poll in range(2):
        probe = _run(_indexer(t), sdk, cursor=probe["cursor"], cap=500)
        assert probe["processed"] == 0, f"poll {poll + 2} must be a no-op"
        assert probe["cursor"] == {"updated_at": "2026-07-19T12:00:00Z",
                                   "number": 50}
        assert probe["events_minted"] == 0
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 600


def test_diff_probe_does_not_steal_cap_from_new_items(sdk):
    """#1990: the probe must not spend the cap on the pre-boundary second
    and thereby starve the NEW items the poll exists to pick up.

    Fixture: cursor `{S, 100}` (clean — everything OLDER than S is already
    indexed, so the invariant holds), 3 new items at S+1, 300 already-
    indexed items at S−1s, cap 10. Pre-#1990 the walk processed the 3 new
    items AND 7 items from the S−1s probe (10 total), then minted
    `{S−1s, 206, truncated}` — the boundary regressed to the OLDER second
    and the cap was spent on items that were already indexed. Post-#1990
    only the 3 new items are processed and the boundary advances to S+1."""
    S = "2026-08-18T02:49:35Z"
    S1 = "2026-08-18T02:49:36Z"
    S_old = "2026-08-18T02:49:34Z"
    issues = [gh_issue(n, updated_at=S1) for n in range(501, 504)]   # new
    issues += [gh_issue(n, updated_at=S) for n in range(1, 101)]    # indexed
    issues += [gh_issue(n, updated_at=S_old) for n in range(200, 500)]
    t = MockGitHubTransport(issues=issues, page_size=100)
    stats = _run(_indexer(t), sdk, cap=5000)  # index everything first
    assert stats["processed"] == 403
    probe = _run(_indexer(t), sdk, cursor={"updated_at": S, "number": 100},
                 cap=10)
    assert probe["processed"] == 3, \
        "only the S+1 items are readable — the S−1s probe must not consume " \
        "the cap (pre-#1990: 3 new + 7 probes = 10)"
    assert probe["cursor"] == {"updated_at": S1, "number": 503}, \
        "the boundary advances to the new items (pre-#1990 it REGRESSED " \
        "to {S−1s, 206, truncated})"
    # the next poll is a no-op: nothing readable remains
    after = _run(_indexer(t), sdk, cursor=probe["cursor"], cap=10)
    assert after["processed"] == 0
    assert after["events_minted"] == 0
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 403


def test_diff_exact_cap_at_boundary_block_mints_clean(sdk):
    """#1990: the #1989 round-2 "exact cap at the final block is not a
    truncation" exemption must also hold when the final READABLE block is
    closed by the stream descending below the boundary second (not just at
    a natural walk end).

    Fixture: cursor `{S, 100}` clean, S holds 600 items (so exactly 500 are
    processable = the cap) and a 10-item block sits at S−1s (already
    indexed — invariant-consistent). Pre-#1990 the cap fell on the block's
    last item and stamped `truncated`, forcing a wasted DRAIN re-walk plus a
    false `gap_audit_required`; post-#1990 the mint is clean {S, 600}."""
    S = "2026-08-18T02:49:35Z"
    S_old = "2026-08-18T02:49:34Z"
    issues = [gh_issue(n, updated_at=S) for n in range(1, 601)]
    issues += [gh_issue(n, updated_at=S_old) for n in range(701, 711)]
    t = MockGitHubTransport(issues=issues, page_size=100)
    _run(_indexer(t), sdk, cap=5000)  # index everything (invariant holds)
    stats = _run(_indexer(t), sdk, cursor={"updated_at": S, "number": 100},
                 cap=500)
    assert stats["processed"] == 500
    assert stats["cursor"] == {"updated_at": S, "number": 600}, \
        "the exact-cap cut on the last readable block is NOT a truncation"
    assert stats["cleared_truncated"] is False
    # the next poll is a no-op DIFF probe (0 processed, no re-walk heal)
    after = _run(_indexer(t), sdk, cursor=stats["cursor"], cap=500)
    assert after["processed"] == 0
    assert after["cleared_truncated"] is False
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 610


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

    def _boom(sdk, proj, repo, issue, **kw):
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
    legacy org_id-as-org bug), resolve_repos falls back to the token's OWN
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


# ── #7951: the HIDDEN ``proj.apply`` door — indexer writes must journal ──────
#
# The sizing measurement on #7951 found a class the ``.query(``-based metric
# cannot see: this indexer wrote Object/Subject/Event nodes through
# ``proj.apply(...)`` — the projection's REPLAY fold — with ZERO journal calls.
# ``proj.apply`` is what ``rebuild_all``/``recover_from_log`` drive once per
# journal record, so the write left no truth carrier and a replay LOST every
# node the indexer wrote.
#
# RED before the fix (this test only, on origin/main):
#   AssertionError: the indexer's Object write did not reach the journal — a
#   replay of the journal LOST it (hidden proj.apply door, #7951)
#   assert 0 == 1
#   … while the journal was literally ``[]``.
#
# GREEN after: the write goes through ``TortoiseSDK._apply_and_journal`` — the
# live twin of the replay fold (apply, then journal the same record) — and a
# POLL RE-RUN does not grow the journal (the first-registration gate
# ``_create_entity`` uses).

_JDOOR_OID = "github-issue-acme/repo1-1"
_JDOOR_EID = "github-issue-acme/repo1-1-created"
_JDOOR_SID = "github-user:user1"


def _jdoor_sdk(tmp_path):
    """A real embedded SDK whose JSONL truth journal is configured."""
    from tortoise.sdk import TortoiseSDK
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "hidden-door.db"),
                      event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _jdoor_one(proj, cypher: str, **params):
    rows = proj.g.query(cypher, params=params or None).result_set
    return rows[0][0] if rows else 0


def _jdoor_object_count(proj, oid: str) -> int:
    return _jdoor_one(proj, "MATCH (o:Object {id:$id}) RETURN count(o)", id=oid)


def _jdoor_event_count(proj, eid: str) -> int:
    return _jdoor_one(proj, "MATCH (e:Event {eventId:$id}) RETURN count(e)", id=eid)


def _jdoor_journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _jdoor_event_state(proj, eid: str) -> tuple:
    rows = proj.g.query(
        "MATCH (e:Event {eventId:$id}) RETURN e.eventKind, e.endedAt",
        params={"id": eid}).result_set
    return (None, None) if not rows else (rows[0][0], rows[0][1])


def _jdoor_count(records, type_: str) -> int:
    return sum(1 for e in records if e.get("type") == type_)


def test_7951_indexer_writes_reach_journal_and_survive_replay(tmp_path):
    """DELIVERABLE — a write through the hidden door lands in the journal and
    survives ``rebuild_all`` (``derived = replay(journal)`` holds again)."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)
        proj = sdk._get_proj()

        # (1) the door wrote the live graph …
        assert _jdoor_object_count(proj, _JDOOR_OID) == 1
        assert _jdoor_event_count(proj, _JDOOR_EID) == 1
        assert _jdoor_one(proj, "MATCH (s:Subject {id:$id}) RETURN count(s)",
                          id=_JDOOR_SID) == 1

        # (2) … and the journal now carries the record of it (the fix).
        journal = _jdoor_journal(events)
        assert _jdoor_count(journal, "ObjectRegistered") == 1, (
            "the indexer's Object write did not reach the journal — a replay "
            "of the journal would LOSE it (hidden proj.apply door, #7951)"
        )
        assert _jdoor_count(journal, "SubjectAdded") == 1
        assert _jdoor_count(journal, "EventRecorded") == 1

        # (3) replay reproduces every door-written node + its folded kind.
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_object_count(proj, _JDOOR_OID) == 1, (
            "the indexer's Object write did not survive replay (#7951)")
        assert _jdoor_event_count(proj, _JDOOR_EID) == 1, (
            "the indexer's Event write did not survive replay (#7951)")
        assert _jdoor_one(proj, "MATCH (s:Subject {id:$id}) RETURN count(s)",
                          id=_JDOOR_SID) == 1, (
            "the indexer's Subject write did not survive replay (#7951)")
        assert _jdoor_one(
            proj, "MATCH (e:Event {eventId:$id}) RETURN e.eventKind",
            id=_JDOOR_EID) == "github.issue.open"
    finally:
        sdk.close()


def test_7951_indexer_poll_rerun_does_not_grow_the_journal(tmp_path):
    """The first-registration gate: a re-run of an already-indexed issue
    refreshes the live graph without appending the journal again."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)
        first = _jdoor_journal(events)
        counts = {t: _jdoor_count(first, t)
                  for t in ("ObjectRegistered", "SubjectAdded", "EventRecorded")}

        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)
        second = _jdoor_journal(events)
        for type_, n in counts.items():
            assert _jdoor_count(second, type_) == n, (
                f"a poll re-run appended a duplicate {type_} (#7951: the "
                f"journal must not grow once per run)")
        assert _jdoor_object_count(sdk._get_proj(), _JDOOR_OID) == 1
    finally:
        sdk.close()


def test_7951_intra_run_shared_subject_is_journaled_once(tmp_path):
    """R2 coverage gap: the ``journal_ids`` threading is LOAD-BEARING.

    Two issues in ONE ``index_repo`` run that share a Subject read the journal
    ONCE at run start (``_journal_registered_entities``); the per-issue
    registration gate must then see the subject the FIRST issue just journaled
    via the run-scoped set. Without the ``journal_ids.add(...)`` in lockstep,
    the second issue re-appends an identical ``SubjectAdded`` — a journal that
    grows once per issue instead of once per subject. No committed test
    covered the intra-run case before."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        a, b = gh_issue(1), gh_issue(2)
        a["user"] = {"login": "shared-user"}
        b["user"] = {"login": "shared-user"}
        _run(_indexer(MockGitHubTransport(issues=[a, b])), sdk)

        sid = "github-user:shared-user"
        records = _jdoor_journal(events)
        n = sum(1 for e in records
                if e.get("type") == "SubjectAdded" and e.get("id") == sid)
        assert n == 1, (
            f"a Subject shared by two issues in ONE run was journaled {n} "
            "times — `journal_ids` is not threaded in lockstep, so the "
            "journal grows once per issue (#7951 R2)")
    finally:
        sdk.close()


def test_7951_indexer_pr_event_reaches_the_journal(tmp_path):
    """The PR arm of the same door: a genuinely-minted PR Event is journaled;
    a re-run that merely refreshes it is not."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        pr = gh_pr(7)
        _run(_indexer(MockGitHubTransport(issues=[pr])), sdk)
        pid = "github-pr-acme/repo1-7"
        assert _jdoor_event_count(sdk._get_proj(), pid) == 1
        journal = _jdoor_journal(events)
        assert any(e.get("type") == "EventRecorded"
                   and e.get("eventId") == pid for e in journal), (
            "the PR Event write bypassed the journal (#7951)")
        n = _jdoor_count(journal, "EventRecorded")
        _run(_indexer(MockGitHubTransport(issues=[pr])), sdk)  # refresh only
        assert _jdoor_count(_jdoor_journal(events), "EventRecorded") == n
        assert _jdoor_event_count(sdk._get_proj(), pid) == 1
    finally:
        sdk.close()


def test_7951_sdk_create_entity_survives_replay(tmp_path):
    """CONTROL — the replay machinery preserves an SDK-door write, so a red
    delivery test is the DOOR and not a ``rebuild_all`` artifact."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", "control-object",
                          objectKind="core:strategy", is_episodic=False)
        assert _jdoor_one(proj,
                          "MATCH (o:Object {name:$n}) RETURN count(o)",
                          n="control-object") == 1
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_one(proj,
                          "MATCH (o:Object {name:$n}) RETURN count(o)",
                          n="control-object") == 1, (
            "the control write through the SDK door did not survive replay — "
            "rebuild_all itself is broken here, not the hidden door"
        )
    finally:
        sdk.close()


# ── P1-b (review): the door skipped `_create_entity`'s `createdAt` synthesis
# `_create_entity` synthesises `event["createdAt"] = now_iso()` on the
# journaling path BEFORE apply (`sdk.py`, the #2194/#2295 block) exactly to
# close the #2164-P4 "a replay stamps rebuild time" class. `_apply_and_journal`
# journaled the record with NO `createdAt`, so a journaled Subject (and Object)
# carried no create instant and a replay re-stamped rebuild time. The payload
# KEY rule was shared (`_journal_record_payload`); the value-SYNTHESIS rule was
# not — which falsified commit 2's "the two live doors cannot drift" claim.
#
# RED before the fix:
#   AssertionError: the journaled SubjectAdded carries no createdAt — a replay
#   would stamp rebuild time (#2164-P4 class, #7951 P1-b)  [rec.get('createdAt')
#   is None]
#   and live != replayed after rebuild_all.

_SUBJ_CREATED_AT = (
    "MATCH (s:Subject {id:$id}) RETURN s.createdAt")


def test_7951_indexer_journals_createdAt_and_replay_does_not_restamp(tmp_path):
    """The indexer's Subject write must carry the SAME create instant into the
    journal and the graph, and a replay must reproduce it (not rebuild time)."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)
        proj = sdk._get_proj()
        live = _jdoor_one(proj, _SUBJ_CREATED_AT, id=_JDOOR_SID)
        assert live, (
            "the live Subject has no createdAt — the probe below would be "
            "vacuous (#7951 P1-b)")

        records = [e for e in _jdoor_journal(events)
                   if e.get("type") == "SubjectAdded"]
        assert len(records) == 1, f"expected one SubjectAdded, got {records!r}"
        assert records[0].get("createdAt") == live, (
            "the journaled SubjectAdded carries no createdAt (or a different "
            "one) — `_apply_and_journal` skipped `_create_entity`'s synthesis, "
            "so a replay would stamp rebuild time instead of the live create "
            "time (#2164-P4 class, #7951 P1-b)")

        proj.rebuild_all(str(events), confirm_destructive=True)
        replayed = _jdoor_one(proj, _SUBJ_CREATED_AT, id=_JDOOR_SID)
        assert replayed == live, (
            f"replay RE-STAMPED the Subject's createdAt: live={live!r} "
            f"replayed={replayed!r} (#7951 P1-b)")
    finally:
        sdk.close()


def test_7951_indexer_journals_createdAt_for_the_object_too(tmp_path):
    """The Object arm of the same rule (both are `_create_entity`-synthesised)."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)
        proj = sdk._get_proj()
        live = _jdoor_one(proj,
                          "MATCH (o:Object {id:$id}) RETURN o.createdAt",
                          id=_JDOOR_OID)
        assert live
        records = [e for e in _jdoor_journal(events)
                   if e.get("type") == "ObjectRegistered"]
        assert len(records) == 1
        assert records[0].get("createdAt") == live, (
            "the journaled ObjectRegistered carries no createdAt (#7951 P1-b)")
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_one(
            proj, "MATCH (o:Object {id:$id}) RETURN o.createdAt",
            id=_JDOOR_OID) == live, "replay re-stamped the Object createdAt"
    finally:
        sdk.close()


def test_7951_indexer_journals_createdAt_for_events(tmp_path):
    """The Event arm — `_apply_and_journal` journals EventRecorded records,
    which the producer also mints without a `createdAt`; the door must stamp
    one so every record it journals carries its create instant uniformly."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        _run(_indexer(MockGitHubTransport(issues=[gh_pr(7)])), sdk)
        pid = "github-pr-acme/repo1-7"
        records = [e for e in _jdoor_journal(events)
                   if e.get("type") == "EventRecorded"
                   and e.get("eventId") == pid]
        assert len(records) == 1, f"expected one EventRecorded, got {records!r}"
        assert records[0].get("createdAt"), (
            "the journaled EventRecorded carries no createdAt — "
            "`_apply_and_journal` skipped the door's synthesis (#7951 P1-b)")
    finally:
        sdk.close()


# ── P2-a (review): a pre-fix node in the GRAPH is never backfilled
# `_entity_registered` probed the LIVE node only, so an Object/Subject a
# PRE-#7951 binary wrote straight through `proj.apply` (graph, no journal)
# read as "registered" on every later poll and `journal=False` for ever —
# `derived = replay(journal)` stayed false for it. Reproduced by writing the
# Object through the pre-fix door and then running a real poll.
#
# ── P2-b (review): the probe is fail-CLOSED
# Id-only with no `try/except`, while `_create_entity`'s probe fails OPEN. A
# transient probe error aborted `_project_issue` (caught by `index_repo`),
# skipping an ENTIRE issue's projection — a failure mode that was impossible
# before the door landed.

def test_7951_prefix_object_in_graph_is_backfilled_to_the_journal(tmp_path):
    """A node the pre-fix binary wrote (graph only) must be journaled by the
    next poll — journal=False for ever is the P2-a defect."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        obj = github_map.issue_to_object(gh_issue(1), "acme/repo1")
        # #7951 §17.1: the seed reproduces the PRE-FIX binary's graph-only
        # state (a node with no journal carrier). A fold with no live write
        # behind it is a replay, so it declares the replay scope; the guard
        # would otherwise refuse it, which is the whole point of this lane.
        with _replay_write_scope():
            proj.apply(obj)  # the PRE-FIX door: live graph, no journal append
        assert _jdoor_object_count(proj, _JDOOR_OID) == 1
        assert _jdoor_journal(events) == [], "premise: the journal is empty"

        _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)

        records = _jdoor_journal(events)
        assert any(e.get("type") == "ObjectRegistered"
                   and e.get("id") == _JDOOR_OID for e in records), (
            "a node the PRE-FIX binary wrote (graph only) was never backfilled "
            "into the journal — `derived = replay(journal)` stays false for it "
            "on every later poll (#7951 P2-a)")
        # and the backfilled record now reproduces the node on replay
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_object_count(proj, _JDOOR_OID) == 1, (
            "the backfilled ObjectRegistered did not reproduce the node on "
            "rebuild_all (#7951 P2-a)")
    finally:
        sdk.close()


def test_7951_prefix_event_in_graph_is_backfilled_to_the_journal(tmp_path):
    """R2: the EVENT arm of the pre-fix backfill.

    ``_entity_registered`` backfills an Object/Subject a pre-#7951 binary wrote
    straight through ``proj.apply`` (graph, no journal), but the creation mint
    gate probed ``_event_exists`` — the LIVE GRAPH only. So an Event the same
    binary wrote read as "already minted" and stayed ``journal=False`` for
    ever: the journal gained ObjectRegistered + SubjectAdded but NO
    EventRecorded, and ``rebuild_all`` yielded event count 0."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        ev = github_map.issue_to_event(gh_issue(1), "acme/repo1",
                                       previous_state=None)
        eid = ev["eventId"]
        with _replay_write_scope():
            proj.apply(ev)  # the PRE-FIX door: live graph, no journal append
        assert _jdoor_event_count(proj, eid) == 1
        assert _jdoor_journal(events) == [], "premise: the journal is empty"

        stats = _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)

        records = _jdoor_journal(events)
        assert any(e.get("type") == "EventRecorded" and e.get("eventId") == eid
                   for e in records), (
            "an Event the PRE-FIX binary wrote (graph only) was never "
            "backfilled into the journal — `derived = replay(journal)` stays "
            "false for it on every later poll; the creation mint gate probed "
            "the GRAPH only (#7951 R2)")
        # `_event_exists` still feeds `events_minted` (a GENUINE graph mint
        # only) — the journal backfill must not inflate it.
        assert stats["events_minted"] == 0, (
            "the Event arm's journal backfill inflated `events_minted` — that "
            "counter is a graph-mint count, not a journal-append count "
            "(#7951 R2)")
        # and the backfilled record reproduces the node on replay
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_event_count(proj, eid) == 1, (
            "the backfilled EventRecorded did not reproduce the Event on "
            "rebuild_all (#7951 R2)")
    finally:
        sdk.close()


def test_7951_frozen_created_backfill_replays_the_live_state(tmp_path):
    """P2-1: the backfilled ``-created`` record must carry the FROZEN node's
    ``eventKind``/``endedAt``, not the freshly-computed current state.

    ``_frozen_created_payload`` is load-bearing exactly here: on the backfill
    path the node is frozen at first observation while ``created`` is rebuilt
    from the issue state NOW, so journaling ``created`` verbatim makes replay
    reproduce the CURRENT state and diverge from the live node. The existing
    ``test_created_event_frozen_across_close_reopen`` never enters this path
    (it drives the plain walk, which re-folds nothing), so before this test
    the committed suite stayed green with the helper reduced to
    ``return dict(created)``."""
    sdk, events = _jdoor_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        # A pre-#7951 binary: the -created Event folded directly (graph only,
        # no journal append) while the issue was OPEN.
        ev = github_map.issue_to_event(gh_issue(1), "acme/repo1",
                                       previous_state=None)
        assert ev["eventKind"] == "github.issue.open" and ev["endedAt"] is None
        with _replay_write_scope():
            proj.apply(ev)
        assert _jdoor_journal(events) == [], "premise: the journal is empty"
        live_before = _jdoor_event_state(proj, _JDOOR_EID)

        # Poll a CLOSED issue: `created` is rebuilt from the CLOSED state, so
        # the naive backfill would journal the closed kind + an endedAt.
        _run(_indexer(MockGitHubTransport(issues=[
            gh_issue(1, state="closed", closed_at="2026-07-20T08:00:00Z",
                     updated_at="2026-07-20T08:00:00Z")])), sdk)

        # The live node stays frozen at first observation …
        live_now = _jdoor_event_state(proj, _JDOOR_EID)
        assert live_now == live_before == ("github.issue.open", None), (
            f"the -created node was re-folded from the current closed state: "
            f"{live_now} (#7951 P2-1)")

        records = [e for e in _jdoor_journal(events)
                   if e.get("type") == "EventRecorded"
                   and e.get("eventId") == _JDOOR_EID]
        assert len(records) == 1, f"expected one backfilled record: {records!r}"
        assert (records[0].get("eventKind"),
                records[0].get("endedAt")) == live_now, (
            "the backfilled EventRecorded carries the CURRENT (closed) state, "
            "not the frozen live node's — a replay would reproduce a node the "
            "live graph does not hold (#7951 P2-1)")

        # … and the replay reproduces the SAME node (live == replay).
        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _jdoor_event_state(proj, _JDOOR_EID) == live_now, (
            "rebuild_all did not reproduce the live -created node — the "
            "frozen-creation invariant is broken on the backfill path "
            "(#7951 P2-1)")
    finally:
        sdk.close()


def test_7951_registration_probe_failure_fails_open(tmp_path, monkeypatch):
    """A transient failure of the existence probe must not abort the issue's
    projection — it fails OPEN, exactly like `_create_entity`'s probe."""
    from tortoise.projection import _GuardedGraph

    sdk, events = _jdoor_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        real_query = _GuardedGraph.query

        def flaky_query(self, q, params=None, **kw):
            if ("RETURN n.id" in q and "{id:$id}" in q
                    and (":Object " in q or ":Subject " in q)):
                raise RuntimeError("transient probe failure (test)")
            return real_query(self, q, params=params, **kw)

        monkeypatch.setattr(_GuardedGraph, "query", flaky_query)
        stats = _run(_indexer(MockGitHubTransport(issues=[gh_issue(1)])), sdk)

        assert not stats["errors"], (
            f"a transient probe error aborted the issue's projection: "
            f"{stats['errors']} (#7951 P2-b)")
        assert _jdoor_object_count(proj, _JDOOR_OID) == 1, (
            "the probe failure skipped the Object write entirely (#7951 P2-b)")
        assert any(e.get("type") == "ObjectRegistered"
                   for e in _jdoor_journal(events)), (
            "the fail-open path did not journal the Object (#7951 P2-b)")
    finally:
        sdk.close()


# ── #13289 round 11 (P2): the `file:line` citations this change WRITES must
# resolve to the statement they NAME — at the MERGE RESULT, not at the branch
# head where they were typed.
#
# ⛔ WHY THIS EXISTS. The defect class this whole PR removes is "a citation that
# points at the wrong statement", and it has a mechanical cause: `main` added 28
# lines to `entities.py` after this branch's base, so every address above the
# insertion moved by 28 — and the citations then resolved to an import list, a
# blank line, and docstring prose. ⛔ No existing gate sees it: the census pins are
# SCALARS, `tests/test_docs_citation_refs.py` resolves markdown-to-markdown and
# states its own limit as line PRESENCE rather than semantic correctness, and the
# `file.py:NNNN` resolver in `tests/test_extractor_v4_citation_rot.py` is scoped
# to a fixed set of design documents. So a wrong-but-present line ships silently.
#
# The pin is by TEXT: an address with no pin FAILS rather than passing quietly,
# which is what makes the next `main`-move a RED test instead of a re-read.

_CITATION_PIN_ROOT = Path(__file__).resolve().parent.parent

# the modules whose citations this change makes claims about, and where they live
_PINNED_MODULES = {
    "entities.py": Path("tortoise/projection/entities.py"),
    "cache_projection.py": Path("tortoise/cache_projection.py"),
    "sdk.py": Path("tortoise/sdk.py"),
}

# (module basename, first line, last line) -> (text required ON the first line,
# text required ON the last line; None when the citation is a single line)
_CITATION_PINS: dict[tuple[str, int, int], tuple[str, str | None]] = {
    # entities.py — the status latch, its two refusals, and the by-name reopen reach
    ("entities.py", 5261, 5263): (
        '"st": ev.get("status"),  # #1350: status ON CREATE only —',
        "# re-mention cannot reset superseded→live)."),
    ("entities.py", 6124, 6124): ("excluded = _terminal_object_statuses()", None),
    ("entities.py", 6206, 6207): (
        "pipeline_key=target_kind,", "decl=index.pipeline_for(target_kind),"),
    ("entities.py", 6364, 6365): (
        "_live_holders = _live_holders_same_entity(", 'self.g, "Object", name)[0]'),
    ("entities.py", 6388, 6392): (
        "_reopen_targets_closed = (", "and isinstance(_held[0], str))"),
    ("entities.py", 6388, 6393): (
        "_reopen_targets_closed = (", "if _reopen_targets_closed:"),
    ("entities.py", 6388, 6394): (
        "_reopen_targets_closed = (", "object_id = _held[0]"),
    ("entities.py", 6389, 6392): (
        "bool(builtin_fold_reopens(event_kind))", "and isinstance(_held[0], str))"),
    ("entities.py", 6396, 6398): (
        "object_id = resolve_fold_name(",
        "mention=bool(_held) and not _live_holders)"),
    ("entities.py", 6417, 6417): ("declared_kinds=sorted(index.kind_forms))", None),
    ("entities.py", 6449, 6454): ("row = self.g.query(", ").result_set"),
    ("entities.py", 6458, 6458): (
        "if status is not None and status in excluded:", None),
    ("entities.py", 6469, 6471): (
        "if (declared_kinds is not None and object_kind is not None", "return False"),
    # cache_projection.py — the NOT MONOTONE bullet the superseded claim rests on
    ("cache_projection.py", 811, 811): (
        "NOT MONOTONE — the carriers CAN be cleared", None),
    # sdk.py — the non-scalar refusal and the coherent un-supersede admission arm
    # ⛔ RE-DERIVED THREE TIMES on the way into `main` (`#13447`), always BY CONTENT.
    # Every input block was stale by the time it was read: this branch's was derived
    # at its own base, and `main`'s at `f6793959a2ae` (`#13486`) — and each forward
    # merge (`130e20fdaf`, then `501270a4bf`, then `895d4dde3d` — whose `sdk.py` moves
    # this region a further +5 and the NOT MONOTONE bullet +1 — left both sides wrong
    # against the merged tree. So NO number here is an
    # offset or a carry: every address was found by searching for its own pinned
    # FIRST-line text (and, where the row has one, its own LAST-line text) at the tree
    # that was about to be committed. `entities.py` used to keep this branch's values
    # across both of those merges; it no longer does — `#13971` adds lines ABOVE the
    # status latch, so the whole block moved together and is re-derived here again
    # (`+59` against `origin/main`'s numbers — `git diff --numstat origin/main...HEAD --
    # tortoise/projection/entities.py` -> `61  2` — resolved line by line, never applied as an
    # offset; the pin TABLE's own keys were only `+30` off, because the last re-derivation of
    # this block was two commits stale when this PR re-measured it); `sdk.py` moves for both sides each
    # time, including
    # `update_knowledge`'s range end, which is located as the function's own last
    # line rather than computed. The numbers in this block are therefore a
    # MEASUREMENT of the tree that carries them, and the test re-checks all 18 rows on
    # every run — a pin resolved by arithmetic on a number you did not measure is how
    # these rot.
    #
    # ⛔ RE-DERIVED ONCE MORE (#13490), for the fourth time and by the same method:
    # the block above was a MEASUREMENT of its own tree and `sdk.py` moved under it
    # again, so those numbers are red on the tree this change lands on. Each address
    # was found by searching for its own pinned FIRST-line text (and, for the arm,
    # its own LAST-line text `continue`) at the tree about to be committed — never by
    # adding an offset: `54294→55020`, `54446→55172` and `54895→55621` are each
    # **+726** and `update_knowledge` is **+33** (`22470→22503`, its range end
    # `22645→22678`, located as the function's own last line `return
    # self._update_entity_any(id, **props)`), and the two different deltas are the
    # reason the rule is per-address. ⛔ The DELTA is a consistency check on a
    # measurement, never its input.
    #
    # ⛔ RE-DERIVED AGAIN at `main` `2c1065a52e` (`#13550`), by the same method and NOT
    # by the delta: the paragraph above was correct for `6f1ffa1121` (its own values
    # open there) and `#13550`'s `sdk.py` insert moved the region under them. Each
    # address was found by searching its own pinned FIRST-line text (and, for the arm,
    # its own LAST-line text `continue`) at `2c1065a52e`, and the numbers are
    # `55206-55208` (the non-scalar refusal), `55358-55367` (the un-supersede arm),
    # `22518-22693` (`update_knowledge`, its end again located as the function's own
    # last line `return self._update_entity_any(id, **props)`) and `55912` (the
    # per-label `SET n += $props`). The two deltas — **+186** for the three later rows
    # and **+15** for `update_knowledge` — are the consistency check, not the input.
    # ⛔ Where a pinned first-line text is NOT itself a unique locator, the search used
    # the longer form of the same line and that is what locates the row: `SET n +=
    # $props` occurs **40×** in `sdk.py`, so `55912` was found by its per-label write
    # `MATCH (n:{label} {{{match_prop}:$id}}) SET n += $props`, which occurs **once**.
    # A row recorded as "found by its pinned text" must name text that actually finds
    # it, or the record does not reproduce the address it explains.
    # ⛔ #13767's own review pass then narrowed the last two `sdk.py` rows, each of
    # which carried a locator that could not FAIL on the defect it exists to catch:
    # `55912`→`55918` stored the bare `SET n += $props` (occurs **40×** in `sdk.py`,
    # 25 of them in comments), so a uniform drift landed it on a comment; the
    # per-label write it was actually found by occurs **once** and is what it now
    # stores. And `22593-22768` recorded `None` for its end — its START text
    # `def update_knowledge(` is unique and did red, but an end that is never read
    # let an insert INSIDE the function leave the cited end naming nothing while
    # both guards stayed green.
    # Both ADDRESSES were re-derived at `ef0abcb`; both LOCATORS were narrowed at
    # `c50a03a` (the commit carrying this note), by the texts pinned below.
    #
    # ⛔ RE-DERIVED ONCE MORE at `28ca66878d` (`#13833`), by the same method and NOT by
    # the delta: `#13833`'s `sdk.py` change is net `+194` BELOW `update_knowledge`, so
    # the three later rows moved and that one did not — a single offset applied to the
    # whole block would have re-pinned `update_knowledge` to the wrong function and
    # left it looking "carried". Each address was found by searching its own pinned
    # FIRST-line text (and, for the arm, its own LAST-line text `continue`) at
    # `28ca66878d`: `55547-55549` (the non-scalar refusal), `55699-55708` (the
    # un-supersede arm) and `56148` (the per-label `SET n += $props`). `update_knowledge`
    # re-measured UNCHANGED at `22629-22804`. The `+194` is the consistency check on a
    # measurement, never its input.
    # ⛔ RE-MEASURED at the `#13854` merge of `origin/main` `f2e5e3a98f0` — and the three later
    # rows are `55774` / `55926` / `56420` again, NOT `28ca66878d`'s figures plus any offset: this
    # branch's own `sdk.py` inserts sit ABOVE them. Measured on the MERGED tree by each row's own
    # pinned text: `A row that ALREADY holds a non-scalar` -> `55774`-`55776`, `if (_np_dead ==
    # [_SUPERSEDED_STATUS]` -> `55926`-`55935` (its bare `continue` at `55935`), the per-label
    # `MATCH (n:{label} {{{match_prop}:$id}}) SET n += $props` -> `56420`, and `def
    # update_knowledge(` -> `22801` with its `return self._update_entity_any(id, **props)` at
    # `22976`. The paragraph above quotes `28ca66878d`'s figures because it describes THAT head.
    # ⛔ RE-MEASURED ONCE MORE at the `#13854` merge of `origin/main` `bd020ff45bb`, which moved
    # `tortoise/sdk.py` again (+150 above the second insertion point, +195 below it). The four
    # rows above are the values MEASURED on that merged tree; every one is read from its own
    # pinned FIRST-line text, never from the previous row plus a guessed offset.
    #
    # ⛔ RE-DERIVED ONCE MORE at `bd020ff45b` on the OTHER side of this PR (#13937 — frozen
    # `main` red, measured on a bare `main` checkout with no PR diff applied), by the same
    # method and NOT by the delta: `sdk.py` moved under every row, `+150` above
    # `update_knowledge` and `+195` below it, so this is TWO regions and a single offset
    # would have re-pinned the wrong function. Each address was found by searching its own
    # pinned FIRST-line text (and, for the arm, its own LAST-line text `continue`) at the
    # tree that carries that row: `55742-55744` (the non-scalar refusal), `55894-55903`
    # (the un-supersede arm), `56343` (the per-label `SET n += $props`, which occurs once
    # while bare `SET n += $props` occurs 40×) and `22779-22954` (`update_knowledge`, its
    # end again located as the function's own last line `return
    # self._update_entity_any(id, **props)`, whose other occurrence is a different door's).
    # `if (_np_dead == [_SUPERSEDED_STATUS]` occurs TWICE, so that row is located by its
    # `continue` terminator and not by its start text. The `+150` / `+195` pair is the
    # consistency check on a measurement, never its input.
    # ⛔ THE MERGE of those two derivations is where this block could have rotted, so the
    # reconciliation is stated rather than left to the diff: on the tree this PR merges
    # (`origin/main`), `#13899`/`#13626` moved `sdk.py` again and EVERY `bd020ff45b` figure
    # above now lands on the wrong line — `55742`->`55774` (+32), `55894`->`55926` (+32),
    # `56343`->`56420` (+77) and `22779`->`22801` (+22). Three different deltas on four
    # rows is the whole argument against adopting either side's block wholesale, and why
    # each row below was re-found by its own pinned text on the MERGED tree. The historical
    # paragraphs are kept because they record real measurements of real trees; the pin
    # table is what resolves HERE.
    #
    # ⛔ RE-DERIVED AGAIN at `3e9278e39349` (`#14220`'s embedder seam moved `sdk.py` and no
    # landing re-derived these four rows — tracked as `#13937`, whose own "suggested scope"
    # item 1 is exactly this: re-derive every address at the current tree). Two deltas, not
    # one — `+53` for the `update_knowledge` pair and `+117` for the NOT-MONOTONE block —
    # which is why each row was re-found by its own pinned text rather than by adding a
    # constant. Every row's SPAN is unchanged (176, 3 and 10 lines), so the addresses moved
    # with the statements rather than being re-pointed at neighbouring code.
    ("sdk.py", 56377, 56379): (
        "A row that ALREADY holds a non-scalar", "be REFUSED, not raised at."),
    ("sdk.py", 56529, 56538): ("if (_np_dead == [_SUPERSEDED_STATUS]", "continue"),
    ("sdk.py", 23041, 23216): (
        "def update_knowledge(", "return self._update_entity_any(id, **props)"),
    ("sdk.py", 57035, 57035): (
        "MATCH (n:{label} {{{match_prop}:$id}}) SET n += $props", None),
}


def _citations_in(module: Path) -> set[tuple[str, int, int]]:
    """Every `file.py:NNN[-MMM]` citation in ``module`` that names one of ``_PINNED_MODULES``.

    ⛔ SCOPE, stated so the gap is not silent: citations to any OTHER module
    (`pipeline.py` 7, `live.py` 5, `lifecycle_vocabulary.py` 3, `commit_ops.py` 3,
    `entity_identity.py` 2, `github_map.py` 2, and one test module) are NOT returned
    and are therefore NOT pinned by this test — **23 of the 41** distinct citations
    in these two files at the head that added this walker. They are a separate,
    larger surface (a follow-up), not a claim this pin makes.

    A BARE `:NNN` inherits the filename last named on its line — the
    ``entities.py:6458`` / ``:6124`` shape, which is how one round of this review
    wrote a second address two words after the first.
    """
    found: set[tuple[str, int, int]] = set()
    for line in module.read_text(encoding="utf-8").splitlines():
        named = [
            (m.group(1).rsplit("/", 1)[-1], int(m.group(2)), int(m.group(3) or m.group(2)))
            for m in re.finditer(r"([A-Za-z0-9_./]+\.py):(\d+)(?:-(\d+))?", line)
        ]
        found.update(named)
        if named:
            carried = named[-1][0]
            for m in re.finditer(r"`:(\d+)(?:-(\d+))?`", line):
                found.add((carried, int(m.group(1)), int(m.group(2) or m.group(1))))
    return {cite for cite in found if cite[0] in _PINNED_MODULES}


def test_the_citations_to_the_pinned_modules_are_pinned_to_a_statement():
    """An address this change writes to `entities.py`/`sdk.py`/`cache_projection.py`
    must be PINNED, not merely well-formed. (Citations to other modules are out of
    this pin's scope — see `_citations_in`'s docstring.)"""
    cited = _citations_in(_CITATION_PIN_ROOT / "tortoise/indexer/github_indexer.py")
    cited |= _citations_in(_CITATION_PIN_ROOT / "tests/test_github_indexer.py")
    assert cited, (
        "the citation walker matched NOTHING — this pin would be inert, so it is "
        "failing rather than passing (an inert verifier is not a verifier)")

    unpinned = sorted(c for c in cited if c not in _CITATION_PINS)
    assert not unpinned, (
        "a citation to a pinned module has no statement pin: "
        f"{[f'{b}:{a}-{z}' for b, a, z in unpinned]}. Re-derive it at THIS tree "
        "(`grep -n` the statement it names) and pin it — an address inherited from "
        "another base is how these rotted when `main` moved `entities.py` (#13289).")

    # The specific failure mode: an address that lands on a blank line, a comment,
    # or an import list instead of the statement it names.
    shapes: list[str] = []
    src = (_CITATION_PIN_ROOT / _PINNED_MODULES["entities.py"]).read_text(
        encoding="utf-8").splitlines()
    for base, a, _last in sorted(cited):
        if base != "entities.py":
            continue
        text = src[a - 1]
        if not text.strip():
            shapes.append(f"entities.py:{a} is a BLANK line")
        elif text.lstrip().startswith("#"):
            shapes.append(f"entities.py:{a} is a COMMENT: {text.strip()[:60]!r}")
        elif re.match(r"\s*(?:from|import)\s", text):
            shapes.append(f"entities.py:{a} is an IMPORT: {text.strip()[:60]!r}")
    assert not shapes, (
        "a cited `entities.py` address does not point at code: " + "; ".join(shapes))


def test_every_pinned_citation_still_names_the_statement_it_is_cited_for():
    """The `entities.py` line numbers are a MEASUREMENT — re-derive, never copy."""
    problems: list[str] = []
    for (base, first, last), (want_first, want_last) in sorted(_CITATION_PINS.items()):
        src = (_CITATION_PIN_ROOT / _PINNED_MODULES[base]).read_text(
            encoding="utf-8").splitlines()
        got_first = src[first - 1] if first <= len(src) else "<BEYOND EOF>"
        if want_first not in got_first:
            problems.append(
                f"{base}:{first} should carry {want_first!r}, holds {got_first.strip()[:70]!r}")
        if want_last is not None:
            got_last = src[last - 1] if last <= len(src) else "<BEYOND EOF>"
            if want_last not in got_last:
                problems.append(
                    f"{base}:{last} should carry {want_last!r}, "
                    f"holds {got_last.strip()[:70]!r}")
    assert not problems, (
        "a pinned citation no longer resolves to the statement it names — the file "
        "moved under this change, so re-derive EVERY address this change writes at "
        "the CURRENT tree: " + "; ".join(problems))
