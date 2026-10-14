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

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ONTOLOGY = REPO / "docs" / "ONTOLOGY.md"


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
