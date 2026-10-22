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
  block-exists rule cannot see;
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
* the guard is NON-VACUOUS: it asserts it parsed a plausible number of
  workflows and that each workflow it makes a claim about actually exists, so a
  renamed file or a glob typo fails here instead of passing silently.

Sibling guards in the same house style: ``test_workflow_gh_token.py`` (#3092)
and ``test_workflow_secret_interpolation.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


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
    "publish-pypi.yml": "the push arm is a TAG (`v*`), and GitHub does not evaluate `paths` for tag pushes",
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
# ⛔ A new per-event workflow that copies the shape WITHOUT a bound fails
# `test_per_event_per_unit_groups_declare_their_bound` — the rule the
# block-exists test structurally cannot express.
PER_UNIT_GROUP_OK = {
    "finding-provenance.yml": (
        "fan-out per issue is bounded by the KEY: every comment on one issue "
        "lands in the same group, so a comment burst supersedes that issue's own "
        "in-flight run instead of adding a runner (#9577). ACROSS issues the "
        "fan-out is only bounded by the number of issues in flight — that residue "
        "is the accepted trade, and batching/coalescing with a catch-up sweep is "
        "the remaining work on #9115. NOTE the CLASSIFIER SPLIT is a COST bound, "
        "never a fan-out bound: it was already in place during the #9577 flood "
        "(38 of 133 queued runs, 0 of 28 PRs mergeable)."
    ),
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
    assert path.name in PER_UNIT_GROUP_OK, (
        f"{path.name} takes per-event triggers ({sorted(triggers)}) and keys its "
        f"concurrency group PER UNIT ({per_unit}) — that bounds repeats of one "
        "unit but NOT the fan-out: N units still run at once. That is the #9115 "
        "flood shape exactly (the offending workflow already had a per-unit key). "
        "Bound the fan-out (a constant/low-cardinality group, a required check "
        "that is one run per head, or a job-level `if:` that spends no runner), "
        "or add it to "
        "PER_UNIT_GROUP_OK with that bound named."
    )
    assert PER_UNIT_GROUP_OK[path.name].strip(), (
        f"PER_UNIT_GROUP_OK needs a reason for {path.name} — it is a reviewed decision"
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
    assert "issue.number" in group, (
        "the key must be per ISSUE: a constant group would let one issue cancel "
        "another issue's gate (#9577)"
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
