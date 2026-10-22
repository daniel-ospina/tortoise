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
#: The changelog that records WHY a pin was added and what its correction was —
#: the third place this claim is written down (#9654 review round 2).
CHANGELOG = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md"
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
#: The pin's evidence says how many cited files are OUT OF RANGE at the base pin
#: (`five of the cited files are shorter at `c79ba1cf2``). The count and the
#: enumeration beside it must agree — see
#: `test_the_pin_evidence_counts_exactly_the_files_it_names` (#9654 review P2).
_SHORTER_COUNT_CLAIM = re.compile(
    r"(?P<count>[a-z]+)\s+of\s+(?:the|these)\s+(?:cited\s+)?files\s+are\s+shorter",
    re.I)
#: The number words that claim uses, so the guard can compare against a list.
_COUNT_WORDS = {word: value for value, word in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven",
     "eight", "nine", "ten"])}
#: A backticked `*.py` name — how the enumeration spells the files.
_ENUMERATED_PY = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
#: `file.py:NNNN` — the citation form the header's rule governs.
_CODE_CITATION = re.compile(r"`?[A-Za-z0-9_./-]+\.py:\d+`?")
#: `§4.N` followed on the SAME line by a failure direction — a pointer that
#: attributes a policy to a section BY NUMBER. §4.4 is the only section that
#: declares one (G3's §4.3 row says *"declared in §4.4"*, and §4.4's heading is
#: `Per-step FAILURE POLICY`), so a pointer to another number sends a reader to a
#: section that holds an ordering argument and no rule at all. Bounded to one
#: line and stopped at `)`/`|` so a wrapping paragraph cannot manufacture a hit.
_POLICY_POINTER = re.compile(r"§\s*(4\.\d+)[^)\n|]{0,80}(fail-open|fail-closed)")
#: §4.1 declares the canonical top-level count as **EIGHT** and forbids writing
#: "seven" (§4.0, §4.1 and §4.2 must agree). A passage that names how many
#: steps an inherited rule covers must therefore use the document's own number:
#: a second count in the same file is what the drift marker exists to stop, and
#: an ordinal derived from it ("not a seventh declaration") is wrong in the same
#: direction. Matched against the passage with markdown emphasis stripped, so
#: `**eight**` and `eight` are read alike.
_NON_CANONICAL_STEP_COUNT = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|nine|ten|eleven)\s+"
    r"(?:[A-Za-z-]+\s+)?(?:pipeline\s+)?steps?\b",
    re.IGNORECASE,
)
#: An ordinal count of DECLARATIONS: with EIGHT steps in §4.4, one more rule is a
#: NINTH declaration — not a seventh.
_NON_CANONICAL_ORDINAL = re.compile(
    r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|tenth|eleventh)"
    r"\s+declaration\b",
    re.IGNORECASE,
)
#: The canonical count, stated in the passage that cites it.
_CANONICAL_STEP_COUNT = re.compile(
    r"eight\s+(?:[A-Za-z-]+\s+)?(?:pipeline\s+)?steps", re.IGNORECASE
)
#: The section that DECLARES per-step failure policy (the pointer must name it).
FAILURE_POLICY_SECTION = "4.4"
#: §4.3's relocation subsection (`#5333`), bounded by the next `###` heading.
G5_DESTRUCTIVE_HEADING = "#### G5's destructive half"
G5_DESTRUCTIVE_END = "### S3 — RESOLVE"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _changelog() -> str:
    """The changelog beside the doc — the record of WHY a pin was added."""
    assert CHANGELOG.exists(), (
        f"{CHANGELOG.name} is gone — the pin's stated evidence and its "
        "correction now have no recorded home (#5333)."
    )
    return CHANGELOG.read_text(encoding="utf-8")


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


# ── a failure policy must be POINTED AT where it is declared (#5333 review) ──


def test_the_pin_evidence_counts_exactly_the_files_it_names():
    """P2 (review of #9654): the stated count must match the enumeration.

    BOTH statements of the pin's evidence — the header's fourth-pin clause and
    the subsection's own pin line — say how many cited files are shorter at
    `c79ba1cf2`. The pre-fix header said "three" and named no file at all, and
    the pin test's own docstring said "three" while naming four (`sdk.py`,
    `assembly.py`, `projection/entities.py`, `projection/__init__.py`) and
    omitting `commit_ops.py`, which the subsection cites at `:51`, `:59` and
    `:1408` and whose vocabulary the whole passage rests on: a lane refreshing
    the pin would re-check the named files and leave it silently unverified.

    This guards INTERNAL CONSISTENCY, not the measured truth (five was measured
    at both refs when the pin was added) — the count a reader can check against
    the list beside it is the cheapest falsifier, and a new citation has to
    update both together, which is the point.

    The claim is written down in THREE places, and the changelog is the one a
    lane auditing the pin opens first: round 2 found it still saying "three"
    after the header, the subsection and this docstring were corrected. It is
    read here for exactly that reason.
    """
    sources = (("the architecture doc", _document()),
               ("its changelog", _changelog()))
    claims = []
    for name, text in sources:
        found = list(_SHORTER_COUNT_CLAIM.finditer(text))
        assert found, (
            f"{name} no longer states how many cited files are shorter at "
            f"`{BASE_PIN}`, so this guard would pass vacuously over it — "
            "re-point the guard at whichever passage now carries the count "
            "(#5333)"
        )
        claims.extend((name, text, match) for match in found)
    assert len(claims) >= 3, (
        "the pin's evidence is stated in the header, the subsection AND the "
        f"changelog; found {len(claims)} occurrence(s) — re-point this guard "
        "(#5333)"
    )
    for name, text, claim in claims:
        expected = _COUNT_WORDS[claim.group("count").lower()]
        tail = text[claim.end():claim.end() + 400]
        stop = tail.find(")")
        names = _ENUMERATED_PY.findall(tail if stop == -1 else tail[:stop])
        assert len(names) == expected, (
            f"{name}: this sentence says {expected} cited files are shorter at "
            f"`{BASE_PIN}` but enumerates {len(names)}: {names}. Name every "
            "out-of-range file and update the count — a lane refreshing the pin "
            "re-checks exactly the names given here (#5333)."
        )


def _g5_destructive_half() -> str:
    """§4.3's relocation subsection, bounded by the next `###` heading."""
    text = _document()
    assert G5_DESTRUCTIVE_HEADING in text, (
        f"{G5_DESTRUCTIVE_HEADING!r} is gone from {DOC.name} — the measured "
        "state of G5's destructive half no longer has a home (#5333)."
    )
    start = text.index(G5_DESTRUCTIVE_HEADING)
    end = text.index(G5_DESTRUCTIVE_END, start)
    return text[start:end]


def test_a_section_cited_for_failure_policy_is_the_one_that_declares_it():
    """§4.2 declares NO failure policy, so `(§4.2 fail-open)` is a dead pointer.

    §4.2 is *"The ordering — cost per unit of SAFE rejection, NOT 'cheapest
    first'"*. Between its heading and `### S3` it states no fail-open/fail-closed
    rule at all; the failure-policy home is named twice — `### 4.4 Per-step
    FAILURE POLICY (G3)` and G3's §4.3 row (*"declared in §4.4"*). A reader who
    follows a `§4.2` pointer lands on the ordering argument and finds no rule; a
    reader who knows the document concludes the passage invented its own home.
    Measured on the pre-fix text, this reds on `(§4.2 fail-open)`.
    """
    assert _policy_section(), "§4.4 must exist for this guard to mean anything"
    offenders = [
        match.group(0)
        for match in _POLICY_POINTER.finditer(_document())
        if match.group(1) != FAILURE_POLICY_SECTION
    ]
    assert not offenders, (
        "these passages attribute a fail-open/fail-closed policy to a section "
        f"that declares none — name §{FAILURE_POLICY_SECTION} (the per-step "
        "FAILURE POLICY says §4.4 is where a policy is declared, #8495):\n  - "
        + "\n  - ".join(offenders)
    )


def test_the_relocation_rule_points_at_the_failure_policy_home():
    """The unclassifiable-row rule (`stays REPORT`) is a POLICY decision.

    A row that cannot be positively classified is never relocated: fail-closed
    in the delete direction, which is exactly the form §4.4 declares for S3
    (`HOLD`, never asserted `new`). The passage must say so against §4.4 and
    must keep saying it is a PROPOSAL — §4.4 declares policy for the EIGHT
    pipeline steps (§4.1's canonical set), and a relocation pass is not one of
    them.
    """
    section = _g5_destructive_half()
    assert "never relocated" in section, (
        "the fail-closed-on-the-unclassifiable-row rule is gone from §4.3's "
        "relocation subsection — re-point this guard (#5333)."
    )
    assert "§4.4" in section, (
        "the relocation rule no longer names §4.4, the section that declares "
        "per-step failure policy; a pointer to any other number is dead."
    )
    assert "proposal" in section.lower(), (
        "the relocation rule must stay marked as a PROPOSAL — §4.4 declares the "
        "eight pipeline steps' policy, and a relocation pass is not one of them."
    )


def test_the_relocation_rule_does_not_invent_a_second_step_count():
    """P2 (review of #9654): §4.3 must not carry a COMPETING step count.

    §4.0 declares *"EIGHT steps"*, §4.1 repeats it (*"The count is EIGHT … do
    not write `seven`"*) and §4.4 declares policy for every one of the eight.
    The subsection's closing sentence nonetheless read *"§4.4 declares the
    policy for the six pipeline steps … not a seventh declaration"* — it
    undercounted §4.4's own coverage by two steps and derived its ordinal from
    that undercount. A second count in the same document is exactly what the
    drift marker forbids, so a future edit that reintroduces one reds here.

    Measured on the pre-fix text (the passage read *"six pipeline steps"* and
    *"not a seventh declaration"*), this fails.
    """
    section = _g5_destructive_half().replace("*", "")
    assert _CANONICAL_STEP_COUNT.search(section), (
        "the relocation rule must state the canonical step count it inherits — "
        "§4.4 declares the policy for the EIGHT steps of §4.1, and without the "
        "number the 'proposal' claim has nothing to be carried over FROM "
        "(#5333)."
    )
    offenders = [
        match.group(0) for match in _NON_CANONICAL_STEP_COUNT.finditer(section)
    ] + [match.group(0) for match in _NON_CANONICAL_ORDINAL.finditer(section)]
    assert not offenders, (
        "this subsection states a step count the document does not declare — "
        "§4.0/§4.1 say EIGHT (and §4.4 covers all eight), so the carried-over "
        f"rule is not a NINTH declaration. Offenders: {offenders!r}"
    )


def test_the_relocation_subsection_citations_name_their_revision_pin():
    """P2 (review of #9654): the subsection's numbers must name their pin.

    The header pins every `file.py:NNNN` to `c79ba1cf2` *except where a passage
    names a later pin*. The subsection's citations are HEAD numbers: at the base
    pin five of the cited files (`sdk.py`, `assembly.py`, `projection/entities.py`,
    `projection/__init__.py`, `commit_ops.py`) are shorter than the cited line and
    `tortoise/definite_description_backfill.py` does not exist at all — so an
    unpinned passage is not a *stale* citation, it is no citation.
    The passage pins the measurement ONCE for the whole subsection (that is its
    shape: a measurement-scope pin, not a per-line one), and the header must
    list that pin among its exceptions so an adopter can find it.

    Measured on the pre-fix text (no pin token anywhere in the subsection), this
    fails.
    """
    section = _g5_destructive_half()
    cited = [line for line in section.splitlines() if _CODE_CITATION.search(line)]
    assert cited, (
        "§4.3's relocation subsection carries no `file.py:NNNN` citation, so "
        "this guard is vacuous — re-point it at whichever passage now cites a "
        "line number (#8600)."
    )
    later = _declared_later_pins()
    assert later, (
        "the header names no later pin — this guard would pass vacuously; the "
        "header's pin vocabulary changed and needs re-reading (#8600)."
    )
    pinned = [pin for pin in later if f"`{pin}`" in section]
    assert pinned, (
        "§4.3's relocation subsection cites a `file.py:NNNN` but names no "
        f"revision pin. The header reads an unnamed number against `{BASE_PIN}`, "
        "where these numbers are wrong or out of range, so the whole passage "
        "resolves to nothing (#8600). Name the measurement revision in the "
        "passage AND in the header's exception list."
    )
