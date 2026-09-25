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

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import coverage_loop, retrieval
from tortoise.retrieval import (
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
    prove a node is a turn: the hosted demo/dashboard seed writes
    ``pointKind='event'`` with NO ``is_episodic`` (its body IS
    ``[role]``-tagged, so ``is_episodic`` is the conjunct that excludes
    it). The default (turn) kind must carry the shape predicate; the eval's
    chunk A/B must NOT (chunks are role-prefixed windows whose shape is
    already pinned by the kind)."""
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
    key (18), not a subset — ``session_date`` in particular is derived from
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
    assert len(base) == 18 and len(added) == 18  # +status (C6 #2520)
    assert set(base) == set(added)
    assert "status" in base  # the C6 key is present BY NAME
    for key in base:
        if key == "match_source":
            continue
        assert added[key] == base[key], key
    assert added["session_date"] == "2026-01-02"
    assert added["match_source"] == "session"
    assert base["match_source"] == "fts"
