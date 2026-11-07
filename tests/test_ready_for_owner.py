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
* a required **entry** check never scheduled          → NOT-READY   (``test_missing_*``)
  (a ``merge_conditions`` check is DEFERRED and PASSES **only** when no PR-triggered run
  of its emitting workflow is on the head —
  ``test_a_head_carrying_no_merge_time_check_is_READY_not_NOT_READY``; when its workflow
  DID run here, absence is ``MISSING`` and NOT-READY, because a ``needs``-gated aggregate
  is absent until its prerequisites finish —
  ``test_a_pending_merge_time_aggregate_is_a_FAIL_not_a_deferral``)
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

import dataclasses
import json
import os
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
from tools import required_set as rfo_required  # noqa: E402

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

#: The ENTRY/MERGE split `.mergify.yml` clause 1 fixes: `queue_conditions` gate entry
#: against the PR head, `merge_conditions` gate the merge and are re-evaluated by Mergify
#: on `mergify/merge-queue/<sha>`. `tools/required_set` derives BOTH from the config, so
#: the tool sees the split that a flat branch-protection payload cannot express.
MERGE_TIME_REQUIRED: tuple[str, ...] = ("python-ci-gate",)
ENTRY_REQUIRED: tuple[str, ...] = tuple(n for n in REQUIRED if n not in MERGE_TIME_REQUIRED)


def _check_runs_for(jobs: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Completed-success check-runs for `jobs`, in the canonical payload shape."""
    return [
        {
            "id": 1000 + i,
            "name": job,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
            "details_url": f"https://github.com/{REPO}/actions/runs/5/job/{1000 + i}",
            "started_at": "2026-11-04T11:40:00Z",
        }
        for i, job in enumerate(jobs)
    ]


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
        "labels": [],
        "pr_state": "open",
        "merged": False,
        "head_stable": True,
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
        "review_verdict": "clean",
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
    # An ENTRY context with no run at all on the head. `_assert_fails` builds a FLAT
    # pre-split fixture (`required` carries every name, `required_deferred` is `()`), so
    # `python-ci-gate` is treated as an entry check here — which is the only sense in which
    # this is "#10962's live shape".
    # ⛔ The LIVE head shape — zero `python-ci-gate` runs, with it being MERGE-TIME — is
    # DEFERRED and PASSES: see test_a_head_carrying_no_merge_time_check_is_READY_not_NOT_READY.
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


def test_an_unrecognised_group_state_cannot_fall_through_to_success() -> None:
    """★ `_gate_token`'s STATE whitelist is a fail-closed arm, and NOTHING pinned it.

    `probe_from_dict` copies a group's `state` verbatim, so a value outside
    `ci_verdict`'s four groups reaches `_gate_token` carrying whatever string the caller
    wrote — and this PR makes the arm load-bearing for a SECOND call site, the
    merge-time contexts, which previously never routed through it. If the arm is
    deleted, an unknown state falls through to the `conclusion` check below and PASSES
    on `conclusion=success` — the unrecognised-value-falls-through-the-gap defect the
    polarity rule forbids, and the module's own comment asserts it is closed.

    Measured on a `/tmp` copy: reverting the arm (returning `""` for every `state:`
    token) left all 320 tests green. So the guard the comment describes was pinned by
    nothing, and this test is that pin — for BOTH call sites.
    """
    # ENTRY context.
    groups = [_group(job, check_id=1000 + i) for i, job in enumerate(REQUIRED)]
    groups[0] = _group(REQUIRED[0], state="greenish", conclusion="success", check_id=2000)
    check = _assert_fails("gates", f"state:greenish:{REQUIRED[0]}", groups=groups)
    assert f"state:greenish:{REQUIRED[0]}" in check.reason, check.reason

    # MERGE-TIME context — the call site this PR adds. A green-looking aggregate whose
    # state this module has never seen is not a pass just because `conclusion` is green.
    fixture = _fixture_with_head_runs({"head_runs": []})
    fixture["groups"] = [
        *fixture["groups"],  # type: ignore[misc]
        _group(MERGE_TIME_REQUIRED[0], state="greenish", conclusion="success", check_id=9000),
    ]
    gates = next(c for c in rfo.evaluate(rfo.probe_from_dict(fixture)).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert f"state:greenish:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason


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


@pytest.mark.parametrize("value", ["dirty", "clean", 1, "true"])
def test_a_non_bool_tree_from_a_fixture_is_not_a_clean_tree(value: Any) -> None:
    """`probe.tree` is UNCOERCED from `--fixture` (`probe_from_dict`), so `not probe.tree`
    graded the STRING "dirty" as PASS — a fail-open in the one condition whose job is "the
    tree that will actually exist". Only the literal `True` is evidence of a clean tree.

    Mutation check: reverting `check_tree` to `if not probe.tree:` turns the `"dirty"`,
    `"clean"` and `1` arms green and fails this test.
    """
    check = _check("tree", tree=value)
    assert check.passed is False, f"tree={value!r} graded PASS"
    assert check.reason == "dirty"


def test_a_malformed_timeout_knob_warns_and_keeps_the_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`_env_int`'s own semantics: a malformed knob warns by name and takes the default.

    ⛔ This arm asserts the HELPER, and on its own it is UNFALSIFIABLE for the defect the fix
    exists for. Restoring `GH_TIMEOUT = int(os.environ.get(...))` at module level while
    keeping `_env_int` leaves this arm GREEN, because `monkeypatch.setenv` runs AFTER this
    module (and `tools.ready_for_owner`) is already imported and the knob is not ambient.
    Measured: `1 passed` on that mutation. `…_is_caught_in_a_FRESH_PROCESS` below is the arm
    that reds; this one covers the fallback semantics it cannot.
    """
    monkeypatch.setenv("READY_GATE_GH_TIMEOUT", "1m")
    assert rfo._env_int("READY_GATE_GH_TIMEOUT", 60) == 60
    err = capsys.readouterr().err
    assert "READY_GATE_GH_TIMEOUT" in err and "1m" in err, err
    # …and a well-formed knob still wins, so the fallback is not "ignore the environment".
    monkeypatch.setenv("READY_GATE_GH_TIMEOUT", "15")
    assert rfo._env_int("READY_GATE_GH_TIMEOUT", 60) == 15
    # an unset knob takes the default silently
    monkeypatch.delenv("READY_GATE_GH_TIMEOUT", raising=False)
    assert rfo._env_int("READY_GATE_GH_TIMEOUT", 60) == 60
    assert capsys.readouterr().err == ""


def test_a_malformed_timeout_knob_is_caught_in_a_FRESH_PROCESS() -> None:
    """THE property the fix exists for: the MODULE still imports — and therefore still reaches
    `main()` and prints its one verdict line — with malformed knobs in the ambient environment.

    Module-level `int(os.environ.get(...))` raised `ValueError` BEFORE `main()`, and `main()`'s
    "a read that is not a verdict must never become a traceback with NO line" backstop lives
    INSIDE `main()`, so it could not see it. Measured pre-fix:

        READY_GATE_GH_TIMEOUT=1m uv run python tools/ready_for_owner.py 1
          -> ValueError: invalid literal for int() with base 10: '1m'
          -> exit 1, NO verdict line

    Mutation check (this is the arm that reds): re-introducing the module-level parse makes
    `import tools.ready_for_owner` raise in the child, so `returncode != 0` and `Traceback`
    appears in stderr. A `monkeypatch.setenv` on the already-imported module cannot detect it.
    """
    env = {**os.environ, "READY_GATE_GH_TIMEOUT": "1m", "READY_GATE_GIT_TIMEOUT": "2m"}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tools.ready_for_owner as m; print(m.GH_TIMEOUT, m.GIT_TIMEOUT)",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stdout}{proc.stderr}"
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "60 180", proc.stdout
    # both knobs warned BY NAME, and neither was silently reinterpreted
    assert "READY_GATE_GH_TIMEOUT" in proc.stderr, proc.stderr
    assert "READY_GATE_GIT_TIMEOUT" in proc.stderr, proc.stderr


@pytest.mark.parametrize("value", ["false", "unstable", 0, 1])
def test_a_non_bool_head_stable_from_a_fixture_is_never_a_stable_head(value: Any) -> None:
    """`bool("false")` is `True`, so the old `bool()` coercion INVENTED a boolean: a payload
    that literally says the head did NOT stay put was graded a STABLE head and passed
    condition 5 (`if probe.head_stable is False`). Measured pre-fix:

        head_stable='false'    -> coerced True  -> passed=True reason=''
        head_stable='unstable' -> coerced True  -> passed=True reason=''

    A value that is not a real `bool` is now "not read" (`_tri_bool`), which fails CLOSED as
    `unreadable` — never as a pass, and never as a claim about the head. The `merged` and
    `is_draft` fields take the same coercion and previously failed closed only by accident
    (a string coerced to `True` reads as merged/draft); they are `_tri_bool` now too.

    Mutation check, measured with `head_stable` reverted to `bool(data["head_stable"])`:

        head_stable='false'    -> passed=True  reason=''          ← GREEN, says head did NOT stay
        head_stable='unstable' -> passed=True  reason=''          ← GREEN
        head_stable=1          -> passed=True  reason=''          ← GREEN
        head_stable=0          -> passed=False reason='head-moved-mid-read'   ← RED, WRONG reason

    ⛔ So three of the four params go GREEN and `0` does not: `bool(0) is False`, so the old
    coercion sends it down the OTHER branch and it reads as a moved head rather than as
    unreadable. That makes the `0` arm discriminated ONLY by the `unreadable`/`reason`
    assertions below — its `assert check.passed is False` already holds on the old code. Do not
    narrow this test to that assertion: doing so would leave `[0]` green under the old
    coercion, i.e. an unfalsifiable arm wearing a note that says it is covered.
    """
    check = _check("exclusions", head_stable=value)
    assert check.passed is False, f"head_stable={value!r} graded PASS"
    assert check.unreadable is True, check.reason
    assert check.reason == "unreadable", check.reason


def test_a_real_bool_head_stable_is_still_graded_by_its_value() -> None:
    """The strict coercion must not turn the two REAL answers into `unreadable`."""
    assert _check("exclusions", head_stable=True).passed is True
    moved = _assert_fails("exclusions", "head-moved-mid-read", head_stable=False)
    assert moved.passed is False


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


def test_a_queue_blocking_label_is_not_ready() -> None:
    """`.mergify.yml`'s `queue_conditions` carry ONE non-check entry: `-label=superseded-by-fold`.

    `tools/batch_open.py::label_only()` leaves fold constituents OPEN and labelled, and they
    are green + reviewed + old by construction. The owner's UI merge bypasses Mergify, so
    without this arm a constituent the queue REFUSES at entry would read READY-FOR-OWNER and
    land separately — the exact outcome `.mergify.yml`'s own note records.
    """
    check = _assert_fails(
        "exclusions", "queue-blocking-label:superseded-by-fold", labels=["superseded-by-fold"]
    )
    assert "superseded-by-fold" in check.raw


def test_an_unrelated_label_does_not_block() -> None:
    assert _check("exclusions", labels=["deploy:staging", "P1-severe"]).passed is True


def test_unreadable_labels_are_unreadable() -> None:
    check = _check("exclusions", labels=None)
    assert check.passed is False
    assert check.unreadable is True


@pytest.mark.parametrize(("state", "merged"), [("closed", True), ("closed", False)])
def test_a_closed_or_merged_pr_is_not_ready(state: str, merged: bool) -> None:
    """Measured: #10916 (merged) printed READY-FOR-OWNER before this arm existed — a tab a
    loop would open on a PR that cannot be merged."""
    check = _assert_fails("exclusions", "not-open:", pr_state=state, merged=merged)
    assert f"state={state}" in check.raw


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


def test_an_extra_undeclared_file_cannot_switch_the_union_arm_off() -> None:
    """⛔ ONE extra file must not turn an ABSENT declared constituent into a PASS.

    The arm shipped as `touched < declared`, a PROPER-subset test: it fires only when the
    diff contains nothing outside the declaration, so a fold that DROPPED a declared
    constituent and ADDED a registrar file (`config/ci-surfaces.yml`, `MEMORY.md` — exactly
    what a fold writes) read `PASS union=declared(3)` here with `docs/ONTOLOGY.md` never
    named, i.e. the checks validated less than the PR claims.

    Measured pre-fix with THIS fixture: `Check("coverage", True, "files=3 …
    union=declared(3)")`, `reason=""`. Post-fix the difference decides, so the missing file
    is named and the arm fails.
    """
    check = _assert_fails(
        "coverage",
        "hand-picked-subset:1",
        union=["tortoise/sdk.py", "tools/ready_for_owner.py", "docs/ONTOLOGY.md"],
        touched=["tortoise/sdk.py", "tools/ready_for_owner.py", "config/ci-surfaces.yml"],
        touched_raw="files=3",
    )
    assert "missing=['docs/ONTOLOGY.md']" in check.raw, check.raw
    assert "config/ci-surfaces.yml" not in check.reason


def test_a_fully_covered_declared_union_with_extra_files_passes() -> None:
    """The other direction: extra files ALONGSIDE a fully delivered union are fine.

    The fix must not red an ordinary fold that writes a registrar file it did not declare —
    only a MISSING declared file is the defect.
    """
    check = _check(
        "coverage",
        union=["tortoise/sdk.py", "tools/ready_for_owner.py"],
        touched=["tortoise/sdk.py", "tools/ready_for_owner.py", "config/ci-surfaces.yml"],
        touched_raw="files=3",
    )
    assert check.passed is True, (check.reason, check.raw)
    assert "union=declared(2)" in check.raw


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

    ⛔ A docs-only path is the example, not `tools/ready_for_owner.py`: #10975 put that tool in
    `ci_selection.TOOL_CARVEOUTS`, so `select(["tools/ready_for_owner.py"])` now returns the
    FULL matrix and no longer illustrates this shape. Measured:
    `select(["docs/ontology/NOTES.md"])` -> surfaces=[], full=False, tier-1 smoke only.
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


def test_a_status_comment_that_only_CITES_a_clean_verdict_cannot_close_an_open_P1() -> None:
    """A QUOTED verdict is not a verdict — the live false PASS, as it was posted.

    Reproduced from #10929 comment `6636540079` (*"## Merge blocked on runner capacity — the
    exact mechanism, measured"*), whose own text reads *"Six fresh-context review rounds; the
    last returned **`NO ISSUES FOUND`** (comment above)."* `CLEAR_RE` matches the bare phrase
    anywhere, so the status comment set `last_clear` and erased the open P1 posted before it:
    condition 6 returned PASS over an open blocker, and the owner's tab said READY-FOR-OWNER.

    Revert red: put `CLEAR_RE.search(text)` back at the clear test in
    `check_blocking_finding` — this arm then reports PASS and the assertion below fails, while
    every other arm in this file stays green (which is why the defect survived review rounds
    that read this condition's corpus).
    """
    comments = [
        {
            "id": 1,
            "created_at": "2026-11-05T09:00:00Z",
            "kind": "review",
            "body": "Outcome: NOT CLEAN — 1 open P1: the contents probe is missing.",
        },
        {
            "id": 2,
            "created_at": "2026-11-05T10:00:00Z",
            "kind": "comment",
            "body": (
                "## Merge blocked on runner capacity\n\n"
                "Six fresh-context review rounds; the last returned "
                "**`NO ISSUES FOUND`** (comment above)."
            ),
        },
    ]
    check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
    assert "open=review:1" in check.raw, check.raw
    # The citation must not even count as a CLEAR, so no `last-clear` was recorded for it.
    assert "last-clear=-" in check.raw, check.raw


def test_the_clear_guard_grades_both_directions_on_its_own() -> None:
    """The severity exemptions are only reachable on `asserts_a_clear` itself.

    `check_blocking_finding` OR's `_grades_as_blocking` with the clear test and evaluates the
    finding arm FIRST, so a body that names a severity is a finding however clean its verdict
    is — `Reviewer verdict: NO ISSUES FOUND. The two P2 findings are closed.` can never reach
    the clear test through the check. The completion/negation exemptions on the severity arm
    therefore need a direct probe, or they are unexercised code.
    """
    # negative → the exemptions stand the severity arm down, and nothing else retracts
    for body in (
        "Reviewer verdict: NO ISSUES FOUND. The two P2 findings are closed.",
        "Outcome: CLEAN — no P0/P1/P2 findings; three P3 nits noted.",
        # the COMPLETION spelling: `been` and intervening adverbs are not optional, or the two
        # most natural completions there are become a permanent false NOT-READY (#10962)
        "Verdict: CLEAN. The P2 has been addressed.",
        "Reviewer verdict: NO ISSUES FOUND — the P1 and the P2 are both addressed now.",
        "Verdict: CLEAN — the P1 from cycle 3 is gone.",
        "Verdict: CLEAN — no P1 outstanding, all P2s closed.",
        # J — A BLANK LINE MUST NOT WIDEN THE HOLE THE LINE BREAK USED TO HAVE (cycle 5,
        # finding 1). These are the SAME sentence as the retraction cases below with one
        # extra newline; before the fix they CLEARED, because `find("\n\n", end)` landed
        # ON `end` and the paragraph window was empty. The other direction is pinned too,
        # or the fix is just an over-block: a standalone verdict followed by a blank line
        # and a REAL completion is still a clear.
        "**Verdict: CLEAN.**\n\nFixed the P1 in 4f450ae81.",
        "**Verdict: CLEAN.**\n\nThe two P2 findings are closed.",
        "**Verdict: CLEAN**\n\nno P0/P1/P2 findings; three P3 nits noted.",
        # ⛔ THE TWO SHAPES THE CROSSING MUST NOT CATCH — they are the whole reason the
        # detached paragraph drops rule (3) instead of carrying the full rule set across.
        # Both CLEARED before the window crossed a blank line and must still clear after.
        "**Verdict: CLEAN.**\n\nFixed the P1 in 4f450ae81; no other files changed except the fixture.",
        "**Verdict: CLEAN.**\n\nAll six findings are closed. Nothing else changed except AGENTS.md.",
        # ⛔ KNOWN RESIDUAL, pinned as a DECISION not an accident: rule (3)'s exception words
        # are graded only in the verdict's own paragraph, so an exception alone one paragraph
        # down clears. Stated in `asserts_a_clear`'s docstring; this arm makes the trade
        # visible, so a later tightening has to come here and change a test on purpose.
        "**Verdict: CLEAN.**\n\nExcept the concern raised in cycle 4.",
        # ⛔ CYCLE 7, FINDING 2 — real clears this guard REFUSED at the previous head: a
        # coordinated completion (`the P1 and the P2`), a negated LIST with `or`, and the
        # qualifier `new`. Each is an over-block (#10962 direction), not a fail-open.
        "**Verdict: CLEAN.**\n\nI fixed the P1 and the P2 in 4f450ae81.",
        "**Verdict: CLEAN.**\n\nNo P0, P1 or P2 findings remain.",
        "**Verdict: CLEAN.**\n\nNo new P1 findings; the fold is clean.",
        "Verdict: CLEAN — no P1 outstanding, all P2s closed.",
    ):
        assert rfo.asserts_a_clear(body) is True, body
    # …and standing the severity arm down does NOT stand down the retraction rules (1)–(3)
    for body in (
        "**Verdict: CLEAN.** No blocking findings; the P1 remains open.",
        "verdict: CLEAN, apart from the P2 in the guard.",
        "**Verdict: CLEAN.** No P1 — the previous head is stale.",
    ):
        assert rfo.asserts_a_clear(body) is False, body


def test_a_verdict_HEADER_still_closes_a_finding() -> None:
    """…and the fix must not over-correct: a verdict SHAPE still clears (the #10962 direction).

    Requiring the clear to stand as a verdict is the sharp edge: reading only a bare
    `^NO ISSUES FOUND` would refuse every body whose verdict is introduced by its own header,
    which is the fleet's dominant format — measured on #10962, where two clean verdicts in that
    shape left `last-clear=-` and a PR with a long-since-fixed finding could NEVER be certified.
    """
    for body in (
        "## Fresh-context review — NO ISSUES FOUND at `ab60fa6a3`",
        "**NO ISSUES FOUND**",
        "Reviewer verdict: NO ISSUES FOUND",
        "Re-review: NO ISSUES FOUND at the new head.",
        "> NO ISSUES FOUND",
        # ⛔ THE GUARD MUST NOT OVER-CORRECT — these are CLEARS, and each was wrongly refused
        # by an earlier draft of the retraction guard. A P3 is not a blocker, so a bare
        # CONTRASTIVE (`but`/`though`/`yet`) may not reject; a NEGATED severity list is the
        # clear spelling; and a COMPLETION after the severity means the finding is gone.
        "Verdict: CLEAN, but three P3 nits recorded.",
        "Verdict: CLEAN though I noted two P3 nits.",
        "Verdict: CLEAN, yet 2 P3 nits remain.",
        "Outcome: CLEAN — no P0/P1/P2 findings; three P3 nits noted.",
        "**Verdict: CLEAN — no blocking issue found.**",
        "**Verdict: CLEAN.**\nFixed the P1 in 4f450ae81.",
        "**Outcome: CLEAN.** remaining: 1 P1 was fixed and verified.",
    ):
        comments = [
            {
                "id": 30,
                "created_at": "2026-11-04T11:40:00Z",
                "kind": "review",
                "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
            },
            {
                "id": 31,
                "created_at": "2026-11-04T11:55:00Z",
                "kind": "comment",
                "body": body,
            },
        ]
        check = _check("blocking-finding", comments=comments)
        assert check.passed is True, f"{body!r} did not clear: {check.raw}"
        assert "last-clear=31" in check.raw, check.raw


def test_a_verdict_that_is_RETRACTED_in_the_same_sentence_cannot_close_a_finding() -> None:
    """The cue SHAPE is satisfied by prose about a verdict — the clause after it decides.

    `CLEAR_VERDICT_RE`'s cue arm asks only for `verdict: CLEAN`, so a status comment that
    NEGATES or SUPERSEDES a past verdict matched it and erased the open P1 — the same false
    PASS as the citation arm, one door over, and NEWLY reachable because none of these bodies
    matched the pre-cue `CLEAR_RE`. Case D is verbatim fleet prose (#10938's status comment);
    case E is the artifact class this whole condition exists for (a status comment about a
    stale record).

    Revert red: neutralise `_CLEAR_RETRACTED_RE` (make `asserts_a_clear` a bare
    `CLEAR_VERDICT_RE.search`) and every case below turns `passed=True` with
    `last-clear=2`, while `test_a_verdict_HEADER_still_closes_a_finding` stays green — which
    is exactly why the retraction needs its own corpus rather than a comment.
    """
    for body in (
        # A — negation of the old verdict
        "Needs another round — the previous verdict: CLEAN no longer holds.",
        # B — supersession
        "The earlier verdict: CLEAN is superseded by this comment.",
        # C — contrast, then the reason it is stale
        "Previous review: CLEAN, but that predates the fix request below.",
        # D — a COMPOUND ADJECTIVE, verbatim fleet prose (#10938)
        "…(`efe6da47d` is that branch's reviewed-clean head).",
        # E — a status comment ABOUT a stale record
        "The record at `4f450ae81` says verdict: clean, but the head has moved.",
        # F1–F3 — PUNCTUATION between the verdict and its retraction. An earlier draft
        # anchored the guard at the byte after the phrase, so `—`, `.`, `;`, `:` or a closing
        # `**` made the retraction invisible; all three cleared an open P1.
        "**Verdict: CLEAN** — though only before the last three commits.",
        "**Verdict: CLEAN.** That was the previous head; the P1 below is unfixed.",
        "verdict: CLEAN - no longer holds after the P1.",
        "review: CLEAN; that assessment is now stale.",
        # F4/F15 — a live EXCEPTION to the clear is not a clear
        "**Verdict: NO ISSUES FOUND** — except the P1 below.",
        "verdict: CLEAN, apart from the P2 in the guard.",
        # F5/F6/F10/F14 — the verdict is ATTRIBUTED to another artifact, and may carry NO
        # suffix at all (`Previous review: CLEAN.`), so the preamble is the only signal.
        "Previous review: CLEAN.",
        "the previous verdict: CLEAN (superseded below)",
        "**Verdict: CLEAN** was returned at the previous head.",
        "Previous round: NO ISSUES FOUND",
        # F7/F9 — a conditional clear
        "outcome: CLEAN, subject to the open P1",
        "conclusion: CLEAN unless the P1 is unaddressed",
        # F12 — a reported verdict plus a live severity
        "the last review concluded: NO ISSUES FOUND, but a P1 landed since.",
        # F13 — the negation word sits on the FINDING, not on the severity token
        "**Verdict: CLEAN.** No blocking findings; the P1 remains open.",
        # G — ONE `no` ANYWHERE used to stand the whole severity arm down, so any sentence
        # containing it exempted a live P1 (cycle 4 finding 1). The exemption must GOVERN the
        # token it exempts.
        "**Verdict: CLEAN** — there is no evidence the P1 was resolved.",
        "Verdict: CLEAN. However, no one has addressed the P1 from cycle 3.",
        "Verdict: CLEAN from the reviewer, but I found no test for the P1.",
        "Verdict: CLEAN; the earlier P2 was fixed, but the P1 is still there.",
        "verdict: CLEAN — a BLOCKER was filed afterwards.",
        # H — the preamble arm was a CLOSED VERB LIST behind an arbitrary 90-char cap: the same
        # `returned` verb stopped being in the window once a clause sat between it and the
        # verdict, and `stated`/`recorded`/`asserted`/`According to`/`review's` were never in it.
        "The reviewer returned a long list of notes on naming, documentation, coverage and "
        "hygiene, and after all of that the verdict: CLEAN.",
        "According to the review at 4f450ae81, verdict: CLEAN.",
        "The review's verdict: CLEAN.",
        "The reviewer stated the verdict: CLEAN at 4f450ae81.",
        "Cycle 3 recorded verdict: CLEAN there.",
        "The author asserted verdict: CLEAN.",
        # I — A LINE BREAK is not a sentence boundary on screen. The rules that can see a
        # retraction are graded over the PARAGRAPH; only the bare-severity arm stays line-scoped.
        "**Verdict: CLEAN**\n— except the open P1**",
        "**Verdict: CLEAN.**\nThat was the previous head; the P1 below is unfixed.",
        "verdict: CLEAN\r\nno longer holds",
        "verdict: CLEAN\nthe head has since moved",
        # J — the BLANK-LINE spelling of the four cases above (cycle 5, finding 1). Each is
        # the identical sentence with one extra newline, and each CLEARED until the window
        # learned to look past a standalone verdict's own blank line.
        "**Verdict: CLEAN**\n\n— except the open P1",
        "**Verdict: CLEAN**\n\nThe head has since moved.",
        "**Verdict: CLEAN**\n\nThis no longer holds.",
        "**Verdict: CLEAN**\n\nThe P1 is still open.",
        "verdict: CLEAN\r\n\r\nno longer holds",
        "**Verdict: CLEAN** — cycle 4.\n\nExcept the open P1 below.",
        "**Verdict: CLEAN** (cycle 5).\n\n— except the open P1",
        "**Verdict: CLEAN** — cycle 4.\n\nThe P1 below is still open.",
        # ⛔ CYCLE 7, FINDING 1 — a BLANK LINE IS A WHITESPACE-ONLY LINE, not `\n\n`. Each of
        # these CLEARED at the previous head because `find("\n\n", …)` missed the boundary
        # entirely and the severity arm was never reached: one trailing space, one tab, an
        # NBSP, or a CRLF pair defeated the whole guard.
        "**Verdict: CLEAN.**\n \nThe rail has an unresolved P1 in check_gates.",
        "**Verdict: CLEAN.**\n  \nThe rail has an unresolved P1 in check_gates.",
        "**Verdict: CLEAN.**\n\t\nThe rail has an unresolved P1 in check_gates.",
        "**Verdict: CLEAN.**\n\xa0\nThe rail has an unresolved P1 in check_gates.",
        "**Verdict: CLEAN.**\r\n\r\nThe rail has an unresolved P1 in check_gates.",
        # ⛔ CYCLE 7, FINDING 3 — the inverted gradient. The severity arm was graded per LINE
        # and the word rules per PARAGRAPH, so a live P1 on the NEXT LINE was invisible while
        # the same sentence a paragraph down blocked, and the window covered exactly one
        # paragraph so a fourth newline was a cliff. The severity arm is now graded over the
        # whole suffix and the word arm to the end of the body.
        "**Verdict: CLEAN.**\nI count one P1 in the rail.",
        "**Verdict: CLEAN.**\n\nFine.\n\nI count one P1 in the rail.",
        "**Verdict: CLEAN.**\n\nFine.\n\n\nI count one P1 in the rail.",
        # ⛔ CYCLE 7, FINDING 4 (the PLURAL) is pinned in `test_the_severity_arm_reads_the_PLURAL`
        # instead of here: a body whose ONLY fault is a plural severity token (`P1s: 2`,
        # `P1s remain open.`) is ALSO read as a blocking-finding body by `_grades_as_blocking`,
        # so end to end the two arms are indistinguishable and the raw shape this loop asserts
        # on would be ambiguous. The guard's own behaviour is what that arm measures.
        # …and a coordinated completion must not leak ACROSS a contrastive onto a live token
        "Verdict: CLEAN; the earlier P2 was fixed, but the P1 is still there.",
    ):
        comments = [
            {
                "id": 1,
                "created_at": "2026-11-05T09:00:00Z",
                "kind": "review",
                "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
            },
            {
                "id": 2,
                "created_at": "2026-11-05T10:00:00Z",
                "kind": "comment",
                "body": body,
            },
        ]
        check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
        assert "open=review:1" in check.raw, f"{body!r}: {check.raw}"
        assert "last-clear=-" in check.raw, f"{body!r} counted as a CLEAR: {check.raw}"


def test_ordinary_prose_about_the_review_does_not_retract_its_own_clear() -> None:
    r"""REVIEW #10975 (P1): a clean review's OWN prose retracted its verdict.

    `_R1_NO_LONGER` carried a BARE copula negation (`(?:is|was|are|were)\s+(?:now\s+)?not\b`)
    graded over the rest of the body, and rule (1b) carried a bare `test` noun. So ordinary
    English about something other than the finding — `the pin is not vacuous`, `this is not a
    blocking problem`, `no test suite was executed` — matched, the clear was graded RETRACTED,
    and condition 6 could NEVER close an open finding. That is the permanent false NOT-READY
    class #10962 exists to prevent, re-created one level down, and it was measured on this
    PR's own thread: all 6 real non-blocking clean comments graded `last-clear=-`.

    Each body below is followed by a real `[P1]`, so a passing assertion means the comment
    COUNTED AS A CLEAR — which is the direction that was broken.
    """
    for body in (
        "**Verdict: CLEAN.**\n\nThe guard is sound; the pin is not vacuous.",
        "**Verdict: CLEAN.** No issues found. This is not a blocking problem.",
        "**Verdict: CLEAN.**\n\nReviewer is read-only: no test suite was executed.",
        "**Verdict: CLEAN.**\n\nReviewer is read-only: no tests were run on this box.",
        "NO ISSUES FOUND\n\nThe reviewer is read-only: no test suite was executed.",
    ):
        comments = [
            {
                "id": 1,
                "created_at": "2026-11-05T09:00:00Z",
                "kind": "review",
                "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
            },
            {
                "id": 2,
                "created_at": "2026-11-05T10:00:00Z",
                "kind": "comment",
                "body": body,
            },
        ]
        check = _check("blocking-finding", comments=comments)
        assert check.passed is True, f"{body!r} was retracted: {check.raw}"
        assert "last-clear=2" in check.raw, f"{body!r}: {check.raw}"


def test_the_narrowed_copula_still_retracts_when_the_negation_is_about_the_finding() -> None:
    """The other half of the trade above: scoping the copula to a DISPOSITION predicate must
    not turn `the P1 is not resolved` into a clear. A false PASS here is the failure this
    whole guard exists to stop, so the narrowing is pinned in both directions."""
    for body in (
        "**Verdict: CLEAN.**\n\nThe P1 is not resolved.",
        "**Verdict: CLEAN.**\n\nThe finding is not fixed.",
        "**Verdict: CLEAN.**\n\nThat condition is not satisfied.",
        "**Verdict: CLEAN.**\n\nThis is not a clear.",
        "**Verdict: CLEAN.**\n\nI found no test for the P1.",
        "**Verdict: CLEAN.**\n\nThere is no evidence the P1 was resolved.",
        "**Verdict: CLEAN.**\n\nNo test exercises the P1 path.",
    ):
        comments = [
            {
                "id": 1,
                "created_at": "2026-11-05T09:00:00Z",
                "kind": "review",
                "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
            },
            {
                "id": 2,
                "created_at": "2026-11-05T10:00:00Z",
                "kind": "comment",
                "body": body,
            },
        ]
        check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
        assert "last-clear=-" in check.raw, f"{body!r} counted as a CLEAR: {check.raw}"


def test_the_no_test_exemption_is_the_instrument_never_the_finding() -> None:
    """`no test` keeps its FAIL-CLOSED default; only prose about the REVIEWER is exempt.

    A first attempt at this narrowing replaced the bare noun with a whitelist of
    finding-referring forms, which made `No test pins it.` a CLEAR — a fail-open on a coverage
    note, the direction this file refuses. The exemption is now a blacklist of three nouns
    (`suite|run|execution`) and a handful of auxiliaries, i.e. the REVIEWER'S OWN ACTIVITY:
    `no test suite was executed` is not a fact about the diff. Everything else still retracts,
    so an omission over-blocks instead of passing a live finding.
    """
    blocks = (
        # the instrument — must CLEAR
        "**Verdict: CLEAN.**\n\nReviewer is read-only: no test suite was executed.",
        "**Verdict: CLEAN.**\n\nReviewer is read-only: no test was executed here.",
        "**Verdict: CLEAN.**\n\nNo test run was possible on this box.",
    )
    for body in blocks:
        comments = _finding_then(body)
        assert _check("blocking-finding", comments=comments).passed is True, body
    # …and anything that is a statement about the CODE keeps retracting, including the
    # token-less spelling the whitelist attempt let through.
    retracts = (
        "**Verdict: CLEAN.** No test pins it, but nothing blocking.",
        "**Verdict: CLEAN.** There is no test that covers the P1.",
        "**Verdict: CLEAN.** I found no test of the P1 path.",
        "**Verdict: CLEAN.** No test exercises the P1 path.",
        "**Verdict: CLEAN.**\n\nThe change is not exercised end to end.",
    )
    for body in retracts:
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))


def _finding_then(body: str) -> list[dict]:
    """An open `[P1]` followed by a candidate clear — the only shape condition 6 grades."""
    return [
        {
            "id": 1,
            "created_at": "2026-11-05T09:00:00Z",
            "kind": "review",
            "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
        },
        {"id": 2, "created_at": "2026-11-05T10:00:00Z", "kind": "comment", "body": body},
    ]


def test_rule4_word_arm_is_never_windowed() -> None:
    """REVIEW #10975 CYCLE 3 (P0 I introduced): bounding the window dropped rule (4)'s closed
    finding-state vocabulary, so a body that opens with a clear verdict and then says
    `that path is unfixed` CLOSED a live P1.

    This was a NEW fail-open, not a pre-existing gap. The sharpest form NAMES a severity: the
    completion `Fixed the …` stands the per-token arm down (correctly — a completion governs
    the token), leaving rule (4)'s word as the only arm that could see the regression, and the
    window had moved it out of reach.

    `unfixed` / `unaddressed` / `still open` / `remains open` / `not fixed` is a CLOSED
    vocabulary of finding-state words, so it is graded over the WHOLE suffix. What may be
    windowed is only the subject-ambiguous half — bare `no longer` (real code prose:
    "`lane_key` can no longer raise"), the bare `stale|superseded|…` words, and rule (2)'s
    attribution.
    """
    for body in (
        "**Verdict: CLEAN.**\n\nGood.\n\nAlso good.\n\nThat path is unfixed.",
        "**Verdict: CLEAN.**\n\nGood.\n\nAlso good.\n\nThe guard is still open.",
        "**Verdict: CLEAN.**\n\nRead the diff.\n\nFixed the P1 at 4f450ae81 — but the "
        "regression is back and that path is unfixed.",
        "**Verdict: CLEAN.**\n\nRead the diff.\n\nI fixed the P1 in 4f450ae81.\n\nOn "
        "re-read, my verdict no longer holds.",
    ):
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))
    # …and the SUBJECT-AMBIGUOUS half really is still windowed, or the fix would just be the
    # old over-block back again: real code prose mentioning `no longer` deep in a clean review
    # must not retract that review's verdict.
    body = (
        "**Verdict: CLEAN.** No blocking finding.\n\n"
        "### Verified clean\n\nThe stored direction is a string, so `lane_key` can no longer "
        "raise on the scalar branch.\n"
    )
    check = _check("blocking-finding", comments=_finding_then(body))
    assert check.passed is True, f"code prose retracted a clean review: {check.raw}"


def test_the_no_test_exemption_grades_the_phrase_not_the_noun() -> None:
    """REVIEW #10975 CYCLE 3 (P2): keying the `no test` exemption on the noun `suite|run`
    exempted statements about the CODE, which is the opposite subject — `No test suite covers
    the guard path.` cleared. The exemption now needs an EXECUTION predicate after the noun."""
    for body in (
        "**Verdict: CLEAN.**\n\nNo test suite covers the guard path.",
        "**Verdict: CLEAN.**\n\nNo test has been written for the guard.",
        "**Verdict: CLEAN.**\n\nNo test could catch this path.",
        "**Verdict: CLEAN.**\n\nNo test run covers the guard.",
    ):
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))


def test_the_retraction_layer_stops_at_the_next_paragraph_not_the_whole_body() -> None:
    """REVIEW #10975 (P1, the measured one): the non-severity layer was graded TO THE END of
    the body, so any later `stale` / `superseded` / `not` anywhere in a 180-line review
    retracted the verdict of the review that wrote it.

    Measured on this PR's own 54 comments before the fix: **11 of the 14 real clean review
    comments graded `last-clear=-`**, with the trigger thousands of characters downstream. A
    real review mentions that a RECORD is stale, that a test is not exercised, that a rung was
    not taken — none of which retracts its verdict.
    """
    long_review = (
        "**Verdict: CLEAN.** No blocking finding.\n\n"
        "### What I read\n\n"
        "Every changed file in full at the head.\n\n"
        "### Verified clean\n\n"
        "The registry validators pass.\n\n"
        "### Notes\n\n"
        "The previous record for this branch is stale, and the positional argument swap is "
        "not exercised by the synthetic cases. Neither is blocking.\n"
    )
    comments = _finding_then(long_review)
    check = _check("blocking-finding", comments=comments)
    assert check.passed is True, f"a long clean review still retracted itself: {check.raw}"
    assert "last-clear=2" in check.raw, check.raw
    # …and the CLIFF the old comment worried about is still covered, by the SEVERITY arm,
    # which is deliberately never windowed: a body naming a P1 blocks at any paragraph depth.
    for depth in (1, 2, 4):
        body = "**Verdict: CLEAN.**" + ("\n\nFine." * depth) + "\n\nI count one P1 in the rail."
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))
    # …and a retraction sitting NEXT TO the verdict still retracts, at both line and paragraph
    # distance — the window is two paragraphs, not zero.
    for body in (
        "**Verdict: CLEAN.** The P1 is not resolved.",
        "**Verdict: CLEAN.**\n\nThe P1 is not resolved.",
        "**Verdict: CLEAN.**\n\nThe fix is not correct.",
    ):
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))


def test_the_copula_exemption_does_not_disable_the_arm() -> None:
    """The fail-open a WHITELIST of predicates introduced, pinned so it cannot come back.

    An earlier attempt in this lane scoped the copula to `fixed|resolved|addressed|…`. That is
    fail-OPEN by construction — any predicate not in the list stopped retracting — and it
    measurably cleared all of the bodies below over an open P1 (9 of them, including one that
    NAMED `P1` and stood the severity arm down through `was fixed`).

    The exemption is now a BLACKLIST of `is not <badness>` (vacuous, a blocking, a problem, a
    finding, a result, a defect, a concern, a regression, an issue, an error, a showstopper),
    so everything here keeps retracting and an omission costs an over-block, not a false PASS.
    """
    for body in (
        "**Verdict: CLEAN.**\n\nThe P1 was fixed, but the fix is not correct.",
        "**Verdict: CLEAN.**\n\nThe P1 was fixed; the guard is not safe.",
        "**Verdict: CLEAN.**\n\nNo P1 outstanding — but the guard is not safe.",
        "**Verdict: CLEAN.**\n\nThe fix is not correct.",
        "**Verdict: CLEAN.**\n\nOne caveat: the new test is not exercised in CI.",
        "**Verdict: CLEAN.**\n\nThe path is not covered by a test.",
        "**Verdict: CLEAN.**\n\nThe change is not tested.",
        "**Verdict: CLEAN.**\n\nThe change is not acceptable.",
        "**Verdict: CLEAN.**\n\nThe change is not robust.",
        "**Verdict: CLEAN.**\n\nThis is not a clear.",
        "**Verdict: CLEAN.**\n\nNo test suite was executed, but the guard is not safe.",
        "**Verdict: CLEAN.**\n\nThe pin is not vacuous; however the fix is not correct.",
    ):
        _assert_fails("blocking-finding", "open-blocking-finding", comments=_finding_then(body))
    # …and the exemption itself works, at the distance a real review writes it.
    for body in (
        "**Verdict: CLEAN.**\n\nThe guard is sound; the pin is not vacuous.",
        "**Verdict: CLEAN.** No issues found. This is not a blocking problem.",
    ):
        check = _check("blocking-finding", comments=_finding_then(body))
        assert check.passed is True, f"{body!r}: {check.raw}"


def test_a_code_formatted_verdict_is_a_verdict() -> None:
    """REVIEW #10975 (P2): the cue arm's separator class omitted the backtick, so a
    code-formatted verdict read NEUTRAL and could never close a finding.

    Both bodies below are VERBATIM from real reviews on this PR, and they are 2 of the 6
    clean verdicts on it — roughly 1 in 3 of the fleet's. The backtick is added to the CUE arm
    only: the two line-start arms must keep refusing the cited form, which is
    `test_a_quoted_verdict_is_not_a_verdict`'s pin.
    """
    for body in (
        "## Fresh-context review — `NO ISSUES FOUND` at `ab60fa6a3`",
        "### Verdict\n\n`CLEAN` — at `d6f5d3f3c`",
    ):
        comments = [
            {
                "id": 1,
                "created_at": "2026-11-05T09:00:00Z",
                "kind": "review",
                "body": "[P1] tools/x.py:5 — the guard returns 0 on a failed read",
            },
            {
                "id": 2,
                "created_at": "2026-11-05T10:00:00Z",
                "kind": "comment",
                "body": body,
            },
        ]
        check = _check("blocking-finding", comments=comments)
        assert check.passed is True, f"{body!r} still read NEUTRAL: {check.raw}"
        assert "last-clear=2" in check.raw, f"{body!r}: {check.raw}"


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


def test_a_clean_outcome_in_the_recorders_own_format_is_a_clear() -> None:
    """`Outcome: clean` is the declaration `record-review.sh` reads. Recognising only
    "NO ISSUES FOUND"/"CLEAN" left it invisible (measured: `last-clear=-` on #10962), so a
    fixed finding could never be closed off and the PR read NOT-READY forever."""
    comments = [
        {
            "id": 90,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "[P1] tortoise/sdk.py:412 — the fold drops the evidence",
        },
        {
            "id": 91,
            "created_at": "2026-11-04T11:55:00Z",
            "kind": "comment",
            "body": "**Outcome: CLEAN.** No blocking finding at the new head.",
        },
    ]
    check = _check("blocking-finding", comments=comments)
    assert check.passed is True, check.raw
    assert "last-clear=91" in check.raw


def test_a_bare_empty_bodied_approval_closes_an_earlier_change_request() -> None:
    """ROUND-4 P3: the commonest GitHub-UI approval carries an EMPTY body, and empty-bodied
    reviews were dropped, so the approval arm was unreachable for exactly that shape."""
    comments = [
        {
            "id": 94,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "review",
            "state": "CHANGES_REQUESTED",
            "body": "",
        },
        {
            "id": 95,
            "created_at": "2026-11-04T11:55:00Z",
            "kind": "review",
            "state": "APPROVED",
            "body": "",
        },
    ]
    # The evaluator is fed the FULL set (the reader is what drops bodies); assert the pair
    # clears here, and separately assert the reader keeps both.
    assert _check("blocking-finding", comments=comments).passed is True


def test_a_dismissed_review_is_neither_a_finding_nor_a_clear() -> None:
    comments = [
        {
            "id": 96,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "[P1] tortoise/sdk.py:1 — real",
        },
        {
            "id": 97,
            "created_at": "2026-11-04T11:55:00Z",
            "kind": "review",
            "state": "DISMISSED",
            "body": "",
        },
    ]
    assert _check("blocking-finding", comments=comments).passed is False


def test_a_later_approval_closes_an_earlier_change_request() -> None:
    """Each item is graded on its own state, so without this an approved-after-changes PR
    could never read READY again."""
    comments = [
        {
            "id": 92,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "review",
            "state": "CHANGES_REQUESTED",
            "body": "",
        },
        {
            "id": 93,
            "created_at": "2026-11-04T11:55:00Z",
            "kind": "review",
            "state": "APPROVED",
            "body": "LGTM now",
        },
    ]
    check = _check("blocking-finding", comments=comments)
    assert check.passed is True, check.raw


#: REAL review bodies, captured from this repo's PRs (ids in the docstrings). Round 6
#: measured that 25 of 203 real bodies graded NEUTRAL because the gate only knew the
#: line-leading `[P1] file:line` shape — these fixtures ARE that measurement.
REAL_FINDING_BODY_10916 = """## Review — PR #10916 (fresh context, `ci-route` @ 76953c1eef4de419b149cdac39ff282761278256)

**Outcome: 2 findings (1 × P1, 1 × P2).** The design refusal itself is sound and I verified it
externally; both findings are about what the PR *claims* is guaranteed.
"""

REAL_ISSUE_BLOCK = """ISSUE:
  check_type: test-coverage-gap
  severity: P1
  origin: diff
  file: tortoise/sdk.py
  outcome: a deleted method is still called
"""

REAL_VERDICT_MIDLINE = (
    "Verdict: ISSUES 1 — one P2 claim-integrity defect. The capture-side change is sound."
)

REAL_SUMMARY_10641 = (
    "**Outcome: 3 issues — 2 blocking (both P1), 1 P2. NOT CLEAN.** See the ISSUE blocks below."
)


@pytest.mark.parametrize(
    "body",
    [REAL_FINDING_BODY_10916, REAL_ISSUE_BLOCK, REAL_VERDICT_MIDLINE, REAL_SUMMARY_10641],
    ids=[
        "outcome-n-findings",
        "issue-block-severity",
        "verdict-issues-midline",
        "outcome-n-issues",
    ],
)
def test_a_finding_in_the_fleets_real_declared_shape_is_blocking(body: str) -> None:
    """ROUND-6 P1: the gate only saw a line-leading `[P1] file:line`. These are the shapes
    actually posted — the reviewer's own `Outcome: N findings` header and the `ISSUE:`
    block's `severity:` field (defined by `skills/code-review/SKILL.md`) — and 25 of 203
    real review bodies graded NEUTRAL."""
    comments = [{"id": 100, "created_at": "2026-11-04T11:40:00Z", "kind": "comment", "body": body}]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


def test_the_measured_false_pass_on_10726_is_now_a_failure() -> None:
    """The exact measured sequence: a clear at 18:54:57Z (6639280674), then two records
    declaring an OPEN P2 at 21:05 and 21:58. Before this fix the check returned PASS and
    printed READY-FOR-OWNER for the PR this whole tool was built because of."""
    comments = [
        {
            "id": 6639280674,
            "created_at": "2026-11-04T18:54:57Z",
            "kind": "comment",
            "body": "NO ISSUES FOUND",
        },
        {
            "id": 6642099634,
            "created_at": "2026-11-04T21:05:44Z",
            "kind": "comment",
            "body": "Verdict: one real issue (P2, claim integrity)",
        },
        {
            "id": 6643177214,
            "created_at": "2026-11-04T21:58:54Z",
            "kind": "comment",
            "body": REAL_VERDICT_MIDLINE,
        },
    ]
    check = _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)
    assert "open=comment:6643177214" in check.raw


def test_a_history_summary_that_mentions_a_clean_verdict_is_not_a_clear() -> None:
    """The mirror image: a body titled "Review history and dispositions (4 rounds)" mentions
    `NO ISSUES FOUND` while listing severities. A clear phrase must not outrank a finding."""
    comments = [
        {
            "id": 110,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": (
                "## Review history and dispositions (4 rounds)\n\n"
                "Round 1: NO ISSUES FOUND\nRound 4: ISSUE:\n  severity: P2\n"
            ),
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


def test_the_recorders_loose_arm2_still_honours_its_outcome_clean_suppression() -> None:
    """The #1964 / tortoise#8866 false refusal: a genuine CLEAN review whose prose contains
    "not clean" later, but which declares `Outcome: clean`. Importing arm (2) WITHOUT the
    recorder's suppression would reproduce that false block."""
    comments = [
        {
            "id": 120,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": ("**Outcome: CLEAN.** The tree is not clean of debt, but nothing blocks here."),
        }
    ]
    assert _check("blocking-finding", comments=comments).passed is True


def test_the_loose_arm2_still_fires_without_an_explicit_clean_outcome() -> None:
    comments = [
        {
            "id": 121,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "Round 2 came back not clean.",
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


@pytest.mark.parametrize(
    "body",
    [
        "**severity: P1 · check_type: fail-open · origin: diff**",
        "- severity: P1",
        "**Severity:** P1",
        "> severity: P2",
        "`check_type: claim-mismatch` · severity: P2 · origin: diff",
    ],
    ids=["composed-bold", "bullet", "bold-key-colon", "blockquote", "backticked-composed"],
)
def test_a_severity_declared_in_a_composed_or_adorned_form_is_blocking(body: str) -> None:
    """ROUND-7 P2 (false PASS): the `severity` alternative was line-anchored while its two
    siblings were not, so the COMPOSED forms reviewers actually post read NEUTRAL."""
    comments = [{"id": 130, "created_at": "2026-11-04T11:40:00Z", "kind": "comment", "body": body}]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


@pytest.mark.parametrize(
    "body",
    [
        "**Outcome: 3 issues (0 blocking).** All three are P3 notes.",
        "**OUTCOME: 2 issues found (both P3), no blocking defect.**",
        "**Outcome: 2 issues (both P3, both measured), no correctness/security/fail-open defect found.**",
        "**Outcome: 2 findings (2 × P3).**",
        "**OUTCOME: 2 findings, both P3 — nothing blocking.**",
    ],
    ids=["zero-blocking", "both-p3", "only-p3-measured", "count-times-p3", "both-p3-nothing"],
)
def test_a_p3_only_review_with_a_count_does_not_block(body: str) -> None:
    """ROUND-7 P2 (false BLOCK): a declared COUNT blocked without reading the severities the
    same sentence declares, so four real P3-only reviews measured across ten PRs read as an
    OPEN BLOCKING FINDING — a permanent false NOT-READY, and a contradiction of this gate's
    own `test_p3_only_note_is_not_blocking`."""
    comments = [{"id": 140, "created_at": "2026-11-04T11:40:00Z", "kind": "comment", "body": body}]
    assert _check("blocking-finding", comments=comments).passed is True, body


def test_a_count_with_a_declared_blocking_severity_still_blocks() -> None:
    comments = [
        {
            "id": 141,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "comment",
            "body": "**Outcome: 2 findings (1 × P1, 1 × P3).**",
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


@pytest.mark.parametrize(
    "body",
    [
        "**Outcome: 2 findings (1 × P1).** No blocking other than the P1 — it is a real open defect.",
        "**Outcome: 2 findings (0 blocking).**\n[P1] tools/x.py:5 — a real open P1",
        "Round 1: **Outcome: 2 issues (0 blocking).**\nRound 3: **Outcome: 1 issue (1 × P1).**",
    ],
    ids=["p1-with-no-blocking-phrase", "zero-count-plus-p1-line", "history-mixed-counts"],
)
def test_a_declared_blocking_severity_outranks_a_no_blocking_phrase(body: str) -> None:
    """ROUND-8 P2 (false PASS, a regression on d62d856d2): `declined` was computed over the
    whole body and short-circuited, so a body that declares an open P1 AND says "0 blocking"
    read NEUTRAL."""
    comments = [{"id": 150, "created_at": "2026-11-04T11:40:00Z", "kind": "comment", "body": body}]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


@pytest.mark.parametrize(
    "body",
    [
        "**Outcome: CLEAN.** The one severity: P2 note from round 2 is fixed and verified.",
        "NO ISSUES FOUND — verified the severity: P1 finding from round 3 is now closed.",
        "No blocking findings. The severity: P1/P2 bar was applied to every hunk.",
    ],
    ids=["clean-outcome-past-severity", "no-issues-past-severity", "clean-prose-severity-bar"],
)
def test_prose_about_a_PAST_severity_is_not_an_open_finding(body: str) -> None:
    """ROUND-8 P3 (false BLOCK): unanchoring `severity` made ordinary CLEAN prose a blocking
    finding — 5 of 6 natural phrasings. A real finding carries the ISSUE block's siblings."""
    comments = [{"id": 151, "created_at": "2026-11-04T11:40:00Z", "kind": "comment", "body": body}]
    assert _check("blocking-finding", comments=comments).passed is True, body


def test_a_change_request_blocks_even_when_its_body_declares_only_p3() -> None:
    """ROUND-8 P3: the state was read only in the `else:` branch, so a formal unresolved
    change request whose body matched the declaration arm was dropped."""
    comments = [
        {
            "id": 152,
            "created_at": "2026-11-04T11:40:00Z",
            "kind": "review",
            "state": "CHANGES_REQUESTED",
            "body": "**Outcome: 2 findings (2 × P3).**",
        }
    ]
    _assert_fails("blocking-finding", "open-blocking-finding", comments=comments)


def test_a_head_that_moved_mid_read_is_not_ready() -> None:
    """ROUND-8 P3: nothing re-witnessed the head after the reads, so a push landing between
    the tree fetch and the recorder run could certify a head that is no longer the target."""
    check = _assert_fails("exclusions", "head-moved-mid-read", head_stable=False)
    assert "head moved during the read" in check.raw


def test_an_unwitnessable_closing_head_re_read_is_unreadable() -> None:
    check = _check("exclusions", head_stable=None)
    assert check.passed is False
    assert check.unreadable is True


def test_a_fixture_without_a_full_head_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ROUND-11 P3: `{"head": ""}` printed `READY-FOR-OWNER(1, )`, breaking the documented
    `(<PR>, <full head sha>)` contract, and nothing marked a fixture as fixture-derived."""
    bad = tmp_path / "nohashes.json"
    bad.write_text(json.dumps(_fixture(head="")))
    assert rfo.main(["--fixture", str(bad)]) == 1
    captured = capsys.readouterr()
    assert "FIXTURE-MODE(" in captured.err
    assert captured.out.strip().startswith("NOT-READY(")


def test_the_probe_dataclass_defaults_a_read_dependent_flag_to_unreadable() -> None:
    """ROUND-8 P3: `is_draft` defaulted to the PASSING side on the dataclass itself, so a
    future edit restoring `bool = False` would silently turn an unread PR payload into
    "open, not a draft" with nothing reddening."""
    bare = rfo.Probe(pr=1, head=HEAD)
    assert bare.is_draft is None
    assert bare.merged is None
    check = rfo.check_exclusions(bare)
    assert check.passed is False
    assert check.unreadable is True


#: The ROUND-9 corpus: every body below was measured against the previous head, and each
#: one read the WRONG way there. `blocking` is the required answer.
ROUND9_CORPUS: tuple[tuple[str, bool], ...] = (
    # The declaration arm was EXCLUSIVE, so one prose severity token masked an abort marker
    # or a real `[P1]`/`ISSUES n` verdict — a false PASS (the dangerous direction).
    ("Auto-fix stalled after 3 cycles — see the severity: P2 note.", True),
    ("Requires human review. The severity: P2 wording needs a human.", True),
    ("ISSUES 2\n\nI addressed the severity: P2 comment and a P3 nit.", True),
    ("The severity: P2 note from round 2 is fixed.\n\n[P1] tools/x.py:5 — still open", True),
    ("Closed `severity: P2` item.\n\n* [P2] tools/x.py:9 — still open", True),
    # quoting a finding is normal review behaviour; the `>` prefix used to read neutral.
    ("> [P1] tools/x.py:5 — quoted finding", True),
    ("**Outcome: 2 findings (1 × P1).** No blocking other than the P1 — a real open defect.", True),
    # A clean round that MENTIONS the P2 it fixed must stay certifiable.
    ("**Outcome: 2 findings (0 blocking).** One P2 was fixed, two P3 deferred.", False),
    ("**Outcome: 3 issues (0 blocking).** Round 7's P2 is fixed; P3 notes remain.", False),
    (
        "**Outcome: CLEAN.** The severity: P2 note from round 2 is fixed; pushed to origin: main",
        False,
    ),
    ("**Outcome: CLEAN.** The one severity: P2 note from round 2 is fixed and verified.", False),
    ("NO ISSUES FOUND — verified the severity: P1 finding from round 3 is now closed.", False),
    ("**Outcome: 2 findings (2 × P3).**", False),
    ("**OUTCOME: 2 issues found (both P3), no blocking defect.**", False),
    # ROUND-10: the verdict-header shapes harvested from LIVE bodies (#10956, #10929,
    # #10920, #10938, #10945). Each read NEUTRAL and, as a PR's last artifact, printed
    # READY-FOR-OWNER.
    ("**Outcome: ISSUES 3** — 1 code defect (security), 2 false statements", True),
    ("## Code review — ISSUES FOUND (1 × P1, 1 × P2)", True),
    ("## Code review — ISSUES FOUND (1 × P2)", True),
    ("### ⛔ P1, verified by reproduction — `pin` refuses in two shapes", True),
    ("⛔ **Do not land this PR as it stands — it carries a known P1**", True),
    # …and the two CLEAN shapes that were over-blocked: a severity TALLY is not a finding.
    ("**Outcome: CLEAN.**\n\nP1: 0, P2: 0, P3: 2 (nits filed as follow-ups).", False),
    ("**Outcome: 3 findings (0 blocking).**\n\n- P1: 0\n- P2: 0\n- P3: 3", False),
    # ROUND-11: the dominant shapes in the FOLD PRs (#10902/#10909/#10920/#10906/#10867).
    ("4 × `P2`, `check_type: bug`, `origin: diff` — `tools/batch_open.py:560`", True),
    ("P2: `tools/batch_open.py:106-107` — the cue", True),
    ("**Verdict: 2 P2 issues, no P0/P1.**\n\n1. [P2] tortoise/sdk.py:12 — x", True),
    ("- P1: 0, P2: 2", True),
    ("| P2 | `tools/x.py:9` | a table cell |", True),
    # …and the phrase that must NOT block now that `Outcome: clean` suppresses it.
    ("**Outcome: CLEAN.** No new issues found — the round-2 P2 is fixed.", False),
    # ROUND-12: parity with `scripts/review-pr.sh`'s own cue list — the label-noun-in-front
    # cue and the table-row cue. Ten live shapes that declare a blocking finding read
    # NEUTRAL before these, each measured on a real body.
    ("**Verdict: 2 real issues — 1 P1 (red core check), 1 P2 (unmeasured write leg).**", True),
    ("**Verdict: ISSUES — 1 blocking (P1), 1 non-blocking (P2).**", True),
    ("**Finding [P1]**: the guard returns 0 without holding the lock.", True),
    ("**Verdict: P1 — the guard returns 0 without a lock.**", True),
    ("There is a **P1** in the guard", True),
    ("**1 blocking (P1)**: a real defect", True),
    ("Blocking finding: P1 in the guard", True),
    ("Requires a fix: P1 in the guard", True),
    ("| **P1** | the guard returns 0 without a lock |", True),
    ("2 [P1] open blocking defects remain", True),
    ("**Outcome: 2 findings (1 × P1, 1 × P2). No blocking defect.**", True),
    # …and the zero counts, in every spelling review-pr.sh exempts.
    ("**P1**: none.", False),
    ("**Outcome: CLEAN.**\n\n**P1**: none.", False),
    # ROUND-13: the three cues `review-pr.sh` has and this module did not — the parenthesised
    # label, the count WRITTEN BEFORE the token / spelled numeral, and the slot separator.
    ("**Finding (P1)**: the guard returns 0 on a failed read", True),
    ("Found a P1: the guard returns 0 on a failed read", True),
    ("One P1 — the guard returns 0 on a failed read", True),
    ("CLEAN — 2 P1 findings.", True),
    ("CLEAN — one blocking finding remains, in a file outside this diff.", True),
    ("- **F1 (P2)** — a claim mismatch\n- **F2 (P1)** — the guard returns 0", True),
    # …and the two over-blocks that came with parity: a heading naming a FIXED severity in a
    # clean-declaring body, and a reviewer's own severity TALLY table.
    ("**Outcome: CLEAN.**\n### The P1 is fixed, and the fix is load-bearing", False),
    ("**Outcome: CLEAN.**\n| 0 | 0 | 2 |", False),
    ("| **P1** | 0 |", False),
    ("| P0 | P1 | P2 |", False),
    # ROUND-14: a cue is a SHAPE, not an enumerated neighbourhood. A count crosses up to three
    # words and a location may be words away; pinning the immediate neighbour re-created the
    # ratchet one word away (review-pr.sh records both as its own measured fail-opens).
    ("CLEAN — 2 open P1 findings remain.", True),
    ("CLEAN — 2 remaining P1 defects", True),
    ("CLEAN — 2 unresolved P1", True),
    ("2 new P1", True),
    ("Found one P1 in tools/x.py:12", True),
    ("there is a P1 in `tools/x.py:12`", True),
    ("P1 in scripts/x.sh:12", True),
    ("| P1 | the guard returns 0 |", True),
    ("| P1 | 2 |", True),
    # ROUND-15: the three SHARED CLASSES review-pr.sh keys on — a numeral class (digits AND
    # spelled), a finding-noun class, and `P<n>s?`. Enumerating a neighbourhood is the ratchet
    # that made `2 remaining P1` block and `Three remaining P1` not.
    ("CLEAN — Three remaining P1.", True),
    ("Two new P1", True),
    ("CLEAN — several open P2", True),
    ("CLEAN — 4 defects remain unresolved.", True),
    ("CLEAN — 5 errors remain unfixed.", True),
    ("CLEAN — 2 regressions remain.", True),
    ("CLEAN — 3 issues outstanding.", True),
    ("CLEAN — several problems remain.", True),
    ("CLEAN — the guard returns 0 (2 defects remain).", True),
    ("CLEAN — 3 unresolved P1s.", True),
    ("CLEAN — 2 open P2s remain.", True),
    # ROUND-22: the COUNT `1` — the commonest open-blocker count, and the one the class could
    # not take (digits started at 2, spelled numerals at `two`). The polarity flipped on the
    # digit alone: `CLEAN — 2 remaining P1` blocked, `CLEAN — 1 remaining P1` did not — and
    # `CLEAR_RE` matches that body too, so the stronger wrong answer followed and the owner's
    # tab said READY-FOR-OWNER for a PR carrying an open P1. Filed by two independent
    # reviewers (rounds 21 and 22), fixed with a PRESENCE CUE rather than by dropping the
    # article guard.
    ("CLEAN — 1 remaining P1", True),
    ("1 open P1 remains", True),
    ("1 unresolved P1", True),
    ("1 remaining P1", True),
    ("One open P1 remains in the guard.", True),
    ("1 more P1", True),
    ("1 outstanding P1 finding", True),
    ("1 P1 open", True),
    ("1 P1 remains", True),
    # …and the ARTICLE shapes the omission existed to protect: `one`/`1` with NO presence cue
    # is not a count, so it must stay NEUTRAL (round 8's false-BLOCK direction).
    ("**Outcome: CLEAN.** One P2 was fixed; two P3 deferred.", False),
    ("**Outcome: CLEAN.** 1 P2 was fixed in the last commit.", False),
    # ROUND-23: the CUE-BEFORE-COUNT spelling — the one a reviewer writes when SUMMARISING.
    # `ONE_OPEN_COUNT_RE` requires the cue BETWEEN count and token or AFTER the token, so
    # moving the cue one word to the rest of the sentence flipped the polarity back: measured
    # at `858191e28`, `CLEAN — 1 remaining P1` blocked while `CLEAN — remaining: 1 P1 (open)`
    # did not, and `CLEAR_RE` matched the body so it was graded a CLEAR. The EXCLUSION cues
    # carry the same polarity (they name the finding as the exception to a clean result) and
    # `single`/`a single` is the commonest one-item spelling.
    ("CLEAN — remaining: 1 P1 (open)", True),
    ("CLEAN — outstanding: one P1", True),
    ("CLEAN apart from a single P1.", True),
    ("NO ISSUES FOUND except 1 P1.", True),
    ("Everything landed cleanly, other than 1 P2 in the guard.", True),
    # …and the CLOSED direction stays certifiable: a completing verb after the token means the
    # finding was fixed, which is the false-BLOCK direction round 8 measured.
    ("**Outcome: CLEAN.** remaining: 1 P1 was fixed and verified.", False),
    ("**Outcome: CLEAN.** apart from a single P2, now closed after round 9.", False),
    # …but `landed`/`cleared`/`done` are NOT completing on their own here: in this fleet
    # "landed" means MERGED / ARRIVED, so the bare form exempted a LIVE finding — the same
    # fail-open direction as the retracted-verdict arm, one spelling over. Those three now
    # require a copula; the unambiguous four (`fixed|closed|resolved|addressed`) still do not.
    ("CLEAN — remaining: 1 P1 landed after the last review and is unfixed.", True),
    ("**Outcome: CLEAN.** remaining: 1 P1 landed from main; it is still open.", True),
    # …while the COPULA'd form is still a completion, so the tightening does not over-correct
    # in the false-BLOCK direction.
    ("**Outcome: CLEAN.** remaining: 1 P1 has now landed and is verified.", False),
)


@pytest.mark.parametrize(("body", "blocks"), ROUND9_CORPUS, ids=range(len(ROUND9_CORPUS)))
def test_the_round_9_corpus_grades_exactly_as_measured(body: str, blocks: bool) -> None:
    """Each of these read the WRONG way at `720b628ba`. The arms are independent and OR'ed
    now, so no single token can mask another; and a single `0 blocking` declaration is
    trusted while a per-round history is graded on the severities it declares."""
    got = rfo._grades_as_blocking(body, {})
    assert got is blocks, f"{body!r} -> {got}, expected {blocks}"


def test_a_not_clean_record_fails_condition_three() -> None:
    """ROUND-11 P1: `record-review.sh --check-carry` returns `head`/`carry` at exit 0 for a
    `not-clean` record too (it reports that verdict on stderr and leaves the exit code
    alone, agent-infra#3400), so keying on the token CERTIFIED the fleet's own strongest
    statement that the PR is not ready. Measured: 5 `verdict: not-clean` records exist on
    this box (`tortoise` 10902/10909/10920 with 4/4/2 open findings)."""
    check = _assert_fails("review", "record-not-clean:not-clean", review_verdict="not-clean")
    assert "record-verdict=not-clean" in check.raw


@pytest.mark.parametrize("verdict", ["clean", "clean-micro", "clean-low"])
def test_every_clean_record_verdict_passes(verdict: str) -> None:
    assert _check("review", review_verdict=verdict).passed is True


def test_an_unreadable_record_verdict_fails_closed() -> None:
    check = _check("review", review_verdict=None)
    assert check.passed is False
    assert check.unreadable is True
    assert check.reason == "record-verdict-unreadable"


@pytest.mark.parametrize("field", ["is_draft", "merged"])
def test_an_omitted_draft_or_merged_flag_is_unreadable(field: str) -> None:
    """ROUND-6 P3: an ABSENT boolean defaulted to the passing side."""
    check = _check("exclusions", **{field: None})
    assert check.passed is False
    assert check.unreadable is True


def test_a_truncated_file_listing_is_unreadable() -> None:
    """ROUND-6 P3: `pulls/N/files` completeness was never witnessed."""
    fake = _FakeGh(
        {
            "/files?per_page": [{"filename": "a.py"}],
            "pulls/": {"changed_files": 3},
        }
    )
    with pytest.raises(rfo.ReadError):
        rfo._read_touched(fake, 1)


def test_an_unwitnessable_file_listing_is_unreadable() -> None:
    """ROUND-7 P3: an ABSENT `changed_files` leaves the truncation witness unable to run —
    an absent field is never the passing side."""
    fake = _FakeGh(
        {
            "/files?per_page": [{"filename": "a.py"}],
            "pulls/": {"state": "open"},
        }
    )
    with pytest.raises(rfo.ReadError):
        rfo._read_touched(fake, 1)


def test_main_prints_a_verdict_line_even_when_a_reader_crashes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ROUND-6 P3-a: the backstop that makes the output contract hold for an unforeseen
    payload shape (a `head` that is a string, a vendor schema change)."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pr": "not-an-int"}))
    assert rfo.main(["--fixture", str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.strip().startswith("NOT-READY("), out


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


def test_an_absent_pr_state_is_unreadable() -> None:
    """ROUND-4 P3: a missing `state` key defaulted to the PASSING side ("open")."""
    check = _check("exclusions", pr_state="")
    assert check.passed is False
    assert check.unreadable is True


def test_the_queue_blocking_label_list_matches_mergify() -> None:
    """ROUND-4 P3, and the reason this test exists: `QUEUE_BLOCKING_LABELS` is a COPY of
    `.mergify.yml`'s label entries. Without a reconciling check, adding `-label=<x>` to
    `queue_conditions` would silently leave this gate LAXER than the queue — the very
    invariant violation round 3 fixed as a P2 (allow what the queue refuses) — with nothing
    reddening anywhere. `.mergify.yml` is the source of truth here."""
    import re

    text = (REPO_ROOT / ".mergify.yml").read_text()
    queue_block = text.split("queue_conditions:", 1)[1].split("merge_conditions:", 1)[0]
    negated = set(re.findall(r"-\s*-label=([A-Za-z0-9_.-]+)", queue_block))
    assert negated == set(rfo.QUEUE_BLOCKING_LABELS), (
        f".mergify.yml's queue_conditions negate {sorted(negated)} but the gate blocks "
        f"{sorted(rfo.QUEUE_BLOCKING_LABELS)} — the gate is now laxis than the queue"
    )


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
    token, rc, _raw, _verdict = rfo._read_review(1, REPO, REPO_ROOT)
    assert (token, rc) == ("norecord", 4)
    recorder.state["rc"] = 1
    assert rfo._read_review(1, REPO, REPO_ROOT)[0] == "fresh"
    recorder.state["rc"] = 3
    assert rfo._read_review(1, REPO, REPO_ROOT)[0] == "unreadable"


def test_a_parsed_stale_token_fails_even_at_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _FakeRecorder(monkeypatch)
    recorder.state.update(rc=0, stdout="fresh\tabc\tdef\n")
    token, rc, _raw, _verdict = rfo._read_review(1, REPO, REPO_ROOT)
    assert (token, rc) == ("fresh", 0)
    assert (
        rfo.check_review(rfo.probe_from_dict(_fixture(review_token=token, review_exit=rc))).passed
        is False
    )


def test_absent_scripts_pointer_is_unreadable_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pointer is gitignored and materialised per machine; its absence must FAIL."""
    monkeypatch.setattr(rfo, "_review_script", lambda workdir: None)
    token, rc, raw, verdict = rfo._read_review(1, REPO, REPO_ROOT)
    assert token is None and rc is None
    assert verdict is None
    assert "absent" in raw


def test_a_caller_supplied_record_pair_cannot_self_attest(monkeypatch: pytest.MonkeyPatch) -> None:
    """ROUND-10 P1: forwarding `--recorded-diff/--recorded-head` to the recorder made this
    arm a SELF-ATTESTATION — its supplied-pair path short-circuits the record lookup, the
    digest is computable from the public diff, and `--recorded-diff aaa…a --recorded-head
    <live head>` made condition 3 PASS on a PR with no review at all. The flags are gone;
    the CLI must not accept them, and the argv must never carry them."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout, cwd=None):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 4, "norecord\t-\t-\n", "")

    monkeypatch.setattr(rfo, "_review_script", lambda workdir: Path("/fake/record-review.sh"))
    monkeypatch.setattr(rfo, "_run", fake_run)
    token, rc, _raw, _verdict = rfo._read_review(7, REPO, REPO_ROOT)
    assert (token, rc) == ("norecord", 4)
    assert seen and "--recorded-diff" not in seen[0]
    # …and the CLI surface refuses it outright rather than silently ignoring a caller.
    with pytest.raises(SystemExit):
        rfo.build_parser().parse_args(["7", "--recorded-diff", "a" * 64])


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

    def api_fresh(self, endpoint: str) -> Any:
        return self._match(endpoint)

    def pages(self, endpoint: str) -> list[Any]:
        value = self._match(endpoint)
        return value if isinstance(value, list) else [value]

    def pr(self, number: int) -> Mapping[str, Any]:
        return self._match("pulls/{n}")


def _workflow_run(
    workflow_file: str,
    *,
    event: str = "pull_request",
    status: str = "in_progress",
    run_id: int = 5,
) -> dict[str, Any]:
    """One `workflow_runs[]` entry, in the shape attribution reads.

    `path` is the handle `emitters` records (`python-ci.yml`); `event` is what separates a
    PR run (evidence the aggregate was created) from a `push` run of the same sha (not).
    """
    return {
        "id": run_id,
        "workflow_id": 7,
        "path": f".github/workflows/{workflow_file}",
        "event": event,
        "status": status,
        "conclusion": None if status != "completed" else "success",
    }


def _gh_payloads(
    *,
    check_runs: list[dict[str, Any]] | None = None,
    body: str = "",
    runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Canned `gh` payloads, ordered SPECIFIC → GENERAL (first substring match wins)."""
    crs = check_runs if check_runs is not None else _check_runs_for(REQUIRED)
    # No `path`/`event` by default: the run listing exists to ATTRIBUTE a merge-time
    # context's absence, and the green fixture never has one absent. Cases that do
    # exercise attribution pass `runs=` explicitly (see `_workflow_run`).
    workflow_runs = runs if runs is not None else [{"id": 5, "workflow_id": 7}]
    return {
        "/files?per_page": [
            {"filename": "tortoise/sdk.py"},
            {"filename": "tools/ready_for_owner.py"},
        ],
        # ⛔ NO `branches/main/protection` PAYLOAD. The required set is DERIVED from
        # `.mergify.yml` (`tools/required_set.py`) and this read is deliberately gone, so a
        # canned response for it would be dead fixture data pointing at a call that no
        # longer exists — and `_FakeGh` fails loudly on an unexpected endpoint, which is the
        # signal that the derivation path was silently replaced by an API read again.
        "check-runs": {"total_count": len(crs), "check_runs": crs},
        "actions/runs": {
            "total_count": len(workflow_runs),
            "workflow_runs": workflow_runs,
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
            "state": "open",
            "merged": False,
            "labels": [],
            "changed_files": 2,
            "body": body,
        },
    }


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, Any],
    fail: str | None = None,
    derive=None,
):
    fake = _FakeGh(payloads, fail=fail)
    monkeypatch.setattr(rfo, "Gh", lambda repo, **kw: fake)
    monkeypatch.setattr(rfo, "_read_tree", lambda gh, pr, head, workdir: (True, "merge-tree ok"))
    monkeypatch.setattr(
        rfo, "_read_review", lambda *a, **kw: ("head", 0, "token=head exit=0", "clean")
    )
    # The required set is read from the PROTECTED ref in production (so a branch cannot
    # shrink the set that judges it). A unit test has no fetched `origin/main` to read and
    # must not depend on one, so the derivation is pinned to the workdir here — the
    # ref-vs-workdir distinction has its own cases in `tests/test_required_set.py` and in
    # `test_the_required_set_is_read_from_the_protected_ref` below.
    monkeypatch.setattr(
        rfo,
        "derive_from_ref",
        derive
        if derive is not None
        else (lambda cwd, ref=None, **kw: rfo_required.derive_from_config(REPO_ROOT)),
    )
    return rfo.collect_probe(10999, REPO, REPO_ROOT, now=NOW), fake


def test_collect_probe_wires_a_green_world_into_a_READY_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read layer, end to end: a green world must produce READY-FOR-OWNER."""
    probe = _collect(monkeypatch, _gh_payloads())[0]
    # The required set is DERIVED from `.mergify.yml`, so it arrives SPLIT: its
    # `queue_conditions` gate entry at the head, its `merge_conditions` are merge-time.
    assert set(probe.required or ()) == set(REQUIRED) - set(MERGE_TIME_REQUIRED)
    assert probe.required_deferred == MERGE_TIME_REQUIRED
    assert all(g.conclusion == "success" for g in probe.groups or ())
    verdict = rfo.evaluate(probe)
    assert verdict.ready is True, [(c.name, c.reason, c.raw) for c in verdict.checks]
    assert verdict.line() == f"READY-FOR-OWNER(10999, {HEAD})"


def test_a_head_carrying_no_merge_time_check_is_READY_not_NOT_READY(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFERRED is decided by EVIDENCE, not by absence: no run of `python-ci.yml` here.

    Mergify evaluates `merge_conditions` on `mergify/merge-queue/<sha>`, so a head where
    the emitting workflow NEVER RAN is the queue working as designed. Against a FLAT
    required set (the protection API's shape, which has no entry/merge split) that absence
    can only read as `FAIL(missing)` — NOT-READY for every PR forever, which is the class
    of disagreeing readiness verdict this instrument exists to end (#10962).

    ⛔ THE PREMISE OF THIS CASE WAS CORRECTED (#10991 review, issue 1). It used to assert
    that absence ALONE is deferral, citing "0 of 6 sampled heads carried python-ci-gate"
    — a sample taken while the matrix was still running. Re-measured 2026-11-05 the
    aggregate reports on author heads (`queued` 11097, `success` 11100, `failure` 11096),
    and a `needs`-gated job that has not started has NO check-run at all. So this fixture
    must also carry the run listing, and it now asserts the reason the answer is DEFERRED:
    nothing from `python-ci.yml` ran on this head.
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(
            check_runs=_check_runs_for(ENTRY_REQUIRED),
            runs=[_workflow_run("docs.yml", event="pull_request")],
        ),
    )[0]
    assert probe.required_deferred == MERGE_TIME_REQUIRED
    assert probe.merge_emit_files == {MERGE_TIME_REQUIRED[0]: ("python-ci.yml",)}
    verdict = rfo.evaluate(probe)
    gates = next(c for c in verdict.checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "DEFERRED(merge-time)" in gates.raw
    assert "no python-ci.yml run on this head" in gates.raw
    assert verdict.ready is True, [(c.name, c.reason, c.raw) for c in verdict.checks]


def test_a_pending_merge_time_aggregate_is_a_FAIL_not_a_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ ISSUE 1 (P1, fail-open): `python-ci.yml` ran here, so the aggregate is PENDING.

    A `needs`-gated job has no check-run until its prerequisites finish (GitHub Actions
    workflow syntax), so a pending aggregate is INDISTINGUISHABLE from an absent one in
    `groups`. Grading that absence DEFERRED returned `PASS` / exit 0 on a head where the
    required suite had not concluded — while the very same state one moment later (the
    check-run created as `queued`) FAILS as in-flight. The verdict depended on scheduling
    timing, not on evidence.

    On the pre-fix code (`seen.append(f"{context}={DEFERRED}"); continue`) this case reads
    `gates.passed is True` and REDs — that mutation is the fix, and it is the only one
    this test permits.
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(
            check_runs=_check_runs_for(ENTRY_REQUIRED),
            runs=[_workflow_run("python-ci.yml", status="in_progress")],
        ),
    )[0]
    verdict = rfo.evaluate(probe)
    gates = next(c for c in verdict.checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert "missing:python-ci-gate" in gates.reason
    assert "MISSING" in gates.raw and "pending creation" in gates.raw
    assert verdict.ready is False, [(c.name, c.reason, c.raw) for c in verdict.checks]


def test_the_measured_false_READY_shape_reds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ The CONCRETE head the review measured: PR 11113 `92299555219e`.

    All six ENTRY contexts green, `test (g)` `completed/failure` on the same head, and
    `python-ci-gate` absent because its `needs` were unsatisfied. The pre-fix gate
    reported `READY-FOR-OWNER` on that head. A red leg inside the required suite's own
    workflow must never be invisible to the readiness answer.
    """
    legs = _check_runs_for((*ENTRY_REQUIRED, "test (g)"))
    for run in legs:
        if run["name"] == "test (g)":
            run["conclusion"] = "failure"
    probe = _collect(
        monkeypatch,
        _gh_payloads(
            check_runs=legs,
            runs=[_workflow_run("python-ci.yml")],
        ),
    )[0]
    verdict = rfo.evaluate(probe)
    gates = next(c for c in verdict.checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert "missing:python-ci-gate" in gates.reason
    assert verdict.ready is False, [(c.name, c.reason, c.raw) for c in verdict.checks]


def test_a_push_run_of_the_same_sha_is_not_evidence_the_aggregate_was_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`head_sha` lists `push` runs too, and a push run is not a PR run.

    Attributing it would fail a genuinely deferred merge-time context on any head that was
    also pushed — a false NOT-READY bought with no evidence. The event filter is the ONLY
    difference between this case and the pending-aggregate case above.
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(
            check_runs=_check_runs_for(ENTRY_REQUIRED),
            runs=[_workflow_run("python-ci.yml", event="push")],
        ),
    )[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "DEFERRED(merge-time)" in gates.raw


def test_an_unattributable_merge_time_context_is_a_FAIL_not_a_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ No emitter recorded => "did its workflow run here?" cannot be ASKED.

    The record carries `emitters` for every effective check-run name; a name the config
    requires but the record cannot attribute leaves the absence unexplained. Falling
    through to DEFERRED there would restore the exact fail-open this fix closes, so it is
    a FAIL with the remedy named (`--recut` rewrites `emitters`).
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(check_runs=_check_runs_for(ENTRY_REQUIRED)),
    )[0]
    probe.merge_emit_files = {}
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert "unattributable:python-ci-gate" in gates.reason
    assert "--recut" in gates.raw


def test_an_unreadable_run_listing_cannot_turn_absence_into_a_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A HALF-READ surface must not defer: no run listing, no attribution, no PASS.

    The groups and the runs come from one call, so this state is not reachable through
    `collect_probe` — which is exactly why it is pinned here rather than left to a
    comment: `Probe` is a plain dataclass and every condition is a pure function of it.
    """
    probe = _collect(monkeypatch, _gh_payloads(check_runs=_check_runs_for(ENTRY_REQUIRED)))[0]
    probe.head_runs = None
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert "unattributable:python-ci-gate" in gates.reason


def test_a_merge_time_check_that_ran_and_red_here_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferral is about WHERE a check is authoritative, not a licence to ignore a red.

    A `merge_conditions` name that DID run on this head is real evidence about the
    tree: a measured red must fail the gate, exactly as it would for an entry check.
    """
    runs = _check_runs_for(REQUIRED)
    for run in runs:
        if run["name"] == "python-ci-gate":
            run["conclusion"] = "failure"
    probe = _collect(monkeypatch, _gh_payloads(check_runs=runs))[0]
    verdict = rfo.evaluate(probe)
    gates = next(c for c in verdict.checks if c.name == "gates")
    assert gates.passed is False
    assert "python-ci-gate" in gates.reason
    assert verdict.ready is False


def test_the_required_set_is_read_from_the_protected_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ A GATE THAT READS ITS OWN SUBJECT IS DEFEATABLE BY ITS SUBJECT.

    Both inputs are tracked files, so a branch that edits `.mergify.yml` and
    `docs/ci/required-contexts.json` together can shrink the set that judges it. The read
    must therefore go to the PROTECTED ref. Asserting the exact ref — rather than only
    that a set came back — is what makes this discriminating: point the read at the
    workdir and this reds.
    """
    seen: list[str] = []

    def _spy(cwd, ref=None, **kw):
        seen.append("" if ref is None else ref)
        return rfo_required.derive_from_config(REPO_ROOT)

    _collect(monkeypatch, _gh_payloads(), derive=_spy)
    assert seen == ["origin/main"], seen


def test_every_required_check_being_merge_time_refuses_with_the_right_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readable config whose entry gate is EMPTY decides nothing at the head.

    That is a configuration fact, not a read failure, and the check must say which it is
    rather than reusing the unreadable arm's wording.
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(check_runs=_check_runs_for(MERGE_TIME_REQUIRED)),
        derive=lambda *a, **k: rfo_required.RequiredSet(
            names=MERGE_TIME_REQUIRED,
            source="test",
            queue_names=(),
            merge_names=MERGE_TIME_REQUIRED,
        ),
    )[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False
    assert "every required check is merge-time" in gates.reason
    # …and the raw carries the SET'S PROVENANCE, which is what a lane debugging
    # NOT-READY needs in order to know which set it was judged against.
    assert "source=" in gates.raw, gates.raw


def test_the_required_set_verb_labels_a_config_error_as_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 either way, but a reader comes here to learn WHY — so the LABEL must say
    which it is. A config error reported as `UNREADABLE` sends them to the wrong place."""
    from tools.mergify_config_guard import MERGIFY_REL, RECORD_REL

    (tmp_path / MERGIFY_REL).write_text(
        "queue_rules:\n"
        "  - name: main\n"
        "    queue_conditions: ['base=main', 'check-success=docs']\n"
        "    branch_protection_injection_mode: merge\n"
        "    merge_conditions: []\n",
        encoding="utf-8",
    )
    record = tmp_path / RECORD_REL
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"required_contexts": ["docs", "vanished"]}), encoding="utf-8")
    # `ref=None` is the in-process workdir diagnostic; an EMPTY `--ref` from the CLI is
    # refused (see the test below), so this cannot be reached by a caller passing a ref.
    code = rfo.report_required_set(tmp_path, None)
    out = capsys.readouterr().out
    assert code == 2
    assert "REQUIRED-SET-CONFIG-ERROR" in out
    assert "REQUIRED-SET-UNREADABLE" not in out


def test_the_required_set_verb_labels_a_DIVERGED_live_read_as_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⛔ THE REFUSAL HAS TO REACH THE VERB, not just the library.

    `required_set._live_attestation` is pinned at the DERIVATION level (six tests in
    `tests/test_required_set.py`), and the test above pins the verb's CONFIG-ERROR label
    for the record↔config EQUALITY shapes only. Neither covers the shape this PR's fix
    added: a record whose `required_contexts` still matches the config — so every equality
    check passes — while its own `live_result` says the live read DIVERGED.

    That gap matters because the two labels are different REMEDIES: `UNREADABLE` means
    "could not be read, re-run it", while this state is not going to change on a re-run —
    it needs `--live` re-attestation under a credential that can read branch protection.
    A refusal that lands on the wrong label sends the reader to the wrong subsystem, which
    is what this file's existing verb test exists to prevent.

    The fixture is deliberately the FAIL-OPEN shape the fix removed: `live_observed_contexts`
    EQUALS `required_contexts` and both equal the derived union, so the payload is a perfect
    match. Only the verdict disagrees — and before the fix it certified the set.
    """
    from tools.mergify_config_guard import MERGIFY_REL, RECORD_REL

    union = ["docs"]
    (tmp_path / MERGIFY_REL).write_text(
        "queue_rules:\n"
        "  - name: main\n"
        "    queue_conditions: ['base=main', 'check-success=docs']\n"
        "    branch_protection_injection_mode: merge\n"
        "    merge_conditions: []\n",
        encoding="utf-8",
    )
    record = tmp_path / RECORD_REL
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps(
            {
                "required_contexts": union,
                "emitters": {},
                "live_result": "DIVERGED",
                "live_observed_contexts": union,
                "live_checked_at": "2026-11-01T00:00:00Z",
                "live_verified_at": "2026-10-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    code = rfo.report_required_set(tmp_path, None)
    out = capsys.readouterr().out
    assert code == 2
    assert "REQUIRED-SET-CONFIG-ERROR" in out, out
    assert "REQUIRED-SET-UNREADABLE" not in out, out
    # The remedy that WORKS must be named: `--recut` rewrites only gate_digest/emitters/
    # verified_at and cannot make a DIVERGED live read SATISFIED.
    assert "--live" in out, out


def test_the_required_set_verb_refuses_an_empty_ref(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty `--ref` (an unset `$BRANCH` in `--ref "$BRANCH"`) must be REFUSED with a
    message that names the problem.

    ⛔ THIS GUARD BUYS A DIAGNOSIS, NOT A BYPASS FIX, and the difference matters for the
    next reader: the fall-back to the workdir was already closed by branching on `ref is
    not None`, so without this guard an empty ref fails closed through
    `git rev-parse --verify ''` -> `fatal: Needed a single revision` — correct, but an
    opaque message from the wrong subsystem, on the one verb a human runs to learn WHY.

    Revert the guard and this reds on the message assertions (it still exits 2).
    """
    code = rfo.main(["--required-set", "--workdir", str(REPO_ROOT), "--ref", ""])
    out = capsys.readouterr().out
    assert code == 2
    assert "REQUIRED-SET-UNREADABLE(--ref must name a ref" in out
    assert "queue_conditions" not in out


def test_the_gates_raw_discloses_the_required_set_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source (and the sha it resolved to) must appear on EVERY gates arm.

    The required set is read from a local ref that is never fetched by that read, so the
    sha is the only honest statement about provenance. It used to appear on the two
    unreadable arms only, leaving a PASS — the verdict a merge rests on — silent about
    what set it certified. Revert to `{raw}` alone and this reds.
    """
    probe = _collect(monkeypatch, _gh_payloads())[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "source=" in gates.raw
    assert ".mergify.yml" in gates.raw


def test_the_gates_raw_discloses_the_source_on_the_FAILING_arm_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provenance must survive on a FAIL, not only on a PASS.

    The only older pin used a green probe, so removing `[{source}]` from the failed arm
    stayed green. A lane debugging NOT-READY reads exactly this line, and needs to know
    WHICH set it was judged against.
    """
    runs = _check_runs_for(REQUIRED)
    for run in runs:
        if run["name"] == "docs":
            run["conclusion"] = "failure"
    probe = _collect(monkeypatch, _gh_payloads(check_runs=runs))[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False
    assert "source=" in gates.raw, gates.raw
    assert ".mergify.yml" in gates.raw


def test_the_gates_raw_discloses_the_source_when_every_check_is_merge_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry-empty arm also judges the SET, so it must name the set's provenance."""
    probe = _collect(
        monkeypatch,
        _gh_payloads(check_runs=_check_runs_for(MERGE_TIME_REQUIRED)),
        derive=lambda *a, **k: rfo_required.RequiredSet(
            names=MERGE_TIME_REQUIRED,
            source="test-source at origin/main (abc123abc123)",
            queue_names=(),
            merge_names=MERGE_TIME_REQUIRED,
        ),
    )[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False
    assert "source=" in gates.raw, gates.raw


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


def test_read_comments_keeps_a_bare_empty_bodied_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUND-5 P3: the approval arm was tested only through the EVALUATOR, so a revert of
    the reader's `state not in (CHANGES_REQUESTED, APPROVED)` would leave every test green
    while a bare GitHub-UI approval became invisible again (permanent false NOT-READY)."""
    fake = _FakeGh(
        {
            "/reviews": [
                {
                    "id": 1,
                    "state": "CHANGES_REQUESTED",
                    "body": "",
                    "submitted_at": "2026-11-04T11:40:00Z",
                },
                {
                    "id": 2,
                    "state": "APPROVED",
                    "body": "",
                    "submitted_at": "2026-11-04T11:55:00Z",
                },
            ],
            "/comments": [],
        }
    )
    items, raw = rfo._read_comments(fake, 1)
    assert [i["state"] for i in items] == ["CHANGES_REQUESTED", "APPROVED"], raw
    verdict = rfo.evaluate(rfo.probe_from_dict(_fixture(comments=list(items))))
    assert next(c for c in verdict.checks if c.name == "blocking-finding").passed is True


def test_read_comments_drops_a_dismissed_review_even_with_a_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGh(
        {
            "/reviews": [
                {
                    "id": 3,
                    "state": "DISMISSED",
                    "body": "[P0] tortoise/sdk.py:1 — dismissed finding",
                    "submitted_at": "2026-11-04T11:40:00Z",
                }
            ],
            "/comments": [],
        }
    )
    items, raw = rfo._read_comments(fake, 1)
    assert items == (), raw


def test_a_non_json_gh_body_is_a_read_failure_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUND-5 P3: `json.JSONDecodeError` escaped `Gh.pages`, so `main()` died with a
    traceback and printed NO verdict line — a third output shape."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "not json at all", ""),
    )
    with pytest.raises(rfo.ReadError):
        rfo.Gh(REPO).pages("pulls/1")


def test_read_coverage_per_path_failure_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUND-5 P3: the per-path `select` is inside the fail-closed conversion; without
    that, a raise escapes `collect_probe` and `main()` prints nothing.

    ⛔ The touched path MUST be one whose whole-set selection is NOT `full` — the per-path
    loop only runs under `if not full:` (`_read_coverage`). `tools/ready_for_owner.py` used to
    be such a path only because it was the silent-drop gap: #10975 gave it a
    `ci_selection.TOOL_CARVEOUTS` entry (fail-closed to the FULL matrix), so the whole-set
    call now returns `full: True`, the per-path loop never executes, and this test stopped
    exercising its own subject (`uncovered` came back `()` instead of `None`). A docs-only
    path is the shape that still reaches the loop: `surfaces == []`, `full is False`, with
    the tier-1 smoke files as `test_files` (73 at this head — re-derive it with
    `len(manifest["tier1"])`; this number moves with every classified test added).
    """
    from tools import ci_selection

    real = ci_selection.select
    calls = {"n": 0}

    def flaky(paths, event, manifest, explain=None):
        calls["n"] += 1
        if calls["n"] > 1:  # the first (whole-set) call succeeds, the per-path one raises
            raise RuntimeError("per-path select blew up")
        return real(paths, event, manifest, explain)

    monkeypatch.setattr(ci_selection, "select", flaky)
    uncovered, raw = rfo._read_coverage(("docs/ontology/NOTES.md",))
    assert calls["n"] == 2, f"the per-path arm was not reached at all (calls={calls['n']})"
    assert uncovered is None
    assert "blew up" in raw


def test_collect_probe_routes_an_empty_declared_union_to_the_fail_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUND-3 P3: `if entries:` was falsy for `()`, so a MALFORMED marker took the
    "no union declared" branch and `check_coverage` printed `union=pr-diff` and PASSED —
    the `empty-declared-union` arm was fixture-only, unreachable from the live path."""
    payloads = _gh_payloads(body="READY-GATE UNION: ,\n")
    probe = _collect(monkeypatch, payloads)[0]
    assert probe.union == ()
    assert probe.union_unreadable is False
    verdict = rfo.evaluate(probe)
    coverage = next(c for c in verdict.checks if c.name == "coverage")
    assert coverage.passed is False
    assert coverage.reason == "empty-declared-union"
    assert verdict.ready is False


def test_collect_probe_reads_labels_and_pr_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The label guard `.mergify.yml` refuses at entry must be visible to the gate."""
    payloads = _gh_payloads()
    payloads["pulls/"] = dict(payloads["pulls/"], labels=[{"name": "superseded-by-fold"}])
    probe = _collect(monkeypatch, payloads)[0]
    assert probe.labels == ("superseded-by-fold",)
    verdict = rfo.evaluate(probe)
    exclusions = next(c for c in verdict.checks if c.name == "exclusions")
    assert exclusions.passed is False
    assert "queue-blocking-label" in exclusions.reason


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


def _tree_runner(monkeypatch: pytest.MonkeyPatch, returns: dict[str, subprocess.CompletedProcess]):
    """Answer `git fetch/rev-parse/merge-tree` by subcommand."""

    def fake_run(cmd, timeout, cwd=None):
        for needle, proc in returns.items():
            if needle in " ".join(cmd):
                return proc
        raise AssertionError(f"unexpected git command {' '.join(cmd)}")

    monkeypatch.setattr(rfo, "_run", fake_run)


def _proc(args: list[str], rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, rc, out, err)


def test_read_tree_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    _tree_runner(
        monkeypatch,
        {
            "fetch": _proc(["git"], 0),
            "merge-tree": _proc(["git"], 0, "f" * 40 + "\n"),
            "rev-parse": _proc(["git"], 0, HEAD + "\n"),
        },
    )
    ok, raw = rfo._read_tree(None, 7, HEAD, REPO_ROOT)
    assert ok is True
    assert "merge-tree write-tree ok" in raw


def test_read_tree_conflicts_are_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    _tree_runner(
        monkeypatch,
        {
            "fetch": _proc(["git"], 0),
            "merge-tree": _proc(["git"], 1, "", "CONFLICT (content): tortoise/sdk.py"),
            "rev-parse": _proc(["git"], 0, HEAD + "\n"),
        },
    )
    ok, raw = rfo._read_tree(None, 7, HEAD, REPO_ROOT)
    assert ok is False
    assert "CONFLICTS" in raw


def test_read_tree_refuses_when_the_fetched_head_is_not_the_live_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comparing a stale local object would certify a tree nobody asked about."""
    _tree_runner(
        monkeypatch,
        {
            "fetch": _proc(["git"], 0),
            "merge-tree": _proc(["git"], 0, "f" * 40 + "\n"),
            "rev-parse": _proc(["git"], 0, "b" * 40 + "\n"),
        },
    )
    ok, raw = rfo._read_tree(None, 7, HEAD, REPO_ROOT)
    assert ok is None
    assert "head moved mid-read" in raw


def test_read_tree_unexpected_exit_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    _tree_runner(
        monkeypatch,
        {
            "fetch": _proc(["git"], 0),
            "merge-tree": _proc(["git"], 128, "", "fatal: bad object"),
            "rev-parse": _proc(["git"], 0, HEAD + "\n"),
        },
    )
    ok, raw = rfo._read_tree(None, 7, HEAD, REPO_ROOT)
    assert ok is None
    assert "exited 128" in raw


def test_read_tree_unfetchable_pr_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    _tree_runner(
        monkeypatch,
        {"fetch": _proc(["git"], 1, "", "fatal: couldn't find remote ref")},
    )
    ok, raw = rfo._read_tree(None, 7, HEAD, REPO_ROOT)
    assert ok is None
    assert "git fetch" in raw


def test_read_coverage_on_real_paths_does_not_call_a_core_smoke_file_uncovered() -> None:
    """ROUND-1 P2, at the layer where it lived: a file with no surface of its own but the
    always-on core smoke set still runs is COVERED and must not fail.

    ⛔ The touched path must be one whose whole-set selection is NOT `full` —
    `_read_coverage` only enters its per-path loop under `if not full:`. This test used
    `tools/ready_for_owner.py`, which #10975 gave a `ci_selection.TOOL_CARVEOUTS` entry
    (fail-closed to the FULL matrix): measured, that call is now
    `full=true … core-smoke-only=0`, so `uncovered == ()` came from the full-matrix
    short-circuit and the `has_leg`/`tier1_only` branch this test is named for was never
    reached. Reverting `tier1_only.append(path)` to `uncovered.append(path)` left the whole
    file green, i.e. the pin had been silently lost. A docs-only path is the shape that
    still reaches it (`files=1 full=false surfaces=[] core-smoke-only=1 no-leg=0`), and
    `core-smoke-only=1` is asserted so the branch cannot become unreachable again unnoticed.
    """
    uncovered, raw = rfo._read_coverage(("docs/ontology/NOTES.md",))
    assert uncovered == (), f"{uncovered} reported uncovered — got raw {raw}"
    assert "core-smoke-only=1" in raw, f"the core-smoke branch was not reached: {raw}"
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


@pytest.mark.parametrize(
    "body", ["READY-GATE UNION:", "READY-GATE UNION:\n", "READY-GATE UNION:   "]
)
def test_a_marker_with_nothing_after_the_colon_is_a_malformed_declaration(body: str) -> None:
    """ROUND-4 P3: `(.+)` did not match an empty marker at all, so it fell down the "no union
    declared" branch and PASSED — the same malformed-marker false pass as the `,` form."""
    entries, _raw = rfo.declared_union(body)
    assert entries == (), f"{body!r} must parse to a DECLARED-but-empty union, got {entries!r}"


def test_a_malformed_marker_fails_coverage_end_to_end() -> None:
    verdict = _verdict(union=[], union_raw="declared union has no entries")
    coverage = next(c for c in verdict.checks if c.name == "coverage")
    assert coverage.passed is False
    assert coverage.reason == "empty-declared-union"


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
    return {"required": None, "required_raw": "required-set: .mergify.yml unreadable"}


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


def test_the_retraction_rule_source_is_the_same_text_in_both_forms() -> None:
    """The split named fragments must BE the combined source — not a second copy of it.

    `_retracted_from` grades three windows that must agree about what a retraction is: the
    verdict's own paragraph uses the combined `_RETRACTION_RULES_1_TO_3`; the paragraph after
    uses rules (1) and (2) only (`_NEXT_PARA_RETRACTION_RE`); and the whole suffix uses rule
    (4)'s WORD arm plus the finding-state PHRASES (`_SUFFIX_RETRACTION_RE`). They are therefore
    rebuilt from one set of named fragments. Two independently-typed copies of a regex that
    have to agree is exactly how one of them rots unnoticed — the next rule added to one form
    and not the other would silently reopen a hole. This pin is the tripwire.

    ⛔ THE LAYERS ARE NOT THE SAME SUBSET, AND THE DIFFERENCE IS LOAD-BEARING (review #10975,
    cycle 3): rule (3)'s exception words do not survive detachment, and rule (4)'s CLOSED state
    vocabulary may never be windowed at all. So every fragment must be graded SOMEWHERE, and the
    two exclusions must be exactly those two.
    """
    assert (
        rfo._R1_NO_LONGER + rfo._R2_ATTRIBUTED + rfo._R3_EXCEPTION + rfo._R4_WORDS
    ) == rfo._RETRACTION_RULES_1_TO_3
    # …and the detached form is exactly that minus rule (3) and minus rule (4), not some other
    # subset…
    assert rfo._R3_EXCEPTION not in rfo._NEXT_PARA_RETRACTION_RE.pattern
    assert rfo._R4_WORDS not in rfo._NEXT_PARA_RETRACTION_RE.pattern
    for fragment in (rfo._R1_NO_LONGER, rfo._R2_ATTRIBUTED):
        assert fragment in rfo._NEXT_PARA_RETRACTION_RE.pattern
    # …and rule (4) is not LOST by that exclusion — it is graded over the whole suffix, which is
    # the fail-closed direction (an omission over-blocks; windowing it was a measured fail-open).
    assert rfo._R4_WORDS.lstrip("|") in rfo._SUFFIX_RETRACTION_RE.pattern


def test_the_severity_arm_reads_the_PLURAL() -> None:
    """`P1s` is a review summary's own spelling, and the clear guard must read it.

    Review cycle 7, finding 4. `_SEVERITY_TOKEN_RE` required a word boundary straight after
    `P[0-2]`, so `P1s` never matched — while the SAME module reads the plural in
    `ANY_SEVERITY_TOKEN_RE` (`P([0-3])s?`) and in `SEV` (`P[0-2]s?`), the latter carrying a
    comment that a plural `P1s: 2` had already recorded a clean record because neither reader
    knew the form. A clear guard that cannot see the plural is the third reader with the same
    blind spot, in the one place it decides whether a verdict counts.

    Pinned directly against `asserts_a_clear` rather than through `check_blocking_finding`,
    because `P1s: 2` on its own is ALSO a blocking-finding body for `_grades_as_blocking` —
    end to end the two arms are indistinguishable, and the guard's own behaviour is what this
    arm exists to measure.
    """
    for body in (
        "**Verdict: CLEAN.** — P1s: 2",
        "**Verdict: CLEAN.**\n\nP1s remain open.",
        "**Verdict: CLEAN.** — two P2s are still unfixed.",
        "**Verdict: CLEAN.**\n\nP0s outstanding.",
    ):
        assert rfo.asserts_a_clear(body) is False, body
    # …and the plural in a COMPLETION is still a clear, so the arm is not a blanket ban
    for body in (
        "**Verdict: CLEAN.** No P1s outstanding.",
        "**Verdict: CLEAN.** — all P2s closed.",
        "**Verdict: CLEAN.**\n\nThe P1s are all closed.",
    ):
        assert rfo.asserts_a_clear(body) is True, body


def test_a_completion_cannot_neutralise_a_live_token_from_across_the_sentence() -> None:
    """A clear-side exemption must GOVERN the token it exempts, not merely be nearby.

    Review cycle 8 found this as a **P0**: `_retracted_from` looked for the completion with
    `search(suffix, token.end(), token.end() + 64)` over a pattern whose leading whitespace and
    coordination groups can both match empty. So any `fixed|closed|resolved|addressed|gone`
    within sixty characters stood the arm down — including the NEGATED spellings, which say the
    exact opposite, and including a completion that governs a DIFFERENT token:

        '**Verdict: CLEAN.** The P1 was never fixed.'                    -> CLEARED
        '**Verdict: CLEAN.** The P1 is still not closed.'                -> CLEARED
        '**Verdict: CLEAN.** The P1 has not yet been addressed.'         -> CLEARED
        '**Verdict: CLEAN.** The P1 is nowhere near fixed.'              -> CLEARED
        '**Verdict: CLEAN.** A P1 remains; the P2 was addressed.'        -> CLEARED
        '**Verdict: CLEAN.** A P1 is open; the old flag is gone.'        -> CLEARED
        '**Verdict: CLEAN.**\nFixed the P2, the P1 needs work.'          -> CLEARED
        '**Verdict: CLEAN.**\nFixed the P2 and the P1 needs work.'       -> CLEARED
        '**Verdict: CLEAN.**\n\nP1 open. P2 resolved.'                   -> CLEARED

    Each one reported READY-FOR-OWNER over an open P1. The completion is now `match`ed AT the
    token, the left-arm may not cross a comma, and a completion on the left never governs a
    token that starts its own predicate. This arm is the tripwire for all three.
    """
    for body in (
        "**Verdict: CLEAN.** The P1 was never fixed.",
        "**Verdict: CLEAN.** The P1 is still not closed.",
        "**Verdict: CLEAN.** The P1 has not yet been addressed.",
        "**Verdict: CLEAN.** The P1 is nowhere near fixed.",
        "**Verdict: CLEAN.** The P1 is a live regression. Nothing else was fixed.",
        "**Verdict: CLEAN.** A P1 remains; the P2 was addressed.",
        "**Verdict: CLEAN.** A P1 is open; the old flag is gone.",
        "**Verdict: CLEAN.**\nFixed the P2, the P1 needs work.",
        "**Verdict: CLEAN.**\nFixed the P2 and the P1 needs work.",
        "**Verdict: CLEAN.**\n\nP1 open. P2 resolved.",
    ):
        assert rfo.asserts_a_clear(body) is False, body


def test_the_zero_count_and_the_noun_qualifier_clear_spellings_still_clear() -> None:
    """…and the anchoring must not refuse the shapes where the exemption DOES govern.

    Review cycle 8 also reported the opposite direction, which is what makes the fix above a
    trade rather than a tightening: a summary form this module already reads elsewhere
    (`ZERO_SEVERITY_COUNT_RE` — `P[0-2]: 0|none|nil|zero`), the qualifiers a writer puts inside
    a negation, and a noun or a provenance phrase sitting between the token and its completion
    were all refused.
    """
    for body in (
        "**Verdict: CLEAN.**\n\nP1: none. P2: none.",
        "**Verdict: CLEAN.**\n\nP1 findings: 0",
        "**Verdict: CLEAN.**\n\nP1 — none.",
        "**Verdict: CLEAN.**\n\nNo remaining blockers.",
        "**Verdict: CLEAN.**\n\nZero outstanding blockers.",
        "**Verdict: CLEAN.**\n\nno other blockers",
        "**Verdict: CLEAN.**\n\nThe P1 fix landed in 4f450ae81.",
        "Verdict: CLEAN — the P1 from cycle 3 is gone.",
        "Reviewer verdict: NO ISSUES FOUND. The two P2 findings are closed.",
        "**Verdict: CLEAN.**\n\nI fixed the P1 and the P2 in 4f450ae81.",
    ):
        assert rfo.asserts_a_clear(body) is True, body


def test_a_negation_that_reaches_a_completion_verb_does_not_clear_the_finding() -> None:
    """A negation before the token is a clear ONLY when it governs the token as a noun.

    Fold review, P1. `_SEVERITY_NEGATED_BEFORE_RE` exempted any token whose left neighbour was
    `no|without|zero|none of` — sound for an ABSENCE (`No P1 remains.`) and unsound for a
    NEGATED COMPLETION (`without the P1 being fixed`), where the same word negates the verb and
    the token therefore names a LIVE finding. Measured at the pre-fix head: every body below
    returned `asserts_a_clear=True` / `_grades_as_blocking=False`, and `evaluate()` printed
    `READY-FOR-OWNER` over an open P1 — a full false PASS in the one tool whose contract is
    that a defect may never reach the owner's merge, because he does not review.
    """
    for body in (
        "**Verdict: CLEAN** — without the P1 being fixed.",
        "**Verdict: CLEAN.** None of the P1 findings were addressed.",
        "Verdict: CLEAN. None of the P1 items were addressed.",
        "Verdict: CLEAN — zero of the P1 findings were closed.",
        "**Verdict: CLEAN.** No P1 changes were made.",
        "**Verdict: CLEAN.** No P1 fix landed.",
        "**Verdict: CLEAN.** No P2 was fixed, and no P1 either.",
        # The SECOND review cycle's corpus: the adverb slot between auxiliary and completion is a
        # CLASS, so every adverb works, and the absolute negations (`not one`, `nothing`) are
        # reached by their own arm. Each of these cleared and printed READY-FOR-OWNER before it.
        "**Verdict: CLEAN.** No P1 was ever fixed.",
        "**Verdict: CLEAN.** No P1 was actually addressed.",
        "**Verdict: CLEAN.** None of the P1 findings has ever been fixed.",
        "**Verdict: CLEAN.** None of the P1 items was properly addressed.",
        "**Verdict: CLEAN.** No P1 was fully resolved.",
        "**Verdict: CLEAN.** No P1 whatsoever was addressed.",
        "**Verdict: CLEAN.** Not one P1 was fixed.",
        "**Verdict: CLEAN.** Not a single P1 was addressed.",
        "**Verdict: CLEAN.** Nothing about the P1 was fixed.",
        # The THIRD review cycle's corpus, both of them REGRESSIONS this branch introduced:
        # an absolute negation with a CONTENT noun between it and the token governs that noun,
        # not the token (`Not a single test covers the P1` is the `no test for the P1` shape the
        # file already rules LIVE), and a universal quantifier asserts at least one remains.
        "**Verdict: CLEAN.** Not a single test covers the P1.",
        "**Verdict: CLEAN.** Nothing was ever done about the P1.",
        "**Verdict: CLEAN.** Not all of the P1 findings were fixed.",
        "**Verdict: CLEAN.** Not every P1 was addressed.",
        # …and the adverb slot's sibling: a multi-word auxiliary and `yet` as an adverb of time.
        "**Verdict: CLEAN.** No P1 is yet to be addressed.",
        "**Verdict: CLEAN.** None of the P1 findings have as of today been addressed.",
    ):
        assert rfo.asserts_a_clear(body) is False, body


#: The clears this arm must NOT refuse — the other half of the trade, and the reason the new
#: predicate accepts only COMPLETION verbs: after a `no`, a LIVENESS word is itself negated.
_NEGATED_LIVENESS_CLEARS = (
    "**Verdict: CLEAN.** No P1 remains.",
    "**Verdict: CLEAN.** No P1 is open.",
    "**Verdict: CLEAN.** No P1 needs work.",
    "**Verdict: CLEAN.** No P1 outstanding.",
    "**Verdict: CLEAN.** No P1 issues are outstanding.",
    "**Verdict: CLEAN.** No P1 blockers are left open.",
    "**Verdict: CLEAN.** No P0/P1/P2 findings remain.",
    "**Verdict: CLEAN.** No P1 was left unresolved.",
    # …and the absolute-negation arm must not reach across a clause break: these are real clears.
    "**Verdict: CLEAN.** Nothing else changed; no P1 remains.",
    "**Verdict: CLEAN.** Nothing was broken, and no P1 remains.",
    # The absolute arm must still exempt the ADJACENT absence form — that is the whole point of it.
    "**Verdict: CLEAN.** Not a single P1 remains.",
    "**Verdict: CLEAN.** Nothing about the P1 remains.",
    "**Verdict: CLEAN.**\n\nno other blockers",
)


def test_the_negated_completion_arm_does_not_refuse_real_negated_clears() -> None:
    """The tightening above must not become a blanket ban on `no P<n>`.

    Widening the completion predicate to the liveness words would reintroduce the #10962
    false NOT-READY from the other direction — a real clear refused on a spelling. This arm is
    what makes that a trade instead of an over-block.
    """
    for body in _NEGATED_LIVENESS_CLEARS:
        assert rfo.asserts_a_clear(body) is True, body


def test_the_negated_completion_arms_name_a_completion_and_nothing_else() -> None:
    """`_SEVERITY_COMPLETION_NEGATED_AFTER_RE` must not be quietly widened to liveness words.

    Pinned on the compiled pattern itself: the arm is reachable only from the negated branch,
    so a later edit that adds `remains`/`open`/`needs` here would silently undo the previous
    test's clear list while leaving every body in it still written down.
    """
    pat = rfo._SEVERITY_COMPLETION_NEGATED_AFTER_RE.pattern
    for verb in ("closed", "fixed", "resolved", "addressed", "landed", "gone", "made"):
        assert verb in pat, verb
    for liveness in ("remains", "outstanding", "needs", "unfixed"):
        assert liveness not in pat, liveness


def test_the_word_arm_declares_its_own_gap_rather_than_carrying_it_silently() -> None:
    """⛔ A RECORDED GAP, NOT DESIRED BEHAVIOUR — #11048. Read this before editing.

    Rule (4)'s WORD arm (`_R4_WORDS`) spells a still-live finding as `still open` /
    `remains open` / `unfixed` / `unaddressed`, so a live finding written WITHOUT a severity
    token and WITHOUT the word `open` is invisible to BOTH arms of `check_blocking_finding`:

        '**Verdict: CLEAN.** A critical finding remains.'   asserts_a_clear=True  blocking=False
        '**Verdict: CLEAN.** The concern remains.'          asserts_a_clear=True  blocking=False

    Measured here, not assumed: this arm executes both functions. The consequence is a false
    PASS on the comment itself — `_grades_as_blocking` is false, so `asserts_a_clear` wins and
    the comment CLOSES whatever `open_finding` was set (see `check_blocking_finding`'s OR-chain).

    ⛔ WHY IT IS PINNED INSTEAD OF FIXED. `_R4_WORDS` has NO negation scope, so the one-line
    `|remains?` addition blocks `No issues remain.` and `Nothing remains.` — the permanent
    false NOT-READY class (#10962) this module widens its positive vocabulary to avoid. The
    correct shape is a negation-governed word arm like `_SEVERITY_NEGATED_BEFORE_RE`, which is
    design work; it is carried on #11048 with the sibling corpus gaps.

    ⛔ SO THIS ARM IS A TRIPWIRE: fixing the gap means changing it ON PURPOSE. When #11048
    lands, flip every assertion below and delete this docstring. It exists so the gap cannot be
    re-derived as a fresh P1 finding, and so it cannot be "fixed" only in the over-block
    direction: the naive `|remains?` addition to `_R4_WORDS` makes `_NEXT_PARA_RETRACTION_RE`
    match `remain` in the clear pinned at `tests/test_ready_for_owner.py:800`
    (`**Verdict: CLEAN.**\n\nNo P0, P1 or P2 findings remain.`), which reds that arm — so the
    over-block direction is caught ABOVE, not uncaught as an earlier draft of this docstring
    claimed (review #10975, P3).
    """
    for body in (
        "**Verdict: CLEAN.** A critical finding remains.",
        "**Verdict: CLEAN.** The concern remains.",
    ):
        assert rfo.asserts_a_clear(body) is True, body
        assert rfo._grades_as_blocking(body, {}) is False, body
    # …and the BOUNDARY, so a later tightening knows what it is buying: a severity token, the
    # word `open`, or a NUMBERED count already puts the same finding in front of an arm that
    # does see it. The gap is prose-with-no-token, not "prose is unreadable".
    for body in (
        "**Verdict: CLEAN.** One P1 remains.",
        "**Verdict: CLEAN.** A P1 finding remains.",
        "**Verdict: CLEAN.** A critical finding remains open.",
        "**Verdict: CLEAN.** One important finding remains.",
    ):
        caught = (rfo.asserts_a_clear(body) is False) or rfo._grades_as_blocking(body, {})
        assert caught, body


def test_a_truncated_run_listing_fails_closed_before_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed `workflow_runs` page is a READ FAILURE, so the gates are UNREADABLE.

    `ci_verdict.collect_items` refuses a listing it could not fully read, and that must win
    over attribution: grading "no run matched" against a surface that refused would read a
    broken read as a deferral. `unreadable` (not a plain FAIL) is right here because the
    read can be retried — unlike a config fact.
    """
    probe = _collect(
        monkeypatch,
        _gh_payloads(
            check_runs=_check_runs_for(ENTRY_REQUIRED),
            runs=["not-a-run", None, 17],  # type: ignore[list-item]
        ),
    )[0]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False
    assert gates.unreadable is True
    assert "TRUNCATED" in gates.raw


def test_a_malformed_run_entry_is_refused_by_the_fixture_door_not_a_traceback() -> None:
    """★ The `--fixture` door validates the run listing's SHAPE, so nothing malformed
    reaches attribution — and the verdict it produces is still FAIL(unattributable).

    ⛔ This test used to claim a malformed entry "can reach attribution without passing
    `collect_items`". It cannot: `probe_from_dict` refuses a listing whose entries are not
    mappings (`_fixture_head_runs`), so the entry arrives as "not read". What is pinned
    here is therefore the DOOR, and the two things it must not do are the ones that
    matter:

    * an AttributeError escaping `check_gates` exits **1** through the CLI, which a caller
      reads as "a required check is red" rather than "I could not read the runs" — the
      mis-attribution earlier rounds fixed for a non-integer run `id`;
    * a listing with unreadable entries is NOT a reading. "Not evidence the workflow ran"
      is not "evidence it did not run", so it may not reach the DEFERRED (PASS) arm —
      that is the same conflation `958b235db` retracts for the omitted key, one shape over.

    `_run_emits`' own `Mapping` guard covers the remaining construction (a hand-built
    `Probe`, which both collect doors cannot produce) and is pinned directly by
    `test_the_malformed_entry_guard_holds_on_a_hand_built_probe`.
    """
    probe = rfo.probe_from_dict(
        {
            "pr": 10999,
            "head": HEAD,
            "tree": True,
            "required": list(ENTRY_REQUIRED),
            "required_deferred": list(MERGE_TIME_REQUIRED),
            "merge_emit_files": {MERGE_TIME_REQUIRED[0]: ["python-ci.yml"]},
            "head_runs": ["not-a-run", None, 17],
            "groups": [
                {
                    "job": job,
                    "state": ci_verdict.GROUP_GREEN,
                    "status": "completed",
                    "conclusion": "success",
                    "check_id": 1000 + i,
                }
                for i, job in enumerate(ENTRY_REQUIRED)
            ],
        }
    )
    assert probe.head_runs is None, probe.head_runs
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    # A partially-readable listing is UNATTRIBUTABLE — a verdict, not a crash, not a deferral.
    assert gates.passed is False, gates.raw
    assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason


def test_the_malformed_entry_guard_holds_on_a_hand_built_probe() -> None:
    """★ `_run_emits`' `Mapping` guard is live, and its arm is UNATTRIBUTABLE — not a deferral.

    Both collect doors refuse a non-mapping run entry, so this input is reachable only by
    building the `Probe` directly — which the module's own "every condition is a pure
    function of the Probe" contract documents as supported. Two outcomes are forbidden and
    this pins both: an `AttributeError` escaping `check_gates` (the CLI reports exit 1,
    "a required check is red") and the DEFERRED PASS arm ("no run of the emitter exists"
    is not a measurement when an entry could not be read).
    """
    probe = dataclasses.replace(
        rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
        # `_run_emits` takes a Mapping; this IS the type-confused input the guard exists
        # for, and `Probe` is a documented pure-function-of-the-Probe construction.
        head_runs=("not-a-run",),  # type: ignore[arg-type]
    )
    assert rfo._run_emits("not-a-run", ("python-ci.yml",)) is False  # type: ignore[arg-type]
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    # A verdict, not a traceback — and not a deferral: a listing with an unreadable entry
    # is not a reading, so it takes the same UNATTRIBUTABLE arm as the fixture door.
    assert gates.passed is False, gates.raw
    assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason


def _fixture_with_head_runs(extra: dict[str, object]) -> dict[str, object]:
    """The smallest fixture that reaches the merge-time ABSENCE arm."""
    return {
        "pr": 10999,
        "head": HEAD,
        "tree": True,
        "required": list(ENTRY_REQUIRED),
        "required_deferred": list(MERGE_TIME_REQUIRED),
        "merge_emit_files": {MERGE_TIME_REQUIRED[0]: ["python-ci.yml"]},
        # ⛔ THE REF'S WORKFLOW LISTING (#11175). A recorded emitter is UNATTRIBUTABLE when it
        # names a file the protected ref does not carry, so a fixture exercising the
        # MISSING/DEFERRED arms must state that the emitter still EXISTS — omission is the
        # fail-closed "not read" arm. `python-ci.yml` is the real emitter here and the real
        # `.github/workflows/python-ci.yml` exists at this repo's `origin/main`.
        "base_workflow_files": ["python-ci.yml"],
        "groups": [
            {
                "job": job,
                "state": ci_verdict.GROUP_GREEN,
                "status": "completed",
                "conclusion": "success",
                "check_id": 1000 + i,
            }
            for i, job in enumerate(ENTRY_REQUIRED)
        ],
        **extra,
    }


def test_an_omitted_head_runs_is_unattributable_not_a_deferral() -> None:
    """★ Omission is "not read", never "read it, zero runs" — the PASS arm.

    `()` is not a neutral default for `head_runs`: it asserts a MEASUREMENT (the listing
    was read and carried no matching run), which is exactly the DEFERRED/PASS arm. A
    fixture that never spoke about attribution must therefore land on the fail-closed
    arm, or the door by which tests construct this state silently exercises the pass.
    """
    probe = rfo.probe_from_dict(_fixture_with_head_runs({}))
    assert probe.head_runs is None, probe.head_runs
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason
    assert "UNATTRIBUTABLE" in gates.raw


def test_a_type_confused_head_runs_is_unattributable_not_a_deferral() -> None:
    """★ A listing's SHAPE is validated, not just its presence.

    `tuple(...)` accepts any iterable, so a string becomes a tuple of CHARACTERS and a bare
    mapping becomes its KEYS — neither matches an emitter, and both therefore graded
    `DEFERRED` / PASS, i.e. "read it, no run of the emitter exists". The live path cannot
    produce either shape (`collect_items` yields mappings only), so this is the fixture door,
    but it is the same conflation the omitted-key pin above closes: an unreadable surface is
    not a reading.
    """
    for shape in (
        "python-ci.yml",
        {"path": ".github/workflows/python-ci.yml", "event": "pull_request"},
        ({"path": ".github/workflows/python-ci.yml"}, "not-a-run"),
    ):
        probe = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": shape}))
        assert probe.head_runs is None, (shape, probe.head_runs)
        gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
        assert gates.passed is False, (shape, gates.raw)
        assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (shape, gates.reason)


def test_a_type_confused_emitter_projection_is_unattributable_not_a_deferral() -> None:
    """★ `merge_emit_files` VALUES are shape-validated, one field over from the run listing.

    An emitter spelled as a bare string becomes a tuple of CHARACTERS, and one spelled as a
    bare mapping becomes its KEYS; neither can ever match a run, so each graded the
    context `DEFERRED`/PASS — **at a head where the emitting workflow really ran**. That is
    the same fail-open the `head_runs` shape pin above closes, one field over. Measured at
    `caa4d7e6f`: the correct list yields `missing:<ctx>`/FAIL, both slips yield PASS.

    A malformed value collapses to `()`, which the absence arm reads as UNATTRIBUTABLE
    (FAIL) — never as "read it, no emitter".
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    for shape in (
        "python-ci.yml",
        {"workflow": "python-ci.yml"},
        ["python-ci.yml", 17],
        [],
    ):
        probe = rfo.probe_from_dict(
            _fixture_with_head_runs(
                {"merge_emit_files": {MERGE_TIME_REQUIRED[0]: shape}, "head_runs": ran}
            )
        )
        assert probe.merge_emit_files.get(MERGE_TIME_REQUIRED[0]) == (), (
            shape,
            probe.merge_emit_files,
        )
        gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
        assert gates.passed is False, (shape, gates.raw)
        assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (shape, gates.reason)


def test_a_well_formed_emitter_projection_still_attributes_the_absence() -> None:
    """The counterpart: a correctly-shaped projection keeps deciding MISSING and DEFERRED.

    Without this, the pin above could be satisfied by collapsing every projection to `()`,
    which would trade the fail-open for a permanent FAIL on every merge-time context.
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    probe = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": ran}))
    assert probe.merge_emit_files == {MERGE_TIME_REQUIRED[0]: ("python-ci.yml",)}
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert f"missing:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason

    deferred = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []}))
    assert deferred.merge_emit_files == {MERGE_TIME_REQUIRED[0]: ("python-ci.yml",)}
    gates = next(c for c in rfo.evaluate(deferred).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "DEFERRED(merge-time)(no python-ci.yml run" in gates.raw, gates.raw


def test_an_emitter_recorded_with_its_full_path_still_attributes_the_absence() -> None:
    """★ The EMITTER side of the comparison is reduced to a basename, like the run side.

    `_run_file` reduces an Actions run's `path` (`.github/workflows/python-ci.yml`) to its
    basename before comparing, so an emitter value must be read the same way.
    `_snapshot_emitters` said "Basename only" in a comment and did NOT do it, and nothing
    normalised the value at the comparison either — so a record naming the FULL PATH matched
    no run at all, the match set came back empty, and `_merge_time_absence` answered
    `DEFERRED(merge-time)` — a **PASS** — on a head where the emitting workflow had really
    run. Measured at `995594919`, with a real PR-triggered `python-ci.yml` run on the head:

        emitter as "python-ci.yml"                     -> missing:<ctx>   FAIL (correct)
        emitter as ".github/workflows/python-ci.yml"   -> reason=''       PASS  ← the hole

    An unmatchable emitter spelling is the record-side twin of `_readable_run`'s `path`
    guard: "no run of the emitter exists" may not be concluded from evidence that could
    never match. It is also the shape a RENAMED emitter produces while the record still
    names the old file — the drift the record's own `gate_digest` leg forces a `--recut`
    for in CI, but not in this gate.
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    probe = rfo.probe_from_dict(
        _fixture_with_head_runs(
            {
                # The `--fixture` door is the one that can carry a full path today; it
                # must reach the same verdict the record's own spelling would.
                "merge_emit_files": {MERGE_TIME_REQUIRED[0]: [".github/workflows/python-ci.yml"]},
                "head_runs": ran,
            }
        )
    )
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    # The aggregate ran here, so its absence is MISSING (a FAIL) — never a deferral.
    assert gates.passed is False, gates.raw
    assert f"missing:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason

    # The same normalisation must NOT turn an unattributable projection into a pass: with
    # no run on the head at all, this emitter still means DEFERRED.
    deferred = rfo.probe_from_dict(
        _fixture_with_head_runs(
            {
                "merge_emit_files": {MERGE_TIME_REQUIRED[0]: [".github/workflows/python-ci.yml"]},
                "head_runs": [],
            }
        )
    )
    gates = next(c for c in rfo.evaluate(deferred).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "DEFERRED(merge-time)" in gates.raw, gates.raw


@pytest.mark.parametrize(
    "shape",
    [
        # The exact JSON slip `_fixture_merge_emit_files` exists for. Iterated as
        # CHARACTERS by the basename comparison, it matched nothing → DEFERRED (PASS).
        "python-ci.yml",
        # A bare mapping becomes its KEYS, none of which is a workflow file name.
        {"workflow": "python-ci.yml"},
        # A non-`str` element escaped as an AttributeError, not a verdict.
        ("python-ci.yml", None),
        # A TRUTHY NON-ITERABLE: the shape rule is not a total function unless the
        # container itself is materialised, and iterating this raises `TypeError` out of
        # `check_gates` — read by a caller as "a required check is red".
        17,
        # A ONE-SHOT ITERATOR: `all(...)` in the shape check used to EXHAUST it, so the
        # later comparison saw an empty sequence. Empty, it must be UNATTRIBUTABLE (no
        # emitters) — not "recorded, nothing matched" → DEFERRED, which is what an empty
        # TUPLE is graded as. (The non-empty case needs a run on the head to be observable
        # at all; see `test_a_one_shot_emitter_projection_still_attributes_a_measured_run`.)
        (f for f in ()),
    ],
)
def test_a_type_confused_emitter_on_a_hand_built_probe_is_unattributable(shape: Any) -> None:
    """★ The projection's SHAPE is checked where the verdict is decided, not just at a door.

    `Probe.merge_emit_files` is an ANNOTATION, and this module documents a hand-built
    `Probe` as supported, so a projection that is not a sequence of file names is an input
    the verdict code must survive. Measured at `980e5131e` with only the `--fixture` door
    guarded:

        "python-ci.yml"                 -> PASS (iterated as characters, matched nothing)
        ("python-ci.yml", None)        -> AttributeError out of `check_gates`

    The bare string is a REGRESSION the basename normalisation introduced: pre-fix,
    `"python-ci.yml" in "python-ci.yml"` was `True`, so the arm said MISSING (a FAIL).
    Both now land on UNATTRIBUTABLE — a verdict, and never a deferral.
    """
    probe = dataclasses.replace(
        rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
        merge_emit_files={MERGE_TIME_REQUIRED[0]: shape},
    )
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, (shape, gates.raw)
    assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (shape, gates.reason)


def test_a_non_mapping_emitter_projection_is_unattributable_not_a_traceback() -> None:
    """★ The FIELD itself is checked, not only the value's shape.

    `probe.merge_emit_files.get(context)` raises `AttributeError` for a non-`Mapping`
    field, and `check_gates` converts every unreadable input into a VERDICT — an exception
    escaping to the CLI exits 1, which a caller reads as "a required check is red" rather
    than "I could not read the projection". Measured at `08fab8605`: `merge_emit_files=[]`
    and `merge_emit_files=None` both raised `AttributeError: 'list' object has no attribute
    'get'` from `_merge_time_absence`.
    """
    for field in ([("python-ci.yml",)], None, 17):
        probe = dataclasses.replace(
            rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
            merge_emit_files=field,  # type: ignore[arg-type]
        )
        gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
        assert gates.passed is False, (field, gates.raw)
        assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (field, gates.reason)


def test_a_one_shot_run_listing_is_unattributable_not_a_deferral() -> None:
    """★ The LISTING is materialised too, for the same reason the projection is.

    `head_runs` is a `tuple` on every live path and via both fixture doors, but `Probe`
    documents a hand-built construction. A one-shot iterator was exhausted by the
    readability `all(...)`, leaving the match loop nothing to iterate: the gate then read
    "no run of the emitter exists" from a listing it had itself consumed, and answered
    DEFERRED — a PASS. Measured at `08fab8605` for `iter(...)`, `map(...)` and a generator.
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    for listing in (iter(ran), map(lambda r: r, ran), (r for r in ran)):
        probe = dataclasses.replace(
            rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
            head_runs=listing,  # type: ignore[arg-type]
        )
        gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
        # The emitter's workflow DID run here, so the absence is MISSING — a FAIL either
        # way; what may never happen is DEFERRED (a PASS) on a listing nobody finished.
        assert gates.passed is False, (listing, gates.raw)


def test_a_one_shot_emitter_projection_still_attributes_a_measured_run() -> None:
    """★ A consumed projection is the case that made the one-shot defect a PASS.

    The shape check walked the projection, and the match loop then walked whatever was left
    of it. With a run of the emitting workflow ON THE HEAD, the exhausted projection matched
    nothing and the absence answered `DEFERRED` — a PASS on a head where the aggregate was
    pending or never posted. Measured at `08fab8605`: `iter(...)`, `map(...)` and a generator
    all returned PASS where the materialised tuple returns `missing:` (a FAIL).
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    for projection in (
        iter(("python-ci.yml",)),
        map(lambda f: f, ("python-ci.yml",)),
        (f for f in ("python-ci.yml",)),
    ):
        probe = rfo.probe_from_dict(
            _fixture_with_head_runs(
                {"head_runs": ran, "merge_emit_files": {MERGE_TIME_REQUIRED[0]: ["python-ci.yml"]}}
            )
        )
        probe = dataclasses.replace(probe, merge_emit_files={MERGE_TIME_REQUIRED[0]: projection})
        gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
        assert gates.passed is False, (projection, gates.raw)
        assert f"missing:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (projection, gates.reason)


def test_a_non_iterable_run_listing_is_unattributable_not_a_traceback() -> None:
    """The counterpart on the listing side: `17` is truthy and not iterable."""
    probe = dataclasses.replace(
        rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
        head_runs=17,  # type: ignore[arg-type]
    )
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason


def test_an_explicit_empty_head_runs_still_defers() -> None:
    """The counterpart: a fixture that DOES state "read it, zero runs" keeps DEFERRED.

    Without this, the pin above could be satisfied by deleting the deferred arm.
    """
    probe = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []}))
    assert probe.head_runs == ()
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "DEFERRED(merge-time)" in gates.raw


# ── round-12 review (P3): the deferred COUNT, and a config error's LABEL ────


def _two_merge_time_fixture(*, aggregate_reports: bool) -> dict[str, Any]:
    """Two DECLARED merge-time contexts, at most one of them measured on this head."""
    groups: list[dict[str, Any]] = [
        {
            "job": "docs",
            "state": ci_verdict.GROUP_GREEN,
            "status": "completed",
            "conclusion": "success",
            "check_id": 1000,
        }
    ]
    if aggregate_reports:
        groups.append(
            {
                "job": "aggregate",
                "state": ci_verdict.GROUP_GREEN,
                "status": "completed",
                "conclusion": "success",
                "check_id": 1001,
            }
        )
    return {
        "pr": 10999,
        "head": HEAD,
        "tree": True,
        "required": ["docs"],
        "required_deferred": ["aggregate", "nightly"],
        # `aggregate` is emitted by `python-ci.yml`; `nightly` by a workflow that never
        # ran on this head, so `nightly` is the only context that can be DEFERRED.
        "merge_emit_files": {
            "aggregate": ["python-ci.yml"],
            "nightly": ["nightly.yml"],
        },
        # Both spellings must exist at the ref, or the absence is UNATTRIBUTABLE (#11175).
        "base_workflow_files": ["python-ci.yml", "nightly.yml"],
        "head_runs": [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}],
        "groups": groups,
    }


def test_the_ready_line_counts_the_deferrals_it_made_not_the_declared_contexts() -> None:
    """★ ISSUE 1 (P3): `deferred=` must count DEFERRALS, not declared merge-time contexts.

    `len(probe.required_deferred)` is how many `merge_conditions` names the config
    DECLARES. That is not how many deferrals the verdict MADE: when a merge-time check
    also reported on this head it is measured, and the READY line then asserted a
    deferral of a check the same line reports as `completed/success#…`. The raw is the
    provenance a lane debugging NOT-READY reads, so a count that misstates what the PASS
    rests on is the one thing this raw exists not to do.

    The fixture declares TWO merge-time contexts and measures one, so `len(...)` and the
    real count differ — the only shape that separates them. The converse below pins that
    the count can reach 0, so a constant cannot satisfy this test.
    """
    probe = rfo.probe_from_dict(_two_merge_time_fixture(aggregate_reports=True))
    assert probe.required_deferred == ("aggregate", "nightly")
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    # The evidence, side by side: one context measured green, one genuinely deferred.
    assert "aggregate=completed/success#1001" in gates.raw, gates.raw
    assert "nightly=DEFERRED(merge-time)" in gates.raw, gates.raw
    # Declared = 2. Deferred = 1. Pre-fix this line read `deferred=2`.
    assert "deferred=1 " in gates.raw, gates.raw
    assert "deferred=2" not in gates.raw, gates.raw

    # Converse: nothing is absent, so nothing is deferred — `deferred=0`, not `1`/`2`.
    measured = rfo.probe_from_dict(_two_merge_time_fixture(aggregate_reports=True))
    measured = dataclasses.replace(measured, required_deferred=("aggregate",))
    gates = next(c for c in rfo.evaluate(measured).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "deferred=0 " in gates.raw, gates.raw

    # Converse the other way: neither context reported, and neither emitter's workflow
    # ran here ("read it, zero runs"), so BOTH are deferred — the count tracks the
    # verdict, not a constant 0.
    both_deferred = dataclasses.replace(
        rfo.probe_from_dict(_two_merge_time_fixture(aggregate_reports=False)),
        head_runs=(),
    )
    gates = next(c for c in rfo.evaluate(both_deferred).checks if c.name == "gates")
    assert gates.passed is True, gates.raw
    assert "deferred=2 " in gates.raw, gates.raw


def test_a_required_set_config_error_is_a_FAIL_not_a_retryable_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ ISSUE 2 (P3): a CONFIG error must not be labelled `FAIL(unreadable)` ("retry").

    `RequiredSetConfigError` is a VERDICT about the repo's contract — the committed
    record names a context `.mergify.yml` no longer derives, or the record's own
    `live_result` is `DIVERGED` (the #3016 regression this module exists to catch).
    `_read_required_contexts` collapsed it into a plain `ReadError`, so `check_gates`
    reported `FAIL(unreadable)` — this module's token for "could not be read, RETRY",
    which sends a lane to re-run a read that no retry can fix. The same file keeps the
    split 20 lines below (`every required check is merge-time` ⇒ `FAIL`, not
    `FAIL(unreadable)`) and `--required-set` keeps it too
    (`REQUIRED-SET-CONFIG-ERROR` vs `REQUIRED-SET-UNREADABLE`).
    """

    def diverged(cwd: Any, ref: Any = None, **kw: Any) -> Any:
        raise rfo_required.RequiredSetConfigError(
            "required-contexts record names 'python-ci-gate' and .mergify.yml no longer derives it"
        )

    probe, _ = _collect(monkeypatch, _gh_payloads(), derive=diverged)
    # Both still REFUSE — the set is unread, and nothing guesses it.
    assert probe.required is None
    assert probe.required_deferred is None
    assert probe.required_config_error, "the class must survive the read boundary"
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert gates.unreadable is False, f"pre-fix this was {gates.status}"
    assert gates.status == "FAIL"
    assert gates.reason.startswith("required-set-config-error:"), gates.reason
    assert "no longer derives it" in gates.reason, gates.reason

    # The `--fixture` door carries the same field, so a fixture can express the state.
    fixture = _check(
        "gates",
        required=None,
        required_deferred=None,
        required_config_error="record and config disagree",
    )
    assert fixture.unreadable is False and fixture.status == "FAIL", fixture.status
    assert fixture.reason.startswith("required-set-config-error:"), fixture.reason


def test_an_unreadable_required_set_still_reads_FAIL_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart: a set that could not be READ keeps the retryable label.

    Without this, the fix above could be satisfied by deleting the `unreadable` arm —
    trading a wrong label for a lost distinction, since "I could not read it" IS
    retryable and a lane does need to be told so.
    """

    def unreadable(cwd: Any, ref: Any = None, **kw: Any) -> Any:
        raise rfo_required.RequiredSetUnreadable(".mergify.yml could not be read")

    probe, _ = _collect(monkeypatch, _gh_payloads(), derive=unreadable)
    assert probe.required is None
    assert probe.required_config_error == ""
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False
    assert gates.unreadable is True, gates.status
    assert gates.status == "FAIL(unreadable)", gates.status
    assert not gates.reason.startswith("required-set-config-error:"), gates.reason


# ── an entry WITHOUT `path` is not a readable run (#10991 review, P2 fail-open) ─


def test_a_run_entry_without_a_path_is_unattributable_not_a_deferral() -> None:
    """★ P2 fail-open measured at `b45af8abd`: a run entry lacking `path` graded DEFERRED.

    `_run_file` maps a missing `path` to `""`, which matches no emitter — so an entry that
    merely OMITTED the field fell through to "no run of the emitter exists", the DEFERRED
    (PASS) arm, on a listing nobody could actually read. The `event` field already fails
    closed this way (`_run_emits`); `path` is the other half of the same attribution and
    did not.

    Measured on the pre-fix code at that head: `head_runs=({"event": "pull_request"},)` →
    `python-ci-gate=DEFERRED(merge-time)` and the gate **PASSed**, while the same entry
    WITH its `path` → `MISSING` / FAIL. Reverting `_readable_run` to
    `isinstance(run, Mapping)` reds every shape below, at both doors.
    """
    shapes = (
        {"event": "pull_request"},  # the field is simply absent
        {"path": "", "event": "pull_request"},  # present and empty
        {"path": None, "event": "pull_request"},  # present and null
        {"path": 7, "event": "pull_request"},  # present and not a string
        {"path": "   ", "event": "pull_request"},  # present and blank
    )
    for shape in shapes:
        # Door 1 — the fixture: an unreadable entry is not a reading, so the listing is
        # "not read" and the absence lands on UNATTRIBUTABLE.
        via_fixture = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": [shape]}))
        assert via_fixture.head_runs is None, (shape, via_fixture.head_runs)
        gates = next(c for c in rfo.evaluate(via_fixture).checks if c.name == "gates")
        assert gates.passed is False, (shape, gates.raw)
        assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (shape, gates.reason)

        # Door 2 — a hand-built Probe, which this module documents as supported.
        hand_built = dataclasses.replace(
            rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": []})),
            head_runs=(shape,),  # type: ignore[arg-type]
        )
        gates = next(c for c in rfo.evaluate(hand_built).checks if c.name == "gates")
        assert gates.passed is False, (shape, gates.raw)
        assert f"unattributable:{MERGE_TIME_REQUIRED[0]}" in gates.reason, (shape, gates.reason)


# ── #11175: a STALE `emitters` PROJECTION IS NOT AN ABSENT RUN ─────────────
#
# The record's `emitters` map is rewritten only by `mergify_config_guard --recut` (a weekly
# cron). Rename or delete the emitting workflow, land it on `main` without a re-cut, and the
# record still names the OLD file — which matches no run on any head, so `_run_emits` finds
# nothing, `matched` is empty, and the arm answers DEFERRED … a PASS while the required
# heavy suite never ran. Measured on the reproduction below (one `pull_request` run of
# `python-suite.yml` on the head, the entry checks green):
#
#   record emitter     verdict (PRE-FIX)          verdict (now)
#   python-ci.yml      PASS, deferred=1            FAIL unattributable:python-ci-gate
#   python-suite.yml   FAIL missing:python-ci-gate FAIL missing:python-ci-gate
#
# The two spellings are the SAME head evidence, so only the record can distinguish them —
# which is exactly why the ref's own workflow listing has to be part of the probe.

#: A minimal `.mergify.yml` with the live ENTRY/MERGE split: two cheap entry checks and the
#: heavy aggregate in `merge_conditions`, which is the shape whose absence is attributed.
_STALE_EMITTER_MERGIFY = """\
queue_rules:
- name: main
  queue_conditions:
  - base=main
  - -draft
  - check-success=docs
  - check-success=test-isolation
  merge_conditions:
  - check-success=python-ci-gate
  branch_protection_injection_mode: merge
merge_protections_settings: {}
"""

#: A workflow file GitHub would load. Its CONTENT is irrelevant — every assertion below is
#: about whether the recorded spelling names a file that EXISTS at the ref.
_TRIVIAL_WORKFLOW = """\
name: suite
on: pull_request
jobs:
  python-ci-gate:
    runs-on: ubuntu-latest
    steps:
    - run: 'true'
"""


def _stale_emitter_repo(tmp_path: Path, *, recorded_emitter: str) -> Path:
    """A tree whose record names `recorded_emitter`, while `python-suite.yml` is what exists.

    Models the landed rename: the emitting workflow is `python-suite.yml` at the protected
    ref, and the committed record still carries whatever `--recut` last wrote.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "python-suite.yml").write_text(_TRIVIAL_WORKFLOW, encoding="utf-8")
    (tmp_path / ".mergify.yml").write_text(_STALE_EMITTER_MERGIFY, encoding="utf-8")
    record = tmp_path / "docs" / "ci" / "required-contexts.json"
    record.parent.mkdir(parents=True)
    required = ["docs", "python-ci-gate", "test-isolation"]
    record.write_text(
        json.dumps(
            {
                "required_contexts": required,
                "emitters": {"python-ci-gate": [[recorded_emitter, "python-ci-gate"]]},
                "live_result": "SATISFIED",
                "live_observed_contexts": required,
                "live_verified_at": "2026-11-03T02:35:26Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return tmp_path


def _stale_emitter_gates(
    monkeypatch: pytest.MonkeyPatch, *, recorded_emitter: str, runs: list[dict[str, Any]]
) -> Any:
    """Drive the WHOLE path — `collect_probe` → derivation → record → emitter → run listing.

    ⛔ `collect_probe`, not a hand-built `Probe` (#11175's DoD): the defect is that the
    record's spelling is compared against the head's runs, so a fixture that sets
    `merge_emit_files` directly would assume away the very link that is wrong.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="stale-emitter-") as tmp:
        repo = _stale_emitter_repo(Path(tmp), recorded_emitter=recorded_emitter)
        probe = _collect(
            monkeypatch,
            _gh_payloads(check_runs=_check_runs_for(["docs", "test-isolation"]), runs=runs),
            derive=lambda cwd, ref=None, **kw: rfo_required.derive_from_config(repo),
        )[0]
        assert probe.required_deferred == ("python-ci-gate",), probe.required_raw
        assert probe.required == ("docs", "test-isolation"), probe.required_raw
        # The listing the live path carries from the SAME derivation, asserted so a revert to
        # "list the temp root" (which answers `()` for every repo) cannot pass silently.
        assert probe.base_workflow_files == ("python-suite.yml",), probe.base_workflow_files
        return next(c for c in rfo.evaluate(probe).checks if c.name == "gates")


def test_a_stale_emitter_spelling_is_unattributable_not_a_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ ISSUE #11175 (P2 fail-open): a STALE projection must not buy a DEFERRED (PASS).

    The control below uses the SAME head evidence and only changes the record's spelling, so
    this pair is the defect stated as a table: `python-suite.yml` (the file that exists)
    reads `FAIL missing` because it ran here, while the renamed-away `python-ci.yml` read
    PASS before this arm existed. Revert the arm and this reds on `gates.passed is False`.

    The refusal is deliberately FLEET-WIDE (stated in `_merge_time_absence`'s docstring, per
    the issue's DoD): until a human re-cuts the record, EVERY PR reads
    `NOT-READY(unattributable)`. That is the safe direction — the alternative is a false
    READY — and it is also the true state, because with the emitting workflow gone the
    required aggregate can never be produced and branch protection deadlocks merges anyway.
    """
    gates = _stale_emitter_gates(
        monkeypatch,
        recorded_emitter="python-ci.yml",
        runs=[_workflow_run("python-suite.yml")],
    )
    assert gates.passed is False, gates.raw
    assert "unattributable:python-ci-gate" in gates.reason, gates.reason
    assert "python-ci.yml" in gates.raw and "STALE" in gates.raw, gates.raw
    assert "--recut" in gates.raw, gates.raw  # the remedy is NAMED, not implied


def test_the_recorded_emitter_that_DOES_exist_still_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart — and the #11175 table's second row, on the SAME head evidence.

    Without this, the pin above could be satisfied by refusing every projection, which would
    trade the fail-open for a permanent FAIL on every merge-time context (and `--recut`
    could never clear it).
    """
    gates = _stale_emitter_gates(
        monkeypatch,
        recorded_emitter="python-suite.yml",
        runs=[_workflow_run("python-suite.yml")],
    )
    assert gates.passed is False, gates.raw
    assert "missing:python-ci-gate" in gates.reason, gates.reason
    assert "unattributable" not in gates.reason, gates.reason


def test_a_stale_emitter_on_a_head_with_no_run_at_all_is_still_not_a_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale spelling is UNATTRIBUTABLE even when NOTHING ran on the head.

    This is the half a run-matching heuristic cannot reach: with no runs at all there is no
    "absent because pending" signal to trip, so the DEFERRED arm is the only place a stale
    spelling can land — and it lands there as a PASS. Attribution must be decided by the
    projection's own validity first, or the verdict depends on what GitHub happened to
    schedule.
    """
    gates = _stale_emitter_gates(monkeypatch, recorded_emitter="python-ci.yml", runs=[])
    assert gates.passed is False, gates.raw
    assert "unattributable:python-ci-gate" in gates.reason, gates.reason


def test_the_deferral_arm_survives_a_valid_projection_with_no_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ AND THE PASS ARM MUST STILL BE REACHABLE — or the fix is a permanent refusal.

    A correctly-spelled emitter that simply did not run on this head IS the queue working as
    designed (Mergify settles `merge_conditions` on `mergify/merge-queue/<sha>`), so this
    case must stay READY.
    """
    gates = _stale_emitter_gates(monkeypatch, recorded_emitter="python-suite.yml", runs=[])
    assert gates.passed is True, gates.raw
    assert "python-ci-gate=DEFERRED" in gates.raw, gates.raw


def test_a_run_entry_that_carries_its_path_still_attributes() -> None:
    """The counterpart: the guard must not reject the WELL-FORMED listing.

    Without this, the pin above could be satisfied by refusing every listing — which would
    trade the fail-open for a permanent FAIL on every merge-time context.
    """
    ran = [{"path": ".github/workflows/python-ci.yml", "event": "pull_request"}]
    probe = rfo.probe_from_dict(_fixture_with_head_runs({"head_runs": ran}))
    assert probe.head_runs is not None
    gates = next(c for c in rfo.evaluate(probe).checks if c.name == "gates")
    assert gates.passed is False, gates.raw
    assert f"missing:{MERGE_TIME_REQUIRED[0]}" in gates.reason, gates.reason


# ── `--repo` does not scope the DERIVATION, and the raw says so ─────────────


def test_the_required_sets_raw_discloses_which_repo_it_was_derived_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ `--repo` parameterises the GitHub reads, NOT the required-set derivation.

    Both inputs (`.mergify.yml`, the committed record) come from `origin/main` of the
    checkout the tool runs in, so a foreign `--repo` is judged against THIS repo's
    contract. The mismatch cannot produce a false READY — `_read_tree` fetches
    `refs/pull/<n>/head` from the local `origin` and refuses when the fetched sha ≠ the live
    head of the named repo — but a residual that lives only in prose is one nobody reads,
    and the required set's provenance is supposed to be on every arm. So the raw names it.

    ⛔ HERMETIC — review round 7, P3 note 1. This case used to read the AMBIENT `origin` of
    `REPO_ROOT`: in a `.git`-less extraction, or any checkout whose remote is named
    `upstream` (or a source-tarball build), it failed with
    `repo-scope=UNDETERMINED(required set read from …'s origin/main)` for a reason that has
    nothing to do with the change — while the other 360 cases passed. It is green under
    `actions/checkout` only because that action creates `origin`. The workdir is now a
    throwaway repo whose remote is set HERE, the shape
    `test_the_origin_slug_is_normalised_however_the_remote_url_is_spelled` already uses.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    workdir = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(workdir)], check=True, env=env)
    subprocess.run(
        [
            "git",
            "-C",
            str(workdir),
            "remote",
            "add",
            "origin",
            f"https://github.com/{REPO}.git",
        ],
        check=True,
        env=env,
    )
    monkeypatch.setattr(
        rfo,
        "derive_from_ref",
        lambda cwd, ref=None, **kw: rfo_required.derive_from_config(REPO_ROOT),
    )
    _, same = rfo._read_required_contexts(workdir, REPO)
    assert "repo-scope=origin(" in same, same
    _, foreign = rfo._read_required_contexts(workdir, "someone-else/another-repo")
    assert "repo-scope=MISMATCH" in foreign, foreign
    assert "someone-else/another-repo" in foreign, foreign


@pytest.mark.parametrize(
    ("remote", "expected"),
    (
        ("https://github.com/daniel-ospina/tortoise.git", "daniel-ospina/tortoise"),
        ("https://github.com/daniel-ospina/tortoise.git/", "daniel-ospina/tortoise"),
        ("https://github.com/daniel-ospina/tortoise/", "daniel-ospina/tortoise"),
        ("git@github.com:daniel-ospina/tortoise.git", "daniel-ospina/tortoise"),
        ("/absolute/local/path", ""),
    ),
)
def test_the_origin_slug_is_normalised_however_the_remote_url_is_spelled(
    tmp_path: Path, remote: str, expected: str
) -> None:
    """★ `repo-scope=` must not cry wolf on a legal remote spelling.

    A remote may be spelled `…/tortoise.git/` — `.git` and a trailing slash, in that order.
    Normalising `.git` FIRST made the suffix strip a no-op and left `tortoise.git` as the
    slug, so an exactly-matching checkout printed `repo-scope=MISMATCH(… from
    daniel-ospina/tortoise.git's origin/main, judging daniel-ospina/tortoise …)`. The whole
    value of the token is that a reader can trust it, and a false MISMATCH teaches them to
    ignore it. Swapping the two calls back reds this.
    """
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True, env=env
    )
    assert rfo._origin_slug(tmp_path) == expected
