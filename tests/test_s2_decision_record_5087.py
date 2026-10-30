"""#5087 / PR #10072 — §8 of the design record must BE a table, and cite by NAME.

THE DEFECT THIS PINS (two, both found at the 17th review round of PR #10072)
----------------------------------------------------------------------------
1. **A blank line ends a GFM table.** `docs/architecture/S2-DECISION-SEAM.md` carried
   one *inside* §8's test table, between the sixth row and the remaining eight. Under GFM
   the blank line terminates the table, and the rows below it have no header and no
   delimiter row, so they are not a table at all — on GitHub eight of the thirteen named
   tests rendered as paragraph text. It is invisible to CI by construction: the `docs`
   leg runs `markdownlint-cli2`, and a block without a delimiter row is not a table, so
   no MD055/MD056 fires. The leg stays green while the artifact reads wrong — and §8 is
   the artifact the PR body points a reviewer at to check that all thirteen tests exist.

2. **§8 rows cited siblings by ORDINAL** (*"dropping it is row 11"*, *"row 22 is the
   raise-branch latch"*). Those pointers went stale in the same direction as the counts
   did: three rows inserted above moved every one of them by three, so a reader following
   "row 11" landed on `the classify fold drops model_served` and one following "row 22"
   landed on `_http_post reverts to bare urlopen`. The table's own preamble already
   forbade the class for LINE NUMBERS (*"Rows cite methods and assertions by NAME, never by
   line number"*); a row ordinal is a line number in disguise and moves the same way.

3. **§5 cited the two classify passes by BARE line number** (*"`(:8608`, the S2 list, and
   `:8740`, the post-S4 union)"*). Both landed on unrelated statements — `:8608` is
   `"recovery": recovery_stats},` inside `extract_session_v2`'s **empty-conversation early
   return** (the branch that returns before any classify pass can exist), and `:8740` is
   `stats=stage_stats,` in the `run_s4(...)` call — while the two passes were at `:8700`
   and `:8832`. A bare `:NNNN` is the one shape with **nothing authoritative to fall back
   on**: it names no symbol, so a reader following it cannot tell a stale cite from a
   correct one, and this one sent them to the no-conversation path. §8's preamble had
   already claimed the record's guard *"reddens on either form"* while nothing checked
   this one.

WHAT THIS PINS
--------------
1. No blank line splits a table anywhere in the design record, unless the block below it
   opens its OWN table (a header row followed by a delimiter row — the legitimate case of
   two tables back to back).
2. No row inside §8 — the section that holds the test table and the mutant census —
   cites a sibling by ordinal. Prose may say "row" in a sentence; a **row** may not point
   at another row by number.
3. No line citation anywhere in the record is **bare** — a `:NNNN` with no symbol, file or
   quoted identifier in front of it. The rule is the one `tests/test_extractor_v4_citation_rot.py`
   states for the sibling document ("the symbol name is authoritative, the line number is a
   convenience"; a citation whose symbol has merely MOVED passes and that is deliberate), so a
   file-anchored `FILE:line` is out of scope here by that recorded tolerance — it is the bare
   form, with no symbol at all, that cannot be checked by its reader.

Non-vacuity: on the pre-fix document check 1 fails on the blank line inside §8's test
table, check 2 fails on the two `row 11` citations in the `_noul` mutant row, and check 3
fails on §5's `:8608`/`:8740` pair. All three are re-verified below by running the same
checkers over the pre-fix text, so a future edit that reintroduces any of them reddens here
instead of merging green.

Static: file reads plus the §7 payload construction through a stub transport. No network,
no DB, no graph, no model — and that is a claim the suite can hold this file to, which is
why the §7 `tok` cells are a RECORDED proxy (`_S7_TOKEN_CELLS`) rather than a re-count
(PR #10302 review round 6: `tiktoken` is a `dev`-group package and its BPE fetch leaves the
box, invisible to the httpx-shaped hermetic guard — details on the constant).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "S2-DECISION-SEAM.md"
#: The docs index also publishes §7's FRAMED pair, on the `#5087` row — a fourth artifact
#: carrying the same two numbers. Until review round 7 no test read it at all.
INDEX = ROOT / "docs" / "00_index.md"

#: A GFM table delimiter row: `|---|---|`, `| :-- | --: |`, … (≥3 dashes per column).
_DELIMITER_ROW = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+$")

#: "row 11", "row 22", "Rows 3" — a citation by ORDINAL, which is a line number in
#: disguise: it moves whenever a row is inserted above it.
_ORDINAL_CITATION = re.compile(r"\brows?\s+\d+\b", re.IGNORECASE)

#: §8 is "The tests, and the mutants that redden them".
_SECTION_8_HEADING = "## 8."

#: A BARE line citation: `:8608` with nothing that names the cited thing in front of it.
#: `EXTRACTOR-V4-ARCHITECTURE.md:823` and `foo.py:12` are anchored by a file name and are
#: NOT this shape — their symbol is recoverable, which is the tolerance
#: `tests/test_extractor_v4_citation_rot.py` documents. A clock time (`12:30`) is excluded
#: by the same lookbehind, because the character before its colon is a digit.
_BARE_LINE_CITATION = re.compile(r"(?<![A-Za-z0-9_.]):\d{2,4}\b")

#: The two primitives LUMPED into one subject (``noul`` / ``score``) under a live-exercise
#: verb. `noul` HAS a live caller of ours (`agent-infra/scripts/ask-owner.py`); `score`
#: has none — no caller anywhere in the fleet builds a `score` question. So a sentence
#: pairing them under "exercised"/"corroborated"/"verified live" is false about `score`,
#: and false in the UNSAFE direction: it licenses the `score` shape (its 2–10 level
#: `criteria` array is the dominant term in `vet-v2`'s request and the input to the
#: `atomic_min` veto that stops a FUSED candidate being dropped) as if it had ever been
#: sent to the vendor. The sentence is the unit, so the match may not cross `.`, `!`, `?`
#: or `;` — otherwise a nearby mention of "verified live" would be attributed to the pair.
_LUMPED_LIVE_CLAIM = re.compile(
    r"`*noul`*\s*/\s*`*score`*[^.!?;]{0,200}(?:exercis|corroborat|verified live)",
    re.IGNORECASE,
)

#: The module whose docstring makes the same claim — the two artifacts must not disagree.
SEAM_MODULE = ROOT / "tortoise" / "s2_decision_seam.py"

#: The THIRD artifact carrying the same ledger (its #10072 entry). Round 18 found it stating the
#: OPPOSITE of §2 for `choice` + `model` while the guard above watched only `SEAM_MODULE` and the
#: record — two of the artifacts that make the claim. It is corpus here, not a comment, because a
#: claim that moves between four artifacts with only two of them guarded is exactly how round 16's
#: `noul`/`score` finding recurred.
CHANGELOG = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md"

#: The `choice` `criteria` MAP and the envelope's `model` echo — the two shapes a caller of OURS
#: exercises live: `agent-infra/scripts/turn-classify.py:207-239` sends one `choice` carrying a
#: `criteria` map, and `:381` reads `model` off the response into its meta. A sentence pairing
#: either with "not exercised by a live call" states the OPPOSITE of §2 and of the code, and it
#: understates the evidence in the one entry that records why this seam exists.
_CHOICE_OR_ECHO = re.compile(r"choice[`\s-]{0,3}criteria|model[`\s-]{0,3}echo", re.IGNORECASE)

#: A "nothing of ours has ever sent this" claim. The bold/code markers between the words are
#: tolerated: the artifacts say "are **not** yet exercised", not "are not yet exercised".
_NO_LIVE_CLAIM = re.compile(
    r"not[\s*_`]{0,4}(?:yet[\s*_`]{0,4})?exercised"
    r"|no live (?:call|caller)"
    r"|reference page only"
    r"|documented only",
    re.IGNORECASE,
)

#: The wording that makes such a sentence TRUE — the shape IS corroborated. A sentence carrying
#: both is the honest per-primitive form ("`choice` … has a live caller, while `score` is
#: documented only") and must pass.
_CORROBORATED = re.compile(r"corroborat|live caller|has a live", re.IGNORECASE)

#: The unit the reader reads. A claim may not be attributed across `.`, `!`, `?` or `;` — otherwise
#: a nearby "corroborated" would be read as covering a sentence that says the opposite.
_SENTENCE = re.compile(r"[^.!?;]+")

#: The `model` echo NAMED as a check/gate/assertion. The code RECORDS the echo and never compares
#: it to `MODEL` (deliberately: asserting it would make an armed seam inert on the vendor's next
#: release), so an artifact naming a check the code refuses to perform is the same defect class as
#: a false live-evidence claim.
_ECHO_NAMED_AS_CHECK = re.compile(
    r"model[`\s-]{0,3}echo[\s`_*-]{0,6}(?:check|assert|gate|validat)", re.IGNORECASE
)


def _per_primitive_claims(text: str) -> list[str]:
    """Sentences pairing `choice`/`model` with a "nothing live has sent this" claim.

    Whitespace is collapsed FIRST: the claim wraps across lines in every artifact, and a checker
    that only saw single lines would pass on a re-wrapped re-introduction.
    """
    collapsed = " ".join(text.split())
    hits: list[str] = []
    for sentence in _SENTENCE.finditer(collapsed):
        span = sentence.group(0)
        if (
            _CHOICE_OR_ECHO.search(span)
            and _NO_LIVE_CLAIM.search(span)
            and not _CORROBORATED.search(span)
        ):
            hits.append(span.strip())
    return hits


def _lines() -> list[str]:
    return DOC.read_text(encoding="utf-8").splitlines()


def _is_row(line: str) -> bool:
    """A table row (or a table's header/delimiter row) — GFM needs the leading pipe."""
    return line.lstrip().startswith("|")


def _is_delimiter(line: str) -> bool:
    return bool(_DELIMITER_ROW.match(line.strip()))


def _split_tables(lines: list[str]) -> list[int]:
    """Return the 1-based line numbers of blank lines that CUT a table in half.

    A blank line whose neighbours are both table rows is only legitimate when the block
    below it opens its own table — i.e. the next row is a header and the one after it is a
    delimiter row. Anything else leaves rows with no header: not a table, no lint error.
    """
    splits: list[int] = []
    for index in range(1, len(lines) - 1):
        if lines[index].strip():
            continue
        if not (_is_row(lines[index - 1]) and _is_row(lines[index + 1])):
            continue
        if index + 2 < len(lines) and _is_delimiter(lines[index + 2]):
            continue  # two tables back to back: the blank line separates, it does not cut
        splits.append(index + 1)
    return splits


def _section_8_rows(lines: list[str]) -> list[tuple[int, str]]:
    """Every table ROW (1-based line number, text) inside §8, up to the next `## ` heading."""
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(_SECTION_8_HEADING)),
        None,
    )
    assert start is not None, f"{DOC.name} no longer carries a '{_SECTION_8_HEADING}' section"
    rows: list[tuple[int, str]] = []
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            break
        if _is_row(lines[index]):
            rows.append((index + 1, lines[index]))
    assert rows, f"§8 of {DOC.name} carries no table rows — the census moved or was lost"
    return rows


def _ordinal_citations(lines: list[str]) -> list[tuple[int, str]]:
    return [
        (lineno, line)
        for lineno, line in _section_8_rows(lines)
        if _ORDINAL_CITATION.search(line)
    ]


def _bare_line_citations(lines: list[str]) -> list[tuple[int, str]]:
    """1-based line numbers of every line carrying a `:NNNN` with no symbol in front of it."""
    return [
        (index + 1, line)
        for index, line in enumerate(lines)
        if _BARE_LINE_CITATION.search(line)
    ]


def test_no_table_in_the_design_record_is_split_by_a_blank_line():
    splits = _split_tables(_lines())
    assert not splits, (
        "a blank line ENDS a GFM table, so every row below it has no header and renders as "
        "plain paragraph text — and markdownlint-cli2 does not fire (a block with no "
        f"delimiter row is not a table), so the docs leg stays green. {DOC.relative_to(ROOT)} "
        f"has a table-cutting blank line at {splits}."
    )


def test_no_row_in_section_8_cites_a_sibling_by_ordinal():
    offenders = _ordinal_citations(_lines())
    assert not offenders, (
        "§8's rows must cite a sibling mutant or test by NAME, never by ordinal: an ordinal "
        "moves the moment a row is inserted above it, and every one of them had already moved "
        "by three. Offending row(s): "
        + "; ".join(
            f"line {lineno}: {line[:160]}" for lineno, line in offenders
        )
    )


def test_no_line_citation_in_the_design_record_is_bare():
    offenders = _bare_line_citations(_lines())
    assert not offenders, (
        "a bare `:NNNN` names no symbol, so a reader has nothing authoritative to fall back on "
        "and cannot tell a stale cite from a correct one — one such pair landed inside "
        "`extract_session_v2`'s empty-conversation early return. Cite the symbol instead. "
        f"Offending line(s) in {DOC.relative_to(ROOT)}: "
        + "; ".join(f"line {lineno}: {line.strip()[:160]}" for lineno, line in offenders)
    )


def test_the_three_checkers_redden_on_the_pre_fix_record():
    """Non-vacuity: the checks fail on the shapes the #10072 review found, and pass on this head."""
    pre_fix_test_table_split = (
        "| test | what it proves |",
        "|---|---|",
        "| `test_the_module_guards_the_suite_never_proved_redden_when_removed` | `Bands` … |",
        "",
        "| `test_a_NOOP_verdict_does_not_rescind_a_mechanical_discard` | an emitted `NOOP` … |",
        "| `test_a_standalone_KEEP_verdict_keeps_its_own_attribution` | a standalone `KEEP` … |",
    )
    assert _split_tables(list(pre_fix_test_table_split)) == [4], (
        "the blank line between two rows, with no new header+delimiter below it, must be "
        "reported as a table split"
    )

    # Two tables back to back are legitimate and must NOT be flagged.
    legitimate = (
        "| test | what it proves |",
        "|---|---|",
        "| `test_a` | … |",
        "",
        "| mutant | red |",
        "|---|---|",
        "| `_noul` drops its `[0, 1]` bound | ✅ … |",
    )
    assert _split_tables(list(legitimate)) == [], (
        "a blank line separating two complete tables (each with its own header and delimiter "
        "row) is not a defect"
    )

    assert _ordinal_citations(
        [
            "## 8. The tests, and the mutants that redden them",
            "| mutant | red |",
            "|---|---|",
            "| `_noul` replaces its `_finite(...)` call (the band is not part of this "
            "mutant — dropping it is row 11) | ✅ … |",
            "",
            "## 9. What this does NOT close",
        ]
    ), "a row that points at a sibling by ordinal must be reported"

    # §5 at the pre-fix head, verbatim — both cites bare, both on unrelated lines.
    pre_fix_section_5 = (
        "runs **two** classify passes on the same `KindClassifier` instance (`:8608`, the",
        "S2 list, and `:8740`, the post-S4 union), so the S2.3 side also pays 2 ×",
    )
    assert [lineno for lineno, _ in _bare_line_citations(list(pre_fix_section_5))] == [1, 2], (
        "a `:NNNN` with no symbol in front of it must be reported — this is the §5 pair the "
        "19th review round found pointing into the empty-conversation early return"
    )

    # An ANCHORED cite is the tolerated form and must NOT be flagged: the file name is the
    # symbol to fall back on, which is what `test_extractor_v4_citation_rot.py` documents.
    assert _bare_line_citations(
        [
            "(`EXTRACTOR-V4-ARCHITECTURE.md:823`) implies once the rate this section",
            "`tortoise/extractor_v2.py:8700` is the S2 classify pass, and 12:30 is a time",
        ]
    ) == [], (
        "a file-anchored `FILE:line` carries a symbol to resolve against and is deliberately "
        "tolerated; a clock time is not a citation at all"
    )


def test_neither_artifact_claims_the_score_primitive_was_exercised_live():
    """`noul` and `score` are NOT corroborated alike, and the record must say so.

    THE DEFECT THIS PINS (review of PR #10072): `tortoise/s2_decision_seam.py`'s module
    docstring said *"the ``noul`` / ``score`` shapes are the ones that caller exercised
    live"*, while §2 of THIS record said *"The `noul` / `score` primitives are documented by
    the vendor and exercised by no caller of ours"* — the same PR, opposite claims, and
    **both wrong**: `noul` IS live-exercised (`agent-infra/scripts/ask-owner.py` posts five
    `noul` questions to `api.typesafe.ai/v1/systemone` on `jev-latest`), and `score` is sent
    by nobody (the deleted `collision_preflight` JEV path sent only `noul`; `turn-classify.py`
    sends only `choice`; the vendor merely documents `score` at
    `docs.typesafe.ai/primitives/score`).

    Why it is not cosmetic: the docstring's form is the unsafe direction. It tells an
    operator that `score` — whose 2–10 level `criteria` array is the dominant term in
    `vet-v2`'s request and the input to the `atomic_min` veto that stops a FUSED candidate
    being dropped — has been exercised live, when nothing has ever sent one. A wrong
    `score` shape is benign (400 → `Unavailable` → no verdict), but the ledger this PR
    exists to make honest would be wrong at exactly the primitive with no live evidence.

    So: no artifact may pair the two primitives under a live-exercise verb, and both must
    name the caller that actually exercises `noul` (the evidence anchor, so a later
    "tidy-up" that restores the lumped claim has to delete a citation to do it).

    Non-vacuity: both pre-fix sentences are re-checked below, and they red on the checker
    rather than on a remembered line number. Static: two file reads, no network.
    """
    record = DOC.read_text(encoding="utf-8")
    docstring = ast.get_docstring(
        ast.parse(SEAM_MODULE.read_text(encoding="utf-8"))
    ) or ""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert docstring, f"{SEAM_MODULE.name} no longer carries a module docstring"

    for name, text in (
        (f"§2 of {DOC.relative_to(ROOT)}", record),
        (f"{SEAM_MODULE.relative_to(ROOT)}'s module docstring", docstring),
        (f"{CHANGELOG.relative_to(ROOT)}'s #10072 entry", changelog),
    ):
        # Collapse whitespace first: the claim wraps across lines in both artifacts, and a
        # checker that only saw single lines would pass on a re-wrapped re-introduction.
        collapsed = " ".join(text.split())
        hit = _LUMPED_LIVE_CLAIM.search(collapsed)
        assert hit is None, (
            f"{name} lumps `noul` and `score` into one subject under a live-exercise verb, "
            "which is false about `score` — no caller of ours has ever sent a score "
            f"question. Offending span: {hit.group(0)!r}"
        )
        assert "ask-owner.py" in collapsed, (
            f"{name} must name the caller that actually exercises `noul` live "
            "(`agent-infra/scripts/ask-owner.py`) — without it, the split is unanchored "
            "and the next reader has no evidence to check it against"
        )

    # ── non-vacuity: the two pre-fix sentences, re-measured ──────────────────
    for pre_fix, artifact in (
        ("repo has left — the vendor's own docs already show the map form and the "
         "``instructions`` / ``criteria`` fields), and the ``noul`` / ``score`` shapes are "
         "the ones that caller exercised live.", "the pre-fix docstring"),
        ("so the two are re-measured and re-cut in ONE pass. The `noul` / `score` "
         "primitives are documented by the vendor and exercised by no caller of ours;",
         "the pre-fix record"),
    ):
        assert _LUMPED_LIVE_CLAIM.search(" ".join(pre_fix.split())) is not None, (
            f"{artifact} must red on this checker — otherwise the guard is inert and the "
            "claim can be restored green"
        )

    # The legitimate, factual shapes must NOT be flagged: `noul` alone (its own live
    # caller), and a mention of both primitives that makes no exercise claim.
    for legitimate in (
        "`noul` is live-exercised by `agent-infra/scripts/ask-owner.py`.",
        "A wrong ``noul``/``score`` shape is the benign case: 400 → Unavailable → no "
        "verdict, and the counters name it.",
    ):
        assert _LUMPED_LIVE_CLAIM.search(legitimate) is None, (
            f"this is a TRUE statement and must pass: {legitimate!r}"
        )


def test_no_artifact_understates_a_live_corroborated_shape():
    """`choice`+`criteria` and `model` ARE live-corroborated; no artifact may say otherwise.

    THE DEFECT THIS PINS (review round 18 of PR #10072, found at head `d511204d4`):
    `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`'s #10072 entry said *"the `choice`-criteria map and
    the `model`-echo check come from the vendor's reference page and are **not** yet exercised by a
    live call"* — while §2 of this record and `s2_decision_seam.py`'s docstring both said a live
    caller corroborates exactly those two. The CHANGELOG was the outlier and it was FALSE:
    `agent-infra/scripts/turn-classify.py` sends one `choice` carrying a `criteria` **map**
    (`scripts/turn-classify.py:207-239`) to the vendor endpoint on the alias, and reads `model` off
    the response on every call (`:381`). Two artifacts overstated nothing; the third understated.

    Two claims, because the same sentence carried both:
    1. **No artifact may pair `choice`+`criteria` or the `model` echo with a "nothing live has sent
       this" claim** (words: not-yet-exercised / reference-page-only / documented-only / no live
       caller) unless the same sentence also says it IS corroborated. Round 16's finding was a
       lumped `noul`/`score` claim in two artifacts; round 18's was the mirror image in a third —
       the reason this file's corpus is now all three artifacts and not two.
    2. **No artifact may call the `model` echo a CHECK.** The code deliberately never compares the
       echo to `MODEL` (asserting it would make an armed seam inert on the vendor's next release),
       so naming one is claiming a guard the code refuses to perform — the class this PR has been
       correcting every round.

    Why it is not cosmetic: the CHANGELOG entry is the artifact that records WHY the seam exists and
    what the first scored run must re-measure, and its Tests section already re-verifies the
    `noul`/`score` split — so the reader is told the ledger is per-primitive one paragraph after
    being told the two corroborated shapes are unexercised.

    Non-vacuity: the pre-fix sentence is re-checked below. Static: three file reads, no network.
    """
    record = DOC.read_text(encoding="utf-8")
    changelog = CHANGELOG.read_text(encoding="utf-8")
    docstring = ast.get_docstring(
        ast.parse(SEAM_MODULE.read_text(encoding="utf-8"))
    ) or ""
    assert docstring, f"{SEAM_MODULE.name} no longer carries a module docstring"

    for name, text in (
        (f"§2 of {DOC.relative_to(ROOT)}", record),
        (f"{SEAM_MODULE.relative_to(ROOT)}'s module docstring", docstring),
        (f"{CHANGELOG.relative_to(ROOT)}'s #10072 entry", changelog),
    ):
        offenders = _per_primitive_claims(text)
        assert not offenders, (
            f"{name} says the `choice` `criteria` map or the `model` echo has no live caller. "
            "That is false — `agent-infra/scripts/turn-classify.py` sends one `choice` with a "
            "`criteria` map and reads `model` on every call — and it understates the evidence in "
            f"the artifact a reader uses to decide what to re-measure. Offending span(s): "
            f"{offenders}"
        )
        named_as_check = _ECHO_NAMED_AS_CHECK.search(text)
        assert named_as_check is None, (
            f"{name} calls the `model` echo a check/gate. The seam RECORDS the echo and never "
            "compares it to `MODEL` (asserting it would make an armed seam inert on the vendor's "
            "next release), so this names a guard the code refuses to perform. Offending span: "
            f"{named_as_check.group(0)!r}"
        )

    # ── non-vacuity: the pre-fix CHANGELOG sentence, re-measured ─────────────
    pre_fix = (
        "The `choice`-criteria map and the `model`-echo check come from the vendor's reference "
        "page and are **not** yet exercised by a live call."
    )
    assert _per_primitive_claims(pre_fix), (
        "the pre-fix CHANGELOG sentence must red on this checker — otherwise the guard is inert "
        "and the false claim can be restored green"
    )
    assert _ECHO_NAMED_AS_CHECK.search(pre_fix) is not None, (
        "the pre-fix wording `model`-echo check must red on the echo-as-check checker"
    )

    # The legitimate, factual shapes must NOT be flagged.
    for legitimate in (
        # §2's own per-primitive form: both subjects present, the claim is TRUE per primitive.
        "the `choice` `criteria` map and the `model` field are both corroborated by a live "
        "caller, while `score` is documented only",
        "the `model` echo is a receipt, never an assertion",
        "`agent-infra/scripts/turn-classify.py` sends one `choice` carrying a `criteria` map",
    ):
        assert _per_primitive_claims(legitimate) == [], (
            f"this is a TRUE statement and must pass: {legitimate!r}"
        )
        assert _ECHO_NAMED_AS_CHECK.search(legitimate) is None, (
            f"this is a TRUE statement and must pass the echo check too: {legitimate!r}"
        )


# ── §7's numbers must BE a measurement of the payload at this head ─────────

#: The construction §7 states, so the recorded figures can be re-checked rather than
#: believed. Kept here as constants because a construction that lives only in prose is
#: the reason the row moved unmeasured through two review rounds (#10077 / #10117).
_S7_FILLER_TEXT = ("the cache stays warm across restarts because the operator decided that a "
                   "marker must survive a session that is still being written to")
_S7_FILLER = _S7_FILLER_TEXT.split()
_S7_VET_ITEM_CHARS = 55
_S7_CLASSIFY_ITEM_CHARS = 71
_S7_NARRATIVE_WORDS = 500

#: A §7 cost row, ALL FOUR cells:
#: ``| `vet-v2`, **40 items** / … | 50,004 B | 11,223 | **\$0.00047** → **\$0.0000118/item** |``.
#:
#: ⚠️ The `tok` and `$` cells are captured, not just the `B` cell (PR #10302 review round 7,
#: P2 — the same finding the section's own §8 row calls "the inert-enforcer shape"). Round 6
#: made the token column a RECORDED proxy on the argument that the recorded cells still *pin*
#: the published ones — but `_S7_TOKEN_CELLS` was consumed only as a whitelist that EXCUSED a
#: pair from `_unmarked_legacy_figures`, and §7 publishes its cells as a **table row**, which
#: `_V1_FIGURE` (it requires the `B ≈ tok` form) never matches. So the table's `tok` and `$`
#: cells were pinned by nothing: `| 50,004 B | 11,223 |` → `| 50,004 B | 9,999 |` left every
#: assertion green, on the artifact an operator prices the seam from.
_S7_COST_ROW = re.compile(
    r"^\| `(?P<version>vet-v2|classify-v2)`[^|]*?\b(?P<items>[\d,]+) (?:tail )?items\b[^|]*?"
    r"\| (?P<b>[\d,]+) B \| (?P<tok>[\d,]+) \| "
    r"\*\*\\?\$(?P<cost>[\d.]+)\*\* → \*\*\\?\$(?P<per_item>[\d.]+)/item\*\* \|",
    re.MULTILINE)

#: §7's own prose statement of the `tok` cell as a per-item count —
#: ``**281 tokens/item** here (11,223 / 40)`` — which derives from the cell above it and is
#: the form a reader compares against the 343 tokens/item comparator a few lines down.
_S7_TOKENS_PER_ITEM = re.compile(
    r"\*\*(?P<per>[\d,]+) tokens/item\*\* here \((?P<tok>[\d,]+) / (?P<items>\d+)\)")

#: The vendor's documented input-token price, quoted as `4.2e-8 \$` per token in §7's
#: "The price" derivation (and `\$42`/`\$0.042` per Btok/Mtok). The `$` column is that rate
#: applied to the `tok` column, so it must re-derive from it — a rate change moves both.
_S7_USD_PER_INPUT_TOKEN = 4.2e-8


def _s7_cost_cells(text: str) -> dict[str, dict[str, int | float]]:
    """§7's four published cells per version, parsed out of the table itself."""
    return {m.group("version"): {
                "items": int(m.group("items").replace(",", "")),
                "bytes": int(m.group("b").replace(",", "")),
                "tok": int(m.group("tok").replace(",", "")),
                "cost": float(m.group("cost")),
                "per_item": float(m.group("per_item")),
            } for m in _S7_COST_ROW.finditer(text)}

#: The §7 ``tok`` cells — a RECORDED `cl100k_base` proxy, deliberately NOT re-derived here.
#: Both columns of the table, because §7 publishes the framed request and the clause-removed
#: one it compares against.
#:
#: ⚠️ Why a constant rather than a measurement (PR #10302 review round 6, P1 + P2): the
#: `tok` column needs `tiktoken`, which is declared ONLY in the `dev` dependency group — no CI
#: leg installs it (`pyproject.toml`'s `test` extra is what `pip install -e '.[test]'` resolves,
#: and the sharded job runs the ambient interpreter, not the `uv sync` venv), so a test that
#: imports it reds the fast leg with `ModuleNotFoundError`. Worse, `tiktoken.get_encoding(
#: "cl100k_base")` DOWNLOADS its ~1.1 MB BPE ranks from
#: `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` at construction
#: whenever `$TMPDIR/data-gym-cache` is cold — and every ephemeral runner's cache is cold. That
#: fetch goes over `requests`/`urllib`, not `httpx`, so the hermetic egress guard in
#: `tests/conftest.py` cannot see it: the call would be real egress from a file that declares
#: itself deterministic, with no opt-in recording it. A deterministic test may do neither.
#:
#: The **`B` cells are still measured exactly** and are the tripwire: any payload change that
#: moves these token counts moves those bytes first, and `test_section_7_cost_rows_…` reds on
#: it. These two dicts then pin the PUBLISHED cells in BOTH forms the artifacts use them — a
#: `39,544 B ≈ 9,999 tok`-shaped edit is neither of them and reds `_unmarked_legacy_figures`,
#: and a **table** edit (`| 50,004 B | 9,999 |`) reds the `tok` equality in
#: `test_section_7_cost_rows_…`, which also re-derives the `$` column from these cells at
#: `4.2e-8 $/token` (PR #10302 review round 7, P2: the table form was pinned by nothing).
#:
#: Re-measure on a dev box (`uv sync --group dev`, which does install `tiktoken`): build
#: `_s7_requests(framed=…)` and run
#: `len(tiktoken.get_encoding("cl100k_base").encode(json.dumps(payload)))` over each payload.
_S7_TOKEN_CELLS = {"vet-v2": 11_223, "classify-v2": 7_674}
_S7_TOKEN_CELLS_UNFRAMED = {"vet-v2": 9_103, "classify-v2": 6_594}


def _s7_text(chars: int, seed: int = 1) -> str:
    words, i = [], seed
    while len(" ".join(words)) < chars:
        words.append(_S7_FILLER[i % len(_S7_FILLER)])
        i += 1
    return " ".join(words)[:chars]


def _s7_requests(*, framed: bool = True) -> list[dict]:
    """The two requests §7 tabulates, as the dicts the transport receives.

    ``framed=False`` rebuilds the PRE-framing request (`#10077` / `#10117`): ``_framed``
    is the only splice of :data:`UNTRUSTED_STATE_FRAMING` — a pure ``question +
    framing`` concatenation called at every question site — so replacing it here is the
    same edit removing the clause by hand would make, and the rest of the payload is
    untouched. That is the construction §7's *clause removed* comparison quotes, so it is
    measured here rather than trusted.
    """
    import sys

    sys.path.insert(0, str(ROOT))
    from tortoise import s2_decision_seam as seam

    seen: list = []

    def call(payload: dict) -> dict:
        seen.append(payload)
        return {"model": "jev-1.14.0", "answers": {}, "usage": {"input_tokens": 1}}

    def measure() -> None:
        candidates = [{"id": f"i{i}", "section": "s",
                       "text": _s7_text(_S7_VET_ITEM_CHARS, i), "kind": "k"} for i in range(40)]
        narrative = " ".join(_S7_FILLER[i % len(_S7_FILLER)]
                             for i in range(_S7_NARRATIVE_WORDS))
        seam.vet_verdicts(candidates, narrative, transport=call, batch_size=seam.BATCH_MAX)
        offered = [f"core:kind{i}" for i in range(5)]
        seam.classify_tail([(f"t{i}", _s7_text(_S7_CLASSIFY_ITEM_CHARS, i), offered)
                            for i in range(40)],
                           descriptions={}, transport=call, batch_size=seam.BATCH_MAX)

    if framed:
        measure()
    else:
        original = seam._framed
        seam._framed = lambda question, ref: question.format(ref=ref)
        try:
            measure()
        finally:
            seam._framed = original
    # One request per seam, named — a bare `zip(..., strict=True)` raises `ValueError`
    # ("zip() argument 2 is longer/shorter") if `BATCH_MAX` ever drops below 40, which is a
    # failure that would blame the wrong thing: the construction would be CHUNKED, so the
    # measured bytes stop being the single-request shape §7 tabulates.
    assert len(seen) == 2, (
        f"expected exactly one request per seam (40 items, BATCH_MAX={seam.BATCH_MAX}); "
        f"got {len(seen)} — if BATCH_MAX fell below 40 the §7 construction is chunked and "
        "its bytes are no longer the single-request shape the table records")
    return seen


def _s7_payloads(*, framed: bool = True) -> dict[str, int]:
    """The two requests §7 tabulates, serialized the way ``_decide`` sends them."""
    import json

    return {version: len(json.dumps(payload).encode("utf-8"))
            for version, payload in zip(("vet-v2", "classify-v2"),
                                        _s7_requests(framed=framed), strict=True)}


def test_section_7_cost_rows_are_a_measurement_of_this_head_s_payload():
    """§7's two recorded request sizes must equal a live measurement of the payload.

    THE DEFECT THIS PINS (`tortoise#10077`, tracked as `#10117`): the framing clause is
    exactly the kind of change that *silently* falsifies a recorded number — the doc keeps
    a figure that was true of a request the module no longer builds, and every artifact
    that quotes it inherits the error. §7 is the record an operator uses to decide whether
    arming the seam is affordable, so a carried-over figure is not cosmetic.

    So the test builds the construction §7 states (40 candidate texts of
    `_S7_VET_ITEM_CHARS`, a `_S7_NARRATIVE_WORDS`-word narrative, 40 tail texts of
    `_S7_CLASSIFY_ITEM_CHARS`, top-5 kinds, no descriptions), serializes it with
    `json.dumps` — the exact bytes the seam puts on the wire — and requires the recorded
    `B` cell to BE that number. Byte counts are deterministic, so this is an equality and
    not a tolerance: a future wording change reddens HERE and forces a re-measurement
    instead of leaving a stale figure in four artifacts.

    Non-vacuity: the same construction measured with the framing removed is what moved these
    numbers, so the sizes the record carried BEFORE the framing (39,484 B / 23,292 B — the
    superseded row, which is not the clause-removed measurement) are asserted NOT to be what
    the rows carry — restoring the old row without re-measuring cannot pass, and neither can
    a re-measurement that forgot the framing is in the payload.

    No network, no DB, no model: the transport is a stub that captures the payload.
    """
    measured = _s7_payloads()
    doc_text = DOC.read_text(encoding="utf-8")
    cells = _s7_cost_cells(doc_text)
    assert set(cells) == {"vet-v2", "classify-v2"}, (
        f"§7 must tabulate both prompts by their CURRENT version — found {sorted(cells)}")
    recorded = {version: cell["bytes"] for version, cell in cells.items()}
    assert recorded == measured, (
        f"§7's recorded request sizes are not this head's payload: recorded {recorded}, "
        f"measured {measured} — re-measure and update the table, do not carry the old row")

    # The row the table carried before the framing (39,484 B / 23,292 B) must NOT be what
    # the rows carry: a revert to it without re-measuring cannot pass.
    stale = {"vet-v2": 39_484, "classify-v2": 23_292}
    assert all(recorded[version] != before for version, before in stale.items()), (
        "the superseded pre-framing row is back in the table — the rows must be the FRAMED "
        f"payload's, not the carried-over figures: recorded {recorded}")

    # ── the `tok` column: pinned to the recorded proxy ────────────────────
    # This is the half round 6's design LEFT INERT (review round 7, P2). The proxy exists
    # because no deterministic test can re-derive it, so its whole value is the equality
    # below — without it a token-only edit to §7 ships green.
    published_tok = {version: cell["tok"] for version, cell in cells.items()}
    assert published_tok == _S7_TOKEN_CELLS, (
        f"§7's recorded token cells are not the recorded proxy: published {published_tok}, "
        f"recorded {_S7_TOKEN_CELLS} — either the payload changed (re-measure BOTH columns "
        "with `tiktoken`, recipe on `_S7_TOKEN_CELLS`) or a cell was edited by hand; the "
        "cost column below is derived from these, so a stale token cell silently reprices "
        "the seam")

    # ── the `$` column must re-derive from the `tok` column ───────────────
    # `$0.00047` and `$0.0000118/item` are the rate applied to 11,223 tokens over 40 items —
    # if they can drift from the token cell they are derived from, the table prices the seam
    # off a figure nothing counted.
    for version, cell in cells.items():
        want_total = round(cell["tok"] * _S7_USD_PER_INPUT_TOKEN, 5)
        assert cell["cost"] == want_total, (
            f"§7's `{version}` cost cell does not re-derive from its token cell at "
            f"{_S7_USD_PER_INPUT_TOKEN:g} $/token: published ${cell['cost']}, derived "
            f"${want_total} from {cell['tok']:,} tokens — re-derive the column, or correct "
            "the rate in §7's derivation and `_S7_USD_PER_INPUT_TOKEN` together")
        want_per_item = round(cell["tok"] * _S7_USD_PER_INPUT_TOKEN / cell["items"], 7)
        assert cell["per_item"] == want_per_item, (
            f"§7's `{version}` per-item cell does not re-derive from {cell['tok']:,} tokens "
            f"over {cell['items']} items: published ${cell['per_item']}, derived "
            f"${want_per_item}")

    # ── the same count in §7's prose ──────────────────────────────────────
    # ``**281 tokens/item** here (11,223 / 40)`` carries the identical cell in the form a
    # reader compares against the 343 tokens/item comparator, and no `B ≈ tok` matcher sees
    # it either.
    prose = _S7_TOKENS_PER_ITEM.search(doc_text)
    assert prose is not None, (
        "§7 no longer states the tokens-per-item figure as `**<n> tokens/item** here "
        "(<tok> / <items>)` — if the sentence was reworded, re-anchor this guard rather "
        "than dropping it: the prose cell derives from the `tok` column and drifts with it")
    assert int(prose.group("tok").replace(",", "")) == _S7_TOKEN_CELLS["vet-v2"], (
        f"§7's prose quotes {prose.group('tok')} tokens where the recorded `vet-v2` cell is "
        f"{_S7_TOKEN_CELLS['vet-v2']:,}")
    assert int(prose.group("items")) == cells["vet-v2"]["items"] == 40, (
        f"§7's prose divides by {prose.group('items')} items but the row tabulates "
        f"{cells['vet-v2']['items']} — the per-item count is derived from both")
    assert int(prose.group("per").replace(",", "")) == round(
        _S7_TOKEN_CELLS["vet-v2"] / cells["vet-v2"]["items"]), (
        f"§7's prose states {prose.group('per')} tokens/item, which is not "
        f"{_S7_TOKEN_CELLS['vet-v2']:,} / {cells['vet-v2']['items']} = "
        f"{round(_S7_TOKEN_CELLS['vet-v2'] / cells['vet-v2']['items'])}")

    # ── non-vacuity, in-test: the edit that shipped green must now red ─────
    # The finding was proved by making a token-ONLY edit to the row and watching every
    # assertion stay green (the bytes still matched). Run that exact edit through the same
    # parser, so a future `tok` comparison that is accidentally inert reddens here.
    row = next(line for line in doc_text.splitlines() if line.startswith("| `vet-v2`,"))
    assert _V1_FIGURE.search(row) is None, (
        "a §7 table row now matches the `B ≈ tok` matcher, so the separate token-cell "
        f"capture this test needs would be redundant — re-check the finding: {row}")
    edited = row.replace(
        f"| {cells['vet-v2']['bytes']:,} B | {_S7_TOKEN_CELLS['vet-v2']:,} |",
        f"| {cells['vet-v2']['bytes']:,} B | 9,999 |", 1)
    assert edited != row, f"the token-only mutation did not apply to the row: {row}"
    mutated = _s7_cost_cells(doc_text.replace(row, edited, 1))["vet-v2"]
    assert mutated["bytes"] == measured["vet-v2"], (
        "the mutation must be token-ONLY (bytes untouched) or it proves the byte guard "
        f"rather than this one: mutated {mutated}")
    assert mutated["tok"] != _S7_TOKEN_CELLS["vet-v2"], (
        "a token-only edit to §7's row leaves the token comparison green — the guard is "
        "inert, which is exactly the defect it was added for")


#: A published figure pair: ``39,544 B ≈ 9,103 tok``. Whitespace-tolerant, because the
#: artifacts are hard-wrapped at ~95 columns and a figure that wraps mid-pair is exactly
#: the one a line-anchored regex would silently stop watching (the pre-fix row wrapped:
#: ``39,484 B ≈ 9,265`` / ``tok``). The unit group also takes the OTHER wrap the CHANGELOG
#: actually uses — ``50,004 B ≈ 11,223`` / ``input tokens`` — because `\s+tok` has no way to
#: match a unit whose first word is `input`.
#:
#: ⚠️ Round 8 of PR #10302's review caught the comment that used to sit here: it claimed a
#: "unit alternation" covered that `input tokens` wrap, and there was no alternation at all —
#: the pattern was ``\s+tok(?:en)?s?\b``, so `"50,004 B ≈ 11,223\ninput tokens"` did not match
#: and the very pair the comment named as covered sat OUTSIDE this guard, where a hand-edited
#: token shipped green. A guard comment that overstates its reach is the failure this file
#: exists to refuse, so the group is now ``\s+(?:input\s+)?tok(?:en)?s?\b`` and the test below
#: proves the widened form both matches and flags.
_V1_FIGURE = re.compile(r"(?P<b>[\d,]+)\s*B\s*≈\s*(?P<t>[\d,]+)\s+(?:input\s+)?tok(?:en)?s?\b")

#: What makes a legacy pair readable as HISTORY rather than as a usable baseline.
#: Round 5 of PR #10302's review (P2) found two different pre-framing figures in these
#: artifacts with neither marked, which is how a percentage ends up measured against a
#: baseline nothing counted.
_SUPERSEDED_MARK = re.compile(r"superseded|not\W{0,4}reproduc|not a baseline|not comparable",
                              re.IGNORECASE)

#: How far from a legacy pair its "superseded" notice may sit — one paragraph.
_SUPERSEDED_WINDOW = 600


def _unmarked_legacy_figures(text: str, measured: dict[str, tuple[int, int]],
                             also_measured: dict[str, tuple[int, int]] | None = None) -> list[str]:
    """Published figure pairs that are neither a measurement nor marked stale."""
    known = set(measured.values()) | set((also_measured or {}).values())
    out = []
    for m in _V1_FIGURE.finditer(text):
        pair = (int(m.group("b").replace(",", "")), int(m.group("t").replace(",", "")))
        if pair in known:
            continue
        window = text[max(0, m.start() - _SUPERSEDED_WINDOW):m.end() + _SUPERSEDED_WINDOW]
        if _SUPERSEDED_MARK.search(window) is None:
            out.append(f"{m.group('b')} B ≈ {m.group('t')} tok")
    return out


def test_the_pre_framing_baseline_is_a_measurement_and_a_superseded_row_is_marked():
    """Every published pre-framing figure is either the measurement or marked stale.

    THE DEFECT THIS PINS (PR #10302 review round 5, P2 — at
    `docs/architecture/EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md:1031-1035`): the framing's
    cost is quoted as a percentage OF A BASELINE, and these artifacts named **two**
    different figures as that pre-framing request — `39,544 B ≈ 9,103 tok` /
    `23,332 B ≈ 6,594 tok` in the sentence that derives +23% / +16%, and
    `39,484 B ≈ 9,265 tok` / `23,292 B ≈ 6,112 tok` in the sentence that called the older
    row "the pre-framing request". A 60 B / 40 B difference cannot move a token count by
    162 / 482 tokens, so at most one of them was ever counted — the headline percentage
    was quoted against a cell no artifact had measured, and a reader re-deriving it got
    +21% / +26% instead.

    So this test MEASURES the clause-removed construction's **bytes** (`_framed` replaced
    with the bare question — the only splice of the clause, so this is the request the module
    built before `#10117`) and takes its **token** cells from the recorded proxy, which no
    deterministic test can re-derive (`_S7_TOKEN_CELLS_UNFRAMED`), and requires: every
    ``<n> B ≈ <n> tok`` pair these artifacts publish is either that recorded pair, or sits in
    a paragraph that says it is superseded. A legacy row may be quoted — it may not be
    published as a second, comparable baseline.

    Non-vacuity is in the test, not in a checkout: the same predicate is applied to the
    PRE-FIX sentence, and it must flag it.
    """
    byte_cells = _s7_payloads(framed=False)
    # The `B` cells are MEASURED (exact and hermetic — see `_s7_payloads`): that equality is
    # the tripwire a payload change hits. The `tok` cells are the RECORDED `cl100k_base`
    # proxy (`_S7_TOKEN_CELLS_UNFRAMED`) — re-deriving them needs `tiktoken`, which no CI leg
    # installs and which downloads its BPE ranks at construction, so it cannot run in a
    # deterministic test (PR #10302 review round 6, P1 + P2: the reason is on the constant).
    # Both cells are still GUARDED below: a published `B ≈ tok` pair that is neither the pair
    # asserted here nor labelled superseded reds `_unmarked_legacy_figures`.
    assert byte_cells == {"vet-v2": 39_544, "classify-v2": 23_332}, (
        "the clause-removed construction no longer measures what the artifacts record as the "
        f"pre-framing request (measured {byte_cells}) — a payload change: re-measure the `B` "
        "cells, the `tok` proxy beside them in `_S7_TOKEN_CELLS_UNFRAMED` (recipe there), and "
        "the percentage derived from them (one baseline, not two)")
    # The clause-removed request is the PRE-framing version, so it is published as `-v1`.
    as_v1 = {"vet-v2": "vet-v1", "classify-v2": "classify-v1"}
    measured = {as_v1[version]: (byte_cells[version], _S7_TOKEN_CELLS_UNFRAMED[version])
                for version in byte_cells}
    assert measured == {"vet-v1": (39_544, 9_103), "classify-v1": (23_332, 6_594)}, (
        "the clause-removed baseline §7 publishes is not this head's pair — a payload change "
        "or a re-counted `tok` cell: re-measure BOTH cells and the percentage derived from "
        f"them (one baseline, not two): got {measured}")
    # The FRAMED cells are the sibling test's subject; named here so this guard is about the
    # baseline only, and does not re-judge a legitimately quoted framed figure.
    framed = {version: (cell, _S7_TOKEN_CELLS[version])
              for version, cell in _s7_payloads().items()}
    for artifact in (DOC, CHANGELOG):
        text = artifact.read_text(encoding="utf-8")
        unmarked = _unmarked_legacy_figures(text, measured, framed)
        assert not unmarked, (
            f"{artifact.name} publishes figures that are neither this head's measurement "
            f"(framed or clause-removed) nor marked superseded: {unmarked} — a second, "
            "un-reproduced baseline is how a headline percentage gets quoted against a "
            "number nothing counted. Either quote the measured pair, or say the old row "
            "does not reproduce.")
        published = {(int(m.group("b").replace(",", "")), int(m.group("t").replace(",", "")))
                     for m in _V1_FIGURE.finditer(text)}
        assert set(measured.values()) <= published, (
            f"{artifact.name} must publish the measured clause-removed baseline "
            f"{sorted(measured.values())} — found {sorted(published)}; the framing's cost "
            "percentage is derived from it")

    # The index publishes the FRAMED pair too (its `#5087` row), in `B ≈ tok` form, and it
    # was the one named victim of round 7's Issue 1 that no test read AT ALL — a token-only
    # edit there was invisible while the same edit to §7's table reds the sibling test. It
    # is checked for `unmarked` only: the index quotes the framed request, never the
    # clause-removed baseline, so it is not a baseline publisher.
    unmarked = _unmarked_legacy_figures(INDEX.read_text(encoding="utf-8"), measured, framed)
    assert not unmarked, (
        f"{INDEX.name} publishes figures that are neither this head's measurement nor "
        f"marked superseded: {unmarked} — its `#5087` row must carry the measured pair, "
        "not a transcription of it")

    # The `input tokens` wrap, which round 8 of PR #10302's review found OUTSIDE this guard:
    # the CHANGELOG publishes the framed VET pair as ``50,004 B ≈ 11,223`` / ``input tokens``
    # (it wraps mid-pair), so a `\s+tok` unit could not match it and a hand-edited token there
    # shipped green. The widened group must SEE that form — and must flag an edit under it.
    wrapped = "`vet-v2` **50,004 B ≈ 11,223\ninput tokens**"
    assert _V1_FIGURE.search(wrapped) is not None, (
        "the `B ≈ tok` matcher cannot see the `input tokens` wrap the CHANGELOG actually "
        f"uses, so that pair is unguarded: {wrapped!r}")
    edited = wrapped.replace("11,223", "9,999")
    assert _unmarked_legacy_figures(edited, measured, framed), (
        "a token-only edit to the `input tokens` wrap passes the legacy-figure guard — the "
        f"widened unit group is inert: {edited!r}")

    # Non-vacuity, in-test: the PRE-FIX sentence (verbatim from the changelog at
    # `952640ecab`, kept as a fixture rather than a checkout) must be flagged by the very
    # predicate the real artifacts pass.
    pre_fix = (
        "The figures this entry carried before — `vet-v1` 39,484 B ≈ 9,265 tok and "
        "`classify-v1` 23,292 B ≈ 6,112 tok — were the pre-framing request, kept here "
        "only as the comparison."
    )
    assert _unmarked_legacy_figures(pre_fix, measured) == ["39,484 B ≈ 9,265 tok",
                                                           "23,292 B ≈ 6,112 tok"], (
        "the guard cannot see the defect it exists for — two pre-framing figures labelled "
        "as the same request, neither reproduced: "
        f"{_unmarked_legacy_figures(pre_fix, measured)}")


#: §7's reproduction claim as one sentence: the two `B` deltas, the two percentages they are
#: published as, and the superseded `B` cells they are stated against —
#: ``**60 B** and **40 B**, i.e. **0.15%** and **0.17%**, above the 39,484 B / 23,292 B``.
_S7_TOLERANCE_CLAIM = re.compile(
    r"\*\*(?P<vet_b>[\d,]+) B\*\* and\s+\*\*(?P<cls_b>[\d,]+) B\*\*, i\.e\.\s+"
    r"\*\*(?P<vet_pct>[\d.]+)%\*\* and\s+\*\*(?P<cls_pct>[\d.]+)%\*\*,\s*above the\s+"
    r"(?P<sup_vet>[\d,]+) B /\s*(?P<sup_cls>[\d,]+) B")

#: §7's per-call delta as money: ``a delta of **~\$0.00009**``.
_S7_DOLLAR_DELTA = re.compile(r"a delta of \*\*~\\?\$(?P<delta>[\d.]+)\*\*")

#: `0.15%` is the VET cell's reproduction figure and the classify cell's is `0.17%`, so a
#: publication of the first must carry the second within this many characters (one paragraph).
_TOLERANCE_WINDOW = 200


def _single_cell_tolerance_claims(text: str) -> list[str]:
    """Reproduction figures published for one cell as if they carried both."""
    return [text[m.start():m.start() + _TOLERANCE_WINDOW]
            for m in re.finditer(re.escape("0.15%"), text)
            if "0.17%" not in text[m.start():m.start() + _TOLERANCE_WINDOW]]


def test_section_7_s_reproduction_figures_re_derive_from_the_measured_pair():
    r"""§7's "how close is the re-measurement" numbers must be DERIVED, not typed.

    THE DEFECT THIS PINS (PR #10302 review round 8, P2 + P3 — `S2-DECISION-SEAM.md` §7 and two
    sites in the CHANGELOG): the section justified treating the re-measured clause-removed
    request as the SAME construction as the superseded row by saying the `B` cells agree
    "within **0.15%**" — and on its own numbers they do not: 60 B of 39,484 B is 0.152% and
    40 B of 23,292 B is 0.172%, so the single figure was false for the classify cell, in the
    sentence the headline +23% / +16% rests on. Nine lines below, the per-call delta was
    `+23% of \$0.00039` → `~\$0.00008`: mis-rounded (`0.23 × 0.00039 = \$0.00009`) AND derived
    from `\$0.00039`, the superseded cell the same section declares is "never … a comparison".

    Neither was pinned, and that is the shape: `_unmarked_legacy_figures` matches `B ≈ tok`
    pairs only, so a `%` claim and a `$` claim drift free — the same inert-guard shape round 7
    fixed for §7's `tok` column. So both are re-derived here from the artifacts' own operands:
    the superseded `B` cells §7 names, the measured clause-removed bytes (exact and hermetic —
    `_s7_payloads`), and the recorded token proxy either side of the framing.

    Non-vacuity: the pre-fix sentences are replayed below through the same predicates — the
    single `0.15%` and the `~\$0.00008` both red what the corrected artifacts pass.
    """
    doc_text = DOC.read_text(encoding="utf-8")
    clause_removed = _s7_payloads(framed=False)

    # ── the reproduction percentages, against the cells they are stated against ──
    claim = _S7_TOLERANCE_CLAIM.search(doc_text)
    assert claim is not None, (
        "§7 no longer states the reproduction as `<n> B and <n> B, i.e. <x>% and <y>%, above "
        "the <n> B / <n> B the row carried before` — re-anchor this guard rather than "
        "dropping it: the +23% / +16% headline rests on that claim")
    zipped = (("vet-v2", "vet_b", "vet_pct", "sup_vet"),
              ("classify-v2", "cls_b", "cls_pct", "sup_cls"))
    for version, b_key, pct_key, sup_key in zipped:
        superseded = int(claim.group(sup_key).replace(",", ""))
        want_b = clause_removed[version] - superseded
        assert int(claim.group(b_key).replace(",", "")) == want_b, (
            f"§7 states a {claim.group(b_key)} B reproduction delta for `{version}`, but the "
            f"clause-removed request it names measures {clause_removed[version]:,} B against "
            f"{superseded:,} B = {want_b} B — re-measure, or correct the delta")
        want_pct = round(want_b / superseded * 100, 2)
        assert float(claim.group(pct_key)) == want_pct, (
            f"§7 publishes {claim.group(pct_key)}% for `{version}` where its own operands give "
            f"{want_pct}% ({want_b} B of {superseded:,} B) — a reproduction claim has to bound "
            "what was measured, or it is the 'within 0.15%' falsehood again")

    for artifact in (DOC, CHANGELOG):
        text = artifact.read_text(encoding="utf-8")
        offenders = _single_cell_tolerance_claims(text)
        assert not offenders, (
            f"{artifact.name} publishes the VET cell's `0.15%` as the reproduction figure "
            f"without the classify cell's `0.17%` beside it: {offenders[0]!r} — the two "
            "deltas are 0.152% and 0.172%, so one figure cannot carry both")

    # ── the per-call delta as money, derived from the measured pair ──────
    published = _S7_DOLLAR_DELTA.search(doc_text)
    assert published is not None, (
        "§7 no longer states the framing's per-call cost as `a delta of **~$<n>**` — "
        "re-anchor this guard rather than dropping it: it is the sentence that says what "
        "arming the seam costs on a call that removes memory")
    want_delta = round((_S7_TOKEN_CELLS["vet-v2"] - _S7_TOKEN_CELLS_UNFRAMED["vet-v2"])
                       * _S7_USD_PER_INPUT_TOKEN, 5)
    assert float(published.group("delta")) == want_delta, (
        f"§7 publishes a per-call delta of ${published.group('delta')} where the measured "
        f"pair gives ({_S7_TOKEN_CELLS['vet-v2']:,} − "
        f"{_S7_TOKEN_CELLS_UNFRAMED['vet-v2']:,}) tok × {_S7_USD_PER_INPUT_TOKEN:g} $/token "
        f"= ${want_delta} — derive it from the measured cells, not from the superseded "
        "$0.00039 row this section disqualifies")

    # ── non-vacuity, in-test: the two pre-fix sentences, verbatim ─────────
    pre_fix_tolerance = (
        "(`classify-v1`) with the clause removed — the `B` cells within **0.15%** of the "
        "39,484 B the row carried before, which is what makes the two comparable rather "
        "than merely newer."
    )
    assert _single_cell_tolerance_claims(pre_fix_tolerance), (
        "the tolerance predicate cannot see the defect it exists for — a single `0.15%` "
        "stated for both cells passes it")
    as_published_shape = "a delta of **~\\$0.00008**"
    pre_fix_delta = _S7_DOLLAR_DELTA.search(as_published_shape)
    assert pre_fix_delta is not None and float(pre_fix_delta.group("delta")) != want_delta, (
        "the dollar-delta equality accepts the pre-fix figure (~$0.00008) — it is not "
        "pinning the derivation")


# ── the framing's PROVENANCE must name a page that exists ─────────────────

#: Every artifact that carries the untrusted-data framing's provenance. The reason the
#: check is corpus-shaped: the same sentence moved between all of these in one PR, so a
#: guard watching one would have passed while the others drifted.
#:
#: `SEAM_TEST` is a member because the retracted paraphrase survived one file OVER: the
#: first corpus (module + doc + changelog) left `tests/` out, so round 4 of PR #10302's
#: review found the seam test's own docstring still presenting "be explicit in the trusted
#: fields" as the vendor's prescription while this guard stayed green. The corpus excludes
#: THIS file only, and only because it carries the paraphrase as a deliberate FIXTURE
#: (`_REMEDY_PARAPHRASE`, and the synthetic pre-fix documents below) — an exclusion with a
#: stated reason, not an uncovered artifact.
SEAM_TEST = ROOT / "tests" / "test_s2_decision_seam_5087.py"
_FRAMING_ARTIFACTS = (SEAM_MODULE, DOC, CHANGELOG, SEAM_TEST)

#: The page that actually carries the note, as a path a reader can follow. The note lives
#: in *Jev 1.13 jaggedness* → *Adversarial content*
#: (`https://docs.typesafe.ai/model-jaggedness/jev-1.13`, HTTP 200, fetched 2026-10-29).
_VENDOR_NOTE_PAGE = re.compile(r"model-jaggedness/jev-1\.13|Jev 1\.13 jaggedness")

#: The page an earlier draft of this PR cited. It does not exist: `docs.typesafe.ai/
#: troubleshooting` and `/troubleshooting.md` both return 404 (verified 2026-10-29) and no
#: such entry appears in `https://docs.typesafe.ai/llms.txt`. A provenance pointing at a
#: 404 is unfalsifiable by the reader it is written for — they cannot check the quote, so
#: the citation reads as evidence while being nothing of the kind.
_PHANTOM_PAGE = re.compile(r"Troubleshooting\s*(?:→|->|>)")

#: The vendor's remedy, VERBATIM (`**Instead:** be explicit in the criteria. Test your
#: integration thoroughly before deploying it to many users.`). The paraphrase this PR
#: carried — "be explicit in the **trusted** fields" — is not in that page, and quoting a
#: vendor for words they did not write is the failure the sibling `citation_rot` guard
#: exists for.
_VENDOR_REMEDY = "be explicit in the criteria"

#: The paraphrase that must NOT be presented as the vendor's. It is a fair description of
#: what this seam DOES; it is not what the page says, so it may not wear the quote marks.
#: A REGEX, not a substring: the artifacts said it more than one way — "be explicit in the
#: `trusted` fields", "prescribes being explicit in the trusted fields" — and a plain
#: substring missed the `being` form, which is verbatim the sentence review round 4 found
#: still alive in `tests/`. A marker that cannot match the phrasing it exists to refuse is
#: a guard whose green means nothing.
#:
#: The EMPHASIS run is in the pattern for that same reason, one spelling further out (PR #10302
#: review round 9, P2). Prose in these artifacts renders a word it wants read with emphasis —
#: `*trusted*`, `**trusted**`, `_trusted_`, `***trusted***` — and the emphasis-free marker matched
#: only the bare and backticked forms. The occurrence that round found live (`#10117`'s scoping
#: comment) is written exactly that way — "the vendor's guidance is to be explicit in the *trusted*
#: fields" — so a re-introduction in the ordinary emphasis spelling passed `_provenance_gaps` green
#: on all four corpus members: round 4's failure reproduced one spelling over, in the file that
#: exists because the sentence "survived one file over". The run is absorbed on BOTH sides of the
#: word, so `*trusted*`, `**trusted**`, `_trusted_` and a mixed pair are all refused; the in-test
#: non-vacuity loop below pins each spelling, so the widening cannot be quietly undone.
_EMPHASIS_RUN = r"[*_`]{0,3}"
_REMEDY_PARAPHRASE = re.compile(
    r"\bb(?:e|eing) explicit in the (?:"
    + _EMPHASIS_RUN
    + r"trusted"
    + _EMPHASIS_RUN
    + r"\s+)?fields"
)

#: The vendor scopes its note to `jev-1.13` ("Applies to `jev-1.13`. Last reviewed
#: 2026-10-02") and says the edges may be fixed in later versions, while this seam sends
#: the `jev-latest` alias (it resolved to `jev-1.14.0` at measurement). So asserting the
#: exposure HOLDS at a concrete later version is a claim the cited page does not support —
#: in the safe direction (it over-states the justification, not the risk), but still an
#: unsourced claim inside a document whose whole job is honest provenance.
_VERSION_OVERCLAIM = re.compile(
    r"1\.\d+\.\d+\s+does not treat\s+`*state`*\s+as hostile", re.IGNORECASE)


#: One-line-per-paragraph view of an artifact, for the phrase checks.
#:
#: Comment markers have to go FIRST: these artifacts are hard-wrapped `#:` comment blocks
#: and markdown prose, so a naive `" ".join(text.split())` leaves the token `#:` sitting
#: between the two halves of a quoted sentence that wrapped — `*"be #: explicit in the
#: criteria"*` — and the check then reports a defect for a line wrap.
_COMMENT_MARKER = re.compile(r"^\s*#:?\s*", re.MULTILINE)


def _flatten(text: str) -> str:
    return " ".join(_COMMENT_MARKER.sub("", text).split())


def _provenance_gaps(text: str) -> list[str]:
    """Every provenance defect in ``text``, named — so a failure says WHICH one.

    Phrase checks read the flattened view (see :func:`_flatten`) because a wrapped quote is
    still the verbatim quote. The regexes read the raw text, since each is scoped to a
    single line.
    """
    gaps: list[str] = []
    flat = _flatten(text)
    if _PHANTOM_PAGE.search(text):
        gaps.append("phantom-page: cites `Troubleshooting`, which 404s and is not in llms.txt")
    if not _VENDOR_NOTE_PAGE.search(text):
        gaps.append("unfollowable: names no page path or title a reader can reach")
    if _VENDOR_REMEDY not in flat:
        gaps.append(f"remedy-not-verbatim: the page says {_VENDOR_REMEDY!r}")
    if _REMEDY_PARAPHRASE.search(flat):
        gaps.append("remedy-put-in-the-vendors-mouth: 'trusted fields' is our wording, not theirs")
    if _VERSION_OVERCLAIM.search(text):
        gaps.append("version-overclaim: the note is scoped to jev-1.13, not a later release")
    return gaps


def test_the_framing_cites_a_vendor_page_that_exists_and_quotes_it_verbatim():
    """The untrusted-data framing's provenance must be followable and accurate.

    THE DEFECT THIS PINS (`tortoise#10077` / `#10117`). The clause's justification rests
    entirely on one vendor sentence — *"State is data, and `jev-1.13` does not treat it as
    hostile by default"* — and the PR cited it three times as coming from
    *Troubleshooting → Adversarial content*. Three things were wrong, and none is
    cosmetic:

    1. **The page does not exist.** `docs.typesafe.ai/troubleshooting` returns 404 (and so
       does `/troubleshooting.md`); the note is in *Jev 1.13 jaggedness* →
       *Adversarial content*, `model-jaggedness/jev-1.13`. A reader handed the first path
       cannot check the quote, which is exactly what a citation is for.
    2. **The remedy was put in the vendor's mouth.** The page prescribes *"be explicit in
       the criteria"*; the artifacts said the vendor prescribes "be explicit in the
       **trusted** fields". We do put the clause in `instructions` — a deliberate and
       defensible departure, since `criteria` is the structural option map / level list —
       but a departure is not a quotation, and must not be written as one.
    3. **The version was over-extended.** That note is scoped to `jev-1.13` and says the
       edges may be fixed in later versions, while this seam sends the `jev-latest` alias
       (→ `jev-1.14.0`). So "1.14.0 does not treat `state` as hostile" is unsupported by
       the cited page.

    The quote is verbatim and the exposure is real — this is a provenance guard, not a
    retraction of the fix. What it refuses is a citation that cannot be followed, a quote
    that was not said, and a scope the source does not grant.

    Non-vacuity: the same checkers run over the **pre-fix** sentence of each artifact and
    must red on the defect that artifact actually carried (all three cited the phantom
    page; the module additionally carried the version overclaim; the seam test carried the
    retracted remedy paraphrase). Static: four file reads, no network — the fetched-at
    facts are recorded in the regexes' comments, and the check is deliberately offline so
    it cannot flake on the vendor's uptime.
    """
    for artifact in _FRAMING_ARTIFACTS:
        text = artifact.read_text(encoding="utf-8")
        gap_list = _provenance_gaps(text)
        assert gap_list == [], f"{artifact.name} carries a bad provenance: {gap_list}"

    # ── non-vacuity: each pre-fix sentence reds on its own defect ──────────
    pre_fix_seam = (
        "#: bump is not bookkeeping — 1.14.0 does not treat ``state`` as hostile by\n"
        "#: default (the vendor says so itself, ``docs.typesafe.ai`` → Troubleshooting →\n"
        "#: *Adversarial content*), so an armed run before and after this clause is\n"
        "#: answering a DIFFERENT question.\n"
        "#:   *Adversarial content* note), with the remedy it prescribes: be explicit in\n"
        "#:   the trusted fields rather than hoping the model separates them.\n")
    pre_fix_gaps = _provenance_gaps(pre_fix_seam)
    assert "phantom-page: cites `Troubleshooting`, which 404s and is not in llms.txt" in pre_fix_gaps
    assert ("remedy-put-in-the-vendors-mouth: 'trusted fields' is our wording, not theirs"
            in pre_fix_gaps)
    assert any(g.startswith("version-overclaim") for g in pre_fix_gaps), pre_fix_gaps

    pre_fix_record = (
        "  hostile by default\"*, Troubleshooting → *Adversarial content*) and prescribes\n"
        "  being explicit in the trusted fields;\n")
    assert _PHANTOM_PAGE.search(pre_fix_record)
    # …and the corpus check reds on it too, which is what makes `SEAM_TEST` a member and
    # not decoration: this is verbatim the sentence round 4 found in `tests/` while the
    # three-artifact corpus passed.
    pre_fix_record_gaps = _provenance_gaps(pre_fix_record)
    assert ("remedy-put-in-the-vendors-mouth: 'trusted fields' is our wording, not theirs"
            in pre_fix_record_gaps), pre_fix_record_gaps

    # …and EVERY spelling of it reds, the emphasized ones included (PR #10302 review round 9,
    # P2). This is the non-vacuity half of the widened marker: a spelling the marker cannot see
    # is the retracted attribution shipping GREEN on all four corpus members, and the occurrence
    # live in the fleet today (`#10117`'s scoping comment) is the `*trusted*` form — the one the
    # emphasis-free marker missed. Emphasis is not exotic in these artifacts; it is how they
    # write a word they want read.
    for spelling in (
        "be explicit in the trusted fields",
        "be explicit in the `trusted` fields",
        "be explicit in the *trusted* fields",
        "be explicit in the **trusted** fields",
        "be explicit in the ***trusted*** fields",
        "be explicit in the _trusted_ fields",
        "be explicit in the *_`trusted`_* fields",
        "be explicit in the **trusted fields**",
        "the vendor's guidance is to be explicit in the *trusted* fields",
        "the vendor prescribes being explicit in the **trusted** fields",
    ):
        assert (
            "remedy-put-in-the-vendors-mouth: 'trusted fields' is our wording, not theirs"
            in _provenance_gaps(spelling)
        ), (
            f"the paraphrase marker cannot see {spelling!r} — a re-introduction in this "
            "spelling ships green, which is the failure the marker exists to refuse"
        )

    # …and a correct provenance passes every checker, so the guard cannot be satisfied by
    # mangling the text in some other direction.
    assert _provenance_gaps(
        f"*Jev 1.13 jaggedness* → *Adversarial content* "
        f"(https://docs.typesafe.ai/model-jaggedness/jev-1.13): "
        f"the vendor prescribes, verbatim, *\"{_VENDOR_REMEDY}\"*."
    ) == []
