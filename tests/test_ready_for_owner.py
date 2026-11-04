"""Red-first tests for ``tools/ready_for_owner.py`` — the owner's merge-readiness gate.

Why this file is the valuable half of that tool
-----------------------------------------------
A gate tool is only worth the failures it can produce. A gate that prints NOT-READY for
everything passes every "does it refuse" test trivially; a gate that prints READY for
everything passes every "does it accept" test. So the matrix below is written per FAILURE
MODE, each fed a fixture that differs from a fully-green one in exactly one place, and it
is then re-run against an in-process MUTANT of each condition — a laxer version that would
ship the defect. A mutant that SURVIVES (i.e. the mode's test still passes while the mutant
is installed) is the gate lying, and ``test_mutation_battery_*`` fails loudly on it.

The modes, one test each:

* a required check concluded ``cancelled``           → NOT-READY   (``test_cancelled_*``)
* a required check concluded ``null``                → NOT-READY   (``test_null_*``)
* a required check never scheduled                   → NOT-READY   (``test_missing_*``)
* a required check still in flight                   → NOT-READY   (``test_in_flight_*``)
* a required check ``skipped``/``neutral``           → NOT-READY   (``test_skipped_*``)
* a red that a cancellation re-ran over              → NOT-READY   (``test_voided_red_*``)
* a review record bound to an older head             → NOT-READY   (``test_stale_review_*``)
* a head that moved 10 s ago                         → NOT-READY   (``test_head_10s_*``)
* ``CONFLICTING`` flag with a CLEAN tree             → PASSES the tree condition
* a declared union the diff under-delivers           → NOT-READY   (``test_hand_picked_*``)
* an open P0/P1 after the last clean verdict         → NOT-READY   (``test_open_blocking_*``)
* any unreadable input, one per condition            → NOT-READY   (``test_unreadable_*``)
* a fully-green fixture                              → READY, exit 0 (``test_green_*``)
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import ci_verdict  # noqa: E402
from tools import ready_for_owner as rfo  # noqa: E402

REPO = "daniel-ospina/tortoise"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)

#: The live required set — `gh api repos/daniel-ospina/tortoise/branches/main/protection`
#: read on 2026-11-04. Kept verbatim so a drift in the real protection shows up here.
REQUIRED = (
    "ai-review-gate",
    "docs",
    "legal-e2e",
    "license-surface",
    "pricing-artifact",
    "python-ci-gate",
    "test-isolation",
)


# ── fixture builders ────────────────────────────────────────────────────────


def _group(
    job: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    state: str = ci_verdict.GROUP_GREEN,
    check_id: int = 1000,
    voided_red: bool = False,
) -> dict[str, Any]:
    return {
        "app": "github-actions",
        "workflow": "CI",
        "workflow_key": "wf-1",
        "job": job,
        "state": state,
        "check_id": check_id,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/{REPO}/actions/runs/{check_id}/job/{check_id}",
        "started_at": "2026-11-04T11:40:00Z",
        "voided_red": voided_red,
    }


def _fixture(**overrides: Any) -> dict[str, Any]:
    """A fully-green PR. Every test below changes ONE field of this."""
    data: dict[str, Any] = {
        "pr": 10999,
        "repo": REPO,
        "head": HEAD,
        "base": "main",
        "is_draft": False,
        "mergeable": "MERGEABLE",
        "merge_state": "CLEAN",
        # 30 minutes before NOW — comfortably past the 15-minute settling bound.
        "head_committed_at": "2026-11-04T11:30:00Z",
        "body": "Refs #10900\n\nA normal PR body.",
        "tree": True,
        "tree_raw": "merge-tree write-tree ok tree=deadbeefcafe base=origin/main",
        "groups": [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)],
        "groups_raw": "surface=7 groups (green=7 red=0 in-flight=0 absent=0)",
        "required": list(REQUIRED),
        "required_raw": f"required={len(REQUIRED)} {list(REQUIRED)}",
        "review_token": "head",
        "review_exit": 0,
        "review_raw": "token=head exit=0 record=- live=-",
        "touched": ["tortoise/sdk.py", "tools/ready_for_owner.py"],
        "touched_raw": "files=2",
        "union": None,
        "union_raw": "no declared union",
        "uncovered": [],
        "coverage_raw": "files=2 full=false surfaces=['core'] unclassified-without-full=0",
        "comments": [
            {
                "id": 1,
                "created_at": "2026-11-04T11:45:00Z",
                "kind": "comment",
                "body": "Reviewer verdict: NO ISSUES FOUND",
            }
        ],
        "comments_raw": "comments=1",
        "now": NOW.isoformat(),
    }
    data.update(overrides)
    return data


def _check(name: str, **overrides: Any) -> rfo.Check:
    verdict = rfo.evaluate(rfo.probe_from_dict(_fixture(**overrides)))
    return next(c for c in verdict.checks if c.name == name)


def _verdict(**overrides: Any) -> rfo.Verdict:
    return rfo.evaluate(rfo.probe_from_dict(_fixture(**overrides)))


def _assert_fails(name: str, reason_prefix: str, **overrides: Any) -> rfo.Check:
    check = _check(name, **overrides)
    assert check.passed is False, f"expected {name} to FAIL, got {check.status}: {check.raw}"
    assert check.reason.startswith(reason_prefix), (
        f"expected {name} reason to start with {reason_prefix!r}, got {check.reason!r}"
    )
    assert check.raw, f"{name} must carry the raw value it read"
    return check


# ── the end-to-end green case (the only way READY may ever appear) ──────────


def test_green_fixture_is_ready_and_prints_the_canonical_line() -> None:
    verdict = _verdict()
    assert [c.name for c in verdict.checks] == list(rfo.CONDITIONS)
    assert all(c.passed for c in verdict.checks), [
        (c.name, c.reason, c.raw) for c in verdict.checks if not c.passed
    ]
    assert verdict.ready is True
    assert verdict.line() == f"READY-FOR-OWNER(10999, {HEAD})"


def test_green_fixture_renders_every_raw_value_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = tmp_path / "probe.json"
    fixture.write_text(json.dumps(_fixture()))
    assert rfo.main(["--fixture", str(fixture)]) == 0
    out = capsys.readouterr().out  # what `main` actually PRINTED, not what render() would
    for condition in rfo.CONDITIONS:
        assert condition in out
    assert "raw:" in out
    assert out.strip().endswith(f"READY-FOR-OWNER(10999, {HEAD})")


def test_not_ready_line_lists_every_failed_condition() -> None:
    verdict = _verdict(tree=False, review_token="fresh", review_exit=1)
    assert verdict.ready is False
    assert verdict.line() == "NOT-READY(10999, failed=tree,review)"
    # Every condition still ran — no short-circuit on the first failure.
    assert len(verdict.checks) == len(rfo.CONDITIONS)


def test_not_ready_exits_non_zero(tmp_path: Path) -> None:
    fixture = tmp_path / "probe.json"
    fixture.write_text(json.dumps(_fixture(tree=False)))
    assert rfo.main(["--fixture", str(fixture)]) == 1


# ── condition 2: the gates (the trap the whole tool exists for) ─────────────


def test_cancelled_required_check_is_not_ready() -> None:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(
        REQUIRED[0],
        conclusion="cancelled",
        state=ci_verdict.GROUP_ABSENT,
        check_id=2000,
    )
    check = _assert_fails("gates", "absent:cancelled", groups=groups)
    assert "ai-review-gate=completed/cancelled" in check.raw


def test_null_conclusion_required_check_is_not_ready() -> None:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(REQUIRED[0], conclusion=None, state=ci_verdict.GROUP_RED, check_id=2000)
    check = _assert_fails("gates", "red:null", groups=groups)
    assert "ai-review-gate=completed/null" in check.raw


def test_missing_required_check_is_not_ready() -> None:
    # #10962's live shape: no `python-ci-gate` run exists at all on the head.
    groups = [
        _group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED) if job != "python-ci-gate"
    ]
    check = _assert_fails("gates", "missing:python-ci-gate", groups=groups)
    assert "python-ci-gate=MISSING" in check.raw


def test_in_flight_required_check_is_not_ready() -> None:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(
        REQUIRED[0],
        status="queued",
        conclusion=None,
        state=ci_verdict.GROUP_IN_FLIGHT,
        check_id=2000,
    )
    check = _assert_fails("gates", "in-flight:ai-review-gate", groups=groups)
    assert "ai-review-gate=queued/null" in check.raw


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_non_measuring_required_check_is_not_ready(conclusion: str) -> None:
    """GitHub calls these passing. This repo's queue does not (`.mergify.yml` check-success)."""
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(REQUIRED[0], conclusion=conclusion, check_id=2000)
    _assert_fails("gates", f"non-measuring:{conclusion}", groups=groups)


def test_voided_red_cannot_be_cleared_by_a_cancellation() -> None:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(
        REQUIRED[0],
        conclusion="cancelled",
        state=ci_verdict.GROUP_ABSENT,
        check_id=3000,
        voided_red=True,
    )
    _assert_fails("gates", "red-voided:ai-review-gate", groups=groups)


def test_unreadable_required_contexts_are_unreadable_not_empty() -> None:
    check = _check("gates", required=None, required_raw="")
    assert check.passed is False
    assert check.unreadable is True
    assert "unreadable" in check.reason


def test_unreadable_check_surface_is_unreadable_not_green() -> None:
    check = _check("gates", groups=None)
    assert check.passed is False
    assert check.unreadable is True


def test_in_flight_rerun_does_not_hide_the_measured_red_behind_it() -> None:
    """Two groups for one context — a `queued` re-run must not mask the `failure`.

    A `pull_request_target` run is absent from `actions/runs?head_sha=`, so
    ``ci_verdict`` keys it by run id and one required context ends up with several
    groups. Reporting only the NEWEST token would name ``in-flight`` and hide the red.
    """
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(REQUIRED[0], conclusion="failure", state=ci_verdict.GROUP_RED, check_id=1000)
    groups.append(
        _group(
            REQUIRED[0],
            status="queued",
            conclusion=None,
            state=ci_verdict.GROUP_IN_FLIGHT,
            check_id=2000,
        )
    )
    check = _check("gates", groups=groups)
    assert check.passed is False
    assert "red:failure:ai-review-gate" in check.reason, check.reason
    assert "in-flight:ai-review-gate" in check.reason, check.reason


# ── condition 1: the tree (merge-tree, never the flag) ──────────────────────


def test_conflicting_flag_with_clean_tree_PASSES_the_tree_condition() -> None:
    """The flag is not evidence. #10790 read DIRTY after its fix had landed."""
    tree = _check("tree", mergeable="CONFLICTING", merge_state="DIRTY", tree=True)
    assert tree.passed is True, tree.raw
    assert "mergeable=CONFLICTING" in tree.raw  # quoted, and visibly not the decision
    # …and the exclusions condition must not fail on the flag alone either.
    exclusions = _check("exclusions", mergeable="CONFLICTING", merge_state="DIRTY", tree=True)
    assert exclusions.passed is True, exclusions.reason
    assert "flag IGNORED" in exclusions.raw


def test_dirty_tree_is_not_ready() -> None:
    check = _assert_fails("tree", "dirty", tree=False, tree_raw="merge-tree CONFLICTS: sdk.py")
    assert "CONFLICTS" in check.raw


def test_conflicting_flag_with_dirty_tree_is_not_ready() -> None:
    _assert_fails(
        "exclusions", "conflicting-flag-with-dirty-tree", mergeable="CONFLICTING", tree=False
    )


def test_unreadable_tree_is_unreadable_not_clean() -> None:
    check = _check("tree", tree=None)
    assert check.passed is False
    assert check.unreadable is True


def test_non_main_base_is_not_ready() -> None:
    """#10597: a `Closes` merged into a sibling branch neither lands nor closes."""
    _assert_fails("tree", "base-not-main:", base="act-10465")


# ── condition 3: the review record, bound to the LIVE head ─────────────────


def test_stale_review_record_is_not_ready() -> None:
    check = _assert_fails(
        "review",
        "not-bound:fresh",
        review_token="fresh",
        review_exit=1,
        review_raw="token=fresh exit=1 record=abc live=def",
    )
    assert "record=abc" in check.raw


@pytest.mark.parametrize("token", ["nodigest", "carry-patch", "norecord"])
def test_non_carrying_review_tokens_are_not_ready(token: str) -> None:
    _assert_fails("review", f"not-bound:{token}", review_token=token, review_exit=0)


def test_carried_review_record_passes() -> None:
    """A base-move carries when the reviewed DIFF is unchanged — that is not a re-review."""
    check = _check("review", review_token="carry", review_exit=0)
    assert check.passed is True


def test_unreadable_review_diff_is_unreadable_not_stale() -> None:
    check = _check("review", review_token="unreadable", review_exit=3)
    assert check.passed is False
    assert check.unreadable is True
    assert check.reason == "diff-unreadable-retry"


# ── condition 5: the declared exclusions ──────────────────────────────────


def test_head_moved_10s_ago_is_not_ready() -> None:
    young = (NOW - timedelta(seconds=10)).isoformat()
    check = _assert_fails("exclusions", "head-too-young:10s", head_committed_at=young)
    assert "head-age=10s" in check.raw


def test_head_older_than_15_minutes_passes() -> None:
    old = (NOW - timedelta(minutes=16)).isoformat()
    assert _check("exclusions", head_committed_at=old).passed is True


def test_draft_is_not_ready() -> None:
    _assert_fails("exclusions", "draft", is_draft=True)


def test_declared_exclusion_10226_is_not_ready() -> None:
    verdict = _verdict(pr=10226)
    check = next(c for c in verdict.checks if c.name == "exclusions")
    assert check.passed is False
    assert check.reason == "declared-exclusion:#10226"
    assert "SDK-surface" in check.raw  # the declared reason is quoted, not summarised


def test_reversal_without_an_overrides_line_is_not_ready() -> None:
    body = "Refs #4282\n\nREVERSES the #7704 owner ruling on dedup evidence.\n"
    _assert_fails("exclusions", "reversal-awaiting-overrides", body=body)


def test_reversal_with_an_overrides_line_passes_that_arm() -> None:
    body = (
        "Refs #4282\n\nREVERSES the #7704 owner ruling.\n"
        "OVERRIDES: the dedup-keeps-evidence default — the owner re-ruled on 2026-11-01.\n"
    )
    assert _check("exclusions", body=body).passed is True


def test_unreadable_head_date_is_unreadable() -> None:
    check = _check("exclusions", head_committed_at="not-a-date")
    assert check.passed is False
    assert check.unreadable is True


# ── condition 4: the declared union vs a hand-picked subset ────────────────


def test_declared_union_covered_by_the_diff_passes() -> None:
    check = _check(
        "coverage",
        union=["tortoise/sdk.py", "tools/ready_for_owner.py"],
        coverage_raw="files=2 full=false surfaces=['core'] unclassified-without-full=0",
    )
    assert check.passed is True
    assert "union=declared(2)" in check.raw


def test_hand_picked_subset_of_a_declared_union_is_not_ready() -> None:
    """The fold claims 3 constituents; the diff carries 2. The gates measured the wrong set."""
    check = _assert_fails(
        "coverage",
        "hand-picked-subset:1",
        union=["tortoise/sdk.py", "tools/ready_for_owner.py", "docs/ONTOLOGY.md"],
    )
    assert "missing=['docs/ONTOLOGY.md']" in check.raw


def test_uncovered_file_is_not_ready() -> None:
    check = _assert_fails("coverage", "uncovered-files", uncovered=["tortoise/orphan_module.py"])
    assert "tortoise/orphan_module.py" in check.raw


def test_unreadable_touched_files_is_unreadable() -> None:
    check = _check("coverage", touched=None, touched_raw="pulls/1/files: 403")
    assert check.passed is False
    assert check.unreadable is True


def test_unreadable_selection_is_unreadable() -> None:
    check = _check("coverage", uncovered=None, coverage_raw="ci_selection.select failed: boom")
    assert check.passed is False
    assert check.unreadable is True


def test_unreadable_declared_union_is_unreadable_not_no_union() -> None:
    """A declared union that could not be resolved must not silently become "no union".

    Both states write `union is None`; conflating them disabled the hand-picked-subset
    arm behind one transient 502 while the raw line still claimed `union=pr-diff`.
    """
    check = _check(
        "coverage",
        union=None,
        union_unreadable=True,
        union_raw="gh api pulls/10901/files: HTTP 502",
    )
    assert check.passed is False
    assert check.unreadable is True
    assert "502" in check.raw


def test_core_smoke_only_file_is_covered() -> None:
    """`tools/*.py` has no surface of its own but the always-on core smoke still runs.

    Measured: `select(["tools/ready_for_owner.py"])` → surfaces=[], full=False, 72 tests.
    An earlier version failed that shape, so the tool could not certify this very PR.
    """
    check = _check(
        "coverage",
        uncovered=[],
        coverage_raw=("files=2 full=false surfaces=[] core-smoke-only=1 no-leg=0"),
    )
    assert check.passed is True, check.reason
    assert "core-smoke-only=1" in check.raw


# ── condition 6: no OPEN blocking finding ─────────────────────────────────


def test_open_blocking_finding_after_the_last_clean_verdict_is_not_ready() -> None:
    comments = [
        {
            "id": 10,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "NO ISSUES FOUND",
        },
        {
            "id": 11,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "comment",
            "body": "[P1] tortoise/sdk.py:412 — the fold drops the evidence",
        },
    ]
    check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
    assert "open=comment:11" in check.raw
    assert "the fold drops the evidence" in check.raw


def test_a_fixed_blocking_finding_does_not_block() -> None:
    """Order decides: a clean verdict AFTER the finding closes it."""
    comments = [
        {
            "id": 20,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "[P0] tools/x.py:9 — crash on empty input",
        },
        {
            "id": 21,
            "created_at": "2026-11-04T11:55:00Z",
            "kind": "comment",
            "body": "Re-review: NO ISSUES FOUND at the new head.",
        },
    ]
    check = _check("blocking-finding", comments=comments)
    assert check.passed is True
    assert "last-clear=21" in check.raw


def test_issues_verdict_is_a_blocking_record() -> None:
    comments = [
        {
            "id": 30,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "comment",
            "body": "ISSUES 2\n\n[P2] docs/x.md:3 — stale table",
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


def test_p3_only_note_is_not_blocking() -> None:
    comments = [
        {
            "id": 40,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "comment",
            "body": "CLEAN\n\n[P3] tools/x.py:1 — a naming nit",
        }
    ]
    assert _check("blocking-finding", comments=comments).passed is True


def test_a_formal_change_request_is_blocking_even_with_an_empty_body() -> None:
    """The findings can live entirely in INLINE comments; `CHANGES_REQUESTED` is GitHub's
    own record that the PR is not clean yet. Reading only non-empty bodies dropped it."""
    comments = [
        {
            "id": 60,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "review",
            "state": "CHANGES_REQUESTED",
            "body": "",
        }
    ]
    check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
    assert "CHANGES_REQUESTED" in check.raw


def test_an_inline_review_finding_is_blocking() -> None:
    comments = [
        {
            "id": 70,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "inline",
            "state": "COMMENTED",
            "body": "[P1] tortoise/sdk.py:412 — the fold drops the evidence",
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


def test_an_approved_review_is_not_blocking() -> None:
    comments = [
        {
            "id": 80,
            "created_at": "2026-11-04T11:50:00Z",
            "kind": "review",
            "state": "APPROVED",
            "body": "NO ISSUES FOUND",
        }
    ]
    assert _check("blocking-finding", comments=comments).passed is True


def test_an_empty_declared_union_is_not_a_pass() -> None:
    """A malformed `READY-GATE UNION:` line parses to `()`; treating that as "no union"
    is the same silent collapse the read-error arm just closed."""
    check = _assert_fails("coverage", "empty-declared-union", union=[])
    assert "union=declared(0)" in check.raw


def test_unreadable_comments_is_unreadable() -> None:
    check = _check("blocking-finding", comments=None)
    assert check.passed is False
    assert check.unreadable is True


def test_a_pr_with_no_comments_at_all_is_not_blocked() -> None:
    """`()` is "read, nothing posted"; `None` is "could not read". Only the latter fails."""
    check = _check("blocking-finding", comments=[])
    assert check.passed is True, check.reason
    assert "comments=0" in check.raw


#: Exactly `scripts/record-review.sh`'s arms (1) and (1b) — the two it applies
#: UNCONDITIONALLY when deciding an artifact is not evidence of a clean outcome.
@pytest.mark.parametrize(
    "body",
    [
        "⚠️ Auto-fix reached this change's proportional cycle ceiling (2 of 2 cycles) — blocking issues remain; escalate to a human",
        "⚠️ Auto-fix stalled after 3 cycles — requires human review",
        "⚠️ Auto-fix aborted (tool-unavailable) — issues require human review",
        "⚠️ Auto-fix converged with issues unresolved — requires human review",
        "⚠️ Auto-fix stuck (honest-stuck): the same finding recurs",
        "⚠️ Auto-fix failed: could not apply 3 fixes",
        "⚠️ Test review capped at 10 cycles — 2 issues remain:",
        "2 issues remain",
        "## Review — NOT CLEAN",
        "Outcome: not clean",
        "[ADVERSARIAL-BOUND] cycles=2 threats=5 covered=4 residuals=1",
        "[NO-LOOP-BUDGET] the loop spent its budget",
        "Requires human input: the owner must rule on the reversal",
    ],
)
def test_a_capped_or_escalated_exit_is_not_a_clean_verdict(body: str) -> None:
    """AGENTS.md: \"a capped exit is never reported as clean\". Order matters: a capped
    marker AFTER a clean verdict must still block."""
    comments = [
        {
            "id": 50,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "NO ISSUES FOUND",
        },
        {"id": 51, "created_at": "2026-11-04T11:55:00Z", "kind": "comment", "body": body},
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


# ── never READY on a partial read ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required", None),
        ("groups", None),
        ("tree", None),
        ("review_token", None),
        ("touched", None),
        ("uncovered", None),
        ("comments", None),
    ],
)
def test_any_unreadable_input_blocks_ready(field: str, value: Any) -> None:
    verdict = _verdict(**{field: value})
    assert verdict.ready is False
    unreadable = [c.name for c in verdict.checks if c.unreadable]
    assert unreadable, f"{field}=None must produce a FAIL(unreadable), got {verdict.line()}"
    assert verdict.line().startswith("NOT-READY(")


@pytest.mark.parametrize("field", ["required", "touched"])
def test_an_empty_collection_is_not_a_pass(field: str) -> None:
    """An empty required set / file list is a set that was NOT READ, not "nothing to do".

    Reachable through `--fixture`, so a hand-written fixture could otherwise print READY.
    """
    verdict = _verdict(**{field: []})
    assert verdict.ready is False, verdict.line()
    assert any(c.unreadable for c in verdict.checks if not c.passed)


class _FakeRecorder:
    """Runs in place of `record-review.sh`, and stands in for the script FILE itself.

    ⛔ The script-existence check is stubbed too (`_review_script`): `scripts/` is a
    machine-local, gitignored pointer that is documented ABSENT on a CI runner, so a test
    that needs the real file turns red for an ENVIRONMENT reason — and the tempting fix is
    then to weaken the test rather than to isolate it.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.state = {"rc": 0, "stdout": "", "stderr": ""}
        monkeypatch.setattr(rfo, "_review_script", lambda workdir: Path("/fake/record-review.sh"))

        def fake_run(cmd, timeout, cwd=None):
            return subprocess.CompletedProcess(
                cmd, self.state["rc"], self.state["stdout"], self.state["stderr"]
            )

        monkeypatch.setattr(rfo, "_run", fake_run)


def test_norecord_exit_code_is_mapped_to_norecord_not_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`record-review.sh`: `fresh` is exit 1 and `norecord` is exit 4 — not the reverse.

    Both FAIL, so there was never a false READY; but the reverse mapping told a lane to
    RETRY a state that cannot change and to RE-RECORD a diff that had moved.
    """
    recorder = _FakeRecorder(monkeypatch)

    # Exit 0 with no stdout is still NOT a verdict — it must not read as a pass.
    assert rfo._read_review(1, REPO, REPO_ROOT)[0] == "unreadable"
    recorder.state["rc"] = 4
    token, rc, _raw = rfo._read_review(1, REPO, REPO_ROOT)
    assert (token, rc) == ("norecord", 4)
    recorder.state["rc"] = 1
    assert rfo._read_review(1, REPO, REPO_ROOT)[0] == "fresh"
    recorder.state["rc"] = 3
    assert rfo._read_review(1, REPO, REPO_ROOT)[0] == "unreadable"


def test_a_parsed_stale_token_fails_even_at_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _FakeRecorder(monkeypatch)
    recorder.state.update(rc=0, stdout="fresh\tabc\tdef\n")
    token, rc, _raw = rfo._read_review(1, REPO, REPO_ROOT)
    assert (token, rc) == ("fresh", 0)
    assert (
        rfo.check_review(rfo.probe_from_dict(_fixture(review_token=token, review_exit=rc))).passed
        is False
    )


def test_absent_scripts_pointer_is_unreadable_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pointer is gitignored and materialised per machine; its absence must FAIL."""
    monkeypatch.setattr(rfo, "_review_script", lambda workdir: None)
    token, rc, raw = rfo._read_review(1, REPO, REPO_ROOT)
    assert token is None and rc is None
    assert "absent" in raw


def test_recorded_diff_is_passed_through_to_the_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-box reviews: the pair must reach `record-review.sh`'s own `--recorded-diff`."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout, cwd=None):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "carry\tabc\tabc\n", "")

    monkeypatch.setattr(rfo, "_review_script", lambda workdir: Path("/fake/record-review.sh"))
    monkeypatch.setattr(rfo, "_run", fake_run)
    token, rc, _raw = rfo._read_review(
        7, REPO, REPO_ROOT, recorded_diff="a" * 64, recorded_head="b" * 40
    )
    assert (token, rc) == ("carry", 0)
    assert seen and seen[0][-4:] == ["--recorded-diff", "a" * 64, "--recorded-head", "b" * 40]


# ── the READ LAYER: both round-1 defects lived here, and neither had a test ──


class _FakeGh:
    """A canned `gh` surface, keyed by substring of the endpoint."""

    def __init__(self, payloads: dict[str, Any], fail: str | None = None) -> None:
        self.payloads = payloads
        self.fail = fail
        self.reads: list[str] = []

    def _match(self, endpoint: str) -> Any:
        self.reads.append(endpoint)
        if self.fail and self.fail in endpoint:
            raise rfo.ReadError(f"gh api {endpoint}: HTTP 502 (transient)")
        # Ordered, substring-keyed, specific-before-general: the concrete ids in an endpoint
        # must not decide which canned payload answers it.
        for needle, value in self.payloads.items():
            if needle in endpoint:
                return value
        raise AssertionError(f"unexpected endpoint {endpoint}")

    def api(self, endpoint: str) -> Any:
        return self._match(endpoint)

    def pages(self, endpoint: str) -> list[Any]:
        value = self._match(endpoint)
        return value if isinstance(value, list) else [value]

    def pr(self, number: int) -> Mapping[str, Any]:
        return self._match("pulls/{n}")


def _gh_payloads(
    *, check_runs: list[dict[str, Any]] | None = None, body: str = ""
) -> dict[str, Any]:
    """Canned `gh` payloads, ordered SPECIFIC → GENERAL (first substring match wins)."""
    crs = (
        check_runs
        if check_runs is not None
        else [
            {
                "id": 1000 + i,
                "name": job,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "details_url": f"https://github.com/{REPO}/actions/runs/5/job/{1000 + i}",
                "started_at": "2026-11-04T11:40:00Z",
            }
            for i, job in enumerate(REQUIRED)
        ]
    )
    return {
        "/files?per_page": [
            {"filename": "tortoise/sdk.py"},
            {"filename": "tools/ready_for_owner.py"},
        ],
        "branches/main/protection": {
            "required_status_checks": {"checks": [{"context": c} for c in REQUIRED]}
        },
        "check-runs": {"total_count": len(crs), "check_runs": crs},
        "actions/runs": {
            "total_count": 1,
            "workflow_runs": [{"id": 5, "workflow_id": 7}],
        },
        "commits/": {"commit": {"committer": {"date": "2026-11-04T11:30:00Z"}}},
        "/comments": [],
        "/reviews": [],
        "issues/": [],
        "pulls/": {
            "head": {"sha": HEAD},
            "base": {"ref": "main"},
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "body": body,
        },
    }


def _collect(monkeypatch: pytest.MonkeyPatch, payloads: dict[str, Any], fail: str | None = None):
    fake = _FakeGh(payloads, fail=fail)
    monkeypatch.setattr(rfo, "Gh", lambda repo, **kw: fake)
    monkeypatch.setattr(rfo, "_read_tree", lambda gh, pr, head, workdir: (True, "merge-tree ok"))
    monkeypatch.setattr(rfo, "_read_review", lambda *a, **kw: ("head", 0, "token=head exit=0"))
    return rfo.collect_probe(10999, REPO, REPO_ROOT, now=NOW), fake


def test_collect_probe_wires_a_green_world_into_a_READY_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read layer, end to end: a green world must produce READY-FOR-OWNER."""
    probe = _collect(monkeypatch, _gh_payloads())[0]
    assert probe.required == tuple(sorted(REQUIRED))
    assert all(g.conclusion == "success" for g in probe.groups or ())
    verdict = rfo.evaluate(probe)
    assert verdict.ready is True, [(c.name, c.reason, c.raw) for c in verdict.checks]
    assert verdict.line() == f"READY-FOR-OWNER(10999, {HEAD})"


def test_collect_probe_marks_an_unreadable_declared_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUND-1 P1, at the layer where it lived: a 502 on the union read must not become
    `union=None` (which reads as "no union declared" and PASSES)."""
    payloads = _gh_payloads(body="READY-GATE UNION: #10901\n")
    # Only the UNION's sub-read fails (PR #10901), not this PR's own file list.
    probe = _collect(monkeypatch, payloads, fail="pulls/10901/files")[0]
    assert probe.union_unreadable is True
    verdict = rfo.evaluate(probe)
    coverage = next(c for c in verdict.checks if c.name == "coverage")
    assert coverage.passed is False
    assert coverage.unreadable is True
    assert verdict.ready is False


def test_collect_probe_scopes_the_union_to_the_named_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolvable union is compared against the diff — a subset must fail."""
    payloads = _gh_payloads(body="READY-GATE UNION: #10901\n")
    probe = _collect(monkeypatch, payloads)[0]
    assert probe.union_unreadable is False
    assert probe.union == ("tools/ready_for_owner.py", "tortoise/sdk.py")
    # The fixture's diff is exactly the union, so coverage passes…
    assert rfo.check_coverage(probe).passed is True, rfo.check_coverage(probe).reason


def test_read_coverage_on_real_paths_does_not_call_a_core_smoke_file_uncovered() -> None:
    """ROUND-1 P2, at the layer where it lived. Measured: `select([<tools/*.py>])` yields
    surfaces=[] and full=False but 72 tests — so it IS covered and must not fail."""
    uncovered, raw = rfo._read_coverage(
        ("tools/ready_for_owner.py", "tests/test_ready_for_owner.py")
    )
    assert uncovered == (), f"{uncovered} reported uncovered — got raw {raw}"
    assert "no-leg=0" in raw


def test_read_coverage_reports_an_unreadable_selection_as_None(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rfo, "_read_coverage", rfo._read_coverage)  # identity, keeps linters calm

    def boom(*a, **kw):
        raise RuntimeError("manifest unreadable")

    from tools import ci_selection

    monkeypatch.setattr(ci_selection, "load_manifest", boom)
    uncovered, raw = rfo._read_coverage(("tortoise/sdk.py",))
    assert uncovered is None
    assert "manifest unreadable" in raw


def test_mergeable_flag_normalisation_handles_rest_booleans() -> None:
    """REST types `mergeable` as a BOOLEAN; GraphQL as a string. `str(True)` is not
    `CONFLICTING`, so an unnormalised read silently disarms the conflicting arm."""
    assert rfo._mergeable_flag(True) == "MERGEABLE"
    assert rfo._mergeable_flag(False) == "CONFLICTING"
    assert rfo._mergeable_flag(None) == "UNKNOWN"
    assert rfo._mergeable_flag("conflicting") == "CONFLICTING"
    assert rfo._mergeable_flag("MERGEABLE") == "MERGEABLE"


def test_every_condition_is_evaluated_even_when_all_fail() -> None:
    verdict = _verdict(
        tree=False,
        required=None,
        review_token="fresh",
        review_exit=1,
        touched=None,
        comments=None,
        head_committed_at="2026-11-04T11:59:50Z",
        is_draft=True,
    )
    assert len(verdict.checks) == 6
    assert set(verdict.failed) == set(rfo.CONDITIONS)


# ── the read layer: rate limits are named, never mistaken for an empty surface ──


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return runner


def test_rate_limited_read_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(1, "", "gh: API rate limit exceeded for user (HTTP 403)"),
    )
    with pytest.raises(rfo.RateLimited) as excinfo:
        rfo.Gh(REPO).pages("pulls/1")
    assert "GH-RATE-LIMITED(" in str(excinfo.value)


def test_empty_body_is_a_read_failure_not_an_empty_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "   ", ""))
    with pytest.raises(rfo.ReadError):
        rfo.Gh(REPO).pages("pulls/1")


def test_gh_reads_are_cached_within_one_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, '{"id": 7}', "")

    monkeypatch.setattr(subprocess, "run", runner)
    gh = rfo.Gh(REPO)
    assert gh.api("pulls/1") == {"id": 7}
    assert gh.api("pulls/1") == {"id": 7}
    assert len(calls) == 1


# ── declared-union parsing ────────────────────────────────────────────────


def test_declared_union_line_is_parsed() -> None:
    entries, raw = rfo.declared_union("body\n\nREADY-GATE UNION: #10901, #10907\n")
    assert entries == ("10901", "10907")
    assert "entries=2" in raw


def test_absent_union_marker_declares_nothing() -> None:
    entries, raw = rfo.declared_union("Refs #1\n\nno union here")
    assert entries is None
    assert raw == "no declared union"


# ── THE MUTATION BATTERY ─────────────────────────────────────────────────
#
# Each mutant is a LAXER version of one condition — the defect that would ship if that
# condition were written naively. Every mode above is re-checked against its mutant: if
# the mutant makes the mode's check PASS, the mode's test would fail, i.e. the test KILLS
# the mutant. A mutant that survives means the matrix is decorative.


def _mutant_gates_cancelled_is_green(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_gates(probe)
    if real.passed:
        return real
    tokens = {t.split(":", 1)[0] for t in real.reason.split(",") if t}
    if tokens and tokens <= {"absent"}:
        return rfo.Check("gates", True, real.raw + " [MUTANT: cancelled is green]")
    return real


def _mutant_gates_in_flight_waits(probe: rfo.Probe) -> rfo.Check:
    """The 'it just hasn't finished, that's fine' defect."""
    real = rfo.check_gates(probe)
    if real.passed:
        return real
    tokens = {t.split(":", 1)[0] for t in real.reason.split(",") if t}
    if tokens and tokens <= {"in-flight"}:
        return rfo.Check("gates", True, real.raw + " [MUTANT: in-flight waits]")
    return real


def _mutant_gates_skipped_is_green(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_gates(probe)
    if real.passed:
        return real
    tokens = {t.split(":", 1)[0] for t in real.reason.split(",") if t}
    if tokens and tokens <= {"non-measuring"}:
        return rfo.Check("gates", True, real.raw + " [MUTANT: skipped/neutral is green]")
    return real


def _mutant_tree_trusts_the_flag(probe: rfo.Probe) -> rfo.Check:
    """The pre-existing defect: trust `mergeable`, never run merge-tree."""
    flag = (probe.mergeable or "").upper()
    if flag == "CONFLICTING":
        return rfo.Check("tree", False, f"mergeable={flag} [MUTANT]", "conflicting")
    return rfo.Check("tree", True, f"mergeable={flag or 'UNKNOWN'} [MUTANT: flag trusted]")


def _mutant_review_accepts_any_record(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_review(probe)
    if real.passed:
        return real
    if probe.review_token in {"fresh", "nodigest", "carry-patch", "norecord"}:
        return rfo.Check("review", True, real.raw + " [MUTANT: any record carries]")
    return real


def _mutant_exclusions_drops_head_age(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_exclusions(probe)
    if real.passed:
        return real
    tokens = {t.split(":")[0] for t in real.reason.split(",") if t}
    if tokens and tokens <= {"head-too-young"}:
        return rfo.Check("exclusions", True, real.raw + " [MUTANT: no settling bound]")
    return real


def _mutant_exclusions_trusts_the_flag(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_exclusions(probe)
    if real.passed:
        return real
    if (probe.mergeable or "").upper() != "CONFLICTING" and probe.tree is True:
        return rfo.Check("exclusions", True, real.raw + " [MUTANT]")
    return real


def _mutant_coverage_ignores_the_union(probe: rfo.Probe) -> rfo.Check:
    real = rfo.check_coverage(probe)
    if real.passed:
        return real
    if real.reason.startswith("hand-picked-subset"):
        return rfo.Check("coverage", True, real.raw + " [MUTANT: union ignored]")
    return real


def _mutant_blocking_any_clear_wins(probe: rfo.Probe) -> rfo.Check:
    """The 'a clean verdict exists somewhere, so we're fine' defect — order-blind."""
    real = rfo.check_blocking_finding(probe)
    if real.passed:
        return real
    bodies = [str(c.get("body") or "") for c in (probe.comments or ())]
    if any(rfo.CLEAR_RE.search(b) for b in bodies):
        return rfo.Check("blocking-finding", True, real.raw + " [MUTANT: order-blind]")
    return real


def _mutant_unreadable_is_pass(probe: rfo.Probe) -> rfo.Check:
    """The defect that makes a gate lie: an unreadable input defaults to green."""
    real = rfo.check_gates(probe)
    if real.unreadable:
        return rfo.Check("gates", True, real.raw + " [MUTANT: unreadable assumed]")
    return real


MUTANTS: dict[str, Any] = {
    "gates": _mutant_gates_cancelled_is_green,
    "gates-in-flight": _mutant_gates_in_flight_waits,
    "gates-skipped": _mutant_gates_skipped_is_green,
    "tree": _mutant_tree_trusts_the_flag,
    "review": _mutant_review_accepts_any_record,
    "exclusions": _mutant_exclusions_drops_head_age,
    "coverage": _mutant_coverage_ignores_the_union,
    "blocking": _mutant_blocking_any_clear_wins,
    "unreadable": _mutant_unreadable_is_pass,
}


@contextmanager
def _installed(name: str, mutant) -> Iterator[None]:
    """Install one mutant for the duration of the block, then restore the real gate."""
    condition = "blocking-finding" if name == "blocking" else name.split("-")[0]
    if name == "gates-in-flight" or name == "gates-skipped":
        condition = "gates"
    if name == "unreadable":
        condition = "gates"
    saved = rfo.EVALUATORS[condition]
    rfo.EVALUATORS[condition] = mutant
    try:
        yield
    finally:
        rfo.EVALUATORS[condition] = saved


def _cancelled_fixture() -> dict[str, Any]:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(
        REQUIRED[0], conclusion="cancelled", state=ci_verdict.GROUP_ABSENT, check_id=2000
    )
    return {"groups": groups}


def _in_flight_fixture() -> dict[str, Any]:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(
        REQUIRED[0],
        status="queued",
        conclusion=None,
        state=ci_verdict.GROUP_IN_FLIGHT,
        check_id=2000,
    )
    return {"groups": groups}


def _skipped_fixture() -> dict[str, Any]:
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(REQUIRED[0], conclusion="skipped", check_id=2000)
    return {"groups": groups}


def _dirty_fixture() -> dict[str, Any]:
    return {"tree": False, "tree_raw": "merge-tree CONFLICTS: sdk.py"}


def _stale_review_fixture() -> dict[str, Any]:
    return {"review_token": "fresh", "review_exit": 1}


def _young_head_fixture() -> dict[str, Any]:
    return {"head_committed_at": (NOW - timedelta(seconds=10)).isoformat()}


def _hand_picked_fixture() -> dict[str, Any]:
    return {
        "union": ["tortoise/sdk.py", "tools/ready_for_owner.py", "docs/ONTOLOGY.md"],
    }


def _open_blocking_fixture() -> dict[str, Any]:
    return {
        "comments": [
            {
                "id": 10,
                "created_at": "2026-11-04T11:40:00Z",
                "kind": "comment",
                "body": "NO ISSUES FOUND",
            },
            {
                "id": 11,
                "created_at": "2026-11-04T11:50:00Z",
                "kind": "comment",
                "body": "[P1] tortoise/sdk.py:412 — the fold drops the evidence",
            },
        ]
    }


def _unreadable_fixture() -> dict[str, Any]:
    return {"required": None, "required_raw": "branches/main/protection: 404"}


MUTATION_CASES = [
    ("gates", "cancelled required check", _cancelled_fixture),
    ("gates-in-flight", "in-flight required check", _in_flight_fixture),
    ("gates-skipped", "skipped required check", _skipped_fixture),
    ("tree", "dirty tree", _dirty_fixture),
    ("review", "stale review record", _stale_review_fixture),
    ("exclusions", "10-second-old head", _young_head_fixture),
    ("coverage", "hand-picked subset", _hand_picked_fixture),
    ("blocking", "open blocking finding", _open_blocking_fixture),
    ("unreadable", "unreadable required set", _unreadable_fixture),
]


@pytest.mark.parametrize(
    ("mutant_name", "label", "fixture_fn"),
    MUTATION_CASES,
    ids=[case[0] for case in MUTATION_CASES],
)
def test_each_mutation_is_killed(mutant_name: str, label: str, fixture_fn) -> None:
    """The mutant must make the failing mode PASS — which is exactly what its test forbids.

    Concretely: the mode's own test asserts ``check.passed is False``. Under the mutant
    the same assertion would raise AssertionError. Proving the mutation flips the value
    proves the test discriminates; a mutation that does NOT flip it is a survived mutant.
    """
    overrides = fixture_fn()
    condition = {
        "gates-in-flight": "gates",
        "gates-skipped": "gates",
        "unreadable": "gates",
        "blocking": "blocking-finding",
    }.get(mutant_name, mutant_name)

    real = _check(condition, **overrides)
    assert real.passed is False, f"{label}: the real gate already passed — the fixture is wrong"

    with _installed(mutant_name, MUTANTS[mutant_name]):
        mutant_check = _check(condition, **overrides)
    assert mutant_check.passed is True, (
        f"SURVIVED MUTANT: {label} — the mutant did not flip {condition} to PASS, so the "
        f"per-mode test does not discriminate. Mutant raw: {mutant_check.raw}"
    )
