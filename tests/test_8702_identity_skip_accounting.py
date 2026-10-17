"""#8702 — a fold-SKIPPED record is not a replayed one, and its cause is the truth.

THE DEFECT THIS PINS
--------------------
#7406 swapped the identity guards (``isinstance(p.get("id"), str)`` →
``_writable_id(...)``) so a NUL-bearing ``point.id`` is DROPPED instead of
reaching ``MERGE (n:Point {id:$id})`` and aborting a replay that has already
wiped the graph. That posture is right and it STAYS. What was not updated is
everything downstream of the skip:

  (a) **ACCOUNTING** — ``recover_from_log``'s ``applied += 1`` runs on a normal
      return, so a dropped record was counted as REPLAYED. Measured on the
      issue (and reproduced by test 1 here): a journal of
      ``[PointAdded p-ok, PointAdded "bad\\x00id"]`` returns
      ``{'recovered': True, 'log_points': 2, 'db_points': 1,
        'reason': 'replayed 2 events from events.jsonl'}`` — a faithful,
      complete replay claimed over a journal from which a record was dropped,
      with the only trace in a log warning. Before #7406 the same journal said
      ``(1 skipped)`` (the raise was counted as ``torn``), so the swap took the
      observability DOWN.
  (b) **REPORT** — the same hunks left ``"skipping ... with missing point id"``
      standing, so it fires for an id that is PRESENT but unwritable. An
      operator reading "missing" for a record that carries one looks in the
      wrong place (the #8018 class: a report naming a cause that is false for
      the record it describes).

WHAT THE FIX DOES — AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------
The skip is RECORDED into the run's collector (the same context-var shape the
R8 non-folded set already uses, so a fold deep inside ``apply()`` records
without a signature change). ``recover_from_log`` therefore counts it apart
from ``applied`` and apart from ``torn`` (crash damage), reports it as the
field ``identity_skipped``, and names the true cause in ``reason``.

``recovered`` is UNCHANGED, and no record is re-raised: a corrupt identity must
keep the recovery gate re-runnable on every retry — that is the whole point of
#7406, and a fix that made the journal permanently un-openable would undo it.

Run (embedded carve-out):
  TORTOISE_TEST_CARVE_OUT=1 python -m pytest \\
      tests/test_8702_identity_skip_accounting.py -q
"""
from __future__ import annotations

import json
import logging
import pathlib

import pytest

from tortoise.consistency import recover_from_log
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection
from tortoise.projection.nonfolded import (
    IDENTITY_ABSENT,
    IDENTITY_UNWRITABLE,
    collect_identity_skips,
    record_identity_skip,
)

#: The load-bearing value: a NUL makes the id unusable as a Cypher parameter,
#: so `_writable_id` refuses it — while it is unmistakably PRESENT.
CORRUPT_ID = "bad\x00id"

_PROJECTION_LOGGER = "tortoise.projection"


#: A journal record carries its own vector (a real SDK journal does), so the
#: replay folds the stored one. That is the AUTHENTIC shape AND it keeps this
#: file off the once-per-process cold embedder load (~125 s), which is a lane
#: cost no single test file owns (#3400).
_EMBEDDING = {
    "embedding": [0.0] * 384,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "embedding_revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "embedding_text_hash": "e8702",
}


def _point_added(pid, content, event_id):
    """An authentic ``PointAdded`` record (shape per a real SDK journal)."""
    point = {"content": content, "pointKind": "statement",
             "is_operator": False, "status": "live",
             "createdAt": "2026-01-01T00:00:00+00:00",
             "updatedAt": "2026-01-01T00:00:00+00:00", **_EMBEDDING}
    if pid is not None:
        point["id"] = pid
    return {"event_id": event_id, "ts": "2026-01-01T00:00:00+00:00",
            "type": "PointAdded", "initiated_by": "sdk",
            "projection_version": 2, "point": point}


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
    """ONE projection for the module (a fresh server per test costs ~20 s).

    Each test drives it back to the 0-node state first, so no test can see
    another's graph.
    """
    path = str(tmp_path_factory.mktemp("g8702") / "graph.db")
    projection = FalkorProjection(path)
    yield projection
    projection.close()


def _empty(proj) -> FalkorProjection:
    """Drive the projection to the 0-node state ``recover_from_log`` acts on.

    Construction mints the ``:Meta`` / FTS markers, so the count is 1 rather
    than 0; the wipe restores the exact pre-call state a lost graph has (the
    same state the real open-time recovery sees, because it runs BEFORE
    ``_ensure_indexes``).
    """
    proj._wipe_all_nodes(confirm_destructive=True,
                         operation="test #8702 empty pre-recovery state")
    assert proj.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    return proj


def _corrupt_journal(tmp_path) -> pathlib.Path:
    return _journal(tmp_path,
                    _point_added("p-ok", "a healthy claim", "ev-ok"),
                    _point_added(CORRUPT_ID, "a corrupt identity", "ev-bad"))


# ── 1. the accounting half (indicators 1 and 3) ──────────────────────────

def test_a_fold_skipped_record_is_not_counted_as_replayed(tmp_path, proj):
    """FALSIFIER (issue indicator 1): the drop must not be a replay.

    Value that makes it fail: the NUL-bearing ``point.id``. On the pre-fix
    revision this returns ``reason='replayed 2 events ...'`` over a graph with
    ONE node and no ``identity_skipped`` at all.
    """
    events_dir = _corrupt_journal(tmp_path)
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)
    nodes = proj.query("MATCH (n:Point) RETURN n.id").result_set

    assert result["recovered"] is True, result
    assert [row[0] for row in nodes] == ["p-ok"], nodes

    # The counts RECONCILE: records read = applied + skipped (+ torn + refused).
    assert result["log_points"] == 2, result
    assert result["db_points"] == 1, result
    assert result["identity_skipped"] == 1, result
    assert "replayed 1 events" in result["reason"], result["reason"]
    assert "replayed 2 events" not in result["reason"], (
        "a record the fold dropped is still being reported as replayed: "
        f"{result['reason']!r}")


def test_a_skipped_identity_is_observable_in_the_result_not_only_a_log(
        tmp_path, proj, caplog):
    """FALSIFIER (issue indicator 2): the RESULT must carry the skip.

    The issue's target is that a skip is visible in the returned dict — the
    documented contract a caller reads — not only in a log line nobody
    branches on.
    """
    events_dir = _corrupt_journal(tmp_path)
    _empty(proj)
    with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
        result = recover_from_log(str(events_dir), proj)

    assert result.get("identity_skipped") == 1, result
    assert "identity_skipped" in result, (
        "the skip exists only in the log; a caller inspecting the result sees "
        f"a complete replay: {result!r}")
    warnings = [r.getMessage() for r in caplog.records]
    assert any("skipping PointAdded" in m for m in warnings), warnings


# ── 2. the report half (indicators 2 and 3) ──────────────────────────────

def test_the_skip_names_the_present_id_instead_of_calling_it_missing(
        tmp_path, proj, caplog):
    """FALSIFIER (issue indicator 2): "missing point id" is FALSE here.

    The id is present — it is just not writable. A report that says "missing"
    sends the operator to look for an absent field, and hides the real cause.
    """
    events_dir = _corrupt_journal(tmp_path)
    _empty(proj)
    with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
        result = recover_from_log(str(events_dir), proj)

    messages = " \n".join(r.getMessage() for r in caplog.records)
    assert "missing point id" not in messages, (
        f"the skip still blames a missing id that is present: {messages!r}")
    assert repr(CORRUPT_ID) in messages, (
        f"the report does not say WHICH id was refused: {messages!r}")
    assert "PRESENT but not" in messages, (
        f"the report does not name the actual cause: {messages!r}")

    assert "missing point id" not in result["reason"], result["reason"]
    assert repr(CORRUPT_ID) in result["reason"], (
        f"the recovery result does not say which identity was dropped: "
        f"{result['reason']!r}")
    assert "PRESENT but not writable" in result["reason"], result["reason"]


def test_an_absent_point_id_is_reported_as_absent_not_unwritable(
        tmp_path, proj):
    """The OTHER cause, kept distinct — the fix must not just swap one wrong
    sentence for another.

    An empty/absent identity is genuinely missing; it must say so, and must not
    be described as present-but-unwritable.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p-ok", "a healthy claim", "ev-ok"),
                          _point_added(None, "no identity at all", "ev-noid"))
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)

    assert result["identity_skipped"] == 1, result
    assert "replayed 1 events" in result["reason"], result["reason"]
    assert "carries NO point id at all" in result["reason"], result["reason"]
    assert "PRESENT" not in result["reason"], result["reason"]


def test_the_rebuild_report_drops_the_missing_point_id_sentence(
        tmp_path, proj, caplog):
    """The two ``rebuild_all`` sites (pass 1a + pass 2 edge wiring) of the same
    stale sentence — behaviour unchanged, cause named.

    #7406 kept the rebuild COMPLETING (the P1 acceptance in
    tests/test_7369_journal_param_boundary.py pins the survival); what this
    pins is only that its report no longer describes a present id as missing.
    """
    events_dir = _corrupt_journal(tmp_path)
    _empty(proj)
    with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
        counts = proj.rebuild_all(str(events_dir), confirm_destructive=True)
    nodes = proj.query("MATCH (n:Point) RETURN n.id").result_set

    assert counts["nodes"] == 1, counts
    assert [row[0] for row in nodes] == ["p-ok"], nodes
    messages = " \n".join(r.getMessage() for r in caplog.records)
    assert "missing point id" not in messages, (
        f"the rebuild still reports a present id as missing: {messages!r}")
    assert "PRESENT but not" in messages, messages


# ── 3. anti-overfix guards ───────────────────────────────────────────────

def test_a_healthy_journal_reports_no_skip(tmp_path, proj):
    """ANTI-OVERFIX: the accounting must not turn every record into a skip.

    Wrong implementation this pins: counting per-record rather than per-SKIP
    (e.g. trusting the collector's length instead of the delta around each
    fold), which would report ``identity_skipped=2`` for a healthy journal and
    drive ``applied`` to 0.
    """
    events_dir = _journal(tmp_path,
                          _point_added("p1", "one", "ev-1"),
                          _point_added("p2", "two", "ev-2"))
    _empty(proj)
    result = recover_from_log(str(events_dir), proj)
    count = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]

    assert result["recovered"] is True, result
    assert count == 2
    assert "identity_skipped" not in result, result
    assert result["reason"] == "replayed 2 events from events.jsonl", (
        "a healthy journal must keep its exact pre-#8702 report: "
        f"{result['reason']!r}")


def test_no_skip_is_recorded_outside_a_run():
    """The collector is a RUN boundary: a one-record ``apply()`` (or any call
    outside ``recover_from_log``) records nothing, exactly like the R8 set."""
    entry = record_identity_skip("PointAdded", CORRUPT_ID, "point id",
                                 event_id="ev-bad")
    assert entry.state == IDENTITY_UNWRITABLE, entry
    assert entry.event_id == "ev-bad", entry
    assert "PRESENT but not writable" in entry.describe(), entry.describe()
    assert record_identity_skip("PointAdded", None, "point id").state == (
        IDENTITY_ABSENT)

    with collect_identity_skips() as recorded:
        record_identity_skip("PointAdded", CORRUPT_ID, "point id")
    assert len(recorded) == 1, recorded
    assert recorded[0].value == CORRUPT_ID
