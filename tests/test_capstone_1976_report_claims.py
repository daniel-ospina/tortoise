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

Non-vacuity: on the pre-fix document, checks 1–3 fail (it said "which is what licenses
the cross-head comparison — checked per file", carried no tree-state line, and its
indicator-2 row had no anchor caveat); check 5's extractor surfaces the dangling name from
synthetic doc text and the resolver rejects it; check 6 fails on "8 files, 201 tests".
7. The report's `Surfaces` column — labelled "verbatim from `04-test-design.md`" —
   is the map's cell, compared as whitespace-normalised TEXT. A number-SET
   comparison (the first version) passed `10, 9, 2, 1` against `1, 2, 9, 10`,
   which is a different cell, so the "verbatim" label stayed false while the
   guard was green. Non-vacuity is in-suite: the comparison is driven with a
   drifted, a REORDERED and a spacing-only pair.

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


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """The body of a `## heading` section, up to the next `## `."""
    text = _doc_text()
    start = text.index(heading)
    end = text.index("\n## ", start + len(heading))
    return text[start:end]


def _bullet(marker: str) -> str:
    """The single `- **Which sha:**`-style bullet, up to the next top-level bullet."""
    text = _doc_text()
    assert marker in text, f"{marker!r} not found in {DOC.name}"
    start = text.index(marker)
    end = text.find("\n- ", start + len(marker))
    return text[start:] if end == -1 else text[start:end]


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
    text = _doc_text()
    row = [
        line
        for line in text.splitlines()
        if line.startswith("| 2 — every integration surface through its assigned layer")
    ]
    assert row, "indicator 2's row not found"
    row_text = row[0]
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
# 7 — the report's `Surfaces` column really IS the map's (the "verbatim" label)
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
