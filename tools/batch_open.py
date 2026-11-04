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
    exit 2 = could not measure (no fold body, no constituent list, git/gh failure)

`--label-only` exists because the two mechanisms have different risk: applying a
label is reversible in one command, closing a PR is not. An operator whose
containment proof is inconclusive can still close the QUEUE, which is where the
CI cost is.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/batch_open.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/batch_open.py`"
    )


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
    errors: list[str] = field(default_factory=list)


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
            report.refused.append(verdict.number)
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


def _git(argv: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=GIT_TIMEOUT_S,
    )
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr).strip()


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


def content_contained(head: str, base: str, fold_head: str, cwd: Path) -> tuple[bool, str]:
    """Reverse-apply the constituent's own diff inside the fold's tree.

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
    """
    tmp = Path(tempfile.mkdtemp(prefix="batch-open-"))
    worktree = tmp / "fold"
    added = False
    try:
        code, err = _git(["worktree", "add", "--detach", str(worktree), fold_head], cwd)
        if code != 0:
            return False, f"could not materialise the fold tree: {err[:200]}"
        added = True
        diff = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "diff", f"{base}...{head}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
        )
        if diff.returncode != 0:
            return False, f"could not compute the constituent diff: {diff.stderr.strip()[:200]}"
        if not diff.stdout.strip():
            # An empty diff cannot be reversed into a proof — an empty PR is not
            # evidence that its work is in the fold, and treating it as contained
            # would close a PR on no evidence at all.
            return False, "the constituent's diff is empty — nothing to prove"
        applied = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "apply", "--reverse", "--check"],
            cwd=worktree,
            input=diff.stdout,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
        )
        conflicting = _reports_conflict(applied.stdout + applied.stderr)
        if applied.returncode == 0 and not conflicting:
            return True, "reverse-apply of the constituent diff is clean"
        if conflicting:
            # Reached only if a three-way apply is reintroduced: a conflict means
            # the fold tree does NOT carry this change, whatever the status says.
            return False, (
                "reverse-apply reported a CONFLICT in the fold tree — the change is "
                f"not contained: {(applied.stderr or applied.stdout).strip()[:300]}"
            )
        return False, f"reverse-apply failed: {applied.stderr.strip()[:300]}"
    finally:
        if added:
            _git(["worktree", "remove", "--force", str(worktree)], cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def verdict_for(number: int, head: str, base: str, fold_head: str, cwd: Path) -> Verdict:
    """Classify one constituent. Ancestry first (cheap), then content."""
    if is_ancestor(head, fold_head, cwd):
        return Verdict(number, "CONTAINED-ANCESTOR", f"{head[:12]} is an ancestor of the fold")
    ok, detail = content_contained(head, base, fold_head, cwd)
    if ok:
        return Verdict(number, "CONTAINED-CONTENT", detail)
    return Verdict(number, "NOT-CONTAINED", detail)


# ---------------------------------------------------------------------------
# gh
# ---------------------------------------------------------------------------


def _gh(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["gh", *argv], capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_S
    )
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr)


#: A `fold-constituents:` line is accepted only as `#<n>`/`<n>` tokens separated by
#: commas and/or whitespace. Anything else on the line REFUSES the line rather than
#: being harvested — see `parse_constituents`.
_CONSTITUENTS_LINE = re.compile(r"\s*fold-constituents\s*:\s*(.*)$")
_CONSTITUENT_TOKEN = re.compile(r"#?(\d+)")
_CONSTITUENT_SEPARATOR = re.compile(r"^[\s,]*(?:#?\d+[\s,]*)*$")


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
      * a parenthetical tail is STRIPPED and never parsed;
      * the remaining line must be ONLY `#<n>`/`<n>` tokens separated by commas
        and/or whitespace — otherwise the line is refused (returns `[]`), because
        a line this tool cannot read unambiguously is not evidence;
      * `fold` is excluded explicitly, and `main` excludes it again for the
        `--constituent` path.
    """
    for line in body.splitlines():
        match = _CONSTITUENTS_LINE.match(line)
        if not match:
            continue
        value = match.group(1)
        # Drop a trailing parenthetical entirely: it is prose, and prose here is
        # how a dropped PR gets closed.
        value = re.sub(r"\([^)]*\)", " ", value)
        value = re.sub(r"\(.*$", " ", value)  # an unterminated parenthetical
        if not value.strip():
            return []
        if not _CONSTITUENT_SEPARATOR.match(value):
            # Something other than the token grammar is on the line (a word, a
            # bare issue reference in prose, a link). Refuse the LINE, do not
            # harvest from it — a partial parse is how the wrong PR gets closed.
            return []
        numbers = [int(n) for n in _CONSTITUENT_TOKEN.findall(value)]
        return [n for n in numbers if n != fold]
    return []


def hint_constituents(body: str, fold: int) -> list[int]:
    """Prose `#NNNN` references — a HINT for the refusal message, never acted on."""
    return sorted({int(n) for n in re.findall(r"#(\d+)", body) if int(n) != fold})


def fetch_fold(number: int) -> tuple[int, str, str, str]:
    """(exit, head_sha, head_branch, body) for a PR."""
    code, out = _gh(["pr", "view", str(number), "--json", "headRefOid,headRefName,body,state"])
    if code != 0:
        return 2, "", "", ""
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return 2, "", "", ""
    return 0, doc.get("headRefOid", ""), doc.get("headRefName", ""), doc.get("body", "")


def constituent_refs(number: int) -> tuple[str, str] | None:
    """(head_sha, base_sha) for a constituent PR, or None if unreadable."""
    code, out = _gh(["pr", "view", str(number), "--json", "headRefOid,baseRefOid"])
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


def _print_report(report: Report, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"fold #{report.fold} at {report.fold_head}")
    for verdict in report.actions:
        mark = "close" if verdict.closable else "KEEP "
        print(f"  {mark} #{verdict.number:<7} {verdict.state:<20} {verdict.detail}")
    if report.closed:
        print(f"closed: {report.closed}")
    if report.labelled:
        print(f"labelled {CONSTITUENT_LABEL}: {report.labelled}")
    if report.refused:
        print(f"REFUSED (not contained in the fold — closing would destroy work): {report.refused}")
    for err in report.errors:
        print(f"  ERROR {err}", file=sys.stderr)
    print(f"{mode}: {len(report.actions)} constituent(s), {len(report.refused)} refused")


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
    code, fold_head, fold_branch, body = fetch_fold(args.fold)
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
        print(
            f"fold #{args.fold} declares no constituents: its body carries no "
            f"`fold-constituents: <n>, <n>` line and no --constituent was given.\n"
            f"The prose references {hint} are a HINT only — a fold title routinely "
            f"names a PR that was deliberately NOT folded, so this tool will not "
            f"close on a guess. Add the line to the fold body, or pass --constituent.",
            file=sys.stderr,
        )
        return 2

    # Ensure the fold head is readable locally; a shallow/missing ref would make
    # every containment check report NOT-CONTAINED and refuse a safe close.
    fetch_code, fetch_err = _git(["fetch", "--no-tags", "origin", fold_branch or fold_head], cwd)
    if fetch_code != 0:
        print(f"could not fetch the fold head ({fetch_err[:200]})", file=sys.stderr)
        return 2

    verdicts: list[Verdict] = []
    for number in constituents:
        refs = constituent_refs(number)
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
        acc_code, acc_err = _gh(["pr", "edit", str(args.fold), "--add-label", FOLD_LABEL])
        if acc_code != 0:
            print(
                f"could not apply {FOLD_LABEL} to fold #{args.fold}: {acc_err[:200]}",
                file=sys.stderr,
            )
            return 2

    if args.label_only:
        report = label_only(verdicts, args.fold, fold_head, gh=_gh, dry_run=False)
        _print_report(report, dry_run=False)
        return 1 if report.errors else 0

    report = execute(plan, gh=_gh, dry_run=not args.apply)
    _print_report(report, dry_run=not args.apply)

    if report.errors:
        return 2
    return 1 if report.refused else 0


if __name__ == "__main__":
    sys.exit(main())
