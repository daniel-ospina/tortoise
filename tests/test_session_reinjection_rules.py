"""#2517 (C4, #2513) — source-session re-injection: hermetic product rules.

Covers the pure primitives in ``tortoise/session_reinjection.py`` plus the
shared contract extracted into ``tortoise/retrieval.py`` (the C3-1/C4
guard + C5 re-cap helper, the ``session_key_of`` authority, the chunk-kind
constant) — no graph, no network.

  * SEED: only pool-head REAL sessions seed; bounded by window + limit;
    label-free (rank is the only trigger); ``idx:N``/sentinel buckets
    dropped as phantom sessions.
  * EXPAND: one batched query per call, pool-membership filter IN the
    query, ``pool_ids`` bound as a list, scope anchored on the seeded
    hit's Session (``Session-[:CONTAINS]->Point``; no ``lme_*`` predicate
    and no ``p.session_id``), the ``WITH DISTINCT s`` plan barrier, a
    deterministic ``ORDER BY``, per-session + total caps applied in that
    order, fail-open.
  * MERGE: anchor = the session's LAST base rank in the pool; splices
    accumulate deterministically in seed order; already-present ids are
    dropped; a base chunk ranked beyond the seed window survives; the
    whole merge is a no-op with zero new ids.
  * CONTRACT: ``guard_and_recap_pool`` caps per-session ranks in the
    guard window, is additive, is a no-op on a single-session pool, and
    ``guard=False`` still re-caps through the SAME function.
  * DIRECTION: ``session_key_of`` is the authority
    (``coverage_loop._session_of`` delegates to it) and the
    ``retrieval → coverage_loop`` edge has no cycle under either import
    order.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import coverage_loop, retrieval
from tortoise.retrieval import (
    DEFAULT_POOL_SESSION_CAP,
    SESSION_TRANSCRIPT_KIND,
    TURN_POINT_KIND,
    dedup_pool,
    guard_and_recap_pool,
    is_raw_chunk,
    session_key_of,
)
from tortoise.session_reinjection import (
    DEFAULT_REINJECTION_PER_SESSION,
    DEFAULT_REINJECTION_SEED_SESSIONS,
    DEFAULT_REINJECTION_SEED_WINDOW,
    DEFAULT_REINJECTION_TOTAL_ITEMS,
    SeededSession,
    reinjection_merge_order,
    seeded_sessions,
    source_session_chunk_pass,
)

ROOT = Path(__file__).resolve().parent.parent


def _chunk(pid: str, sid: str, *, idx: int = 0) -> dict:
    return {"id": pid, "session_id": sid, "point_kind": SESSION_TRANSCRIPT_KIND,
            "lme_chunk_index": idx}


def _point(pid: str, sid: str) -> dict:
    return {"id": pid, "session_id": sid, "point_kind": TURN_POINT_KIND}


# ── SEED ────────────────────────────────────────────────────────────────
def test_seed_is_rank_ordered_bounded_and_label_free():
    pool = [_point("a1", "s1"), _point("b1", "s2"),
            _point("a2", "s1"), _point("c1", "s3"), _point("d1", "s4")]
    seeds = seeded_sessions(pool, window=10, limit=3)
    assert seeds == [SeededSession("s1", 0, "a1"),
                     SeededSession("s2", 1, "b1"),
                     SeededSession("s3", 3, "c1")]
    # no mark/answer signal is read at all: the same pool with gold marks
    # injected yields the identical seeds
    marked = [dict(h, has_answer=True) for h in pool]
    assert seeded_sessions(marked, window=10, limit=3) == seeds


def test_seed_window_bounds_the_head():
    pool = [_point("a1", "s1"), _point("b1", "s2")]
    assert seeded_sessions(pool, window=1) == [SeededSession("s1", 0, "a1")]
    assert seeded_sessions(pool, window=0) == []
    assert seeded_sessions(pool, limit=0) == []
    assert seeded_sessions([]) == []


def test_seed_matches_the_readers_admitted_set_across_a_skip_gap():
    """#3594: the seed must be the reader's ADMITTED head, not the raw pool
    prefix. ``assemble_context`` skips claim-text-less hits (#2978, the
    ``_has_claim_text`` gate) WITHOUT spending an item slot, so it admits
    hits BELOW the raw prefix — and seeding from the prefix alone left a
    session that is reader-reachable yet unseeded, biasing exactly the A/B
    the arm exists to measure."""
    def _pt(pid: str, sid: str, text: str = "") -> dict:
        return {"id": pid, "session_id": sid, "point_kind": TURN_POINT_KIND,
                "content": text}

    # rank 1 is decoration-only (no content, no validity marker): the
    # reader skips it and its slot goes to rank 2's s2. So s2 is
    # READER-REACHABLE, but a window of 2 spent on the raw prefix
    # (a1 + the gap) never reaches it.
    pool = [_pt("a1", "s1", "alpha one"),
            {"id": "gap", "session_id": "sGap"},
            _pt("c1", "s2", "charlie two")]

    admitted = retrieval.assemble_context(
        pool, top_k=2, max_context_tokens=10_000, context_item_cap=2)
    admitted_ids = [h["id"] for h in admitted]
    # preconditions: the reader skips the gap AND still admits past it
    assert "gap" not in admitted_ids
    assert admitted_ids == ["a1", "c1"]

    reader_sessions = [session_key_of(h) for h in admitted]
    assert reader_sessions == ["s1", "s2"]

    # the fix: given the reader's OWN predicate, the seed agrees exactly
    seeds = seeded_sessions(pool, window=2, limit=5,
                            admitted=retrieval._has_claim_text)
    assert [s.session_id for s in seeds] == reader_sessions
    # pool ranks are preserved (not renumbered by the skip)
    assert seeds == [SeededSession("s1", 0, "a1"),
                     SeededSession("s2", 2, "c1")]

    # and the raw prefix (the pre-#3594 behaviour) did NOT agree — and it
    # is wrong in BOTH directions with one window: it seeded sGap, whose
    # only hit the reader SKIPS (so it is not reader-reachable at all),
    # and it missed s2, which the reader does admit. Neither error is
    # discoverable from the prefix alone, which is why the predicate — not
    # a wider window — is the fix.
    assert [s.session_id
            for s in seeded_sessions(pool, window=2, limit=5)] \
        == ["s1", "sGap"]


def test_driver_passes_the_readers_admission_predicate_to_the_seed():
    """#3594 wiring: the invariant above only holds if the DRIVER hands the
    predicate to ``seeded_sessions``. The hermetic test would still pass on
    a driver that forgot it, so pin the call site itself."""
    src = (ROOT / "tools" / "longmem_eval" / "retrieve.py").read_text()
    call = src[src.index("_sr.seeded_sessions("):]
    call = call[:call.index(")")]
    assert "admitted=_has_claim_text" in call, (
        "the eval driver must seed through the reader's #2978 admission "
        f"predicate; got:\n{call}")


def test_seed_drops_synthetic_and_sentinel_buckets():
    pool = [_point("x1", "idx:0"), _point("x2", "idx:-1"),
            {"id": "x3", "lme_session_index": 2},
            {"id": "x4", "session_id": ""},
            _point("y1", "real")]
    seeds = seeded_sessions(pool, window=10)
    assert seeds == [SeededSession("real", 4, "y1")]


def test_seed_requires_the_pool_hit_id_it_anchors_on():
    """The fetch resolves the session through ``Session-[:CONTAINS]->hit``,
    so a hit with no ``id`` names no session to expand and must not seed."""
    pool = [_point("a1", "s1"), {"session_id": "s2"},
            _point("c1", "s3")]
    assert seeded_sessions(pool, window=10) == [SeededSession("s1", 0, "a1"),
                                                SeededSession("s3", 2, "c1")]


def test_seed_defaults_are_the_documented_constants():
    assert DEFAULT_REINJECTION_SEED_WINDOW == 40
    assert DEFAULT_REINJECTION_SEED_SESSIONS == 5
    assert DEFAULT_REINJECTION_PER_SESSION == 3
    assert DEFAULT_REINJECTION_TOTAL_ITEMS == 10


def test_total_budget_is_below_the_structural_fan_out():
    """The reachability condition is ``TOTAL <= SEED_SESSIONS *
    PER_SESSION``: above it the total is structurally inert (the
    per-session cap alone bounds retained items), so ``total_cap_hit``
    could never be True — the defect this PR fixes. The shipped value is
    the conservative choice STRICTLY below that line. Pin the invariant so
    a future bump of either constant cannot silently re-inert the census
    key. (The binding of the key at the shipped defaults is pinned
    separately, behaviourally — see
    ``test_total_cap_binds_at_the_shipped_defaults``.)"""
    assert (DEFAULT_REINJECTION_TOTAL_ITEMS
            < DEFAULT_REINJECTION_SEED_SESSIONS
            * DEFAULT_REINJECTION_PER_SESSION)


# ── EXPAND ──────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params))
        if self.error is not None:
            raise self.error
        return _Result(self.rows)


class _FakeProj:
    def __init__(self, rows=None, error=None):
        self.g = _FakeGraph(rows, error)


def test_fetch_is_one_query_with_the_filter_in_the_query():
    proj = _FakeProj(rows=[("c1", "s1", 1)])
    out = source_session_chunk_pass(
        proj, ["seed1", "seed2"], pool_ids={"p1", "p2"})
    assert len(proj.g.calls) == 1
    cypher, params = proj.g.calls[0]
    assert "NOT p.id IN $pool_ids" in cypher          # filter in-query
    assert "coalesce(p.pointKind, '') = $chunk_kind" in cypher
    # #2517: the scope is the Session the seeded hit BELONGS to (product-
    # real), never a benchmark-only field and never ``p.session_id`` (a
    # product turn Point carries no such property — that predicate was dead
    # in every product graph).
    assert "MATCH (s:Session)-[:CONTAINS]->(seed)" in cypher
    assert "MATCH (s)-[:CONTAINS]->(p:Point)" in cypher
    assert "seed.id IN $seed_ids" in cypher
    assert "lme_question_id" not in cypher
    assert "p.session_id IN" not in cypher
    # the plan barrier that keeps the driver on the seed list: without it
    # FalkorDB scans the whole Point label and applies the seed filter last
    # (verified with GRAPH.EXPLAIN). Result-neutral, plan-decisive.
    assert "WITH DISTINCT s" in cypher
    # the group key the merge's ``seed_order`` must agree with
    assert "coalesce(p.session_id, s.id)" in cypher
    # the deterministic budget order: which rows get charged a cap slot
    # must not be left to the graph (normalise the query's line-continuation
    # whitespace before matching)
    assert ("ORDER BY coalesce(p.session_id, s.id), "
            "coalesce(p.lme_chunk_index, -1), p.id") in " ".join(
        cypher.split())
    # the default kind is the PRODUCT's verbatim material, not the eval's
    assert params["chunk_kind"] == TURN_POINT_KIND
    assert sorted(params["pool_ids"]) == ["p1", "p2"]  # coerced to a list
    assert params["seed_ids"] == ["seed1", "seed2"]
    assert out["ok"] is True
    assert out["total"] == 1
    assert out["by_session"] == {"s1": [{"id": "c1", "session_id": "s1",
                                         "lme_chunk_index": 1}]}


def test_fetch_constrains_event_kind_to_the_turn_shape():
    """``pointKind``'s vocabulary is open, so the turn kind alone does not
    prove a node is a turn: a graph seeded BEFORE ``#10000`` by the hosted
    demo/dashboard seed holds ``pointKind='event'`` points with NO
    ``is_episodic`` (its body IS ``[role]``-tagged, so ``is_episodic`` is the
    conjunct that excludes them). Since ``#10000`` that seed writes no turn
    node at all (§9.1/D1: the transcript is Postgres'), so no writer in this
    tree produces that shape — the clause is kept because a legacy graph still
    holds those nodes, and it must stay excluded. The default (turn) kind must
    carry the shape predicate; the eval's chunk A/B must NOT (chunks are
    role-prefixed windows whose shape is already pinned by the kind)."""
    proj = _FakeProj(rows=[])
    source_session_chunk_pass(proj, ["seed1"], pool_ids=[])
    turn_cypher, _ = proj.g.calls[0]
    assert "coalesce(p.is_episodic, false) = true" in turn_cypher
    assert "coalesce(p.content, '') STARTS WITH '['" in turn_cypher
    proj2 = _FakeProj(rows=[])
    source_session_chunk_pass(
        proj2, ["seed1"], pool_ids=[], chunk_kind=SESSION_TRANSCRIPT_KIND)
    chunk_cypher, params = proj2.g.calls[0]
    assert "is_episodic" not in chunk_cypher
    assert "STARTS WITH" not in chunk_cypher
    assert params["chunk_kind"] == SESSION_TRANSCRIPT_KIND


def test_total_cap_binds_at_the_shipped_defaults():
    """Behavioural pin of the retune: at the SHIPPED defaults the total
    budget is the binding volume guard. Five sessions x 4 candidate rows
    (all off-pool) = 20 candidates; ``per_session_cap`` is 3 and
    ``total_cap`` is 10, so the total trips and ``dropped_by_cap`` counts
    the excess. Without this the only coverage of the retune would be the
    constant comparison above, which is a necessary condition, not the
    claim (that ``total_cap_hit`` is a LIVE census key)."""
    rows = [(f"s{s}i{i}", f"s{s}", i)
            for s in range(5) for i in range(4)]
    out = source_session_chunk_pass(_FakeProj(rows=rows), ["seed"] * 5,
                                   pool_ids=[])
    assert out["total"] == DEFAULT_REINJECTION_TOTAL_ITEMS
    assert out["total_cap_hit"] is True
    # 20 candidates - 10 admitted = 10 dropped: 3 by the per-session cap
    # (one each on sessions s0/s1/s2) and 7 by the total cap (s3i1..i3,
    # s4i0..i3). The fake returns rows already in the query's ORDER BY
    # order, so this test pins the budget LOOP; the ordering itself is
    # pinned by the ``ORDER BY`` assertion in
    # test_fetch_is_one_query_with_the_filter_in_the_query.
    assert out["dropped_by_cap"] == 10
    assert sum(len(v) for v in out["by_session"].values()) == 10


def test_fetch_applies_per_session_and_total_caps_in_order():
    rows = [("a1", "s1", 0), ("a2", "s1", 1), ("a3", "s1", 2),
            ("b1", "s2", 0), ("b2", "s2", 1),
            ("c1", "s3", 0)]
    proj = _FakeProj(rows=rows)
    out = source_session_chunk_pass(
        proj, ["seed1", "seed2", "seed3"], pool_ids=[],
        per_session_cap=2, total_cap=4)
    assert out["total"] == 4
    assert [r["id"] for r in out["by_session"]["s1"]] == ["a1", "a2"]
    assert [r["id"] for r in out["by_session"]["s2"]] == ["b1", "b2"]
    assert "s3" not in out["by_session"]
    assert out["dropped_by_cap"] == 2
    assert out["total_cap_hit"] is True


def test_fetch_total_cap_hit_flag_is_false_when_only_per_session_binds():
    proj = _FakeProj(rows=[("a1", "s1", 0), ("a2", "s1", 1)])
    out = source_session_chunk_pass(
        proj, ["seed1"], pool_ids=[], per_session_cap=1,
        total_cap=10)
    assert out["dropped_by_cap"] == 1
    assert out["total_cap_hit"] is False


def test_fetch_is_fail_open():
    proj = _FakeProj(error=RuntimeError("boom"))
    out = source_session_chunk_pass(
        proj, ["seed1"], pool_ids=[])
    assert out == {"ok": False, "by_session": {}, "total": 0,
                   "dropped_by_cap": 0, "total_cap_hit": False}


def test_fetch_budgets_count_distinct_ids_not_duplicate_nodes():
    """A graph can hold several Point nodes for one id (a concurrent
    re-ingest races the ingest exist-probe). Duplicate rows must not spend
    the per-session budget twice: the budget is per DISTINCT chunk."""
    rows = [("a1", "s1", 0), ("a1", "s1", 0), ("a1", "s1", 0),
            ("a2", "s1", 1), ("a3", "s1", 2)]
    proj = _FakeProj(rows=rows)
    out = source_session_chunk_pass(
        proj, ["seed1"], pool_ids=[], per_session_cap=2,
        total_cap=10)
    assert out["ok"] is True
    assert [r["id"] for r in out["by_session"]["s1"]] == ["a1", "a2"]
    assert out["total"] == 2
    # a3 is dropped by the real budget, not by phantom duplicates
    assert out["dropped_by_cap"] == 1


def test_fetch_no_seeds_is_a_clean_noop():
    proj = _FakeProj(rows=[("a1", "s1", 0)])
    out = source_session_chunk_pass(proj, [], pool_ids=[])
    assert out["ok"] is True and out["total"] == 0
    assert proj.g.calls == []


def test_fetch_zero_budgets_short_circuit_before_the_query():
    """A zero per-session OR zero total budget returns the clean zeroed
    report with NO graph call. Deleting either disjunct falls through to a
    real fetch and a non-empty group, so this test fails on that revert."""
    rows = [("a1", "s1", 0), ("a2", "s1", 1)]
    for label, kwargs in (("per_session_cap", {"per_session_cap": 0}),
                          ("total_cap", {"total_cap": 0})):
        proj = _FakeProj(rows=rows)
        out = source_session_chunk_pass(
            proj, ["seed1"], pool_ids=[],
            per_session_cap=kwargs.get("per_session_cap", 2),
            total_cap=kwargs.get("total_cap", 10))
        assert out == {"ok": True, "by_session": {}, "total": 0,
                       "dropped_by_cap": 0, "total_cap_hit": False}, label
        assert proj.g.calls == [], label


# ── MERGE ───────────────────────────────────────────────────────────────
def test_merge_anchors_after_the_last_base_rank_in_the_pool():
    # s1's base hits are ranks 0 and 4 (the second one BEYOND the seed
    # window): the injected group lands after rank 4, never between them.
    pool = [_point("a0", "s1"), _point("b0", "s2"), _point("c0", "s3"),
            _point("d0", "s4"), _point("a1", "s1")]
    added = {"s1": [_chunk("x1", "s1", idx=5)]}
    out = reinjection_merge_order(
        pool, added, guard=False, max_chunks_per_session=3)
    ids = [h["id"] for h in out]
    assert ids == ["a0", "b0", "c0", "d0", "a1", "x1"]
    # the base chunk ranked beyond the seed window survived
    assert "a1" in ids


def test_merge_is_additive_and_never_drops_a_base_hit():
    pool = [_point("a0", "s1"), _point("b0", "s2")]
    added = {"s1": [_chunk("x1", "s1")]}
    out = reinjection_merge_order(
        pool, added, guard=True, max_chunks_per_session=3)
    assert set(h["id"] for h in pool) <= set(h["id"] for h in out)


def test_merge_drops_already_present_ids():
    pool = [_point("a0", "s1"), _chunk("x1", "s1")]
    added = {"s1": [_chunk("x1", "s1"), _chunk("x2", "s1", idx=2)]}
    out = reinjection_merge_order(
        pool, added, guard=False, max_chunks_per_session=3)
    ids = [h["id"] for h in out]
    assert ids.count("x1") == 1
    assert "x2" in ids


def test_merge_accumulates_multiple_groups_in_seed_order():
    pool = [_point("a0", "s1"), _point("b0", "s2"), _point("c0", "s3")]
    added = {"s2": [_chunk("y1", "s2")], "s1": [_chunk("x1", "s1")]}
    out = reinjection_merge_order(
        pool, added, guard=False, max_chunks_per_session=3)
    assert [h["id"] for h in out] == ["a0", "x1", "b0", "y1", "c0"]


def test_merge_seed_order_drives_the_defensive_tail_not_dict_order():
    """A seeded session with NO base rank is kept (the defensive tail) and
    the tail follows ``seed_order`` — NOT ``added_by_session``'s key order.
    Reverting ``order`` to ``list(added_by_session)`` must fail this test."""
    pool = [_point("a0", "s1")]
    added = {"s2": [_chunk("y1", "s2")], "s3": [_chunk("z1", "s3")]}
    out = reinjection_merge_order(
        pool, added, seed_order=["s3", "s2"], guard=False,
        max_chunks_per_session=3)
    assert [h["id"] for h in out] == ["a0", "z1", "y1"]
    # the reversed seed_order reverses the tail (same items, new order)
    out2 = reinjection_merge_order(
        pool, added, seed_order=["s2", "s3"], guard=False,
        max_chunks_per_session=3)
    assert [h["id"] for h in out2] == ["a0", "y1", "z1"]


def test_merge_empty_pool_is_a_noop():
    assert reinjection_merge_order(
        [], {"s1": [_chunk("x1", "s1")]}, guard=True,
        max_chunks_per_session=3) == []


def test_arm_conflict_is_raised_at_resolution_before_the_loop():
    """Run-level safety gate, moved here from the docker-lane E2E module so
    it executes on EVERY lane (the E2E module skips when the live FalkorDB
    probe is unavailable, and its skip reason is skip-guard-exempt)."""
    from tools.longmem_eval.run import ArmConflictError, run_evaluation
    with pytest.raises(ArmConflictError) as excinfo:
        run_evaluation([], reader=None, judge=None, split="s",
                       coverage_loop=True, session_reinjection=True)
    assert "coverage_loop" in str(excinfo.value)
    assert "session_reinjection" in str(excinfo.value)


def test_fingerprint_refuses_arm_and_guard_mismatches():
    """Every new always-present fingerprint key refuses a resume that
    flips it (moved here from the docker-lane E2E module, see above)."""
    from tools.longmem_eval import run as _run
    fp = _run._build_fingerprint(
        reader_model="m", judge_model="m", ks=(5,), top_k=5, split="s",
        ingest_mode="v2", extractor_model=None, max_retries=0,
        dataset_fingerprint="unknown", rerank_config={},
        session_reinjection=True, session_reinjection_guard=True)
    assert fp["session_reinjection"] is True
    assert fp["session_reinjection_guard"] is True
    off = dict(fp, session_reinjection=False)
    assert "session_reinjection" in _run._fingerprint_diffs(off, fp)
    flipped = dict(fp, session_reinjection_guard=False)
    assert "session_reinjection_guard" in _run._fingerprint_diffs(flipped, fp)
    # a pre-feature fingerprint (no keys at all) refuses too
    assert "session_reinjection" in _run._fingerprint_diffs({}, fp)


def test_reinjection_total_cap_resolves_once_before_the_loop(monkeypatch):
    """#2513 (delta-review P1): the injection total budget resolves ONCE,
    outside the per-question path.

    Arm-ON: explicit > env > the product constant. Arm-OFF: ``None`` — the
    knob is inert, so a stray env value is never read and never stamped.
    (It does not make a fingerprint-bearing pre-C4 checkpoint resumable:
    the always-present ``session_reinjection`` /
    ``session_reinjection_guard`` bools already refuse that resume.) The pre-fix
    shape read the env lazily inside ``retrieve_for_question``, where no
    fingerprint or methodology record can see it.
    """
    from tools.longmem_eval import run as _run

    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "15")
    assert _run._resolve_reinjection_total_cap(False) is None
    assert _run._resolve_reinjection_total_cap(False, 99) is None
    assert _run._resolve_reinjection_total_cap(True) == 15
    assert _run._resolve_reinjection_total_cap(True, 7) == 7
    # garbage / <1 / unset fall back to the shipped product constant — the
    # SAME ``rerank._env_int`` clamp the direct-caller fallback uses, so a
    # measured run never crashes
    for raw in ("garbage", "0"):
        monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", raw)
        assert _run._resolve_reinjection_total_cap(
            True) == DEFAULT_REINJECTION_TOTAL_ITEMS
    monkeypatch.delenv("TORTOISE_LME_REINJECTION_TOTAL_CAP")
    assert _run._resolve_reinjection_total_cap(
        True) == DEFAULT_REINJECTION_TOTAL_ITEMS


def test_reinjection_total_cap_explicit_is_clamped_like_the_env(monkeypatch):
    """#2513 (delta-review P2): the EXPLICIT value goes through the SAME
    clamp as the env value.

    The pre-fix shape returned it verbatim: ``explicit 0`` resolved 0, so
    the arm ran ON with zero injection and reported
    ``{'ok': True, 'total': 0, 'total_cap_hit': False}`` — no error, no
    signal — while ``'15'`` reached ``int``-typed arithmetic and raised a
    ``TypeError`` swallowed by the fail-open handler. Here explicit 0 / -3
    / '15' are asserted to resolve IDENTICALLY to their env twins.
    """
    from tools.longmem_eval import run as _run

    default = DEFAULT_REINJECTION_TOTAL_ITEMS
    # the env twins: 0 and -3 (out of range) fall back; '15' is honoured
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "0")
    assert _run._resolve_reinjection_total_cap(True) == default
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "-3")
    assert _run._resolve_reinjection_total_cap(True) == default
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "15")
    assert _run._resolve_reinjection_total_cap(True) == 15
    # ... and the explicit value resolves identically
    assert _run._resolve_reinjection_total_cap(True, 0) == default
    assert _run._resolve_reinjection_total_cap(True, -3) == default
    assert _run._resolve_reinjection_total_cap(True, "15") == 15
    assert _run._resolve_reinjection_total_cap(True, "garbage") == default
    # a legitimate explicit value is untouched, and an arm-OFF run still
    # resolves None (the clamp never fires off-path)
    assert _run._resolve_reinjection_total_cap(True, 7) == 7
    assert _run._resolve_reinjection_total_cap(False, 0) is None


# ── #2513 (#10629 review): the guard's LOWER bound — ARMING PATH ─────────
#
# The seam landed armable from the library only: no CLI flag, and neither
# eval-lane composition forwarded it, so the "exact next command" the PR body
# and #2513 published died at argparse and the arm was never selected at all.

def test_floor_term_resolves_tristate_and_the_guard_cap_clamps(monkeypatch):
    """#2513 (#10629 review): both guard knobs resolve ONCE, at arm
    resolution — never lazily inside the per-question path.

    ``floor_term`` is tri-state (explicit flag > env > OFF, fail-safe: only
    1/true/yes/on arms — it changes the graded top-k, so the sealed A/B
    decides adoption). ``guard_per_session_cap`` is explicit > env > the
    product constant through ``rerank._clamp_int``, and it resolves ``None``
    on a run with no pool-order arm: the guard is never called there, so no
    inert value is read, stamped or recorded.
    """
    from tools.longmem_eval import run as _run

    monkeypatch.delenv("TORTOISE_LME_FLOOR_TERM", raising=False)
    assert _run._resolve_floor_term() is False
    for raw in ("garbage", "0", ""):
        monkeypatch.setenv("TORTOISE_LME_FLOOR_TERM", raw)
        assert _run._resolve_floor_term() is False
    monkeypatch.setenv("TORTOISE_LME_FLOOR_TERM", "yes")
    assert _run._resolve_floor_term() is True
    # explicit flag beats the env in BOTH directions (tri-state)
    assert _run._resolve_floor_term(False) is False
    monkeypatch.delenv("TORTOISE_LME_FLOOR_TERM", raising=False)
    assert _run._resolve_floor_term(True) is True

    monkeypatch.delenv("TORTOISE_LME_GUARD_PER_SESSION_CAP", raising=False)
    assert _run._resolve_guard_per_session_cap(True) == DEFAULT_POOL_SESSION_CAP
    monkeypatch.setenv("TORTOISE_LME_GUARD_PER_SESSION_CAP", "3")
    assert _run._resolve_guard_per_session_cap(True) == 3
    for raw in ("garbage", "0", "-2"):
        monkeypatch.setenv("TORTOISE_LME_GUARD_PER_SESSION_CAP", raw)
        assert _run._resolve_guard_per_session_cap(
            True) == DEFAULT_POOL_SESSION_CAP
    # the explicit value goes through the SAME clamp as its env twin
    assert _run._resolve_guard_per_session_cap(True, 1) == 1
    assert _run._resolve_guard_per_session_cap(True, 0) == \
        DEFAULT_POOL_SESSION_CAP
    assert _run._resolve_guard_per_session_cap(True, "3") == 3
    # arm-OFF: None, whatever the env or the explicit value says
    monkeypatch.setenv("TORTOISE_LME_GUARD_PER_SESSION_CAP", "3")
    assert _run._resolve_guard_per_session_cap(False) is None
    assert _run._resolve_guard_per_session_cap(False, 1) is None


def test_the_guard_arm_requires_a_pool_order_composition():
    """#2513 (#10629 review): the knobs are REFUSED where they cannot act.

    The session-diverse guard runs only inside the coverage loop's merge or
    the re-injection MERGE (and the latter only while its guard is ON —
    ``--no-session-reinjection-guard`` is the injection-only ablation).
    Arming the lower bound anywhere else is a silent no-op, and a measurement
    lane reading its own receipt would report the OFF leg as the armed one —
    so the run aborts at arm resolution instead.
    """
    from tools.longmem_eval import run as _run
    from tools.longmem_eval.run import ArmConflictError, run_evaluation

    # the truth table: only the two guarded compositions arm the knobs
    assert _run._pool_order_arm_on(True, False, True) is True
    assert _run._pool_order_arm_on(False, True, True) is True
    assert _run._pool_order_arm_on(False, False, True) is False
    assert _run._pool_order_arm_on(False, True, False) is False

    for kwargs in (# the arms are pinned EXPLICITLY here, not inherited from
                   # the environment (#10629 review): cases 1-2 pass neither
                   # composition, so ``run_evaluation`` would resolve both from
                   # the documented env twins (``TORTOISE_LME_COVERAGE_LOOP`` /
                   # ``TORTOISE_LME_SESSION_REINJECTION``) — an exported value
                   # armed a pool-order composition and made the expected
                   # refusal DID-NOT-RAISE in the very test that pins the gate.
                   # Pinning them also asserts the POSITIVE side of the truth
                   # table above: no composition, knob armed ⇒ refused.
                   {"coverage_loop": False, "session_reinjection": False,
                    "floor_term": True},
                   {"coverage_loop": False, "session_reinjection": False,
                    "floor_term": False, "guard_per_session_cap": 3},
                   # a reinjection run with the guard ablated calls no guard
                   {"coverage_loop": False, "session_reinjection": True,
                    "session_reinjection_guard": False,
                    "floor_term": True}):
        with pytest.raises(ArmConflictError) as excinfo:
            run_evaluation([], reader=None, judge=None, split="s", **kwargs)
        assert "floor_term" in str(excinfo.value)
        assert "--coverage-loop" in str(excinfo.value)


def test_floor_term_and_guard_cap_ride_the_fingerprint():
    """#2513 (#10629 review): the BUILDER contract for the two guard knobs.

    ``floor_term`` is ALWAYS present (the arm/guard-bool convention), so a
    checkpoint produced with the lower bound armed can never be resumed with
    it OFF and a pre-feature checkpoint refuses on resume; the cap is present
    only while a pool-order arm runs (no inert value stamped).

    ⛔ Scope (the #10629 review's finding): this calls ``_build_fingerprint``
    DIRECTLY, so it proves only that the builder handles the keys — it cannot
    observe that ``run_evaluation`` forwards them, and it stayed green over a
    dropped `guard_per_session_cap` forward. The WIRING is pinned end to end
    by ``test_eval_reinjection_cap_resume.py::
    test_run_path_refuses_a_guard_cap_change_on_resume``.
    """
    from tools.longmem_eval import run as _run

    kw = dict(reader_model="m", judge_model="m", ks=(5,), top_k=5,
              split="s", ingest_mode="v2", extractor_model=None,
              max_retries=0, dataset_fingerprint="unknown", rerank_config={})
    off = _run._build_fingerprint(session_reinjection=True, **kw)
    assert off["floor_term"] is False
    assert "guard_per_session_cap" not in off   # inert → not stamped
    on = _run._build_fingerprint(session_reinjection=True, floor_term=True,
                                 guard_per_session_cap=3, **kw)
    assert on["floor_term"] is True
    assert on["guard_per_session_cap"] == 3
    assert "floor_term" in _run._fingerprint_diffs(off, on)
    assert "guard_per_session_cap" in _run._fingerprint_diffs(off, on)
    # a pre-feature fingerprint (no keys) refuses too
    assert "floor_term" in _run._fingerprint_diffs({}, on)


def test_both_pool_order_compositions_pass_the_same_guard_arm():
    """#2513 (#10629 review): ONE expression of the arm, spent by BOTH
    compositions.

    The fix's own failure mode is a dropped forward: the seam landed armable
    from the direct composition only, and three earlier review rounds each
    found a forward that had silently stopped being passed. The helper is
    asserted here (its resolution), and the two call sites are asserted
    STRUCTURALLY — ``retrieve.py`` imports the two functions by name, so no
    behavioural unit test can see a dropped keyword without a live graph, and
    the docker-lane E2E modules skip as a whole when FalkorDB is unavailable.
    """
    import ast

    from tools.longmem_eval import retrieve as _ret

    # the helper: None cap = the product default; an explicit cap is verbatim
    assert _ret._floor_arm_guard_kwargs(
        floor_term=True, guard_per_session_cap=None) == {
            "per_session_cap": DEFAULT_POOL_SESSION_CAP, "floor_term": True}
    assert _ret._floor_arm_guard_kwargs(
        floor_term=True, guard_per_session_cap=3) == {
            "per_session_cap": 3, "floor_term": True}
    assert _ret._floor_arm_guard_kwargs(
        floor_term=False, guard_per_session_cap=None) == {
            "per_session_cap": DEFAULT_POOL_SESSION_CAP, "floor_term": False}

    # both call sites spend it: each passes ``**<same name>``, and that name
    # is assigned exactly once, from ``_floor_arm_guard_kwargs`` — a dropped
    # forward (or a second, divergent expression) reds HERE
    tree = ast.parse((ROOT / "tools/longmem_eval/retrieve.py").read_text(
        encoding="utf-8"))
    spent_by: dict[str, set[str]] = {}
    helper_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute) else "")
        if name == "_floor_arm_guard_kwargs":
            helper_calls += 1
            continue
        if name not in ("_guard_and_recap_pool", "reinjection_merge_order"):
            continue
        spent_by.setdefault(name, set()).update(
            kw.value.id for kw in node.keywords
            if kw.arg is None and isinstance(kw.value, ast.Name))
    assert helper_calls == 1, helper_calls
    assert set(spent_by) == {"_guard_and_recap_pool",
                             "reinjection_merge_order"}, spent_by
    (spread_on,) = spent_by["_guard_and_recap_pool"]
    assert spent_by["reinjection_merge_order"] == {spread_on}, spent_by
    spread_source = f"{spread_on} = _floor_arm_guard_kwargs("
    assert (ROOT / "tools/longmem_eval/retrieve.py").read_text(
        encoding="utf-8").count(spread_source) == 1, spread_source


def test_fingerprint_and_resume_refuse_a_total_cap_change(tmp_path):
    """#2513 (delta-review P1, false-PASS class): the resolved injection
    total budget gates the checkpoint — a cap-10 checkpoint is REFUSED by a
    cap-15 resume, so two injection volumes can never blend into one
    artifact that declares one config.

    Every call below executes the real handlers (``_build_fingerprint`` /
    ``_save_checkpoint`` / ``_load_checkpoint``) — no source inspection.
    """
    from tools.longmem_eval import run as _run

    base = dict(reader_model="m", judge_model="m", ks=(5,), top_k=5,
                split="s", ingest_mode="v2", extractor_model=None,
                max_retries=0, dataset_fingerprint="unknown",
                rerank_config={}, session_reinjection=True,
                session_reinjection_guard=True)
    fp10 = _run._build_fingerprint(**base, reinjection_total_cap=10)
    cp = tmp_path / "cp_cap10.json"
    _run._save_checkpoint(str(cp), [], [], fp10)

    fp15 = _run._build_fingerprint(**base, reinjection_total_cap=15)
    # THE DEFECT: a cap-10 checkpoint must be REFUSED by a cap-15 resume —
    # asserted BEFORE the key-existence checks so a fingerprint that drops
    # the cap REDs here (silent acceptance of a different injection volume),
    # not on an incidental KeyError.
    with pytest.raises(_run.CheckpointStaleError) as ei:
        _run._load_checkpoint(str(cp), fp15)
    assert "session_reinjection_total_cap" in str(ei.value)
    assert fp10["session_reinjection_total_cap"] == 10
    assert fp15["session_reinjection_total_cap"] == 15

    # the LEGITIMATE form stays GREEN: the same cap resumes the checkpoint
    done, failures = _run._load_checkpoint(str(cp), fp10)
    assert done == {} and failures == []

    # an arm-OFF fingerprint carries NO cap key at all (the knob is inert —
    # a checkpoint that already carries the always-present C4 bools keeps
    # resuming byte-identically; a fingerprint-bearing pre-C4 one is refused
    # by those bools)
    off = _run._build_fingerprint(**dict(base, session_reinjection=False))
    assert "session_reinjection_total_cap" not in off

    # an arm-ON run at the product default stamps it explicitly, and BOTH
    # directions of the cross refuse (the key-union compares values)
    on_default = _run._build_fingerprint(
        **base, reinjection_total_cap=DEFAULT_REINJECTION_TOTAL_ITEMS)
    assert on_default["session_reinjection_total_cap"] == \
        DEFAULT_REINJECTION_TOTAL_ITEMS
    assert "session_reinjection_total_cap" in _run._fingerprint_diffs(
        on_default, fp15)
    assert "session_reinjection_total_cap" in _run._fingerprint_diffs({}, fp10)


def test_merge_with_no_new_ids_returns_the_base_pool_unchanged():
    pool = [_point("a0", "s1"), _chunk("x1", "s1")]
    out = reinjection_merge_order(
        pool, {}, guard=True, max_chunks_per_session=3)
    assert [h["id"] for h in out] == [h["id"] for h in pool]
    out2 = reinjection_merge_order(
        pool, {"s1": [_chunk("x1", "s1")]}, guard=True,
        max_chunks_per_session=3)
    assert [h["id"] for h in out2] == [h["id"] for h in pool]


def test_c5_recap_keeps_base_chunks_first_so_injection_cannot_evict_them():
    # s1 already holds 3 base chunks at the C5 ceiling: the injected chunk
    # is anchored after the last base hit and dropped by the re-cap. The
    # property must hold in BOTH guard configurations — the measured arm
    # runs guard=True, where the session-diverse reorder is the step that
    # could otherwise move an injected chunk ahead of a base one.
    pool = [_chunk("a1", "s1", idx=0), _chunk("a2", "s1", idx=1),
            _chunk("a3", "s1", idx=2), _point("b0", "s2")]
    added = {"s1": [_chunk("a4", "s1", idx=3)]}
    for guard in (False, True):
        out = reinjection_merge_order(
            pool, added, guard=guard, max_chunks_per_session=3)
        ids = [h["id"] for h in out]
        assert "a1" in ids and "a2" in ids and "a3" in ids, (guard, ids)
        assert "a4" not in ids, (guard, ids)


# ── shared contract: guard_and_recap_pool ───────────────────────────────
def test_guard_caps_a_monopolising_session_in_the_window():
    pool = [_point("a0", "s1"), _point("a1", "s1"), _point("a2", "s1"),
            _point("a3", "s1"), _point("b0", "s2"), _point("b1", "s2"),
            _point("c0", "s3"), _point("c1", "s3")]
    out = guard_and_recap_pool(pool, guard=True, window=5,
                               per_session_cap=2, max_chunks_per_session=3)
    assert [h["session_id"] for h in out[:5]].count("s1") <= 2
    assert set(h["id"] for h in pool) <= set(h["id"] for h in out)


def test_guard_is_a_noop_on_a_single_session_pool():
    pool = [_point("a0", "s1"), _point("a1", "s1"), _point("a2", "s1")]
    out = guard_and_recap_pool(pool, guard=True, window=5,
                               per_session_cap=2, max_chunks_per_session=3)
    assert [h["id"] for h in out] == [h["id"] for h in pool]


def test_guard_off_still_recaps_through_the_same_function():
    pool = [_chunk("a1", "s1", idx=0), _chunk("a2", "s1", idx=1),
            _chunk("a3", "s1", idx=2), _chunk("a4", "s1", idx=3)]
    out = guard_and_recap_pool(pool, guard=False, window=5,
                               per_session_cap=2, max_chunks_per_session=3)
    assert [h["id"] for h in out] == ["a1", "a2", "a3"]
    # the reorder is skipped, the re-cap is NOT
    assert [h["id"] for h in out] == [
        h["id"] for h in dedup_pool(pool, max_chunks_per_session=3)]


def test_guard_off_skips_only_the_reorder_and_matches_dedup_pool():
    """The guard-ablated arm must skip ONLY the session-diverse reorder
    (mutating the guard argument to always-reorder must fail this test)."""
    pool = [_point("a0", "s1"), _point("a1", "s1"), _point("a2", "s1"),
            _point("a3", "s1"), _point("a4", "s1"),
            _point("b0", "s2"), _point("c0", "s3")]
    off = guard_and_recap_pool(pool, guard=False, window=5,
                               per_session_cap=2, max_chunks_per_session=3)
    assert [h["id"] for h in off] == [
        h["id"] for h in dedup_pool(pool, max_chunks_per_session=3)]
    on = guard_and_recap_pool(pool, guard=True, window=5,
                              per_session_cap=2, max_chunks_per_session=3)
    assert [h["id"] for h in on] != [h["id"] for h in off]
    # the reorder is what differs: the same items, a different order
    assert sorted(h["id"] for h in on) == sorted(h["id"] for h in off)


def test_guard_recap_uses_the_pinned_pool_session_key():
    # same session_id, different lme indexes: one bucket, one cap.
    pool = [_chunk("a1", "s1", idx=0), _chunk("a2", "s1", idx=1),
            _chunk("a3", "s1", idx=2), _chunk("a4", "s1", idx=3)]
    out = guard_and_recap_pool(pool, guard=True, window=5,
                               per_session_cap=5, max_chunks_per_session=2)
    assert len(out) == 2


def test_reinjection_merge_order_forwards_the_floor_term():
    """#2513: the LOW (minimum-representation) bound must be armable from
    THIS composition too.

    ``reinjection_merge_order`` is the second composition the eval lane
    calls (``tools/longmem_eval/retrieve.py`` uses both it and
    ``guard_and_recap_pool`` directly), so a measured A/B that could arm
    only the direct one would leave the reinjection leg unmeasurable — the
    exact property the default-off seam exists to make possible.

    Fails pre-fix: ``floor_term`` is not a parameter (``TypeError``). Fails
    if the forward is dropped: ``floor_term=True`` is then accepted and
    silently ignored, leaving the capped output below.
    """
    # s1 and s2 hold two window slots each, s3 one — the window is FULL at
    # rank 5, so s4 (no base rank at all, hence spliced into the defensive
    # tail) is exactly the "session ranked past the window" case the floor
    # term exists for. Turn points, so the C5 re-cap cannot masquerade as
    # the reorder.
    pool = [_point("a0", "s1"), _point("a1", "s1"),
            _point("b0", "s2"), _point("b1", "s2"),
            _point("c0", "s3"), _point("c1", "s3")]
    added = {"s4": [_point("d0", "s4")]}
    kw = dict(guard=True, guard_window=5, per_session_cap=2,
              max_chunks_per_session=10)
    off = reinjection_merge_order(pool, added, floor_term=False, **kw)
    on = reinjection_merge_order(pool, added, floor_term=True, **kw)
    # precondition: without the floor term the unrepresented session stays
    # deferred past the window (the 4/0 row of the #2513 census).
    assert [h["id"] for h in off] == [
        "a0", "a1", "b0", "b1", "c0", "c1", "d0"], off
    # the bound reached the guard: s4 takes a slot back from s2's duplicate.
    assert [h["id"] for h in on][:5] == ["a0", "a1", "b0", "c0", "d0"], on
    assert len({h["session_id"] for h in on[:5]}) == 4, on
    # ... and it is the SAME answer the direct composition gives, so the two
    # eval compositions cannot disagree about the armed arm.
    assert [h["id"] for h in on] == [
        h["id"] for h in guard_and_recap_pool(
            pool + added["s4"], floor_term=True, window=5,
            per_session_cap=2, max_chunks_per_session=10)]
    # additive in both arms: the reorder never drops an item
    assert sorted(h["id"] for h in on) == sorted(h["id"] for h in off)


# ── contract: key authority + import direction ──────────────────────────
def test_session_key_matches_the_historical_bucket_key():
    assert session_key_of({"session_id": "s1"}) == "s1"
    assert session_key_of({"lme_session_index": 7}) == "idx:7"
    assert session_key_of({}) == "idx:-1"
    # dedup_pool's default is the same authority
    pool = [_chunk("a1", "s1"), _chunk("a2", "s1"), _chunk("a3", "s1")]
    assert len(dedup_pool(pool, max_chunks_per_session=1)) == 1
    assert is_raw_chunk(_chunk("a1", "s1"))
    assert not is_raw_chunk(_point("a1", "s1"))


def test_identity_less_hits_get_a_per_hit_bucket_3591():
    """#3591: an identity-less hit is keyed by its OWN id, so two unrelated
    identity-less chunks no longer share the C5 allowance and are not
    dropped collectively. The PHANTOM property is unchanged — an ``idx:``
    key still names no graph session, so it can never seed a fetch."""
    alpha, beta = _chunk("alpha", ""), _chunk("beta", "")
    assert session_key_of(alpha) == "idx:point:alpha"
    assert session_key_of(beta) == "idx:point:beta"
    # The shipped defect (measured before the fix): both read ``idx:-1``, so
    # cap=1 dropped ``beta`` and cap=3 kept only 3 of 5.
    assert [h["id"] for h in dedup_pool([alpha, beta],
                                        max_chunks_per_session=1)] == [
        "alpha", "beta"]
    five = [_chunk(f"c{i}", "") for i in range(5)]
    assert [h["id"] for h in dedup_pool(five,
                                        max_chunks_per_session=3)] == [
        f"c{i}" for i in range(5)]
    # The historical key is UNCHANGED where an index exists — including the
    # explicit ``-1`` sentinel, which is not the same shape as absent.
    assert session_key_of({"lme_session_index": 7}) == "idx:7"
    assert session_key_of({"lme_session_index": -1}) == "idx:-1"
    # ... and a hit with nothing at all to key on keeps the one bucket.
    assert session_key_of({}) == "idx:-1"
    # PHANTOM, not session: a per-hit bucket is still refused as a seed, so
    # the fix cannot cause a fetch against a session id that does not exist.
    assert seeded_sessions([alpha, beta], window=5, limit=5) == []
    assert seeded_sessions([_chunk("a1", "s1")], window=5, limit=5) == [
        SeededSession(session_id="s1", rank=0, point_id="a1")]


def test_coverage_loop_session_of_delegates_to_the_authority(monkeypatch):
    # sentinel: coverage_loop must DELEGATE, not re-implement — reverting it
    # to the historical inline copy must fail this test
    monkeypatch.setattr(retrieval, "session_key_of",
                        lambda h: "SENTINEL")
    assert coverage_loop._session_of({"session_id": "s1"}) == "SENTINEL"
    assert coverage_loop._session_of({"lme_session_index": 7}) == "SENTINEL"
    assert coverage_loop._session_of(
        {"lme_session_index": 7}, lambda h: "explicit") == "explicit"


def test_no_import_cycle_under_either_import_order():
    for first in ("tortoise.retrieval", "tortoise.coverage_loop"):
        proc = subprocess.run(
            [sys.executable, "-c",
             f"import {first}; import tortoise.retrieval; "
             "import tortoise.coverage_loop; import tortoise.session_reinjection"],
            cwd=str(ROOT), capture_output=True, text=True)
        assert proc.returncode == 0, (first, proc.stderr[-2000:])


# ── chunk-kind single source (all four consumers) ───────────────────────
def test_chunk_kind_is_single_sourced_across_all_four_consumers():
    """#2517: the product constant is the ONE source — is_raw_chunk, the
    D5 exclusion twin, the equality filter the chunk-count Cypher binds,
    and the ingest re-export all derive from it; drift fails closed."""
    from tools.longmem_eval.ingest import (
        SESSION_TRANSCRIPT_KIND as _ingest_kind,
    )
    from tools.longmem_eval.retrieve import (
        CHUNK_KIND_FILTER,
        D5_POINTKIND_FILTER,
    )

    assert SESSION_TRANSCRIPT_KIND == "session-transcript"
    assert _ingest_kind == SESSION_TRANSCRIPT_KIND
    expected_chunk = (
        f"coalesce(p.pointKind, '') = {SESSION_TRANSCRIPT_KIND!r}")
    expected_exclusion = (
        f"coalesce(p.pointKind, '') <> {SESSION_TRANSCRIPT_KIND!r}")
    assert expected_chunk == CHUNK_KIND_FILTER
    assert expected_exclusion == D5_POINTKIND_FILTER
    assert is_raw_chunk({"point_kind": SESSION_TRANSCRIPT_KIND}) is True
    assert is_raw_chunk({"point_kind": "statement"}) is False


# ── annotate_pool_additions: the whole-dict no-regression proof ─────────
def test_annotate_pool_additions_is_whole_dict_identical_except_leg():
    """#2517: the C3-1 annotation extraction must reproduce EVERY annotated
    key (21), not a subset — ``session_date`` in particular is derived from
    the QUESTION's ``haystack_dates`` via ``lme_session_index``, so a
    4-field golden would pass while it silently emptied."""
    from tools.longmem_eval.retrieve import (
        _annotate_hits,
        annotate_pool_additions,
    )

    hits = [{"id": "p1", "content": "c1", "match_source": "fts"}]
    props = {"p1": {"session_id": "s1", "lme_session_index": 1,
                    "has_answer": True, "content": "c1", "quote": "q",
                    "search_keys": ["k"], "source_turn_id": "t1",
                    "speaker": "user", "point_kind": "event"}}
    dates = ["2026-01-01", "2026-01-02"]
    base = _annotate_hits([dict(hits[0])], props, dates)[0]
    added = annotate_pool_additions(
        [dict(hits[0])], props, dates, match_source="session")[0]
    assert len(base) == 21 and len(added) == 21  # +status (C6 #2520) +the #2350 triple
    assert set(base) == set(added)
    assert "status" in base  # the C6 key is present BY NAME
    for key in base:
        if key == "match_source":
            continue
        assert added[key] == base[key], key
    assert added["session_date"] == "2026-01-02"
    assert added["match_source"] == "session"
    assert base["match_source"] == "fts"


# ── the DELETED demo turn writer may not be described as current fact ───────
_REPO = Path(__file__).resolve().parent.parent

#: Every home in the tree that explains WHY the turn-shape constraint needs
#: the ``is_episodic`` conjunct (``session_reinjection._TURN_SHAPE_FILTER``).
#: The hosted demo/dashboard seed's ``pointKind='event'`` turn writer is
#: HISTORY since #10000, so a home stating it as current fact is the sentence a
#: later reader would cite to delete a conjunct a legacy graph still needs.
_TURN_SHAPE_NARRATIVE_HOMES = (
    "tortoise/session_reinjection.py",
    "tortoise/retrieval.py",
    "tests/test_session_reinjection.py",
    "tests/test_session_reinjection_rules.py",
    # The eval census's own residual note: it names `_TURN_SHAPE_FILTER` by
    # name and states that excluding a flagless `pointKind='event'` demo node
    # is the repo's decision rather than a local predicate — the same narrative
    # this tuple exists to keep honest, so it is a home. ⚠️ Its presence here
    # is COVERAGE, not a correction this change makes at this base: it already
    # carries #10000 (it is byte-identical on origin/main), so the two halves
    # below pass there too and this entry is vacuous AGAINST THIS BASE. The
    # earlier wording (`the demo seed, tortoise/hosted_api.py:12173`, no
    # `#10000`) exists only on this branch's own prior commits — which is why
    # the entry was added, and is not a claim about the base.
    "tools/longmem_eval/ep_activation.py",
)

#: Homes that name the demo/sample seed for a reason that is NOT about the
#: `is_episodic` conjunct and so cannot carry the marker-beside-the-mention
#: invariant, but that still must never state the DELETED writer as current fact.
#: `session_schema.py` has exactly ONE seed mention (`_SEED_MENTION`): a
#: PAST-TENSE line in the three-shapes history ("…the demo seeder wrote a third
#: shape…") that carries no `#10000` because it is not about this change — so
#: the windowed half would demand a marker that does not belong there.
#: Present-tense half ONLY, on purpose: pinning the marker would need an
#: exception carved inside the window loop, and an exception is how a population
#: pin stops being one (#10675 review P3).
_TURN_SHAPE_NARRATIVE_HOMES_PRESENT_TENSE_ONLY = (
    "tortoise/session_schema.py",
)

#: The PRESENT-TENSE form: the demo/dashboard seed named as the WRITER of
#: ``pointKind='event'``. The corrected sentences all say the opposite ("since
#: #10000 it writes no turn node at all"), which this deliberately does not
#: match — note the stored form is spelled out here as prose precisely so this
#: comment is not itself a hit.
#: ⛔ ``seed(?:er)?`` is load-bearing: "the demo seeder" is this tree's own
#: vocabulary (`session_schema.py`, `tools/turn_parity_census.py`,
#: `ep_activation.py`) and a bare ``seed`` misses every one of those spellings —
#: measured: injecting "the demo SEEDER WRITES ``pointKind='event'``" left the
#: ``seed\s+writes?`` form GREEN (#10675 review P2). The qualifier and the
#: bounded window keep it from firing on an unrelated seed ("the demo seed
#: writes ~10 Points") or on the corrected prose ("a graph seeded BEFORE
#: #10000 … the seed holds ``pointKind``"). ⚠️ The demonstration above is
#: spelled in CAPITALS as belt-and-braces only — the block is EXCISED before
#: this file is scanned (see `_GUARD_BLOCK`), so a lower-case sample here would
#: not match either. Measured: injecting one inside the block leaves the pin
#: GREEN.
#:
#: ⛔ TWO MORE PROPERTIES, both measured (#10675 review round 10, P2):
#: * **CASE** — without ``IGNORECASE`` an uppercase ``WRITES`` was fail-OPEN.
#:   That was the sharpest form of the defect: this guard's own failure message
#:   spelled the claim in capitals, i.e. in a spelling its own regex could not
#:   match.
#: * **KIND** — the seeder WRITES points of other kinds: its semantic,
#:   epistemic and procedural tuples bind ``observation``, ``statement``,
#:   ``hypothesis``, ``decision``, ``evidence`` and ``workflow`` through
#:   ``p.pointKind=$k`` (``hosted_api.py``'s ``_DEMO_*_POINTS``, one shared
#:   write loop). Those are TRUE present-tense statements, and matching a bare
#:   ``pointKind`` reds them as if they were the deleted writer. So the kind is
#:   CAPTURED and read in code: a violation is an UNQUALIFIED kind or
#:   ``event``; any other named kind is explicitly not this claim. Measured:
#:   ``writes ``pointKind='hypothesis'`` `` was a false RED before, and is
#:   GREEN now.
_PRESENT_TENSE_DEMO_EVENT_WRITER = re.compile(
    # The SUBJECT: the qualified seed noun, the FUNCTION name, or the bare
    # ``the seeder``. `the ask-fixture seeder` does not match — the article has
    # to sit directly on ``seeder`` — and that seeder really does read `event`,
    # so it must not be swept up.
    r"(?:(?:demo|dashboard|hosted|sample)[^.\n]{0,40}?seed(?:er)?"
    r"|_seed_demo_graph|the\s+seeder)"
    # The gap matters: this tree writes "the demo seed STILL writes …",
    # "… CONTINUES TO WRITE …", and backticks the identifier
    # (``_seed_demo_graph``) — all of which a bare `subject\\s+verb` misses,
    # measured (round 12, P2: all three were GREEN on a present-tense `event`
    # claim). Backticks only, never a bare `\s*`, so the subject must still be
    # the sentence's subject.
    r"[`'\"“]*\s+(?:also\s+|still\s+|continues?\s+to\s+)?"
    r"(?:writes?|mints?|creates?)\b"
    # * **NEGATION** — the CORRECTED sentences say the seed "writes NO turn
    #   node at all", and a bare verb match refuses exactly the prose this pin
    #   wants written: measured, "since #10000 the demo seed writes no
    #   `pointKind='event'` node" matched before this lookahead and is GREEN
    #   after it (the `event` kind would otherwise still be a violation).
    r"(?!\s+(?:no|nothing|none|never)\b)[^.\n]{0,160}?pointKind"
    r"(?:\s*[=:]\s*[`'\"“]*[a-z_]+)?", re.IGNORECASE)

#: The kind a ``pointKind`` mention names, if it names one — the group read by
#: :func:`_present_tense_writer_claim`. Split out so the KIND rule above is
#: stated once, in code, instead of as a lookahead inside the pattern.
_POINT_KIND = re.compile(r"pointKind\s*[=:]\s*[`'\"“]*([a-z_]+)", re.IGNORECASE)


def _present_tense_writer_claim(flat: str) -> str | None:
    """The forbidden present-tense claim in ``flat``, or ``None``.

    A match is a violation unless it names a kind that is NOT ``event``: the
    seeder still writes ``claim`` points, so a true sentence about those must
    not red (#10675 review round 10, P2).
    """
    for hit in _PRESENT_TENSE_DEMO_EVENT_WRITER.finditer(flat):
        kind = _POINT_KIND.search(hit.group())
        if kind is None or kind.group(1).lower() == "event":
            return hit.group()
    return None

#: The guard's OWN block, EXCISED before this file is scanned. Its comments must
#: quote the pre-``#10000`` wording AND the marker that annotates it, so leaving
#: them in let the pin satisfy ITSELF: measured, deleting a marker from the
#: guard's own commentary left the pin GREEN (#10675 review round 10, P3).
#: Excising the block is what makes "this file is also a home" falsifiable
#: rather than circular — the alternative, spelling every sample in CAPITALS so
#: the pattern misses its own comment, only protects the one demonstration and
#: leaves the rest of the file's prose in the population.
#: ⛔ Spelled as a CONCATENATION so this line does not contain the marker:
#: the check below requires exactly ONE occurrence in the file, and a
#: self-matching literal here would make a reworded header split at this
#: assignment — reintroducing the self-grading it exists to prevent.
_GUARD_BLOCK = "# ── the " + "DELETED demo turn writer"
#: Where the guard's machinery ENDS and the population file resumes. Excising
#: from the header to EOF would leave every test appended after it unscanned —
#: measured (round 12, P2): a present-tense claim appended at EOF stayed GREEN.
_GUARD_END = "def test_no_home_states_the_deleted_demo_turn_writer_as_current_fact("


def _narrative_text(rel: str) -> str:
    """The home's text, with this guard's own commentary removed for its file."""
    raw = (_REPO / rel).read_text(encoding="utf-8")
    if rel == "tests/test_session_reinjection_rules.py":
        # ⛔ The excision must be VERIFIED, not assumed: `split` with a marker
        # that no longer occurs silently returns the whole file, which puts the
        # guard's own commentary back in the population and lets the pin grade
        # ITSELF again (#10675 review round 11, P2). Measured: renaming the
        # section header made the pin RED on its own CAPITALS demonstration,
        # with nothing pointing at the real cause.
        parts = raw.split(_GUARD_BLOCK, 1)
        assert len(parts) == 2 and raw.count(_GUARD_BLOCK) == 1, (
            f"the guard block marker {_GUARD_BLOCK!r} was not found in "
            f"`{rel}` — the section header it names is what keeps this pin from "
            "grading its own commentary, so a reworded header must update "
            "`_GUARD_BLOCK` too")
        rest = parts[1]
        assert _GUARD_END in rest, (
            f"the guard's machinery end marker {_GUARD_END!r} was not found "
            f"after the guard block in `{rel}` — without it the excision would "
            "swallow every test below the guard, which is where a later lane "
            "appends")
        raw = parts[0] + rest.split(_GUARD_END, 1)[1]
    return " ".join(raw.split())


#: HOW far from a seed mention the ``#10000`` history marker may sit and still
#: be read as annotating it. The POSITIVE half below is a WINDOW, not a
#: file-scoped ``"#10000" in flat``: every one of these homes carries OTHER
#: ``#10000`` mentions — re-derive with `grep -c '#10000'` rather than trusting a
#: count written into this comment, which rots the moment any lane edits the
#: prose (the first version of this note stated a census that the very fix
#: introducing it had already made stale; #10675 review round 10, P3). A
#: file-scoped check stays GREEN whenever the marker leaves the one sentence
#: that makes the conjunct defensible — and for THIS file it was strictly
#: tautological, since the assertion's own message names ``#10000``.
#: ⚠️ It is a DISTANCE heuristic (±`_MARKER_WINDOW` chars around the
#: mention), deliberately generous: a tighter radius red a home that was merely
#: EXPANDED — measured, 500 chars of new prose between the mention and its
#: marker, still the same sentence (#10675 review round 11, P3) — while a
#: paragraph-scoped or sentence-scoped window red a home whose marker sits in
#: the sentence BEFORE its `demo-seed` mention. Its job is to tell "this home
#: says the nodes are pre-#10000 history" from "any `#10000` anywhere in this
#: file"; it is not a grammar.
_MARKER_WINDOW = 800
_SEED_MENTION = re.compile(r"demo(?:/dashboard)?[\s-]+seed", re.IGNORECASE)


def test_no_home_states_the_deleted_demo_turn_writer_as_current_fact():
    """#10000 review P3: three homes were corrected and the fourth was missed.

    `hosted_api._seed_demo_graph` is the non-turn ``pointKind='event'`` writer
    the ``is_episodic`` conjunct exists to exclude — the shape `#10000` removed
    across §9.1/D1. A home that still says the seed WRITES that shape is false
    in the present tense, and it reads as grounds for deleting the (still
    necessary, legacy-population) conjunct.

    A POPULATION assertion over every narrative home, not a pin at the one site
    this round edited: that site-local shape is the failure. The same sentence
    was restated in `session_reinjection.py`, `retrieval.py` and
    `test_session_reinjection.py` and left present-tense HERE, so a pin inside
    the one file being fixed reds on nothing when another home is added.
    """
    missing = [rel for rel in _TURN_SHAPE_NARRATIVE_HOMES
               + _TURN_SHAPE_NARRATIVE_HOMES_PRESENT_TENSE_ONLY
               if not (_REPO / rel).is_file()]
    assert not missing, (
        "a narrative home moved or was deleted, so this pin no longer covers "
        f"the claim it exists for: {missing}")

    for rel in _TURN_SHAPE_NARRATIVE_HOMES + _TURN_SHAPE_NARRATIVE_HOMES_PRESENT_TENSE_ONLY:
        flat = _narrative_text(rel)
        present = _present_tense_writer_claim(flat)
        assert present is None, (
            f"`{rel}` states the demo/dashboard seed as the PRESENT-TENSE "
            f"writer of a `pointKind='event'` point — quoted: {present!r} — "
            "but since #10000 it writes no turn node at all "
            "(§9.1/D1). Restate it as history (the clause is kept because a "
            "graph seeded BEFORE #10000 still holds those nodes) rather than "
            "leaving a claim about a writer that no longer exists")
        if rel in _TURN_SHAPE_NARRATIVE_HOMES_PRESENT_TENSE_ONLY:
            continue
        # The positive half: the HISTORY MARKER is what makes the surviving
        # conjunct defensible. Without it the clause reads as dead weight and
        # the next reader deletes it — and legacy demo graphs lose the only
        # exclusion keeping their orphan `event` nodes out of the turn set.
        # ⛔ WINDOWED, and it must be: a file-scoped check passes on ANY
        # `#10000` mention elsewhere in the file, so it cannot red on the
        # regression above that it claims to guard.
        seed_hits = list(_SEED_MENTION.finditer(flat))
        assert seed_hits, (
            f"`{rel}` is a declared narrative home but no longer mentions the "
            "demo/dashboard seed at all — either the home was reworded beyond "
            "this pin's reach or the seed's legacy `event` nodes stopped being "
            "the reason given for the `is_episodic` conjunct")
        for _hit in seed_hits:
            window = flat[max(0, _hit.start() - _MARKER_WINDOW):
                          _hit.end() + _MARKER_WINDOW]
            assert "#10000" in window, (
                f"`{rel}` names the demo/dashboard seed without naming #10000 "
                "beside it, so the reader cannot tell the seed's `event` nodes "
                "are a LEGACY population the conjunct is retained for. Got: "
                f"...{window}...")
