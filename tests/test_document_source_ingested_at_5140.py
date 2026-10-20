"""#5140 (P2 finding 5) — a document `:Source` minted by the EVENT branch carries
no `ingestedAt`.

ONTOLOGY §4.6 marks `ingestedAt` **REQUIRED** on every `:Source` ("when Tortoise
first saw this source"). A document is a `:Source` (§4.4), and it is written by
three paths:

  * the `DocumentCreated` fold — `entities.py::_upsert_document`
    (`s.ingestedAt=coalesce(s.ingestedAt, $now)`);
  * the `objectType='Document'` event branch —
    `entities.py::_event_plain_merge` — **which wrote none**;
  * the hosted `/v1/sessions` transcript MERGE —
    `hosted_api.py::_execute_commit_writes` — **which wrote none**.

The two gaps are the same defect class #5082 closed for `sourceKind` on these
exact clauses: *"two writers of one node, disagreeing on a REQUIRED field"*. The
owners of the D10 requirement sweeps
(`MATCH (s:Source) WHERE … OR s.ingestedAt IS NULL …`) therefore read the
document the event branch minted as a violation.

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.projection import FalkorProjection

#: The instant the journal RECORD carries. `_emit` stamps every record with
#: `ts`, so this is the value the fold must read — not a replay clock.
RECORD_TS = "2026-01-02T03:04:05+00:00"


def _proj() -> FalkorProjection:
    """A throwaway embedded graph (no docker lane, no shared state)."""
    return FalkorProjection(
        os.path.join(tempfile.mkdtemp(prefix="tortoise_5140_"), "t.db"),
        graph_name="test",
    )


def _event(object_id: str, **record) -> dict:
    """The `EventRecorded` envelope `_upsert_event` accepts (nested form).

    ``ts`` rides the RECORD, exactly as `EventAPI._emit` writes it — the nested
    ``event`` payload never carries it.
    """
    return {
        "type": "EventRecorded",
        "ts": RECORD_TS,
        **record,
        "event": {
            "id": f"evt-{object_id}",
            "eventKind": "sessionCaptured",
            "subject": "agent-pi",
            "object": object_id,
            "objectType": "Document",
        },
    }


def _ingested_at(proj, url: str):
    rows = proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.ingestedAt", params={"u": url},
    ).result_set
    return rows[0][0] if rows else None


def test_event_document_branch_stamps_ingested_at():
    """Input: an `EventRecorded` that produces a document `:Source`.

    FAILS IF the minted document `:Source` has no `ingestedAt` (§4.6 REQUIRED),
    or carries something other than the RECORD's own instant — the fold must
    read the recorded `ts`, so a replay produces the same value the live write
    did instead of re-stamping a replay clock (#5024's rule for `updatedAt`).
    """
    proj = _proj()
    try:
        proj.apply(_event("doc-5140-a"))
        assert _ingested_at(proj, "doc-5140-a") == RECORD_TS
    finally:
        proj.close()


def test_event_document_branch_fills_a_pre_fix_null_and_never_clobbers():
    """Input: a document `:Source` that already exists.

    FAILS IF the match arm leaves a pre-existing NULL `ingestedAt` NULL (the
    repair half #5082 added for `sourceKind`), or overwrites an
    already-recorded instant (the field means "when Tortoise FIRST saw this
    source" — #398's never-overwrite contract).
    """
    proj = _proj()
    try:
        for url in ("doc-5140-legacy", "doc-5140-stamped"):
            proj.g.query(
                "MERGE (s:Source {url:$u}) SET s.documentKind='transcript'",
                params={"u": url},
            )
        proj.g.query(
            "MATCH (s:Source {url:'doc-5140-stamped'}) "
            "SET s.ingestedAt='2020-05-06T07:08:09+00:00'")
        proj.apply(_event("doc-5140-legacy"))
        proj.apply(_event("doc-5140-stamped"))
        assert _ingested_at(proj, "doc-5140-legacy") == RECORD_TS
        assert _ingested_at(proj, "doc-5140-stamped") == "2020-05-06T07:08:09+00:00"
    finally:
        proj.close()


def test_the_d10_requirement_sweep_finds_no_violating_source():
    """Input: the §4.6 requirement query the D10 sweeps run.

    FAILS IF any `:Source` the event branch minted violates the sweep
    (`url`/`ingestedAt`/`sourceKind` all non-NULL). This is the exact query
    `#5140` used to reproduce the finding — it returned 1.
    """
    proj = _proj()
    try:
        proj.apply(_event("doc-5140-sweep"))
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' "
            "   OR s.ingestedAt IS NULL OR s.sourceKind IS NULL "
            "RETURN s.url, s.ingestedAt, s.sourceKind",
        ).result_set
        assert rows == [], f"§4.6 REQUIRED-field violations: {rows!r}"
    finally:
        proj.close()
