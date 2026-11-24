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
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    env.update(
        FLY_SECRETS_FILE=str(secrets),
        FLY_MANAGED_SECRETS_FILE=str(manifest),
        DEPLOY_WORKFLOW=str(workflow),
        FLY_TOML=str(FLY_TOML),
        FLY_MITIGATION_LEDGER=str(EMPTY_LEDGER),
    )
    if present is not None:
        env["GH_SECRETS_PRESENT"] = " ".join(sorted(present))
    if extra_env:
        env.update(extra_env)
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
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
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
    assert reading["remainder"] == [
        {"name": "TORTOISE_LEDGER_LEGACY_ARM", "classes": ["live"]}
    ]
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
    assert reading["remainder"] == [{"name": "GIT_SHA", "classes": ["staged"]}]
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
    assert reading["remainder"] == [
        {"name": "FASTAPI_INTERNAL_KEY", "classes": ["live", "staged"]}
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
        import fly_secret_declaration_report as tool  # noqa: PLC0415 — path-based
    finally:
        sys.path.pop(0)
    help_text = tool.declaration_help("SOME_UNDECLARED_KEY")
    for source, _why in tool.SOURCES:
        assert source in help_text, source
        assert f"SOME_UNDECLARED_KEY" in help_text
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
        import fly_secret_declaration_report as tool  # noqa: PLC0415 — path-based
    finally:
        sys.path.pop(0)
    gate = tool._gate()
    declared = set(gate.read_manifest(REAL_MANIFEST))
    staged = tool.staged_names(gate, REAL_WORKFLOW.read_text())
    assert sorted(staged - declared) == []
