"""Standing guard: a REPORTING workflow must not starve PR CI of runner slots (#9115).

On 2026-10-19 a reporting workflow ate the repo's whole concurrency budget and
the REQUIRED check never started::

    queued runs: 44-49        in_progress: 7
    top of queue: finding-provenance — a new run every ~30 s, 12+ at once
    12 PR heads with ZERO check-runs: #9043 #9069 #9070 … #9112

`python-ci-gate` is the required aggregate, so a head with no runner slot never
gets a check-run at all — and every PR then reads ``WAITING-CI`` forever, which
is indistinguishable from "slow CI" to every instrument the fleet had. The rails
reported ``queued`` (true, and useless), the heartbeat reported a long CI
duration, and hours were spent waiting for a queue that could not move.

Two workflow-shape properties make that class of outage possible, and both are
cheap to pin:

1. **A workflow with no ``concurrency:`` block is unbounded.** Every run of it
   that an event creates is eligible for a runner at once. Seven workflows in
   this repo had none before this guard — all dispatch/cron/publish, none of
   which can flood, which is why they were never the incident.

2. **A per-unit ``concurrency:`` key bounds ONE unit, never the fan-out.**
   ``finding-provenance`` — the workflow that ACTUALLY flooded — already had
   ``group: … comment.id … `` with ``cancel-in-progress: true``, and it still
   produced 12+ concurrent runs: every comment is a different unit, so every
   comment got its own group. A per-unit key deduplicates repeats of one unit
   and says nothing about N units, so "has a ``concurrency:`` block" is
   NECESSARY BUT NOT SUFFICIENT, and a guard that stops there would pass the
   #9115 workflow itself (it did, before this property was added). The key is
   nevertheless REQUIRED where it appears, because collapsing it to a constant
   would cancel pending verdicts — a cancelled gate posts nothing (a fail-open)
   — so the fan-out has to be bounded OUTSIDE a per-unit key: the group itself
   must be low-cardinality, or a job-level ``if:`` must spend no runner, or the
   check must be one run per head. :data:`PER_UNIT_GROUP_OK` names the bound.
   A workflow whose across-unit fan-out is deliberately UNBOUNDED is not that
   shape and does not belong in that list: it lives in
   :data:`PER_ISSUE_GROUP_OK` under a contract that says so (#9577 review).
   ⛔ A cheap ``classify`` leg is a **COST** bound, never a fan-out bound:
   #9115's workflow had one in place *during* the #9577 flood (38 of 133 queued
   runs, 0 of 28 PRs mergeable), so it cannot be the reason a per-unit key is
   safe.

3. **A per-push REPORTING workflow with no path filter fires on every merge.**
   Under a merge flood it starts, is cancelled by the next merge
   (``cancel-in-progress: true``), and starts again: ``welcome-e2e-monitor``
   spent 13 runner-minutes in one 3-hour window producing almost no completed
   reading. A report has no business being triggered by a change it cannot
   observe.

What this module pins
---------------------
* every workflow declares ``concurrency:`` **with an explicit
  ``cancel-in-progress:``** — a silent default of ``false`` is how a per-event
  workflow grows an unbounded queue;
* a per-unit group on a per-event workflow must declare what bounds its
  FAN-OUT (:data:`PER_UNIT_GROUP_OK`) — the #9115 shape, which a
  block-exists rule cannot see — and a per-ISSUE key whose across-issue
  fan-out is deliberately unbounded must be in :data:`PER_ISSUE_GROUP_OK`,
  a DIFFERENT contract whose two halves are both asserted, so the excuse cannot
  be copied onto a key it is not true of: the unit STRUCTURALLY is the issue
  (:func:`_per_issue_key_problem` — one expression, `github.event.issue.number`
  first, `github.run_id` last, no per-event-unique term), and the residue names
  the PINNED tracking home (:data:`PER_ISSUE_RESIDUE_TRACKER`), which may not be
  a CLOSED issue;
* every workflow with a ``push`` trigger is either path-scoped or listed in
  :data:`UNFILTERED_PUSH_OK` with its reason (a merged-tree validation
  genuinely has no path filter to take), and an excused entry must actually
  BE unfiltered — the excuse and the artifact have to agree, or the entry
  silently licenses deleting a ``paths:`` filter that exists today;
* ``finding-provenance`` — the workflow that actually flooded — keys its
  group **per issue**, and #9577 pins three properties of that key: it carries
  EXACTLY ONE expression (an appended `${{ github.run_id }}` would make it
  unique per run), it never carries a ``comment.id`` (a per-comment key is
  unique-per-event, so ``cancel-in-progress`` could never fire), and its term
  list is the exact ordered one in :data:`_FINDING_PROVENANCE_GROUP_TERMS`
  (``||`` returns the first truthy operand, so an always-truthy term in front of
  the issue re-creates the same bug under another name); separately, an
  ``issue_comment`` gate must never carry a ``paths:`` filter (it would never
  run);
* the REQUIRED aggregate keeps its load-bearing identity: ``python-ci.yml``
  still defines a job with the id ``python-ci-gate`` and no ``name:`` override
  (renaming it silently un-requires the gate — branch protection and Mergify's
  ``check-success=python-ci-gate`` name it);
* no job reuses its workflow's run-level ``concurrency.group``, and
  ``supabase-deploy``'s two locks are pinned exactly (a CONSTANT apply lock,
  an event-scoped run lock, both ``cancel-in-progress: false``) — equal names
  deadlock the job with ZERO steps, and a constant run group lets a starved
  ``push`` run hold the RUN lock a dispatch needs, so its apply cannot start
  (measured 2026-10-20, #9120 x #1095);
* the MERGED-TREE GUARD (``main-lint.yml``, #2174) triggers on every push to
  ``main`` **and** keys its concurrency group on the pushed tree, so nothing can
  cancel it — a cheap guard sharing a constant group with a long job does not run
  on a busy main (measured: 21 of 25 merged trees skipped, 2026-10-22) — and it
  may not be re-added to ``post-merge-validation.yml``; a companion assertion
  keeps its ruff pin equal to ``ci.yml``'s PR-lint pin and pyproject's;
* the guard is NON-VACUOUS: it asserts it parsed a plausible number of
  workflows and that each workflow it makes a claim about actually exists, so a
  renamed file or a glob typo fails here instead of passing silently.

Sibling guards in the same house style: ``test_workflow_gh_token.py`` (#3092)
and ``test_workflow_secret_interpolation.py``.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shlex
import subprocess
import tempfile
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _workflow_paths() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"})


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), f"{path.name}: not a YAML mapping"
    return doc


def _triggers(doc: dict) -> dict:
    """The ``on:`` block normalised to a mapping.

    PyYAML resolves the bare key ``on`` to the BOOLEAN ``True`` (YAML 1.1), so
    ``doc["on"]`` is the wrong lookup in half the files; both spellings are
    accepted here, and a bare-string / bare-list trigger is normalised to a
    mapping too.
    """
    raw = doc.get("on", doc.get(True))
    assert raw is not None, "no `on:` trigger block"
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(t): None for t in raw}
    return {str(k): v for k, v in raw.items()}


# Workflows that legitimately keep an UNFILTERED `push` trigger. Each reason is
# a property of the artifact, not a convenience: the workflow either must see
# every merged tree (it validates/aggregates ONE tree) or its push arm keys on
# a TAG, where GitHub does not evaluate `paths` at all. Anything else that fires
# per push must be path-scoped — that is the rule `welcome-e2e-monitor` follows.
#
# ⛔ An entry here is UNCONDITIONAL: it is consulted before the `paths:`
# assertion, so it also licenses the REMOVAL of a `paths:` filter that the
# workflow carries today. That is why only genuinely-unfiltered workflows may
# be listed — `deploy-hosted`, `deploy-pages` and `redis-guard` were removed
# from an earlier draft of this list for exactly that reason (all three ARE
# path-scoped, so they already satisfy the rule and their entries could only
# have weakened it). `test_the_allow_list_has_no_dead_entries` now asserts the
# converse too: an excused workflow must have NO `paths`/`paths-ignore` key.
UNFILTERED_PUSH_OK = {
    "python-ci.yml": "the push-to-main leg IS the merged-tree test run the merge rail baselines against",
    "post-merge-validation.yml": "validates every merged tree (warn-only) — the whole point is one run per push",
    "main-lint.yml": (
        "ruff reads the WHOLE tree, so any merged tree can redden main — a path "
        "filter would skip exactly the trees that need it. Cheap enough (~2 min, "
        "no package install) to run on every push, and its per-SHA group means no "
        "push is starved (see MERGED_TREE_GUARD)"
    ),
    "publish-pypi.yml": "the push arm is a TAG (`v*`), and GitHub does not evaluate `paths` for tag pushes",
}

# The guard that keeps a lint-red MAIN from reddening every PR's merge ref
# (#2174). It must run on EVERY merged tree, and the artifact that proves it does
# is the one pinned below.
#
# WHY A SEPARATE PIN, when `UNFILTERED_PUSH_OK` above already excuses the file
# from the path-filter rule: "triggers on every push" is only half of "runs on
# every merged tree". The other half is that nothing CANCELS the run — and
# GitHub keeps ONE pending run per `concurrency.group`, so a run whose group is
# shared with a long job is cancelled when the next push queues one. That is the
# measured defect this pin exists for: as a job inside post-merge-validation.yml
# (constant group, 30-60 min `validate` sibling) the ruff gate ran on 4 of the
# last 25 merged trees and was ABSENT on the other 21 — including `3337ad1f`, the
# merge that reddened main for 11 hours and left **14 of 27** open PRs red on
# `agent-infra-ci / lint` for findings that were main's, not theirs.
MERGED_TREE_GUARD = {
    "main-lint.yml": (
        "the ruff gate on main (#2174): a lint-red main reddens the merge ref of "
        "EVERY open PR, so a run that did not happen here is a gate that did not "
        "exist — and a narrowed rule surface is the same failure one level down"
    ),
}

# Per-event workflows that are on the pull-request path but cost NOTHING on it,
# because the JOB is skipped before a runner is allocated. `if:` at job level is
# evaluated before allocation, so a false condition spends no slot — which is
# why `inbound-relay` can take `pull_request_target` events without competing
# with `python-ci-gate`. Each entry must keep the exclusion that makes it true
# (pinned below); drop the `if:` and the workflow becomes a slot consumer.
ZERO_COST_PR_EVENT = {
    "inbound-relay.yml": (
        "job-level `if:` excludes the owner and the merge queue's own batch "
        "PRs (queue-owned head branch, or a bot-authored same-repo PR on the "
        "default branch) — all internal traffic — so the job is skipped "
        "before a runner exists"
    ),
}

# Per-event workflows whose `concurrency.group` is keyed PER UNIT — deliberately,
# and with the reason its FAN-OUT is bounded SOMEWHERE ELSE.
#
# A per-unit key gives every unit its own group, so it bounds repeats of ONE unit
# and nothing in aggregate: N units still run at once. #9115 is the proof — the
# offending `finding-provenance` ALREADY had `group: … comment.id …` with
# `cancel-in-progress: true` and still produced 12+ concurrent runs, because
# every comment is a different unit. So the per-unit shape is only safe when
# something else bounds the fan-out, and each entry here must name that bound.
#
# ⛔ The bound must be SOMEWHERE ELSE. "the key is per unit, so it bounds each
# unit" is not a bound, it is the definition of the shape — and it is exactly
# the reasoning that let #9115's flood pass this module. A workflow whose fan-out
# is unbounded ACROSS units by design does not belong here at all; it belongs in
# :data:`PER_ISSUE_GROUP_OK`, whose contract states the residue out loud.
#
# ⛔ A new per-event workflow that copies the shape WITHOUT a bound fails
# `test_per_event_per_unit_groups_declare_their_bound` — the rule the
# block-exists test structurally cannot express.
PER_UNIT_GROUP_OK = {
    "ai-review-gate.yml": (
        "a REQUIRED check: one run per PR head, superseded on each push. A "
        "constant group would let one PR's review cancel another's and leave "
        "the required check pending forever."
    ),
    "inbound-relay.yml": (
        "the JOB is skipped before a runner is allocated for ALL internal "
        "traffic (its `if:` excludes the owner and the queue's own batch PRs), "
        "so the only fan-out is external `opened` events — one per unit."
    ),
}

# Per-event workflows keyed PER ISSUE whose across-issue fan-out is deliberately
# UNBOUNDED and accepted, with the residue tracked.
#
# This is a DIFFERENT contract from :data:`PER_UNIT_GROUP_OK`, and keeping them
# apart is the point (#9577 review): that list means "something else bounds the
# fan-out", which a per-issue key does NOT do — N issues in flight still permit
# N concurrent runs. Merged into one dict, a future per-unit workflow could quote
# a per-issue reason verbatim to excuse a key that bounds nothing anywhere, and
# the guard's only remaining check ("the entry carries prose") cannot tell.
#
# So an entry here is a recorded LOSS, and both halves of it are asserted by
# `test_the_per_issue_allow_list_keys_on_the_issue_and_names_its_residue`:
#   * the unit IS the issue — STRUCTURALLY, not by substring. A bare
#     `"issue.number" in group` admits
#     `…-${{ github.event.issue.number }}-${{ github.run_id }}`, which is unique
#     per RUN: `cancel-in-progress` can never fire, i.e. the #9577 defect under
#     another token with the guard green (#9577 review).
#     :func:`_per_issue_key_problem` therefore requires EXACTLY ONE expression,
#     `github.event.issue.number` as the FIRST term, `github.run_id` as the LAST
#     (the always-truthy fallback for a trigger with no `event.issue`), and
#     nothing in between but the `inputs.issue_number` synonym — so a comment
#     burst on one issue collapses to one run rather than one per comment
#     (#9577: 100 comments an hour, 38 of 133 queued runs, 0 of 28 PRs
#     mergeable);
#   * the reason names the TRACKING HOME for the unbounded residue, and it must
#     be the number PINNED in :data:`PER_ISSUE_RESIDUE_TRACKER` — an OPEN issue.
#     A `#\d+` regex cannot tell a live tracker from a CLOSED one, and this
#     artifact shipped citing `#9115`, closed as COMPLETED two days before the
#     citation was written. Pinning the number makes re-citing a reviewed edit
#     here instead of a prose tweak.
PER_ISSUE_GROUP_OK = {
    "finding-provenance.yml": (
        "the key bounds ONE issue's stream: every comment on one issue shares a "
        "group, so a burst supersedes that issue's own in-flight run instead of "
        "adding a runner. ACROSS issues there is NO bound — N issues in flight "
        "permit N concurrent runs — and that residue is the accepted trade, "
        "tracked as #9595 (batching / coalescing with a catch-up sweep). What a "
        "cancel loses, stated: the superseded run posts no verdict and there is "
        "no `comment_id` dispatch input to recover that comment; the lane "
        "re-posts (its own new group member), and the issue BODY gate is "
        "re-runnable with `workflow_dispatch -f issue_number=` (#9577). NOTE the "
        "CLASSIFIER SPLIT is a COST bound, never a fan-out bound: it was already "
        "in place during the #9577 flood (38 of 133 queued runs, 0 of 28 PRs "
        "mergeable)."
    ),
}

# The tracking home for each PER_ISSUE_GROUP_OK entry's unbounded residue —
# PINNED here rather than read out of the prose (#9577 review).
#
# ⛔ What the guard can and cannot check: it runs OFFLINE (no `gh`, no token), so
# it cannot open the issue and ask whether it is still open. What it can do — and
# does — is (a) require the exact pinned number, so moving the tracker is a
# reviewed edit HERE rather than a prose tweak, and (b) refuse a number known to
# be CLOSED. Both artifacts in the original #9588 revision cited `#9115`, closed
# as COMPLETED two days before that head while its own last comment recorded the
# flood recurring: a reference that reads like a tracker and tracks nothing.
# Liveness of the number below is re-verified AT REVIEW TIME — the assertions do
# not claim otherwise.
PER_ISSUE_RESIDUE_TRACKER = {
    "finding-provenance.yml": 9595,
}

# Tracking homes that are CLOSED. A closed issue is not the home of an ACCEPTED
# residue: either the work landed (the allowance is stale and must go) or it did
# not (the residue has no open home and must be filed).
_CLOSED_TRACKERS = {
    9115: (
        "closed 2026-10-19 as COMPLETED, while its own last comment recorded the "
        "flood recurring"
    ),
}

# A tracking reference: `#9595`, `#9577`. Used by the per-issue list, which
# records an ACCEPTED LOSS and must therefore name where the residue is handled.
_TRACKING_REF = re.compile(r"#\d+")

# The one term a PER-ISSUE key must START with, the synonym it may carry, and the
# always-truthy fallback it must END with — see :func:`_per_issue_key_problem`.
_PER_ISSUE_TERM = "github.event.issue.number"
_PER_ISSUE_TERM_SYNONYMS = {"github.event.inputs.issue_number", "inputs.issue_number"}
_PER_ISSUE_FALLBACK = "github.run_id"


def _per_issue_key_problem(group: str) -> str | None:
    """Why ``group`` is NOT a per-issue concurrency key, or ``None`` when it is.

    The premise :data:`PER_ISSUE_GROUP_OK` excuses a key on is *the unit IS the
    issue*, and a SUBSTRING test cannot check it: the group
    ``…-${{ github.event.issue.number }}-${{ github.run_id }}`` contains
    ``issue.number``, carries no ``comment.id``, and is unique per RUN — so
    ``cancel-in-progress`` can never fire, which is the #9577 defect under
    another token with the guard green (#9577 review). The premise is therefore
    checked STRUCTURALLY:

    * exactly ONE ``${{ … }}`` expression — an appended second expression is how
      a run-unique suffix gets in while the first one still reads per-issue (the
      same count pin ``test_finding_provenance_concurrency_group_never_keys_on_a_comment``
      applies to the artifact);
    * the FIRST term is ``github.event.issue.number`` — ``||`` returns the first
      TRUTHY operand, so an always-truthy term ahead of it mints a per-run group
      (``github.run_id``) or ONE group for the whole fleet (``github.event_name``);
    * the LAST term is ``github.run_id`` — the fallback a ``workflow_dispatch``
      trigger needs, and one that cannot collapse different units together;
      without it the group can resolve EMPTY, one shared group for every unit;
    * nothing else but the ``inputs.issue_number`` synonym — which keeps out
      ``comment.id`` (unique per EVENT), ``event_name`` (global), and
      ``ref``/``sha`` (per RUN).

    Returns the reason instead of asserting so
    ``test_the_per_issue_key_check_rejects_excused_shapes`` can prove the check
    rejects each shape on SYNTHETIC groups: a guard whose only evidence is the
    one artifact it happens to pass on cannot show its teeth.
    """
    expressions = _EXPRESSION.findall(group)
    if len(expressions) != 1:
        return (
            f"it carries {len(expressions)} `${{{{ … }}}}` expressions; a per-issue "
            "key must carry EXACTLY ONE — an appended `${{ github.run_id }}` suffix "
            "makes the group unique per RUN while the first expression still reads "
            "per-issue"
        )
    terms = [t.strip() for t in expressions[0].split("||")]
    if terms[0] != _PER_ISSUE_TERM:
        return (
            f"its first term is {terms[0]!r}; `||` returns the FIRST truthy operand, "
            f"so a per-issue key must START with {_PER_ISSUE_TERM!r} — an "
            "always-truthy term ahead of it (`github.run_id`) mints one group per "
            "RUN, and `github.event_name` collapses every event into ONE group"
        )
    if terms[-1] != _PER_ISSUE_FALLBACK:
        return (
            f"its last term is {terms[-1]!r}; a per-issue key must END with "
            f"{_PER_ISSUE_FALLBACK!r} — the always-truthy fallback for a trigger "
            "with no `event.issue` (`workflow_dispatch`). Without it the group can "
            "resolve EMPTY, and one shared empty group lets one unit cancel another"
        )
    allowed = {_PER_ISSUE_TERM, _PER_ISSUE_FALLBACK, *_PER_ISSUE_TERM_SYNONYMS}
    extra = [t for t in terms if t not in allowed]
    if extra:
        return (
            f"it also resolves through {extra}, which is not a per-issue term. "
            "`comment.id` is unique per EVENT (the #9577 defect: every comment in "
            "its own group, `cancel-in-progress` cancels nothing); `event_name` is "
            "ONE group for the whole fleet; `ref`/`sha` are per RUN. A per-issue "
            "key is the issue, the dispatch synonym, and the run_id fallback"
        )
    return None

# Per-event workflows that spend runner slots on REPORTING/BOOKKEEPING rather
# than gating a merge. They must declare a `concurrency:` group — a flood in any
# one of them starves the REQUIRED `python-ci-gate` exactly as #9115 measured.
# Membership is explicit so that adding a new per-event workflow is a REVIEWED
# decision rather than an omission.
REPORTING_PER_EVENT = {
    "finding-provenance.yml": "the #9115 flood: one run per finding comment, ~50 lanes",
    "inbound-relay.yml": "one run per external issue/PR `opened`",
    "availability-liveness.yml": "cron/uptime bookkeeping",
    "availability-record.yml": "cron/uptime bookkeeping",
    "availability-watchdog.yml": "cron/uptime watchdog",
    "billing-reconcile-cron.yml": "cron reconciliation",
    "ci-timing.yml": "cron CI-timing ledger",
    "ci-check-repair.yml": "cron CI repair sweep",
    "e2e-live-reconcile.yml": "cron live-e2e ledger",
    "main-health-nightly.yml": "nightly report",
    "mergify-guard-recut.yml": "cron guard re-cut",
    "prod-csp-monitor.yml": "deployed-surface report",
    "registry-backup-cron.yml": "cron backup",
    "registry-drill-cron.yml": "cron drill",
    "skills-parity.yml": "parity report",
    "skip-bypass-expiry.yml": "expiry bookkeeping",
    "tenant-provision-monitor.yml": "cron tenant monitor",
    "welcome-e2e-monitor.yml": "live signup-funnel report",
}


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_every_workflow_declares_a_concurrency_block(path: Path) -> None:
    """A workflow with no ``concurrency:`` is unbounded, and that is the #9115 shape.

    NOTE the narrow claim: this asserts a block EXISTS and is explicit. It does
    NOT assert the workflow is bounded — a per-unit key passes every assertion
    here and still fans out without limit (#9115's own workflow did). That
    second property is `test_per_event_per_unit_groups_declare_their_bound`.
    """
    doc = _load(path)
    conc = doc.get("concurrency")
    assert conc is not None, (
        f"{path.name} declares no `concurrency:` block — an unbounded per-event "
        "workflow can occupy the whole runner budget and starve the REQUIRED "
        "`python-ci-gate` (#9115). Add a group; use `cancel-in-progress: false` "
        "only when a cancel would half-apply a mutating action (publish/deploy/"
        "relay), and say why."
    )
    if isinstance(conc, str):  # `concurrency: some-group` shorthand
        pytest.fail(
            f"{path.name} uses the string shorthand `concurrency: {conc}` — it "
            "inherits the `cancel-in-progress: false` default silently. Declare "
            "the key explicitly."
        )
    assert isinstance(conc, dict), f"{path.name}: `concurrency:` must be a mapping"
    assert str(conc.get("group", "")).strip(), f"{path.name}: `concurrency.group` is empty"
    assert "cancel-in-progress" in conc, (
        f"{path.name}: `concurrency:` has no explicit `cancel-in-progress` — the "
        "default is `false`, so superseded runs QUEUE instead of being replaced. "
        "That is the unbounded-queue shape #9115 measured."
    )
    assert isinstance(conc["cancel-in-progress"], (bool, str)), (
        f"{path.name}: `cancel-in-progress` must be a boolean or an expression"
    )


_PER_UNIT_TOKENS = ("comment.id", "issue.number", "pull_request.number", "run_id")
_PER_EVENT_TRIGGERS = {"issues", "issue_comment", "pull_request", "pull_request_target"}

# The ONE expression `finding-provenance`'s concurrency group may resolve through
# (#9577). Pinned as an exact term list because every property that makes the key
# a BOUND lives in the ORDER and in the ABSENCE of terms, not in their presence:
# `||` returns the first truthy operand, so an always-truthy term placed AHEAD of
# the issue (`github.run_id` gives one group per run; `github.event_name` gives
# ONE group for every event in the fleet — the global group the workflow forbids)
# breaks the bound, and a per-event-unique term (`github.event.comment.id`) makes
# the key unique per event wherever it is the resolving term and is dead code
# elsewhere — so it must not be in the KEY at all. A TRAILING always-truthy term
# is required, not a hazard: it is the dispatch fallback for a trigger that has
# no `github.event.issue.number`.
_FINDING_PROVENANCE_GROUP_TERMS = [
    "github.event.issue.number",
    "github.event.inputs.issue_number",
    "github.run_id",
]


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_per_event_per_unit_groups_declare_their_bound(path: Path) -> None:
    """A per-unit key bounds ONE unit's repeats, never the fan-out (#9115).

    This is the property the block-exists rule structurally cannot express. The
    workflow that actually flooded in #9115 already had
    ``group: … comment.id …`` with ``cancel-in-progress: true``, so it passed
    that rule — and it still ran 12+ at once. A per-event workflow may keep a
    per-unit key only when something ELSE bounds its fan-out, and
    :data:`PER_UNIT_GROUP_OK` must say what.

    A workflow whose across-unit fan-out is DELIBERATELY unbounded (the unit is
    the issue and the residue is accepted) is excused by the other list instead,
    :data:`PER_ISSUE_GROUP_OK`, whose two premises are asserted by
    `test_the_per_issue_allow_list_keys_on_the_issue_and_names_its_residue`.
    """
    doc = _load(path)
    triggers = _PER_EVENT_TRIGGERS & set(_triggers(doc))
    if not triggers:
        return
    conc = doc.get("concurrency") or {}
    group = str(conc.get("group", ""))
    per_unit = [tok for tok in _PER_UNIT_TOKENS if tok in group]
    if not per_unit:
        return  # a constant / low-cardinality group bounds the fan-out itself
    # Two allow-lists, two contracts. The lookup is explicit (`or`, not a merged
    # dict) so the failure message names the list the entry must satisfy; the
    # lists are asserted disjoint in
    # `test_the_per_issue_allow_list_keys_on_the_issue_and_names_its_residue`, so
    # a name in both cannot silently resolve to whichever is checked first.
    reason = PER_UNIT_GROUP_OK.get(path.name) or PER_ISSUE_GROUP_OK.get(path.name)
    assert reason is not None, (
        f"{path.name} takes per-event triggers ({sorted(triggers)}) and keys its "
        f"concurrency group PER UNIT ({per_unit}) — that bounds repeats of one "
        "unit but NOT the fan-out: N units still run at once. That is the #9115 "
        "flood shape exactly (the offending workflow already had a per-unit key). "
        "Bound the fan-out (a constant/low-cardinality group, a required check "
        "that is one run per head, or a job-level `if:` that spends no runner), "
        "or add it to PER_UNIT_GROUP_OK with that bound named — or, when the "
        "across-unit fan-out is acknowledged as unbounded and the loss is "
        "accepted, to PER_ISSUE_GROUP_OK, which asserts that the unit really is "
        "the issue and that the residue names its tracking home."
    )
    assert reason.strip(), (
        f"{path.name} needs a reason — an allow-list entry is a reviewed "
        "decision, not an omission with a comment-shaped hole next to it"
    )


def test_the_per_issue_allow_list_keys_on_the_issue_and_names_its_residue() -> None:
    """:data:`PER_ISSUE_GROUP_OK` is a CONTRACT, not prose (#9577 review).

    The entry that lives here is excused from "bound the FAN-OUT" on a promise:
    *the unit is the issue*, and the unbounded across-issue residue is *tracked*.
    Both halves are checked — otherwise a FUTURE per-unit workflow can copy this
    wording verbatim and pass a rule whose stated premise it denies, which is the
    shape that let #9115's flood pass this module.

    (1) The key is checked STRUCTURALLY through :func:`_per_issue_key_problem`,
    not by substring: `…-${{ github.event.issue.number }}-${{ github.run_id }}`
    contains `issue.number`, has no `comment.id`, and is unique per RUN.

    (2) The tracking home is PINNED in :data:`PER_ISSUE_RESIDUE_TRACKER` and may
    not be a CLOSED issue (:data:`_CLOSED_TRACKERS`). Liveness itself is NOT
    checkable here — this suite runs offline — so the docstring does not claim
    it; what the pin buys is that re-citing the residue is a reviewed edit rather
    than a prose tweak. That distinction is the one that failed once: the entry
    cited #9115 by name, and #9115 had been closed as COMPLETED two days before
    that citation, so the deferred work had no open home while the entry read as
    if it did.
    """
    assert set(PER_ISSUE_RESIDUE_TRACKER) == set(PER_ISSUE_GROUP_OK), (
        "PER_ISSUE_RESIDUE_TRACKER and PER_ISSUE_GROUP_OK must name the same "
        "workflows — an accepted residue with no pinned tracker slips through, "
        "and a tracker with no entry is a stale pin. tracker-only: "
        f"{sorted(set(PER_ISSUE_RESIDUE_TRACKER) - set(PER_ISSUE_GROUP_OK))}, "
        f"entry-only: {sorted(set(PER_ISSUE_GROUP_OK) - set(PER_ISSUE_RESIDUE_TRACKER))}"
    )
    overlap = sorted(set(PER_UNIT_GROUP_OK) & set(PER_ISSUE_GROUP_OK))
    assert not overlap, (
        f"{overlap} appear in BOTH allow-lists. The guard resolves one of them, "
        "so the other becomes documentary — and the two say different things "
        "(fan-out bounded SOMEWHERE ELSE vs across-unit fan-out unbounded and "
        "accepted). Keep the one that is true."
    )
    # Non-vacuity: an empty per-issue list is a bucket that no longer judges
    # anything, and every assertion below would pass while checking nothing.
    assert PER_ISSUE_GROUP_OK, "PER_ISSUE_GROUP_OK is empty — drop it or populate it"
    for name, reason in PER_ISSUE_GROUP_OK.items():
        path = WORKFLOW_DIR / name
        assert path.exists(), f"PER_ISSUE_GROUP_OK names a missing workflow: {name}"
        group = str((_load(path).get("concurrency") or {}).get("group", ""))
        problem = _per_issue_key_problem(group)
        assert problem is None, (
            f"{name} is excused as PER-ISSUE but its group is {group!r}, and "
            f"{problem}. The excuse's whole premise is that the UNIT IS THE "
            "ISSUE — keyed on any other unit this is the per-event shape #9115 "
            "measured, not an accepted residue. Key on "
            "`github.event.issue.number` (dispatch synonym, `github.run_id` "
            "fallback last), or move the entry to PER_UNIT_GROUP_OK and name the "
            "bound its fan-out actually has."
        )
        # Kept as its own assertion for the message: the structural check above
        # already excludes it, but the #9577 defect deserves to be named.
        assert "comment.id" not in group, (
            f"{name} is excused as PER-ISSUE and its group {group!r} still carries "
            "`comment.id` — a per-comment key is the #9577 defect itself, and "
            "no allow-list entry may excuse it"
        )
        tracker = PER_ISSUE_RESIDUE_TRACKER[name]
        assert f"#{tracker}" in reason, (
            f"PER_ISSUE_GROUP_OK[{name!r}] does not name its pinned tracking home "
            f"#{tracker} for its unbounded across-issue residue. This list records "
            "an ACCEPTED LOSS; an untracked residue is narration, and a future "
            "entry could copy it without the loss landing anywhere."
        )
        assert tracker not in _CLOSED_TRACKERS, (
            f"PER_ISSUE_RESIDUE_TRACKER[{name!r}] = #{tracker}, which is CLOSED "
            f"({_CLOSED_TRACKERS.get(tracker)}). A closed issue is not the home of "
            "an accepted residue: either the work landed (then this allowance is "
            "stale — drop the entry) or it did not (then file the residue as an "
            "OPEN issue and cite that number)."
        )


@pytest.mark.parametrize(
    "group",
    [
        # The exact shape the old substring check excused (#9577 review): it
        # contains `issue.number`, carries no `comment.id`, and is unique per RUN,
        # so `cancel-in-progress` can never fire.
        "finding-provenance-${{ github.event.issue.number }}-${{ github.run_id }}",
        "finding-provenance-${{github.event.issue.number}}${{github.run_id}}",
        # An always-truthy term FIRST — `run_id`: one group per RUN.
        "finding-provenance-${{ github.run_id || github.event.issue.number }}",
        # `event_name` in front: ONE group for the whole fleet on a dispatch.
        "finding-provenance-${{ github.event_name || github.event.issue.number }}",
        # `comment.id`: per-EVENT wherever it resolves, dead code elsewhere.
        "finding-provenance-${{ github.event.comment.id || github.event.issue.number || github.run_id }}",
        "finding-provenance-${{ github.event.issue.number || github.event.comment.id || github.run_id }}",
        # No trailing always-truthy fallback: can resolve EMPTY on a dispatch.
        "finding-provenance-${{ github.event.issue.number || github.event.inputs.issue_number }}",
        # A constant group: not per-issue at all, and it lets one issue cancel
        # another's gate.
        "finding-provenance",
    ],
)
def test_the_per_issue_key_check_rejects_excused_shapes(group: str) -> None:
    """The per-issue premise is STRUCTURAL — these mutants prove it (#9577 review).

    The first group is the one the original guard admitted: it asserted
    ``"issue.number" in group``, which
    ``…-${{ github.event.issue.number }}-${{ github.run_id }}`` satisfies while
    being unique per run. Each shape below reads like a per-issue key without
    being one, and :func:`_per_issue_key_problem` must reject every one —
    otherwise PER_ISSUE_GROUP_OK would excuse a key that bounds nothing, which is
    the defect the allow-list exists to prevent.
    """
    problem = _per_issue_key_problem(group)
    assert problem is not None, (
        f"{group!r} reads like a per-issue key and is not one, yet the check "
        "accepted it — the PER_ISSUE_GROUP_OK allowance would excuse a key that "
        "bounds nothing (#9577)."
    )


@pytest.mark.parametrize(
    "group",
    [
        "finding-provenance-${{ github.event.issue.number || github.run_id }}",
        "finding-provenance-${{ github.event.issue.number || github.event.inputs.issue_number || github.run_id }}",
        "finding-provenance-${{ github.event.issue.number || inputs.issue_number || github.run_id }}",
        # Spacing inside `${{ }}` is not a property change.
        "finding-provenance-${{github.event.issue.number||github.run_id}}",
    ],
)
def test_the_per_issue_key_check_accepts_genuine_per_issue_shapes(group: str) -> None:
    """The other direction: a real per-issue key must NOT red.

    A pin that fails a correct artifact is worse than no pin — the next
    maintainer's correct dispatch fallback or spacing tidy-up has to stay green,
    or the guard gets deleted.
    """
    assert _per_issue_key_problem(group) is None, (
        f"{group!r} IS a per-issue key (issue first, run_id fallback last) and "
        "the check rejected it — the guard would red a correct artifact"
    )


def test_the_reporting_set_is_real_and_complete() -> None:
    """Non-vacuity: a renamed file must fail here, not silently leave the guard."""
    present = {p.name for p in _workflow_paths()}
    assert len(present) >= 30, (
        f"only {len(present)} workflow files found — glob or checkout is wrong"
    )
    missing = sorted(set(REPORTING_PER_EVENT) - present)
    assert not missing, f"REPORTING_PER_EVENT names files that do not exist: {missing}"


def test_every_reporting_per_event_workflow_is_bounded() -> None:
    """The #9115 offenders, by name — so the fix cannot be reverted by omission."""
    for name in sorted(REPORTING_PER_EVENT):
        path = WORKFLOW_DIR / name
        doc = _load(path)
        conc = doc.get("concurrency")
        assert isinstance(conc, dict) and str(conc.get("group", "")).strip(), (
            f"{name} is per-event reporting ({REPORTING_PER_EVENT[name]}) and has "
            "no `concurrency.group` (#9115)"
        )
        assert "cancel-in-progress" in conc, (
            f"{name} must state `cancel-in-progress` explicitly — the silent "
            "default is `false`, i.e. superseded runs queue instead of being "
            "replaced (#9115)"
        )


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_per_push_workflows_are_path_scoped_or_declared(path: Path) -> None:
    """A report cannot observe a change it never reads — path-scope it (#9115)."""
    doc = _load(path)
    trig = _triggers(doc)
    # KEY PRESENCE, not value. `_triggers()` normalises a bare-string `on: push`,
    # a list-form `on: [push]` and a value-less `push:` to ``{"push": None}`` —
    # exactly the three UNFILTERED forms. Reading ``.get("push") is None`` as
    # "no push trigger" made the guard skip the widest triggers it exists to
    # catch (fail-open); ``None`` here means "push, with no filter".
    if "push" not in trig:
        return
    push = trig["push"] or {}
    if path.name in UNFILTERED_PUSH_OK:
        # The reason must still be a live claim: this dict is the allow-list.
        assert UNFILTERED_PUSH_OK[path.name].strip()
        return
    assert isinstance(push, dict), (
        f"{path.name} triggers on every push with no filter. Either add `paths:` "
        "for the files it actually reads, or add it to UNFILTERED_PUSH_OK "
        "with the reason its artifact must see every merged tree (#9115)."
    )
    assert "paths" in push or "paths-ignore" in push, (
        f"{path.name} triggers on every push with no `paths:`/`paths-ignore:` "
        "filter (#9115). Add one, or declare it in UNFILTERED_PUSH_OK."
    )


def test_the_allow_list_has_no_dead_entries() -> None:
    """A stale allow-list entry is a rule that silently stopped applying."""
    for name in UNFILTERED_PUSH_OK:
        path = WORKFLOW_DIR / name
        assert path.exists(), f"UNFILTERED_PUSH_OK names a missing workflow: {name}"
        assert "push" in _triggers(_load(path)), (
            f"{name} is excused from the path-filter rule but no longer triggers "
            "on push — drop the entry so the next per-push workflow is caught."
        )
        # The excuse and the artifact must AGREE. An allow-list entry is
        # consulted before the `paths:` assertion, so an entry whose workflow
        # IS path-scoped silently licenses deleting that filter later — the
        # exact regression this rule exists to catch.
        push = _triggers(_load(path))["push"]
        assert not (isinstance(push, dict) and {"paths", "paths-ignore"} & set(push)), (
            f"{name} is in UNFILTERED_PUSH_OK but its push trigger IS path-scoped "
            "— it already satisfies the rule, so the entry can only weaken it "
            "(it would license removing the filter). Drop the entry."
        )
    for name in ZERO_COST_PR_EVENT:
        assert (WORKFLOW_DIR / name).exists(), (
            f"ZERO_COST_PR_EVENT names a missing workflow: {name}"
        )
    for name in PER_UNIT_GROUP_OK:
        path = WORKFLOW_DIR / name
        assert path.exists(), f"PER_UNIT_GROUP_OK names a missing workflow: {name}"
        group = str((_load(path).get("concurrency") or {}).get("group", ""))
        assert [tok for tok in _PER_UNIT_TOKENS if tok in group], (
            f"{name} is declared in PER_UNIT_GROUP_OK but no longer keys its group "
            "per unit — drop the entry (and the bound it names) so the next "
            "per-event workflow is judged on its own shape."
        )


def test_finding_provenance_keeps_a_per_issue_group_and_no_path_filter() -> None:
    """The #9115 workflow's two correctness properties, pinned individually.

    (a) The group is keyed PER ISSUE (#9577). Not per comment — a comment id is
        unique per event, so a `comment.id` term puts every comment in its OWN
        group and `cancel-in-progress: true` can never fire: the key bounds
        nothing while reading like a bound (38 of 133 queued runs, 0 of 28 PRs
        mergeable). Not a constant either — one issue must never cancel
        another issue's gate.
    (b) An ``issue_comment`` gate must never gain a ``paths:``/``paths-ignore:``
        filter: those events carry no path, so a filtered gate never runs and a
        skipped check reads as a pass to the rail (the reason the sibling guard
        in ``.github/scripts/ai-review-gate.test.sh`` refuses one too).
    """
    doc = _load(WORKFLOW_DIR / "finding-provenance.yml")
    group = str(doc["concurrency"]["group"])
    assert _per_issue_key_problem(group) is None, (
        "the key must be per ISSUE, structurally — a constant group would let one "
        f"issue cancel another's gate, and a run-unique suffix cancels nothing "
        f"(#9577): {group!r}"
    )
    assert doc["concurrency"]["cancel-in-progress"] is True
    trig = _triggers(doc)
    for event in ("issues", "issue_comment"):
        assert event in trig, f"finding-provenance lost its `{event}` trigger"
        assert trig[event] is None or not (
            isinstance(trig[event], dict) and {"paths", "paths-ignore"} & set(trig[event])
        ), f"`{event}` cannot carry a path filter — the gate would never run"


def test_finding_provenance_concurrency_group_never_keys_on_a_comment() -> None:
    """The #9577 guard: the concurrency KEY must never carry `comment.id`.

    ``github.event.comment.id`` is unique PER COMMENT, so a group containing it
    gives every comment its own group and ``cancel-in-progress: true`` can never
    fire — the key is not a bound, it is a unique key that reads like a bound.
    Measured 2026-10-21 (#9577): 100 comments in one hour, 38 of 133 queued runs
    this workflow, ahead of the ``python-ci`` runs PRs need to merge, 0 of 28
    open PRs mergeable (all ``PENDING``).

    This is the assertion a ``yaml.safe_load`` -and-parses check structurally
    cannot make: the file is valid YAML either way, because the expression IS
    the bug. It scans the RUN-level group AND every job-level ``concurrency:``
    block in the file — re-introducing the term at a narrower scope would starve
    exactly the same pool.

    ``comment.id`` is legitimately READ by this workflow (``COMMENT_ID`` in the
    ``Resolve the issue body`` step fetches THAT comment's body by id, which is
    the #4732 injection-safe path). Only the concurrency KEY is forbidden, so
    this guard scopes to ``concurrency:`` blocks and must not be widened into a
    file-wide grep — that would fail the very read the workflow needs.
    """
    doc = _load(WORKFLOW_DIR / "finding-provenance.yml")

    groups: list[tuple[str, str]] = []
    run_group = _concurrency_group(doc.get("concurrency"))
    assert run_group is not None, "finding-provenance must keep a concurrency group (#9115)"
    groups.append(("run-level `concurrency:`", run_group))
    for name, job in (doc.get("jobs") or {}).items():
        job_group = _concurrency_group(job.get("concurrency")) if isinstance(job, dict) else None
        if job_group is not None:
            groups.append((f"job `{name}` `concurrency:`", job_group))

    # Non-vacuity: the `strip()` assertion below is the one that does work (the
    # run-level group is appended under a `run_group is not None` assert, so a
    # `len(groups) >= 1` check here could never fire — it would advertise a
    # check that does not exist).
    assert _normalise_group(run_group).strip(), "the run-level group is empty"

    offenders = [(where, group) for where, group in groups if "comment.id" in group]
    assert not offenders, (
        f"{offenders} key a concurrency group on `comment.id` (#9577). Wherever "
        "it is the RESOLVING term it makes the key unique per comment, so every "
        "comment gets its own group and `cancel-in-progress: true` can NEVER "
        "fire — the key bounds nothing while reading like a bound (measured: 38 "
        "of 133 queued runs, 0 of 28 open PRs mergeable). Behind "
        "`github.event.issue.number` it is dead code TODAY, and it still may not "
        "be in the key: a re-order silently activates it, which is exactly how "
        "this defect got in. Key on the ISSUE (`github.event.issue.number`). "
        "`comment.id` may still be read as EVENT DATA (the body is fetched by "
        "id) — it must never be part of the KEY."
    )

    # ⛔ A TOKEN blacklist alone is not the property (#9577 review). `||`
    # short-circuits on the FIRST truthy operand, so a key that keeps
    # `issue.number` but puts an always-truthy context in the MIDDLE —
    # `finding-provenance-${{ github.event.issue.number || github.event_name ||
    # github.run_id }}` — resolves to ONE SHARED group on `workflow_dispatch`
    # (where `issue.number` is absent), i.e. one global dispatch group, which the
    # workflow forbids two lines ABOVE its own key. A first-term-only check
    # misses that; so does a `comment.id` blacklist. The exact TERM LIST is therefore
    # pinned — the same exactness `test_supabase_deploy_apply_lock_…` uses and
    # for the same reason (substring pins were vacuous there).
    #
    # The pin is scoped to INSIDE `${{ }}`, not to the group's literal prefix:
    # renaming `finding-provenance-` is not a property change and must not red
    # (see `_normalise_group`'s docstring: a pin must never fail with a message
    # that is false about the artifact in front of the maintainer). Extracting
    # through `_EXPRESSION` — never a hand-rolled `split("||")` — is what keeps
    # a single-term group from leaving a stray `}}` in the compared text.
    #
    # ⛔ EXACTLY ONE expression. `_EXPRESSION.search` reads only the FIRST, so
    # `…-${{ issue.number || … }}-${{ github.run_id }}` (a second, APPENDED
    # always-truthy expression) passed a term-list pin that then described a key
    # it had not checked — and that key is unique per run, i.e. the #9577 defect
    # with the module green. The absence of extra terms is the property here
    # (measured), so the count is pinned before the contents.
    expressions = _EXPRESSION.findall(run_group)
    assert len(expressions) == 1, (
        f"the group is {run_group!r} and carries {len(expressions)} `${{{{ … }}}}` "
        "expressions; it must carry EXACTLY ONE (#9577). A second expression —"
        " appended as a suffix, e.g. `…-${{ github.run_id }}` — makes the group "
        "unique per RUN while the term-list assertion is satisfied, so "
        "`cancel-in-progress` cancels nothing: the exact #9577 defect, with a "
        "green guard. Term ABSENCE is part of the property, not an extra."
    )
    terms = [
        "github.event." + term if term.startswith("inputs.") else term
        for term in (t.strip() for t in expressions[0].split("||"))
    ]
    assert terms == _FINDING_PROVENANCE_GROUP_TERMS, (
        f"the group is {run_group!r} and resolves via the terms {terms}; it must "
        f"be {_FINDING_PROVENANCE_GROUP_TERMS} (#9577). `||` returns the first "
        "TRUTHY operand, so these are the shapes to avoid: (a) an always-truthy "
        "term FIRST — `github.run_id` mints a group per RUN, `github.event_name` "
        "collapses EVERY event into one group (the global group the workflow "
        "forbids); (b) an always-truthy term in the MIDDLE — `github.event_name` "
        "collapses every `workflow_dispatch`, which has no "
        "`github.event.issue.number`, into ONE shared group; (c) DROPPING the "
        "trailing `github.run_id` fallback does the same for any trigger whose "
        "issue/input term is absent; and (d) a per-event-UNIQUE term "
        "(`github.event.comment.id`) makes the key unique per event wherever it "
        "is the resolving term — and dead code elsewhere, waiting for a re-order "
        "— so it must never be in the KEY at all. Every one of (a)-(d) means "
        "`cancel-in-progress` is not a per-issue bound. The `inputs.issue_number` "
        "spelling is ACCEPTED as the synonym of `github.event.inputs.issue_number` "
        "(normalised above), because GitHub evaluates both identically. Any other "
        "change here is a reviewed decision, not a tidy-up: pin it deliberately."
    )


def test_finding_provenance_does_not_pay_for_history_before_classifying() -> None:
    """The #9115 cost fix: the full-history clone lives behind the classifier.

    Measured on this repo: 19 of 20 sampled runs were ordinary discussion
    comments that paid a full-history checkout (14 s of an 18 s job) and then
    skipped the gate step entirely. The ``classify`` job must therefore check
    out SHALLOW, and the only job that takes ``fetch-depth: 0`` must be
    conditional on the classification.
    """
    doc = _load(WORKFLOW_DIR / "finding-provenance.yml")
    jobs = doc["jobs"]
    classify = jobs["classify"]
    provenance = jobs["provenance"]
    assert classify["outputs"]["is_finding"].strip()
    assert "needs" in provenance, "the expensive leg must not run for every comment"
    assert "is_finding" in str(provenance["if"]), (
        "the full-history leg must be gated on the classifier's verdict"
    )

    def depths(job: dict) -> list[str]:
        return [
            str(step["with"].get("fetch-depth"))
            for step in job.get("steps", [])
            if "actions/checkout" in str(step.get("uses", ""))
        ]

    assert depths(classify) == ["1"], (
        "classification is a pure TEXT predicate — it must not clone history "
        f"(#9115) — got fetch-depth={depths(classify)}"
    )
    assert depths(provenance) == ["0"], "the ancestry test needs full history"
    for job in (classify, provenance):
        assert "timeout-minutes" in job, (
            "an unbounded job can hold a runner for the 360-minute default; "
            "bounding it is what makes a wedged clone survivable (#9115)"
        )


def test_the_required_aggregate_check_keeps_its_identity() -> None:
    """``python-ci-gate`` is load-bearing: the job id is the required check name.

    Branch protection AND ``.mergify.yml`` (``check-success=python-ci-gate``)
    name it. Renaming the job, or adding a ``name:`` override, silently
    un-requires the gate — a merge would then wait for a check that no run
    produces, which is the same ``WAITING-CI`` forever shape #9115 cost hours.
    """
    doc = _load(WORKFLOW_DIR / "python-ci.yml")
    jobs = doc["jobs"]
    assert "python-ci-gate" in jobs, "the REQUIRED aggregate job id is gone"
    assert "name" not in jobs["python-ci-gate"], (
        "`python-ci-gate` must carry no `name:` override — the job ID is the required check name"
    )
    # It must stay an AGGREGATE (it gates on the legs, it runs none itself).
    assert jobs["python-ci-gate"].get("needs"), "the aggregate must depend on the legs"

    mergify = (REPO_ROOT / ".mergify.yml").read_text()
    assert "check-success=python-ci-gate" in mergify, (
        "Mergify's merge condition names the check; keep the two in step"
    )


def test_no_reporting_workflow_runs_on_the_pull_request_path() -> None:
    """A REPORT must never be a ``pull_request`` check: a red report would block.

    #9115's lesson generalised — the required-check surface is small and
    deliberate, and a report competes with it for the same runner budget.
    """
    offenders = []
    for name in REPORTING_PER_EVENT:
        if name in ZERO_COST_PR_EVENT:
            continue
        trig = _triggers(_load(WORKFLOW_DIR / name))
        if {"pull_request", "pull_request_target"} & set(trig):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} are per-event REPORTS registered on the pull-request "
        "path; a report there competes with `python-ci-gate` for the same "
        "runner budget (#9115)"
    )


def test_the_zero_cost_pr_event_exemption_is_still_true() -> None:
    """`inbound-relay`'s exemption rests on its job-level `if:`, not on trust.

    A ``pull_request_target`` event creates the RUN, but the JOB is skipped when
    the ``if:`` is false — and a skipped job is never allocated a runner. If that
    condition is ever removed or widened, the relay starts competing with the
    REQUIRED gate for slots while this module still excused it.
    """
    doc = _load(WORKFLOW_DIR / "inbound-relay.yml")
    # ALL whitespace stripped: a reformatted (line-wrapped) expression changes no
    # decision, so it must not red this gate — and this is the SAME normalisation
    # the other two assertions' files use, so one edit has one answer.
    job_if = "".join(str(doc["jobs"]["relay"].get("if", "")).split())
    assert "github.actor!='daniel-ospina'" in job_if, (
        "the internal-traffic exclusion (the owner) is what makes this workflow "
        "zero-cost on the PR path — keep it in the job-level `if:`"
    )
    # The queue's batch PRs must stay exempt at JOB level (a skipped job costs
    # no runner; a step-level decision would), and the exemption must be
    # EVIDENCE rather than a bot login that rots (#5185).
    assert "startsWith(github.event.pull_request.head.ref" in job_if, (
        "the queue-owned head branch must stay exempt (#3558/#5185) — without "
        "it every queued merge is dequeued and this workflow stops being "
        "zero-cost for the queue's traffic"
    )
    assert "github.event.pull_request.user.type=='Bot'" in job_if, (
        "the author-identity belt must stay explicit, keyed on GitHub's `type` "
        "field rather than a bot login (#5185)"
    )
    assert "mergify[bot]" not in job_if, (
        "a hardcoded bot login is the rot #5185 removed: a renamed app "
        "re-breaks the relay silently"
    )


# ── A run-level group and a job-level group must never share a name ──────────
#
# Measured 2026-10-20. Adding a run-level `concurrency:` to supabase-deploy
# (#9120) reused the group name the `deploy` JOB already declared. The RUN
# acquires the group first, so the job can never acquire it — and the run cannot
# finish until the job runs. GitHub resolves that deadlock by failing the job
# INSTANTLY with ZERO steps and `runner_name: null`:
#
#     JOB deploy | conclusion=failure | steps=0 | runner=none
#     started_at 20:02:38Z -> completed_at 20:02:37Z   (completed BEFORE started)
#
# Nothing in that output names concurrency, so it reads as a mystery red. The
# consequence was not cosmetic: the job is the ONLY thing that applies repo
# migrations to prod, so no migration could be applied, so the drift gate
# blocked every `deploy-hosted` behind it, so a merged fix could not deploy.
#
# A name comparison catches the whole class in one line, and the two intents
# stay separately satisfiable: the run-level group serialises whole deploys
# (#9115), the job-level one serialises only the apply (#1095). Callers compare
# through :func:`_normalise_group`, because GitHub treats `${{github.ref}}` and
# `${{ github.ref }}` as the SAME group — a raw-text comparison would miss a
# real deadlock written the other way round.


def _concurrency_group(block: object) -> str | None:
    """The group name of a `concurrency:` block, or None when it is not a name.

    Accepts both spellings GitHub allows (bare string, or a mapping).

    The contract is **textual**: a group containing `${{ … }}` is returned as
    its literal text, NOT resolved and NOT skipped. That is deliberate and it
    cuts both ways:

    * two blocks naming the SAME group collide, including expression-valued
      ones (`ci-${{ github.ref }}` twice) — correctly flagged;
    * two blocks whose expressions name DIFFERENT groups (`github.ref` vs
      `github.ref_name`, or an extra `github.run_id`) compare unequal, so a
      collision there is MISSED — that is a semantic difference, not a
      spelling one, and resolving it needs the expression evaluated.

    Callers MUST compare through :func:`_normalise_group`: spacing inside
    `${{ … }}` does not change the group, so raw-text comparison would both
    miss a real collision (`${{github.ref}}` vs `${{ github.ref }}`) and invent
    one in the other direction. Skipping expressions altogether would lose the
    collision case to catch the semantics case, which is the worse trade: an
    unresolved collision deadlocks a job, while differing contexts are caught
    by review. Callers must not assume an expression means "no group".
    """
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        group = block.get("group")
        return group if isinstance(group, str) else None
    return None


_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_CONDITION_OPERATOR = re.compile(r"\s*(==|!=|>=|<=|&&|\|\||>|<)\s*")


def _normalise_condition(condition: str) -> str:
    """An `if:` expression canonicalised: no `${{ }}`, quote style, or spacing.

    `if: github.event_name == 'workflow_dispatch'`, the same condition with
    double quotes, with the operators unpadded, and the wrapped
    `if: ${{ github.event_name == 'workflow_dispatch' }}` are all the SAME
    condition — GitHub evaluates an `if:` as an expression with or without the
    wrapper — so a format pass or a hand-edit copying the documented form must
    not red the pin, and the failure message ("revisit the group design") would
    then be false about the artifact in front of the maintainer. Only the FORM
    is normalised: adding, dropping or changing a term still fails, wrapped or
    not.
    """
    stripped = condition.strip()
    whole = _EXPRESSION.fullmatch(stripped)
    if whole is not None:
        stripped = whole.group(1)
    collapsed = re.sub(r"\s+", " ", stripped.replace('"', "'")).strip()
    return _CONDITION_OPERATOR.sub(r" \1 ", collapsed).strip()


def _normalise_group(group: str) -> str:
    """Group text with `${{ … }}` spacing collapsed, for exact comparison.

    GitHub ignores the whitespace inside an expression, so `${{github.ref}}`
    and `${{ github.ref }}` are the SAME group. Comparing raw text would turn a
    cosmetic reformat into a red, and the assertion message ("must be
    event-scoped and NOTHING else") would then be false about the artifact in
    front of the maintainer. Normalising keeps the pin about WHICH contexts a
    group uses and still fails every change of context (an added
    `${{ github.run_id }}`, a removed `github.event_name`).
    """
    return _EXPRESSION.sub(lambda m: "${{ " + m.group(1) + " }}", group)


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_no_job_reuses_the_run_level_concurrency_group(path: Path) -> None:
    """A job group equal to the run group deadlocks: the job never starts.

    Compared through :func:`_normalise_group`, so `ci-${{github.ref}}` in a job
    against `ci-${{ github.ref }}` at run level is flagged as the collision it
    is. Raw text would miss it, and that pair deadlocks exactly like an
    identical spelling does.
    """
    doc = _load(path)
    run_group = _concurrency_group(doc.get("concurrency"))
    if run_group is None:
        return
    jobs = doc.get("jobs") or {}
    assert isinstance(jobs, dict), f"{path.name}: `jobs:` is not a mapping"
    normalised_run = _normalise_group(run_group)
    colliding = sorted(
        name
        for name, job in jobs.items()
        if isinstance(job, dict)
        and (job_group := _concurrency_group(job.get("concurrency"))) is not None
        and _normalise_group(job_group) == normalised_run
    )
    assert not colliding, (
        f"{path.name}: job(s) {colliding} reuse the run-level concurrency group "
        f"{run_group!r}. The run holds that group first, so the job can never "
        f"acquire it and GitHub fails it with ZERO steps and no runner — which "
        f"reads as a mystery red (measured 2026-10-20, #9120 x #1095). Give the "
        f"job its own group name."
    )


def test_supabase_deploy_apply_lock_is_distinct_from_the_run_lock() -> None:
    """The incident itself, pinned: every property of the two locks.

    (a) the apply lock is a CONSTANT and differs from the run group — that is the
        deadlock (#9120 x #1095);
    (b) the run group is EVENT-SCOPED, so a starved `push` run cannot hold the
        lock a `workflow_dispatch` needs. A constant run group re-breaks #1095
        even with distinct names, because every run of the workflow acquires it
        (#1095's reviewed decision: "workflow-level concurrency is claimed
        per-run — a push run would still hold the shared group");
    (c) neither lock cancels (a cancelled apply leaves a half-applied schema);
    (d) a push run cannot reach the CONSTANT apply lock at all: `check-drift`
        declares no job-level group, and `deploy` is dispatch-only. The exact
        pins (a)-(c) describe names and flags only, so without (d) either
        mechanism can be removed with the module fully green — measured.

    Named separately from the parametrised sweep because a regression here is
    the one that silently kills the ONLY automated path from repo to prod
    migrations, and it must fail with that consequence spelled out.
    """
    doc = _load(WORKFLOW_DIR / "supabase-deploy.yml")
    run_group = _concurrency_group(doc.get("concurrency"))
    apply_group = _concurrency_group(doc["jobs"]["deploy"].get("concurrency"))
    assert run_group is not None, "the run-level deploy lock (#9115) must stay"
    assert apply_group is not None, "the apply lock (#1095) must stay in place"
    # ⛔ EXACT pins, not substring checks. An earlier revision asserted
    # `"github.event_name" in run_group` and `"${{'" not in apply_group`, and
    # BOTH were vacuous in the direction that matters: the first is satisfied by
    # `supabase-deploy-${{ github.event_name }}-${{ github.run_id }}` (every run
    # gets its own group, so deploys never serialise), and the second — the
    # literal is `$`, `{`, `{`, `'`, and a real group always has a character
    # between `{{` and a quote — is satisfied by
    # `supabase-deploy-apply-${{ github.run_id }}` (every dispatch gets its own
    # apply group, so the apply lock stops being a lock at all). Exact text
    # pins both properties at once: constant apply lock, event-scoped run lock,
    # and the two distinct from each other.
    #
    # The comparison is on NORMALISED text (`${{github.ref}}` == `${{ github.ref }}`),
    # so a cosmetic reformat is not a false red: the pin is about which contexts
    # the group uses, not about spacing. Every mutation that changes the contexts
    # still fails — see `test_group_pin_ignores_whitespace_but_not_extra_context`.
    assert _normalise_group(run_group) == "supabase-deploy-${{ github.event_name }}", (
        f"the run-level group must be event-scoped and NOTHING else "
        f"({run_group!r}). A constant holds the lock on every run — including "
        "the `push` drift run, which then starves an operator's dispatch apply "
        "(#1095); any extra context (e.g. `github.run_id`) gives every run its "
        "own group, so the run-level lock bounds NOTHING (#9115's unbounded "
        "shape) and whole deploys stop serialising. Applies themselves are still "
        "serialised by the constant apply lock below — which is exactly why "
        "this mutation has to be caught HERE."
    )
    assert _normalise_group(apply_group) == "supabase-deploy-apply", (
        f"the apply lock must be the CONSTANT 'supabase-deploy-apply' "
        f"({apply_group!r}): an expression gives every dispatch its own apply "
        "lock, so the ONLY thing serialising applies would be the event-scoped "
        "RUN lock above — the apply lock would no longer be independent of the "
        "run-level design #1095 exists to change, and a second applying "
        "workflow (or a push deploy after the #771 flip) would have nothing "
        "excluding it. Collapsing onto the run group "
        f"{run_group!r} is the 2026-10-20 deadlock, where the deploy job "
        "fails with zero steps and no migration can be applied automatically."
    )
    # ⛔ `cancel-in-progress: false` is the other half of the same promise, and
    # it is stated as load-bearing in the workflow's own comment (“a cancelled
    # migration apply leaves the schema half-migrated”). Presence alone is not
    # it: flipping either lock to `true` left the whole module green, so the
    # claim in that comment would have been enforced by nothing.
    assert doc["concurrency"]["cancel-in-progress"] is False, (
        "the run-level deploy lock must never cancel: a cancelled migration "
        "apply leaves the schema half-migrated and no re-run repairs it "
        "deterministically — a superseded run must QUEUE instead (#9115)"
    )
    assert doc["jobs"]["deploy"]["concurrency"]["cancel-in-progress"] is False, (
        "the apply lock must never cancel a workflow_dispatch run: a cancelled "
        "apply leaves the schema half-migrated and no re-run repairs it "
        "deterministically (#1095). If superseded `push` DRIFT runs should "
        "cancel, that is a different lock — write it as the expression "
        "`cancel-in-progress: ${{ github.event_name == 'push' }}` and update "
        "this pin deliberately; the literal `true` must stay red."
    )
    # ⛔ The apply lock is CONSTANT, so it is only safe because a push run can
    # never reach it. TWO things make that true and neither is a group name, so
    # the exact pins above cannot see either one going away:
    #   * `check-drift` (the push arm) never acquires the apply lock — if it
    #     did, a starved push run would hold the lock an operator's dispatch
    #     apply needs (#1095);
    #   * `deploy` is dispatch-only — let it run on push and every push run's
    #     deploy job queues on the SAME constant group.
    # Both mutants left the whole module green before these pins existed.
    drift_group = _concurrency_group(doc["jobs"]["check-drift"].get("concurrency"))
    assert drift_group is None or (
        _normalise_group(drift_group) != _normalise_group(apply_group)
    ), (
        "check-drift must NOT acquire the constant apply lock: it runs in a push "
        "run, so a starved push run would hold the lock an operator's dispatch "
        "apply needs (#1095), and no group on this job needs an apply lock."
    )
    assert _normalise_condition(str(doc["jobs"]["deploy"].get("if", ""))) == (
        "github.event_name == 'workflow_dispatch'"
    ), (
        "deploy must stay DISPATCH-ONLY: the apply lock is a constant, so a push "
        "run that reaches this job queues behind an operator's apply. If you are "
        "deliberately flipping #771 so pushes deploy, the apply lock has to stop "
        "being reachable from a push run first — revisit this pin and the group "
        "design together, not this test alone."
    )


def test_group_pin_ignores_whitespace_but_not_extra_context() -> None:
    """The normaliser the exact pins compare through: cosmetic, and no more.

    A whitespace-only reformat of an expression is the SAME GitHub group, so it
    must not read as a property change — while every change of CONTEXT (adding
    `github.run_id`, dropping `github.event_name`) must still be visible to the
    pin, or the pin would be the vacuous assertion this module replaced.
    """
    pinned = "supabase-deploy-${{ github.event_name }}"
    assert _normalise_group("supabase-deploy-${{github.event_name}}") == (
        _normalise_group(pinned)
    ), "a whitespace-only reformat is the same group; it must not be a red"
    assert _normalise_group("supabase-deploy-${{  github.event_name  }}") == (
        _normalise_group(pinned)
    )
    differing = [
        "supabase-deploy-${{ github.event_name }}-${{ github.run_id }}",
        "supabase-deploy-${{ github.ref }}",
        "supabase-deploy",
    ]
    for group in differing:
        assert _normalise_group(group) != _normalise_group(pinned), (
            f"{group!r} differs in the CONTEXTS it uses, so the pin must see it "
            "as different — otherwise every run gets its own group and deploys "
            "stop serialising"
        )


def test_if_condition_pin_ignores_quote_style_but_not_extra_terms() -> None:
    """The `if:` pin's normaliser: canonical FORM, exact CONDITION.

    A YAML format pass may switch a quoted scalar's quote style or reflow
    spaces; that is the same condition and must not red the dispatch-only pin
    with a message about redesigning the locks. A changed term must still fail,
    or the pin would not hold the property.
    """
    pinned = "github.event_name == 'workflow_dispatch'"
    accepted = (
        'github.event_name  ==  "workflow_dispatch"',
        "  github.event_name == 'workflow_dispatch' \n",
        # `if:` is an expression with or without the `${{ }}` wrapper, and the
        # wrapped form is the one the docs show.
        "${{ github.event_name == 'workflow_dispatch' }}",
        "${{github.event_name=='workflow_dispatch'}}",
    )
    for form in accepted:
        assert _normalise_condition(form) == pinned, (
            f"{form!r} is the SAME condition; it must not red the pin"
        )
    rejected = (
        "github.event_name == 'push'",
        "github.event_name != 'workflow_dispatch'",
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'",
        "${{ github.event_name == 'push' }}",
        "${{ true }}",
        "",
    )
    for other in rejected:
        assert _normalise_condition(other) != pinned, (
            f"{other!r} is not the dispatch-only condition; the pin must see it"
        )


# ── The merged-tree guard must RUN on every merged tree (#2174) ───────────────
#
# "Triggers on every push" and "runs on every merged tree" are different
# properties, and only the first one is visible in the `on:` block. GitHub keeps
# ONE pending run per `concurrency.group` (`queue: single` is the default) and
# CANCELS that pending run when a newer run in the same group is queued — so a
# cheap guard sharing a group with a long job is skipped, silently, on exactly
# the busy main it exists to watch.
#
# MEASURED (2026-10-22, the incident this pin is written from): the #2174 ruff
# gate on main lived as a job in `post-merge-validation.yml`, whose group is the
# constant `postmerge-validation` and whose `validate` sibling runs 30-60 min.
# Over the 25 pushes before 2026-10-22T13:02Z the `lint` job existed on **4** and
# was ABSENT on the other **21** (run cancelled before it started) — including
# `3337ad1f`, the merge that reddened main with 5 ruff findings. Main stayed red
# for 11 hours and **14 of 27** open PRs carried `agent-infra-ci / lint` =
# failure on merge refs that were red because of MAIN, not their own diff.
#
# ⛔ The pin has TWO jobs, and the second one is the one that actually loses
# gates: proving the guard RUNS (trigger + concurrency key), and proving it still
# FAILS on a finding. A step that runs the right command with
# `continue-on-error: true`, `|| true`, or an `if:` that never fires is a guard
# that runs and reports green — a false PASS, one level below "the guard did not
# run at all".

# Context terms that make a `push` concurrency group UNSTARVEABLE: each is
# unique per pushed tree (or per run, which is even safer). `github.sha` and
# `github.event.after` are the pushed tip; `github.run_id` is unique per run.
# ⛔ `github.ref` is NOT one of them — on a `push` to main it is the constant
# `refs/heads/main`, i.e. the shared group with a keyed-looking name.
_UNSTARVEABLE_GROUP_TERMS = frozenset(
    {"github.sha", "github.event.after", "github.run_id"}
)

# The only step keys the ruff step may carry. An allow-list rather than a
# deny-list because the ways to defuse a step are open-ended and the ways to run
# one honestly are not: `if:` (never runs), `continue-on-error:` (never fails
# the job), `shell:`/`working-directory:`/`env:` (runs something else). Same
# idiom as `tests/test_mcp_rename_table.py`'s step-key allow-list.
_RUFF_STEP_KEYS = frozenset({"name", "run", "id", "timeout-minutes"})


def _run_commands(doc: dict) -> list[str]:
    """Every literal `run:` string in the workflow (jobs → steps)."""
    commands: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            run = (step or {}).get("run")
            if isinstance(run, str):
                commands.append(run)
    return commands


def _dev_ruff_pin() -> str:
    """The ONE ``ruff==X`` pin declared in ``pyproject.toml``.

    Read rather than duplicated as a literal, so the workflows' install lines
    cannot drift from what ``uv sync`` installs: #1685/#2150 were the
    UNPINNED-install class, and a *differently*-pinned install is the same class
    with a number in it. Exactly one pin must exist — a second, divergent one
    fails HERE rather than in a red PR hours later.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    specs: list[str] = []
    for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
        specs.extend(group)
    for group in (data.get("dependency-groups") or {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    pins = {s.split("==", 1)[1].strip() for s in specs if s.startswith("ruff==")}
    assert len(pins) == 1, (
        f"pyproject.toml must declare exactly one `ruff==` pin, found {sorted(pins)} "
        "— a second, divergent pin lets main lint and PR lint disagree about what "
        "a finding is (#1685)"
    )
    return pins.pop()


# Flags that change only how ruff REPORTS, never which rules run or what the exit
# code is. An allow-list, not a deny-list: `--select`/`--ignore`/`--config` narrow
# or replace the rule surface, `--exit-zero` makes every run green, and `--fix`
# would rewrite the tree the guard is measuring.
_RUFF_PRESENTATION_FLAGS = ("--no-cache", "--no-fix", "--quiet", "--statistics", "-q")
_REDIRECTION = re.compile(r"^(?:\d?>&\d+|\d?>\s*/dev/null|>\s*/dev/null)$")

# Keys the workflow, and the ONE job, may carry. Allow-lists rather than
# deny-lists because the ways to lose the gate are open-ended: a second job plus
# a `needs:` SKIPS the guard (every job skipped ⇒ the run concludes SUCCESS with
# ruff never having run — the shape `test_merge_throughput.py` pins for the docs
# job), and a key GitHub does not accept at workflow scope (`continue-on-error`)
# means no run exists at all, so nothing reports red.
_WORKFLOW_KEYS = frozenset(
    {"name", "on", "concurrency", "permissions", "jobs", "run-name", "env", "defaults"}
)
_JOB_KEYS = frozenset(
    {"runs-on", "name", "timeout-minutes", "steps", "permissions", "env", "defaults"}
)


def _ruff_argv_problem(command: str) -> str | None:
    """Why ``command`` is not a full-tree, still-failing ``ruff check .``, or ``None``.

    Tokenised, not substring-matched: `ruff check . || true`, `; true`, a pipe and
    a `$(...)` all satisfy a membership test while making a REAL failure report
    green (the exact-invocation precedent in ``test_merge_throughput.py``).

    Flags may sit BEFORE or AFTER the path — `ruff check --output-format=github .`
    is the conventional order for the edit this workflow is most likely to get,
    and a pin that reds it is a pin that gets deleted.
    """
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError as exc:
        return f"the command cannot be parsed as a shell word list ({exc})"
    # A leading `VAR=value` / `env VAR=value` assignment cannot narrow the run
    # either; strip it before looking at the program.
    while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
        tokens = tokens[1:]
    if tokens[:1] == ["env"]:
        tokens = tokens[1:]
        while tokens and "=" in tokens[0]:
            tokens = tokens[1:]
    # `uv run ruff …` is this repo's documented command form (AGENTS.md → Python)
    # and `python -m ruff …` is the same binary; neither can narrow the surface,
    # and the pinned install ahead of the step still constrains the version.
    prefixes = (
        ["uv", "run", "ruff"],
        ["python", "-m", "ruff"],
        ["python3", "-m", "ruff"],
        ["ruff"],
    )
    offset = None
    for prefix in prefixes:
        if tokens[: len(prefix)] == prefix:
            offset = len(prefix)
            break
    if offset is None:
        if "ruff" in tokens and "check" in tokens:
            return (
                "the step must be a SINGLE `ruff check .` invocation: a `run: |` block "
                "whose other commands include it is not modelled (an arbitrary prefix "
                "is how the step stops being the guard), and a shell escape "
                "(`|| true`, `; true`, a pipe) is not accepted either"
            )
        return (
            "the invocation does not run ruff — a shell escape (`|| true`, `; true`, "
            "a pipe) or a different command satisfies a substring test while making "
            "a real failure report green"
        )
    tokens = tokens[offset:]
    if tokens[:1] != ["check"]:
        return f"it runs `ruff {' '.join(tokens[:1]) or '(nothing)'}` rather than `ruff check`"
    tokens = tokens[1:]
    paths: list[str] = []
    consume_value = False
    for token in tokens:
        if consume_value:
            consume_value = False
            continue
        if token in {">", "1>", "2>"}:
            consume_value = True  # `> /dev/null`: the target is the next token
            continue
        # Redirection (`2>&1`, `2>/dev/null`) cannot change what is checked or the
        # exit code, so it is not a narrowing. A pipe/`;`/`&&`/`||` is a shell
        # escape and stays a non-flag token, which fails the `.`-only comparison.
        if _REDIRECTION.search(token):
            continue
        if token.startswith("-"):
            if token == "--output-format":
                consume_value = True  # the space-separated CLI form
                continue
            if token in _RUFF_PRESENTATION_FLAGS or token.startswith("--output-format="):
                continue
            return (
                f"it passes {token!r}, which is not a presentation-only flag — "
                "`--select`/`--ignore`/`--extend-ignore`/`--config` narrow or replace "
                "the rule surface, `--exit-zero` makes every run green, and `--fix` "
                "rewrites the tree the guard measures. Presentation flags allowed: "
                f"{['--output-format=<fmt>', *_RUFF_PRESENTATION_FLAGS]}"
            )
        paths.append(token)
    normalised = [posixpath.normpath(path) for path in paths]
    if normalised != ["."]:
        return (
            f"it checks {paths or 'nothing'} rather than the whole tree — ruff reads "
            "`.`, and any narrower target leaves the rest of main unguarded"
        )
    return None


def _ruff_install_specs(command: str) -> list[str]:
    """Every ``ruff`` requirement token installed by ``command`` (all lines/tokens).

    Parsed rather than regex-searched, because a raw search is blind in both
    directions: a COMMENTED ``# pip install ruff==0.16.4`` satisfied it while the
    real line was bare ``pip install ruff``, and the equally-pinned spellings
    ``-q`` / ``--upgrade`` / a quoted ``'ruff==…'`` were rejected. `uv` and
    `python -m pip` prefixes install into the same environment.

    ⛔ It returns EVERY spec, not "a match": pip is last-write-wins, so a second,
    unpinned ``pip install ruff`` after the pinned one silently replaces it.
    """
    specs: list[str] = []
    for line in command.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            # An unparseable install line is not a pinned install; the caller's
            # message says so instead of raising out of the checker.
            continue
        # Shell SEGMENTS: `pip install ruff==0.16.4 && pip install -U ruff` is TWO
        # installs, and only splitting on the control operators finds the second
        # one (a single forward scan stops at the `&&` and reports the pin alone).
        segment: list[str] = []
        for token in [*tokens, ";"]:
            if token in {"&&", "||", ";", "|", "&"}:
                specs.extend(_segment_ruff_specs(segment))
                segment = []
                continue
            segment.append(token)
    return specs


_INSTALL_WRAPPERS = {"sudo", "env", "command", "time"}


def _segment_ruff_specs(segment: list[str]) -> list[str]:
    """The ``ruff`` requirement tokens in ONE shell segment (empty if not an install).

    The segment is identified by what it DOES, not by its first word: `sudo pip
    install ruff`, `env X=1 pip install ruff`, `/usr/bin/pip install ruff` and
    `python3.12 -m pip install ruff` all install a ruff, and a first-word rule
    read every one of them as "not an install" — so an unpinned ruff could be
    installed AFTER the pinned one and the pin reported the pinned set.
    `uv sync` is also accepted: it installs the `pyproject.toml` dev group, which
    is the pin `_dev_ruff_pin` reads.
    """
    segment = list(segment)
    while segment and segment[0] in _INSTALL_WRAPPERS:
        segment = segment[1:]
        while segment and "=" in segment[0] and not segment[0].startswith("-"):
            segment = segment[1:]
    if not segment:
        return []
    head = segment[0].rsplit("/", 1)[-1]
    if head == "uv" and segment[1:2] == ["sync"]:
        return ["uv sync"]
    if "install" not in segment:
        return []
    if not any(part in {"pip", "pip3", "pipx"} or part.startswith("pip3") for part in segment):
        return []
    specs: list[str] = []
    for token in segment[segment.index("install") + 1 :]:
        if token.startswith("-"):
            continue
        name = re.split(r"[\[<>=!~@]", token, maxsplit=1)[0].lower().replace("_", "-")
        if name == "ruff":
            specs.append(token)
    return specs


def _pinned_install_problem(commands: list[str], pin: str) -> str | None:
    """Why the installs before the guard do not leave exactly ``ruff==<pin>``, or None."""
    specs = [spec for command in commands for spec in _ruff_install_specs(command)]
    if not specs:
        return (
            f"no step BEFORE the ruff step installs the pinned ruff "
            f"(`pip install ruff=={pin}`; `uv`/`python -m pip`, `-q`, `--upgrade` and a "
            "quoted spec are all accepted), so main lints with whatever resolution a "
            "bare install gives — the #1685/#2150 drift class"
        )
    if specs == ["uv sync"]:
        return None  # installs exactly the pyproject dev group this pin is read from
    unpinned = [spec for spec in specs if spec.strip("'\"") != f"ruff=={pin}"]
    if unpinned:
        return (
            f"the installs ahead of the guard are {specs} — pip is LAST-WRITE-WINS, so "
            f"{unpinned} replaces `ruff=={pin}` and main lints with a different ruff "
            "than PR lint and `uv sync` (#1685). Install exactly the pin, once"
        )
    return None


# Keys an `actions/checkout` `with:` may carry: `ref` pins the tree, and the rest
# cannot change WHICH tree lands in the workspace (they change fetch depth, tags,
# credentials, LFS, cleaning or submodules — all orthogonal to what ruff reads).
_CHECKOUT_WITH_KEYS = frozenset(
    {"ref", "fetch-depth", "fetch-tags", "persist-credentials", "lfs", "clean", "submodules", "path"}
)
_STATUS_FUNCTIONS = frozenset({"failure()", "always()", "success()", "cancelled()"})
_STATUS_SCAN = re.compile(r"\b(failure|always|success|cancelled)\(\)")
_JS_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_JS_LITERALS = re.compile(
    r'"(?:[^"\\\n]|\\.)*"' r"|'(?:[^'\\\n]|\\.)*'" r"|`(?:[^`\\]|\\.)*`",
    re.S,
)


def _pattern_list_problem(value: object, key: str) -> str | None:
    """Why ``value`` is not a valid ``branches``/``branches-ignore`` selector, or ``None``.

    GitHub requires a sequence of glob strings (or a single string). A MAPPING is
    iterated as its keys — so `{"main": null}` read as "selects main" and the pin
    went green on a workflow GitHub refuses to run; an int escaped as an uncaught
    `TypeError` out of a function whose contract is to RETURN the reason.
    """
    if isinstance(value, str):
        return None
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return None
    return (
        f"its `push:` trigger's `{key}:` is {value!r}, which is not a sequence of glob "
        "strings — GitHub rejects the workflow, so no run exists at all and nothing "
        "reports red"
    )


def _balanced(expression: str) -> bool:
    """True when ``expression`` has no unbalanced parenthesis (quote-naive is fine here)."""
    depth = 0
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
_INERT_SHELLS = {"bash", "sh"}


def _inert_step_key(key: str, value: object) -> bool:
    """True when ``key: value`` on the ruff step cannot change what the step does.

    The pin must not red an honest tidy-up: `continue-on-error: false`, the default
    shell, `working-directory: .` and an empty `env:` are all no-ops — and the same
    file already models `defaults.run.shell: bash` as inert. Any OTHER value of
    these keys is rejected, with a message that says the pin does not model it
    rather than asserting an effect the artifact does not have.
    """
    if key == "continue-on-error":
        return value is False
    if key == "shell":
        return str(value).strip() in _INERT_SHELLS
    if key == "working-directory":
        return str(value).strip() in {".", "./"}
    if key == "env":
        return not value
    return False


def _condition_terms(condition: str) -> set[str]:
    """The `&&` conjuncts of an `if:` expression, normalised and order-free.

    `failure() && steps.ruff.outcome == 'failure'` and the same two conjuncts
    swapped are the SAME condition; comparing the raw text rejected the swapped
    form with a message about the implicit `success()`, which was false about the
    artifact. A constant-false conjunct (`&& (1 == 2)`) still adds a term and
    still fails the comparison.
    """
    stripped = condition.strip()
    whole = _EXPRESSION.fullmatch(stripped)
    if whole is not None:
        stripped = whole.group(1).strip()
    # `steps['ruff'].outcome` and `steps.ruff.outcome` are the SAME accessor.
    stripped = re.sub(r"steps\['([^']+)'\]", r"steps.\1", stripped)
    # A condition wrapped as a whole is the same condition: `(failure() && … )` and
    # `((failure() && … ))` both evaluate identically, and cycle 7 only stripped
    # parentheses around ONE CONJUNCT.
    while stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1].strip()
        if not _balanced(inner):
            break
        stripped = inner
    terms = set()
    for part in re.split(r"&&", stripped):
        term = part.strip()
        # `(steps.ruff.outcome == 'failure')` is the same conjunct one layer deeper.
        while term.startswith("(") and term.endswith(")"):
            term = term[1:-1].strip()
        if term:
            terms.add(_normalise_condition(term))
    return terms


def _defaults_problem(defaults: object) -> str | None:
    """Why a `defaults:` block can defuse the guard, or ``None`` for an inert one.

    `defaults.run.shell` / `defaults.run.working-directory` rewrite every `run:`;
    `shell: bash` and `working-directory: .` are the defaults and change nothing,
    so they are accepted — the pin must not red an honest tidy-up.
    """
    if defaults is None:
        return None
    if not isinstance(defaults, dict):
        return "it is not a mapping"
    run = defaults.get("run") or {}
    shell = str(run.get("shell", "bash")).strip()
    workdir = str(run.get("working-directory", ".")).strip()
    if shell not in {"bash", "sh"}:
        return (
            f"`shell: {shell}` replaces the shell every `run:` executes in, which is "
            "how `ruff check .` becomes an echo that exits 0 forever"
        )
    if workdir not in {".", "./"}:
        return (
            f"`working-directory: {workdir}` makes `ruff check .` scan one directory "
            "while reading exactly like the whole tree"
        )
    return None


# A `case` arm head: the pattern list, an unescaped `)`, and nothing after it on
# the line. `*)` is the default arm; `success|failure)` is two conclusions.
_CASE_ARM = re.compile(r"^\s*([A-Za-z0-9_.|*'\"$!-]+)\)\s*$")
# ... and the SAME head with its body on the line (`success) echo … ;;`), which is
# an honest spelling this pin must not call "no `case` over the outcome".
_INLINE_ARM = re.compile(r"^\s*([A-Za-z0-9_.|*'\"$!-]+)\)\s*(\S.*)$")
# The step's own way of naming the outcome: `${{ steps.ruff.outcome }}` (or
# `.conclusion`), matched structurally so a spacing pass cannot red the pin.
_OUTCOME_REF = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_.-]+)\.(?:outcome|conclusion)\s*\}\}")
# A non-zero exit: `exit 1` and an honest `exit 2` both fail the step; `exit 0`
# and `|| true` do not.
_NONZERO_EXIT = re.compile(r"\bexit\s+[1-9]\d*\b")


def _shell_code(run: str) -> list[str]:
    """``run:`` lines with shell comments removed, TRAILING ones included.

    A `# exit 1` in a comment is not an exit, and the shipped `run:` carries both
    full-line comments that NAME the semantics and trailing ones — so a pin reading
    raw text can be satisfied by prose (`true  # exit 1` exits 0; a summary write
    mentioned only after `#` writes nothing). Quotes are respected, so an `echo
    "# …"` keeps its text.
    """
    lines: list[str] = []
    for raw in run.splitlines():
        quote: str | None = None
        cut = len(raw)
        for index, char in enumerate(raw):
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#" and (index == 0 or raw[index - 1].isspace()):
                cut = index
                break
        line = raw[:cut].rstrip()
        if line:
            lines.append(line)
    return lines


def _case_selector(run: str) -> str | None:
    """The word after ``case`` (up to ``in``) — what the branches actually SELECT on."""
    for line in _shell_code(run):
        match = re.match(r"^\s*case\s+(.*?)\s+in\s*$", line)
        if match:
            return match.group(1).strip()
    return None


def _case_arms(run: str) -> dict[str, list[str]] | None:
    """The arms of the first ``case … esac`` in ``run``, or ``None`` if it has none.

    Keys are the arm's pattern text (`success`, `*`, `failure|error`), values the
    code lines of the arm's body, with the `;;` terminator dropped.
    """
    lines = _shell_code(run)
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*case\s", line)), None)
    if start is None:
        return None
    arms: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        if line.strip() == "esac":
            return arms
        if line.strip() == ";;":
            current = None
            continue
        match = _CASE_ARM.match(line)
        if match:
            current = match.group(1)
            arms.setdefault(current, [])
            continue
        inline = _INLINE_ARM.match(line)
        if inline:
            # `success) echo … ;;` — a compact arm is still an arm. Without this the
            # whole `case` read as unrecognisable and the pin told a correct step it
            # "has no `case … esac` over the outcome" (measured in review cycle 5).
            current = inline.group(1)
            body = inline.group(2).split(";;")[0]
            arms.setdefault(current, []).append(body)
            continue
        if current is not None:
            arms[current].append(line)
    return None  # no `esac`: this is not a `case` this pin can read


# What GitHub actually interpolates into `env:` for each conclusion. The null
# conclusion is the EMPTY STRING (`${{ steps.x.outcome }}` on a skipped step renders
# `""`, never an unset variable); modelling it by omission redded a correct
# `case "${OUTCOME}" in` under `set -u` — measured in review cycle 6.
_VERDICT_OUTCOMES = ("", "success", "failure", "skipped", "weird-conclusion")


def _verdict_behaviour_problem(run: str, outcome_var: str) -> str | None:
    """What the verdict step DOES for each conclusion, executed under bash.

    Text cannot answer the questions this pin asks. Measured in review cycles 4:
    `case "${OUTCOME:-success}" in` mentions the outcome and maps the EMPTY value
    into the PASS arm; `true  # exit 1` contains an exit it never runs; a summary
    write mentioned after `#` writes nothing. Every one of those satisfied a
    text-shaped check while an unmeasured tree finished GREEN. So the step is RUN,
    once per conclusion, and judged on its exit code, its step summary and its
    annotations — the three things a reader of the run actually gets.

    The expected behaviour is the artifact's own contract (#5120):

    * `success` → exit 0, a `PASS` in the summary, a `::notice` annotation;
    * `failure` → exit 0 (the lint step already owns that red), `FAIL`, `::error`;
    * `skipped` / an EMPTY outcome (a null conclusion — GitHub interpolates the
      variable to `""`, it is never unset) / an undocumented conclusion → exit
      NON-ZERO, `NO VERDICT`, `::error` — absence of a verdict is never a pass.
    """
    # `exit_rule`: the exit code a run of this path must have. Only the PASS arm
    # must be 0 and only the unmeasured paths must be non-zero; the FAIL arm may do
    # either (the lint step already owns that red), so it is unconstrained — a pin
    # that reds `failure)` + `exit 1` reports a green tree as red when it is red.
    expected = [
        ("", "nonzero", "NO VERDICT", "::error"),
        ("skipped", "nonzero", "NO VERDICT", "::error"),
        ("weird-conclusion", "nonzero", "NO VERDICT", "::error"),
        ("success", "zero", "PASS", "::notice"),
        ("failure", "any", "FAIL", "::error"),
    ]
    # The matrix must judge EVERY conclusion GitHub can interpolate: a value added
    # to `_VERDICT_OUTCOMES` and forgotten here would skip the matrix silently, which
    # is the fail-open this pin exists for.
    assert {outcome for outcome, *_ in expected} == set(_VERDICT_OUTCOMES), (
        "the polarity matrix no longer covers every conclusion in _VERDICT_OUTCOMES"
    )
    for outcome, exit_rule, verdict, annotation in expected:
        if outcome == "":
            label = (
                "an EMPTY outcome (GitHub's interpolation of a null conclusion) — "
                "the state a skipped or never-reached lint step produces"
            )
        else:
            label = f"outcome {outcome!r}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                summary = Path(tmp, "summary.md")
                summary.write_text("")
                env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": os.environ.get("HOME", "/tmp"),
                    "GITHUB_SHA": "deadbeef",
                    "GITHUB_STEP_SUMMARY": str(summary),
                    # The standard runner variables a verdict summary may name. Without
                    # them an honest enrichment (`… run ${GITHUB_RUN_ID}`) aborts under
                    # `set -u` and the pin reds a CORRECT step, blaming the PASS arm —
                    # measured in review cycle 7.
                    "GITHUB_REPOSITORY": "o/r",
                    "GITHUB_RUN_ID": "1",
                    "GITHUB_RUN_NUMBER": "1",
                    "GITHUB_REF": "refs/heads/main",
                    "GITHUB_REF_NAME": "main",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_WORKFLOW": "main-lint",
                    "GITHUB_ACTOR": "github-actions",
                    "RUNNER_OS": "Linux",
                    # ALWAYS set, empty for a null conclusion: GitHub interpolates
                    # `${{ steps.x.outcome }}` into `env:` as `""`. Modelling it by
                    # omission redded a correct `${OUTCOME}` under `set -u`.
                    outcome_var: outcome,
                }
                # `bash -e`: GitHub runs an unspecified `shell:` on Linux as
                # `bash -e {0}`, so a command that returns non-zero mid-arm ABORTS
                # the real step. Running `bash -c` judged a script GitHub would fail
                # (measured in review cycle 5: an injected `false` exited 0 here and
                # 1 on the runner, with an EMPTY summary either way).
                proc = subprocess.run(
                    ["bash", "-e", "-c", run],
                    cwd=tmp,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                summary_text = summary.read_text()
                output = proc.stdout + proc.stderr
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
            return f"the verdict step could not be executed to be judged: {exc}"
        if exit_rule == "zero" and proc.returncode != 0:
            # The LAST lines of output are named: an unbound runner variable under
            # `set -u` aborts with exit 1, and a message that only says "reports a green
            # tree red" sends the fixer to the PASS arm, which is correct (cycle 7).
            tail = " | ".join(line for line in output.splitlines()[-2:] if line.strip())
            return (
                f"the verdict step EXITS {proc.returncode} on {label} — that path is "
                f"not a failure of the lint gate, so a green tree is reported red "
                f"(last output: {tail[:160]!r})"
            )
        if exit_rule == "nonzero" and proc.returncode == 0:
            return (
                f"the verdict step EXITS 0 on {label} and writes {verdict!r} — an "
                "unmeasured tree finishes the run GREEN with a verdict nobody reads, "
                "which is the #5120 fail-open"
            )
        # The STEP SUMMARY is judged on its own, never on the concatenation: the
        # annotation lines carry the verdict word themselves, so one `echo` to
        # stdout satisfied both checks while the summary stayed EMPTY — the exact
        # #5120 shape, with the pin green (measured in review cycle 5).
        if verdict not in summary_text:
            return (
                f"the verdict step's STEP SUMMARY does not state {verdict!r} for "
                f"{label} (it reads {summary_text.strip()[:80]!r}) — the verdict is "
                "readable only in the log, which is the #5120 shape"
            )
        others = {"PASS", "FAIL", "NO VERDICT"} - {verdict}
        leaked = sorted(word for word in others if word in summary_text)
        if leaked:
            return (
                f"the verdict step's STEP SUMMARY states {leaked} as well as "
                f"{verdict!r} for {label} — a reader cannot tell which verdict this "
                "run has, and an announcement hoisted above the `case` reports PASS "
                "on a failing tree"
            )
        if annotation not in output:
            return (
                f"the verdict step emits no `{annotation}` annotation on {label} — the "
                "run page names neither direction, so a reader sees only a bare exit"
            )
        # The annotation surface is judged for EXCLUSIVITY too: presence alone accepted
        # `::notice … PASS` hoisted above the `case`, so a lint-red (or unmeasured) tree
        # announced PASS beside its FAIL/NO VERDICT annotation — and the hoisted `::error`
        # told every GREEN run that main is lint-red. That is the exact #5120 complaint
        # ("its annotations could not tell the two apart") restored with a green pin
        # (measured in review cycle 9).
        opposite = "::error" if annotation == "::notice" else "::notice"
        if opposite in output:
            return (
                f"the verdict step emits `{opposite}` as well as `{annotation}` on "
                f"{label} — a reader of the annotations cannot tell which verdict this "
                "run has, and an announcement hoisted above the `case` claims the "
                "opposite direction on the surface this PR exists to make readable"
            )
    return None


def _verdict_step_problem(steps: list, ruff_index: int, ruff_id: str) -> str | None:
    """Why the steps after the ruff step do not PUBLISH the verdict, or ``None``.

    #5120 measured the defect this pins: run 40154972406 was red for a real
    finding AND for an inert notification, and its annotations could not tell the
    two apart. The fix is a step that NAMES the verdict (`PASS`/`FAIL`/`NO
    VERDICT`) from the ruff step's own outcome — and the pin has to read it, or a
    tidy-up deletes the whole contribution with every test still green while the
    header claims a test protects it.

    Seven properties are required, each of which a mutant below removes:

    * it is the ONE step deriving ``steps.<ruff>.outcome`` through ``env:`` — the
      notifier keys on it in its ``if:``, and a second reader of the same outcome
      is how a step stops being the thing that reports;
    * it sits AFTER the ruff step (before it, the outcome is null);
    * it carries no ``continue-on-error`` — that would silence the ONE arm that
      must fail (NO VERDICT, an unmeasured tree);
    * its ``if:`` carries a status function that is not ``success()``/``cancelled()``:
      `always()` removes the implicit `success()` that skips it on exactly the
      failing path;
    * the ``run:`` writes ``$GITHUB_STEP_SUMMARY`` and emits BOTH ``::notice`` and
      ``::error`` (comment-stripped text, so prose cannot satisfy it);
    * its `case` SELECTOR reads that outcome variable (a constant selector, or a
      ``${OUTCOME:-success}`` default, publishes PASS without measuring);
    * and — the decisive one — the ``run:`` is EXECUTED once per conclusion
      (``success``, ``failure``, ``skipped``, unset, an undocumented one) and must
      exit 0 with a ``PASS``/``FAIL`` verdict for the measured ones and exit
      NON-ZERO with ``NO VERDICT`` for the rest. Polarity is judged by running it,
      because text cannot answer these questions: `true  # exit 1` contains an exit
      it never runs, and `${OUTCOME:-success}` mentions the outcome while mapping
      the empty value into the PASS arm (both measured in review cycle 4).
    """
    derivers = [
        (index, step)
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and isinstance(step.get("env"), dict)
        and any(
            ruff_id in _OUTCOME_REF.findall(str(value))
            or f"steps['{ruff_id}']" in str(value)
            for value in step["env"].values()
        )
    ]
    if not derivers:
        return (
            f"nothing derives `steps.{ruff_id}.outcome` through `env:` — the step "
            "that PUBLISHES the verdict (#5120) is gone, so a reader is back to "
            "inferring PASS from a green exit code and cannot tell a real finding "
            "from a reporting failure"
        )
    if len(derivers) > 1:
        return (
            f"{len(derivers)} steps derive `steps.{ruff_id}.outcome` through `env:` "
            "— exactly one step publishes the verdict; a second reader is how a "
            "step stops being the thing that reports"
        )
    index, verdict = derivers[0]
    if index < ruff_index:
        return (
            f"the step deriving `steps.{ruff_id}.outcome` sits BEFORE the ruff step "
            "— at that point the outcome is null, so the published verdict is "
            "`NO VERDICT`/empty on every run"
        )
    if not isinstance(verdict.get("run"), str):
        return (
            "the verdict-publishing step has no `run:` — it publishes nothing, and "
            "the verdict a reader needs is back to being inferred from an exit code"
        )
    # ── the step's OTHER keys are pinned too (review cycle 6, F2) ──────────────
    # The execution harness below builds its OWN env and shell and runs the `run:`
    # text, so it is blind to the step's own keys: measured accepted-and-inert were
    # `shell: python`, `shell: echo`, `working-directory: /nonexistent` on this step
    # (the runner never executes the block — no summary, no annotation — while all
    # six executed polarity cases pass) and an `env:` entry named
    # `GITHUB_STEP_SUMMARY` (the real summary stays empty while the pin reads the
    # one it wrote). The ruff step is protected by exactly this allow-list for
    # exactly this reason, so the same discipline applies here.
    unmodelled = sorted(
        key
        for key, value in verdict.items()
        if key not in _RUFF_STEP_KEYS | {"if", "env", "continue-on-error"}
        and not _inert_step_key(key, value)
    )
    if unmodelled:
        return (
            f"the verdict-publishing step carries {unmodelled}, which this pin does "
            "not model. The inert spellings are accepted (`continue-on-error: "
            "false`, `shell: bash|sh`, `working-directory: .`); anything else — "
            "`shell: echo`, a real `working-directory` — stops the runner executing "
            "the block, so no verdict is published while every executed case still "
            "passes. Remove it, or extend `_inert_step_key` with the reason"
        )
    redirected = sorted(
        key
        for key in (verdict.get("env") or {})
        if str(key).strip() == "GITHUB_STEP_SUMMARY"
    )
    if redirected:
        return (
            "the verdict-publishing step sets "
            f"{redirected} in its own `env:` — that diverts the summary the verdict "
            "is published in, so the run page shows nothing while this pin reads the "
            "file the harness wrote"
        )
    coe = verdict.get("continue-on-error")
    if not (
        coe is None
        or coe is False
        or (isinstance(coe, str) and coe.strip().lower() == "false")
    ):
        # `is False` rather than `in (None, False, "false")`: `0` and `0.0` are `== False`
        # in Python, so the membership form accepted a numeric scalar GitHub's schema may
        # reject (an invalid workflow is a run that does not exist — a fail-open).
        # PRESENCE is not the property: `continue-on-error: false` is inert (the
        # same spelling `_inert_step_key` accepts on the ruff step), and a pin that
        # reds it gets deleted by the next tidy-up. Only an ACTIVE silencing value
        # removes the one arm that must fail.
        return (
            "the verdict-publishing step carries `continue-on-error: "
            f"{verdict.get('continue-on-error')}`, which silences the ONE arm that "
            "must fail (NO VERDICT — the lint step did not run). An unmeasured tree "
            "can then end the run GREEN behind an annotation nobody reads"
        )
    condition = str(verdict.get("if", ""))
    status_terms = {f"{name}()" for name in _STATUS_SCAN.findall(condition)}
    if status_terms & {"success()", "cancelled()"}:
        return (
            f"the verdict-publishing step has `if: {condition}` — "
            f"{sorted(status_terms & {'success()', 'cancelled()'})} makes it skip "
            "exactly the paths whose verdict a reader needs (a failed or "
            "unmeasured lint step)"
        )
    if "always()" not in status_terms:
        return (
            f"the verdict-publishing step has `if: {condition or 'none'}` — without "
            "`always()` GitHub prepends an implicit `success()`, so the step is "
            "SKIPPED on the failing path and the header's 'published in BOTH "
            "directions' is false about the artifact"
        )
    # NAMING `always()` is not the property: `always() && false`, `always() && (1 ==
    # 2)` and `always() && <an event filter>` all skip the step on the paths that
    # need a verdict, and each leaves the run green with no verdict at all. The
    # expression must BE `always()` (GitHub's `${{ }}` wrapper is the same pin).
    bare = condition.strip()
    if bare.startswith("${{") and bare.endswith("}}"):
        bare = bare[3:-2].strip()
    if bare != "always()":
        return (
            f"the verdict-publishing step has `if: {condition}` — `always()` must be "
            "the WHOLE condition; an extra conjunct can only narrow it, and one that "
            "is false on the failing path skips the step and leaves the run green "
            "with no verdict at all"
        )
    run = str(verdict.get("run", ""))
    # NEWLINE-joined, never "".join: an anchored (`re.M`) check needs the line
    # starts back, and a bare concatenation made `^` unreachable mid-string — the
    # guard then fired only when the previous line happened to end in a space
    # (measured in review: `set -u\nOUTCOME=success` was ACCEPTED).
    code = "\n".join(_shell_code(run))
    if "GITHUB_STEP_SUMMARY" not in code:
        return (
            "the verdict-publishing step never writes `$GITHUB_STEP_SUMMARY` — the "
            "verdict exists only in the log of a step nobody opens, which is the "
            "inference #5120 exists to remove"
        )
    for command in ("::notice", "::error"):
        if command not in code:
            return (
                f"the verdict-publishing step never emits `{command}` — BOTH "
                "directions must be NAMED, or a passing tree is silent and a failing "
                "one is a bare exit code, which is the #5120 shape"
            )
    outcome_vars = [
        key
        for key, value in (verdict.get("env") or {}).items()
        if ruff_id in _OUTCOME_REF.findall(str(value)) or f"steps['{ruff_id}']" in str(value)
    ]
    # COMMENT-STRIPPED `code`, like every neighbouring check: a `${OUTCOME}` that
    # appears only in a comment satisfies a raw-text test while the `case` is keyed
    # on something else — the check would read prose, not behaviour.
    if not any(f"${{{key}" in code or f"${key}" in code for key in outcome_vars):
        return (
            f"the verdict-publishing step sets {outcome_vars} but never READS it in "
            "its `run:` — the `case` is not keyed on the ruff step's own outcome, "
            "so it publishes whatever the shell last saw"
        )
    selector = _case_selector(run)
    if selector is None or not any(
        f"${{{key}" in selector or f"${key}" in selector for key in outcome_vars
    ):
        # PRESENCE is not the property: the default arm's own message interpolates
        # the variable, so a one-line edit to `case "success" in` leaves every
        # `${OUTCOME}` in place and the step publishes PASS for EVERY conclusion.
        return (
            f"the verdict `case` selector is `{selector}` and does not read "
            f"{outcome_vars} — the outcome is printed but never SELECTED on, so the "
            "verdict is a constant (PASS) even when the lint step failed or never ran"
        )
    for key in outcome_vars:
        # An ASSIGNMENT before the `case` decides the verdict without measuring:
        # `OUTCOME=success` publishes PASS on every run, including a red lint step.
        # `^\s*` because every line of a `run:` block is indented to its base.
        if re.search(rf"^\s*(export\s+)?{re.escape(key)}=", code, re.M):
            return (
                f"the verdict-publishing step ASSIGNS `{key}` in its own `run:` — the "
                "published verdict is then a constant, not the lint step's outcome, "
                "and a red tree can be reported as PASS"
            )
    arms = _case_arms(run)
    if not arms:
        return (
            "the verdict-publishing step has no `case … esac` over the outcome — the "
            "verdict must be selected by an explicit arm per conclusion, not by an "
            "`if` chain whose default is the pass"
        )
    tokens = {pattern: {tok.strip("'\"") for tok in pattern.split("|")} for pattern in arms}
    pass_arms = [pattern for pattern, toks in tokens.items() if toks == {"success"}]
    merged_success = [
        pattern for pattern, toks in tokens.items() if "success" in toks and toks != {"success"}
    ]
    default_arms = [pattern for pattern, toks in tokens.items() if "*" in toks]
    if merged_success:
        # `success|skipped)` makes a PASS and an unmeasured run share one arm; the
        # verdict published for the second one is whatever the first needs.
        return (
            f"the verdict `case` merges the PASS conclusion into {merged_success} — "
            "`success` must have an arm of its own, or a second conclusion is reported "
            "with the PASS verdict"
        )
    if not pass_arms:
        return (
            f"the verdict `case` has no `success)` arm ({sorted(arms)}) — a PASS has "
            "no arm of its own, so `success` falls into whatever the default does "
        )
    if not default_arms:
        return (
            f"the verdict `case` has no `*)` default arm ({sorted(arms)}) — a "
            "conclusion GitHub has not documented yet, and a `skipped` lint step, "
            "then match nothing and the step exits 0 with no verdict published"
        )
    # POLARITY, JUDGED BY RUNNING IT (see `_verdict_behaviour_problem`): "the arm
    # exits non-zero" is not readable from text — `true  # exit 1` contains an exit
    # it never runs, `${OUTCOME:-success}` maps the empty value into PASS, and
    # `echo "not measured"; exit 1` is an honest arm a text pattern rejects. The
    # arm SHAPES are checked above; only the `success` and plain `failure` outcomes
    # may end green, and execution is what decides that.
    return _verdict_behaviour_problem(run, outcome_vars[0])


def _merged_tree_guard_problem(doc: dict) -> str | None:
    """Why ``doc`` is not a guard that RUNS (and still FAILS) on every merged tree.

    Returns the reason instead of asserting so
    :func:`test_the_merged_tree_guard_check_rejects_the_starved_shapes` can prove
    the check still catches each shape on SYNTHETIC mutants of the live artifact:
    a pin whose only evidence is the one file it happens to pass on cannot show
    its teeth, and every shape below is a way to lose the guard while leaving its
    name, trigger and command intact.
    """
    triggers = _triggers(doc)
    if "push" not in triggers:
        return "it does not trigger on `push`, so merged trees are never linted here"
    push = triggers["push"] or {}
    if isinstance(push, dict):
        if "paths" in push or "paths-ignore" in push:
            return (
                "its `push:` trigger is path-filtered — ruff reads the WHOLE tree, "
                "so a merged tree outside those paths is never linted, and that is "
                "the tree that reddens every PR"
            )
        # The trigger must select the MERGED TREES. `branches: [release/*]` and
        # `branches-ignore: [main]` both keep a `push` trigger that never fires on
        # main — the #2174 defect by an edit no other mutant here tests.
        branches = push.get("branches")
        if branches is not None:
            # A YAML SCALAR is iterated CHARACTER BY CHARACTER, so `"main" in
            # branches` becomes a substring test and `branches: main-next` reads as
            # selecting main. `_triggers` already normalises a scalar `on:` for the
            # same reason.
            branches_types = _pattern_list_problem(branches, "branches")
            if branches_types is not None:
                return branches_types
            branches = [branches] if isinstance(branches, str) else list(branches)
            # GitHub reads `!pattern` as NEGATION, so `branches: [main, '!main']`
            # selects nothing — while a membership test sees "main" present.
            negations = [entry for entry in branches if str(entry).startswith("!")]
            if negations:
                return (
                    f"its `push:` trigger carries negated branch patterns {negations} "
                    "— GitHub reads `!…` as an exclusion, so this can select nothing "
                    "while a membership test still sees `main`"
                )
            # Patterns, not literals: `*`, `**` and `main*` all select main, so a
            # broadening must not be redded as "a push to main is never linted".
            if not any(fnmatch.fnmatchcase("main", str(entry)) for entry in branches):
                return (
                    f"its `push:` trigger is limited to {branches}, which does not "
                    "match `main` — the guard's whole subject is the merged tree"
                )
        ignore_raw = push.get("branches-ignore") or []
        ignore_types = _pattern_list_problem(ignore_raw, "branches-ignore")
        if ignore_types is not None:
            return ignore_types
        ignore = [ignore_raw] if isinstance(ignore_raw, str) else [str(entry) for entry in ignore_raw]
        if any(fnmatch.fnmatchcase("main", pattern) for pattern in ignore):
            return (
                "its `push:` trigger EXCLUDES `main` (`branches-ignore`), so the "
                "merged trees it exists to guard are the ones it never sees"
            )
    conc = doc.get("concurrency")
    if isinstance(conc, str):
        # GitHub's documented shorthand: `concurrency: <group>` IS a group with
        # `cancel-in-progress: false` (the same value the workflow documents as
        # load-bearing), and `_concurrency_group` in this module already accepts
        # both spellings. Rejecting it claimed runs were "unbounded", which was
        # false about the artifact.
        conc = {"group": conc, "cancel-in-progress": False}
    if not isinstance(conc, dict):
        return (
            "it declares no `concurrency:`, so its runs are unbounded and one can "
            "still supersede another"
        )
    group = str(conc.get("group", ""))
    # `||` returns the FIRST TRUTHY operand, so a starveable term AHEAD of the sha
    # decides the group: `github.ref || github.sha` resolves to the constant
    # `refs/heads/main`. Only the LEADING term of each expression can make the group
    # unstarveable (the same structural rule `_per_issue_key_problem` uses).
    leads = [expr.split("||")[0].strip() for expr in _EXPRESSION.findall(group)]
    if conc.get("cancel-in-progress") is not False:
        return (
            "its `concurrency:` does not declare `cancel-in-progress: false` — the "
            "value this workflow documents as load-bearing: a half-run guard is not a "
            "reading, and the literal `false` is what the next editor copies"
        )
    if not (_UNSTARVEABLE_GROUP_TERMS & set(leads)):
        return (
            f"its concurrency group {group!r} carries none of "
            f"{sorted(_UNSTARVEABLE_GROUP_TERMS)}. GitHub keeps ONE pending run per "
            "group and CANCELS it when a newer run in the same group is queued, so "
            "on a busy main the guard is skipped silently (measured: 21 of 25 merged "
            "trees). `github.ref` is NOT a key here — on a `push` to main it is the "
            "constant `refs/heads/main`, the same shared group under a name that "
            "looks keyed. Key the group on the pushed sha: one group per tree, "
            "nothing to cancel."
        )
    # `defaults.run` rewrites EVERY `run:` in scope — `defaults: {run: {shell:
    # echo}}` turns `ruff check .` into an echo that exits 0. Named before the
    # key allow-list so the message says WHY, not just "unexpected key".
    defaults_problem = _defaults_problem(doc.get("defaults"))
    if defaults_problem is not None:
        return f"it declares workflow-level `defaults:` and {defaults_problem}"
    # PyYAML resolves the bare `on:` key to the BOOLEAN `True` (YAML 1.1), so the
    # key set is normalised before it is compared with the allow-list.
    keys = {"on" if key is True else key for key in doc}
    unexpected = sorted(keys - _WORKFLOW_KEYS)
    if unexpected:
        return (
            f"it carries workflow-level {unexpected}, which this pin does not model. "
            "Either it is inert (add it to _WORKFLOW_KEYS with that reason) or it is "
            "a key GitHub rejects — in which case no run exists and nothing reports "
            "red (e.g. `continue-on-error` is not a workflow-scope key)"
        )
    jobs = doc.get("jobs") or {}
    if len(jobs) != 1:
        return (
            f"it declares {len(jobs)} jobs; this guard must be exactly ONE. A second "
            "job plus a `needs:` on the guard SKIPS it — every job skipped makes the "
            "run conclude SUCCESS with ruff never having run"
        )
    job_id, job = next(iter(jobs.items()))
    if not isinstance(job, dict):
        return f"job {job_id!r} is not a mapping"
    job_defaults_problem = _defaults_problem(job.get("defaults"))
    if job_defaults_problem is not None:
        return f"the job ({job_id!r}) declares `defaults:` and {job_defaults_problem}"
    if "if" in job or job.get("continue-on-error"):
        return (
            f"the job ({job_id!r}) carries "
            f"{sorted(k for k in ('if', 'continue-on-error') if k in job)}"
            " — a job-level condition or `continue-on-error` skips or silences the "
            "guard exactly as the step-level form does"
        )
    if "concurrency" in job:
        return (
            f"the job ({job_id!r}) declares its own `concurrency:` — job groups share "
            "the SAME repo-global namespace as workflow groups, so a constant job "
            "group is starved by the next push to main exactly like the workflow-level "
            "one this pin rejects, while the per-sha workflow group above still looks "
            "correct"
        )
    # A job GitHub cannot ALLOCATE is the same defect one level down: no run, no
    # red, no comment. `runs-on` must be present and a GitHub-hosted ubuntu image;
    # a self-hosted label that does not exist queues forever.
    runs_on = job.get("runs-on")
    if runs_on is None:
        return (
            f"job {job_id!r} has no `runs-on:` — GitHub rejects the workflow, so no "
            "run exists at all and nothing reports red"
        )
    for where, value in [("the job", job.get("timeout-minutes"))] + [
        (f"step {index}", step.get("timeout-minutes"))
        for index, step in enumerate(job.get("steps") or [])
        if isinstance(step, dict)
    ]:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 360:
            return (
                f"{where} declares `timeout-minutes: {value!r}` — GitHub rejects a "
                "non-positive or non-integer timeout, so the workflow is invalid and "
                "no run exists to report red"
            )
    runs_on_labels = runs_on if isinstance(runs_on, list) else [runs_on]
    if not runs_on_labels or not all(
        isinstance(value, str) and value.startswith("ubuntu-") for value in runs_on_labels
    ):
        return (
            f"job {job_id!r} runs on {runs_on!r}; this guard must run on a "
            "GitHub-hosted `ubuntu-*` image. A missing or self-hosted label queues "
            "forever, which is the same 'the gate did not run' outcome as the "
            "cancellation this pin exists for"
        )
    unexpected_job = sorted(set(job) - _JOB_KEYS)
    if unexpected_job:
        return (
            f"the job ({job_id!r}) carries {unexpected_job}, which this pin does not "
            "model — `needs` is the load-bearing one: an upstream skip skips the "
            "guard, and an all-skipped run concludes SUCCESS"
        )
    # `ruff check .` on an EMPTY workspace prints "No Python files found" and exits
    # 0 — verified — so a guard that never checked out the tree is GREEN having
    # measured nothing. That is the silent-pass this whole pin exists for, so the
    # checkout is part of the guard's shape, not an implementation detail.
    ruff_index = next(
        (
            index
            for index, step in enumerate(job.get("steps") or [])
            if isinstance(step, dict)
            and isinstance(step.get("run"), str)
            and "ruff check" in step["run"]
        ),
        None,
    )
    if ruff_index is not None:
        checkouts = [
            step
            for step in (job.get("steps") or [])[:ruff_index]
            if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        if not checkouts:
            return (
                "no `actions/checkout` step runs BEFORE the guard — `ruff check .` on "
                "an empty workspace exits 0, so the guard would be green having "
                "measured nothing (the silent pass this pin exists for)"
            )
        if len(checkouts) > 1:
            return (
                f"{len(checkouts)} `actions/checkout` steps run before the guard — a "
                "second checkout re-points the workspace, so the tree ruff measures is "
                "not necessarily the pinned one"
            )
        # EVERY checkout before the guard is checked, not just the first: a second
        # one re-points the tree at a moving ref while the first stays correct.
        for checkout in checkouts:
            if "if" in checkout or checkout.get("continue-on-error"):
                return (
                    "the `actions/checkout` step before the guard carries "
                    f"{sorted(k for k in ('if', 'continue-on-error') if k in checkout)} — a "
                    "skipped checkout leaves an empty tree that ruff passes"
                )
            with_block = checkout.get("with") or {}
            # GitHub ignores the spacing inside `${{ }}`, and this module already
            # treats `sha-spaced`/`sha-unspaced` as the same group key; the ref must
            # be compared the same way or a format pass reds a correct artifact.
            if not re.fullmatch(r"\$\{\{\s*github\.sha\s*\}\}", str(with_block.get("ref", "")).strip()):
                return (
                    "an `actions/checkout` step before the guard does not pin "
                    "`with.ref: ${{ github.sha }}` — the workflow's own comment claims a "
                    "deterministic tree ('mid-run main advance must not move the tree'), "
                    "and it is unchecked"
                )
            # `sparse-checkout` leaves a NARROWED working tree (a finding outside the
            # cone is invisible, `ruff check .` exits 0) and `repository` checks out a
            # different repo entirely — both measure a tree main never had. The keys
            # that can change WHICH tree lands are excluded by allow-list, not by
            # naming the two that exist today.
            unexpected_with = sorted(set(with_block) - _CHECKOUT_WITH_KEYS)
            if unexpected_with:
                return (
                    f"the checkout's `with:` carries {unexpected_with}, which can change "
                    "WHICH tree ruff measures (`sparse-checkout` narrows it, "
                    "`repository` replaces it) while `ruff check .` still exits 0. "
                    f"Allowed: {sorted(_CHECKOUT_WITH_KEYS)}"
                )
    ruff_steps = [
        (idx, step)
        for idx, step in enumerate(job.get("steps") or [])
        if isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and "ruff check" in step["run"]
    ]
    if not ruff_steps:
        return (
            "no step runs `ruff check .` — the FULL tree is what this guard exists "
            "for; a narrowed selection (e.g. `--select E9`) or a dropped step leaves "
            "the rule surface on main unguarded while the check stays green"
        )
    for idx, step in ruff_steps:
        argv_problem = _ruff_argv_problem(step["run"])
        if argv_problem is not None:
            return (
                f"step {step.get('name') or step.get('id') or idx!r} runs "
                f"{step['run'].strip()!r}: {argv_problem}"
            )
        extra = sorted(
            key
            for key, value in step.items()
            if key not in _RUFF_STEP_KEYS and not _inert_step_key(key, value)
        )
        if extra:
            return (
                f"the ruff step carries {extra}, which this pin does not model. The "
                "inert spellings are accepted (`continue-on-error: false`, "
                "`shell: bash|sh`, `working-directory: .`, an empty `env:`); anything "
                "else — `if:`, `continue-on-error: true`, `shell: echo`, a real "
                "`working-directory` — can make a finding not fail the job. Remove it, "
                "or extend `_inert_step_key` with the reason"
            )
        step_id = step.get("id")
        if not step_id:
            return (
                "the ruff step has no `id:` — the failure notification keys on "
                "`steps.<id>.outcome`, so without it the merged PR is never told that "
                "main went lint-red (the silent-main class this guard exists for)"
            )
        notifiers = [
            (index, other)
            for index, other in enumerate(job.get("steps") or [])
            if isinstance(other, dict)
            and (
                f"steps.{step_id}." in str(other.get("if", ""))
                or f"steps['{step_id}']" in str(other.get("if", ""))
            )
        ]
        if not notifiers:
            return (
                f"nothing keys on `steps.{step_id}.outcome` — the attribution step "
                "that tells the merged PR its lint broke main is gone, so the guard "
                "reports red into a run nobody is watching"
            )
        if len(notifiers) > 1:
            return (
                f"{len(notifiers)} steps key on `steps.{step_id}.outcome` in their "
                "`if:` — exactly ONE may, because a second keyed step is how the "
                "notification stops being the thing that notifies"
            )
        for index, _other in notifiers:
            if index < idx:
                return (
                    f"the step keyed on `steps.{step_id}.outcome` sits BEFORE the ruff "
                    "step — at that point the outcome is null, so the condition is "
                    "never true and the notification cannot fire"
                )
        for _, other in notifiers:
            if "uses" not in other and "run" not in other:
                return (
                    f"the step keyed on `steps.{step_id}.outcome` has neither `uses:` "
                    "nor `run:` — GitHub rejects the workflow, so the guard never runs"
                )
            if not str(other.get("uses", "")).startswith("actions/github-script@"):
                return (
                    f"the step keyed on `steps.{step_id}.outcome` is "
                    f"`{other.get('uses') or other.get('run')!s}` — it must be the "
                    "github-script step that POSTS the comment; any other step "
                    "satisfies the shape while notifying nobody"
                )
            if other.get("continue-on-error") is not True:
                return (
                    f"the github-script step keyed on `steps.{step_id}.outcome` does "
                    f"not carry `continue-on-error: true` (it has "
                    f"{other.get('continue-on-error')!r}) — a reporter that throws "
                    "before its own try/catch then REDS the job, so the notification "
                    "changes the verdict, which is the direction #5120 measured"
                )
            # Comments AND string/template literals are stripped: the membership
            # test was satisfied by `core.info("see the issues.createComment docs")`,
            # a valid no-op — the same class as the commented-out call, one spelling
            # further on. What remains is code.
            raw_script = _JS_COMMENTS.sub(
                "", str((other.get("with") or {}).get("script", ""))
            )
            script = _JS_LITERALS.sub("", raw_script)
            if "issues.createComment" not in script:
                return (
                    f"the github-script step keyed on `steps.{step_id}.outcome` never "
                    "calls `issues.createComment` — the notification the guard exists "
                    "for is not in it"
                )
            # ── the WRITE is GATED, not merely present (review cycle 6, F1) ────
            # Deleting the non-push guard left this suite green: a `workflow_dispatch`
            # on a branch resolves to that branch's OPEN PR, so the body below — "This
            # PR **failed post-merge ruff lint on main** … Main is now lint-red" — is
            # posted onto an UNMERGED PR, and the `commit <sha>` marker makes it
            # idempotent, so no re-run corrects it. Presence of the call is not the
            # property; the call has to be behind the gate.
            write_at = script.find("issues.createComment")
            gate_at = script.find("context.eventName")
            if gate_at == -1 or gate_at > write_at:
                return (
                    f"the github-script step keyed on `steps.{step_id}.outcome` checks "
                    "`context.eventName` nowhere before it POSTS (or only after the "
                    "call) — a `workflow_dispatch` run on a branch then posts \"main is "
                    "lint-red\" onto that branch's OPEN pull request, permanently: the "
                    "body claims a post-merge failure of a PR that has not merged"
                )
            if "return" not in script[gate_at:write_at]:
                return (
                    f"the github-script step keyed on `steps.{step_id}.outcome` tests "
                    "`context.eventName` but never RETURNS between that test and the "
                    "POST — naming the event without acting on it gates nothing"
                )
            # ⛔ The block above is satisfiable by the WRONG `return` (measured in review
            # cycle 7: `!target` and `already` both sit between the two offsets), so the
            # GUARD BLOCK itself is read, on comment-stripped text with STRING LITERALS
            # KEPT — `_JS_LITERALS` strips `'push'`, and a polarity test on stripped text
            # cannot see which side of the comparison it is on. Two evasions, both measured
            # accepted before this: deleting only the guard's `return;` (it logs, then falls
            # through to the POST), and inverting the test to `=== 'push'` (it then returns
            # on every real push — main goes red in silence — and posts on a dispatch).
            gate_lines = raw_script.splitlines()
            guard_start = next(
                (i for i, line in enumerate(gate_lines) if "context.eventName" in line), None
            )
            if guard_start is not None:
                guard_end = next(
                    (
                        i
                        for i in range(guard_start + 1, len(gate_lines))
                        if gate_lines[i].strip() == "}"
                    ),
                    None,
                )
                if guard_end is not None:
                    if not re.search(
                        r"context\.eventName\s*!==?\s*(['\"])push\1", gate_lines[guard_start]
                    ):
                        return (
                            f"the github-script step keyed on `steps.{step_id}.outcome` "
                            f"tests `{gate_lines[guard_start].strip()}` — not `context."
                            "eventName !== 'push'`. An INVERTED (or otherwise mismatched) "
                            "test returns on every real push, so a red main is reported "
                            "to nobody, and posts on the dispatch path that must not "
                            "notify at all"
                        )
                    # ⛔ The body of this test is read from the LITERALS-STRIPPED
                    # `script`, NOT from `raw_script`. A `return` inside string
                    # content satisfies a substring test over `raw_script` — and the
                    # shipped log line is one word away from the hole (`not
                    # notifying`), so deleting the guard's own `return` while saying
                    # "returning early" in the message kept this suite GREEN: the
                    # exact fail-open this block exists to catch (review cycle 7).
                    # `_JS_LITERALS.sub` can remove newlines, so the indices are NOT
                    # shared with `gate_lines` — the window is re-derived here.
                    code_lines = script.splitlines()
                    code_start = next(
                        (
                            i
                            for i, line in enumerate(code_lines)
                            if "context.eventName" in line
                        ),
                        None,
                    )
                    code_end = (
                        next(
                            (
                                i
                                for i in range(code_start + 1, len(code_lines))
                                if code_lines[i].strip() == "}"
                            ),
                            None,
                        )
                        if code_start is not None
                        else None
                    )
                    if code_start is not None and code_end is not None:
                        guard_body = "\n".join(code_lines[code_start : code_end + 1])
                        if "return" not in guard_body:
                            return (
                                f"the github-script step keyed on `steps.{step_id}.outcome` "
                                "guards on `context.eventName` but its guard body never "
                                "RETURNS — it logs and falls through to the POST, so a "
                                "dispatch on a branch posts \"main is lint-red\" onto that "
                                "branch's OPEN pull request"
                            )
            # Property 3 is claimed for this step, so it is asserted here: the two
            # datums the 2026-10-25 lane could not recover are the ENDPOINT and the
            # HTTP STATUS, and a warning that drops `err.status` is that failure again.
            # Read with string literals KEPT (the interpolation lives in a template
            # literal, which `script` strips) and comments removed (prose is not code).
            if "err.status" not in raw_script:
                return (
                    f"the github-script step keyed on `steps.{step_id}.outcome` reports "
                    "failures without `err.status` — the next 403 yields "
                    "`Resource not accessible by integration` with no HTTP status, "
                    "which is the datum #5120 could not recover from"
                )
            outcome_term = f"steps.{step_id}.outcome == 'failure'"
            condition_text = str(other["if"])
            actual_terms = _condition_terms(condition_text)
            # The status function is detected by SCAN, not by standalone-conjunct
            # equality: `(failure() && …)` and a conjunct written inside a larger
            # group are the same pin, and the old test redded them as "no status
            # function", which was false about the artifact.
            status_terms = {f"{name}()" for name in _STATUS_SCAN.findall(condition_text)}
            if "||" in condition_text:
                return (
                    f"the step keyed on `steps.{step_id}.outcome` has "
                    f"`if: {condition_text}` — a DISJUNCTION (`||`) fires on paths the "
                    "pinned conjunction does not (an earlier step's failure, for one), "
                    "so it is not the notification this pin requires"
                )
            others = sorted(actual_terms - _STATUS_FUNCTIONS - {outcome_term})
            # `failure()` and `always()` both remove the implicit `success()` — which
            # is the ONLY thing required of the status slot — and ANDed with the
            # outcome conjunct they fire on exactly the failing path. `success()` and
            # `cancelled()` make it never fire on that path.
            if status_terms & {"success()", "cancelled()"}:
                return (
                    f"the step keyed on `steps.{step_id}.outcome` has "
                    f"`if: {other['if']}` — {sorted(status_terms & {'success()', 'cancelled()'})} "
                    "makes it never run on a failed ruff step, so the failure is "
                    "reported into a run nobody is watching"
                )
            if not status_terms:
                return (
                    f"the step keyed on `steps.{step_id}.outcome` has "
                    f"`if: {other['if']}` — with no status function GitHub prepends an "
                    "implicit `success()`, which is false once the ruff step failed, "
                    "so this step is SKIPPED on exactly the path it exists for "
                    "(measured on run 39734942845: step 6 skipped, step 5 ruff failed)"
                )
            if outcome_term not in actual_terms or others:
                extra = others
                missing = [] if outcome_term in actual_terms else [outcome_term]
                return (
                    f"the step keyed on `steps.{step_id}.outcome` has "
                    f"`if: {other['if']}`, which is not the pinned condition: "
                    f"extra {extra or 'none'}, missing {missing or 'none'}. An extra "
                    "conjunct (a constant, another event filter) can only narrow it — "
                    "a step that never fires notifies nobody"
                )
        # ── #5120: the VERDICT is published by its own step, and the pin reads it ──
        # The notification and the verdict are two different readers of the same
        # outcome: the notifier keys on it in its `if:`, the verdict step derives it
        # through `env:`. Both are pinned here, or the header's claim ("the first
        # two properties are asserted by this pin") is false about the artifact —
        # deleting the whole verdict step left this suite green.
        verdict_problem = _verdict_step_problem(job.get("steps") or [], idx, step_id)
        if verdict_problem is not None:
            return verdict_problem
        # The install must be in THIS job, BEFORE the ruff step, and the LAST word:
        # a decoy pinned install in another job (or after the guard) leaves the real
        # one bare, and a second unpinned install replaces a pinned one (pip is
        # last-write-wins). Both are the #1685 drift class.
        earlier = [
            s.get("run")
            for s in (job.get("steps") or [])[:idx]
            if isinstance(s, dict) and isinstance(s.get("run"), str)
        ]
        pin = _dev_ruff_pin()
        install_problem = _pinned_install_problem(earlier, pin)
        if install_problem is not None:
            return install_problem
    # The failure notification is the whole point of "main went red": without
    # `issues: write`, `createComment` 403s on a push event (the default token is
    # read-only), and `pull-requests` is what the attribution lookup needs.
    # A job-level `permissions:` REPLACES the workflow level wholesale.
    # PRESENCE, not truthiness: a job-level `permissions: {}` is GitHub's idiom for
    # "grant nothing" and REPLACES the workflow grant — `or` read it as absent.
    perms = job["permissions"] if "permissions" in job else doc.get("permissions", {})
    if isinstance(perms, str):
        # GitHub accepts `read-all` / `write-all` at this scope. `write-all` grants
        # strictly more than the pin needs (so the comment works); `read-all` grants
        # no `issues: write`, so it 403s on a push event; anything else is invalid
        # and means no run exists at all — three different facts, three messages.
        if perms == "write-all":
            return None
        if perms == "read-all":
            return (
                "its `permissions: read-all` grants no `issues: write`, so "
                "`issues.createComment` 403s on a push event (the default token is "
                "read-only) and a lint-red main is reported into a run nobody sees"
            )
        return (
            f"its `permissions: {perms}` is not a form GitHub accepts — the workflow "
            "is invalid, so no run exists at all and nothing reports red"
        )
    if not isinstance(perms, dict):
        return (
            f"its `permissions:` block is {perms!r}, which is not a form GitHub accepts "
            "— the workflow is invalid, so no run exists at all and nothing reports red"
        )
    # `pull-requests: read` was the whole requirement until #5120, on the reading
    # that `issues: write` covers `issues.createComment`. MEASURED, 2026-10-25 (run
    # 40154972406, `main @ 0cbeb1918`): the job's token DID hold `Issues: write` —
    # the runner prints the granted scopes — and the notification still answered
    # "Resource not accessible by integration". ⚠️ What is NOT established is the
    # MECHANISM: the old step wrapped `issues.listComments` AND
    # `issues.createComment` in ONE catch, so the log does not record which call
    # 403'd, and GitHub's own contract for POST /issues/{n}/comments lists "Issues
    # (write)" as sufficient ("at least one of: Issues write, Pull requests
    # write", docs.github.com/en/rest/issues/comments). The measurement and the doc
    # reading disagree, so this pin requires the scope the measurement says was
    # missing and does NOT pretend to know which of the two is redundant: the
    # reporter names the failing call and its status, and the first successful
    # comment on a real PR is what settles it. `write` implies `read`, so either
    # level satisfies the attribution lookup and only `write` can satisfy a comment.
    missing = [
        f"{scope}: {level}"
        for scope, level in (
            ("issues", "write"),
            ("contents", "read"),
        )
        if perms.get(scope) != level
    ]
    pull_requests = perms.get("pull-requests")
    if pull_requests != "write":
        # ONE message for both sub-cases (key absent, and key `read`): they have the
        # same fix, and a first message naming `read` would hand the fixer the exact
        # value this requirement rejects — the shipped-and-inert #5120 shape.
        missing.append(
            "pull-requests: write (it implies the read the attribution lookup needs; "
            "MEASURED, #5120: the notification still answered 'Resource not accessible "
            "by integration' on a token that held `issues: write`. WHICH call 403'd is "
            "unrecorded — the step wrapped listComments and createComment in one catch "
            "— so this requirement is what the measurement says was missing, not a "
            "settled mechanism; do not drop `issues: write` on the strength of it)"
        )
    if missing:
        return (
            f"its effective permissions are {perms} and omit {missing} — "
            "`issues.createComment` 403s without the scope the endpoint actually "
            "reads on a pull request, so a lint-red main is reported into a run "
            "nobody sees"
        )
    return None


def _merged_tree_guard_registration_problem(registry: dict, pmv_jobs: dict) -> str | None:
    """Why the merged-tree guard's REGISTRATION does not hold, or ``None``.

    Split out of the test that asserts it so the REJECTION is provable against
    the regressions themselves (see the mutants below) instead of only asserted
    about today's tree. Without that, the entry's only reader is a
    parametrization over the entry's own keys: an EMPTY registry expands to ZERO
    cases, pytest emits a `PytestCollectionWarning` (not a failure), and the
    module stays green — while every live-artifact assertion that matters (the
    per-SHA group, the exact `ruff check .`, the pinned ruff install) lives
    inside that body. Deleting the entry would therefore re-open #2174 silently,
    which is the "a gate that did not exist" class one level up.
    """
    if "main-lint.yml" not in registry:
        return (
            "its registry entry was REMOVED. MERGED_TREE_GUARD is the only thing "
            "that makes main-lint.yml's per-SHA concurrency group, its exact "
            "`ruff check .` and its pinned ruff install ASSERTED — with the entry "
            "gone the parametrization yields no cases, so the guard can be "
            "starved again (4 of 25 merged trees ran it) with this module green"
        )
    if not str(registry["main-lint.yml"]).strip():
        return (
            "its registry entry carries no reason. An unargued entry is a stamp: "
            "the next edit cannot tell a reviewed decision from a leftover key"
        )
    if "lint" in pmv_jobs:
        return (
            "post-merge-validation.yml carries a `lint` job again — its constant "
            "`concurrency.group` starves it (21 of 25 merged trees never ran it), "
            "and re-adding it beside the working guard restores the starved path: "
            "it looks like redundancy and is a silent gap, because a green "
            "counterexample proves nothing when the other one is cancelled"
        )
    return None


_REGISTRATION_GAPS = [
    # The exact regression the reviewer measured as silently re-openable: drop the
    # entry, revert main-lint.yml's group, and a parametrized-only pin stays green.
    ("the registry entry was deleted", {}, {}),
    ("the entry carries no reason", {"main-lint.yml": "   "}, {}),
    (
        "the guard was restored beside the working one",
        MERGED_TREE_GUARD,
        {"dedup-check": {}, "validate": {}, "lint": {}},
    ),
]


@pytest.mark.parametrize(
    "label,registry,pmv_jobs",
    _REGISTRATION_GAPS,
    ids=[label for label, _, _ in _REGISTRATION_GAPS],
)
def test_the_merged_tree_guard_registration_check_rejects_the_gaps(
    label: str, registry: dict, pmv_jobs: dict
) -> None:
    """The registration pin is not vacuous — the gaps it exists for are rejected."""
    problem = _merged_tree_guard_registration_problem(registry, pmv_jobs)
    assert problem is not None, (
        f"{label!r} is a way to lose the merged-tree guard (#2174) and the "
        "registration check accepted it — main could go lint-red again with no "
        "gate reporting it"
    )


def test_the_merged_tree_guard_is_registered() -> None:
    """The pin itself is pinned: an emptied registry must red HERE, not vanish.

    Non-parametrized ON PURPOSE. This is the assertion that survives deleting the
    entry — the parametrized test below cannot: with an empty ``MERGED_TREE_GUARD``
    it collects zero cases and passes vacuously. The "ONE home" property is
    asserted here too, so it stays alive independently of the dict's contents.
    """
    assert set(MERGED_TREE_GUARD) == {"main-lint.yml"}, (
        "MERGED_TREE_GUARD must name exactly {'main-lint.yml'}: got "
        f"{sorted(MERGED_TREE_GUARD)}. Empty means the per-SHA-group pin and the "
        "one-home pin both stopped being asserted."
    )
    pmv_jobs = _load(WORKFLOW_DIR / "post-merge-validation.yml").get("jobs") or {}
    problem = _merged_tree_guard_registration_problem(MERGED_TREE_GUARD, pmv_jobs)
    assert problem is None, (
        "the merged-tree guard (#2174) is not correctly registered, and " + problem
    )


@pytest.mark.parametrize("name", sorted(MERGED_TREE_GUARD))
def test_the_merged_tree_guard_runs_on_every_merged_tree(name: str) -> None:
    """The live artifact satisfies the pin (registration itself: see above)."""
    assert MERGED_TREE_GUARD[name].strip(), "an allow-list entry is a reviewed decision"
    path = WORKFLOW_DIR / name
    assert path.exists(), f"MERGED_TREE_GUARD names a missing workflow: {name}"
    doc = _load(path)
    assert name in UNFILTERED_PUSH_OK, (
        f"{name} is the merged-tree guard, so it cannot be path-scoped — declare "
        "it in UNFILTERED_PUSH_OK with the reason, or it is not a guard on every tree"
    )
    problem = _merged_tree_guard_problem(doc)
    assert problem is None, (
        f"{name} is the guard that keeps a lint-red main from reddening every PR's "
        f"merge ref (#2174), and {problem}"
    )
    # The "ONE home" assertion this block used to carry now lives in
    # ``test_the_merged_tree_guard_is_registered`` — it has to outlive the dict, or
    # emptying MERGED_TREE_GUARD unasserts it along with everything else.


def test_the_pr_and_main_lint_pins_agree() -> None:
    """PR lint and main lint must install the SAME ruff, or they disagree on findings.

    Three installs pin ruff: `agent-infra`'s reusable workflow (external repo, so
    not readable from here), `ci.yml`'s `lint-command` for the PR job, and
    `main-lint.yml`. The external one moves by tag bump; the two in this repo are
    coupled to pyproject here, so a bump cannot land in one leg only — which is
    the #1685 class (an unpinned/differently-pinned leg reports a different set of
    findings for the same tree).
    """
    pin = _dev_ruff_pin()
    ci = _load(WORKFLOW_DIR / "ci.yml")
    job = (ci.get("jobs") or {}).get("agent-infra-ci") or {}
    command = str((job.get("with") or {}).get("lint-command", ""))
    specs = _ruff_install_specs(command)
    assert specs == [f"ruff=={pin}"], (
        f"ci.yml's `agent-infra-ci.with.lint-command` is {command!r} and does not "
        f"install the pyproject pin `ruff=={pin}` — PR lint would then run a "
        "different ruff than main lint and than `uv sync` (#1685)"
    )


def _main_lint_mutant(mutate) -> dict:
    """The LIVE guard workflow with one property broken, for the mutational proof."""
    doc = deepcopy(_load(WORKFLOW_DIR / "main-lint.yml"))
    mutate(doc)
    return doc


def _trigger_block(doc: dict) -> dict:
    """The `on:` mapping, whichever key PyYAML resolved it to (YAML 1.1 `True`)."""
    return doc["on"] if "on" in doc else doc[True]


def _ruff_step(doc: dict) -> dict:
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "ruff check" in step["run"]:
                return step
    raise AssertionError("the live artifact lost its ruff step — the mutant is unbuildable")


def _set_narrowed_ruff(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check --select E9 ."


def _set_shell_escaped_ruff(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . || true"


def _set_continue_on_error(doc: dict) -> None:
    _ruff_step(doc)["continue-on-error"] = True


def _set_step_never_runs(doc: dict) -> None:
    _ruff_step(doc)["if"] = "false"


def _set_job_skips(doc: dict) -> None:
    for job in doc["jobs"].values():
        job["if"] = "false"


def _set_unpinned_ruff(doc: dict) -> None:
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
                step["run"] = "pip install ruff"


def _set_output_format_space(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check --output-format github ."


def _set_statistics(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . --statistics"


def _set_env_prefix(doc: dict) -> None:
    _ruff_step(doc)["run"] = "RUFF_CACHE_DIR=/tmp/r ruff check ."


def _set_uv_sync(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = "uv sync"


def _set_inert_defaults(doc: dict) -> None:
    doc["defaults"] = {"run": {"shell": "bash"}}
    doc["jobs"]["lint"]["defaults"] = {"run": {"working-directory": "."}}


def _set_notifier_reordered(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"steps.{step_id}.outcome == 'failure' && failure()"


def _set_uv_run(doc: dict) -> None:
    _ruff_step(doc)["run"] = "uv run ruff check ."


def _set_redirect_stderr(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . 2>&1"


def _set_output_format_before_path(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check --output-format=github ."


def _set_no_cache_quiet(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check --no-cache -q ."


def _set_quiet_install(doc: dict) -> None:
    pin = _dev_ruff_pin()
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
                step["run"] = f"pip install -q ruff=={pin}"


def _set_upgrade_install(doc: dict) -> None:
    pin = _dev_ruff_pin()
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
                step["run"] = f"pip install --upgrade ruff=={pin}"


def _set_quoted_install(doc: dict) -> None:
    pin = _dev_ruff_pin()
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
                step["run"] = f"pip install 'ruff=={pin}'"


def _set_exit_zero(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . --exit-zero"


def _set_selected_rules(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . --select E9"


def _set_output_format(doc: dict) -> None:
    """A NON-narrowing edit: inline annotations are the standard CI presentation."""
    _ruff_step(doc)["run"] = "ruff check . --output-format=github"


def _set_workflow_defaults(doc: dict) -> None:
    doc["defaults"] = {"run": {"shell": "echo"}}


def _set_shell_default(doc: dict) -> None:
    for job in doc["jobs"].values():
        job["defaults"] = {"run": {"shell": "echo"}}


def _set_working_directory_default(doc: dict) -> None:
    for job in doc["jobs"].values():
        job["defaults"] = {"run": {"working-directory": "docs"}}


def _set_job_concurrency(doc: dict) -> None:
    for job in doc["jobs"].values():
        job["concurrency"] = {"group": "main-lint-shared", "cancel-in-progress": True}


def _set_decoy_commented_install(doc: dict) -> None:
    """A COMMENTED pinned install next to a bare real one read as pinned to a regex."""
    steps = doc["jobs"]["lint"]["steps"]
    steps.append({"name": "decoy", "run": "# pip install ruff==0.16.4"})
    for step in steps:
        if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
            step["run"] = "pip install ruff"


def _set_decoy_later_install(doc: dict) -> None:
    """The pinned install moved AFTER the ruff step: the guard already ran bare."""
    steps = doc["jobs"]["lint"]["steps"]
    for step in steps:
        if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]:
            step["run"] = "pip install ruff"
    steps.append({"name": "too late", "run": "pip install ruff==" + _dev_ruff_pin()})


def _set_extra_job_with_needs(doc: dict) -> None:
    """A skipped `needs` skips the guard; all jobs skipped ⇒ the run SUCCEEDS."""
    doc["jobs"]["gate"] = {"runs-on": "ubuntu-latest", "if": "false", "steps": [{"run": "true"}]}
    doc["jobs"]["lint"]["needs"] = "gate"


def _set_workflow_continue_on_error(doc: dict) -> None:
    """Not a workflow-scope key: GitHub rejects the file, so no run exists at all."""
    doc["continue-on-error"] = True


def _set_ruff_step_without_id(doc: dict) -> None:
    _ruff_step(doc).pop("id")


def _set_notifier_without_failure(doc: dict) -> None:
    """The inherited defect: implicit `success()` skips the step on the failure path."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"steps.{step_id}.outcome == 'failure'"


def _set_pipe(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check . | tee out.txt"


def _set_dot_slash(doc: dict) -> None:
    _ruff_step(doc)["run"] = "ruff check ./"


def _set_second_unpinned_install_step(doc: dict) -> None:
    """pip is last-write-wins: a later bare install replaces the pinned one."""
    steps = doc["jobs"]["lint"]["steps"]
    index = next(i for i, s in enumerate(steps) if "pip install ruff==" in str(s.get("run")))
    steps.insert(index + 1, {"name": "hotfix ruff", "run": "pip install ruff"})


def _set_second_unpinned_install_line(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = step["run"] + "\npip install ruff"


def _set_second_pinned_other_version(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = step["run"] + "\npip install ruff==0.0.1"


def _set_no_checkout(doc: dict) -> None:
    steps = doc["jobs"]["lint"]["steps"]
    doc["jobs"]["lint"]["steps"] = [
        s for s in steps if not str(s.get("uses", "")).startswith("actions/checkout@")
    ]


def _set_checkout_skipped(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step["if"] = "false"


def _set_checkout_moving_ref(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step["with"] = {"ref": "main"}


def _set_empty_job_permissions(doc: dict) -> None:
    """`permissions: {}` is GitHub's idiom for GRANT NOTHING, not for 'unset'."""
    doc["jobs"]["lint"]["permissions"] = {}


def _set_notifier_before_ruff(doc: dict) -> None:
    steps = doc["jobs"]["lint"]["steps"]
    step_id = _ruff_step(doc)["id"]
    index = next(
        i
        for i, s in enumerate(steps)
        if isinstance(s, dict) and f"steps.{step_id}." in str(s.get("if", ""))
    )
    notifier = steps.pop(index)
    ruff_index = next(i for i, s in enumerate(steps) if s.get("id") == step_id)
    steps.insert(ruff_index, notifier)


def _set_notifier_wrong_action(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["uses"] = "actions/upload-artifact@v4"
            step.pop("with", None)


def _set_notifier_without_comment(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["with"] = {"script": "return;\n"}


def _set_branches_negated_main(doc: dict) -> None:
    _trigger_block(doc)["push"]["branches"] = ["main", "!main"]


def _set_starveable_leading_term(doc: dict) -> None:
    """`||` returns the FIRST truthy operand: a constant ahead of the sha wins."""
    doc["concurrency"]["group"] = "main-lint-${{ github.ref || github.sha }}"


def _set_scalar_branches_ignore(doc: dict) -> None:
    """A YAML scalar is iterated character by character — main is excluded."""
    push = _trigger_block(doc)["push"]
    push.pop("branches", None)
    push["branches-ignore"] = "main"


def _set_scalar_branches_other(doc: dict) -> None:
    _trigger_block(doc)["push"]["branches"] = "main-next"


def _set_commented_create_comment(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            script = step["with"]["script"].replace(
                "await github.rest.issues.createComment", "// await github.rest.issues.createComment"
            )
            step["with"]["script"] = script


def _set_string_literal_create_comment(doc: dict) -> None:
    """A quoted mention is not a call — the no-op that notifies nobody."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["with"]["script"] = 'core.info("see the issues.createComment docs");\nreturn;\n'


def _set_mapping_branches(doc: dict) -> None:
    """A mapping is iterated as its keys; GitHub rejects it outright."""
    _trigger_block(doc)["push"]["branches"] = {"main": None}


def _set_int_branches(doc: dict) -> None:
    _trigger_block(doc)["push"]["branches"] = 123


def _set_bare_string_concurrency(doc: dict) -> None:
    """The docs' own shorthand, with the same per-sha group — must be ACCEPTED."""
    doc["concurrency"] = "main-lint-${{ github.sha }}"


def _set_notifier_disjunction(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"failure() || steps.{step_id}.outcome == 'failure'"


def _set_glob_branches(doc: dict) -> None:
    """`*` selects main too — must be ACCEPTED."""
    _trigger_block(doc)["push"]["branches"] = ["*"]


def _set_whole_condition_parens(doc: dict) -> None:
    """The whole condition wrapped is the same condition — must be ACCEPTED."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"(failure() && steps.{step_id}.outcome == 'failure')"


def _set_sparse_checkout(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step.setdefault("with", {})["sparse-checkout"] = "tortoise/"


def _set_foreign_repository(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step.setdefault("with", {})["repository"] = "someone-else/not-tortoise"


def _set_read_all_permissions(doc: dict) -> None:
    doc["permissions"] = "read-all"


def _set_invalid_permissions(doc: dict) -> None:
    doc["permissions"] = 42


def _set_notifier_success_status(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"success() && steps.{step_id}.outcome == 'failure'"


def _set_unspaced_checkout_ref(doc: dict) -> None:
    """Same expression, different spacing — must be ACCEPTED (GitHub ignores it)."""
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step.setdefault("with", {})["ref"] = "${{github.sha}}"


def _set_always_status(doc: dict) -> None:
    """`always()` + the outcome conjunct fires on exactly the pinned path."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"always() && steps.{step_id}.outcome == 'failure'"


def _set_parenthesised_conjunct(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"failure() && (steps.{step_id}.outcome == 'failure')"


def _set_write_all_permissions(doc: dict) -> None:
    """GitHub accepts `write-all`, and it grants everything — must be ACCEPTED."""
    doc["permissions"] = "write-all"


def _set_checkout_path_subdir(doc: dict) -> None:
    """`with.path` lands the same tree one directory down; `ruff check .` recurses."""
    for step in doc["jobs"]["lint"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            step.setdefault("with", {})["path"] = "repo"


def _set_second_checkout(doc: dict) -> None:
    steps = doc["jobs"]["lint"]["steps"]
    steps.insert(1, {"uses": "actions/checkout@v4", "with": {"ref": "main"}})


def _set_inert_continue_on_error(doc: dict) -> None:
    _ruff_step(doc)["continue-on-error"] = False


def _set_inert_shell(doc: dict) -> None:
    _ruff_step(doc)["shell"] = "bash"


def _set_echo_shell(doc: dict) -> None:
    _ruff_step(doc)["shell"] = "echo"


def _set_step_working_directory(doc: dict) -> None:
    _ruff_step(doc)["working-directory"] = "docs"


def _set_bracket_notifier(doc: dict) -> None:
    """The bracket accessor is the same expression; it must be ACCEPTED."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"failure() && steps['{step_id}'].outcome == 'failure'"


def _set_notifier_extra_conjunct(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = (
                f"failure() && steps.{step_id}.outcome == 'failure' "
                "&& github.ref == 'refs/heads/release'"
            )


def _set_cancel_in_progress(doc: dict) -> None:
    doc["concurrency"]["cancel-in-progress"] = True


def _set_job_timeout_string(doc: dict) -> None:
    doc["jobs"]["lint"]["timeout-minutes"] = "abc"


def _set_step_timeout_zero(doc: dict) -> None:
    _ruff_step(doc)["timeout-minutes"] = 0


def _set_sudo_install(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = step["run"] + "\nsudo pip install ruff"


def _set_unparseable_install(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = 'pip install \\\n  "ruff==0.16.4'


def _set_install_then_upgrade(doc: dict) -> None:
    for step in doc["jobs"]["lint"]["steps"]:
        if "pip install ruff==" in str(step.get("run")):
            step["run"] = step["run"] + " && pip install -U ruff"


def _set_no_runs_on(doc: dict) -> None:
    doc["jobs"]["lint"].pop("runs-on")


def _set_self_hosted_runner(doc: dict) -> None:
    doc["jobs"]["lint"]["runs-on"] = ["self-hosted", "no-such-runner-label"]


def _set_notifier_with_false_conjunct(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step["if"] = f"failure() && steps.{step_id}.outcome == 'failure' && (1 == 2)"


def _set_notifier_without_action(doc: dict) -> None:
    step_id = _ruff_step(doc)["id"]
    steps = doc["jobs"]["lint"]["steps"]
    for index, step in enumerate(steps):
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            steps[index] = {"name": step["name"], "if": step["if"]}


def _set_verdict_if_success(doc: dict) -> None:
    _verdict_step(doc)["if"] = "success()"


def _verdict_step(doc: dict) -> dict:
    """The live artifact's verdict-publishing step (the one deriving the outcome via `env:`)."""
    ruff_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if (
            isinstance(step, dict)
            and isinstance(step.get("env"), dict)
            and any(
                f"steps.{ruff_id}." in str(value) for value in step["env"].values()
            )
        ):
            return step
    raise AssertionError("the live artifact lost its verdict step — the mutant is unbuildable")


def _set_notifier_fatal(doc: dict) -> None:
    """`continue-on-error` dropped: a reporter that throws now reds the job (#5120)."""
    step_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", "")):
            step.pop("continue-on-error", None)


def _remove_verdict_step(doc: dict) -> None:
    doc["jobs"]["lint"]["steps"].remove(_verdict_step(doc))


def _set_verdict_without_error(doc: dict) -> None:
    """The other half of "both directions are NAMED" — FAIL becomes a bare exit code."""
    step = _verdict_step(doc)
    step["run"] = "\n".join(
        line for line in step["run"].splitlines() if "::error" not in line
    )


def _set_verdict_success_arm_fails(doc: dict) -> None:
    """A PASS arm that `exit 1`s: every green tree would be reported red."""
    step = _verdict_step(doc)
    lines = step["run"].splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "success)":
            lines.insert(index + 1, '    exit 1')
            break
    step["run"] = "\n".join(lines)


def _set_verdict_default_merges_failure(doc: dict) -> None:
    """`*|failure)` with no exit: the unmeasured default rides the FAIL arm's pass."""
    step = _verdict_step(doc)
    run = step["run"].replace("  *)\n", "  *|failure)\n", 1)
    step["run"] = "\n".join(
        line for line in run.splitlines() if not _NONZERO_EXIT.search(line)
    )


def _set_verdict_skipped_rides_failure(doc: dict) -> None:
    """`skipped` merged into the passing FAIL arm — an unmeasured tree goes green."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace("  failure)\n", "  failure|skipped)\n", 1)


def _set_verdict_selector_defaults_success(doc: dict) -> None:
    """`${OUTCOME:-success}`: the selector MENTIONS the outcome but maps unset to PASS."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(
        'case "${OUTCOME:-}" in', 'case "${OUTCOME:-success}" in', 1
    )


def _set_verdict_summary_trailing_comment(doc: dict) -> None:
    """The summary write named only AFTER `#`: the verdict reaches no summary."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(
        '>> "$GITHUB_STEP_SUMMARY"', '> /dev/null  # "$GITHUB_STEP_SUMMARY"'
    )


def _set_verdict_notice_trailing_comment(doc: dict) -> None:
    """The `::notice` named only AFTER `#`: the passing direction is never announced."""
    step = _verdict_step(doc)
    step["run"] = "\n".join(
        "true  # ::notice (was here)" if "::notice" in line and line.strip().startswith("echo")
        else line
        for line in step["run"].splitlines()
    )


def _set_verdict_exit_never_run(doc: dict) -> None:
    """`true  # exit 1`: the text contains an exit that never executes."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace("exit 1", "true  # exit 1", 1)


def _set_verdict_case_selector_constant(doc: dict) -> None:
    """ONE line: the `case` selector becomes a constant, every echo left alone.

    The default arm's message still interpolates `${OUTCOME}`, so a presence-only
    check is satisfied while the step publishes PASS for every conclusion —
    `success`, `failure`, `skipped` and unset all exit 0, and the NO VERDICT arm is
    unreachable (measured in review cycle 3: `case "success" in` was ACCEPTED).
    """
    step = _verdict_step(doc)
    step["run"] = step["run"].replace('case "${OUTCOME:-}" in', 'case "success" in', 1)


def _set_verdict_outcome_overridden(doc: dict) -> None:
    """`OUTCOME=success` at the run-block's BASE indent: the verdict becomes constant.

    The base indent is what a maintainer writes — a mutant carrying extra spaces
    only proves the guard can match an alignment the artifact never has (review
    cycle 3 measured exactly that: 0 columns accepted, 2/4/10 rejected).
    """
    step = _verdict_step(doc)
    step["run"] = step["run"].replace("set -u\n", "set -u\nOUTCOME=success\n", 1)


def _set_verdict_if_always_and_false(doc: dict) -> None:
    """`always()` NAMED but a false conjunct appended — the step never runs."""
    _verdict_step(doc)["if"] = "always() && false"


def _set_second_keyed_notifier(doc: dict) -> None:
    """A second step keys on the outcome in its `if:` — the notifier stops being THE one."""
    step_id = _ruff_step(doc)["id"]
    steps = doc["jobs"]["lint"]["steps"]
    notifier = next(
        step
        for step in steps
        if isinstance(step, dict) and f"steps.{step_id}." in str(step.get("if", ""))
    )
    steps.insert(len(steps), dict(notifier, name="a second keyed notifier"))


def _set_verdict_without_notice(doc: dict) -> None:
    """An annotation in only ONE direction: a failing tree is a bare exit code again."""
    step = _verdict_step(doc)
    step["run"] = "\n".join(
        line for line in step["run"].splitlines() if "::notice" not in line
    )


def _set_verdict_without_summary(doc: dict) -> None:
    """The verdict survives only in a log line, which is the inference #5120 removes."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(' >> "$GITHUB_STEP_SUMMARY"', "")




def _set_verdict_without_success_arm(doc: dict) -> None:
    """`success)` removed: PASS falls into the default arm, which exits 1."""
    step = _verdict_step(doc)
    step["run"] = "\n".join(
        line for line in step["run"].splitlines() if line.strip() != "success)"
    )


def _set_second_outcome_reader(doc: dict) -> None:
    """A second step derives the same outcome, so the reporter is no longer THE reader."""
    step_id = _ruff_step(doc)["id"]
    doc["jobs"]["lint"]["steps"].append(
        {
            "name": "a second reader of the ruff outcome",
            "if": "always()",
            "env": {"OUTCOME": f"${{{{ steps.{step_id}.outcome }}}}"},
            "run": "echo reading $OUTCOME",
        }
    )


def _set_verdict_before_ruff(doc: dict) -> None:
    """Before the ruff step the outcome is null, so the verdict is empty on every run."""
    steps = doc["jobs"]["lint"]["steps"]
    step = _verdict_step(doc)
    steps.remove(step)
    steps.insert(steps.index(_ruff_step(doc)), step)


def _set_permissions_without_issues_write(doc: dict) -> None:
    """Missing `issues: write` and NOTHING else, so the clause is isolated.

    `pull-requests: write` is present on purpose: when this mutant also carried
    `pull-requests: read`, the shape was rejected for two reasons at once and the
    `issues: write` requirement had no mutation proof left — a future edit dropping
    it from the requirement list stayed green (found in review of #9960; with the
    requirement removed from the pin this mutant is ACCEPTED, i.e. it discriminates
    exactly its own clause).
    """
    doc["permissions"] = {"contents": "read", "pull-requests": "write"}


def _set_permissions_without_pull_requests_write(doc: dict) -> None:
    """The EXACT shape that shipped and never notified anyone (#5120).

    `issues: write` + `pull-requests: read` is what this workflow carried from
    c72c66be2 until 2026-10-25. The token really did hold `Issues: write` (the
    runner prints the granted scopes) and the notification still 403'd, so it was
    inert on every run — the shape has to be a REJECTED mutant, not the live
    artifact.
    """
    doc["permissions"] = {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }




def _set_verdict_step_never_runs(doc: dict) -> None:
    _verdict_step(doc)["if"] = "false"


def _set_verdict_step_silenced(doc: dict) -> None:
    _verdict_step(doc)["continue-on-error"] = True


def _set_verdict_echo_shell(doc: dict) -> None:
    """A step-level `shell:` that runs something other than bash.

    The runner then never executes the `run:` block — no summary, no annotation —
    while the pin's execution harness, which supplies its own shell, stays green
    (measured accepted in review cycle 6, F2).
    """
    _verdict_step(doc)["shell"] = "echo"


def _set_verdict_step_working_directory(doc: dict) -> None:
    """A `working-directory:` the runner cannot enter: the step fails to start."""
    _verdict_step(doc)["working-directory"] = "/nonexistent"


def _set_verdict_summary_redirected_by_env(doc: dict) -> None:
    """`env: GITHUB_STEP_SUMMARY` diverts the surface the verdict is published in."""
    env = _verdict_step(doc).setdefault("env", {})
    env["GITHUB_STEP_SUMMARY"] = "/dev/null"


def _notifier_step(doc: dict) -> dict:
    """The live artifact's notification step (the one keyed on the outcome in `if:`)."""
    ruff_id = _ruff_step(doc)["id"]
    for step in doc["jobs"]["lint"]["steps"]:
        if isinstance(step, dict) and f"steps.{ruff_id}.outcome" in str(step.get("if", "")):
            return step
    raise AssertionError("the live artifact lost its notification step — mutant unbuildable")


def _notifier_script_lines(doc: dict) -> list[str]:
    return str(_notifier_step(doc)["with"]["script"]).splitlines()


def _set_notifier_script_lines(doc: dict, lines: list[str]) -> None:
    _notifier_step(doc)["with"]["script"] = "\n".join(lines) + "\n"


def _set_notifier_without_event_guard(doc: dict) -> None:
    """The non-push guard deleted: a dispatch on a branch posts onto its OPEN PR."""
    lines = _notifier_script_lines(doc)
    start = next(i for i, line in enumerate(lines) if "context.eventName !== 'push'" in line)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    _set_notifier_script_lines(doc, lines[:start] + lines[end + 1 :])


def _set_notifier_guard_after_write(doc: dict) -> None:
    """The same guard, moved BELOW the call it is supposed to gate."""
    lines = _notifier_script_lines(doc)
    start = next(i for i, line in enumerate(lines) if "context.eventName !== 'push'" in line)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    guard = lines[start : end + 1]
    rest = lines[:start] + lines[end + 1 :]
    write_at = next(i for i, line in enumerate(rest) if "issues.createComment" in line)
    _set_notifier_script_lines(doc, rest[: write_at + 1] + guard + rest[write_at + 1 :])


def _set_verdict_notice_hoisted(doc: dict) -> None:
    """The `::notice … PASS` annotation moved above the `case`.

    Every conclusion then carries a PASS annotation — beside its FAIL or NO VERDICT
    one — which is the #5120 read-back: annotations that cannot tell a finding from
    a pass (measured accepted in review cycle 9).
    """
    step = _verdict_step(doc)
    lines = step["run"].splitlines()
    notice = next(line for line in lines if "::notice" in line)
    case_at = next(i for i, line in enumerate(lines) if line.lstrip().startswith("case "))
    moved = [line for line in lines if line is not notice]
    step["run"] = "\n".join([*moved[:case_at], notice.strip(), *moved[case_at:]]) + "\n"


def _set_verdict_error_hoisted(doc: dict) -> None:
    """The FAIL `::error` annotation moved above the `case` — every green run says lint-red."""
    step = _verdict_step(doc)
    lines = step["run"].splitlines()
    error = next(line for line in lines if "::error title=main-lint FAIL" in line)
    case_at = next(i for i, line in enumerate(lines) if line.lstrip().startswith("case "))
    moved = [line for line in lines if line is not error]
    step["run"] = "\n".join([*moved[:case_at], error.strip(), *moved[case_at:]]) + "\n"


def _set_notifier_without_status_diagnostic(doc: dict) -> None:
    """The HTTP status dropped from the warning — the datum #5120 could not recover."""
    lines = _notifier_script_lines(doc)
    _set_notifier_script_lines(doc, [line for line in lines if "err.status" not in line])


def _set_notifier_guard_falls_through(doc: dict) -> None:
    """The guard's `return;` deleted: it logs the event and falls through to the POST."""
    lines = _notifier_script_lines(doc)
    start = next(i for i, line in enumerate(lines) if "context.eventName !== 'push'" in line)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    body = [line for line in lines[start : end + 1] if line.strip() != "return;"]
    _set_notifier_script_lines(doc, lines[:start] + body + lines[end + 1 :])


def _set_notifier_guard_falls_through_behind_prose(doc: dict) -> None:
    """The guard's `return;` deleted AND the log line claiming it returns.

    This is the mutant the substring test could not see: `\"return\" not in
    <guard body of the LITERALS-KEPT script>` was satisfied by the word
    `returning` in the message, so a guard that gates NOTHING while its own
    prose says otherwise was ACCEPTED — a false PASS on the one property that
    stops a branch `workflow_dispatch` from posting \"main is lint-red\" onto an
    unmerged PR (review cycle 7). The shipped line is one word away from the
    hole (`not notifying`), so this is a plausible tidy-up, not an exotic one.
    """
    _set_notifier_guard_falls_through(doc)
    lines = _notifier_script_lines(doc)
    for i, line in enumerate(lines):
        if "not notifying" in line:
            lines[i] = line.replace("not notifying", "returning early without notifying")
            _set_notifier_script_lines(doc, lines)
            return
    raise AssertionError("the live artifact's guard log line moved — mutant is unbuildable")


def _set_notifier_guard_inverted(doc: dict) -> None:
    """`=== 'push'`: returns on every real push (a red main is silent) and posts on a dispatch."""
    lines = _notifier_script_lines(doc)
    start = next(i for i, line in enumerate(lines) if "context.eventName !== 'push'" in line)
    lines[start] = lines[start].replace(
        "context.eventName !== 'push'", "context.eventName === 'push'"
    )
    _set_notifier_script_lines(doc, lines)


def _set_verdict_default_green(doc: dict) -> None:
    step = _verdict_step(doc)
    step["run"] = step["run"].replace("exit 1", "exit 0")




def _set_verdict_if_spaced_always(doc: dict) -> None:
    _verdict_step(doc)["if"] = "${{ always() }}"


def _set_verdict_one_line_echo_then_exit(doc: dict) -> None:
    """An HONEST arm: `echo …; exit 1` folded onto ONE line really does exit 1.

    The mutation must produce the spelling it claims to protect — an extra echo
    above the `exit 1` line is not a one-liner, and an acceptance case for a shape
    the mutant never builds protects nothing (measured in review cycle 5).
    """
    step = _verdict_step(doc)
    lines = step["run"].splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "exit 1":
            continue
        # The statement above `exit 1` is a COMMENT in the shipped arm, so folding
        # there would hide the exit in prose — find the last line that is code.
        target = next(
            (i for i in range(index - 1, -1, -1) if not lines[i].lstrip().startswith("#")),
            None,
        )
        if target is not None:
            lines[target] = lines[target].rstrip() + "; exit 1"
            del lines[index]
        break
    step["run"] = "\n".join(lines)


def _set_verdict_failure_arm_exits(doc: dict) -> None:
    """HONEST: a `failure)` arm that also exits — the run is red either way."""
    step = _verdict_step(doc)
    lines = step["run"].splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "failure)":
            continue
        terminate = next(i for i in range(index, len(lines)) if lines[i].strip() == ";;")
        lines.insert(terminate, "    exit 1")
        break
    step["run"] = "\n".join(lines)


def _set_verdict_compact_arms(doc: dict) -> None:
    """HONEST: `success) echo … ;;` — one line per arm, no separate body lines."""
    step = _verdict_step(doc)
    run = step["run"]
    arms = _case_arms(run)
    raw = run.splitlines()
    # The RAW line index — `_shell_code` drops comment lines, so its index would
    # slice the wrong place and take the `case` selector out of the run.
    head = next(i for i, line in enumerate(raw) if re.match(r"^\s*case\s", line))
    prefix = "\n".join(raw[: head + 1])  # everything up to and incl. `case … in`
    # Joined with `;`: a SPACE-joined body turns the arm's `exit 1` into an
    # argument of the preceding `echo` — the arm then exits 0 and the compact
    # spelling silently reproduces the fail-open.
    compact = [
        f"    {pattern}) " + "; ".join(part.strip().rstrip(";") for part in body) + " ;;"
        for pattern, body in arms.items()
    ]
    step["run"] = "\n".join([prefix, *compact, "          esac"])


def _set_verdict_summary_behind_false(doc: dict) -> None:
    """The only summary write executes inside `if false` — the file stays EMPTY.

    The text still NAMES `$GITHUB_STEP_SUMMARY`, so this shape is invisible to the
    presence check and only the executed step can catch it.
    """
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(' >> "$GITHUB_STEP_SUMMARY"', "").replace(
        "set -u",
        "set -u\n          if false; then echo never >> \"$GITHUB_STEP_SUMMARY\"; fi",
        1,
    )


def _set_verdict_pass_announced_unconditionally(doc: dict) -> None:
    """A PASS sentence hoisted above the `case`: every tree claims PASS as well."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(
        'case "${OUTCOME:-}" in',
        'echo "### main-lint verdict: **PASS** — no findings" >> "$GITHUB_STEP_SUMMARY"\n          case "${OUTCOME:-}" in',
        1,
    )


def _set_verdict_arm_aborts_midway(doc: dict) -> None:
    """A command that returns non-zero mid-arm: `bash -e` ABORTS the real step."""
    step = _verdict_step(doc)
    step["run"] = step["run"].replace(
        "  success)\n", "  success)\n    false\n", 1
    )


_STARVED_SHAPES = [
    # ── does not RUN on every merged tree ──
    # The measured defect: the guard inside its former host's constant group.
    (
        "the former host's shared group",
        lambda d: d.__setitem__(
            "concurrency", {"group": "postmerge-validation", "cancel-in-progress": False}
        ),
    ),
    # A constant group that merely LOOKS keyed by name.
    (
        "a constant group named after the guard",
        lambda d: d.__setitem__("concurrency", {"group": "main-lint", "cancel-in-progress": False}),
    ),
    # `github.ref` is `refs/heads/main` for every push to main — the same shared
    # group in disguise, and the most plausible re-introduction of the bug.
    (
        "a group keyed on github.ref (constant on main)",
        lambda d: d["concurrency"].__setitem__("group", "main-lint-${{ github.ref }}"),
    ),
    ("no concurrency block at all", lambda d: d.pop("concurrency")),
    (
        "a path-filtered push trigger",
        lambda d: _trigger_block(d)["push"].__setitem__("paths", ["tortoise/**"]),
    ),
    (
        "a push trigger limited to another branch",
        lambda d: _trigger_block(d)["push"].__setitem__("branches", ["release/*"]),
    ),
    (
        "a push trigger that ignores main",
        lambda d: _trigger_block(d)["push"].__setitem__("branches-ignore", ["main"]),
    ),
    # ── runs, but cannot FAIL ──
    ("a narrowed rule surface", _set_narrowed_ruff),
    ("a shell-escaped invocation (`|| true`)", _set_shell_escaped_ruff),
    ("continue-on-error on the ruff step", _set_continue_on_error),
    ("a step `if:` that never fires", _set_step_never_runs),
    ("a job `if:` that never fires", _set_job_skips),
    ("a job-level concurrency group", _set_job_concurrency),
    ("a workflow-level `defaults:` block", _set_workflow_defaults),
    ("a job-level `defaults.run.shell`", _set_shell_default),
    ("a job-level `defaults.run.working-directory`", _set_working_directory_default),
    # ── runs, but reports green ──
    ("`--exit-zero`", _set_exit_zero),
    ("a narrowed `--select` after the path", _set_selected_rules),
    ("an unpinned ruff install", _set_unpinned_ruff),
    ("a piped invocation (`| tee`)", _set_pipe),
    # ── the install is not really pinned ──
    ("a commented decoy pinned install", _set_decoy_commented_install),
    ("a pinned install AFTER the ruff step", _set_decoy_later_install),
    # ── the run cannot execute, or concludes green ──
    ("a second job plus a `needs:`", _set_extra_job_with_needs),
    ("workflow-level `continue-on-error`", _set_workflow_continue_on_error),
    # ── the failure never reaches anyone ──
    ("a ruff step with no `id:`", _set_ruff_step_without_id),
    ("a notifier `if:` without `failure()`", _set_notifier_without_failure),
    ("a notifier `if:` with a false conjunct", _set_notifier_with_false_conjunct),
    ("a notifier step with no `uses:`/`run:`", _set_notifier_without_action),
    ("a notifier that can fail the job", _set_notifier_fatal),
    ("a second step keyed on the outcome in its `if:`", _set_second_keyed_notifier),
    # ── the VERDICT is never published, or is published green (#5120) ──
    ("the verdict step deleted", _remove_verdict_step),
    ("a verdict `if:` gated on success", _set_verdict_if_success),
    ("a verdict step that never runs", _set_verdict_step_never_runs),
    ("a verdict step silenced by `continue-on-error`", _set_verdict_step_silenced),
    ("a verdict step with a `shell:` that runs something else", _set_verdict_echo_shell),
    ("a verdict step with an unreachable `working-directory`", _set_verdict_step_working_directory),
    (
        "a verdict step whose summary is redirected by its own `env:`",
        _set_verdict_summary_redirected_by_env,
    ),
    ("a notifier with no non-push guard", _set_notifier_without_event_guard),
    ("a notifier whose event guard sits AFTER the write", _set_notifier_guard_after_write),
    (
        "a notifier whose warning drops the HTTP status",
        _set_notifier_without_status_diagnostic,
    ),
    ("a notifier whose guard logs but never returns", _set_notifier_guard_falls_through),
    (
        "a notifier guard that falls through behind a `returning` log line",
        _set_notifier_guard_falls_through_behind_prose,
    ),
    ("a notifier whose event guard is inverted (`=== 'push'`)", _set_notifier_guard_inverted),
    ("a verdict step that never writes the summary", _set_verdict_without_summary),
    ("a verdict step that emits no `::notice`", _set_verdict_without_notice),
    ("a verdict step that emits no `::error`", _set_verdict_without_error),
    ("a verdict `success)` arm that exits non-zero", _set_verdict_success_arm_fails),
    ("a default arm merged with `failure`", _set_verdict_default_merges_failure),
    ("`skipped` merged into the passing FAIL arm", _set_verdict_skipped_rides_failure),
    ("the `case` selector made a constant", _set_verdict_case_selector_constant),
    ("a selector that defaults to PASS", _set_verdict_selector_defaults_success),
    ("a summary write only in a trailing comment", _set_verdict_summary_trailing_comment),
    ("an `::notice` only in a trailing comment", _set_verdict_notice_trailing_comment),
    ("an `exit 1` that never runs", _set_verdict_exit_never_run),
    ("a summary write behind `if false`", _set_verdict_summary_behind_false),
    ("a PASS sentence announced unconditionally", _set_verdict_pass_announced_unconditionally),
    ("a `::notice` annotation hoisted above the `case`", _set_verdict_notice_hoisted),
    ("a `::error` annotation hoisted above the `case`", _set_verdict_error_hoisted),
    ("a command that aborts the arm mid-way", _set_verdict_arm_aborts_midway),
    ("the outcome variable overridden in `run:`", _set_verdict_outcome_overridden),
    ("`always()` plus a false conjunct", _set_verdict_if_always_and_false),
    ("a verdict step whose default arm is green", _set_verdict_default_green),
    ("a verdict `case` with no `success)` arm", _set_verdict_without_success_arm),
    ("a second reader of the ruff outcome", _set_second_outcome_reader),
    ("the verdict step placed before the ruff step", _set_verdict_before_ruff),
    # ── the install is pinned, then replaced (last-write-wins) ──
    ("a second bare install step", _set_second_unpinned_install_step),
    ("a second bare install line", _set_second_unpinned_install_line),
    ("a second install of another version", _set_second_pinned_other_version),
    ("the pin followed by `pip install -U ruff`", _set_install_then_upgrade),
    # ── the job cannot be allocated, or cannot notify ──
    ("no `runs-on:`", _set_no_runs_on),
    ("an unresolvable self-hosted label", _set_self_hosted_runner),
    ("permissions without `issues: write`", _set_permissions_without_issues_write),
    (
        "permissions without `pull-requests: write` (#5120)",
        _set_permissions_without_pull_requests_write,
    ),
    # ── the tree is never checked out, or is not the pushed tree ──
    ("no checkout step", _set_no_checkout),
    ("a skipped checkout", _set_checkout_skipped),
    ("a checkout of a moving ref", _set_checkout_moving_ref),
    # ── the guard cannot see the tree, or cannot notify ──
    ("an empty job-level `permissions:`", _set_empty_job_permissions),
    ("a notifier placed before the guard", _set_notifier_before_ruff),
    ("a notifier that is not github-script", _set_notifier_wrong_action),
    ("a notifier that never comments", _set_notifier_without_comment),
    # ── the trigger selects nothing, or the run cannot start ──
    ("a negated `!main` branch pattern", _set_branches_negated_main),
    ("`cancel-in-progress: true`", _set_cancel_in_progress),
    ("a non-numeric job timeout", _set_job_timeout_string),
    ("a zero step timeout", _set_step_timeout_zero),
    # ── one more unpinned install, in a spelling the first-word rule missed ──
    ("a `sudo pip install ruff` line", _set_sudo_install),
    ("an unparseable install line", _set_unparseable_install),
    # ── the group resolves to a constant even though a sha term is present ──
    ("a starveable operand ahead of the sha", _set_starveable_leading_term),
    # ── the tree is re-pointed after the pinned checkout ──
    ("a second checkout of a moving ref", _set_second_checkout),
    # ── a step-level key that can silence the finding ──
    ("`shell: echo` on the ruff step", _set_echo_shell),
    ("`working-directory: docs` on the ruff step", _set_step_working_directory),
    ("an extra conjunct on the notifier", _set_notifier_extra_conjunct),
    # ── the checkout can narrow or replace the measured tree ──
    ("a sparse checkout", _set_sparse_checkout),
    ("a checkout of another repository", _set_foreign_repository),
    # ── permissions the workflow cannot post with, or that make it invalid ──
    ("`permissions: read-all`", _set_read_all_permissions),
    ("invalid `permissions`", _set_invalid_permissions),
    # ── a status slot that never fires on the failing path ──
    ("`success()` in the notifier status slot", _set_notifier_success_status),
    # ── a scalar branch filter inverts the membership test ──
    ("a scalar `branches-ignore: main`", _set_scalar_branches_ignore),
    ("a scalar `branches: main-next`", _set_scalar_branches_other),
    # ── the notification is commented out, or made a disjunction ──
    ("a commented-out `createComment`", _set_commented_create_comment),
    ("a notifier using `||`", _set_notifier_disjunction),
    # ── the notification is a string literal, not a call ──
    ("a `createComment` mention inside a string", _set_string_literal_create_comment),
    # ── a selector GitHub cannot parse means no run exists at all ──
    ("a mapping `branches:`", _set_mapping_branches),
    ("an integer `branches:`", _set_int_branches),
]


def _set_verdict_inert_continue_on_error(doc: dict) -> None:
    _verdict_step(doc)["continue-on-error"] = False


def _set_verdict_exit_2(doc: dict) -> None:
    step = _verdict_step(doc)
    step["run"] = step["run"].replace("exit 1", "exit 2")


def _set_verdict_renamed_outcome_var(doc: dict) -> None:
    """The same step with a renamed outcome variable — the header promises this is green.

    The header lists a renamed outcome variable among the honest spellings that must
    stay green, and nothing covered it: simplifying `outcome_vars` to the shipped
    literal left every test green while the promise silently became false, so the
    next maintainer to rename it got a red naming a variable the artifact no longer
    had (measured in review cycle 8).
    """
    step = _verdict_step(doc)
    step["env"] = {k.replace("OUTCOME", "VERDICT_OF_RUFF"): v for k, v in step["env"].items()}
    step["run"] = step["run"].replace("OUTCOME", "VERDICT_OF_RUFF")


@pytest.mark.parametrize(
    "label,mutate",
    [
        ("the spaced form `if: ${{ always() }}`", _set_verdict_if_spaced_always),
        ("an honest non-zero `exit 2` in the default arm", _set_verdict_exit_2),
        ("the inert `continue-on-error: false`", _set_verdict_inert_continue_on_error),
        ("an honest `echo …; exit 1` one-liner", _set_verdict_one_line_echo_then_exit),
        ("an honest `failure)` arm that also exits", _set_verdict_failure_arm_exits),
        ("compact one-line `case` arms", _set_verdict_compact_arms),
        ("a renamed outcome variable", _set_verdict_renamed_outcome_var),
    ],
    ids=[
        "spaced-always",
        "exit-2",
        "inert-continue-on-error",
        "one-line-echo-then-exit",
        "failure-arm-exits",
        "compact-arms",
        "renamed-outcome-var",
    ],
)
def test_the_verdict_pin_accepts_honest_spellings(label: str, mutate) -> None:
    """The verdict pin must not red spellings that are equally correct.

    A pin that reds `${{ always() }}` (GitHub's own documentation form, with the
    expression braces) or an honest non-zero `exit 2` gets deleted by the next
    maintainer, and then it guards nothing — the module's stated doctrine. The
    property is "fires on both paths" and "the fallback fails", not one spelling
    of either.
    """
    problem = _merged_tree_guard_problem(_main_lint_mutant(mutate))
    assert problem is None, f"{label!r} is an honest spelling; the pin said: {problem}"


@pytest.mark.parametrize(
    "label,mutate", _STARVED_SHAPES, ids=[label for label, _ in _STARVED_SHAPES]
)
def test_the_merged_tree_guard_check_rejects_the_starved_shapes(label: str, mutate) -> None:
    """Every way to lose the guard while keeping its name must be caught.

    Each entry is a mutation of the LIVE ``main-lint.yml`` — the shapes a future
    edit or a copied workflow would actually produce — not a hand-built fragment,
    so the check is proven against the artifact it guards. The list is in two
    halves on purpose: losing the RUN (trigger, concurrency key) and losing the
    FAIL (a step or job that cannot fail), because a guard that runs green on
    findings is a false PASS, not a missing gate.
    """
    problem = _merged_tree_guard_problem(_main_lint_mutant(mutate))
    assert problem is not None, (
        f"{label!r} loses the merged-tree guard (#2174) and the check accepted it "
        "— main could go lint-red again with every PR's merge ref red and no gate "
        "reporting it"
    )


@pytest.mark.parametrize(
    "group",
    [
        "main-lint-${{ github.sha }}",
        "main-lint-${{github.sha}}",
        "main-lint-${{ github.event.after }}",
        "main-lint-${{ github.run_id }}",
    ],
    ids=["sha-spaced", "sha-unspaced", "event-after", "run-id"],
)
def test_the_merged_tree_guard_check_accepts_an_unstarveable_group(group: str) -> None:
    """The other direction: an equally-correct key must stay green.

    `github.sha`, `github.event.after` and `github.run_id` are all unique per
    pushed tree or per run, so none of them can be superseded. A pin that reds
    the honest alternatives gets deleted, and then it guards nothing — the
    spacing case is here so a format pass cannot red it either.
    """
    doc = _main_lint_mutant(lambda d: d["concurrency"].__setitem__("group", group))
    problem = _merged_tree_guard_problem(doc)
    assert problem is None, f"{group!r} is unstarveable; the check said: {problem}"


def test_the_merged_tree_guard_check_accepts_presentation_only_edits() -> None:
    """Honest, non-narrowing edits must stay green.

    `--output-format=github` (inline annotations) and `uv pip install` are forms
    this repo already uses; a pin that reds them gets deleted by the next
    maintainer, and then it guards nothing. The rule surface and the exit code
    are what this pin is about — not the presentation.
    """
    for label, mutate in (
        ("`--output-format github` (space form)", _set_output_format_space),
        ("`--statistics`", _set_statistics),
        ("a leading `VAR=value`", _set_env_prefix),
        ("`uv sync` as the install", _set_uv_sync),
        ("`defaults.run.shell: bash`", _set_inert_defaults),
        ("the notifier conjuncts swapped", _set_notifier_reordered),
        ("`--output-format=github` after the path", _set_output_format),
        ("`--output-format=github` BEFORE the path", _set_output_format_before_path),
        ("`--no-cache -q`", _set_no_cache_quiet),
        ("`pip install -q ruff==<pin>`", _set_quiet_install),
        ("`pip install --upgrade ruff==<pin>`", _set_upgrade_install),
        ("a quoted `'ruff==<pin>'`", _set_quoted_install),
        ("`ruff check ./` (the same tree)", _set_dot_slash),
        ("the bracket notifier accessor", _set_bracket_notifier),
        ("an unspaced `${{github.sha}}` checkout ref", _set_unspaced_checkout_ref),
        ("`always()` in the status slot", _set_always_status),
        ("a parenthesised outcome conjunct", _set_parenthesised_conjunct),
        ("`permissions: write-all`", _set_write_all_permissions),
        ("a checkout `with.path` subdirectory", _set_checkout_path_subdir),
        ("`branches: ['*']`", _set_glob_branches),
        ("the whole notifier condition parenthesised", _set_whole_condition_parens),
        ("a bare-string `concurrency:` group", _set_bare_string_concurrency),
        ("`continue-on-error: false` on the ruff step", _set_inert_continue_on_error),
        ("`shell: bash` on the ruff step", _set_inert_shell),
        ("`uv run ruff check .`", _set_uv_run),
        ("`ruff check . 2>&1`", _set_redirect_stderr),
    ):
        problem = _merged_tree_guard_problem(_main_lint_mutant(mutate))
        assert problem is None, f"{label} changes presentation only; the pin said: {problem}"
    pin = _dev_ruff_pin()
    doc = _main_lint_mutant(
        lambda d: d["jobs"]["lint"].__setitem__(
            "steps",
            [
                {**step, "run": f"uv pip install ruff=={pin}"}
                if isinstance(step.get("run"), str) and "pip install ruff==" in step["run"]
                else step
                for step in d["jobs"]["lint"]["steps"]
            ],
        )
    )
    assert _merged_tree_guard_problem(doc) is None, (
        "`uv pip install ruff==<pin>` installs exactly the pin; the pin must accept it"
    )


# ── the OTHER half of #9115: the guard must not be starvable itself ────────
#
# The guard above keeps a reporting workflow from TAKING the runner budget.
# This one pins the complementary failure, measured 2026-10-27: the workflow
# that MEASURES the queue was itself served by the pool it was measuring.
#
#     runs busy 9/9 · queued 78 · ci-queue-health: 1 success in its last 8
#     scheduled runs (6 cancelled, 1 pending)
#
# Two independent reasons a tick is lost; the first is this file's subject and
# the second is why the pin has to be a LABEL, not a flag:
#
#   1. `runs-on: ubuntu-latest` is served by BOTH GitHub-hosted and this repo's
#      self-hosted pool (the pool advertises it while the fallback is engaged), so a
#      tick CAN land on the pool and queue behind every test shard in the fleet —
#      measured 2026-10-27: of the 83 jobs these four workflows created, 72 were
#      served by a GitHub-hosted runner and 11 by this repo's self-hosted runners;
#   2. GitHub keeps ONE pending run per concurrency group, so each 10-minute
#      tick cancels the previous PENDING tick while it waits — `cancel-in-progress:
#      false` prevents cancelling a RUNNING governor and does nothing for a
#      queued one.
#
# So a guard whose job is to relieve a full pool must not run ON that pool.
# `ubuntu-24.04` is the version-pinned hosted label this repo already uses for
# `finding-provenance`, `test-slow`, `workflow-lint` and `mirror-images`.
#
# ⛔ OBSERVED, NOT ENFORCED — and the comment in each of the four workflows says
# the same thing, because an overstated guarantee is how the next lane talks
# itself out of the pin. As measured 2026-10-27 with the fallback engaged, all
# nine runners advertised `ubuntu-latest` and none advertised `ubuntu-24.04`
# (with the fallback disengaged, none carries either). The installer cannot emit
# it (`tests/test_ci_runner_x64_install.py:228`) and the re-registration path
# strips it (`tools/runner_autoscale.py::_preserve_labels`, pinned by
# `tests/test_runner_autoscale.py`), but NO test here reads the LIVE label set,
# and the pool HAS served this very label: 2026-10-24's `finding-provenance`
# jobs ran on `pi-runner-vm-*` carrying `ubuntu-24.04`
# (`tests/test_runner_tool_contract.py:5-12`; the gate's drift arm reads the
# gate's TEXT, so it could not fire). Re-measure the live label set if a runner
# is re-cut. The repo is public, so hosted minutes are free — the pin costs
# nothing.
HOSTED_ONLY_PIN = "ubuntu-24.04"

#: Workflows whose job is to OBSERVE or GOVERN the fleet rather than to test
#: product code. Every one of these was measured starved on 2026-10-27, and a
#: monitor that cannot run is worse than no monitor: it reports nothing and
#: reads as "nothing to report".
FLEET_OBSERVER_WORKFLOWS = {
    "ci-queue-health.yml": "queue-health",
    "ci-check-repair.yml": "repair",
    "availability-liveness.yml": "check",
    "availability-watchdog.yml": "probe",
}

#: The step that makes each observer an observer, by the name this repo gives it.
#: A truncated file loses it silently, and a name is the only handle a reader or a
#: test has on "the job's actual work" — `actions/checkout` and the tool-contract
#: step are shared boilerplate that would survive any truncation that keeps a body.
FLEET_OBSERVER_PAYLOAD_STEP = {
    "ci-queue-health.yml": "Measure the queue, and unjam a trim-safe flood",
    "ci-check-repair.yml": "Repair PRs whose required checks never ran",
    "availability-liveness.yml": "Check the pager's heartbeat (#4573)",
    "availability-watchdog.yml": "Probe the public endpoint (self-heal + deduplicated alert)",
}


def test_a_fleet_observer_runs_on_the_hosted_pin_not_the_pool_it_observes() -> None:
    """Each observer's job must pin the hosted label, not `ubuntu-latest`."""
    for name, job_id in FLEET_OBSERVER_WORKFLOWS.items():
        doc = _load(WORKFLOW_DIR / name)
        job = doc["jobs"][job_id]
        assert job.get("runs-on") == HOSTED_ONLY_PIN, (
            f"{name}:{job_id} runs on {job.get('runs-on')!r}. An observer or "
            f"governor of the runner pool must run on the version-pinned hosted "
            f"label ({HOSTED_ONLY_PIN!r}). `ubuntu-latest` is served by both "
            f"GitHub-hosted and the self-hosted pool this repo fills with test "
            f"shards (while the fallback is engaged). The pool arm can hang in "
            f"`actions/checkout` (8m40s, 4m41s) until the job's own timeout kills "
            f"it with the measuring step SKIPPED — 2 of the 11 pool-served jobs "
            f"measured 2026-10-27, same day ci-queue-health completed 1 of its "
            f"last 8 scheduled runs. Public repo: hosted is free.")


def _runs_on_labels(job: dict) -> set[str]:
    """Every label string a job's ``runs-on`` names, in either legal form.

    ``runs-on`` may be a string, a list of strings, or a mapping with a
    ``labels`` key. Flattening it here is the point of this helper: a reviewer
    measured that the first version put ``runs-on`` into a SET directly, so the
    legal list form died of ``TypeError: unhashable type: 'list'`` — a traceback
    from the comparison instead of the assertion message written for exactly
    that case, which fails closed but tells the reader nothing.
    """
    raw = job.get("runs-on")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str)}
    if isinstance(raw, dict):
        labels = raw.get("labels")
        if isinstance(labels, str):
            return {labels}
        if isinstance(labels, list):
            return {item for item in labels if isinstance(item, str)}
    return set()


def test_no_observer_job_keeps_the_pools_fallback_label_in_any_runs_on_form() -> None:
    """A structural cross-check on the pin, in every legal ``runs-on`` spelling.

    This is NOT a second safety net for the pin — ``test_a_fleet_observer_runs_
    on_the_hosted_pin_not_the_pool_it_observes`` owns that, and since each of
    these workflows has exactly one job, a reviewer correctly measured that no
    repo mutation reds this test ALONE. What it adds is shape coverage the pin
    test cannot express: it reads ``runs-on`` through a flattening helper, so a
    job written ``runs-on: [self-hosted, ubuntu-latest]`` (legal, satisfies
    nothing this PR wants, and equal to no single string) is reported by name
    here instead of crashing this test's own set comprehension.

    The pool's fallback label is ``ubuntu-latest`` — the installer's
    ``resolve_labels()`` attaches it deliberately (``self-hosted-x64,ubuntu-
    latest``), so carrying it on an observer job is the starvation this PR
    removes, however it is spelled.
    """
    pool_fallback_label = "ubuntu-latest"
    # The pin's value is that the observer does not ride the pool's FALLBACK label; ⛔ it is NOT a
    # guarantee that the pool cannot serve the pinned label any more. Since the 2026-10-29
    # mitigation (#6897 suspended; `docs/ops/ci-runner-x64.md`, the label contract) the installer
    # CAN attach `ubuntu-24.04` under `CI_RUNNER_OS_LABEL=on|auto` and `_preserve_labels` keeps it,
    # so the four observers are pool-eligible while the mitigation is engaged — an accepted cost
    # recorded in the runbook, not a property this test can assert. `ubuntu-latest` is a different
    # case: it is the label the fallback attaches, so a pin to it puts the observer straight onto
    # the starved arm, and a `ubuntu-<version>` label is the recorded decision
    # (`docs/ops/ci-runner-x64.md:25-26`). Asserting the SHAPE is the only
    # check of `HOSTED_ONLY_PIN` itself that can fail — the loop below reads the workflows.
    assert re.fullmatch(r"ubuntu-\d+\.\d+", HOSTED_ONLY_PIN), (
        f"HOSTED_ONLY_PIN={HOSTED_ONLY_PIN!r} is not a version-pinned hosted label — "
        f"`{pool_fallback_label}` (and any other moving alias) is what the pool's fallback "
        f"attaches, so pinning to it would put the observer back on the starved pool"
    )
    for name in FLEET_OBSERVER_WORKFLOWS:
        doc = _load(WORKFLOW_DIR / name)
        for job_id, job in doc["jobs"].items():
            assert isinstance(job, dict), f"{name}:{job_id} is not a mapping"
            labels = _runs_on_labels(job)
            assert pool_fallback_label not in labels, (
                f"{name}:{job_id} names `{pool_fallback_label}` in runs-on "
                f"({sorted(labels)}) — an observer job must not accept the "
                f"label the self-hosted pool advertises"
            )
            assert labels, (
                f"{name}:{job_id} has an unreadable runs-on value "
                f"({job.get('runs-on')!r}) — this check cannot see what it pins"
            )


def test_the_fleet_observer_roster_cannot_quietly_shrink() -> None:
    """A roster that drifts to empty would pass both tests above vacuously."""
    assert len(FLEET_OBSERVER_WORKFLOWS) >= 4, (
        "the observer roster shrank — if a workflow was renamed, update the "
        "roster rather than deleting its entry, or the pin stops being checked"
    )


def test_a_workflow_edit_keeps_its_steps() -> None:
    """A comment edit must not eat the workflow it annotates.

    Measured while writing this very PR: a note-splice written
    ``s[:i] + NEW_NOTE + RUNS_ON`` (no ``s[j:]``) silently DELETED everything
    below ``runs-on:`` — ``timeout-minutes``, ``env`` and every step — in four
    workflows. Every check that existed still passed: the YAML blocks that
    remained still parsed, and the pin test still read the pinned label from a
    file that no longer did anything. `availability-watchdog.yml` went from 275
    lines to 121 with a green suite.

    So the pin above needs a companion that answers the question the pin cannot:
    is the workflow still a workflow? Cheap invariants, checked on the four
    observers (which is where the splice happened), not on all 39 workflow files
    — a step-count floor, the payload step by name, and the keys all four
    observers carry (job-level ``timeout-minutes`` is required of "python-ci.yml"'s
    jobs and of jobs emitting a required merge context
    (``tests/test_ci_selection.py:6561``), NOT repo-wide: 35 of the tree's 88 jobs
    have none).
    """
    required_job_keys = {"runs-on", "timeout-minutes", "steps"}
    for name, job_id in FLEET_OBSERVER_WORKFLOWS.items():
        doc = _load(WORKFLOW_DIR / name)
        job = doc["jobs"][job_id]
        missing = required_job_keys - set(job)
        assert not missing, (
            f"{name}:{job_id} is missing {sorted(missing)} — the job cannot run. "
            f"A comment-only edit that drops keys below `runs-on:` reads as a "
            f"successful edit to every other test in this file."
        )
        steps = job["steps"]
        assert isinstance(steps, list) and len(steps) >= 2, (
            f"{name}:{job_id} has {len(steps) if isinstance(steps, list) else 'no'} "
            f"step(s) — an observer workflow has a checkout/setup step plus the "
            f"step that does its job, so fewer than 2 means the body was cut."
        )
        # The step that makes each observer an observer, BY NAME. Checking only
        # that *some* step is named is vacuous the moment `actions/checkout` gains
        # a `name:`: the assertion would then pass while the measuring step was
        # gone — the exact truncation this test exists to catch.
        expected = FLEET_OBSERVER_PAYLOAD_STEP[name]
        step_names = [s.get("name") for s in steps if isinstance(s, dict)]
        assert expected in step_names, (
            f"{name}:{job_id} lost its payload step {expected!r} (has "
            f"{step_names}) — a comment-only edit ate the job's body"
        )


def test_every_fleet_observer_payload_step_is_name_pinned() -> None:
    """A payload-step roster that drifts from the observer roster is REPORTED here.

    The equality is the guard: it names the workflow whose payload step went
    unpinned, rather than being the thing that makes another test able to fail.
    """
    assert set(FLEET_OBSERVER_PAYLOAD_STEP) == set(FLEET_OBSERVER_WORKFLOWS), (
        "the payload-step roster drifted from the observer roster — add the "
        "workflow's payload step name rather than deleting its entry "
        f"(missing={sorted(set(FLEET_OBSERVER_WORKFLOWS) - set(FLEET_OBSERVER_PAYLOAD_STEP))}, "
        f"extra={sorted(set(FLEET_OBSERVER_PAYLOAD_STEP) - set(FLEET_OBSERVER_WORKFLOWS))})"
    )
