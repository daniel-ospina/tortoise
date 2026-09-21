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
      graph on all four properties (the RED guard: with the fold removed or
      the type back in ``_NO_PROJECTION_FOLD`` this assertion fails);
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
    """White-box companion to the behavioral guard: the type must NOT be
    listed as recognized-and-not-folded, or ``rebuild_all`` silently
    discards it again (the exact pre-fix state)."""
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
