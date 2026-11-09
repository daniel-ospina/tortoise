#!/usr/bin/env python3
"""Close a fold's constituents when the fold opens — and refuse when that is unsafe.

WHY THIS FILE EXISTS
--------------------
Owner's question (2026-11-04, verbatim):

    "will we have a way to prioritise for CI queues the combined ones? so their
     components don't land before and disrupt the process?"

The batching rule's own answer is the second half of it: *"close the constituents
(that is what stops them landing separately)"*. A **closed** PR cannot be queued,
cannot be merged, and cannot spend a runner slot — strictly stronger than any
queue condition, because it removes the PR from every population rather than
filtering it in one.

This tool is that step. Given a fold PR and its constituents it proves, per
constituent, that the constituent's work is **already in the fold's tree** before
closing it, applies the queue-entry label, and — under `--apply` only — closes it
with a comment naming the fold and the head.

THE THREE MECHANISMS, AND WHICH ONE THIS IS
-------------------------------------------
1. **HARD GATE** — `.mergify.yml`'s `queue_conditions` carries
   `-label=superseded-by-fold`, so a labelled constituent is refused at queue
   ENTRY. (Mergify's documented lifecycle also drops an already-queued PR that
   stops satisfying `queue_conditions`, so the label should drain the queue too;
   that part is DOCUMENTED rather than measured, and the entry gate does not rely
   on it.) This file APPLIES that label.
2. **CLOSE AT FOLD-OPEN** — this file, `--apply`.
3. **CI PRIORITY** — this file applies `queue-accelerator` to the FOLD, which
   the existing `priority_rules` resolves to `priority: high` with
   `allow_checks_interruption: true`. Deliberately NOT a `fold` runner pool: see
   `PRIORITY: ORDERING, NOT ROUTING` below.

PRIORITY: ORDERING, NOT ROUTING
-------------------------------
The plan offered a `fold` runner class via a job-level `runs-on` expression. That
is refused here for two independent, measured reasons:

* **No such runner exists on this fleet.** `tools/install-ci-runner-x64.sh
  --print-labels` resolves the registered set to `self-hosted-x64`. A routing
  expression naming an unregistered label does not queue — it selects ZERO
  runners and the job never starts, which is strictly worse than waiting in a
  queue (an unstarted job also never reports, so the required context hangs).
* **It would contradict a recorded decision.** `.github/workflows/python-ci.yml`
  states that `runs-on` "has no logic of its own: it parses the per-row `runner`
  the selector emitted, so the mode cannot drift between the two — there is no
  second place to edit." A job-level fold expression IS that second place.

So mechanism 3 uses the lever that already exists (#5527's `queue-accelerator` +
`priority_rules`). Its cost is stated rather than hidden: an accelerated fold can
**interrupt checks already running** (`allow_checks_interruption: true`), so CI
time already paid for is spent again. That is the queue's own reliability/velocity
trade, and it is why the label is applied to a FOLD only, never to a preference.

CONTAINMENT — WHY IT IS CHECKED AT ALL
--------------------------------------
Closing a PR whose work is NOT in the fold destroys work that nothing else
carries. So each constituent must be proven contained, by one of two routes:

* `CONTAINED-ANCESTOR` — `git merge-base --is-ancestor <head> <fold-head>`. True
  for a fold built by merging (`--no-ff`) where the constituent is an ancestor.
* `CONTAINED-CONTENT` — the fold is squash-built, so ancestry is FALSE even
  though the work is present. Reversing the constituent's own diff
  (`git diff <base>...<head>`) inside a scratch worktree of the fold head
  applies cleanly iff the fold tree already contains that change.

Anything else is `NOT-CONTAINED` and is **reported and kept**, under `--apply`
as well as in the dry run. That is not a courtesy: it is the difference between
a tool that tidies and a tool that deletes.

WHY THE CONSTITUENT LIST IS NOT PARSED FROM PROSE
-------------------------------------------------
A fold's title reads `batch(combine-a): land #10543 #10610 #10575 as one
validated tree (#10641 dropped: ...)` — and `#10641` there is a PR that was
DELIBERATELY NOT folded. Deriving the close list from that prose would close a
PR the fold never carried. So the list comes from an explicit machine-readable
line in the fold body (`fold-constituents: 10543, 10610, 10575`) or from
`--constituent`, and prose `#NNNN` references are only ever a HINT printed in the
refusal message.

USAGE
-----
    uv run python tools/batch_open.py --fold 10894                 # dry run (default)
    uv run python tools/batch_open.py --fold 10894 --label-only    # gate entry, do not close
    uv run python tools/batch_open.py --fold 10894 --apply         # label + comment + close

    exit 0 = every constituent contained (and closed, under --apply)
    exit 1 = at least one constituent NOT contained — refused, and reported
    exit 2 = could not measure (no fold body, no constituent list, git/gh failure,
             or a constituent whose head/base could not be read at all — an
             UNMEASURABLE constituent is NOT a containment verdict and never
             prints the "closing would destroy work" line)

What "could not be read at all" COVERS, so it is not read as "containment failed":
    a CONSTITUENT's head/base being absent from the LOCAL OBJECT DB. This tool fetches
    only the fold branch (`main`), so on a fresh hub or a new lane worktree that has
    never fetched the constituent's PR head, the reverse-apply cannot even compute the
    diff. That is a failed MEASUREMENT, not evidence about the fold, and it exits 2.

`--label-only` exists because the two mechanisms have different risk: applying a
label is reversible in one command, closing a PR is not. An operator whose
containment proof is inconclusive can still close the QUEUE, which is where the
CI cost is.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE every other import. The POSITION is
# the property this exists for, not this file's current import list: the corpus
# rule (`tests/test_entry_point_python_guard.py`, property 1) requires `import
# sys` + the guard to precede all other imports, because a module-level
# 3.11+-only import — `from datetime import UTC`, present in sibling tools —
# raises ImportError on an old interpreter before any LATER-placed guard could
# run. This file imports no `datetime` today; the guard is hoisted anyway so the
# rule holds uniformly instead of being re-litigated per import list.
# ⛔ The invocation named is the SCRIPT form, which is the one that works here:
# `tests/test_entry_point_python_guard.py` treats `tools/<x>.py` as a loose
# script (a package module needs a directory chain under `tools/`), so
# `-m tools.batch_open` is NOT this file's invocation.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/batch_open.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/batch_open.py`"
    )

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MERGIFY_REL = ".mergify.yml"

# The label that gates queue ENTRY. It must match `.mergify.yml`'s negated
# `queue_conditions` entry exactly — `tests/test_batch_gate.py` pins the pair so
# the two cannot drift apart quietly (a tool applying a label no condition reads
# is a gate that is never closed).
CONSTITUENT_LABEL = "superseded-by-fold"

# The existing (#5527) priority label. Applied to the FOLD, never to a constituent.
FOLD_LABEL = "queue-accelerator"

# Mergify's priority scale. `priority_rules` in `.mergify.yml` names a LEVEL
# (`high`), not a number, so the tool resolves to the level and carries its RANK
# for comparison. The ranks are Mergify's documented defaults; a level the
# configuration invents is UNKNOWN and therefore ranks as the default, never
# above it — an unrecognised value must not silently outrank real work.
PRIORITY_RANK = {"low": 1000, "medium": 2000, "high": 3000}
MEDIUM_PRIORITY = "medium"
MEDIUM_RANK = PRIORITY_RANK[MEDIUM_PRIORITY]

# States a containment check can return. `closable` is derived from the STATE, not
# from the caller's intent: an unrecognised state must never be a licence to close.
_CONTAINED_STATES = frozenset({"CONTAINED-ANCESTOR", "CONTAINED-CONTENT"})

GIT_TIMEOUT_S = 300
GH_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# Containment verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """One constituent's containment result."""

    number: int
    state: str
    detail: str = ""

    @property
    def closable(self) -> bool:
        """Only a POSITIVELY proven containment may be closed.

        Fail-closed by construction: `_CONTAINED_STATES` is an allow-list, so a
        new/typo'd/empty state is not closable. The alternative (a deny-list on
        `NOT-CONTAINED`) would close on every state nobody thought of.
        """
        return self.state in _CONTAINED_STATES


@dataclass(frozen=True)
class Priority:
    """The effective priority a label set resolves to."""

    name: str
    priority: str
    allow_checks_interruption: bool

    @property
    def rank(self) -> int:
        """Numeric rank of the level — unknown levels rank as the default."""
        return PRIORITY_RANK.get(self.priority, MEDIUM_RANK)


@dataclass
class Report:
    """What `execute` did (or, in a dry run, would report)."""

    fold: int
    fold_head: str
    actions: list[Verdict] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    labelled: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)
    #: Constituents for which NO containment verdict exists (the `gh pr view` that
    #: would give head/base failed). Reported separately from `refused` because the
    #: two say opposite things to the operator: `refused` asserts "its work is NOT in
    #: the fold — closing would destroy it", while these say "nothing was measured —
    #: retry". Folding them together printed the former for the latter, which is the
    #: sentence a human acts on by closing the PR by hand.
    unmeasured: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _file_verdict(report: Report, verdict: Verdict) -> None:
    """Record a NOT-closable verdict under the list that describes it."""
    if verdict.state == "UNMEASURABLE":
        report.unmeasured.append(verdict.number)
    else:
        report.refused.append(verdict.number)


# ---------------------------------------------------------------------------
# Queue entry, evaluated the way Mergify evaluates it
# ---------------------------------------------------------------------------


def _condition_holds(cond: str, pr: dict) -> bool:
    """Evaluate ONE `queue_conditions` entry against a PR record.

    Deliberately narrow: it models the condition kinds this repository actually
    uses (`base=`, `-draft`, `label=`/`-label=`, `check-success=`) and REFUSES to
    guess at anything else — an unevaluable condition raises, so a future
    condition kind cannot silently read as satisfied. Failing closed matters more
    here than covering Mergify's whole grammar: this function exists so the entry
    gate can be *tested*, not so it can replace Mergify.
    """
    negated = cond.startswith("-")
    body = cond[1:] if negated else cond

    if body == "draft":
        holds = bool(pr.get("draft"))
    elif "=" in body:
        key, _, value = body.partition("=")
        if key == "base":
            holds = pr.get("base") == value
        elif key == "label":
            holds = value in pr.get("labels", set())
        elif key == "check-success":
            holds = bool(pr.get("green")) if value else False
        else:
            raise ValueError(f"cannot evaluate queue condition {cond!r}")
    else:
        raise ValueError(f"cannot evaluate queue condition {cond!r}")
    return (not holds) if negated else holds


def entry_decision(pr: dict, queue_conditions: list[str]) -> tuple[bool, str]:
    """(admitted, reason). The PR enters the queue iff EVERY condition holds.

    `reason` names the FIRST failing condition — which, for a fold constituent, is
    the label exclusion. Mergify cannot name the fold (the fold is our concept,
    not the queue's), so the human-readable naming lives in `closure_comment`;
    what this proves is that the label ALONE is sufficient to keep the PR out.
    """
    for cond in queue_conditions:
        if not _condition_holds(cond, pr):
            return False, f"refused at queue entry by condition {cond!r}"
    return True, "admitted"


# ---------------------------------------------------------------------------
# Priority resolution
# ---------------------------------------------------------------------------


def priority_for(labels: set[str], config: dict) -> Priority:
    """Resolve a label set to the effective queue priority.

    Mirrors `.mergify.yml`'s `priority_rules`: first matching rule wins, and an
    unmatched PR stays at Mergify's `medium` default. Reads the configuration
    rather than hard-coding `high`, so a rule edit changes the answer and a
    typo'd label resolves to `medium` (and reds the test) instead of silently
    leaving a fold unprioritised.
    """
    for rule in config.get("priority_rules") or []:
        conditions = rule.get("conditions") or []
        matched = True
        for cond in conditions:
            negated = cond.startswith("-")
            body = cond[1:] if negated else cond
            key, _, value = body.partition("=")
            if key.strip() != "label":
                matched = False
                break
            holds = value.strip() in labels
            if negated:
                holds = not holds
            if not holds:
                matched = False
                break
        if matched:
            level = rule.get("priority", MEDIUM_PRIORITY)
            return Priority(
                name=rule.get("name", ""),
                priority=str(level).strip().lower(),
                allow_checks_interruption=bool(rule.get("allow_checks_interruption")),
            )
    return Priority(name="(default)", priority=MEDIUM_PRIORITY, allow_checks_interruption=False)


# ---------------------------------------------------------------------------
# Plan + execution
# ---------------------------------------------------------------------------


def closure_comment(fold: int, fold_head: str, number: int) -> str:
    """The comment left on a closed constituent.

    It must name WHERE the work went — the fold PR and the exact head whose tree
    was proven to contain it. The review record is NOT what this preserves: that
    is a git ref plus `~/.pi/agent/reviews/*.json`, and it survives closure
    intact. What closure loses is the trail from the PR to its new home, so that
    is what the comment carries.
    """
    return (
        f"Superseded by fold #{fold} at `{fold_head}` — this PR's work is "
        f"contained in that tree, so it is closed to stop it landing separately "
        f"and spending a queue cycle ahead of the fold.\n\n"
        f"- fold: #{fold}\n"
        f"- fold head: `{fold_head}`\n"
        f"- constituent: #{number}\n\n"
        f"The review record is unaffected (it is a git ref, not this PR's state). "
        f"If the fold is abandoned, re-open this PR and remove the "
        f"`{CONSTITUENT_LABEL}` label — that label is what gates queue entry."
    )


@dataclass
class Plan:
    fold: int
    fold_head: str
    verdicts: list[Verdict]


def plan_from_verdicts(fold: int, fold_head: str, verdicts: list[Verdict]) -> Plan:
    return Plan(fold=fold, fold_head=fold_head, verdicts=list(verdicts))


def execute(
    plan: Plan,
    gh: Callable[[list[str]], tuple[int, str]],
    dry_run: bool = True,
) -> Report:
    """Apply the plan. A `NOT-CONTAINED` constituent is never closed, ever.

    Under `--apply` a closable constituent gets, in order: the entry-gate label,
    the closure comment, then the close. The label goes first on purpose — if the
    run dies between steps, the PR is already refused at queue entry, which is
    the mechanism that saves the CI cycle.

    Reporting is unconditional: every constituent appears in `report.actions`,
    so a dry run still tells the operator about a PR it will leave open.
    """
    report = Report(fold=plan.fold, fold_head=plan.fold_head)
    for verdict in plan.verdicts:
        report.actions.append(verdict)
        if not verdict.closable:
            _file_verdict(report, verdict)
            continue
        if dry_run:
            continue
        number = str(verdict.number)
        steps = [
            ["pr", "edit", number, "--add-label", CONSTITUENT_LABEL],
            [
                "pr",
                "comment",
                number,
                "--body",
                closure_comment(plan.fold, plan.fold_head, verdict.number),
            ],
            ["pr", "close", number],
        ]
        for argv in steps:
            code, err = gh(argv)
            if code != 0:
                report.errors.append(f"#{number}: gh {' '.join(argv)} failed: {err[:200]}")
                break
            if argv[1] == "edit":
                report.labelled.append(verdict.number)
            elif argv[1] == "close":
                report.closed.append(verdict.number)
    return report


def label_only(
    verdicts: list[Verdict],
    fold: int,
    fold_head: str,
    gh: Callable[[list[str]], tuple[int, str]],
    dry_run: bool = True,
) -> Report:
    """Mechanism 1 on its own: gate queue entry, close nothing.

    Safe without a containment proof, and that is the point — the label removes a PR
    from the QUEUE, not from existence, and it is reversible in one command. So an
    operator whose containment check is inconclusive (a fold still assembling, a
    squash whose content proof the tool could not make) can still stop the CI bleed
    without risking a PR that no fold carries.
    """
    report = Report(fold=fold, fold_head=fold_head, actions=list(verdicts))
    for verdict in verdicts:
        if not verdict.closable:
            # REPORTED, not skipped — and note the asymmetry with `execute`, which is
            # deliberate: the label is safe without a containment proof (reversible in
            # one command, and it closes the QUEUE, not the PR), so it still goes on.
            # But the run's exit status must not claim every constituent was contained
            # when one was not: the docstring's contract is "exit 1 = at least one
            # constituent NOT contained — refused, and reported", and this path used
            # to print `0 refused` and return 0 while a NOT-CONTAINED constituent sat
            # in the plan.
            _file_verdict(report, verdict)
    if dry_run:
        return report
    for verdict in verdicts:
        code, err = gh(["pr", "edit", str(verdict.number), "--add-label", CONSTITUENT_LABEL])
        if code == 0:
            report.labelled.append(verdict.number)
        else:
            report.errors.append(f"#{verdict.number}: gh pr edit failed: {err[:200]}")
    return report


# ---------------------------------------------------------------------------
# Containment — real git
# ---------------------------------------------------------------------------


def _run(
    argv: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    """`subprocess.run` that CANNOT raise: a missing binary is rc=127, a hang rc=124.

    ⛔ A `FileNotFoundError` IS NOT A NON-ZERO RC. `subprocess.run(["gh", ...])`
    raises when the CLI is not installed, and `TimeoutExpired` when it hangs; both
    used to escape `main()` as a traceback, so the process exited **1** — the code
    this tool's contract reserves for *"at least one constituent NOT contained"*.
    That is a wrong claim about the wrong thing, and it is reachable on the fleet's
    `ubuntu-latest` fallback pool, where there is no `gh` on `PATH` at all. It is
    also the lesson `MEMORY.md` records from #9018 (`[cli-absent-is-not-failure]`).

    Both failures now come back as a `CompletedProcess` with an explanatory stderr,
    so every existing `returncode != 0` arm decides — and every one of those arms
    refuses rather than acts, which is the safe direction.
    """
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            argv, 127, "", f"{Path(argv[0]).name}: command not found on PATH"
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", f"{Path(argv[0]).name}: did not finish within {timeout}s"
        )


def _git(argv: list[str], cwd: Path) -> tuple[int, str]:
    proc = _run(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=cwd, timeout=GIT_TIMEOUT_S)
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr).strip()


def _commit_present(sha: str, cwd: Path) -> bool:
    """True iff `sha` resolves to a commit in THIS checkout's object DB.

    ⛔ `main` fetches ONLY the fold branch, so a constituent's head is routinely absent:
    a fresh hub, or a lane worktree that never fetched that PR, has no such object. Every
    git call below then fails for a reason that says NOTHING about the fold (`git diff`
    dies before it can compare), and the previous reading filed that as `NOT-CONTAINED` —
    printing "closing would destroy work" and exiting 1 on a run where nothing was
    measured. That is the sentence an operator acts on, and it is the same class round 5
    fixed one door over (`constituent_refs` returning `None`).
    """
    code, _ = _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd)
    return code == 0


def is_ancestor(head: str, fold_head: str, cwd: Path) -> bool:
    """`git merge-base --is-ancestor` — the git-built fold route."""
    code, _ = _git(["merge-base", "--is-ancestor", head, fold_head], cwd)
    return code == 0


#: What a three-way apply prints when it FAILS to apply cleanly but still exits 0.
#: Matched case-insensitively on the apply's own output, because the exit status
#: cannot be trusted for this (see `content_contained`).
_CONFLICT_MARKERS = ("with conflicts", "conflict", "does not apply", "patch failed")


def _reports_conflict(output: str) -> bool:
    """True iff the apply's output says the patch did not apply cleanly.

    A nested function would be tidier; it is module-level so a test can call it.
    """
    lowered = output.lower()
    return any(marker in lowered for marker in _CONFLICT_MARKERS)


def content_contained(head: str, base: str, fold_head: str, cwd: Path) -> tuple[str, str]:
    """Reverse-apply the constituent's own diff inside the fold's tree.

    Returns `(state, detail)` where `state` is one of `CONTAINED-CONTENT`,
    `NOT-CONTAINED` or `UNMEASURABLE`. ⛔ THREE states, not two: a diff that cannot be
    COMPUTED (an absent object, no merge base in a shallow clone, no scratch worktree) is
    a failed measurement, and collapsing it into `NOT-CONTAINED` makes the report assert
    the fold lacks work nobody ever compared.

    A squash-built fold has rewritten history, so `--is-ancestor` is false while
    the work is fully present. The test that DOES survive a squash is content:
    if reversing this PR's diff against the fold tree applies cleanly, the fold
    already carries the change.

    A scratch worktree is used so the operator's checkout is never touched, and
    it is removed in a `finally` so a failure cannot leave one behind.

    ⛔ NO `--3way`, AND THE EXIT STATUS IS NOT THE EVIDENCE. Measured on git 2.43.0
    against a fold tree that conflicts with the constituent at the same lines —
    i.e. a constituent that was DROPPED from the fold:

        git apply --reverse --check          -> rc=1  "patch does not apply"   (correct)
        git apply --reverse --check --3way   -> rc=0  "Applied patch to 'f.txt' with conflicts."

    `--3way` exits ZERO while reporting a CONFLICT, so a status-only reading of it
    returns "contained" for a diff the fold tree does not carry — and `--apply`
    then closes that PR with a comment asserting a containment that is false.
    That is the one unrecoverable outcome this tool can produce, so:

      * `--3way` is not used at all. A plain reverse-apply is the actual test:
        it succeeds only when the change is already in the tree it is reversed
        against. Acknowledged cost — a fold that *moved* a change within a file
        now reports NOT-CONTAINED and the operator closes the PR by hand. A false
        refusal costs one manual step; a false proof destroys work
        irreversibly, so the trade is taken in that direction deliberately.
      * the conflict phrase is ALSO refused, as a belt for any future edit that
        reintroduces a three-way apply. Exit status alone is not containment
        evidence — the same reason `_git` treats an unreadable result as a
        failure rather than as empty.

    ⛔ rc=0 WITH OUTPUT IS NOT A CLEAN PROOF EITHER, and that is a SECOND message
    git emits while exiting 0. Measured on git 2.43.0 with a MODE-ONLY constituent
    (`old mode 100644 / new mode 100755`) reversed against a fold tree that carries
    the file WITHOUT the chmod:

        git apply --reverse --check   -> rc=0  "warning: s.sh has type 100644,
                                               expected 100755"   and NOTHING applied

    so the executable bit is not in the fold, yet the status-only reading called it
    CONTAINED-CONTENT and `--apply` closed it. A genuinely contained diff is SILENT
    (measured on the same git: rc=0, empty output), so "rc=0 **and** silent" is the
    proof and non-empty output is a refusal. Whitespace noise cannot turn a true
    positive into a refusal for this call because `--check` does not apply the patch
    (measured: a contained trailing-whitespace diff is silent).
    """
    tmp = Path(tempfile.mkdtemp(prefix="batch-open-"))
    worktree = tmp / "fold"
    added = False
    try:
        code, err = _git(["worktree", "add", "--detach", str(worktree), fold_head], cwd)
        if code != 0:
            # The fold tree could not be BUILT — nothing was compared either way.
            return "UNMEASURABLE", f"could not materialise the fold tree: {err[:200]}"
        added = True
        diff = _run(
            ["git", "-c", "core.hooksPath=/dev/null", "diff", f"{base}...{head}"],
            cwd=cwd,
            timeout=GIT_TIMEOUT_S,
        )
        if diff.returncode != 0:
            # ⛔ NOT a containment verdict. Reachable whenever a ref is absent from this
            # clone (this tool fetches the fold branch only) or the merge base is missing
            # in a shallow one; `main` fetches nothing else, so the operator's retry is a
            # fetch, not a manual close.
            return (
                "UNMEASURABLE",
                f"could not compute the constituent diff: {diff.stderr.strip()[:200]}",
            )
        if not diff.stdout.strip():
            # An empty diff cannot be reversed into a proof — an empty PR is not
            # evidence that its work is in the fold, and treating it as contained
            # would close a PR on no evidence at all. This IS a measurement: the diff
            # was computed, and it is empty.
            return "NOT-CONTAINED", "the constituent's diff is empty — nothing to prove"
        applied = _run(
            ["git", "-c", "core.hooksPath=/dev/null", "apply", "--reverse", "--check"],
            cwd=worktree,
            stdin=diff.stdout,
            timeout=GIT_TIMEOUT_S,
        )
        noise = (applied.stdout + applied.stderr).strip()
        conflicting = _reports_conflict(noise)
        if applied.returncode == 0 and not conflicting and not noise:
            return "CONTAINED-CONTENT", "reverse-apply of the constituent diff is clean"
        if applied.returncode == 0 and noise and not conflicting:
            # rc=0 is NOT the proof — silence is. Measured: a mode-only
            # constituent reversed against a tree without the chmod exits 0 and
            # prints "warning: <file> has type 100644, expected 100755" while
            # applying nothing, so the exec bit is not in the fold.
            return "NOT-CONTAINED", (
                "reverse-apply exited 0 but was NOT silent — the fold tree does not "
                f"carry this change: {noise[:300]}"
            )
        if applied.returncode > 1 or applied.returncode < 0:
            # ⛔ rc > 1 (OR A SIGNAL, WHICH PYTHON REPORTS AS A NEGATIVE rc) IS "THE
            # MEASUREMENT DID NOT HAPPEN", NOT A VERDICT (#10941 review).
            # `git apply` uses rc=1 for "the patch does not apply", and THAT is the
            # measurement answering NOT-CONTAINED. Every other code means the
            # comparison never ran: `_run` reports a missing binary as 127 and a hang
            # as 124, git itself uses 128 for a broken repo/index, and `subprocess.run`
            # reports a SIGNAL-KILLED child as a NEGATIVE code (an OOM kill is -9) —
            # so `> 1` alone still folded a killed `git` into NOT-CONTAINED. Folding
            # those into NOT-CONTAINED told the operator "REFUSED … closing would
            # destroy work" and exited 1 ("at least one NOT contained") about a
            # comparison that was never made — contradicting the tool's own contract,
            # which reserves exit 2 / UNMEASURABLE for exactly this. Reproduced with a
            # monkeypatched rc=124: the operator was told a timed-out `git` meant the
            # work was missing.
            # ⛔ THIS ARM RUNS BEFORE THE `conflicting` ONE. Tested the other way round, a
            # git-level failure whose output happened to contain "conflict" (or git's
            # own "patch does not apply") was still read as a containment verdict
            # through the conflict branch, leaving the class this arm fixes alive by
            # ORDER. Not reachable through `_run`'s own shapes today (124/127/128 carry
            # no such text), which is exactly why it is closed structurally.
            return "UNMEASURABLE", (
                "reverse-apply did not run: "
                f"git exited {applied.returncode} — "
                f"{(applied.stderr or applied.stdout).strip()[:200]}"
            )
        if conflicting:
            # Reached for rc=0 (a three-way apply reporting a conflict) and for rc=1,
            # whose `error: … patch does not apply` is git's own way of saying the fold
            # tree does NOT carry this change, whatever the status says. A git-level
            # failure is classified ABOVE, so it can never be read as a verdict here.
            return "NOT-CONTAINED", (
                "reverse-apply reported a CONFLICT in the fold tree — the change is "
                f"not contained: {(applied.stderr or applied.stdout).strip()[:300]}"
            )
        return "NOT-CONTAINED", f"reverse-apply failed: {applied.stderr.strip()[:300]}"
    finally:
        if added:
            _git(["worktree", "remove", "--force", str(worktree)], cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def verdict_for(number: int, head: str, base: str, fold_head: str, cwd: Path) -> Verdict:
    """Classify one constituent. Presence first, then ancestry (cheap), then content.

    ⛔ PRESENCE COMES FIRST because every later step answers a question that needs the
    objects. `merge-base --is-ancestor` on an absent commit exits 128 and reads exactly
    like "not an ancestor", and `git diff` then dies before comparing anything — so the
    old order turned "this clone has not fetched that PR" into "the fold does not carry
    this work", which `_print_report` renders as "closing would destroy work" and
    `main` exits 1 (refused) for, instead of 2 (could not measure).
    ⛔ PRESENCE IS CHECKED IN TWO STAGES, because the two routes need different
    objects. ANCESTRY needs `head` and `fold_head` only; the content route needs
    `base` as well (it computes `base...head`). Requiring all three up front would
    turn a perfectly provable `CONTAINED-ANCESTOR` into "could not measure"
    whenever the base is absent — an over-block, and the wrong remedy for a
    verdict the ancestry proof already answers.
    """
    missing_refs = [sha for sha in (head, fold_head) if not _commit_present(sha, cwd)]
    if missing_refs:
        return Verdict(
            number,
            "UNMEASURABLE",
            "commit(s) absent from this checkout, so containment was never measured: "
            + ", ".join(sha[:12] for sha in missing_refs)
            + " — fetch the constituent's head and the fold head (this tool fetches the "
            "fold branch only) and re-run",
        )
    if is_ancestor(head, fold_head, cwd):
        return Verdict(number, "CONTAINED-ANCESTOR", f"{head[:12]} is an ancestor of the fold")
    # The CONTENT route needs the base too: it reverse-applies `base...head`.
    if not _commit_present(base, cwd):
        return Verdict(
            number,
            "UNMEASURABLE",
            f"the constituent's BASE ({base[:12]}) is absent from this checkout, so its "
            "diff could not be computed — the head is not an ancestor of the fold, and "
            "a content proof needs the base. Fetch it and re-run",
        )
    state, detail = content_contained(head, base, fold_head, cwd)
    return Verdict(number, state, detail)


# ---------------------------------------------------------------------------
# gh
# ---------------------------------------------------------------------------


def _gh(argv: list[str], repo: str | None = None) -> tuple[int, str]:
    """Run `gh`, pinned to `repo` when one was named.

    ⛔ `--repo` is INJECTED, never merely parsed. It used to be declared on the
    CLI and then never read, so `--apply --repo other/repo` labelled, commented on
    and CLOSED same-numbered PRs in whatever repository the operator happened to
    stand in — with a comment asserting containment that was about a different
    repo's PR. Naming a target and silently mutating a different one is the same
    class of false record as a false containment proof.
    """
    full = ["gh", *argv, *(["--repo", repo] if repo else [])]
    proc = _run(full, timeout=GH_TIMEOUT_S)
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr)


#: A `fold-constituents:` line is accepted only as `#<n>`/`<n>` tokens separated by
#: commas and/or whitespace. Anything else on the line REFUSES the line rather than
#: being harvested — see `parse_constituents`.
_CONSTITUENTS_LINE = re.compile(r"\s*fold-constituents\s*:\s*(.*)$")
_CONSTITUENT_TOKEN = re.compile(r"#?(\d+)")

#: A single token: `#<digits>` or `<digits>`. Validation SPLITS and checks each
#: token rather than matching the whole tail with a repeated group. The obvious
#: `^(?:#?\d+[\s,]*)*$` backtracks EXPONENTIALLY when a long digit run is
#: followed by a non-token character (measured: 16 digits 4 ms, 20 -> 64 ms,
#: 22 -> 0.27 s, 24 -> 1.08 s, doubling per digit), so a malformed operator line
#: would wedge the tool for hours. Splitting is linear and cannot backtrack.
_CONSTITUENT_TOKEN_ONLY = re.compile(r"#?\d+\Z")


def parse_constituents(body: str, fold: int | None = None) -> list[int]:
    """The fold's declared constituents, from an explicit machine-readable line.

    NOT from prose. A fold title routinely names a PR that was deliberately NOT
    folded (`(#10641 dropped: ...)`), and closing that one would destroy work the
    fold does not carry.

    ⛔ THE GRAMMAR IS STRICT, and the fold's own number is never a constituent.
    Harvesting every digit group on the line was a real defect: a natural line
    like

        fold-constituents: 10543, 10610, 10575 (was 10641, dropped; see #10820)

    yielded `10641` (the PR the fold deliberately dropped) and `10820` (an issue),
    and a line naming the fold itself yielded the FOLD — for which
    `is_ancestor(fold_head, fold_head)` is vacuously true, so `--apply` would have
    closed the fold along with its constituents, leaving nothing that carries the
    validated tree. So:
      * a parenthetical tail is STRIPPED and never parsed — and it must BE the
        tail: anything after it is prose too;
      * the remaining line must be ONLY `#<n>`/`<n>` tokens separated by commas
        and/or whitespace — otherwise the line is refused (returns `[]`), because
        a line this tool cannot read unambiguously is not evidence;
      * `fold` is excluded explicitly, and `main` excludes it again for the
        `--constituent` path.

    ⛔ AND TEXT AFTER A STRIPPED PARENTHETICAL IS REFUSED, not harvested. Digits
    after it are still TOKENS to the grammar, so

        fold-constituents: 10543 (10641 dropped) #10820

    read as `[10543, 10820]` — and `#10820` is an *issue*, not a PR. (Measured;
    containment still gates the close, so the worst case is a wasted refusal, but
    a line whose prose parses as a token is not a line this tool can read.)
    """
    for line in body.splitlines():
        match = _CONSTITUENTS_LINE.match(line)
        if not match:
            continue
        value = match.group(1)
        # A parenthetical is prose, so it is dropped — and it must be the END of the
        # line. Whatever follows it is prose too, but `#10820` there is a valid
        # TOKEN, so harvesting the residue turns an issue reference into a close
        # target. A parenthetical that is not a well-formed tail refuses the line.
        prose = re.search(r"\(.*$", value)
        if prose:
            after = value[prose.start() :]
            if not re.fullmatch(r"\([^)]*\)[\s,]*", after):
                return []
            value = value[: prose.start()]
        if not value.strip():
            return []
        tokens = [tok for tok in re.split(r"[\s,]+", value.strip()) if tok]
        if not tokens or not all(_CONSTITUENT_TOKEN_ONLY.match(tok) for tok in tokens):
            # Something other than the token grammar is on the line (a word, a
            # bare issue reference in prose, a link). Refuse the LINE, do not
            # harvest from it — a partial parse is how the wrong PR gets closed.
            return []
        numbers = [int(n) for n in _CONSTITUENT_TOKEN.findall(value)]
        return [n for n in numbers if n != fold]
    return []


def declared_constituents_line(body: str) -> str | None:
    """The raw `fold-constituents:` line, or None if the body carries none.

    Exists so the CLI can tell "you never declared the constituents" apart from
    "you declared them in a form this tool refuses". Reporting the first when the
    second is true sends the operator looking for a line that is already there.
    """
    for line in body.splitlines():
        if _CONSTITUENTS_LINE.match(line):
            return line.strip()
    return None


def hint_constituents(body: str, fold: int) -> list[int]:
    """Prose `#NNNN` references — a HINT for the refusal message, never acted on."""
    return sorted({int(n) for n in re.findall(r"#(\d+)", body) if int(n) != fold})


def fetch_fold(number: int, repo: str | None = None) -> tuple[int, str, str, str]:
    """(exit, head_sha, head_branch, body) for a PR."""
    code, out = _gh(
        ["pr", "view", str(number), "--json", "headRefOid,headRefName,body,state"], repo
    )
    if code != 0:
        return 2, "", "", ""
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return 2, "", "", ""
    return 0, doc.get("headRefOid", ""), doc.get("headRefName", ""), doc.get("body", "")


def constituent_refs(number: int, repo: str | None = None) -> tuple[str, str] | None:
    """(head_sha, base_sha) for a constituent PR, or None if unreadable."""
    code, out = _gh(["pr", "view", str(number), "--json", "headRefOid,baseRefOid"], repo)
    if code != 0:
        return None
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return None
    head, base = doc.get("headRefOid", ""), doc.get("baseRefOid", "")
    return (head, base) if head and base else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(report: Report, dry_run: bool, label_only: bool = False) -> None:
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"fold #{report.fold} at {report.fold_head}")
    for verdict in report.actions:
        if label_only:
            # This run closes nothing, so a `close` marker would describe an action
            # the tool never takes (measured: an `--label-only --apply`-shaped run
            # printed `close #101` while closing no PR at all).
            mark = "label " if verdict.closable else "KEEP  "
        else:
            mark = "close" if verdict.closable else "KEEP "
        print(f"  {mark} #{verdict.number:<7} {verdict.state:<20} {verdict.detail}")
    if report.closed:
        print(f"closed: {report.closed}")
    if report.labelled:
        print(f"labelled {CONSTITUENT_LABEL}: {report.labelled}")
    if report.refused:
        print(f"REFUSED (not contained in the fold — closing would destroy work): {report.refused}")
    if report.unmeasured:
        print(
            "COULD NOT MEASURE (no containment verdict either way — NOT a refusal, retry): "
            f"{report.unmeasured}"
        )
    for err in report.errors:
        print(f"  ERROR {err}", file=sys.stderr)
    print(
        f"{mode}: {len(report.actions)} constituent(s), {len(report.refused)} refused, "
        f"{len(report.unmeasured)} unmeasured"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Close a fold's constituents once their work is in the fold tree."
    )
    parser.add_argument("--fold", type=int, required=True, help="the fold PR number")
    parser.add_argument(
        "--constituent",
        type=int,
        action="append",
        default=None,
        help="a constituent PR number (repeatable). Omit to read the "
        "fold body's `fold-constituents:` line.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="label + comment + close (default is a dry run)"
    )
    mode.add_argument(
        "--label-only", action="store_true", help="apply the entry-gate label, do not close"
    )
    parser.add_argument("--repo", default=None, help="owner/name (default: the cwd repo)")
    parser.add_argument("--cwd", default=str(REPO), help="checkout to run git in")
    args = parser.parse_args(argv)

    cwd = Path(args.cwd)
    code, fold_head, fold_branch, body = fetch_fold(args.fold, args.repo)
    if code != 0:
        print(f"cannot read fold #{args.fold} — nothing measured is never a pass", file=sys.stderr)
        return 2

    # ⛔ The fold is never one of its own constituents. `--constituent <fold>` would
    # otherwise yield `is_ancestor(fold_head, fold_head) == True` and close the fold
    # itself, leaving nothing that carries the validated tree.
    requested = args.constituent or parse_constituents(body, fold=args.fold)
    constituents = [n for n in dict.fromkeys(requested) if n != args.fold]
    if args.constituent and args.fold in args.constituent:
        print(
            f"ignoring --constituent {args.fold}: that is the fold itself, and a fold "
            "is never one of its own constituents",
            file=sys.stderr,
        )
    if not constituents:
        hint = hint_constituents(body, args.fold)
        declared = declared_constituents_line(body)
        if declared is not None:
            # The line EXISTS and was refused. Saying "you have no line" here would
            # send the operator hunting for something that is already in the body.
            reason = (
                f"its `fold-constituents:` line is present but could not be read "
                f"unambiguously: {declared!r}\n"
                f"It must carry ONLY `#<n>`/`<n>` tokens separated by commas and/or "
                f"whitespace (a parenthetical tail is stripped; any other text "
                f"refuses the line). Refusing an ambiguous line is deliberate — "
                f"harvesting the numbers out of prose is how the wrong PR gets "
                f"closed. Rewrite the line, or pass --constituent."
            )
        else:
            reason = (
                f"its body carries no `fold-constituents: <n>, <n>` line and no "
                f"--constituent was given.\n"
                f"The prose references {hint} are a HINT only — a fold title "
                f"routinely names a PR that was deliberately NOT folded, so this "
                f"tool will not close on a guess. Add the line to the fold body, or "
                f"pass --constituent."
            )
        print(f"fold #{args.fold} declares no usable constituents: {reason}", file=sys.stderr)
        return 2

    # Ensure the fold head is readable locally; a shallow/missing ref would make
    # every containment check report NOT-CONTAINED and refuse a safe close.
    fetch_code, fetch_err = _git(["fetch", "--no-tags", "origin", fold_branch or fold_head], cwd)
    if fetch_code != 0:
        print(f"could not fetch the fold head ({fetch_err[:200]})", file=sys.stderr)
        return 2

    verdicts: list[Verdict] = []
    for number in constituents:
        refs = constituent_refs(number, args.repo)
        if refs is None:
            verdicts.append(
                Verdict(number, "UNMEASURABLE", "could not read the constituent's head/base")
            )
            continue
        head, base = refs
        verdicts.append(verdict_for(number, head, base, fold_head, cwd))

    plan = plan_from_verdicts(args.fold, fold_head, verdicts)

    # Mechanism 3: the FOLD gets the priority label, which `priority_rules`
    # resolves to `priority: high` + `allow_checks_interruption`. Applied to the
    # fold only — never to a constituent (a constituent is being closed; giving it
    # priority would accelerate exactly the landing this change prevents).
    if args.apply and not args.label_only:
        acc_code, acc_err = _gh(
            ["pr", "edit", str(args.fold), "--add-label", FOLD_LABEL], args.repo
        )
        if acc_code != 0:
            print(
                f"could not apply {FOLD_LABEL} to fold #{args.fold}: {acc_err[:200]}",
                file=sys.stderr,
            )
            return 2

    if args.label_only:
        report = label_only(
            verdicts,
            args.fold,
            fold_head,
            gh=lambda argv: _gh(argv, args.repo),
            dry_run=False,
        )
        _print_report(report, dry_run=False, label_only=True)
        # Same exit contract as the close path below: 2 is "could not measure / a
        # call failed", 1 is "a constituent is NOT contained". This path used to
        # return 1 for a call failure (2) and 0 when a constituent was refused —
        # both wrong against the docstring's own table.
        if report.errors or report.unmeasured:
            return 2
        return 1 if report.refused else 0

    report = execute(plan, gh=lambda argv: _gh(argv, args.repo), dry_run=not args.apply)
    _print_report(report, dry_run=not args.apply)

    if report.errors or report.unmeasured:
        return 2
    return 1 if report.refused else 0


if __name__ == "__main__":
    sys.exit(main())
