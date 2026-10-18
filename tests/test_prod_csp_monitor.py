"""Standing guard: the deployed-CSP beacon detector is SCHEDULED, and its wiring cannot read green (#4762).

WHY THIS FILE EXISTS
--------------------
``tests/e2e/audit_prod_csp.py`` is the only artifact that can observe what
Cloudflare's edge INJECTS into a response — a plain ``curl`` sends
``Accept: */*`` and misses the tag, and a local ``wrangler pages dev`` is not the
edge. Before #4762 **nothing ran it**: no workflow referenced it, and its
``audit_*.py`` name does not match pytest's collection, so even
``pytest tests/e2e/`` skipped it. A *new* edge-injected origin (Rocket Loader, a
zone feature, a changed injection format) would then be blocked in production
with every repo gate green — the #4638/#3525 failure, a second time. The
``securityHeaders`` guard pins the origins it already knows and cannot discover
one it does not.

The monitor is a **workflow**, so nothing else in the repo can exercise it: a
``welcome-e2e-monitor.yml``-shaped job is only ever executed by GitHub. These
tests therefore pin the wiring by EXECUTION where execution is possible (the
audit step's own shell body, against a stub ``python``) and by CONTENT where it
is not (the cron, the ungated ``ALLOW_PROD``, the dedupe title, the substrate,
the resolve half of the ledger).

⛔ The load-bearing assertion is ``test_audit_step_exit_code_is_not_swallowed``.
GitHub's default shell is ``bash -e {0}``, and ``-e`` does **not** imply
``pipefail``: with a bare ``python … | tee`` the pipeline's status is ``tee``'s,
so a detector that exited 1 reads GREEN — the #4638 class inside the very thing
that exists to catch it. The test runs the real ``run:`` body with a stub
``python`` that exits 1 and requires a non-zero status.

The beacon contract (the #8334 addendum) is pinned against SYNTHETIC request
lists through the detector's pure ``_beacon_verdict``, so the same-origin rule is
tested without touching production.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "prod-csp-monitor.yml"
DETECTOR = REPO_ROOT / "tests" / "e2e" / "audit_prod_csp.py"
SUBSTRATE = REPO_ROOT / ".github" / "scripts" / "auto-file-issue.sh"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    """The `on:` block.

    `on:` is parsed as the boolean True by YAML 1.1, so the raw document is
    rewritten before parsing — the key must be quoted for the block to be
    readable at all.
    """
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8").replace("\non:\n", "\n'on':\n"))
    return raw["on"]


def _job() -> dict:
    jobs = _workflow()["jobs"]
    assert len(jobs) == 1, f"expected exactly one job, got {sorted(jobs)}"
    return jobs[next(iter(jobs))]


def _step(predicate) -> dict:
    matches = [s for s in _job()["steps"] if predicate(s)]
    assert len(matches) == 1, f"expected exactly one matching step, got {len(matches)}"
    return matches[0]


def _audit_step() -> dict:
    # Selected by `id`, not by a substring of the body: the "compose the finding
    # body" step also SPELLS the detector path (in its reproduce hint), so a
    # substring match is ambiguous and would let a test pick the wrong step.
    return _step(lambda s: s.get("id") == "audit")


def _detector_module():
    spec = importlib.util.spec_from_file_location("_audit_prod_csp_4762", DETECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the workflow exists at all ──────────────────────────────────────────
def test_workflow_is_scheduled_and_dispatchable():
    """A monitor nothing triggers is the gap this issue is about.

    Both arms are required: the cron is the standing monitor, and
    ``workflow_dispatch`` is what lets a human re-run it against production after
    a zone or policy change without waiting a day.
    """
    # `on:` is parsed as the boolean True by YAML 1.1 — read the quoted form.
    triggers = _triggers()
    assert [t["cron"] for t in triggers["schedule"]], "a live monitor with no cron is a manual script"
    assert "workflow_dispatch" in triggers


# ── the probe must be ORDERED AFTER the deployment it audits ────────────
def test_a_policy_change_is_probed_after_the_deploy_not_racing_it():
    """The `push`-trigger defect: a probe that starts with the deploy reads the OLD artifact.

    `Deploy Landing Pages` fires on the same event (`push` → main → `website/**`)
    and is what publishes the new policy. A `push:` trigger here starts
    concurrently, spends ~2-3 min on `pip install` + `playwright install` against
    the deploy's build + wrangler deploy + probes, and normally WINS — so a policy
    that blocks the beacon reported GREEN (truthfully describing the previous
    deployment) and was only observed by the next daily cron, up to ~24h later:
    exactly the window #4762 exists to close, on its highest-risk event.

    Nothing else in the file establishes that ordering — no `needs`, no wait, no
    retry — so the trigger itself must be the ordering construct.
    """
    triggers = _triggers()
    deploy_completion = triggers.get("workflow_run")
    assert deploy_completion, (
        "no `workflow_run` trigger: a push-triggered probe races the deployment it audits "
        "and reports on the PREVIOUS one, so a policy change that breaks the beacon reads GREEN"
    )
    assert "Deploy Landing Pages" in deploy_completion["workflows"], (
        "the ordered trigger must name the workflow that PUBLISHES the policy; got "
        f"{deploy_completion['workflows']}"
    )
    assert "completed" in deploy_completion["types"], "`workflow_run` must wait for the run to COMPLETE"
    assert deploy_completion["branches"] == ["main"], (
        "ordering is keyed on the triggering run's branch; without `main` a deploy from any "
        "branch would fire the prod probe"
    )
    # The `workflows:` entry is matched against the deploy workflow's `name:`, so a
    # RENAME there silently disables this trigger — no error, no run, and the monitor
    # falls back to the daily cron only. Pin the literal to the file it names.
    deploy_doc = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
    )
    assert deploy_doc["name"] in deploy_completion["workflows"], (
        f"the ordered trigger names {deploy_completion['workflows']}, but the deploy workflow's "
        f"`name:` is {deploy_doc['name']!r} — `workflow_run` matches on that string, so this "
        "would never fire"
    )
    # …and the race cannot be reintroduced through the push arm: the deployment's
    # own input must not trigger the probe directly.
    push_paths = triggers["push"]["paths"]
    assert "website/**" not in push_paths, (
        "`website/**` is the DEPLOYMENT's input — a push-triggered probe of it races the "
        "deploy and reads the previous deployment (use the `workflow_run` trigger)"
    )


def test_a_probe_code_change_still_gets_an_immediate_live_run():
    """The other side of the same filter: the probe's own files DO trigger a run.

    Neither file changes the deployed artifact, so there is nothing to wait for —
    dropping them too would mean a detector change was only exercised by the next
    cron, and the assertion above would then be satisfied by deleting the trigger
    entirely (the vacuous direction).
    """
    push_paths = _triggers()["push"]["paths"]
    assert "tests/e2e/audit_prod_csp.py" in push_paths
    assert ".github/workflows/prod-csp-monitor.yml" in push_paths


def test_workflow_runs_the_detector_script_that_exists():
    assert DETECTOR.is_file(), "the monitor points at a detector that is not in the tree"
    assert str(DETECTOR.relative_to(REPO_ROOT)) in _audit_step()["run"]


def test_audit_step_supplies_allow_prod_ungated():
    """The script refuses an https target without ``ALLOW_PROD=1`` and exits 2.

    An un-opted-in run therefore probes NOTHING and still exits 2 — and if the
    step were conditional, every run would silently degrade to "refused" while
    the job looked fine.
    """
    step = _audit_step()
    assert step.get("env", {}).get("ALLOW_PROD") == "1", "the audit would exit 2 without probing"
    assert "if" not in step, "a conditional audit step can silently skip the probe"


# ── the load-bearing one: a failing detector cannot read green ──────────
def _run_step_body(body: str, tmp_path: Path, stub_exit: int) -> subprocess.CompletedProcess:
    """Run a workflow ``run:`` body the way Actions does, with a stub ``python``.

    GitHub's default shell is ``bash -e {0}`` (no ``pipefail``, no ``set -u``), so
    that is exactly what runs here — a body that relies on a wider shell would be
    tested against something the runner never provides.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "python"
    stub.write_text(f'#!/bin/sh\necho "stub python $*"\nexit {stub_exit}\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "RUNNER_TEMP": str(tmp_path),
        "PROD_CSP_MONITOR_TITLE": "unused-here",
        "GITHUB_REPOSITORY": "daniel-ospina/tortoise",
    }
    return subprocess.run(
        ["/bin/bash", "-e", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(tmp_path),
    )


def test_audit_step_exit_code_is_not_swallowed(tmp_path):
    """A detector exit of 1 must fail the step — `tee` must not become the verdict.

    GitHub runs a ``run:`` body under ``bash -e {0}``. ``-e`` alone leaves the
    pipeline status as ``tee``'s, so the body MUST set ``pipefail``; without it a
    refusal print-outs into the log and the job reads green.
    """
    body = _audit_step()["run"]
    result = _run_step_body(body, tmp_path, stub_exit=1)
    assert result.returncode != 0, (
        "the audit step swallowed the detector's exit code — a blocked beacon would read GREEN.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # …and the output was still captured, which is what the issue body needs.
    log = (tmp_path / "prod-csp-audit.log").read_text(encoding="utf-8")
    assert "stub python" in log, "the audit output is not being teed for the finding body"


def test_audit_step_passes_a_green_detector(tmp_path):
    """The control: the same harness must NOT fail on a clean run.

    Without this, a body that always exits non-zero would satisfy the test above.
    """
    result = _run_step_body(_audit_step()["run"], tmp_path, stub_exit=0)
    assert result.returncode == 0, f"a green audit failed the step: {result.stderr!r}"


def test_the_rc_assertion_is_discriminating(tmp_path):
    """Prove the harness can fail: dropping `pipefail` makes a red run read green.

    This is the mutation test for the test above, kept in the suite because the
    property it protects is invisible in review — the two bodies differ by one
    line and both "look" fine.
    """
    body = _audit_step()["run"]
    assert "pipefail" in body
    mutated = "\n".join(line for line in body.splitlines() if "pipefail" not in line)
    result = _run_step_body(mutated, tmp_path, stub_exit=1)
    assert result.returncode == 0, (
        "removing `pipefail` did NOT swallow the exit code here — this harness is not "
        "actually exercising the pipeline status, so the guard above may be vacuous"
    )


# ── the monitor's signal: the shared auto-file substrate ────────────────
def test_failure_auto_files_through_the_shared_substrate():
    """The monitor's ONLY signal is the issue it files; a silent step is a deaf monitor."""
    step = _step(lambda s: "auto-file-issue.sh file" in str(s.get("run", "")))
    run = step["run"]
    assert SUBSTRATE.is_file()
    assert ".github/scripts/auto-file-issue.sh file" in run
    assert '--body-file "$RUNNER_TEMP/prod-csp-issue.md"' in run
    # A body file the composing step never wrote would file an empty finding.
    composes = _step(lambda s: "prod-csp-issue.md" in str(s.get("run", "")) and ">" in str(s.get("run", "")))
    assert composes, "no step writes the finding body"


def test_auto_file_runs_on_failure_never_always():
    """`if: always()` would file a recurrence on every GREEN scheduled run.

    Measured shape of that defect: #3907's own (2) — a monitor that fires on the
    healthy state is a check nobody reads.
    """
    step = _step(lambda s: "auto-file-issue.sh file" in str(s.get("run", "")))
    assert step["if"] == "failure()"


def test_dedupe_title_is_stable_and_shared_by_file_and_resolve():
    """One issue per failing streak: the title is the dedupe key, so it must not vary.

    The #2706 defect was a title embedding ``${{ github.run_id }}`` — 59
    near-identical issues. The title also has to be IDENTICAL in the resolve step,
    or a recovered fault closes nothing and the ledger grows forever (#8841).
    """
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    title = _job()["env"]["PROD_CSP_MONITOR_TITLE"]
    assert "${{" not in title and "github.run_id" not in title
    file_step = _step(lambda s: "auto-file-issue.sh file" in str(s.get("run", "")))
    resolve_step = _step(lambda s: "auto-file-issue.sh resolve" in str(s.get("run", "")))
    for step in (file_step, resolve_step):
        assert '--title "$PROD_CSP_MONITOR_TITLE"' in step["run"], (
            "a step spells its own title — file and resolve can then drift apart"
        )
    # The literal appears exactly once in the workflow (the job env), so there is
    # no second place for it to go stale.
    assert workflow_text.count("prod-csp-monitor: a deployed surface") == 1


def test_recovery_closes_the_ledger_without_reddening_a_healthy_run():
    """The other half of #8841's ledger, and it must be non-fatal on a green run."""
    step = _step(lambda s: "auto-file-issue.sh resolve" in str(s.get("run", "")))
    assert step["if"] == "success()"
    assert step["continue-on-error"] is True, "bookkeeping must never red a healthy probe"


def test_the_close_reason_claims_only_what_a_green_run_measured():
    """A green run may close the ledger with a claim it MEASURED — no more.

    `no-tag` is a green outcome by design (the edge injecting nothing is a zone
    setting, #4706 is open), so a green run can legitimately contain a surface that
    never loaded the beacon. The pre-fix `--why` asserted that "every anonymous
    production surface loaded the edge-injected beacon … and reported its RUM
    payload same-origin" — a false record, written into the monitor's only durable
    artifact, on exactly those runs.
    """
    step = _step(lambda s: "auto-file-issue.sh resolve" in str(s.get("run", "")))
    why = step["run"]
    assert "every anonymous production surface loaded" not in why, (
        "the close reason still claims every surface LOADED the beacon, which a `no-tag` "
        "(green) surface contradicts"
    )
    assert "loaded the edge-injected beacon" not in why
    assert "no-tag" in why, (
        "the close reason must name the `no-tag` outcome it deliberately does not fail — "
        "otherwise the caveat is invisible to the next reader of the ledger"
    )
    assert "not-measured" in why, "the close reason must not claim a surface the run never loaded"


def test_both_gh_steps_use_the_actions_token():
    """The substrate REQUIRES the Actions token; a PAT files a duplicate every run.

    Its dedupe search keys on ``author:app/github-actions``, so a token that is
    not the Actions app token matches nothing, ``total`` reads 0 with no warning,
    and a FRESH issue is filed on EVERY run (#2706). It also needs
    ``issues: write`` — the org's restricted default 403s, the step dies, and the
    monitor is deaf (#2140).
    """
    for needle in ("auto-file-issue.sh file", "auto-file-issue.sh resolve"):
        step = _step(lambda s, n=needle: n in str(s.get("run", "")))
        assert step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert _job()["permissions"]["issues"] == "write"
    assert _job()["permissions"]["contents"] == "read"


def test_job_is_bounded():
    """A hung production probe must not hold a runner indefinitely."""
    assert isinstance(_job()["timeout-minutes"], int)
    assert _job()["timeout-minutes"] <= 30


# ── the beacon contract (#4706/#8334 addendum) ──────────────────────────
STRICT_CSP = "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; connect-src 'self'"
NO_BEACON_CSP = "default-src 'self'; script-src 'self'; connect-src 'self'"
PAGE = "https://app.premiselabs.co/team"
RUM = [("POST", "https://app.premiselabs.co/cdn-cgi/rum?")]
BEACON_SCRIPT = [("GET", "https://static.cloudflareinsights.com/beacon.min.js/v1")]
# The console line Chrome emits when the policy refuses the beacon script. It is the
# EVIDENCE `tag-blocked` needs; a policy-text parse alone is not (see the tests below).
BEACON_REFUSED = [
    "[error] Refused to load the script "
    "'https://static.cloudflareinsights.com/beacon.min.js/v8331559384204938955' because it "
    "violates the following Content Security Policy directive: 'script-src self'."
]


def test_beacon_same_origin_is_the_healthy_verdict():
    module = _detector_module()
    label, findings = module._beacon_verdict(BEACON_SCRIPT + RUM, STRICT_CSP, True, PAGE)
    assert (label, findings) == ("rum-ok", [])


def test_third_party_beacon_host_is_a_finding_even_when_the_rum_is_also_seen():
    """A request to the MANUAL-embed apex means the tag stopped using `version`."""
    module = _detector_module()
    cross = [("POST", "https://cloudflareinsights.com/cdn-cgi/rum")]
    label, findings = module._beacon_verdict(BEACON_SCRIPT + RUM + cross, STRICT_CSP, True, PAGE)
    assert label == "rum-ok", "the same-origin POST is still observed; the label reports the happy path"
    assert findings, "a cross-origin RUM request must be a finding"
    assert "beacon-cross-origin" in findings[0]


def test_the_beacon_script_origin_is_not_mistaken_for_the_third_party_host():
    """HOST-EXACT. A substring test also matches `static.cloudflareinsights.com`.

    That host is the beacon's own SCRIPT origin, which every policy must allow —
    so a substring test would red every healthy surface, which is the false-POSITIVE
    direction that gets a monitor switched off.
    """
    module = _detector_module()
    assert module.MANUAL_RUM_HOST != module.BEACON_SCRIPT_HOST
    _label, findings = module._beacon_verdict(BEACON_SCRIPT + RUM, STRICT_CSP, True, PAGE)
    assert findings == []
    # And the reverse direction: the apex host really is caught.
    _, apex = module._beacon_verdict([("POST", "https://cloudflareinsights.com/x")], STRICT_CSP, True, PAGE)
    assert apex


def test_a_beacon_that_reports_nowhere_is_a_finding():
    """The absence case: the tag loaded, the policy admits it, and no RUM went out.

    This is a real failure with NO console message — invisible to the violation
    scan, which is why it needs its own arm. #8334's whole `connect-src` removal
    rests on the POST happening, so it must be observed rather than inferred.
    """
    module = _detector_module()
    label, findings = module._beacon_verdict(BEACON_SCRIPT, STRICT_CSP, True, PAGE)
    assert label == "rum-missing"
    assert findings and "rum-missing" in findings[0]


def test_no_tag_is_reported_not_failed():
    """Web Analytics is a ZONE SETTING; #4706's keep-or-drop decision is open.

    Failing here would turn the monitor into an argument about a product decision
    and red every surface the day someone switches the beacon off on purpose.
    """
    module = _detector_module()
    label, findings = module._beacon_verdict([], STRICT_CSP, False, PAGE)
    assert (label, findings) == ("no-tag", [])


def test_a_blocked_beacon_is_not_double_counted_as_a_missing_rum():
    """The violation scan already fails that surface; a second arm would double-count one cause.

    `tag-blocked` requires the OBSERVED refusal (see the next test): this is the
    legitimate case — the console line naming the beacon script was recorded.
    """
    module = _detector_module()
    label, findings = module._beacon_verdict(BEACON_SCRIPT, NO_BEACON_CSP, True, PAGE, BEACON_REFUSED)
    assert (label, findings) == ("tag-blocked", [])


def test_a_nonadmitting_policy_alone_cannot_silence_the_missing_rum_arm():
    """FAIL-OPEN: the policy parse must not suppress `rum-missing` without evidence.

    `tag-blocked` is a SUPPRESSION — it returns zero findings — so asserting it from
    the policy TEXT can only ever invent a "blocked" (the violation scan owns every
    refusal it could find). Measured on the pre-fix code: a policy spelling the
    beacon origin in any form the whitelist did not enumerate
    (`https://*.cloudflareinsights.com`, `//static.cloudflareinsights.com`, an
    explicit `:443`, a path-scoped source) read as BLOCKED while nothing was
    blocked, and a beacon that loaded and reported nowhere printed `OK … tag-blocked`
    with exit 0 — the exact silent green this arm exists to prevent.

    With no observed refusal the run must FAIL CLOSED and report `rum-missing`.
    """
    module = _detector_module()
    label, findings = module._beacon_verdict(BEACON_SCRIPT, NO_BEACON_CSP, True, PAGE)
    assert label == "rum-missing", (
        "a non-admitting policy suppressed the missing-RUM arm with no observed refusal: "
        f"{label!r} — a beacon that loaded and reported nowhere reads GREEN"
    )
    assert findings and "rum-missing" in findings[0]
    # The same surface WITH the observed refusal is the legitimate suppression,
    # which keeps this test from being satisfied by deleting the arm.
    assert module._beacon_verdict(BEACON_SCRIPT, NO_BEACON_CSP, True, PAGE, BEACON_REFUSED) == (
        "tag-blocked",
        [],
    )


def test_every_csp_form_that_admits_the_beacon_is_recognised():
    """The admit matcher must cover the CSP `host-source` grammar, not four literals.

    Each spelling below legitimately admits `static.cloudflareinsights.com` in Chrome,
    and each was answered `False` by the pre-fix whitelist (which is what turned the
    missing-RUM arm off on a loaded beacon). The non-admitting forms are pinned too,
    so the matcher cannot be widened into "always True".
    """
    module = _detector_module()
    admitting = (
        "*",
        "https:",
        "static.cloudflareinsights.com",
        "https://static.cloudflareinsights.com",
        "https://*.cloudflareinsights.com",
        "*.cloudflareinsights.com",
        "//static.cloudflareinsights.com",
        "//*.cloudflareinsights.com",
        "https://static.cloudflareinsights.com:443",
        "https://*",
        "https://static.cloudflareinsights.com/beacon.min.js",
    )
    for src in admitting:
        assert module._host_source_admits_the_beacon(src) is True, (
            f"the matcher does not recognise `{src}`, which admits the beacon script — "
            "an admit spelling it cannot enumerate can silence the missing-RUM arm"
        )
    rejecting = (
        "'self'",
        "'nonce-abc123'",
        "https://example.com",
        "https://static.cloudflareinsights.com.evil.example",
        "https://static.cloudflareinsights.com:8443",
        "https://static.cloudflareinsights.com/other-path",
        "http:",
        "cloudflareinsights.com",
    )
    for src in rejecting:
        assert module._host_source_admits_the_beacon(src) is False, (
            f"the matcher accepts `{src}`, which does NOT admit the beacon script"
        )
    # And it is wired in: the same spellings decide `_admits_beacon_script`.
    for src in ("https://*.cloudflareinsights.com", "//static.cloudflareinsights.com"):
        assert module._admits_beacon_script(f"script-src {src}") is True, src


def test_a_surface_that_never_loaded_is_not_counted_as_no_tag():
    """A FAILED navigation is `not-measured`, never `no-tag`.

    The run never saw that document, so a 5xx/timeout must not inflate the "carried NO
    injected beacon tag" count — that count is the only signal for a CHANGED injection
    format, and it is also tailed into the auto-filed issue body. The surface still
    fails (via `goto-failed`); it is just not counted as a beacon verdict.
    """
    module = _detector_module()
    blocked, others, label, findings = module._classify_surface(
        goto_error=RuntimeError("net::ERR_TIMED_OUT"),
        close_error=None,
        console=[],
        failed=[],
        csp="",
        requests=[],
        tag_present=False,
        landed_url=PAGE,
    )
    assert label == "not-measured", f"a surface that never loaded was labelled {label!r}"
    assert findings == []
    assert others == []
    assert any("goto-failed" in b for b in blocked), "the failed navigation must still fail the run"
    # …and the summary derives its counts from the LABEL, so the two cannot drift.
    assert module._summarize_labels([label]) == (0, 1), (
        "a not-measured surface was counted as `no-tag` — a 5xx would read as the edge "
        "having stopped injecting the beacon"
    )
    assert module._summarize_labels(["no-tag", "rum-ok"]) == (1, 0)


def test_a_loaded_surface_still_reaches_the_beacon_verdict():
    """The control for the test above: a surface that DID load is classified normally."""
    module = _detector_module()
    _blocked, _others, label, findings = module._classify_surface(
        goto_error=None,
        close_error=None,
        console=[],
        failed=[],
        csp=STRICT_CSP,
        requests=BEACON_SCRIPT,
        tag_present=True,
        landed_url=PAGE,
    )
    assert label == "rum-missing"
    assert findings and "rum-missing" in findings[0]


def test_rum_on_another_origin_is_not_accepted_as_same_origin():
    module = _detector_module()
    label, findings = module._beacon_verdict(
        [*BEACON_SCRIPT, ("POST", "https://some-other-host.example/cdn-cgi/rum")],
        STRICT_CSP,
        True,
        PAGE,
    )
    assert label == "rum-missing"
    assert findings


def test_rum_path_match_is_exact_not_a_prefix():
    """`/cdn-cgi/rum-x` is a different endpoint; a prefix test would accept it."""
    module = _detector_module()
    label, _ = module._beacon_verdict(
        [*BEACON_SCRIPT, ("POST", "https://app.premiselabs.co/cdn-cgi/rump")],
        STRICT_CSP,
        True,
        PAGE,
    )
    assert label == "rum-missing"


def test_rum_path_match_is_the_zone_root_endpoint_not_any_suffix():
    """PATH-EXACT: `/x/cdn-cgi/rum` is not the endpoint the beacon reports to.

    A `endswith` test accepted any path ending in the RUM path, so a POST to a
    deeper path read as the healthy same-origin report — the docstring called it
    an exact match while the predicate was a suffix test, and a reviewer could not
    tell which was intended. The predicate is exact now.
    """
    module = _detector_module()
    label, findings = module._beacon_verdict(
        [*BEACON_SCRIPT, ("POST", "https://app.premiselabs.co/x/cdn-cgi/rum")],
        STRICT_CSP,
        True,
        PAGE,
    )
    assert label == "rum-missing"
    assert findings


def test_script_src_elem_precedence_is_respected():
    """`script-src-elem` overrides `script-src`: a union of all three would answer True."""
    module = _detector_module()
    csp = (
        "script-src 'self' https://static.cloudflareinsights.com; "
        "script-src-elem 'self'; connect-src 'self'"
    )
    assert module._admits_beacon_script(csp) is False
    assert module._admits_beacon_script(STRICT_CSP) is True
    assert module._admits_beacon_script(NO_BEACON_CSP) is False


def test_default_src_is_the_last_resort():
    module = _detector_module()
    assert module._admits_beacon_script("default-src 'self' https://static.cloudflareinsights.com") is True
    assert module._admits_beacon_script("default-src 'self'") is False


def test_no_label_is_documented_that_the_verdict_cannot_return():
    """The label list is the contract a maintainer reads; every label must exist.

    The docstring listed `rum-cross-origin`, which was never returned — the
    cross-origin case returns `rum-ok` PLUS a `beacon-cross-origin` finding
    (pinned above). A documented-but-impossible label reads as coverage and sends
    the next reader looking for a branch that is not there.
    """
    module = _detector_module()
    doc = (module._beacon_verdict.__doc__ or "")
    returned = set()
    for requests, csp, tag, violations in (
        ([], STRICT_CSP, False, []),
        ([], STRICT_CSP, True, []),
        (BEACON_SCRIPT, NO_BEACON_CSP, True, BEACON_REFUSED),
        (BEACON_SCRIPT, STRICT_CSP, True, []),
        (BEACON_SCRIPT + RUM, STRICT_CSP, True, []),
    ):
        returned.add(module._beacon_verdict(requests, csp, tag, PAGE, violations)[0])
    assert returned == {"no-tag", "tag-blocked", "rum-missing", "rum-ok"}
    assert "rum-cross-origin" not in doc


# ── the script's own safety rails survive the edit ──────────────────────
def test_detector_still_refuses_https_without_allow_prod():
    """The opt-in is what keeps a stray local run off production.

    Exercised as a real subprocess: the refusal happens BEFORE Playwright starts,
    so this costs no browser and no network. `ALLOW_PROD` is removed from the
    child env rather than set to '' — the script compares to '1'.
    """
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_PROD"}
    result = subprocess.run(
        [sys.executable, str(DETECTOR), "https://example.com/"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, f"expected the ALLOW_PROD refusal, got {result.returncode}: {result.stderr!r}"
    assert "ALLOW_PROD" in (result.stdout + result.stderr)


# ── the file is selected on the change it guards ────────────────────────
def test_this_file_is_registered_in_a_ci_surface():
    """An unregistered test file is never selected, and `manifest-integrity` fails.

    Registered in `core` because BOTH subjects select it there: a
    `.github/workflows/*` edit falls through to the full matrix, and the detector
    (under `tests/e2e/`, which the manifest does not classify) selects `core`.
    """
    manifest = yaml.safe_load((REPO_ROOT / "config" / "ci-surfaces.yml").read_text(encoding="utf-8"))
    assert "test_prod_csp_monitor.py" in manifest["surfaces"]["core"]


def test_registration_covers_the_changes_it_guards():
    """`--integrity` does not prove SELECTION — ask the selector itself.

    A registration in a surface the diff never selects reads green and guards
    nothing (the #1349/#2938 silent-drop class). Both subjects are checked: the
    workflow edit (a full-matrix path today) and the detector edit under
    `tests/e2e/`, which the manifest does not classify and so falls through to
    `core`.
    """
    tool = REPO_ROOT / "tools" / "ci_selection.py"
    for changed in (
        ".github/workflows/prod-csp-monitor.yml",
        "tests/e2e/audit_prod_csp.py",
    ):
        result = subprocess.run(
            [sys.executable, str(tool), "--changed-files", changed],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            cwd=str(REPO_ROOT),
        )
        data = json.loads(result.stdout)
        assert "core" in data["surfaces"], f"a {changed} change does not select `core`: {data['surfaces']}"


def test_same_origin_is_the_landed_origin_not_the_requested_one():
    """A REDIRECT must not report a healthy surface as broken.

    `https://premiselabs.co/docs/` 301s to `https://tortoise.premiselabs.co/docs`,
    and the beacon correctly POSTs to the LANDED origin. Comparing against the
    requested host reported `rum-missing` on that surface — the only false
    positive this arm produced while being verified (measured), and precisely the
    false-positive direction that gets a monitor switched off.
    """
    module = _detector_module()
    requested = "https://premiselabs.co/docs/"
    landed = "https://tortoise.premiselabs.co/docs"
    rum_on_landed = [("POST", "https://tortoise.premiselabs.co/cdn-cgi/rum?")]
    assert module._beacon_verdict(BEACON_SCRIPT + rum_on_landed, STRICT_CSP, True, landed) == ("rum-ok", [])
    # …and the same requests judged against the pre-redirect host are NOT ok,
    # which is what makes the wiring below load-bearing rather than cosmetic.
    assert module._beacon_verdict(BEACON_SCRIPT + rum_on_landed, STRICT_CSP, True, requested)[0] == "rum-missing"


def test_detector_passes_the_landed_url_to_the_beacon_verdict():
    """Wiring pin: the detector must supply `page.url`, not the requested url.

    The two differ exactly on a redirect, and that difference is invisible in
    review — a one-word edit here silently re-introduces the /docs false
    positive. The behaviour itself is pinned above.
    """
    source = DETECTOR.read_text(encoding="utf-8")
    assert "landed_url = page.url" in source
    assert "landed_url=landed_url" in source
    assert "landed_url=url" not in source
    # …and the pure classifier forwards the LANDED url into the verdict.
    assert "requests, csp, tag_present, landed_url" in source
