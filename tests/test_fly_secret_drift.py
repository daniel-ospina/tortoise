"""Hermetic tests for .github/scripts/check-fly-secret-drift.py (#4126).

The script exists because a Fly secret with no managing source is invisible:
nothing in CI can compare a hand-set name against "nothing". These tests pin the
bidirectional contract that makes it visible.

Zero network: every seam is env-driven.
  FLY_SECRETS_FILE          fixture JSON — the `flyctl secrets list --json` shape
  FLY_MANAGED_SECRETS_FILE  fixture manifest (`<NAME> <source>` lines)
  DEPLOY_WORKFLOW           fixture deploy workflow
  FLY_TOML                  fixture fly.toml (its `app` line names the app)
  FLY_APP                   app-name override

Exit contract (fail-closed, mirrors check-migration-drift):
  0 clean, 1 drift found, 2 could-not-determine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-fly-secret-drift.py"

# Ambient seams a developer might have exported — popped for full hermeticity.
_AMBIENT = (
    "FLY_SECRETS_FILE",
    "FLY_MANAGED_SECRETS_FILE",
    "FLY_APP",
    "FLY_TOML",
    "DEPLOY_WORKFLOW",
    "FLY_SECRETS_CMD",
)

_FLY_TOML = """\
app = "fixture-app"

[env]
  DECLARED_ENV = "1"
  # NOT_AN_ASSIGNMENT = "1"   <- documented in a comment only; must NOT count
"""

# The fixture workflow mirrors the real deploy-hosted.yml shapes: an ARGS seed
# line, an optionally-guarded append, and a `secrets.X` whose Fly name differs.
_WORKFLOW = """
name: Deploy Hosted API
jobs:
  deploy:
    steps:
      - name: Verify secrets exist
        run: |
          if [ -z "${{ secrets.FASTAPI_INTERNAL_KEY }}" ]; then exit 1; fi
      - name: Set all app secrets on Fly.io
        run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          [ -n "${{ secrets.STRIPE_PRICE_IDS }}" ] && ARGS="$ARGS STRIPE_PRICE_IDS=${{ secrets.STRIPE_PRICE_IDS }}"
          ARGS="$ARGS GIT_SHA=${GITHUB_SHA} GITHUB_CLIENT_ID=${{ secrets.GH_CLIENT_ID }}"
          ARGS="$ARGS LITERALLY_SET=1"
          if [ -n "${{ secrets.A_KEY }}" ] && [ -n "${{ secrets.B_KEY }}" ]; then
            ARGS="$ARGS MULTILINE_FLAG=true"
          fi
          flyctl secrets set --stage $ARGS
"""

_MANIFEST = """\
# fixture manifest
FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY
STRIPE_PRICE_IDS      gh-secret:STRIPE_PRICE_IDS
GIT_SHA               workflow
GITHUB_CLIENT_ID      gh-secret:GH_CLIENT_ID
DECLARED_ENV          fly-toml-env
HAND_SET_FLAG         unmanaged
LITERALLY_SET         workflow
MULTILINE_FLAG        workflow
"""

_TMP = Path(tempfile.mkdtemp(prefix="fly-secret-drift-"))


def _fixture(name: str, text: str) -> Path:
    path = _TMP / name
    path.write_text(text)
    return path


MANIFEST = _fixture("manifest.txt", _MANIFEST)
WORKFLOW = _fixture("deploy-hosted.yml", _WORKFLOW)
FLY_TOML = _fixture("fly.toml", _FLY_TOML)
REAL_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
REAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"
REAL_FLY_TOML = REPO_ROOT / "fly.toml"


def _secrets_file(names: list[str], name: str) -> Path:
    return _fixture(
        name,
        json.dumps([{"name": n, "digest": "deadbeef", "status": "Deployed"} for n in names]),
    )


def _run(
    secrets: Path,
    *,
    manifest: Path = MANIFEST,
    workflow: Path = WORKFLOW,
    toml: Path = FLY_TOML,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    env.update(
        FLY_SECRETS_FILE=str(secrets),
        FLY_MANAGED_SECRETS_FILE=str(manifest),
        DEPLOY_WORKFLOW=str(workflow),
        FLY_TOML=str(toml),
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60
    )


_ALL_DECLARED = [
    "FASTAPI_INTERNAL_KEY",
    "STRIPE_PRICE_IDS",
    "GIT_SHA",
    "GITHUB_CLIENT_ID",
    "DECLARED_ENV",
    "HAND_SET_FLAG",
    "LITERALLY_SET",
    "MULTILINE_FLAG",
]


def test_clean_when_every_fly_secret_is_declared():
    r = _run(_secrets_file(_ALL_DECLARED, "clean.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK: all 8 Fly secret(s) are declared" in r.stdout
    # STRIPE_PRICE_IDS (inline guard) and MULTILINE_FLAG (multi-line `if`) are
    # conditional, not managed.
    assert "2 conditionally propagated" in r.stdout
    assert "STRIPE_PRICE_IDS" in r.stdout and "MULTILINE_FLAG" in r.stdout


def test_undeclared_fly_secret_fails_and_names_it():
    """The #4126 class: a name on Fly that no declaration covers."""
    r = _run(_secrets_file([*_ALL_DECLARED, "TORTOISE_SESSION_LLM_MODEL"], "undeclared.json"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout
    assert "TORTOISE_SESSION_LLM_MODEL" in r.stdout


def test_unmanaged_is_reported_but_does_not_fail():
    r = _run(_secrets_file(_ALL_DECLARED, "debt.json"))
    assert r.returncode == 0
    assert "RECORDED DEBT — 1 secret(s)" in r.stdout
    assert "HAND_SET_FLAG" in r.stdout


def test_empty_secret_list_is_exit_2_not_clean():
    """Fail-closed: a truncated payload must not read as a spotless fleet.

    `[]` makes every `set(fly_names) - set(declared)` empty, so without this the
    guard certifies a fleet it never read.
    """
    r = _run(_fixture("empty.json", "[]"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing to read that as a clean fleet" in r.stderr


def test_gh_secret_referenced_only_in_a_gate_is_stale():
    """A `secrets.X` reference is not propagation unless the Fly var is assigned."""
    manifest = _fixture(
        "gate-only.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
        "NEVER_PROPAGATED  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "NEVER_PROPAGATED"], "gate-only.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "NEVER_PROPAGATED" in r.stdout


def test_gh_secret_declaration_the_workflow_ignores_is_stale():
    manifest = _fixture(
        "stale-gh.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nGONE_KEY  gh-secret:GONE_KEY\n",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "stale-gh.json"), manifest=manifest)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "GONE_KEY" in r.stdout


def test_workflow_declaration_the_deploy_never_assigns_is_stale():
    """The `workflow` branch must be pinned, not merely present."""
    manifest = _fixture(
        "stale-workflow.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nNEVER_ASSIGNED  workflow\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "NEVER_ASSIGNED"], "stale-workflow.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "NEVER_ASSIGNED" in r.stdout


def test_unmanaged_entry_the_workflow_propagates_is_stale():
    manifest = _fixture(
        "stale-unmanaged.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nSTRIPE_PRICE_IDS  unmanaged\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "STRIPE_PRICE_IDS"], "stale-unmanaged.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "STRIPE_PRICE_IDS" in r.stdout


def test_fly_toml_declaration_must_exist_in_fly_toml():
    manifest = _fixture(
        "stale-env.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nNOT_IN_TOML  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "NOT_IN_TOML"], "stale-env.json"), manifest=manifest
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "NOT_IN_TOML" in r.stdout


def test_fly_toml_comment_does_not_satisfy_a_declaration():
    """A name documented only in a fly.toml COMMENT is not assigned."""
    manifest = _fixture(
        "comment-env.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nNOT_AN_ASSIGNMENT  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "NOT_AN_ASSIGNMENT"], "comment-env.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "NOT_AN_ASSIGNMENT" in r.stdout


def test_workflow_assigned_name_not_declared_is_undeclared():
    """The reverse half: a Fly variable the deploy can assign must be declared.

    Undeclared on this side, the run that first sets it passes (the guard runs
    before the propagation step) and every run after hard-fails.
    """
    manifest = _fixture(
        "missing-workflow-name.txt", "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "missing-workflow-name.json"), manifest=manifest
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout
    assert "LITERALLY_SET" in r.stdout and "MULTILINE_FLAG" in r.stdout


def test_multiline_if_guard_is_conditional():
    """A multi-line `if [ -n "${{ secrets.X }}" ]; then` block is a guard too.

    deploy-hosted.yml assigns BACKUP_SWEEP_ENABLED that way; a same-line-only
    detector counted it as managed.
    """
    r = _run(_secrets_file(_ALL_DECLARED, "multiline.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    conditional_block = r.stdout.split("CONDITIONAL PROPAGATION")[1].split("OK:")[0]
    assert "MULTILINE_FLAG" in conditional_block
    assert "LITERALLY_SET" not in conditional_block


def test_gh_secret_never_referenced_is_stale_even_when_assigned():
    """A declaration cannot borrow another name's propagation.

    The Fly variable is assigned unconditionally, but the declared GitHub secret
    name appears nowhere — the declaration has rotted.
    """
    manifest = _fixture(
        "gh-name-absent.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nGIT_SHA  gh-secret:NO_SUCH_GH_SECRET\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "gh-name-absent.json"), manifest=manifest
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "NO_SUCH_GH_SECRET" in r.stdout


def test_assignment_outside_env_table_does_not_satisfy_fly_toml_env():
    """Only an assigned key INSIDE `[env]` counts.

    The real fly.toml has `app = "tortoise-y4mjjq"` and a `[build]` table, so a
    whole-file search would accept a `fly-toml-env` declaration for `app`.
    """
    manifest = _fixture(
        "outside-env.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\napp  fly-toml-env\n",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "outside-env.json"), manifest=manifest)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "'app'" in r.stdout


def test_duplicate_declaration_is_exit_2():
    """Two conflicting sources for one name must not silently last-win."""
    manifest = _fixture(
        "duplicate.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nFASTAPI_INTERNAL_KEY  unmanaged\n",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "duplicate.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "duplicate declaration" in r.stderr


def test_payload_not_a_single_variable_is_exit_2():
    """A payload the scan cannot scope must fail closed, not scan nothing.

    `flyctl secrets set NEWKEY=1` on its own line makes the assignment scope
    unreadable; scanning no lines would silently disable the Fly half.
    """
    wf = _fixture(
        "off-args.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          flyctl secrets set NEWKEY=1 --app fixture-app --stage
""",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "off-args.json"), workflow=wf)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "payload variable" in r.stderr


def test_comment_lines_are_not_propagation():
    """A commented `ARGS="$ARGS X=…"` is documentation, not propagation.

    The real step is comment-dense and its own comment block carries `NAME=`
    tokens; reading them as assignments false-blocks an otherwise correct deploy.
    """
    wf = _fixture(
        "commented.yml",
        _WORKFLOW.replace(
            "          flyctl secrets set --stage $ARGS",
            '          # ARGS="$ARGS COMMENTED_KEY=1"\n'
            "          # fly secrets set REGISTRY_STREAM_KEY=… --app fixture-app\n"
            "          flyctl secrets set --stage $ARGS",
        ),
    )
    r = _run(_secrets_file(_ALL_DECLARED, "commented.json"), workflow=wf)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COMMENTED_KEY" not in r.stdout


def test_nested_guard_does_not_end_the_enclosing_guard():
    """An inner `fi` must not close the outer guard (stack, not a pop-on-any-fi)."""
    wf = _fixture(
        "nested.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          if [ -n "${{ secrets.A_KEY }}" ]; then
            if [ -n "${{ secrets.B_KEY }}" ]; then
              ARGS="$ARGS INNER_KEY=1"
            fi
            ARGS="$ARGS OUTER_KEY=1"
          fi
          if [ "${{ secrets.GH_CLIENT_ID }}" != "" ]; then
            ARGS="$ARGS ALT_FORM_KEY=1"
          fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "nested-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
        "INNER_KEY  workflow\nOUTER_KEY  workflow\nALT_FORM_KEY  workflow\n",
    )
    r = _run(
        _secrets_file(
            ["FASTAPI_INTERNAL_KEY", "INNER_KEY", "OUTER_KEY", "ALT_FORM_KEY"], "nested.json"
        ),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    conditional_block = r.stdout.split("CONDITIONAL PROPAGATION")[1].split("OK:")[0]
    for name in ("INNER_KEY", "OUTER_KEY", "ALT_FORM_KEY"):
        assert name in conditional_block, f"{name} must be conditional, not managed"
    assert "3 conditionally propagated" in r.stdout


def test_malformed_manifest_is_exit_2_not_clean():
    """Fail-closed: an unreadable state is never reported as clean."""
    manifest = _fixture("malformed.txt", "FASTAPI_INTERNAL_KEY\n")
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "malformed.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "manifest unusable" in r.stderr


def test_unknown_source_is_exit_2():
    manifest = _fixture("unknown-source.txt", "FASTAPI_INTERNAL_KEY  magic\n")
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "unknown-source.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unknown source" in r.stderr


def test_secret_list_of_wrong_shape_is_exit_2():
    """A changed flyctl payload must fail closed, not read as an empty fleet."""
    bad = _fixture("not-a-list.json", '{"secrets": []}')
    r = _run(bad)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not a JSON array" in r.stderr


def test_secret_entry_without_a_name_is_exit_2():
    bad = _fixture("nameless.json", '[{"digest": "deadbeef"}]')
    r = _run(bad)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "has no name" in r.stderr


def test_shipped_manifest_is_accepted_by_the_guard_against_the_real_deploy():
    """End-to-end over the SHIPPED artifacts, not fixtures.

    Seeds the secret list from the real manifest's own names and runs the guard
    against the real workflow + real fly.toml. A typo in the shipped manifest
    (a `gh-secret` whose GH name does not exist, a declared name the workflow no
    longer assigns, a `fly-toml-env` that is not an [env] key, a Fly variable the
    workflow can assign but nothing declares) fails HERE — in review — instead
    of hard-blocking the deploy in CI.

    The assertions pin DETECTION, not the transient state #4126 was filed in: a
    fix that creates the GitHub secret (or moves the override to fly.toml) must
    not have to edit this test.
    """
    sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_fly_secret_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    declared = module.read_manifest(REAL_MANIFEST)
    # #4126 must stay represented: the model override is declared under SOME
    # managing source (which one is the open decision, not this test's business),
    # and the hand-set names are recorded rather than silently dropped.
    assert "TORTOISE_SESSION_LLM_MODEL" in declared
    assert declared["SUPABASE_URL"] == "unmanaged"

    real_secrets = _fixture(
        "real-manifest-names.json",
        json.dumps([{"name": n} for n in sorted(declared)]),
    )
    r = _run(real_secrets, manifest=REAL_MANIFEST, workflow=REAL_WORKFLOW, toml=REAL_FLY_TOML)
    assert r.returncode == 0, r.stdout + r.stderr
    # The classification is reported for the filing case, whatever its current
    # home: it is either conditional propagation (today) or managed (once the
    # GitHub secret exists).
    assert "TORTOISE_SESSION_LLM_MODEL" in r.stdout
