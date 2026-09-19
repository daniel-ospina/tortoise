"""#3689 — Operator ``annotator_*`` props are journaled and survive ``rebuild_all``.

``annotate_operator`` writes four epistemic dims (``annotator_bias``,
``annotator_precision``, ``annotator_consistency``, ``annotator_directness``)
onto an Operator Point. Before this fix the live write landed but
``rebuild_all`` erased it **silently** — no error, no warning — for two
independent reasons:

1. ``annotate_operator`` emitted ``OperatorAnnotated`` **positionally**, so
   the record reached ``_emit_event`` with ``point is None and id is None``
   and took the JSONL early-return — it was never journaled at all (the
   graph-event store still got it, which is why no #3299 unknown-type warning
   could ever see it).
2. The journal record that *did* carry the data — ``update_point``'s
   ``PointRevised`` extras — was dropped by the replay fold, which restored
   only ``content`` / ``embedding`` / ``content_hash`` / ``updatedAt``.

Pinned contracts here:
  - **core**: annotate → ``rebuild_all`` → all four dims equal their live
    values (reproduced ``[None, None, None, None]`` before the fix);
  - the JSONL now carries an explicit ``OperatorAnnotated`` record with the
    dims (the write-path half);
  - the dims survive from the ``OperatorAnnotated`` record ALONE (prune the
    ``PointRevised`` lines) — mutation-covers ``rebuild_all``'s pass-1b
    branch;
  - the dims survive from a LEGACY ``PointRevised`` record ALONE (prune the
    ``OperatorAnnotated`` line) — covers journals written before the fix;
  - a partial ``update_point(annotator_bias=…)`` is presence-conditional: it
    must not clobber the sibling dims it did not carry;
  - idempotency across rebuild → rebuild;
  - the pure ``_apply_one`` fold (``fold``) and the ``apply()`` dispatch both
    restore the dims, and both are presence-conditional;
  - the ``:GraphEvent`` payload is UNCHANGED (``id/bias/precision/
    consistency/directness`` — the shipped contract in
    ``docs/event-catalog.md``); the ``annotator_*`` names are JSONL-only;
  - a raw producer journaling the documented short-name payload is accepted.

Run (docker lane):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_test_matrix' \\
      .venv/bin/python -m pytest tests/test_operator_annotator_rebuild.py -q \\
      --import-mode=importlib
"""
from __future__ import annotations

import json
import logging

import pytest

from tortoise.projection import fold
from tortoise.sdk import TortoiseSDK

DIMS = (
    "annotator_bias",
    "annotator_precision",
    "annotator_consistency",
    "annotator_directness",
)


@pytest.fixture
def sup(tmp_path):
    """(db, events_dir, sdk) with the journal wired."""
    db = str(tmp_path / "annot.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _operator(sdk: TortoiseSDK) -> str:
    a = sdk.create_point("statement", "source claim", status="live")["id"]
    b = sdk.create_point("statement", "target claim", status="live")["id"]
    return sdk.create_operator("IMPL", a, [b])["id"]


def _dims(sdk: TortoiseSDK, pid: str) -> dict:
    p = sdk.get_point(pid) or {}
    return {k: p.get(k) for k in DIMS}


def _rebuild(sdk: TortoiseSDK, events) -> None:
    sdk._get_proj().rebuild_all(str(events))


def _journal(events) -> list[dict]:
    path = events / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _rewrite_journal(events, records: list[dict]) -> None:
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


# ═══════════════════════════════════════════════════════════════════════
# Core: the annotation survives a wipe+replay
# ═══════════════════════════════════════════════════════════════════════

def test_annotator_dims_survive_rebuild(sup):
    """The core defect: annotate → rebuild must not erase the dims.

    ``precision`` is pinned to 0.0 — the fold must key on PRESENCE, not
    truthiness, or a legitimate zero dim silently reverts.
    """
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.0, 0.2, 0.1)
    pre = _dims(sdk, op)
    assert pre == {
        "annotator_bias": 0.4,
        "annotator_precision": 0.0,
        "annotator_consistency": 0.2,
        "annotator_directness": 0.1,
    }
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre, "annotator dims were erased by rebuild_all"


def test_rebuild_does_not_leak_deleted_incarnation_dims(sup, tmp_path):
    """A delete→recreate of the same id must not put the OLD incarnation's
    annotation on the new one. ``rebuild_all`` pass-1a hoists every creation
    before pass-1b folds, so the fold needs the survivor gate to match the
    chronological ``apply()`` (#330 parity; review P1).

    The oracle is the LIVE ``apply()`` path (not the pure ``fold()``):
    ``_apply_one`` REPLACES the ``{id: point}`` entry wholesale while
    ``apply()`` MERGEs node props, so ``fold()`` certifies behaviour the live
    path does not exhibit (#3689 review P2)."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    records = _journal(events)
    snap = next(r["point"] for r in records
                if r.get("type") == "OperatorAdded"
                and r["point"]["id"] == op)
    # Terminate incarnation 1, then recreate the SAME id with no dims (raw
    # producer shape: a PointAdded-carried operator snapshot).
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
        "type": "EntityMutated", "initiated_by": "raw-producer",
        "projection_version": 2, "op": "delete", "id": op,
        "label": "Point",
    })
    recreate = dict(snap)
    recreate.pop("embedding", None)
    recreate.pop("content_hash", None)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:01+00:00",
        "type": "PointAdded", "initiated_by": "raw-producer",
        "projection_version": 2, "point": recreate,
    })
    _rewrite_journal(events, records)
    # Live chronological oracle on a SEPARATE graph: replay every record
    # through the real apply() dispatcher, then compare to this graph's
    # rebuild_all().
    oracle = TortoiseSDK(str(tmp_path / "oracle.db"))
    try:
        for r in records:
            oracle._get_proj().apply(r)
        applied = {k: (oracle.get_point(op) or {}).get(k) for k in DIMS}
    finally:
        oracle.close()
    _rebuild(sdk, events)
    rebuilt = _dims(sdk, op)
    assert rebuilt == {k: None for k in DIMS}, (
        "rebuild_all resurrected the deleted incarnation's annotator dims")
    assert rebuilt == applied, (
        "rebuild_all diverged from the live apply() oracle (#330)")


def test_bare_upsert_does_not_suppress_prior_annotation(sup, tmp_path):
    """P2#3: the annotator-dim survivor gate must advance only across a REAL
    hard-delete boundary — not any ``PointAdded``. A duplicate same-id
    creation with no intervening delete MERGEs live (``_upsert_point_props``
    never clears ``annotator_*``), so an annotation at an earlier seq is
    still live-truth and replay must keep it. Differential against a live
    ``apply()`` oracle."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    pre = _dims(sdk, op)
    records = _journal(events)
    snap = next(r["point"] for r in records
                if r.get("type") == "OperatorAdded"
                and r["point"]["id"] == op)
    duplicate = dict(snap)
    duplicate.pop("embedding", None)
    duplicate.pop("content_hash", None)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:02+00:00",
        "type": "PointAdded", "initiated_by": "raw-producer",
        "projection_version": 2, "point": duplicate,
    })
    _rewrite_journal(events, records)
    oracle = TortoiseSDK(str(tmp_path / "oracle.db"))
    try:
        for r in records:
            oracle._get_proj().apply(r)
        applied = {k: (oracle.get_point(op) or {}).get(k) for k in DIMS}
    finally:
        oracle.close()
    _rebuild(sdk, events)
    rebuilt = _dims(sdk, op)
    assert rebuilt == pre, (
        "a bare same-id re-emit over-suppressed the earlier annotation")
    assert rebuilt == applied, (
        "rebuild_all diverged from the live apply() oracle (#330)")


def test_annotator_dims_survive_double_rebuild(sup):
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    pre = _dims(sdk, op)
    _rebuild(sdk, events)
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre


def test_update_entity_annotator_dims_survive_rebuild(sup):
    """#4094 (P1 of the #3689 review): ``update_entity``'s Point branch wrote
    ``annotator_*`` with ``SET n += $p`` and emitted NO journal record, so
    ``rebuild_all`` restored the operator from its creation snapshot and the
    dims were gone — silently. The Point branch must journal the mutation."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.update_entity(op, annotator_bias=0.11, annotator_precision=0.22)
    pre = _dims(sdk, op)
    assert pre == {
        "annotator_bias": 0.11,
        "annotator_precision": 0.22,
        "annotator_consistency": None,
        "annotator_directness": None,
    }
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre, (
        "update_entity annotator dims were erased by rebuild_all")


# ═══════════════════════════════════════════════════════════════════════
# Write path: the explicit record reaches the JSONL journal
# ═══════════════════════════════════════════════════════════════════════

def test_operator_annotated_is_journaled_with_dims(sup):
    """Before the fix there was NO ``OperatorAnnotated`` line in the journal
    at all (the positional emit took ``_emit_event``'s early return)."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    recs = [r for r in _journal(events)
            if r.get("type") == "OperatorAnnotated"]
    assert len(recs) == 1, "OperatorAnnotated was not journaled"
    r = recs[0]
    assert r["id"] == op
    assert r["annotator_bias"] == 0.4
    assert r["annotator_precision"] == 0.3
    assert r["annotator_consistency"] == 0.2
    assert r["annotator_directness"] == 0.1


def test_rebuild_restores_dims_from_operator_annotated_only(sup):
    """Mutation coverage for rebuild_all's pass-1b ``OperatorAnnotated``
    branch: prune every ``PointRevised`` line so the explicit record is the
    ONLY dim carrier. Without that branch the dims are lost (and the rebuild
    logs an unrecognized-type warning)."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    pre = _dims(sdk, op)
    kept = [r for r in _journal(events) if r.get("type") != "PointRevised"]
    assert any(r.get("type") == "OperatorAnnotated" for r in kept)
    _rewrite_journal(events, kept)
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre


def test_rebuild_warns_when_operator_annotated_matches_nothing(sup, caplog):
    """The pre-fix defect was SILENT loss. An annotation whose operator was
    never created by any journaled event must be audible, not dropped in
    silence (parity with the other #3299 fold-miss warnings)."""
    _, events, sdk = sup
    sdk.create_point("statement", "unrelated claim", status="live")
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(),
        "ts": "2026-09-18T00:00:00+00:00",
        "type": "OperatorAnnotated",
        "initiated_by": "raw-producer",
        "projection_version": 2,
        "id": "op-never-created",
        "annotator_bias": 0.4,
        "annotator_precision": 0.3,
        "annotator_consistency": 0.2,
        "annotator_directness": 0.1,
    })
    _rewrite_journal(events, records)
    with caplog.at_level(logging.WARNING):
        _rebuild(sdk, events)
    assert any("OperatorAnnotated fold matched no Point" in m
               for m in caplog.messages), caplog.messages


def test_rebuild_restores_dims_from_legacy_point_revised_only(sup):
    """Journals written before the emit fix carry the dims on ``PointRevised``
    only. Prune the ``OperatorAnnotated`` line: the ``_revise_point`` fold
    extension must still restore them."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    pre = _dims(sdk, op)
    kept = [r for r in _journal(events) if r.get("type") != "OperatorAnnotated"]
    assert any(r.get("type") == "PointRevised" and "annotator_bias" in r
               for r in kept)
    _rewrite_journal(events, kept)
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre


def test_point_revised_short_named_props_are_not_aliased(sup):
    """A prop literally named ``precision``/``bias`` is written VERBATIM by
    ``update_point``; replay must not reinterpret it as an annotator dim —
    that would rename the node prop on rebuild and silently clobber the real
    annotator value (and the retrieval ordering that reads it; #3689 review
    P1).
    """
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.2, 0.3, 0.1)
    sdk.update_point(op, precision=0.9, bias=0.8)
    _rebuild(sdk, events)
    post = sdk.get_point(op)
    assert post["annotator_precision"] == 0.2, (
        "unrelated `precision` prop was aliased onto annotator_precision")
    assert post["annotator_bias"] == 0.4, (
        "unrelated `bias` prop was aliased onto annotator_bias")
    assert post["annotator_consistency"] == 0.3
    assert post["annotator_directness"] == 0.1


def test_fold_point_revised_does_not_alias_short_names():
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "pt1", "content": "claim", "pointKind": ""}},
        {"type": "PointRevised", "id": "pt1",
         "precision": 0.9, "bias": 0.8},
    ])
    p = pts["pt1"]
    assert "annotator_precision" not in p
    assert "annotator_bias" not in p


def test_direct_update_point_annotator_dims_survive_rebuild(sup):
    """A partial annotator write through ``update_point`` must replay
    without clobbering the sibling dims (presence-conditional fold)."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    sdk.update_point(op, annotator_bias=0.9)
    pre = _dims(sdk, op)
    assert pre == {
        "annotator_bias": 0.9,
        "annotator_precision": 0.3,
        "annotator_consistency": 0.2,
        "annotator_directness": 0.1,
    }
    _rebuild(sdk, events)
    assert _dims(sdk, op) == pre, "partial annotator update clobbered siblings"


# ═══════════════════════════════════════════════════════════════════════
# Replay-path parity: the pure fold and apply()
# ═══════════════════════════════════════════════════════════════════════

def test_fold_operator_annotated_restores_dims():
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "op1", "content": "IMPL(a, b)", "pointKind": "",
            "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}},
        {"type": "OperatorAnnotated", "id": "op1",
         "annotator_bias": 0.1, "annotator_precision": 0.2,
         "annotator_consistency": 0.3, "annotator_directness": 0.4},
    ])
    p = pts["op1"]
    assert tuple(p[k] for k in DIMS) == (0.1, 0.2, 0.3, 0.4)


def test_fold_operator_annotated_accepts_documented_short_payload_names():
    """A raw producer journaling the docs/event-catalog.md payload shape
    (short names) must not be silently dropped by the fold."""
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "op1", "content": "IMPL(a, b)", "pointKind": "",
            "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}},
        {"type": "OperatorAnnotated", "id": "op1",
         "bias": 0.7, "precision": 0.6, "consistency": 0.5, "directness": 0.4},
    ])
    p = pts["op1"]
    assert tuple(p[k] for k in DIMS) == (0.7, 0.6, 0.5, 0.4)


def test_fold_point_revised_restores_dims_presence_conditional():
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "pt1", "content": "claim", "pointKind": ""}},
        {"type": "PointRevised", "id": "pt1",
         "annotator_bias": 0.1, "annotator_precision": 0.2,
         "annotator_consistency": 0.3, "annotator_directness": 0.4},
        {"type": "PointRevised", "id": "pt1", "annotator_bias": 0.9},
    ])
    p = pts["pt1"]
    assert p["annotator_bias"] == 0.9
    assert p["annotator_precision"] == 0.2
    assert p["annotator_consistency"] == 0.3
    assert p["annotator_directness"] == 0.4


def test_apply_annotator_ignores_non_str_id(sup):
    """Malformed id → skip WITHOUT issuing a query (the str guard is what
    prevents passing an unhashable/odd Cypher parameter), never crash."""
    from unittest.mock import patch
    _, _, sdk = sup
    proj = sdk._get_proj()
    # _GuardedGraph uses __slots__, so patch the CLASS method (instance
    # attributes are read-only). The discriminator: the guard must short-
    # circuit BEFORE issuing the Cypher.
    with patch.object(type(proj.g), "query") as q:
        assert proj._apply_annotator({
            "type": "OperatorAnnotated", "id": ["not", "a", "str"],
            "annotator_bias": 0.5,
        }) == 0
    q.assert_not_called()
    assert proj._apply_annotator({
        "type": "OperatorAnnotated", "id": "op-absent",
        "annotator_bias": 0.5,
    }) == 0  # no such node → fold-miss
    # A NUL / lone-surrogate id is ALSO rejected before any query (the id
    # rides as a Cypher param exactly like a value; review P1).
    with patch.object(type(proj.g), "query") as q2:
        assert proj._apply_annotator({
            "type": "OperatorAnnotated", "id": "bad\x00id",
            "annotator_bias": 0.5,
        }) == 0
        assert proj._apply_annotator({
            "type": "OperatorAnnotated", "id": "a\ud800b",
            "annotator_bias": 0.5,
        }) == 0
    q2.assert_not_called()


def test_fold_operator_annotated_accepts_point_dict_payload():
    """A raw producer journaling a nested ``point`` snapshot (the ``_norm``
    splice path) is folded too."""
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "op1", "content": "IMPL(a, b)", "pointKind": "",
            "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}},
        {"type": "OperatorAnnotated", "point": {
            "id": "op1", "bias": 0.7, "precision": 0.6,
            "consistency": 0.5, "directness": 0.4}},
    ])
    p = pts["op1"]
    assert tuple(p[k] for k in DIMS) == (0.7, 0.6, 0.5, 0.4)


def test_apply_annotator_accepts_short_names(sup):
    """The graph-lane alias admission for OperatorAnnotated (not just the
    pure fold) — a raw producer journaling the documented payload."""
    _, _, sdk = sup
    op = _operator(sdk)
    sdk._get_proj().apply({
        "type": "OperatorAnnotated", "id": op,
        "bias": 0.7, "precision": 0.6, "consistency": 0.5,
        "directness": 0.4,
    })
    assert _dims(sdk, op) == {
        "annotator_bias": 0.7, "annotator_precision": 0.6,
        "annotator_consistency": 0.5, "annotator_directness": 0.4,
    }


def test_annotator_dims_alias_rejects_non_persistable_value():
    """The short-alias admission path must apply the SAME value gate: a
    malformed short-named value is dropped, and a valid canonical value wins
    over it (review P2)."""
    def _point() -> dict:
        # fold() stores the point dict by reference and mutates it in place,
        # so each fold needs its OWN event objects (pre-existing behaviour).
        return {"type": "PointAdded", "point": {
            "id": "op1", "content": "IMPL(a, b)", "pointKind": "",
            "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}}

    with_canonical = fold([
        _point(),
        {"type": "OperatorAnnotated", "id": "op1",
         "annotator_bias": 0.5, "bias": {"nested": 1}},
    ])
    assert with_canonical["op1"]["annotator_bias"] == 0.5
    alias_only = fold([
        _point(),
        {"type": "OperatorAnnotated", "id": "op1",
         "bias": {"nested": 1}},
    ])
    assert "annotator_bias" not in alias_only["op1"]


def test_annotator_dims_long_name_wins_over_short_alias():
    pts = fold([
        {"type": "PointAdded", "point": {
            "id": "op1", "content": "IMPL(a, b)", "pointKind": "",
            "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}},
        {"type": "OperatorAnnotated", "id": "op1",
         "annotator_bias": 0.1, "bias": 0.9},
    ])
    assert pts["op1"]["annotator_bias"] == 0.1


def test_rebuild_drops_non_finite_annotator_dim(sup):
    """NaN/±Inf must be dropped like any non-persistable value — FalkorDB
    rejects them at parameter parse, and JSON ``1e400`` yields ``inf``, so a
    raw journal can carry one (review P1; aborts after the wipe otherwise)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op,
        "annotator_bias": float("nan"),
        "annotator_precision": float("inf"),
        "annotator_consistency": [float("inf")],
        "annotator_directness": 0.25,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    dims = _dims(sdk, op)
    assert dims["annotator_bias"] is None
    assert dims["annotator_precision"] is None
    assert dims["annotator_consistency"] is None
    assert dims["annotator_directness"] == 0.25


def test_rebuild_skips_operator_annotated_with_unwritable_id(sup, caplog):
    """A NUL/lone-surrogate ``id`` rides as a Cypher param; it must be skipped
    (with the fold-miss warning), never abort the rebuild after the wipe
    (review P1)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:04+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": "bad\x00id",
        "annotator_bias": 0.5,
    })
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:05+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": "a\ud800b",
        "annotator_bias": 0.5,
    })
    _rewrite_journal(events, records)
    with caplog.at_level(logging.WARNING):
        _rebuild(sdk, events)  # must not raise
    assert _dims(sdk, op) == {k: None for k in DIMS}
    assert any("OperatorAnnotated fold matched no Point" in m
               for m in caplog.messages)


def test_rebuild_skips_ungated_point_revised_content(sup):
    """A corrupt ``new_content`` (NUL/surrogate/map) must not abort the
    rebuild after the wipe, and the annotator dims on the SAME record must
    still fold (review P1)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:07+00:00",
        "type": "PointRevised", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op, "new_content": "a\x00b",
        "annotator_bias": 0.5,
    })
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:08+00:00",
        "type": "PointRevised", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op,
        "new_content": {"nested": 1},
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)  # must not raise
    assert _dims(sdk, op)["annotator_bias"] == 0.5


def test_corrupt_new_content_fold_matches_rebuild(sup):
    """P2#2 (fold-parity #330): ``_apply_one`` assigned ``new_content``
    unconditionally while ``_revise_point`` drops an UNWRITABLE one before
    building its query — so a corrupt ``PointRevised`` made the pure fold and
    ``rebuild_all`` silently disagree. Both must drop the same edit."""
    _, events, sdk = sup
    pid = sdk.create_point("statement", "original content",
                           status="live")["id"]
    before = sdk.get_point(pid)["content"]
    records = _journal(events)
    for seq, corrupt in ((7, "a\x00b"), (8, "a\ud800b"),
                         (9, {"nested": 1}), (10, float("inf"))):
        records.append({
            "event_id": sdk.ulid(),
            "ts": f"2026-09-18T00:00:{seq:02d}+00:00",
            "type": "PointRevised", "initiated_by": "raw-producer",
            "projection_version": 2, "id": pid, "new_content": corrupt,
        })
    _rewrite_journal(events, records)
    folded = fold(records)[pid]["content"]
    _rebuild(sdk, events)
    rebuilt = sdk.get_point(pid)["content"]
    assert folded == before, "fold applied an unwritable new_content"
    assert rebuilt == before, "rebuild applied an unwritable new_content"
    assert folded == rebuilt, (
        "fold/rebuild diverged on a corrupt new_content (#330)")


def test_rebuild_skips_point_revised_with_unwritable_id(sup):
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:09+00:00",
        "type": "PointRevised", "initiated_by": "raw-producer",
        "projection_version": 2, "id": "bad\x00id",
        "new_content": "irrelevant", "annotator_bias": 0.5,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)  # must not raise
    assert _dims(sdk, op) == {k: None for k in DIMS}


def test_rebuild_skips_operator_annotated_with_non_str_id(sup):
    """A non-str ``id`` must not raise ``TypeError: unhashable`` in the
    pass-1b survivor-anchor lookup (review P2)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:06+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": ["not", "a", "str"],
        "annotator_bias": 0.5,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)  # must not raise
    assert _dims(sdk, op) == {k: None for k in DIMS}


def test_fold_operator_annotated_absent_id_is_noop():
    """An annotation for an id absent from the folded points must skip, not
    raise (review P2)."""
    pts = fold([{"type": "OperatorAnnotated", "id": "absent",
                 "annotator_bias": 0.5}])
    assert pts == {}


def test_rebuild_drops_nul_and_surrogate_annotator_strings(sup):
    """Strings with a NUL byte or a lone surrogate are valid JSONL content
    but rejected by FalkorDB's parameter parser / the driver's UTF-8 encode —
    drop them instead of aborting the rebuild after the wipe (review P1)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:03+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op,
        "annotator_bias": "a\x00b",
        "annotator_precision": "a\ud800b",
        "annotator_consistency": ["x\x00y"],
        "annotator_directness": 0.25,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    dims = _dims(sdk, op)
    assert dims["annotator_bias"] is None
    assert dims["annotator_precision"] is None
    assert dims["annotator_consistency"] is None
    assert dims["annotator_directness"] == 0.25


def test_rebuild_drops_non_finite_point_revised_dim(sup):
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:01+00:00",
        "type": "PointRevised", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op, "new_content": None,
        "annotator_bias": float("-inf"),
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    assert _dims(sdk, op)["annotator_bias"] is None


def test_rebuild_replays_none_annotator_dim_as_clear(sup):
    """A journaled ``None`` dim must CLEAR the property (live parity) —
    dropping the key would leave the earlier value in place (review P2)."""
    _, events, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:02+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op, "annotator_bias": None,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    dims = _dims(sdk, op)
    assert dims["annotator_bias"] is None
    assert dims["annotator_precision"] == 0.3  # siblings untouched


def test_rebuild_drops_non_persistable_annotator_dim(sup):
    """A malformed record (map-valued dim) must degrade to a dropped dim —
    NOT abort rebuild pass-1b after the wipe (#2894/#2795; review P1)."""
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
        "type": "OperatorAnnotated", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op,
        "annotator_bias": {"nested": 1},
        "annotator_precision": 0.5,
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    dims = _dims(sdk, op)
    assert dims["annotator_bias"] is None
    assert dims["annotator_precision"] == 0.5


def test_rebuild_drops_non_persistable_point_revised_dim(sup):
    _, events, sdk = sup
    op = _operator(sdk)
    records = _journal(events)
    records.append({
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:01+00:00",
        "type": "PointRevised", "initiated_by": "raw-producer",
        "projection_version": 2, "id": op, "new_content": None,
        "annotator_bias": [1, {"bad": 2}],
    })
    _rewrite_journal(events, records)
    _rebuild(sdk, events)
    assert _dims(sdk, op)["annotator_bias"] is None


def test_apply_annotator_str_id_without_dims_is_noop(sup):
    """str id but no dim → must NOT build an empty ``SET  RETURN`` (FalkorDB
    syntax error); the guard is load-bearing."""
    _, _, sdk = sup
    op = _operator(sdk)
    assert sdk._get_proj()._apply_annotator({
        "type": "OperatorAnnotated", "id": op}) == 0


def test_apply_folds_operator_annotated(sup):
    _, _, sdk = sup
    op = _operator(sdk)
    proj = sdk._get_proj()
    assert proj._apply_annotator({
        "type": "OperatorAnnotated", "id": op,
        "annotator_bias": 0.11, "annotator_precision": 0.22,
        "annotator_consistency": 0.33, "annotator_directness": 0.44,
    }) == 1  # positive matched-count path
    assert _dims(sdk, op) == {
        "annotator_bias": 0.11,
        "annotator_precision": 0.22,
        "annotator_consistency": 0.33,
        "annotator_directness": 0.44,
    }


# ═══════════════════════════════════════════════════════════════════════
# The :GraphEvent payload contract is preserved (docs/event-catalog.md)
# ═══════════════════════════════════════════════════════════════════════

def test_operator_annotated_graph_event_payload_unchanged(sup):
    """The JSONL extras must NOT rename the shipped :GraphEvent payload.

    Distinct dim values: identical values (0.5 ×4) pin only the KEY names and
    cannot detect a transposition of the four values in the emit dict — a real
    regression for every ``events_poll`` consumer (#3689 review P2).
    """
    _, _, sdk = sup
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.11, 0.22, 0.33, 0.44)
    evs = sdk.events_poll(types=["OperatorAnnotated"])["events"]
    assert len(evs) == 1
    payload = evs[0]["payload"]
    assert payload["id"] == op
    assert payload["bias"] == 0.11
    assert payload["precision"] == 0.22
    assert payload["consistency"] == 0.33
    assert payload["directness"] == 0.44
    assert not any(k in payload for k in DIMS), (
        "the JSONL annotator_* extras leaked into the :GraphEvent payload")
