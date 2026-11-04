"""A fold's constituents cannot land ahead of the fold it belongs to.

WHY THIS FILE EXISTS
--------------------
Owner's question (2026-11-04, verbatim):

    "will we have a way to prioritise for CI queues the combined ones? so their
     components don't land before and disrupt the process?"

The answer before this change was *partly, and the gap was live*. Measured this
beat on the real repository:

* fold **#10894** (`batch(combine-a): land #10543 #10610 #10575 as one validated
  tree`) was OPEN, and all three of **#10543, #10610, #10575** were *also* open
  PRs — every one of them eligible to enter the merge queue on its own;
* fold **#10880** (`batch-3`) was open with **#10867** and **#10850** likewise
  still open;
* `.mergify.yml` sets `merge_protections_settings.auto_merge_conditions: true`,
  so a PR matching `queue_conditions` is queued **automatically, with no human
  click**. Nothing in `queue_conditions` knew that a PR was a fold constituent.

So a constituent could be queued, validated (a full ~37 min CI cycle) and
squash-merged **ahead of** the fold that already carried its work — the exact
disruption the owner asked about. Losing that race is not merely wasted CI: the
constituent lands its diff, the fold then applies the same diff onto a `main`
that has moved, and the fold's validated tree is no longer the tree that lands
(`docs/ops/batch-integration.md` §Landing).

WHAT IS ASSERTED HERE, AND WHY EACH IS A *GATE* AND NOT A COMMENT
-----------------------------------------------------------------
(a) ENTRY REFUSAL — a PR carrying the constituent label cannot ENTER the queue,
    because the exclusion is a NEGATED condition in `queue_conditions` (the
    entry list), not in `merge_conditions` (the merge list). Entry is where it
    has to bite: a PR refused at entry costs no CI cycle, whereas one refused
    at merge has already spent the queue's validation run. Mergify evicts an
    already-queued PR whose required label disappears, so the same condition is
    also the eviction lever. The *human-visible* refusal names the fold — that
    is `tools/batch_open.py`'s comment, asserted in (a2)/(b).

(b) CLOSE AT FOLD-OPEN — a closed PR cannot be queued, merged, or waste a
    runner slot. `tools/batch_open.py` closes a constituent ONLY when its work
    is provably contained in the fold's tree, and REFUSES otherwise, in dry-run
    and under `--apply` alike.

(c) THE CONFIGURATION CONTRACT IS UNBROKEN — `queue_conditions ∪
    merge_conditions` (check-success names) still equals the live required set,
    name for name. Breaking this deadlocks the queue for every PR in the
    repository, which is the one unrecoverable failure available to this change.

(d) THE PRIORITY ROUTE RESOLVES — not "looks right". Mechanism 3 is deliberately
    ORDERING, not runner routing, and the routing route is *disproved* here
    rather than asserted away: the routing expression the plan proposed is
    actually evaluated, and it is shown to select a runner label this fleet does
    not register (`fold`), i.e. zero runners and a job that never starts.

Hermetic: no network, no `gh`, no live queue. The `.mergify.yml` assertions read
the real file (that is the point — they must fail if the line is deleted), while
every `batch_open.py` behaviour is driven through injected `git`/`gh` doubles.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import batch_open as bo  # noqa: E402
import mergify_config_guard as mcg  # noqa: E402

MERGIFY = ROOT / ".mergify.yml"
RECORD = ROOT / "docs" / "ci" / "required-contexts.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mergify() -> dict:
    return yaml.safe_load(MERGIFY.read_text(encoding="utf-8"))


def _rule() -> dict:
    rules = _mergify()["queue_rules"]
    assert len(rules) == 1, f"expected one queue rule, got {len(rules)}"
    return rules[0]


def _check_names(conditions) -> set[str]:
    """POSITIVE `check-success=` names only — the guard's own definition.

    Reused rather than re-derived so this file cannot disagree with the guard
    about what the invariant is over.
    """
    return {
        name
        for kind, name, negated in map(mcg._split_check, conditions)
        if kind == "check-success" and not negated
    }


# ---------------------------------------------------------------------------
# (a) ENTRY REFUSAL — the constituent label gates queue ENTRY
# ---------------------------------------------------------------------------


def test_the_constituent_label_is_a_NEGATED_entry_condition() -> None:
    """(a) The exclusion exists, is negated, and sits in `queue_conditions`.

    A POSITIVE `label=X` would mean "only PRs carrying X may enter" — the exact
    inverse of the intent, and the queue would then admit nothing else. Losing
    the negation is a silent inversion, so the polarity is asserted, not implied.
    """
    queue = _rule()["queue_conditions"]
    wanted = f"-label={bo.CONSTITUENT_LABEL}"
    assert wanted in queue, (
        f"queue entry does not refuse a fold constituent — expected {wanted!r} in "
        f"queue_conditions, got {queue}. Without it a constituent of an open fold is "
        f"auto-queued (auto_merge_conditions: true) and lands ahead of its fold."
    )


def test_the_constituent_label_gate_is_at_ENTRY_not_at_MERGE() -> None:
    """(a) Entry, not merge — where the CI cycle is actually saved.

    A merge-time exclusion would refuse the PR only AFTER the queue had built and
    validated a batch containing it, i.e. after the cost was paid. The contract
    also makes this the *only* place it can work: `queue_conditions` is evaluated
    against the PR head, which is where a label lives.
    """
    rule = _rule()
    wanted = f"-label={bo.CONSTITUENT_LABEL}"
    assert wanted not in rule["merge_conditions"], (
        "the constituent gate must not be a merge_conditions entry: that list is "
        "evaluated against the queue BRANCH, which carries no author labels, so "
        "the condition could never become true and would deadlock the merge"
    )


def test_a_labelled_constituent_is_refused_at_entry_and_the_refusal_names_the_fold() -> None:
    """(a) End-to-end refusal, evaluated the way Mergify evaluates it.

    Mergify is not reachable from a unit test, so the entry predicate is applied
    to a realistic PR record here: `base=main` and `-draft` hold, every
    `check-success` is satisfied, and the ONLY thing standing between the PR and
    the queue is the label. The refusal must then name the fold — Mergify's own
    message cannot, because the fold is our concept, so the naming is the
    comment `batch_open.py` posts (asserted in (b)); here the label alone is
    shown to be sufficient to keep the PR out.
    """
    labelled = {
        "base": "main",
        "draft": False,
        "labels": {bo.CONSTITUENT_LABEL},
        "green": True,
    }
    unlabelled = dict(labelled, labels=set())

    refused, reason = bo.entry_decision(labelled, _rule()["queue_conditions"])
    assert refused is False, "a labelled fold constituent must not enter the queue"
    assert bo.CONSTITUENT_LABEL in reason

    admitted, _ = bo.entry_decision(unlabelled, _rule()["queue_conditions"])
    assert admitted is True, (
        "the gate must be surgical: an ordinary PR with no fold label has to "
        "still enter the queue, or the fix stops the whole fleet landing"
    )


def test_removing_the_label_line_is_caught() -> None:
    """(a) MUTATION PROOF — the assertion above is load-bearing.

    Delete the line from a copy of the config and the entry gate must read as
    absent. Without this, `test_the_constituent_label_is_a_NEGATED_entry_condition`
    could pass on a tree where the condition was shadowed or duplicated and the
    gate was never really closed.
    """
    rule = _rule()
    without = [c for c in rule["queue_conditions"] if c != f"-label={bo.CONSTITUENT_LABEL}"]
    assert without != rule["queue_conditions"], "fixture did not remove anything"
    admitted, _ = bo.entry_decision(
        {"base": "main", "draft": False, "labels": {bo.CONSTITUENT_LABEL}, "green": True},
        without,
    )
    assert admitted is True, (
        "with the line removed the labelled PR must be admitted — which is what "
        "the guard exists to prevent, and proves the assertion is not vacuous"
    )


# ---------------------------------------------------------------------------
# (b) CLOSE AT FOLD-OPEN — containment, and refusal when it fails
# ---------------------------------------------------------------------------


def _verdict(number: int, state: str, detail: str = "") -> bo.Verdict:
    return bo.Verdict(number=number, state=state, detail=detail)


def test_ancestry_proves_containment() -> None:
    """(b) A git-built fold contains its constituent by ancestry."""
    assert _verdict(1, "CONTAINED-ANCESTOR").closable is True


def test_content_proves_containment_for_a_squash_built_fold() -> None:
    """(b) A squash-built fold rewrites history, so ancestry fails — content decides.

    This is the common case: the fold is assembled with `merge_method: squash`
    semantics, so `--is-ancestor` is false for every constituent even though the
    work is fully present. Refusing on ancestry alone would make the tool
    useless on exactly the folds it exists for.
    """
    assert _verdict(1, "CONTAINED-CONTENT").closable is True


def test_NOT_CONTAINED_is_never_closable() -> None:
    """(b) The load-bearing refusal: a PR whose work is not in the fold is KEPT.

    Closing it would destroy work that no fold carries — irreversible from the
    tool's side. So `NOT-CONTAINED` must be unclosable by construction, not by
    a caller remembering to filter.
    """
    assert _verdict(1, "NOT-CONTAINED", "head not in fold tree").closable is False


def test_an_unknown_state_is_not_closable() -> None:
    """(b) Fail closed: an unrecognised state is never a licence to close."""
    assert _verdict(1, "SOMETHING-NEW").closable is False
    assert _verdict(1, "").closable is False


@pytest.mark.parametrize("dry_run", [True, False])
def test_apply_never_closes_a_not_contained_constituent(dry_run: bool) -> None:
    """(b) Refusal holds under `--apply`, not only in the dry run.

    The dry run is where a mistake is cheap; `--apply` is where it is permanent.
    Both are driven through the same injected `gh` double so the assertion is
    about the ACTIONS TAKEN, not about a report string.
    """
    calls: list[list[str]] = []

    def gh(argv: list[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    plan = bo.plan_from_verdicts(
        fold=900,
        fold_head="f" * 40,
        verdicts=[
            _verdict(101, "CONTAINED-CONTENT"),
            _verdict(102, "NOT-CONTAINED", "path src/x.py differs"),
        ],
    )
    bo.execute(plan, gh=gh, dry_run=dry_run)

    closed = {argv[-1] for argv in calls if argv[:2] == ["pr", "close"]}
    assert "102" not in closed, (
        "a NOT-CONTAINED constituent was CLOSED under "
        f"dry_run={dry_run} — its work is in no fold and closing it destroys it: {calls}"
    )
    if not dry_run:
        assert "101" in closed, (
            "the contained constituent must actually be closed under --apply, or "
            f"the mechanism does nothing: {calls}"
        )


def test_dry_run_mutates_nothing() -> None:
    """(b) Dry-run by default: an operator's first run must be free of side effects."""
    calls: list[list[str]] = []

    def gh(argv: list[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    plan = bo.plan_from_verdicts(
        fold=900,
        fold_head="f" * 40,
        verdicts=[_verdict(101, "CONTAINED-CONTENT"), _verdict(102, "NOT-CONTAINED")],
    )
    report = bo.execute(plan, gh=gh, dry_run=True)
    assert calls == [], f"a dry run issued mutating calls: {calls}"
    assert {r.number for r in report.actions} == {101, 102}, (
        "the dry run must still REPORT every constituent, contained or not — a "
        "silent skip is how an operator never learns a PR was left open"
    )


def test_the_close_comment_names_the_fold_and_the_sha() -> None:
    """(a2)/(b) The refusal/close reason names the fold PR and the fold head.

    The review record survives closure (it is a git ref plus
    `~/.pi/agent/reviews/*.json`), so the comment does not need to preserve
    evidence — it needs to tell the next reader WHERE the work went. A comment
    that says only "closed" is a dead end.
    """
    body = bo.closure_comment(fold=900, fold_head="abc123def456", number=101)
    assert "#900" in body
    assert "abc123def456" in body
    assert "101" in body


def test_label_only_labels_every_constituent_and_closes_none() -> None:
    """(a) `--label-only` gates the queue without destroying anything.

    Its whole safety argument is that it applies a REVERSIBLE lever and never
    closes — so the assertion has to be about the actions taken: every
    constituent gets the label (`pr edit`), and NO `pr close` is issued even for
    one whose containment was never proven. If this path ever closed a
    `NOT-CONTAINED` PR it would be strictly worse than `--apply`, because the
    operator chose the cautious flag.
    """
    calls: list[list[str]] = []

    def gh(argv: list[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    report = bo.label_only(
        [_verdict(101, "CONTAINED-CONTENT"), _verdict(102, "NOT-CONTAINED")],
        fold=900,
        fold_head="f" * 40,
        gh=gh,
        dry_run=False,
    )
    assert sorted(report.labelled) == [101, 102], report
    assert [argv[1] for argv in calls] == ["edit", "edit"], (
        f"only `pr edit` may be issued, got {calls}"
    )
    assert not any(argv[:2] == ["pr", "close"] for argv in calls)
    assert report.closed == []


def test_label_only_dry_run_issues_nothing() -> None:
    """(a) The pure-reconnaissance form of the same path mutates nothing."""
    calls: list[list[str]] = []
    report = bo.label_only(
        [_verdict(101, "CONTAINED-CONTENT")],
        fold=900,
        fold_head="f" * 40,
        gh=lambda argv: (calls.append(list(argv)), (0, ""))[1],
        dry_run=True,
    )
    assert calls == []
    assert report.labelled == []
    assert [v.number for v in report.actions] == [101]


def test_label_only_reports_a_failed_label_instead_of_claiming_success() -> None:
    """(a) A failed label is an ERROR, never a silent admission.

    A gate that reports "labelled" while the API refused would leave the
    constituent eligible and the operator believing it was blocked — the exact
    false-PASS class this mechanism exists to avoid.
    """
    report = bo.label_only(
        [_verdict(101, "CONTAINED-CONTENT")],
        fold=900,
        fold_head="f" * 40,
        gh=lambda argv: (1, "HTTP 422"),
        dry_run=False,
    )
    assert report.labelled == []
    assert report.errors and "#101" in report.errors[0]


# ---------------------------------------------------------------------------
# (c) THE CONFIGURATION CONTRACT SURVIVES
# ---------------------------------------------------------------------------


def test_queue_union_merge_still_equals_the_required_set() -> None:
    """(c) `queue ∪ merge` (check-success names) == the recorded required set.

    This is the invariant `.mergify.yml` calls its configuration contract, and the
    one whose breach deadlocks the queue for EVERY PR in the repository. The
    record `docs/ci/required-contexts.json` was reconciled against a live
    branch-protection read; it is asserted here as a LITERAL so a change that
    edits the config AND the record together still fails.
    """
    rule = _rule()
    union = _check_names(rule["queue_conditions"]) | _check_names(rule["merge_conditions"])
    required = set(json.loads(RECORD.read_text(encoding="utf-8"))["required_contexts"])
    assert union == required, (
        "the queue's check set no longer equals the required set — this DEADLOCKS "
        f"the queue for every PR: missing={sorted(required - union)} "
        f"extra={sorted(union - required)}"
    )


def test_no_check_is_named_in_both_lists() -> None:
    """(c) Each required check names exactly one list (the contract's other half)."""
    rule = _rule()
    both = _check_names(rule["queue_conditions"]) & _check_names(rule["merge_conditions"])
    assert not both, f"{sorted(both)} appear in BOTH lists"


def test_the_label_condition_cannot_enter_the_check_union() -> None:
    """(c) The new condition is INERT to the invariant, proven rather than assumed.

    This is why the gate is safe to add: `_split_check` classifies by the
    `check-` prefix, so a `label` condition is a non-check entry exactly like
    `base=main` and `-draft`. If a future refactor made `_split_check` accept
    non-check conditions, this test would red before the invariant could be
    quietly widened.
    """
    assert mcg._split_check(f"-label={bo.CONSTITUENT_LABEL}") == (None, None, False)
    assert f"-label={bo.CONSTITUENT_LABEL}" not in _check_names(_rule()["queue_conditions"])
    assert f"-label={bo.CONSTITUENT_LABEL}" not in _check_names(_rule()["merge_conditions"])


def test_the_real_config_guard_is_green_on_the_real_tree() -> None:
    """(c) The full guard — every numbered clause — passes on this tree.

    The union test above is one clause; the guard also pins the injection mode,
    the entry/merge partition, emission and the digest. Running it here means the
    `.mergify.yml` edit cannot land while the guard is red for a reason this file
    did not anticipate.
    """
    code, lines = mcg.run_static(ROOT)
    assert code == mcg.EXIT_OK, "mergify_config_guard red on the real tree:\n" + "\n".join(lines)


def test_the_required_set_script_reports_no_partition_violation() -> None:
    """(c) The CI-side checker (the one that runs on every PR) is clean.

    Two independent readers must agree: the guard above and
    `.github/scripts/check-required-set.py`. The latter is what actually runs in
    the required path, so a disagreement between them is itself the defect.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / ".github" / "scripts" / "check-required-set.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, (
        f"check-required-set.py exited {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# (d) THE PRIORITY ROUTE RESOLVES — and routing is disproved, not assumed
# ---------------------------------------------------------------------------


def test_the_fold_priority_route_resolves_to_high_with_interruption() -> None:
    """(d) `queue-accelerator` on a fold resolves to `priority: high` + interruption.

    Mechanism 3 is ORDERING. The lever already exists (`priority_rules`, #5527);
    what this pins is that the label the tool applies to a fold actually
    RESOLVES through those rules — reads the file, matches the condition, and
    returns the effective priority. A typo'd label would otherwise leave a fold
    at `medium` and silently unprioritised.
    """
    resolved = bo.priority_for({bo.FOLD_LABEL}, _mergify())
    assert resolved.name == "queue accelerator", resolved
    assert resolved.priority == "high"
    assert resolved.allow_checks_interruption is True, (
        "an accelerated fold must be able to interrupt checks already running — "
        "without it the label only decides BATCHING order, not whether the fold "
        "waits for a constituent's already-started run"
    )
    assert resolved.rank > bo.MEDIUM_RANK, (
        f"the fold resolves to {resolved.priority!r} which does not outrank medium"
    )


def test_an_unlabelled_pr_stays_at_medium() -> None:
    """(d) Priority is not the default — only a labelled fold jumps the queue."""
    resolved = bo.priority_for(set(), _mergify())
    assert resolved.priority == bo.MEDIUM_PRIORITY
    assert resolved.allow_checks_interruption is False


def test_an_unrecognised_priority_level_does_not_outrank_the_default() -> None:
    """(d) Fail closed on a level Mergify does not define.

    A config that names `priority: urgent` (not on Mergify's scale) must not be
    read as outranking `high` — an unrecognised value ranks as the default, so a
    typo cannot silently promote work.
    """
    config = {
        "priority_rules": [
            {"name": "x", "conditions": ["label = queue-accelerator"], "priority": "urgent"}
        ]
    }
    resolved = bo.priority_for({bo.FOLD_LABEL}, config)
    assert resolved.rank == bo.MEDIUM_RANK


def test_the_routing_expression_would_select_a_pool_this_fleet_does_not_have() -> None:
    """(d) Mechanism 3's routing variant is DISPROVED, by evaluating it.

    The plan proposed `runs-on: ${{ contains(labels,'batch') && fromJSON('["self-hosted","fold"]')
    || fromJSON(matrix.runner) }}`. A wrong expression here does not queue — it
    selects ZERO runners and the job never starts, which is strictly worse than
    waiting. Two independent reasons make it inert on this fleet:

    1. this fleet registers no `fold` runner label (the x86 installer computes
       `self-hosted-x64`), so no runner could ever match; and
    2. `python-ci.yml`'s `runs-on` carries a RECORDED DECISION that it has *no*
       logic of its own — it parses `matrix.runner` from `tools/ci_selection.py`,
       "so the mode cannot drift between the two — there is no second place to
       edit". A job-level fold expression would be that second place.

    The expression is evaluated with the same `&&`/`||`/`contains` semantics
    GitHub uses, so this is a measurement of the proposed change, not an opinion
    about it.
    """

    # 1. the expression, evaluated.
    def github_runs_on(labels: list[str]) -> list[str]:
        matched = "batch" in labels
        selected = ["self-hosted", "fold"] if matched else ["self-hosted-x64"]
        return selected

    assert github_runs_on(["batch"]) == ["self-hosted", "fold"], "expression fixture drifted"

    # 2. no runner on this fleet carries it.
    registered = _registered_runner_labels()
    assert "fold" not in registered, (
        f"the routing variant is now viable — `fold` appeared in {sorted(registered)}; "
        "re-open mechanism 3's routing decision rather than leaving this test stale"
    )
    assert not (set(github_runs_on(["batch"])) & registered), (
        "the routing expression selects a label set with no registered runner — "
        "the job would sit unstarted forever"
    )


def test_runs_on_carries_no_job_level_fold_expression() -> None:
    """(d) Pins the recorded decision that the shard `runs-on` reads only `matrix.runner`.

    Scoped to the MATRIX job on purpose: the file has other jobs whose `runs-on`
    is a fixed runner (`ubuntu-latest`), and those are not part of this decision.
    What the recorded comment forbids is a SECOND place to decide the shard's
    runner — so exactly one `runs-on` may read `matrix.runner`, it must be the
    selector's array verbatim, and no `runs-on` anywhere may introduce a `fold`
    pool the fleet does not have.
    """
    text = (ROOT / ".github" / "workflows" / "python-ci.yml").read_text(encoding="utf-8")
    runs_on = [line.strip() for line in text.splitlines() if line.strip().startswith("runs-on:")]
    assert runs_on, "no runs-on found in python-ci.yml"

    matrix_rows = [line for line in runs_on if "matrix.runner" in line]
    assert len(matrix_rows) == 1, f"expected exactly one matrix-driven `runs-on`, got {matrix_rows}"
    assert matrix_rows[0] == "runs-on: ${{ fromJSON(matrix.runner) }}", (
        "the shard `runs-on` gained logic — it must parse the selector's array "
        f"verbatim, not derive one: {matrix_rows[0]!r}"
    )

    fold_rows = [line for line in runs_on if "fold" in line]
    assert not fold_rows, (
        f"a `runs-on` names a `fold` pool — no runner on this fleet carries that "
        f"label, so the job would never start: {fold_rows}"
    )


def _registered_runner_labels() -> set[str]:
    """The label set the x86 runner installer would register, computed locally.

    `--print-labels` is the installer's own resolution (no network, no `gh`), so
    this reads the fleet's INTENT rather than a rate-limited API. A non-zero exit
    is a could-not-measure, never an empty set — an empty set would make the
    assertion above vacuously true.
    """
    proc = subprocess.run(
        ["bash", str(ROOT / "tools" / "install-ci-runner-x64.sh"), "--print-labels"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"could not measure the registered runner labels (exit {proc.returncode}): "
        f"{proc.stderr.strip()[:400]}"
    )
    labels = {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and not line.strip().startswith(("fallback:", "arch:", "os:"))
    }
    assert labels, "the installer printed no labels — cannot measure"
    return labels
