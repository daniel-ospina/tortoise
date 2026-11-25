"""Hermetic tests for ``tools/fly_secret_declaration_report.py`` (#14574).

The reporter is the SCHEDULE for an enumeration that, done by hand, cost two days
of undeployable ``main``: two undeclared Fly secrets were each discovered only by
attempting a deploy. What it must be is narrow, and each narrowing below is one
test:

  * it must be SILENT when the set is clean (a silent green run is the output);
  * it must name the EXACT variable when it is not — one issue per name, never a
    vague "drift found";
  * it must NEVER report a read that did not happen as clean (the `spawn-gate`
    `UNREADABLE` principle);
  * it must not be able to MUTATE anything, read a secret VALUE, or hold a second
    opinion from the gate it reports on.

Zero network, zero credential: every seam is the gate's own
(``FLY_SECRETS_FILE`` / ``FLY_MANAGED_SECRETS_FILE`` / ``DEPLOY_WORKFLOW`` /
``FLY_TOML`` / ``FLY_SECRETS_CMD``), so the "dirty" case is produced by a FIXTURE
list and the live app is never touched.

Exit contract (the gate's, for the gate's reason):
  0 clean · 1 drift found · 2 could-not-determine (NEVER read as clean)
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "fly_secret_declaration_report.py"
GATE = REPO_ROOT / ".github" / "scripts" / "check-fly-secret-drift.py"
REAL_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
REAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"

#: Ambient seams a developer might have exported (the gate suite's list, minus the
#: ones this tool does not read).
_AMBIENT = (
    "FLY_SECRETS_FILE",
    "FLY_MANAGED_SECRETS_FILE",
    "FLY_APP",
    "FLY_TOML",
    "DEPLOY_WORKFLOW",
    "FLY_SECRETS_CMD",
    "GH_SECRETS_PRESENT",
    "FLY_STUB_TIMEOUT",
    "FLY_MITIGATION_LEDGER",
    "FLY_MITIGATION_TODAY",
)

_FLY_TOML = """\
app = "fixture-app"

[env]
  ENV_ONLY_KEY = "1"
"""

# Mirrors the real deploy's shapes: an unconditional seed, an inline guard, and a
# name whose GitHub secret differs from the Fly variable.
_WORKFLOW = """\
name: Deploy Hosted API
jobs:
  deploy:
    steps:
      - name: Set all app secrets on Fly.io
        run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          [ -n "${{ secrets.STRIPE_PRICE_IDS }}" ] && ARGS="$ARGS STRIPE_PRICE_IDS=${{ secrets.STRIPE_PRICE_IDS }}"
          ARGS="$ARGS GIT_SHA=${GITHUB_SHA} GITHUB_CLIENT_ID=${{ secrets.GH_CLIENT_ID }}"
          flyctl secrets set --stage $ARGS
"""

_MANIFEST = """\
# fixture manifest
FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY
STRIPE_PRICE_IDS      gh-secret:STRIPE_PRICE_IDS
GIT_SHA               workflow
GITHUB_CLIENT_ID      gh-secret:GH_CLIENT_ID
ENV_ONLY_KEY          fly-toml-env
"""

_TMP = Path(tempfile.mkdtemp(prefix="fly-secret-report-"))


def _fixture(name: str, text: str) -> Path:
    path = _TMP / name
    path.write_text(text)
    return path


MANIFEST = _fixture("manifest.txt", _MANIFEST)
WORKFLOW = _fixture("deploy-hosted.yml", _WORKFLOW)
FLY_TOML = _fixture("fly.toml", _FLY_TOML)
EMPTY_LEDGER = _fixture("no-ledger.txt", "# no entries\n")

#: Every name the fixture manifest declares EXCEPT the two `fly-toml-env`-shadow
#: trap: a Fly secret of a `fly-toml-env` name is itself a violation, so a "clean"
#: fixture must not carry one.
_CLEAN_LIVE = ["FASTAPI_INTERNAL_KEY", "STRIPE_PRICE_IDS", "GIT_SHA", "GITHUB_CLIENT_ID"]
_DIGEST = "deadbeefcafe"  # distinctive, so "was a digest printed?" is assertable


def _secrets_file(names: list[str], name: str) -> Path:
    return _fixture(
        name,
        json.dumps([{"name": n, "digest": _DIGEST, "status": "Deployed"} for n in names]),
    )


def _env(
    secrets: Path | str,
    *,
    manifest: Path = MANIFEST,
    workflow: Path = WORKFLOW,
    present: set[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env: dict[str, str] = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    # ⛔ A MAPPING OVERLAY, NOT `env.update(...)`: `sdk_privatise.py --check` counts
    # untyped ordinary-word receivers (`.update(`/`.query(`/`.delete(`) and this file
    # is a NEW member of that whole-tree census — four `env.update` rows here raised
    # the ratchet by 6 on the PR that added this suite. Convert, do not annotate: the
    # sweep's `Typing` only recognises a `TortoiseSDK` annotation, so `dict[str, str]`
    # changes nothing it measures.
    env = {
        **env,
        "FLY_SECRETS_FILE": str(secrets),
        "FLY_MANAGED_SECRETS_FILE": str(manifest),
        "DEPLOY_WORKFLOW": str(workflow),
        "FLY_TOML": str(FLY_TOML),
        "FLY_MITIGATION_LEDGER": str(EMPTY_LEDGER),
    }
    if present is not None:
        env["GH_SECRETS_PRESENT"] = " ".join(sorted(present))
    if extra_env:
        env = {**env, **extra_env}
    return env


def _run(
    secrets: Path | str,
    *,
    manifest: Path = MANIFEST,
    workflow: Path = WORKFLOW,
    extra_env: dict[str, str] | None = None,
    json_to: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    argv = [sys.executable, str(TOOL)]
    if json_to is not None:
        argv += ["--json", str(json_to)]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=_env(secrets, manifest=manifest, workflow=workflow, extra_env=extra_env),
        timeout=120,
    )
    reading = json.loads(json_to.read_text()) if json_to and json_to.exists() else None
    return proc, reading


# ── (a) SILENCE WHEN THE SET IS CLEAN ────────────────────────────────────────


def test_a_clean_run_is_one_silent_line_and_exits_0(tmp_path):
    """A silent green run IS the correct output — nothing is filed, nothing is loud."""
    out = tmp_path / "clean.json"
    proc, reading = _run(
        _secrets_file(_CLEAN_LIVE, "clean.json"), json_to=out
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert reading is not None and reading["verdict"] == "CLEAN"
    assert reading["remainder"] == []
    assert "UNDECLARED" not in proc.stdout
    # One line, and it states the counts so a reader can tell 44-of-44 from 0-of-0.
    assert len(proc.stdout.strip().splitlines()) == 1, proc.stdout
    assert "CLEAN" in proc.stdout
    assert f"{len(_CLEAN_LIVE)} live" in proc.stdout
    assert "5 declared" in proc.stdout


def test_a_clean_run_files_nothing_it_has_no_credential_at_all():
    """The tool must not be able to file/comment/close — that is the workflow's job.

    Asserted structurally rather than by intent: no subprocess machinery is
    imported by the tool, so the only command it can run is the one the provenance
    gate itself runs (``read_fly_secret_names``), and the tool never touches ``gh``.
    """
    tree = ast.parse(TOOL.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported |= {node.module.split(".")[0]}
    assert "subprocess" not in imported, (
        "the reporter must not spawn processes of its own — the ONLY external read "
        "is the provenance gate's `read_fly_secret_names`, whose default command "
        "`flyctl secrets list` is pinned by tests/test_fly_secret_drift.py"
    )
    # `os.system`/`os.popen` would reintroduce exactly what the import check rules out.
    source = TOOL.read_text()
    assert "os.system" not in source and "os.popen" not in source
    assert "gh api" not in source and "gh issue" not in source
    assert "gh pr " not in source


# ── (b) THE EXACT VARIABLE NAMED WHEN IT IS NOT ──────────────────────────────


def test_an_undeclared_live_secret_is_named_exactly(tmp_path):
    """The #4126/#14574 class: a name on Fly that no declaration covers.

    Produced WITHOUT touching the live app: ``FLY_SECRETS_FILE`` is the gate's own
    fixture seam, so the "dirty" app is a JSON file on disk.
    """
    out = tmp_path / "dirty.json"
    proc, reading = _run(
        _secrets_file([*_CLEAN_LIVE, "TORTOISE_LEDGER_LEGACY_ARM"], "dirty.json"),
        json_to=out,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert reading is not None
    assert reading["verdict"] == "DRIFT"
    assert [(e["name"], e["classes"]) for e in reading["remainder"]] == [
        ("TORTOISE_LEDGER_LEGACY_ARM", ["live"])
    ]
    # Every finding carries the one-line fix, with the variable named in it.
    assert "TORTOISE_LEDGER_LEGACY_ARM" in reading["remainder"][0]["fix"]
    assert (
        "UNDECLARED — TORTOISE_LEDGER_LEGACY_ARM is on Fly, and declared nowhere" in proc.stdout
    )


def test_an_undeclared_staged_name_is_named_exactly(tmp_path):
    """The same violation one step earlier: the deploy CAN create it, nobody declared it.

    Its window of harm is the deploy AFTER the one that creates it (the gate runs
    before propagation), so it is worth naming before it is live.
    """
    # A manifest that forgets the workflow-staged GIT_SHA. It is deliberately NOT
    # on the fixture Fly app, so the ONLY class that can catch it is `staged`.
    manifest = _fixture(
        "manifest-missing-git-sha.txt",
        _MANIFEST.replace("GIT_SHA               workflow\n", ""),
    )
    live_without_git_sha = [n for n in _CLEAN_LIVE if n != "GIT_SHA"]
    proc, reading = _run(
        _secrets_file(live_without_git_sha, "clean2.json"),
        manifest=manifest,
        json_to=tmp_path / "staged.json",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert reading is not None
    assert [(e["name"], e["classes"]) for e in reading["remainder"]] == [
        ("GIT_SHA", ["staged"])
    ]
    assert "UNDECLARED — GIT_SHA is staged by the deploy" in proc.stdout


def test_both_classes_are_reported_once_for_the_same_name(tmp_path):
    """A name that is BOTH live and stageable is ONE finding, not two issues."""
    manifest = _fixture(
        "manifest-missing-key.txt",
        _MANIFEST.replace(
            "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n", ""
        ),
    )
    proc, reading = _run(
        _secrets_file(_CLEAN_LIVE, "clean3.json"),
        manifest=manifest,
        json_to=tmp_path / "both.json",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert reading is not None
    assert [(e["name"], e["classes"]) for e in reading["remainder"]] == [
        ("FASTAPI_INTERNAL_KEY", ["live", "staged"])
    ]
    assert len(reading["remainder"]) == 1


# ── (c) A READ THAT COULD NOT HAPPEN IS NEVER "CLEAN" ────────────────────────


def test_a_failed_fly_read_is_exit_2_and_never_says_clean(tmp_path):
    """The gate's own principle: an unreadable state is not an OK state."""
    out = tmp_path / "unreadable.json"
    proc, reading = _run(
        _TMP / "does-not-exist.json",
        extra_env={"FLY_SECRETS_FILE": str(_TMP / "does-not-exist.json")},
        json_to=out,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout
    assert "UNREADABLE" in proc.stderr
    assert reading is not None and reading["verdict"] == "UNREADABLE"


def test_a_secret_list_command_that_fails_is_exit_2(tmp_path):
    """A ``flyctl`` that is absent, unauthorised, or broken is NO MEASUREMENT."""
    proc, reading = _run(
        _TMP / "ignored.json",
        extra_env={
            "FLY_SECRETS_FILE": "",
            # The gate's own command seam; `false` is a read that cannot complete.
            "FLY_SECRETS_CMD": "false --no-such-flag",
        },
        json_to=tmp_path / "u.json",
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout
    assert reading is not None and reading["verdict"] == "UNREADABLE"


def test_an_empty_secret_list_is_exit_2_not_a_clean_fleet(tmp_path):
    """Zero secrets is never a state the app is in; reading it as clean is the fail-open."""
    proc, reading = _run(_secrets_file([], "empty.json"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout
    assert reading is None  # no --json asked for
    assert "UNREADABLE" in proc.stderr


def test_a_malformed_secret_list_is_exit_2(tmp_path):
    bad = _fixture("bad-shape.json", json.dumps({"secrets": []}))
    proc, _ = _run(bad)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout


def test_a_manifest_this_tool_cannot_read_is_exit_2(tmp_path):
    bad = _fixture("bad-manifest.txt", "ONLY_ONE_FIELD\n")
    proc, _ = _run(_secrets_file(_CLEAN_LIVE, "ok.json"), manifest=bad)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout


def test_a_workflow_with_no_payload_is_exit_2(tmp_path):
    """If the payload cannot be classified, the ``staged`` half is unanswered.

    Rendering that as "nothing is stageable" would silently retire half the
    finding, so it must fail closed instead.
    """
    no_payload = _fixture(
        "no-payload.yml",
        "name: x\njobs:\n  d:\n    steps:\n      - name: nothing\n        run: echo hi\n",
    )
    proc, _ = _run(_secrets_file(_CLEAN_LIVE, "ok2.json"), workflow=no_payload)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout


# ── (d) IT HOLDS NO SECOND OPINION — the gate and the reporter agree ─────────


def test_the_reporter_and_the_gate_agree_on_an_undeclared_name(tmp_path):
    """Same fixture ⇒ the SAME variable named by both, and the same verdict class.

    The reporter reuses the gate's ``read_manifest`` / ``read_fly_secret_names`` /
    payload classification on purpose: two readers of one contract disagree
    eventually, and the disagreement would be read by a human as "the deploy is
    wrong" when the reporter is. This test is what makes the reuse a checked fact
    rather than an intention.
    """
    secrets = _secrets_file([*_CLEAN_LIVE, "SOME_UNDECLARED_KEY"], "agree.json")
    present = {"FASTAPI_INTERNAL_KEY", "STRIPE_PRICE_IDS", "GH_CLIENT_ID"}
    env = _env(secrets, present=present)
    gate_proc = subprocess.run(
        [sys.executable, str(GATE)], capture_output=True, text=True, env=env, timeout=120
    )
    report_proc = subprocess.run(
        [sys.executable, str(TOOL)],
        capture_output=True,
        text=True,
        # The reporter has no GH_SECRETS_PRESENT seam (the deploy states that, not
        # this tool) — the all-present sample is what its `staged` half uses.
        env={k: v for k, v in env.items() if k != "GH_SECRETS_PRESENT"},
        timeout=120,
    )
    assert gate_proc.returncode == 1, gate_proc.stdout + gate_proc.stderr
    assert report_proc.returncode == 1, report_proc.stdout + report_proc.stderr
    for name in ("SOME_UNDECLARED_KEY",):
        assert f"UNDECLARED — {name!r}" in gate_proc.stdout, gate_proc.stdout
        assert f"UNDECLARED — {name} " in report_proc.stdout, report_proc.stdout


# ── (e) NO SECRET VALUE, AND NO MUTATION, CAN APPEAR ────────────────────────


@pytest.mark.parametrize("dirty", [False, True])
def test_no_secret_digest_or_value_is_printed(tmp_path, dirty):
    """``secrets list`` returns digests; the report and the reading carry NAMES only."""
    out = tmp_path / "r.json"
    names = [*_CLEAN_LIVE, "UNDECLARED_ONE"] if dirty else _CLEAN_LIVE
    proc, reading = _run(_secrets_file(names, f"n{dirty}.json"), json_to=out)
    assert _DIGEST not in proc.stdout
    assert _DIGEST not in proc.stderr
    assert _DIGEST not in out.read_text()
    assert reading is not None
    assert not any(k in out.read_text() for k in ('"digest"', '"value"', '"secret"'))


def test_the_help_block_names_every_declaration_shape_and_the_variable():
    """The issue body must carry the copy-paste line — that is the FIX, not advice."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import fly_secret_declaration_report as tool  # noqa: PLC0415, RUF100 — path-based
    finally:
        sys.path.pop(0)
    help_text = tool.declaration_help("SOME_UNDECLARED_KEY")
    # Asserted once, outside the loop: inside it this line had no placeholder and so
    # could not vary — four identical assertions grading nothing about the shape the
    # iteration is about (#14585 review P3).
    assert "SOME_UNDECLARED_KEY" in help_text
    for source, _why in tool.SOURCES:
        assert source in help_text, source
    # Every shape the gate's `read_manifest` accepts, and no invented fifth one.
    assert {s for s, _ in tool.SOURCES} == {
        "gh-secret:<GH_NAME>",
        "workflow",
        "fly-toml-env",
        "fly-only:<issue-ref>",
    }


# ── (f) THE SHIPPED TREE, as the workflow will read it ──────────────────────


def test_the_real_manifest_declares_every_name_the_real_payload_stages():
    """The invariant that keeps the reverse half honest, checked on the real files.

    ``staged - declared`` must be empty: a name the deploy can create with no
    declaration is the violation the reporter exists to report, and finding one
    HERE means the tree is already in that state. (The live half needs a
    credential, so it is not asserted here — the workflow reads it hourly.)
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import fly_secret_declaration_report as tool  # noqa: PLC0415, RUF100 — path-based
    finally:
        sys.path.pop(0)
    gate = tool._gate()
    declared = set(gate.read_manifest(REAL_MANIFEST))
    staged = tool.staged_names(gate, REAL_WORKFLOW.read_text())
    assert sorted(staged - declared) == []


# ── (g) THE WORKFLOW — a report, and provably nothing else ────────────────────
#
# These read `.github/workflows/fly-secret-declaration-drift.yml` as PARSED YAML, so
# a YAML comment (which is where a human explains the rules) can never satisfy or
# fail a check about what the file DOES. `bash -n` on every `run:` block is the
# cheap half of the static check: it catches a shell typo that actionlint would only
# surface after a push, e.g. the `${$(cat …)}` that a first draft of the CLEAN
# warning carried.

WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "fly-secret-declaration-drift.yml"


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW_FILE.read_text())


def _run_blocks() -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for job in (_workflow().get("jobs") or {}).values():
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step.get("run"), str):
                blocks.append((step.get("name") or f"#{index}", step["run"]))
    return blocks


def test_the_workflow_is_scheduled_hourly():
    """#14574's indicator is "<= 1 h to a human"; 6-hourly would miss it 6x over."""
    triggers = _workflow()[True]
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert crons == ["23 * * * *"], (
        "the reporter must be HOURLY to meet #14574's <= 1 h target, and off the "
        "hour boundary (GitHub delays schedule events at the start of the hour) and "
        "clear of consent-key-drift's :11/:41 ticks"
    )


def test_the_workflow_can_write_issues_and_nothing_else():
    perms = _workflow()["permissions"]
    assert perms == {"contents": "read", "issues": "write"}, (
        "the only write this workflow may hold is `issues` (the alert IS the issue); "
        "`contents: write` would let it push"
    )


#: A mutating Fly/GitHub verb. Matched on the PARSED `run:` text only, so the header
#: comments that explain why these must never appear do not trip it.
_FORBIDDEN_IN_RUN = (
    "flyctl",
    "secrets set",
    "secrets unset",
    "gh pr",
    "gh release",
    "git push",
    "gh api -X POST repos",  # belt: a hand-rolled create instead of the substrate
)


def test_no_run_block_can_mutate_fly_a_deploy_or_a_pr():
    for label, run in _run_blocks():
        for needle in _FORBIDDEN_IN_RUN:
            assert needle not in run, (
                f"step {label!r} may not contain {needle!r}: this workflow is a "
                "read-only reporter. The Fly READ is the provenance gate's own "
                "`read_fly_secret_names` (invoked inside the tool); file writes go "
                "through `.github/scripts/auto-file-issue.sh` only"
            )
    # …and the only external actions it pulls are the three below.
    uses = {
        step["uses"]
        for job in (_workflow().get("jobs") or {}).values()
        for step in job.get("steps") or []
        if "uses" in step
    }
    assert uses == {
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "superfly/flyctl-actions/setup-flyctl@master",
    }


def test_every_run_block_is_valid_bash():
    """A shell typo here is a silent hour of no reporting; catch it before the push."""
    import tempfile

    for label, run in _run_blocks():
        path = Path(tempfile.mkdtemp()) / "step.sh"
        path.write_text(run)
        proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"step {label!r} is not valid bash: {proc.stderr}"


def test_the_report_step_still_runs_when_the_read_step_failed():
    """DRIFT fails the read step on purpose; the filing step must still run on it."""
    steps = [
        step
        for job in _workflow()["jobs"].values()
        for step in job.get("steps") or []
    ]
    report = [s for s in steps if s.get("name", "").startswith("Report —")]
    assert report, "the filing step is gone"
    condition = report[0]["if"]
    assert "always()" in condition and "cancelled()" in condition, (
        "without `always()` the filing step is skipped on exactly the run it exists "
        "for (rc=1); without `!cancelled()` a cancel landing inside the read renders "
        "an empty reading and files from it"
    )


#: Every name shape the title grammar must round-trip: the manifest's own class, a
#: lowercase name `read_fly_secret_names` would still accept, and digits/underscores.
_TITLE_NAMES = ["TORTOISE_RECORD_VECTOR_LEG", "lower_case_name", "A1_B2", "X"]


def _tool(*argv: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *argv],
        capture_output=True,
        text=True,
        input=stdin,
        env={k: v for k, v in os.environ.items() if k not in _AMBIENT},
        timeout=60,
    )


def test_the_issue_title_is_a_stable_dedupe_key():
    """A title with a run id / timestamp / count files a duplicate EVERY tick (#2706)."""
    for name in _TITLE_NAMES:
        proc = _tool("--title", name)
        assert proc.returncode == 0, proc.stderr
        title = proc.stdout.strip()
        assert title.startswith(f"Fly secret {name} ")
        assert title.endswith("#4126)")
        # Stable: two calls agree exactly.
        assert _tool("--title", name).stdout.strip() == title
        # Nothing occurrence-varying in it: no recurrence counter, no timestamp.
        assert "Recurrence" not in title
        assert not re.search(r"\d{4}-\d{2}-\d{2}", title), title
        assert "actions/runs" not in title


def test_the_resolve_sweep_only_recognises_this_workflows_own_titles():
    """The clean-run sweep must never close a sibling monitor's issue.

    The discriminator is the EXACT grammar `--title` produces — checked here by
    round-tripping every name shape through both, and by feeding it three decoys
    that a substring test would have accepted.
    """
    ours = [_tool("--title", name).stdout.strip() for name in _TITLE_NAMES]
    decoys = [
        # a sibling monitor's finding, sharing a word
        "CONSENT_TOKEN_SECRET: the consent bridge's two halves hold different keys (#10488)",
        # our wording, but not the exact grammar (one word short)
        "Fly secret FOO is undeclared — the deploy gate refuses every run (#4126)",
        # our prefix, a stranger's suffix
        "Fly secret FOO is undeclared — please advise (#4126)",
    ]
    proc = _tool("--filter-owned-titles", stdin="\n".join([*ours, *decoys]) + "\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == _TITLE_NAMES, (
        "the sweep must return exactly this workflow's names, in order, and none of "
        f"the decoys — got {proc.stdout!r}"
    )


def test_a_whitespace_bearing_name_round_trips_through_the_title_grammar():
    r"""``\S+`` could not re-read a name the gate's reader accepts, so the issue outlived its fix.

    `read_fly_secret_names` accepts ANY non-empty name, so `A B` is filable; under the
    old `\S+` class the sweep never recognised the title it had just filed — an issue
    that can never be resolved, i.e. the "ledger that can only grow" defect (#8841) the
    comment above the grammar claims the permissive class exists to prevent.
    """
    for name in ("A B", "A\tB", "TORTOISE_RECORD_VECTOR_LEG"):
        title = _tool("--title", name).stdout.strip()
        proc = _tool("--filter-owned-titles", stdin=title + "\n")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [name], (
            f"the grammar filed a title it cannot re-read for {name!r} — the issue "
            f"would never be resolved by the clean-run sweep: {proc.stdout!r}"
        )


def test_a_name_that_contains_the_title_suffix_still_round_trips():
    """The grammar's `$` anchor is what makes a NON-GREEDY capture safe, so pin it.

    An earlier revision of the comment above `_TITLE_RE` claimed the NON-GREEDINESS was
    what made a suffix-bearing name round-trip. That claim was wrong, and it was
    measured: with the anchor kept, replacing `(.+?)` by `(.+)` leaves every row in
    this file green (49 passed), because a hard `$` forces the group's extent either
    way. What protects the class is the ESCAPED SUFFIX being anchored with `$`, and
    nothing else pins that — the sibling whitespace row exercises `A B`, `A\tB` and an
    underscore shape, none of which contains the suffix, so dropping the anchor leaves
    it green.

    Measured here, not asserted: drop the trailing `r"$"` from `_TITLE_RE` and
    `pytest -k round_trips` gives `1 failed, 1 passed` — this row reds with
    `'BEFORE\n'`-style truncation while the sibling whitespace row STAYS GREEN, which is
    the point. `read_fly_secret_names` accepts any non-empty name and a shortest-match
    group is free to stop early, so the class is reachable in production. The comment
    above `_TITLE_RE` currently states the same causal fact this docstring does — noted
    as CURRENTLY AGREEING rather than as an enforced invariant, because both are prose
    and NOTHING READS THAT COMMENT, so neither its wording nor its presence is pinned by
    any row. They can drift, and no row would notice. What IS pinned is the grammar's
    BEHAVIOUR: a change to `_TITLE_RE` reds this row, the whitespace row, and — measured
    by stripping the suffix's leading space — the behavioural
    `test_a_clean_tick_files_nothing_and_closes_what_it_owns` (3 failed, 46 passed), while
    `test_the_workflow_owns_the_grammar_in_exactly_one_place` separately keeps the shell
    from spelling a title literal of its own. No row reads the comment.
    """
    suffix = "is undeclared — the deploy gate refuses every run until it is declared (#4126)"
    for name in (
        f"BEFORE {suffix}",
        f"BEFORE {suffix} AFTER",
        f"{suffix} {suffix}",
    ):
        title = _tool("--title", name).stdout.strip()
        proc = _tool("--filter-owned-titles", stdin=title + "\n")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [name], (
            f"{name!r} was filed under a title the sweep reads back as "
            f"{proc.stdout!r} — that issue could never be resolved"
        )


def test_the_workflow_owns_the_grammar_in_exactly_one_place():
    """No title literal in the shell: a grammar spelled twice drifts."""
    text = WORKFLOW_FILE.read_text()
    for label, run in _run_blocks():
        assert "is undeclared — the deploy gate" not in run, (
            f"step {label!r} spells the issue-title grammar; it lives in "
            "`tools/fly_secret_declaration_report.py` (`--title` / "
            "`--filter-owned-titles`) so filing and resolving cannot disagree"
        )
    assert "--title" in text and "--filter-owned-titles" in text


# ── (h) THE GLUE — one issue per NAME, and the ledger closes on a clean read ──
#
# The step's shell is the one part of this workflow no unit test reaches: it reads
# the reading, files through `.github/scripts/auto-file-issue.sh`, and decides which
# open issues it OWNS. This exercises the SHIPPED `run:` text (parsed out of the YAML,
# not copied) against a stub `gh`, so the substrate's real dedupe path runs:
#
#   dirty tick 1 → one `issues` CREATE per undeclared name
#   dirty tick 2 → NO create; one RECURRENCE comment per name on the SAME issue
#   clean tick   → NO create, NO comment; one PATCH close per issue this workflow owns
#   clean tick, with a sibling monitor's issue open → untouched
#
# ⛔ Nothing here reaches GitHub: `gh` on PATH is the stub below, and every URL it
# answers is recorded to a log the assertions read.

_GH_STUB = r"""#!/usr/bin/env bash
# A `gh` that answers the shapes `auto-file-issue.sh` and the report step issue.
# State lives in $GH_STUB_DIR: `issues` = "<number>\t<title>" per filed issue.
set -u
state="${GH_STUB_DIR:?}"
printf '%s\n' "$*" >> "$state/calls.log"
touch "$state/issues"

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '; }

titles_json() {  # {"number":N,"items":[...]} built from the state file
  first=1
  printf '{"items":['
  while IFS=$'\t' read -r num title; do
    [ -n "${num:-}" ] || continue
    [ "$first" = 1 ] || printf ','
    first=0
    printf '{"number":%s,"title":"%s","user":{"login":"github-actions[bot]"}}' \
      "$num" "$(json_escape "$title")"
  done < "$state/issues"
  printf ']}'
}

args="$*"
case "$args" in
  # ── dedupe search (exact-title, Actions-authored) ──────────────────────
  *"search/issues?q="*)
    # Only answer with an item when the QUERY names a title we already filed: the
    # substrate's own select is exact-title, so a stub that always answered would
    # hide a duplicate-filing bug instead of exposing it.
    if [ -s "$state/issues" ]; then titles_json; else printf '{"items":[]}'; fi
    ;;
  # ── the resolve sweep's listing, which gh renders through --jq itself ──
  *"issues?labels="*)
    if printf '%s' "$args" | grep -q -- '--jq'; then
      filter=""; prev=""
      for a in "$@"; do [ "$prev" = "--jq" ] && filter="$a"; prev="$a"; done
      { printf '['; first=1
        while IFS=$'\t' read -r num title; do
          [ -n "${num:-}" ] || continue
          [ "$first" = 1 ] || printf ','
          first=0
          printf '{"number":%s,"title":"%s"}' "$num" "$(json_escape "$title")"
        done < "$state/issues"
        printf ']\n'; } | jq -r "${filter:-.}"
    else
      printf '[]'
    fi
    ;;
  # ── recurrence counting ────────────────────────────────────────────────
  *"comments?per_page"*)
    printf '[]'
    ;;
  # ── CREATE an issue ────────────────────────────────────────────────────
  *"issues --method POST"*)
    title=""
    prev=""
    for a in "$@"; do [ "$prev" = "title=" ] || true; prev="$a"; done
    # `-f title=<value>` arrives as one argv token: title=<value>
    for a in "$@"; do case "$a" in title=*) title="${a#title=}";; esac; done
    num=$(( $(wc -l < "$state/issues") + 1 ))
    printf '%s\t%s\n' "$num" "$title" >> "$state/issues"
    printf '{"number":%s,"title":"%s","user":{"login":"github-actions[bot]"}}' \
      "$num" "$(json_escape "$title")"
    ;;
  # ── POST a comment ─────────────────────────────────────────────────────
  *"comments --method POST"*)
    printf '{"user":{"login":"github-actions[bot]"}}'
    ;;
  # ── PATCH (close) an issue ─────────────────────────────────────────────
  *"--method PATCH"*)
    printf '{"state":"closed","number":1}'
    ;;
  *)
    printf '{}'
    ;;
esac
"""


def _report_step() -> str:
    for job in _workflow()["jobs"].values():
        for step in job.get("steps") or []:
            if str(step.get("name", "")).startswith("Report —"):
                return step["run"]
    raise AssertionError("the report step is gone")


def _read_step() -> dict:
    for job in _workflow()["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("id") == "read":
                return step
    raise AssertionError("the read step is gone")


class _Harness:
    """A run of the SHIPPED report step, offline, against the stub `gh`."""

    def __init__(self, root: Path, *, jq_tsv_failure: bool = False) -> None:
        self.root = root
        self.last_rc: int | None = None
        self.state = root / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.bin = root / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        stub = self.bin / "gh"
        stub.write_text(_GH_STUB)
        stub.chmod(0o755)
        jq = self.bin / "jq"
        if jq_tsv_failure:
            # A `jq` that answers the COUNT and fails only on the row enumeration, so
            # the test grades the ROWS arm specifically rather than being satisfied by
            # the count guard. It is also the pre-fix shape exactly: `count=$(jq …)`
            # succeeded and the process substitution produced nothing.
            real = shutil.which("jq") or "/usr/bin/jq"
            jq.write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do case "$a" in *@tsv*) exit 5 ;; esac; done\n'
                f'exec "{real}" "$@"\n'
            )
            jq.chmod(0o755)
        else:
            jq.symlink_to(shutil.which("jq") or "/usr/bin/jq")

    def calls(self) -> str:
        log = self.state / "calls.log"
        return log.read_text() if log.exists() else ""

    def reset_calls(self) -> None:
        (self.state / "calls.log").unlink(missing_ok=True)

    def filed_titles(self) -> list[str]:
        path = self.state / "issues"
        if not path.exists():
            return []
        return [line.split("\t", 1)[1] for line in path.read_text().splitlines() if "\t" in line]

    def run(
        self,
        *,
        rc: str,
        reading: dict | None,
        file_issues: str = "true",
        report: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        """Run the SHIPPED report step. `report` mimics `steps.read.outputs.report`."""
        report = report if report is not None else self.root / "reading.json"
        if reading is not None:
            report.write_text(json.dumps(reading))
        script = self.root / "report-step.sh"
        script.write_text(_report_step())
        env: dict[str, str] = {
            k: v
            for k, v in os.environ.items()
            if k not in _AMBIENT and not k.startswith("GH_")
        }
        # ⛔ A MAPPING OVERLAY, NOT `env.update(...)` — see `_env`'s note: the
        # whole-tree untyped-receiver ratchet counts `.update(` on an ordinary-word
        # receiver, and this file is a new member of that census.
        env = {
            **env,
            # The stub is the ONLY gh/jq on PATH; python3 and the shell still resolve.
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_STUB_DIR": str(self.state),
            "GH_TOKEN": "stub-token",
            "GITHUB_REPOSITORY": "daniel-ospina/tortoise",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "0",
            "RC": rc,
            # ⛔ The reading's identity now comes from the read step's own OUTPUT, so
            # there is no fixed path to substitute: the harness stands in for that
            # output the same way Actions would.
            "REPORT": str(report),
            "FILE_ISSUES": file_issues,
            # The CLEAN arm's sweep gates, as a SCHEDULED tick sees them: the default
            # branch IS the ref the reading was taken on. Tests that exercise another
            # ref override these through `extra_env`.
            "REF": "refs/heads/main",
            "DEFAULT_BRANCH": "main",
        }
        if extra_env:
            env = {**env, **extra_env}
        proc = subprocess.run(
            ["bash", "-e", str(script)], capture_output=True, text=True, env=env, timeout=300
        )
        self.last_rc = proc.returncode
        return proc.stdout + proc.stderr

    def assert_ok(self, proc_out: str) -> None:
        assert "::error::" not in proc_out, proc_out


def _drift_reading(*names: str) -> dict:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import fly_secret_declaration_report as tool  # noqa: PLC0415, RUF100 — path-based
    finally:
        sys.path.pop(0)
    return {
        "verdict": "DRIFT",
        "app": "fixture-app",
        "remainder": [
            {"name": n, "classes": ["live"], "fix": tool.declaration_help(n)} for n in names
        ],
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_dirty_tick_files_one_issue_per_name_and_never_twice(tmp_path):
    h = _Harness(tmp_path)
    reading = _drift_reading("ALPHA_SECRET", "BETA_SECRET")

    h.run(rc="1", reading=reading)
    titles = h.filed_titles()
    assert len(titles) == 2, h.calls()
    assert sum(1 for t in titles if "ALPHA_SECRET" in t) == 1
    assert sum(1 for t in titles if "BETA_SECRET" in t) == 1
    first_calls = h.calls()
    assert "issues --method POST" in first_calls
    assert "comments --method POST" not in first_calls

    # The SAME name on the next tick must NOT file again: the dedupe search finds
    # the exact title and the substrate records a recurrence on it instead.
    h.reset_calls()
    h.run(rc="1", reading=reading)
    assert len(h.filed_titles()) == 2, "a second tick filed a duplicate issue"
    second_calls = h.calls()
    assert "issues --method POST" not in second_calls, second_calls
    assert second_calls.count("comments --method POST") == 2, second_calls


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_clean_tick_files_nothing_and_closes_what_it_owns(tmp_path):
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"))
    assert len(h.filed_titles()) == 1

    h.reset_calls()
    out = h.run(rc="0", reading={"verdict": "CLEAN", "remainder": []})
    calls = h.calls()
    assert "issues --method POST" not in calls, "a CLEAN tick filed an issue"
    # No RECURRENCE (that is the drift signal). The substrate's own resolve path
    # does post a RESOLUTION comment — close first, comment second (#8841) — so the
    # assertion is on which comment, not on whether one was posted at all.
    assert "Recurrence #" not in calls, "a CLEAN tick recorded a recurrence"
    assert "--method PATCH" in calls, (
        f"a CLEAN tick must close the ledger entry it owns — calls were:\n{calls}"
    )
    assert "Resolved" in calls, "the close was not recorded on the issue"
    assert "CLEAN" in out


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_clean_tick_never_closes_a_sibling_monitors_issue(tmp_path):
    """The public repo makes a look-alike reachable; the sweep must not touch it."""
    h = _Harness(tmp_path)
    sibling = "CONSENT_TOKEN_SECRET: the consent bridge's two halves hold different keys (#10488)"
    (h.state / "issues").write_text(f"9\t{sibling}\n")
    h.reset_calls()
    h.run(rc="0", reading={"verdict": "CLEAN", "remainder": []})
    calls = h.calls()
    assert "--method PATCH" not in calls, (
        f"the sweep closed an issue it does not own:\n{calls}"
    )


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_an_unreadable_tick_files_nothing_and_resolves_nothing(tmp_path):
    """rc=2 must never be read as 'no drift' — and must never touch the ledger."""
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"))
    h.reset_calls()
    out = h.run(rc="2", reading={"verdict": "UNREADABLE", "why": "flyctl absent"})
    calls = h.calls()
    assert "::error::" in out and "NOT 'no drift'" in out, out
    assert "issues --method POST" not in calls
    assert "--method PATCH" not in calls, "an unreadable tick resolved the ledger"
    assert len(h.filed_titles()) == 1


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_dispatched_dry_run_files_nothing(tmp_path):
    h = _Harness(tmp_path)
    out = h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"), file_issues="false")
    assert h.filed_titles() == []
    assert "issues --method POST" not in h.calls()
    assert "DRY RUN" in out
    assert "ALPHA_SECRET" in out, "a dry run must still show the finding"


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_dispatched_dry_run_resolves_nothing(tmp_path):
    """A dry run must be dry on BOTH mutating arms, not only on the filing one.

    `FILE_ISSUES` was consulted only in the DRIFT arm, so a dispatch with
    `file_issues=false` (the default) that read CLEAN printed "Nothing to file." and
    then PATCH-closed every issue this workflow owns: a run the UI calls a DRY RUN
    closed a real alert while asserting a recovery nobody measured. Pinned on the
    SHIPPED step, so a comment cannot stand in for the guard.

    ⛔ THE `jq` SKIPIF IS A DECLARED REQUIREMENT, and the honest reason is narrower than
    "the mutant would stay green" (a review round measured it and an earlier version of
    this comment was wrong): on a host without the `jq` BINARY the DRIFT arm dies at the
    count read — BEFORE its `DRY RUN` notice or any filing — so run 1 files nothing and
    this row REDs at `len(h.filed_titles()) == 1`, with the mutant and without it. It is
    not vacuous; its failure is a HOST failure, and declaring the substrate keeps a
    missing tool from reading as a broken guard.
    """
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"))
    assert len(h.filed_titles()) == 1

    h.reset_calls()
    out = h.run(rc="0", reading={"verdict": "CLEAN", "remainder": []}, file_issues="false")
    calls = h.calls()
    assert "--method PATCH" not in calls, (
        f"a DRY RUN closed a live finding — the sweep mutates on a run the UI calls dry:\n{calls}"
    )
    assert "DRY RUN" in out, out
    assert len(h.filed_titles()) == 1, "the ledger entry vanished on a dry run"


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_clean_tick_on_a_non_default_ref_resolves_nothing(tmp_path):
    """The ledger records the DEFAULT BRANCH's state — a reading of another tree cannot close it.

    `actions/checkout` reads the event ref, so a dispatch from the branch that ADDS a
    declaration reads CLEAN for a name `main`'s deploy gate is still refusing; resolving
    on it closes the live alert with a resolution that has not happened (and `main`'s
    next tick re-files it as a NEW issue, because the dedupe search matches OPEN issues).
    """
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"))
    assert len(h.filed_titles()) == 1

    h.reset_calls()
    out = h.run(
        rc="0",
        reading={"verdict": "CLEAN", "remainder": []},
        extra_env={"REF": "refs/heads/chore/drift-reporter"},
    )
    calls = h.calls()
    assert "--method PATCH" not in calls, (
        f"a reading of a NON-default ref resolved the default branch's ledger:\n{calls}"
    )
    assert "NOT THE DEFAULT BRANCH" in out, out
    assert len(h.filed_titles()) == 1


# ── (h) THE COLOUR CONTRACT, THE READING'S IDENTITY, AND THE PINNED COMMAND ──
#
# Every test below reds on the PRE-FIX shape, and each names the pre-fix behaviour
# it forbids — a colour contract that was only asserted in a comment, a reading that
# could be a previous tick's bytes, a "pinned" command that was only a default, and
# an issue body that dropped the blocking consequence for a name that is BOTH live
# and stageable (#14585 review).


def test_the_read_step_cannot_red_its_own_run():
    """DRIFT exits 1 — so without `continue-on-error` every DRIFT tick is a RED run.

    The header's whole argument is that a red tick must mean ONE thing (the
    instrument could not read); 24 reds a day for a state already filed is the
    "monitor nobody reads" defect. A failing step reddens its job, so the colour
    contract needs this key — a comment asserting it was the only thing holding it,
    and a comment cannot red a test.
    """
    step = _read_step()
    assert step.get("continue-on-error") is True, (
        "without `continue-on-error: true` this step's rc=1 verdict reddens the job, "
        "so DRIFT is a red run and red no longer means 'the instrument could not "
        "read' — the exact defect the workflow header refuses"
    )
    # …and the colour is then decided by the Report step, which is the only place
    # that knows whether the finding was DELIVERED.
    assert "steps.read.outputs.rc" in _report_step_env()


def _report_step_env() -> str:
    for job in _workflow()["jobs"].values():
        for step in job.get("steps") or []:
            if str(step.get("name", "")).startswith("Report —"):
                return json.dumps(step.get("env") or {})
    raise AssertionError("the report step is gone")


def test_the_resolve_gate_is_supplied_a_default_branch_on_every_event():
    """The CLEAN sweep's guards are pinned; their `env:` INPUTS were pinned by nothing.

    `_Harness.run()` supplies `REF`/`DEFAULT_BRANCH` itself, so no row can see the
    workflow's own wiring: delete either expression (or mis-wire it — `github.ref_name`
    for `REF`, the raw `github.ref` for `DEFAULT_BRANCH`) and every test stays green
    while the sweep takes its skip arm on every tick, forever. That is the `#8841`
    defect this sweep closes, reintroduced with a green suite — the silent-disable
    shape `_report_step_env()` exists to prevent for the read step's outputs.

    The FALLBACK is asserted as part of the wiring because it is the difference between
    a working sweep and a permanently parked one: `schedule` is the only event that
    resolves in production, and a scheduled payload carries no `repository` object, so
    the bare payload field is EMPTY on every tick that matters.
    """
    env = json.loads(_report_step_env())
    assert env.get("REF") == "${{ github.ref }}", (
        "the sweep's ref must be the ref `checkout` read — the tree the reading describes; "
        f"`github.ref_name` here would be `main` on a branch dispatch. Got {env.get('REF')!r}"
    )
    assert env.get("DEFAULT_BRANCH") == (
        "${{ github.event.repository.default_branch || github.ref_name }}"
    ), (
        "DEFAULT_BRANCH must carry a fallback: a `schedule` payload has no `repository` "
        "object, so the bare payload field is empty on every tick that resolves and the "
        "sweep parks on its skip arm forever. Got "
        f"{env.get('DEFAULT_BRANCH')!r}"
    )


def test_the_dry_run_wire_is_PINNED_because_its_absence_is_FAIL_OPEN():
    """The CLEAN arm's other two wires fail CLOSED; this one fails OPEN, so pin it.

    `DEFAULT_BRANCH` unset and a short-form `REF` both take the sweep's SKIP arm, so
    their absence is at least safe, and
    `test_the_resolve_gate_is_supplied_a_default_branch_on_every_event` pins their
    values for correctness. `FILE_ISSUES` is the opposite direction: the guards read
    `${FILE_ISSUES:-true}`, so an ABSENT wire means "not a dry run". Delete this one
    `env:` line from the workflow and a `workflow_dispatch` with the DEFAULT
    `file_issues=false` closes the ledger again — the exact defect this PR exists to
    fix — with every behavioural row still green, because `_Harness.run()` supplies
    `FILE_ISSUES` itself and so cannot see the workflow's own wiring (#14601 item 2,
    measured there: with that entry deleted nothing in this file reds).
    """
    env = json.loads(_report_step_env())
    assert env.get("FILE_ISSUES") == (
        "${{ github.event_name != 'workflow_dispatch' || "
        "github.event.inputs.file_issues == 'true' }}"
    ), (
        "the dry-run wire is unpinned: `FILE_ISSUES` is read as `${FILE_ISSUES:-true}`, "
        "so an ABSENT entry reads as NOT a dry run and a dispatched dry run closes the "
        f"ledger (#14601 item 2). Got {env.get('FILE_ISSUES')!r}"
    )


def test_the_clean_arms_guards_sit_ABOVE_the_resolve_sweep():
    """A ledger-closing guard placed BELOW the sweep guards nothing.

    ⛔ THIS ROW IS A DIAGNOSIS, NOT THE ONLY NET — an earlier revision of this
    docstring claimed "no behavioural row can see WHERE in the step the guards sit",
    and a review measured that FALSE: the behavioural rows run the SHIPPED step, so
    lifting the CLEAN arm's dry-run guard (its `if [ "${FILE_ISSUES:-true}" != "true" ]`
    block in `.github/workflows/fly-secret-declaration-drift.yml`, the CLEAN arm's copy —
    the DRIFT arm carries an identical one) to just after `done < "$mine"`
    gives 2 failed — this row and `test_a_dispatched_dry_run_resolves_nothing`, which
    reds because the sweep mutated on a run the UI calls dry. The net is that NARROW,
    and its breadth was measured rather than assumed: a CLEAN-arm REORDER can only reach
    rows that enter the CLEAN arm, which is why `test_a_dispatched_dry_run_files_nothing`
    and `test_a_dirty_tick_files_one_issue_per_name_and_never_twice` — both `rc=1`, both
    DRIFT-arm rows — stay green on it. `files_nothing` is reached by a DIFFERENT mutant:
    lift the guard ABOVE the CLEAN arm and it reds as well (2 failed, 47 passed — it and
    this row). What this row adds is that it NAMES the fault: a deletion or a reordering
    reds here with
    "the CLEAN arm no longer honours the dry-run input" / "a ledger-closing guard sits
    BELOW the sweep", rather than off a downstream behaviour diff — and it catches a
    CLEAN-arm deletion at all, which is what the scoping note below exists for.

    The guards' VALUES are pinned separately and both halves exist: `REF` and
    `DEFAULT_BRANCH` by
    `test_the_resolve_gate_is_supplied_a_default_branch_on_every_event`, and
    `FILE_ISSUES` by `test_the_dry_run_wire_is_PINNED_because_its_absence_is_FAIL_OPEN`
    (#14601 — that wire is FAIL-OPEN, unlike the other two). This row pins only the
    ORDER, so neither property can be lost silently when the other is edited.

    ⛔ THE SEARCH IS SCOPED TO THE CLEAN ARM, and that is load-bearing rather than
    tidy (a review round measured the unscoped version and an earlier draft of this
    docstring was wrong): `if [ "${FILE_ISSUES:-true}" != "true" ]` occurs in BOTH
    arms — the CLEAN arm's guard and the DRIFT arm's identical one — so an unscoped
    `find` on a CLEAN-arm deletion silently re-anchors on the DRIFT arm and the
    `!= -1` arms never fire: the row still reds, but it complains about ORDER when
    the guard is GONE, which is a fair test reporting the wrong fault. Slicing to the
    arm the sweep lives in gives each anchor exactly one occurrence, so a deletion
    reds on its own message.

    `.find()` rather than `.index()`: a deleted guard must RED with a message that
    names it, not raise `ValueError: substring not found`.
    """
    run = _report_step()
    clean_start = run.find('if [ "${RC:-}" = "0" ]; then')
    drift_start = run.find('if [ ! -s "$REPORT" ]; then')
    assert clean_start != -1, "the CLEAN arm is gone — where does a clean reading go?"
    assert drift_start != -1, "the DRIFT arm is gone — where does a drift reading go?"
    assert clean_start < drift_start, "the CLEAN arm no longer precedes the DRIFT arm"
    clean_arm = run[clean_start:drift_start]
    dry_run_guard = clean_arm.find('if [ "${FILE_ISSUES:-true}" != "true" ]')
    ref_guard = clean_arm.find('if [ -z "${DEFAULT_BRANCH:-}" ]')
    sweep = clean_arm.find("auto-file-issue.sh resolve")
    assert dry_run_guard != -1, "the CLEAN arm no longer honours the dry-run input"
    assert ref_guard != -1, "the CLEAN arm no longer checks which branch it read"
    assert sweep != -1, "the resolve sweep is gone — would the ledger ever close (#8841)?"
    assert dry_run_guard < sweep and ref_guard < sweep, (
        "a ledger-closing guard sits BELOW the sweep, so it guards nothing"
    )


def test_the_read_step_publishes_a_fresh_reading_identity_every_tick():
    """A fixed `/tmp` path outlives a tick that died before writing.

    Pre-fix the read step wrote to a predictable path and the report step judged
    whatever was THERE — so a tick that died at rc=127 on a runner whose /tmp
    survives between jobs would judge the previous tick's CLEAN bytes and exit 0
    with no reading at all.
    """
    run = _read_step()["run"]
    assert "mktemp" in run, (
        "the reading must get a per-run identity; a predictable path lets a previous "
        "tick's bytes be consumed as this tick's verdict"
    )
    assert "report=" in run and "$GITHUB_OUTPUT" in run, (
        "the path must be published as a step OUTPUT, or the report step has to guess"
    )
    assert "/tmp/fly-secret-declaration.json" not in run, (
        "the fixed path is back — that is the stale-reading hole"
    )
    # And the report step must CONSUME that output rather than a literal.
    assert "steps.read.outputs.report" in _report_step_env(), _report_step_env()
    assert "/tmp/fly-secret-declaration.json" not in _report_step(), (
        "the report step still falls back to the fixed path"
    )


def test_no_scratch_file_in_the_report_step_uses_a_fixed_tmp_path():
    """Static, because the defect is only reachable under CONCURRENT execution.

    A fixed `/tmp` name lets a killed/concurrent tick's listing be consumed as this
    tick's, which resolves an issue whose condition nobody re-read. It was measured
    at 5 failures in 24 runs with the fixed literals versus 0 in 24 with `mktemp`,
    driving 4 pytest processes at once — and the repo's own CI is SERIAL
    (`config/ci-surfaces.yml`: "with no xdist — one serial pytest process per leg"),
    so `-n 4` reproduces nothing and a behavioural pin would be vacuous. A static
    assertion reds the moment the literal comes back, whatever the suite shape.
    """
    run = _report_step()
    assert "/tmp/" not in run, (
        "the Report step holds a fixed /tmp path again: a stale listing from a "
        "killed or concurrent tick can be consumed as this tick's, resolving an "
        "issue whose condition nobody re-read. Give it its own identity (mktemp)."
    )
    # …and the same for the read step's reading (which publishes its identity).
    assert "/tmp/" not in _read_step()["run"]


def test_the_workflow_pins_the_only_command_it_runs(tmp_path):
    """`FLY_SECRETS_CMD` is an env seam — a default is not a pin.

    Pre-fix the workflow set only FLY_API_TOKEN/FLY_APP, so "read-only by shape"
    rested on the TOOL's default command while the header claimed the workflow
    pinned it. Spelled out in the step, the only command this job can run is
    reviewable in the diff that grants the credential.
    """
    env = _read_step()["env"]
    assert "FLY_SECRETS_CMD" in env, (
        "the read must be PINNED in the workflow, not inherited from the tool's "
        "default — an ambient FLY_SECRETS_CMD would re-point the job's only command"
    )
    argv = shlex.split(env["FLY_SECRETS_CMD"])
    assert argv == ["flyctl", "secrets", "list", "--app", env["FLY_APP"], "--json"], argv
    for mutating in ("set", "unset", "import", "destroy", "deploy"):
        assert mutating not in argv, f"the pinned command may not contain {mutating!r}"

    # Behavioural half: the TOOL must actually run exactly that argv. A static read
    # of the YAML cannot prove the tool honours the variable at all.
    binder = tmp_path / "bin"
    binder.mkdir()
    recorded = tmp_path / "argv.txt"
    fly = binder / "flyctl"
    fly.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{recorded}"\n'
        "printf '[{\"name\":\"FASTAPI_INTERNAL_KEY\"},{\"name\":\"STRIPE_PRICE_IDS\"},'"
        "'{\"name\":\"GIT_SHA\"},{\"name\":\"GITHUB_CLIENT_ID\"}]'\n"
    )
    fly.chmod(0o755)
    e: dict[str, str] = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    # ⛔ Mapping overlay, not `e.update(...)` — the whole-tree untyped-receiver ratchet
    # counts ordinary-word `.update(` receivers, and this file is a new member of it.
    e = {
        **e,
        "PATH": f"{binder}:{os.environ['PATH']}",
        "FLY_SECRETS_CMD": env["FLY_SECRETS_CMD"],
        "FLY_MANAGED_SECRETS_FILE": str(MANIFEST),
        "DEPLOY_WORKFLOW": str(WORKFLOW),
        "FLY_TOML": str(FLY_TOML),
        "FLY_MITIGATION_LEDGER": str(EMPTY_LEDGER),
    }
    proc = subprocess.run(
        [sys.executable, str(TOOL)], capture_output=True, text=True, env=e, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert recorded.read_text().strip() == " ".join(argv[1:]), (
        f"the tool ran a different command than the workflow pins: {recorded.read_text()!r}"
    )


# ── (i) THE VERDICT SET IS CLOSED, AND THE ROWS ARE MATERIALISED FIRST ────────


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_an_unknown_verdict_never_reads_as_no_drift(tmp_path):
    """rc=127 (no python3) used to fall through into the DRIFT branch.

    With a stale CLEAN reading still at the path, pre-fix the step printed
    `DRIFT — 0`, filed nothing and exited 0: a false green with no reading at all.
    """
    h = _Harness(tmp_path)
    out = h.run(rc="127", reading={"verdict": "CLEAN", "remainder": []})
    assert h.last_rc == 1, out
    assert "UNEXPECTED" in out, out
    assert "issues --method POST" not in h.calls()
    assert "--method PATCH" not in h.calls()


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_zero_count_drift_reading_never_exits_green(tmp_path):
    """rc=1 with an empty `.remainder` is a CONTRADICTION, not a finding-free run.

    The step read `count` and never graded it, so a self-contradictory reading printed
    "DRIFT — 0 undeclared name(s)", the "have been filed/refreshed" warning over ZERO
    filings, and exited 0 — a green tick asserting a finding that does not exist.
    Unreachable while `report()` derives the verdict from the remainder, which is why
    the guard is a shell fact rather than an assumption about the tool.
    """
    h = _Harness(tmp_path)
    h.reset_calls()
    out = h.run(rc="1", reading={"verdict": "DRIFT", "remainder": []})
    assert h.last_rc != 0, f"a zero-count DRIFT reading exited 0:\n{out}"
    assert "::error::" in out, out
    # ⛔ GRADE SOMETHING THE MUTANT MOVES. `"issues --method POST" not in h.calls()`
    # held with the guard present AND with it deleted (a `count=0` reading enumerates
    # no ROWS, so the loop files nothing — 0 POST calls either way): dead weight that
    # read as coverage. The guard's own sentence exists only on that branch, and the
    # mutant replaces it with `DRIFT — 0 undeclared Fly secret name(s)`.
    assert "contradicts its own verdict" in out, (
        f"the zero-count guard did not fire — a self-contradictory reading fell through "
        f"to the DRIFT arm:\n{out}"
    )


def test_an_empty_reading_identity_is_an_error(tmp_path):
    """No path from the read step ⇒ nothing to judge ⇒ red, never a default path."""
    h = _Harness(tmp_path)
    out = h.run(
        rc="1", reading=None, report=tmp_path / "never.json", extra_env={"REPORT": ""}
    )
    assert h.last_rc == 1, out
    assert "no reading path" in out, out
    assert "issues --method POST" not in h.calls()


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_failing_row_enumeration_never_exits_green(tmp_path):
    """A failing row `jq` must not yield an empty loop and a green tick.

    Pre-fix the rows came from `done < <(jq …)` with no error arm: the process
    substitution was empty, the loop body never ran, `failed` stayed 0 and the step
    exited 0 — "not reported" in the same colour as "reported". The stub below
    answers the count and fails only on the rows, so this grades the ROWS arm rather
    than being satisfied by the count guard.
    """
    h = _Harness(tmp_path, jq_tsv_failure=True)
    out = h.run(rc="1", reading=_drift_reading("ALPHA_SECRET"))
    assert h.last_rc != 0, out
    assert "could not enumerate" in out, out
    assert "issues --method POST" not in h.calls()


# ── (j) THE ISSUE BODY MUST NEVER LOSE THE BLOCKING CONSEQUENCE ───────────────


def _reading_with(classes: list[str], name: str = "ALPHA_SECRET") -> dict:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import fly_secret_declaration_report as tool
    finally:
        sys.path.pop(0)
    return {
        "verdict": "DRIFT",
        "app": "fixture-app",
        "remainder": [
            {"name": name, "classes": classes, "fix": tool.declaration_help(name)}
        ],
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_both_classes_issue_still_names_the_blocking_consequence(tmp_path):
    """`classes` is `join("+")`, so `live+staged` failed an `= "live"` test.

    Pre-fix an issue for a name that is BOTH live and stageable told the operator the
    deploy "can stage it" and omitted that it is already on the app refusing every
    deploy — the weaker of the two consequences, on the artifact a human acts on.
    """
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_reading_with(["live", "staged"]))
    calls = h.calls()
    # `Production is frozen` is the LIVE arm's sentence. (`refuses every` is not
    # discriminating — it is also in the issue TITLE, which every tick carries.)
    assert "Production is frozen" in calls, (
        "a live+staged name's issue dropped the blocking consequence — it must say "
        f"the deploy is refused NOW. calls were:\n{calls[:2000]}"
    )
    assert "also" in calls and "stageable" in calls, (
        "the staged half of the finding was dropped for a live+staged name"
    )


@pytest.mark.skipif(shutil.which("jq") is None, reason="the substrate requires jq")
def test_a_staged_only_issue_states_the_staged_consequence(tmp_path):
    """The staged-only arm must not claim the deploy is already refused."""
    h = _Harness(tmp_path)
    h.run(rc="1", reading=_reading_with(["staged"]))
    calls = h.calls()
    assert "can STAGE it" in calls, calls[:2000]
    assert "Production is frozen" not in calls, (
        "a staged-only name claimed a live refusal"
    )


# ── (k) THE RUN SAMPLE — the seam the gate has and the schedule does not ──────

_RUN_SAMPLE_WORKFLOW = """\
name: Deploy Hosted API
jobs:
  deploy:
    steps:
      - name: Set all app secrets on Fly.io
        run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          if [ -n "${{ secrets.GATE_A }}" ] && [ -z "${{ secrets.GATE_B }}" ]; then
            ARGS="$ARGS RUN_SAMPLE_ONLY=x"
          fi
          flyctl secrets set --stage $ARGS
"""

_RUN_SAMPLE_MANIFEST = """\
# fixture: declares the unconditional name only, so a run-only name is undeclared
FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY
"""


def test_the_run_sample_widens_the_staged_set_when_it_is_provided():
    """A guard NEITHER synthetic sample reproduces is visible only in the run sample.

    With no ``GH_SECRETS_PRESENT`` (the SCHEDULED case) the run sample collapses onto
    the all-present one, so ``RUN_SAMPLE_ONLY`` is deploy-time discovery — that is the
    residual the module docstring now states. With the gate's own seam set, the same
    name MUST be reported, because that is the population the gate's UNDECLARED check
    unions in.
    """
    workflow = _fixture("run-sample.yml", _RUN_SAMPLE_WORKFLOW)
    manifest = _fixture("run-sample-manifest.txt", _RUN_SAMPLE_MANIFEST)
    secrets = _secrets_file(["FASTAPI_INTERNAL_KEY"], "run-sample.json")
    loud_json = _TMP / "run-sample-reading.json"

    quiet, _ = _run(secrets, manifest=manifest, workflow=workflow)
    assert quiet.returncode == 0, quiet.stdout + quiet.stderr
    assert "RUN_SAMPLE_ONLY" not in quiet.stdout, (
        "the reporter claimed a run-sample-only name with no run sample to see it in"
    )

    loud, reading = _run(
        secrets,
        manifest=manifest,
        workflow=workflow,
        extra_env={"GH_SECRETS_PRESENT": "GATE_A FASTAPI_INTERNAL_KEY"},
        json_to=loud_json,
    )
    assert loud.returncode == 1, loud.stdout + loud.stderr
    assert reading is not None
    assert [entry["name"] for entry in reading["remainder"]] == ["RUN_SAMPLE_ONLY"], reading


def test_a_report_tool_only_change_selects_core_so_this_guard_actually_runs():
    """`tools/` is in `NON_PYTHON_PREFIXES`, so `_keep_changed` DROPS a tools-only path.

    MEASURED before the `CORE_ALSO` entry: `--changed-files
    tools/fly_secret_declaration_report.py` → no surface, and `select()` takes the
    docs-only early return, so a change to the reporter would run NONE of these
    tests. That is the fail-OPEN direction for this file in particular: its job is
    to decide whether a human is told that a deploy is about to be refused, so the
    diff most worth guarding is an edit to the code that makes that decision
    (#1349/#3332/#3616 silent-drop class). Pinned here, in the file it protects.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["tools/fly_secret_declaration_report.py"], "pull_request", manifest
    )
    assert "core" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    # ⛔ NOT a bare `return`: a branch that asserts nothing cannot red anything.
    assert files != "ALL" or selection.get("full") is True, (
        "test_files == 'ALL' without full=True: an unscoped 'ALL' would make this pin vacuous"
    )
    if files != "ALL":
        assert "test_fly_secret_declaration_report.py" in files, (
            "a diff to tools/fly_secret_declaration_report.py no longer selects its own "
            "pin — the `CORE_ALSO` entry was dropped or the prefix behaviour changed"
        )


def test_an_all_guarded_payload_is_exit_2_exactly_as_the_gate_refuses_it(tmp_path):
    """A payload whose assignments are ALL `-z`-guarded leaves `assigned` EMPTY.

    `payload_partition`'s own docstring names that shape as real (`-z` assigns only
    when the secret is ABSENT), so the gate refuses it: `if not assigned: exit 2`,
    "the payload is empty when every GitHub secret is present". A reporter that
    guarded on `not assigned and not unconditional` reported **CLEAN** here instead —
    a clean reading for a repo the deploy gate refuses outright, which is exactly the
    window #14574 exists to close (measured by the #14585 review, and the reason this
    pin asserts BOTH verdicts rather than the reporter's alone).
    """
    guarded_only = _fixture(
        "guarded-only.yml",
        """\
name: Deploy Hosted API
jobs:
  deploy:
    steps:
      - name: Set all app secrets on Fly.io
        run: |
          ARGS=""
          if [ -z "${{ secrets.ONLY_SECRET }}" ]; then
            ARGS="GUARDED_NAME="
          fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture("manifest-guarded.txt", _MANIFEST + "GUARDED_NAME  workflow\n")
    secrets = _secrets_file(_CLEAN_LIVE, "guarded-only.json")

    proc, reading = _run(
        secrets, manifest=manifest, workflow=guarded_only, json_to=tmp_path / "g.json"
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout
    assert "UNREADABLE" in proc.stderr
    assert reading is not None and reading["verdict"] == "UNREADABLE"

    # …and the gate it mirrors refuses the same tree, for the same reason.
    gate = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        env=_env(
            secrets,
            manifest=manifest,
            workflow=guarded_only,
            present=set(),
        ),
        timeout=120,
    )
    assert gate.returncode == 2, gate.stdout + gate.stderr
    assert "payload is empty" in (gate.stdout + gate.stderr)
