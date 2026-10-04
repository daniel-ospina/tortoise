"""#5048 (recorded from #4666) — a retraction's instant is RECORDED, not re-read.

THE DEFECT THIS PINS
--------------------
`docs/durability-posture.md` declares `updatedAt` **RECORDED** — *"the instant
the transition happened. Minted once by the producer and carried on the
record; a replay that read `_now_iso()` diverged live vs rebuilt"* — the
sentence #5024 added when it fixed exactly this for `:Source.updatedAt`.

The Point-retraction path never honoured it, and burned **three** clock reads
for one transition (measured on this lane before the fix):

    PointRetracted record  ts         = ...03.831715   (producer's emit)
    live node              updatedAt  = ...03.832132   (producer's CAS write)
    rebuilt node           updatedAt  = ...06.611460   (the REPLAY's clock)

So `derived = replay(journal)` was false for the field in the strongest
possible way: the value was not merely wrong, it was *unreconstructible* —
two rebuilds of the same journal disagreed with each other, because the stamp
came from the replay wall-clock rather than from the journal.

THE STRUCTURE, WHICH IS THE ACTUAL DEFECT
-----------------------------------------
The producer already minted the instant and already put it on the record; the
fold then declined to read it. `projection/__init__.py`'s pure fold carried
"``updatedAt`` is the one prop this fold still does not stamp; that divergence
is a #5048 symptom — recorded from #4666" — and the graph arm's `_retract`
called `_now_iso()` at replay time. The fix is the doc's own rule: mint once
at the producer, carry it on the record, replay it verbatim.

WHAT IS DELIBERATELY *NOT* COVERED HERE
---------------------------------------
  * `tags` / `:Tag` / `TAGGED` loss on replay — a DECLARED residual owned by
    #2897, not this change.
  * `ep_dirty` / `ep_dirty_at` — the replay does restore a stale value out of
    the payload, and `consistency.py` says so. It is NOT fixed here on
    purpose: `tests/test_ep_dirty_persist.py` (#1163) makes those props the
    CROSS-PROCESS SOURCE OF TRUTH for EP dirty state, so "deny them on replay"
    and "preserve them across the wipe" are two different products. That is a
    design decision, routed to the owner — not a silent edit here.
  * the `:GraphEvent` / `:GraphEventMeta` event store, which a rebuild empties
    entirely (measured 7 -> 0 rows, `next_seq()` back to 1 — #4664/#4653).
    Its disposition needs a sidecar version that is CONTESTED on main
    (version 3: `event_meta` per #5327, `graph_identity` per #5241), so it is
    routed, not guessed.
  * a phantom record: `retract_point` emits before the CAS, so a concurrent
    double-retract whose CAS loses still appends a record carrying its own
    instant. The live node keeps the winner's stamp while a replay applies
    both records in order. Pre-existing (the old arm re-stamped on every
    record too); an atomic guard is a different change.

Run (embedded carve-out):
  TORTOISE_TEST_CARVE_OUT=1 python -m pytest \\
      tests/test_5048_retract_instant_recorded.py -q
"""
from __future__ import annotations

import json

import pytest

from tortoise.event_store import read_after
from tortoise.projection import _apply_one, fold
from tortoise.sdk import TortoiseSDK


def _records(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _of_type(events_dir, type_) -> list[dict]:
    return [r for r in _records(events_dir) if r.get("type") == type_]


def _node(sdk, pid: str) -> dict:
    """Raw read — a retracted Point is filtered out of every public surface."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN properties(n)", params={"i": pid}
    ).result_set
    assert rows, f"point {pid!r} missing from the graph"
    return rows[0][0]


@pytest.fixture
def retracted(tmp_path):
    """(events_dir, sdk, pid) with one retracted point on the journal."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "retract5048.db"),
                      event_log_path=str(events / "events.jsonl"))
    pid = sdk.create_point("statement", "a claim worth retracting")["id"]
    sdk.retract_point(pid)
    yield events, sdk, pid
    sdk.close()


# ── 1. the producer mints ONE instant ────────────────────────────────────

def test_the_record_and_the_node_carry_the_same_instant(retracted):
    """The producer's own two writes must agree — this is what makes the
    record usable as the replay source.

    FAILS BEFORE: measured record `...03.831715` vs live `...03.832132` — the
    CAS re-read the clock instead of reusing the instant it had just recorded
    (`retract_point` read it once inside `_emit_event` and again for the SET).
    """
    events, sdk, pid = retracted
    recs = _of_type(events, "PointRetracted")
    assert len(recs) == 1, f"expected exactly one retraction record, got {recs}"
    live = _node(sdk, pid)
    assert live["status"] == "retracted", live
    assert live["updatedAt"] == recs[0]["ts"], (
        "the instant on the record and the instant on the node are two "
        "different reads of the wall clock — a replay cannot reproduce the "
        "node from this journal")


# ── 2. the replay reproduces it verbatim ─────────────────────────────────

def test_a_rebuild_reproduces_the_retraction_instant(retracted):
    """THE proof obligation: the rebuilt node is the live node.

    FAILS BEFORE: measured live `...05.520761` vs rebuilt `...06.611460` —
    `_retract` stamped the rebuild's own wall-clock.
    """
    events, sdk, pid = retracted
    before = _node(sdk, pid)["updatedAt"]

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    after = _node(sdk, pid)
    assert after["status"] == "retracted", after
    assert after["updatedAt"] == before, (
        f"replay invented an instant: live={before!r} rebuilt="
        f"{after['updatedAt']!r} — `updatedAt` is RECORDED, so the rebuild "
        "must read it from the journal")


def test_two_rebuilds_of_one_journal_agree(retracted):
    """`derived = replay(journal)` means replay is a FUNCTION of the journal.

    FAILS BEFORE: each rebuild stamped its own clock, so the same journal
    produced a different graph on every replay — the strongest form of the
    defect (not merely divergent from live, but non-deterministic).
    """
    events, sdk, pid = retracted
    stamps = []
    for _ in range(2):
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        stamps.append(_node(sdk, pid)["updatedAt"])
    assert stamps[0] == stamps[1], (
        "two replays of the same journal produced different graphs: "
        f"{stamps}")


# ── 3. the #330 fold parity contract ─────────────────────────────────────

def test_the_pure_fold_agrees_with_the_rebuilt_graph(retracted):
    """`fold()` and `rebuild_all` are the two replay engines and must not
    drift (the #330 contract the neighbouring arms cite).

    FAILS BEFORE: the pure fold did not stamp `updatedAt` at all, so it kept
    the point's ORIGINAL `PointAdded` stamp while the graph held a third
    value — three stamps for one retraction.
    """
    events, sdk, pid = retracted
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _node(sdk, pid)

    folded = fold(_records(events))
    assert pid in folded, "the fold lost the retracted point entirely"
    assert folded[pid]["status"] == "retracted", folded[pid]
    assert folded[pid]["updatedAt"] == rebuilt["updatedAt"], (
        f"fold={folded[pid].get('updatedAt')!r} vs "
        f"rebuild_all={rebuilt['updatedAt']!r}")


# ── 4. the fallback is a fallback, not a second source ───────────────────

def test_a_new_named_parameter_must_not_steal_an_extra_key(tmp_path):
    """``ts`` is an established ``**extra`` key for two OTHER emitters, and
    ``extra`` is what builds the ``:GraphEvent`` payload — which
    ``events_poll`` returns to clients verbatim.

    This test exists because the first version of this change named its new
    parameter ``ts``, which silently REMOVED ``ts`` from the
    ``PointInvalidated`` payload (measured: origin/main payload keys
    ``{corrected_by, expired_at, id, ts, valid_to}``; under the ``ts``-named
    keyword the same set MINUS ``ts``). Nothing reddened: no source or test
    reads ``payload['ts']``, so the only signal was a reviewer reading the
    callers. The parameter is ``recorded_ts`` precisely so it cannot recur.

    The call form below mirrors the real site (``invalidate_point``,
    sdk.py ~8484: ``_emit_event("PointInvalidated", id=..., ts=now, ...)`` with
    NO explicit payload dict) — that is what routes ``ts`` through ``**extra``
    into ``{"id": id, **extra}``. Passing an explicit payload instead would
    bypass the merge and make this test vacuous.
    """
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "emit5048.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        instant = "2026-01-02T03:04:05.000000+00:00"
        sdk._emit_event("PointInvalidated", id="p1", corrected_by="c1",
                        ts=instant)
        by_type = {r.get("type"): r for r in read_after(sdk._get_proj(), 0)}
        assert "PointInvalidated" in by_type, by_type
        payload = by_type["PointInvalidated"]["payload"]
        assert payload.get("ts") == instant, (
            "a caller's `ts` no longer reaches the :GraphEvent payload — a "
            f"named parameter has stolen an extra key: {payload}")
        # ...and the JSONL envelope still carried it, which is exactly why
        # only the PAYLOAD (not the journal) would have revealed the loss.
        recs = _of_type(events, "PointInvalidated")
        assert len(recs) == 1 and recs[0]["ts"] == instant, recs
    finally:
        sdk.close()


def test_a_legacy_record_without_an_instant_still_retracts():
    """A record predating the field (or a hand-written one) must still
    tombstone the point, and must not have an instant INVENTED for it.

    This pins the `ev.get("ts")` guard: the fix reads the record, it does not
    assume one.
    """
    points = {"p1": {"id": "p1", "status": "live", "updatedAt": "T0"}}
    _apply_one(points, {"type": "PointRetracted", "id": "p1"})
    assert points["p1"]["status"] == "retracted", points["p1"]
    assert points["p1"]["updatedAt"] == "T0", (
        "a record with no instant had one invented for it — the fold must "
        "only ever replay what the journal carries")
