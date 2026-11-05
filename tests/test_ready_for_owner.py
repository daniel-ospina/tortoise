"""Tests for tools/ready_for_owner.py — condition 2 of the READY-FOR-OWNER gate.

The defect these tests exist for (#10962): the readiness verdict did not agree
with the merge rail, because computing "are all REQUIRED checks green?" began
with a branch-protection read that a fine-grained PAT gets HTTP 403 on. Lanes
then improvised — counting ALL checks, counting only the ones that reported, or
treating `cancelled` as a pass.

Every test below is RED on the old behaviour and on a naive implementation:

  * `cancelled` / `timed_out` / `action_required` / unknown → FAIL, never a pass
  * `skipped` / `neutral` → FAIL (contract clause 3: `check-success` is strict)
  * `null` conclusion on a completed run → FAIL (never falls through a gap)
  * missing check-run → FAIL(missing)
  * in-flight (queued / in_progress / pending / waiting / requested) → FAIL
  * a name in BOTH condition lists → FAIL(config), loud, never de-duplicated
  * unreadable config → the committed snapshot, else FAIL(unreadable)
  * fully green → PASS

Two properties are load-bearing beyond the brief, and both are mutation-proofed
by a test:
  * a RE-RUN must not leave the gate red on an older attempt (a re-run allocates
    a new workflow run id — keying on it was a measured false red on #10895);
  * a NEGATED `-check-success=X` must not satisfy a requirement it inverts.

Hermetic: every tree is a scratch directory; no network, no DB.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import ready_for_owner as rfo  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

MERGIFY_TEMPLATE = """\
queue_rules:
  - name: main
    queue_conditions:
      - base=main
      - -draft
{extra_queue}      - check-success=docs
      - check-success=ai-review-gate
    branch_protection_injection_mode: merge
    merge_conditions:
      - check-success=python-ci-gate
    merge_method: squash
"""


def _write_tree(root: Path, *, mergify: str | None = None, snapshot: list[str] | None = None) -> Path:
    if mergify is not None:
        (root / ".mergify.yml").write_text(mergify, encoding="utf-8")
    if snapshot is not None:
        record = root / "docs" / "ci" / "required-contexts.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"required_contexts": snapshot}), encoding="utf-8")
    return root


def _mergify(extra_queue: str = "") -> str:
    return MERGIFY_TEMPLATE.format(extra_queue=extra_queue)


def run(name: str, status: str = "completed", conclusion: str | None = "success",
        *, run_id: int = 1000, app: str = "github-actions") -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": run_id,
        "app": {"slug": app},
        "details_url": f"https://github.com/o/r/actions/runs/{run_id}/job/{run_id}",
    }


def all_green(**overrides: dict) -> list[dict]:
    """The three required contexts from the template, all green.

    A replacement whose `name` matches no base run is a TEST BUG (it would make
    the assertion vacuous), so it is asserted rather than silently dropped.
    """
    runs = [run("docs"), run("ai-review-gate"), run("python-ci-gate")]
    for replacement in overrides.values():
        for i, existing in enumerate(runs):
            if existing["name"] == replacement["name"]:
                runs[i] = replacement
                break
        else:
            raise AssertionError(f"override {replacement['name']!r} matched no base run")
    return runs


def evaluate(root: Path, runs: list[dict], workflow_of: dict[int, int] | None = None) -> rfo.ConditionResult:
    return rfo.evaluator_for(rfo.resolve_required_set(root), runs, workflow_of)


# ── 1. The derived set ──────────────────────────────────────────────────────


def test_union_is_queue_plus_merge(tmp_path):
    required = rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify()))
    assert required.names == ("docs", "ai-review-gate", "python-ci-gate")
    assert required.queue_names == ("docs", "ai-review-gate")
    assert required.merge_names == ("python-ci-gate",)
    assert required.overlap == ()


def test_non_check_conditions_are_excluded_by_prefix(tmp_path):
    """`base=main`, `-draft`, `-label=…` name no check and must not enter the union."""
    required = rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify("      - -label=superseded-by-fold\n")))
    assert required.names == ("docs", "ai-review-gate", "python-ci-gate")


def test_negated_check_success_does_not_satisfy_a_requirement(tmp_path):
    """`-check-success=X` is the OPPOSITE of `check-success=X`; it must not add X."""
    required = rfo.resolve_required_set(
        _write_tree(tmp_path, mergify=_mergify("      - -check-success=forbidden\n"))
    )
    assert "forbidden" not in required.names


# ── 2. A name in BOTH lists is a CONFIGURATION ERROR ────────────────────────


def test_name_in_both_lists_is_a_config_error(tmp_path):
    both = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
    merge_conditions:
      - check-success=docs
"""
    with pytest.raises(rfo.RequiredSetConfigError) as excinfo:
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=both))
    assert "docs" in str(excinfo.value)


def test_overlap_reaches_the_verdict_as_FAIL_config_not_a_quiet_success(tmp_path):
    both = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
    merge_conditions:
      - check-success=docs
"""
    result = rfo.condition_2(_write_tree(tmp_path, mergify=both), "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_CONFIG
    assert "docs" in result.detail


# ── 2b. The injection MODE is read, never assumed ────────────────────


def test_under_queue_mode_an_overlap_is_LEGAL_not_a_config_error(tmp_path):
    """`queue` mode REQUIRES `merge_conditions` ⊆ `queue_conditions`.

    Hard-coding `merge` semantics reported FAIL(config) for a legal `queue` config
    — a false FAIL on every PR, and a disagreement with the guard's own clause ii.
    """
    queue_mode = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: queue
    queue_conditions:
      - check-success=docs
      - check-success=python-ci-gate
    merge_conditions:
      - check-success=python-ci-gate
"""
    required = rfo.resolve_required_set(_write_tree(tmp_path, mergify=queue_mode))
    assert required.mode == "queue"
    assert required.names == ("docs", "python-ci-gate")


def test_under_queue_mode_nothing_is_deferred_and_a_missing_heavy_check_fails(tmp_path):
    """`queue` injects for QUEUING too, so the heavy check gates ENTRY.

    Deferring it (the `merge` rule) would report PASS at a head with no
    python-ci-gate run — the normal state of every head.
    """
    queue_mode = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: queue
    queue_conditions:
      - check-success=docs
      - check-success=python-ci-gate
    merge_conditions:
      - check-success=python-ci-gate
"""
    runs = [run("docs")]  # python-ci-gate genuinely absent
    result = evaluate(_write_tree(tmp_path, mergify=queue_mode), runs)
    assert result.verdict == rfo.FAIL_MISSING
    row = next(r for r in result.rows if r.name == "python-ci-gate")
    assert row.verdict == rfo.FAIL_MISSING  # NEVER DEFERRED under queue mode


def test_an_absent_injection_mode_defaults_to_queue_like_mergify(tmp_path):
    no_mode = """\
queue_rules:
  - name: main
    queue_conditions:
      - check-success=docs
    merge_conditions:
      - check-success=docs
"""
    required = rfo.resolve_required_set(_write_tree(tmp_path, mergify=no_mode))
    assert required.mode == "queue"
    assert required.names == ("docs",)


def test_an_unknown_injection_mode_is_unreadable(tmp_path):
    """Pin the MODE check itself, not an aggregate verdict.

    This test previously called `condition_2` unpatched, so the assertion was
    satisfied by a LIVE `gh api` 404 on the trailing read rather than by the mode
    branch — deleting the mode check left the suite green. It now asserts the
    branch's own detail and never touches the network.
    """
    weird = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: sideways
    queue_conditions:
      - check-success=docs
"""
    tree = _write_tree(tmp_path, mergify=weird)
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.resolve_required_set(tree)
    assert "unrecognised branch_protection_injection_mode" in str(excinfo.value)
    assert "sideways" in str(excinfo.value)


def test_two_rules_with_different_valid_modes_are_unreadable(tmp_path):
    """Mixed modes cannot be reasoned about as one required set."""
    mixed = """\
queue_rules:
  - name: a
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
  - name: b
    branch_protection_injection_mode: queue
    queue_conditions:
      - check-success=test-isolation
"""
    tree = _write_tree(tmp_path, mergify=mixed)
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.resolve_required_set(tree)
    assert "disagree" in str(excinfo.value)


# ── 3. Unreadable config → snapshot → FAIL(unreadable) ──────────────────────


def test_unreadable_config_falls_back_to_the_committed_snapshot(tmp_path):
    tree = _write_tree(tmp_path, snapshot=["docs", "python-ci-gate"])  # no .mergify.yml
    required = rfo.resolve_required_set(tree)
    assert required.names == ("docs", "python-ci-gate")
    assert "snapshot" in required.source
    # The snapshot has NO entry/merge split, so it reports the set but cannot
    # decide condition 2 — an honest unreadable, not a guessed verdict.
    assert required.entry_merge_split is False
    result = rfo.condition_2(tree, "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert result.detail  # a real reason, never an empty detail


def test_unreadable_config_with_no_snapshot_is_FAIL_unreadable(tmp_path):
    result = rfo.condition_2(tmp_path, "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "unreadable from BOTH sources" in result.detail


def test_an_empty_config_is_unreadable_not_an_empty_pass(tmp_path):
    empty = "queue_rules:\n  - name: main\n    queue_conditions:\n      - base=main\n"
    result = rfo.condition_2(_write_tree(tmp_path, mergify=empty), "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "no positive `check-success=`" in result.detail


def test_an_empty_ENTRY_gate_is_unreadable_not_a_PASS_over_nothing(tmp_path):
    """A set with no entry check leaves every row DEFERRED — nothing was evaluated.

    `derive_from_config` only refuses an EMPTY UNION. A config that names one
    merge-time check and no positive entry check therefore RESOLVES, and under
    `branch_protection_injection_mode: merge` that is an empty ENTRY gate (every PR
    enters unconditionally). Before this test's guard, the PASS arm accepted
    `all(verdict in (PASS, DEFERRED))`, so a head with ZERO check-runs returned
    PASS (exit 0) — a claim about nothing, the fail-open class this tool exists to
    remove.
    """
    entryless = """\
queue_rules:
  - name: main
    queue_conditions:
      - base=main
      - -draft
    branch_protection_injection_mode: merge
    merge_conditions:
      - check-success=python-ci-gate
"""
    tree = _write_tree(tmp_path, mergify=entryless)
    # Zero check-runs on the head: the merge-time row is DEFERRED and no entry row
    # exists — the state that used to read PASS.
    result = evaluate(tree, [])
    assert result.verdict == rfo.FAIL_UNREADABLE, result.verdict
    assert result.rows and all(row.verdict == rfo.DEFERRED for row in result.rows)
    assert "nothing" in result.detail.lower()


def test_a_config_error_does_NOT_fall_back_to_the_snapshot(tmp_path):
    """A config error is a verdict about the config; a snapshot must not mask it."""
    both = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
    merge_conditions:
      - check-success=docs
"""
    tree = _write_tree(tmp_path, mergify=both, snapshot=["docs"])
    result = rfo.condition_2(tree, "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_CONFIG


# ── 4. Green ────────────────────────────────────────────────────────────────


def test_fully_green_is_PASS(tmp_path):
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), all_green())
    assert result.verdict == rfo.PASS
    assert all(row.verdict == rfo.PASS for row in result.rows)


def test_extra_non_required_checks_do_not_affect_the_verdict(tmp_path):
    runs = all_green() + [run("some-unrelated-job", conclusion="failure", run_id=9999)]
    assert evaluate(_write_tree(tmp_path, mergify=_mergify()), runs).verdict == rfo.PASS


# ── 5. Non-green conclusions are never a pass ───────────────────────────────


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "action_required", "stale", "neutral", "skipped", None],
)
def test_non_success_conclusion_on_a_required_check_is_FAIL(tmp_path, conclusion):
    """`skipped`/`neutral` are included deliberately: `check-success` is strict."""
    runs = all_green(ai=_replacement("ai-review-gate", conclusion=conclusion))
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_RED, conclusion
    row = next(r for r in result.rows if r.name == "ai-review-gate")
    assert row.verdict == rfo.FAIL_RED


@pytest.mark.parametrize("status", ["queued", "in_progress", "pending", "waiting", "requested"])
def test_in_flight_is_never_a_pass(tmp_path, status):
    runs = all_green(ai=_replacement("ai-review-gate", status=status, conclusion=None))
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_PENDING, status
    assert result.verdict != rfo.PASS


def test_a_missing_required_check_is_FAIL(tmp_path):
    runs = [run("docs"), run("python-ci-gate")]  # ai-review-gate absent
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_MISSING
    row = next(r for r in result.rows if r.name == "ai-review-gate")
    assert row.verdict == rfo.FAIL_MISSING


def test_a_cancelled_check_is_not_rescued_by_a_green_sibling(tmp_path):
    """One red required check fails the condition no matter how green the rest is."""
    runs = all_green(ai=_replacement("ai-review-gate", conclusion="cancelled"))
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_RED


def test_completed_with_a_null_conclusion_never_falls_through_to_a_pass(tmp_path):
    runs = all_green(ai=_replacement("ai-review-gate", conclusion=None))
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_RED


def test_an_undocumented_conclusion_is_red_not_ignored(tmp_path):
    """Allow-list polarity: a conclusion GitHub has not documented yet must not pass."""
    runs = all_green(ai=_replacement("ai-review-gate", conclusion="some-future-conclusion"))
    assert evaluate(_write_tree(tmp_path, mergify=_mergify()), runs).verdict == rfo.FAIL_RED


# ── 6. A re-run must not leave the gate red on an older attempt ─────────────


def test_a_rerun_to_success_clears_the_earlier_cancelled_attempt(tmp_path):
    """MUTATION PROOF for the #10895 false red.

    A re-run allocates a NEW workflow run id. Keying the grouping on the run id
    splits one check's retry history into two "different" checks and lets the
    OLDEST red win — measured on #10895 (`cancelled` run 41895779449, then
    `success` run 41898449197), a merged and fully-green PR read as FAIL(red).
    """
    runs = all_green(
        ai=_replacement("ai-review-gate", conclusion="cancelled", run_id=100),
    ) + [_replacement("ai-review-gate", run_id=200, conclusion="success")]
    # Both attempts belong to the SAME workflow (a re-run), which is what makes
    # them one check. Without the map the fallback is run-id keying (strict).
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs, {100: 1, 200: 1})
    assert result.verdict == rfo.PASS
    row = next(r for r in result.rows if r.name == "ai-review-gate")
    assert row.conclusion == "success"


def test_a_later_failure_still_reds_after_an_earlier_success(tmp_path):
    """The inverse of the carry rule: the NEWEST attempt decides, both ways."""
    runs = all_green(
        ai=_replacement("ai-review-gate", conclusion="success", run_id=100),
    ) + [_replacement("ai-review-gate", run_id=200, conclusion="cancelled")]
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs, {100: 1, 200: 1})
    assert result.verdict == rfo.FAIL_RED


def test_the_newest_attempt_is_chosen_by_id_not_by_input_order(tmp_path):
    """OLDEST-FIRST input, so a "first item wins" implementation is caught."""
    runs = [
        run("docs"),
        _replacement("ai-review-gate", run_id=100, conclusion="cancelled"),
        _replacement("ai-review-gate", run_id=200, conclusion="success"),
    ]
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs, {100: 1, 200: 1})
    assert result.verdict == rfo.PASS
    row = next(r for r in result.rows if r.name == "ai-review-gate")
    assert row.conclusion == "success"


def test_a_sibling_workflow_with_the_same_job_name_cannot_be_masked(tmp_path):
    """Two DIFFERENT workflows publishing the same job name stay separate.

    Without a workflow identity the newer green (id=200) would hide the older red
    (id=100) of the sibling workflow — a latent false PASS. The workflow map is
    what keeps them apart, so both attempts reach `_evaluate_at_head` and red wins.
    """
    runs = [
        run("docs"),
        _replacement("ai-review-gate", run_id=100, conclusion="cancelled"),
        _replacement("ai-review-gate", run_id=200, conclusion="success"),
    ]
    workflow_of = {100: 1111, 200: 2222}  # DIFFERENT workflows
    result = rfo.evaluator_for(rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())),
                              runs, workflow_of)
    assert result.verdict == rfo.FAIL_RED


def test_a_rerun_of_the_same_workflow_is_collapsed_even_via_run_ids(tmp_path):
    """The SAME workflow id on both attempts is a retry, so the newest decides."""
    runs = [
        run("docs"),
        _replacement("ai-review-gate", run_id=100, conclusion="cancelled"),
        _replacement("ai-review-gate", run_id=200, conclusion="success"),
    ]
    workflow_of = {100: 346071631, 200: 346071631}  # SAME workflow
    result = rfo.evaluator_for(rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())),
                              runs, workflow_of)
    assert result.verdict == rfo.PASS


# ── 6b. Entry vs merge-time ────────────────────────────────────────────────


def test_a_merge_time_check_absent_at_the_head_is_DEFERRED_not_FAIL(tmp_path):
    """`python-ci-gate` lives in `merge_conditions`, so it runs on the QUEUE branch.

    Measured 2026-11-04: all six sampled open PR heads carried zero
    `python-ci-gate` runs. Treating that absence as FAIL(missing) would report
    NOT-READY for every PR forever — the false FAIL this split removes.
    """
    runs = [run("docs"), run("ai-review-gate")]  # python-ci-gate genuinely absent
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.PASS
    row = next(r for r in result.rows if r.name == "python-ci-gate")
    assert row.verdict == rfo.DEFERRED
    assert "queue branch" in row.detail


def test_a_merge_time_check_present_and_RED_still_fails(tmp_path):
    """Deferred is not a licence to ignore a red heavy suite that did report."""
    runs = [run("docs"), run("ai-review-gate"), run("python-ci-gate", conclusion="failure")]
    assert evaluate(_write_tree(tmp_path, mergify=_mergify()), runs).verdict == rfo.FAIL_RED


def test_an_ENTRY_check_absent_is_still_FAIL_even_though_merge_checks_defer(tmp_path):
    """The deferral applies to `merge_conditions` ONLY — entry checks stay mandatory."""
    runs = [run("docs"), run("python-ci-gate")]  # ai-review-gate (entry) absent
    assert evaluate(_write_tree(tmp_path, mergify=_mergify()), runs).verdict == rfo.FAIL_MISSING


def test_the_real_repo_defers_python_ci_gate_at_a_head_without_it():
    """Green-on-reality: a head carrying only the six entry checks is READY."""
    required = rfo.resolve_required_set(REPO_ROOT)
    runs = [
        dict(name=n, status="completed", conclusion="success", id=i,
             app={"slug": "github-actions"}, details_url="")
        for i, n in enumerate(required.queue_names, start=1)
    ]
    result = rfo.evaluator_for(required, runs)
    assert result.verdict == rfo.PASS
    assert next(r for r in result.rows if r.name == "python-ci-gate").verdict == rfo.DEFERRED


# ── 7. The real tree ────────────────────────────────────────────────────────


def test_the_real_repo_derives_the_seven_contexts_the_snapshot_records():
    """Green-on-landing proof: the repo derives the committed snapshot's set."""
    required = rfo.resolve_required_set(REPO_ROOT)
    assert set(required.names) == {
        "pricing-artifact", "docs", "test-isolation", "license-surface",
        "legal-e2e", "ai-review-gate", "python-ci-gate",
    }
    assert required.overlap == ()
    assert required.snapshot_agrees is True


def test_the_real_repo_keeps_docs_in_the_entry_list_not_the_merge_list():
    """`#3016`: `-draft`/`base=main` sit beside the checks; do not "reconcile" them away."""
    mergify = (REPO_ROOT / ".mergify.yml").read_text(encoding="utf-8")
    assert "- -draft" in mergify
    assert "base=main" in mergify
    assert required_is_python_ci_gate_merge_only()


def required_is_python_ci_gate_merge_only() -> bool:
    required = rfo.resolve_required_set(REPO_ROOT)
    return required.merge_names == ("python-ci-gate",)


def test_the_real_repo_agrees_with_the_EXISTING_reader_in_github_scripts():
    """Two independent readers must not drift.

    `.github/scripts/check-required-set.py::load_mergify` is the pre-existing
    extractor (prefix `check-success=`). This tool resolves the set through
    `tools/mergify_config_guard._success_names` instead. Two answers to the same
    question WILL drift unless something pins them, so this test does.
    """
    import importlib.util

    path = REPO_ROOT / ".github" / "scripts" / "check-required-set.py"
    if not path.is_file():
        pytest.skip("check-required-set.py absent")
    spec = importlib.util.spec_from_file_location("crs_for_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["crs_for_test"] = module
    spec.loader.exec_module(module)

    theirs = module.load_mergify(REPO_ROOT / ".mergify.yml")
    ours = rfo.resolve_required_set(REPO_ROOT)

    assert set(ours.queue_names) == set(theirs["queue"])
    assert set(ours.merge_names) == set(theirs["merge"])
    assert set(ours.queue_names) & set(ours.merge_names) == set()


# ── 6c. Page reading is FAIL-CLOSED ────────────────────────────────────────


def test_a_truncated_page_raises_instead_of_dropping_the_page():
    """A dropped page is how a superseding RED attempt disappears.

    If the trailing object is unterminated it never enters `chunks`, and because
    `chunks` is non-empty a `chunks or [stdout]` fallback would not fire either —
    the partial page would vanish silently and the check would read PASS.
    """
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo._split_pages('{"a": 1}{"b": ', "endpoint")
    assert "truncated" in str(excinfo.value)


def test_empty_output_raises_instead_of_returning_a_bogus_page():
    with pytest.raises(rfo.RequiredSetUnreadable):
        rfo._split_pages("", "endpoint")


def test_valid_pages_are_split_and_concatenated():
    pages = rfo._split_pages('{"a": 1}{"a": 2}', "ep")
    assert [json.loads(p) for p in pages] == [{"a": 1}, {"a": 2}]


def test_a_brace_inside_a_string_does_not_confuse_the_splitter():
    pages = rfo._split_pages('{"t": "a}b{"}{"a": 2}', "ep")
    assert len(pages) == 2
    assert json.loads(pages[0]) == {"t": "a}b{"}


# ── 6d. A permissions denial is NOT a rate limit ────────────────────────────


def test_a_permission_403_is_reported_as_a_denial_not_a_rate_limit():
    """The lane PAT's protection denial is a 403 too — do not mislabel it.

    Sending an operator to chase a rate limit that does not exist is the exact
    misdiagnosis this instrument exists to remove, migrated one endpoint over.
    """
    import subprocess as sp

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Resource not accessible by personal access token (HTTP 403)"

    original = sp.run
    sp.run = lambda *a, **k: Proc()
    try:
        with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
            rfo._gh_api_json("repos/o/r/commits/x/check-runs")
        assert not isinstance(excinfo.value, rfo.RateLimited)
        assert "Resource not accessible" in str(excinfo.value)
    finally:
        sp.run = original


def test_a_real_rate_limit_still_raises_RateLimited():
    import subprocess as sp

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "API rate limit exceeded for user ID 1"

    original = sp.run
    sp.run = lambda *a, **k: Proc()
    try:
        with pytest.raises(rfo.RateLimited) as excinfo:
            rfo._gh_api_json("repos/o/r/commits/x/check-runs")
        assert "GH-RATE-LIMITED" in str(excinfo.value)
    finally:
        sp.run = original


def test_a_missing_gh_binary_is_a_verdict_not_a_traceback():
    import subprocess as sp

    def boom(*a, **k):
        raise FileNotFoundError("gh")

    original = sp.run
    sp.run = boom
    try:
        with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
            rfo._gh_api_json("repos/o/r/commits/x/check-runs")
        assert "not on PATH" in str(excinfo.value)
    finally:
        sp.run = original


# ── 6e. Exit codes ─────────────────────────────────────────────────────────


def test_exit_2_when_the_required_set_is_unreadable(tmp_path):
    assert rfo.main(["--required-set", "--root", str(tmp_path)]) == rfo.EXIT_UNREADABLE


def test_exit_0_for_the_real_repo_required_set():
    assert rfo.main(["--required-set", "--root", str(REPO_ROOT)]) == rfo.EXIT_PASS


def test_a_partial_sha_list_is_refused_rather_than_mispaired():
    """Pairing by index would evaluate PR A at PR B's head."""
    with pytest.raises(SystemExit):
        rfo.main(["--pr", "1", "--pr", "2", "--sha", "abc"])


# ── 6f. An unavailable workflow map must not guess ────────────────────────


def test_unavailable_workflow_map_with_two_attempts_is_UNREADABLE_not_red(tmp_path):
    """Two attempts + no workflow identity = undecidable, so neither verdict is issued.

    Guessing "newest wins" is a false red if they are sibling workflows; guessing
    "red wins" is a false red if it is a retry (the measured #10895 case).
    """
    runs = [
        run("docs"),
        _replacement("ai-review-gate", run_id=100, conclusion="cancelled"),
        _replacement("ai-review-gate", run_id=200, conclusion="success"),
        run("python-ci-gate"),
    ]
    result = rfo.evaluator_for(
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())),
        runs, {}, "workflow map unavailable (HTTP 403)",
    )
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "ai-review-gate" in result.detail
    assert "403" in result.detail


def test_unavailable_workflow_map_with_one_attempt_each_still_decides(tmp_path):
    """A single attempt is the whole history: exact under either keying."""
    runs = [run("docs"), run("ai-review-gate"), run("python-ci-gate")]
    result = rfo.evaluator_for(
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())),
        runs, {}, "workflow map unavailable",
    )
    assert result.verdict == rfo.PASS


def test_a_name_whose_attempts_are_missing_from_the_map_is_UNREADABLE(tmp_path):
    """A PARTIAL map is as undecidable as an empty one."""
    runs = [
        run("docs"),
        _replacement("ai-review-gate", run_id=100, conclusion="cancelled"),
        _replacement("ai-review-gate", run_id=200, conclusion="success"),
    ]
    result = rfo.evaluator_for(
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())), runs, {999: 1},
    )
    assert result.verdict == rfo.FAIL_UNREADABLE


# ── 6g. Exit codes in --pr mode ────────────────────────────────────────────


def test_exit_2_when_the_head_cannot_be_read(tmp_path, monkeypatch):
    """`FAIL(unreadable)` is "I could not read it", not "the PR is not ready"."""
    tree = _write_tree(tmp_path, mergify=_mergify())

    def boom(repo, sha):
        raise rfo.RequiredSetUnreadable("boom")

    monkeypatch.setattr(rfo, "fetch_check_runs", boom)
    assert rfo.main(["--pr", "1", "--sha", "deadbeef", "--root", str(tree)]) == rfo.EXIT_UNREADABLE


def test_exit_2_when_the_required_set_is_unreadable_in_pr_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(rfo, "fetch_check_runs", lambda repo, sha: [])
    assert rfo.main(["--pr", "1", "--sha", "deadbeef", "--root", str(tmp_path)]) == rfo.EXIT_UNREADABLE


def test_exit_1_when_a_required_check_is_genuinely_red(tmp_path, monkeypatch):
    tree = _write_tree(tmp_path, mergify=_mergify())
    runs = [run("docs", conclusion="failure"), run("ai-review-gate"), run("python-ci-gate")]
    monkeypatch.setattr(rfo, "fetch_check_runs", lambda repo, sha: runs)
    monkeypatch.setattr(rfo, "fetch_workflow_ids", lambda repo, sha: ({}, None))
    assert rfo.main(["--pr", "1", "--sha", "deadbeef", "--root", str(tree)]) == rfo.EXIT_FAIL


# ── 6h. A malformed payload is a verdict, never a traceback ────────────────


def test_a_non_object_check_run_entry_raises_a_verdict(monkeypatch):
    monkeypatch.setattr(
        rfo, "_gh_api_json", lambda endpoint: {"check_runs": [{"name": "docs"}, "oops"]}
    )
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.fetch_check_runs("o/r", "deadbeef")
    assert "non-object" in str(excinfo.value)


def test_the_check_run_endpoint_pins_filter_all_per_page_and_head_sha(monkeypatch):
    """`filter=all` is the ONE parameter that makes "every attempt" true.

    The default filter is NOT all (the module records 26 of 40 on a real commit),
    and `total_count` SHRINKS with the filter — so the truncation guard cannot fire
    and a required context can simply vanish. That reads as `FAIL(missing)` (a
    permanent block on a legitimate PR) or, if GitHub selects "latest" per
    (app, name), drops a sibling workflow's red in favour of another workflow's
    newer green — the fail-open the `workflow_id` grouping exists to prevent.

    Every other test in this file either monkeypatches `_gh_api_json` for its
    PAYLOAD or replaces `fetch_check_runs` outright, so NONE of them asserts the
    endpoint string: mutating `filter=all` to `filter=latest` left all 83 tests
    green. This is the test that reds it.
    """
    seen: list[str] = []

    def _capture(endpoint: str) -> dict:
        seen.append(endpoint)
        return {"check_runs": [{"name": "docs"}], "total_count": 1}

    monkeypatch.setattr(rfo, "_gh_api_json", _capture)
    rfo.fetch_check_runs("o/r", "deadbeefcafe")

    assert len(seen) == 1, seen
    endpoint = seen[0]
    assert "filter=all" in endpoint, endpoint
    assert "per_page=100" in endpoint, endpoint
    assert "/commits/deadbeefcafe/check-runs" in endpoint, endpoint


def test_a_malformed_payload_reaches_condition_2_as_a_verdict(tmp_path, monkeypatch):
    tree = _write_tree(tmp_path, mergify=_mergify())
    monkeypatch.setattr(rfo, "_gh_api_json", lambda endpoint: {"check_runs": ["oops"]})
    result = rfo.condition_2(tree, "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE


def test_the_workflow_map_failure_is_surfaced_in_the_required_set_notes(tmp_path, monkeypatch):
    tree = _write_tree(tmp_path, mergify=_mergify())
    monkeypatch.setattr(rfo, "fetch_check_runs", lambda repo, sha: [run("docs")])
    monkeypatch.setattr(
        rfo, "fetch_workflow_ids", lambda repo, sha: ({}, "workflow map unavailable (HTTP 403)")
    )
    report = rfo.gate(1, "o/r", "deadbeef", tree)
    notes = report["required_set"]["notes"]
    assert any("403" in n for n in notes)


def test_exit_0_for_a_green_head_in_pr_mode(tmp_path, monkeypatch):
    """A fully-green head exits 0 even while conditions 1/3/4 are stubs.

    The exit status reports CONDITION 2; deriving it from the whole-gate verdict
    would make a merged, green PR exit 1 — "a required check is red" — which is
    the readiness/rail divergence this instrument exists to remove.
    """
    tree = _write_tree(tmp_path, mergify=_mergify())
    runs = [run("docs"), run("ai-review-gate"), run("python-ci-gate")]
    monkeypatch.setattr(rfo, "fetch_check_runs", lambda repo, sha: runs)
    monkeypatch.setattr(rfo, "fetch_workflow_ids", lambda repo, sha: ({}, None))
    assert rfo.main(["--pr", "1", "--sha", "deadbeef", "--root", str(tree)]) == rfo.EXIT_PASS


def test_json_mode_emits_parsable_json_when_a_head_cannot_be_resolved(tmp_path, monkeypatch, capsys):
    """stdout must always be JSON in `--json` mode, and the PR must not vanish."""
    tree = _write_tree(tmp_path, mergify=_mergify())

    def boom(repo, pr):
        raise rfo.RequiredSetUnreadable("cannot resolve head")

    monkeypatch.setattr(rfo, "_resolve_head", boom)
    code = rfo.main(["--pr", "7", "--json", "--root", str(tree)])
    out = capsys.readouterr().out
    doc = json.loads(out)  # would raise on the old plain-text-on-stdout path
    assert code == rfo.EXIT_UNREADABLE
    assert len(doc) == 1 and doc[0]["pr"] == 7
    assert doc[0]["condition_2_verdict"] == rfo.FAIL_UNREADABLE


def test_a_short_but_valid_check_run_set_is_UNREADABLE_not_a_PASS(monkeypatch):
    """`total_count` > the bodies received is a TRUNCATED set, not a clean one."""
    monkeypatch.setattr(
        rfo, "_gh_api_json",
        lambda endpoint: {"total_count": 3, "check_runs": [{"name": "docs", "status": "completed",
                                                             "conclusion": "success", "id": 1}]},
    )
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.fetch_check_runs("o/r", "deadbeef")
    assert "truncated" in str(excinfo.value)
    assert "1 of 3" in str(excinfo.value)


def test_a_complete_set_passes_the_total_count_check(monkeypatch):
    runs = [{"name": "docs", "status": "completed", "conclusion": "success", "id": 1}]
    monkeypatch.setattr(rfo, "_gh_api_json", lambda endpoint: {"total_count": 1, "check_runs": runs})
    assert rfo.fetch_check_runs("o/r", "deadbeef") == runs


def test_short_check_runs_are_unreadable_when_total_count_is_missing(monkeypatch):
    """A guard that disables itself on missing input is fail-open."""
    monkeypatch.setattr(rfo, "_gh_api_json", lambda endpoint: {"check_runs": []})
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.fetch_check_runs("o/r", "deadbeef")
    assert "total_count" in str(excinfo.value)


@pytest.mark.parametrize("total", [True, False])
def test_a_boolean_total_count_is_not_a_count(monkeypatch, total):
    """`isinstance(True, int)` is True and `True == 1`, so a bool slips the guard.

    With `total_count: true` a ONE-element set satisfies `len(runs) < total` and the
    truncation defence never fires — a 1-of-5 set would be returned as complete,
    which is how a dropped red attempt becomes a PASS.
    """
    monkeypatch.setattr(
        rfo, "_gh_api_json",
        lambda endpoint: {"total_count": total, "check_runs": [{"name": "docs"}]},
    )
    with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
        rfo.fetch_check_runs("o/r", "deadbeef")
    assert "total_count" in str(excinfo.value)


def test_a_non_string_details_url_does_not_traceback(monkeypatch):
    runs = [{"name": "docs", "status": "completed", "conclusion": "success",
             "id": 1, "details_url": 12345}]
    monkeypatch.setattr(
        rfo, "_gh_api_json",
        lambda endpoint: {"total_count": 1, "check_runs": runs}
        if "check-runs" in endpoint else {"workflow_runs": []},
    )
    result = rfo.evaluator_for(rfo.resolve_required_set(REPO_ROOT), rfo.fetch_check_runs("o/r", "x"), {})
    assert result.verdict in {rfo.PASS, rfo.FAIL_MISSING, rfo.FAIL_UNREADABLE}


def test_a_container_typed_injection_mode_is_a_verdict_not_a_traceback(tmp_path):
    """A list mode is unhashable — the type check must fire BEFORE `modes.add`.

    Uncaught, the TypeError escaped as a traceback and the process exited 1, which
    reads to a caller as "a required check is red" rather than "unreadable".
    """
    bad = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: [merge]
    queue_conditions:
      - check-success=docs
"""
    result = rfo.condition_2(_write_tree(tmp_path, mergify=bad), "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "must be a string" in result.detail


def test_mixed_type_injection_modes_are_unreadable_not_a_sorted_crash(tmp_path):
    mixed = """\
queue_rules:
  - name: a
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
  - name: b
    branch_protection_injection_mode: 3
    queue_conditions:
      - check-success=test-isolation
"""
    result = rfo.condition_2(_write_tree(tmp_path, mergify=mixed), "o/r", "deadbeef")
    assert result.verdict == rfo.FAIL_UNREADABLE


def test_required_set_json_is_parsable(capsys):
    code = rfo.main(["--required-set", "--json", "--root", str(REPO_ROOT)])
    doc = json.loads(capsys.readouterr().out)
    assert code == rfo.EXIT_PASS
    assert doc["injection_mode"] == "merge"
    assert len(doc["names"]) == 7
    assert doc["evaluable"] is True


def test_required_set_json_on_a_snapshot_only_tree_is_parsable_and_unevaluable(tmp_path, capsys):
    tree = _write_tree(tmp_path, snapshot=["docs"])
    code = rfo.main(["--required-set", "--json", "--root", str(tree)])
    doc = json.loads(capsys.readouterr().out)
    assert code == rfo.EXIT_UNREADABLE
    assert doc["evaluable"] is False
    assert doc["injection_mode"] is None  # never claim a config read that did not happen


def test_required_set_json_on_an_unreadable_tree_is_parsable(tmp_path, capsys):
    code = rfo.main(["--required-set", "--json", "--root", str(tmp_path)])
    doc = json.loads(capsys.readouterr().out)
    assert code == rfo.EXIT_UNREADABLE
    assert doc["verdict"] == rfo.FAIL_UNREADABLE
    assert doc["evaluable"] is False
    assert doc["injection_mode"] is None


def test_required_set_json_on_a_config_error_is_parsable(tmp_path, capsys):
    both = """\
queue_rules:
  - name: main
    branch_protection_injection_mode: merge
    queue_conditions:
      - check-success=docs
    merge_conditions:
      - check-success=docs
"""
    tree = _write_tree(tmp_path, mergify=both)
    code = rfo.main(["--required-set", "--json", "--root", str(tree)])
    doc = json.loads(capsys.readouterr().out)
    assert code == rfo.EXIT_FAIL
    assert doc["verdict"] == rfo.FAIL_CONFIG
    assert doc["injection_mode"] is None  # no invented mode on an unreadable set


# ── 6i. Malformed FIELD types are verdicts, not tracebacks ────────────────


@pytest.mark.parametrize("bad_status", [1, ["completed"], {"a": 1}])
def test_a_non_string_status_does_not_traceback(tmp_path, bad_status):
    runs = [run("docs"), _replacement("ai-review-gate")]
    runs[1]["status"] = bad_status
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict in {rfo.FAIL_PENDING, rfo.FAIL_RED, rfo.FAIL_MISSING}
    assert result.verdict != rfo.PASS


@pytest.mark.parametrize("bad_conclusion", [["success"], {"a": 1}, 7])
def test_a_non_string_conclusion_is_never_green(tmp_path, bad_conclusion):
    runs = [run("docs"), _replacement("ai-review-gate")]
    runs[1]["conclusion"] = bad_conclusion
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_RED
    assert result.verdict != rfo.PASS


def test_a_non_integer_check_run_id_is_unreadable_not_a_guess(tmp_path):
    """Two attempts of ONE check, unorderable ids: refuse rather than keep the first."""
    runs = [run("docs"), _replacement("ai-review-gate", run_id=200)]
    bad = _replacement("ai-review-gate", run_id=300)
    bad["id"] = "900"
    runs.append(bad)
    result = rfo.evaluator_for(
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())),
        runs, {200: 1, 300: 1},
    )
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "cannot be ordered" in result.detail


def test_a_field_that_changes_type_across_pages_is_a_verdict(monkeypatch):
    """A rewritten list field must not raise AttributeError from `.extend`."""
    pages = '{"total_count":2,"check_runs":null}{"total_count":2,"check_runs":[{"name":"docs"}]}'

    class Proc:
        returncode = 0
        stdout = pages
        stderr = ""

    import subprocess as sp
    original = sp.run
    sp.run = lambda *a, **k: Proc()
    try:
        with pytest.raises(rfo.RequiredSetUnreadable) as excinfo:
            rfo._gh_api_json("repos/o/r/commits/x/check-runs")
        assert "changed type across pages" in str(excinfo.value)
    finally:
        sp.run = original


def test_a_snapshot_only_set_refuses_BEFORE_reading_the_head(tmp_path, monkeypatch):
    """The snapshot refusal must not be masked by a read failure.

    Previously `condition_2` fetched check-runs first, so this branch was
    satisfied by a live `gh api` 404 and the guard itself was never exercised —
    deleting it left the suite green.
    """
    tree = _write_tree(tmp_path, snapshot=["docs", "python-ci-gate"])
    required = rfo.resolve_required_set(tree)
    assert required.entry_merge_split is False

    # Network-free: the refusal happens inside evaluator_for.
    result = rfo.evaluator_for(required, [], {})
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "entry/merge split" in result.detail

    def _must_not_be_called(repo, sha):
        raise AssertionError("condition_2 read the head before refusing an unevaluable set")

    monkeypatch.setattr(rfo, "fetch_check_runs", _must_not_be_called)
    via_condition = rfo.condition_2(tree, "o/r", "deadbeef")
    assert via_condition.verdict == rfo.FAIL_UNREADABLE
    assert "entry/merge split" in via_condition.detail


def test_RED_wins_over_in_flight_on_the_same_head(tmp_path):
    """A definitively red check must not be reported as "retry later".

    Every red test used only `completed` and every in-flight test used a single
    pending attempt, so swapping the two arms went undetected: a polling caller
    would wait forever on a PR that can never go green.
    """
    runs = [
        run("docs", conclusion="failure"),
        run("ai-review-gate", status="in_progress", conclusion=None),
    ]
    result = evaluate(_write_tree(tmp_path, mergify=_mergify()), runs)
    assert result.verdict == rfo.FAIL_RED
    assert next(r for r in result.rows if r.name == "docs").verdict == rfo.FAIL_RED
    assert result.verdict != rfo.FAIL_PENDING


def test_a_non_object_run_reaching_the_evaluator_is_a_verdict(tmp_path):
    runs = [run("docs"), "oops"]
    result = rfo.evaluator_for(
        rfo.resolve_required_set(_write_tree(tmp_path, mergify=_mergify())), runs, {}
    )
    assert result.verdict == rfo.FAIL_UNREADABLE
    assert "not an object" in result.detail


# ── helpers ─────────────────────────────────────────────────────────────────


def _replacement(name: str, *, status: str = "completed", conclusion: str | None = "success",
                 run_id: int = 5000) -> dict:
    return run(name, status, conclusion, run_id=run_id)
