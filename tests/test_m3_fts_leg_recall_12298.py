"""Pins what #12298's M3 measurement rests on, and the artifact it published.

The brief (`docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`)
publishes numbers a reader will quote, and one of them is a **defect claim about
the shipped code**. Both rot the same two ways this repo already knows:

1. **The mechanism drifts out from under the prose.** The finding is that
   ``sparse.tokenize_sparse_query`` splits on ``_`` while RediSearch's index
   does not, so a stored ``web_search`` is queried as ``search|web`` — two terms
   the index never holds. That is asserted here against the real functions: if
   the tokenizer gains ``_``-preservation (the recommended repair) these tests
   red, and somebody must re-measure instead of the document quietly becoming
   false.
2. **The tables drift away from the artifact.** The brief's published tables are
   recomputed from the committed `artifact.json` and compared CELL BY CELL —
   §0, §3.1, §3.2, §3.3 and §3.5 below, plus §2's census and §3.3's identifier
   decomposition — and the changelog's recall@3 table and fixture-count sentence
   are compared too (⛔ NOT the whole changelog: its paired-p and Δ columns are
   not pinned — saying "the changelog is compared" was wider than the tests, so
   the claim is the narrow true one, #12349 review cycle 3). A
   re-run that moves a number therefore reds here instead of leaving prose that
   the committed artifact contradicts (#12298 review cycle 2, findings 1–3: an
   earlier revision compared FOUR values by whole-document substring and claimed
   in three places that every headline number was covered — a coverage claim the
   pin did not enforce is the silent-rot class this file exists to close).
   ⛔ The cell comparisons are backed by a recomputation from the arms' per-query
   `ranks`: `recall_by_k`, every paired `both`/`only_a`/`only_b`/`discordant` AND
   the McNemar p (a pure function of the discordant split) are re-derived from the
   ranks that produced them, so the summary cells and the prose cannot agree with
   each other while disagreeing with the run (#12349 review, P2). `ranks` is the
   only per-query record the artifacts carry, and two populations live in them —
   `tokens_controlled_subset` is computed over 174 rows, not 300 — so the subset's
   cells are recomputed from the ranks restricted to the NON-degenerate ids, which
   the artifact records (`arms["<b>"]["degenerate_ids"]`).

The recovery arm's own terms are pinned as a CLASS, not as strings: an additive
arm that could ever *remove* a term would not be the #8933 pattern the brief
says it is, and its zero-regression property would stop being structural.

⛔ Nothing here runs the measurement — it needs a live FalkorDB, a Postgres with
`pg_search`, and a real capture corpus. That is deliberate: a test that needed
those would be skipped on CI and would guard nothing. What is pinned is the
reasoning surface (this is #11901's own convention, followed rather than
reinvented).
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
from collections import Counter
from pathlib import Path
from typing import ClassVar

import pytest
from markdown_it import MarkdownIt

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "docs" / "research" / "2026-11-13-12298-m3-fts-leg"
BRIEF = DOC_DIR / "research-brief.md"
ARTIFACT = DOC_DIR / "artifact.json"
POPULATION = DOC_DIR / "population.json"
TOOL = REPO_ROOT / "tools" / "m3_fts_leg_recall.py"
CHANGELOG = (REPO_ROOT / "docs" / "architecture"
             / "SEARCH-ARCHITECTURE-CHANGELOG.md")
SEARCH_ARCH = REPO_ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md"


@pytest.fixture(scope="module")
def tool_mod():
    assert TOOL.exists(), f"missing harness {TOOL}"
    spec = importlib.util.spec_from_file_location("m3_fts_leg_recall", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def artifact() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def population() -> dict:
    assert POPULATION.exists(), f"missing committed population {POPULATION}"
    return json.loads(POPULATION.read_text())


@pytest.fixture(scope="module")
def brief() -> str:
    assert BRIEF.exists(), f"missing committed brief {BRIEF}"
    return BRIEF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brief_text(brief: str) -> str:
    """The brief as text, for the claims that must be PRESENT rather than equal."""
    return brief


@pytest.fixture(scope="module")
def changelog() -> str:
    assert CHANGELOG.exists(), f"missing committed changelog {CHANGELOG}"
    return CHANGELOG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def search_arch() -> str:
    assert SEARCH_ARCH.exists(), f"missing committed decision doc {SEARCH_ARCH}"
    return SEARCH_ARCH.read_text(encoding="utf-8")


# ── 0. reading the published tables, to the CELL ───────────────────────────
# The brief's tables are hand-written prose products, so a whole-document
# substring test is not a pin: `"0.7133" in brief` stays green when the §0 cell
# that STATES it is rewritten to 0.5000, because the number also appears four
# other times (measured on the earlier revision). These helpers compare cells.

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def _table(text: str, *header_needles: str) -> list[list[str]]:
    """Body rows of the FIRST pipe table whose header carries every needle."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        if not all(any(n in c for c in _cells(line)) for n in header_needles):
            continue
        rows = []
        for body in lines[i + 1:]:
            if not body.lstrip().startswith("|"):
                break
            cells = _cells(body)
            if all(set(c) <= set("-: ") for c in cells):
                continue          # the `|---|---|` rule
            rows.append(cells)
        return rows
    raise AssertionError(f"no table whose header carries {header_needles!r}")


def _num(cell: str) -> float:
    """The first number in a cell, with the brief's bold/backtick decoration off."""
    m = re.search(r"-?\d+(?:\.\d+)?", _plain(cell))
    assert m, f"no number in the cell {cell!r}"
    return float(m.group())


def _rendered_heading_lines(doc: str, skip_through: int = 0) -> list[int]:
    """0-based source lines that COMMONMARK renders as headings.

    ⛔ The oracle for the heading-shape guard, instead of a hand-rolled regex
    (#12349 round 20, P1 + P2). Enumerating the bad forms had failed twice — the
    round-19 list missed setext underlines of 1-2 characters or 1-3 space indent,
    headings inside list items, and `# Appendix` terminating nothing — while its
    setext arm FALSELY refused `---` used as a thematic break under an ATX
    heading, after a fence, and after a blockquote. `markdown-it-py` is a declared
    test extra (`pyproject.toml`), already used as a CommonMark oracle by
    `tests/test_blog_authoring_doc.py`, so the guard can ask the parser rather
    than guess. Measured: `## 3. Results` + `---` -> heading + `hr` (NOT a
    setext), while `Results` + `   ---` -> a heading.
    """
    return [tok.map[0] for tok in MarkdownIt("commonmark").parse(doc)
            if tok.type == "heading_open" and tok.map
            # ⛔ `> skip_through` dropped LINE 0 of a document with no
            # frontmatter (`skip == 0`), so arm 1 never saw a heading on the
            # first line — and the enclosing-section arm below depends on
            # seeing it (round 24).
            and tok.map[0] > (skip_through if skip_through else -1)]


def _unscoped_heading_forms(doc: str,
                            readable: re.Pattern | None = None) -> list[str]:
    """Every section opener `_scope_slice` cannot see — as messages, one per line.

    ⛔ The guard on the pins' own INSTRUMENT (#12349 rounds 19-21), factored out of
    the asserting test so its COVERAGE is itself pinned:
    `test_every_rendered_heading_form_the_helper_cannot_read_is_refused` drives each
    form through it, asserts the form really RENDERS a heading (a leak case that
    renders none proves nothing — round 21, P2), and asserts the guard refuses it.

    Three sources, because no single one is complete:

    1. THE PARSER. `markdown-it-py` (a declared test extra, already the CommonMark
       oracle in `tests/test_blog_authoring_doc.py`) is asked which lines it
       renders as headings — ATX of any level, ATX indented up to three spaces or
       behind blockquote markers, and setext underlines of ANY run length at any
       valid indent, including one inside a list item. Enumerating these by regex
       failed twice (rounds 19 and 20), each time on a form nobody listed.
    2. LOOKALIKES THE PARSER DOES NOT CALL A HEADING but a reader and
       `_scope_slice` both cannot see: a `#` run with no space after it
       (`##3. Results` renders as prose), and a raw HTML heading.
    3. A SETEXT UNDERLINE UNDER PROSE — the spec allows `=`/`-` runs of ANY length
       and up to 3 spaces of indent. A `-`/`=` run whose PRECEDING line starts with
       `#`, `>`, ``` or `|` is skipped, which is what keeps the round-20 false
       positives out (`---` under an ATX heading, after a fence, after a blockquote)
       and the changelog's own `---` separators (they follow a blank line).
       ⚠️ KNOWN OVER-BLOCK, category B, recorded rather than chased (#12349 round
       22): that exclusion is a four-item PREFIX test, NOT the parser's `hr`
       verdict, so `---` after a list item, an indented code block, a
       link-reference definition or an HTML block is refused with a message that
       calls it a setext heading. No committed document trips it (all three guard
       clean); the consequence if it is never fixed is friction on a future docs
       edit, not a wrong result — and the repair is to ask the parser for `hr`,
       never to weaken the guard.

    The INVERSE hazard — a `## `-prefixed line inside a FENCED block truncates a
    section the document continues — is NOT fail-closed, contrary to what this
    docstring said before round 21: a presence pin can be satisfied from text
    BEFORE the truncation while the section's real text after it is never read
    (measured, M-7). It is refused by its own arm, and so is the INDENTED-code
    form of the same shape, which the parser does not even render as a heading.
    """
    readable = readable or _READABLE_HEADING
    lines = doc.splitlines()
    skip = 0
    if lines and lines[0].strip() == "---":
        # YAML frontmatter: under the commonmark preset its closing `---` renders
        # the `title:` line as an h2, which is not a section opener.
        skip = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    bad: list[str] = []
    reported: set[int] = set()

    def flag(i: int, why: str) -> None:
        if i in reported or (skip and i <= skip):
            return
        reported.add(i)
        bad.append(f"line {i + 1}: {lines[i][:60]!r} {why}")

    # ⛔ THE ENCLOSING SECTION NUMBER (#12349 round 24, P1). `_scope_slice` keeps
    # every `###` inside the enclosing `##` slice, so a `### 3.9 …` heading written
    # INSIDE §2 is absorbed by §2's scope — and `_READABLE_BRIEF` has to accept
    # `### \d+.\d+ ` for the REAL subsections, so arm 1 cannot refuse it on shape
    # alone. The predicate needs the number, not a wider regex: a subsection is
    # readable only when its major number is the enclosing section's. Measured:
    # falsify §2, restate under `### 3.9 Restated finding` -> 117 passed.
    enclosing_major: list[str | None] = []
    cur: str | None = None
    for ln in lines:
        # ⛔ The trailing period is OPTIONAL in `_READABLE_ARCH` (`## 2 …`) and in
        # `_scope_slice`'s own `^## 2\.? `, so the tracker must accept it or it
        # abstains for a heading form that document's own guard allows
        # (round 24 review, P2).
        major = re.match(r"^## (\d+)(?:\.\d+)?\.? ", ln)
        if major:
            cur = major.group(1)
        enclosing_major.append(cur)
    for i in _rendered_heading_lines(doc, skip):
        minor_h3 = re.match(r"^### (\d+)\.\d+ ", lines[i])
        if (minor_h3 and enclosing_major[i] is not None
                and minor_h3.group(1) != enclosing_major[i]):
            flag(i, "is a subsection heading numbered for a DIFFERENT section — "
                    "`_scope_slice` keeps every `###` inside the enclosing `##` "
                    "slice, so its body satisfies the PRECEDING section's pins")
        if not readable.match(lines[i]):
            # ⚠️ `# ` is accepted by every document's pattern on purpose, and the
            # reason is a property of `_scope_slice`, not of markdown: an `h1`
            # ENDS a level-2 slice (`end` is `^#{1,2} `), so an `h1` cannot be
            # absorbed by the preceding § — it is fail-CLOSED instead (a `# `
            # section is not readable, so `_scoped` asserts loudly if a pin ever
            # needs one). Refusing it here was measured as over-blocking: the
            # mid-document-`h1` case does not leak, because the slice stops at it.
            flag(i, "is a heading CommonMark renders but `_scope_slice` cannot "
                    "read (it anchors on two forms at column 0), so the "
                    "PRECEDING section's scope runs past its own boundary "
                    "through it")
    rendered = set(_rendered_heading_lines(doc, skip))
    code_lines = _code_block_lines(doc)
    for i, line in enumerate(lines):
        # ⛔ NOT `readable.match(line)`: `_scope_slice`'s terminator is `^## `
        # (and `^#{2,3} ` for a level-3 scope) whatever the heading SAYS, so a
        # fenced `## Restated finding` truncates a scope just as a fenced
        # `## 3. Results` does. Anchoring on the readable forms left the
        # non-numbered case uncovered (measured: the truncated §2 still
        # satisfied nothing, but the guard did not say why).
        if re.match(r"^#{1,6}(?:[ \t]|$)", line) and i > skip and i not in rendered:
            # ⛔ THE INVERSE (round 21): a line that LOOKS like a readable opener
            # but which the parser does not render as a heading — `## ` inside a
            # fenced block — TERMINATES a scope the document continues, so a
            # presence pin can be satisfied from text BEFORE the truncation while
            # the section's real text after it is never read. Measured (M-7): a
            # fenced `## 3. Results` at the end of §2 with a false triple inside §2
            # was green at 74 tests; it reds here.
            flag(i, "looks like a readable section opener but is not a heading "
                    "CommonMark renders (a `## ` inside a fenced block, say) — it "
                    "TRUNCATES the preceding section's scope")
        elif i in code_lines and re.match(
                # ⛔ `)*`, not `)?`: container markers NEST, and one level was the
                # whole distance between this arm and useless (`    - - ## 3.`
                # scored 114 green with §2 falsified — round 22 review, P1).
                r"^[ \t]*(?:(?:>|[-*+]|\d{1,9}[.)])[ \t]+)*[ \t]*"
                r"(?:#{1,6}(?:[ \t]|$)|<h[1-6][\s>/])", line, re.IGNORECASE):
            # ⛔ DERIVED FROM THE PARSER, not a fourth regex: ANY `#`-run or `<hN>`
            # inside a code block (fenced or indented) is a line `_scope_slice`
            # cannot see and the parser does not render, so the PRECEDING section's
            # scope runs past it. Round 21's arm required the `#` run IMMEDIATELY
            # after the indent, so `<indent><container marker>` + `## 3.` walked
            # straight through it — measured on the real brief: `    1. ## Restated
            # finding`, `\t- ## Restated finding` and `    <h2>Restated
            # finding</h2>` left 105 tests GREEN with §2's published claim
            # falsified.
            #
            # ⛔ THE `#` RUN OR `<hN>` MUST BE THE LINE'S FIRST TOKEN (after an
            # optional indent and an optional container marker). This repo's own
            # fenced examples carry TRAILING `#` comments — `'web_search' ->
            # [['web_search', 8.0]]   # the index …` in the brief, `graph window =
            # 0.75 · p   # a graph …` in SEARCH-ARCHITECTURE.md — and refusing
            # every `#` inside a code block flagged all five of them, i.e. it
            # over-blocked exactly the documents this guard protects. A line whose
            # FIRST token is the hash run is a heading-shaped line smuggled into
            # code; a trailing comment is prose.
            flag(i, "is a `#` run or `<hN>` inside a CODE BLOCK (fenced or "
                    "indented) — the parser renders no heading and `_scope_slice` "
                    "sees no boundary, so the PRECEDING section's scope runs "
                    "past it")
        elif re.match(r"^(?: {4,}|\t| {1,3}\t)#{1,6}(?:[ \t]|$)", line):
            # ⛔ INDENTED CODE, the arm the docstring used to call fail-closed
            # (#12349 round 21, P2). 4+ spaces or a tab makes the line an indented
            # code block: the parser renders no heading, so the inverse arm above
            # does not see it, and `_scope_slice` matches no boundary either — the
            # preceding § absorbs everything after it. Measured: falsify §2 and
            # restate the truth under `    ## Restated finding` and the guard +
            # reuse pins stayed green; a FENCED block, by contrast, is caught by
            # the inverse arm.
            flag(i, "is a `#` run indented by 4+ spaces or a tab — "
                    "`_scope_slice` sees no boundary there, so the PRECEDING "
                    "section's scope runs past it. ⛔ This arm is a FAIL-CLOSED "
                    "BACKSTOP, not a code-block oracle: the parser does not always "
                    "call such a line code (a paragraph continuation renders as "
                    "prose) and refusing those too costs only a docs edit")
        elif (not readable.match(line)
                and re.match(rf"^{_BLOCK_PREFIX}#{{1,6}}\s?\d+(?:\.\d+)*\.\s",
                             line)):
            # ⛔ A SECTION-NUMBER LOOKALIKE only: `##3. Results` renders as prose
            # but a reader sees a section, and `_scope_slice` cannot see it. NOT
            # any line starting with `#` — measured false positive on
            # SEARCH-ARCHITECTURE.md's `  #2206's agreement indicator …`, which is
            # prose beginning with an issue reference (#12349 round 20 resolution).
            flag(i, "starts with a `#` run followed by a section number but is not "
                    "a readable section opener — a reader sees a section here and "
                    "`_scope_slice` does not")
        elif re.match(rf"^{_BLOCK_PREFIX}<h[1-6][\s>/]", line, re.IGNORECASE):
            # `_BLOCK_PREFIX`, not a column-0 anchor: `> <h2>…` and `- <h2>…`
            # render as headings too and the parser emits them as raw HTML, not as
            # `heading_open` tokens (#12349 round 21, P1).
            flag(i, "is a raw HTML heading — it renders as a section boundary and "
                    "`_scope_slice` cannot see it")
        elif (i and lines[i - 1].strip()
              and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", line)
              and not lines[i - 1].lstrip().startswith(("#", ">", "```", "|"))):
            flag(i, "is a SETEXT underline under prose (the spec sets no minimum "
                    "run length and allows 1-3 spaces of indent) — it turns that "
                    "prose into a heading `_scope_slice` cannot see")
    return bad


def _code_block_lines(doc: str) -> set[int]:
    """0-based lines the parser renders as CODE (fenced or indented).

    A `#` run or an `<hN>` on one of these lines is TEXT, not a section: it is
    invisible to `_scope_slice` (column-0 anchored) and to
    `_rendered_heading_lines` (which lists only `heading_open`), so the preceding
    section absorbs everything after it. Asking the parser where the code blocks
    are is the derived form of the question; a regex over indent widths requires
    the marker to sit immediately after the indent, and round 21 measured that
    `<indent><container marker>## 3.` walks through such a rule.
    """
    out: set[int] = set()
    for tok in MarkdownIt("commonmark").parse(doc):
        if tok.type in ("fence", "code_block") and tok.map:
            out.update(range(tok.map[0], tok.map[1]))
    return out


def _assert_only_readable_headings(doc: str, readable: re.Pattern,
                                   label: str) -> None:
    """Every section opener must be one `_scope_slice` can read."""
    bad = _unscoped_heading_forms(doc, readable)
    assert not bad, f"{label}: " + "; ".join(bad)


# The forms each guarded document uses and `_scope_slice` can read.
_READABLE_BRIEF = re.compile(r"^(?:# [^#\s]|## \d+\. |### \d+\.\d+ )")
_READABLE_CHANGELOG = re.compile(r"^(?:# [^#\s]|## `#\d+` )")
_READABLE_ARCH = re.compile(r"^(?:# [^#\s]|## \d+(?:\.\d+)?\.? |### \d+\.\d+ )")


def _scope_slice(brief: str, heading: str) -> str:
    """The RAW text of one markdown section — the slice `_scoped` flattens.

    Raw is what a table parse needs (`_table` reads lines), so the two forms are
    one derivation, not two: `_scoped` is this, flattened.
    """
    if re.fullmatch(r"\d+(?:\.\d+)?", heading):
        level = "##" if "." not in heading else "###"
        # `## 2. Method` carries a period, `### 3.3 Per class` does not — measured.
        own = rf"^{re.escape(level)} {re.escape(heading)}\.? "
    else:
        # A `## ` heading identified by a LABEL — the changelog's entries are
        # `## \`#12298\` — …`, which has no numeric prefix to anchor on.
        level = "##"
        own = rf"^## (?=[^\n]*{re.escape(heading)})"
    # A TOP-LEVEL section OWNS its subsections: end at the next `##` only, not at
    # the first `###` (measured: ending at any heading made `_scoped(brief, "4")`
    # the heading line alone, and §4's Result line lives in 4.1). An `h1` DOES end
    # it — `# Appendix` mid-document was otherwise absorbed into the preceding
    # section (#12349 round 20, P1).
    end = r"^#{1,2} " if level == "##" else r"^#{1,3} "
    hit = re.search(rf"(?ms){own}.*?(?={end}|\Z)", brief)
    assert hit is not None, f"the brief no longer carries a `{level} {heading}` section"
    starts = re.findall(rf"(?m){own}", brief)
    assert len(starts) == 1, (
        f"the brief carries {len(starts)} `{level} {heading}` headings — the pin "
        "cannot tell which section it is reading")
    # The slice must END at a heading or at EOF. This helper anchors on `## `/`### `
    # ONLY, so a document whose next heading is rendered some other way cannot be
    # seen from here — the OTHER side of that guarantee is the document-shape
    # guard, `test_the_brief_uses_only_heading_forms_the_scope_helper_can_see`,
    # which refuses ANY heading form this helper cannot see (measured leak, round
    # 17: a setext `3. Results` / `----------` made `_scoped(brief, "2")` 14 740
    # chars = §2 PLUS §3's body; round 19: the same leak via `####`, `#####`,
    # `######` and `> ##`, which enumerated checks had missed).
    assert hit.end() == len(brief) or re.match(end, brief[hit.end():]), (
        f"the `{heading}` scope ended in the middle of the document without a "
        "heading — refusing a scope that may have run past its section")
    return hit.group(0)


def _scoped(brief: str, heading: str) -> str:
    """The whitespace-flattened text of ONE markdown section — and only that one.

    ⛔ THE ROOT FIX for a class that survived three review rounds (#12349 rounds
    14-16). A pin that asserts `"<a published number>" in <the whole document>`
    is satisfied by a copy of that number ANYWHERE — so the section the pin is
    named after can be falsified in place while every gate stays green, provided
    the true text also appears in another section. That is not hypothetical: a
    planted restatement turned §2's census (M-A2), §3.3's decomposition (M-B1..3),
    §3.5's fixture summary (M-C1) and §0's Reading-2 bullet (M-D1) green while
    each was contradicted in its own section.

    Two properties make this the scope and not a substring of a scope:

    * HEADING-ANCHORED on the RAW document — `^## <heading>` at a line start —
      before any flattening, so a MENTION of `## 2. ` inside prose cannot open a
      scope (M-A4: flattening first made a §1 mention indistinguishable from the
      heading, and §2's whole block was then satisfied from §1's tail).
    * EXACTLY ONE such heading is required. A second `## 2.` is a document
      defect the pin should report, not something to resolve by taking `[1]`.
    """
    return " ".join(_scope_slice(brief, heading).split())


# A numeric token as it appears in these documents: an OPTIONAL sign (`-`, `+`,
# U+2212) and a decimal. The sign is part of the VALUE — `_magnitude` strips it
# when matching `values`, so a caller keeps passing the magnitude it derives from
# the artifact while the shape still matches (and therefore counts) a falsified
# copy whose sign was flipped (#12349 round-24 reviewer, P2).
_SIGNED_NUMBER = r"[-\u2212+]?\d+(?:\.\d+)*(?:e[-\u2212+]?\d+)?"

# ⛔ THE WORDS THAT CAN INVERT A CLAIM (#12349 round-24 reviewer, cycle 5: five
# consecutive cycles found one of these left unnamed, so the discipline becomes a
# GATE like the numeric one). A claim's direction lives in its comparatives, its
# polarities and its operators — a small, enumerable class — and a pivot left
# literal is exactly what turns the pin back into a presence check. This is NOT a
# stop-word list: a word is here only because a document could falsify the claim
# by rewriting it.
_DIRECTIONAL = frozenset({
    "greater", "less", "strictly", "weakly", "ahead", "behind", "inside",
    "outside", "adds", "removes", "identical", "identically", "differ",
    "differs", "differently", "exact", "approximate", "reachable",
    "unreachable", "refuses", "accepts", "asserted", "ignored", "ascending",
    "descending", "zero", "non-zero", "nonzero", "not", "fail", "pass",
    "significant", "recovered", "two-sided", "one-sided", "tie", "tied",
    # ⛔ POLARITY DETERMINERS are polarities too (round-24 reviewer, cycle 6):
    # `any` → `no`, `nothing` → `something`, `both` → `neither` each invert the
    # claim without touching its numbers, and the set's own docstring claims to
    # cover polarities.
    "any", "no", "none", "both", "neither", "nothing", "something", "all",
    "every", "only", "plus", "minus",
    # ⛔ The FAMILIES of the entries above, not just the instances a reviewer
    # happened to measure (round-24 reviewer, cycle 7: six one-token
    # falsifications at five live claims all pinned green — `constant`→`variable`,
    # `holds`→`breaks`, `largest`→`smallest`, `find`→`miss`,
    # `shipped`→`postgres`, `above`→`below`).
    "larger", "smaller", "largest", "smallest", "higher", "lower", "holds",
    "breaks", "constant", "variable", "find", "miss", "above", "below", "still",
    "never", "shipped",
    # ⛔ The INFLECTED forms the documents actually use, and the arithmetic verb
    # (round-24 reviewer, cycle 9): `find`/`miss` never match the prose's `found`/
    # `missed`, so an entry that cannot fire is the same false-pass shape as a
    # missing entry — the file already recorded this defect once for `m3-000014`.
    "found", "missed", "sum",
    # ⛔ Two more of the same class at live claims (round-24 reviewer, cycle 10):
    # `does beat it` (the verb the whole §6.2 paragraph turns on) and
    # `the same 300` (`same`/`different` is the reuse claim's content).
    "beat", "beats", "same", "different",
    "+", "\u2212",
    "==", "!=", ">=", "<=", "<", ">", "\u2192", "\u00d7", "\u00f7", "\u00b1",
    "\u2248",
})
# ⛔ The ARITHMETIC operators are matched too (round-24 reviewer, cycle 6): a set
# entry for a symbol the tokeniser cannot see is a no-op — `\u00d7` listed above
# would never fire otherwise. `/` is deliberately NOT in the alternation: it is the
# "of" separator in these documents' `180 / 90 / 30` lists, so a site that pins one
# names it in `free` explicitly rather than every list-triple being refused.
# ⛔ The arithmetic operators are matched only when they are NOT the sign of a
# number: `62.3 + 2.2` is an operator, `+1.33 pts` is a signed value (and its sign
# belongs to `_SIGNED_NUMBER`). A `[+−]` alternative without those lookarounds
# would flag every signed value in the file (round-24 reviewer, cycle 9).
_WORD_OR_OPERATOR = re.compile(
    r"[A-Za-z][A-Za-z'\-]*|[<>=!]=?|\u2192|\u00d7|\u00f7|\u00b1|\u2248"
    r"|(?<!\d)[+\u2212](?!\d)")


def _strip_free(text: str, free: tuple[str, ...]) -> str:
    """Remove each named free ALTERNATIVE at TOKEN boundaries, case-insensitively.

    ⛔ Substring removal is a fail-OPEN in the gate (#12349 round-24 reviewer,
    cycle 7): naming `no` deleted every `not`/`nothing` in the claim, and naming
    `identical` deleted every `identically`, so the gate reported nothing while a
    pivot sat literal. A free entry must only ever remove ITSELF — and it must
    remove it at a token boundary, or `not` eats the `not` inside `nothing`.
    """
    alts = sorted({a for entry in free for a in entry.split("|")},
                  key=len, reverse=True)
    for alt in alts:
        text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(alt) + r"(?![A-Za-z0-9_])",
                      " ", text, flags=re.I)
    return text


def _unnamed_words(true: str, free: tuple[str, ...]) -> list[str]:
    """DIRECTIONAL words/operators in `true` that the caller did not name in `free`.

    The mirror of `_unnamed_values`, and the answer to a class five review cycles
    found by hand (#12349 round 24): a pivot left literal stays literal in the
    shape, so a falsified copy no longer matches and the restated truth satisfies
    the pin — the same silent degradation an unnamed number causes.
    """
    residual = _strip_free(true, free)
    return [tok for tok in _WORD_OR_OPERATOR.findall(residual)
            if tok.lower() in _DIRECTIONAL]


def _magnitude(token: str) -> str:
    """The unsigned form of a numeric token — what `values` carries."""
    return token.lstrip("-\u2212+")


def _unnamed_values(true: str, values: tuple[str, ...],
                    free: tuple[str, ...]) -> list[str]:
    """Numeric tokens in `true` that are neither a NAMED value nor a LABEL digit.

    A label digit IDENTIFIES the claim rather than being its content — `k=5`,
    `k=15`, `§4.1`, `#12298`, `n=300` — and must stay literal, or two claims of
    the same wording about different windows collide (measured: a true
    `while at k=10 …` sentence reddened the k=5 pin). Everything else is a value
    and must be named, because an unnamed value is the one thing that turns this
    helper back into a presence pin without anybody noticing.
    """
    residual = _strip_free(true, free)
    named = {_magnitude(v) for v in values}
    out = []
    for match in re.finditer(_SIGNED_NUMBER, residual):
        token = match.group(0)
        if _magnitude(token) in named:
            continue
        after = residual[match.end()] if match.end() < len(residual) else ""
        # ⛔ Letters AFTER the number are a unit glued to the value (`1.33pts`),
        # not an identifier — exempting them was a bypass (#12349 round-24
        # reviewer, cycle 5). Letters BEFORE it are an identifier only when they
        # are two or more (`BM25`) or the token is a hyphenated id (`m3-000014`).
        if after.isalnum() or after == "_":
            out.append(token)
            continue
        # ⛔ The hyphenated-id exemption is decided from the LOCAL WORD only
        # (round-24 reviewer, cycle 6): a residual-wide search let one `m3-000014`
        # anywhere in the claim exempt every number glued to a single letter, so
        # `m3-000014 gains p5 rows` pinned with `5` literal. Two letters before the
        # token still make it an identifier (`BM25`); one letter plus a hyphenated
        # numeric run makes it one too (`m3-000014`, both halves).
        word_start = residual.rfind(" ", 0, match.start()) + 1
        word_end = residual.find(" ", match.end())
        word = residual[word_start:len(residual) if word_end < 0 else word_end]
        # ⛔ The IDENTIFIER test reads the word with its punctuation STRIPPED
        # (#12349 review, P2). The docs write every identifier in backticks, so
        # `m3-000014` arrived as `` `m3-000014` `` and the hyphenated-id test
        # below could never match it — the exemption existed and was unusable
        # on exactly the form these documents use, which left `3` and `000014`
        # reported as unnamed values in a claim that names no value at all.
        bare = word.strip("`'\"()[]{}.,;:!?")
        prefix = re.search(r"[A-Za-z_]+$", residual[:match.start()])
        if (prefix and len(prefix.group(0)) >= 2) or re.fullmatch(
                r"[A-Za-z][A-Za-z0-9]*(?:-\d+)+", bare):
            continue  # part of an IDENTIFIER (`BM25`, `m3-000014`)
        # ⛔ NO bare `=` here (#12349 round-24 reviewer, cycle 4): `p = 0.34` is
        # the form every p-value pin uses, so exempting `=` exempted the exact
        # values this gate exists to require. Only real LABELS are exempt.
        if re.search(r"(?:k=|k<|k>|n=|§|#|@)\s*$", residual[:match.start()]):
            continue
        out.append(token)
    return out


def _shape(text: str, values: tuple[str, ...] = (),
           free: tuple[str, ...] = ()) -> str:
    """`text` as a SHAPE: the CLAIM'S OWN content becomes wildcards, nothing else.

    ⛔ THE NEXT SCOPE INWARD (#12349 round 24, closing the class ten rounds
    converged on). A §-scoped pin of the form `assert <true text> in <section>`
    is a PRESENCE check, so a claim falsified IN PLACE stays green whenever the
    same section also still carries the true text — which it does whenever a
    falsifier plants one. The file already had the remedy in one place (the
    census pin matches the claim's SHAPE and requires exactly one match); this
    generalises it: the claim's own content is wildcarded, the sentence that
    carries it must be present exactly once.

    Two knobs, and BOTH are about the pin still IDENTIFYING its claim:

    * `values` — the numbers the caller derives from the artifact (the claim's
      VALUES). Wildcarding EVERY digit instead makes the shape match a
      DIFFERENT, true statement of the same wording, and the pin then false-reds
      on correct prose (measured by the round-24 reviewer: a true
      `while at k=10 it is …` sentence reddened the k=5 pin because `k=` was
      wildcarded too). Every digit NOT named here (`k=5`, `§4.1`, `p < 0.05`)
      stays literal, and that is what tells two claims apart.
    * `free` — the WORDS that are the claim's content rather than its identity
      (`identical`, `differs`, the operators in `` `==`, not `>=` ``). A
      wording-only claim has no number to wildcard, so without this a
      `identical` → `differ` rewrite stops matching at all and a planted true
      copy satisfies the pin — the very class this helper closes.

    Declared residual: a falsification that rewrites MORE than the named
    value/word (say `count is asserted` → `count is not compared`) no longer
    matches the shape, so it reds only while the true text is ABSENT. A shape
    cannot anticipate text it cannot name; the claim is that the pin covers the
    content named here, not that it can recognise arbitrary rewording. ⛔ That
    includes DELETION of a free word — a shape can only match text that is
    PRESENT, so a pivot word removed outright (`not significant` → `significant`)
    is invisible here and needs the file's negative-assertion treatment at the
    call site instead (see §6.2's status note). ⛔ And the MIRROR of the
    author-time gate: the gate requires every NUMBER to be named, and there is no
    equivalent for a WORD — a single unnamed content word (`refuses` → `accepts`,
    `inside` → `outside`, `two-sided` → `one-sided`) degrades the pin to a
    presence check exactly as silently as an unnamed number did. So `free=` must
    name every word the claim's direction turns on — and `_only_claim` now REFUSES
    a claim that leaves a DIRECTIONAL word literal (`_DIRECTIONAL`), which is the
    mirror of the numeric gate. A `free` entry may also be an ALTERNATION
    (`"strictly|weakly"`): a slot is then matched by exactly those words instead
    of `\\S+`, which is what keeps two ADJACENT free slots from collapsing into
    `\\S+ \\S+` and matching the whole section (measured: `strictly greater` with
    both words freed matched 145 pairs in §4.1).

    ⛔ A SIGN is part of the value, not part of the wording (#12349 round-24
    reviewer, P2): every caller passes the MAGNITUDE, so with an unsigned token a
    `−0.67` → `+0.67` rewrite stopped matching and the plant went green — in the
    four sentences this file calls "the sentence that turns a sign change into a
    floor". So the numeric token is signed (`_SIGNED_NUMBER`) and matching is by
    magnitude (`_magnitude`), which puts the sign inside the wildcard.
    """
    # Each `free` entry maps every one of its ALTERNATIVES to that entry's
    # pattern; a plain entry is `[^\s*]+`, an alternation entry only its own words.
    # ⛔ The slot is `[^\s*]+`, NOT `\S+`: a slot at the head of a claim otherwise
    # swallows the markdown emphasis (`**Shipped:`), so the match no longer equals
    # the stored text and a CORRECT document false-reds (measured).
    # ⛔ The split is CASE-INSENSITIVE and anchored at TOKEN BOUNDARIES, mirroring
    # `_strip_free` (#12349 round-24 reviewer, cycle 8): the gate counted
    # `shipped` as named while the case-sensitive split kept `Shipped` literal in
    # the shape, so the plant went green — a declaration that silently does
    # nothing, which is the one failure this mechanism exists to prevent.
    slot: dict[str, str] = {}
    for entry in free:
        pat = (r"(?:" + "|".join(re.escape(a) for a in entry.split("|")) + ")"
               if "|" in entry else r"[^\s*]+")
        for alt in entry.split("|"):
            slot[alt.lower()] = pat
    pattern = "(" + "|".join(
        [r"(?<![A-Za-z0-9_])(?i:" + re.escape(a) + r")(?![A-Za-z0-9_])"
         for a in sorted(slot, key=len, reverse=True)]
        + [_SIGNED_NUMBER]) + ")"
    out = []
    named = {_magnitude(v) for v in values}
    for part in re.split(pattern, text):
        if part is not None and part.lower() in slot:
            # `\S+`, not `\w+`: the falsifiable content of a claim is not always a
            # word — `` `==`, not `>=` `` has operators as its content, and a
            # `\w+` wildcard there matched nothing at all (measured: the pin
            # reddened on the TRUE sentence).
            out.append(slot[part.lower()])
        elif _magnitude(part) in named:
            out.append(_SIGNED_NUMBER)
        else:
            out.append(re.escape(part))
    return "".join(out)


def _only_claim(scope: str, true: str, where: str,
                values: tuple[str, ...] = (),
                free: tuple[str, ...] = ()) -> None:
    """`scope` must state this claim EXACTLY ONCE, and to `true`'s numbers.

    Any of the three falsifications a presence pin cannot see reds here: the
    claim rewritten in place (its values no longer match the shape `true`
    defines), the claim DELETED (`[]`), and the truth restated beside a
    falsified copy (`[false, true]` or `[true, true]` — more than one match).

    `values` names the numbers that are the CLAIM'S values (usually formatted
    from the artifact by the caller) and `free` the words that are its content;
    see `_shape` for why wildcarding more than that is wrong.
    """
    # ⛔ A GATE ON THE PINS THEMSELVES, not on the documents (#12349 round-24
    # reviewer: the SAME class — "a value nobody named" — was found in three
    # consecutive review cycles). A value the caller forgets to name stays
    # literal in the shape, so a falsified copy does not match it and the plant
    # goes GREEN, silently, with no test able to see it. The pin therefore
    # refuses to be written that way: every number in the claim must be a named
    # value, a named free token, or part of an identifying LABEL.
    unnamed = _unnamed_values(true, values, free)
    assert not unnamed, (
        f"{where} pins a claim whose values are not all named: {unnamed!r} in "
        f"{true!r}. An unnamed value stays LITERAL in the shape, so a falsified "
        "copy no longer matches and the plant passes — the false-pass class "
        "three review cycles found (#12349 round 24). Pass it in `values=`, or "
        "in `free=` if it is a word/threshold rather than a number")
    # ⛔ A PLAIN `free` entry that does not occur in its own claim declares
    # nothing (an alternation legitimately holds words the claim does not use).
    # This is the check that would have caught the case-sensitivity split above
    # (#12349 round-24 reviewer, cycle 8).
    for entry in free:
        # ⛔ For an ALTERNATION, at least ONE arm must occur: `free=("find|miss",)`
        # is inert on prose that says `found`, and an inert declaration is the
        # same false pass as a missing one (round-24 reviewer, cycle 9).
        # ⛔ An alternation whose arms are ALL THE SAME word is a plain entry
        # wearing an alternation's clothes — it emits `(?:word)`, a LITERAL, so it
        # passes every check while wildcarding nothing (round-24 reviewer,
        # cycle 11: `free=("recovered|recovered",)` silently disabled a pin).
        if "|" in entry and len(set(entry.split("|"))) == 1:
            raise AssertionError(
                f"{where} declares `free={entry!r}` whose arms are all identical "
                "— write the plain entry instead: an alternation of one distinct "
                "arm is a LITERAL in the shape, i.e. an inert declaration")
        if "|" in entry and not any(
                re.search(r"(?<![A-Za-z0-9_])" + re.escape(arm)
                          + r"(?![A-Za-z0-9_])", true, re.I)
                for arm in entry.split("|")):
            raise AssertionError(
                f"{where} declares `free={entry!r}` but NO arm of it occurs in "
                f"the claim {true!r} — an inert declaration leaves the word "
                "LITERAL in the shape, so the pin silently stops seeing it")
        if "|" in entry:
            continue
        if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(entry)
                         + r"(?![A-Za-z0-9_])", true, re.I):
            raise AssertionError(
                f"{where} declares `free={entry!r}` but the claim {true!r} does "
                "not contain that word — an unused declaration leaves the word "
                "LITERAL in the shape, so the pin silently stops seeing it")
    unnamed_words = _unnamed_words(true, free)
    assert not unnamed_words, (
        f"{where} pins a claim whose DIRECTION words are not all named: "
        f"{unnamed_words!r} in {true!r}. A pivot left literal stays LITERAL in "
        "the shape, so a falsified copy does not match and the restated truth "
        "satisfies the pin — the same silent degradation an unnamed number "
        "causes, and the class five review cycles found by hand (#12349 round "
        "24). Add the word to `free=` (it becomes `\\S+`, which is the point: the "
        "claim's CONTENT must not be literal, its identity must be)")
    # ⛔ Case-INSENSITIVE matching (#12349 round-24 reviewer, cycle 7): a free
    # slot declared lowercase did not match the document's `Postgres`, so the
    # falsified copy stayed unmatched and the pin went green. A case flip is not a
    # change of claim, and `found == [true]` still compares the stored text
    # exactly, so a case-differing copy counts as a second statement (RED).
    found = re.findall(_shape(true, values, free), scope, re.I)
    assert found == [true], (
        f"{where} must state this claim EXACTLY ONCE and to the artifact's "
        f"numbers ({true!r}) — it carries {found!r}. A §-scoped PRESENCE pin is "
        "satisfied by a restatement anywhere in the same section, so a claim "
        "falsified in place survives while the true text sits beside it "
        "(#12349 round 24)")


def test_the_claim_shape_identifies_its_claim_and_reds_the_plant() -> None:
    """`_only_claim` must catch the plant WITHOUT false-redding a second TRUE claim.

    ⛔ Both halves of this were broken on the round-24 first cut, in opposite
    directions (#12349 round-24 reviewer, P2 — each measured): wildcarding EVERY
    digit made the k=5 pin red on a true `while at k=10 it is …` sentence (`k=` was
    wildcarded too, so the shape stopped identifying the window), and a `\\w+` word
    wildcard made the operator claim `` `==`, not `>=` `` match nothing at all,
    redding on its own true sentence. So the shape wildcards exactly the claim's
    named VALUES plus its named CONTENT words, and this pins both halves.
    """
    claim = "while at k=5 it is \u22120.67 pts (the shipped leg ahead 2\u20130)"
    values = ("0.67", "2", "0")
    claim_free = ("ahead", "shipped|postgres")
    # 1. the true claim alone is accepted
    _only_claim(claim, claim, "scope", values=values, free=claim_free)
    # 2. a SECOND, DIFFERENT claim about another window is NOT a duplicate.
    #    ⛔ The sentence must share the claim's WORDING — otherwise it cannot
    #    collide under any digit policy and the case proves nothing (round-24
    #    reviewer, P2: the first cut used "At k=10 the arms are tied", which
    #    matched under the wildcard-every-digit shape too, so the guard was a
    #    NO-OP for the defect its docstring names).
    _only_claim(
        claim + " while at k=10 it is \u22120.67 pts (the shipped leg ahead 2\u20130).",
        claim, "scope", values=values, free=claim_free)
    # 3. falsified in place -> red
    with pytest.raises(AssertionError):
        _only_claim(claim.replace("\u22120.67", "\u22120.99"), claim, "scope",
                    values=values, free=claim_free)
    # 4. falsified + the truth restated beside it: the class this helper closes
    with pytest.raises(AssertionError):
        _only_claim(claim.replace("\u22120.67", "\u22120.99") + " " + claim,
                    claim, "scope", values=values, free=claim_free)
    # 5. deleted -> red
    with pytest.raises(AssertionError):
        _only_claim("no claim here", claim, "scope", values=values,
                    free=claim_free)
    # 6. a WORD-only claim: its content word is wildcarded, so a one-token
    #    falsification (identical -> equal/differ, the shape a real edit takes) is
    #    caught even when the true sentence is restated beside it. ⛔ Declared
    #    residual, in `_shape`'s docstring: a rewrite of MORE than one token
    #    (`are identical` -> `are not identical`) no longer matches the shape, so
    #    it reds only while the true text is absent.
    word = "the two Postgres arms' ranks are identical."
    _only_claim(word, word, "scope", free=("identical",))
    with pytest.raises(AssertionError):
        _only_claim("the two Postgres arms' ranks are equal. " + word, word,
                    "scope", free=("identical",))
    # 8. an OPERATOR claim's content is its operators
    ops = "`==`, not `>=`"
    _only_claim(ops, ops, "scope", free=("==|>=", "not"))
    with pytest.raises(AssertionError):
        _only_claim("`>=`, not `==` and " + ops, ops, "scope",
                    free=("==|>=", "not"))
    # 9. ARTIFACT-TIME GATE: a value the caller forgot to name is REFUSED rather
    #    than silently degrading the pin back to a presence check — the false-pass
    #    shape three review cycles independently found (`1e-6` unnamed, `only_b`'s
    #    `0` unnamed, a sign outside `values`).
    with pytest.raises(AssertionError, match="values are not all named"):
        _only_claim(claim, claim, "scope", values=("0.67",), free=claim_free)
    # …and an EXPONENT is a value, not an identifier: `1e-6` unnamed is refused
    # (the gate's first cut skipped it as "adjacent to a letter", which is how a
    # threshold slipped through a cycle).
    with pytest.raises(AssertionError, match="values are not all named"):
        _only_claim("p < 1e-6", "p < 1e-6", "scope", values=(), free=("<",))
    _only_claim("p < 1e-6", "p < 1e-6", "scope", free=("1e-6", "<"))
    # 10. …and it must NOT fire on an IDENTIFIER's digits (`BM25`, `m3-000014`) or
    #     on a LABEL (`k=5`, `§4.1`, `#12298`, `recall@3`) — those identify the claim.
    _only_claim("BM25 buys +1.33 pts (p = 0.34) in m3-000014",
                "BM25 buys +1.33 pts (p = 0.34) in m3-000014", "scope",
                values=("1.33", "0.34"))
    _only_claim("while at k=5 it is −0.67 pts", "while at k=5 it is −0.67 pts",
                "scope", values=("0.67",))
    _only_claim("recall@3 is higher", "recall@3 is higher", "scope",
                free=("higher|lower",))
    # 11. a bare `=` is NOT a label (round-24 reviewer, cycle 4): `p = 0.34` is how
    #     every p-value in these documents is written, so exempting `=` exempted
    #     the very values the gate exists to require.
    with pytest.raises(AssertionError, match="values are not all named"):
        _only_claim("gives p = 0.34 overall", "gives p = 0.34 overall", "scope")
    # 12. a value named EXACTLY as written (with its sign) must be accepted — the
    #     gate normalises the token AND the entries.
    _only_claim("it is -1.33 pts", "it is -1.33 pts", "scope", values=("-1.33",))
    # 13. the WORD half of the gate: a DIRECTIONAL word left literal is REFUSED,
    #     because it degrades the pin exactly as an unnamed number does — the class
    #     five review cycles found by hand (#12349 round 24).
    with pytest.raises(AssertionError, match="DIRECTION words are not all named"):
        _only_claim("the shipped leg ahead 2–0", "the shipped leg ahead 2–0",
                    "scope", values=("2", "0"))
    # 14. …and an ALTERNATION entry keeps two ADJACENT freed words precise instead
    #     of collapsing them into `\S+ \S+` (which matched the whole section).
    _only_claim("is strictly greater than", "is strictly greater than", "scope",
                free=("strictly|weakly", "greater|less"))
    with pytest.raises(AssertionError):
        _only_claim("is weakly less than, and elsewhere is strictly greater than",
                    "is strictly greater than", "scope",
                    free=("strictly|weakly", "greater|less"))
    # 15. a free entry must not MASK a different pivot it is a SUBSTRING of.
    #     ⛔ Naming `no` used to delete every `not`/`nothing` from the residual, so
    #     the gate reported success while a pivot sat literal — a fail-OPEN in the
    #     gate itself (#12349 round-24 reviewer, cycle 7).
    with pytest.raises(AssertionError, match="DIRECTION words are not all named"):
        _only_claim("not accepted, and nothing else is",
                    "not accepted, and nothing else is", "scope",
                    free=("not",))
    _only_claim("not accepted, and nothing else is",
                "not accepted, and nothing else is", "scope",
                free=("not", "nothing"))
    # …and a PLAIN `free` entry that does not occur in the claim declares NOTHING,
    # which is the other half of the same failure (cycle 8: a case-sensitive split
    # let `free=("shipped",)` be counted as named while `Shipped` stayed literal).
    with pytest.raises(AssertionError, match="does not contain that word"):
        _only_claim("nothing else is accepted", "nothing else is accepted",
                    "scope", free=("not",))


# ── 1. the defect claim, asserted against the shipped tokenizer ─────────────

@pytest.mark.parametrize("name,run", [
    ("web_search", "web_search"),
    ("test_ci_selection", "test_ci_selection"),
    ("capstone_verdict_check", "capstone_verdict_check"),
    ("lease_until", "lease_until"),
])
def test_the_shipped_tokenizer_splits_the_run_the_index_keeps(name: str, run: str) -> None:
    """The measured defect, in the code rather than in prose.

    RediSearch treats ``_`` as a word character, so the index's term for
    ``web_search`` IS ``web_search`` (probe recorded in the brief §3.4:
    ``'web_search' -> [['web_search', 8.0]]`` while ``'web' -> []``). The
    shipped tokenizer splits that run, so the OR query it builds contains
    **no term the index holds** — which is why the identifier class scores
    ``recall@3 = 0.20`` with 21 empty result sets.

    ⛔ If this reds, the tokenizer gained ``_``-preservation: re-measure M3 and
    update the brief. Do not delete the test to make the document true.
    """
    from tortoise.sparse import build_or_query, tokenize_sparse_query

    assert tokenize_sparse_query(name, keep_numeric=False,
                                 keep_identifiers=False) == name.split("_"), (
        f"{name!r}: the tokenizer's split changed — re-measure #12298")
    emitted = build_or_query(name).split("|")
    assert run not in emitted, (
        f"{name!r}: build_or_query now emits the run the index holds "
        f"({emitted}) — the measured defect is repaired; re-measure and "
        f"rewrite the brief §3.4 rather than relaxing this assertion")


def test_the_identifier_class_cannot_match_its_own_node(tool_mod) -> None:
    """The finding in one line: every emitted term for the class is not the index's term."""
    from tortoise.sparse import build_or_query

    for name in ("web_search", "verified_files", "py_compile"):
        emitted = set(build_or_query(name).split("|"))
        # The OR string is a real multi-term query (not a fallback to the raw
        # name), and NONE of its terms is the term the index actually holds.
        assert len(emitted) > 1, (name, emitted)
        assert not (emitted & {name}), name


# ── 2. the control is verified, not assumed ─────────────────────────────────

@pytest.mark.parametrize("query", [
    "the diff",
    "the admin-merge rail",
    "web_search",
    "tests/test_7949_capstone_verdict_check.py",
    "ERR-4032",
    "PR #12298",
    "the work is finished",
    # degenerate shapes: single token, all-digits, stopword-only
    "diff", "2258", "the", "", "10:00",
])
def test_production_terms_reproduce_build_or_query(tool_mod, query: str) -> None:
    """The term list reproduces `build_or_query`, byte-exactly.

    A control that silently drifted would measure a tokenizer that is not the
    product's and look perfectly healthy — so the tool refuses at run time
    (`assert_terms_match`) and this pins the same property at test time.

    ⚠️ Reproducing `build_or_query` is NOT the same as handing Postgres terms:
    on the DEGENERATE branch `build_or_query` returns the escaped RAW string, so
    there is no term list to hand over and `arm_terms` sends the raw text — the
    declared break in the control, pinned by
    `test_the_degenerate_branch_is_declared_not_hidden`.
    """
    from tortoise.sparse import build_or_query, escape_redisearch_literal

    terms, degenerate = tool_mod.production_terms(query)
    expected = escape_redisearch_literal(query) if degenerate else "|".join(terms)
    assert build_or_query(query) == expected


def test_recovery_terms_only_add(tool_mod) -> None:
    """The recovery arm is additive — the property its zero-regression relies on."""
    assert tool_mod.recovery_terms("web_search") == ["web_search"]
    assert tool_mod.recovery_terms("tests/test_7949_capstone_verdict_check.py") == [
        "test_7949_capstone_verdict_check"]
    # A run WITHOUT an underscore is already a token the shipped tokenizer
    # emits, so the recovery arm must not add it again.
    assert tool_mod.recovery_terms("the diff") == []
    assert tool_mod.recovery_terms("ERR-4032") == []
    assert tool_mod.recovery_terms("tests") == []
    # A lone separator is not a run, so it cannot be a term.
    assert tool_mod.recovery_terms("_") == []
    # A run shorter than 2 characters is not a term either side indexes.
    assert tool_mod.recovery_terms("x _ y") == []


def test_recovery_terms_never_remove_an_existing_term(tool_mod) -> None:
    """Every shipped term survives into the recovery query — on the HARNESS's own builder.

    ⛔ This asserts the production function, not a re-implementation of it. An
    earlier cut rebuilt ``shipped + extra`` here and then checked that the list
    had ``shipped`` as its prefix — a tautology about a local variable that could
    not fail, presented as the pin for the zero-regression claim (#12349 review,
    P2).
    """
    from tortoise.sparse import build_or_query

    for name in ("web_search", "tests/test_7949_capstone.py", "the diff",
                 "the admin-merge rail", "ERR-4032"):
        shipped = build_or_query(name).split("|")
        recovered = tool_mod.recovery_query(name).split("|")
        assert set(shipped) <= set(recovered), (
            f"the recovery arm DROPPED a shipped term for {name!r}: "
            f"{sorted(set(shipped) - set(recovered))}")
        # It is additive and nothing else: the difference is exactly the runs the
        # shipped tokenizer split, and nothing not already in `recovery_terms`.
        assert set(recovered) - set(shipped) <= set(tool_mod.recovery_terms(name)), name
        if not tool_mod.recovery_terms(name):
            assert recovered == shipped, (
                f"nothing to add for {name!r}, so the query must be unchanged")


def test_the_artifact_ranks_reproduce_the_recall_table(artifact) -> None:
    """``recall_by_k`` must be re-derivable from the per-query ``ranks``.

    ⛔ The discriminating half of the brief pin (#12349 review, P2). The headline
    test compares each arm's ``recall`` cell to a literal and checks the literal
    appears in the brief — but the same literals appear in several cells, so a
    swapped or hand-edited cell would pass. This recomputes every k for every arm
    from the ranks the run recorded, which no edit to a summary cell can satisfy.
    """
    for arm_name, arm in artifact["arms"].items():
        ranks = arm["ranks"]
        n = len(ranks)
        assert n == artifact["sample"], (arm_name, n)
        for k, cell in arm["recall_by_k"].items():
            found = [r for r in ranks.values() if r is not None and r <= int(k)]
            assert cell["in_window"] == len(found), (arm_name, k)
            assert cell["recall"] == pytest.approx(round(len(found) / n, 4)), (
                arm_name, k, cell["recall"], round(len(found) / n, 4))
            assert cell["rows_zero"] <= n, (arm_name, k)


def test_the_paired_comparison_recomputes_from_both_arms_ranks(
        artifact, tool_mod) -> None:
    """Every paired statistic must fall out of the two arms' ranks.

    Same reasoning as the test above: ``delta_pts``/``only_a``/``only_b`` are one
    subtraction away from the ranks, so recomputing them is what makes the
    published ``+25.00 pts`` a measurement rather than a claimed cell.

    ⛔ Two populations live in this artifact and they are NOT
    interchangeable (#12349 review, P2 caught the first cut of this test doing
    exactly that): ``tokens_controlled_subset`` is computed over the queries with
    a surviving token list (its recorded ``n``, with ``excluded_degenerate``
    removed), so its cells cannot be checked against the FULL arms' ranks. They are
    recomputed from the ranks restricted to the NON-degenerate ids, which the
    artifact records (``arms["<b>"]["degenerate_ids"]``); that is the same
    computation as the full comparisons, on the population the comparison claims.
    ⛔ An earlier revision of this docstring described the weaker version it
    replaced — "checked for internal consistency, against its own ``n``" — which
    is a self-consistency check a self-consistent rewrite passes (review cycle 4,
    finding 5). The code below had already been made real; the docstring had not.
    """
    for key, cmp in artifact["comparisons"].items():
        ra = artifact["arms"][cmp["a"]]["ranks"]
        rb = artifact["arms"][cmp["b"]]["ranks"]
        n_full = len(ra)
        # The subset comparisons drop the degenerate queries; anything else that
        # disagrees with the sample size is a broken pairing, not a subset.
        subset = "n" in cmp
        n = cmp["n"] if subset else n_full
        if not subset:
            assert sorted(ra) == sorted(rb), key
            assert n == artifact["sample"], (key, n)
        for k in artifact["ks"]:
            cell = cmp[f"k{k}"]
            # Invariants that hold for ANY population: the cells agree with each
            # other, and the delta is the one division away from them.
            a_found = cell["only_a"] + cell["both"]
            b_found = cell["only_b"] + cell["both"]
            assert cell["discordant"] == cell["only_a"] + cell["only_b"], (key, k)
            assert cell["delta_pts"] == pytest.approx(
                round(100.0 * (b_found - a_found) / n, 2)), (key, k)
            assert cell["a_recall"] == pytest.approx(round(a_found / n, 4)), (key, k)
            assert cell["b_recall"] == pytest.approx(round(b_found / n, 4)), (key, k)
            # ⛔ The p value is a pure function of the discordant split, so it is
            # recomputed here rather than trusted: a hand-edited significance is
            # the single cell a reader will quote to settle an argument.
            assert cell["mcnemar_p_two_sided"] == pytest.approx(
                round(tool_mod._binom_two_sided(
                    cell["only_b"], cell["discordant"]), 5)), (key, k)
            # The discriminating half: the cells recomputed straight from the
            # ranks. For the SUBSET comparison the population is the non-degenerate
            # one, and the artifact records WHICH ids are degenerate
            # (`arms["<b>"]["degenerate_ids"]`), so this is done from the ranks
            # there too instead of being skipped (#12349 review cycle 2,
            # finding 4: a self-consistent rewrite of the subset's cells passed).
            a_ids = {i for i, r in ra.items() if r is not None and r <= k}
            b_ids = {i for i, r in rb.items() if r is not None and r <= k}
            if subset:
                deg = set(artifact["arms"][cmp["b"]]["degenerate_ids"])
                a_ids -= deg
                b_ids -= deg
                assert deg <= set(ra), (key, len(deg - set(ra)))
                assert len(ra) - len(deg) == cmp["n"], (key, cmp["n"])
            assert cell["only_a"] == len(a_ids - b_ids), (key, k)
            assert cell["only_b"] == len(b_ids - a_ids), (key, k)
            assert cell["both"] == len(a_ids & b_ids), (key, k)
            assert cell["discordant"] == len(a_ids ^ b_ids), (key, k)
        if subset:
            assert cmp["excluded_degenerate"] + n == n_full, (
                key, cmp["excluded_degenerate"], n, n_full)


# ── 3. the paired statistics ────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,want", [
    (0, 0, 1.0),
    (0, 5, 0.0625),
    (5, 5, 0.0625),
    (1, 1, 1.0),
    (3, 3, 0.25),
    (2, 4, 1.0),
    (0, 10, 0.001953125),
])
def test_mcnemar_two_sided_exact(tool_mod, k: int, n: int, want: float) -> None:
    assert tool_mod._binom_two_sided(k, n) == pytest.approx(want)


def test_compare_counts_discordant_pairs(tool_mod) -> None:
    """The pairing arithmetic, so a swapped column cannot pass as a gain."""
    a = {"arm": "a", "ranks": {"x": 1, "y": None, "z": 2, "w": None}}
    b = {"arm": "b", "ranks": {"x": 1, "y": 3, "z": None, "w": None}}
    out = tool_mod.compare(a, b, (3,))
    assert out["population_ids_identical"] is True
    assert out["k3"]["both"] == 1
    assert out["k3"]["only_b"] == 1     # b gained y
    assert out["k3"]["only_a"] == 1     # b lost z
    assert out["k3"]["discordant"] == 2


# ── 4. the artifact, and the brief against it ──────────────────────────────

def test_artifact_carries_the_rule_as_the_harness_constant(
        tool_mod, artifact, brief_text) -> None:
    """The rule is a CONSTANT in the harness and travels verbatim into the artifact.

    ⚠️ What this proves: the rule cannot be re-chosen per run — the artifact's
    `decision_rule` is byte-identical to `DECISION_RULE` in the harness, so a
    different rule has to be edited in two places, and the brief quotes the same
    operative clauses.

    ⛔ What it does NOT prove, and the earlier cut of this test implied it did:
    that the constant existed BEFORE the 300-name run. Harness, artifact and
    brief landed in ONE commit, so the ordering is a claim about the lane's
    process, not a fact this repository can check — the repo's own executable
    form of that claim (`tools/capstone_preregistration_check.py`) needs an
    evidence log that predates the registration, which this lane does not have.
    """
    assert artifact["decision_rule"] == tool_mod.DECISION_RULE
    assert artifact["decision_rule"].startswith("PASS iff")
    assert artifact["primary_k"] == 3
    assert artifact["verdict"]["rule"] == artifact["decision_rule"]
    # ⛔ The threshold is DERIVED from the rule, not restated: a hard-coded
    # `p < 0.05` asserted here could agree with §4.1 forever after the rule
    # moved. Each clause is then shape-unique in §4.1 (#12349 round 24): a
    # presence check let a clause be falsified in place with the truth restated
    # beside it.
    threshold = re.search(r"p\s*<\s*\d+(?:\.\d+)?",
                          artifact["decision_rule"]).group(0)
    # ⛔ Each clause names its PIVOT word, and only that: free-ing every word of a
    # two-word claim turns the shape into `\S+ \S+`, which matched every
    # two-word pair in §4.1 (measured: 145 matches).
    # ⛔ ALTERNATIONS, not `\S+` slots: two adjacent freed words would collapse
    # into `\S+ \S+` and match the whole section (measured: 145 matches).
    for clause, pivot in (("strictly greater", ("strictly|weakly",
                                                "greater|less")),
                          ("exact two-sided McNemar", ("exact|approximate",
                                                       "two-sided|one-sided")),
                          (threshold, ("<|>",))):
        assert clause in artifact["decision_rule"], clause
        # §4.1 is where the brief states the rule's clauses (#12349 round 19).
        # The threshold's own digits are its VALUES (derived from the rule), so a
        # falsified copy still matches the shape.
        _only_claim(_scoped(brief_text, "4.1"), clause,
                    f"§4.1's rule clause {clause!r}",
                    values=tuple(re.findall(r"\d+(?:\.\d+)?", clause)),
                    free=pivot)

def test_artifact_population_is_the_committed_pin(artifact, population) -> None:
    assert artifact["population_digest"] == population["digest"]
    # ⛔ The digest is a CONTENT digest, so it has to be RE-DERIVED from the names
    # it summarises — comparing the same string on both sides proves only that
    # the two files agree, not that either was built from those names (#12349
    # review, P2-1). Measured on the earlier cut: rewriting one class's name text
    # in `population.json` and leaving `digest` untouched kept all 72 tests
    # green, while the recomputed hash moved off the recorded `668229a6adeb9d0a`
    # — the exact tamper class a content digest exists to catch. The derivation
    # is the producer's own (`tools/m3_fts_leg_recall.py`: sha256("\n".join(
    # names))[:16]).
    assert population["digest"] == hashlib.sha256(
        "\n".join(r["name"] for r in population["names"]).encode()
    ).hexdigest()[:16], (
        "`population.json`'s digest does not re-derive from its own names — the "
        "pin file can then be hand-edited with every other gate in this file "
        "still green")
    assert artifact["sample"] == population["sample"] == len(population["names"])
    ids = [r["id"] for r in population["names"]]
    assert len(ids) == len(set(ids)), "the pinned ids must be unique"
    for arm in artifact["arms"].values():
        assert sorted(arm["ranks"]) == sorted(ids), (
            f"{arm['arm']}: ranks do not cover the pinned population — the "
            f"comparison would not be paired")


def test_all_arms_measured_the_same_population(artifact) -> None:
    assert len(artifact["arms"]) == 4, (
        "M3 needs the shipped leg, the scorer-controlled BM25 arm, the "
        "deployable BM25 arm and the in-place recovery arm")
    assert set(artifact["arms"]) == {
        "falkordb_tfidf", "postgres_bm25/tokens",
        "postgres_bm25/native", "falkordb_tfidf__tokens_fixed"}


@pytest.mark.parametrize("arm,want", [
    ("falkordb_tfidf", 0.7133),
    ("postgres_bm25/tokens", 0.7267),
    ("postgres_bm25/native", 0.9633),
    ("falkordb_tfidf__tokens_fixed", 0.9633),
])
def test_brief_headline_matches_the_artifact(artifact, brief, arm: str, want: float) -> None:
    """The brief's §0 table is the artifact's, ROW BY ROW.

    ⛔ Bound to the row, not to the document: `f"{want:.4f}" in brief` passed
    while the §0 cell itself said something else, because the same four decimals
    appear elsewhere in the brief (measured — the previous revision had exactly
    that hole; review cycle 2, finding 2).
    """
    assert artifact["arms"][arm]["recall_by_k"]["3"]["recall"] == pytest.approx(want)
    got = {}
    for cells in _table(_scope_slice(brief, "0"), "recall@3", "Δ vs shipped"):
        m = re.match(r"`([^`]+)`", cells[0])
        if m:
            got[m.group(1)] = cells[1]
    assert arm in got, f"the brief's §0 table has no `{arm}` row"
    assert round(_num(got[arm]), 4) == round(want, 4), (
        f"the brief's §0 row for {arm} states {_plain(got[arm])!r}; the committed "
        f"artifact says {want:.4f} — rewrite the brief, do not relax the pin")


#: §0's five rows → the artifact cell each one must equal. The subset row has no
#: arm of its own (it is a comparison over a subset of the same two arms), so it
#: is keyed `__subset__`; the shipped row carries em-dashes in both derived
#: columns because it IS the baseline.
_HEADLINE_ROWS: tuple[tuple[str, tuple[str, str] | None], ...] = (
    ("falkordb_tfidf", None),
    ("postgres_bm25/tokens",
     ("falkordb_tfidf__vs__postgres_bm25/tokens", "k3")),
    ("__subset__", ("tokens_controlled_subset", "k3")),
    ("postgres_bm25/native",
     ("falkordb_tfidf__vs__postgres_bm25/native", "k3")),
    ("falkordb_tfidf__tokens_fixed",
     ("falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed", "k3")),
)


#: §1's "pinned read of the code": `(file, the line the brief cites, the text that line
#: must hold, the citation form the brief must still carry)`. The line numbers are
#: duplicated here ON PURPOSE — the brief's number and the code's real number are the
#: two halves this pin compares, so sharing one source would make the comparison
#: vacuous.
_S1_LOCATORS: tuple[tuple[str, int, str, str], ...] = (
    ("tortoise/sparse.py", 505, "def build_or_query(",
     "`tortoise/sparse.py:505`"),
    ("tortoise/sparse.py", 58, "_SPLIT_RE", "`:58`"),
    ("tortoise/sparse.py", 381, "def tokenize_sparse_query(",
     "`tortoise/sparse.py:381`"),
    ("tortoise/sparse.py", 63, "DEFAULT_MAX_OR_TERMS = 12",
     "`tortoise/sparse.py:63`"),
    # ⛔ The degenerate branch is the `return`, NOT the enclosing `def`: the row
    # cited 505 (the function header) for a statement 85 lines below it.
    ("tortoise/sparse.py", 590, "return escape_redisearch_literal(query)",
     "`sparse.py:590`"),
    ("tortoise/search_engine.py", 2800, "def run_fts_query(",
     "`tortoise/search_engine.py:2800`"),
    ("tortoise/search_engine.py", 2986, "CALL db.idx.fulltext.queryNodes",
     "`:2986-2998`"),
    ("tortoise/search_engine.py", 2642, "def recency_tie_clause(",
     "`search_engine.py:2642`"),
    ("tortoise/search_engine.py", 4492,
     "for rank, (pid, _score) in enumerate(ranked):",
     "`tortoise/search_engine.py:4492`"),
    ("tortoise/projection/__init__.py", 20346, '("Object", "name", "FULLTEXT")',
     "`tortoise/projection/__init__.py:20346`"),
    ("tortoise/projection/__init__.py", 20774, "def _create_fulltext_index(",
     "`:20774`"),
    ("tortoise/projection/__init__.py", 21224,
     "self._create_fulltext_index(label, fields)", "`:21224`"),
)


def _signed(cell: str) -> float:
    """The first SIGNED number in a cell, U+2212 included (`−0.67` -> -0.67).

    ⛔ ``_num`` is ``re.search(r"-?\\d+...")``, so it keeps an ASCII minus but NOT
    the typographic U+2212: ``_num("−0.67")`` is ``0.67``, which lets a prose
    sign flip compare EQUAL to its own correct positive value. The brief's prose
    uses U+2212 (its §0 and §3.2 bodies state `−0.67 pts`) while its tables use
    ASCII, so the Δ column needs the reader that understands both.
    """
    m = re.search(r"[-+\u2212]?\d+(?:\.\d+)?", _plain(cell))
    assert m, f"no number in the cell {cell!r}"
    return float(m.group().replace("\u2212", "-").replace("+", ""))


def test_brief_headline_table_pins_every_column_and_the_subset_row(
        artifact, brief) -> None:
    """§0's table is the artifact's in ALL FOUR columns, on ALL FIVE rows.

    ⛔ The sibling test above binds the RECALL column only, and its arm regex
    skips the `⤷` subset row entirely — so §0's Δ and paired-p cells were
    unpinned while this file's docstring claimed the table was compared CELL BY
    CELL (#12349 review, P2). Measured before this pin existed: rewriting
    `+1.33 pts | 0.344` to `+9.99 pts | 0.001`, the subset row's `+1.15 pts` to
    `+8.88 pts`, and `+25.00 pts` to `+99.00 pts` left every brief-reading test
    in this file GREEN. This is the pin for that hole.
    """
    rows: dict[str, list[str]] = {}
    for cells in _table(_scope_slice(brief, "0"), "recall@3", "Δ vs shipped"):
        if "tokens-controlled subset" in cells[0]:
            rows["__subset__"] = cells
            continue
        m = re.match(r"`([^`]+)`", cells[0])
        if m:
            rows[m.group(1)] = cells

    for arm, cell in _HEADLINE_ROWS:
        assert arm in rows, f"§0's table has no row for {arm!r}"
        cells = rows[arm]
        want_recall = (artifact["comparisons"][cell[0]][cell[1]]["b_recall"]
                       if arm == "__subset__"
                       else artifact["arms"][arm]["recall_by_k"]["3"]["recall"])
        # The subset row states BOTH arms in its recall cell (`0.5287 vs 0.5172`).
        got = [round(_num(c), 4) for c in cells[1].split(" vs ")]
        assert round(want_recall, 4) in got, (
            f"§0's row for {arm!r} states {cells[1]!r}; the artifact says "
            f"{want_recall:.4f}")

        if cell is None:
            assert "—" in cells[2] and "—" in cells[3], (
                f"the shipped row IS the baseline and carries em-dashes in both "
                f"derived columns, got {cells[2]!r} / {cells[3]!r}")
            continue

        comparison, kk = cell
        row = artifact["comparisons"][comparison][kk]
        assert abs(_signed(cells[2]) - row["delta_pts"]) < 0.005, (
            f"§0's Δ cell for {arm!r} states {cells[2]!r}; the artifact says "
            f"{row['delta_pts']:+.2f} — rewrite the brief, do not relax the pin")
        if cells[3].lstrip().startswith("<"):
            bound = _num(cells[3])
            assert row["mcnemar_p_two_sided"] < bound, (
                f"§0's p cell for {arm!r} states {cells[3]!r}; the artifact says "
                f"{row['mcnemar_p_two_sided']} — the bound does not hold")
        else:
            assert abs(_num(cells[3]) - row["mcnemar_p_two_sided"]) < 1e-3, (
                f"§0's p cell for {arm!r} states {cells[3]!r}; the artifact says "
                f"{row['mcnemar_p_two_sided']}")

        # ⛔ The p cell's TRAILING CLAUSE is a claim of its own, and nothing read
        # it (#12349 review, P2). `only_a` is that count: queries the SHIPPED leg
        # finds and the arm does not, i.e. regressions. Measured before this pin:
        # rewriting the brief's `0 regressions` to `1 regression` left all 118
        # tests GREEN, so §0's table could contradict §0's own Reading-2 bullet
        # (which IS pinned, 15 lines below) with the pin reporting OK — the same
        # class as the Δ and p cells this test was written for.
        tail = cells[3].split(",", 1)[1].strip() if "," in cells[3] else ""
        if arm == "falkordb_tfidf__tokens_fixed":
            assert tail, (
                "§0's in-place row must state its regression count beside the "
                f"p bound; the cell is {cells[3]!r}. Zero is the reason this arm "
                "is preferred — dropping the clause drops the claim")
        if tail:
            stated = re.search(r"\bzero\b|-?\d+", tail.lower())
            assert stated, (
                f"§0's trailing clause for {arm!r} states no count: {tail!r}")
            said = 0 if stated.group() == "zero" else int(stated.group())
            assert said == row["only_a"], (
                f"§0's row for {arm!r} states {tail!r}; the artifact records "
                f"only_a = {row['only_a']} (names the shipped leg finds and this "
                "arm does not) — rewrite the brief, do not relax the pin")


def test_section_1_locators_still_point_at_the_code_they_name(brief: str) -> None:
    """§1's "pinned read of the code" must POINT AT the code it names.

    ⛔ The table exists BECAUSE "§6.2's own have drifted twice", and it had
    already drifted itself (#12349 review, P2): at the head this PR merges, the
    three `projection/__init__.py` locators were uniformly +13 (main's #12348
    inserted lines above them), the declared head `c17e3c5f78` was not the head
    the numbers came from (at `c17e3c5f78` they are a further 6 lower), and the
    degenerate-branch row cited `` `sparse.py:505` `` for a `return` that is at
    line 590. Nothing read the numbers, so all of it was prose.

    The pin is (file, line the brief cites, text that line must hold). `line`
    is here only so a DELETED citation is caught; the code side is resolved
    from the file at test time, so a `main` that moves the code reds this and
    the brief gets updated rather than silently dating. `cited` is the form the
    brief must still carry — a bare `:N` for a row that continues the previous
    row's path (`:58`, `:20774`), the full `path:N` otherwise.

    ⛔ Do NOT reintroduce a declared HEAD here (#12349 review round 2, P2): the
    table used to name one, nothing read it, and reverting it to the stale
    `c17e3c5f78` left every test green — while the merge rail SQUASHES, so any
    sha it could name is one `main` will never contain. The table now claims
    exactly what this test enforces: these locators point at their symbols.
    """
    scope = _scope_slice(brief, "1")
    marker = "**The pinned read of the code**"
    assert marker in scope, (
        "§1 lost the 'pinned read of the code' table — the locators it carries "
        "are the only record of which lines the mechanism claims rest on")
    table = scope[scope.index(marker):]

    for rel, line, symbol, cited in _S1_LOCATORS:
        assert cited in table, (
            f"§1 no longer cites {cited!r} for {symbol!r} in {rel} — the table "
            "must name the line each claim rests on, and this test reads that "
            "citation")
        src = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
        actual = next((i + 1 for i, text in enumerate(src) if symbol in text),
                      None)
        assert actual is not None, (
            f"{rel} no longer contains {symbol!r}; §1's table names it — the "
            "mechanism the brief describes has moved or gone")
        assert line == actual, (
            f"§1 cites {rel}:{line} for {symbol!r}, but that text is at line "
            f"{actual} — update the brief's locator, do not relax this pin")


def test_the_scorer_control_is_a_tie_and_the_rule_says_fail(artifact, brief_text) -> None:
    """M3's verdict, pinned with BOTH of its parts.

    ⛔ A later lane must not read the deployable arm's 25-point swing as M3
    passing: the primary arm is the scorer-controlled one, and the rule is the
    harness's `DECISION_RULE` constant, asserted byte-identical in
    `test_artifact_carries_the_rule_as_the_harness_constant`.
    """
    primary = artifact["comparisons"][
        "falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    assert primary["mcnemar_p_two_sided"] > 0.05, (
        "the scorer control is now significant — re-run and re-decide")
    assert artifact["verdict"]["M3"] == "FAIL"
    # ⛔ The closing condition in the issue's OWN falsifier sentence is NOT
    # triggered — a deployable BM25-in-Postgres beats the current scorer —
    # which is why M3's own rule can still say FAIL here. The brief must
    # present BOTH readings; this pin makes the second one impossible to
    # drop when the first is quoted. ⛔ Do NOT write "the falsifier is
    # satisfied": quote-mined, that says the leg is CLOSED, the opposite of
    # what this assertion exists for.
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    assert subset["n"] < artifact["sample"], (
        "the tokens control is only real on the non-degenerate queries; the "
        "artifact must record the subset it holds on")
    assert subset["mcnemar_p_two_sided"] > 0.05
    assert "Reading 1" in _scoped(brief_text, "0") and \
        "Reading 2" in _scoped(brief_text, "0"), (
        "the brief must state the literal-falsifier reading as well as the "
        "decomposition — M3 is not simply 'FAIL'")
    dep = artifact["verdict"]["deployable_arm"]
    assert dep["delta_pts"] > 20, (
        "the deployable arm's swing is the whole finding; if it shrank, the "
        "cause moved and the brief's §3.4 mechanism is no longer the reason")
    assert artifact["verdict"]["recovery_arm_no_index_move"]["delta_pts"] > 20, (
        "the in-place recovery arm is what keeps the index where it is")


def test_the_in_place_arm_loses_nothing(artifact) -> None:
    """+25 pts with zero regressions is the structural reason to prefer it."""
    rec = artifact["comparisons"][
        "falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"]
    assert rec["only_a"] == 0
    assert rec["only_b"] > 50


def test_both_engines_are_named_with_their_versions(artifact) -> None:
    """A measurement must name the engine it measured (the #11901 JEV lesson)."""
    assert "RediSearch" in artifact["arms"]["falkordb_tfidf"]["engine"]
    assert "RediSearch TF-IDF" in artifact["arms"]["falkordb_tfidf"]["engine"]
    for arm in ("postgres_bm25/tokens", "postgres_bm25/native"):
        engine = artifact["arms"][arm]["engine"]
        assert "PostgreSQL" in engine and "pg_search" in engine, engine
    assert "no OPTIONS" in artifact["arms"]["falkordb_tfidf"]["engine"], (
        "the shipped index's option surface is part of the finding: the "
        "score is TF-IDF because the index is created with none")


# ── 5. the declared break in the control, and the DoD's own requirements ────

def test_the_degenerate_branch_is_declared_not_hidden(tool_mod, artifact) -> None:
    """126 of 300 queries have no term list to control — and the artifact says so.

    On the degenerate branch `build_or_query` returns the escaped RAW string, so
    `arm_terms` sends the raw text and the two Postgres arms are ONE arm. That
    was a P1 review finding on the first version of this harness, which claimed
    a constant tokenizer everywhere; the census is now recorded as data.
    """
    census = artifact["terms_census"]
    assert census["queries"] == artifact["sample"]
    assert 0 < census["degenerate"] < census["queries"]
    assert census["degenerate"] + census["tokens_controlled"] == census["queries"]
    # the arm agrees with the census
    assert artifact["arms"]["postgres_bm25/tokens"]["degenerate_n"] == census["degenerate"]

    # What the two arms do on a degenerate query, stated as behaviour.
    assert tool_mod.arm_terms("the diff") == ("the diff", True)
    assert tool_mod.arm_terms("web_search") == ("search web", False)


def test_degenerate_rows_are_identical_across_the_two_postgres_arms(artifact) -> None:
    """The mechanism of the break, pinned: on those rows the arms coincide."""
    deg = artifact["arms"]["postgres_bm25/tokens"]["degenerate_ids"]
    tok = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    nat = artifact["arms"]["postgres_bm25/native"]["ranks"]
    differing = [i for i in deg if tok[i] != nat[i]]
    assert not differing, (
        f"{len(differing)} degenerate rows differ between the arms — the "
        f"declared break is no longer what the artifact says it is")


def test_the_tie_rule_is_matched(tool_mod, artifact, brief_text) -> None:
    """Requirement 6: the id tie key is DESC on both stores — pinned at the SQL.

    ⚠️ The artifact's `engine` note is PROSE beside the SQL, not the SQL itself,
    so the id key is asserted against the harness SOURCE as well; otherwise a
    query change could diverge from the note with this test green.
    """
    src = inspect.getsource(tool_mod)
    assert "ORDER BY s DESC, id DESC LIMIT %s" in src, (
        "the Postgres arm's ORDER BY moved — re-check the tie rule before "
        "trusting the artifact's engine note")
    assert "ORDER BY paradedb.score(id) DESC, " in src
    for arm in ("postgres_bm25/tokens", "postgres_bm25/native"):
        assert "id DESC" in artifact["arms"][arm]["engine"], (
            f"{arm} does not state the tie key it ordered by")
    assert "DESC on every label" in artifact["verdict"]["requirements"]["6_tie_rule"]

    # ⚠️ The RECENCY half is not controlled, and its mechanism is checkable: the
    # projection stamps `createdAt` ON CREATE, so the shipped leg's recency key
    # is live while the Postgres arm has no timestamp — the two orders agree only
    # because the sweep writes in ascending id order.
    entities = (REPO_ROOT / "tortoise" / "projection" / "entities.py").read_text(
        encoding="utf-8")
    assert "o.createdAt=coalesce($ca, $now)" in entities, (
        "the :Object upsert no longer stamps createdAt on create — the brief's "
        "declared residual for the recency half is now wrong")
    # ⛔ SHAPE-UNIQUE (#12349 round-24 reviewer, P2): `ascending` → `descending`
    # with the truth restated in §2 was green under the presence form.
    _only_claim(_scoped(brief_text, "2"), "ascending id order",
                "§2's write-order claim", free=("ascending",))


def test_the_scorers_do_not_rank_identically(artifact, brief_text) -> None:
    """§0's zero-discordance result is about the WINDOW, not the order.

    ⛔ The first cut of the brief read "zero discordant pairs at k=15" as "the
    two scorers rank these documents identically", and the artifact refutes it:
    a name can sit inside the window on both arms and still move five places.
    """
    shipped = artifact["arms"]["falkordb_tfidf"]["ranks"]
    tokens = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    both = [q for q in shipped if shipped[q] is not None and tokens[q] is not None]
    moved = [q for q in both if shipped[q] != tokens[q]]
    assert len(both) == 228, len(both)
    assert len(moved) == 45, (len(moved), sorted(moved)[:5])
    assert max(abs(shipped[q] - tokens[q]) for q in moved) == 5
    assert (shipped["m3-000014"], tokens["m3-000014"]) == (1, 6)
    assert artifact["arms"]["falkordb_tfidf"]["recall_by_k"]["15"][
        "mean_rank_when_found"] > artifact["arms"]["postgres_bm25/tokens"][
        "recall_by_k"]["15"]["mean_rank_when_found"]
    # and the brief must state it — the false reading must not come back. The two
    # POSITIVE claims are §0-scoped (#12349 round 19); the NEGATIVE one stays
    # document-wide on purpose — a false reading must not appear ANYWHERE.
    assert "rank these documents identically" not in brief_text
    _s0 = _scoped(brief_text, "0")
    assert "45 rank differently" in _s0
    assert "1.452 vs 1.285" in _s0


def test_the_threshold_arm_is_recorded_as_not_exercised(artifact) -> None:
    """Requirement 4: the single-leg floor arm must be stated, not implied."""
    thr = artifact["threshold_arm"]
    assert thr["applied"] is False
    assert thr["shipped_default"] == 0.0
    assert "not exercised" in artifact["verdict"]["requirements"]["4_single_leg_floor_arm"]


FIXTURES_EXPECTED = {
    "m3fx-0": "ERR-4032",
    "m3fx-1": "INV-2024-0091",
    "m3fx-2": "abc_def",
    "m3fx-3": "test_7949_capstone_verdict_check",
    "m3fx-4": "ERR-4032 retry",
}


def test_the_identifier_fixture_runs_on_every_arm(artifact) -> None:
    """Requirement 5 is the DoD's correctness gate — and it is on every arm."""
    for name, arm in artifact["arms"].items():
        fx = arm.get("identifier_fixture")
        assert fx is not None, f"{name} carries no identifier fixture"
        assert {r["id"] for r in fx["results"]} == set(FIXTURES_EXPECTED)
        for r in fx["results"]:
            assert r["stored"] == FIXTURES_EXPECTED[r["id"]]
            assert "terms_sent" in r and "rank" in r and "rank_wide" in r
        assert fx["PASS"] == all(r["rank_wide"] is not None
                                 for r in fx["results"])


def test_the_shipped_leg_fails_the_identifier_fixture_and_the_arms_differ(artifact) -> None:
    """The finding the fixture exists to surface.

    ⛔ If the shipped leg starts PASSING this, the tokenizer defect is fixed:
    re-measure M3 and rewrite the brief §3.5 rather than relaxing this.
    """
    shipped = artifact["arms"]["falkordb_tfidf"]["identifier_fixture"]
    fixed = artifact["arms"]["falkordb_tfidf__tokens_fixed"]["identifier_fixture"]
    assert shipped["PASS"] is False, (
        "the shipped leg now passes the #8933 fixture — the defect is repaired; "
        "re-measure")
    assert fixed["PASS"] is True
    bad = {r["stored"] for r in shipped["results"] if r["rank_wide"] is None}
    assert "abc_def" in bad, (
        "abc_def being unretrievable by its own name (no row at the production "
        "window AND none at k=15 — that is what the artifact records; it is not a "
        "row COUNT) is the crispest case in "
        "the lane; if it stopped happening the mechanism moved")
    # The scorer is not what fixes it: our terms fail, the analyser passes.
    assert artifact["arms"]["postgres_bm25/tokens"]["identifier_fixture"]["PASS"] is False
    assert artifact["arms"]["postgres_bm25/native"]["identifier_fixture"]["PASS"] is True


def test_a_second_independent_run_reproduced_every_table() -> None:
    """`artifact-repeat.json` is the repeatability evidence, and it must agree.

    An earlier draft claimed a repeat in prose while committing one artifact; a
    number a reader cannot check does not belong in the same table as one they
    can.
    """
    rep = DOC_DIR / "artifact-repeat.json"
    assert rep.exists(), "the repeatability artifact is missing"
    a = json.loads(ARTIFACT.read_text())
    b = json.loads(rep.read_text())
    assert a["population_digest"] == b["population_digest"]
    for name in a["arms"]:
        assert a["arms"][name]["recall_by_k"] == b["arms"][name]["recall_by_k"], name
        assert a["arms"][name]["per_class"] == b["arms"][name]["per_class"], name
    assert a["verdict"] == b["verdict"]
    assert a["falkordb_graph"] != b["falkordb_graph"], (
        "each run must use its own graph — a reused graph re-upserts into a "
        "live index")


def test_the_fixture_ordering_defect_is_recorded_in_both_artifacts() -> None:
    """The 305-vs-300 disclosure must exist, and be true of what the artifacts hold.

    ⛔ This pins a DISCLOSURE, which is the one thing the rest of this file does
    not do for the harness's own defect (#12349 review cycle 2, finding 8). The
    brief states the caveat and points a reader at `measurement_notes`; with no
    assertion behind it, an artifact edit — or a re-run — leaves the prose
    claiming a defect the record no longer shows, which is the same rot this file
    exists to stop for every published NUMBER.

    The relationship, not the literal: both FalkorDB arms share one population, so
    the recovery arm's index must carry exactly the five `#8933` fixture rows the
    shipped arm inserted before it. ⛔ If the re-run that the harness fix makes
    possible lands, BOTH counts become the population size and this reds — that is
    intended: whoever re-runs must replace the caveat in the brief and this pin in
    the same change, rather than leaving a disclosure that describes a run nobody
    can reproduce.
    """
    fixture_rows = 5
    for name in ("artifact.json", "artifact-repeat.json"):
        path = DOC_DIR / name
        assert path.exists(), f"missing committed artifact {path}"
        doc = json.loads(path.read_text())
        notes = doc.get("measurement_notes")
        assert notes, f"{name} must carry `measurement_notes` — the brief cites it"
        assert "FIXTURE ORDERING" in notes, (name, "the note must name the defect")
        shipped = doc["arms"]["falkordb_tfidf"]["index_numDocuments"]
        recovery = doc["arms"]["falkordb_tfidf__tokens_fixed"]["index_numDocuments"]
        assert shipped == doc["sample"], (name, shipped, doc["sample"])
        assert recovery == shipped + fixture_rows, (
            f"{name}: the recovery arm's index is {recovery} against the shipped "
            f"arm's {shipped} — the fixture-ordering defect has changed shape, so "
            "the brief's caveat and this pin must be re-derived together")


def test_an_authoring_path_did_not_leak_into_the_artifacts() -> None:
    """`population_file` must be repo-relative — an absolute path is one box's fact."""
    for name in ("artifact.json", "artifact-repeat.json"):
        doc = json.loads((DOC_DIR / name).read_text())
        rel = doc["population_file"]
        assert not rel.startswith("/"), (
            f"{name}: `population_file` is an absolute authoring path ({rel})")
        assert (REPO_ROOT / rel).exists(), (name, rel)


# ── 5. every published table is the artifact's, CELL BY CELL ────────────────
# Review cycle 2, finding 1: the pins above compare the ARTIFACT to itself. The
# brief's published tables — the per-class decomposition §4.3's argument rests
# on, the paired tests, the k=5/10/15 rows — were compared to nothing, so a
# re-run that regenerated `artifact.json` left every one of them quotable and
# false while `--integrity` and the `core`/`tier1` registrations stayed green.
# The section below binds each published cell to the committed artifact.

ARM_ORDER_31 = (
    "falkordb_tfidf", "postgres_bm25/tokens", "postgres_bm25/native",
    "falkordb_tfidf__tokens_fixed",
)


def test_brief_rank_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.1 — the whole recall@k table, the empty-row census and the latencies."""
    rows = {_plain(r[0]).lower(): r for r in _table(
        _scope_slice(brief, "3.1"), "falkordb__tokens_fixed")}
    assert len(rows) == 6, f"§3.1 no longer has its 6 rows: {sorted(rows)}"
    for k in ("3", "5", "10", "15"):
        cells = rows[k]
        for arm, cell in zip(ARM_ORDER_31, cells[1:], strict=True):
            want = artifact["arms"][arm]["recall_by_k"][k]["recall"]
            assert round(_num(cell), 4) == round(want, 4), (
                f"§3.1 row k={k}, arm {arm}: brief {_plain(cell)!r} vs artifact "
                f"{want:.4f}")
    zero_row = rows["empty result sets"]
    for arm, cell in zip(ARM_ORDER_31, zero_row[1:], strict=True):
        want = artifact["arms"][arm]["recall_by_k"]["3"]["rows_zero"]
        assert int(_num(cell)) == want, (
            f"§3.1 empty-result-sets row, arm {arm}: brief {_plain(cell)!r} vs "
            f"artifact {want}")
    ms_row = rows["ms per query (cpu, this box)"]
    for arm, cell in zip(ARM_ORDER_31, ms_row[1:], strict=True):
        want = artifact["arms"][arm]["ms_per_query"]
        assert round(_num(cell), 2) == round(want, 2), (
            f"§3.1 ms-per-query row, arm {arm}: brief {_plain(cell)!r} vs "
            f"artifact {want}")


def test_brief_paired_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.2 — both / shipped only / other only / Δ pts / p, for every comparison.

    ⛔ k=5 and k=10 are rows of this table ON PURPOSE (#12349 review, P2): k=5 is
    the only width in the whole artifact where the shipped leg beats BM25 fed our
    terms, so a table that jumps 3 → 15 reads as a floor where the evidence has a
    sign change. They are pinned like every other row, so the omission cannot
    come back.
    """
    cmp = artifact["comparisons"]
    want = {
        ("pg/tokens", 3): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"],
        ("pg/tokens", 5): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k5"],
        ("pg/tokens", 10): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k10"],
        ("subset", 3): cmp["tokens_controlled_subset"]["k3"],
        ("pg/tokens", 15): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"],
        ("pg/native", 3): cmp["falkordb_tfidf__vs__postgres_bm25/native"]["k3"],
        ("in-place fix", 3): cmp["falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"],
    }
    seen = set()
    for cells in _table(_scope_slice(brief, "3.2"), "shipped only", "other only"):
        label = _plain(cells[0]).lower()
        k = int(_num(cells[1]))
        if "subset" in label:
            key = ("subset", k)
        elif "in-place" in label:
            key = ("in-place fix", k)
        elif "native" in label:
            key = ("pg/native", k)
        elif "pg/tokens" in label or "tokens" in label:
            key = ("pg/tokens", k)
        else:                                    # pragma: no cover - a new row
            raise AssertionError(f"§3.2 row this pin cannot identify: {cells[0]!r}")
        assert key in want, f"§3.2 row {key} is not one the artifact records"
        assert key not in seen, f"§3.2 states {key} twice"
        seen.add(key)
        pair = want[key]
        for cell, field, nd in (
            (cells[2], "both", 0),
            (cells[3], "only_a", 0),
            (cells[4], "only_b", 0),
            (cells[5], "delta_pts", 2),
        ):
            assert round(_num(cell), nd) == round(pair[field], nd), (
                f"§3.2 {key} {field}: brief {_plain(cell)!r} vs artifact {pair[field]}")
        p_cell = _plain(cells[6])
        if p_cell.startswith("<"):
            assert pair["mcnemar_p_two_sided"] < _num(p_cell), (
                f"§3.2 {key} p: brief {p_cell!r} vs artifact "
                f"{pair['mcnemar_p_two_sided']}")
        else:
            assert round(_num(p_cell), 3) == round(pair["mcnemar_p_two_sided"], 3), (
                f"§3.2 {key} p: brief {p_cell!r} vs artifact "
                f"{pair['mcnemar_p_two_sided']}")
    assert seen == set(want), f"§3.2 lost a comparison: {sorted(set(want) - seen)}"


def test_brief_per_class_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.3 — the per-class recall@3 / recall@15 grid, which §4.3 argues from."""
    cls_of = {"definite": "definite", "identifier": "ident", "path": "path"}
    seen = set()
    # The header is `| class | n | shipped | pg/tokens | pg/native | in-place fix |`;
    # the `(recall@3 / recall@15)` in the section heading is prose, not a cell.
    for cells in _table(_scope_slice(brief, "3.3"), "class", "in-place fix"):
        label = _plain(cells[0]).lower()
        cls = next((v for k, v in cls_of.items() if label.startswith(k)), None)
        assert cls, f"§3.3 class row this pin cannot identify: {cells[0]!r}"
        seen.add(cls)
        n = int(_num(cells[1]))
        assert n == artifact["arms"]["falkordb_tfidf"]["per_class"][cls]["n"], (
            f"§3.3 {cls} n: brief {n} vs artifact "
            f"{artifact['arms']['falkordb_tfidf']['per_class'][cls]['n']}")
        for arm, cell in zip(ARM_ORDER_31, cells[2:], strict=True):
            k3, k15 = (round(_num(p), 4) for p in _plain(cell).split("/"))
            for k, got in (("3", k3), ("15", k15)):
                want = artifact["arms"][arm]["per_class"][cls][k]
                assert got == round(want, 4), (
                    f"§3.3 {cls} arm {arm} recall@{k}: brief {got} vs artifact {want}")
    assert seen == set(cls_of.values()), f"§3.3 lost a class: {sorted(set(cls_of.values()) - seen)}"


def test_brief_fixture_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.5 — the `#8933` fixture table and the sentence that summarises it."""
    arms = ("falkordb_tfidf", "falkordb_tfidf__tokens_fixed",
            "postgres_bm25/tokens", "postgres_bm25/native")
    by_stored = {
        arm: {r["stored"]: r for r in artifact["arms"][arm]["identifier_fixture"]["results"]}
        for arm in arms
    }
    rows = _table(_scope_slice(brief, "3.5"), "stored name", "query")
    assert len(rows) == 5, f"§3.5 no longer has its five fixture rows: {len(rows)}"
    for cells in rows:
        stored = _plain(cells[0])
        for arm, cell in zip(arms, cells[2:], strict=True):
            res = by_stored[arm][stored]
            got = _plain(cell).split(" (")[0].strip()
            if got == "never":
                assert res["rank_wide"] is None, (
                    f"§3.5 {stored} on {arm}: the brief says never, the artifact "
                    f"reaches it at k=15 rank {res['rank_wide']}")
            else:
                expect = res["rank"] if res["rank"] is not None else res["rank_wide"]
                assert int(_num(got)) == expect, (
                    f"§3.5 {stored} on {arm}: brief {got!r} vs artifact rank {expect}")
    # The summary sentence, built from the artifact rather than restated.
    fx_arm = "falkordb_tfidf__tokens_fixed"
    sh = artifact["arms"]["falkordb_tfidf"]["identifier_fixture"]
    fx = artifact["arms"][fx_arm]["identifier_fixture"]
    tk = artifact["arms"]["postgres_bm25/tokens"]["identifier_fixture"]
    nv = artifact["arms"]["postgres_bm25/native"]["identifier_fixture"]
    n = sh["n"]
    sentence = (f"Shipped: {sh['reachable']} of {n} reachable. In-place fix: "
                f"{fx['reachable']} of {n}. `pg/tokens`: {tk['reachable']} of {n}. "
                f"`pg/native`: {nv['reachable']} of {n}.")
    # ⛔ §3.5-SCOPED (#12349 round 16, finding 4): a whole-document `in " ".join(...)`
    # was satisfied by a copy of this sentence planted in §4 while §3.5's own
    # summary was falsified — measured M-C1, green. And SHAPE-UNIQUE (#12349 round
    # 24): even §-scoped, a restatement inside §3.5 kept the falsified summary
    # green, so the numbers must appear exactly once.
    _only_claim(_scoped(brief, "3.5"), sentence, "§3.5's fixture summary",
                free=("reachable", "shipped"),
                values=(str(sh["reachable"]), str(n), str(fx["reachable"]),
                        str(tk["reachable"]), str(nv["reachable"])))


# The forms `_scope_slice` reads. Anything else that opens a section is a leak.
_READABLE_HEADING = re.compile(r"^(?:# [^#\s].*|## \d+\. .*|### \d+\.\d+ .*)$")
# The container prefixes a block can sit behind — blockquote and list markers,
# nested in any order (CommonMark 5.1/5.2). `_scope_slice` anchors at column 0, so a
# section opened behind ANY of them is invisible to it.
# ⛔ `\d+[.)]`, not `\d+\.`: CommonMark 5.2's ordered-list marker is "a
# sequence of 1-9 arabic digits, followed by either a `.` character or a `)`
# character". A `)` item is invisible to `_scope_slice`, so a section opened
# behind one leaks exactly like the `-`/`*`/`+`/`>` cases (measured: falsify
# §2, restate under `1) <h2>…</h2>` -> 101 green before this line).
_BLOCK_PREFIX = r"(?: {0,3}(?:>[ \t]?|[-*+][ \t]+|\d{1,9}[.)][ \t]+))* {0,3}"


def test_the_brief_uses_only_heading_forms_the_scope_helper_can_see(
        brief: str) -> None:
    """The brief gets the guard too — it is the document every table pin reads.

    ⛔ Round 21, P1: this test was DROPPED when the parser oracle replaced the regex
    scan, while `_READABLE_BRIEF` was still defined for it — so the changelog and
    `SEARCH-ARCHITECTURE.md` were guarded and the research brief, whose §-scoped
    slices carry every published table, was not. §2's slice is what a restatement
    would be absorbed into; that is the whole reason the guard exists.
    """
    _assert_only_readable_headings(brief, _READABLE_BRIEF, "research-brief.md")


# Every heading form CommonMark renders that `_scope_slice` cannot read.
# ⛔ EACH ONE MUST *RENDER*. Round 21's P2: the previous list wrote its setext cases
# as `3. Results` followed by a run of `=`/`-`, and `3. Results` is an ORDERED LIST
# ITEM, not a paragraph — so `3. Results\n---` renders `<ol><li>Results</li></ol><hr>`
# and NO heading at all. Seven of the sixteen cases therefore proved nothing: the
# "leak" half passed for every one of them because *any* line that is not `## ` fails
# to end a level-2 slice, which is not the property under test (the vacuity control
# `banana` passed it too). The prose line here is `Results`; the assertion asks the
# parser rather than the test author whether a heading exists.
RENDERED_BUT_UNREADABLE = [
    "### 3. Results",                   # ATX level 3
    "#### 3. Results",                  # ATX level 4
    "##### 3. Results",                 # ATX level 5
    "###### 3. Results",                # ATX level 6
    "   ## 3. Results",                 # ATX, 3-space indent (valid CommonMark)
    "> ## 3. Results",                  # ATX behind a blockquote marker
    "> > ## 3. Results",                # … and behind two
    "- ## 3. Results",                  # ATX inside a list item
    "1. ## 3. Results",                 # … inside an ordered one
    "> - ## 3. Results",                # … inside both
    "Results\n-",                        # setext, ONE-character underline
    "Results\n--",                       # setext, two characters
    "Results\n---",                      # setext, three characters
    "Results\n=",                        # setext h1, one character
    "Results\n==",                       # setext h1, two characters
    "Results\n===",                      # setext h1, three characters
    "Results\n  ---",                    # setext, 2-space indent (valid CommonMark)
    "Results\n   ---",                   # setext, 3-space indent
    "> Results\n> ---",                  # setext inside a blockquote
    "> Results\n>===",                   # … with no space after the marker
]

# Section openers that render NO heading but are still invisible boundaries: a
# reader sees a section, `_scope_slice` does not, so the preceding scope runs
# through them exactly as it does through a real heading.
LOOKALIKE_BUT_UNREADABLE = [
    "##3. Results",                     # no space after the marker (renders as prose)
    "- ##3. Results",                   # … inside a list item
    "<h2>3. Results</h2>",              # raw HTML heading at column 0
    "> <h2>3. Results</h2>",             # … behind a blockquote marker
    "- <h2>3. Results</h2>",             # … inside a list item
    "1) <h2>3. Results</h2>",           # … inside a `)`-marked ordered list item
    "1) ##3. Results",                  # `)`-marked item, no space after the run
    "    ## 3. Results",                # INDENTED CODE: renders nothing, bounds nothing
    "\t## 3. Results",                  # … tab-indented

    "    1. ## 3. Results",             # … indented, behind an ordered marker
    "    1) ## 3. Results",             # … behind a `)`-marked one
    "\t- ## 3. Results",                # … tab-indented, behind a bullet
    "\t> ## 3. Results",                # … tab-indented, behind a quote
    "    <h2>3. Results</h2>",          # … indented raw HTML heading
    "\t<h2>3. Results</h2>",            # … tab-indented raw HTML heading

    "    - - ## 3. Results",             # … NESTED container markers (depth 2)
    "    1. 1. ## 3. Results",            # … nested ordered markers
    "    > - ## 3. Results",             # … quote then bullet
]


def _rendered_heading_tags(doc: str) -> list[str]:
    """The tags the parser emits as headings — the oracle for `RENDERED_BUT…`."""
    return [tok.tag for tok in MarkdownIt("commonmark").parse(doc)
            if tok.type == "heading_open"]


@pytest.mark.parametrize("opener", RENDERED_BUT_UNREADABLE)
def test_every_rendered_heading_form_the_helper_cannot_read_is_refused(
        opener: str) -> None:
    """A rendered heading the helper cannot read must leak AND be refused.

    Both halves, because either alone is satisfiable by a form that proves nothing:
    a form that is refused without leaking is over-blocking, and a form that leaks
    without rendering a heading is not the class under test (it is merely "a line
    that is not `## `").
    """
    doc = f"## 2. Method\n\n§2 body.\n\n{opener}\n\n§3 BODY\n"
    # 1. the opener really is a heading the parser renders
    assert len(_rendered_heading_tags(doc)) >= 2, (
        f"{opener!r} renders NO heading beyond §2's own — this case would prove "
        "nothing about headings and must not be counted as coverage")
    # 2. … and the helper's §2 scope really does swallow §3's body through it
    assert "§3 BODY" in _scope_slice(doc, "2"), opener
    # 3. … and the guard refuses it
    assert _unscoped_heading_forms(doc), (
        f"{opener!r} renders a heading, leaks, and the guard ACCEPTS it — the "
        "fail-open this guard exists to close")


@pytest.mark.parametrize("opener", LOOKALIKE_BUT_UNREADABLE)
def test_every_invisible_section_lookalike_is_refused(opener: str) -> None:
    """The lookalikes render no heading, so their half is `leak + refused` only."""
    doc = f"## 2. Method\n\n§2 body.\n\n{opener}\n\n§3 BODY\n"
    assert "§3 BODY" in _scope_slice(doc, "2"), opener
    assert _unscoped_heading_forms(doc), (
        f"{opener!r} opens a section a reader can see and the helper cannot, and "
        "the guard ACCEPTS it")


# ⛔ A column-0 heading the parser does NOT render: `_scope_slice` still ends the
# section there, so it TRUNCATES. Its leak is different in kind from the lists
# above (it hides the section's own TAIL rather than swallowing the next §), so it
# gets its own two-sided test rather than being folded into a list whose leak
# assertion would not describe it (#12349 rounds 21-22: a fenced
# `## Restated finding` at the end of §2 hid a false census, and a §2 that
# contradicted its own reuse guard, with the whole suite green).
TRUNCATING_HEADING_FORMS = [
    "```\n## Restated finding\n```",   # fenced, non-numeric heading
    "```\n## 3. Results\n```",         # fenced, numeric heading
    "```\n# Restated finding\n```",     # fenced h1
]


@pytest.mark.parametrize("form", TRUNCATING_HEADING_FORMS)
def test_a_fenced_heading_that_truncates_the_scope_is_refused(form: str) -> None:
    """Each form must BOTH truncate `_scope_slice` AND be refused by the guard."""
    doc = f"## 2. Method\n\n\u00a72 body.\n\n{form}\n\n\u00a72 TAIL\n\n## 4. Next\n"
    assert "\u00a72 TAIL" not in _scope_slice(doc, "2"), (
        f"{form!r} does not truncate \u2014 this case would prove nothing")
    assert _unscoped_heading_forms(doc), (
        f"{form!r} truncates \u00a72 and the guard accepts it \u2014 a presence pin can "
        "then be satisfied from before the truncation")


def test_the_heading_guard_accepts_every_form_the_helper_can_read() -> None:
    """The over-refusal half, so the guard cannot be "repaired" by weakening it.

    A guard that refused the document's REAL headings would red this suite for a
    reason unrelated to the claim under test, and the obvious repair from there is
    to delete the guard.
    """
    # the readable forms, including the document title at the top
    for doc in (
            "# M3 — the FTS leg\n\nintro\n\n## 2. Method\n\nbody\n\n### 2.1 Why\n\nx\n",
            "## 2. Method\n\nbody\n\n## 3. Results\n\nprose\n",
            "## 2. Method\n\nbody\n\n### 2.1 Recall@k\n\n## 4. Verdict\n"):
        assert not _unscoped_heading_forms(doc), doc
    # `# ` mid-document is accepted: it BOUNDS a level-2 slice (`end` is
    # `^#{1,2} `), so it cannot absorb the preceding §, and a `# ` section is
    # unreadable-fail-closed rather than a leak. Refusing it here was measured as
    # over-blocking in round 21.
    assert not _unscoped_heading_forms("## 2. Method\n\nbody\n\n# 3. Results\n\nmore\n")
    assert "# 3. Results" not in _scope_slice(
        "## 2. Method\n\n§2 body.\n\n# 3. Results\n\n§3 BODY\n", "2")
    # a readable §3 really does end §2's scope — the control for the leak half
    assert "§3 BODY" not in _scope_slice(
        "## 2. Method\n\n§2 body.\n\n## 3. Results\n\n§3 BODY\n", "2")
    # and `---` used as a THEMATIC BREAK after a heading is not a setext underline
    assert not _unscoped_heading_forms("## 2. Method\n---\n\nbody\n")
    # a subsection numbered for a DIFFERENT section is refused; the enclosing
    # number is what decides (#12349 round 24, P1)
    assert _unscoped_heading_forms("## 2. Method\n\nbody\n\n### 3.9 R\n\nx\n")
    assert not _unscoped_heading_forms("## 2. Method\n\nbody\n\n### 2.9 R\n\nx\n")



def test_the_changelog_uses_only_heading_forms_the_scope_helper_can_see(
        changelog: str) -> None:
    """The changelog gets the same guard (#12349 round 20, finding 1).

    `_scoped(changelog, "#12298")` ends at the next heading, so a following entry
    written in a form the helper cannot see would be ABSORBED into this entry —
    measured: falsifying `#12298`'s `is **−0.67 at k=5**` and appending `#12350`
    under a setext underline kept the whole suite green.
    """
    _assert_only_readable_headings(changelog, _READABLE_CHANGELOG,
                                   "SEARCH-ARCHITECTURE-CHANGELOG.md")


def test_the_decision_document_uses_only_heading_forms_the_scope_helper_can_see(
        search_arch: str) -> None:
    """`SEARCH-ARCHITECTURE.md` §6.2 is read through `_scoped`, so it needs the
    same guard (#12349 round 20, finding 1)."""
    _assert_only_readable_headings(search_arch, _READABLE_ARCH,
                                   "SEARCH-ARCHITECTURE.md")


def test_brief_census_and_identifier_decomposition_are_the_artifact(
        artifact, population, brief: str) -> None:
    """§2's control break and §3.3's identifier decomposition — recomputed.

    ⛔ §3.3, not §3.4 (§3.4 is "The mechanism — a tokenizer contract mismatch"):
    three review rounds labelled this block §3.4, which sends a red pin's reader
    to the wrong section of the brief.

    The decomposition is what makes §4.3's conclusion ("the scorer is not the
    constraint") checkable, and it is arithmetic over the committed ranks, so a
    re-run must move it here rather than only in the artifact.
    """
    census = artifact["terms_census"]
    # ⛔ EVERY PIN BELOW READS ITS OWN §-SCOPED SLICE, never the whole document
    # (#12349 rounds 13-16 — the same class four times, so the scope is derived
    # once by `_scoped` and used everywhere rather than patched per round):
    #   * r13 whole-document `in text` -> a §4 restatement + edited §2 cells = green;
    #   * r15 the census COUNT was still whole-document -> §2 `126`->`226` with
    #     the true 126 restated in §4 = green;
    #   * r15 `next(...)` took the FIRST match anywhere -> an earlier §0 sentence
    #     carrying the true split = green;
    #   * r16 a restatement INSIDE §2, and a `## 2. ` MENTION in §1 (indistinguishable
    #     from the heading once the document is flattened), both = green.
    # `_scoped` is heading-anchored on the RAW document and requires exactly one
    # heading; the census sentence is then located BY THE COUNT, not by a phrase
    # that a restatement can also carry.
    section2 = _scoped(brief, "2")
    sentences2 = re.split(r"(?<=\.)\s+", section2)
    # ⛔ UNIQUENESS IS ABOUT THE CLAIM'S SHAPE, NOT ABOUT THE TRUE TEXT
    # (#12349 round 17, P1). The previous cut counted occurrences of the TRUE
    # statement, so a FALSE census statement elsewhere in §2 was invisible and
    # `next(...)` then read the planted true one — measured: `226 of the 300` in
    # §2's disclosure sentence plus `126 of the 300` in an added §2 restatement
    # kept all 72 green. So: exactly ONE census-SHAPED statement in §2, and it
    # must carry the artifact's numbers.
    census_shape = r"(?<!\d)\d+ of the \d+ queries are degenerate"
    census_true = (f"{census['degenerate']} of the {census['queries']} "
                   "queries are degenerate")
    assert re.findall(census_shape, section2) == [census_true], (
        "§2 must state its degenerate-query census EXACTLY ONCE, and to the "
        f"artifact's numbers ({census_true!r}) — §2 carries "
        f"{re.findall(census_shape, section2)}. A second, FALSE statement is "
        "what let a restatement satisfy this pin while §2's own sentence was "
        "falsified (round 17, P1)")
    census_sentence = next(s for s in sentences2 if re.search(census_shape, s))
    # ⛔ §2's DEGENERATE CLASS SPLIT is published ("108 `definite` … and 18
    # ident — none of them a path") and was unpinned while §2's census is
    # described as compared CELL BY CELL (#12349 review, P2-2): rewriting those
    # cells to 118/28 kept all 72 tests green. It is arithmetic over
    # `degenerate_ids` + `names[i].cls`, exactly like §3.3's decomposition, so
    # it gets the same cell treatment.
    by_id = {n["id"]: n for n in population["names"]}
    deg = Counter(by_id[i]["cls"] for i in
                  artifact["arms"]["postgres_bm25/tokens"]["degenerate_ids"])
    # §2 also publishes the POPULATION's stratification ("a 300-name sample
    # (180 `definite` / 90 `ident` / 30 `path`)"), a different pair from the
    # degenerate split — and nothing pinned it, so §2 could be made to contradict
    # `population.json` and §3.3's pinned cells with all 73 green (#12349 round
    # 18, P2). Pinned here, from the population itself.
    strat = Counter(n["cls"] for n in population["names"])
    # ⛔ SHAPE-UNIQUE, not a presence check (#12349 round 24): §2 legitimately
    # carries TWO `definite`/`ident` pairs (this stratification and the
    # degenerate split), so the claim is matched as ONE shape and required
    # exactly once — a falsified stratification with the true one restated
    # beside it now reds instead of passing on presence. ⛔ The claim carries its
    # IDENTIFYING context (`a 300-name sample (…)`) and `values` names only its
    # VALUES: wildcarding every digit made the shape match the degenerate
    # split's own three-way list and false-red on correct prose (round-24
    # reviewer).
    _only_claim(
        section2,
        f"a {len(population['names'])}-name sample "
        f"({strat['definite']} `definite` / {strat['ident']} `ident` / "
        f"{strat['path']} `path`)",
        "§2's 300-name stratification",
        values=(str(len(population["names"])), str(strat["definite"]),
                str(strat["ident"]), str(strat["path"])),
        # ⛔ The CLASS NAMES are the claim here: swapping which class gets 90 and
        # which gets 30 keeps every count in place, so the numbers alone cannot
        # see it (round-24 reviewer, cycle 4).
        free=("definite", "ident", "path"))
    assert sum(deg.values()) == census["degenerate"], (
        f"the degenerate ids cover {sum(deg.values())} of the census's "
        f"{census['degenerate']} — the split below would then describe a "
        "different population")
    # ⛔ The split clauses are read from the CENSUS SENTENCE — the one statement
    # located by shape above — not from anywhere in §2: §2 legitimately carries a
    # SECOND `definite`/`ident` pair (the 300-name sample stratification, 180/90),
    # so a section-wide "every statement agrees" rule would red on correct prose.
    # The artifact-side split it must agree with is the DEGENERATE one.
    assert re.search(rf"(?<!\d){deg['definite']} `definite`", census_sentence), (
        f"§2's own census sentence must state {deg['definite']} `definite` "
        f"degenerate queries — recomputed from `degenerate_ids` and the pinned "
        f"classes, and read from the sentence that carries the census rather "
        f"than from anywhere in the document: {census_sentence}")
    assert re.search(rf"(?<!\d){deg['ident']} ident", census_sentence), (
        f"§2's own census sentence must state {deg['ident']} ident degenerate "
        f"queries — recomputed from `degenerate_ids` and the pinned classes: "
        f"{census_sentence}")
    assert (deg["path"] == 0) == ("none of them a path" in census_sentence), (
        "§2's `none of them a path` clause must agree with the classes: "
        f"{deg['path']} degenerate queries are `path`")
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    _only_claim(section2, f"{subset['n']} tokens-controlled queries",
                "§2's tokens-controlled population", values=(str(subset["n"]),))
    # §2's OWN published triple, and the control-break disclosure that gives it
    # meaning (#12349 round 16, finding 6): both were unpinned, so §2 could state
    # `0.6172 → 0.6287, +2.15 pts, p = 0.827` — contradicting the artifact — with
    # every gate green, and the disclosure sentence could be DELETED silently.
    triple = (f"{subset['recall_at_3_base']:.4f} → {subset['recall_at_3']:.4f}, "
              f"+{subset['delta_pts']:.2f} pts, "
              f"p = {subset['mcnemar_p_two_sided']:.3f}")
    _only_claim(section2, triple, "§2's tokens-controlled triple", free=("\u2192",),
                values=(f"{subset['recall_at_3_base']:.4f}",
                        f"{subset['recall_at_3']:.4f}",
                        f"{subset['delta_pts']:.2f}",
                        f"{subset['mcnemar_p_two_sided']:.3f}"))
    def _hit3(ranks: dict, i: str) -> bool:
        return ranks.get(i) is not None and ranks[i] <= 3

    _shipped = artifact["arms"]["falkordb_tfidf"]["ranks"]
    _pg_tokens = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    _gains = [i for i in _shipped
              if _hit3(_pg_tokens, i) and not _hit3(_shipped, i)]
    _losses = [i for i in _shipped
               if _hit3(_shipped, i) and not _hit3(_pg_tokens, i)]
    net_rows = len(_gains) - len(_losses)
    _deg_ids = set(artifact["arms"]["postgres_bm25/tokens"]["degenerate_ids"])
    on_collapsed = sum(1 for i in _gains if i in _deg_ids)
    _only_claim(section2, f"net +{net_rows} rows at k=3",
                "§2's net row count", values=(str(net_rows),))
    _only_claim(section2, f"**{on_collapsed}** sit on collapsed rows",
                "§2's collapsed-row share", values=(str(on_collapsed),))
    # ⛔ The disclosure is DERIVED, not restated: the artifact's own ranks must
    # show the two Postgres arms agreeing on EVERY degenerate id, which is what
    # makes the sentence true. Then the sentence itself is shape-unique, so
    # deleting it (or rewriting it to "differ") reds instead of passing.
    _tok_ranks = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    _nat_ranks = artifact["arms"]["postgres_bm25/native"]["ranks"]
    # ⛔ `.get`, not `[i]`: `degenerate_ids` comes from the TOKENS arm, so a
    # missing id in the NATIVE map must be reported by THIS assertion's message
    # (the reader needs to know the control break changed shape) rather than by
    # a bare `KeyError` with no context (round-24 reviewer, P2).
    _differ = [i for i in _deg_ids
               if _tok_ranks.get(i) != _nat_ranks.get(i)]
    assert not _differ, (
        "§2 discloses that the two Postgres arms' ranks are identical on every "
        f"degenerate query; the artifact now differs on {len(_differ)} of "
        f"{len(_deg_ids)} ({_differ[:3]}) — the control break changed shape and "
        "the disclosure, §2's split and §0's k=5 reading all need re-deriving")
    _only_claim(
        section2,
        "for every one of them the two Postgres arms' ranks are identical.",
        "§2's control-break disclosure", free=("identical", "every"))
    # §0's subset row states BOTH recalls, and §0 is where a reader quotes from —
    # read from §0's SLICE, like every other pin here (round 17, P2: the previous
    # form read the whole document, so the comment above was wider than the code).
    subset_row = next(
        (r for r in _table(_scope_slice(brief, "0"), "recall@3", "Δ vs shipped")
         if "subset" in _plain(r[0]).lower()), None)
    assert subset_row is not None, "§0 no longer carries the subset row"
    base, arm = (round(_num(p), 4) for p in _plain(subset_row[1]).split("vs"))
    # The brief states this cell arm-vs-baseline (`0.5287 vs 0.5172`), the same
    # order §3.2's Δ column uses; read it that way rather than assuming it.
    assert arm == round(subset["recall_at_3_base"], 4), (
        f"§0 subset baseline: brief {arm} vs artifact {subset['recall_at_3_base']}")
    assert base == round(subset["recall_at_3"], 4), (
        f"§0 subset arm: brief {base} vs artifact {subset['recall_at_3']}")

    ident = [n for n in population["names"] if n["cls"] == "ident"]
    hashed = [n["id"] for n in ident if "#" in n["name"]]
    snake = [n["id"] for n in ident if "#" not in n["name"]]

    def found(arm_name: str, ids: list[str]) -> int:
        ranks = artifact["arms"][arm_name]["ranks"]
        # ⛔ `<= 3`, not `< 3` — rank 3 IS found at k=3, and `< 3` silently
        # recomputes recall@2 while the brief publishes the result as recall@3
        # (review r3: inert in the committed artifact only because no `ident`
        # name sits at exactly rank 3, which is a property of a FROZEN file).
        # `ranks` is 1-BASED — `ids.index(row["id"]) + 1` in the harness — which
        # is what makes the off-by-one invisible to a reader comparing it against
        # a 0-based mental model of `index()`.
        return sum(1 for i in ids if ranks.get(i) is not None and ranks[i] <= 3)

    sh = found("falkordb_tfidf", hashed)
    sn_sh = found("falkordb_tfidf", snake)
    sn_fx = found("falkordb_tfidf__tokens_fixed", snake)
    # ⛔ §3.3-SCOPED (#12349 round 16, finding 3): these three were whole-document
    # substrings, so the paragraph could be falsified in place while a copy of the
    # true sentence sat in another section — measured M-B1/M-B2/M-B3, all green.
    # The section was ALSO mislabelled §3.4 in this block, in its messages and in
    # the CI row: §3.4 is "The mechanism — a tokenizer contract mismatch", and
    # this paragraph is §3.3 "Per class (recall@3 / recall@15)".
    section33 = _scoped(brief, "3.3")
    plain33 = section33.replace("**", "")
    # ⛔ SHAPE-UNIQUE (#12349 round 24 reviewer, P2: these were the remaining
    # §-scoped PRESENCE pins, each measured GREEN when falsified in place with the
    # truth restated beside it in §3.3).
    _only_claim(section33, f"the {len(hashed)} `#n` shapes",
                "§3.3's `#n` population", values=(str(len(hashed)),))
    # ⛔ EVERY digit here is a value, so `**N of N**` alone identifies nothing: a
    # second, TRUE bolded ratio in §3.3 false-reds it (round-24 reviewer, cycle 8).
    # The claim carries the sentence's own words.
    _only_claim(section33,
                f"found by the **shipped** leg **{sh} of {len(hashed)}**",
                "§3.3's `#n` recall cell",
                values=(str(sh), str(len(hashed))),
                free=("shipped", "found"))
    want = (f"the {len(snake)} `snake_case` names: {sn_sh} of {len(snake)} "
            f"({sn_sh / len(snake):.3f}) shipped → {sn_fx} of {len(snake)} "
            f"({sn_fx / len(snake):.3f}) with the in-place fix, "
            f"+{(sn_fx - sn_sh) / len(snake) * 100:.1f} pts")
    _only_claim(plain33, want,
                "§3.3's `snake_case` decomposition (re-derived from the ranks)",
                values=(str(len(snake)), str(sn_sh),
                        f"{sn_sh / len(snake):.3f}", str(sn_fx),
                        f"{sn_fx / len(snake):.3f}",
                        f"{(sn_fx - sn_sh) / len(snake) * 100:.1f}"),
                # ⛔ `with|without` inverts which arm the +91.8 belongs to.
                free=("\u2192", "shipped", "with|without"))


def test_changelog_does_not_restate_superseded_numbers(artifact, population,
                                                      changelog: str) -> None:
    """`SEARCH-ARCHITECTURE-CHANGELOG.md` duplicates §0 and the fixture counts.

    It is the record `SEARCH-ARCHITECTURE.md` §6.2's reader opens to learn why the
    text index did not move, and nothing read it before this test: a re-run could
    move every number in the brief and leave the changelog quoting the superseded
    ones forever (review cycle 2, finding 3).
    """
    arms = artifact["arms"]
    # ⛔ `#12298`'s ENTRY, not the whole 122-line file (#12349 round 22, P1):
    # `_table` returns the FIRST matching table, so a complete decoy table under an
    # earlier entry satisfied this pin while `#12298`'s own row was falsified
    # (measured B23, green). The test's other two halves already read `_scoped`.
    rows = _table(_scope_slice(changelog, "#12298"), "Δ vs shipped", "recall@3")
    expected = [
        ("db.idx.fulltext", arms["falkordb_tfidf"]["recall_by_k"]["3"]["recall"]),
        ("our production terms", arms["postgres_bm25/tokens"]["recall_by_k"]["3"]["recall"]),
        ("its own analyser", arms["postgres_bm25/native"]["recall_by_k"]["3"]["recall"]),
        ("the `_`-preserving runs",
         arms["falkordb_tfidf__tokens_fixed"]["recall_by_k"]["3"]["recall"]),
    ]
    for needle, want in expected:
        hit = [r for r in rows if needle in r[0]]
        assert len(hit) == 1, f"the changelog table lost its {needle!r} row"
        assert round(_num(hit[0][1]), 4) == round(want, 4), (
            f"the changelog states recall@3 {_plain(hit[0][1])} for {needle!r}; "
            f"the committed artifact says {want:.4f}")
    subset = [r for r in rows if "subset" in r[0]]
    assert len(subset) == 1, "the changelog table lost its tokens-controlled subset row"
    # ⛔ The row's LABEL is a claim too (#12349 review, P2): the cell pair below
    # was pinned and the `174 of 300` label was not — measured, `174` → `173`
    # left all 118 tests GREEN while the brief's own `174` (which IS pinned) then
    # contradicted it in the same release. `n` is the subset's own denominator,
    # not the population's, because the comparison is computed over 174 rows.
    n_sub = artifact["comparisons"]["tokens_controlled_subset"]["n"]
    n_pop = len(population["names"])
    assert n_pop == artifact["sample"], (
        f"the committed population holds {n_pop} names; the artifact measured "
        f"a sample of {artifact['sample']} — the two must be the same run")
    assert f"({n_sub} of {n_pop} queries)" in subset[0][0], (
        f"the changelog's subset row states {subset[0][0]!r}; the artifact "
        f"records n = {n_sub} over a {n_pop}-name population")
    pair = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    # Arm-vs-baseline, the same order the brief's §0 cell uses.
    arm, base = (round(_num(p), 4) for p in _plain(subset[0][1]).split("vs"))
    assert (arm, base) == (round(pair["recall_at_3"], 4),
                           round(pair["recall_at_3_base"], 4))

    text = _scoped(changelog, "#12298").replace("**", "")
    k5 = artifact["comparisons"]["falkordb_tfidf__vs__postgres_bm25/tokens"]["k5"]
    assert k5["only_b"] == 0 and k5["only_a"] > 0, (
        "the k=5 comparison no longer favours the shipped leg \u2014 the changelog's "
        "k=5 sentence needs re-deriving")
    # ⛔ SHAPE-UNIQUE (#12349 round 24 reviewer, P2): these two were PRESENCE pins,
    # each measured GREEN when falsified in place and restated inside the same
    # `#12298` entry.
    _only_claim(
        text,
        f"is {'\u2212' if k5['delta_pts'] < 0 else '+'}"
        f"{abs(k5['delta_pts']):.2f} at k=5",
        "the changelog's k=5 direction",
        values=(f"{abs(k5['delta_pts']):.2f}",))
    n = arms["falkordb_tfidf"]["identifier_fixture"]["n"]
    reach = {a: arms[a]["identifier_fixture"]["reachable"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed",
        "postgres_bm25/tokens", "postgres_bm25/native")}
    sentence = (f"shipped {reach['falkordb_tfidf']} of {n} reachable, the in-place "
                f"query {reach['falkordb_tfidf__tokens_fixed']} of {n}, BM25 fed our "
                f"terms {reach['postgres_bm25/tokens']} of {n}, BM25 with its own "
                f"analyser {reach['postgres_bm25/native']} of {n}.")
    _only_claim(text, sentence, "the changelog's `#8933` fixture counts",
                free=("reachable", "shipped"),
                values=(str(reach["falkordb_tfidf"]), str(n),
                        str(reach["falkordb_tfidf__tokens_fixed"]),
                        str(reach["postgres_bm25/tokens"]),
                        str(reach["postgres_bm25/native"])))
    # ⛔ The `snake_case` finding had NO pin in either document (a single edit was
    # green, #12349 round 24), so it is re-derived from the committed ranks and
    # required to appear exactly once in the `#12298` entry.
    snakes = [n["id"] for n in population["names"]
              if n["cls"] == "ident" and "#" not in n["name"]]
    sh_ranks = arms["falkordb_tfidf"]["ranks"]
    fx_ranks = arms["falkordb_tfidf__tokens_fixed"]["ranks"]
    a = sum(1 for i in snakes
            if sh_ranks.get(i) is not None and sh_ranks[i] <= 3)
    b = sum(1 for i in snakes
            if fx_ranks.get(i) is not None and fx_ranks[i] <= 3)
    _only_claim(
        text,
        f"{a} was found at k=3 by the shipped leg and {b} by the in-place query",
        "the changelog's `snake_case` finding",
        # ⛔ `values` is what makes the falsified copy MATCH the shape: without it
        # the shape is the literal sentence and a plant beside it went green
        # (measured: P3 GREEN on the first cut of this round).
        values=(str(a), str(b)), free=("shipped", "found"))


# ── 6. the two things a re-run would otherwise decide wrongly ───────────────

def test_the_degraded_leg_refusal_is_WIRED_at_every_shipped_leg_call(tool_mod) -> None:
    """The refusal must be called AT the call site, with the trace actually passed.

    ⛔ A unit test of `_refuse_degraded_legs` alone cannot see the two regressions
    that matter (#12349 review cycle 5, finding 3 — both measured GREEN before
    this test existed): deleting the call from the sweep, and calling
    `_refuse_degraded_legs(trace, q)` while dropping `leg_trace=trace` from
    `run_fts_query`. The second leaves the trace EMPTY, so the refusal is a
    silent no-op — which is the fail-open it was written to close. Neither needs
    a database to detect: they are properties of the source.

    So this parses the harness and asserts the wiring: every non-warmup
    `run_fts_query(...)` call in `arm_falkordb` passes `leg_trace=` a NAMED list,
    and each `_refuse_degraded_legs(...)` call is handed the same name the
    nearest preceding measured call wrote its trace to. A count would not do:
    refusing on a DIFFERENT list leaves the refusal reading an empty trace and
    passing every degraded read.
    """
    import ast

    src = TOOL.read_text()
    tree = ast.parse(src)
    sweep = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert sweep is not None, "arm_falkordb is the measurement's sweep"

    def _calls(node, name):
        return [n for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) == name
                     or getattr(n.func, "attr", None) == name)]

    def _innermost_loop(call, fn):
        """The innermost ``for``/``while`` body the call sits in, or ``None``.

        ⛔ SCOPE, not line order (#12349 review cycle 8). The previous attempt
        asserted the refusal came before the NEXT measured call by line number —
        which is **unsatisfiable**: any call after the paired one is also after the
        refusal's own line, so the assertion could never fire, and a refusal
        hoisted out of the inner loop stayed GREEN. Measured by the reviewer:
        hoisting it to the end of the row loop left 900 of 1200 measured reads
        unguarded with the pin reporting OK. Two call sites 40 lines apart cannot
        see a 14-line hoist; their ENCLOSING BLOCKS can.
        """
        found = []

        def walk(node, cur):
            for child in ast.iter_child_nodes(node):
                nxt = (child if isinstance(child, (ast.For, ast.While,
                                                   ast.AsyncFor))
                       else cur)
                if child is call:
                    found.append(cur)
                walk(child, nxt)

        walk(fn, None)
        return found[0] if found else None

    def _is_warmup(call) -> bool:
        """The pre-wait warm query, whose RESULT is discarded.

        ⛔ Excluded deliberately, and the exclusion is the only one: it runs
        BEFORE `_wait_indexed`, so on a cold start the index legitimately is not
        built yet and a refusal there would abort every honest run. It measures
        nothing, so it cannot publish a degraded read as a number.
        """
        return any(isinstance(a, ast.Constant) and a.value == "warm"
                   for a in call.args)

    legs = [c for c in _calls(sweep, "run_fts_query") if not _is_warmup(c)]
    assert legs, "the shipped leg must be called from the sweep"
    refusals = _calls(sweep, "_refuse_degraded_legs")
    # ⛔ TWO DIRECTIONS, not a count (#12349 review cycle 8). A global
    # `len(refusals) >= len(legs)` is satisfied by duplicating ONE loop's
    # refusal, and that was measured: deleting the sweep's refusal in
    # `arm_falkordb` and duplicating the fixture loop's left 1,200 sweep reads
    # (300 names x 4 k) unguarded with the count check GREEN. So the contract is
    # asserted per measured call: every measured call owns at least one refusal
    # before the NEXT measured call; that refusal reads the same trace NAME the
    # call wrote; and it sits in the same innermost loop body (a hoisted refusal
    # runs once per row and leaves every k but the last unguarded).
    legs_sorted = sorted(legs, key=lambda c: c.lineno)
    for i, call in enumerate(legs_sorted):
        kw = {k.arg: k.value for k in call.keywords}
        assert "leg_trace" in kw, (
            "a `run_fts_query` call does not pass `leg_trace=` — the trace stays "
            "empty and `_refuse_degraded_legs` becomes a silent no-op")
        assert isinstance(kw["leg_trace"], ast.Name), (
            "`leg_trace=` must be a named list the refusal can also read, not an "
            "inline expression")
        name = kw["leg_trace"].id
        nxt = (legs_sorted[i + 1].lineno if i + 1 < len(legs_sorted)
               else float("inf"))
        owned = [r for r in refusals if call.lineno < r.lineno < nxt]
        assert owned, (
            f"the measured `run_fts_query` call at line {call.lineno} has no "
            f"`_refuse_degraded_legs` call before the next measured call at line "
            f"{nxt} — every read it makes is unguarded, and a degraded read books "
            f"as a measurement")
        for refusal in owned:
            first = refusal.args[0] if refusal.args else None
            assert isinstance(first, ast.Name) and first.id == name, (
                f"`_refuse_degraded_legs` at line {refusal.lineno} must read the "
                f"trace the leg wrote ({name!r}); it reads "
                f"{getattr(first, 'id', first)!r} — a refusal inspecting an empty "
                "or stale list passes every degraded read")
            assert _innermost_loop(refusal, sweep) is _innermost_loop(call, sweep), (
                f"`_refuse_degraded_legs` at line {refusal.lineno} is in a "
                f"different loop body from the `run_fts_query` call at line "
                f"{call.lineno} it guards — it has been hoisted out, so the reads "
                "in between are unguarded while this pin reports OK")


def test_a_degraded_shipped_leg_is_a_REFUSAL_not_a_miss(tool_mod) -> None:
    """`run_fts_query` returns `[]` for a failed read as well as for a miss.

    ⛔ The direction that matters: a wedged FalkorDB (breaker open, index
    missing, query failed) collapsing the shipped baseline to ~0 publishes a
    spectacular PASS — the very trigger `#12298` step 3 pre-authorises. The
    sweep therefore reads the leg's own trace and refuses; a genuine empty
    result must still be accepted, or the measurement could never run.
    """
    for reason in ("breaker_open", "index_missing", "query_failed"):
        bad = [{"leg": "fts", "ran": False, "degraded": True,
                "reason": reason, "count": 0}]
        with pytest.raises(SystemExit) as err:
            tool_mod._refuse_degraded_legs(bad, "web_search")
        assert "REFUSED" in str(err.value) and reason in str(err.value)

    # ran, degraded=False, count 0 — the shipped leg RAN and found nothing.
    tool_mod._refuse_degraded_legs(
        [{"leg": "fts", "ran": True, "degraded": False,
          "reason": "empty_results", "count": 0}], "web_search")
    # ⛔ An ABSENT fts entry is a refusal too (`leg_trace_unavailable` is the
    # repo's own fail-closed reading of an empty trace): the guard must not
    # accept "no account" as "a clean read". A vector-only trace is that case.
    with pytest.raises(SystemExit) as err:
        tool_mod._refuse_degraded_legs([], "web_search")
    assert "NO trace entry" in str(err.value)
    with pytest.raises(SystemExit):
        tool_mod._refuse_degraded_legs(
            [{"leg": "vector", "ran": True, "degraded": True,
              "reason": "no_query_vector", "count": 0}], "web_search")


def test_brief_production_estimate_rederives_from_the_per_class_cells(
        artifact, population, brief_text) -> None:
    """§4.3's derived figures are the only published numbers §3.3 does not pin.

    The class cells they come from ARE pinned, so a re-run reds §3.3 — but §4.3
    could still go stale on its own, which is the rot class the pins exist to
    close. `+26.7` is the path class's delta; `+91.8` is the `snake_case`
    SUB-class's (1 of 73 → 68 of 73), not the whole identifier class's 74.4.
    """
    pc = {a: artifact["arms"][a]["per_class"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed")}
    path_delta = (pc["falkordb_tfidf__tokens_fixed"]["path"]["3"]
                  - pc["falkordb_tfidf"]["path"]["3"]) * 100
    assert round(path_delta, 1) == 26.7
    # ⛔ SHAPE-UNIQUE (#12349 round 24 reviewer, P2: a presence pin here was
    # measured GREEN when the prose was falsified and the truth restated in §4.3).
    _only_claim(_scoped(brief_text, "4.3"),
                f"share(path) × {path_delta:.1f}",
                "§4.3's path term",
                values=(f"{path_delta:.1f}",),
                # ⛔ `×` is the operator the claim turns on (round-24 reviewer,
                # cycle 6): `×` → `/` pinned green while it stayed literal.
                free=("\u00d7",))

    snake = [n["id"] for n in population["names"]
             if n["cls"] == "ident" and "#" not in n["name"]]
    path_cls = [n["id"] for n in population["names"] if n["cls"] == "path"]
    shipped, fixed = (artifact["arms"][a]["ranks"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed"))

    def hit(ranks: dict, ids: list[str]) -> int:
        return sum(1 for i in ids if ranks.get(i) is not None and ranks[i] <= 3)

    snake_delta = (hit(fixed, snake) - hit(shipped, snake)) / len(snake) * 100
    assert len(snake) == 73
    assert round(snake_delta, 1) == 91.8, snake_delta
    # ⛔ THE CELL, not a whole-document substring (#12349 review cycle 5,
    # finding 5). `"91.8" in brief_text` passed while the §4.3 CELL could be
    # rewritten to `191.8` — the exact hole this file's module docstring
    # condemns for §0, reproduced three hundred lines lower. The cells are read
    # from the row they belong to and compared to the recomputed value.
    path_delta = (hit(fixed, path_cls) - hit(shipped, path_cls)) / len(path_cls) * 100
    table_rows = _table(_scope_slice(brief_text, "4.3"), "class / sub-class", "weighted")
    assert len(table_rows) == 5, [r[0] for r in table_rows]
    assert round(_num(table_rows[2][2]), 1) == 91.8, (
        f"§4.3's `snake_case` Δ cell says {table_rows[2][2]!r}; the artifact "
        f"recomputes {round(snake_delta, 1)}")
    assert round(_num(table_rows[3][2]), 1) == round(path_delta, 1), (
        f"§4.3's `path` Δ cell says {table_rows[3][2]!r}; the artifact "
        f"recomputes {round(path_delta, 1)}")

    # The sentence the table's remainder rests on, arithmetic included. The
    # shares are read from the rows the brief prints them in: the definite cell,
    # the `#n` cell, and the code-token share the `snake_case` row NAMES (that
    # cell says NOT RECORDED, which is the point of the row).
    table = table_rows
    definite = _num(table[0][1])
    hashed = _num(table[1][1])
    assert (round(definite, 1), round(hashed, 1)) == (62.3, 2.2)
    token_share = 9.0
    assert round(_num(table[2][1]), 1) == round(token_share, 1), (
        f"§4.3's code-token share cell says {table[2][1]!r}; the bound below it "
        f"is derived from {token_share:.1f} % — a SUBSTRING check passes on "
        "`19.0 %`, so the cell is compared as a number")
    assert "NOT RECORDED" in table[2][1]
    total = definite + hashed + token_share
    assert round(total, 1) == 73.5
    assert round(100 - total, 1) == 26.5
    _s43 = _scoped(brief_text, "4.3")
    # ⛔ SHAPE-UNIQUE (#12349 round 24): §4.3-scoped but still a PRESENCE pair, so
    # a falsified total with the true one restated inside §4.3 stayed green. ⛔ The
    # claim carries its own words — a bare `73.5 %` shape (`\d+\.\d+ %`) matches
    # every other percentage in the section, which is a false red, not a pin.
    # ⛔ `above|below` freed plus a value-only number leaves the SHAPE too wide: a
    # true "The rows below sum to 26.5 %" (the remainder this very paragraph
    # states) false-reds it (round-24 reviewer, cycle 8). The arithmetic is the
    # identity, so it is carried in the claim.
    _only_claim(_s43,
                f"the rows above sum to {total:.1f} % "
                f"({definite} + {hashed} + {token_share:.1f})",
                "§4.3's published share total",
                values=(f"{total:.1f}", f"{definite}", f"{hashed}",
                        f"{token_share:.1f}"),
                # ⛔ The ARITHMETIC is the claim's content (round-24 reviewer,
                # cycle 9): `+` → `−` and `sum` → `average` both planted green.
                free=("above|below", "+", "sum"))
    _only_claim(_s43, f"i.e. **{100 - total:.1f} %**",
                "§4.3's published remainder",
                values=(f"{100 - total:.1f}",))
    # ⛔ The PRODUCT is its own claim (#12349 review, P2): both factors were
    # pinned and the arithmetic joining them was not — `0.09 × 91.8 ≈ +8.3 pts`
    # → `+8.4 pts` left all 118 tests GREEN. 0.09 × 91.8 = 8.262, which the brief
    # rounds to 8.3, so the pin compares the ROUNDED product and reds a digit the
    # factors alone cannot see.
    bound_pts = token_share / 100 * snake_delta
    # ⛔ The FRAME is the identity (round-24 reviewer, cycle 10): with only the
    # three numbers and both operators wildcarded the shape was `N × N ≈ +N pts`,
    # so a second TRUE arithmetic sentence in §4.3 false-redded it.
    # ⛔ The SHARE is the claim's subject and was pinned nowhere that can fail
    # (round-24 reviewer, cycle 11): the prose copy sat outside this claim and the
    # table cell was checked by SUBSTRING (`"9.0 %" in "19.0 %"` is True), so
    # `9.0 %` → `19.0 %` was green in both places. The claim now starts at the
    # share, and the cell is compared NUMERICALLY below.
    _only_claim(_s43,
                f"The {token_share:.1f} % \"contains a code token\" share "
                f"bounds **that one term**: `"
                f"{token_share / 100:.2f} \u00d7 {snake_delta:.1f} \u2248 "
                f"+{bound_pts:.1f} pts`",
                "§4.3's single-term bound",
                values=(f"{token_share:.1f}", f"{token_share / 100:.2f}",
                        f"{snake_delta:.1f}", f"{bound_pts:.1f}"),
                # ⛔ `×` and `≈` are DIRECTIONAL operators: left literal, a
                # rewrite from a product to a sum stops matching and a planted
                # true copy satisfies the pin. Wildcarding them is the declared
                # residual; the three numbers are the claim.
                free=("\u00d7", "\u2248"))

    # ⛔ The Δ COLUMN, in its own cells — a whole-document `"91.8" in brief` is a
    # substring that §3.3's sentence and `share × 91.8` both satisfy, so the two
    # figures §4.3 is actually quoted from could be falsified with the pin green
    # (measured by mutation on the previous cut: 26.7→26.6 and 91.8→81.8 both
    # passed).
    assert round(_num(table[2][2].split("pts")[0]), 1) == round(snake_delta, 1), (
        f"§4.3 `snake_case` Δ cell {table[2][2]!r} vs re-derived {snake_delta:.1f}")
    assert round(_num(table[3][2]), 1) == round(path_delta, 1), (
        f"§4.3 path Δ cell {table[3][2]!r} vs re-derived {path_delta:.1f}")
    assert _num(table[1][2]) == 0.0, (
        f"§4.3 `#n` row claims a non-zero effect: {table[1][2]!r} — the "
        "measured effect on that sub-class is zero")
    # ⛔ The second-positive-term claim had NO pin (#12349 round 24): it is the
    # sentence that stops the 9.0 % row being read as an upper bound on the
    # composite, and it is only true while the path delta is POSITIVE.
    assert path_delta > 0, (
        "§4.3 says a non-zero path share ADDS to the composite; the path delta "
        f"is now {path_delta:.1f} pts")
    _only_claim(_scoped(brief_text, "4.3"),
                "any non-zero path share adds on top of it.",
                "§4.3's second-positive-term claim",
                # ⛔ `adds` and `non-zero` are both pivots: `adds` → `costs` and
                # `non-zero` → `zero` each planted green (round-24 reviewer,
                # cycles 2 and 5).
                free=("non-zero", "adds", "any"))


def test_brief_reading_two_bullets_are_the_artifact(artifact, brief: str) -> None:
    """§0's Reading-2 bullets — the summary §0 tells a reader to quote from.

    ⛔ §0 is the section the PR body instructs a reader to consult before quoting
    any number, and its bullets DUPLICATE figures that §3.1–§3.3 publish. With
    only the tables pinned, a re-run that legitimately moved a §3.2 cell could
    leave these bullets quoting the superseded figures with every gate green —
    the condition the file claims is impossible (review cycle 5, finding 2).
    """
    # ⛔ §0-SCOPED (#12349 round 16, finding 5): a whole-document `plain` was
    # satisfied by a copy of a bullet planted in §5 while §0's own bullet was
    # falsified — measured M-D1, green. §4's Result line gets its OWN scope, since
    # it is §4 that carries it (see the last assertion).
    plain = _scoped(brief, "0").replace("**", "")
    plain4 = _scoped(brief, "4").replace("**", "")
    cmp = artifact["comparisons"]
    k3 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    k15 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"]
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    dep = artifact["verdict"]["deployable_arm"]
    recovery = artifact["verdict"]["recovery_arm_no_index_move"]
    inplace = cmp["falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"]

    # ⛔ SHAPE-UNIQUE (#12349 round 24 reviewer, P2: this was a PRESENCE pin,
    # measured GREEN when the bullet was falsified and the truth restated in §0).
    _only_claim(
        plain,
        "with the query terms held constant, BM25 buys "
        f"+{k3['delta_pts']:.2f} pts (p = {k3['mcnemar_p_two_sided']:.2f}) overall, "
        f"+{subset['delta_pts']:.2f} pts (p = {subset['mcnemar_p_two_sided']:.2f}) on "
        "the subset where the control holds, and "
        f"+{k15['delta_pts']:.2f} pts at k=15 with "
        f"{'ZERO' if k15['discordant'] == 0 else k15['discordant']} discordant pairs",
        "§0's scorer bullet",
        values=(f"{k3['delta_pts']:.2f}", f"{k3['mcnemar_p_two_sided']:.2f}",
                f"{subset['delta_pts']:.2f}",
                f"{subset['mcnemar_p_two_sided']:.2f}",
                f"{k15['delta_pts']:.2f}", str(k15["discordant"])),
        # ⛔ The formatting branch emits the WORD when the count is zero, so the
        # passed magnitude (`str(0)`) can never match a token in the claim — the
        # word IS the value there (round-24 reviewer, P2: `ZERO` → `two` with the
        # truth restated was green).
        # ⛔ `with|without` is the sentence's axis — it says what the +1.33 CONTROLS
        # FOR — and one token inverts it (round-24 reviewer, cycle 8).
        free=("with|without", "constant|variable", "holds|breaks")
        + (("ZERO",) if k15["discordant"] == 0 else ()))
    k5 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k5"]
    assert k5["only_b"] == 0 and k5["only_a"] > 0, (
        "the k=5 comparison no longer favours the shipped leg \u2014 the bullet's "
        "'the shipped leg ahead N\u20130' sentence needs re-deriving")
    # ⛔ SHAPE-UNIQUE (#12349 round 24): a presence pin let the k=5 reading be
    # falsified in place while a restatement kept it green — and this is the one
    # width where the shipped leg leads, i.e. the sentence that turns a sign
    # change into a floor.
    _only_claim(
        plain,
        f"while at k=5 it is {'\u2212' if k5['delta_pts'] < 0 else '+'}"
        f"{abs(k5['delta_pts']):.2f} pts (the shipped leg ahead "
        f"{k5['only_a']}\u20130)",
        "§0's k=5 reading",
        # ⛔ BOTH halves of the N–0 split are values (round-24 reviewer, cycle 3):
        # naming `only_a` and not `only_b` left the `–0` literal, so `ahead 2–1`
        # with the truth restated was GREEN — the same asymmetry the sign fix
        # removed. `"0"` wildcards the `–0` token only; `−0.67` is one signed
        # token whose magnitude is already named, and `k=5` stays literal.
        values=(f"{abs(k5['delta_pts']):.2f}", str(k5["only_a"]),
                str(k5["only_b"])),
        free=("ahead", "shipped|postgres"))

    # §0's Reading-1 figure — the falsifier's own numbers, quoted verbatim by
    # every downstream reader, and unpinned until round 24 (ONE edit was green).
    assert dep["mcnemar_p_two_sided"] < 1e-6, (
        "§0 quotes the deployable comparison as p < 1e-6; the artifact now "
        f"records p = {dep['mcnemar_p_two_sided']}")
    # ⛔ The THRESHOLD is the claim's content too (round-24 reviewer, cycle 3):
    # `p < 1e-6` is not a `_SIGNED_NUMBER` token, so it stayed literal and a
    # `p < 0.5` rewrite with the truth restated beside it was GREEN. It is now a
    # `free` word, and the claim carries a trailing literal (`On that`) so the
    # `\S+` wildcard cannot swallow the sentence's own period.
    _only_claim(
        plain,
        f"{dep['recall_at_3']:.4f} vs {dep['recall_at_3_base']:.4f}, "
        f"+{dep['delta_pts']:.2f} pts, p < 1e-6. On that",
        "§0's Reading-1 falsifier figure",
        values=(f"{dep['recall_at_3']:.4f}", f"{dep['recall_at_3_base']:.4f}",
                f"{dep['delta_pts']:.2f}"),
        # ⛔ `1e-6` is the threshold and `<` is its COMPARISON (round-24 reviewer,
        # cycle 5: `p > 1e-6` with the truth restated was green).
        free=("1e-6", "<"))

    shipped = artifact["arms"]["falkordb_tfidf"]["ranks"]
    tokens = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    both = [q for q in shipped if shipped[q] is not None and tokens[q] is not None]
    moved = {q: abs(shipped[q] - tokens[q]) for q in both if shipped[q] != tokens[q]}
    # ⛔ The bullet NAMES its largest mover, and the name was unpinned (#12349
    # review, P2): `m3-000014` goes 1 → 6 was free text beside a pinned
    # `largest move 5 places`, so rewriting the example to `1 → 5` left 118 tests
    # GREEN. The example must be the artifact's actual largest mover, and its two
    # ranks must be the recorded ones.
    worst = max(moved, key=lambda q: (moved[q], q))
    assert worst == "m3-000014", (
        "§0 names `m3-000014` as the largest mover; the artifact's largest mover "
        f"is now {worst!r} — re-derive the example, do not just rename it")
    example = (shipped[worst], tokens[worst])
    mean_sh = artifact["arms"]["falkordb_tfidf"]["recall_by_k"]["15"]["mean_rank_when_found"]
    mean_tk = artifact["arms"]["postgres_bm25/tokens"]["recall_by_k"]["15"]["mean_rank_when_found"]
    assert mean_sh > mean_tk, (
        "the shipped leg no longer ranks worse on mean rank — §0's 'rank differently' "
        "warning has lost its evidence")
    # ⛔ SHAPE-UNIQUE (#12349 round 24 reviewer, P2: both were PRESENCE pins,
    # each measured GREEN when falsified in place and restated in §0).
    _only_claim(
        plain,
        f"of the {len(both)} names both arms find inside k=15, {len(moved)} rank "
        f"differently (mean rank when found {mean_sh:.3f} vs {mean_tk:.3f}, "
        f"largest move {max(moved.values())} places \u2014 `{worst}` goes "
        f"{example[0]} \u2192 {example[1]})",
        "§0's window-vs-order bullet",
        values=(str(len(both)), str(len(moved)), f"{mean_sh:.3f}",
                f"{mean_tk:.3f}", str(max(moved.values())),
                str(example[0]), str(example[1])),
        # ⛔ `inside` is the claim's direction (`inside` → `outside`) and
        # `differently` is its point (`rank differently` → `identically`) —
        # both planted green when left literal (round-24 reviewer, cycles 4-5).
        # ⛔ `find|miss` was INERT on this sentence's `found` (round-24 reviewer,
        # cycle 9 — the same defect the file recorded once for `m3-000014`).
        free=("inside", "differently", "both", "largest|smallest",
              "find|found|miss|missed", "\u2192"))

    _only_claim(
        plain,
        f"the same +{recovery['delta_pts']:.2f} pts is recovered inside the "
        f"current index ({dep['recall_at_3']:.4f}, "
        f"{'zero' if inplace['only_a'] == 0 else inplace['only_a']} regressions)",
        "§0's recovery bullet",
        values=(f"{recovery['delta_pts']:.2f}", f"{dep['recall_at_3']:.4f}",
                str(inplace["only_a"])),
        # ⛔ Same shape as the scorer bullet's ZERO: when the count is 0 the claim
        # says `zero`, so the word is the value (round-24 reviewer, P2). `inside`
        # is the recovery's DIRECTION (`inside` → `outside` inverts it).
        # ⛔ A PLAIN `recovered` (`[^\s*]+`), not an alternation: an alternation
        # whose arms are all the same word emits `(?:recovered)` — a LITERAL — so
        # the declaration passes every gate while wildcarding nothing
        # (#12349 round-24 reviewer, cycle 11: my own "recovered|recovered"
        # silently disabled this pin).
        free=("recovered", "inside", "same")
        + (("zero",) if inplace["only_a"] == 0 else ()))

    # §4's verdict line restates the same two deltas; it is the sentence a reader
    # quotes as the RESULT, so it is pinned with them rather than left as prose.
    # ⛔ SHAPE-UNIQUE (#12349 round 24): presence left it falsifiable in place.
    _only_claim(
        plain4,
        "Result: FAIL — on the full arm "
        f"(+{k3['delta_pts']:.2f} pts, p = {k3['mcnemar_p_two_sided']:.3f}) and on "
        f"the tokens-controlled subset (+{subset['delta_pts']:.2f} pts, "
        f"p = {subset['mcnemar_p_two_sided']:.3f})",
        "§4's Result line",
        values=(f"{k3['delta_pts']:.2f}", f"{k3['mcnemar_p_two_sided']:.3f}",
                f"{subset['delta_pts']:.2f}",
                f"{subset['mcnemar_p_two_sided']:.3f}"),
        # ⛔ The VERDICT WORD is the claim (round-24 reviewer, P2): `Result: FAIL`
        # → `Result: PASS` with the truth restated beside it was green, i.e. the
        # document's single most quotable word was the one token the pin could not
        # distinguish.
        free=("FAIL",))


def test_the_decision_documents_status_note_is_the_artifact(artifact, search_arch: str) -> None:
    """`SEARCH-ARCHITECTURE.md` §6.2's STATUS note restates three figures.

    It is the DECISION document — the record a lane reads to learn why the text
    index did not move — and nothing opened it before this test, so a re-run could
    move every pinned figure in the brief and the changelog and leave the note
    asserting the superseded swing (review cycle 5, finding 3).
    """
    # A blockquote in the decision doc puts a `>` between the words a sentence
    # spans on two lines; drop the marker before comparing prose. §6.2-SCOPED
    # (#12349 round 19): the doc is 560 lines and the note is one section of it.
    text = _scoped(re.sub(r"(?m)^\s*>\s?", "", search_arch), "6.2")
    cmp = artifact["comparisons"]
    k3 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    k15 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"]
    dep = artifact["verdict"]["deployable_arm"]
    recovery = artifact["verdict"]["recovery_arm_no_index_move"]

    assert k3["mcnemar_p_two_sided"] > 0.05, (
        "the primary comparison is now significant — the status note's "
        "'not significant' is wrong; re-measure and re-decide")
    # ⛔ SHAPE-UNIQUE (#12349 round-24 reviewer, P2): these four were §6.2-scoped
    # PRESENCE checks, so each could be falsified in place with the true text
    # restated inside the same § — measured GREEN for the `~N point` figure, the
    # `zero` at k=15, the SIGN of the k=5 sentence and the 25-point swing. The note
    # is the decision document; a presence pin over it is the class this round
    # exists to close, and `**zero**` / `−0.67` are exactly its content.
    _only_claim(
        text, f"by ~{round(k3['delta_pts'])} point (not significant)",
        "the status note's '~N point (not significant)'",
        values=(str(round(k3["delta_pts"])),), free=("not", "significant"))
    # ⛔ `free` covers a word SUBSTITUTED; it cannot see one DELETED, and `not` is
    # the word whose deletion INVERTS this claim while leaving it grammatical
    # (round-24 reviewer, cycle 3: `(not significant)` → `(significant)` with the
    # truth restated was GREEN, and a wildcard wide enough to catch it spans
    # sentences and false-reds five other claims). So the inverted phrasing gets
    # the file's own NEGATIVE-assertion treatment — the same pattern
    # `test_the_falsifier_predicate_is_stated_the_same_way_in_every_document`
    # uses for the inverted C3 reading.
    assert not re.search(r"by ~\d+ point \(significant\)", text), (
        "the status note asserts the primary comparison is SIGNIFICANT; the "
        f"artifact records p = {k3['mcnemar_p_two_sided']} (> 0.05) and §0/§4.1 "
        "say FAIL — the inverted reading is the one this decision document must "
        "not carry")
    _only_claim(text, f"by **zero** at k={15}",
                "the status note's 'zero at k=15'", free=("zero",))
    assert k15["delta_pts"] == 0.0
    k5 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k5"]
    assert k5["only_b"] == 0 and k5["only_a"] > 0, (
        "the k=5 comparison no longer favours the shipped leg \u2014 the status "
        "note's k=5 sentence needs re-deriving")
    _only_claim(
        text,
        f"**{'\u2212' if k5['delta_pts'] < 0 else '+'}"
        f"{abs(k5['delta_pts']):.2f} pts at k=5**",
        "the status note's k=5 direction",
        values=(f"{abs(k5['delta_pts']):.2f}",))
    _only_claim(text, f"the whole {dep['delta_pts']:g}-point swing",
                "the status note's swing",
                values=(f"{dep['delta_pts']:g}",))
    # ⛔ The note's CLOSING figure pair was unpinned (#12349 review, P2): four
    # neighbouring figures were shape-pinned while `(0.9633 vs 0.7133)` — the
    # pair a reader quotes as the size of the swing — was free text, and
    # `0.9634` left all 118 tests GREEN.
    _only_claim(
        text,
        f"does beat it ({dep['recall_at_3']:.4f} vs "
        f"{dep['recall_at_3_base']:.4f})",
        "the status note's closing figure pair",
        values=(f"{dep['recall_at_3']:.4f}", f"{dep['recall_at_3_base']:.4f}"),
        # ⛔ `beat` is the verb the paragraph turns on (round-24 reviewer,
        # cycle 10): `beat` → `trail` with the truth restated was GREEN.
        free=("beat",))
    assert recovery["delta_pts"] == dep["delta_pts"], (
        "the recovery arm no longer reproduces the deployable arm's swing, so the "
        "status note's 'reproduces inside the current index' is now false")


def test_the_index_wait_requires_exactly_the_expected_count(tool_mod) -> None:
    """The wait requires EXACTLY the expected count, and the polarity is pinned.

    ⛔ `>=` is why the 305-document index in §3.5 was accepted without a word:
    `305 >= 300` returns immediately, where `==` times out and refuses loudly.
    The previous revision of this test asserted `>=` and FORBADE `==`, on the
    reading that "a partially built index would then be measured as misses" —
    that reading is wrong, and it conflated `==` with `<=`: a partial index
    (say 150 of 300) does not satisfy `==`, so the loop keeps waiting and
    eventually REFUSES; it never measures. The real hazard `<=` returns `-1` at
    once, so it stays asserted. `==` is the only operator that both waits for a
    complete build AND refuses an index that is not the one this run wrote.
    """
    src = inspect.getsource(tool_mod._wait_indexed)
    assert "last == expected" in src, (
        "the index wait must require the EXACT expected count: `>=` accepts an "
        "over-full index, which is the §3.5 reusable-graph confound")
    assert "last <= expected" not in src, (
        "the wait became an upper bound, which returns -1 at once and reopens "
        "the partially-built-index artefact the wait exists to close")
    assert "last >= expected" not in src, (
        "the wait accepts an at-least-full index again — re-read §3.5 and the "
        "artifacts' `measurement_notes` before changing this")


def test_the_index_wait_refuses_an_object_row_without_a_fulltext_index_on_name(
        tool_mod) -> None:
    """The wait must refuse a row that does not carry FULLTEXT over `name`.

    ⛔ A document COUNT is not the identity of the index (#12349 review, P2).
    Measured on docker FalkorDB **v4.20.6**: `CALL db.idx.fulltext.queryNodes`
    against a graph with NO fulltext index returns `[]` WITHOUT raising, so
    `run_fts_query` records `{'ran': True, 'degraded': False, 'reason':
    'empty_results'}` and `_refuse_degraded_legs` — which reads the TRACE —
    cannot refuse it. An `Object` index over `id` is worse than absent: it
    reaches the same document count and answers every query with zero rows, so
    the shipped baseline would publish as 0.0 with every guard green.

    ⛔ And the FIELD NAME alone is not the identity either — the case this test
    exists for, found only on review round 2. `FalkorProjection._ensure_indexes`
    ALWAYS creates `("Object", "name", "RANGE")` (the DDL loop over
    `_RANGE_INDEX_LABEL_PROPS`, not `_REQUIRED_RANGE_INDEXES` — that tuple is the
    PROBE's requirement list, and membership is not creation: `("Session", "id",
    "RANGE")` is required and created by a separate statement), so a
    field-name-only probe is satisfied on a graph with no fulltext index at all:
    the guard would have been a silent no-op on exactly the case it was added for
    — a false PASS inside a fail-closed guard. Hence the KIND is asserted, through
    the triple the repo's own `_schema_is_current` requires.

    BEHAVIOURAL pin (a fake catalogue, not a source grep), over the measured row
    shape `[label, properties, {prop: [kind, …]}, …, stats]`, plus ONE short-row
    case (`["Object", ["name"]]`) that the engine this measurement ran on does
    not emit — it is the defensive branch `index_catalogue` documents for an
    engine whose catalogue shape differs, and the docstring says so because this
    is the only record distinguishing real shapes from defensive ones.
    """
    import pytest as _pytest

    class _Res:
        def __init__(self, rows):
            self.result_set = rows

    class _Graph:
        def __init__(self, rows):
            self._rows = rows

        def query(self, _cypher):
            return _Res(self._rows)

    class _Proj:
        def __init__(self, rows):
            self.g = _Graph(rows)

    stats = {"numDocuments": 3}

    def accepts(rows) -> None:
        assert tool_mod._wait_indexed(_Proj(rows), 3, timeout_s=1.0) == (3, 0.0)

    def refuses(rows, why: str) -> None:
        with _pytest.raises(SystemExit) as exc:
            tool_mod._wait_indexed(_Proj(rows), 3, timeout_s=1.0)
        assert "no FULLTEXT index over `name`" in str(exc.value), (
            f"the index wait ACCEPTED a catalogue row that {why} — every query "
            "would return zero rows and the shipped baseline would publish as "
            "0.0 with every other guard green")

    # The shape the catalogue's own module docstring records for a field that
    # has both indexes.
    accepts([["Object", ["id", "name"],
              {"id": ["RANGE"], "name": ["RANGE", "FULLTEXT"]}, stats]])
    # ⛔ THE CASE THE FIELD-NAME PROBE MISSED: `name` IS indexed — as RANGE —
    # which is what every Tortoise graph carries, and there is no fulltext index.
    refuses([["Object", ["id", "name"],
              {"id": ["RANGE"], "name": ["RANGE"]}, stats]],
            "indexes `name` only as RANGE (the default every graph has)")
    refuses([["Object", ["id"], {"id": ["FULLTEXT"]}, stats]],
            "full-text-indexes `id` instead of `name`")
    # …and a row that cannot PROVE the kind fails CLOSED, the way
    # `_schema_is_current` fails closed on a row shorter than three cells
    # (`len(row) < 3`, `tortoise/projection/__init__.py:20465`). ⛔ `["Object",
    # ["name"]]` — NOT a two-cell row whose third cell is the stats dict: that
    # one DOES carry a dict in the kinds column, and `_schema_is_current` raises
    # TypeError on it rather than failing closed, so it is a different case and
    # not the one the prose names.
    refuses([["Object", ["name"]]],
            "has no kinds column at all (the shape `_schema_is_current` fails "
            "closed on)")


def test_the_reuse_count_guard_is_wired(tool_mod) -> None:
    """`arm_falkordb` must QUERY the graph's total `:Object` count AND refuse on it.

    ⛔ Two failures this pin exists for, and the reason it reads the AST rather
    than the source text. (1) The brief claims this guard (§2), and a brief-pin
    asserts the CLAIM, not the CODE — deleting the guard from the harness left
    every test in this file green while the brief went on claiming it. (2) The
    first version of this pin used `in src` substring checks, which a NEUTRALISED
    guard defeats: commenting the block out, or wrapping it in `if False:`, keeps
    both substrings present, so the pin reports OK over a graph that is silently
    accepted (measured — the reviewer's reproduction). The guard is therefore
    bound structurally: an `If` whose test compares `total` against `len(rows)`
    with `!=`, whose body RAISES.
    """
    import ast

    tree = ast.parse(TOOL.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert fn is not None, "arm_falkordb is the measurement's sweep"

    count_calls = [
        c for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                and "count(o)" in a.value for a in c.args)
    ]
    assert count_calls, (
        "the total-:Object count query is gone — the stale-name check cannot see "
        "a previous arm's `m3fx-*` fixture rows (`STARTS WITH 'm3-'` misses "
        "them), so a reused graph is accepted and swept as 305 against 300")

    guards = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        if not isinstance(node.test, ast.Compare):
            continue
        operands = " ".join(ast.unparse(o) for o in
                            [node.test.left, *node.test.comparators])
        ops = {type(o).__name__ for o in node.test.ops}
        if "total" not in operands or "len(rows)" not in operands:
            continue
        # ⛔ `!=` ONLY. An earlier revision also accepted `>`/`<`, and `<` is the
        # operator that ACCEPTS an over-full graph — the 305-vs-300 confound the
        # guard exists to stop — while the run merely refuses later on a 60 s
        # index-wait timeout (#12349 review cycle 10, measured: `int(total) <
        # len(rows)` left the suite green).
        if ops != {"NotEq"}:
            continue
        if [ast.unparse(o) for o in node.test.comparators] != ["len(rows)"]:
            continue
        if any(isinstance(b, ast.Raise) for b in node.body):
            guards.append(node)
    assert guards, (
        "the total count is queried but never COMPARED-and-REFUSED — the guard "
        "must be an `if <count> != len(rows): raise SystemExit(...)`; a substring "
        "match would also accept the block commented out or under `if False:`")


def _control_flow(tool_mod) -> list[str]:
    """The ordered load-bearing control flow of `arm_falkordb`, as markers."""
    import ast

    tree = ast.parse(TOOL.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert fn is not None, "arm_falkordb is the measurement's sweep"

    def mark(stmt):
        src = ast.unparse(stmt)
        if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Name):
            if stmt.iter.id == "rows":
                if "run_fts_query" in src or "_fts_cypher" in src:
                    return "sweep"
                if "_upsert_object" in src:
                    return "upsert_population"
            if stmt.iter.id == "FIXTURES":
                return ("upsert_fixtures" if "_upsert_object" in src
                        else "fixture_query")
        if (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Name)
                and stmt.test.id == "stale"):
            return ("stale_refusal"
                    if any(isinstance(b, ast.Raise) for b in stmt.body)
                    else "stale_refusal_NEUTERED")
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare):
            ops = {type(o).__name__ for o in stmt.test.ops}
            if "total" in ast.unparse(stmt.test) and ops == {"NotEq"}:
                return ("count_guard"
                        if any(isinstance(b, ast.Raise) for b in stmt.body)
                        else "count_guard_NEUTERED")
        if isinstance(stmt, ast.Assign) and "_wait_indexed" in src:
            return ("wait_population" if "FIXTURES" not in src
                    else "wait_population_BAD")
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = getattr(stmt.value.func, "id", None)
            if name == "verify_cypher_parity":
                return "verify_cypher_parity"
            if name == "_wait_indexed":
                return ("wait_fixtures" if "FIXTURES" in src
                        else "wait_fixtures_BAD")
            if name == "run_fts_query" and "warm" in src:
                return "warm"
        return None

    return [m for m in (mark(s) for s in fn.body) if m]


def test_the_harness_control_flow_is_pinned_as_a_sequence(tool_mod) -> None:
    """`arm_falkordb`'s load-bearing order, asserted as ONE sequence.

    ⛔ Why a SEQUENCE and not nine little tests. Every line below is a place
    where deleting or neutralising it publishes a wrong-but-plausible number, and
    each prior review round found one more of them: the pre-sweep wait, then
    `verify_cypher_parity`, then the stale-name refusal, then the fixture
    upsert/wait order. Pinning them individually is an unbounded job — you can
    always find another deletable line — so the SURFACE is declared here once:
    the ordered set of markers. Deleting ANY of them, reordering ANY two, or
    neutering a `raise` changes this list.

    What each marker buys, in the direction that would otherwise flatter a
    conclusion:
      * `warm` + `wait_population` — a sweep over a partially built `Object`
        index books present names as misses, understating the SHIPPED baseline;
      * `verify_cypher_parity` — the recovery arm's Cypher must reproduce
        `run_fts_query`, or the "+25.00 pts recoverable in place" headline
        measures a replica nobody checked (and its anti-vacuity refusal must
        survive);
      * `sweep` before `upsert_fixtures` — the fixture rows must not enter the
        population the sweep is scored on (the 305-vs-300 confound, §3.5);
      * `wait_fixtures` — the fixture ranks must be read on a COMPLETE index,
        or a present name is booked as a capability FAILURE — the DoD's own
        gate;
      * `stale_refusal` / `count_guard` — a reused graph must be refused, not
        measured.
    """
    expected = [
        "upsert_population",      # write the 300 names
        "stale_refusal",          # refuse a graph whose stored names differ
        "count_guard",            # refuse a graph carrying extra rows
        "warm",                   # pay the cold full-scan cost once
        "wait_population",        # the index must be COMPLETE for 300 documents
        "verify_cypher_parity",   # the recovery arm's replica must match the leg
        "sweep",                  # score the population
        "upsert_fixtures",        # only AFTER the sweep
        "wait_fixtures",          # and wait for THOSE to be indexed
        "fixture_query",          # then read the #8933 fixture ranks
    ]
    observed = _control_flow(tool_mod)
    assert observed == expected, (
        "`arm_falkordb`'s load-bearing control flow changed. Expected "
        f"{expected}, observed {observed}. Every entry is a step whose removal "
        "publishes a wrong-but-plausible number (see this test's docstring); a "
        "genuine reordering needs this list updated in the same commit, and a "
        "`_NEUTERED`/`_BAD` marker means a guard kept its shape but lost its "
        "refusal.")


def test_the_decision_predicate_applies_the_rule_not_just_its_text(tool_mod) -> None:
    """`m3_passes` must implement `DECISION_RULE` — pinning the TEXT is not it.

    ⛔ The artifact carries `DECISION_RULE` verbatim and the pin asserts that
    byte-equality, which proves the rule was not reworded. It does NOT prove the
    code APPLIES it: before `m3_passes` existed the predicate was inline, and
    flipping `>` to `>=` (PASS on a tie), reversing the inequality, or widening
    `< 0.05` to `< 0.5` all left the suite green (#12349 review cycle 11). The
    rule says a TIE fails, so that is the case this pins hardest.
    """
    passes = tool_mod.m3_passes
    assert passes({"a_recall": 0.70, "b_recall": 0.75,
                   "mcnemar_p_two_sided": 0.01}) is True, (
        "a strictly better arm at p<0.05 must PASS")
    assert passes({"a_recall": 0.70, "b_recall": 0.70,
                   "mcnemar_p_two_sided": 0.01}) is False, (
        "a TIE must FAIL — DECISION_RULE says 'including a tie'")
    assert passes({"a_recall": 0.75, "b_recall": 0.70,
                   "mcnemar_p_two_sided": 0.01}) is False, (
        "a worse arm must FAIL — the inequality is not reversible")
    assert passes({"a_recall": 0.70, "b_recall": 0.75,
                   "mcnemar_p_two_sided": 0.05}) is False, (
        "p must be strictly below 0.05 — `<=` would admit the boundary")
    assert passes({"a_recall": 0.70, "b_recall": 0.95,
                   "mcnemar_p_two_sided": 0.5}) is False, (
        "a big delta with p=0.5 must still FAIL — the gain has to be "
        "distinguishable from coin-flipping on the discordant pairs")


def test_the_tokenizer_control_refusal_is_wired(tool_mod) -> None:
    """`run` must pass the WHOLE population through `assert_terms_match`.

    ⛔ The harness calls the tokenizer control "verified, never assumed", and
    `terms_census` — which §2's census pin reads — is that call's RESULT.
    Replacing the call with a literal dict left the suite green, so the
    self-check could be dropped and the census fabricated (#12349 review cycle
    11). The gate is the call, so the call is what is pinned.
    """
    import ast

    tree = ast.parse(TOOL.read_text())
    run_fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run"), None)
    assert run_fn is not None, "the harness entry point `run` is gone"
    calls = [c for c in ast.walk(run_fn) if isinstance(c, ast.Call)
             and getattr(c.func, "id", None) == "assert_terms_match"]
    assert calls, (
        "`assert_terms_match` is not called from `run` — the controlled term "
        "set is then unverified and `terms_census` can be fabricated")
    assert any("rows" in ast.unparse(a) for c in calls for a in c.args), (
        "the tokenizer control must be run over the POPULATION, not a sample")
    assert any("terms_census" in ast.unparse(s)
               and "assert_terms_match" in ast.unparse(s)
               for s in ast.walk(run_fn)), (
        "the control's return value must BE the recorded `terms_census`")


def test_the_index_wait_REFUSES_on_timeout_and_on_overshoot(tool_mod) -> None:
    """`_wait_indexed` must REFUSE — pinning the operator is not pinning that.

    ⛔ The operator pin (`last == expected`, no `<=`/`>=`) says how the loop
    EXITS, never that exiting without a match raises. Measured at this head
    (#12349 review cycle 12): making the timeout `raise` unreachable, with the
    text left intact, kept all 68 tests green — so a partially built index, or
    one that is not the graph this run wrote, could be MEASURED and its count
    published as `index_numDocuments`, which the function's own docstring calls
    "indistinguishable from a real one in the artifact".
    """

    class _G:
        def __init__(self, counts):
            self.counts = list(counts)
            self.i = 0

        def query(self, _cypher, params=None):
            n = self.counts[min(self.i, len(self.counts) - 1)]
            self.i += 1
            # ⛔ The MEASURED row shape, not `[label, stats]` (#12349 review,
            # P2): `_wait_indexed` now also requires the row to COVER `name`,
            # so a two-cell stub is refused for the wrong reason and this test
            # would pass while the count logic it exists for went unmeasured.
            row = ["Object", ["name"], {"name": ["FULLTEXT"]},
                   {"numDocuments": n}]

            class _R:
                # ClassVar, not a plain mutable class attribute (RUF012): the
                # stub only has to expose `result_set`, and ruff's `check .`
                # gate reds on a mutable default here (agent-infra-ci / lint).
                result_set: ClassVar[list] = [row]

            return _R()

    class _Proj:
        def __init__(self, counts):
            self.g = _G(counts)

    with pytest.raises(SystemExit) as err:
        tool_mod._wait_indexed(_Proj([1, 1, 1, 1]), 3, timeout_s=0.3)
    assert "REFUSED" in str(err.value), "a PARTIAL index must be refused"

    with pytest.raises(SystemExit) as err:
        tool_mod._wait_indexed(_Proj([5]), 3, timeout_s=0.3)
    assert "REFUSED" in str(err.value), (
        "an OVER-count must be refused — it means the graph is not the one "
        "this run wrote")

    assert tool_mod._wait_indexed(_Proj([3]), 3, timeout_s=1.0)[0] == 3, (
        "an exactly-complete index must be ACCEPTED, or no honest run completes")


def test_verify_cypher_parity_refuses_drift_and_vacuity(tool_mod, monkeypatch) -> None:
    """The replica check must refuse a DRIFTED replica and an EMPTY comparison.

    ⛔ The recovery arm's "+25.00 pts recoverable in place" rests on `_fts_cypher`
    reproducing `run_fts_query`, and the harness docstring promises it "refuses
    if it ever stops reproducing". Nothing pinned that promise: neutering the
    id comparison, or the anti-vacuity `if not matched:`, each kept all 68 tests
    green (#12349 review cycle 12) — the sequence pin only sees that the CALL
    exists.
    """
    import tortoise.search_engine as se
    import tortoise.sparse as sp

    monkeypatch.setattr(sp, "build_or_query", lambda q: q)

    # (i) the replica drifted: same shape, different ids -> REFUSE
    monkeypatch.setattr(se, "run_fts_query", lambda *a, **k: [("A", 1.0)])
    monkeypatch.setattr(tool_mod, "_fts_cypher", lambda *a, **k: [("B", 1.0)])
    with pytest.raises(SystemExit) as err:
        tool_mod.verify_cypher_parity(object(), ["q"])
    assert "REFUSED" in str(err.value)

    # (ii) both sides empty: equal, but proves nothing -> REFUSE
    monkeypatch.setattr(se, "run_fts_query", lambda *a, **k: [])
    monkeypatch.setattr(tool_mod, "_fts_cypher", lambda *a, **k: [])
    with pytest.raises(SystemExit) as err:
        tool_mod.verify_cypher_parity(object(), ["q"])
    assert "REFUSED" in str(err.value)

    # (iii) a genuine match on a non-empty result -> PASS
    monkeypatch.setattr(se, "run_fts_query", lambda *a, **k: [("A", 1.0)])
    monkeypatch.setattr(tool_mod, "_fts_cypher", lambda *a, **k: [("A", 1.0)])
    tool_mod.verify_cypher_parity(object(), ["q"])


def test_the_stale_name_refusal_reads_the_graphs_actual_names(tool_mod) -> None:
    """The stale-name guard must DERIVE `stale` from the graph, not be a constant.

    ⛔ The count guard's input is pinned; this one's was not. Measured at this
    head (#12349 review cycle 12): `stale = []` — a FABRICATION, which keeps the
    `if stale:` shape so the sequence marker stays green — left all 68 tests
    passing, and a reused graph carrying a DIFFERENT population of the same size
    is then swept instead of refused. That is the `o.name`-is-ON-CREATE hazard
    (§2) and the one case the count guard cannot see.
    """
    import ast

    tree = ast.parse(TOOL.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert fn is not None
    assigns = [s for s in ast.walk(fn)
               if isinstance(s, ast.Assign)
               and any(getattr(tg, "id", None) == "stale" for tg in s.targets)]
    assert assigns, "the `stale` computation is gone"
    src = ast.unparse(assigns[0].value)
    assert isinstance(assigns[0].value, ast.ListComp) or "for r in rows" in src, (
        "`stale` must be a comprehension over `rows`")
    assert "stored.get" in src and "!=" in src and "name" in src, (
        f"`stale` does not compare the graph's stored names against the "
        f"population (got {src!r}) — a constant here silently accepts a reused "
        "graph")


def test_the_recovery_arm_is_measured_through_its_own_cypher(tool_mod) -> None:
    """The `recovery` branch must issue `_fts_cypher`, never `run_fts_query`.

    ⛔ `_fts_cypher` exists precisely because `run_fts_query` RE-TOKENISES its
    input: the harness's own first cut passed `search|web|web_search` and
    measured +0.33 pts because the function split it straight back, destroying
    the thing under test. The `sweep` marker only checks that ONE of the two
    calls appears somewhere in the loop, so `if recovery:` → `if False:` kept
    every test green while the arm silently measured the SHIPPED leg — making
    the published "recoverable in place" delta 0 (#12349 review cycle 12).
    """
    import ast

    tree = ast.parse(TOOL.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert fn is not None
    sweep = [s for s in fn.body
             if isinstance(s, ast.For) and isinstance(s.iter, ast.Name)
             and s.iter.id == "rows"
             and ("run_fts_query" in ast.unparse(s)
                  or "_fts_cypher" in ast.unparse(s))]
    assert len(sweep) == 1, "the recall sweep could not be identified"
    branches = [n for n in ast.walk(sweep[0])
                if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "recovery"]
    assert branches, (
        "the `if recovery:` branch is gone from the sweep — without it the "
        "recovery arm measures the shipped leg and reports a 0-point delta")
    # ⛔ BOTH branches: one binds the OR string (`or_string = recovery_query(q)`),
    # the other issues the query inside `for k in ks`. They are separate `if
    # recovery:` statements, so a pin reading only the first sees half the
    # contract — which is how the first version of this pin failed.
    body = " ".join(ast.unparse(s) for b in branches for s in b.body)
    assert any("recovery_query(" in ast.unparse(s)
               for b in branches for s in b.body), (
        "the recovery branch must build its OR string from `recovery_query(q)` ")
    assert "_fts_cypher" in body, (
        "no recovery branch issues `_fts_cypher` — `run_fts_query` re-tokenises "
        "the query and would silently undo the added terms (the +0.33 pts "
        "self-inflicted regression this harness already hit once)")
    assert "run_fts_query" not in body, (
        "a recovery branch calls `run_fts_query`, which re-tokenises its input "
        "and destroys the thing under test")


def test_brief_states_what_the_reuse_guards_actually_do(brief_text,
                                                      population) -> None:
    """§2's reuse claim must match EXACTLY what the guards do — and that is narrow.

    ⛔ HISTORY, because this sentence has now been wrong in BOTH directions. The
    harness once refused a reused graph only when a STORED NAME differed, and the
    index wait accepted `numDocuments >= n`, so `--graph <a previous run's graph>`
    with the SAME population was ACCEPTED and the sweep ran over that run's
    leftover fixture rows — the confound §3.5 and `measurement_notes` disclose.
    The brief said "refuses a reused graph outright", which was FALSE in the
    over-claiming direction, and was corrected DOWN. Closing the two holes then
    made that strong sentence tempting again — and it is STILL too wide: a graph
    holding exactly the same 300 names and nothing else passes both guards. This
    pin therefore requires the NARROWED claim (a different population, or that
    population plus leftover rows) AND a disclosure of the case that is still
    accepted. Over-claiming and under-claiming are the same failure.
    """
    flat = _scoped(brief_text, "2").replace("**", "")
    # ⛔ SHAPE-UNIQUE (#12349 round 24): these four phrases were §-scoped PRESENCE
    # checks, so each could be falsified in place with the true phrasing restated
    # beside it in §2 and every gate stayed green. Each carries its IDENTIFYING
    # context (round-24 reviewer: the bare `count is asserted against the
    # population` collided with any paraphrase), and `free` names the word that IS
    # the claim — the disclosure's `differs`, the guard's `asserted`, the
    # acceptance's `accepted`, the operator pair in `` `==`, not `>=` ``.
    _only_claim(flat, "refuses a graph whose population differs, or that "
                "population plus leftover rows",
                "§2's reuse-guard scope", free=("refuses", "differs", "plus"))
    _only_claim(flat, "the graph's total `:Object` count is asserted against "
                "the population",
                "§2's count guard", free=("asserted",))
    _only_claim(flat, "`==`, not `>=`", "§2's index-wait equality",
                free=("==|>=", "not"))
    _only_claim(flat, "a graph already holding exactly the same 300 names and "
                "nothing else is still accepted",
                "§2's accepted-reuse disclosure",
                values=(str(len(population["names"])),),
                free=("accepted", "nothing", "still|never", "same"))
    assert "with the SAME population is ACCEPTED" not in flat
    assert "numDocuments >= n" not in flat, (
        "§2 still says the wait accepts an at-least-full index — it requires "
        "equality now")


def test_the_falsifier_predicate_is_stated_the_same_way_in_every_document(
        artifact, brief: str, changelog: str, search_arch: str) -> None:
    """One predicate, four documents — and the artifact is what decides it.

    ⛔ This is not a style rule. `#12298`'s falsifier sentence states a CLOSURE
    condition ("BM25 does **not** beat the current scorer ⇒ the leg is closed and
    no SPLADE work happens"). A deployable arm that BEATS the scorer leaves that
    condition **unmet** — while the *pre-authorisation's* trigger ("if M3 passes,
    proceed into SPLADE") IS satisfied. Writing the two as one word ("the
    falsifier is satisfied by the deployable arm") inverts the premise the whole
    deferral rests on: it reads as "the closure was right, C3 stays closed", the
    opposite of what the measurement says. It appeared in five places at once and
    was repaired in stages; nothing pinned the predicate, so it could flip back
    silently (review cycle 6).
    """
    dep = artifact["verdict"]["deployable_arm"]
    # The FACT the wording must follow: the deployable arm beats the shipped leg,
    # so the closure condition is not triggered and the trigger is.
    assert dep["recall_at_3"] > dep["recall_at_3_base"], (
        "the deployable arm no longer beats the shipped scorer — the whole "
        "'closure not triggered' reading reverses; re-measure and re-decide")

    inverted = re.compile(r"falsifier[^.]*?is\s+satisfied\s+by", re.I)
    # The POSITIVE half is read from the section that CARRIES the ruling (#12349
    # round 19): a bare "not triggered" anywhere in a 560-line decision document
    # kept the pin green while the ruling sentence itself was inverted. The
    # NEGATIVE half stays whole-document on purpose — the inverted phrasing must
    # not appear ANYWHERE.
    where = (("research-brief.md", brief, "0"),
             ("SEARCH-ARCHITECTURE-CHANGELOG.md", changelog, "#12298"),
             ("SEARCH-ARCHITECTURE.md", search_arch, "6.2"))
    for name, doc, section in where:
        plain = " ".join(doc.replace("**", "").replace("*", "").split())
        ruled = _scoped(doc, section).replace("**", "").replace("*", "")
        assert re.search(r"not\s+met|not\s+triggered", ruled, re.I), (
            f"{name}: §{section} no longer states that the closure condition is "
            f"not met/triggered — that is the section a reader takes the C3 "
            f"ruling from")
        assert not inverted.search(plain), (
            f"{name} calls the falsifier 'satisfied by' the arm — that is the "
            f"closure condition, which the deployable arm leaves UNSATISFIED; only "
            f"the pre-authorisation's trigger is satisfied")
