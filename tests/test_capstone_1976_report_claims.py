"""#2008 review — a verification artifact's cross-head licence must be the one it can prove.

THE DEFECTS THIS PINS
---------------------
Two findings from the review of `docs/epics/2026-08-29-agent-driven-onboarding-1976/
capstone-report.md`, both of which let the report's evidence outrun its evidence:

1. **The cross-head licence did not hold.** Batches A/B were measured at the anchor
   `fbc1d96d5` and the report read their result at the verified head `180fe6fd3`. The
   licence offered was a *test-file* disjointness check: "none of those 18 files is in
   batches A/B's file lists". But A/B's lists are **test** files, so the check answers
   "did any suite I ran also change?" — never "did the production code those suites
   exercise change?". It did: `9c2137470` rewrites `tortoise/env_truthy.py` (the
   `env_flag` resolver) and `tortoise/sdk.py` (+340 lines, the valid-time rerank in the
   read path), and both sit on the anchor-head suites' import path —
   `tests/test_onboarding_seed_endpoint.py` imports `_make_sdk, app` from
   `tortoise.hosted_api`, which imports `env_flag, is_truthy` from
   `tortoise.env_truthy`, and `_make_sdk` constructs the `tortoise.sdk` whose read path
   that commit alters.

2. **The hub checkout's tree state was never recorded.** Batches A/B ran in the shared
   main ("hub") worktree, which is the tree the repo's worktree-isolation rule exists to
   keep other lanes out of — and the report itself proves the hub is not a clean
   checkout: D3 is exactly a stale *ignored* `dist/` living there. No
   `git status --porcelain` accompanied the attribution, so those 324 executions
   (batch A's 201 over 8 files + batch B's 123 over 6) could not be shown to have
   measured `fbc1d96d5` rather than `fbc1d96d5` plus another lane's leftovers.

WHAT THIS PINS
--------------
1. "Which sha" states the *test-file* limit and the production-code counter-evidence,
   and does not claim the file-list check licenses the cross-head reading.
2. Lane conditions records the un-recorded hub tree state, with the clean-worktree
   re-run named as the measurement that would settle it.
3. Indicator 2 carries the anchor-measurement limit (it is graded partial either way, so
   this is a wording pin, not a re-grade).
4. The mechanism the report states is TRUE in the tree — the import path it names really
   is the path those suites take. If that ever stops being true, the report's caveat is
   overstated and this check names it.
5. Every `test_*` **citation** the report makes resolves — not only the bare-symbol form.
   A citation is a bare symbol (`test_decide`), a module (`test_decide.py`), a node id
   (`test_x.py::Class::test_y`), a path (`tests/e2e/test_x.py:1314`) or a glob
   (`test_onboarding_state*.py`). The pre-fix defect this class already produced once: an
   earlier revision cited `test_fork_card_is_shown_once_per_org`, which exists nowhere — a
   dangling pointer in the one document whose value is that every pointer resolves. That
   name is used below as the resolver's non-vacuity case.
6. The report's own batch arithmetic holds: the size it states for batches A/B equals the
   sum of the batch table's own A and B rows. The defect this pins: the artefact called
   A/B "the same 8 files, 201 tests" while its table shows 14 files / 324 executions, so the
   remedy sentence scoped the one measurement that would remove the limit to batch A alone.
7. The surface counts follow from the report's **own per-surface table**, never from a
   restated number: the not-green set `{7, 12}` and the derived `15 green` are recomputed
   from the table's rows and then required in all four places the report states them (status
   line, "Surfaces:" paragraph, Verdict, indicator 2), and every green count **and not-green
   count** a site states is read — in digits or spelled out (`fifteen green`, `2 are not (7
   and 12)`, the Verdict's `the two that are not`) — so a site that keeps its true sentence
   and gains a stale one fails, and a count that disagrees with the list beside it fails at
   its own site. Required, not
   optional: a site whose count no reading matches is a site that is not guarded at all.
   Each leg is also recorded on its own surface row. The defect this pins: the headline
   numbers are the artefact's whole deliverable and were hand-maintained, so they drifted
   from the table three times ("three stated, five listed"; "16 green" beside a table whose
   surface 12 already carried a failed leg; the leg set said to be five while the table
   carried six).
8. The leg-gap set is derived and stated **identically** wherever it appears: the surface
   set, the leg total and the per-kind breakdown (automated attempted-and-failing / automated
   never run / walk-through / manual) are all counted off the rows and required in the status
   line, the "Surfaces:" paragraph, the leg-gap bullet (whose buckets and whose automated
   sub-breakdown must both sum to the derived numbers, and which is read only up to the
   bullet's own indented sub-items — a slice that ended at the next column-0 bullet ran 107
   lines past it, so the Totals paragraph could satisfy the buckets for a bullet that
   stated none) and the Verdict, which must name exactly `{5, 7, 8, 12, 15, 16}`. A count
   that does not reconcile with its own list is this artefact's recurring defect, and it
   reached the pull-request description too ("five", where the document says six).
9. The report's `Surfaces` column — labelled "verbatim from `04-test-design.md`" —
   is the map's cell, compared as whitespace-normalised TEXT. A number-SET
   comparison (the first version) passed `10, 9, 2, 1` against `1, 2, 9, 10`,
   which is a different cell, so the "verbatim" label stayed false while the
   guard was green. Non-vacuity is in-suite: the comparison is driven with a
   drifted, a REORDERED and a spacing-only pair.
10. This file's own contract stays one entry per check: the `WHAT THIS PINS` list numbers
    1..N once each, in order, every `# N —` section header carries an unused number, is
    ruled and separated from the code above, and claims nothing the list does not name.
    The defect this pins: integrating #9013 left a duplicated docstring fragment, **two**
    items numbered `7.`, and a `# 7 —` header under this branch's `# 7-8 —` with its
    opening rule dropped — a merged guard whose contract no longer said what it pins,
    while every assertion in it stayed green.

Claims are read as **paragraphs**, not physical lines (`_claim_block`, `_leg_gap_sentence`):
markdown wrapping is free, so reflowing a sentence must not fail a guard that is about what
the report says. Table rows are still read line-wise — a wrapped row is a broken table.

Non-vacuity: on the pre-fix document, checks 1–3 fail (it said "which is what licenses
the cross-head comparison — checked per file", carried no tree-state line, and its
indicator-2 row had no anchor caveat); check 5's extractor surfaces the dangling name from
synthetic doc text and the resolver rejects it; check 6 fails on "8 files, 201 tests";
checks 7–8 fail on it too (its per-surface table's only not-green row is 7, so the derived
set is `{7}` against a stated `16 green`; it carries no "seven legs" claim at all, and its
rows 7, 8 and 12 carry none of their leg clauses). Checks 9 and 10 are non-vacuous in-suite.
So are the four fail-opens found in the readings by mutation, and each is driven negatively
below: a leg-gap bullet whose buckets were moved into the Totals paragraph must stop reading
as stating them (pre-fix the widest slice still read them); a status line whose count is
changed to `3 are not (7 and 12)` must be read as 3 (pre-fix no reading matched the
`are not` form, so the assertion was inert); the Verdict's `the two that are not` must be
read as 2, and as 3 once it is drifted (pre-fix no reading matched the `that are not` form,
and the Verdict's count was never asserted at all); and a stale spelled-out `sixteen green`
beside the true `15 green` must be read (pre-fix only digits were).

Static: doc-text scans plus file reads. No network, DB or graph.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (
    ROOT
    / "docs"
    / "epics"
    / "2026-08-29-agent-driven-onboarding-1976"
    / "capstone-report.md"
)

ANCHOR_SHA = "fbc1d96d5"
VERIFIED_SHA = "180fe6fd3"


def _doc_text(text: str | None = None) -> str:
    return DOC.read_text(encoding="utf-8") if text is None else text


def _section(heading: str, text: str | None = None) -> str:
    """The body of an exact `## heading` section, up to the next `## `.

    The heading must match to end-of-line: `## Verdict` also prefixes
    `## Verdict summary`, and returning that table instead of the Verdict is how a
    count check silently passes over the wrong region.
    """
    text = _doc_text(text)
    needle = heading + "\n"
    assert needle in text, f"heading {heading!r} not found in {DOC.name}"
    start = text.index(needle)
    end = text.find("\n## ", start + len(needle))
    return text[start:] if end == -1 else text[start:end]


#: A bullet ends at the next bullet at ANY indent. The leg-gap bullet's own sub-items are
#: indented two spaces (`  - **hosted-e2e (automated) — 12.**`), so a column-0-only
#: terminator ran the slice 107 lines past the bullet — through the whole per-surface
#: table, the Totals paragraph and the browser walk — and every bucket assertion could be
#: met from text outside the bullet (removing the buckets from the bullet and pasting them
#: into Totals left the guard green). The sub-items are the bullet's own detail; the
#: vocabulary the leg-gap check reads is stated in its opening lines.
_BULLET_END = re.compile(r"\n[ \t]*- ")


def _bullet(marker: str, text: str | None = None) -> str:
    """The single `- **Which sha:**`-style bullet, up to the next bullet at any indent."""
    text = _doc_text(text)
    assert marker in text, f"{marker!r} not found in {DOC.name}"
    start = text.index(marker)
    end = _BULLET_END.search(text, start + len(marker))
    return text[start:] if end is None else text[start : end.start()]


def _single_line(prefix: str, text: str | None = None) -> str:
    """The single line starting with `prefix` — a duplicate prefix is a hard error, not a guess."""
    lines = [line for line in _doc_text(text).splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, (
        f"expected exactly one line starting {prefix!r}, found {len(lines)}"
    )
    return lines[0]


def _fold(text: str) -> str:
    """Markdown-equivalent text: inner newlines collapsed, so re-wrapping a paragraph is
    not a change of claim."""
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _claim_block(prefix: str, text: str | None = None) -> str:
    """The single markdown *paragraph* starting with `prefix`, folded.

    A claim is a paragraph, not a layout: this report wraps most of its prose, so reading a
    claim off one physical line would fail the guard on a faithful reflow. Table rows are
    read with `_single_line` instead — a wrapped row is a broken table, not a rewording.
    """
    blocks = [
        block
        for block in re.split(r"\n[ \t]*\n", _doc_text(text))
        if _fold(block).startswith(prefix)
    ]
    assert len(blocks) == 1, (
        f"expected exactly one paragraph starting {prefix!r}, found {len(blocks)}"
    )
    return _fold(blocks[0])


def _with_doc_edit(old: str, new: str, text: str | None = None) -> str:
    """The document with one asserted-unique substitution.

    The negative tests below drive the guard against a document that has been edited the
    way a real drift edit looks. The substitution must be unique, or the mutation would be
    testing something other than what it names.
    """
    text = _doc_text(text)
    found = text.count(old)
    assert found == 1, f"expected exactly one {old!r} in {DOC.name}, found {found}"
    return text.replace(old, new, 1)


def _leg_gap_sentence(text: str | None = None) -> str:
    """The Verdict's leg-gap sentence, read from the folded section so it is wrap-safe.

    The sentence ends at its own full stop. A missing stop is a hard error rather than a
    silent overrun into the next sentence (whose numerals would then read as surfaces);
    an inline abbreviation inside the sentence is not expected and would truncate it, so
    the assertion below names the sentence when that happens.
    """
    verdict = _fold(_section("## Verdict", text))
    match = re.search(
        r"\*\*(" + _WORD_ALTERNATION + r")\*\* surfaces have an assigned leg",
        verdict,
        re.IGNORECASE,
    )
    assert match, (
        "the Verdict no longer states its leg gap as '<count> surfaces have an assigned "
        "leg ...': " + verdict
    )
    end = verdict.find(". ", match.end())
    assert end != -1, (
        "the Verdict's leg-gap sentence has no terminating full stop, so its boundary "
        "cannot be read (an overrun would read the next sentence's numerals as surfaces): "
        + verdict[match.start() :]
    )
    return verdict[match.start() : end + 1]


# --------------------------------------------------------------------------- #
# 1 — the cross-head licence is restated as what it can prove
# --------------------------------------------------------------------------- #


def test_the_cross_head_bullet_does_not_claim_a_test_file_check_licenses_the_reading():
    bullet = _bullet("- **Which sha:**")
    assert "which is what licenses the cross-head comparison" not in bullet, (
        "the bullet still offers the test-file disjointness check as the licence: "
        "batches A/B's file lists are TEST files, so they cannot show that the "
        "production code those suites exercise is unchanged. Bullet: " + bullet
    )
    assert "**test-file** disjointness check" in bullet, (
        "the bullet must name what the check actually is — a test-file check, not a "
        "head-equivalence proof. Bullet: " + bullet
    )


def test_the_cross_head_bullet_records_that_the_production_code_did_change():
    bullet = _bullet("- **Which sha:**")
    for needle in (
        "`tortoise/env_truthy.py`",
        "`tortoise/sdk.py`",
        f"not asserted at `{VERIFIED_SHA}`",
    ):
        assert needle in bullet, (
            f"the cross-head caveat must state {needle!r}: `9c2137470` changes "
            "`tortoise/sdk.py` and `tortoise/env_truthy.py`, both on the anchor-head "
            "suites' import path, so their green is anchor-measured only. "
            "Bullet: " + bullet
        )
    assert "Re-running batches A/B" in bullet, (
        "the remedy (a clean-worktree re-run of batches A/B at the verified head) must "
        "be named, or the caveat has no exit. Bullet: " + bullet
    )


# --------------------------------------------------------------------------- #
# 2 — Lane conditions records the un-recorded hub tree state
# --------------------------------------------------------------------------- #


def test_lane_conditions_records_that_the_hub_tree_state_was_not_measured():
    lane = _section("## Lane conditions (measurement caveats)")
    assert "tree state at measurement time was not recorded" in lane, (
        "batches A/B ran in the shared hub worktree and no `git status --porcelain` "
        "was captured, so the attribution cannot show they measured the anchor commit "
        "itself. Lane conditions: " + lane
    )
    assert "`git status --porcelain`" in lane, (
        "the un-recorded artifact must be named (the clean-tree check that was not "
        "taken). Lane conditions: " + lane
    )
    assert VERIFIED_SHA in lane, (
        "the clean-worktree re-run at the verified head is the measurement that would "
        "settle it; it must be named. Lane conditions: " + lane
    )


# --------------------------------------------------------------------------- #
# 3 — indicator 2 carries the anchor limit
# --------------------------------------------------------------------------- #


def test_indicator_two_is_graded_on_the_anchor_measurement():
    row_text = _single_line("| 2 — every integration surface through its assigned layer")
    assert f"anchor `{ANCHOR_SHA}`" in row_text, (
        "indicator 2 must state that eight of its rows are anchor-measured. Row: "
        + row_text
    )
    assert f"not asserted at `{VERIFIED_SHA}`" in row_text, (
        "and that the anchor rows are not asserted at the verified head. Row: "
        + row_text
    )


# --------------------------------------------------------------------------- #
# 4 — the mechanism the report states is true in the tree
# --------------------------------------------------------------------------- #


def test_the_anchor_head_suites_really_import_the_modules_the_commit_changed():
    """If this stops holding, the report's caveat is overstated and must be re-derived.

    The claim is an import path, not a behaviour: `test_onboarding_seed_endpoint.py`
    -> `tortoise.hosted_api` -> `tortoise.env_truthy`, plus `_make_sdk` building the
    `tortoise.sdk` whose read path `9c2137470` alters.
    """
    seed = (ROOT / "tests" / "test_onboarding_seed_endpoint.py").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"^from tortoise\.hosted_api import .*_make_sdk", seed, re.MULTILINE
    ), (
        "tests/test_onboarding_seed_endpoint.py no longer imports `_make_sdk` from "
        "`tortoise.hosted_api`, so the cross-head caveat's import path no longer holds"
    )

    hosted = (ROOT / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    assert re.search(
        r"^from tortoise\.env_truthy import .*env_flag", hosted, re.MULTILINE
    ), (
        "tortoise/hosted_api.py no longer imports `env_flag` from "
        "`tortoise.env_truthy` — the module `9c2137470` rewrote"
    )
    assert "def _make_sdk(" in hosted, (
        "`_make_sdk` moved out of tortoise/hosted_api.py; the report cites it as the "
        "construction point of the SDK the suites drive"
    )

    sdk = (ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assert "class TortoiseSDK" in sdk, (
        "tortoise/sdk.py no longer defines TortoiseSDK — the read path `9c2137470` "
        "alters"
    )


# --------------------------------------------------------------------------- #
# 5 — every cited test reference resolves (the dangling-pointer class)
# --------------------------------------------------------------------------- #

#: A name an earlier revision of this report shipped as surface 4's evidence. It
#: exists nowhere in the repo — the extractor must surface it and the resolver must
#: reject it.
_DANGLING_NAME = "test_fork_card_is_shown_once_per_org"

#: ``test_*`` citations in inline code. Whitespace is excluded so fenced command lines
#: (`RUN_DASHBOARD_E2E=1 pytest tests/e2e/...`) are not read as citations.
_CITATION_RE = re.compile(r"`([^`\s]+)`")

#: A `:line`, `:1-3` or `::Class::method` suffix, stripped before resolution.
_LINE_SUFFIX_RE = re.compile(r"::?[A-Za-z0-9_][A-Za-z0-9_\-–]*$")


@lru_cache(maxsize=1)
def _test_tree_texts() -> tuple[tuple[Path, str], ...]:
    return tuple(
        (path, path.read_text(encoding="utf-8", errors="ignore"))
        for path in sorted((ROOT / "tests").rglob("*.py"))
    )


@lru_cache(maxsize=1)
def _test_defs() -> frozenset[str]:
    pattern = re.compile(r"^[ \t]*def (test_\w+)", re.MULTILINE)
    return frozenset(
        name for _path, text in _test_tree_texts() for name in pattern.findall(text)
    )


@lru_cache(maxsize=1)
def _test_classes() -> frozenset[str]:
    pattern = re.compile(r"^[ \t]*class (\w+)", re.MULTILINE)
    return frozenset(
        name for _path, text in _test_tree_texts() for name in pattern.findall(text)
    )


@lru_cache(maxsize=1)
def _test_module_stems() -> frozenset[str]:
    return frozenset(path.stem for path in (ROOT / "tests").rglob("*.py"))


def _expand_braces(pattern: str) -> list[str]:
    """`tests/e2e/{a,b}.py` -> [`tests/e2e/a.py`, `tests/e2e/b.py`]."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    expanded: list[str] = []
    for part in match.group(1).split(","):
        expanded.extend(
            _expand_braces(pattern[: match.start()] + part.strip() + pattern[match.end() :])
        )
    return expanded


def _glob_ref_resolves(ref: str) -> bool:
    for pattern in _expand_braces(ref):
        rooted = pattern if "/" in pattern else f"tests/{pattern}"
        if not any(ROOT.glob(rooted)):
            return False
    return True


def _resolves(ref: str) -> bool:
    """A citation resolves as a node id, a glob, a path, a module stem or a `def`."""
    if "::" in ref:
        head, *parts = ref.split("::")
        if not _resolves(head):
            return False
        known = _test_defs() | _test_classes() | _test_module_stems()
        return all(part in known for part in parts)
    if any(ch in ref for ch in "*?[{"):
        return _glob_ref_resolves(ref)
    if "/" in ref:
        return any(ROOT.glob(ref))
    if ref.endswith(".py"):
        return ref[:-3] in _test_module_stems()
    return ref in _test_module_stems() or ref in _test_defs()


def _normalise(token: str) -> str | None:
    """A cited `test_*` reference in the form the resolver takes, or None."""
    if "test_" not in token:
        return None
    head, sep, tail = token.partition("::")
    head = _LINE_SUFFIX_RE.sub("", head).strip()
    if not head or "test_" not in head:
        return None
    if any(ch in head for ch in "<>$()"):
        return None
    return head + sep + tail


def _cited_test_refs(text: str | None = None) -> list[str]:
    """Every ``test_*`` citation in the document — bare, module, node id, path or glob.

    The pre-fix version of this extractor required the closing backtick immediately
    after the identifier, so it saw 15 of the report's 62 ``test_*`` tokens and was
    blind to the module/path form the report mostly uses.
    """
    raw = _doc_text() if text is None else text
    refs = {_normalise(token) for token in _CITATION_RE.findall(raw)}
    return sorted(ref for ref in refs if ref)


def test_every_test_citation_the_report_makes_resolves():
    dangling = [ref for ref in _cited_test_refs() if not _resolves(ref)]
    assert not dangling, (
        "the report cites test references that do not resolve — neither as a module "
        f"under `tests/`, a `def`, a class, a path nor a glob: {dangling}. A reader "
        "cannot detect a dangling pointer by reading, which is why this is a guard."
    )


def test_the_extractor_sees_the_module_and_path_citations_not_only_bare_symbols():
    """The gap the pre-fix extractor had: it only matched `` `test_foo` ``.

    Non-vacuity for the extractor itself — a regex regression that stops matching the
    `.py` / path / node-id forms must fail here, not pass silently.
    """
    refs = set(_cited_test_refs())
    for expected in (
        # a `:line` suffix is stripped, the module citation survives
        "test_dashboard_onboarding.py",
        # a path citation with a line number
        "tests/e2e/hosted/test_14_onboarding_journey.py",
        # a glob citation
        "test_onboarding_state*.py",
        # a node-id citation (`module.py::Class::test`)
        "test_onboarding_w6_capture_disclosure.py::TestDeleteDuringCapture::"
        "test_no_orphaned_receipt_under_concurrent_delete",
        # a brace-set citation
        "tests/e2e/{test_welcome_page,test_ship_test_onboarding,test_dashboard_gate,"
        "test_dashboard_identity,test_session_login_flow}.py",
    ):
        assert expected in refs, (
            f"the extractor no longer surfaces the citation {expected!r}; it is in the "
            f"report. Refs found: {sorted(refs)}"
        )
    assert _normalise("test_dashboard_onboarding.py:1314") == (
        "test_dashboard_onboarding.py"
    ), "a `:line` suffix must be stripped, not skipped, or the module goes unchecked"
    assert _normalise("tests/test_onboarding_seed_endpoint.py:34") == (
        "tests/test_onboarding_seed_endpoint.py"
    )
    module_refs = [
        ref
        for ref in refs
        if ref.endswith(".py") or "/" in ref or "::" in ref or "*" in ref
    ]
    assert len(module_refs) >= 20, (
        "the report cites dozens of test modules/paths; the extractor surfaced only "
        f"{len(module_refs)}: {sorted(module_refs)}"
    )


def test_the_extractor_surfaces_the_dangling_name_from_a_synthetic_document():
    """Non-vacuity: check 5 must be able to FAIL on a real dangling citation.

    This exercises the *extractor*, not just the resolver — the pre-fix guard called
    `_resolves()` directly, so it proved the resolver rejects a name the extractor
    would have had to hand it, never that the extractor sees the citation at all.
    """
    synthetic = (
        "surface 4 (fork card) — asserted by `test_fork_card_is_shown_once_per_org` "
        "and its companions"
    )
    assert _DANGLING_NAME in _cited_test_refs(synthetic), (
        "the extractor did not surface the dangling citation from doc text, so the "
        "resolution guard cannot see this class of defect at all"
    )
    assert not _resolves(_DANGLING_NAME), (
        f"{_DANGLING_NAME!r} now exists. It was the dangling citation this guard was "
        "written for; if it has been added, pick another known-absent name so the "
        "extractor keeps a non-vacuity case."
    )


def test_a_missing_module_citation_is_rejected():
    """The class the pre-fix extractor could not see: a ``.py`` citation that is absent."""
    absent = "test_module_that_does_not_exist_8997.py"
    assert _normalise(absent) == absent
    assert absent in _cited_test_refs(f"see `{absent}` for the evidence")
    assert not _resolves(absent)
    assert not _resolves(f"tests/e2e/{absent}:1314")


# --------------------------------------------------------------------------- #
# 6 — the report's own batch arithmetic holds
# --------------------------------------------------------------------------- #


def _batch_rows() -> dict[str, tuple[int, int]]:
    """passed/failed per batch, parsed from the report's own ```text batch block."""
    text = _doc_text()
    block = text[text.index("```text") : text.index("```", text.index("```text") + 7)]
    rows = re.findall(r"^([A-F])\s+(\d+) passed, (\d+) failed", block, re.MULTILINE)
    assert rows, f"the batch table no longer parses: {block!r}"
    return {letter: (int(passed), int(failed)) for letter, passed, failed in rows}


def test_the_cross_head_bullet_states_the_size_of_batches_a_and_b():
    """The remedy sentence must scope the re-run to both batches, at the true size.

    Pre-fix the artefact said "the same 8 files, 201 tests" — batch A's size — while
    batches A and B together are 14 files and 324 executions (its own table's rows).
    Following that sentence literally would have left batch B's 123 anchor-measured
    executions un-asserted at the verified head while reading as if the limit were gone.
    """
    a_passed, a_failed = _batch_rows()["A"]
    b_passed, b_failed = _batch_rows()["B"]
    executions = a_passed + a_failed + b_passed + b_failed
    bullet = _bullet("- **Which sha:**")
    assert executions == 324, (
        "the batch table no longer sums to 324 for A+B (A "
        f"{a_passed}+{a_failed}, B {b_passed}+{b_failed}) — re-derive the report's "
        "figures before trusting this guard"
    )
    assert "**14 files, 324 executions**" in bullet, (
        "the cross-head remedy must state batches A/B at the size the report's own "
        f"table shows ({executions} executions; batch A {a_passed + a_failed} over 8 "
        "files, batch B "
        f"{b_passed + b_failed} over 6). Bullet: " + bullet
    )
    assert "the same 8 files, 201 tests" not in bullet, (
        "the bullet still sizes batches A/B as batch A alone, which scopes the one "
        "measurement that would remove the anchor limit to the wrong set. Bullet: "
        + bullet
    )
    lane = _section("## Lane conditions (measurement caveats)")
    assert f"**{executions}** executions" in lane, (
        "Lane conditions must carry the same A/B execution count as the batch table "
        f"({executions}). Lane conditions: " + lane
    )


def test_the_resolver_rejects_the_dangling_name_an_earlier_revision_cited():
    """Non-vacuity: check 5 must be able to FAIL on a real dangling citation."""
    assert not _resolves(_DANGLING_NAME), (
        f"{_DANGLING_NAME!r} now exists. It was the dangling citation this guard was "
        "written for; if it has been added, pick another known-absent name so the "
        "resolver keeps a non-vacuity case."
    )


# --------------------------------------------------------------------------- #
# 7-8 — the headline counts follow from the per-surface table, and the leg-gap
#       set is stated identically everywhere it appears
# --------------------------------------------------------------------------- #

# The classifiers below decide the report's headline numbers, so all of them read the rows'
# **Result** cells and none of them restates a number from the prose. Their vocabulary is
# listed here because a re-worded cell is a re-derivation event, not a silent
# re-classification:
#
#   not green            — ⚠️ in the Result cell AND "N passed, M failed" (M > 0) or
#                          "leg exists and FAILS"
#   a leg never run      — ⚠️ + "leg never run"
#   a walk-through gap   — ⚠️ + "walk-through not executed"
#   a manual gap         — ⚠️ + "leg not executed" / "leg **partly** covered"
#
# Every leg classifier is ⚠️-gated, because a green row may legitimately *quote* a failure
# count in prose ("N passed, M failed") without that leg being its own — surface 1's cell
# names the walk-through of surface 8, and D4's ratios are quoted all over the artefact. A
# row that failed and then passed on a re-run is written as prose ("failed at `x` and
# passed on re-run at `y`"), so it matches none of these — surface 16 is green. `0 failed`
# matches nothing either.
#
# Failing closed: `_assert_warnings_and_failures_are_classified` requires every ⚠️ row and
# every hard-failure marker (❌ ✗ FAIL ERROR) to be one of these classifiers or one of the
# two warned-but-green phrasings, so a row whose result really changed cannot slip past the
# pins as an unknown spelling.
_FAILED_RUN_RESULT = re.compile(r"\b\d+ passed, [1-9]\d* failed\b")
_FAILED_LEG_CLAUSE = re.compile(r"\bleg exists and FAILS\b")
_NOT_GREEN_RESULT = re.compile(
    _FAILED_RUN_RESULT.pattern + "|" + _FAILED_LEG_CLAUSE.pattern
)
_NEVER_RUN_LEG = re.compile(r"\bleg never run\b")
_WALK_THROUGH_GAP = re.compile(r"\bwalk-through not executed\b")
_MANUAL_GAP = re.compile(r"\bleg not executed\b|\bleg \*\*partly\*\* covered\b")
_HARD_FAILURE = re.compile(r"❌|✗|\bFAIL|\bERROR")

# ⚠️ rows that are nevertheless green, in the report's own two phrasings: a flake that
# passes alone (D2, surface 11) and a failure proven an environment artifact on re-run
# (D3, surface 16). Anything else carrying ⚠️ must be a leg classifier's business.
_WARNED_BUT_GREEN = (
    re.compile(r"\bflake\b[^.]*\bpasses alone\b"),
    re.compile(r"\bfailed at\b[^.]*\bpassed on re-run\b"),
)

# The leg KINDS a row can record, in the order the report's bullet states them, each with
# the classifier that derives its count.
_LEG_KIND_PATTERNS = {
    "automated_failed": _NOT_GREEN_RESULT,
    "automated_never_run": _NEVER_RUN_LEG,
    "walk_through": _WALK_THROUGH_GAP,
    "manual": _MANUAL_GAP,
}

# The surfaces the map assigns a leg this run did not fully pass or execute — a golden set
# asserted against the rows, so either side moving is loud.
_LEG_GAP_SURFACES = {5, 7, 8, 12, 15, 16}

_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
_NUMBER_NAMES = {value: word for word, value in _NUMBER_WORDS.items()}
_WORD_ALTERNATION = "|".join(_NUMBER_WORDS)
_NUMERAL_ALTERNATION = "|".join(_NUMBER_WORDS) + r"|\d+"

# Every way this report states a green count — digits and spelled-out numbers, in the
# orders it uses them. Reading *all* of them, rather than banning one stale spelling, is
# what catches a site that keeps its true sentence and gains a contradictory one.
_GREEN_READINGS = (
    re.compile(
        r"(?<![\w.])(\d+)\s+of the 17 (?:surfaces are|are)\s+green", re.IGNORECASE
    ),
    re.compile(r"(?<![\w.])(\d+)\s+are\s+green\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])(\d+)\s+green\b", re.IGNORECASE),
    # Spelled-out `N green` — the report spells its other headline counts (`**six**
    # surfaces`, `**seven legs**`), so a spelled-out green count is a plausible edit, and
    # reading it is what the comment above already claimed.
    re.compile(r"(?<![\w.])(" + _WORD_ALTERNATION + r")\s+green\b", re.IGNORECASE),
    re.compile(
        r"(?<![\w.])(" + _WORD_ALTERNATION + r")\s+of the 17 (?:surfaces are|are)\s+green",
        re.IGNORECASE,
    ),
    re.compile(r"(?<!of the )(?<![\w.])(\d+)\s+surfaces are green\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])(\d+)\s+of 17 green\b", re.IGNORECASE),
    re.compile(r"green count is (\d+)", re.IGNORECASE),
    re.compile(r"(?<![\w.])(\d+)\s*/\s*17\s*(?:surfaces\s*)?green", re.IGNORECASE),
)

# The forms this report states a leg TOTAL in (`**seven legs**`, `(seven legs`, `all
# seven legs`); reading all of them means a stale total in any of the three is caught.
_LEG_TOTAL_PATTERNS = (
    re.compile(r"\*\*(" + _NUMERAL_ALTERNATION + r") legs\*\*", re.IGNORECASE),
    re.compile(r"\((" + _NUMERAL_ALTERNATION + r") legs", re.IGNORECASE),
    re.compile(
        r"\b(?:(?:all|the)\s+)?(" + _NUMERAL_ALTERNATION + r") legs\b", re.IGNORECASE
    ),
)


def _numeral(token: str) -> int:
    """`"3"` -> 3, `"seven"` -> 7."""
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def _surface_rows(text: str | None = None) -> dict[int, str]:
    """surface number -> its full row, from the report's own `## Per-surface evidence` table."""
    section = _section("## Per-surface evidence", text)
    matched = [
        (int(match.group(1)), line)
        for line in section.splitlines()
        if (match := re.match(r"\|\s*(\d+)\s+[^|]*\|", line))
    ]
    # Physical row count AND key set: `len(dict)` collapses a duplicated surface row, and
    # the key set alone would not notice a duplicate of a row that already exists.
    assert len(matched) == 17, (
        f"the per-surface table has {len(matched)} surface rows, not 17 "
        f"({[number for number, _ in matched]}) — re-derive the report's counts before "
        "trusting this guard"
    )
    rows = dict(matched)
    assert sorted(rows) == list(range(1, 18)), (
        f"the per-surface table no longer has exactly surfaces 1–17 ({sorted(rows)})"
    )
    return rows


def _result_cell(row: str) -> str:
    """The row's Result cell — the Assigned-layer column also says "manual", so it cannot be
    the thing that satisfies a claim about the manual leg."""
    return row.strip().strip("|").split("|")[-1].strip()


def _assert_warnings_and_failures_are_classified(rows: dict[int, str]) -> None:
    """Every ⚠️ row and every hard-failure marker must be one the classifiers understand.

    Without this, a row that really did change from green to failed — or into a ⚠️ state
    phrased in a way no classifier matches — would match no pattern and every derived count
    would simply not move: the table would contradict the prose in silence, which is the
    defect this whole guard exists to prevent.
    """
    understood = (*_LEG_KIND_PATTERNS.values(), *_WARNED_BUT_GREEN)

    def unclassified(rows_to_check):
        return [
            (surface, cell)
            for surface, row in rows_to_check.items()
            if (cell := _result_cell(row))
            and ("⚠️" in cell or _HARD_FAILURE.search(cell))
            and not any(pattern.search(cell) for pattern in understood)
        ]

    unknown = unclassified(rows)
    assert not unknown, (
        "these rows are marked ⚠️ or carry a failure marker in a phrasing no classifier "
        "understands, so the derived counts cannot see them — extend the vocabulary above "
        "with the new phrasing: " + repr(unknown)
    )


def _surfaces_where(pattern: re.Pattern[str], text: str | None = None) -> set[int]:
    """⚠️ rows whose Result cell matches `pattern` — every leg classifier is gated this way."""
    return {
        surface
        for surface, row in _surface_rows(text).items()
        if "⚠️" in _result_cell(row) and pattern.search(_result_cell(row))
    }


def _not_green_surfaces(text: str | None = None) -> set[int]:
    """Derived from the table alone: ⚠️ rows recording an automated leg that did not pass."""
    _assert_warnings_and_failures_are_classified(_surface_rows(text))
    return _surfaces_where(_NOT_GREEN_RESULT, text)


def _legs_per_surface(text: str | None = None) -> dict[int, dict[str, int]]:
    """Leg counts per surface and kind, derived from the rows.

    A surface can carry more than one leg (surface 7's integration leg fails under load and
    its hosted-e2e leg never ran), which is why the leg total exceeds the surface count.
    """
    per_surface = {}
    for surface, row in _surface_rows(text).items():
        cell = _result_cell(row)
        counts = {
            kind: len(pattern.findall(cell)) if "⚠️" in cell else 0
            for kind, pattern in _LEG_KIND_PATTERNS.items()
        }
        per_surface[surface] = counts
    return per_surface


def _leg_kinds(text: str | None = None) -> dict[str, int]:
    """Leg totals per kind, summed from `_legs_per_surface`."""
    totals = dict.fromkeys(_LEG_KIND_PATTERNS, 0)
    for counts in _legs_per_surface(text).values():
        for kind, count in counts.items():
            totals[kind] += count
    return totals


def _leg_gap_surfaces(text: str | None = None) -> set[int]:
    """Derived from the table alone: rows recording an assigned leg not fully passed/executed."""
    return {
        surface
        for surface, counts in _legs_per_surface(text).items()
        if sum(counts.values())
    }


def _enumerated(numbers: list[int]) -> str:
    """`[5, 15, 16]` -> `"5, 15 and 16"`, the form the report enumerates surfaces in."""
    ordered = sorted(numbers)
    assert len(ordered) >= 2, f"not an enumeration: {ordered}"
    return ", ".join(str(n) for n in ordered[:-1]) + f" and {ordered[-1]}"


def _stated_leg_totals(site: str) -> set[int]:
    """Every leg total a site states (`**seven legs**`, `(seven legs`, `**7 legs**`)."""
    found: set[int] = set()
    for pattern in _LEG_TOTAL_PATTERNS:
        found |= {_numeral(token) for token in pattern.findall(site)}
    return found


#: Every way this report states a NOT-GREEN count. The status line phrases it "2 are not
#: (7 and 12)", not "2 not green": without the `N are not` form no reading matched that
#: site, so the count assertion written for it could never fail (a `3 are not (7 and 12)`
#: — a count disagreeing with the list beside it, this artefact's recurring defect — left
#: the suite green). An optional `surfaces` keeps a faithful reword readable. The Verdict
#: phrases it "the two that are not are **surface 7** ... and **surface 12**", where the
#: word *that* sits between the count and `are not`, so a `the N that are not` form is
#: needed as well — without it no reading matched the Verdict either, and a `the three that
#: are not` beside a two-surface list left the suite green.
_NOT_GREEN_READINGS = (
    re.compile(r"(?<![\w.])(\d+)\s+not green\b", re.IGNORECASE),
    re.compile(
        r"(?<![\w.])(" + _WORD_ALTERNATION + r")\s+not green\b", re.IGNORECASE
    ),
    re.compile(
        r"(?<![\w.])(" + _NUMERAL_ALTERNATION + r")\s+(?:surfaces?\s+)?are\s+not\b",
        re.IGNORECASE,
    ),
    # "the two that are not" — the Verdict's phrasing: the count, then `that are not`.
    re.compile(
        r"(?<![\w.])(" + _NUMERAL_ALTERNATION + r")\s+that\s+are\s+not\b",
        re.IGNORECASE,
    ),
)


def _not_green_readings(site: str) -> set[int]:
    """Every not-green count a site states — a count that disagrees with its list fails."""
    found: set[int] = set()
    for pattern in _NOT_GREEN_READINGS:
        found |= {_numeral(token) for token in pattern.findall(site)}
    return found


def _green_readings(site: str) -> set[int]:
    """Every green count a site states — a site that says two different things says both."""
    found: set[int] = set()
    for pattern in _GREEN_READINGS:
        found |= {_numeral(token) for token in pattern.findall(site)}
    return found


def _surfaces_named_in(site: str, phrase: str) -> set[int]:
    """The surface numbers a site names in the clause that starts with `phrase`.

    Set-based, so an enumeration reordered or spelled differently still reads the same; the
    clause stops before the next claim ("N with/carry an assigned leg"), which names the
    leg-gap surfaces rather than the not-green ones.
    """
    match = re.search(phrase + r"[^.]*", site)
    assert match, f"{phrase!r} not found in this site: {site}"
    clause = re.split(r"\d+\s+(?:with|carry) an assigned leg", match.group(0))[0]
    return {int(n) for n in re.findall(r"(?<![A-Za-z0-9])(\d+)(?![A-Za-z0-9])", clause)}


def _enumeration_pattern(numbers: list[int]) -> str:
    """A regex for an enumeration of these numbers, accepting `,` / `and` / `, and`."""
    separator = r"(?:\s*(?:,|and)\s*)*"
    return separator.join(str(n) for n in sorted(numbers))


def _manual_surfaces_in(sentence: str) -> set[int]:
    """The surfaces the sentence's `legs of ...` bucket enumerates.

    Read as an enumeration, not as "any numeral after `legs of`": parenthetical annotations
    are discarded (they can carry ratios like "1 of 4 variants"), the numeral run is read,
    and the first non-enumerating word ends it — so a trailing clause cannot contribute a
    bogus surface.
    """
    start = sentence.index("legs of ") + len("legs of ")
    tail = re.sub(r"\([^)]*\)", ", ", sentence[start:])
    surfaces: set[int] = set()
    for token in re.finditer(r"(\d+)|and\b|,|[A-Za-z]+|\S", tail):
        if token.group(1):
            surfaces.add(int(token.group(1)))
        elif token.group(0) in ("and", ","):
            continue
        else:
            break
    return surfaces


def test_the_not_green_set_and_the_green_count_follow_from_the_per_surface_table():
    """The stated `15 green` / `2 not green (7, 12)` must be what the table's own rows say.

    Pre-fix the table's only not-green row was 7 (surface 12's cell read "123-test batch
    green"), so the derived set was `{7}` and the derived green count 16 — exactly what the
    prose stated. Reading the counts off the rows is what stops the statement drifting
    silently: change a row and this fails.
    """
    not_green = _not_green_surfaces()
    assert not_green == {7, 12}, (
        "the per-surface table no longer shows exactly surfaces 7 and 12 as not green "
        f"(derived: {sorted(not_green)}). If a cell was re-worded, check it against the "
        "classifier's documented vocabulary above; if a result really changed, the "
        "report's counts must change with it."
    )
    green = len(_surface_rows()) - len(not_green)

    sites = {
        "status line": _claim_block("**Status:"),
        "\"Surfaces:\" paragraph": _claim_block("**Surfaces:"),
        "Verdict": _fold(_section("## Verdict")),
        "indicator 2": _single_line("| 2 — every integration surface through its assigned layer"),
    }
    for name, site in sites.items():
        assert _green_readings(site) == {green}, (
            f"the {name} must state exactly the green count the table derives ({green}); "
            f"it reads {sorted(_green_readings(site)) or 'none'}. Site: {site}"
        )
    # The Verdict is here because it states the count too ("the two that are not"); a
    # site left out of this loop is a site whose count is not guarded at all.
    for name in ("status line", "\"Surfaces:\" paragraph", "Verdict"):
        # Required, not optional: a site that states no readable count is not guarded at
        # all, and the status line is the site where the drift originally occurred.
        assert _not_green_readings(sites[name]) == {len(not_green)}, (
            f"the {name} must state the not-green count the table derives "
            f"({len(not_green)}); it reads "
            f"{sorted(_not_green_readings(sites[name])) or 'none'}, which no reading can "
            "reconcile with the table. State it as `N not green` or `N are not (...)`. "
            "Site: " + sites[name]
        )

    # The Deviations paragraph that restates the green count must carry the derived one too.
    overlap = [
        _fold(block)
        for block in re.split(r"\n[ \t]*\n", _doc_text())
        if "green count is" in _fold(block)
    ]
    assert len(overlap) == 1, (
        f"expected exactly one paragraph restating the green count, found {len(overlap)}"
    )
    assert _green_readings(overlap[0]) == {green}, (
        "the paragraph that restates the green count must state the derived count "
        f"({green}); it reads {sorted(_green_readings(overlap[0])) or 'none'}. Paragraph: "
        + overlap[0]
    )

    # The not-green surfaces are pinned wherever the report names them — not only count.
    assert _surfaces_named_in(sites["status line"], r"are not") == not_green, (
        "the status line must name exactly the not-green surfaces the table derives "
        f"({sorted(not_green)}): " + sites["status line"]
    )
    assert _surfaces_named_in(sites["\"Surfaces:\" paragraph"], r"not green") == not_green, (
        "the \"Surfaces:\" paragraph must name exactly the not-green surfaces the table "
        f"derives ({sorted(not_green)}): " + sites["\"Surfaces:\" paragraph"]
    )
    for surface in sorted(not_green):
        assert f"**surface {surface}**" in sites["Verdict"], (
            f"the Verdict must name surface {surface} as not green, by name: "
            + sites["Verdict"]
        )


def test_the_count_readings_read_every_spelling_the_guard_claims():
    """A count is only guarded if the guard can READ it — driven with both fail-opens.

    Two sites where the check above could not fail, both found by mutation against the head
    document. (a) The status line states its not-green count as "2 are not (7 and 12)", a
    form no reading matched, so the loop over `_not_green_readings` could never fail there
    — although its message claimed to police exactly that site: `3 are not (7 and 12)`, a
    count that does not reconcile with the list beside it, left the suite green. (b) `N
    green` was digits-only, so a spelled-out stale count (`sixteen green`, beside the true
    `15 green`) was invisible in a document that spells its other headline counts out.
    """
    not_green = _not_green_surfaces()
    green = len(_surface_rows()) - len(not_green)

    # (a) the status line's COUNT is read, not only the surfaces listed after it
    status = _claim_block("**Status:")
    assert _not_green_readings(status) == {len(not_green)}, (
        "the status line's not-green count must be readable by `_NOT_GREEN_READINGS` "
        f"(the table derives {len(not_green)}); it reads "
        f"{sorted(_not_green_readings(status)) or 'none'}, so the count assertion is inert "
        "at this site. Site: " + status
    )
    drifted = _with_doc_edit("2 are not (7 and 12)", "3 are not (7 and 12)")
    assert _not_green_readings(_claim_block("**Status:", drifted)) == {3}, (
        "a status line whose count (3) disagrees with its own list (7 and 12) must be "
        "read as 3 — that is the \"three stated, five listed\" defect this guard exists "
        "for, at the site where it originally occurred"
    )
    assert _surfaces_named_in(
        _claim_block("**Status:", drifted), r"are not"
    ) == not_green, "the mutation must move only the count, not the list"

    # (a2) the Verdict states its count as "the two that are not", which no reading
    # matched before the `the N that are not` form was added — so the assertion written
    # for the Verdict was inert there, exactly as the status line's was.
    verdict = _fold(_section("## Verdict"))
    assert _not_green_readings(verdict) == {len(not_green)}, (
        "the Verdict's not-green count must be readable by `_NOT_GREEN_READINGS` (the "
        f"table derives {len(not_green)}); it reads "
        f"{sorted(_not_green_readings(verdict)) or 'none'}, so a count that disagrees "
        "with the surfaces named beside it would pass unread. Site: " + verdict
    )
    verdict_drifted = _fold(
        _section(
            "## Verdict",
            _with_doc_edit("the two that are not", "the three that are not"),
        )
    )
    assert _not_green_readings(verdict_drifted) == {3}, (
        "a Verdict whose count (3) disagrees with the two surfaces it names must be read "
        "as 3, so the site assertion fails; pre-fix the `that are not` form matched no "
        "reading and the drift passed"
    )
    for surface in sorted(not_green):
        assert f"**surface {surface}**" in verdict_drifted, (
            "the mutation must move only the count, not the surfaces named: "
            + verdict_drifted
        )

    # (b) spelled-out green counts are read in the same form the report spells its others
    summary = _claim_block("**Surfaces:")
    assert _green_readings(summary) == {green}, (
        f"the \"Surfaces:\" paragraph must read as {green} green: " + summary
    )
    spelled_true = _claim_block(
        "**Surfaces:", _with_doc_edit("— 15 green;", "— 15 green (fifteen green);")
    )
    assert _green_readings(spelled_true) == {green}, (
        "a faithful spelled-out restatement of the same count must not read as a "
        "contradiction: " + spelled_true
    )
    spelled_stale = _claim_block(
        "**Surfaces:", _with_doc_edit("— 15 green;", "— 15 green (sixteen green);")
    )
    assert _green_readings(spelled_stale) == {green, green + 1}, (
        "a stale spelled-out green count beside the true one must be read, so the site "
        "assertion fails; pre-fix it was invisible and the drift passed. Site: "
        + spelled_stale
    )


def test_the_leg_gap_set_is_six_surfaces_and_seven_legs_wherever_it_is_stated():
    """Six surfaces / seven legs, stated identically in the status, summary, bullet and Verdict.

    Every number here is derived from the table — the surface set, the leg total and the
    per-kind breakdown — so a row that grows a leg breaks the prose pins instead of passing
    them. Two failures this pins, both shipped: a set stated as one number and listed as
    another ("three stated, five listed"), and a leg set the document states as six while
    the change description says five.
    """
    per_surface = _legs_per_surface()
    derived = _leg_gap_surfaces()
    assert derived == _LEG_GAP_SURFACES, (
        "the per-surface table's leg gaps no longer match the set the prose states: "
        f"derived {sorted(derived)}, expected {sorted(_LEG_GAP_SURFACES)}. If a cell was "
        "re-worded, check the classifier's documented vocabulary above; if a result "
        "really changed, the counts must change with it."
    )
    kinds = _leg_kinds()
    automated = kinds["automated_failed"] + kinds["automated_never_run"]
    legs = sum(kinds.values())
    assert legs > len(_LEG_GAP_SURFACES), (
        "a leg total equal to the surface count would mean no surface carries two legs, "
        f"contradicting surface 7's two: legs={legs}, surfaces={len(_LEG_GAP_SURFACES)}"
    )
    multi = sorted(surface for surface, counts in per_surface.items() if sum(counts.values()) > 1)
    assert multi == [7], (
        "exactly one surface carries two legs (7's integration failure plus its un-run "
        f"hosted-e2e leg); the rows show {multi}. Kinds: {kinds}"
    )
    surfaces_stated = str(len(_LEG_GAP_SURFACES))

    status = _claim_block("**Status:")
    assert f"{surfaces_stated} carry an assigned leg not fully passed or executed" in status, (
        "the status line must state every surface with a leg gap: " + status
    )
    assert _stated_leg_totals(status) == {legs}, (
        f"the status line must state {legs} legs, once: {status}"
    )

    summary = _claim_block("**Surfaces:")
    assert f"{surfaces_stated} with an assigned leg not fully passed or executed" in summary, (
        "the \"Surfaces:\" paragraph must state every surface with a leg gap: " + summary
    )
    assert _stated_leg_totals(summary) == {legs}, (
        f"the \"Surfaces:\" paragraph must state {legs} legs, once: " + summary
    )

    layer_bullet = _bullet("- **Which layer was not fully reached")
    assert f"**{_NUMBER_NAMES[len(_LEG_GAP_SURFACES)]}** surfaces have an assigned leg" in (
        layer_bullet
    ), "the leg-gap bullet must state every surface with a leg gap: " + layer_bullet
    assert _stated_leg_totals(layer_bullet) == {legs}, (
        f"the leg-gap bullet must state {legs} legs, once: " + layer_bullet
    )

    # The breakdown's buckets must BE the table's per-kind counts, each kind stated exactly
    # once (a corrected-then-restated count is a contradiction, not a rewording), and their
    # sum must be the stated total.
    stated_pairs = [
        (kind, _numeral(word))
        for word, kind in re.findall(
            r"\*\*(" + _NUMERAL_ALTERNATION + r") (automated|walk-through|manual)\*\*",
            layer_bullet,
            re.IGNORECASE,
        )
    ]
    assert len(stated_pairs) == 3, (
        "the leg-gap bullet must state its automated / walk-through / manual buckets once "
        f"each; it states {stated_pairs}. Bullet: " + layer_bullet
    )
    assert len({kind for kind, _ in stated_pairs}) == 3, (
        f"a leg-gap bucket is stated twice, which is a contradiction: {stated_pairs}"
    )
    stated_buckets = dict(stated_pairs)
    assert stated_buckets == {
        "automated": automated,
        "walk-through": kinds["walk_through"],
        "manual": kinds["manual"],
    }, (
        "the leg-gap bullet's per-kind counts must equal the table's: stated "
        f"{stated_buckets}, derived {{'automated': {automated}, 'walk-through': "
        f"{kinds['walk_through']}, 'manual': {kinds['manual']}}}. Bullet: {layer_bullet}"
    )
    assert sum(stated_buckets.values()) == legs, (
        f"the stated breakdown {stated_buckets} does not sum to the {legs} legs the table "
        "derives. Bullet: " + layer_bullet
    )

    # And the automated bucket's own sub-breakdown must add up, each state tied to its own
    # count — the order the two are named in carries no meaning.
    automated_clause = re.search(
        r"\*\*" + re.escape(_NUMBER_NAMES[automated]) + r" automated\*\* \(([^)]*)\)",
        layer_bullet,
        re.IGNORECASE,
    )
    assert automated_clause, (
        "the leg-gap bullet must break the automated legs down: " + layer_bullet
    )
    for state, kind in (
        ("attempted and failing", "automated_failed"),
        ("never run", "automated_never_run"),
    ):
        stated = re.search(
            r"(" + _NUMERAL_ALTERNATION + r") " + re.escape(state),
            automated_clause.group(1),
            re.IGNORECASE,
        )
        assert stated and _numeral(stated.group(1)) == kinds[kind], (
            f"the automated sub-breakdown must state {kinds[kind]} {state}; clause: "
            + automated_clause.group(1)
        )

    # The E2E-12 row inherits every leg, so its total must be the same total.
    e2e_12 = _single_line("| 12 | Cross-W journey")
    assert _stated_leg_totals(e2e_12) == {legs}, (
        f"the E2E-12 row must inherit all {legs} legs, stated as one total: " + e2e_12
    )

    # The Verdict's leg sentence must name exactly the six surfaces. Only *surface*
    # numerals and the `legs of ...` enumeration are read — a `#9001` citation or a
    # `2 of 54` ratio in the same sentence is not a seventh surface — and the sentence is
    # read from the folded paragraph, so re-wrapping the markdown cannot fail it.
    verdict_sentence = _leg_gap_sentence()
    assert f"**{_NUMBER_NAMES[len(_LEG_GAP_SURFACES)].capitalize()}** surfaces" in (
        verdict_sentence
    ), "the Verdict must state every surface with a leg gap: " + verdict_sentence
    assert _stated_leg_totals(verdict_sentence) == {legs}, (
        f"the Verdict must state {legs} legs, once: " + verdict_sentence
    )
    # Each surface's leg is named with the state the table records — the whole point of the
    # leg bullet is that "attempted and failed" is not "never run".
    assert re.search(
        r"surface 7['’]s[^.]*never passes under load[^.]*hosted-e2e[^.]*never run",
        verdict_sentence,
    ), "the Verdict must state surface 7's two legs and their states: " + verdict_sentence
    assert re.search(
        r"surface 12['’]s[^.]*hosted-e2e[^.]*(?:fails|not green)", verdict_sentence
    ), "the Verdict must state surface 12's failing hosted-e2e leg: " + verdict_sentence
    assert re.search(
        r"surface 8['’]s[^.]*walk-through[^.]*not run", verdict_sentence
    ), "the Verdict must state surface 8's un-run walk-through: " + verdict_sentence
    named = {int(n) for n in re.findall(r"surface[s]?\s+(\d+)", verdict_sentence)}
    if "legs of " in verdict_sentence:
        named |= _manual_surfaces_in(verdict_sentence)
    assert named == _LEG_GAP_SURFACES, (
        "the Verdict's leg sentence must name exactly surfaces "
        f"{sorted(_LEG_GAP_SURFACES)} (surface 7 supplies two legs, which is why the leg "
        f"count exceeds the surface count); it names {sorted(named)}. Sentence: "
        + verdict_sentence
    )

    # Indicator 2 carries the same set, surface by surface — each automated leg named with
    # its own state, and the manual legs enumerated. Read per surface and per state (not as
    # one long literal), so a faithful rewording is not a failure.
    indicator_two = _single_line("| 2 — every integration surface through its assigned layer")
    for surface, states in (
        (7, ("never passed under load", "hosted-e2e", "never ran")),
        (12, ("hosted-e2e", "fails")),
        (8, ("walk-through", "not run")),
    ):
        for state in states:
            assert re.search(
                rf"surface[s]? .{{0,40}}\b{surface}\b[^.]*{re.escape(state)}", indicator_two
            ), (
                f"indicator 2 must name surface {surface}'s leg and its state "
                f"({state!r}): " + indicator_two
            )
    manual_clause = re.search(
        r"surfaces "
        + _enumeration_pattern(sorted(_LEG_GAP_SURFACES - {7, 8, 12}))
        + r"[^.]*",
        indicator_two,
    )
    assert manual_clause, (
        "indicator 2 must enumerate the manual legs of surfaces "
        f"{sorted(_LEG_GAP_SURFACES - {7, 8, 12})}: " + indicator_two
    )
    assert "manual" in manual_clause.group(0), (
        "the enumerated clause must name the manual leg: " + manual_clause.group(0)
    )
    assert ("not executed" in manual_clause.group(0)) or (
        "partly" in manual_clause.group(0)
    ), (
        "the enumerated clause must state the manual leg's state: " + manual_clause.group(0)
    )


def test_the_leg_gap_bullet_is_read_only_as_far_as_its_own_sub_items():
    """The bucket assertions must be satisfiable by the bullet alone — driven negatively.

    The leg-gap bullet's own sub-items are indented two spaces, so a terminator of "the next
    COLUMN-0 bullet" ran the slice 107 lines past the bullet: the whole `## Per-surface
    evidence` table, the Totals paragraph and the browser walk were all inside it, and every
    bucket assertion could be met from text the bullet does not say. Proven by mutation on
    the head document (buckets and their total moved out of the bullet into Totals, nothing
    contracted): the derived readings must change. Pre-fix they did not — `_stated_leg_totals`
    still returned `{7}` and the three buckets were still found.
    """
    marker = "- **Which layer was not fully reached"
    legs = sum(_leg_kinds().values())

    bullet = _bullet(marker)
    assert "## Per-surface evidence" not in bullet and "**Totals" not in bullet, (
        "the leg-gap bullet's slice still runs past its own sub-items into the rest of the "
        f"document ({len(bullet.splitlines())} lines), so its counts can be satisfied from "
        "outside the bullet: " + bullet
    )
    assert _stated_leg_totals(bullet) == {legs}, (
        f"the bullet itself must state its {legs}-leg total: " + bullet
    )

    # Move the total-and-buckets line out of the bullet and into the Totals paragraph —
    # the reviewer's mutation. Only the bullet's own text may speak for the bullet.
    text = _doc_text()
    bucket_lines = [
        line
        for line in bullet.splitlines()
        if "legs** between them" in line and "automated" in line
    ]
    assert len(bucket_lines) == 1, (
        "expected exactly one line in the leg-gap bullet stating its total and buckets, "
        f"found {len(bucket_lines)}: {bullet}"
    )
    moved = _with_doc_edit(bucket_lines[0] + "\n", "", text)
    totals_anchor = "**Totals, stated so the arithmetic can be checked:** "
    moved = _with_doc_edit(totals_anchor, totals_anchor + _fold(bucket_lines[0]) + " ", moved)

    mutated = _bullet(marker, moved)
    assert _stated_leg_totals(mutated) != {legs}, (
        "a leg-gap bullet that no longer states its own total still reads as stating one "
        "— the reading is coming from outside the bullet: " + mutated
    )
    stated = re.findall(
        r"\*\*(" + _NUMERAL_ALTERNATION + r") (automated|walk-through|manual)\*\*",
        mutated,
        re.IGNORECASE,
    )
    assert len(stated) != 3, (
        "a leg-gap bullet that no longer states its three buckets still satisfies the "
        "bucket assertion — the reading is coming from outside the bullet: " + mutated
    )


def test_each_leg_gap_is_recorded_on_its_own_surface_row():
    """The table is where the set is checkable, so every leg must be on its row.

    Pre-fix, rows 7, 8 and 12 carried none of these clauses — a reader who checked the set
    against the table would have concluded the document contradicted itself (the defect an
    earlier round fixed for surface 7's row alone).
    """
    rows = _surface_rows()
    expected = {
        7: ("hosted-e2e leg never run", "its un-run hosted-e2e leg"),
        8: ("walk-through not executed", "its un-run walk-through"),
        12: ("hosted-e2e leg exists and FAILS", "its failing hosted-e2e leg"),
        5: ("manual 6-harness leg not executed", "its unexecuted manual leg"),
        15: ("manual leg not executed", "its unexecuted manual leg"),
        16: ("Manual leg **partly** covered", "its partly-covered manual leg"),
    }
    assert set(expected) == _leg_gap_surfaces(), (
        "this row-by-row check must cover exactly the surfaces the table derives: "
        f"derived {sorted(_leg_gap_surfaces())}, covered {sorted(expected)}"
    )
    for surface, (clause, description) in sorted(expected.items()):
        assert clause in _result_cell(rows[surface]), (
            f"surface {surface}'s row must record {description} (`{clause}`) in its "
            "Result cell: " + rows[surface]
        )


# --------------------------------------------------------------------------- #
# 9 — the report's `Surfaces` column really IS the map's (the "verbatim" label)
# --------------------------------------------------------------------------- #

#: The map `capstone-report.md` declares canonical — its `Surfaces` column is
#: labelled "verbatim from `04-test-design.md`" (header, line 26) and "copied
#: verbatim" (line 53). Nothing checked that, so PR #9013 changed the map's
#: E2E-6 row to `1, 2, 9, 10` while the report still said `1, 2, 10` — the
#: report misquoting its declared source is exactly the silent drift both
#: documents exist to prevent.
MAP = DOC.parent / "04-test-design.md"


def _row_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def _surface_numbers(cell: str) -> set[int]:
    """`1, 2, 9, 10` and `1-17 (all)` → the surface numbers the cell names."""
    numbers: set[int] = set()
    for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", cell):
        if "-" in chunk:
            lo, hi = (int(p) for p in chunk.split("-"))
            numbers.update(range(lo, hi + 1))
        else:
            numbers.add(int(chunk))
    return numbers


@lru_cache(maxsize=1)
def _map_e2e_surfaces() -> dict[str, str]:
    """`E2E-N` → the map's raw `Surfaces exercised` cell."""
    out: dict[str, str] = {}
    for line in MAP.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(E2E-\d+)\s*[^|]*\|", line)
        if not m:
            continue
        cells = _row_cells(line)
        if len(cells) != 2:
            continue
        out[m.group(1)] = cells[1]
    return out


@lru_cache(maxsize=1)
def _report_e2e_surfaces() -> dict[str, str]:
    """`E2E-N` → the report's raw `Surfaces` cell (Verdict-summary table, row #)."""
    out: dict[str, str] = {}
    for line in _doc_text().splitlines():
        m = re.match(r"^\|\s*(\d{1,2})\s*\|", line)
        if not m:
            continue
        cells = _row_cells(line)
        if len(cells) != 5:  # # | scenario | surfaces | evidence | result
            continue
        out[f"E2E-{m.group(1)}"] = cells[2]
    return out


def _norm_cell(cell: str) -> str:
    """Whitespace-normalised cell text.

    Spacing between numbers is markdown formatting; the NUMBERS and their ORDER
    are the claim. Nothing else is normalised — `1,2,9,10` is not `1, 2, 9, 10`,
    which is deliberate: the column says "verbatim".
    """
    return " ".join(cell.split())


def _verbatim_drift(
    report: dict[str, str], mapped: dict[str, str]
) -> dict[str, tuple[str, str]]:
    """`E2E-N` → (report cell, map cell) for every row that is not the map's.

    Deliberately NOT a set comparison: `10, 9, 2, 1` names exactly the surfaces
    `1, 2, 9, 10` does, so a number-set check passes it while the column header
    — "Surfaces (**verbatim** from `04-test-design.md`)" — becomes false. The
    pre-fix defect this replaces was found by reading, not by the guard.
    """
    return {
        e2e: (report[e2e], mapped[e2e])
        for e2e in report
        if _norm_cell(report[e2e]) != _norm_cell(mapped[e2e])
    }


def test_the_report_surfaces_column_is_the_maps_e2e_row_verbatim():
    """The `Surfaces` column is labelled verbatim — so it must equal the map's
    row for the same E2E, CELL FOR CELL. PRE-FIX (before this PR's capstone fix,
    and on any later map edit that forgets the copy): `E2E-6` was `1, 2, 10`
    here while the map said `1, 2, 9, 10`."""
    report, mapped = _report_e2e_surfaces(), _map_e2e_surfaces()
    assert len(mapped) == 12, f"could not parse the map's 12 E2E rows: {sorted(mapped)}"
    assert set(report) == set(mapped), (
        "the report's scenario rows drifted from the map — "
        f"missing {sorted(set(mapped) - set(report))}, "
        f"extra {sorted(set(report) - set(mapped))}")
    drifted = _verbatim_drift(report, mapped)
    assert not drifted, (
        "the report's `Surfaces` column is labelled verbatim but is not the "
        "map's cell (report vs map): "
        + "; ".join(f"{k}: {v[0]!r} vs {v[1]!r}" for k, v in sorted(drifted.items())))


def test_the_surfaces_cross_check_rejects_a_misquoted_row():
    """Non-vacuity: the comparison above must FAIL on the pre-fix disagreement,
    and on the two drifts the previous NUMBER-SET comparison silently accepted."""
    mapped = {"E2E-6": "1, 2, 9, 10"}
    # the pre-fix disagreement itself
    assert _verbatim_drift({"E2E-6": "1, 2, 10"}, mapped), (
        "the comparison passes the exact drift this pin was written for")
    # same set, different ORDER — the label says verbatim, so this is drift
    assert _verbatim_drift({"E2E-6": "10, 9, 2, 1"}, mapped), (
        "a reordered cell names the same surfaces and is still not verbatim")
    # same set and order, only the spacing differs
    assert _verbatim_drift({"E2E-6": "1,2,9,10"}, mapped)
    # and it must not fire on an exact copy, spacing included
    assert not _verbatim_drift({"E2E-6": "1, 2, 9, 10"}, mapped)
    # the drift is reported per row, with both cells, so a failure is readable
    assert _verbatim_drift({"E2E-6": "1, 2, 10"}, mapped) == {"E2E-6": ("1, 2, 10", "1, 2, 9, 10")}
    # the number side still parses, for the readers that need sets
    assert _surface_numbers("1, 2, 9, 10") == {1, 2, 9, 10}
    assert _surface_numbers("1-17 (all)") == set(range(1, 18))
    assert _surface_numbers(_report_e2e_surfaces()["E2E-6"]) == _surface_numbers(
        _map_e2e_surfaces()["E2E-6"])
    assert {1, 2, 10} != _surface_numbers(_map_e2e_surfaces()["E2E-6"]), (
        "E2E-6 is back to `1, 2, 10` — the surface-9 door this pin exists for "
        "was dropped again")


# --------------------------------------------------------------------------- #
# 10 — the guard's own contract: one numbered entry per check, and one ruled
#      section per number
# --------------------------------------------------------------------------- #

# The module docstring is this file's only contract ("THE DEFECTS THIS PINS") and the
# `# N — title` section comments are how each check is located, so a merge can leave both
# wrong while every assertion above stays green. It did: integrating #9013 produced a
# duplicated docstring fragment, two items numbered `7.`, and a section header labelled
# `# 7 —` sitting under this branch's `# 7-8 —`, with its opening rule dropped. Nothing
# here is about the report — this reads the guard's own source.

_GUARD_SOURCE = Path(__file__).resolve()

_SECTION_TITLE = re.compile(r"^# (\d+)(?:-(\d+))? — ")
_SECTION_RULE = re.compile(r"^# -{20,} #$")
_CONTRACT_ITEM = re.compile(r"^(\d+)\. ", re.MULTILINE)


def _contract_list(source: str) -> tuple[list[int], list[str]]:
    """The `WHAT THIS PINS` item numbers, and the docstring lines that follow the list."""
    docstring = source.split('"""')[1]
    if "WHAT THIS PINS" not in docstring:
        return [], []
    lines = docstring.split("WHAT THIS PINS", 1)[1].splitlines()
    items: list[int] = []
    for index, line in enumerate(lines):
        item = _CONTRACT_ITEM.match(line)
        if item:
            items.append(int(item.group(1)))
        elif (
            items
            and not line.strip()
            # the list ends at the first blank line that no further item follows
            and (index + 1 == len(lines) or not _CONTRACT_ITEM.match(lines[index + 1]))
        ):
            return items, lines[index + 1 :]
    return items, []


def _contract_items(source: str) -> list[int]:
    """The `WHAT THIS PINS` list's item numbers, in the order they are written."""
    return _contract_list(source)[0]


def _section_checks(source: str) -> list[int]:
    """The check number each ruled section header starts at, in file order."""
    return [
        int(m.group(1))
        for line in source.splitlines()
        if (m := _SECTION_TITLE.match(line))
    ]


def _contract_violations(source: str) -> list[str]:
    """Every way `source` breaks the one-entry-per-check contract (empty when clean)."""
    out: list[str] = []
    documented = _contract_items(source)
    if documented != list(range(1, len(documented) + 1)):
        out.append(
            "the `WHAT THIS PINS` list must number 1..N once each, in order — a repeated "
            f"or skipped number is merge residue: {documented}"
        )
    sections = _section_checks(source)
    if len(sections) < 2:
        out.append(f"could not read the guard's section headers: {sections}")
    trailing = [ln for ln in _contract_list(source)[1] if _CONTRACT_ITEM.match(ln)]
    if trailing:
        out.append(
            "a `WHAT THIS PINS` item appears after the list ended — merge residue drops "
            f"one into the middle of the prose: {trailing[0]!r}"
        )
    if sections != sorted(set(sections)):
        out.append(
            "each section must carry its own check number — two sections claiming one "
            f"number is merge residue: {sections}"
        )
    if not set(sections) <= set(documented):
        out.append(
            "a section header claims a check the docstring does not list: "
            f"{sorted(set(sections) - set(documented))}"
        )
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if not _SECTION_TITLE.match(line):
            continue
        if index < 3:
            out.append(f"section header at line {index + 1} has no room for its rule")
            continue
        if not _SECTION_RULE.match(lines[index - 1]):
            out.append(
                f"the section header at line {index + 1} lost its opening rule: {line}"
            )
        if lines[index - 2] != "" or lines[index - 3] != "":
            out.append(
                f"the section header at line {index + 1} is glued to the code above it "
                "— a top-level block needs two blank lines"
            )
        cursor = index + 1
        while (
            cursor < len(lines)
            and lines[cursor].startswith("#")
            and not _SECTION_RULE.match(lines[cursor])
        ):
            cursor += 1
        if cursor == len(lines) or not _SECTION_RULE.match(lines[cursor]):
            out.append(
                f"the section header at line {index + 1} has no closing rule: {line}"
            )
    return out


def test_the_guards_own_contract_numbers_each_check_once():
    """Every check is one entry in the docstring and one ruled section, numbered the
    same. PRE-FIX (the #9013 merge residue this PR fixes): two sections both started at
    7, the second with no opening rule and glued to the code above, and a stray `7.`
    item sat after the list had ended."""
    violations = _contract_violations(_GUARD_SOURCE.read_text(encoding="utf-8"))
    assert not violations, "the guard's own contract is inconsistent:\n" + "\n".join(
        violations
    )


def test_the_contract_check_rejects_merge_residue():
    """Non-vacuity: driven with the three shapes the #9013 merge actually produced."""
    source = _GUARD_SOURCE.read_text(encoding="utf-8")
    # (a) a second item numbered 7 — the residue: `... 8. ... 7. The report's Surfaces ...`
    renumbered = source.replace(
        "9. The report's `Surfaces` column", "7. The report's `Surfaces` column", 1
    )
    assert "repeated or skipped number" in " ".join(_contract_violations(renumbered))
    # (b) a section header reusing a number already taken by `# 7-8`
    duplicate = source.replace(
        "# 9 — the report's `Surfaces` column", "# 7 — the report's `Surfaces` column", 1
    )
    assert "two sections claiming one number" in " ".join(_contract_violations(duplicate))
    # (c) the dropped opening rule and the glued block — the residue's other half
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if _SECTION_TITLE.match(line) and line.startswith("# 9 —"):
            del lines[index - 1]  # the opener
            del lines[index - 3]  # one of the two blank lines
            break
    glued = "\n".join(lines) + "\n"
    reported = " ".join(_contract_violations(glued))
    assert "lost its opening rule" in reported
    assert "glued to the code above" in reported
