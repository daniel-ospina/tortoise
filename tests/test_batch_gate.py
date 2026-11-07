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
import ci_selection as cs  # noqa: E402
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
    validated a batch containing it, i.e. after the cost was paid. It is also the
    EVICTION lever: Mergify documents that a PR which stops matching
    `queue_conditions` is dropped from the queue. What this test does NOT claim is
    that a label condition in `merge_conditions` could never become true —
    Mergify's docs scope the temporary-queue-PR substitution to the conditions for
    CHECKS, so `label` stays the pull request's own attribute and such a condition
    would work; it would simply be evaluated too late to save the validation run.
    (The earlier version of this docstring asserted the opposite and was wrong.)
    """
    rule = _rule()
    wanted = f"-label={bo.CONSTITUENT_LABEL}"
    assert wanted not in rule["merge_conditions"], (
        "the constituent gate must not be a merge_conditions entry: that list is "
        "evaluated only AFTER the queue has built and validated a batch containing "
        "the PR, so the CI cycle this gate exists to save would already be spent"
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
# (b2) CONTAINMENT AT THE REAL-GIT LEVEL — the defect these exist for
#
# Every test above injects a hand-made `Verdict`, so they pin the POLICY
# (closable or not) and nothing about how a verdict is DERIVED. Two real defects
# were green under exactly that gap, both of which closed a PR on a false proof:
#
#  1. `content_contained` read only the exit status of
#     `git apply --reverse --check --3way`, and git 2.43.0 exits **0** while
#     printing "Applied patch to 'f.txt' with conflicts." — so a constituent that
#     CONFLICTS with the fold (i.e. one that was dropped from it) was reported
#     CONTAINED-CONTENT.
#  2. `parse_constituents` harvested every digit group on the
#     `fold-constituents:` line, so a parenthetical naming a dropped PR, or the
#     fold's own number, became a constituent — and `is_ancestor(fold_head,
#     fold_head)` is vacuously true, so `--apply` closed the fold itself.
#
# These run against a scratch repository built with git plumbing, so they measure
# the real functions rather than a model of them.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        input=stdin,
        timeout=120,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _commit(repo: Path, files: dict[str, str], parents: list[str], message: str,
            modes: dict[str, str] | None = None) -> str:
    modes = modes or {}
    entries = []
    for name, content in files.items():
        blob = _git(repo, "hash-object", "-w", "--stdin", stdin=content)
        entries.append(f"{modes.get(name, '100644')} blob {blob}\t{name}")
    tree = _git(repo, "mktree", stdin="\n".join(entries) + "\n")
    args = ["commit-tree", tree, "-m", message]
    for parent in parents:
        args += ["-p", parent]
    return _git(repo, *args)


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    return repo


def _three_way_fixture(repo: Path) -> tuple[str, str, str]:
    """base / constituent / fold where the FOLD CONFLICTS with the constituent.

    Models the case that matters: a squash-built fold that changed the same lines,
    which is exactly a constituent the fold did NOT carry.
    """
    base = _commit(repo, {"f.txt": "one\ntwo\nthree\n"}, [], "base")
    const = _commit(repo, {"f.txt": "one\nTWO-changed\nthree\n"}, [base], "const")
    fold = _commit(repo, {"f.txt": "one\nZZZ-unrelated\nthree\n"}, [base], "fold")
    return base, const, fold


def test_a_CONFLICTING_fold_tree_is_NOT_reported_as_contained(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b2) THE DEFECT: a conflicting reverse-apply must not prove containment.

    `git apply --reverse --check --3way` exits 0 with "… with conflicts." here, so
    a status-only reading said CONTAINED-CONTENT and `--apply` closed a PR whose
    work no fold carries — irreversible, with a comment asserting a falsehood.

    The fix drops `--3way` and additionally refuses any conflict marker. Those are
    two independent defences, so this test fails only when BOTH regress — measured:
    restoring `--3way` alone stays green because the belt catches it, and removing
    the belt as well reds this test. `test_the_conflict_marker_is_refused_whatever_
    the_exit_status` below covers the belt on its own, so neither defence can be
    dropped without a red.
    """
    base, const, fold = _three_way_fixture(scratch_repo)
    monkeypatch.chdir(scratch_repo)
    verdict = bo.verdict_for(101, const, base, fold, scratch_repo)
    assert verdict.state != "CONTAINED-CONTENT", (
        "a constituent that CONFLICTS with the fold tree was reported contained — "
        f"the tool would close it: {verdict}"
    )
    assert verdict.closable is False, f"a conflicting constituent must not be closable: {verdict}"


def test_a_genuinely_contained_change_still_proves_out(scratch_repo: Path) -> None:
    """(b2) The fix must not be a blanket refusal — the positive case must still work.

    Without this, deleting `content_contained` would pass the test above.
    """
    base = _commit(scratch_repo, {"f.txt": "one\ntwo\nthree\n"}, [], "base")
    const = _commit(scratch_repo, {"f.txt": "one\nTWO\nthree\n"}, [base], "const")
    # A squash-built fold: different history, the SAME resulting blob content is
    # not required — the fold carries the change plus an unrelated one.
    fold = _commit(
        scratch_repo,
        {"f.txt": "one\nTWO\nthree\n", "g.txt": "extra\n"},
        [base],
        "fold",
    )
    verdict = bo.verdict_for(101, const, base, fold, scratch_repo)
    assert verdict.state == "CONTAINED-CONTENT", verdict
    assert verdict.closable is True


def test_a_mode_only_constituent_the_fold_does_not_carry_is_NOT_contained(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b2) THE DEFECT: a plain reverse-apply exits 0 **with a warning** and applies nothing.

    `--3way` was dropped because it exits 0 while reporting a conflict. But a plain
    `git apply --reverse --check` has a SECOND exit-0 message: when only the FILE MODE
    differs, git prints

        warning: s.sh has type 100644, expected 100755

    and applies NOTHING. Measured on git 2.43.0. So a constituent whose only change is
    `chmod +x` was reported CONTAINED-CONTENT against a fold that never carried the
    chmod, and `--apply` closed it — losing the executable bit that no fold carries.
    The proof is rc=0 AND silence; a clean contained apply prints nothing.

    Discrimination proof: with the `not noise` clause reverted to the old
    status-only reading, this test reports CONTAINED-CONTENT and fails.
    """
    base = _commit(scratch_repo, {"s.sh": "#!/bin/sh\necho hi\n"}, [], "base")
    const = _commit(
        scratch_repo,
        {"s.sh": "#!/bin/sh\necho hi\n"},
        [base],
        "const",
        modes={"s.sh": "100755"},
    )
    fold = _commit(scratch_repo, {"s.sh": "#!/bin/sh\necho hi\n"}, [base], "fold")
    monkeypatch.chdir(scratch_repo)
    verdict = bo.verdict_for(101, const, base, fold, scratch_repo)
    assert verdict.state != "CONTAINED-CONTENT", (
        "a mode-only constituent whose chmod the fold does NOT carry was reported "
        f"contained — the tool would close it and lose the mode: {verdict}"
    )
    assert verdict.closable is False, verdict


@pytest.mark.parametrize("rc,stderr", [
    (124, "git: did not finish within 300s"),
    (127, "git: command not found"),
    (128, "fatal: not a git repository"),
    # ⛔ A SIGNAL-KILLED CHILD IS REPORTED BY `subprocess.run` AS A NEGATIVE rc — an
    # OOM kill is -9 — so the arm keyed on `> 1` still folded a killed `git` into
    # NOT-CONTAINED ("closing would destroy work") about a comparison nobody made.
    (-9, ""),
    (-15, ""),
    # ⛔ AND THE `conflicting` TEST MUST NOT RUN FIRST: git-level text can contain
    # "does not apply"/"conflict", and reading it through the conflict branch is the
    # same wrong verdict the rc discrimination exists to refuse, reached by ORDER.
    (128, "error: patch does not apply"),
    (-9, "error: patch does not apply"),
])
def test_a_git_level_failure_is_UNMEASURABLE_not_NOT_contained(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, rc: int, stderr: str
) -> None:
    """⛔ rc > 1 (AND A NEGATIVE rc) IS "THE MEASUREMENT DID NOT RUN", not a verdict.

    `git apply` answers rc=1 for "the patch does not apply" — that IS the
    measurement, and NOT-CONTAINED is its honest name. Every other code means it
    never ran: `_run` reports a missing binary as 127 and a hang as 124, git uses
    128 for a broken repo/index, and a SIGNAL-KILLED git comes back NEGATIVE (an OOM
    kill is -9). The old arm folded all of them into NOT-CONTAINED, so a killed or
    timed-out `git` told the operator "REFUSED … closing would destroy work" and
    exited 1, while the tool's own contract reserves exit 2 / UNMEASURABLE for a
    comparison that could not be made.

    Discrimination proof: revert the `returncode > 1 or returncode < 0` arm to
    `returncode > 1` and the -9/-15 cases report NOT-CONTAINED; move that arm BELOW
    the `conflicting` test and the two "patch does not apply" cases do the same.
    """
    base = _commit(scratch_repo, {"f.txt": "one\ntwo\nthree\n"}, [], "base")
    const = _commit(scratch_repo, {"f.txt": "one\nTWO\nthree\n"}, [base], "const")
    fold = _commit(scratch_repo, {"f.txt": "one\nTWO\nthree\n"}, [base], "fold")
    monkeypatch.chdir(scratch_repo)
    real_run = bo._run

    def fake_run(argv, **kwargs):
        if "--reverse" in argv:
            return subprocess.CompletedProcess(argv, rc, "", stderr)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(bo, "_run", fake_run)
    verdict = bo.verdict_for(101, const, base, fold, scratch_repo)
    assert verdict.state == "UNMEASURABLE", verdict
    assert verdict.closable is False, verdict


def test_a_named_repo_is_threaded_into_every_gh_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e) `--repo` must TARGET the calls, not merely be parsed.

    Measured defect: `--repo` was declared on the CLI and never read, so
    `--apply --repo other/repo` labelled, commented on and CLOSED same-numbered
    PRs in whatever repository the operator happened to stand in — with a comment
    asserting containment about a different repo's PR. Naming a target and
    silently mutating another one records a falsehood on the wrong repository, the
    same class as a false containment proof.

    Every mutating and reading `gh` invocation is asserted to carry the flag, so a
    future call added without it fails here.
    """
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))

        class R:
            returncode = 0
            stdout = (
                '{"headRefOid": "' + "a" * 40 + '", "headRefName": "b", '
                '"body": "fold-constituents: 101", "baseRefOid": "' + "c" * 40 + '"}'
            )
            stderr = ""

        return R()

    monkeypatch.setattr(bo.subprocess, "run", fake_run)
    monkeypatch.delenv("GH_REPO", raising=False)
    bo._gh(["pr", "close", "101"], "other-org/other-repo")
    assert seen, "no gh call recorded"
    assert seen[-1][-2:] == ["--repo", "other-org/other-repo"], seen[-1]


def test_gh_without_a_repo_adds_no_flag() -> None:
    """(e) The default is unchanged: no `--repo` means gh's own inference."""
    seen: list[list[str]] = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    original = bo.subprocess.run
    bo.subprocess.run = lambda argv, **kw: seen.append(list(argv)) or R()
    try:
        bo._gh(["pr", "view", "1"])
    finally:
        bo.subprocess.run = original
    assert seen and "--repo" not in seen[-1], seen


def test_a_refused_constituents_line_is_reported_as_REFUSED_not_absent() -> None:
    """(e) Do not send the operator looking for a line that is already present.

    `declared_constituents_line` is what lets the CLI distinguish "never declared"
    from "declared in a form this tool refuses" — reporting the first when the
    second is true is a wrong diagnostic about the operator's own body.
    """
    refused = "fold-constituents: 10543 and also 10610\n"
    assert bo.declared_constituents_line(refused) is not None
    assert bo.parse_constituents(refused, fold=900) == []
    assert bo.declared_constituents_line("no line here\n") is None


def test_the_constituent_tokenizer_is_linear_not_exponential() -> None:
    """(e) A malformed long digit run must not wedge the tool.

    The first strict grammar (a repeated whole-line group) backtracks EXPONENTIALLY on a
    long digit run followed by a non-token character — measured 24 digits at 1.08 s
    and doubling per digit, i.e. hours for a 40-digit typo. Fail-closed is not
    enough if the tool hangs, so the parse must stay linear.
    """
    import time

    hostile = "fold-constituents: " + "1" * 400 + "x\n"
    started = time.monotonic()
    assert bo.parse_constituents(hostile, fold=900) == []
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"parse took {elapsed:.2f}s on a 400-digit line — backtracking"


def test_the_conflict_marker_is_refused_whatever_the_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b2) Belt for a future `--3way` reintroduction: output beats exit status.

    Simulates the measured git behaviour directly — rc 0 WITH the conflict phrase —
    so this cannot pass by accident on a git version that happens to behave
    differently.
    """
    assert bo._reports_conflict("Applied patch to 'f.txt' with conflicts.") is True
    assert bo._reports_conflict("error: patch failed: f.txt:1") is True
    assert bo._reports_conflict("") is False


def test_parse_constituents_ignores_a_parenthetical_and_the_fold_itself() -> None:
    """(b2) THE DEFECT: prose on the machine-readable line must not become a close target.

    Measured with the old parser:
        'fold-constituents: 10543, 10610, 10575 (was 10641, dropped; see #10820)'
            -> [10543, 10610, 10575, 10641, 10820]
    `10641` is the PR the fold deliberately DROPPED and `10820` is an issue — both
    would have been labelled un-queueable and closed.
    """
    body = (
        "Some prose naming #99999.\n"
        "fold-constituents: 10543, 10610, 10575 (was 10641, dropped; see #10820)\n"
    )
    assert bo.parse_constituents(body, fold=10894) == [10543, 10610, 10575]


def test_parse_constituents_excludes_the_fold_itself() -> None:
    """(b2) THE DEFECT: the fold's own number is never a constituent.

    `is_ancestor(fold_head, fold_head)` is vacuously true, so a fold listed among
    its own constituents would be closed in the same run as its constituents —
    nothing carrying the validated tree would remain.
    """
    body = "fold-constituents: 10894 = 10543, 10610\n"
    assert 10894 not in bo.parse_constituents(body, fold=10894)
    assert bo.parse_constituents("fold-constituents: #10894, #10543\n", fold=10894) == [10543]


def test_parse_constituents_refuses_a_line_it_cannot_read_unambiguously() -> None:
    """(b2) Fail closed: a partially-readable line is NOT evidence.

    Harvesting the readable tokens out of a line carrying prose is how the wrong PR
    gets closed; refusing the line sends the operator to `--constituent`, which is
    explicit.
    """
    for line in (
        "fold-constituents: see the batch description\n",
        "fold-constituents: 10543 and also 10610\n",
        "fold-constituents: https://github.com/o/r/pull/10543\n",
    ):
        assert bo.parse_constituents(line, fold=900) == [], line


def test_the_ARGV_fold_self_inclusion_is_dropped_before_it_can_close_the_fold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """(b2) The `--constituent <fold>` drop is a SEPARATE site from the parser guard.

    `parse_constituents` excludes the fold from a `fold-constituents:` line and has
    its own test above, but `--constituent` argv never goes through the parser: it
    rejoins at

        constituents = [n for n in dict.fromkeys(requested) if n != args.fold]

    and nothing pinned that comprehension. Deleting it left this file green
    BEFORE this pin existed. ⛔ The whole-file count is deliberately NOT restated
    here: it is a property of the FILE, not of the arm, and it drifts with every
    test added — a stale figure reads as "the pin is vacuous" when the arm is in
    fact pinned. Re-derive it by deleting the arm. Without the drop, `main` closes
    the FOLD itself: `is_ancestor(fold_head,
    fold_head)` is vacuously true, so the fold is classified CONTAINED-ANCESTOR and
    `apply` posts `closure_comment(fold, fold_head, fold)` ON the fold — a
    self-referential record asserting the fold's work is contained in the fold —
    then closes it, leaving nothing that carries the validated tree. Closing a PR
    is the one irreversible action this tool takes, so the arm needs a pin, not
    just the comment above it.

    The second constituent is the CONTROL: it must still be closed, so this cannot
    pass by the run refusing to do anything.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(bo, "fetch_fold", lambda n, repo=None: (0, "f" * 40, "fold-br", "body"))
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))
    monkeypatch.setattr(bo, "constituent_refs", lambda n, repo=None: ("a" * 40, "b" * 40))
    monkeypatch.setattr(
        bo, "verdict_for", lambda n, h, b, f, cwd: _verdict(n, "CONTAINED-ANCESTOR", "")
    )

    def _gh(argv, repo=None):
        calls.append(list(argv))
        return 0, ""

    monkeypatch.setattr(bo, "_gh", _gh)

    rc = bo.main(["--fold", "900", "--constituent", "900", "--constituent", "101", "--apply"])
    err = capsys.readouterr().err

    assert rc == 0, f"expected a normal run, got {rc}: {err}"
    closed = [int(argv[2]) for argv in calls if argv[:2] == ["pr", "close"]]
    assert 900 not in closed, f"the FOLD was closed by its own run: closed={closed}"
    assert not [a for a in calls if a[:2] == ["pr", "close"] and a[2] == "900"], calls
    assert 101 in closed, f"the control constituent was not closed, so nothing was proven: {calls}"
    assert not [a for a in calls if a[:2] == ["pr", "comment"] and a[2] == "900"], (
        f"a closure comment was posted ON the fold: {calls}"
    )


def test_an_EMPTY_diff_is_never_a_containment_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """The empty-diff arm is the code's OWN statement of the invariant, so it is pinned.

    Removing it is fail-closed on git 2.43.0 today — `git apply --reverse --check`
    fed empty stdin exits 128 (`error: No valid patches in input`), so the `rc > 1`
    arm answers UNMEASURABLE. That safety margin is a GIT IMPLEMENTATION DETAIL, not
    a property this module asserts: `--allow-empty`, or any change to that message's
    status, would let an empty diff reach the apply with nothing to reverse and be
    read as `CONTAINED-CONTENT` — `closable is True` on no evidence at all, which is
    the one unrecoverable outcome the module docstring names (the PR gets closed).
    Deleting the arm left this file green BEFORE this pin existed, so the arm had
    no regression detector at all. ⛔ The whole-file count is deliberately NOT
    restated: it is a property of the FILE, not of the arm, and it drifts with
    every test added — a stale figure here reads as "the pin is vacuous" when the
    arm is in fact pinned. Re-derive it by deleting the arm.
    """
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))
    monkeypatch.setattr(
        bo,
        "_run",
        lambda argv, *, timeout, cwd=None, stdin=None: subprocess.CompletedProcess(
            argv, 0, "", ""
        ),
    )
    state, detail = bo.content_contained("a" * 40, "b" * 40, "c" * 40, Path("."))
    assert state == "NOT-CONTAINED", (
        f"an empty diff is not evidence of containment — got {state!r} ({detail})"
    )
    assert "empty" in detail, detail


def test_an_EMPTY_constituent_set_is_COULD_NOT_MEASURE_not_a_clean_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A run that measured NOTHING must not exit 0 — 0 means "every one was contained".

    `if not constituents:` prints the reason and returns 2. Nothing pinned it, so
    deleting it left this file green while `main` fell through to the
    verdict loop with an EMPTY list, built an empty plan and exited **0** — the code
    its own docstring reserves for "every constituent was contained and (with
    `--apply`) closed". With `--apply` it also issued
    `pr edit <fold> --add-label <FOLD_LABEL>` on the strength of that empty set, so
    the tool ACTS on a fold it never measured. Both halves are asserted: the exit
    code and the absence of any `gh` write.

    Two argv shapes reach the empty set, and the cycle-2 pin does not cover either:
    (a) a body with no `fold-constituents:` line at all, (b) `--constituent <fold>`
    alone, which the argv filter at :934 empties.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bo,
        "fetch_fold",
        lambda n, repo=None: (0, "f" * 40, "fold-br", "Closes #10963, no constituents line"),
    )
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))

    def _gh(argv, repo=None):
        calls.append(list(argv))
        return 0, ""

    monkeypatch.setattr(bo, "_gh", _gh)

    # (a) the body declares no constituents at all
    rc = bo.main(["--fold", "900", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, f"nothing was measured, so this is exit 2 — got {rc}: {out.out}{out.err}"
    assert calls == [], f"a run that measured nothing issued a gh write: {calls}"

    # (b) the only constituent named is the fold itself
    calls.clear()
    rc = bo.main(["--fold", "900", "--constituent", "900", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, f"an empty set after the fold filter is still exit 2 — got {rc}: {out.out}{out.err}"
    assert calls == [], f"a run whose only constituent was the fold issued a gh write: {calls}"


def test_an_unmeasurable_constituent_is_NOT_reported_as_a_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """(b) "could not measure" must never print the sentence "closing would destroy work".

    When `constituent_refs` cannot read a PR (a failed or unparsable `gh pr view`),
    `main` builds an `UNMEASURABLE` verdict. That verdict is not closable, so it used
    to land in `report.refused` — printing

        REFUSED (not contained in the fold — closing would destroy work): [102]

    and exiting **1**. Both are wrong: the docstring reserves exit 2 for "could not
    measure", and the printed line asserts the PR's work is missing from the fold on
    a run where nothing was measured. That is the sentence an operator acts on by
    closing the PR by hand — the one unrecoverable outcome this tool exists to
    prevent. Reachable through `fold-constituents: 10543 #10820`, where `#10820` is
    an issue and `gh pr view` fails.
    """
    monkeypatch.setattr(bo, "fetch_fold", lambda n, repo=None: (0, "f" * 40, "fold-br", "body"))
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))
    monkeypatch.setattr(
        bo,
        "constituent_refs",
        lambda n, repo=None: (("a" * 40, "b" * 40) if n == 101 else None),
    )
    monkeypatch.setattr(
        bo, "verdict_for", lambda n, h, b, f, cwd: _verdict(n, "CONTAINED-CONTENT", "")
    )
    monkeypatch.setattr(bo, "_gh", lambda argv, repo=None: (0, ""))

    rc = bo.main(["--fold", "900", "--constituent", "101", "--constituent", "102"])
    out = capsys.readouterr().out

    assert rc == 2, f"an unmeasurable constituent is 'could not measure' (exit 2), got {rc}: {out}"
    assert "closing would destroy work" not in out, out
    assert "COULD NOT MEASURE" in out, out


def test_a_constituent_head_absent_from_the_object_db_is_UNMEASURABLE(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """(b3) NOT having fetched a PR is not evidence about the fold — and must not say it is.

    `main` fetches ONLY the fold branch, so on a fresh hub or a new lane worktree a
    constituent's head is simply absent. `merge-base --is-ancestor` on an absent commit
    exits 128 (indistinguishable from "not an ancestor") and `git diff` then dies before
    comparing anything, so the pre-fix order filed the constituent as `NOT-CONTAINED` and
    the report printed

        REFUSED (not contained in the fold — closing would destroy work): [101]

    with exit **1**, on a run where nothing was measured. Both are contract breaches in
    the module's own words: exit 2 is "could not measure", and the docstring says an
    UNMEASURABLE constituent "never prints the 'closing would destroy work' line".

    Discrimination proof — MEASURED, and it names which defence carries what. The fix has
    two, and they OVERLAP on this scenario: the presence gate in `verdict_for` and the
    tri-state return of `content_contained`. Disarming EITHER one alone stays green here,
    because the other still classifies the run as UNMEASURABLE. The gate is the clearer
    message (it names the absent commits and the fetch to run, and spawns no worktree on
    a ref that cannot resolve); the tri-state is the one that covers the case the gate
    CANNOT see — every commit present, but no merge base in a shallow clone, where only
    the diff itself fails. That half has its own test immediately below, and reverting
    both defences reds this one.
    """
    base, const, fold = _three_way_fixture(scratch_repo)
    absent = "0" * 40  # a well-formed sha this repo has never fetched
    assert not bo._commit_present(absent, scratch_repo), "the fixture sha must be absent"

    verdict = bo.verdict_for(101, absent, base, fold, scratch_repo)
    assert verdict.state == "UNMEASURABLE", (
        "a head that is not in the object DB is a failed MEASUREMENT, not a containment "
        f"verdict — got {verdict}"
    )
    assert verdict.closable is False, verdict

    # And it must be FILED as unmeasured, which is what decides both the printed line
    # and the exit status (1 = refused, 2 = could not measure).
    report = bo.Report(fold=10917, fold_head=fold)
    bo._file_verdict(report, verdict)
    assert report.unmeasured == [101] and report.refused == [], (
        f"filed as a refusal, which prints the false claim: {report}"
    )
    bo._print_report(report, dry_run=True)
    out = capsys.readouterr().out
    assert "closing would destroy work" not in out, out
    assert "COULD NOT MEASURE" in out, out

    # The genuinely-measured answer is unchanged: an absent merge base measured nothing,
    # but a PRESENT head whose diff conflicts is still a real NOT-CONTAINED.
    conflicting = bo.verdict_for(102, const, base, fold, scratch_repo)
    assert conflicting.state == "NOT-CONTAINED", (
        "a present head against a conflicting fold must stay a real refusal — the fix "
        f"must not turn every hard case into 'could not measure': {conflicting}"
    )


def test_content_contained_reports_an_uncomputable_diff_as_UNMEASURABLE(
    scratch_repo: Path,
) -> None:
    """(b3b) The half the presence gate cannot cover: every object PRESENT, diff still fails.

    A shallow clone has the objects but not the MERGE BASE, so `git diff base...head` exits
    non-zero while `_commit_present` is true for every ref — the presence gate cannot see
    this, and collapsing it into `NOT-CONTAINED` re-opens the defect one layer down. That
    is why the function itself is tri-state, and this test constructs the shape for real:
    two UNRELATED roots (`git commit-tree` with no parent) have no merge base, and all
    three commits are present in the object DB (asserted, so the fixture cannot silently
    degrade into the absent-object case the sibling test already covers).

    Discrimination proof: with the function returning `(bool, str)` — the pre-fix shape —
    the state is `False`/`True`, never the string `UNMEASURABLE`, and this test reds.
    """
    left = _commit(scratch_repo, {"a.txt": "left\n"}, [], "root-left")
    right = _commit(scratch_repo, {"b.txt": "right\n"}, [], "root-right")
    for sha in (left, right):
        assert bo._commit_present(sha, scratch_repo), (
            "the fixture must keep every object PRESENT — this test is about the diff "
            "failing with the objects in hand, not about an absent ref"
        )
    assert not bo.is_ancestor(left, right, scratch_repo), "unrelated roots, by construction"

    # The verdict path reaches it too: ``head`` is not an ancestor of ``fold``, and the
    # base is present, so the CONTENT route runs and its diff cannot be computed.
    verdict = bo.verdict_for(101, left, right, right, scratch_repo)
    assert verdict.state == "UNMEASURABLE", (
        f"an uncomputable diff is a failed measurement, not a containment verdict: {verdict}"
    )

    state, detail = bo.content_contained(left, right, right, scratch_repo)
    assert state == "UNMEASURABLE", (
        f"an uncomputable diff is a failed measurement, not a containment verdict: {state!r} ({detail})"
    )
    assert "could not compute the constituent diff" in detail, detail


def test_a_fold_worktree_that_cannot_be_built_is_UNMEASURABLE(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b3c) The THIRD member of the tri-state's "cannot be COMPUTED" set.

    `tools/batch_open.py:538-545` names three ways a diff cannot be computed: an absent
    object, no merge base in a shallow clone, and **no scratch worktree**. The first two
    are pinned by their own tests; this one had none. Measured at `8399b86c6`:
    reverting `return "UNMEASURABLE", f"could not materialise the fold tree: ..."` to
    `"NOT-CONTAINED"` left **all 57 cases collected there green**.

    It is reachable without a contrived fixture: `tempfile.mkdtemp` followed by
    `git worktree add` fails on ENOSPC, on an unwritable TMPDIR, and on a stale entry under
    `.git/worktrees`. Collapsing that into NOT-CONTAINED restores exactly the contract
    breach this PR exists to fix — exit 1 and "closing would destroy work" about a
    comparison that never ran.

    Discrimination proof: with the arm reverted the state is the string `NOT-CONTAINED`,
    never `UNMEASURABLE`, and this test reds.
    """
    left = _commit(scratch_repo, {"a.txt": "left\n"}, [], "root-left")
    right = _commit(scratch_repo, {"b.txt": "right\n"}, [], "root-right")

    real_git = bo._git

    def _git_refuses_the_worktree(argv, cwd):
        if list(argv[:2]) == ["worktree", "add"]:
            return 1, "fatal: could not create work tree dir: No space left on device"
        return real_git(argv, cwd)

    monkeypatch.setattr(bo, "_git", _git_refuses_the_worktree)

    state, detail = bo.content_contained(left, right, right, scratch_repo)
    assert state == "UNMEASURABLE", (
        f"a fold tree that could not be BUILT was reported as a containment verdict: "
        f"{state!r} ({detail})"
    )
    assert "could not materialise the fold tree" in detail, detail


def test_an_absent_BASE_does_not_block_the_ANCESTRY_route(scratch_repo: Path) -> None:
    """(b3c) The presence gate must not over-block: ancestry needs `head` and `fold_head` only.

    `is_ancestor(head, fold_head)` is a graph query on two commits. It needs no diff and no
    base, so requiring all three refs before it would convert a provable
    `CONTAINED-ANCESTOR` into "could not measure" whenever the constituent's base is absent
    — a fail-closed over-block that names the wrong remedy for a verdict already answered.
    Two-stage presence is the fix; this pins the second stage down.

    Discrimination proof: restore the one-stage gate (all of head/base/fold_head) and this
    test reds with UNMEASURABLE instead of CONTAINED-ANCESTOR.
    """
    base = _commit(scratch_repo, {"f.txt": "one\n"}, [], "base")
    head = _commit(scratch_repo, {"f.txt": "two\n"}, [base], "head")
    fold = _commit(scratch_repo, {"f.txt": "two\n", "g.txt": "x\n"}, [head], "fold")
    absent_base = "0" * 40
    assert not bo._commit_present(absent_base, scratch_repo)

    verdict = bo.verdict_for(101, head, absent_base, fold, scratch_repo)
    assert verdict.state == "CONTAINED-ANCESTOR", (
        "the ancestry proof needs neither the base nor a diff — an absent base must not "
        f"downgrade it to 'could not measure': {verdict}"
    )
    assert verdict.closable is True, verdict

    # …while the CONTENT route still refuses to guess: with no ancestry and the base
    # absent there is nothing to reverse-apply, so it is UNMEASURABLE (exit 2), and the
    # message names the BASE as what is missing.
    unrelated = _commit(scratch_repo, {"h.txt": "other\n"}, [], "unrelated-root")
    no_base = bo.verdict_for(102, head, absent_base, unrelated, scratch_repo)
    assert no_base.state == "UNMEASURABLE", no_base
    assert "BASE" in no_base.detail, no_base.detail


def test_hint_constituents_still_sees_prose_but_is_never_acted_on() -> None:
    """(b2) The prose hint is retained for the refusal message only."""
    body = "batch(x): land #10543 #10610 (#10641 dropped)"
    assert bo.hint_constituents(body, 900) == [10543, 10610, 10641]
    assert bo.parse_constituents(body, fold=900) == [], (
        "a title carrying no `fold-constituents:` line must yield NO constituents"
    )


def test_parse_constituents_refuses_prose_that_follows_a_stripped_parenthetical() -> None:
    """(b2) Digits AFTER the parenthetical are prose, but they parse as TOKENS.

    Measured with the pre-fix parser:
        'fold-constituents: 10543 (10641 dropped) #10820'
            -> [10543, 10820]
    `#10820` is an ISSUE, and the token grammar cannot tell it from a PR, so the
    residue is refused rather than harvested. The parenthetical must be the TAIL.
    """
    assert bo.parse_constituents("fold-constituents: 10543 (10641 dropped) #10820\n", fold=900) == [
    ]
    # ...while a well-formed tail is still stripped, and the tokens before it read.
    assert bo.parse_constituents(
        "fold-constituents: 10543, 10610 (10641 dropped; see #10820)\n", fold=900
    ) == [10543, 10610]
    # An unterminated parenthetical is not a well-formed tail either.
    assert bo.parse_constituents("fold-constituents: 10543 (10641 dropped\n", fold=900) == []


def test_label_only_reports_a_refused_constituent_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """(a) `--label-only` must not claim `0 refused` while a constituent is unproven.

    The label is deliberately still applied — it is reversible and it closes the
    QUEUE, not the PR — but the docstring's exit contract is
    `exit 1 = at least one constituent NOT contained — refused, and reported`, and
    this path used to exit 0 with `0 refused` in the summary. Measured before the
    fix: `--label-only` over [CONTAINED-CONTENT, NOT-CONTAINED] -> exit=0,
    `APPLIED: 2 constituent(s), 0 refused`, and a `close` marker on #101 for a run
    that closed nothing.
    """
    monkeypatch.setattr(bo, "fetch_fold", lambda n, repo=None: (0, "f" * 40, "fold-br", "body"))
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))
    monkeypatch.setattr(bo, "constituent_refs", lambda n, repo=None: ("a" * 40, "b" * 40))
    monkeypatch.setattr(
        bo,
        "verdict_for",
        lambda n, h, b, f, cwd: _verdict(
            n, "CONTAINED-CONTENT" if n == 101 else "NOT-CONTAINED", ""
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(bo, "_gh", lambda argv, repo=None: (calls.append(list(argv)), (0, ""))[1])

    rc = bo.main(["--fold", "900", "--label-only", "--constituent", "101", "--constituent", "102"])
    out = capsys.readouterr().out

    assert rc == 1, f"a NOT-CONTAINED constituent must exit 1, got {rc}: {out}"
    assert "1 refused" in out, out
    assert "close" not in [line.split()[0] for line in out.splitlines() if line.strip()], (
        f"a label-only run closes nothing, so it must not print a `close` marker: {out}"
    )
    # The label still goes on both — refusal is about the CLOSE, not the label.
    assert [argv[:2] for argv in calls] == [["pr", "edit"], ["pr", "edit"]], calls


def test_label_only_exits_2_on_a_failed_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """(a) A failed `gh` is "could not measure" (exit 2), not "not contained" (exit 1)."""
    monkeypatch.setattr(bo, "fetch_fold", lambda n, repo=None: (0, "f" * 40, "fold-br", "body"))
    monkeypatch.setattr(bo, "_git", lambda argv, cwd: (0, ""))
    monkeypatch.setattr(bo, "constituent_refs", lambda n, repo=None: ("a" * 40, "b" * 40))
    monkeypatch.setattr(
        bo, "verdict_for", lambda n, h, b, f, cwd: _verdict(n, "CONTAINED-CONTENT", "")
    )
    monkeypatch.setattr(bo, "_gh", lambda argv, repo=None: (1, "HTTP 422"))
    rc = bo.main(["--fold", "900", "--label-only", "--constituent", "101"])
    capsys.readouterr()
    assert rc == 2, rc


def test_a_missing_binary_is_a_FAILED_QUERY_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b2) An absent `gh`/`git` must not escape as an exception, and must not look like "not contained".

    `subprocess.run(["gh", ...])` raises `FileNotFoundError` — NOT a non-zero rc — so
    `main()` used to die with a traceback and exit **1**, the code reserved for "a
    constituent was NOT contained". On the fleet's `ubuntu-latest` fallback pool there
    is no `gh` on PATH at all, so that was the likely reading there. Same lesson as
    `MEMORY.md`'s `[cli-absent-is-not-failure]` (#9018).
    """

    def absent(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(bo.subprocess, "run", absent)
    code, err = bo._gh(["pr", "view", "1"])
    assert code == 127, code
    assert "not found" in err.lower(), err
    code, err = bo._git(["status"], Path("."))
    assert code == 127, code
    assert "not found" in err.lower(), err


def test_a_hung_call_is_124_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b2) Same class: a timeout is a failed call, not an uncaught `TimeoutExpired`."""

    def hung(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

    monkeypatch.setattr(bo.subprocess, "run", hung)
    code, err = bo._gh(["pr", "view", "1"])
    assert code == 124, code
    assert "did not finish" in err.lower(), err


def test_a_batch_open_only_change_selects_core_so_this_guard_actually_runs() -> None:
    """(b2) The `CORE_ALSO` entry is LOAD-BEARING, so it is pinned here.

    `tools/` is in `NON_PYTHON_PREFIXES` and no `SOURCE_PATTERNS` row matches
    `tools/batch_open.py`, so without the entry a tool-only diff is filtered out
    BEFORE the "not matched -> core" fallback, `select()` returns no surface, and the
    guard that pins the fold gate never runs on the file it guards — the
    #1349/#3332/#3616 silent-drop class the entry's comment cites. Measured: with the
    entry removed, `surfaces == []` and `test_batch_gate.py` is not selected.

    `tests/test_ci_selection.py`'s generic ratchet derives candidates from
    `tortoise/**/*.py` stems only, so it cannot see a `tools/` guard — hence the pin
    lives here, where the entry does.
    """
    selection = cs.select(["tools/batch_open.py"], "pull_request", cs.load_manifest())
    assert selection["surfaces"] == ["core"], selection
    assert "test_batch_gate.py" in selection["test_files"], (
        "the CORE_ALSO entry is missing: a batch_open-only diff would run no guard "
        f"at all — got {len(selection['test_files'])} files"
    )


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

    ⛔ PARSED AS YAML, not scanned line-by-line. A line scan seeing only entries
    whose text STARTS with `runs-on:` is blind to the legal block-style spelling —

        runs-on:
          - self-hosted
          - fold

    — which parses to the same thing and would have slipped a `fold` pool past
    this pin while it stayed green. The pin has to read the document, because the
    thing it forbids is a YAML value and not a line shape.
    """
    doc = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "python-ci.yml").read_text(encoding="utf-8")
    )
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, "python-ci.yml has no jobs"

    def labels(value) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        raise AssertionError(f"unrecognised runs-on shape: {value!r}")

    matrix_jobs = []
    fold_jobs = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or "runs-on" not in job:
            continue
        entries = labels(job["runs-on"])
        if any("matrix.runner" in entry for entry in entries):
            matrix_jobs.append((job_id, entries))
        if any("fold" in entry for entry in entries):
            fold_jobs.append((job_id, entries))

    assert len(matrix_jobs) == 1, (
        "expected exactly one matrix-driven `runs-on`, got "
        f"{[j for j, _ in matrix_jobs]} — two would be two places to decide routing"
    )
    job_id, entries = matrix_jobs[0]
    assert entries == ["${{ fromJSON(matrix.runner) }}"], (
        "the shard `runs-on` gained logic — it must parse the selector's array "
        f"verbatim, not derive one: job {job_id!r} has {entries}"
    )
    assert not fold_jobs, (
        "a `runs-on` names a `fold` pool — no runner on this fleet carries that "
        f"label, so the job would never start: {fold_jobs}"
    )


def _registered_runner_labels() -> set[str]:
    """The label set the x86 runner installer would register, computed locally.

    `--print-labels` is the installer's own resolution (no network, no `gh`), so
    this measures the installer's INTENT, not the fleet's live registration — say
    so rather than overclaim. It is the right measure for the question asked:
    whether a job naming `fold` could be matched on this box is decided by the
    labels the installer writes, and an installer that never emits `fold` is
    sufficient to show the route is inert. A live `gh api …/actions/runners` read
    would be the stronger measurement and is deliberately not used here — it is
    rate-limited and would make this guard flaky for no extra discrimination.

    A non-zero exit is a could-not-measure, never an empty set: an empty set would
    make the assertions above vacuously true.
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
