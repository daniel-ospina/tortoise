"""#3590 Slice 0 — the `rebuild == live` + empty-non-folded-set invariant (R8/R9).

The load-bearing half of #3585. This module asserts the invariant as a
POSITIVE assertion (the state found, not a warning) on every apply-based
replay engine, and — because R8's fail-closed rule makes a naive
`rebuild == live` compare two *equally incomplete* projections — asserts the
non-folded set is empty as well (decision doc R9 in detail).

S0 is NOT a pure test addition: it lands the recording-only `_record_non_fold`
recorder + `materialize()` in `tortoise/projection/__init__.py`. Without a
source of non-folds the self-test below could not fail and the whole harness
would be vacuous by construction. The recorder never raises and never alters a
fold — the fail-loud raise on a non-empty set is S2.

Shapes that are known-bad today are `xfail(strict=True)` with an owner issue:
`strict=True` turns "unexpectedly passing" into a failure, so the pin cannot
silently outlive its bug. The shared vectors live in
`tests/fixtures/entity_identity_vectors.json` (#3589's mechanism); the
consistency test at the foot of this module keeps the fixture and the drivers
from drifting apart.
"""
from __future__ import annotations

import inspect
import json
import os

sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
import sys  # noqa: E402

sys.path.insert(0, sys_path)

import pytest  # noqa: E402
from identity_vectors import load_vectors, validate_fixture, vector  # noqa: E402

from tortoise.consistency import recover_from_log  # noqa: E402
from tortoise.ids import ulid  # noqa: E402
from tortoise.log import EventLog  # noqa: E402
from tortoise.projection import (  # noqa: E402
    _PROJECTION_NOEFFECT_EVENT_TYPES,
    FalkorProjection,
    NonFoldedEntry,
    materialize,
)
from tortoise.sdk import _GRAPH_EVENT_TYPES, TortoiseSDK  # noqa: E402

# The three apply-based replay engines. `fold`/`_apply_one`
# (projection/__init__.py) is deliberately EXCLUDED: it is Object-blind by
# design, so it cannot see the entity class this invariant exists to protect.
# Its exclusion is a decision, not an oversight.
ALL_ENGINES = ("rebuild", "rebuild_all", "recover_from_log")


# ── harness ───────────────────────────────────────────────────────────────

def _mk_sdk(tmp_path, name="t.db"):
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / name),
                      event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _fmt(entries) -> list:
    """Sorted, comparable rendering of the non-folded set (names the position,
    the failure shape and the event — a bare 'something failed' is not
    reportable)."""
    return sorted((e.journal_pos, e.shape, e.type_name or "", e.event_id or "")
                  for e in entries)


def _append_raw(log_path, event: dict) -> None:
    """Append a raw JSONL line — the synthetic-journal injection the self-test
    and the `unknown_event_type_in_journal` vector need (never via the SDK, so
    the live path cannot see it)."""
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def assert_rebuild_equals_live(sdk, events_dir, *, engines=ALL_ENGINES) -> None:
    """R9: `(materialize(), non_folded)` must match live on every engine.

    The live snapshot is taken BEFORE any wipe, then each engine rebuilds from
    the same journal and is compared against it. The non-folded set is
    asserted empty per engine — that is what converts "both sides agree they
    are incomplete" into a failure (decision doc R9 in detail).
    """
    proj = sdk._get_proj()
    live = materialize(proj)
    assert live.non_folded == frozenset(), (
        "non-folded events present on the LIVE path: "
        f"{_fmt(live.non_folded)}")
    log_path = events_dir / "events.jsonl"
    for engine in engines:
        proj.non_folded.clear()
        if engine == "rebuild":
            proj.rebuild(EventLog(str(log_path)))
        elif engine == "rebuild_all":
            proj.rebuild_all(str(events_dir))
        elif engine == "recover_from_log":
            # recover_from_log refuses unless the graph is empty.
            proj.g.query("MATCH (n) DETACH DELETE n")
            recover_from_log(str(events_dir), proj)
        else:  # pragma: no cover - programming error, not a data path
            raise ValueError(f"unknown replay engine {engine!r}")
        replay = materialize(proj)
        assert replay.state == live.state, (
            f"{engine}: rebuild != live — "
            f"live_nodes={live.nodes!r} replay_nodes={replay.nodes!r} "
            f"live_edges={live.edges!r} replay_edges={replay.edges!r}")
        assert replay.non_folded == frozenset(), (
            f"{engine}: non-folded events {_fmt(replay.non_folded)}")


# ── shape drivers (keyed by fixture vector id; see the consistency test) ──

def _drive_create_object_twice(sdk):
    sdk.create_object("Acme")
    sdk.create_object("Acme")


def _drive_create_subject_twice(sdk):
    sdk.create_subject("Acme")
    sdk.create_subject("Acme")


def _drive_delete_object(sdk):
    node = sdk.create_object("DeleteMe")
    sdk._delete_entity(node["id"])


def _drive_delete_then_recreate(sdk):
    node = sdk.create_object("Recreate")
    sdk._delete_entity(node["id"])
    sdk.create_object("Recreate")


def _drive_rename_then_rebuild(sdk):
    node = sdk.create_object("OldName")
    sdk._update_entity(node["id"], name="NewName")


def _drive_connector_bare_name_object(sdk):
    # A bare-name reference with no registration. S0 pinned this xfail: the
    # projection minted a random-ULID stub live and a DIFFERENT one on replay
    # (the D9 non-determinism). #3590 S1 keys the stub on the canonical entity
    # key, so both sides mint the SAME node and the shape is green. S2 owns
    # the other half of D9 (producers register; a reference to an unregistered
    # name refuses and records a non-folded entry) and must re-derive this
    # vector when it lands.
    sdk.create_event("BareEvent", "meeting",
                     subject="BareSubject", object="BareObject")


def _drive_event_about_object_by_id(sdk):
    node = sdk.create_object("Acme")
    sdk.create_event("Meeting", "meeting", aboutObject=node["id"])


def _drive_event_about_subject_by_name(sdk):
    sdk.create_subject("Acme")
    sdk.create_event("Meeting", "meeting", aboutSubject="Acme")


def _drive_delete_then_replay_vs_never_created(sdk):
    # The standard harness already fails here (live gone, replay resurrected);
    # the dedicated test below pins the tombstone-vs-never-created distinction.
    node = sdk.create_object("Gone")
    sdk._delete_entity(node["id"])


SHAPE_DRIVERS = {
    "create_object_twice": _drive_create_object_twice,
    "create_subject_twice": _drive_create_subject_twice,
    "event_about_object_by_id": _drive_event_about_object_by_id,
    "event_about_subject_by_name": _drive_event_about_subject_by_name,
    "delete_object": _drive_delete_object,
    "delete_then_recreate": _drive_delete_then_recreate,
    "rename_then_rebuild": _drive_rename_then_rebuild,
    "connector_bare_name_object": _drive_connector_bare_name_object,
    "delete_then_replay_vs_never_created":
        _drive_delete_then_replay_vs_never_created,
}


def _run_shape(tmp_path, driver) -> None:
    sdk, events = _mk_sdk(tmp_path)
    try:
        driver(sdk)
        assert_rebuild_equals_live(sdk, events)
    finally:
        sdk.close()


# ── baseline shapes (green today) ─────────────────────────────────────────

def test_create_object_twice_rebuild_equals_live(tmp_path):
    """The #452 idempotency contract holds live AND on every replay engine."""
    _run_shape(tmp_path, _drive_create_object_twice)


def test_create_subject_twice_rebuild_equals_live(tmp_path):
    """The Subject twin."""
    _run_shape(tmp_path, _drive_create_subject_twice)


def test_create_object_twice_holds_per_engine(tmp_path, replay_engine):
    """Per-engine visibility (the shared harness stops at the first failing
    engine, so this proves each of the three independently). Uses the
    conftest `replay_engine` parametrisation; `ALL_ENGINES` must list the same
    three engines."""
    assert replay_engine in ALL_ENGINES
    sdk, events = _mk_sdk(tmp_path)
    try:
        _drive_create_object_twice(sdk)
        assert_rebuild_equals_live(sdk, events, engines=(replay_engine,))
    finally:
        sdk.close()


# ── known-bad shapes (strict xfail, owner issue required) ─────────────────

@pytest.mark.xfail(strict=True,
                   reason="no delete event in the journal vocabulary — "
                          "owner #3573")
def test_delete_object(tmp_path):
    _run_shape(tmp_path, _drive_delete_object)


@pytest.mark.xfail(strict=True,
                   reason="replay first-wins the first incarnation — "
                          "owner #3573")
def test_delete_then_recreate(tmp_path):
    _run_shape(tmp_path, _drive_delete_then_recreate)


@pytest.mark.xfail(strict=True,
                   reason="renames are not journaled (#3377) — owner #3377")
def test_rename_then_rebuild(tmp_path):
    _run_shape(tmp_path, _drive_rename_then_rebuild)


def test_connector_bare_name_object(tmp_path):
    """#3590 S1 un-xfailed this: id-keying the projection's bare-name stub on
    the canonical entity key makes live and replay mint the SAME node (S0
    pinned the xfail while the stub was a random ulid)."""
    _run_shape(tmp_path, _drive_connector_bare_name_object)


@pytest.mark.xfail(strict=True,
                   reason="Event about* edges are not replayed from the "
                          "EventRecorded line — owner #2296 (sibling #3307)")
def test_event_about_object_by_id(tmp_path):
    _run_shape(tmp_path, _drive_event_about_object_by_id)


@pytest.mark.xfail(strict=True,
                   reason="Event about* edges are not replayed from the "
                          "EventRecorded line — owner #2296 (sibling #3307)")
def test_event_about_subject_by_name(tmp_path):
    _run_shape(tmp_path, _drive_event_about_subject_by_name)


@pytest.mark.xfail(strict=True,
                   reason="live-after-delete materialises nothing, so it "
                          "equals a never-created name — owner #3573 "
                          "(un-xfailed in S2)")
def test_delete_then_replay_vs_never_created(tmp_path):
    """The P1-1 distinction: a deleted entity must not compare equal to a
    name that was never created. Today BOTH sides materialise nothing.

    Live-after-delete is `{}` because `_delete_entity` is a bare
    `DETACH DELETE`; a never-created name is `{}` too. After S2 the deleted
    side is a `status='retracted'` tombstone on live AND replay, so the two
    stop comparing equal.
    """
    sdk, _events = _mk_sdk(tmp_path)
    try:
        node = sdk.create_object("Gone")
        sdk._delete_entity(node["id"])
        deleted = materialize(sdk._get_proj()).state
        # A never-created name materialises nothing at all.
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        never_created = materialize(sdk._get_proj()).state
        assert never_created == ((), ()), never_created
        assert deleted != never_created, (
            "a deleted entity must materialise differently from a name that "
            "was never created — today both materialise nothing")
    finally:
        sdk.close()


# ── non-vacuity, totality, drift ──────────────────────────────────────────

def test_invariant_detects_injected_non_folded_event(tmp_path):
    """The harness is NOT vacuous: a synthetic non-folded event in the journal
    makes it fail, and the failure names the offending journal position.

    This test is the reason S0 lands the recorder: with `_record_non_fold`
    removed the harness cannot detect the injection and this test goes RED
    (proved in the S0 verification, not merely asserted here).
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        sdk.create_object("Acme")  # journal line 0
        _append_raw(events / "events.jsonl",
                    {"type": "NoSuchEventType", "event_id": "inj-1"})  # line 1
        with pytest.raises(AssertionError) as excinfo:
            assert_rebuild_equals_live(sdk, events)
        msg = str(excinfo.value)
        assert "unhandled-event-type" in msg, msg
        assert "inj-1" in msg, msg
        assert "1" in msg, msg  # the journal position is named
    finally:
        sdk.close()


def test_materialize_is_canonical_and_total(tmp_path):
    """Two materialisations of one state are identical (canonical), and each
    model property is actually compared (total, falsifiably)."""
    sdk, _events = _mk_sdk(tmp_path)
    try:
        sdk.create_object("Acme")
        proj = sdk._get_proj()
        first = materialize(proj)
        second = materialize(proj)
        assert first.state == second.state, "materialize() is not canonical"
        assert first.non_folded == frozenset()

        # Totality: every one of these is read by the model — `outdated` and
        # `supersededBy` by the live-holder/supersession logic, `deletedAt` by
        # S2's delete fold, the payload by `_persist_extra_props`. A
        # hand-picked identity-field list would silently omit all four.
        prose = (
            "MATCH (o:Object {name:$n}) SET o.outdated = $v",
            "MATCH (o:Object {name:$n}) SET o.supersededBy = $v",
            "MATCH (o:Object {name:$n}) SET o.deletedAt = $v",
            "MATCH (o:Object {name:$n}) SET o._extra_payload = $v",
        )
        state = first.state
        for cypher in prose:
            proj.g.query(cypher, params={"n": "Acme", "v": "x"})
            after = materialize(proj).state
            assert after != state, f"materialize() did not compare: {cypher}"
            state = after
    finally:
        sdk.close()


def test_materialize_detects_single_side_property_drift(tmp_path):
    """A single-side property change breaks the invariant — 'total' is
    falsifiable, not asserted."""
    sdk, events = _mk_sdk(tmp_path)
    try:
        sdk.create_object("Acme")
        proj = sdk._get_proj()
        live = materialize(proj)
        proj.non_folded.clear()
        proj.rebuild_all(str(events))
        assert materialize(proj).state == live.state, "baseline must hold"

        # Drift ONE side only (the replayed graph).
        proj.g.query("MATCH (o:Object {name:$n}) SET o.supersededBy = $v",
                     params={"n": "Acme", "v": "obj-drifted"})
        assert materialize(proj).state != live.state, (
            "a single-side `supersededBy` mutation was invisible to the "
            "invariant — materialize() is not total")
    finally:
        sdk.close()


# ── journal vocabulary parity (scoped to the routed surface) ──────────────

# The plan's explicit three-way partition. The naive "every type in
# _GRAPH_EVENT_TYPES has an apply() branch" version is RED on `main` (#3597
# drops PointSuperseded/PointInvalidated from apply(); pass-1b has no branch
# for OperatorAnnotated/DedupeRecorded/DedupeRejected/BatchIdStamped), and S0
# must not weaken the assertion to a vacuous pass — it partitions instead.
_APPLY_FOLDED = (
    "PointAdded", "OperatorAdded", "PointRevised", "PointRetracted",
    "PointPromoted", "OperatorPromoted", "PointsMerged", "EventRecorded",
    "SubjectAdded", "ObjectSuperseded", "ObjectRegistered",
    "DocumentCreated", "SourceCreated",
)
# Folded by rebuild_all's pass-1b only — apply() has no branch (#3597).
_PASS1B_ONLY = ("PointSuperseded", "PointInvalidated", "DirectEdgeRepoint")
# Neither engine folds these: store-only / JSONL-only records with no entity
# graph effect today. The four #3597 gap types are real missing folds, cited
# by number and deliberately NOT absorbed into S0.
_NOT_A_FOLD = (
    "OperatorAnnotated", "DedupeRecorded", "DedupeRejected",
    "BatchIdStamped", "DirectEdgeCreated", "CalibrationRecorded",
    "IngestStarted", "ConfidenceChanged",
)


def test_journal_vocabulary_has_a_dispatch_branch():
    """Every registered type is in exactly one partition, and the two folded
    partitions have the branch they claim in the engine they claim it for."""
    apply_src = inspect.getsource(FalkorProjection.apply)
    pass1b_src = inspect.getsource(FalkorProjection.rebuild_all)

    partitions = [_APPLY_FOLDED, _PASS1B_ONLY, _NOT_A_FOLD]
    seen: set[str] = set()
    for part in partitions:
        assert seen.isdisjoint(part), f"partition overlap: {set(part) & seen}"
        seen |= set(part)
    # No registered GraphEvent type may escape classification.
    assert set(_GRAPH_EVENT_TYPES) <= seen, (
        f"unclassified registered types: "
        f"{sorted(set(_GRAPH_EVENT_TYPES) - seen)}")
    # The runtime no-effect exemption must live inside the no-fold partition,
    # or the catch-all and the exemption have drifted apart.
    assert _PROJECTION_NOEFFECT_EVENT_TYPES.issubset(_NOT_A_FOLD)

    for t in _APPLY_FOLDED:
        assert f'"{t}"' in apply_src, f"apply() has no branch for {t}"
    for t in _PASS1B_ONLY:
        assert f'"{t}"' in pass1b_src, f"rebuild_all pass-1b has no branch for {t}"


# ── the unknown-type vector ───────────────────────────────────────────────

def test_unknown_event_type_is_recorded(tmp_path):
    """An unhandled `type` is recorded as a shape='unhandled-event-type'
    non-fold naming its journal position, on replay."""
    sdk, events = _mk_sdk(tmp_path)
    try:
        sdk.create_object("Acme")  # line 0
        _append_raw(events / "events.jsonl",
                    {"type": "NoSuchEventType", "event_id": "inj-1"})  # line 1
        proj = sdk._get_proj()
        for engine in ("rebuild", "rebuild_all"):
            proj.non_folded.clear()
            if engine == "rebuild":
                proj.rebuild(EventLog(str(events / "events.jsonl")))
            else:
                proj.rebuild_all(str(events))
            recorded = [e for e in proj.non_folded
                        if e.shape == "unhandled-event-type"]
            assert recorded == [NonFoldedEntry(
                journal_pos=1, shape="unhandled-event-type",
                type_name="NoSuchEventType", event_id="inj-1",
            )], f"{engine}: {proj.non_folded!r}"
    finally:
        sdk.close()


@pytest.mark.xfail(strict=True,
                   reason="the fail-loud replay gate lands in S2 — "
                          "owner #3590/S2")
def test_unknown_event_type_fails_the_run_on_replay(tmp_path):
    """The 'run raises' half of the vector — xfail-pinned at S0 (recording
    only), un-xfailed at S2 where rebuild/rebuild_all raise on a non-empty
    non-folded set."""
    sdk, events = _mk_sdk(tmp_path)
    try:
        sdk.create_object("Acme")
        _append_raw(events / "events.jsonl",
                    {"type": "NoSuchEventType", "event_id": "inj-1"})
        with pytest.raises(Exception):  # noqa: B017 — S2 names the type
            sdk._get_proj().rebuild_all(str(events))
    finally:
        sdk.close()


# ── fixture <-> driver consistency ────────────────────────────────────────

def test_fixture_shape():
    """The shared vector fixture is well-formed and every skipped vector is
    owned (#3589's no-unowned-skip rule). Lives here — not in the loader
    module — because pytest's default ``python_files`` would not collect
    ``tests/identity_vectors.py``."""
    validate_fixture()


def test_fixture_and_drivers_do_not_drift():
    """Every harness-driven vector resolves through a driver, and every driver
    is a real vector id — the shared fixture cannot become decoration."""
    vectors = load_vectors()
    ids = {v["id"] for v in vectors}
    assert set(SHAPE_DRIVERS) <= ids, (
        f"drivers with no vector: {sorted(set(SHAPE_DRIVERS) - ids)}")
    harness_driven = {v["id"] for v in vectors if v["harness"]}
    assert harness_driven == set(SHAPE_DRIVERS), (
        f"harness vectors without a driver: "
        f"{sorted(harness_driven - set(SHAPE_DRIVERS))}; "
        f"drivers with harness=false: "
        f"{sorted(set(SHAPE_DRIVERS) - harness_driven)}")
    # Spot-check the loader, not just its shape.
    assert vector("create_object_twice")["verdict"] == "pass"
    assert vector("delete_object")["owner"] == "#3573"
    # Sanity: the id minter is imported for parity with the S1/S2 suites.
    assert isinstance(ulid(), str)
