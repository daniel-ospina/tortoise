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
   `git status --porcelain` accompanied the attribution, so those 201 executions could
   not be shown to have measured `fbc1d96d5` rather than `fbc1d96d5` plus another lane's
   leftovers.

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
5. Every `test_*` symbol the report cites resolves to a real module stem or a real
   `def`. The pre-fix defect this class already produced once: an earlier revision cited
   `test_fork_card_is_shown_once_per_org`, which exists nowhere — a dangling pointer in
   the one document whose value is that every pointer resolves. That name is used below
   as the resolver's non-vacuity case.

Non-vacuity: on the pre-fix document, checks 1–3 fail (it said "which is what licenses
the cross-head comparison — checked per file", carried no tree-state line, and its
indicator-2 row had no anchor caveat). Check 5's resolver rejects the dangling name that
the earlier revision actually shipped.

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
# 5 — every cited test symbol resolves (the dangling-pointer class)
# --------------------------------------------------------------------------- #

#: A name an earlier revision of this report shipped as surface 4's evidence. It
#: exists nowhere in the repo — the resolver below must reject it.
_DANGLING_NAME = "test_fork_card_is_shown_once_per_org"


@lru_cache(maxsize=1)
def _test_defs() -> frozenset[str]:
    names: set[str] = set()
    pattern = re.compile(r"^[ \t]*def (test_\w+)", re.MULTILINE)
    for path in sorted((ROOT / "tests").rglob("*.py")):
        names.update(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return frozenset(names)


@lru_cache(maxsize=1)
def _test_module_stems() -> frozenset[str]:
    return frozenset(path.stem for path in (ROOT / "tests").rglob("*.py"))


def _resolves(name: str) -> bool:
    return name in _test_module_stems() or name in _test_defs()


def _cited_test_symbols() -> list[str]:
    """Bare ``test_*`` tokens in inline code — a module stem or a test function."""
    return sorted(set(re.findall(r"`(test_[A-Za-z0-9_]+)`", _doc_text())))


def test_every_test_symbol_the_report_cites_resolves():
    dangling = [name for name in _cited_test_symbols() if not _resolves(name)]
    assert not dangling, (
        "the report cites test symbols that exist neither as a module under `tests/` "
        f"nor as a `def`: {dangling}. A reader cannot detect a dangling test name by "
        "reading, which is why this is a guard."
    )


def test_the_resolver_rejects_the_dangling_name_an_earlier_revision_cited():
    """Non-vacuity: check 5 must be able to FAIL on a real dangling citation."""
    assert not _resolves(_DANGLING_NAME), (
        f"{_DANGLING_NAME!r} now exists. It was the dangling citation this guard was "
        "written for; if it has been added, pick another known-absent name so the "
        "resolver keeps a non-vacuity case."
    )
