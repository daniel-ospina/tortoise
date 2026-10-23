"""#8843 — a MALFORMED journal envelope is dropped, and was still counted REPLAYED.

THE DEFECT THIS PINS
--------------------
`apply()` drops a record whose ENVELOPE is broken, on two guards that both
`return` normally:

  * the ``#331`` guard — a missing, or non-``str``, ``type``;
  * the ``#325`` guard — a creation record (``PointAdded`` / ``OperatorAdded``)
    whose ``point`` payload is not a dict.

Neither raise, so `recover_from_log`'s per-record ``else`` arm incremented
``applied``. MEASURED on the issue, and reproduced by the first test here:

    recover_from_log([healthy PointAdded, {"event_id": "ev-x", "type": null}])
      -> {"recovered": True, "log_points": 2, "db_points": 1,
          "reason": "replayed 2 events from events.jsonl"}

A complete, faithful recovery claimed over a journal from which a record was
dropped, with the only trace a log warning nobody branches on. ⛔ Note precisely
WHAT is wrong there: `log_points` is 2 and `applied` is 2, so
``reads = applied + identity_skipped + torn + refused`` BALANCED arithmetically —
the falsehood was inside `applied`, which counted a dropped record as a replay.
That is the harder half of the false-PASS shape: a reconciliation is only as
honest as its terms, and a term that absorbs the drop makes the sum agree with a
recovery that never happened.

#8702 had already given a DROPPED record a signal, but only for identity
refusals (``identity_skipped``). A malformed envelope carries no identity at
all, so it fell through every bucket.

WHAT THE FIX DOES — AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------
The drop is RECORDED by the guard itself, into a run-scoped collector
(``collect_malformed_envelopes``) — the same context-var shape #8702's identity
collector uses, so a fold deep inside ``apply()`` records without a signature
change at every call site. ``recover_from_log`` counts the collector's
per-record delta into ``malformed_skipped``, reports it as its own additive
field, and names the cause in ``reason``.

⛔ The bucket is its OWN, never ``identity_skipped``. The two causes send an
operator to different places: an identity skip is a well-formed record with a
key the store cannot take; a malformed envelope leaves no record to key. Folding
them would reintroduce the #8018 class (a report naming a cause that is false for
the record it describes) at a new site.

⛔ NO record is re-raised, and no policy about malformed journals is changed: the
replay stays runnable on every retry, exactly as #7406/#8702 require. This test
pins ACCOUNTING and REPORT only.

⛔ The predicate is NOT re-implemented here or in `consistency.py`: on the
replay-accounting path it has ONE home (the two guards in `apply()`), and the
replay loop reads the collector rather than re-testing an envelope. ⚠️ Scope,
stated exactly (review cycle 4): the same two `isinstance` guards also live in
`_apply_one` (the in-memory fold behind `fold()` / `InMemoryProjection` /
`rebuild()`) and in `consistency._fold_journal` (the reference view
`check_consistency` compares against). Neither records, because neither has an
accounting channel — the invariant is "one RECORDER per accounting path", not
"one guard in the package".

Run (embedded carve-out):
  TORTOISE_TEST_CARVE_OUT=1 python -m pytest \\
      tests/test_8843_malformed_envelope_accounting.py -q
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from tortoise.consistency import recover_from_log
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection
from tortoise.projection.nonfolded import (
    MALFORMED_MISSING_TYPE,
    MALFORMED_NON_DICT_POINT,
    MALFORMED_SHADOWED,
    MalformedEnvelope,
    collect_malformed_envelopes,
    record_malformed_envelope,
)

_PROJECTION_LOGGER = "tortoise.projection"

#: A journal record carries its own vector (a real SDK journal does), so the
#: replay folds the stored one. That keeps this file off the once-per-process
#: cold embedder load (~125 s) — the same reason #8702's suite does it (#3400).
_EMBEDDING = {
    "embedding": [0.0] * 384,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "embedding_revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "embedding_text_hash": "e8843",
}


def _point_added(pid, content, event_id):
    """An authentic ``PointAdded`` record (shape per a real SDK journal)."""
    return {
        "event_id": event_id,
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "PointAdded",
        "initiated_by": "sdk",
        "projection_version": 2,
        "point": {
            "id": pid,
            "content": content,
            "pointKind": "statement",
            "is_operator": False,
            "status": "live",
            "createdAt": "2026-01-01T00:00:00+00:00",
            "updatedAt": "2026-01-01T00:00:00+00:00",
            **_EMBEDDING,
        },
    }


def _journal(tmp_path, *records) -> pathlib.Path:
    """Write ONE adjacent JSONL log (the shape recover_from_log requires)."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    log = EventLog(str(events_dir / "events.jsonl"))
    for record in records:
        log.append(record)
    return events_dir


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    """ONE projection for the module (a fresh server per test costs ~20 s)."""
    path = str(tmp_path_factory.mktemp("g8843") / "graph.db")
    projection = FalkorProjection(path)
    yield projection
    projection.close()


def _empty(proj) -> FalkorProjection:
    """Drive the projection to the 0-node state ``recover_from_log`` acts on."""
    proj._wipe_all_nodes(confirm_destructive=True,
                         operation="test #8843 empty pre-recovery state")
    assert proj.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    return proj


#: The exact record shape the issue measured. `type: None` is PRESENT but not a
#: str, so `_norm` cannot select a fold for it.
def _type_less_record():
    return {"event_id": "ev-x", "ts": "2026-01-01T00:00:00+00:00",
            "type": None}


def _point_not_a_dict_record():
    return {"event_id": "ev-p", "ts": "2026-01-01T00:00:00+00:00",
            "type": "PointAdded", "initiated_by": "sdk",
            "projection_version": 2, "point": "not-a-dict"}


# ── 1. the accounting half (issue indicators 1 and 2) ─────────────────────

def test_a_malformed_envelope_is_not_counted_as_replayed(tmp_path, proj):
    """FALSIFIER (issue indicator 1): the drop must not be a replay.

    Value that makes it fail: the `type: None` record. On the pre-fix revision
    this returns `reason='replayed 2 events ...'` over a graph with ONE node and
    carries no bucket at all for the second record.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _type_less_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)
    nodes = proj.query("MATCH (n:Point) RETURN n.id").result_set

    assert result["recovered"] is True, result
    assert [row[0] for row in nodes] == ["p-ok"], nodes

    # The counts RECONCILE: records read = applied + malformed (+ identity +
    # torn + refused, none of which fired here).
    assert result["log_points"] == 2, result
    assert result["db_points"] == 1, result
    assert result.get("malformed_skipped") == 1, result
    assert result.get("identity_skipped") is None, (
        "a malformed envelope is NOT an identity skip — it has no identity to "
        f"attribute: {result!r}")
    assert "replayed 1 events" in result["reason"], result["reason"]
    assert "replayed 2 events" not in result["reason"], (
        "a record the fold dropped is still being reported as replayed: "
        f"{result['reason']!r}")


def test_a_non_dict_point_is_counted_in_the_same_bucket(tmp_path, proj):
    """FALSIFIER (issue components): the OTHER malformed guard, same bucket.

    `#325` is a different guard from `#331` but the same defect — a normal
    return that the replay loop could not tell from a fold. Counting only one of
    the two would leave the class half-closed.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _point_not_a_dict_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result["recovered"] is True, result
    assert result.get("malformed_skipped") == 1, result
    assert "replayed 1 events" in result["reason"], result["reason"]


def test_malformed_and_identity_drops_are_counted_apart(tmp_path, proj):
    """FALSIFIER: the two drop channels must not absorb each other.

    A journal carrying BOTH kinds must report each in its own field — an
    operator sent looking for an unwritable key on an envelope-less record is
    the #8018 class this split exists to prevent.
    """
    corrupt_id = "bad\x00id"
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _type_less_record(),
                          _point_added(corrupt_id, "a corrupt id", "ev-bad"))
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result["log_points"] == 3, result
    assert result.get("malformed_skipped") == 1, result
    assert result.get("identity_skipped") == 1, result
    # reads reconcile against the graph: one node, one applied record.
    assert result["db_points"] == 1, result
    assert "replayed 1 events" in result["reason"], result["reason"]


# ── 2. the report half (issue indicator 2) ────────────────────────────────

def test_the_reason_names_the_cause_and_the_record_to_repair(tmp_path, proj):
    """FALSIFIER (issue indicator 2): the RESULT must carry it, not only a log.

    The signal has to be in the returned dict — the documented contract a caller
    branches on — and it must name WHICH line to repair, never call the record
    `missing` (its `event_id` is right there).
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _type_less_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert "malformed_skipped" in result, (
        "the drop exists only in the log; a caller inspecting the result sees "
        f"a complete replay: {result!r}")
    reason = result["reason"]
    # ⛔ NEUTRAL on purpose (review cycle 7): this clause covers four causes and
    # an earlier draft said "whose discriminator is unreadable", which is FALSE
    # for a bad payload on a record whose discriminator is perfectly readable.
    assert "it could not read" in reason, reason
    assert "ev-x" in reason, (
        f"the clause does not name the record to repair: {reason!r}")
    # NOT attributed to a cause that is false for this record.
    assert "identity" not in reason.lower(), reason


def test_the_drop_is_still_announced_on_the_log_boundary(
        tmp_path, proj, caplog):
    """The pre-existing warning is KEPT: the recorder adds a channel, never
    replaces the one an operator tails."""
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _type_less_record())
    _empty(proj)
    with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
        recover_from_log(str(events_dir), proj)

    messages = " \n".join(r.getMessage() for r in caplog.records)
    assert "missing/non-string 'type'" in messages, messages


# ── 2b. the ABSENT-key vs PRESENT-but-wrong split, END TO END ────────────
#
# ⛔ These tests exist because a mutation proved cycle 1's `field_present` fix
# had NO end-to-end guard (review cycle 2): every other fixture in this file
# carries the key with a WRONG VALUE (`type: None`, `point: "not-a-dict"`), so an
# implementation that inferred presence from the value —
# `field_present=t is not None` at the call site — passed the entire suite.
#
# ⛔ WHICH fixture discriminates WHICH way (corrected in review cycle 4, because
# the first draft of this comment was inverted and would have invited a lane to
# delete the load-bearing fixture as redundant):
#
#   * the type site is pinned by the PRESENT-with-a-wrong-value fixture —
#     `type: None`. Under `field_present=t is not None` that record renders the
#     ABSENT sentence, so `test_a_present_but_wrong_type_...` reds. The
#     ABSENT-key test still passes under that mutation (`None` happens to give
#     the right answer there), so it is NOT the discriminator — it pins the
#     OPPOSITE direction of the same report.
#   * the point site is pinned by `point: null` for the same reason, and NOT by
#     `point: "not-a-dict"` (`p is not None` is True for any string, so a
#     value-based flag renders correctly and the mutation survives).
#
# Both directions are asserted on the PRODUCT's own output (`reason`), which is
# what an operator reads; the unit-level matrix below cannot see a call site.

def _absent_type_key_record():
    """No `type` key at all — distinct from `type: None`."""
    return {"event_id": "ev-absent-type",
            "ts": "2026-01-01T00:00:00+00:00"}


def _absent_point_key_record():
    """A creation record with NO `point` key — distinct from `point: <str>`."""
    return {"event_id": "ev-absent-point",
            "ts": "2026-01-01T00:00:00+00:00", "type": "PointAdded",
            "initiated_by": "sdk", "projection_version": 2}


def test_an_absent_type_key_is_not_reported_as_a_present_wrong_value(
        tmp_path, proj):
    """ABSENT `type` → the absent-key sentence exactly (one direction of the pin).

    Pins the report for a record that really has no discriminator key. It does
    NOT discriminate the presence flag — `None` gives the right answer under a
    value-based flag too; the present-`null` test below is what does.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _absent_type_key_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result.get("malformed_skipped") == 1, result
    reason = result["reason"]
    assert "carries no 'type' key at all" in reason, reason
    assert "ev-absent-type" in reason, reason
    # And NOT the sentence for a present-but-unwritable discriminator.
    assert "NoneType" not in reason, (
        f"an absent key is being described as present: {reason!r}")


def test_a_present_but_wrong_type_is_not_reported_as_absent(tmp_path, proj):
    """`type: None` (PRESENT) → the present-value sentence, never "absent".

    The opposite direction of the pin above, and the one the #8018 class is
    about: the key is right there, so calling the record envelope-less sends the
    operator to insert a field instead of fixing a value.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _type_less_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert "no 'type' key" not in reason, (
        f"a PRESENT key is being reported as absent: {reason!r}")
    assert "NoneType" in reason and "not a str" in reason, reason


def test_an_absent_point_key_is_reported_as_absent(tmp_path, proj):
    """ABSENT `point` → the absent-key sentence; the OTHER guard's call site."""
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _absent_point_key_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result.get("malformed_skipped") == 1, result
    reason = result["reason"]
    assert "has no 'point' key at all" in reason, reason
    assert "ev-absent-point" in reason, reason


def test_a_present_but_wrong_point_is_not_reported_as_absent(tmp_path, proj):
    """`point: "not-a-dict"` (PRESENT) → never the absent-key sentence."""
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _point_not_a_dict_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert "no 'point' key" not in reason, (
        f"a PRESENT key is being reported as absent: {reason!r}")
    assert "not a dict" in reason, reason


def test_an_explicit_null_point_is_present_not_absent(tmp_path, proj):
    """`point: null` (PRESENT, null value) → the present-value sentence.

    ⛔ This is the fixture that makes the `point` call site's `field_present`
    load-bearing (review cycle 2). `point: "not-a-dict"` does NOT distinguish a
    presence flag from a value check — `p is not None` is True for any string,
    so a value-inferred flag renders correctly and the mutation survives. Only
    an explicit `null` separates them: `ev.get("point")` is `None` while the
    key is right there, so a value-inferred flag calls it absent.
    """
    events_dir = _journal(
        tmp_path,
        _point_added("p-ok", "a healthy claim", "ev-ok"),
        {"event_id": "ev-null-point", "ts": "2026-01-01T00:00:00+00:00",
         "type": "PointAdded", "initiated_by": "sdk",
         "projection_version": 2, "point": None})
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result.get("malformed_skipped") == 1, result
    reason = result["reason"]
    assert "no 'point' key" not in reason, (
        f"a PRESENT (null) key is being reported as absent: {reason!r}")
    assert "NoneType" in reason and "not a dict" in reason, reason
    assert "ev-null-point" in reason, reason


# ── 3. a journal of ONLY malformed records is not a "recovered" graph ─────

def test_a_journal_of_only_malformed_records_reports_no_replay(tmp_path, proj):
    """FALSIFIER (narrow, stated honestly): the `malformed_skipped` bucket.

    ⛔ The ONLY discriminating assertion here is the BUCKET. `recovered` was
    ALREADY `False` before the fix, for an independent reason: `ok` also
    requires the graph to be non-empty afterwards, and these records create no
    node, so the pre-fix head said `'replay produced an empty graph'` over
    `applied=2`. The two records were counted as REPLAYED and the empty graph
    still saved the verdict — which is exactly why the accounting, not
    `recovered`, is what this test has to pin. (Measured at the pre-fix head;
    an earlier draft of this docstring claimed `recovered` was True, and that
    was wrong.)

    The single-branch assertion is deliberate: an `... or ...` over two
    different reason shapes passes on either, which is how the earlier draft
    stayed green at the pre-fix head.
    """
    events_dir = _journal(tmp_path, _type_less_record(),
                          _point_not_a_dict_record())
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result["log_points"] == 2, result
    assert result.get("malformed_skipped") == 2, result
    # Pinned as the ONE shape the empty-graph branch produces.
    assert "replay produced an empty graph" in result["reason"], result["reason"]
    # And the pre-fix over-report is gone.
    assert "replayed 2 events" not in result["reason"], result["reason"]
    # `recovered` is unchanged by this fix — stated, not assumed.
    assert result["recovered"] is False, result


# ── 4. the collector itself (the seam, not the pipeline) ─━━━━━━━━━━━━━━━

def test_the_collector_is_inert_outside_a_run():
    """A one-record ``apply()`` has no run boundary — recording must be a no-op.

    This is what lets the guard call the recorder unconditionally without
    changing the behaviour of every direct `apply()` caller. ⛔ Pinned by
    inspecting the CONTEXT VAR, not by `isinstance(entry, ...)`: an
    implementation that appended to a module-global list returns a well-formed
    entry and passes the weaker assertion unchanged.
    """
    from tortoise.projection.nonfolded import _MALFORMED_ENVELOPES

    assert _MALFORMED_ENVELOPES.get() is None, "ambient collector state leaked in"
    entry = record_malformed_envelope(
        "journal record", MALFORMED_MISSING_TYPE,
        field_present=False, event_id="ev-outside", where="test")
    assert isinstance(entry, MalformedEnvelope)
    # Nothing was recorded anywhere, and no ambient state was created.
    assert _MALFORMED_ENVELOPES.get() is None, (
        "an out-of-run call created ambient collector state")

    with collect_malformed_envelopes() as entries:
        assert entries == [], "an earlier out-of-run call leaked into this run"

    # Nested runs are independent.
    with collect_malformed_envelopes() as outer:
        with collect_malformed_envelopes() as inner:
            record_malformed_envelope(
                "journal record", MALFORMED_MISSING_TYPE, event_id="ev-in")
        assert [e.event_id for e in inner] == ["ev-in"]
        assert outer == []


def test_a_clean_journal_omits_the_bucket_entirely(tmp_path, proj):
    """The key's ABSENCE must keep meaning "no malformed record".

    The additive-field contract (present only when non-zero, the same shape as
    `onboarding_gap`) is what lets a caller read `result.get(...) is None` as
    "none" rather than "unknown", so it needs its own pin.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"))
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result["recovered"] is True, result
    assert "malformed_skipped" not in result, result
    assert "it could not read" not in result["reason"], result["reason"]


def test_the_entry_names_the_true_cause_for_each_guard():
    """The report states WHICH guard fired AND whether the field was there.

    ⛔ Four combinations, because presence is the fact that must not be guessed:
    ``ev.get("type")`` is ``None`` for an ABSENT key and for a present
    ``type: null``, and a report that branched on the value alone called the
    second one "missing" — the #8018 class, at the very site that exists to
    keep it out.
    """
    absent = MalformedEnvelope("journal record", MALFORMED_MISSING_TYPE,
                              field_present=False)
    assert "no 'type' key at all" in absent.describe(), absent.describe()

    present_null = MalformedEnvelope("journal record", MALFORMED_MISSING_TYPE,
                                     field_present=True, value=None)
    described = present_null.describe()
    assert "no 'type'" not in described, (
        f"an ABSENT-key sentence for a record whose type key is present: {described}")
    assert "None" in described and "not a str" in described, described

    present_list = MalformedEnvelope("journal record", MALFORMED_MISSING_TYPE,
                                     field_present=True, value=["PointAdded"])
    assert "list" in present_list.describe(), present_list.describe()

    no_point = MalformedEnvelope("PointAdded", MALFORMED_NON_DICT_POINT,
                                field="point", field_present=False)
    assert "no 'point' key at all" in no_point.describe(), no_point.describe()

    bad_point = MalformedEnvelope("PointAdded", MALFORMED_NON_DICT_POINT,
                                 field="point", field_present=True, value="s")
    described = bad_point.describe()
    assert "no 'point' key" not in described, described
    assert "'s'" in described and "not a dict" in described, described


# ── 5. the SHADOWED discriminator is not an "envelope is malformed" ──────
#
# ⛔ Review cycle 4, and the sharpest instance of the #8018 class this channel
# exists to keep out. `apply()` classifies on `self._norm(event)` —
# `{**ev, **ev["point"]}` — so a point PAYLOAD carrying its own `type` key
# replaces a perfectly good envelope `type`. The record is then dropped for a
# discriminator the ENVELOPE never had, and a clause saying "whose ENVELOPE is
# malformed" names a cause that is false for it: the operator is sent to repair
# a line that is already correct. `apply()` already reads around exactly this
# shadowing for `contains_session`; this pins the accounting report doing the same.

def test_a_shadowing_nested_type_is_not_reported_as_a_malformed_envelope(
        tmp_path, proj):
    """A WELL-FORMED envelope shadowed by `point.type` → its own cause.

    Falsifier: the raw envelope here carries `type: "PointAdded"`. Before the
    shadowed cause existed, `reason` said the ENVELOPE was malformed — false for
    this record — and named no shadowing at all.
    """
    record = {
        "event_id": "ev-shadowed",
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "PointAdded",              # the envelope is FINE
        "initiated_by": "sdk",
        "projection_version": 2,
        # `_norm` splices this over the envelope: `type` becomes None.
        "point": {"id": "p-shadow", "content": "shadowing payload",
                  "type": None},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "shadows the envelope" in reason, reason
    assert "PointAdded" in reason, (
        f"the clause does not name the envelope value that WAS usable: {reason!r}")
    assert "ENVELOPE is malformed" not in reason, reason
    # The cause is NOT the plain missing/non-str sentence either — that would
    # still send the operator to add a field the envelope already carries.
    assert "carries no 'type' key" not in reason, reason


def test_a_shadowing_nested_point_is_not_reported_as_a_non_dict_payload(
        tmp_path, proj):
    """A payload with a nested `point` key → its own cause, not "not a dict".

    ⛔ Review cycle 5 found this branch had NO pin at all: mutating
    `isinstance(raw.get("point"), dict)` to `False` left the whole suite green,
    so a cleanup could delete the branch invisibly. `_norm` splices
    `{**ev, **ev["point"]}`, so `{"point": {"point": "collision"}}` makes the
    fold read the STRING as the payload — the record IS dropped, but the
    envelope is well-formed and "not a dict" would blame the wrong line.

    Also pins the RENDER: the first draft printed the entire payload dict (with
    its embedding) as the value and compared it against itself, which made the
    operator-facing `reason` 7,226 chars for three exemplars.
    """
    record = {
        "event_id": "ev-shadowed-point",
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "PointAdded",
        "initiated_by": "sdk",
        "projection_version": 2,
        # `_norm` splices {"point": "collision"} over the payload: p becomes str.
        "point": {"id": "p-s0", "content": "a shadowing payload",
                  # ⛔ the payload's OWN discriminator: `_norm` splices it over
                  # the envelope's, so `what` must not come from the splice
                  # (review cycle 8 — it rendered "OperatorAdded: … the envelope
                  # record is well-formed", naming the wrong family while
                  # asserting the envelope was fine).
                  "type": "OperatorAdded",
                  "point": "collision",
                  "embedding": [0.5] * 384},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "nested 'point' key" in reason, reason
    # The FAMILY named is the envelope's; the payload's is the shadow, not the
    # record. Asserted positively and negatively so neither direction slips.
    assert "PointAdded" in reason, (
        f"the clause does not name the envelope's own type: {reason!r}")
    assert "OperatorAdded" not in reason, (
        f"the clause names the payload's type as the record family: {reason!r}")
    assert "'collision'" in reason, (
        f"the clause does not name the colliding value: {reason!r}")
    # Neither the generic non-dict sentence nor the missing-key one is true here.
    assert "not a dict" not in reason, reason
    assert "no 'point' key" not in reason, reason
    # ⛔ The render stays readable: the 384-float embedding must never be dumped
    # into an operator-facing line (measured 2,537 chars per exemplar before).
    assert len(reason) < 900, f"reason is a dict dump ({len(reason)} chars): {reason}"
    assert "0.5, 0.5, 0.5" not in reason, "the payload was rendered verbatim"


# ── 6. the report cites the ENVELOPE's id, not a payload's ────────────────
#
# ⛔ Review cycle 6. The clause exists to tell an operator WHICH journal line to
# repair, and every field it cites was read from `ev` — the `_norm` splice
# `{**ev, **ev["point"]}`. A point payload carrying its own `event_id` therefore
# replaced the envelope's, and the report named a line that does not exist
# while the real id appeared NOWHERE in the message. Measured before the fix:
# envelope `event_id="TRUE-ID"` + payload `event_id="FORGED-ID"` rendered
# `(event_id='FORGED-ID')`.

def test_the_report_cites_the_envelope_event_id_not_a_payloads(
        tmp_path, proj):
    """A shadowed `event_id` must not replace the one the operator needs.

    Falsifier: the payload's `event_id`. If any report field is read off the
    splice, this renders `FORGED-ID` and drops `TRUE-ID` entirely.
    """
    record = {
        "event_id": "TRUE-ID",
        "ts": "2026-01-01T00:00:00+00:00",
        "type": None,                      # the drop happens on THIS
        "point": {"id": "p-f", "content": "c", "event_id": "FORGED-ID"},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "TRUE-ID" in reason, (
        f"the report does not name the envelope's own event_id: {reason!r}")
    assert "FORGED-ID" not in reason, (
        f"the report cites a nested payload's event_id as the journal line: {reason!r}")


def test_a_long_refused_value_is_truncated_and_says_so():
    """`_short()` bounds EVERY rendered value, and discloses the cut.

    ⛔ Review cycle 6 found the bound had no pin: reverting `_short` to a bare
    `repr` left the whole suite green, because the end-to-end fixtures all
    collide on a SHORT value (`"collision"`). The reachable regression is a
    large non-dict colliding value — a payload vector, say — where the
    operator-facing `reason` becomes a dump again. Asserted at the unit level
    on purpose: this is a property of the renderer, and a record big enough to
    trip it end to end would be a fixture pretending to be a fuzz case.
    """
    big = [0.5] * 384                          # 1920 chars of repr
    described = MalformedEnvelope(
        "PointAdded", MALFORMED_SHADOWED,
        field="point", field_present=True, value=big).describe()

    assert len(described) < 400, (
        f"the report dumped the whole value ({len(described)} chars)")
    assert "chars)" in described, (
        f"the cut is silent — it reads as a complete value: {described!r}")
    assert "1920" in described, (
        f"the disclosure does not carry the FULL length: {described!r}")
    # The identifying prefix survives: a report that truncates to nothing
    # useful is no better than one that dumps.
    assert described.count("0.5") > 3, described


def test_an_absent_type_key_is_reported_absent_even_when_a_payload_has_one(
        tmp_path, proj):
    """`field_present` is read from the ENVELOPE, not from the `_norm` splice.

    ⛔ Review cycle 6, the last of the same family: `"type" in ev` is True for a
    record whose envelope has NO `type` at all, whenever its payload carries
    one. The report then said "its 'type' is None — a NoneType, not a str"
    about a key that is not there, instead of "carries no 'type' key at all".
    Both send the operator to the same line, but only one of them is true, and
    this channel exists because a plausible false statement about a journal is
    how the wrong line gets "repaired".
    """
    record = {
        "event_id": "ev-nested-only",
        "ts": "2026-01-01T00:00:00+00:00",
        # NO envelope-level `type` at all.
        "point": {"id": "p-n", "content": "c", "type": None},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "carries no 'type' key at all" in reason, reason
    assert "not a str" not in reason, (
        f"a key that is absent was reported as present-with-a-wrong-value: {reason!r}")


def test_a_bad_payload_is_not_described_as_an_unreadable_discriminator(
        tmp_path, proj):
    """The AGGREGATE sentence is neutral — it covers four causes, not one.

    ⛔ Review cycle 7. The clause said "a record whose discriminator is
    unreadable", which is FALSE for a `PointAdded` whose `type` is perfectly
    good and whose `point` is not a dict. Only three exemplars are rendered, so
    for a fourth-and-later drop this sentence is the ONLY thing an operator
    reads — a plausible false statement about a journal is how the wrong line
    gets "repaired" (#8018).
    """
    record = {
        "event_id": "ev-badpayload",
        "ts": "2026-01-01T00:00:00+00:00",
        "type": "PointAdded",              # the discriminator is READABLE
        "initiated_by": "sdk",
        "projection_version": 2,
        "point": "not-a-dict",
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "it could not read" in reason, reason
    assert "unreadable" not in reason, (
        f"the aggregate clause blames the discriminator for a bad payload: {reason!r}")


def test_the_quoted_type_is_the_envelopes_when_a_payload_shadows_it(
        tmp_path, proj):
    """The clause quotes the ENVELOPE's value, not the `_norm` splice's.

    ⛔ Review cycle 7, the last site of the same family. `value=t` was
    `ev.get("type")` — the spliced view — eight lines after a comment saying
    every field below is read from `raw`. When BOTH the envelope and a payload
    carry a non-str `type`, the operator was quoted the payload's value:
    measured, envelope `type: 123` reported as `'type' is None`.

    (This branch is only reachable when `raw["type"]` is not a str, so the two
    values differ only via a nested `type`.)
    """
    record = {
        "event_id": "ev-div",
        "ts": "2026-01-01T00:00:00+00:00",
        "type": 123,                        # the ENVELOPE's value
        "point": {"id": "pdiv", "content": "c", "type": None},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    assert "123" in reason, (
        f"the clause does not quote the envelope's own 'type': {reason!r}")
    assert "is None" not in reason, (
        f"the clause quotes the spliced payload's value: {reason!r}")


def test_a_shadow_with_no_envelope_type_of_its_own_claims_neither(
        tmp_path, proj):
    """Envelope `type: null` + a payload `type` + a shadowing key.

    ⛔ Review cycle 9, the residue of cycle 8's fix: the `else t` fallback on the
    shadow branch still named the PAYLOAD's family, and the sentence still
    asserted "the envelope record is well-formed" about an envelope whose `type`
    is `None`. Both falsehoods, in the clause built to prevent them.

    Reachable in a normal script-format journal, where the discriminator may
    live only inside `point` — which is exactly why `_norm` splices.
    """
    record = {
        "event_id": "ev-combo",
        "ts": "2026-01-01T00:00:01+00:00",
        "type": None,                      # the envelope has NO usable type
        "point": {"id": "p-c", "content": "c",
                  "type": "OperatorAdded",  # the payload's family
                  "point": "collision"},
    }
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          record)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    reason = result["reason"]
    assert result.get("malformed_skipped") == 1, result
    # (a) the payload's family is not the record's
    assert "OperatorAdded" not in reason, (
        f"the clause names the payload's type as the record family: {reason!r}")
    # (b) the envelope is NOT claimed well-formed when its type is null
    assert "well-formed" not in reason, (
        f"the clause calls a typeless envelope well-formed: {reason!r}")
    assert "ALSO dropped" in reason, (
        f"the clause does not disclose the absent discriminator: {reason!r}")
    # The shadow detail is still there — this is an ADDITION, not a replacement.
    assert "nested 'point' key" in reason, reason
    assert "'collision'" in reason, reason
