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

import re
import shlex
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
_RUFF_PRESENTATION_FLAGS = ("--no-cache", "--no-fix", "--quiet", "-q")
_RUFF_ARGV = ["ruff", "check", "."]
_INSTALL_PIN = re.compile(r"(?:uv )?pip install ruff==([^\s]+)")


def _ruff_argv_problem(command: str) -> str | None:
    """Why ``command`` is not a full-tree, still-failing ``ruff check .``, or ``None``.

    Tokenised, not substring-matched: `ruff check . || true`, `; true`, a pipe
    and a `$(...)` all satisfy a membership test while making a REAL failure
    report green (the exact-invocation precedent in ``test_merge_throughput.py``).
    """
    tokens = shlex.split(command, comments=True)
    if not tokens or tokens[:3] != _RUFF_ARGV:
        return (
            "the invocation is not literally `ruff check .` as its first three tokens "
            "— a shell escape (`|| true`, `; true`, a pipe) or a different command "
            "satisfies a substring test while making a real failure report green"
        )
    for token in tokens[3:]:
        if token in _RUFF_PRESENTATION_FLAGS or token.startswith("--output-format="):
            continue
        return (
            f"it passes {token!r}, which is not a presentation-only flag — "
            "`--select`/`--ignore`/`--extend-ignore`/`--config` narrow or replace the "
            "rule surface, `--exit-zero` makes every run green, and `--fix` rewrites "
            "the tree the guard measures. Presentation flags allowed: "
            f"{['--output-format=<fmt>', *_RUFF_PRESENTATION_FLAGS]}"
        )
    return None


def _installs_pinned_ruff(command: str, pin: str) -> bool:
    """True when ``command`` installs exactly ``ruff==<pin>`` (a `uv` prefix is fine)."""
    return any(m.group(1) == pin for m in _INSTALL_PIN.finditer(command))


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
        if branches is not None and "main" not in branches:
            return (
                f"its `push:` trigger is limited to {branches}, so a push to main is "
                "never linted — the guard's whole subject is the merged tree"
            )
        if "main" in (push.get("branches-ignore") or []):
            return (
                "its `push:` trigger EXCLUDES `main` (`branches-ignore`), so the "
                "merged trees it exists to guard are the ones it never sees"
            )
    conc = doc.get("concurrency")
    if not isinstance(conc, dict):
        return (
            "it declares no `concurrency:` mapping, so its runs are unbounded and "
            "one can still supersede another"
        )
    group = str(conc.get("group", ""))
    terms = [t.strip() for expr in _EXPRESSION.findall(group) for t in expr.split("||")]
    if not (_UNSTARVEABLE_GROUP_TERMS & set(terms)):
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
    # `defaults.run` rewrites EVERY `run:` in scope — a job-level
    # `defaults: {run: {shell: echo}}` turns `ruff check .` into an echo that
    # exits 0, and `defaults: {run: {working-directory: docs}}` lints one
    # directory. Checked at BOTH levels because either one defuses the guard
    # while every other property here stays correct.
    if "defaults" in doc:
        return (
            "it declares workflow-level `defaults:` — `defaults.run.shell` / "
            "`defaults.run.working-directory` rewrite every `run:` in the workflow, "
            "so the ruff step can become an echo (exit 0 forever) or scan one "
            "directory while reading exactly like `ruff check .`"
        )
    ruff_steps = [
        (job_id, job, step)
        for job_id, job in (doc.get("jobs") or {}).items()
        if isinstance(job, dict)
        for step in (job.get("steps") or [])
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
    for job_id, job, step in ruff_steps:
        argv_problem = _ruff_argv_problem(step["run"])
        if argv_problem is not None:
            return (
                f"step {step.get('name') or step.get('id') or job_id!r} runs "
                f"{step['run'].strip()!r}: {argv_problem}"
            )
        extra = sorted(set(step) - _RUFF_STEP_KEYS)
        if extra:
            return (
                f"the ruff step carries {extra}, which can silently defuse it: `if:` "
                "means it never runs, `continue-on-error:` means a finding never "
                "fails the job, and `shell:`/`working-directory:`/`env:` run "
                "something other than the repo's config on the merged tree"
            )
        if "defaults" in job or "if" in job or job.get("continue-on-error"):
            return (
                f"the job owning the ruff step ({job_id!r}) carries "
                f"{sorted(k for k in ('defaults', 'if', 'continue-on-error') if k in job)}"
                " — a job-level condition, `continue-on-error` or `defaults.run` skips,"
                " silences or rewrites the guard exactly as the step-level form does"
            )
        if "concurrency" in job:
            return (
                f"the job owning the ruff step ({job_id!r}) declares its own "
                "`concurrency:` — job groups share the SAME repo-global namespace as "
                "workflow groups, so a constant job group is starved by the next push "
                "to main exactly like the workflow-level one this pin rejects, while "
                "the per-sha workflow group above still looks correct"
            )
    commands = _run_commands(doc)
    pin = _dev_ruff_pin()
    if not any(_installs_pinned_ruff(c, pin) for c in commands):
        return (
            f"it does not install the pinned ruff (`pip install ruff=={pin}`, `uv` "
            "prefix allowed), so main lints with whatever a bare install resolves "
            "to — the #1685/#2150 drift class, one direction or the other"
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
    assert _installs_pinned_ruff(command, pin), (
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
]


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
    doc = _main_lint_mutant(_set_output_format)
    assert _merged_tree_guard_problem(doc) is None, (
        "`--output-format=github` changes presentation only; the pin must accept it"
    )
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
