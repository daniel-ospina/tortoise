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
#: The pin's evidence states how many cited files are OUT OF RANGE at the base
#: pin. The count must match the enumeration beside it, and every passage that
#: states the claim must state the SAME list — see
#: `test_the_pin_evidence_counts_exactly_the_files_it_names` (#9654 review P2/P3).
#: ⚠️ Do not restate the number or the list here: an unguarded copy of it is the
#: defect that guard exists for.
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
#: ⚠️ `re.IGNORECASE`: the document's canonical direction words are UPPERCASE
#: (`FAIL-OPEN` / `FAIL-CLOSED`, and §4.4's own rows), so a case-sensitive
#: pattern reds only on the lowercase spelling of the dead pointer it was written
#: for and reads a pointer written in the document's own vocabulary as clean
#: (#9654 review). Measured document-wide: exactly one match either way, and it
#: is the correct `§4.4` one, so the flag introduces no false positive.
_POLICY_POINTER = re.compile(
    r"§\s*(4\.\d+)[^)\n|]{0,80}(fail-open|fail-closed)", re.IGNORECASE
)
#: §4.0 declares the canonical top-level count (currently **EIGHT**; §4.1 repeats
#: it and §4.2 must agree, with a drift marker forbidding "seven"). A passage
#: that names how many steps an inherited rule covers must use the document's OWN
#: number — a second count in the same file is what the drift marker exists to
#: stop, and an ordinal derived from it ("not a seventh declaration") is wrong in
#: the same direction.
#:
#: ⚠️ The count is DERIVED from §4.0 (`_canonical_steps()`, the same parser
#: `test_every_canonical_step_has_a_declared_failure_policy` uses, which is built
#: to accept a step being ADDED) rather than hardcoded here. A hardcoded list
#: mis-fires in both directions on the day the document legitimately grows to
#: nine: it would red a correctly-updated passage with a message insisting on
#: the now-stale number, while a passage left at the old number stayed green
#: (#9654 review P2). Matched against the passage with markdown emphasis
#: stripped, so `**eight**` and `eight` are read alike.
#:
#: Spelled numbers, so a count written as a word and one written as a digit are
#: read alike. Anything outside this table is not a count ("the steps").
_NUMBER_VALUES = {
    word: value
    for value, word in enumerate(
        [
            "zero", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
            "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
        ]
    )
}
_ORDINAL_VALUES = {
    word: value
    for value, word in enumerate(
        [
            "zeroth", "first", "second", "third", "fourth", "fifth", "sixth",
            "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth",
            "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
            "eighteenth", "nineteenth", "twentieth",
        ]
    )
}
#: The alternations the patterns use, longest-first so `sixteen` cannot be read
#: as `six`. ⚠️ The token must be a KNOWN number: with a bare `[A-Za-z]+` the
#: engine consumes the article in *"the eight pipeline steps"* as the count and
#: then skips past the real number, which read the canonical sentence as carrying
#: no count at all (measured: `finditer` returned `[]`).
_NUMBER_TOKEN = "|".join(
    sorted(_NUMBER_VALUES, key=len, reverse=True)) + r"|\d+"
_ORDINAL_TOKEN = "|".join(sorted(_ORDINAL_VALUES, key=len, reverse=True))
_STEP_COUNT_MENTION = re.compile(
    rf"\b(?P<n>{_NUMBER_TOKEN})\s+(?:[A-Za-z]+\s+)?(?:pipeline\s+)?steps?\b",
    re.IGNORECASE,
)
#: The hyphenated form (`nine-step`), which the whitespace pattern above cannot
#: see. The token is restricted to a NUMBER, so an ordinary compound
#: (`per-step`) is not read as a count.
_HYPHEN_STEP_COUNT = re.compile(rf"\b(?P<n>{_NUMBER_TOKEN})-steps?\b", re.IGNORECASE)
#: An ordinal count of DECLARATIONS: with N steps in §4.4, one more rule is the
#: (N+1)th declaration.
_ORDINAL_DECLARATION = re.compile(
    rf"\b(?P<o>{_ORDINAL_TOKEN})\s+declaration\b", re.IGNORECASE
)
#: The section that DECLARES per-step failure policy (the pointer must name it).
FAILURE_POLICY_SECTION = "4.4"
#: The revision §4.4's citations are read at — named in the header's exception
#: list. Bound PER SECTION rather than "any declared pin": the header's rule is
#: pin IDENTITY ("except where a passage names a later pin"), so a pin that only
#: resolves §4.3's numbers must not satisfy §4.4 (#9654 review).
POLICY_SECTION_PIN = "7d348d351"
#: The revision §4.3's *G5's destructive half* subsection is read at.
G5_DESTRUCTIVE_PIN = "653980445"
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


def test_the_state_of_code_paragraph_tracks_what_runs():
    """The spec-change warning must not outlive the behaviour it describes.

    This guard was written to red the day the renderer was fixed: while
    `extractor_v2.py` still said *"assume everything is new"*, §4.4 had to keep
    saying the code implemented the inverted policy. **`#5095` fixed the render
    and the record**, so the pair is now asserted in the other polarity — the
    instruction is gone from the code, the document says so, and the document
    names what is STILL absent (the mint gate and the carry-forward store) so a
    reader does not mistake the record half for the whole mechanism.
    """
    section = _policy_section()
    code = EXTRACTOR.read_text(encoding="utf-8")
    assert "assume everything is new" not in code, (
        "`tortoise/extractor_v2.py` invites the model to treat every candidate "
        "as new again — §4.4 I1 forbids it, and this change's record half "
        "(#5095) removed it. If that was deliberate, this pin and the doc's "
        "state-of-code paragraph both have to change with it."
    )
    assert "the INVERTED policy" not in section, (
        "§4.4 still claims the code implements the inverted policy, but the "
        "renderer no longer invites `new` (#5095) — the paragraph is stale in "
        "the direction that makes a fixed defect read as open."
    )
    assert "NEVER asserted" in section or "never mint it" in section or \
        "never mint it" in section, (
        "§4.4 lost the invariant the state-of-code paragraph is about"
    )
    # What runs now, and what remains: BOTH halves must be stated, or the
    # paragraph over-claims in one direction or the other.
    assert "identity" in section, (
        "§4.4's state-of-code paragraph does not name the `identity` block / "
        "record half that #5095 landed — a reader would read the remaining "
        "absence as if nothing had landed."
    )
    assert "never mint it" in section.lower() or "still not enforced" in section.lower() \
        or "still absent" in section.lower(), (
        "§4.4 does not say the MINT half is still absent — the record half "
        "would then read as the whole of I1 (#5095)."
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

    ⚠️ **Bound to §4.4's OWN revision, not to "some declared pin"** (#9654
    review P2): the header's rule is pin IDENTITY, and once a fourth pin joined
    the list, accepting any member made a §4.4 line pinned at a §4.3 revision
    read as compliant. `POLICY_SECTION_PIN` is that section's revision and must
    also still be declared in the header, so the binding cannot drift silently.
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
    assert POLICY_SECTION_PIN in later, (
        f"§4.4's citations are read at `{POLICY_SECTION_PIN}`, but the header no "
        "longer declares that pin among its exceptions — the numbers in §4.4 "
        "now read against the base pin, where they are wrong (#8600)."
    )
    cited = [line for line in section.splitlines() if _CODE_CITATION.search(line)]
    assert cited, (
        "§4.4 carries no `file.py:NNNN` citation, so this guard is vacuous — "
        "re-point it at whichever passage now carries a line number (#8600)."
    )
    unpinned = [
        line.strip()[:160]
        for line in cited
        if f"`{POLICY_SECTION_PIN}`" not in line
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
    the pin test's own docstring said "three" while naming four files and
    omitting `commit_ops.py`, which the subsection cites at `:51`, `:59` and
    `:1408` and whose vocabulary the whole passage rests on: a lane refreshing
    the pin would re-check the named files and leave it silently unverified.

    This guards INTERNAL CONSISTENCY, not the measured truth (the count was
    measured at both refs when the pin was added) — the number a reader can
    check against the list beside it is the cheapest falsifier, and a new
    citation has to update both together, which is the point.

    The claim is written down in TWO files and three passages — the architecture
    doc's header clause, its §4.3 pin line, and the changelog that records the
    correction — and the changelog is the one a lane auditing the pin opens
    first: round 2 found it still saying "three" after the other two had been
    corrected. The pin test's own docstring deliberately does NOT restate the
    count or the list any more: a copy no guard reads is exactly how that
    happened, so it names the doc's two passages as the authoritative pair.
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
        "the pin's evidence is stated in the header, the §4.3 pin line AND the "
        f"changelog; found {len(claims)} occurrence(s) — re-point this guard "
        "(#5333)"
    )
    enumerated = []
    for index, (name, text, claim) in enumerate(claims):
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
        # Keyed by PASSAGE, not by source: the architecture doc states the claim
        # twice (header + §4.3 pin line), and keying on the source label let the
        # second overwrite the first, so a divergent HEADER evaporated (#9654
        # round-3 verification).
        enumerated.append((f"{name}, passage {index + 1}", expected,
                           frozenset(names)))
    # Round 3: a per-passage check alone let a DIVERGENT copy pass as long as it
    # was internally consistent (changelog "four" + four names, doc "five" +
    # five names) — which is the round-2 defect one edit away. The copies must
    # agree with EACH OTHER, not just with themselves.
    distinct = {(count, names) for _, count, names in enumerated}
    assert len(distinct) == 1, (
        "the pin's evidence disagrees between the passages that state it: "
        + "; ".join(f"{where} — {count} {sorted(names)}"
                    for where, count, names in enumerated)
        + " — one list, one count, in the header, the §4.3 pin line and the "
        "changelog (#5333)."
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

    ⚠️ **Anchored, not merely exhaustive** (#9654 review P3): the guard rejects
    offenders, so a pattern that matched NOTHING would report the same clean
    result as a document with no dead pointer. The canonical form is therefore
    required to still be found — the document does name §4.4 beside a direction
    (G3's row), and if the section renumbers or the spellings change, that is a
    red rather than silence.
    """
    assert _policy_section(), "§4.4 must exist for this guard to mean anything"
    matches = list(_POLICY_POINTER.finditer(_document()))
    canonical = [m for m in matches if m.group(1) == FAILURE_POLICY_SECTION]
    assert canonical, (
        "no `§" + FAILURE_POLICY_SECTION + " … fail-open/fail-closed` pointer was "
        f"found anywhere in {DOC.name} ({[m.group(0) for m in matches]}), so "
        "this guard is passing vacuously — re-point its pattern at the form the "
        "document actually uses, or restore the pointer to the section that "
        "declares the policy (#8495)."
    )
    offenders = [m.group(0) for m in matches if m.group(1) != FAILURE_POLICY_SECTION]
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


def _stated_step_counts(section: str) -> list[tuple[str, int]]:
    """``(text, value)`` for every step-count mention in ``section``.

    Word and digit forms are read alike, and the hyphenated form is read too, so
    the guard covers what a reader would recognise as a count rather than one
    spelling of it.
    """
    found: list[tuple[str, int]] = []
    for pattern in (_STEP_COUNT_MENTION, _HYPHEN_STEP_COUNT):
        for match in pattern.finditer(section):
            token = match.group("n").lower()
            value = int(token) if token.isdigit() else _NUMBER_VALUES.get(token)
            if value is not None:
                found.append((match.group(0), value))
    return found


def _stated_ordinals(section: str) -> list[tuple[str, int]]:
    """``(text, value)`` for every ``… declaration`` ordinal in ``section``."""
    return [
        (match.group(0), _ORDINAL_VALUES[match.group("o").lower()])
        for match in _ORDINAL_DECLARATION.finditer(section)
        if match.group("o").lower() in _ORDINAL_VALUES
    ]


def test_the_relocation_rule_does_not_invent_a_second_step_count():
    """P2 (review of #9654): §4.3 must not carry a COMPETING step count.

    §4.0 declares the canonical set, §4.1 repeats the count (*"The count is
    EIGHT … do not write `seven`"*) and §4.4 declares policy for every one of
    them. The subsection's closing sentence nonetheless read *"§4.4 declares the
    policy for the six pipeline steps … not a seventh declaration"* — it
    undercounted §4.4's own coverage by two steps and derived its ordinal from
    that undercount. A second count in the same document is exactly what the
    drift marker forbids, so a future edit that reintroduces one reds here.

    ⚠️ **The count is derived, not restated.** `N` comes from §4.0's own
    declared set (`_canonical_steps()`), so the passage, §4.0/§4.1 and this
    guard agree by construction: when the document legitimately grows to nine
    steps, the guard follows it instead of reding a correct passage and instead
    of accepting the stale number (#9654 review P2). The permitted ordinal is
    `N + 1` (one more declaration than the canonical steps).

    Non-vacuity: measured on the pre-fix text (the passage read *"six pipeline
    steps"* and *"not a seventh declaration"*), both arms fail.
    """
    canonical = _canonical_steps()
    count = len(canonical)
    assert count >= 8, f"§4.0's canonical step set parsed as {canonical!r}"
    word = next(w for w, value in _NUMBER_VALUES.items() if value == count)
    section = _g5_destructive_half().replace("*", "")

    stated = _stated_step_counts(section)
    assert any(value == count for _, value in stated), (
        "the relocation rule must state the canonical step count it inherits — "
        f"§4.4 declares the policy for the {word.upper()} steps of §4.1 "
        f"({', '.join(canonical)}), and without the number the 'proposal' "
        f"claim has nothing to be carried over FROM. Counts found: {stated} "
        "(#5333)."
    )
    offenders = [text for text, value in stated if value != count]
    assert not offenders, (
        "this subsection states a step count the document does not declare — "
        f"§4.0/§4.1 say {word.upper()} ({count}), and §4.4 covers all {count}, "
        f"so the carried-over rule is not a "
        f"{next(w for w, v in _ORDINAL_VALUES.items() if v == count + 1).upper()} "
        f"declaration. Offenders: {offenders!r}"
    )
    ordinal_offenders = [
        text for text, value in _stated_ordinals(section) if value != count + 1
    ]
    assert not ordinal_offenders, (
        "this subsection derives its ordinal from something other than the "
        f"document's step count — with {count} canonical steps, one more rule "
        "is the "
        f"{next(w for w, v in _ORDINAL_VALUES.items() if v == count + 1)} "
        f"declaration. Offenders: {ordinal_offenders!r} (#5333)"
    )


def test_the_relocation_subsection_citations_name_their_revision_pin():
    """P2 (review of #9654): the subsection's numbers must name their pin.

    The header pins every `file.py:NNNN` to `c79ba1cf2` *except where a passage
    names a later pin*. The subsection's citations are HEAD numbers: at the base
    pin most of the cited files do not reach the cited line at all, and
    `tortoise/definite_description_backfill.py` does not exist there — so an
    unpinned passage is not a *stale* citation, it is no citation. ⚠️ The count
    and the file list live in the header's `653980445` clause and in §4.3's pin
    line, and nowhere else: they are guarded there
    (`test_the_pin_evidence_counts_exactly_the_files_it_names`), so this
    docstring deliberately does not restate them — an unguarded copy is how the
    round-2 defect happened (#5333).
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
    assert G5_DESTRUCTIVE_PIN in later, (
        f"this subsection's numbers are read at `{G5_DESTRUCTIVE_PIN}`, but the "
        "header no longer declares that pin among its exceptions — the passage "
        "now reads against the base pin, where it resolves to nothing (#8600)."
    )
    #: Bound to THIS subsection's own revision, not to "any declared pin": with
    #: four pins in the header, a set-membership test accepted a passage pinned
    #: at a sibling's revision, i.e. it verified that a pin appears rather than
    #: that the passage names the revision its numbers hold at (#9654 review).
    assert f"`{G5_DESTRUCTIVE_PIN}`" in section, (
        "§4.3's relocation subsection cites a `file.py:NNNN` but does not name "
        f"`{G5_DESTRUCTIVE_PIN}`, the revision its numbers hold at. The header "
        f"reads an unnamed (or differently-pinned) number against `{BASE_PIN}`, "
        "where these numbers are wrong or out of range, so the whole passage "
        "resolves to nothing (#8600). Name the measurement revision in the "
        "passage AND in the header's exception list."
    )
