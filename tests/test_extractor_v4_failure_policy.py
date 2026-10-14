"""#8495 — every v4 step must keep a DECLARED failure policy.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.3 recorded gap **G3**: only
two of the eight steps (S2.2 *"fail-open — keep"*, S2.3 *"never raises"*) said
what they do when they fail. The unstated default is not neutral — for S3 the
natural fail-open marks every candidate `new`, which **manufactures duplicates**,
the exact failure S3 exists to prevent. The shipped renderer still invites it:
`_render_search_results` tells the model *"no existing items available; assume
everything is new"* (`extractor_v2.py:2353`).

The repair DECLARES the policy per step, with three invariants (I1: an
unresolved identity is never asserted `new`; I2: a failure holds or records, it
never silently drops; I3: a committed run is all-or-nothing and the journal is
the truth). It is a SPEC change, not a description of what runs — so the doc
must say so, and that claim must stay true.

WHAT THIS PINS
--------------
1. §4.4 exists, carries a row per canonical step, and every row names a
   direction (`FAIL-OPEN` or `FAIL-CLOSED`) — a step added to §4.0's canonical
   set reds until its policy is declared.
2. The policy rows cover EXACTLY the canonical set (no policy invented for a
   step that does not exist).
3. The three invariants and the re-narrate tie-break are present.
4. `HOLD` is declared a run-level disposition and NOT a new `vet_gate.OUTCOMES`
   member — cross-checked against the shipped module, so adding one without
   updating the doc reds.
5. The spec-change warning stays TRUE: while `extractor_v2.py` still says
   "assume everything is new", the doc must keep saying the code implements the
   inverted policy. Fix the code and this reds, forcing the doc to follow.
6. Every `file.py:NNNN` citation in §4.4 names its revision pin. The header
   pins every `file.py:NNNN` to `c79ba1cf2` *except where a passage names a later
   pin*; `extractor_v2.py:2353` is a `7d348d351` line (2042 at the default pin),
   so a bare number here reads as the wrong revision and the guard reds
   (#8600 finding 1).

Non-vacuity: the pre-fix document has no `### 4.4` heading, so check 1 fails.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
EXTRACTOR = ROOT / "tortoise" / "extractor_v2.py"
VET_GATE = ROOT / "tortoise" / "vet_gate.py"

POLICY_HEADING = "### 4.4 Per-step FAILURE POLICY"
#: §4.0's declaration of the canonical top-level set. The sentence is bolded, so
#: it is bounded by the closing `**` — the rest of the paragraph also spells
#: `(S1–S8)`, which must not leak into the parsed set.
CANONICAL_ANCHOR = "EIGHT steps:"
STEP_ID = re.compile(r"S\d+(?:\.\d+)?")
#: A §4.4 policy-table row: `| **S3 RESOLVE** — the lookup *(mechanical)* | …`.
POLICY_ROW = re.compile(r"^\|\s*\*\*(S\d+(?:\.\d+)?)")
#: The document's header (line 16) pins every `file.py:NNNN` reference to a base
#: revision, *"except where a passage names a later pin"*. A citation that is
#: not one of the header's named exceptions therefore reads against the base
#: pin — so a section written at HEAD must name its own (#8600 finding 1).
PIN_HEADER_MARKER = "Code citations — revision pin"
BASE_PIN = "c79ba1cf2"
_PIN_TOKEN = re.compile(r"`([0-9a-f]{7,40})`")
#: `file.py:NNNN` — the citation form the header's rule governs.
_CODE_CITATION = re.compile(r"`?[A-Za-z0-9_./-]+\.py:\d+`?")


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _policy_section() -> str:
    text = _document()
    assert POLICY_HEADING in text, (
        f"§4.4 ({POLICY_HEADING!r}) is gone from {DOC.name} — the per-step "
        "failure policy declared by #8495 no longer exists."
    )
    start = text.index(POLICY_HEADING)
    end = text.index("\n## 5.", start)
    return text[start:end]


def _canonical_steps() -> list[str]:
    text = _document()
    assert CANONICAL_ANCHOR in text, f"{CANONICAL_ANCHOR!r} not found in {DOC.name}"
    after = text.split(CANONICAL_ANCHOR, 1)[1]
    sentence = after.split("**", 1)[0]
    return STEP_ID.findall(sentence)


def _policy_rows() -> list[tuple[str, str]]:
    section = _policy_section()
    rows: list[tuple[str, str]] = []
    for line in section.splitlines():
        match = POLICY_ROW.match(line)
        if match:
            rows.append((match.group(1), line))
    return rows


def test_the_failure_policy_section_exists_and_is_populated():
    """Check 1's non-vacuity half — the pre-fix doc reds here."""
    steps = _canonical_steps()
    assert len(steps) >= 8, f"canonical step set parsed as {steps!r}"
    rows = _policy_rows()
    assert len(rows) >= 8, (
        "§4.4 declares fewer policy rows than the canonical step set — a step "
        f"has no stated failure policy ({[i for i, _ in rows]!r})"
    )


def test_every_canonical_step_has_a_declared_failure_policy():
    """A step added to §4.0 reds until §4.4 declares its direction."""
    covered = {step for step, _ in _policy_rows()}
    missing = [s for s in _canonical_steps() if s not in covered]
    assert not missing, (
        f"§4.0's canonical steps {missing} have no §4.4 policy row. Every step "
        "must state fail-open or fail-closed (G3, #8495)."
    )


def test_the_policy_rows_cover_exactly_the_canonical_set():
    """No policy invented for a step that does not exist."""
    declared = {step for step, _ in _policy_rows()}
    canonical = set(_canonical_steps())
    extra = sorted(declared - canonical)
    assert not extra, (
        f"§4.4 declares a failure policy for non-step(s) {extra} — add them to "
        "§4.0's canonical list or remove the row (#8495)."
    )


def test_every_policy_row_states_a_direction():
    """A row without a direction is the G3 defect repeated."""
    undirected = [
        row for _, row in _policy_rows()
        if "FAIL-OPEN" not in row and "FAIL-CLOSED" not in row
    ]
    assert not undirected, (
        "these §4.4 rows state no fail-open / fail-closed direction:\n  - "
        + "\n  - ".join(undirected)
    )


def test_the_three_invariants_are_declared():
    section = _policy_section()
    for invariant in ("**I1**", "**I2**", "**I3**"):
        assert invariant in section, f"§4.4 lost invariant {invariant}"
    assert "NEVER asserted `new`" in section, (
        "I1 — the invariant that an unresolved identity is never asserted `new` "
        "— is the load-bearing half of the S3 policy (#8495)."
    )


def test_the_renarrate_tiebreak_is_deterministic():
    section = _policy_section()
    assert "Keep the FIRST narrative unless the second is STRICTLY better" in section, (
        "the re-narrate tie-break is gone — without it, which narrative survives "
        "a worse second reading is unspecified (#8495)."
    )
    assert "A tie keeps the FIRST." in section, "the tie case itself must be stated"


def test_hold_is_not_a_new_vet_outcome_and_the_doc_says_so():
    section = _policy_section()
    assert "NOT a member of `vet_gate.OUTCOMES`" in section, (
        "§4.4 must declare that `HOLD` is a run-level disposition, not a new "
        "shipped VET outcome (#8495)."
    )
    # Cross-check against the shipped vocabulary: if `HOLD` is ever added to
    # `OUTCOMES`, the doc's claim is false and this reds.
    source = VET_GATE.read_text(encoding="utf-8")
    marker = "OUTCOMES = frozenset({"
    assert marker in source, f"{marker!r} not found in {VET_GATE.name}"
    body = source[source.index(marker):]
    body = body[: body.index("})")]
    assert "HOLD" not in body, (
        "`HOLD` has been added to the shipped `vet_gate.OUTCOMES` — §4.4 still "
        "claims it is deliberately absent; reconcile the doc and the module "
        "(#8495)."
    )


def test_the_shipped_code_still_matches_the_documented_warning():
    """The spec-change warning must not outlive the inverted behaviour.

    §4.4 says the shipped renderer invites *"assume everything is new"*. If the
    code is fixed, this reds and the doc must stop claiming the inverted policy
    runs.
    """
    section = _policy_section()
    assert "assume everything is new" in section, (
        "§4.4 no longer quotes the shipped renderer's inverted instruction (#8495)."
    )
    assert "assume everything is new" in EXTRACTOR.read_text(encoding="utf-8"), (
        "`tortoise/extractor_v2.py` no longer says 'assume everything is new' — "
        "the §4.4 state-of-code warning is now stale; update it to describe what "
        "the code does instead (#8495)."
    )


def _declared_later_pins() -> set[str]:
    """The revisions the header names as exceptions to its base pin."""
    for line in _document().splitlines():
        if PIN_HEADER_MARKER in line:
            return set(_PIN_TOKEN.findall(line)) - {BASE_PIN}
    raise AssertionError(
        f"the header's revision-pin rule ({PIN_HEADER_MARKER!r}) is gone from "
        f"{DOC.name}; the revisions this guard resolves against cannot be read"
    )


def test_the_state_of_code_citation_names_its_revision_pin():
    """P2 (#8600): the §4.4 citation must not be a bare `file.py:NNNN`.

    The header pins every `file.py:NNNN` in this document to `c79ba1cf2`
    *except where a passage names a later pin*. `extractor_v2.py:2353` is the
    `7d348d351` (current-HEAD) line; at the declared default pin `c79ba1cf2`
    the same symbol sits at 2042. A bare number therefore reads as the wrong
    revision — the exact ambiguity `#8506` added the third-pin clause to kill.

    The pin is resolved against the header's OWN declared pins and bound to the
    LINE carrying the citation, so it cannot be satisfied by a pin mentioned
    elsewhere in the section, and a second unpinned citation in §4.4 also reds.
    """
    section = _policy_section()
    assert "_render_search_results" in section, (
        "§4.4 no longer cites `_render_search_results`; the state-of-code "
        "warning in this section depends on it (#8600)."
    )

    later = _declared_later_pins()
    assert later, (
        "the header names no later pin — this guard would pass vacuously; the "
        "header's pin vocabulary changed and needs re-reading (#8600)."
    )
    cited = [line for line in section.splitlines() if _CODE_CITATION.search(line)]
    assert cited, (
        "§4.4 carries no `file.py:NNNN` citation, so this guard is vacuous — "
        "re-point it at whichever passage now carries a line number (#8600)."
    )
    unpinned = [
        line.strip()[:160]
        for line in cited
        if not any(f"`{pin}`" in line for pin in later)
    ]
    assert not unpinned, (
        "these §4.4 lines cite a `file.py:NNNN` without naming the revision the "
        f"number holds at. The header reads an unnamed number against its base "
        f"pin (`{BASE_PIN}`), where §4.4's HEAD numbers are wrong — name the pin "
        "(`at `7d348d351``) in the passage or add the citation to the header's "
        "`7d348d351` list (#8600):\n  - " + "\n  - ".join(unpinned)
    )
