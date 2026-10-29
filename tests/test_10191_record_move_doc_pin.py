"""#10191 — the RECORD half of a 2b repoint is a MOVE: the docs say it, the plan does it.

Split out of ``tests/test_record_projection.py`` so this half can be registered
in ``core`` + ``tier1`` (the ``test_7818_ontology_record.py`` /
``test_5142_ontology_record.py`` shape). The reason is measured, not stylistic:
a ``docs/INGEST_CONTRACT.md``-only diff matches no ``SOURCE_PATTERNS`` entry
(``docs/`` is in ``NON_PYTHON_PREFIXES``), so the selector returns
``surfaces: []`` and runs **tier1 only**. ``test_record_projection.py`` is
registered under ``core`` alone, so the INGEST half of this pin *slept on the one
edit it exists to catch* — the #8474 shape, recorded at
``config/ci-surfaces.yml``:2049. ``docs/ONTOLOGY.md`` is an explicit selector
entry, which is why that half fired while this one did not.

WHAT THIS PINS. ``#10191`` landed the record-side half of a supersede's
structural repoint (a ``DELETE`` at the superseded point + an ``INSERT`` at the
terminal endpoint of the ``superseded_by`` chain) and left both artifacts
describing pass-2b only, so a lane had to read
``record_projection._structural_repoint_statements`` to learn what the record
actually does with a 2b ``DirectEdgeRepoint`` — and ``#10244`` was spent
re-deriving it. A doc is only a fix if it stays true, so every clause below is
asserted TWICE: the artifact states it, and the emitted plan has the property it
names. A statement deleted from a doc, or a plan that stops doing it, reds here
instead of misinforming the next reader.

The two epic artifacts named in ``_DOC_MOVE_POINTERS`` carried the *retired*
premise in the present tense (“the record's ``DirectEdgeRepoint`` is a plain
upsert”), which is the same doc-vs-consumer disagreement that produced this bug
family. They are pinned inverted: the corrected pointer must be present AND the
retired sentence must be absent.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Deleting or rewording any clause below
      from §4.4 / the supersede row; a plan that stops emitting the two halves,
      the chain CTE, the terminal landing, or ``DO NOTHING``; or a doc that goes
      back to claiming the record upserts in place.
  (2) *Does the test reach it?*  Yes — the docs are read from the tree and the
      plan is emitted by the module the docs describe, not paraphrased here.

DB-free and fixture-free on purpose: it must be cheap enough for ``tier1``,
which runs on every docs-only PR.
"""
from __future__ import annotations

from pathlib import Path

from tortoise import record_projection as rp

ROOT = Path(__file__).resolve().parents[1]

# The two artifacts that state the RECORD's half of the move, and the sentence
# each one carries it on. A wrong anchor here fails loudly (a missing clause),
# never silently.
_DOC_MOVE_ANCHORS = {
    "docs/ONTOLOGY.md": "applies a 2b descriptor as the same MOVE",
    "docs/INGEST_CONTRACT.md": "applies the SAME descriptor as a MOVE",
}

# Epic artifacts that asserted the retired premise (a plain upsert → the row
# never leaves the superseded point). Each carries the corrected pointer, and
# the retired sentence must be GONE — the inverted half is what makes this a
# guard rather than a spelling check.
_DOC_MOVE_POINTERS = {
    "docs/epics/2026-10-10-7879-postgres-record/04-projection.md": {
        "points_at": "CLOSED for the derivable set by #10191",
        "retired": "the record's `DirectEdgeRepoint` is a plain",
    },
    "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md": {
        "points_at": "#10191 moves the derivable set",
        "retired": "is a plain upsert (`record_projection.py`), so `relation` keeps the row",
    },
}


def _structural_repoint(src="p1", tgt="Acme Corp", edge_type="aboutObject",
                        **extra):
    """The 2b descriptor `sdk.supersede_point` journals (no successor field)."""
    return {"type": "DirectEdgeRepoint", "ts": "T",
            "id": f"{src}->p2:{edge_type}:{tgt}",
            "src": src, "tgt": tgt, "target_label": "Object",
            "edge_type": edge_type, **extra}


def _line_with(path: str, anchor: str) -> str:
    """The WHOLE line the anchor sits on (a table row, or one blockquote line)."""
    text = (ROOT / path).read_text(encoding="utf-8")
    at = text.find(anchor)
    assert at != -1, (
        f"{path} no longer says what the RECORD does with a 2b "
        f"`DirectEdgeRepoint` ({anchor!r}) — the gap #10244 closed")
    start = text.rfind("\n", 0, at) + 1
    return text[start:].split("\n", 1)[0]


def test_the_documented_record_side_move_is_the_move_the_record_emits():
    """ONTOLOGY §4.4 and the INGEST supersede row describe the RECORD's half."""
    lines = {path: _line_with(path, anchor)
             for path, anchor in _DOC_MOVE_ANCHORS.items()}

    statements = rp.mutations_for(_structural_repoint(), org_id="o", graph_id="g")
    assert len(statements) == 2, statements             # the move, in two halves
    delete, insert = statements[0].sql, statements[1].sql
    assert "DELETE FROM public.relation" in delete, delete   # first half
    assert "INSERT INTO public.relation" in insert, insert   # second half
    point_arm = rp._structural_target_resolution(
        "o", "g", "Point", "aboutPoint", "N", "")[0]

    for path, line in lines.items():
        # A MOVE spelled out — remove at the superseded point, then create.
        assert "MOVE" in line and "`DELETE`" in line and "INSERT" in line, (path, line)
        # The destination is the record's OWN successor chain (Interface A: the
        # journal alone is sufficient — the descriptor carries no successor).
        assert "superseded_by" in line, (path, line)
        assert "superseded_by" in delete and "WITH RECURSIVE succ" in delete, delete
        assert "no successor field" in line, (path, line)
        assert "new_id" not in delete + insert, (delete, insert)
        # ... and it lands at the chain's TERMINAL row, not at `src`.
        assert "terminal" in line, (path, line)
        assert "succ WHERE next_id IS NULL" in insert, insert
        # The successor's own live value wins (#9417 / §11.1) — never `DO UPDATE`.
        assert "DO NOTHING" in line and "§11.1" in line, (path, line)
        assert insert.rstrip().endswith("DO NOTHING"), insert
        assert "DO UPDATE" not in insert, insert
        # #5509: a name held by two carriers is a refusal, not a pick.
        assert "#5509" in line, (path, line)
        assert "HAVING count(*) = 1" in point_arm, point_arm

    # The full resolution table is stated in the ontology, so it is pinned there:
    # a Point has no `name` column and resolves by `props ->> 'name'` (the
    # property the fold's `MATCH (x:Point {name:$key})` reads); `aboutDocument`
    # additionally needs the kind test, which is load-bearing on this route.
    ontology = lines["docs/ONTOLOGY.md"]
    assert "props->>'name'" in ontology, ontology
    assert "props ->> 'name'" in point_arm, point_arm
    assert "document_kind IS NOT NULL" in ontology, ontology
    source_arm = rp._structural_target_resolution(
        "o", "g", "Source", "aboutDocument", "https://x", "")[0]
    assert "document_kind IS NOT NULL" in source_arm, source_arm
    # Both halves carry the `_src_is_terminal` gate (an id-reuse point that was
    # re-created LIVE owns its edges; moving them clobbers a live point).
    assert "_src_is_terminal" in ontology, ontology
    for sql in (delete, insert):
        assert "(SELECT terminal FROM succ WHERE depth = 1)" in sql, sql


def test_the_epic_artifacts_no_longer_assert_the_retired_upsert_premise():
    """The record route's two epic statements now point at the shipped MOVE.

    Both sentences were present-tense and false for the derivable set since
    #10191, and both were in ``docs/`` — so they reached a lane reading the epic
    as current state and never reached a pin. The assertion is inverted on
    purpose: restoring the retired sentence reds here.
    """
    for path, spec in _DOC_MOVE_POINTERS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert spec["points_at"] in text, (path, spec["points_at"])
        assert spec["retired"] not in text, (path, spec["retired"])
