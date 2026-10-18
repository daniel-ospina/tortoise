"""#7818 — the ONTOLOGY §4.7 coverage-boundary record vs the code (doc↔code pin).

Split out of ``tests/test_7818_boundary_ambiguity_contract.py`` so this half can
be registered in ``core`` + ``tier1`` (the ``test_5142_ontology_record.py``
shape). A docs-only ``docs/ONTOLOGY.md`` edit runs *only* tier1, so a pin that
lives inside the docker-lane contract file never runs on the change it exists to
guard. This file reads one doc and asserts text anchors — no fixture, no DB.

The defect it closes was a *record* gap: §4.7 declared no coverage rule for the
query instant at all, so the closed rule the code implements and the half-open
reading its own ``validTo`` prose describes coexisted silently and the next lane
would re-derive one of them as the answer. This pin keeps the record from being
tidied away — a wording change that drops the rule, the shared instant, or the
open-decision pointer reds here.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Deleting any of the named anchors from
      §4.7 — the boundary sentence, the ``#5374`` pin it delegates to, the
      entry-point-dependence disclosure, or the ``#7818`` open-decision pointer.
  (2) *Does the test reach it?*  Yes — the section text is read from the
      committed ONTOLOGY and each anchor is asserted, not paraphrased.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ONTOLOGY = REPO / "docs" / "ONTOLOGY.md"
SDK = REPO / "tortoise" / "sdk.py"

# Any `file.py:NNN` citation. See the rot test at the bottom of this file.
_LINE_CITATION = re.compile(r"\S*\.py:\d+")


def _section_4_7() -> str:
    """The committed §4.7 slice, cut at the next heading."""
    doc = ONTOLOGY.read_text(encoding="utf-8")
    start = doc.index("### §4.7 Temporal Model")
    ends = [doc.find(m, start + 1) for m in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    return doc[start:min(ends)] if ends else doc[start:]


def _method_body(source: str, name: str) -> str:
    """Slice ``def <name>(`` up to the next ``def`` at the same indent."""
    marker = f"def {name}("
    start = source.rindex("\n", 0, source.index(marker)) + 1
    indent = len(source[start:]) - len(source[start:].lstrip())
    sibling = source.find("\n" + " " * indent + "def ", start)
    return source[start:sibling if sibling != -1 else len(source)]


def test_ontology_section_4_7_states_the_coverage_boundary() -> None:
    """ONTOLOGY §4.7 records the boundary rule, its consequence, and the OPEN flag.

    (1) *What value makes this test fail?*  Any of the anchors above; the last
        two single out the OPEN decision — see the comment on the flattened
        phrase below.
    (2) *Does the test reach it?*  Yes — the section is located by its unique
        heading and sliced to the next heading, so an anchor moved OUT of §4.7
        also reds.
    """
    doc = ONTOLOGY.read_text(encoding="utf-8")
    start = doc.index("### §4.7 Temporal Model")
    ends = [doc.find(m, start + 1) for m in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    section = doc[start:min(ends)] if ends else doc[start:]

    assert "coverage boundary is stated here because it was not (#7818)" in section
    # the implemented rule, and the test that pins it
    assert "`_covers`" in section and "`vF <= t <= vT`" in section
    assert "#5374" in section
    # the consequence: one shared instant, and its entry-point dependence
    assert "well-formed contiguous chain" in section.lower()
    assert "entry-point-dependent" in section
    # the half-open alternative is NAMED, not silently dropped
    assert "[vF, vT)" in section
    # and the decision is flagged OPEN at the issue that owns it.
    #
    # Flatten the blockquote wrapping before matching, and pin the PHRASE rather
    # than the bare word ``open``: §4.7 already says "open/unbounded start" in
    # its `validFrom` row, so ``"open" in section`` passes on an edit that flips
    # the decision to DECIDED while keeping the #7818 mention — the exact
    # weakening this assertion exists to prevent.
    flat = " ".join(section.replace(">", " ").split())
    assert "Which is canonical is an **open decision**" in flat, (
        "§4.7 must keep the OPEN-decision sentence — a bare `open` substring "
        "also matches the `validFrom` row's `open/unbounded start` prose")
    assert "tracked at **#7818**" in flat, (
        "the open decision must still name the issue that owns it (#7818)")


def test_ontology_section_4_7_states_the_half_open_zero_length_consequence() -> None:
    """§4.7 records what a half-open `_covers` does to an ALREADY-persisted `[t, t]`.

    The half-open option is the one an owner is being asked to choose, so its
    cheapest-looking form — a one-character predicate change — has to carry the
    consequence that makes it expensive, measured rather than argued: a
    zero-length window is legal today (#5374), it answers ``status="found"``
    under the closed rule, and under ``[vF, vT)`` it answers ``status="absent"``
    with **no** ``malformed`` flag (that detection is strictly inverted). Without
    the record, the next lane prices the flip as a predicate edit and silently
    empties every already-persisted single-instant belief.

    (1) *What value makes this test fail?*  Deleting the zero-length consequence,
        weakening it to a general "a zero-length window is empty" (which loses
        the read-path shapes), or dropping the ``malformed``-stays-silent clause
        — each restores exactly the pricing error this paragraph prevents.
    (2) *Does the test reach it?*  Yes — the anchors are read from the committed
        §4.7 slice, located by its unique heading and cut at the next heading, so
        text moved out of §4.7 reds here too.
    """
    doc = ONTOLOGY.read_text(encoding="utf-8")
    start = doc.index("### §4.7 Temporal Model")
    ends = [doc.find(m, start + 1) for m in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    section = doc[start:min(ends)] if ends else doc[start:]
    flat = " ".join(section.replace(">", " ").split())

    assert "not a predicate-only change" in flat, (
        "§4.7 must say the half-open flip is not a predicate-only edit")
    assert "zero-length `[t, t]` window is already persistable" in flat
    # the two measured read shapes, one per representation
    assert 'status="found"' in flat and 'status="absent"' in flat, (
        "the consequence must name the read-path shapes on both sides of the "
        "flip, not just that the interval becomes empty")
    assert "`malformed` flag stays **silent**" in flat, (
        "#5361's flag cannot report the emptied window — it is not inverted")
    assert "`_inverted` returning `kt < kf`" in flat, (
        "the strictness of _inverted is the whole reason the flag stays silent")
    assert "well-formed-**empty**" in flat, (
        "the flip needs R2's well-formed-empty signal WITH it")


def test_ontology_section_4_7_records_match_the_code_they_cite() -> None:
    """§4.7's two coverage claims are pinned against `sdk.py`, not restated.

    §4.7 tells the next lane that the closed rule is "reject only `vT < t`" and
    that `_inverted` is strict enough to stay silent on a zero-length window.
    Asserting only that *the doc says so* proves the doc agrees with itself; the
    claim that can actually go stale is about the CODE, so this test reads the
    predicates out of ``get_historical_knowledge`` and checks the two operators
    the paragraph's argument rests on.

    (1) *What value makes this test fail?*  Widening either predicate in the
        code — `vt < t` → `vt <= t` (the flip the row prices) or `kt < kf` →
        `kt <= kf` — or moving/renaming either predicate out of the method §4.7
        names.
    (2) *Does the test reach it?*  Yes — the method body is sliced out of the
        committed `tortoise/sdk.py` and each predicate is sliced from it, so the
        operators are read from source rather than from the doc.
    """
    method = _method_body(SDK.read_text(encoding="utf-8"),
                          "get_historical_knowledge")
    assert "def _covers(" in method and "def _inverted(" in method, (
        "§4.7 cites both predicates as living in `get_historical_knowledge`")

    covers = _method_body(method, "_covers")
    assert re.search(r"_created_sort_key\(vt\)\s*<\s*t_key", covers), (
        "the closed rule rejects only `vT < t` — the END bound is inclusive")
    assert not re.search(r"_created_sort_key\(vt\)\s*<=\s*t_key", covers), (
        "§4.7 records a CLOSED rule; a `<=` here is the half-open flip it "
        "prices at R2 of the #5362 plan, which is still an OPEN decision")

    inverted = _method_body(method, "_inverted")
    assert re.search(r"return\s+kt\s*<\s*kf", inverted), (
        "_inverted is strict, so a zero-length `[t, t]` is not inverted")
    assert not re.search(r"return\s+kt\s*<=\s*kf", inverted), (
        "a non-strict `_inverted` would flag every zero-length window as "
        "malformed, contradicting §4.7's 'the flag stays silent' consequence")


def test_ontology_section_4_7_cites_by_symbol_never_by_line() -> None:
    """§4.7 carries no `file.py:NNN` citation — a line number here rots silently.

    Both citations this section used to carry went stale the same way: §4.7
    cited `_inverted` at `sdk.py:19221`, the #8337 rename moved it to `:25167`,
    and merging `origin/main` (#8487, +291 lines in the same file) moved it
    again to `:25458` — while `hosted_api.py:9461-9484`, cited for the hosted
    commit path's `validFrom` write, no longer contains that statement at all.
    A reader cannot tell a rotted pointer from a current one, so the section
    names symbols (`_covers`, `_inverted`, `tortoise/hosted_api.py`) and this
    test keeps a line number from coming back.

    (1) *What value makes this test fail?*  Re-adding any `file.py:NNN` — e.g.
        the pre-fix `sdk.py:25094`, `sdk.py:25167` or `hosted_api.py:9461-9484`
        — to §4.7.
    (2) *Does the test reach it?*  Yes — the pattern is applied to the whole
        committed §4.7 slice, so a citation added anywhere in the section reds.
    """
    stale = _LINE_CITATION.findall(_section_4_7())
    assert stale == [], (
        "§4.7 cites code by line number; these go stale on the next rename or "
        f"merge of the same file: {stale}. Cite the symbol instead")
