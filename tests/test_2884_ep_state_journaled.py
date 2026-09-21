"""#2884 D3 — EP/dream belief state is journaled and replayed by ``rebuild_all``.

Before this fix the EP and dream write-backs mutated the graph directly and
journaled **nothing**: ``ConfidenceChanged`` was a registered-but-never-emitted
type, and the replay dispatcher listed it in ``_NO_PROJECTION_FOLD`` as an
"audit-only, no graph effect" no-op. A JSONL wipe+rebuild therefore dropped
``posterior_alpha`` / ``posterior_beta`` / ``confidence`` / ``lastDreamedAt``
and reset the dream schedule.

Pinned contracts here:
  (a) a journaled SDK's EP/dream write-back appends ``ConfidenceChanged``
      records carrying the exact committed values;
  (b) ``rebuild_all`` replays them — the rebuilt graph equals the pre-rebuild
      graph on all four properties (the RED guard: with the fold branch
      removed this assertion fails). NOTE: re-adding the type to
      ``_NO_PROJECTION_FOLD`` does NOT red this test — the explicit
      ``elif t == "ConfidenceChanged":`` branch precedes the
      ``elif t in _NO_PROJECTION_FOLD:`` branch, so it still folds; that
      set-membership claim is pinned separately by ``test_c2`` (white-box).
  (c) the pure ``_apply_one`` fold (``fold``) and the graph ``apply`` both
      restore the record (the second RED guard surface);
  (d) with NO event log configured the graph write still happens and no
      journal exists — the legacy lane is untouched (and a ``TortoiseEP``
      built without an emitter does not crash);
  (e) a write that goes through ``_node_cache`` is journaled EXACTLY ONCE at
      flush (not per EP iteration, not twice).

Run (embedded carve-out lane):
  PYTHONPATH=$PWD TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_2884_ep_state_journaled.py -x -q
"""
from __future__ import annotations

import itertools
import json
import os

import pytest

from tortoise.ep import TortoiseEP
from tortoise.projection import fold
from tortoise.sdk import TortoiseSDK

BELIEF_PROPS = ("posterior_alpha", "posterior_beta", "confidence",
                "lastDreamedAt")


@pytest.fixture
def journaled(tmp_path):
    """(db, events_dir, sdk) with the JSONL journal wired."""
    db = os.path.join(str(tmp_path), "ep2884.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _chain(sdk: TortoiseSDK, n: int = 3) -> list[str]:
    """n live claims linked by n-1 IMPL operators (a real EP factor graph)."""
    ids = []
    for i in range(n):
        pid = sdk.create_point("statement", f"claim {i}", dedup=False,
                               status="live")["id"]
        ids.append(pid)
    for a, b in itertools.pairwise(ids):
        sdk.create_operator("IMPL", a, [b])
    return ids


def _state(sdk: TortoiseSDK, ids: list[str]) -> dict[str, dict]:
    """Read the four belief props straight off the graph (no read-path
    coalescing: the test must see exactly what was persisted)."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, n.posterior_alpha, n.posterior_beta, n.confidence, "
        "       n.lastDreamedAt",
        params={"ids": list(ids)},
    ).result_set
    return {r[0]: {"posterior_alpha": r[1], "posterior_beta": r[2],
                   "confidence": r[3], "lastDreamedAt": r[4]}
            for r in rows}


def _records(events) -> list[dict]:
    path = events / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _run_dream(sdk: TortoiseSDK, ids: list[str]) -> None:
    """Drive a real incremental EP+dream pass over the chain."""
    sdk.set_point_baseline(ids[-1], 8.0, 2.0)  # evidence → non-uniform EP
    sdk.dream(dirty_only=True, require_calibration=False)


# ── (a) the write-back is journaled ───────────────────────────────


def test_a_confidence_changed_is_journaled_with_exact_values(journaled):
    _db, events, sdk = journaled
    ids = _chain(sdk)
    _run_dream(sdk, ids)

    recs = [r for r in _records(events) if r["type"] == "ConfidenceChanged"]
    assert recs, "the EP/dream write-back journaled no ConfidenceChanged"

    by_id: dict[str, list[dict]] = {}
    for r in recs:
        by_id.setdefault(r["id"], []).append(r)
    for pid in ids:
        assert pid in by_id, f"{pid} was written but not journaled"

    live = _state(sdk, ids)
    for pid in ids:
        posterior_recs = [r for r in by_id[pid] if "posterior_alpha" in r]
        assert posterior_recs, f"{pid}: no journaled posterior"
        last_posterior = posterior_recs[-1]
        # NB: FalkorDB replies format doubles to 15 significant digits, so the
        # graph read is a truncated view of the full-precision value the
        # journal carries. Compare at 1e-12, not bit-exactly.
        assert last_posterior["posterior_alpha"] == pytest.approx(
            live[pid]["posterior_alpha"], rel=1e-12)
        assert last_posterior["posterior_beta"] == pytest.approx(
            live[pid]["posterior_beta"], rel=1e-12)
        # lastDreamedAt rides the LAST record (the dream write-back).
        assert by_id[pid][-1].get("lastDreamedAt") == live[pid]["lastDreamedAt"]


# ── (b) rebuild replays it — the behavioral RED guard ─────────────


def test_b_rebuild_all_replays_the_belief_state(journaled):
    _db, events, sdk = journaled
    ids = _chain(sdk)
    _run_dream(sdk, ids)

    before = _state(sdk, ids)
    for pid in ids:
        assert before[pid]["posterior_alpha"] is not None, before
        assert before[pid]["confidence"] is not None, before

    sdk._get_proj().rebuild_all(str(events))

    after = _state(sdk, ids)
    for pid in ids:
        assert after[pid] == before[pid], (
            f"{pid}: belief state changed across rebuild_all: "
            f"{before[pid]} -> {after[pid]}")


def test_b2_point_added_snapshot_carries_no_belief_state(journaled):
    """Contrast that proves test_b is load-bearing: the ``PointAdded``
    snapshot is taken at CREATE time, before any EP ran, so it carries none of
    the four properties — the ``ConfidenceChanged`` fold is their ONLY replay
    carrier."""
    _db, events, sdk = journaled
    ids = _chain(sdk)
    _run_dream(sdk, ids)

    added = [r for r in _records(events) if r["type"] == "PointAdded"]
    assert added
    for rec in added:
        snap = rec.get("point") or {}
        for key in BELIEF_PROPS:
            assert key not in snap, (
                f"PointAdded snapshot unexpectedly carries {key} — test_b "
                f"would no longer isolate the ConfidenceChanged fold"
            )


# ── (c) the fold is wired on both dispatchers ─────────────────────


def test_c_fold_applies_on_graph_and_pure_index(journaled):
    _db, _events, sdk = journaled
    ids = _chain(sdk, n=2)
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
        "n.posterior_beta = 1.0, n.confidence = 0.5",
        params={"id": ids[0]},
    )
    ev = {"type": "ConfidenceChanged", "id": ids[0], "confidence": 0.9,
          "posterior_alpha": 9.0, "posterior_beta": 1.0}

    proj.apply(ev)
    assert _state(sdk, [ids[0]])[ids[0]]["posterior_alpha"] == 9.0
    assert _state(sdk, [ids[0]])[ids[0]]["confidence"] == 0.9

    points = fold([
        {"type": "PointAdded", "point": {"id": "p", "content": "x"}},
        {"type": "ConfidenceChanged", "id": "p", "posterior_alpha": 9.0},
    ])
    assert points["p"]["posterior_alpha"] == 9.0


def test_c2_confidence_changed_is_not_an_ignored_type():
    """White-box ratchet: the type must NOT be listed as
    recognized-and-not-folded. ``_NO_PROJECTION_FOLD`` is a CLAIM that a type
    is intentionally not folded — and its own header says never to add a type
    with a real fold branch, because the branch would win while the set
    misleads the next reader. Behaviourally re-adding it is inert (the
    explicit ``elif`` above wins); this test keeps the CLAIM honest."""
    from tortoise.projection import _NO_PROJECTION_FOLD
    assert "ConfidenceChanged" not in _NO_PROJECTION_FOLD


# ── (d) backward compatibility: no event log ──────────────────────


def test_d_no_event_log_writes_graph_and_journals_nothing(tmp_path):
    db = os.path.join(str(tmp_path), "nolog.db")
    sdk = TortoiseSDK(db)  # NO event_log_path — the legacy/embedded lane
    try:
        ids = _chain(sdk)
        _run_dream(sdk, ids)

        state = _state(sdk, ids)
        for pid in ids:
            assert state[pid]["posterior_alpha"] is not None, state
            assert state[pid]["posterior_beta"] is not None, state
            assert state[pid]["confidence"] is not None, state

        # Nothing to journal to, and the EP was built with no emitter.
        assert sdk._get_event_log() is None
        assert sdk._get_ep()._emit is None
    finally:
        sdk.close()


def test_d2_legacy_tortoiseep_without_emitter_runs(journaled):
    """A TortoiseEP constructed the pre-fix way (no ``emit``) must not crash
    and must still commit the graph write."""
    _db, _events, sdk = journaled
    ids = _chain(sdk)
    proj = sdk._get_proj()
    ep = TortoiseEP(proj)  # legacy construction — positional projection only
    assert ep._emit is None

    iterations, converged = ep.run(list(ids), max_hops=2)
    assert iterations >= 1
    assert converged is True

    state = _state(sdk, ids)
    for pid in ids:
        assert state[pid]["posterior_alpha"] is not None, state


# ── (e) the deferred cache path journals exactly once ─────────────


def test_e_cache_path_journaled_exactly_once(journaled):
    _db, _events, sdk = journaled
    ids = _chain(sdk)
    proj = sdk._get_proj()

    emitted: list[tuple] = []
    ep = TortoiseEP(proj, emit=lambda t, **kw: emitted.append((t, kw)))

    # Spy on the cached write path: _update_claim_posterior calls _write_node
    # once per claim per iteration; every one of those lands in _node_cache
    # and is only committed by the single batched _flush_cache.
    cache_writes: list[str] = []
    orig_write = ep._write_node

    def _spy(node_id, alpha, beta):
        cache_writes.append(node_id)
        return orig_write(node_id, alpha, beta)

    ep._write_node = _spy

    iterations, converged = ep.run(list(ids), max_hops=2)
    assert iterations >= 1 and converged is True
    assert len(cache_writes) > len(ids), (
        "expected many per-iteration cache writes — the cache path was not "
        f"exercised ({cache_writes[:5]})")

    changed = [kw for t, kw in emitted if t == "ConfidenceChanged"]
    assert sorted(kw["id"] for kw in changed) == sorted(ids), changed
    # EXACTLY once per claim: not zero (un-journaled) and not once per
    # iteration (cache_writes is strictly larger — proven above).
    seen = [kw["id"] for kw in changed]
    assert len(seen) == len(set(seen)) == len(ids), seen
    for kw in changed:
        assert "posterior_alpha" in kw and "posterior_beta" in kw


# ── FIX-2: one value/id gate shared by BOTH folds ─────────────────
# A corrupt journal line must degrade to a DROPPED value/record, never abort
# rebuild_all AFTER the wipe (the guard's own purpose). The numeric keys must
# be REAL FINITE numbers; a string/bool/NaN posterior persists verbatim
# through FalkorDB's param parse and then brick the next EP run's
# ``float(rows[0][0])`` read.


def test_f2_pure_fold_skips_corrupt_values():
    """The pure `_apply_one` fold drops a corrupt belief value and still
    applies the good keys in the same record."""
    base = {"type": "PointAdded", "point": {"id": "p", "content": "x"}}
    for bad_key, bad_val in [
        ("posterior_alpha", "abc"),
        ("posterior_alpha", True),
        ("posterior_alpha", float("nan")),
        ("confidence", [1, 2]),
        ("posterior_beta", float("inf")),
        ("lastDreamedAt", "\x00bad"),
        ("lastDreamedAt", 123),
    ]:
        pts = fold([base, {"type": "ConfidenceChanged", "id": "p",
                           bad_key: bad_val,
                           "confidence": 0.25 if bad_key != "confidence" else bad_val}])
        assert bad_key not in pts["p"] or pts["p"][bad_key] == 0.25, (
            f"corrupt {bad_key}={bad_val!r} was folded: {pts['p']}")
        if bad_key != "confidence":
            assert pts["p"]["confidence"] == 0.25, pts["p"]
    # None remains VALID — it is the journaled clear.
    pts = fold([base, {"type": "ConfidenceChanged", "id": "p",
                       "posterior_alpha": None}])
    assert pts["p"]["posterior_alpha"] is None
    # A normal finite int/float IS accepted.
    pts = fold([base, {"type": "ConfidenceChanged", "id": "p",
                       "posterior_alpha": 3, "confidence": 0.5}])
    assert pts["p"]["posterior_alpha"] == 3
    assert pts["p"]["confidence"] == 0.5


def test_f2_graph_fold_skips_corrupt_values(journaled):
    """The graph fold (`FalkorProjection.apply` -> `_fold_confidence_changed`)
    drops the same corrupt values and does NOT persist them verbatim."""
    _db, _events, sdk = journaled
    ids = _chain(sdk, n=2)
    proj = sdk._get_proj()
    proj.apply({"type": "ConfidenceChanged", "id": ids[0],
                "posterior_alpha": "abc", "confidence": True,
                "posterior_beta": float("nan"),
                "lastDreamedAt": "2024-01-01T00:00:00+00:00"})
    st = _state(sdk, [ids[0]])[ids[0]]
    assert st["posterior_alpha"] is None, st
    assert st["confidence"] is None, st
    assert st["posterior_beta"] is None, st
    # The one well-formed key in the SAME record still applies.
    assert st["lastDreamedAt"] == "2024-01-01T00:00:00+00:00", st


def test_f2_graph_fold_nul_id_does_not_raise(journaled):
    """A NUL-bearing id must be SKIPPED, not handed to the driver — the
    driver raises `ResponseError` at param parse, and during pass-1b that is
    AFTER the graph was wiped. The real point must be untouched."""
    _db, _events, sdk = journaled
    ids = _chain(sdk, n=2)
    proj = sdk._get_proj()
    proj.apply({"type": "ConfidenceChanged", "id": ids[0] + "\x00",
                "posterior_alpha": 9.0})
    proj.apply({"type": "ConfidenceChanged", "id": "\ud800",
                "posterior_alpha": 9.0})
    assert _state(sdk, [ids[0]])[ids[0]]["posterior_alpha"] != 9.0


# ── FIX-3: the three live belief writers are journaled ────────────


def test_f3_baseline_clear_replays_over_the_prior_posterior(journaled):
    """`set_point_baseline` clears the posteriors LIVE; without a journaled
    clear a rebuild resurrects the earlier EP posterior the live graph no
    longer holds."""
    _db, events, sdk = journaled
    ids = _chain(sdk)
    _run_dream(sdk, ids)                       # journals real posteriors
    sdk.set_point_baseline(ids[0], 5.0, 1.0)   # live clear of posteriors
    assert _state(sdk, [ids[0]])[ids[0]]["posterior_alpha"] is None
    sdk._get_proj().rebuild_all(str(events))
    assert _state(sdk, [ids[0]])[ids[0]]["posterior_alpha"] is None, (
        "rebuild resurrected the pre-baseline posterior")


def test_f3_inheritance_revert_journals_the_clear(journaled):
    """The `_apply_source_inheritance` revert REMOVE must journal the two
    belief keys it cleared, or a rebuild restores the reverted prior."""
    _db, events, sdk = journaled
    pid = sdk.create_point("statement", "inherited claim",
                           extractedFrom="https://s0.example",
                           status="live")["id"]
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier='T0', "
        "s.sourceDate=$sd, s.ingestedAt=$sd",
        params={"url": "https://s0.example",
                "sd": "2024-01-01T00:00:00+00:00"})
    sdk._apply_source_inheritance(recency_decay=1.0)   # write path
    # A live posterior exists (as if EP had run).
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha=7.0, "
        "n.posterior_beta=3.0", params={"id": pid})
    before = len([r for r in _records(events)
                  if r["type"] == "ConfidenceChanged" and r["id"] == pid])
    # Delete the source edge -> no eligible source -> the revert branch.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id})-[r:extractedFrom]->(s:Source) DELETE r",
        params={"id": pid})
    sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
    after = [r for r in _records(events)
             if r["type"] == "ConfidenceChanged" and r["id"] == pid]
    assert len(after) > before, "the inheritance revert journaled no clear"
    assert after[-1].get("posterior_alpha", "missing") is None, after[-1]
    assert _state(sdk, [pid])[pid]["posterior_alpha"] is None


def test_f3_assess_source_decay_is_journaled(journaled):
    """The older-assessment sweep writes the decayed belief LIVE; it must be
    journaled for the rebuild to restore it."""
    _db, events, sdk = journaled
    url = "https://s.example"
    first = sdk.assess_source(url, "alice", 0.4, "first")
    sdk.assess_source(url, "alice", 0.9, "second")
    recs = [r for r in _records(events)
            if r["type"] == "ConfidenceChanged"
            and r["id"] == first["assessment_point_id"]]
    assert recs, "the older-assessment decay journaled no ConfidenceChanged"
    last = recs[-1]
    assert last["confidence"] == 0.5, last
    assert last["posterior_alpha"] == 1.0, last
    assert last["posterior_beta"] == 1.0, last
    sdk._get_proj().rebuild_all(str(events))
    st = _state(sdk, [first["assessment_point_id"]])[
        first["assessment_point_id"]]
    assert st["confidence"] == 0.5, st
    assert st["posterior_alpha"] == 1.0, st


# ── FIX-4: `update_point`'s belief props are folded on replay ─────


def test_f4_update_point_belief_props_folded_on_replay(journaled):
    """`update_point(confidence=...)` writes live and carries the value in
    the `PointRevised` payload; the replay fold must apply it, not drop it."""
    _db, events, sdk = journaled
    ids = _chain(sdk, n=2)
    sdk.update_point(ids[0], confidence=0.123, posterior_alpha=4.5)
    assert _state(sdk, [ids[0]])[ids[0]]["confidence"] == pytest.approx(0.123)

    sdk._get_proj().rebuild_all(str(events))
    st = _state(sdk, [ids[0]])[ids[0]]
    assert st["confidence"] == pytest.approx(0.123), st
    assert st["posterior_alpha"] == pytest.approx(4.5), st

    # The pure fold must agree.
    pts = fold([
        {"type": "PointAdded", "point": {"id": "p", "content": "x"}},
        {"type": "PointRevised", "id": "p", "confidence": 0.5,
         "posterior_alpha": 2.0},
    ])
    assert pts["p"]["confidence"] == 0.5
    assert pts["p"]["posterior_alpha"] == 2.0
    # ... and drop a corrupt value with the shared gate.
    pts = fold([
        {"type": "PointAdded", "point": {"id": "p", "content": "x"}},
        {"type": "PointRevised", "id": "p", "confidence": "abc",
         "posterior_alpha": 2.0},
    ])
    assert "confidence" not in pts["p"], pts["p"]
    assert pts["p"]["posterior_alpha"] == 2.0


# ── FIX-5: the ingest-propagation EP gets a journal emitter ────────


def test_f5_belief_emitter_writes_a_replayable_record(tmp_path):
    """The ingest seam's emitter writes a `ConfidenceChanged` record the
    replay fold reads, and ignores any other type."""
    from tortoise.ingest import _belief_emitter
    from tortoise.log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    emit = _belief_emitter(log)
    emit("ConfidenceChanged", id="p", confidence=0.5,
         posterior_alpha=1.0, posterior_beta=1.0)
    emit("NotBelief", id="q")                       # ignored by this seam
    recs = [json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
            if line.strip()]
    assert len(recs) == 1, recs
    assert recs[0]["type"] == "ConfidenceChanged"
    assert recs[0]["id"] == "p"
    pts = fold([
        {"type": "PointAdded", "point": {"id": "p", "content": "x"}},
        recs[0],
    ])
    assert pts["p"]["confidence"] == 0.5


def test_f5_run_ep_propagation_journals_through_the_threaded_log(journaled):
    """`_run_ep_propagation` must build its `TortoiseEP` WITH the emitter —
    otherwise the ingest-propagation belief writes are unjournaled (the exact
    silent-loss class #2884 fixes)."""
    _db, _events, sdk = journaled
    from tortoise.ingest import _run_ep_propagation
    from tortoise.log import EventLog

    ids = _chain(sdk)
    # Non-empty evidence: `_run_ep_propagation` refuses a fully
    # uncalibrated graph. (update_point also journals a PointRevised.)
    for pid in ids:
        sdk.update_point(pid, confidence=0.6)

    ingest_log = _events / "ingest_propagation.jsonl"
    _run_ep_propagation(sdk._get_proj(), EventLog(ingest_log), label="EP")
    recs = [json.loads(line) for line in ingest_log.read_text().splitlines()
            if line.strip()]
    changed = [r for r in recs if r.get("type") == "ConfidenceChanged"]
    assert changed, "ingest EP propagation journaled nothing"
    assert {r["id"] for r in changed} == set(ids), changed
    # The run-evidence pre-write journals the two posteriors AS A CLEAR; the
    # flush journals confidence + both posteriors. Every record must carry at
    # least one belief key (a record with none is a format defect), and at
    # least one must carry the posteriors.
    for r in changed:
        assert any(k in r for k in
                   ("confidence", "posterior_alpha", "posterior_beta",
                    "lastDreamedAt")), r
    assert any("posterior_alpha" in r and "posterior_beta" in r
               for r in changed), changed
