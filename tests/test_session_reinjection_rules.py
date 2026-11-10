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

import ast
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
    reader_window_displacement,
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


def test_an_unseeded_group_key_is_dropped_never_tailed():
    """#10540: the merge DROPS a group whose key is not in ``seed_order``.

    This is the EXCLUSION half of the corrected contract, and it is what
    both ``source_session_chunk_pass`` and the module docstring now promise.
    The sibling test above pins only the tail's ORDER when EVERY added key is
    seeded, so a refactor that UNIONED ``added_by_session`` into ``order``
    would keep that test green while making every docstring in this module
    false again — and the graph fetch would have charged the per-session /
    total budget for rows it then spliced in.

    Red on that refactor; green on the shipped code, which builds its groups
    over ``order`` (``seed_order``) alone. ⚠️ Scoped to the UNION variant,
    and measured rather than assumed: making only the group BUILD iterate
    ``added_by_session`` leaves this test GREEN, because the anchor loop and
    the defensive tail still walk ``order`` — so the unseeded group is never
    spliced, i.e. it is still DROPPED, which is exactly what this test
    asserts. Widening ``order`` itself is the mutation that breaks the
    contract.
    """
    pool = [_point("a0", "s1"), _point("a1", "s1")]
    added = {"s1": [_chunk("x1", "s1")], "s2": [_chunk("y1", "s2")]}
    out = reinjection_merge_order(
        pool, added, seed_order=["s1"], guard=False,
        max_chunks_per_session=3)
    # the SEEDED group splices after s1's last base rank ...
    assert [h["id"] for h in out] == ["a0", "a1", "x1"]
    # ... and the unseeded one is DROPPED — not tailed, not spliced
    assert "y1" not in [h["id"] for h in out]
    # dropping is TOTAL: an added key with no seeded sibling disappears too
    only_unseeded = reinjection_merge_order(
        pool, {"s2": [_chunk("y1", "s2")]}, seed_order=["s1"], guard=False,
        max_chunks_per_session=3)
    assert [h["id"] for h in only_unseeded] == ["a0", "a1"]


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


# ── #10540: the wire identity the product fetch actually emits ──────────
def _wire_turn(pid: str, sid: str, text: str = "[user] hi") -> dict:
    """A pool hit byte-shaped like ``tortoise_fts_query``'s point fetch
    returns for a captured turn (``tortoise/sdk.py``, the D3/#3804 block):
    the session identity arrives as the CAMEL wire key ``sessionId`` ALONE —
    the fetch resolves it from ``n.sessionId`` / the snake prop / the
    ``:Session`` ``CONTAINS`` edge and then POPS the snake key. A product
    turn Point carries no ``session_id`` prop, so there is no snake key to
    pop and ``sessionId`` is the hit's only identity."""
    return {"id": pid, "sessionId": sid, "point_kind": TURN_POINT_KIND,
            "content": text, "is_episodic": True}


def test_wire_session_identity_keys_the_pool_and_seeds_the_session():
    """#10540 red-first: a retrieved answer must carry the source session it
    came from, and the C4 arm must SEE it.

    MEASURED on the lane's tree before the fix (product-shaped pool, two
    captured sessions, turns only):

        hit_session_id  -> ['sessA', 'sessB', 'sessA', 'sessB']   (reader: OK)
        session_key_of  -> ['idx:point:A_t0', 'idx:point:B_t0', ...]  (pool: BLIND)
        seeded_sessions -> []                                     (arm: INERT)

    i.e. the identity WAS on the retrieved answer (the reader rendered
    ``[session sessA]``) and the pool authority one layer below it dropped
    it, so ``seeded_sessions`` rejected every turn hit as a phantom
    (``_is_real_session_id`` refuses ``idx:*``) and on the raw search pool
    no hit could seed a session at all — the fetch pops the snake id from
    every point hit, turn or extracted, so the camel key was the only
    identity left. Scope in full: see ``session_reinjection``'s module
    docstring."""
    pool = [_wire_turn("A_t0", "sessA"), _wire_turn("B_t0", "sessB"),
            _wire_turn("A_t1", "sessA"), _wire_turn("B_t1", "sessB")]

    # the identity is on the hit; both authorities must agree on it
    assert [retrieval.hit_session_id(h) for h in pool] == [
        "sessA", "sessB", "sessA", "sessB"]
    assert [session_key_of(h) for h in pool] == [
        "sessA", "sessB", "sessA", "sessB"]

    # ... so the SEED fires, rank-ordered, one entry per distinct session
    assert seeded_sessions(pool, window=40, limit=5) == [
        SeededSession(session_id="sessA", rank=0, point_id="A_t0"),
        SeededSession(session_id="sessB", rank=1, point_id="B_t0")]

    # the phantom branch is still the branch for a hit carrying no identity
    # in any spelling — #3591's behaviour is untouched
    assert session_key_of({"id": "z"}) == "idx:point:z"
    assert seeded_sessions([{"id": "z"}], window=5, limit=5) == []


def test_wire_identity_never_moves_an_existing_key():
    """#10540's inertness claim, and the reason the camel source sits AFTER
    the ``lme_session_index`` bucket: **every key an IDENTIFIED hit already
    had that is not PADDED stays byte-identical** (a snake ``session_id``; an
    ``lme_session_index`` bucket), so the census #8713 measured cannot move.

    ⚠️ #11382 scoped that sentence: a PADDED-but-real snake id is an
    IDENTIFIED key and it DOES move (``'  s1  '`` → ``'s1'``), on purpose —
    that is the defect, not a phantom (`retrieval.session_key_of`'s own page
    says so, and the flagged key below skips it for exactly that reason).
    What stays byte-identical is every key that is a real identity already in
    normal form; the PHANTOM keys are the other class that changes.

    The eval's pool hits carry the snake ``session_id``
    (``tools/longmem_eval/retrieve.py``: ``{"session_id": p.get("session_id",
    "")}``) and the eval lane's index bucket rides alongside; both outrank
    the camel leg."""
    # an eval-shaped hit: snake + index (+ a camel spelling that must NOT win)
    eval_hit = {"id": "e1", "session_id": "lme_s1", "lme_session_index": 3,
                "sessionId": "lme_s1", "point_kind": SESSION_TRANSCRIPT_KIND}
    assert session_key_of(eval_hit) == "lme_s1"
    # an eval-shaped hit with NO snake id still keeps its index bucket —
    # this is the case the camel leg could have silently stolen
    assert session_key_of({"id": "e2", "lme_session_index": 3,
                           "sessionId": "lme_s1"}) == "idx:3"
    assert session_key_of({"id": "e3", "lme_session_index": -1,
                           "sessionId": "lme_s1"}) == "idx:-1"
    # explicit identity outranks the derived wire spelling
    assert session_key_of({"session_id": "snake",
                           "sessionId": "camel"}) == "snake"
    # a non-string / empty wire value is not an identity
    assert session_key_of({"id": "e4", "sessionId": None}) == "idx:point:e4"
    assert session_key_of({"id": "e5", "sessionId": ""}) == "idx:point:e5"
    # non-str spellings are refused by the isinstance guard, not stringified
    # — on BOTH spellings (#11382). Before #11382 only the camel leg had the
    # guard: the snake leg returned the value verbatim, so
    # ``{"session_id": 5}`` keyed the int ``5`` and all three key-reading
    # callers then raised ``AttributeError`` on ``.startswith``
    # (``seeded_sessions`` through ``_is_real_session_id``,
    # ``reader_window_displacement``'s ``idx:`` set comprehensions, and
    # ``coverage_loop.coverage_gap``). ⚠️ For an UNHASHABLE value (``[1]``) the
    # exception CLASS depends on which runs first: ``coverage_gap`` raises
    # ``TypeError: unhashable type`` where it builds a set around the key,
    # while the other two reach the ``.startswith`` ``AttributeError`` before
    # any hash is taken — measured on the pre-fix tree, so do not read the
    # ``TypeError`` as universal. The loop runs both spellings on purpose: two
    # per-branch blocks would let a one-sided regression pass, which is the
    # shape #11382 exists to close.
    for junk in (0, 5, True, {"a": 1}, ["s"]):
        for spell in ("sessionId", "session_id"):
            assert session_key_of({"id": "e6", spell: junk}) == (
                "idx:point:e6"), spell


def test_a_non_str_session_identity_is_discarded_not_keyed_11382():
    """#11382, the NO-`id` half of the non-str arm: a non-str identity is
    DISCARDED, so the hit falls all the way to the identity-less bucket.

    Its own test, not a tail of the arm above, so each guard has an
    attributable mutation row: in one function the id-bearing loop fails
    first and this assert could never be the row that goes red.

    ⚠️ This is the one place the fail-closed leg FUSES two distinct values:
    with no ``id`` and no ``lme_session_index``, ``{"session_id": 5}`` and
    ``{"session_id": 6}`` are the SAME ``idx:-1`` bucket — and so is a hit
    carrying no identity at all. Pinned deliberately rather than left silent,
    because the alternative (keying the raw int) is the shape this arm
    replaces: pre-#11382 the two were distinct dict keys, and the callers that
    read the key as TEXT crashed on them — ``seeded_sessions`` (through
    ``_is_real_session_id``'s ``.startswith``), ``coverage_loop.coverage_gap``
    and ``reader_window_displacement``. ⚠️ Not EVERY caller did: measured on
    the pre-fix tree, ``dedup_pool``, ``session_diverse_order`` and
    ``reinjection_merge_order`` only DICT-KEY the value and returned normally
    — which is why the hazard was a crash on three callers, not a universal
    one. It binds only the untyped seam
    ``retrieval.session_key_of`` documents — no in-repo pool carries a
    non-``str`` session id — and it is the same answer the camel leg has given
    since #10540, now spelled by both.

    An ``id`` changes the answer, which is why the arm above asserts
    ``idx:point:e6``: the per-hit bucket is per-ID, and only a hit with no id
    at all reaches the shared ``idx:-1``.
    """
    for spell in ("sessionId", "session_id"):
        assert session_key_of({spell: 5}) == "idx:-1", spell
        # ... a SECOND distinct value lands in the same bucket, which is the
        # fusion this test exists to pin: 5 and 6 are one identity-less bucket.
        assert session_key_of({spell: 6}) == "idx:-1", spell
        # ... and an explicit index still outranks the phantom bucket, so the
        # collapse above cannot swallow an eval hit's own ``idx:{n}``.
        assert session_key_of({spell: 5, "lme_session_index": 3}) == (
            "idx:3"), spell


def test_wire_identity_is_stripped_like_the_reader_names_it():
    """#10540: the pool key and the reader must agree on whether a hit HAS an
    identity, and on WHICH one — the two authorities answer that question,
    and an unstripped pool leg split them.

    MEASURED with the camel leg added but BEFORE the ``strip()`` (this PR's
    first commit, ``40c4d85578``): ``session_key_of({"id":"p",
    "sessionId":"  "}) -> "  "`` while ``hit_session_id`` returned ``""``,
    and ``_is_real_session_id("  ")`` is True — so ``seeded_sessions``
    returned a ``SeededSession(session_id="  ")``: a seed, a graph fetch, and
    a census entry for a session the reader calls UNKNOWN. (At
    ``origin/main`` there is no camel leg at all, so the hit was a phantom —
    that is the neighbouring test's baseline, not this one's.)

    ⛔ Scope — what the strip does NOT do (corrected: the earlier wording
    argued this backwards, and #11382 then closed it). The fetch groups by
    ``coalesce(p.session_id, s.id)`` — the RAW STORED spelling — so a
    *stripped* seed key ALONE could never equal it, and a one-sided strip
    CAUSES a charged-then-dropped group rather than preventing it. That is
    why #11382 normalises BOTH sides (the fetch's group key through the same
    ``retrieval.normalize_session_key``) instead of tightening one; the
    one-sided shape is pinned as the red case by
    ``test_padded_session_id_is_not_charged_then_dropped_11382``. What is
    still UNSETTLED is the STORED spelling, i.e. whether the write door
    should refuse or normalise a padded id, and whether two nodes (``s1`` /
    ``  s1  ``) can coexist — an OPEN OWNER QUESTION recorded on the CLOSED
    #9599, whose home is ``tortoise/ids.py``; do not read that issue's closure
    as a decision about padding. The key normalisation neither merges nor
    rejects
    what the write door accepted. On a REAL product hit the strip is a
    no-op, because ``sdk.tortoise_fts_query`` sanitises the wire value at
    the producer. What this test therefore pins is exactly the hand-built
    half: the pool and the reader agree on whether a hit HAS an identity, and
    on which.

    Stripping is not filtering: the reader's ALLOWLIST is deliberately not
    applied here (``session_key_of``'s docstring says why), so this pins the
    whitespace half only.
    """
    assert session_key_of({"id": "p", "sessionId": "  "}) == "idx:point:p"
    assert session_key_of({"id": "p", "sessionId": "\t\n"}) == "idx:point:p"
    assert session_key_of({"id": "p", "sessionId": " S1 "}) == "S1"
    assert session_key_of({"id": "p", "sessionId": "S1"}) == "S1"
    # the two authorities agree on both the existence and the value
    for sid in ("S1", " S1 ", "  "):
        hit = {"id": "p", "sessionId": sid}
        key = session_key_of(hit)
        assert retrieval.hit_session_id(hit) in ("", key)
        assert (key == "idx:point:p") == (retrieval.hit_session_id(hit) == "")
    # a whitespace-only identity fails CLOSED: no seed, nothing fetched
    assert seeded_sessions([{"id": "p", "sessionId": "  "}],
                           window=5, limit=5) == []


def test_injected_group_anchors_on_its_own_sessions_product_base_ranks():
    """#10540, the MERGE half: with the wire identity visible, a seeded
    session's injected group lands immediately AFTER that session's own base
    ranks.

    MEASURED by REMOVING the camel leg from ``session_key_of`` — a mutation of
    this tree, NOT a pre-fix behaviour of it: the anchor map is then built
    from phantom keys, ``sessA`` has no base rank at all, and its injected
    group falls to ``reinjection_merge_order``'s defensive TAIL —
    ``['A_t0', 'B_t0', 'B_t1', 'A_t1']`` — placing one session's context
    behind every other session's evidence. That is the displacement direction
    this issue's title forbids, arriving from the SEED side rather than from
    a head placement.

    ⚠️ The distinction matters, and the module docstring now makes the same
    one: pre-fix there was no camel leg, so ``seeded_sessions`` returned
    ``[]`` and NO injected group existed to place. The tail is reachable only
    from a pool keyed by one authority and merged by another — the shape this
    test constructs directly, and the one the fix removes."""
    pool = [_wire_turn("A_t0", "sessA"), _wire_turn("B_t0", "sessB"),
            _wire_turn("B_t1", "sessB")]
    added = {"sessA": [_wire_turn("A_t1", "sessA")]}
    merged = reinjection_merge_order(pool, added, seed_order=["sessA"],
                                     max_chunks_per_session=3)
    assert [h["id"] for h in merged] == ["A_t0", "A_t1", "B_t0", "B_t1"]
    # additive, never evicting: every base hit survives, in order
    assert sorted(h["id"] for h in merged) == [
        "A_t0", "A_t1", "B_t0", "B_t1"]


def test_one_session_id_resolves_to_one_key_whichever_spelling_11382():
    """#11382 red-first: ONE session id, ONE key — whichever spelling and
    whichever whitespace it arrives with.

    The two halves of the source-session arm read a session identity from
    two DIFFERENTLY-PRODUCED strings: the seed key comes from the pool HIT
    (stripped at the wire producer, ``_safe_session_tag``), the group key
    from the GRAPH (``coalesce(p.session_id, s.id)``, never stripped at the
    write door). Before this fix the snake leg was raw and the camel leg was
    stripped, so a padded-but-REAL id — a shape ``tortoise/ids.py``
    explicitly does NOT refuse (PADDED-but-REAL, #9599) — resolved to
    ``"  s1  "`` through one spelling and ``"s1"`` through the other.
    MEASURED at ``origin/main``:

        session_key_of({"session_id": "s1"})  -> "s1"
        session_key_of({"sessionId":  "s1"})  -> "s1"
        session_key_of({"session_id": "  s1  "}) -> "  s1  "   (raw)
        session_key_of({"sessionId":  "  s1  "}) -> "s1"       (stripped)
    """
    spellings = [
        {"id": "p1", "session_id": "s1"},
        {"id": "p1", "session_id": "  s1  "},
        {"id": "p1", "sessionId": "s1"},
        {"id": "p1", "sessionId": "  s1  "},
    ]
    assert {session_key_of(h) for h in spellings} == {"s1"}

    # ⛔ NOT over-normalised — the other direction of the same test. Stripping
    # must not FUSE distinct sessions into one bucket, and must not blank a
    # real id into the phantom namespace:
    assert session_key_of({"id": "p", "session_id": "s2"}) == "s2"
    assert session_key_of({"id": "p", "sessionId": "s2"}) == "s2"
    assert session_key_of({"id": "p", "session_id": "s 1"}) == "s 1"
    # a whitespace-only value is still NOT an identity (it fails closed to the
    # per-hit bucket on both spellings, as #10540 pinned on the camel leg)
    for junk in ("  ", "\t\n"):
        assert session_key_of({"id": "p", "session_id": junk}) == (
            "idx:point:p")
        assert session_key_of({"id": "p", "sessionId": junk}) == (
            "idx:point:p")
    # ... and the per-session cap therefore treats the four spellings as ONE
    # session. Measured: pre-fix the two buckets survived one chunk each —
    # ``['c0', 'c3']`` (raw ``'  s1  '`` and stripped ``'s1'``) — and after
    # the fix ONE bucket survives at cap=1.
    pool = [_chunk(f"c{i}", "  s1  ") for i in range(3)] + [
        _chunk("c3", "s1")]
    assert [h["id"] for h in dedup_pool(pool, max_chunks_per_session=1)] == [
        "c0"]


# ── the group side: the fetch's key is the SAME key as the seed's ───────
def test_padded_session_id_is_not_charged_then_dropped_11382():
    """#11382, the arm-level direction: a padded id must be SEEDED, FETCHED
    and SPLICED — not charged and dropped.

    The fetch groups by ``coalesce(p.session_id, s.id)``, the RAW stored
    spelling, while the SEED key on the wire side is stripped. MEASURED at
    ``origin/main`` (a577d0e25c) with one base hit and TWO fetched turns
    ``('t1', '  s1  ', 0)`` / ``('t2', '  s1  ', 1)``, and the two spellings
    land in DIFFERENT places — which is the whole point, and is why the
    drop arm is spelled as the CAMEL pool hit:

        pool hit {session_id: '  s1  '}   (snake — the RAW side of the pair)
            by_session -> ['  s1  ']   seeds -> ['  s1  ']
            merged     -> ['p1','t1','t2']   NOTHING dropped: both sides
                                            were raw, so they agreed on a
                                            RAW key
        pool hit {sessionId: '  s1  '}    (camel — the STRIPPED side)
            by_session -> ['  s1  ']   seeds -> ['s1']
            merged     -> ['p1']        sessions=0, injected=0
                                        with fetch['total'] == 2 CHARGED

    The group key is RAW under BOTH spellings (one ``coalesce``, one stored
    id); what varies is the POOL HIT / seed side, and that is the divergence:
    one session, two keys, and only the raw one can ever equal the group.

    So the pre-fix defect is not "a padded id is always dropped": it is that
    the SAME session resolves to two keys and only one of them can ever match
    the graph — the snake spelling gets the raw key, the camel (and any wire
    caller) the stripped one. Both spellings are asserted below, and each one
    fails on its own at ``origin/main`` (snake: ``['  s1  '] != ['s1']`` on
    the group key; camel: the dropped merge). ⚠️ This is also why the fix is
    NOT "normalise the seed" alone (what #10540 did for the camel leg): that
    one-sided strip is what CAUSES the drop, so the group key is normalised
    too.
    """
    rows = [("t1", "  s1  ", 0), ("t2", "  s1  ", 1)]
    fetch = source_session_chunk_pass(
        _FakeProj(rows=rows), ["p1"], pool_ids={"p1"})
    assert list(fetch["by_session"]) == ["s1"]   # ONE normalised group key
    assert [r["session_id"] for r in fetch["by_session"]["s1"]] == [
        "s1", "s1"]
    assert fetch["total"] == 2

    for spell in ("session_id", "sessionId"):
        padded_pool = [{"id": "p1", spell: "  s1  ",
                        "point_kind": TURN_POINT_KIND}]
        seeds = seeded_sessions(padded_pool, window=5, limit=5)
        assert [s.session_id for s in seeds] == ["s1"], spell

        merged = reinjection_merge_order(
            padded_pool, fetch["by_session"],
            seed_order=[s.session_id for s in seeds],
            max_chunks_per_session=10)
        # both turns spliced: charged AND delivered, whichever spelling
        assert [h["id"] for h in merged] == ["p1", "t1", "t2"], spell

    # ⛔ NOT over-widened, and this arm DISCRIMINATES against the tempting
    # "just normalise harder" (#11382 review): dropping EVERY space would fuse
    # `'  s 1  '` (pool hit) with `'s1'` (group key), so the group would be
    # ANCHORED after that base hit. Under `strip` they are two identities and
    # the unanchored group falls to the defensive TAIL — which is the only
    # place the difference is visible, hence TWO base hits (with one, an
    # anchored splice and a tail append produce the same list).
    spaced = {"id": "p1", "session_id": "  s 1  ",
              "point_kind": TURN_POINT_KIND}
    assert session_key_of(spaced) == "s 1"
    tail = reinjection_merge_order(
        [spaced, {"id": "p2", "session_id": "s2",
                  "point_kind": TURN_POINT_KIND}],
        {"s1": [{"id": "x1", "session_id": "s1"}]},
        seed_order=["s1"], max_chunks_per_session=10)
    assert [h["id"] for h in tail] == ["p1", "p2", "x1"]
    # ... and a group whose session is not in ``seed_order`` at all is not
    # spliced into someone else's block (groups are built over ``order``, so
    # an inconsistent caller/group pair contributes nothing — pre-existing
    # behaviour, pinned so the normalisation cannot widen it)
    other = reinjection_merge_order(
        [{"id": "p1", "session_id": "  s1  ", "point_kind": TURN_POINT_KIND},
         {"id": "p2", "session_id": "s2", "point_kind": TURN_POINT_KIND}],
        {"s2": [{"id": "x2", "session_id": "s2"}]},
        seed_order=["s1"], max_chunks_per_session=10)
    assert [h["id"] for h in other] == ["p1", "p2"]


def test_a_raw_custom_session_key_cannot_split_the_seed_from_the_merge_11382():
    """#11382, the CUSTOM-KEY arm: a caller-supplied ``session_key`` that
    returns the RAW stored id must not desynchronise the two halves of the
    pair.

    ``seeded_sessions`` normalises what it emits; before this PR
    ``reinjection_merge_order`` keyed the pool with the raw custom key, so its
    ``last_rank`` map never matched a normalised seed. MEASURED, three trees,
    same fixture (pool ``['p1'('  s1  '), 'p2'('s2'), 'p3'('s2')]``, group for
    ``s1`` = ``['t1','t2']``, ``seed_order`` from ``seeded_sessions``):

        origin/main                      -> ['p1','p2','p3']
                                           (group DROPPED: the raw seed key
                                           matches neither ``order`` nor
                                           ``last_rank``, so ``groups`` is
                                           empty and the merge short-circuits)
        one-sided (seeds normalised,
          merge raw)                     -> ['p1','p2','p3','t1','t2']
                                           (group in the defensive TAIL —
                                           the shape this test exists for)
        both-sided (this PR)             -> ['p1','t1','p2','p3','t2']
                                           (= the assertion below; the guard
                                           still pushes ``t2`` past its window
                                           cap, which is the guard working,
                                           not the seed/merge split)

    The tail is reader-visible: the reader consumes a PREFIX of the pool, so
    a tail-appended group is context it can lose. No shipped caller supplies
    ``session_key=`` here (this file's ``raw_key`` tests are what pin the
    contract; ``dedup_pool``'s own custom-key path is shipped in
    ``tortoise/ask_lane.py`` etc.), so this pins the GUARANTEE rather than a
    live lane.
    """
    def raw_key(hit: dict) -> str:
        return hit.get("session_id") or ""

    pool = [{"id": "p1", "session_id": "  s1  ", "point_kind": TURN_POINT_KIND},
            {"id": "p2", "session_id": "s2", "point_kind": TURN_POINT_KIND},
            {"id": "p3", "session_id": "s2", "point_kind": TURN_POINT_KIND}]
    seeds = seeded_sessions(pool, window=9, limit=9, session_key=raw_key)
    assert [(s.session_id, s.point_id) for s in seeds] == [
        ("s1", "p1"), ("s2", "p2")]

    added = {"s1": [{"id": "t1", "session_id": "  s1  "},
                    {"id": "t2", "session_id": "  s1  "}]}
    merged = reinjection_merge_order(
        pool, dict(added), seed_order=[s.session_id for s in seeds],
        session_key=raw_key, max_chunks_per_session=9)
    assert [h["id"] for h in merged] == ["p1", "t1", "p2", "p3", "t2"]
    # ... which IS the default key's answer on the same pool, so the custom
    # key is normalised into the SAME namespace rather than a second one. (The
    # guard still keeps s1 to `per_session_cap` in the window — t2 is pushed
    # past it, which is the guard doing its job, not the seed/merge split.)
    default_merged = reinjection_merge_order(
        pool, dict(added), seed_order=["s1", "s2"],
        max_chunks_per_session=9)
    assert [h["id"] for h in default_merged] == [h["id"] for h in merged]
    # the tail shape the raw merge used to produce is NOT what happened: the
    # group starts at s1's own last base rank, not after every other session
    assert [h["id"] for h in merged] != ["p1", "p2", "p3", "t1", "t2"]


def test_a_self_consistent_raw_caller_still_anchors_its_groups_11382():
    """#11382, the RAW-CONSISTENT arm: a caller whose ``session_key``, its
    ``seed_order`` AND its ``added_by_session`` keys are ALL raw and mutually
    consistent keeps working.

    Normalising only the POOL side of the merge is not a partial fix — it is a
    REGRESSION for that caller, and nothing errors. MEASURED by reviewing the
    one-sided version of this change:

        pool ['p1'('  s1  '), 'p2'('s2'), 'p3'('s2')]
        added {'  s1  ': ['t1'], 's2': ['t9']}   seed_order ['  s1  ', 's2']
        one-sided -> ['p1','p2','p3','t1','t9']   (BOTH groups in the tail)
        both-sided -> ['p1','t1','p2','p3','t9']  (each after its own session)

    Reader-visible: the reader consumes a PREFIX of the pool, so a
    tail-appended group is context it can lose.
    """
    def raw_key(hit: dict) -> str:
        return hit.get("session_id") or ""

    pool = [{"id": "p1", "session_id": "  s1  ", "point_kind": TURN_POINT_KIND},
            {"id": "p2", "session_id": "s2", "point_kind": TURN_POINT_KIND},
            {"id": "p3", "session_id": "s2", "point_kind": TURN_POINT_KIND}]
    added = {"  s1  ": [{"id": "t1", "session_id": "  s1  "}],
             "s2": [{"id": "t9", "session_id": "s2"}]}
    merged = reinjection_merge_order(
        pool, dict(added), seed_order=["  s1  ", "s2"], session_key=raw_key,
        max_chunks_per_session=10)
    assert [h["id"] for h in merged] == ["p1", "t1", "p2", "p3", "t9"]


def test_the_merge_passes_phantom_keys_through_untouched_11382():
    """#11382: the merge's normalisation must NOT reach the phantom ``idx:``
    namespace — that payload is an IDENTITY, not a session id.

    ``session_key_of`` interpolates the raw ``id`` into ``idx:point:{id}`` and
    the raw index into ``idx:{index}``, deliberately: a Point id carrying
    boundary whitespace is a DIFFERENT Point, so stripping it fuses two
    distinct hits into one bucket and lets the C5 cap drop a base hit.
    MEASURED with a blind strip applied to the produced key:

        dedup_pool(max_chunks_per_session=1) via the authority -> ['p1','p1 ']
        with the merge's key wrapped blindly                  -> ['p1']

    So this pins that the merge's key IS the authority on the default path —
    including the phantom legs — which is what ``_merge_key`` exists for.
    """
    # two DISTINCT points, no session identity — RAW CHUNKS, because the C5
    # cap is the only thing that can DROP on a fused bucket (`is_raw_chunk`),
    # and a non-chunk fixture would pass this arm for the wrong reason
    # (measured: it did, before this line set the kind).
    a, b = _chunk("p1", ""), _chunk("p1 ", "")
    assert session_key_of(a) == "idx:point:p1"
    assert session_key_of(b) == "idx:point:p1 "
    assert dedup_pool([dict(a), dict(b)], max_chunks_per_session=1) == [a, b]
    # the merge keyed by the authority keeps them apart too. The pool needs a
    # REAL seeded session as well, otherwise the merge short-circuits before
    # the C5 re-cap (`if not groups: return list(pool)`) and the key is never
    # used at all.
    pool = [dict(a), dict(b), {"id": "x1", "session_id": "s1",
                               "point_kind": TURN_POINT_KIND}]
    merged = reinjection_merge_order(
        pool, {"s1": [{"id": "t1", "session_id": "s1"}]}, seed_order=["s1"],
        max_chunks_per_session=1)
    ids = [h["id"] for h in merged]
    assert "p1" in ids and "p1 " in ids, ids   # nothing fused, nothing capped
    # ... and a phantom bucket is not a REAL seed, so it never anchors a group
    assert seeded_sessions([dict(a)], window=5, limit=5) == []


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


# ── READER-WINDOW crowding (#10540) ─────────────────────────────────────
# ``reinjection_merge_order`` is additive on the POOL; the reader consumes a
# PREFIX of it. These pin the prefix cost, which no other census key saw.
def _corpus(per_session=5, n_sessions=8):
    return [{"id": f"s{s}#{i}", "session_id": f"s{s}",
             "point_kind": TURN_POINT_KIND, "is_episodic": True,
             "content": f"[user] turn s{s}#{i}"}
            for s in range(n_sessions) for i in range(per_session)]


def _groups(pool, sizes):
    """The shipped fetch's admission shape: ``sizes`` items per seeded
    session, seeded by that session's FIRST pool hit (in rank order)."""
    order: list[str] = []
    for h in pool:
        if h["session_id"] not in order:
            order.append(h["session_id"])
    out: dict[str, list[dict]] = {}
    for sid, n in zip(order, sizes, strict=False):
        if n:
            out[sid] = [_point(f"inj-{sid}-{k}", sid) for k in range(n)]
    return out, order[:len(sizes)]


def _merge(pool, sizes):
    added, seed_order = _groups(pool, sizes)
    return added, seed_order, reinjection_merge_order(
        pool, added, seed_order=seed_order, guard=True,
        guard_window=5, per_session_cap=2, max_chunks_per_session=3)


def test_reader_window_displacement_names_the_sessions_injection_evicts():
    """The shipped budgets (per-session 3, total 10) over 40 hits / 8
    sessions: the injection's cost lands ENTIRELY on the two lowest-ranked
    sessions, which lose all five hits each. ``injected_merged`` reports a
    healthy 10 either way — that is the blind spot this key closes.

    ⚠️ This fixture is the **full-window** case (40 base hits, window 40),
    where the function's own residual 2 makes ``base_displaced``
    algebraically EQUAL to ``injected_in_window`` — the one case the
    docstring says does not corroborate. It therefore cannot tell the two
    keys apart on its own: an implementation that aliased
    ``base_displaced := injected_in_window`` passes it. The partial-window
    test below is what pins ``base_displaced`` independently.
    """
    pool = _corpus()
    _added, _order, merged = _merge(pool, [3, 3, 3, 1])
    out = reader_window_displacement(pool, merged, window=40)
    assert out["injected_in_window"] == 10
    assert out["base_displaced"] == 10
    assert out["sessions_lost"] == ["s6", "s7"]
    assert (out["sessions_before"], out["sessions_after"]) == (8, 6)
    # NOT a top-5 effect: against the FAIR counterfactual (the base pool the
    # reader would have seen, guard reorder included) the injection costs the
    # top-5 nothing. Measured on the helper itself at window=5 the number is
    # 3, and those 3 are the guard's reorder — which is why this helper is
    # used at the READER's window, not at the loop's.
    fair_base = guard_and_recap_pool(
        pool, guard=True, window=5, per_session_cap=2,
        max_chunks_per_session=3)
    top5_base = {h["id"] for h in fair_base[:5]}
    top5_merged = {h["id"] for h in merged[:5]}
    assert top5_base <= top5_merged, (top5_base - top5_merged)


def test_reader_window_displacement_partial_window_separates_the_two_keys():
    """``base_displaced`` is NOT ``injected_in_window`` — pinned where they
    must diverge, which the full-window fixture above cannot do.

    Same pool and same 10 injected items, read at three windows. Only the
    middle one is partial, and there the two keys disagree (10 injected in
    the window, but just 5 base hits displaced, because the window already
    contains all 40 base hits: 45 - 10 = 35). ``sessions_lost`` narrows with
    it. Full window (50) is the other degenerate end: everything fits, so
    nothing is lost and the counters are not the same number.

    An aliasing implementation (``base_displaced = injected_in_window``)
    passes at 40 and at 50 and FAILS at 45 with ``10 == 5``. That is the only
    mutant these fixtures kill: every window here is ``0``, ``40``, ``45`` or
    ``50`` against a 40-item base pool, so a whole-``base_pool`` read and
    the shipped ``base_pool[:window]`` slice are the SAME list and the
    substitution is a no-op. ``_corpus(per_session=5, n_sessions=12)`` below
    is the fixture that separates them.
    """
    pool = _corpus()
    _added, _order, merged = _merge(pool, [3, 3, 3, 1])
    assert len(pool) == 40 and len(merged) == 50, (len(pool), len(merged))

    partial = reader_window_displacement(pool, merged, window=45)
    assert partial["injected_in_window"] == 10
    assert partial["base_displaced"] == 5, "must not alias injected_in_window"
    assert partial["sessions_lost"] == ["s7"]
    assert (partial["sessions_before"], partial["sessions_after"]) == (8, 7)

    full = reader_window_displacement(pool, merged, window=50)
    assert (full["injected_in_window"], full["base_displaced"]) == (10, 0)
    assert full["sessions_lost"] == []
    assert (full["sessions_before"], full["sessions_after"]) == (8, 8)


def test_reader_window_displacement_ignores_sessions_outside_the_window():
    """The PRODUCTION shape: a base pool LONGER than the reader window.

    Every other fixture in this file has a 40-item base pool read at a window
    of ``0``, ``40``, ``45`` or ``50`` — none smaller than the pool — so
    ``base_pool[:window]`` and ``base_pool`` are the same list there and an
    implementation that read the whole pool cannot be told from the shipped
    slice. This one is the shape the eval lane actually runs: pool 60 (the
    default ``DEFAULT_POOL_SIZE`` is 120) read at the reader's 40
    (``DEFAULT_CONTEXT_ITEM_CAP``).

    Why it matters beyond the fixture: on this shape a whole-``base_pool``
    read puts the sessions whose hits sit at base ranks >= 40 into
    ``sessions_before``, and ``sessions_lost`` then NAMES A SESSION AS LOST
    that the reader never had — on the key the function's own docstring calls
    load-bearing. 12 sessions x 5 hits = 60, so the window holds s0-s7 and
    ``sessions_before`` must be 8, not 12.
    """
    pool = _corpus(per_session=5, n_sessions=12)
    _added, _order, merged = _merge(pool, [3, 3, 3, 1])
    assert (len(pool), len(merged)) == (60, 70), (len(pool), len(merged))
    assert sorted({h["session_id"] for h in pool[:40]}) == \
        [f"s{s}" for s in range(8)]

    out = reader_window_displacement(pool, merged, window=40)
    assert out["sessions_before"] == 8, (
        "the reader's window holds 8 of the 12 sessions — counting the whole "
        "base pool would report 12 and name s8-s11 as lost")
    assert out["injected_in_window"] == 10
    assert out["sessions_lost"] == ["s6", "s7"]
    assert out["sessions_after"] == 6


def test_reader_window_displacement_ignores_the_phantom_namespace():
    """An identity-less hit is keyed ``idx:-1`` (or ``idx:point:{id}`` since
    #3591) by ``session_key_of``, and that key names NO graph session —
    ``retrieval.session_key_of`` documents the whole namespace as phantom and
    ``coverage_loop.coverage_gap`` excludes it from its window census.

    Unfiltered, the phantom lands in ``sessions_before`` (inflating the
    denominator of any derived rate) and, once it is pushed out of the merged
    window, in ``sessions_lost`` — the function's own LOAD-BEARING key, whose
    contract is "a session with no hit in the merged window has no evidence
    the reader can cite". A phantom is the falsest possible entry there: the
    reader could never have cited it. The path is reachable from the eval lane
    (``retrieve.py``'s pool annotator always emits ``lme_session_index``,
    defaulted to ``-1``), so this is not a hypothetical bucket.

    Two injected items push the two phantom hits out of the 8-item window, so
    pre-fix (no filter): ``sessions_lost == ['idx:-1']`` and
    ``sessions_before == 4``; post-fix both assertions below hold. (The
    injected items' own session is a REAL bucket in ``after`` — 4 there is
    the injected session plus the three base ones, not a phantom.)
    """
    from tortoise.retrieval import session_key_of

    pool = [{"id": f"{s}#{i}", "session_id": s}
            for s in ("sA", "sB", "sC") for i in range(2)]
    # the annotator's shape for a hit with no session identity: no
    # ``session_id``, ``lme_session_index`` defaulted to -1
    pool += [{"id": "x1", "lme_session_index": -1},
             {"id": "x2", "lme_session_index": -1}]
    assert {session_key_of(h) for h in pool[-2:]} == {"idx:-1"}
    injected = [{"id": f"inj#{i}", "session_id": "inj"} for i in range(2)]
    merged = injected + pool

    out = reader_window_displacement(pool, merged, window=8)
    assert out["injected_in_window"] == 2  # the push-out really happened
    assert out["sessions_lost"] == [], (
        "idx:-1 names no graph session — it must never be reported LOST")
    assert out["sessions_before"] == 3, (
        "the phantom also inflates the denominator")
    assert out["sessions_after"] == 4  # 3 base sessions + the injected one

    # the other half: a phantom that STAYS inside the window is not a session
    # PRESENT either (``coverage_loop``'s #3591 note — the opposite reading)
    full = reader_window_displacement(pool, merged, window=20)
    assert (full["sessions_before"], full["sessions_after"]) == (3, 4), full
    assert full["sessions_lost"] == []


def test_reader_window_displacement_is_zero_without_injection():
    pool = _corpus()
    out = reader_window_displacement(pool, list(pool), window=40)
    assert out == {"window": 40, "injected_in_window": 0,
                   "base_displaced": 0, "sessions_lost": [],
                   "sessions_before": 8, "sessions_after": 8,
                   "measured": True}


def test_a_rank_below_five_count_is_invariant_to_injection():
    """The metric artifact #10540's probe reports as "35 fall out of the
    top-5": 35 of 40 base hits START below rank 5, so counting base hits at
    final rank >= 5 returns 35 with 0, 10 or 15 injected items. It is not a
    crowding measurement; ``base_displaced`` (above) is."""
    pool = _corpus()
    base_ids = {h["id"] for h in pool}
    for sizes in ([0], [3, 3, 3, 1], [3, 3, 3, 3, 3]):
        _added, _order, merged = _merge(pool, sizes)
        # RANK-based, not count-based: the artifact is that the population
        # below the cut never shrinks, so the number is input-invariant.
        below5 = sum(1 for i, h in enumerate(merged)
                     if i >= 5 and h["id"] in base_ids)
        assert below5 == 35, (sizes, below5)


def test_reader_window_displacement_window_zero_is_a_clean_zero():
    """The ``window < 1`` branch is load-bearing, not cosmetic: the SAME
    list sliced with ``merged[:window]`` reads the tail at a negative
    window and reports ``injected_in_window=10``. Deleting the guard must
    fail this test, so the whole shape is asserted at 0 AND at -1."""
    pool = _corpus()
    _added, _order, merged = _merge(pool, [3, 3, 3, 1])
    zero = {"window": 0, "injected_in_window": 0, "base_displaced": 0,
            "sessions_lost": [], "sessions_before": 0,
            "sessions_after": 0, "measured": True}
    assert reader_window_displacement(pool, merged, window=0) == zero
    # NEGATIVE is the case the guard exists for.
    assert reader_window_displacement(pool, merged, window=-1) == zero


def test_the_c4_arm_comment_does_not_promise_reader_additivity():
    """#10632 review ISSUE 1: the C4 arm's own comment block said
    *"additive, so an injected item can never evict a base item"* — true of
    the pool LIST and false of the READER, which consumes a PREFIX. That is
    the exact sentence the PR names as "why the crowding stayed invisible to
    the census", and it survived verbatim ~215 lines above the census that
    disproves it, in the file this PR modifies.

    Non-vacuity: pre-fix ``"can never evict a base item"`` is present in
    this source, so the first assertion reds; the other two pin that the
    qualification names the reader limb and points at the census.

    Reads the text rather than importing: the claim IS a comment, and
    importing the eval lane's ``retrieve`` would drag its graph deps into
    this hermetic rules file.
    """
    src = (ROOT / "tools" / "longmem_eval" / "retrieve.py").read_text(
        encoding="utf-8")
    assert "can never evict a base item" not in src, (
        "the falsified pool-additivity claim must be QUALIFIED, not repeated")
    flat = " ".join(src.split())
    assert "NOT reader-additivity (#10540)" in flat, (
        "the C4 arm comment must say pool-additivity is not reader-additivity")
    assert "reader_window_displacement" in flat, (
        "the arm comment must point at the census that measures the gap")


def test_reader_window_displacement_docstring_never_claims_a_bound():
    """#10632 review ISSUE 2 / F1: the residual list concluded *"inside the
    window is an UPPER BOUND on the delivered surface"*.

    That is false on every arm, by the mechanism the same residual list
    states two bullets above: ``assemble_context`` admits a SKIP-FILTERED
    subsequence — a hit that carries no claim text, or that a token/byte cap
    refuses, is skipped WITHOUT spending an item slot
    (``tortoise/retrieval.py``: ``for h in pool: if len(selected) >=
    item_bound: break`` … ``continue``), so later hits are admitted past the
    raw prefix. An injected item below the window can therefore be
    DELIVERED while the census never counts it — the under-count, which is
    the direction this key exists for.

    ``tools/longmem_eval/retrieve.py`` re-SELECTS the pool from the WHOLE
    candidate list when the reranker applies (``pool = selected``, ``<=
    top_k``) and C6 ``prefer_latest_order`` reorders the final pool after
    that — both AFTER this census — so with rerank ON the window bounds
    neither side. Both movers must stay named; neither may be replaced by a
    claim that the no-rerank arm is a bound.

    Non-vacuity: pre-fix the docstring says "an UPPER BOUND on the delivered
    surface" in the no-rerank sentence, so the first assertion reds (and the
    under-count reason it now gives is absent). The R6/C6 assertions pin
    what must SURVIVE the correction.
    """
    flat = " ".join((reader_window_displacement.__doc__ or "").split())
    assert "upper bound" not in flat.lower(), (
        "the window count is not a bound on the delivered surface: a "
        "skipped hit frees its slot, so the reader admits later hits past "
        "the raw prefix")
    assert "not a bound on the delivered surface in EITHER direction" in flat, (
        "the residual must say the count is no bound in either direction")
    assert "under-count" in flat.lower(), (
        "the residual must name the UNDER-count the skip mechanism causes — "
        "it is the direction this key exists for")
    assert "rerank" in flat.lower(), (
        "the residual list must name the R6 rerank arm it is scoped away from")
    assert "prefer_latest_order" in flat, (
        "the residual list must name the C6 reorder it is scoped away from")
    assert "not a bound at all" in flat, (
        "the rerank-ON case must state that the window is no bound at all")
    assert "``base_displaced`` and ``sessions_lost``" in flat, (
        "``sessions_lost`` is derived from the SAME two windows as the two "
        "counters, so the rerank-APPLIED residual must name it too — "
        "leaving it out is the over-report this assertion exists to keep out")
    assert "load-bearing key on every arm that does not re-SELECT" in flat, (
        "``sessions_lost`` may not be called load-bearing unconditionally: "
        "on the rerank-APPLIED arm the reranker can deliver a session ranked "
        "below the window while the census names it lost")


def test_the_pool_only_window_is_not_claimed_as_a_hard_ceiling():
    """#10632 review P3: the pool-only arm's comment said the narrowed window
    makes the count ``never under-counts — the cut is below the reader's walk,
    not inside it``.

    That premise is only "the cut is below the WINDOW". ``--top-k`` and
    ``--context-items`` are independent CLI flags (``run.py``), so with
    ``top_k > eff_item_cap`` the truncation cut (``pool[:top_k]``) sits ABOVE
    the census window, ``assemble_context`` walks past ``eff_item_cap``
    whenever a skip frees a slot, and an injected item below the window IS
    delivered without being counted — the under-count the P3 names.

    Non-vacuity: pre-fix the source says "never under-counts" and "the cut is
    below the reader's walk", so the first assertion reds.

    #10632 review ISSUE 2 (P3): the SAME falsified sentence had survived in
    the test that polices it — the pool-only arm's docstring in
    ``tests/test_session_reinjection.py`` said the narrowed count is "never an
    under-count". Pinning ``retrieve.py`` alone let the claim live on in the
    prose of the file whose job is to keep it out, so BOTH files are read.
    """
    src = (ROOT / "tools" / "longmem_eval" / "retrieve.py").read_text(
        encoding="utf-8")
    flat = " ".join(src.split())
    assert "never under-counts" not in flat, (
        "the pool-only window cannot claim to never under-count: the cut is "
        "above the window whenever top_k > eff_item_cap")
    assert "top_k <= eff_item_cap" in flat, (
        "the comment must name the condition the no-under-count claim holds "
        "under")
    assert "walks past ``eff_item_cap``" in flat, (
        "the comment must say WHY it under-counts above that condition: the "
        "reader's walk extends past the window")
    prose = " ".join((ROOT / "tests" / "test_session_reinjection.py")
                     .read_text(encoding="utf-8").split())
    assert "never an under-count" not in prose, (
        "the test that polices the no-under-count claim cannot carry the "
        "falsified sentence itself")
    assert "UNDER-counts whenever ``top_k > eff_item_cap``" in prose, (
        "the pool-only docstring must state the under-count condition the "
        "source states, not only stop over-claiming")


def test_the_degrade_arms_are_not_declared_outside_the_census_window():
    """#10632 review ISSUE 1 (P2), prose half: ``session_reinjection.py``'s
    residual list kept naming R6's DEGRADE path ``pool = pool[:top_k]`` among
    the movers *"not in this count at all"*.

    That was true before ``8ca8528ad``. After it, each truncating site
    declares its cut (``_sr_prefix_cut = len(pool)``) and the census is taken
    with ``window = min(item_cap, cut)`` — so on the degrade arms the window
    IS the prefix the reader receives, and the residual list was denying the
    fix in the same document that states it. A reader of the residual would
    discount ``sessions_lost`` on exactly the arm the fix was written for.

    Non-vacuity: pre-fix the module says "on its degrade path ``pool =
    pool[:top_k]``", so the first assertion reds; it also says "describe
    ``merged[:40]``", which is the widened window the fix replaced, so the
    second reds with it.
    """
    src = (ROOT / "tortoise" / "session_reinjection.py").read_text(
        encoding="utf-8")
    flat = " ".join(src.split())
    assert "on its degrade path ``pool = pool[:top_k]``" not in flat, (
        "the degrade arm's cut is the census window after 8ca8528ad — the "
        "residual list cannot keep naming it as an uncovered pool mover")
    assert "``merged[:40]``" not in flat, (
        "with the window following the cut, the applied arm's window is "
        "`merged[:window]`, never the eff_item_cap 40 the sentence pinned")
    assert "The three TRUNCATING arms are NOT in that class" in flat, (
        "the residual list must say which arms the cut DOES cover, not only "
        "which mover it misses")


def test_the_pool_only_under_count_is_MEASURED_not_merely_asserted_in_prose():
    """The BEHAVIOURAL half of ``test_the_pool_only_window_is_not_claimed_as_a_hard_ceiling``.

    That test proves the falsified sentence is GONE from the comment; this one
    proves the replacement sentence is TRUE — on the pool-only arm with
    ``top_k > eff_item_cap`` the census really does under-count. A comment
    that stops over-claiming and starts asserting the opposite still has to
    be checked against the code, which is this PR's whole thesis.

    Shape: 40 base hits / 8 sessions, 10 injected for a ninth session at the
    TAIL (the shipped lane's own shape), window = the 40-item cap. Two of the
    hits INSIDE the window carry no claim text, so ``#2978`` frees their
    slots; the arm truncates the pool to ``top_k = 50``, so the reader walks
    all 50 candidates and admits 2 injected items from BELOW the window while
    the census — which stops at 40 — counts none of them.

    Passing ``window=50`` (the aligned shape) reports 10, so the assertion
    discriminates on the window/pool MISALIGNMENT, not on delivery alone.
    """
    base = [{"id": f"b{i}", "session_id": f"s{i // 5}",
             "point_kind": TURN_POINT_KIND,
             "content": "" if i in (5, 17) else f"base hit content {i}"}
            for i in range(40)]
    injected = [{"id": f"x{i}", "session_id": "sx",
                 "point_kind": TURN_POINT_KIND,
                 "content": f"injected content {i}"}
                for i in range(10)]
    merged = base + injected
    assert len(merged) == 50

    window = 40                     # eff_item_cap: the reader's item bound
    top_k = 50                      # --top-k, INDEPENDENT of --context-items
    out = reader_window_displacement(base, merged, window=window)
    assert out["injected_in_window"] == 0, (
        "the census counts the RAW prefix, and every injected item sits below "
        "it -- this is the number a lane reads as 'no crowding'")
    aligned = reader_window_displacement(base, merged, window=top_k)
    assert aligned["injected_in_window"] == 10, (
        "with the window ALIGNED to the truncated pool the same items are "
        "counted, so the zero above is the misalignment, not the fixture")

    pool_only_arm = merged[:top_k]  # retrieve.py: pool = pool[:top_k]
    assert len(pool_only_arm) == 50, (
        "the arm truncates to top_k, which is LARGER than the window here -- "
        "so candidates exist below the cut")
    delivered = retrieval.assemble_context(
        pool_only_arm, top_k=top_k, max_context_tokens=8000,
        context_item_cap=window)
    delivered_injected = [h for h in delivered if h["session_id"] == "sx"]
    assert [h["id"] for h in delivered_injected] == ["x0", "x1"], (
        "the two freed slots admit exactly the 2 injected items below the "
        f"window; got {[h['id'] for h in delivered]}")

    assert out["injected_in_window"] == 0 < len(delivered_injected), (
        "\u26d4 the under-count: the census reports 0 injected items in the "
        "window while the reader was delivered 2 of them")


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
    # ⛔ The file that WROTE the deleted turn writer, and the largest narrative
    # about it — the single most likely home for a restatement, and it was
    # outside the population (#10675 review cycle 5, P3). Its absence was
    # justified on the grounds that it would owe the positive half (a `#10000`
    # marker beside every turn-shape seed mention) and that a false RED there
    # blocks on prose. That reason does not reproduce on this tree: MEASURED,
    # its seed mentions carry no sentence matching `_TURN_SHAPE_VOCABULARY`, so
    # it owes no marker, and `_present_tense_writer_claim` over the whole file
    # is None — adding it reds neither half. A population assertion that omits
    # its likeliest member is the shape this tuple exists to prevent.
    "tortoise/hosted_api.py",
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
#: ⛔ TWO MORE PROPERTIES, both measured (#10675 review, P2). Each was a
#: fail-open on the tree's OWN spellings — the guard's job is to red on a
#: restatement of the deleted writer, and it was not doing it:
#: * **BACKTICKS.** ``_seed_demo_graph`` is normally written in backticks here
#:   (``session_reinjection.py``: "(`hosted_api.py` `_seed_demo_graph`)"). A
#:   bare ``_seed_demo_graph|`` alternative followed by ``\s+`` cannot cross the
#:   closing backtick, so `"`_seed_demo_graph` writes a pointKind='event'"`,
#:   the double-backticked form, and `hosted_api._seed_demo_graph` were all
#:   GREEN — while the comment below claimed this spelling was covered.
#: * **TURN-NODE VOCABULARY.** The tail required the literal ``pointKind``,
#:   yet the positive half of this same guard treats ``turn node`` as the
#:   turn-shape claim. "the demo seed writes one turn node per demo turn" was
#:   therefore GREEN. The tail accepts either now.
#: ⚠️ Neither of these is the negation rule: that is ``_NEGATED_CLAIM``, read on
#: the MATCHED SPAN, so the greedy gap below stays greedy on purpose (#10675
#: review round 16).
_PRESENT_TENSE_DEMO_EVENT_WRITER = re.compile(
    # The SUBJECT: the qualified seed noun, the FUNCTION name, or the bare
    # ``the seeder``. `the ask-fixture seeder` does not match — the article has
    # to sit directly on ``seeder`` — and that seeder really does read `event`,
    # so it must not be swept up. Backticks are optional on BOTH sides and a
    # dotted prefix (``hosted_api._seed_demo_graph``) is allowed: that is how
    # this tree writes the identifier (#10675 review, P2).
    r"`{0,2}(?:(?:demo|dashboard|hosted|sample)[^.;:,\n]{0,40}?seed(?:er)?"
    r"|(?:\w+\.)*_seed_demo_graph|the\s+seeder)`{0,2}"
    # The gap matters: this tree writes "the demo seed STILL writes …",
    # "… CONTINUES TO WRITE …", and backticks the identifier
    # (``_seed_demo_graph``) — all of which a bare `subject\\s+verb` misses,
    # measured (round 12, P2: all three were GREEN on a present-tense `event`
    # claim). Backticks only, never a bare `\s*`, so the subject must still be
    # the sentence's subject.
    # …and a possessive with a noun phrase in front of the verb ("the demo
    # seed's turn writer MINTS …"), which the bare form also missed (round 14).
    # ⛔ GREEDY on purpose: the gap must swallow a pre-verb negator ("no
    # longer", "does not") so that ``_NEGATED_CLAIM``, which reads the matched
    # SPAN, can see it (#10675 review round 16).
    r"(?:['’]s)?(?:\s+\w+){0,3}\s+(?:also\s+|still\s+|continues?\s+to\s+)?"
    r"(?:writes?|mints?|creates?)\b"
    # * **NEGATION** — the CORRECTED sentences say the seed "writes NO turn
    #   node at all", and a bare verb match refuses exactly the prose this pin
    #   wants written: measured, "since #10000 the demo seed writes no
    #   `pointKind='event'` node" matched before this lookahead and is GREEN
    #   after it (the `event` kind would otherwise still be a violation).
    r"(?!\s+(?:no|nothing|none|never)\b)[^.\n]{0,160}?"
    r"(?:pointKind|\bturn\s+node\b)"
    r"(?:\s*[=:]\s*[`'\"“]*[a-z_]+)?", re.IGNORECASE)

#: The kind a ``pointKind`` mention names, if it names one — the group read by
#: :func:`_present_tense_writer_claim`. Split out so the KIND rule above is
#: stated once, in code, instead of as a lookahead inside the pattern.
_POINT_KIND = re.compile(
    r"pointKind[`'\"\s]*(?:[=:]\s*|is\s+|of\s+)[`'\"“]*([a-z_]+)",
    re.IGNORECASE)

#: A NEGATED claim about the seed's writing — i.e. the CORRECTED spelling, and
#: the one an author reaches for first. The lookahead inside
#: :data:`_PRESENT_TENSE_DEMO_EVENT_WRITER` only covers a negator AFTER the verb
#: (``writes no …``); a negator BEFORE it (``does not write …``, ``never writes
#: …``, ``no longer writes …``) still matched and red the very sentence this
#: guard asks for — measured (#10675 review P2): all three were RED
#: end-to-end, and the failure message quoted the NEGATION as the violation
#: while telling the lane to "restate it as history", which it already was.
#: Read on the MATCHED SPAN, so the negator may sit on either side of the verb.
#: ⛔ THE NEGATOR MUST GOVERN THE MENTION, not merely share a sentence: the
#: parent pattern's verb→`pointKind` gap is `[^.\n]{0,160}?`, which crosses `,`,
#: `;`, `and`, `but` and `yet`, so a negated verb ANYWHERE in the span excused a
#: match — and because the span already consumed that `pointKind`, the violating
#: clause behind it was never examined. Measured (#10675 review cycle 2, P2):
#: "the demo seed no longer writes turn text, but still writes a
#: `pointKind='event'` node" was GREEN — the HALF-CORRECTION this pin exists
#: for, in the vocabulary its own comment enumerates. So the first alternative
#: walks from the negated verb to the `pointKind` it negates and stops at a
#: CLAUSE boundary. (The negator AFTER the verb needs no alternative here: the
#: parent pattern's own lookahead refuses that match at the source, so an
#: alternative for it was unreachable except on an UNRELATED write verb in the
#: gap — where it excused a real violation. "the demo seed writes a turn, writes
#: no duplicate, and writes a `pointKind='event'` node" was GREEN. Removed
#: (#10675 review cycle 4, P3); the lookahead still covers the after-verb form.)
#: ⛔ A CLAUSE BOUNDARY IS NOT PUNCTUATION, AND IT IS NOT ONLY `,`/`;`: `and`,
#: `but` and `yet` cross a clause exactly as `,` does, and the comma before them
#: is OPTIONAL. Nor is the set closed at the dash: a COLON, an ASCII hyphen and
#: an OPENING PARENTHESIS all start a new clause in this tree's prose, and a gap
#: that let them through kept the half-correction GREEN — measured (#10675 review
#: cycle 5, P2): "… no longer writes turn text to the graph: it still writes a
#: `pointKind='event'` node" and its " - " and "(…)" twins. Every one of them is
#: a clause separator in its CLAUSE use, so every one of them terminates the walk.
#: ⚠️ Residual, stated because the previous line overstated its cost: the same
#: characters also introduce an APPOSITIVE that merely GLOSSES the shape
#: ("no longer writes the turn shape (a `pointKind='event'` node)", "… the turn
#: shape: a `turn node`"), and those are TRUE corrections that still red. The
#: separators cannot be dropped — the half-correction arms depend on them — so
#: this is named rather than hidden (#10675 review cycle 6, P3).
#: ⛔ AND A DASH IS A CLAUSE SEPARATOR TOO, not an odd character: `—`/`–` is
#: this tree's dominant one (it appears throughout the comment block that states
#: this rule), so `[^,;]` alone still crossed it — "… no longer writes turn text
#: to the graph — it still writes a `pointKind='event'` node" was GREEN
#: (#10675 review cycle 4, P2). Both dashes are in the excluded class.
#: ⚠️ The residual is stated precisely, because an earlier wording overstated
#: its cost: the escape is not the only mechanism. A sentence that negates its
#: FIRST clause with the negator the PARENT pattern's own lookahead recognises
#: (`writes no turn text …, and then writes a `pointKind='event'` node`) produces
#: no anchored match at all — the parent never fires — and closing THAT needs the
#: subject re-anchored across clauses. It is not what the gap above is for.
#: ⛔ THE TAIL IS THE PARENT'S TAIL, AND THAT IS PINNED, not asserted: the literal
#: appears in the parent (`pointKind` OR `turn node` — the second form is what
#: its own positive half calls the turn shape) and again in
#: `_TURN_SHAPE_TAIL` below, because the parent is defined first and cannot
#: reference it. `test_the_present_tense_guard_reads_a_NEGATED_writer_claim_as_
#: the_CORRECTION` asserts `_TURN_SHAPE_TAIL in
#: _PRESENT_TENSE_DEMO_EVENT_WRITER.pattern`, so widening the parent's tail
#: without the escape's is a RED rather than a silent return of the false red
#: that cycle 1 filed and cycle 3 nearly reintroduced (#10675 review cycle 4,
#: P3: the comment said "spelled once" while it was spelled twice).
#: ⛔ CONTRASTS ARE CORRECTIONS TOO, and they were RED: "writes the turns to
#: Postgres RATHER THAN a turn node", "INSTEAD OF a `pointKind='event'` node",
#: "NOT TO a `turn node`" and — the most natural English of the four — "NOT A
#: `turn node`" / "NOT INTO a …" / "NOT AS a …" state §9.1/D1 accurately and are
#: what the homes should say, so they are excused. An enumerated list missed
#: `not a`, which is why the preposition after `not` is a CLASS rather than one
#: more alternative (#10675 review cycle 5, P3). ⚠️ The marker must INTRODUCE
#: the shape: the walk below goes from the marker to the shape, so "writes a
#: `pointKind='event'` node RATHER THAN Postgres rows" is still a violation —
#: pinned as an arm rather than left to inspection.
_TURN_SHAPE_TAIL = r"(?:pointKind|\bturn\s+node\b)"
#: The clause connectors, stated ONCE (#10675 review cycle 7, P2: `_CONTINUE`
#: listed `but|rather than|instead of` while the walk excluded eight, so a
#: re-assertion behind `while`/`though`/`yet`/`and`/`although`/`however` stayed
#: GREEN — the two lists drifting apart is the whole `#:`-documents-its-subject
#: hazard this file guards against elsewhere).
_CLAUSE_CONNECTORS = r"but|yet|and|although|while|whereas|however|instead|though"
_NEGATED_CLAUSE_GAP = (
    r"(?:(?!(?:" + _CLAUSE_CONNECTORS + r")\b)"
    # ⛔ A SPACED hyphen is a clause separator; a COMPOUND hyphen is not. `-`
    # therefore stays in the class (so `pre-#10000` — this tree's own spelling
    # for exactly this population — is walkable) and only the ` - ` form is
    # refused (#10675 review cycle 6, P3: excluding bare `-` falsely red
    # "no longer writes the pre-#10000 turn node").
    r"(?!(?<=\s)-(?=\s))[^,;:()!?\u2014\u2013]){0,60}?")
_NEGATED_CLAIM = re.compile(
    r"\b(?:not|never|no\s+longer|no\s+more)\s+(?:also\s+|still\s+|ever\s+)?"
    r"(?:writes?|mints?|creates?)\b" + _NEGATED_CLAUSE_GAP + _TURN_SHAPE_TAIL
    # A contrast marker INTRODUCING the shape ("rather than a turn node").
    # ⛔ `never` is the negative-polarity twin of `not`, and an earlier wideneing
    # dropped it silently: "… never a `turn node`" went GREEN→RED (#10675 review
    # cycle 6, P3).
    + r"|\b(?:rather\s+than|instead\s+of|(?:not|never)\s+(?:to|a|as|into|in|on))\s+"
    + _NEGATED_CLAUSE_GAP + _TURN_SHAPE_TAIL,
    re.IGNORECASE)
#: A clause that RE-ASSERTS the shape after the escape's own span ("not a `turn
#: node` BUT a `pointKind='event'` node"). The escape matches INSIDE the parent's
#: span, which ends at the FIRST shape, so the asserted clause behind it was never
#: examined — widening the contrast class turned four spellings of that construction
#: into a false PASS, the guard's core job (#10675 review cycle 6, P2).
#: ⛔ The continuation is judged the way the parent judges its OWN tail: the
#: shape it finds is read for its KIND, and a negator before the continued verb
#: means the clause DENIES the shape rather than asserting it. Without that, a
#: true sentence ("… no longer writes a `pointKind='event'` node but writes a
#: `pointKind='observation'` node") was a false RED — the class this guard has
#: spent several cycles removing (#10675 review cycle 7, P3).
#: ⛔ The continued clause must ASSERT the shape, so it must carry a WRITE VERB
#: (or elide one, "yet a `pointKind='event'` node"). Without that requirement a
#: merely DESCRIPTIVE clause red a true correction — "… no longer writes a turn
#: node, and every remaining turn node is pre-#10000 history" — which is the
#: homes' own census vocabulary (#10675 review cycle 8, P3).
_WRITE_VERB = r"(?:writes?|mints?|creates?|writing|minting|creating)"
_CONTINUE = re.compile(
    r"\s*(?:,|;|:)?\s*(?:although\s+|however\s*,\s*)?"
    r"(?:(?:" + _CLAUSE_CONNECTORS + r")\b|rather\s+than\b|instead\s+of\b)"
    r"(?:[^.\n]{0,40}?" + _WRITE_VERB + r"\b[^.\n]{0,40}?"
    r"|\s*(?:(?:to|in|into|as|on|of)\s+)?a\s+`{0,2})"
    + _TURN_SHAPE_TAIL, re.IGNORECASE)
#: ⛔ THE NEGATOR MUST GOVERN THE WRITE VERB — the file's own rule (see the
#: `_NEGATED_CLAIM` block), applied here too. A bare `not|never` anywhere in the
#: clause excused the re-assertion ("but not quite: it still writes a
#: `pointKind='event'` node" was GREEN), and the class now matches the parent's
#: own vocabulary rather than a subset (#10675 review cycle 8, P2 and P3).
#: ⚠️ RESIDUALS, measured and named rather than chased — this guard has had eight
#: review cycles and every one found an English edge, so the limits are recorded
#: here instead of being re-derived: (a) a continued clause that asserts the shape
#: through a NON-write verb ("does not skip the `pointKind='event'` node") is not
#: recognised, because the continuation is required to carry a write verb; (b) a
#: negator separated from its verb by a noun ("no code path writes …") is not
#: matched, because the negator must be ADJACENT to the verb — widening it to a
#: window is what let "but not quite: it still writes …" through. Both are
#: fail-closed (a false RED or a missed excuse), and both are contrived prose no
#: home in this tree writes. The guard is a documentation pin over 8 files with no
#: product effect; the product change it sits beside is verified separately.
_NEGATOR_IN_CLAUSE = re.compile(
    r"\b(?:not|never|no\s+longer|no\s+more|nothing|none|no)\s+"
    r"(?:also\s+|still\s+|ever\s+)?" + _WRITE_VERB + r"\b", re.IGNORECASE)


def _present_tense_writer_claim(flat: str) -> str | None:
    """The forbidden present-tense claim in ``flat``, or ``None``.

    A match is a violation unless it names a kind that is NOT ``event``: the
    seeder still writes ``hypothesis``/``observation``/``statement``/``decision``
    points, so a true sentence about those must not red (#10675 review round 10,
    P2).
    """
    for hit in _PRESENT_TENSE_DEMO_EVENT_WRITER.finditer(flat):
        # ⛔ ``the seeder`` is only THIS seeder when no other one was just named.
        # `tools/ask_spotcheck.py`'s fixture seeder really does write the `event`
        # kind, and a sentence introducing it ("A demo-only fixture: the seeder
        # writes …") is TRUE — it must not be attributed to the demo seed
        # (#10675 review round 13, P3).
        before = flat[max(0, hit.start() - 40):hit.start()]
        if _OTHER_SEEDER_CONTEXT.search(before):
            continue
        # ⛔ A NEGATED match is the CORRECTION, not the claim (#10675 review
        # P2): "since #10000 the demo seed does NOT write …" states exactly
        # what this guard wants stated, and reding it tells the lane to restate
        # a sentence that is already right.
        if _NEGATED_CLAIM.search(hit.group()):
            # ⛔ …unless the clause AFTER the span re-asserts a shape, in which
            # case the sentence is the half-correction, not the correction. The
            # continued clause is read the way the parent reads its own tail: a
            # NON-`event` kind is a TRUE statement, and a negator in that clause
            # means it DENIES the shape rather than asserting it.
            window = flat[hit.end():hit.end() + 80]
            cont = _CONTINUE.search(window)
            if cont:
                # Read the KIND a short way PAST the shape token, exactly as the
                # parent does for its own tail — the match stops at `pointKind`,
                # so the `='observation'` value would otherwise be invisible.
                tail = window[cont.start():]
                kinds = [k.group(1).lower() for k in _POINT_KIND.finditer(tail)]
                # ⛔ ANY `event` kind is the violation — reading only the FIRST
                # let "writes a `pointKind='observation'` node and a
                # `pointKind='event'` node" through (#10675 review cycle 8, P3).
                if kinds and "event" not in kinds:
                    continue
                if _NEGATOR_IN_CLAUSE.search(tail):
                    continue
                return hit.group()
            continue
        # The kind may be named just PAST the verb's match ("… with a
        # ``pointKind`` of ``observation``"), so read a short context, not the
        # matched span alone (#10675 review round 14, P2).
        kind = _POINT_KIND.search(flat[hit.start():hit.end() + 40])
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
#:
#: ⛔ It ends the guard's BODY, not its ``def`` line. Ending at the ``def``
#: prefix excised the header block only and returned the guard's own ~3.3 kB
#: body — docstring, assertion messages and all — to the scanned population,
#: which is the same self-grading the header excision exists to prevent, moved
#: one block down: measured, an ordinary comment added inside the guard's body
#: (``the demo seed writes `pointKind='event'` for each turn``) flipped the pin
#: to RED on its own file (#10675 review P3).
#:
#: ⛔ Spelled as a CONCATENATION for the same reason as ``_GUARD_BLOCK``: the
#: check below requires exactly ONE occurrence, and a self-matching literal
#: here would split at this assignment.
_GUARD_END = "# ── " + "END of the guard's machinery"


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
        assert rest.count(_GUARD_END) == 1, (
            f"the guard's machinery end marker {_GUARD_END!r} must occur "
            f"exactly once after the guard block in `{rel}`; it occurs "
            f"{rest.count(_GUARD_END)} time(s). Without exactly one, the "
            "excision either swallows every test below the guard (which is "
            "where a later lane appends) or returns the guard's own body to "
            "the population, letting this pin grade its own commentary")
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

#: What makes a seed mention a statement about the TURN SHAPE — the only
#: kind that owes a `#10000` history marker. Deliberately narrow: the
#: `event` kind, the `turn node` the seeder stopped writing, or the
#: `is_episodic` flag that excludes the legacy ones.
#: ⛔ ONE paragraph per `#:` block: `#:` documents the symbol that FOLLOWS it,
#: and a second paragraph with no blank line between them would attach to the
#: same symbol. Measured (#10675 review, P3): this paragraph and the one below
#: were run together, so the TURN SHAPE rule shipped as the docstring of
#: `_OTHER_SEEDER_CONTEXT` — the symbol that answers "is this somebody ELSE's
#: seeder?" — which is precisely the conflation these 300 lines exist to
#: prevent. `test_each_guard_regex_documents_its_own_subject` keeps them apart.
_TURN_SHAPE_VOCABULARY = re.compile(
    r"pointKind\s*[=:]\s*[`'\"“]*event\b|\bturn node\b|\bis_episodic\b",
    re.IGNORECASE)

#: A subject introduced as somebody ELSE's seeder, so a `the seeder writes
#: …` immediately after it is not the demo seed's claim.
_OTHER_SEEDER_CONTEXT = re.compile(
    r"(?:ask[-_ ]?fixture|ask_spotcheck|onboarding)\W*$",
    re.IGNORECASE)



def _sentence_at(text: str, pos: int) -> str:
    """The sentence of ``text`` that contains ``pos``.

    Sentence, not window: a guard that decides "is this mention about the turn
    shape" on a character radius answers differently depending on where the
    sentence sits in the file (#10675 review round 14, P2).
    """
    cut = text.rfind(". ", 0, pos)
    start = 0 if cut == -1 else cut + 2
    end = text.find(". ", pos)
    return text[start:len(text) if end == -1 else end + 1]

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
            # ⛔ Only a mention ABOUT the turn shape owes the marker, and "about"
            # is decided on the mention's OWN SENTENCE: judged on the ±window it
            # depended on where in the file the sentence was typed — measured
            # (#10675 review round 14, P2), the same TRUE sentence was GREEN at
            # one insertion point and RED at another because a NEIGHBOUR's
            # `pointKind='event'` fell inside the radius.
            sentence = _sentence_at(flat, _hit.start())
            if not _TURN_SHAPE_VOCABULARY.search(sentence):
                continue
            assert "#10000" in window, (
                f"`{rel}` names the demo/dashboard seed without naming #10000 "
                "beside it, so the reader cannot tell the seed's `event` nodes "
                "are a LEGACY population the conjunct is retained for. Got: "
                f"...{window}...")


def test_the_present_tense_guard_reads_a_NEGATED_writer_claim_as_the_CORRECTION():
    """#10675 review P2: the guard red the sentence it exists to encourage.

    The corrected prose says the seed does NOT write the turn shape, and English
    offers several ways to spell that. Only the negator-AFTER-the-verb form
    ("writes no …") was excluded by the lookahead inside the pattern; every
    negator-BEFORE-the-verb form matched, so a TRUE correction produced a RED
    whose message quoted the negation as the violation.

    FAILS IF: any of the standard negated spellings reds again, or the guard
    stops matching the un-negated claim (which would make the arms above
    vacuous).
    """
    # ⛔ THE ESCAPE AND THE PARENT MUST AGREE ON THE TAIL, and a comment cannot
    # assert that: the literal is spelled in both places (the parent is defined
    # first and cannot reference a constant defined below it), so this line is
    # what makes the sharing true rather than claimed. Widening the parent's tail
    # without the escape's is the cycle-3 false red returning silently (#10675
    # review cycle 4, P3).
    assert _TURN_SHAPE_TAIL in _PRESENT_TENSE_DEMO_EVENT_WRITER.pattern, (
        "the parent guards a tail the negation escape does not know about, so "
        "a CORRECTED sentence using it is a false RED: "
        f"{_TURN_SHAPE_TAIL!r} not in the parent pattern")
    corrections = (
        "since #10000 the demo seed writes no `pointKind='event'` node",
        "since #10000 the demo seed does not write `pointKind='event'` nodes",
        "since #10000 the demo seed never writes `pointKind='event'` nodes",
        "since #10000 the demo seed no longer writes `pointKind='event'` nodes",
        # ⛔ THE OTHER TAIL: the parent fires on `turn node` as well as on
        # `pointKind`, so an escape that only reached `pointKind` red these three
        # — the same false red cycle 1 filed, reintroduced by the interaction of
        # two fixes. Measured (cycle 3): all three RED before `_TURN_SHAPE_TAIL`
        # was shared, GREEN after.
        "since #10000 the demo seed writes no turn node at all",
        "since #10000 the demo seed no longer writes a turn node",
        "since #10000 the demo seed does not write a turn node per turn",
        # ⛔ CONTRASTS introduce the shape rather than negating the verb, and
        # they are accurate §9.1/D1 prose in the homes' own vocabulary. All
        # three RED before the contrast alternative was added (#10675 review
        # cycle 4, P3).
        "the demo seed writes the turns to Postgres rather than a turn node",
        "since #10000 the demo seed writes its turns to Postgres instead of a "
        "`pointKind='event'` node",
        "the demo seed writes turn text to Postgres, not to a `turn node`",
        # ⛔ `not a` / `not into` / `not as` — the most natural English contrast,
        # which an enumerated preposition list missed (#10675 review cycle 5, P3).
        "the demo seed writes turn text to Postgres, not a `turn node`",
        "the demo seed writes turn text to Postgres, not into a "
        "`pointKind='event'` node",
        "the demo seed writes turn text to Postgres, not as a `turn node`",
        # ⛔ `never a` is `not a`'s negative-polarity twin, dropped silently by
        # an earlier widening (#10675 review cycle 6, P3).
        "the demo seed writes its turns to Postgres, never a `turn node`",
        "the demo seed writes its turns to Postgres, never a "
        "`pointKind='event'` node",
        # ⛔ A COMPOUND hyphen is not a clause separator: removing bare `-` from
        # the walk falsely red these, and `pre-#10000` is this tree's own
        # spelling for exactly this population (#10675 review cycle 6, P3).
        "the demo seed no longer writes its pre-#10000 turn node",
        "the demo seed does not write the pre-#10000 `pointKind='event'` node",
        # ⛔ The continued clause is judged the way the parent judges its own
        # tail: another KIND is a true statement, and a negator DENIES the shape
        # (#10675 review cycle 7, P3: both were false REDs).
        "since #10000 the demo seed no longer writes a `pointKind='event'` node "
        "but writes a `pointKind='observation'` node",
        "the demo seed no longer writes a `pointKind='event'` node, but it "
        "never writes a `turn node` either",
        # ⛔ A merely DESCRIPTIVE continued clause is not a re-assertion, and it
        # is the homes' own census vocabulary (#10675 review cycle 8, P3).
        "the demo seed no longer writes a turn node, and every remaining turn "
        "node is pre-#10000 history",
        "the demo seed no longer writes a turn node, and the nine `pointKind` "
        "nodes are pre-#10000 history",
        "the demo seed no longer writes a turn node, and nothing writes a "
        "`pointKind='event'` node",
    )
    for sentence in corrections:
        found = _present_tense_writer_claim(sentence)
        assert found is None, (
            "the guard reds a CORRECTED sentence — the prose it asks for: "
            f"{sentence!r} → {found!r}")
    # ⛔ NON-VACUITY: the same subject, verb and kind WITHOUT a negator is the
    # violation. Without this arm the four above would pass on a pattern that
    # simply stopped matching anything.
    positive = "the demo seed writes a `pointKind='event'` node per turn"
    assert _present_tense_writer_claim(positive) is not None, (
        "the guard stopped matching the present-tense claim altogether, so "
        f"its negation arms prove nothing: {positive!r}")
    # ⛔ AND THE ESCAPE MUST NOT SWALLOW A LATER CLAUSE (#10675 review cycle 2,
    # P2): the verb→`pointKind` gap crosses `,` `;` `but` `yet`, so a negator
    # anywhere in the span used to excuse a violating clause behind it — which
    # is the HALF-CORRECTION (text fixed, node still asserted as current) that
    # this pin exists for. Each sentence below negates one clause and states
    # the shape in the next; all three must be the violation.
    compound = (
        "since #10000 the demo seed no longer writes turn text to the graph, "
        "but still writes a `pointKind='event'` node per turn",
        "the demo seed does not write turn text into the graph, but it writes "
        "a `pointKind='event'` node per turn (#10000)",
        "since #10000 the demo seed no longer writes `[role]`-tagged text into "
        "the graph; it STILL writes a `pointKind='event'` node per turn",
        # ⛔ NO COMMA BEFORE THE CONNECTOR — the comma is optional in English, so
        # a gap that stopped only at `,`/`;` passed this one (#10675 review
        # cycle 3, P2, GREEN end-to-end) while catching its comma'd twin above.
        "since #10000 the demo seed no longer writes turn text to the graph but "
        "still writes a `pointKind='event'` node per turn",
        "since #10000 the demo seed no longer writes turn text to the graph and "
        "instead writes a `pointKind='event'` node per turn",
        # The same crossing, in the OTHER tail vocabulary — that both tails are
        # escaped is what keeps the comma-less arm a property of the rule.
        "since #10000 the demo seed no longer writes turn text to the graph but "
        "still writes a turn node per turn",

        # ⛔ AND A DASH CROSSES A CLAUSE AS WELL — this tree's dominant separator,
        # so a gap that excluded only `,`/`;`/connectors passed it (#10675 review
        # cycle 4, P2).
        "since #10000 the demo seed no longer writes turn text to the graph — "
        "it still writes a `pointKind='event'` node per turn",

        # ⛔ A SECOND, UNRELATED WRITE VERB must not excuse the real clause. The
        # removed `writes no …` alternative was unanchored, so this passed: it
        # found `writes no` in the gap and excused the `pointKind` behind it
        # (#10675 review cycle 4, P3: GREEN before, RED now).
        "the demo seed writes a turn, writes no duplicate, and writes a "
        "`pointKind='event'` node",
        # ⛔ A CONTRAST AFTER the shape does not excuse it: the marker has to
        # INTRODUCE the thing being denied.
        "the demo seed writes a `pointKind='event'` node rather than Postgres "
        "rows",
        # ⛔ COLON / ASCII HYPHEN / PARENTHESIS each start a new clause, and a
        # gap that let them through kept the half-correction GREEN (#10675
        # review cycle 5, P2).
        "since #10000 the demo seed no longer writes turn text to the graph: it "
        "still writes a `pointKind='event'` node per turn",
        "since #10000 the demo seed no longer writes turn text to the graph - "
        "it still writes a `pointKind='event'` node per turn",
        "since #10000 the demo seed no longer writes turn text to the graph (it "
        "still writes a `pointKind='event'` node)",
        # ⛔ `not X BUT Y` — the escape matches inside the parent's span, so the
        # clause ASSERTED after `but` was never examined; widening the contrast
        # class made these false PASSes (#10675 review cycle 6, P2). `_CONTINUE`
        # now re-reads the clause behind the span.
        "the demo seed writes its turn not as a `turn node` but as a "
        "`pointKind='event'` node",
        "the demo seed writes its turn not a `turn node`, but a "
        "`pointKind='event'` node",
        "the demo seed writes its turn not in a `turn node` but in a "
        "`pointKind='event'` node",
        "the demo seed writes its turn not into a `turn node` but into a "
        "`pointKind='event'` node",
        # ⛔ The continuation set was `but|rather than|instead of` while the walk
        # excluded NINE connectors, so a re-assertion behind `while`/`though`/
        # `yet` stayed GREEN — the two lists drifting apart, which is why they
        # now share `_CLAUSE_CONNECTORS` (#10675 review cycle 7, P2).
        "since #10000 the demo seed no longer writes a turn node while still "
        "writing a `pointKind='event'` node",
        "since #10000 the demo seed no longer writes turn text, though it "
        "still writes a `pointKind='event'` node",
        "the demo seed writes its turn not a `turn node`, yet a "
        "`pointKind='event'` node",
        # ⛔ The negator must GOVERN the continued clause's write verb — a bare
        # `not|never` anywhere in it excused the re-assertion (#10675 review
        # cycle 8, P2).
        "the demo seed no longer writes a turn node, but not quite: it still "
        "writes a `pointKind='event'` node",
        "the demo seed no longer writes a turn node, but it never stopped "
        "writing a `pointKind='event'` node",
        # ⛔ ANY `event` kind in the continued clause — reading only the FIRST
        # let an observation-then-event clause through (#10675 review cycle 8, P3).
        "the demo seed no longer writes a turn node but writes a "
        "`pointKind='observation'` node and a `pointKind='event'` node",
    )
    for sentence in compound:
        assert _present_tense_writer_claim(sentence) is not None, (
            "a clause that NEGATES one shape and ASSERTS the turn node in the "
            "next is the half-correction this guard exists for, and the "
            f"negation escape passed it: {sentence!r}")


def test_the_present_tense_guard_reads_the_spellings_the_tree_writes():
    """The present-tense guard must red on the claim and NOT on the correction.

    #10675 review (P2) measured six sentences the guard graded wrongly, three in
    each direction. They live here, INSIDE the excised guard block, so the
    population pin above does not read its own test fixtures as a claim — the
    same self-grading hazard `_GUARD_BLOCK` exists to prevent.

    FAILS IF: the guard goes back to requiring a bare (un-backticked) subject,
    to demanding the literal ``pointKind`` where ``turn node`` says the same
    thing, or to reading a pre-verb negator as an assertion.
    """
    # ── MUST RED: restatements of the deleted writer ──────────────────────
    for claim in (
        # Backticked identifier — how this tree actually writes it.
        "`_seed_demo_graph` writes a pointKind='event' node per turn",
        "``_seed_demo_graph`` writes a pointKind='event' node per turn",
        "`hosted_api._seed_demo_graph` writes pointKind='event' per turn",
        # The vocabulary the positive half of this guard calls the turn shape.
        "the demo seed writes one turn node per demo turn",
        # Baseline: the un-backticked form that already worked.
        "the demo seed writes a pointKind='event' node per turn",
    ):
        assert _present_tense_writer_claim(claim) is not None, (
            "the guard is fail-OPEN on a restatement of the deleted turn "
            f"writer: {claim!r} was accepted (#10675 review, P2)")

    # ── MUST NOT RED: the corrections, and true statements ────────────────
    for okay in (
        # Negation BEFORE the verb — the corrected sentence, and the one the
        # pin is supposed to encourage.
        "the demo seed no longer writes pointKind='event' nodes",
        "the demo seed does not write pointKind='event' nodes",
        "the demo seed never writes pointKind='event' nodes",
        # Negation AFTER the verb (the form that already worked).
        "since #10000 the demo seed writes no pointKind='event' node",
        # TRUE: the seeder still writes these other kinds.
        "the demo seed writes pointKind='hypothesis' points",
        "the demo seed writes ~10 Points to the sample graph",
    ):
        assert _present_tense_writer_claim(okay) is None, (
            "the guard refused a TRUE or CORRECTED sentence, which is a false "
            f"RED on the prose it asks for: {okay!r} (#10675 review, P2)")


# ── END of the guard's machinery — below this line the population resumes


def _doc_block_above(source: str, symbol: str) -> list[str]:
    """The contiguous ``#:`` lines immediately above ``symbol``'s assignment."""
    lines = source.splitlines()
    index = next(i for i, line in enumerate(lines)
                 if re.match(rf"^{re.escape(symbol)}\s*=", line))
    block: list[str] = []
    for line in reversed(lines[:index]):
        if not line.startswith("#:"):
            break
        block.append(line)
    return list(reversed(block))


def test_each_guard_regex_documents_its_own_subject():
    """A ``#:`` block documents the symbol that FOLLOWS it, and only the block
    immediately above it.

    Measured (#10675 review, P3): two ``#:`` paragraphs with no blank line
    between them both attach to the SECOND constant, so the vocabulary rule
    (`event` / the node kind that was deleted / the exclusion flag) shipped as
    the documentation of the OTHER guard — the regex that answers "is this
    somebody ELSE's seeder?". The constant VALUES were right and nothing
    behaved differently; what broke is the one job those paragraphs have,
    telling two guards apart for the next reader.

    FAILS IF: the blocks are merged, swapped, or one is attached to the wrong
    symbol. Deliberately asserted on each block's FIRST line: a merged block
    still CONTAINS the other constant's keywords, so a "keyword is present"
    check passes on exactly the shape that caused this.
    """
    source = (_REPO / "tests/test_session_reinjection_rules.py").read_text(
        encoding="utf-8")
    for symbol, opening in (
        ("_TURN_SHAPE_VOCABULARY",
         "What makes a seed mention a statement about the TURN SHAPE"),
        ("_OTHER_SEEDER_CONTEXT",
         "A subject introduced as somebody ELSE's seeder"),
    ):
        block = _doc_block_above(source, symbol)
        assert block, (
            f"`{symbol}` has no `#:` doc block above it — a paragraph that "
            "documents a guard must sit immediately above that guard, not "
            "above its neighbour")
        first = block[0].removeprefix("#:").strip()
        assert first.startswith(opening), (
            f"the `#:` block above `{symbol}` opens with {first!r}, which is "
            f"another constant's subject — expected it to open with "
            f"{opening!r}. Two `#:` paragraphs with no blank line between "
            "them attach to the SAME symbol, so the two guards' rules end up "
            "documenting each other (#10675 review, P3)")


def test_the_population_scan_excludes_the_guards_own_body():
    """The excision must end this guard's BODY, not its ``def`` line.

    FAILS ON the shape that ended at the ``def`` prefix: that removed the header
    block and returned the guard test's own body — docstring, assertion messages
    and all — to the scanned population, so the pin graded its own commentary.
    Measured there: an ordinary comment added inside the guard's body, naming
    that writer as a present-tense producer of the legacy turn shape, flipped
    the pin to RED on its own file with nothing pointing at the cause
    (#10675 review P3).

    The probe is the guard's OWN docstring, read through ``ast`` rather than
    spelled here: a hard-coded sample would go stale the first time the
    docstring is reworded, and this test would then pass for the wrong reason.
    """
    source = (_REPO / "tests/test_session_reinjection_rules.py").read_text(
        encoding="utf-8")
    guard = next(
        (node for node in ast.parse(source).body
         if isinstance(node, ast.FunctionDef) and node.name ==
         "test_no_home_states_the_deleted_demo_turn_writer_as_current_fact"),
        None)
    assert guard is not None, (
        "this file no longer defines the guard test the excision is written "
        "for, so `_GUARD_END` names nothing and this pin would be vacuous")

    doc = ast.get_docstring(guard) or ""
    lines = [line.strip() for line in doc.splitlines() if line.strip()]
    assert lines, (
        "the guard test lost its docstring; this pin uses its first non-empty "
        "line as body-only text the scanned population must NOT contain")
    probe = lines[0]

    flat = _narrative_text("tests/test_session_reinjection_rules.py")
    assert probe not in flat, (
        "the guard test's own body is back inside the scanned population — "
        f"its docstring line {probe!r} was found. The excision ended at the "
        "guard's `def` prefix instead of the END of its body, so this pin can "
        "grade its own commentary (the round-10/11 self-grading class, returned "
        "one block down)")


def test_a_non_str_custom_key_is_dropped_not_stringified_11382():
    """#11382: the merge's ``_merge_key`` FAILS CLOSED on a non-``str`` key —
    it does not stringify it into a session name.

    Why this needs its own pin: the two directions are behaviourally opposite
    and only one is right. ``_merge_key(5)`` returning ``""`` (not a session,
    so the group is dropped) and returning ``"5"`` (a session literally named
    "5", which can FUSE with a real one) both leave every other test green —
    measured: the one-line ``else str(value)`` mutant passed the entire
    six-file seam batch with only this test red once it existed (before it, the
    batch was green: 849 on the head of the round that found the gap, 851 when
    the pin landed — the batch's size moves with every test added, so the figure
    that matters is "nothing else reds"). So the arm needed a row of its own, exactly like the fail-closed
    snake-id arm one helper over.

    ⚠️ And the docstring's story is the MEASURED one, not the tidy one: with a
    custom key that returns an ``int``, ``seeded_sessions`` drops every seed
    BEFORE any fetch (``[]`` — there is nothing to collapse into a bucket), and
    ``reinjection_merge_order`` drops the groups as well, returning the base
    pool unchanged. Pre-#11382 the two halves DIFFERED: ``seeded_sessions``
    RAISED ``AttributeError`` (it reaches ``.startswith`` through
    ``_is_real_session_id``) while ``reinjection_merge_order`` matched and
    spliced such a key, so the pair never "worked" together — the direction of
    this change is a DROP, not a fusion. ⚠️ ``session_reinjection._merge_key``'s
    own docstring carries the same correction: keep the two copies in step, or
    the PR ships a claim and the correction of it.
    """
    from tortoise.session_reinjection import _merge_key

    assert _merge_key(5) == ""
    assert _merge_key(True) == ""
    assert _merge_key(None) == ""
    # ... a str key is still stripped, so the fail-closed arm cannot be
    # "return the value unchanged" either
    assert _merge_key("  5  ") == "5"
    assert _merge_key("idx:3 ") == "idx:3 "

    def int_key(hit: dict):
        return hit.get("lme_session_index")

    pool = [_chunk("p1", ""), _chunk("p2", "")]
    for rank, hit in enumerate(pool):
        hit["lme_session_index"] = rank
    # seeds are dropped before the fetch — not stringified into "0"/"1"
    assert seeded_sessions(pool, session_key=int_key) == []
    # ... and the merge drops the groups too, returning the base pool's order
    merged = reinjection_merge_order(
        [dict(h) for h in pool],
        {0: [{"id": "t1", "text": "t"}], 1: [{"id": "t2", "text": "t"}]},
        seed_order=[0, 1], session_key=int_key, max_chunks_per_session=9)
    assert [h["id"] for h in merged] == ["p1", "p2"], (
        "a non-str custom key must not acquire an identity: under a "
        "stringifying mutant these groups splice as '0'/'1'")


def test_a_padded_phantom_snake_id_converges_and_goes_unseeded_11382():
    """#11382, the disclosed CONVERGENCE — pinned, because it loses something.

    An id whose STRIPPED form lands inside the ``idx:`` namespace
    (``' idx:3 '`` → ``'idx:3'``) now resolves to the phantom key. Two
    consequences, both measured and both asserted here:

    * the snake spelling now agrees with the camel/wire spelling (which already
      behaved that way at ``origin/main``, because ``_safe_session_tag``
      strips) and with an unrelated ``lme_session_index: 3`` bucket;
    * ⚠️ the snake spelling is consequently NOT SEEDED — at ``8ad61c2ba1``
      ``seeded_sessions([{"id":"p1","session_id":" idx:3 "}])`` returned
      ``[(' idx:3 ', 'p1')]`` and spliced; at this head it returns ``[]``,
      because the strip lands the key on ``_is_real_session_id``'s phantom
      rejection. So a shape the write door should not have produced stops
      working BY ACCIDENT, which is the half the fix's prose previously
      disclosed without a pin.
    """
    # the convergence: three spellings, ONE phantom key
    assert session_key_of({"id": "p", "session_id": " idx:3 "}) == "idx:3"
    assert session_key_of({"id": "p", "sessionId": " idx:3 "}) == "idx:3"
    assert session_key_of({"id": "p", "lme_session_index": 3}) == "idx:3"
    # ... and the loss that follows from it, on both spellings
    for spell in ("session_id", "sessionId"):
        assert seeded_sessions([{"id": "p1", spell: " idx:3 "}]) == [], spell


def test_two_raw_spellings_of_one_session_both_deliver_11382():
    """#11382: the merge's ``groups_in`` UNION is what makes "two raw spellings
    of ONE session arrive as ONE group" true — and it is an arm of its own.

    ``source_session_chunk_pass`` now emits one normalised key per group, so a
    duplicate can only come from a BESPOKE caller that builds ``added_by_session``
    itself. Measured on a copy with the union replaced by assignment
    (``groups_in[sid] = list(rows or [])``): the whole six-file seam batch stays
    GREEN (851 at the head that found the gap; only this test reds once it
    exists), and ``t1`` is silently LOST —

        head   -> ['p1','t1','p2','t9','t2']   (both spellings delivered)
        mutant -> ['p1','t2','p2','t9']        ('t1' dropped, no error)

    which is the round-8 class again (a guard whose regression no test reds), so
    it gets a row here rather than a comment.
    """
    def raw_key(hit: dict):
        return hit.get("session_id") or ""

    pool = [_chunk("p1", "  s1  "), _chunk("p2", "s2")]
    added_by_session = {
        "  s1  ": [{"id": "t1", "session_id": "  s1  ", "text": "t"}],
        "s1": [{"id": "t2", "session_id": "s1", "text": "t"}],
        "s2": [{"id": "t9", "session_id": "s2", "text": "t"}],
    }
    merged = reinjection_merge_order(
        [dict(h) for h in pool], added_by_session,
        seed_order=["  s1  ", "s2"], session_key=raw_key,
        max_chunks_per_session=9)
    ids = [h["id"] for h in merged]
    assert ids == ["p1", "t1", "p2", "t9", "t2"], ids
    assert "t1" in ids and "t2" in ids, (
        "both raw spellings of one session must deliver their chunks: an "
        "assignment instead of a union drops one silently")
