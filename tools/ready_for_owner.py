#!/usr/bin/env python3
"""``ready_for_owner`` — is this PR *truly* ready for the owner to merge from the UI?

The owner's position (2026-11-04, verbatim):

    "when PRs are truly ready for me to merge them then you open them in my regular
     chrome browser and i merge from github ui. note i am not a dev so i wont review
     them, hence the importance of the reviews before and we want them aligned with
     main so there's no regression."

He merges from the GitHub UI and **does not review**. Every defect that reaches the
merge must therefore have been caught *before* the tab opens. That failed twice on
2026-11-04 — tabs were opened for #10641 (its own ``test (a)``/``test (d)`` red,
``ai-review-gate`` **cancelled**) and #10726 (``ai-review-gate`` + lint red) — because
the list that produced those tabs checked the **merge tree** and never the **gates**.
And #10962 read ``mergeable: MERGEABLE`` while 10 of its 11 checks were ``cancelled``
or ``null``: ``mergeable`` is a tree fact, not a readiness fact.

So readiness is a COMMAND here, not a judgement. Six conditions, evaluated in order,
each printing PASS/FAIL with the RAW VALUE it read:

  1. ``tree``             ``git merge-tree --write-tree origin/main <head>`` — never the
                          ``mergeable`` flag (a stale flag is not evidence).
  2. ``gates``            the newest attempt per ``(app.slug, workflow, job)`` for every
                          required context, and ``success`` is the ONLY pass.
  3. ``review``           ``record-review.sh --check-carry`` bound to the LIVE head.
  4. ``coverage``         the CI selection covers the union of the touched files; a
                          hand-picked subset of a declared union is refused.
  5. ``exclusions``       the declared exclusions (#10226, a reversal awaiting an
                          ``OVERRIDES:`` line, a conflicting flag with a dirty tree, a
                          head younger than 15 minutes).
  6. ``blocking-finding`` no open P0/P1/P2 finding posted after the last clean verdict.

Output is exactly one of::

    READY-FOR-OWNER(<PR>, <full head sha>)      # exit 0, all six PASS
    NOT-READY(<PR>, failed=<condition,...>)     # exit 1, with each failing raw value
                                                # printed on its own check line above

⛔ **It must never print READY on a partial read.** An input that could not be read is
``FAIL(unreadable)`` — never assumed, never skipped, never defaulted to green. That is
the whole point of the tool: a human who does not review relies on this thing saying no.

Why ``success`` is the only pass, and why ``skipped``/``neutral`` are not
------------------------------------------------------------------------
GitHub's own branch protection accepts ``success``, ``skipped`` and ``neutral`` as
passing statuses. This repo deliberately does NOT, and the difference is recorded here
rather than assumed:

* ``.mergify.yml`` (the mechanism that actually merges) uses ``check-success`` for every
  entry/merge condition, and its header says why: *"a skipped or neutral check must not
  read as a pass"*. A queue entry gate weaker than the queue it gates is a false PASS.
* The owner does not review. A required check that CONCLUDED WITHOUT EXERCISING CODE
  (``skipped``/``neutral``) or was CANCELLED measured nothing, so it is not evidence
  that the tree is sound. Absence is not a pass — the fail-closed rule this file shares
  with ``/v1``-style convergence (see ``docs``/``AGENTS.md`` check-run polarity).

The divergence is deliberate and it is the strict direction: this tool can only refuse
a merge the queue would have allowed, never allow one the queue would refuse.

Grouping is NOT re-derived here
-------------------------------
``(app.slug, workflow identity, job name)`` grouping, latest-attempt-by-``id`` ordering,
voided-red handling and the unrecognised-conclusion polarity all live in
``tools/ci_verdict.py`` — the repo's single authoritative check-run reading (#5042).
This tool imports it rather than growing a second definition, and applies only its own
STRICTER predicate over the groups it returns (``success`` only, for required contexts
only). Two predicates over one grouping is the design; two groupings would be the
divergence #5042 exists to remove.

Live use::

    python tools/ready_for_owner.py 10962
    python tools/ready_for_owner.py 10962 --json
    python tools/ready_for_owner.py 10962 --repo daniel-ospina/tortoise

⛔ ONE KNOWN LIMITATION, NAMED RATHER THAN HIDDEN: condition 3 asks
``record-review.sh --check-carry``, and the review RECORD is per-machine
(``$HOME/.pi/agent/reviews``). A review minted on another box is therefore invisible to
the default read, so such a PR reads ``NOT-READY(review)`` — the safe direction, but it can
cost a re-review the fleet already paid for. ``--recorded-diff``/``--recorded-head`` pass a
known pair straight through to the recorder's own ``--recorded-diff`` seam (#3315); this
tool deliberately does NOT parse the signed marker itself.

Read-only against other PRs. It writes nothing but its own stdout: no PR comment, no
label, no merge. (A lane that cannot write to PRs — #10930 — can still run it.)

Bounded commands: there is no ``timeout``/``gtimeout`` on macOS, so this tool does its
own bounding with ``subprocess`` timeouts (``GH_TIMEOUT``/``GIT_TIMEOUT``). API reads
are cached for the life of one run, so a checked PR costs one read per endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import ci_verdict  # noqa: E402  (path set up above)

# ── the contract's constants ────────────────────────────────────────────────

CONDITIONS: tuple[str, ...] = (
    "tree",
    "gates",
    "review",
    "coverage",
    "exclusions",
    "blocking-finding",
)

#: The ONLY conclusion that passes a required context. See the module docstring.
REQUIRED_CONCLUSION = "success"

#: A head younger than this is still settling: CI may not have been scheduled, the
#: review record may not have been minted, and the queue has not seen it. Opening the
#: owner's tab on a 10-second-old head is the trap this bound closes.
HEAD_MIN_AGE = timedelta(minutes=15)

DEFAULT_REPO = "daniel-ospina/tortoise"
PROTECTED_BRANCH = "main"

#: PRs that must NEVER be declared ready, with the reason each is here. A declared
#: exclusion is an auditable list, not a heuristic: adding an entry is a deliberate act,
#: and the tool quotes the reason verbatim so the owner can see why it refused.
DECLARED_EXCLUSIONS: Mapping[int, str] = {
    # #10226: the #4282 26-method SDK deletion. It is a DRAFT, it changes the MCP/SDK
    # surface (which needs Daniel's explicit approval, not a readiness judgement), and it
    # carries 39 review rounds with an in-progress merge-up. It is never a routine
    # "merge from the UI" candidate, whatever its checks say.
    10226: (
        "declared exclusion: the #4282 SDK-surface deletion is a draft surface change "
        "awaiting an explicit owner decision, not a routine merge-from-the-UI candidate"
    ),
}

#: Labels that make the QUEUE refuse a PR at ENTRY (`.mergify.yml` `queue_conditions`).
#: `superseded-by-fold` is the only non-check entry there; it is listed here by name so a
#: change to that list is a visible edit rather than a silent divergence.
QUEUE_BLOCKING_LABELS = frozenset({"superseded-by-fold"})

#: A reviewer's blocking finding, in the shape ``scripts/review-pr.sh`` defines and
#: ``code-review`` converges on: a line that LEADS with ``[P0]``/``[P1]``/``[P2]``
#: (``[P1] tortoise/sdk.py:12 — …``). The token must be the line's FIRST word, which is
#: what makes this a finding rather than prose that happens to mention a severity.
BLOCKING_RE = re.compile(r"^\s*(?:[-*]\s*)?\**\[?(P0|P1|P2)\]?\**\s*(?=\S|$)", re.MULTILINE)
ISSUES_RE = re.compile(r"^\s*ISSUES\s+(\d+)\b", re.MULTILINE)
#: A CLEAN outcome. ⛔ THE POSITIVE VOCABULARY IS THE RECORDER'S TOO: `Outcome: clean` is
#: the declaration `scripts/record-review.sh` reads (its own suppress arm keys on it), and
#: recognising only "NO ISSUES FOUND"/"CLEAN" left the fleet's dominant format invisible —
#: measured on #10962, two clean verdicts in that format left `last-clear=-`, so once any
#: finding had been posted the PR could NEVER be certified (a permanent false NOT-READY).
CLEAR_RE = re.compile(
    r"(?im)(?:^\s*CLEAN\b|NO ISSUES FOUND|(?:^|[*\s])outcome:[\t ]*[*\s]*clean\b)"
)
#: A CAPPED / ABORTED / DECLARED-NEGATIVE review exit. ⛔ THE VOCABULARY IS COPIED FROM
#: `scripts/record-review.sh`, NOT INVENTED HERE: it is that script's arms (1) and (1b) —
#: the two it applies UNCONDITIONALLY when deciding whether a review artifact is evidence
#: of a clean outcome (`grep -qiE 'auto-fix +(stalled|failed|reached|aborted|converged|stuck)|…'`
#: and the `Outcome: not clean` / `Review — NOT CLEAN` declarations). Adopting its own list
#: keeps ONE definition of "not clean" instead of growing a second, weaker one.
#:
#: ⛔ ARM (2) OF THAT SCRIPT IS DELIBERATELY *NOT* IMPORTED — the loose `not[ _-]*clean`
#: substring search. It is prose-tolerant there for a measured reason (#1964 / tortoise#8866:
#: a genuine CLEAN review was refused because a later sentence contained the phrase), and
#: importing it would reproduce that false block on good PRs. Arms (1) and (1b) veto only
#: DECLARATIONS and STRUCTURAL EXIT PREFIXES, which is the fail-closed half with no such
#: history.
#:
#: Residual, stated rather than hidden: a reviewer who writes a brand-new negative phrasing
#: outside this vocabulary reads as NEUTRAL here. The bound on that is condition 3 — the
#: recorder refuses to mint a clean record over a `[P0]/[P1]/[P2]` finding, so the documented
#: finding shape cannot slip through this arm, only an undocumented one.
NON_CLEAN_RE = re.compile(
    r"(?i)(?:auto-fix[ \t]+(?:stalled|failed|reached|aborted|converged|stuck)"
    r"|require[s]?[ \t]+human[ \t]+(?:attention|input|review)"
    r"|unresolved[ \t]+issues?"
    r"|[0-9]+[ \t]+issues?[ \t]+remain"
    r"|test[ \t]+review[ \t]+capped"
    r"|\[ADVERSARIAL-BOUND\]"
    r"|\[NO-LOOP-BUDGET\]"
    r"|(?:^|[*\s])outcome:[ \t]*[*\s]*(?:not|non|no)[ \t_-]*clean"
    r"|(?:^|[\s>*-])review[ \t]*[\u2014\u2013-][ \t]*not[ \t_-]*clean)"
)

#: A FORMAL change request is a blocking record even when the review body is empty — the
#: findings may live entirely in inline comments, and an unresolved `CHANGES_REQUESTED`
#: is GitHub's own record of "not clean yet".
CHANGES_REQUESTED = "CHANGES_REQUESTED"
APPROVED = "APPROVED"

#: A PR that DECLARES it reverses a recorded decision. The `OVERRIDES:` line (AGENTS.md
#: USER QUESTIONS PROTOCOL) is what such a departure must carry; without it the reversal
#: is awaiting an owner decision and must not be offered as a merge.
REVERSAL_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:REVERSES|REVERSAL|REVERSING)\b[^\n]*"
    r"|reverses a recorded decision"
)
OVERRIDES_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\**OVERRIDES\**\s*:")

#: The declared-union marker a fold/batch PR writes in its body, e.g.
#:     READY-GATE UNION: #10901, #10907
#:     READY-GATE UNION: tortoise/sdk.py, tools/foo.py
UNION_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\**READY-GATE UNION\**\s*:\s*(.+)$")
PR_REF_RE = re.compile(r"^#?(\d+)$")

REVIEW_OK_TOKENS = frozenset({"head", "carry"})

GH_TIMEOUT = int(os.environ.get("READY_GATE_GH_TIMEOUT", "60"))
GIT_TIMEOUT = int(os.environ.get("READY_GATE_GIT_TIMEOUT", "180"))


class ReadError(RuntimeError):
    """An input could not be read. NEVER a verdict, and never green."""


class RateLimited(ReadError):
    """GitHub refused the read for rate-limit reasons (403/429)."""

    def __init__(self, endpoint: str, detail: str = "") -> None:
        super().__init__(f"GH-RATE-LIMITED({endpoint})" + (f": {detail}" if detail else ""))
        self.endpoint = endpoint


# ── the verdict of one condition ────────────────────────────────────────────


@dataclass(frozen=True)
class Check:
    """One condition's verdict, carrying the RAW value that produced it."""

    name: str
    passed: bool
    raw: str
    reason: str = ""
    unreadable: bool = False

    @property
    def status(self) -> str:
        if self.passed:
            return "PASS"
        return "FAIL(unreadable)" if self.unreadable else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.name,
            "status": self.status,
            "passed": self.passed,
            "reason": self.reason,
            "raw": self.raw,
            "unreadable": self.unreadable,
        }


@dataclass(frozen=True)
class Verdict:
    pr: int
    head: str
    checks: tuple[Check, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.passed)

    def line(self) -> str:
        """The one machine-greppable line. READY only when EVERY condition passed."""
        if self.ready:
            return f"READY-FOR-OWNER({self.pr}, {self.head})"
        return f"NOT-READY({self.pr}, failed={','.join(self.failed)})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "head": self.head,
            "ready": self.ready,
            "failed": list(self.failed),
            "line": self.line(),
            "checks": [c.to_dict() for c in self.checks],
        }


# ── the raw inputs, exactly as read ─────────────────────────────────────────


@dataclass
class Probe:
    """Everything the six conditions read, already fetched.

    The split is deliberate: the IO layer builds a Probe, the six condition functions
    are PURE functions of it. That is what makes the red-first test matrix possible —
    a test mutates one field of a green fixture and asserts the exact failure, with no
    network, no clock and no git.
    """

    pr: int
    repo: str = DEFAULT_REPO
    head: str = ""
    base: str = "main"
    is_draft: bool = False
    mergeable: str = "UNKNOWN"
    merge_state: str = ""
    head_committed_at: str = ""
    body: str = ""
    #: ``True`` clean · ``False`` dirty · ``None`` unreadable.
    tree: bool | None = None
    tree_raw: str = ""
    #: Newest attempt per ``(app, workflow, job)`` — ``None`` = the surface was unreadable.
    groups: tuple[ci_verdict.GroupVerdict, ...] | None = None
    groups_raw: str = ""
    #: Required contexts from branch protection — ``None`` = unreadable.
    required: tuple[str, ...] | None = None
    required_raw: str = ""
    #: ``record-review.sh --check-carry`` verdict token and exit code.
    review_token: str | None = None
    review_exit: int | None = None
    review_raw: str = ""
    #: The PR's changed files — ``None`` = unreadable.
    touched: tuple[str, ...] | None = None
    touched_raw: str = ""
    #: The declared union (from the PR body / ``--declared-union``) — ``None`` = none.
    union: tuple[str, ...] | None = None
    union_raw: str = ""
    #: A union WAS declared but could not be resolved. Distinct from ``union is None``
    #: (no declaration at all) — conflating them disables the hand-picked-subset arm.
    union_unreadable: bool = False
    #: Files for which the selection schedules NO test leg at all (``test_files`` empty).
    uncovered: tuple[str, ...] | None = None
    coverage_raw: str = ""
    #: PR comments + review bodies, chronological, each ``{id, created_at, kind, body}``.
    comments: tuple[Mapping[str, Any], ...] | None = None
    comments_raw: str = ""
    #: The PR's label names, as read. ``None`` = unreadable.
    labels: tuple[str, ...] | None = ()
    labels_raw: str = ""
    #: ``open``/``closed`` as GitHub states it, and whether it already merged.
    pr_state: str = "open"
    merged: bool = False
    #: Set by tests/callers that need a fixed clock; default = now (UTC).
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── condition 1 — tree ──────────────────────────────────────────────────────


def check_tree(probe: Probe) -> Check:
    """The tree that will actually exist — ``merge-tree``, never the ``mergeable`` flag.

    ``mergeable`` is GitHub's cached tree fact and it lags: #10790 read DIRTY after its
    fix had landed, #10962 read MERGEABLE with 10 of 11 checks dead. The flag is quoted
    in the raw value for the owner's benefit and is NEVER the decision.
    """
    flag = f"mergeable={probe.mergeable} mergeState={probe.merge_state or '-'}"
    if probe.tree is None:
        return Check("tree", False, probe.tree_raw or "-", "unreadable", unreadable=True)
    if probe.base != PROTECTED_BRANCH:
        # #10597: a merge into a non-default base lands the work on a sibling branch and
        # closes nothing. The tree that matters is `main ∪ head`; a PR based elsewhere is
        # not a candidate for a merge-from-the-UI at all.
        return Check(
            "tree",
            False,
            f"base={probe.base} head={probe.head[:12]} {flag}",
            f"base-not-main:{probe.base}",
        )
    if not probe.tree:
        return Check("tree", False, probe.tree_raw or "dirty", "dirty")
    return Check("tree", True, f"{probe.tree_raw} {flag}".strip())


# ── condition 2 — gates ─────────────────────────────────────────────────────


def _gate_token(group: ci_verdict.GroupVerdict) -> str:
    """`''` when this group PASSES the readiness gate, else the fail token."""
    conclusion = (group.conclusion or "null").lower()
    if group.voided_red:
        # A measured red that no newer SUCCESS re-measured. A cancellation — or a
        # skipped/neutral re-run — cannot clear it (ci_verdict's rule, and the rail's).
        return "red-voided"
    if group.state == ci_verdict.GROUP_IN_FLIGHT:
        return "in-flight"
    if group.state == ci_verdict.GROUP_RED:
        return f"red:{conclusion}"
    if group.state == ci_verdict.GROUP_ABSENT:
        # cancelled / stale: measured NOTHING. Absence is not a pass.
        return f"absent:{conclusion}"
    if conclusion != REQUIRED_CONCLUSION:
        # green for GitHub, not for us: skipped/neutral exercised no code.
        return f"non-measuring:{conclusion}"
    return ""


def check_gates(probe: Probe) -> Check:
    """Every required context must have a newest attempt concluded ``success``.

    In-flight is a FAIL here, not a wait: a head whose checks have not concluded is not
    ready *now*, and the answer the owner needs is about now.
    """
    if probe.required is None or not probe.required:
        # An EMPTY required set is not "nothing is required" — it is a set that was not
        # read. Zero protection contexts, a truncated payload, or a hand-written fixture
        # must never read as an unconditional pass.
        return Check(
            "gates",
            False,
            probe.required_raw or "required context set is empty",
            "unreadable",
            unreadable=True,
        )
    if probe.groups is None:
        return Check("gates", False, probe.groups_raw or "-", "unreadable", unreadable=True)
    # ⛔ The BUCKET is the check's JOB NAME, because that is what branch protection
    # names: `checks[].context` IS the job name (`python-ci-gate`, `docs`, `ai-review-gate`).
    # Matching anything else would leave a required context unmatched forever. What is NOT
    # re-derived here is the GROUPING — whether two check-runs are the same workflow's next
    # attempt, and which attempt is newest, is `ci_verdict`'s (`(app.slug, workflow, job)`,
    # latest by `id`). Two workflows may therefore publish one job name and land in one
    # bucket: every group in it must then pass, which is the fail-CLOSED direction (a bad
    # attempt in either workflow fails the context) and is why the spread below is over all
    # matching groups rather than the newest only.
    by_job: dict[str, list[ci_verdict.GroupVerdict]] = {}
    for group in probe.groups:
        by_job.setdefault(group.job, []).append(group)

    failed: list[str] = []
    seen: list[str] = []
    for context in probe.required:
        groups = by_job.get(context) or []
        if not groups:
            # A required check that is ABSENT is a FAIL, never a skip. #10962 currently
            # has no `python-ci-gate` run at all.
            failed.append(f"missing:{context}")
            seen.append(f"{context}=MISSING(no check-run on this head)")
            continue
        tokens = []
        for group in sorted(groups, key=lambda g: g.check_id or 0, reverse=True):
            token = _gate_token(group)
            seen.append(f"{context}={group.status}/{(group.conclusion or 'null')}#{group.check_id}")
            tokens.append(token)
        # ⛔ EVERY failing token is reported, never just the newest. When a check's
        # workflow identity cannot be resolved (a `pull_request_target` run is absent from
        # `actions/runs?head_sha=`, so ci_verdict falls back to a per-run key), ONE required
        # context has SEVERAL groups — and reporting only the newest would let a fresh
        # `queued` attempt HIDE a measured `failure` sitting right behind it. That is the
        # masking the voided-red rule exists to stop inside ci_verdict; reporting one token
        # here would reintroduce it in this consumer.
        for token in tokens:
            if token:
                failed.append(f"{token}:{context}")

    raw = "; ".join(seen) if seen else "no check-runs on this head"
    if failed:
        return Check("gates", False, f"required={len(probe.required)} {raw}", ",".join(failed))
    return Check(
        "gates",
        True,
        f"required={len(probe.required)} all/{REQUIRED_CONCLUSION} {raw}",
    )


# ── condition 3 — review, bound to the live head ────────────────────────────


def check_review(probe: Probe) -> Check:
    """A valid review record must cover what would merge, at the LIVE head.

    ``record-review.sh --check-carry`` is the ONE way to ask (it runs the same predicate
    the merge gate runs), so this tool calls it rather than re-deriving "is the record
    stale". Its exit/doc contract:

    ============ ===== ==========================================================
    token        exit  meaning
    ============ ===== ==========================================================
    head         0     the record is bound to the CURRENT head
    carry        0     the reviewed DIFF is unchanged across the head move
    fresh        1     the diff CHANGED — the record does not cover the merge
    unreadable   3     the live diff could not be read (RETRY, never an assumption)
    nodigest     5     the record carries no usable diff digest
    carry-patch  6     the patch is identical but the rendered diff moved (re-record)
    norecord     4     no review record at all
    ============ ===== ==========================================================
    """
    raw = probe.review_raw or f"token={probe.review_token or '-'} exit={probe.review_exit}"
    if probe.review_token is None:
        return Check("review", False, raw, "unreadable", unreadable=True)
    if probe.review_token in REVIEW_OK_TOKENS and probe.review_exit == 0:
        return Check("review", True, raw)
    if probe.review_token == "unreadable":
        return Check("review", False, raw, "diff-unreadable-retry", unreadable=True)
    return Check("review", False, raw, f"not-bound:{probe.review_token}")


# ── condition 4 — coverage of the declared union ────────────────────────────


def check_coverage(probe: Probe) -> Check:
    """The CI selection must cover the UNION of the touched files.

    Two ways this fails, and both are the "hand-picked subset" the brief names:

    1. **A declared union that the tree under-delivers** — a fold/batch PR that says it
       carries ``#A, #B, #C`` but whose diff is a strict subset. The checks then validated
       less than the PR claims, and a green gate is evidence about the wrong set.
    2. **A touched file for which the selection schedules NO test leg at all** —
       ``select()`` returns no surface, ``full is False`` and an EMPTY ``test_files``. An
       unfamiliar path is not this: ``select()`` fails closed to FULL for unknown paths,
       and a path the manifest maps to no surface still runs the always-on core smoke set
       (measured: ``select(["tools/ready_for_owner.py"])`` → ``surfaces=[]``,
       ``full=False``, **72 tests**). Only a genuinely empty leg means nothing ran, and
       that is what fails here.

    A PR with no declared union is covered by its own diff — that is the declared set,
    not a hand-picked one.

    ⛔ A DECLARED union that could not be READ is ``FAIL(unreadable)``. It must never be
    silently downgraded to "no declared union": the two states resolve to the same
    ``union is None``, and conflating them would disable this arm with one transient 502
    while the raw line still claims ``union=pr-diff``.
    """
    if probe.touched is None or not probe.touched:
        return Check("coverage", False, probe.touched_raw or "-", "unreadable", unreadable=True)
    if probe.union_unreadable:
        return Check(
            "coverage",
            False,
            probe.union_raw or "declared union unreadable",
            "unreadable",
            unreadable=True,
        )
    if probe.uncovered is None:
        return Check("coverage", False, probe.coverage_raw or "-", "unreadable", unreadable=True)
    touched = set(probe.touched)
    raw = probe.coverage_raw or f"files={len(touched)}"
    if probe.union is not None and not probe.union:
        return Check(
            "coverage",
            False,
            f"{raw} union=declared(0) {probe.union_raw}".strip(),
            "empty-declared-union",
        )
    if probe.union is not None:
        declared = set(probe.union)
        if touched < declared:
            missing = sorted(declared - touched)
            return Check(
                "coverage",
                False,
                f"{raw} union={len(declared)} touched={len(touched)} missing={missing[:5]}",
                f"hand-picked-subset:{len(missing)}",
            )
        raw = f"{raw} union=declared({len(declared)})"
    else:
        raw = f"{raw} union=pr-diff"
    if probe.uncovered:
        return Check(
            "coverage",
            False,
            f"{raw} uncovered={list(probe.uncovered)[:5]}",
            "uncovered-files",
        )
    return Check("coverage", True, raw)


# ── condition 5 — the declared exclusions ───────────────────────────────────


def check_exclusions(probe: Probe) -> Check:
    """The four things that make a PR not-a-candidate even with green gates.

    * a **declared exclusion** (``DECLARED_EXCLUSIONS``) — quoted verbatim;
    * a **declared reversal awaiting an ``OVERRIDES:`` line** — the departure from a
      recorded decision is the owner's to rule on, not the gate's to wave through;
    * **``CONFLICTING`` with a dirty tree** — note the ORDER: the flag alone is NOT a
      failure (GitHub's cached flag is stale often enough that #10790 read DIRTY after
      its fix landed). The authoritative tree read is condition 1; the flag is only a
      diagnosis label attached when the tree agrees;
    * a **head younger than 15 minutes** — evidence has not settled.
    """
    reasons: list[str] = []
    parts: list[str] = []

    # The PR must still be OPEN and unmerged: a merged PR cannot be merged from the UI, and
    # certifying one opens a dead tab (measured: #10916, merged, printed READY before this).
    if probe.merged or probe.pr_state.lower() not in ("", "open"):
        reasons.append(f"not-open:{probe.pr_state or 'unknown'}{'-merged' if probe.merged else ''}")
        parts.append(f"state={probe.pr_state or 'unknown'} merged={str(probe.merged).lower()}")

    if probe.labels is None:
        return Check("exclusions", False, probe.labels_raw or "-", "unreadable", unreadable=True)

    # ⛔ THE QUEUE-ENTRY GUARD. `.mergify.yml`'s `queue_conditions` carry ONE non-check
    # condition — `-label=superseded-by-fold` — and it is the one entry this gate did not
    # model, so a constituent PR left OPEN and labelled by `tools/batch_open.py` (its
    # `label_only()` path: "gate queue entry, close nothing") was green + reviewed + old and
    # read READY-FOR-OWNER, while the queue REFUSES it at entry. The owner merges from the
    # UI, which bypasses Mergify entirely, so the constituent would land separately and the
    # fold would re-apply the same diff to a moved main — the exact outcome recorded at
    # `.mergify.yml`'s own `superseded-by-fold` note. A gate that can allow what the queue
    # refuses is the false PASS this tool exists to prevent.
    blocked_labels = sorted(set(probe.labels) & QUEUE_BLOCKING_LABELS)
    if blocked_labels:
        reasons.append("queue-blocking-label:" + ",".join(blocked_labels))
        parts.append(f"labels={sorted(probe.labels)}")

    declared = DECLARED_EXCLUSIONS.get(probe.pr)
    if declared:
        reasons.append(f"declared-exclusion:#{probe.pr}")
        parts.append(declared)

    body = probe.body or ""
    if REVERSAL_RE.search(body) and not OVERRIDES_RE.search(body):
        reasons.append("reversal-awaiting-overrides")
        parts.append("body declares a reversal but carries no OVERRIDES: line")

    flag = (probe.mergeable or "UNKNOWN").upper()
    if flag == "CONFLICTING" and probe.tree is not True:
        reasons.append("conflicting-flag-with-dirty-tree")
        parts.append(f"mergeable={probe.mergeable} and merge-tree is not clean")
    elif flag == "CONFLICTING":
        parts.append(f"mergeable={probe.mergeable} (flag IGNORED: merge-tree is clean)")

    if probe.is_draft:
        reasons.append("draft")
        parts.append("isDraft=true")

    age_s: int | None = None
    committed = _parse_instant(probe.head_committed_at)
    if committed is None:
        return Check(
            "exclusions",
            False,
            probe.head_committed_at or "-",
            "unreadable",
            unreadable=True,
        )
    age = probe.now - committed
    age_s = int(age.total_seconds())
    parts.append(f"head-age={age_s}s")
    if age < HEAD_MIN_AGE:
        reasons.append(f"head-too-young:{age_s}s")
        parts.append(f"< {int(HEAD_MIN_AGE.total_seconds())}s")

    raw = f"mergeable={probe.mergeable} mergeState={probe.merge_state or '-'} " + " ".join(parts)
    if reasons:
        return Check("exclusions", False, raw.strip(), ",".join(reasons))
    return Check("exclusions", True, raw.strip())


def _parse_instant(value: str) -> datetime | None:
    """Parse a GitHub ISO-8601 instant. ``None`` when it is absent or unparseable."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# ── condition 6 — no OPEN blocking finding ──────────────────────────────────


def check_blocking_finding(probe: Probe) -> Check:
    """No P0/P1/P2 finding may still be open — the last word must be a clean verdict.

    "Open" is decided by ORDER, not by prose: walk the PR's comments and review bodies
    chronologically; a clean verdict closes whatever was open, and a blocking finding
    reopens it. Whatever the LAST one was, that is the state. Counting findings instead
    would read a fixed P0 as still open forever.
    """
    if probe.comments is None:
        return Check(
            "blocking-finding",
            False,
            probe.comments_raw or "-",
            "unreadable",
            unreadable=True,
        )
    open_finding: dict[str, Any] | None = None
    last_clear: dict[str, Any] | None = None
    blocking_seen = 0
    for item in sorted(
        probe.comments, key=lambda c: (str(c.get("created_at") or ""), int(c.get("id") or 0))
    ):
        text = str(item.get("body") or "")
        issues = ISSUES_RE.search(text)
        blocking = (
            BLOCKING_RE.search(text)
            or (issues and int(issues.group(1)) > 0)
            or NON_CLEAN_RE.search(text)
            or str(item.get("state") or "").upper() == CHANGES_REQUESTED
        )
        if blocking:
            blocking_seen += 1
            open_finding = item
            continue
        if CLEAR_RE.search(text) or str(item.get("state") or "").upper() == APPROVED:
            # A formal APPROVAL is a clear in its own right — otherwise a change request
            # at T1 could never be closed by an approval at T2, because each item is
            # graded on its own state forever, and the PR would read NOT-READY to eternity.
            last_clear = item
            open_finding = None
    raw = (
        f"comments={len(probe.comments)} blocking={blocking_seen} "
        f"last-clear={'-' if last_clear is None else last_clear.get('id')}"
    )
    if open_finding is None:
        return Check("blocking-finding", True, raw)
    snippet = " ".join(str(open_finding.get("body") or "").split())[:140]
    if not snippet:
        # A change request can carry its findings entirely inline. The raw value must still
        # say WHAT blocked, or the owner sees an empty «» and learns nothing.
        snippet = (
            f"review state {open_finding.get('state') or 'unknown'} with an empty body — "
            "the findings are inline"
        )
    return Check(
        "blocking-finding",
        False,
        f"{raw} open={open_finding.get('kind', 'comment')}:{open_finding.get('id')} "
        f"at={open_finding.get('created_at')} «{snippet}»",
        "open-blocking-finding",
    )


# ── evaluation ──────────────────────────────────────────────────────────────

EVALUATORS = {
    "tree": check_tree,
    "gates": check_gates,
    "review": check_review,
    "coverage": check_coverage,
    "exclusions": check_exclusions,
    "blocking-finding": check_blocking_finding,
}


def evaluate(probe: Probe) -> Verdict:
    """Run the six conditions IN ORDER. Every one runs — no short-circuit.

    Running all six (rather than stopping at the first failure) is deliberate: the owner
    and the lane both need the whole picture, and a tool that stops early hides the
    second defect behind the first.
    """
    checks = tuple(EVALUATORS[name](probe) for name in CONDITIONS)
    return Verdict(pr=probe.pr, head=probe.head, checks=checks)


def render(verdict: Verdict) -> str:
    lines: list[str] = []
    for index, check in enumerate(verdict.checks, start=1):
        line = (
            f"[{index}/{len(verdict.checks)}] {check.name:<17} {check.status:<17} raw: {check.raw}"
        )
        if check.reason:
            line += f"  reason: {check.reason}"
        lines.append(line.rstrip())
    failures = [c for c in verdict.checks if not c.passed]
    if failures:
        lines.append("")
        lines.append("REASONS:")
        for check in failures:
            lines.append(f"  {check.name}: {check.reason or 'failed'} — {check.raw}")
    lines.append("")
    lines.append(verdict.line())
    return "\n".join(lines)


# ── IO layer: build a Probe from the live world ─────────────────────────────


class Gh:
    """One bounded, cached ``gh`` reader for a single run.

    Caching is per-run on purpose: a checked PR costs one read per endpoint, and the
    three-PR proof run costs three, not twenty. ``403``/``429`` is surfaced as
    ``RateLimited`` so the caller can print ``GH-RATE-LIMITED(<endpoint>)`` rather than
    pretending the surface was empty — an unreadable surface is never "no checks".
    """

    def __init__(self, repo: str, timeout: int = GH_TIMEOUT) -> None:
        self.repo = repo
        self.timeout = timeout
        self._cache: dict[str, Any] = {}

    def pages(self, endpoint: str) -> list[Any]:
        """Every page of a paginated read, as raw JSON values (cached)."""
        key = "pages:" + endpoint
        if key in self._cache:
            return self._cache[key]
        url = endpoint if endpoint.startswith("http") else f"repos/{self.repo}/{endpoint}"
        try:
            proc = subprocess.run(
                ["gh", "api", "--paginate", url],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReadError(f"gh api {url}: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            message = detail[0] if detail else f"exit {proc.returncode}"
            lowered = message.lower()
            if "rate limit" in lowered or " 403" in lowered or "429" in lowered:
                raise RateLimited(url, message)
            raise ReadError(f"gh api {url}: {message}")
        pages = _decode_pages(proc.stdout)
        if not pages:
            # An empty body is a READ FAILURE, never an empty surface.
            raise ReadError(f"gh api {url} returned an empty body")
        self._cache[key] = pages
        return pages

    def api(self, endpoint: str) -> Any:
        pages = self.pages(endpoint)
        return pages[0] if len(pages) == 1 else pages

    def pr(self, number: int) -> Mapping[str, Any]:
        raw = self.api(f"pulls/{number}")
        if not isinstance(raw, Mapping):
            raise ReadError(f"pulls/{number}: unexpected payload")
        return raw


def _decode_pages(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        pass
    pages: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        pages.append(value)
    return pages


def _flatten(payload: Any) -> list[Any]:
    """``gh api --paginate`` emits one object per page; flatten list pages."""
    if isinstance(payload, Mapping):
        return [payload]
    out: list[Any] = []
    if isinstance(payload, list):
        for page in payload:
            if isinstance(page, list):
                out.extend(page)
            elif isinstance(page, Mapping):
                out.append(page)
    return out


def _run(cmd: Sequence[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError(f"{' '.join(cmd)}: {exc}") from exc


def _mergeable_flag(value: Any) -> str:
    """Normalise the ``mergeable`` flag to ``MERGEABLE``/``CONFLICTING``/``UNKNOWN``.

    The REST payload types it as a BOOLEAN (``true``/``false``/``null``) while GraphQL
    types it as one of those three STRINGS. Undefined coercion is how a flag check
    silently stops firing: ``str(True)`` is ``"True"``, which is never ``"CONFLICTING"``,
    so the CONFLICTING arm would simply never run on a REST read.
    """
    if isinstance(value, bool):
        return "MERGEABLE" if value else "CONFLICTING"
    text = str(value or "UNKNOWN").strip().upper()
    return text or "UNKNOWN"


def _read_head_commit_date(gh: Gh, head: str) -> str:
    raw = gh.api(f"commits/{head}")
    if isinstance(raw, Mapping):
        commit = raw.get("commit") or {}
        for key in ("committer", "author"):
            stamp = (commit.get(key) or {}).get("date")
            if stamp:
                return str(stamp)
    raise ReadError(f"commits/{head}: no committer date")


def _read_required_contexts(gh: Gh) -> tuple[tuple[str, ...], str]:
    """Required status-check contexts from branch protection. Unreadable ⟹ ReadError.

    ``checks[].context`` and the legacy ``contexts[]`` are unioned: a ruleset-era repo
    populates one and a classic-protection repo the other, and reading only one would
    silently drop a required check (a false PASS, the one failure this tool exists for).
    """
    raw = gh.api(f"branches/{PROTECTED_BRANCH}/protection")
    if not isinstance(raw, Mapping):
        raise ReadError(f"branches/{PROTECTED_BRANCH}/protection: unexpected payload")
    block = raw.get("required_status_checks")
    if not isinstance(block, Mapping):
        raise ReadError(
            f"branches/{PROTECTED_BRANCH}/protection carries no required_status_checks "
            "(no protection configured, or a token without admin read) — the required "
            "set is UNKNOWN, so no gate can be certified"
        )
    contexts: list[str] = []
    for entry in block.get("checks") or []:
        if isinstance(entry, Mapping) and entry.get("context"):
            contexts.append(str(entry["context"]))
    for entry in block.get("contexts") or []:
        if isinstance(entry, str) and entry:
            contexts.append(entry)
    if not contexts:
        raise ReadError(f"branches/{PROTECTED_BRANCH}/protection declares no required context")
    deduped = tuple(sorted(dict.fromkeys(contexts)))
    return deduped, f"required={len(deduped)} {list(deduped)}"


def _read_check_groups(
    gh: Gh, head: str, repo: str
) -> tuple[tuple[ci_verdict.GroupVerdict, ...], str]:
    """Newest attempt per ``(app, workflow, job)`` — delegated to ``ci_verdict``.

    The FETCH goes through this module's bounded, cached ``Gh`` reader; the GROUPING and
    the completeness check (``collect_items`` refuses a truncated listing) stay in
    ``ci_verdict``, which is the repo's single authoritative check-run reading (#5042).
    Nothing about what counts as a group or an attempt is re-derived here.
    """
    check_url = ci_verdict.CHECK_RUNS_URL.format(repo=repo, sha=head)
    run_url = ci_verdict.RUNS_URL.format(repo=repo, sha=head)
    prefix = f"repos/{repo}/"
    check_runs = ci_verdict.collect_items(
        gh.pages(check_url.removeprefix(prefix)),
        ci_verdict.CHECK_RUNS_KEY,
        check_url,
    )
    runs = ci_verdict.collect_items(
        gh.pages(run_url.removeprefix(prefix)), ci_verdict.RUNS_KEY, run_url
    )
    verdict = ci_verdict.compute_verdict(head, check_runs, runs, repo=repo)
    counts = verdict.counts
    raw = (
        f"surface={len(verdict.groups)} groups "
        f"(green={counts.get(ci_verdict.GROUP_GREEN, 0)} "
        f"red={counts.get(ci_verdict.GROUP_RED, 0)} "
        f"in-flight={counts.get(ci_verdict.GROUP_IN_FLIGHT, 0)} "
        f"absent={counts.get(ci_verdict.GROUP_ABSENT, 0)}) commit-verdict={verdict.verdict}"
    )
    return verdict.groups, raw


def _read_tree(gh: Gh, pr: int, head: str, workdir: Path) -> tuple[bool | None, str]:
    """``git merge-tree --write-tree origin/main <head>`` — the authoritative tree read.

    The PR head is fetched into a LOCAL throwaway remote-tracking ref
    (``refs/remotes/ready-gate/pr-<n>``) — a read of the remote, no push, no branch of
    the PR touched. If the fetched sha is not the live head the read is REFUSED rather
    than compared: comparing a stale local object would certify a tree nobody asked about.
    """
    ref = f"refs/remotes/ready-gate/pr-{pr}"
    fetch = _run(
        ["git", "fetch", "--no-tags", "origin", f"+refs/pull/{pr}/head:{ref}"],
        GIT_TIMEOUT,
        cwd=workdir,
    )
    if fetch.returncode != 0:
        return None, f"git fetch refs/pull/{pr}/head failed: {_first_line(fetch.stderr)}"
    rev = _run(["git", "rev-parse", ref], GIT_TIMEOUT, cwd=workdir)
    fetched = (rev.stdout or "").strip()
    if rev.returncode != 0 or len(fetched) != 40:
        return None, f"git rev-parse {ref} was unreadable"
    if fetched != head:
        return None, f"fetched {fetched[:12]} != live head {head[:12]} (head moved mid-read)"
    base = _run(["git", "fetch", "--no-tags", "origin", PROTECTED_BRANCH], GIT_TIMEOUT, cwd=workdir)
    if base.returncode != 0:
        return None, f"git fetch origin {PROTECTED_BRANCH} failed: {_first_line(base.stderr)}"
    merge_tree = _run(
        ["git", "merge-tree", "--write-tree", f"origin/{PROTECTED_BRANCH}", fetched],
        GIT_TIMEOUT,
        cwd=workdir,
    )
    detail = _first_line(merge_tree.stdout) or _first_line(merge_tree.stderr)
    if merge_tree.returncode == 0:
        tree = (merge_tree.stdout or "").strip().splitlines()
        oid = tree[0] if tree else "-"
        return True, f"merge-tree write-tree ok tree={oid[:12]} base=origin/{PROTECTED_BRANCH}"
    if merge_tree.returncode == 1:
        return False, f"merge-tree CONFLICTS base=origin/{PROTECTED_BRANCH}: {detail}"
    return None, f"git merge-tree exited {merge_tree.returncode}: {detail}"


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _review_script(workdir: Path) -> Path | None:
    """The recorder, or ``None`` when the machine-local ``scripts/`` pointer is absent.

    Its own function so a test can simulate a CI checkout (where `scripts/` is documented
    ABSENT — the pointer is gitignored and materialised per machine) without depending on
    the checkout it happens to run in. A test that needs the real file turns red for an
    ENVIRONMENT reason, and the tempting fix is then to weaken the test.
    """
    script = workdir / "scripts" / "record-review.sh"
    return script if script.is_file() else None


def _read_review(
    pr: int,
    repo: str,
    workdir: Path,
    recorded_diff: str | None = None,
    recorded_head: str | None = None,
) -> tuple[str | None, int | None, str]:
    """Ask the gate's OWN predicate: ``record-review.sh --check-carry``.

    The script is the single reader of "does this record cover what would merge", so it
    is invoked rather than re-implemented. Its first stdout field is the verdict token.

    ⛔ THE RECORD FILE IS PER-MACHINE (``$HOME/.pi/agent/reviews``), so a review minted on
    ANOTHER box is invisible to the default read — ``record-review.sh``'s own header
    measures that population ("of 45 open PRs, 12 had a marker whose ``diff=`` equals the
    PR's live diff and no record file here"). ``--recorded-diff``/``--recorded-head`` is
    the recorder's seam for exactly that reader (#3315); this tool passes them through
    rather than parsing the signed marker itself, because a second marker parser is a
    second definition. Without them a cross-box review reads ``norecord`` — which FAILS,
    so the direction is safe, but it costs a re-review the fleet already paid for.
    """
    script = _review_script(workdir)
    if script is None:
        return (
            None,
            None,
            f"{workdir / 'scripts' / 'record-review.sh'} is absent "
            "(the scripts/ pointer is not materialised)",
        )
    cmd = ["bash", str(script), "--check-carry", str(pr), repo]
    if recorded_diff:
        cmd += ["--recorded-diff", recorded_diff]
        if recorded_head:
            cmd += ["--recorded-head", recorded_head]
    proc = _run(cmd, GIT_TIMEOUT, cwd=workdir)
    token = ""
    for line in (proc.stdout or "").splitlines():
        fields = line.split("\t")
        if fields and fields[0].strip():
            token = fields[0].strip()
            break
    digest = f"record={_field(proc.stdout, 1)} live={_field(proc.stdout, 2)}"
    message = _first_line(proc.stderr)
    raw = f"token={token or '-'} exit={proc.returncode} {digest}"
    if message:
        raw += f" «{message[:160]}»"
    if not token:
        # ⛔ THE EXIT CODES ARE THE SCRIPT'S, NOT A GUESS: `norecord` is 4 (#2234) and
        # `fresh` is 1. An earlier version mapped rc==1 to `norecord`, which told a lane
        # to RE-RECORD a diff that had changed and told it to RETRY a PR with no record
        # at all — both still FAIL, so no false READY, but both sent the lane the wrong way.
        return {1: "fresh", 4: "norecord"}.get(proc.returncode, "unreadable"), proc.returncode, raw
    return token, proc.returncode, raw


def _field(stdout: str, index: int) -> str:
    for line in (stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) > index:
            return fields[index].strip()
    return "-"


def _read_touched(gh: Gh, pr: int) -> tuple[tuple[str, ...], str]:
    files = _flatten(gh.api(f"pulls/{pr}/files?per_page=100"))
    names: list[str] = []
    for entry in files:
        if isinstance(entry, Mapping) and entry.get("filename"):
            names.append(str(entry["filename"]))
    if not names:
        raise ReadError(f"pulls/{pr}/files returned no filenames")
    return tuple(names), f"files={len(names)}"


def declared_union(body: str) -> tuple[tuple[str, ...] | None, str]:
    """The union a PR DECLARES it carries, from its ``READY-GATE UNION:`` line."""
    match = UNION_RE.search(body or "")
    if not match:
        return None, "no declared union"
    entries = [p.strip() for p in re.split(r"[,\s]+", match.group(1)) if p.strip()]
    entries = [entry.removeprefix("#") for entry in entries]
    return tuple(entries), f"declared union entries={len(entries)}"


def _resolve_union(entries: tuple[str, ...], gh: Gh) -> tuple[tuple[str, ...] | None, str]:
    """Expand a declared union: ``#N`` entries become that PR's changed files.

    Returns ``(None, why)`` ONLY when the read failed. An EMPTY declaration returns
    ``((), why)`` — a malformed marker, which ``check_coverage`` fails rather than
    silently treating as "no union declared" (the same collapse the read-error arm avoids).
    """
    if not entries:
        return (), "declared union has no entries (malformed READY-GATE UNION: line)"
    resolved: list[str] = []
    prs: list[int] = []
    for entry in entries:
        ref = PR_REF_RE.match(entry)
        if ref:
            prs.append(int(ref.group(1)))
        else:
            resolved.append(entry)
    for number in prs:
        files, _ = _read_touched(gh, number)
        resolved.extend(files)
    return tuple(sorted(dict.fromkeys(resolved))), (
        f"declared union: {len(prs)} PR(s) + {len(entries) - len(prs)} path(s) "
        f"= {len(set(resolved))} file(s)"
    )


def _read_coverage(touched: tuple[str, ...]) -> tuple[tuple[str, ...] | None, str]:
    """Ask the repo's own selection function whether it covers the touched set.

    ``tools/ci_selection.select()`` is the single declared function (#1021): it either
    matches a file to a surface, falls closed to FULL, or maps it to the always-on core
    smoke set. A path therefore has NO test leg only when ``surfaces == []``,
    ``full is False`` AND ``test_files`` is empty — and that is the only shape reported as
    uncovered. (An earlier version treated ``surfaces == [] and not full`` as uncovered;
    measured, ``select(["tools/ready_for_owner.py"])`` returns exactly that shape with
    **72 tests** scheduled, so it would have failed this tool's own PR and every PR that
    adds a `tools/*.py` file. The docstring said "recorded, not failed"; the code was
    wrong, not the docstring.)

    Returns ``(uncovered, raw)``; ``uncovered is None`` means the selection was UNREADABLE
    and the caller must FAIL rather than assume coverage.
    """
    try:
        from tools import ci_selection
    except Exception as exc:  # pragma: no cover - import guard
        return None, f"ci_selection import failed: {exc}"
    try:
        manifest = ci_selection.load_manifest()
        explain: dict[str, Any] = {}
        selection = ci_selection.select(list(touched), "pull_request", manifest, explain)
    except Exception as exc:
        return None, f"ci_selection.select failed: {exc}"
    surfaces = selection.get("surfaces") or []
    full = bool(selection.get("full"))
    uncovered: list[str] = []
    tier1_only: list[str] = []
    if not full:
        for path in touched:
            one = ci_selection.select([path], "pull_request", manifest, {})
            if one.get("full") or (one.get("surfaces") or []):
                continue
            test_files = one.get("test_files")
            has_leg = test_files == "ALL" or bool(test_files)
            if has_leg:
                # No surface of its own, but the always-on core smoke set runs.
                tier1_only.append(path)
            else:
                uncovered.append(path)
    raw = (
        f"files={len(touched)} full={str(full).lower()} surfaces={list(surfaces)} "
        f"core-smoke-only={len(tier1_only)} no-leg={len(uncovered)}"
    )
    return tuple(uncovered), raw


def _read_comments(gh: Gh, pr: int) -> tuple[tuple[Mapping[str, Any], ...], str]:
    """Every artifact that can carry a review verdict — issue comments, review BODIES
    (with their ``state``), and INLINE review comments.

    ⛔ Each of the three was a real hole. A review can carry an EMPTY body with all its
    findings inline and its state set to ``CHANGES_REQUESTED``; reading only non-empty
    bodies dropped it entirely, so a PR with a formal unresolved change request read as
    CLEAN here.
    """
    comments = _flatten(gh.api(f"issues/{pr}/comments?per_page=100"))
    reviews = _flatten(gh.api(f"pulls/{pr}/reviews?per_page=100"))
    inline = _flatten(gh.api(f"pulls/{pr}/comments?per_page=100"))
    out: list[Mapping[str, Any]] = []
    for kind, items in (("comment", comments), ("review", reviews), ("inline", inline)):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            body = str(item.get("body") or "")
            state = str(item.get("state") or "").upper()
            if not body and state != CHANGES_REQUESTED:
                continue
            out.append(
                {
                    "id": item.get("id"),
                    "created_at": item.get("created_at") or item.get("submitted_at") or "",
                    "kind": kind,
                    "state": state,
                    # A change request with an empty body still has to say WHAT it is.
                    "body": body or f"review state {CHANGES_REQUESTED}",
                }
            )
    if not out:
        # A 200 with `[]` on all three endpoints is a legitimate "nothing was posted yet",
        # not a read failure — the distinction is `()` (read, empty) vs `None`
        # (unreadable), and conflating them made every brand-new PR FAIL as unreadable.
        return (), "comments=0"
    return tuple(out), f"comments={len(out)}"


def collect_probe(
    pr: int,
    repo: str = DEFAULT_REPO,
    workdir: Path | None = None,
    now: datetime | None = None,
    recorded_diff: str | None = None,
    recorded_head: str | None = None,
) -> Probe:
    """Read the live world into a Probe. Every unreadable input is recorded, not raised.

    A failed read is ``FAIL(unreadable)`` on its own condition — never a crash (a crashed
    gate is indistinguishable from an unrun one) and never a default.
    """
    workdir = workdir or REPO_ROOT
    gh = Gh(repo)
    probe = Probe(pr=pr, repo=repo, now=now or datetime.now(UTC))

    try:
        pull = gh.pr(pr)
    except ReadError as exc:
        probe.tree_raw = probe.required_raw = probe.groups_raw = probe.touched_raw = str(exc)
        probe.review_raw = probe.comments_raw = probe.coverage_raw = str(exc)
        return probe

    head = str(pull.get("head", {}).get("sha") or "")
    probe.head = head
    probe.base = str(pull.get("base", {}).get("ref") or "")
    probe.is_draft = bool(pull.get("draft"))
    probe.mergeable = _mergeable_flag(pull.get("mergeable"))
    # REST spells it `mergeable_state`; GraphQL (`gh pr view --json`) spells it
    # `mergeStateStatus`. Read both, so the reported value is the same either way.
    probe.merge_state = str(pull.get("mergeable_state") or pull.get("mergeStateStatus") or "")
    probe.body = str(pull.get("body") or "")
    probe.pr_state = str(pull.get("state") or "")
    probe.merged = bool(pull.get("merged") or pull.get("merged_at"))
    labels = pull.get("labels")
    if isinstance(labels, list):
        probe.labels = tuple(
            str(entry.get("name"))
            for entry in labels
            if isinstance(entry, Mapping) and entry.get("name")
        )
        probe.labels_raw = f"labels={sorted(probe.labels)}"
    else:
        # An ABSENT `labels` key is not "no labels": the entry guard cannot be evaluated,
        # so it is unreadable rather than silently empty.
        probe.labels = None
        probe.labels_raw = "pulls/N carried no labels array"

    try:
        probe.head_committed_at = _read_head_commit_date(gh, head)
    except ReadError:
        # An unreadable head date makes `exclusions` FAIL(unreadable) on its own; it is
        # NOT evidence about the tree, so it must not be written into `tree_raw`.
        probe.head_committed_at = ""

    probe.required, probe.required_raw = _try(gh, _read_required_contexts)
    probe.groups, probe.groups_raw = _try(
        gh, lambda g: _read_check_groups(g, head, repo), surface="check-runs"
    )
    probe.tree, probe.tree_raw = _try_tree(gh, pr, head, workdir)
    probe.review_token, probe.review_exit, probe.review_raw = _try_review(
        pr, repo, workdir, recorded_diff, recorded_head
    )
    probe.touched, probe.touched_raw = _try(gh, lambda g: _read_touched(g, pr))
    if probe.body:
        entries, _union_raw = declared_union(probe.body)
        # ⛔ `is not None`, NOT truthiness: an empty/malformed `READY-GATE UNION:` line
        # parses to `()`, and `if entries:` sent it down the "no union declared" branch —
        # leaving the `empty-declared-union` fail arm unreachable from the live path
        # (fixture-only), and silently certifying a fold PR with a broken marker.
        if entries is not None:
            # A DECLARED union that cannot be resolved is UNREADABLE, never "no union":
            # both write `union is None`, and conflating them would disable the
            # hand-picked-subset arm behind one transient 502.
            resolved, probe.union_raw = _try(gh, lambda g: _resolve_union(entries, g))
            probe.union = resolved
            probe.union_unreadable = resolved is None
        else:
            probe.union_raw = _union_raw
    else:
        probe.union_raw = "no PR body"
    if probe.touched:
        probe.uncovered, probe.coverage_raw = _read_coverage(probe.touched)
    else:
        probe.coverage_raw = probe.touched_raw
    probe.comments, probe.comments_raw = _try(gh, lambda g: _read_comments(g, pr))
    return probe


def _try(gh: Gh, fn, surface: str = "") -> tuple[Any, str]:
    try:
        return fn(gh)
    except RateLimited:
        raise
    except ReadError as exc:
        return None, f"{surface + ': ' if surface else ''}{exc}"
    except ci_verdict.SurfaceReadError as exc:
        return None, f"{surface + ': ' if surface else ''}{exc}"


def _try_tree(gh: Gh, pr: int, head: str, workdir: Path) -> tuple[bool | None, str]:
    try:
        return _read_tree(gh, pr, head, workdir)
    except ReadError as exc:
        return None, str(exc)


def _try_review(
    pr: int,
    repo: str,
    workdir: Path,
    recorded_diff: str | None = None,
    recorded_head: str | None = None,
) -> tuple[str | None, int | None, str]:
    try:
        return _read_review(pr, repo, workdir, recorded_diff, recorded_head)
    except ReadError as exc:
        return None, None, str(exc)


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ready_for_owner.py",
        description=(
            "Answer READY / NOT-READY for the owner to merge a PR from the GitHub UI, "
            "with the raw value every condition read. Exit 0 only when READY."
        ),
    )
    parser.add_argument(
        "pr",
        type=int,
        nargs="?",
        default=None,
        help="pull request number (omit only with --fixture)",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name (default {DEFAULT_REPO})")
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    parser.add_argument(
        "--workdir",
        default=str(REPO_ROOT),
        help="repo root the git/merge-tree and record-review reads run in",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="evaluate a Probe JSON fixture instead of reading the live world (tests)",
    )
    parser.add_argument(
        "--recorded-diff",
        default=None,
        help=(
            "the 64-hex diff_sha256 a review record carries, when the record FILE is not on "
            "this box (a review minted elsewhere). Passed straight through to "
            "`record-review.sh --check-carry` — this tool does not parse the signed marker "
            "itself, because a second marker parser is a second definition."
        ),
    )
    parser.add_argument(
        "--recorded-head",
        default=None,
        help="the 40-hex head that record was minted at (only with --recorded-diff)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fixture is None and args.pr is None:
        parser.error("a pull request number is required unless --fixture is used")
    workdir = Path(args.workdir).resolve()
    try:
        if args.fixture:
            probe = probe_from_dict(json.loads(Path(args.fixture).read_text()))
        else:
            probe = collect_probe(
                args.pr,
                args.repo,
                workdir,
                recorded_diff=args.recorded_diff,
                recorded_head=args.recorded_head,
            )
    except RateLimited as exc:
        # ⛔ The contract is "exactly one of READY-FOR-OWNER / NOT-READY", and a rate limit
        # used to print an EMPTY stdout — a third shape a caller grepping the line could not
        # parse. Every condition is unread, so every condition failed.
        print(f"GH-RATE-LIMITED({exc.endpoint})", file=sys.stderr)
        pr = args.pr if args.pr is not None else 0
        print(f"NOT-READY({pr}, failed={','.join(CONDITIONS)})")
        return 1
    verdict = evaluate(probe)
    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
    else:
        print(render(verdict))
    return 0 if verdict.ready else 1


def probe_from_dict(data: Mapping[str, Any]) -> Probe:
    """Build a Probe from a fixture dict (used by tests and ``--fixture``)."""
    groups = data.get("groups")
    parsed_groups: tuple[ci_verdict.GroupVerdict, ...] | None = None
    if groups is not None:
        parsed_groups = tuple(
            ci_verdict.GroupVerdict(
                app=g.get("app", "github-actions"),
                workflow=g.get("workflow"),
                workflow_key=g.get("workflow_key"),
                job=g["job"],
                state=g["state"],
                check_id=g.get("check_id"),
                status=g["status"],
                conclusion=g.get("conclusion"),
                details_url=g.get("details_url", ""),
                started_at=g.get("started_at"),
                voided_red=bool(g.get("voided_red", False)),
            )
            for g in groups
        )
    now = _parse_instant(str(data.get("now") or "")) or datetime.now(UTC)
    return Probe(
        pr=int(data["pr"]),
        repo=str(data.get("repo", DEFAULT_REPO)),
        head=str(data.get("head", "")),
        base=str(data.get("base", "main")),
        is_draft=bool(data.get("is_draft", False)),
        mergeable=str(data.get("mergeable", "UNKNOWN")),
        merge_state=str(data.get("merge_state", "")),
        head_committed_at=str(data.get("head_committed_at", "")),
        body=str(data.get("body", "")),
        # A fixture that omits `labels` means "no labels"; an explicit null means the
        # READ was unreadable. The live collector is the authority on which happened.
        labels=(
            None if "labels" in data and data["labels"] is None else tuple(data.get("labels") or ())
        ),
        labels_raw=str(data.get("labels_raw", "")),
        pr_state=str(data.get("pr_state", "open")),
        merged=bool(data.get("merged", False)),
        tree=data.get("tree"),
        tree_raw=str(data.get("tree_raw", "")),
        groups=parsed_groups,
        groups_raw=str(data.get("groups_raw", "")),
        required=None if data.get("required") is None else tuple(data["required"]),
        required_raw=str(data.get("required_raw", "")),
        review_token=data.get("review_token"),
        review_exit=data.get("review_exit"),
        review_raw=str(data.get("review_raw", "")),
        touched=None if data.get("touched") is None else tuple(data["touched"]),
        touched_raw=str(data.get("touched_raw", "")),
        union=None if data.get("union") is None else tuple(data["union"]),
        union_raw=str(data.get("union_raw", "")),
        union_unreadable=bool(data.get("union_unreadable", False)),
        uncovered=None if data.get("uncovered") is None else tuple(data["uncovered"]),
        coverage_raw=str(data.get("coverage_raw", "")),
        comments=None if data.get("comments") is None else tuple(data["comments"]),
        comments_raw=str(data.get("comments_raw", "")),
        now=now,
    )


if __name__ == "__main__":
    raise SystemExit(main())
