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
   that an event creates is eligible for a runner at once. Nine workflows in
   this repo had none before this guard; ``finding-provenance`` fires on
   ``issue_comment: [created]`` — i.e. on every comment of ~50 lanes — so it
   could, and did, occupy the entire budget of §"queued".

2. **A per-push REPORTING workflow with no path filter fires on every merge.**
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
* every workflow with a ``push`` trigger is either path-scoped or listed in
  :data:`MUST_RUN_ON_EVERY_PUSH` with its reason (a deploy or a merged-tree
  validation genuinely has no path filter to take);
* ``finding-provenance`` — the workflow that actually flooded — keeps its
  group keyed **per unit of work** and the two arms that make that key a
  correctness requirement are pinned: a constant group would cancel a finding's
  pending annotation (a fail-open: ``cancelled`` posts nothing), and a
  ``paths:`` filter on an ``issue_comment`` gate would make it never run;
* the REQUIRED aggregate keeps its load-bearing identity: ``python-ci.yml``
  still defines a job with the id ``python-ci-gate`` and no ``name:`` override
  (renaming it silently un-requires the gate — branch protection and Mergify's
  ``check-success=python-ci-gate`` name it);
* the guard is NON-VACUOUS: it asserts it parsed a plausible number of
  workflows and that each workflow it makes a claim about actually exists, so a
  renamed file or a glob typo fails here instead of passing silently.

Sibling guards in the same house style: ``test_workflow_gh_token.py`` (#3092)
and ``test_workflow_secret_interpolation.py``.
"""

from __future__ import annotations

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
# every merged tree (it validates/aggregates ONE tree) or must publish the tree
# (a deploy cannot skip a merge), or its push arm keys on a TAG, where GitHub
# does not evaluate `paths` at all. Anything else that fires per push must be
# path-scoped — that is the rule `welcome-e2e-monitor` now follows.
UNFILTERED_PUSH_OK = {
    "python-ci.yml": "the push-to-main leg IS the merged-tree test run the merge rail baselines against",
    "post-merge-validation.yml": "validates every merged tree (warn-only) — the whole point is one run per push",
    "deploy-hosted.yml": "publishes main; a merge it skipped would never be deployed",
    "deploy-pages.yml": "publishes main's website; same reason",
    "redis-guard.yml": "the push leg provisions/repairs the shared Redis; gating the PR leg is not enough",
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
        "job-level `if:` excludes the owner and `mergify[bot]` — all internal "
        "traffic — so the job is skipped before a runner exists"
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
def test_every_workflow_declares_bounded_concurrency(path: Path) -> None:
    """A workflow with no ``concurrency:`` is unbounded, and that is the #9115 shape."""
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
    push = _triggers(doc).get("push")
    if push is None:
        return
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
    for name in ZERO_COST_PR_EVENT:
        assert (WORKFLOW_DIR / name).exists(), (
            f"ZERO_COST_PR_EVENT names a missing workflow: {name}"
        )


def test_finding_provenance_keeps_a_per_unit_group_and_no_path_filter() -> None:
    """The #9115 workflow's two correctness properties, pinned individually.

    (a) The group is keyed PER UNIT. Collapsing it to one constant group would
        make a burst of comments cancel each other, and a cancelled comment
        gate posts NOTHING — so every cancelled finding would be silently
        ungated (a fail-open), not merely deferred.
    (b) An ``issue_comment`` gate must never gain a ``paths:``/``paths-ignore:``
        filter: those events carry no path, so a filtered gate never runs and a
        skipped check reads as a pass to the rail (the reason the sibling guard
        in ``.github/scripts/ai-review-gate.test.sh`` refuses one too).
    """
    doc = _load(WORKFLOW_DIR / "finding-provenance.yml")
    group = str(doc["concurrency"]["group"])
    assert "comment.id" in group and "issue.number" in group, (
        "the per-unit key is load-bearing: keying by a constant silences the "
        "flood by cancelling the verdicts that owed an annotation (#9115)"
    )
    assert doc["concurrency"]["cancel-in-progress"] is True
    trig = _triggers(doc)
    for event in ("issues", "issue_comment"):
        assert event in trig, f"finding-provenance lost its `{event}` trigger"
        assert trig[event] is None or not (
            isinstance(trig[event], dict) and {"paths", "paths-ignore"} & set(trig[event])
        ), f"`{event}` cannot carry a path filter — the gate would never run"


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
    job_if = str(doc["jobs"]["relay"].get("if", ""))
    assert "github.actor != 'daniel-ospina'" in job_if, (
        "the internal-traffic exclusion (the owner) is what makes this workflow "
        "zero-cost on the PR path — keep it in the job-level `if:`"
    )
    assert "mergify[bot]" in job_if, "the Mergify exemption must stay explicit (#3558)"
