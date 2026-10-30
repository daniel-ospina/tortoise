"""#10191 — the RECORD half of a 2b repoint is a MOVE: the docs say it, the plan does it.

A NEW module, not a file moved out of ``tests/test_record_projection.py`` — that
module gains a comment that points here and nothing else (0 deletions), because
the doc↔code half had to run where that module cannot. Registered in ``core`` +
``tier1`` (the ``test_7818_ontology_record.py`` / ``test_5142_ontology_record.py``
split shape). The reason for the second registration is measured, not stylistic:
a ``docs/INGEST_CONTRACT.md``-only diff matches no ``SOURCE_PATTERNS`` entry
(``docs/`` is in ``NON_PYTHON_PREFIXES``), so the selector returns
``surfaces: []`` and runs **tier1 only**. ``test_record_projection.py`` is
registered under ``core`` alone, so the INGEST half of this pin *slept on the one
edit it exists to catch* — the #8474 shape, whose ``test_8474_ontology_record.py``
row in ``config/ci-surfaces.yml`` records the same split for the same reason.
``docs/ONTOLOGY.md`` is an explicit selector
entry, which is why that half fired while this one did not.

WHAT THIS PINS. ``#10191`` landed the record-side half of a supersede's
structural repoint (a ``DELETE`` at the superseded point + an ``INSERT`` at
the hop the ``superseded_by`` chain names, one hop per descriptor, so the chain's
TERMINAL row is where the row ends up once every hop has run) and left both artifacts
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
retired sentence must be absent — plus ``_NON_DERIVABLE_CLAIM``, the corrected
statement of where an *unmoved* row sits, whose false predecessor (“the record
holds those rels at neither endpoint”) is asserted ABSENT. That sentence was the
#10244 review's P1: it inverted a true claim, and a lane scoping #9836 would
have shipped the ``INSERT``-without-``DELETE`` fix the same file forbids.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Deleting or rewording any clause below
      from §3.1 / the supersede row; a plan that stops emitting the two halves,
      the chain CTE, the one-hop landing, or ``DO NOTHING``; or a doc that goes
      back to claiming the record upserts in place, or that an unmoved
      non-derivable rel sits at *neither* endpoint.
  (2) *Does the test reach it?*  Yes — the docs are read from the tree and the
      plan is emitted by the module the docs describe, not paraphrased here.

DB-free and fixture-free on purpose: it must be cheap enough for ``tier1``,
which runs on every docs-only PR.
"""
from __future__ import annotations

import re
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
# guard rather than a spelling check. ``points_at`` is the corrected CLOSURE
# pointer; ``keeps_row_at`` is the corrected statement of where a row that is NOT
# moved still sits (the #10244 review's P1), and ``_NON_DERIVABLE_CLAIM`` is its
# false predecessor.
_DOC_MOVE_POINTERS = {
    "docs/epics/2026-10-10-7879-postgres-record/04-projection.md": {
        "points_at": "CLOSED for the derivable set by #10191",
        "keeps_row_at": "the record keeps the row at the pre-supersede point",
        "retired": "the record's `DirectEdgeRepoint` is a plain",
    },
    "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md": {
        "points_at": "CLOSED for the derivable set by #10191",
        "keeps_row_at": "the record keeps the row at the pre-supersede point",
        "retired": "is a plain upsert (`record_projection.py`), so `relation` keeps the row",
    },
}

#: The #10244 review's P1, verbatim. It claimed a non-derivable rel sits at
#: NEITHER endpoint after a supersede; measured, an addressable
#: `wasDerivedFrom`/`aboutAction` is journaled as a `DirectEdgeCreated`
#: (`RECORD_CARRIED_STRUCTURAL_RELS`) and 2b journals no repoint for it, so the
#: row STAYS at the pre-supersede point. A doc asserting the opposite sends a
#: lane scoping #9836 to add an INSERT at the successor without the DELETE.
_NON_DERIVABLE_CLAIM = "at neither endpoint"


def _structural_repoint(src="p1", tgt="Acme Corp", edge_type="aboutObject",
                        **extra):
    """The 2b descriptor `sdk.supersede_point` journals (no successor field)."""
    return {"type": "DirectEdgeRepoint", "ts": "T",
            "id": f"{src}->p2:{edge_type}:{tgt}",
            "src": src, "tgt": tgt, "target_label": "Object",
            "edge_type": edge_type, **extra}


#: The document both artifacts cite for the precedence half, and the ruling it
#: must land on. `#10244`'s review (P2) measured the failure this deriver exists
#: to make impossible: the first fix froze a bare `§11.1`, which resolves to the
#: edge-cost CENSUS (no `#9417`, no PRECEDENCE clause) — and because the assert
#: was presence-only, the pin certified the wrong section and no edit could red
#: it. So the section number is DERIVED from the document here and compared to
#: the citation: a doc edit that moves the ruling, or a citation edited to point
#: elsewhere, both fail.
_STORAGE = "docs/architecture/STORAGE-ARCHITECTURE.md"
_PRECEDENCE_RULING = "PRECEDENCE — the successor's own live value WINS"


def _section_stating_the_precedence_ruling() -> str:
    """The `## N.` section of STORAGE-ARCHITECTURE.md that states #9417's rule."""
    text = (ROOT / _STORAGE).read_text(encoding="utf-8")
    at = text.find(_PRECEDENCE_RULING)
    assert at != -1, (
        f"{_STORAGE} no longer states the #9417 ruling the record's `DO NOTHING` "
        f"rests on ({_PRECEDENCE_RULING!r}) — re-derive the citation before "
        "freezing a pointer at it")
    headings = list(re.finditer(r"(?m)^## (\d+)\.", text[:at]))
    assert headings, f"{_STORAGE} has no numbered `## N.` section before the ruling"
    return headings[-1].group(1)


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
    """ONTOLOGY §3.1 and the INGEST supersede row describe the RECORD's half."""
    lines = {path: _line_with(path, anchor)
             for path, anchor in _DOC_MOVE_ANCHORS.items()}
    ruling_section = _section_stating_the_precedence_ruling()

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
        # ... and it lands at the successor `superseded_by` NAMES, one hop per
        # descriptor (`#10139` review round 4) — which is what makes a chain
        # reach its TERMINAL row only as each hop's descriptor runs, and is the
        # same hop the graph's replay takes (`succ[src]`, not `_final(src)`).
        # Resolving straight to the terminal row collapses a chain's
        # descriptors onto one destination, where `DO NOTHING` then keeps the
        # FIRST — right for two independent retired points, wrong for a chain.
        assert "one hop per descriptor" in line.lower() or "one hop" in line, (path, line)
        assert "terminal" in line, (path, line)
        assert "succ WHERE depth = 2" in insert, insert
        assert "next_id IS NULL" not in insert, insert
        # The successor's own live value wins (#9417) — never `DO UPDATE`.
        # CITED WITH ITS DOCUMENT **and its section derived from the rule**: the
        # bare `§11.1` resolved to INGEST's own "11.1 Derived liveness", and to
        # ONTOLOGY §11 (Reputation); with the filename added it resolved to the
        # edge-cost census, which states neither `#9417` nor the precedence rule
        # (#10244 review P2). A presence-only assert cannot tell those apart, so
        # the section number is computed above and compared here.
        assert "DO NOTHING" in line, (path, line)
        assert f"`STORAGE-ARCHITECTURE.md` §{ruling_section}" in line, (
            path, line, f"the ruling is in §{ruling_section}")
        assert "#9417" in line, (path, line)
        # ... and the carry it rests on is `ON CREATE`, not a bare `SET` (#9417).
        # The pre-#9417 wording ("the graph's transfer is a bare `MERGE`") names
        # the RETIRED code, so it is pinned out rather than merely unpinned.
        assert "ON CREATE" in line, (path, line)
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
    # The linear-vs-cyclic qualifier must NAME its residual where that residual
    # lives. The bare "residual (b) above" had no referent in ONTOLOGY at all, and
    # at its likely intended home (04-projection) (b) is the CLOSED item — a
    # dangling pointer that sent a reader to a closed residual (#10244 review P2).
    assert "residual (b)" not in ontology, ontology
    # ... and the cycle claim is now SCOPED to the shape BOTH routes handle
    # (#10139 review round 5). "Both walks are path-guarded, so both stop at the
    # same hop" was FALSE for a cycle carrying a descriptor per hop: pass-2b's
    # `_final` returns the first REPEATED node, so `_final(src) == src` SKIPS the
    # create after that descriptor's delete-leg already removed the previous
    # hop's edge — measured, the rebuilt graph keeps NO edge, while the record's
    # path-guarded walk ends at the LAST hop's successor. The over-claim is
    # pinned ABSENT and the scoped statement pinned PRESENT, so a doc that goes
    # back to calling cycles non-divergent reds here.
    assert "cyclic" in ontology.lower(), ontology
    assert "no longer a divergence" not in ontology, ontology
    assert "descriptor per hop" in ontology, ontology
    assert "residual (c)" not in ontology, ontology


def test_the_epic_artifacts_no_longer_assert_the_retired_upsert_premise():
    """The record route's two epic statements now point at the shipped MOVE.

    Both sentences were present-tense and false for the derivable set since
    #10191, and both were in ``docs/`` — so they reached a lane reading the epic
    as current state and never reached a pin. The assertion is inverted on
    purpose: restoring the retired sentence reds here.

    The same inversion covers the P1 the #10244 review found: a *non-derivable*
    rel is NOT at "neither endpoint" — it is journaled as a `DirectEdgeCreated`
    and never moved, so the row stays at the pre-supersede point. Restoring that
    false claim reds here too, which is the whole point: it is the claim a lane
    scoping #9836 would act on.
    """
    for path, spec in _DOC_MOVE_POINTERS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert spec["points_at"] in text, (path, spec["points_at"])
        assert spec["keeps_row_at"] in text, (path, spec["keeps_row_at"])
        assert spec["retired"] not in text, (path, spec["retired"])
        assert _NON_DERIVABLE_CLAIM not in text, (path, _NON_DERIVABLE_CLAIM)
