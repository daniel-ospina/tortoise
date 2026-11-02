"""#10524 review ISSUE 3 (P3) — ONE declaration for "what an ABSENT status reports".

The #9974 R1 change made an absent ``status`` the ordinary birth state of an
``:Object``/``:Subject``, so the reference fold's status leg has to compare the
**effective** value (absent READS live) or every status-less birth is reported
as ``divergence="content"`` on a faithful journal.

The first draft spelled that rule INLINE in ``consistency._compare_entities``:

    if (gv if gv is not None else "live") != jv:

while the same PR introduced the one declaration for exactly that question —
``live.status_as_reported`` (with ``STATUS_CARRYING_EPISTEMIC_LABELS``, the
``:Event`` carve-out it exists to carry). Two spellings of one rule that are
**not equivalent in general**: the declaration returns ``""`` for a label whose
layer has no ``status`` family, the inline copy returns ``"live"``
unconditionally. No live bug TODAY only because the leg is bounded to
``_STATUS_COMPARED_LABELS`` = ``{Object, Subject}``, where the two agree — so
the next label widened into that set would silently take the wrong arm of
whichever copy it hit, and a durability verifier taking the wrong arm is a
false GREEN.

⛔ The defect is the MISSING ROUTING, so the pin has to be about routing, not
about a coincidence the two spellings happen to share. ``test_the_status_leg_
routes_through_the_shared_declaration`` is the load-bearing one: it swaps the
declaration for a recording function and fails on ANY code path that does not
call it. The behavioural leg below is the reason routing matters — it is the
case where the two spellings disagree.

Non-vacuity, both legs, against the pre-fix code:
  * ``…routes_through_the_shared_declaration`` — pre-fix the leg never calls the
    helper, so the spy records nothing and the assertion reds.
  * ``…a_label_whose_layer_has_no_status_is_not_read_as_live`` — pre-fix the
    inline copy yields ``"live" == jv``, so ``count == 0`` and the assertion
    reds. It is deliberately a monkeypatched widening of the compared set: the
    hazard the finding names is the NEXT label, and a pin that only re-stated
    today's ``{Object, Subject}`` set could not see it.
  * ``…an_absent_Object_status_still_reads_live`` is the anti-vacuity control —
    without it a fix that compared the RAW value would pass both legs above and
    re-break #9974 R1.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise import consistency


def _rows(*, label: str, journal_status, graph_status):
    """One journal entity and its graph twin, as the comparison sees them."""
    journal = {(label, "eid-1"): {"id": "eid-1", "name": "n1"}}
    if journal_status is not None:
        journal[(label, "eid-1")]["status"] = journal_status
    graph = {(label, "eid-1"): {}}
    if graph_status is not None:
        graph[(label, "eid-1")]["status"] = graph_status
    return journal, graph


def test_the_status_leg_routes_through_the_shared_declaration(monkeypatch):
    """The leg must ASK ``live.status_as_reported``, not re-type its rule.

    Pre-fix ``_compare_entities`` inlined ``(gv if gv is not None else "live")``
    and never called the declaration, so nothing here is recorded and this reds.
    The spy returns the real answer, so the behaviour of the comparison is
    unchanged — what is pinned is that the ONE declaration is the one consulted.
    """
    calls = []
    real = consistency.status_as_reported

    def spy(value, *, label=None):
        calls.append((value, label))
        return real(value, label=label)

    monkeypatch.setattr(consistency, "status_as_reported", spy)

    journal, graph = _rows(label="Object", journal_status="live", graph_status=None)
    mismatches, count, _ = consistency._compare_entities(journal, graph, set(), set())

    assert calls == [(None, "Object")], (
        "the entity-parity status leg did not consult `live.status_as_reported` "
        f"(spy saw {calls!r}) — it is re-typing the absent-reads-live rule inline "
        "again (#10524 review ISSUE 3)")
    assert count == 0, (
        "an absent graph status against a journal `live` must not be a "
        f"divergence (#9974 R1); got {mismatches!r}")


def test_a_label_whose_layer_has_no_status_is_not_read_as_live(monkeypatch):
    """The two spellings DISAGREE here — which is why there must be only one.

    ``:Event`` carries ``eventStatus``, so ``status_as_reported(None,
    label="Event")`` reports ``""`` ("this layer exposes no ``status``"), never
    ``live``. A widened compared set therefore has to see the divergence; the
    pre-fix inline copy reported ``live`` and silently passed.
    """
    monkeypatch.setattr(
        consistency, "_STATUS_COMPARED_LABELS", frozenset({"Object", "Subject", "Event"}))

    journal, graph = _rows(label="Event", journal_status="live", graph_status=None)
    mismatches, count, _ = consistency._compare_entities(journal, graph, set(), set())

    assert count == 1, (
        "a journal `live` against an ABSENT :Event status must be reported — "
        "an :Event has no `status` family, so `\"\"` is not `\"live\"`; the "
        f"inline copy said live and passed vacuously. got {mismatches!r}")
    assert mismatches[0]["field"] == "status", mismatches


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_an_absent_Object_status_still_reads_live(label):
    """Anti-vacuity: the #9974 R1 direction must survive the re-routing.

    ``status_as_reported`` is only correct as a replacement if it still treats
    an absent status as LIVE on the two families that actually own the field.
    A "fix" that compared the RAW ``gv`` would pass the routing test and red
    this one.
    """
    journal, graph = _rows(label=label, journal_status="live", graph_status=None)
    _, count, _ = consistency._compare_entities(journal, graph, set(), set())
    assert count == 0, (
        f"an absent :{label} status must read live against a journal `live` "
        "(#9974 R1); the effective value is not being compared")

    # …and a genuinely buried (out-of-band terminal) status is still caught.
    journal, graph = _rows(label=label, journal_status="live", graph_status="archived")
    _, count, _ = consistency._compare_entities(journal, graph, set(), set())
    assert count == 1, (
        f"a :{label} buried behind an `archived` status is the failure this leg "
        "exists for; it must still be reported")


# --- the changelog's consumer pointer (review at `22a448f3d`, ISSUE 1 P3) -----
#
# The v3.31 build record publishes a consumer TRAP (a literal `status:'live'`
# filter now drops every status-less birth) and closes it with a scope carve-out
# pointing at the Point idiom: "`MATCH (p:Point {status:'live'})` idiom printed
# in §4.3 remains correct as written." The claim was true and the SECTION was
# wrong — the idiom is printed in §5's `Point Status Vocabulary`. A lane
# following that pointer lands in §4.3 (`### §4.3 Object`), reads the
# pre-change `ObjectRegistered→live` cell, and does not find the Filter it was
# told was there — the exact failure the preceding paragraph exists to prevent.
#
# Non-vacuity, against the pre-fix text (both legs red on it):
#   * the section leg — pre-fix named `§4.3`, whose span is §4's (:385–:956),
#     while the idiom is at :1149 inside §5 and that assertion reds;
#   * the line leg — pre-fix carried no `ONTOLOGY.md:NNNN` citation at all.

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_CHANGELOG = _DOCS / "ONTOLOGY-CHANGELOG.md"
_ONTOLOGY = _DOCS / "ONTOLOGY.md"
_POINT_IDIOM = "MATCH (p:Point {status:'live'})"


def _pointer_text() -> str:
    """The changelog passage that tells a consumer where the Point idiom lives.

    Taken as a window from the idiom forward, because the sentence wraps: the
    section name and the `ONTOLOGY.md:NNNN` citation can sit on either side of
    the line break.
    """
    text = _CHANGELOG.read_text(encoding="utf-8")
    start = text.index(_POINT_IDIOM)
    window = text[start:start + 400]
    assert "idiom printed in" in window, (
        "the v3.31 block no longer points a consumer at the Point idiom — this "
        "pin has lost its subject, re-anchor it rather than deleting it")
    return window


def _section_span(number: str) -> range:
    """Line numbers (1-based) covered by `## §<number>` up to the next `## §`."""
    lines = _ONTOLOGY.read_text(encoding="utf-8").splitlines()
    head_pat = re.compile(r"^## §(\d+)\.")
    headings = [(i + 1, m.group(1)) for i, line in enumerate(lines)
                if (m := head_pat.match(line))]
    for idx, (line_no, sec) in enumerate(headings):
        if sec != number:
            continue
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines) + 1
        return range(line_no, end)
    raise AssertionError(f"`docs/ONTOLOGY.md` has no `## §{number}` section")


def test_the_point_idiom_pointer_names_the_section_that_prints_it():
    """The named § must actually CONTAIN the idiom it points at.

    Section numbers drift; the claim that matters is "go here and you will find
    it". Pre-fix this named §4.3 and red; §5 is where `:1149` lives.
    """
    window = _pointer_text()
    named = re.search(r"§(\d+(?:\.\d+)?)", window)
    assert named, f"the Point-idiom pointer names no section: {window[:160]!r}"
    section = named.group(1)

    idiom_lines = [
        i + 1 for i, line in enumerate(_ONTOLOGY.read_text(encoding="utf-8").splitlines())
        if _POINT_IDIOM in line
    ]
    assert idiom_lines, (
        "`docs/ONTOLOGY.md` no longer prints the Point idiom — the changelog's "
        "carve-out claims it still does")

    span = _section_span(section.split(".")[0])
    in_section = [n for n in idiom_lines if n in span]
    assert in_section, (
        f"the changelog points a consumer at §{section} for the Point idiom, but "
        f"`ONTOLOGY.md` prints it at {idiom_lines} — outside §{section.split('.')[0]} "
        f"({span.start}–{span.stop - 1}). A lane following the pointer lands in the "
        "wrong section and does not find it (#10524 review ISSUE 1)")


def test_the_point_idiom_pointer_line_number_lands_on_the_idiom():
    """A `ONTOLOGY.md:NNNN` citation must resolve to a line carrying the idiom.

    The line number is what makes the pointer actionable; pre-fix the sentence
    cited no line at all, and a later edit can silently move it.
    """
    window = _pointer_text()
    cited = re.search(r"ONTOLOGY\.md:(\d+)", window)
    assert cited, (
        "the Point-idiom pointer carries no `ONTOLOGY.md:NNNN` citation, so the "
        f"consumer has a section number and no landmark: {window[:200]!r}")

    line_no = int(cited.group(1))
    lines = _ONTOLOGY.read_text(encoding="utf-8").splitlines()
    assert 1 <= line_no <= len(lines), (
        f"the changelog cites `ONTOLOGY.md:{line_no}`, but the file has "
        f"{len(lines)} lines")
    assert _POINT_IDIOM in lines[line_no - 1], (
        f"the changelog cites `ONTOLOGY.md:{line_no}` for the Point idiom, but "
        f"that line reads {lines[line_no - 1].strip()!r} — the landmark moved "
        "and the pointer was not updated with it")
