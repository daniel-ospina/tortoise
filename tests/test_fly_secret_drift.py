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
import re
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
    "GH_SECRETS_PRESENT",
    "FLY_STUB_TIMEOUT",
)

_FLY_TOML = """\
app = "fixture-app"

[env]
  ENV_ONLY_KEY = "1"
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
ENV_ONLY_KEY          fly-toml-env
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


def _declared_gh_names(manifest: Path) -> set[str]:
    """The GitHub secret names a manifest declares, for the presence fixture."""
    names: set[str] = set()
    for line in manifest.read_text().splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and parts[1].startswith("gh-secret:"):
            names.add(parts[1].split(":", 1)[1])
    return names


def _referenced_secret_names(workflow: Path) -> set[str]:
    """Every ``secrets.X`` a fixture workflow references.

    A fixture's contract is the all-present case, and a guard may read a secret
    that is not itself DECLARED (it gates a name declared with another source) —
    e.g. the fixture's `MULTILINE_FLAG` is gated on `A_KEY`/`B_KEY`. The
    per-run skip rule reads the payload the RUN builds, so those guards must be
    satisfied for the fixture to mean what it says.
    """
    return set(re.findall(r"secrets\.([A-Za-z0-9_]+)", workflow.read_text()))


def _run(
    secrets: Path,
    *,
    manifest: Path = MANIFEST,
    workflow: Path = WORKFLOW,
    toml: Path = FLY_TOML,
    present: set[str] | None = None,
    drop_present: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    env.update(
        FLY_SECRETS_FILE=str(secrets),
        FLY_MANAGED_SECRETS_FILE=str(manifest),
        DEPLOY_WORKFLOW=str(workflow),
        FLY_TOML=str(toml),
    )
    if not drop_present:
        # The deploy step states which GitHub Actions secrets the run carries (no
        # CI token can list them). Unless a test says otherwise, the fixtures mean
        # "every secret this workflow reads exists".
        if present is None:
            present = _declared_gh_names(manifest) | _referenced_secret_names(workflow)
        env["GH_SECRETS_PRESENT"] = " ".join(sorted(present))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60
    )


# The names the fixture Fly app carries. ENV_ONLY_KEY is deliberately absent: it
# is declared fly-toml-env, and a Fly secret would shadow [env].
_ALL_DECLARED = [
    "FASTAPI_INTERNAL_KEY",
    "STRIPE_PRICE_IDS",
    "GIT_SHA",
    "GITHUB_CLIENT_ID",
    "LITERALLY_SET",
    "MULTILINE_FLAG",
]


def test_clean_when_every_fly_secret_is_declared():
    r = _run(_secrets_file(_ALL_DECLARED, "clean.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK: all 6 Fly secret(s) are declared" in r.stdout
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


def test_unmanaged_is_a_failure_not_recorded_debt():
    """#4126 acceptance: a Fly secret with NO managing source must FAIL the gate.

    The gate used to print the debt and exit 0 — i.e. it passed on the exact state
    #4126 was filed for (TORTOISE_SESSION_LLM_MODEL + the hand-set names existed
    only on Fly, and nothing failed). This is the mutation anchor for that fix:
    make `unmanaged` benign again and this test goes red.
    """
    manifest = _fixture(
        "debt-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nHAND_SET_FLAG  unmanaged\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "HAND_SET_FLAG"], "debt.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNSOURCED" in r.stdout and "HAND_SET_FLAG" in r.stdout


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


def test_assignment_outside_the_accumulator_is_still_seen():
    """Execution sees a payload built WITHOUT the accumulator variable.

    The old parse-based scan keyed on the literal `ARGS` and missed
    `flyctl secrets set NEWKEY=1` entirely; executing the block cannot.
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
    manifest = _fixture(
        "off-args-manifest.txt", "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "off-args.json"), manifest=manifest, workflow=wf
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout and "NEWKEY" in r.stdout


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


def test_fly_toml_env_shadowed_by_a_fly_secret_is_stale():
    """A Fly secret shadows fly.toml `[env]`, so that declaration is not the source."""
    manifest = _fixture(
        "shadowed-env.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "ENV_ONLY_KEY"], "shadowed-env.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "shadows [env]" in r.stdout


def test_shell_spelling_of_the_payload_variable_is_accepted():
    """`${ARGS}` is the same payload; it must not exit 2."""
    wf = _fixture(
        "braces.yml",
        _WORKFLOW.replace("flyctl secrets set --stage $ARGS", "flyctl secrets set --stage ${ARGS}"),
    )
    r = _run(_secrets_file(_ALL_DECLARED, "braces.json"), workflow=wf)
    assert r.returncode == 0, r.stdout + r.stderr


def test_same_line_shell_variable_is_not_a_fly_secret():
    """`ARGS_SAVED="$ARGS"` must not be read as declaring ARGS_SAVED."""
    wf = _fixture(
        "saved.yml",
        _WORKFLOW.replace(
            "          flyctl secrets set --stage $ARGS",
            '          ARGS_SAVED="$ARGS"\n          flyctl secrets set --stage $ARGS',
        ),
    )
    r = _run(_secrets_file(_ALL_DECLARED, "saved.json"), workflow=wf)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ARGS_SAVED" not in r.stdout


def test_alternate_guard_spellings_are_conditional():
    """`test -n …&&`, `[[ … ]]` and a one-line `if` are guards too.

    The default is CONDITIONAL: anything that could guard an assignment leaves it
    conditional, so no shell spelling can hide the #4126 state.
    """
    wf = _fixture(
        "spellings.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          test -n "${{ secrets.T_KEY }}" && ARGS="$ARGS TEST_FORM_KEY=1"
          [[ -n "${{ secrets.D_T_KEY }}" ]] && ARGS="$ARGS DOUBLE_BRACKET_KEY=1"
          if [ -n "${{ secrets.ONE_KEY }}" ]; then ARGS="$ARGS ONE_LINER_KEY=1"; fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "spellings-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
        "TEST_FORM_KEY  workflow\nDOUBLE_BRACKET_KEY  workflow\nONE_LINER_KEY  workflow\n",
    )
    r = _run(
        _secrets_file(
            ["FASTAPI_INTERNAL_KEY", "TEST_FORM_KEY", "DOUBLE_BRACKET_KEY", "ONE_LINER_KEY"],
            "spellings.json",
        ),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    conditional_block = r.stdout.split("CONDITIONAL PROPAGATION")[1].split("OK:")[0]
    for name in ("TEST_FORM_KEY", "DOUBLE_BRACKET_KEY", "ONE_LINER_KEY"):
        assert name in conditional_block, f"{name} must be conditional"
    assert "3 conditionally propagated" in r.stdout


def test_gh_secret_declaration_must_feed_that_variable():
    """A declared GitHub secret must be the one feeding the variable's assignment."""
    wf = _fixture(
        "linked.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          if [ -z "${{ secrets.WRONG_KEY }}" ]; then echo missing; fi
      - run: |
          ARGS="FOO=${{ secrets.RIGHT_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture("linked-manifest.txt", "FOO  gh-secret:WRONG_KEY\n")
    r = _run(_secrets_file(["FOO"], "linked.json"), manifest=manifest, workflow=wf)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "does not feed the assignment" in r.stdout


def test_payload_with_no_assignment_is_exit_2():
    """Zero scanned assignments would silently disable the reverse half."""
    wf = _fixture(
        "no-assignments.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS=""
          flyctl secrets set --stage $ARGS
""",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "no-assignments.json"), workflow=wf)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "empty when every GitHub secret is present" in r.stderr


def test_step_level_if_is_exit_2_not_certified():
    """A YAML `if:` on the payload step is an UNVERIFIABLE state → exit 2.

    It is not part of the `run:` shell, so execution cannot see it — and the
    checker cannot evaluate it either. Certifying the names anyway (as merely
    CONDITIONAL, exit 0) is the #4126 state: on a run where the condition is
    false nothing propagates and every Fly value is hand-managed, while the gate
    is green. `skip-fly-secret-provenance` cannot bypass exit 2, which is the
    point: the workflow must move the condition inside the shell, where the
    execution-based classifier sees the guard.
    """
    wf = _fixture(
        "step-if.yml",
        """name: w
jobs:
  j:
    steps:
      - if: ${{ github.event_name == 'workflow_dispatch' }}
        run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "step-if.json"),
        manifest=_fixture(
            "step-if-manifest.txt",
            "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
        ),
        workflow=wf,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "step-level `if:`" in r.stderr


def test_assignment_visible_only_in_the_run_sample_is_undeclared():
    """A name assigned only under a composite guard the samples do not model.

    `[ -n A ] && [ -z B ]` reproduces in NEITHER the all-present nor the
    none-present payload, so the name is visible only in the payload the RUN
    builds — and the deploy assigns it exactly as much as any other. The reverse
    checks unioned only the synthetic samples, so it was invisible (#4259
    review).
    """
    wf = _fixture(
        "hidden.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="BASE=${{ secrets.BASE }}"
          [ -n "${{ secrets.A }}" ] && [ -z "${{ secrets.B }}" ] && ARGS="$ARGS HIDDEN_NAME=1"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture("hidden-manifest.txt", "BASE  gh-secret:BASE\n")
    r = _run(
        _secrets_file(["BASE"], "hidden.json"),
        manifest=manifest,
        workflow=wf,
        present={"BASE", "A"},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout and "HIDDEN_NAME" in r.stdout


def test_fly_toml_env_name_assigned_only_in_the_run_sample_is_stale():
    """The fly-toml-env reverse check reads the run sample too.

    Same composite-guard shape: the deploy creates the Fly secret that shadows
    the versioned `[env]` value, so the declaration is wrong — and the synthetic
    samples cannot see it (#4259 review).
    """
    wf = _fixture(
        "hidden-env.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="BASE=${{ secrets.BASE }}"
          [ -n "${{ secrets.A }}" ] && [ -z "${{ secrets.B }}" ] && ARGS="$ARGS ENV_ONLY_KEY=1"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "hidden-env-manifest.txt",
        "BASE  gh-secret:BASE\nENV_ONLY_KEY  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["BASE"], "hidden-env.json"),
        manifest=manifest,
        workflow=wf,
        present={"BASE", "A"},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ENV_ONLY_KEY" in r.stdout and "shadows [env]" in r.stdout


def test_fly_toml_env_boundary_is_the_env_table_only():
    """A key assigned in another table (`[[vm]]`/`[build]`) is not `[env]`.

    The real fly.toml has `[build]`, `[checks]` and `[[vm]]` tables, so a
    whole-file search would accept a `fly-toml-env` declaration for `cpus`.
    """
    toml = _fixture(
        "tables.toml",
        'app = "fixture-app"\n\n[build]\n  dockerfile = "Dockerfile.hosted"\n\n'
        '[env]\n  ENV_ONLY_KEY = "1"\n\n[[vm]]\n  cpus = 2\n',
    )
    manifest = _fixture(
        "tables-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\ncpus  fly-toml-env\n",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "tables.json"), manifest=manifest, toml=toml)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "cpus" in r.stdout


def test_malformed_manifest_is_exit_2_not_clean():
    """Fail-closed: an unreadable state is never reported as clean."""
    manifest = _fixture("malformed.txt", "FASTAPI_INTERNAL_KEY\n")
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "malformed.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "manifest unusable" in r.stderr


def test_bare_gh_secret_source_is_exit_2_not_exit_1():
    """A `gh-secret` with no `:NAME` must be EXIT 2, never the bypassable 1.

    It used to reach `source.split(":", 1)[1]` → IndexError → an uncaught crash,
    and Python's crash exit code is 1 — the very code the deploy step translates
    into a `::warning::` bypass when `skip-fly-secret-provenance` is set. A
    malformed declaration could therefore disarm the gate silently (#4126 review).
    """
    manifest = _fixture("bare-gh.txt", "FASTAPI_INTERNAL_KEY  gh-secret\n")
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "bare-gh.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "gh-secret needs the GitHub secret name" in r.stderr
    # ...and it is the DECLARATION check that says so, not the __main__ backstop
    # that maps an escaped exception to exit 2 — pinning the backstop alone would
    # let this defect hide behind it.
    assert "unexpected" not in r.stderr, r.stderr


def test_gh_secret_argument_on_a_non_gh_source_is_exit_2():
    manifest = _fixture("workflow-arg.txt", "FASTAPI_INTERNAL_KEY  workflow:typo\n")
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "workflow-arg.json"), manifest=manifest)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "takes no ':' argument" in r.stderr


def test_echo_on_the_step_stdout_is_not_an_assignment():
    """The stub reports argv on fd 3; the block's own stdout is never parsed.

    The stub used to print the flyctl argv to STDOUT — the same stream the step's
    `echo` notices use — so a payload could certify an assignment it never made
    (`echo "LEAKED_KEY=1"`, or a `::notice::` line carrying `NAME=`) and the gate
    would treat the name as deploy-propagated (#4126 review).
    """
    wf = _fixture(
        "echo-forge.yml",
        _WORKFLOW.replace(
            "          flyctl secrets set --stage $ARGS",
            '          echo "LEAKED_KEY=1"\n          flyctl secrets set --stage $ARGS',
        ),
    )
    r = _run(_secrets_file(_ALL_DECLARED, "echo-forge.json"), workflow=wf)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LEAKED_KEY" not in r.stdout


def test_secret_marker_does_not_collide_with_a_longer_secret_name():
    """`__SEC_FOO__` matched inside `__SEC_FOO__BAR__` — a marker may not collide.

    The old substitution produced `__SEC_FOO__`, which IS a substring of
    `__SEC_FOO__BAR__`, so a declaration for FOO was "fed by" a value that only
    ever contained BAR. The delimiters are now control characters that no secret
    name can contain (#4126 review).
    """
    wf = _fixture(
        "collide.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="BAR_VAL=${{ secrets.FOO__BAR }}"
          [ -n "${{ secrets.FOO }}" ] && true
          flyctl secrets set --stage $ARGS
""",
    )
    # BAR_VAL is declared as fed by the GH secret `FOO` — which is referenced in
    # the block but feeds BAR_VAL nothing; only `FOO__BAR` does. With the old
    # `__SEC_FOO__` marker this declaration PASSED (the marker is a substring of
    # `__SEC_FOO__BAR__`); the delimited marker makes the linkage check catch it.
    manifest = _fixture("collide-manifest.txt", "BAR_VAL  gh-secret:FOO\n")
    r = _run(_secrets_file(["BAR_VAL"], "collide.json"), manifest=manifest, workflow=wf)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "does not feed the assignment" in r.stdout
    assert "'BAR_VAL'" in r.stdout


def _load_gate_module():
    """Import the gate as a module (for partition-level unit pins)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_fly_secret_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_partition_classification_is_the_symmetric_difference():
    """Only the INTERSECTION of the two samples is unconditionally propagated.

    `assigned` is the payload with every GitHub secret present, `unconditional`
    the payload with none. A `-z`-guarded name is in the second and not the
    first; the old `assigned - unconditional` partition dropped it from BOTH the
    managed and the conditional side, so the report called a guarded name
    managed (#4126 review).
    """
    module = _load_gate_module()
    assigned = {"ALWAYS", "GUARDED"}
    unconditional = {"ALWAYS", "ABSENT_ONLY"}
    assert module.conditional_names(assigned, unconditional) == {"GUARDED", "ABSENT_ONLY"}
    assert module.conditional_names({"ALWAYS"}, {"ALWAYS"}) == set()


def test_absent_only_assignment_must_be_declared():
    """A `-z`-guarded assignment happens on EVERY deploy, so it must be declared.

    `assigned` — the payload with every GitHub secret PRESENT — was the only
    sample feeding the reverse-completeness rule, so a name assigned by a
    `[ -z "${{ secrets.X }}" ]` branch was invisible to it: the deploy sets the Fly
    variable on every run while X is absent, and no rule ever asks for a
    declaration. Both samples are now consulted (#4126 review — the non-monotone
    read of the payload).
    """
    wf = _fixture(
        "absent-undeclared.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          if [ -z "${{ secrets.A_KEY }}" ]; then ARGS="$ARGS ABSENT_ONLY_KEY=1"; fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "absent-undeclared-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "absent-undeclared.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout and "ABSENT_ONLY_KEY" in r.stdout


def test_fly_toml_env_name_assigned_only_without_the_secret_is_stale():
    """The fly-toml-env reverse check reads BOTH samples, not just `assigned`.

    The deploy may assign the name only in its no-secret run (`-z` guard) — that
    still creates the Fly secret that shadows [env] on every such deploy, so the
    declaration is already wrong. Checking `assigned` alone left the case green:
    the name fell out of `assigned`, never entered `conditional`, and was counted
    as managed (#4126 review).
    """
    wf = _fixture(
        "absent-only.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          if [ -z "${{ secrets.A_KEY }}" ]; then ARGS="$ARGS ENV_ONLY_KEY=1"; fi
          flyctl secrets set --stage $ARGS
""",
    )
    # ENV_ONLY_KEY is declared fly-toml-env and is NOT on Fly — so only the
    # assignment check can catch that the deploy still sets it as a Fly secret.
    manifest = _fixture(
        "absent-only-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "absent-only.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ENV_ONLY_KEY" in r.stdout
    assert "shadows [env]" in r.stdout


def test_fly_toml_env_name_assigned_by_the_deploy_is_stale():
    """The reverse check on the fly-toml-env branch.

    [env] is the source only while no Fly secret shadows it — and the deploy
    assigning the name CREATES that secret, so the declaration is wrong from the
    first run even before the secret exists. Only the forward check existed
    (is an [env] key, not currently a Fly secret), so this passed (#4126 review).
    """
    wf = _fixture(
        "toml-env-assigned.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }} ENV_ONLY_KEY=1"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "toml-env-assigned-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "toml-env-assigned.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION" in r.stdout and "ENV_ONLY_KEY" in r.stdout


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
    assert "no usable string name" in r.stderr


# ── The GitHub-secret presence half — the incident's own shape ────────────────
# #4126 happened because `[ -n "${{ secrets.TORTOISE_SESSION_LLM_MODEL }}" ] &&
# ARGS=...` was a permanent no-op: the GitHub secret never existed, so the
# hand-set Fly value survived every deploy. A guard alone is not harm (with the
# secret present it propagates every run); the harm is the ABSENCE, which only
# the deploy run can see — hence `GH_SECRETS_PRESENT`.

_GUARDED_WORKFLOW = """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS=""
          [ -n "${{ secrets.GUARDED_KEY }}" ] && ARGS="$ARGS GUARDED_KEY=${{ secrets.GUARDED_KEY }}"
          flyctl secrets set --stage $ARGS
"""

_GUARDED_MANIFEST = "GUARDED_KEY  gh-secret:GUARDED_KEY\n"


def test_guarded_declaration_with_no_github_secret_is_unsourced():
    """The #4126 incident's exact shape: the guard's GitHub secret is absent.

    The guarded propagation is then a no-op on EVERY deploy and the Fly value is
    hand-managed. The gate used to print this as CONDITIONAL PROPAGATION and exit
    0 — i.e. the incident's own name passed (#4259 review P1).
    """
    wf = _fixture("guarded.yml", _GUARDED_WORKFLOW)
    manifest = _fixture("guarded-manifest.txt", _GUARDED_MANIFEST)
    r = _run(
        _secrets_file(["GUARDED_KEY"], "guarded-absent.json"),
        manifest=manifest,
        workflow=wf,
        present=set(),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNSOURCED" in r.stdout and "GUARDED_KEY" in r.stdout


def test_guarded_declaration_with_the_github_secret_present_is_not_drift():
    """The same guarded line with the secret present propagates on every deploy.

    The two cases must not be conflated: only the ABSENCE is the #4126 defect,
    and only the deploy run can observe it.
    """
    wf = _fixture("guarded-present.yml", _GUARDED_WORKFLOW)
    manifest = _fixture("guarded-present-manifest.txt", _GUARDED_MANIFEST)
    r = _run(
        _secrets_file(["GUARDED_KEY"], "guarded-present.json"),
        manifest=manifest,
        workflow=wf,
        present={"GUARDED_KEY"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 conditionally propagated" in r.stdout


def test_absent_github_secret_for_a_name_not_on_fly_is_not_drift():
    """Not-yet-configured is not drift: nothing on Fly can be hand-managed."""
    wf = _fixture(
        "guarded-half.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          [ -n "${{ secrets.GUARDED_KEY }}" ] && ARGS="$ARGS GUARDED_KEY=${{ secrets.GUARDED_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "guarded-half-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
        "GUARDED_KEY  gh-secret:GUARDED_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "guarded-half.json"),
        manifest=manifest,
        workflow=wf,
        present={"FASTAPI_INTERNAL_KEY"},
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_unset_presence_probe_is_exit_2_not_clean():
    """Fail-closed: without the probe a declaration cannot be verified.

    Treating an unset probe as "every secret exists" would silently reopen the
    hole whenever a workflow edit dropped the export (#4259 review P1).
    """
    r = _run(_secrets_file(_ALL_DECLARED, "probe.json"), drop_present=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GH_SECRETS_PRESENT is not set" in r.stderr
    assert "unexpected" not in r.stderr, r.stderr


def test_second_payload_block_is_scanned_too():
    """A later `flyctl secrets set` step assigns Fly variables as well.

    Only the FIRST payload block used to be read, so a name assigned by a second
    step was never declared, never detected, and the bidirectional contract was
    one-sided (#4259 review P2).
    """
    wf = _fixture(
        "two-blocks.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
      - run: |
          flyctl secrets set --stage EXTRA_SECRET=literal
""",
    )
    manifest = _fixture(
        "two-blocks-manifest.txt", "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "two-blocks.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNDECLARED" in r.stdout and "EXTRA_SECRET" in r.stdout


def test_hanging_payload_is_exit_2_not_a_crash():
    """A payload that never finishes is unclassifiable → exit 2.

    An uncaught TimeoutExpired would exit 1 — the code the deploy step translates
    into a bypass (#4259 review P2).
    """
    wf = _fixture(
        "hang.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          while :; do :; done
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "hang-manifest.txt", "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "hang.json"),
        manifest=manifest,
        workflow=wf,
        extra_env={"FLY_STUB_TIMEOUT": "2"},
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "did not finish" in r.stderr
    assert "unexpected" not in r.stderr, r.stderr


def test_assignment_gated_on_another_absent_secret_is_unsourced():
    """A guard on ANY secret the run does not carry leaves the Fly value alone.

    The declared secret's own presence is not the question — the payload the RUN
    builds is. Gating a name on a DIFFERENT secret (a natural future edit, e.g.
    "all provider keys present") would otherwise certify as merely conditional
    (#4259 review).
    """
    wf = _fixture(
        "cross-guard.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS=""
          [ -n "${{ secrets.GUARD }}" ] && ARGS="$ARGS FOO=${{ secrets.FOO }}"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture("cross-guard-manifest.txt", "FOO  gh-secret:FOO\n")
    absent_guard = _run(
        _secrets_file(["FOO"], "cross-guard-absent.json"),
        manifest=manifest,
        workflow=wf,
        present={"FOO"},
    )
    assert absent_guard.returncode == 1, absent_guard.stdout + absent_guard.stderr
    assert "UNSOURCED" in absent_guard.stdout and "FOO" in absent_guard.stdout
    present_guard = _run(
        _secrets_file(["FOO"], "cross-guard-present.json"),
        manifest=manifest,
        workflow=wf,
        present={"FOO", "GUARD"},
    )
    assert present_guard.returncode == 0, present_guard.stdout + present_guard.stderr


def test_workflow_declaration_skipped_in_this_run_is_unsourced():
    """A `workflow` name the guarded payload skips in this run is hand-managed.

    The shipped `BACKUP_SWEEP_ENABLED` is a composite flag: if any secret it
    composes is missing, the assignment is skipped and Fly keeps the old value
    (#4259 review).
    """
    wf = _fixture(
        "composite.yml",
        """name: w
jobs:
  j:
    steps:
      - run: |
          ARGS=""
          if [ -n "${{ secrets.SECRET_VALUE }}" ]; then ARGS="$ARGS COMPOSITE_FLAG=true"; fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture("composite-manifest.txt", "COMPOSITE_FLAG  workflow\n")
    r = _run(
        _secrets_file(["COMPOSITE_FLAG"], "composite.json"),
        manifest=manifest,
        workflow=wf,
        present=set(),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNSOURCED" in r.stdout and "COMPOSITE_FLAG" in r.stdout


def test_non_string_secret_name_is_exit_2_not_a_crash():
    """A wrong-typed name must fail closed (exit 2), not crash (exit 1).

    Truthiness alone accepted a non-string name, which then raised TypeError in a
    set/sort — an uncaught exception, so the process exited 1, the code the deploy
    step translates into a bypass (#4259 review).
    """
    r = _run(_fixture("bad-name.json", '[{"name": ["FASTAPI_INTERNAL_KEY"]}]'))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no usable string name" in r.stderr
    # The SHAPE check must be the thing that catches it: without the assertion
    # below, the __main__ backstop would hide a regression in the shape check
    # behind its own generic exit-2.
    assert "unexpected" not in r.stderr, r.stderr


def test_every_yaml_spelling_of_a_step_if_is_exit_2():
    """The step `if:` is a YAML KEY, so its spelling cannot matter.

    A positional line regex certified four spellings at exit 0 — the value on the
    next line, `if :` (space before the colon), `"if":` (quoted key), and `if:`
    written AFTER `run:` — each of which the deploy honours and the checker then
    ignored (#4259 review). PyYAML resolves the key, so none of them can slip by.
    """
    call = (
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        "          flyctl secrets set --stage $ARGS\n"
    )
    spellings = {
        "value on the next line": "      - if:\n          ${{ 1 == 1 }}\n        run: |\n",
        "space before the colon": "      - if : ${{ 1 == 1 }}\n        run: |\n",
        "quoted key": '      - "if": ${{ 1 == 1 }}\n        run: |\n',
        "written after run": None,
    }
    for label, header in spellings.items():
        if header is None:
            body = "      - run: |\n" + call + "        if: ${{ 1 == 1 }}\n"
        else:
            body = header + call
        wf = _fixture("spelled-if.yml", f"name: w\njobs:\n  j:\n    steps:\n{body}")
        r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "spelled-if.json"), workflow=wf)
        assert r.returncode == 2, f"{label}: rc={r.returncode}\n{r.stdout}{r.stderr}"
        assert "step-level `if:`" in r.stderr, label


def test_job_level_if_on_another_job_is_exit_2():
    """The payload sits in ANOTHER job, gated: it can be skipped while we are green."""
    wf = _fixture(
        "job-if-other.yml",
        """name: w
jobs:
  gate:
    steps:
      - run: python3 .github/scripts/check-fly-secret-drift.py
  secrets:
    if: ${{ 1 == 1 }}
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    r = _run(_secrets_file(["FASTAPI_INTERNAL_KEY"], "job-if-other.json"), workflow=wf)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "job-level `if:`" in r.stderr


def test_job_level_if_on_the_gates_own_job_is_not_rejected():
    """The SHIPPED shape: the gate and the payload share one gated job.

    A job-level `if:` there cannot forge a certificate — if the job is skipped the
    gate never ran, so nothing is certified. Rejecting it would block the real
    deploy (the shipped `deploy-api` job carries one).
    """
    wf = _fixture(
        "job-if-same.yml",
        """name: w
jobs:
  deploy:
    if: ${{ github.ref == 'refs/heads/main' }}
    steps:
      - run: python3 .github/scripts/check-fly-secret-drift.py
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    # The manifest must match THIS workflow: the shared MANIFEST fixture declares
    # seven names this synthetic job never assigns, which would read as five
    # STALE DECLARATION violations (exit 1) and mask the behaviour under test.
    manifest = _fixture(
        "job-if-same-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "job-if-same.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_step_env_is_modelled_in_the_run_sample():
    """The payload shell inherits an `env:` value, and an UNMODELLED read fails closed.

    The stub env used to carry only PATH/HOME/GITHUB_SHA, so a `[ -z "$VAR" ]`
    guard read differently than in the deploy and the run sample disagreed with
    reality (#4259 review). A step `env:` is now modelled; a variable that is NOT
    modelled (a `$GITHUB_ENV` value from an earlier step, a runner-provided one)
    makes the assignment unclassifiable, so it is exit 2 — never a green
    certificate guessed from an empty value.
    """
    body = (
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        '          if [ -z "$VAR" ]; then ARGS="$ARGS ENV_ONLY_KEY=1"; fi\n'
        "          flyctl secrets set --stage $ARGS\n"
    )
    manifest = _fixture(
        "step-env-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    without = _fixture(
        "step-env-none.yml", f"name: w\njobs:\n  j:\n    steps:\n      - run: |\n{body}"
    )
    empty = _fixture(
        "step-env-empty.yml",
        f'name: w\njobs:\n  j:\n    steps:\n      - env:\n          VAR: ""\n'
        f"        run: |\n{body}",
    )
    with_env = _fixture(
        "step-env-set.yml",
        f'name: w\njobs:\n  j:\n    steps:\n      - env:\n          VAR: "1"\n'
        f"        run: |\n{body}",
    )
    # No env → `$VAR` is unmodelled → the assignment cannot be determined.
    r_none = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "step-env-none.json"),
        manifest=manifest,
        workflow=without,
    )
    assert r_none.returncode == 2, r_none.stdout + r_none.stderr
    assert "cannot determine" in r_none.stderr
    # env VAR="" → the guard is TRUE → the deploy assigns ENV_ONLY_KEY, creating
    # the Fly secret that shadows the versioned [env] value.
    r_empty = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "step-env-empty.json"),
        manifest=manifest,
        workflow=empty,
    )
    assert r_empty.returncode == 1, r_empty.stdout + r_empty.stderr
    assert "shadows [env]" in r_empty.stdout
    # env VAR=1 → the guard is FALSE → not assigned → no drift.
    r_set = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "step-env-set.json"),
        manifest=manifest,
        workflow=with_env,
    )
    assert r_set.returncode == 0, r_set.stdout + r_set.stderr


def test_workflow_level_env_is_modelled_in_the_run_sample():
    """A workflow-root `env:` value reaches the payload shell, as it does in Actions.

    Only job/step `env:` was modelled, so a guard reading a workflow-level value
    read it as empty and the run sample could certify an assignment the deploy
    skips (#4259 review).
    """
    body = (
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        '          if [ -z "$WF_FLAG" ]; then ARGS="$ARGS ENV_ONLY_KEY=1"; fi\n'
        "          flyctl secrets set --stage $ARGS\n"
    )
    manifest = _fixture(
        "wf-env-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    wf = _fixture(
        "wf-env.yml",
        f'name: w\nenv:\n  WF_FLAG: "1"\njobs:\n  j:\n    steps:\n      - run: |\n{body}',
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "wf-env.json"),
        manifest=manifest,
        workflow=wf,
    )
    # WF_FLAG=1 → the guard is FALSE → ENV_ONLY_KEY is NOT assigned, so the
    # versioned [env] value is not shadowed.
    assert r.returncode == 0, r.stdout + r.stderr


def test_github_env_written_by_an_earlier_step_fails_closed():
    """A value an earlier step wrote to `$GITHUB_ENV` is unmodellable → exit 2.

    The payload's control flow then depends on a value the gate cannot see, so
    the assignment is unclassifiable — never a green certificate guessed from an
    empty value (#4259 review).
    """
    wf = _fixture(
        "github-env.yml",
        """name: w
jobs:
  j:
    steps:
      - run: echo "GH_FLAG=1" >> "$GITHUB_ENV"
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          if [ -z "$GH_FLAG" ]; then ARGS="$ARGS ENV_ONLY_KEY=1"; fi
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "github-env-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nENV_ONLY_KEY  fly-toml-env\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "github-env.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 2, r.stdout + r.stderr


def test_fly_alias_is_scanned_as_a_payload():
    """`fly secrets set` is the same command as `flyctl secrets set` and must be seen.

    Detection matched only `flyctl`, so a block written with the alias was
    invisible: its names never entered the partition, and a declaration it
    satisfies was reported STALE (#4259 review).
    """
    wf = _fixture(
        "fly-alias.yml",
        "name: w\njobs:\n  j:\n    steps:\n      - run: |\n"
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        "          fly secrets set --stage $ARGS\n",
    )
    manifest = _fixture(
        "fly-alias-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "fly-alias.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_payload_job_needs_dependency_in_another_job_is_exit_2():
    """A `needs:` on a payload job in ANOTHER job can skip it while the gate is green.

    The same fail-open shape as a job-level `if:`: a skipped or failed dependency
    skips the propagation, but the gate ran in its own job (#4259 review).
    """
    wf = _fixture(
        "needs-other.yml",
        """name: w
jobs:
  gate:
    steps:
      - run: python3 .github/scripts/check-fly-secret-drift.py
  dep:
    steps:
      - run: echo build
  secrets:
    needs: [dep]
    steps:
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "needs-other-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "needs-other.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "needs:" in r.stderr


def test_payload_job_needs_in_the_gates_own_job_is_accepted():
    """The SHIPPED shape: the gate and the payload share one job that has `needs:`.

    `deploy-api` carries `needs: [packaging-smoke]`; because the gate runs in that
    same job, a skipped dependency skips the gate too and certifies nothing, so
    the dependency is not a fail-open (#4259 review).
    """
    wf = _fixture(
        "needs-same.yml",
        """name: w
jobs:
  dep:
    steps:
      - run: echo build
  deploy:
    needs: [dep]
    steps:
      - run: python3 .github/scripts/check-fly-secret-drift.py
      - run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    manifest = _fixture(
        "needs-same-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "needs-same.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr



def test_step_marker_on_its_own_line_does_not_false_positive():
    """`-` alone on a line is a valid step marker, and it must not read as an `if:`.

    A positional scan walked past such a marker and matched an unrelated EARLIER
    step's `if:`, blocking the deploy for nothing (#4259 review). Only the payload
    step's own condition may matter.
    """
    wf = _fixture(
        "dash-alone.yml",
        """name: w
jobs:
  j:
    steps:
      - if: ${{ 1 == 1 }}
        run: echo "not the payload"
      -
        run: |
          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"
          flyctl secrets set --stage $ARGS
""",
    )
    # Matching manifest, for the same reason as the job-level test above: the
    # shared MANIFEST fixture would otherwise emit STALE DECLARATION violations.
    manifest = _fixture(
        "dash-alone-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "dash-alone.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_shipped_manifest_matches_the_real_workflow_and_recorded_debt():
    """End-to-end over the SHIPPED artifacts, not fixtures.

    Seeds the secret list from the real manifest's own names and runs the guard
    against the real workflow + real fly.toml. A typo in the shipped manifest
    (a `gh-secret` whose GH name does not exist, a declared name the workflow no
    longer assigns, a `fly-toml-env` that is not an [env] key, a Fly variable the
    workflow can assign but nothing declares) fails HERE — in review — instead
    of hard-blocking the deploy in CI.

    The exit code is derived from the manifest, not pinned: `unmanaged` names NO
    managing source, so the gate FAILS while any remains and the same test goes
    green the moment the last one is retired — without an edit here. What IS
    pinned is that the recorded debt is the ONLY problem: any STALE/UNDECLARED
    violation on the shipped artifacts is a real defect.
    """
    sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_fly_secret_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    declared = module.read_manifest(REAL_MANIFEST)
    # #4126: the model override is now MANAGED — the deploy assigns it
    # unconditionally from the versioned default, so "the GitHub secret is
    # absent" and "deliberately using the default" are no longer
    # indistinguishable, and Fly's hand-set value is overwritten on the next
    # deploy.
    assert declared["TORTOISE_SESSION_LLM_MODEL"] == "workflow"
    # ... and the declaration is TRUE: the deploy assigns it UNCONDITIONALLY from
    # the versioned default. Pinned on the partition, not just the manifest token —
    # reverting deploy-hosted.yml to the pre-#4126 guarded line must turn this red
    # (#4259 review P2: the manifest token alone stayed green on that revert).
    _, unconditional, _, _ = module.payload_partition(
        module.extract_propagation_blocks(REAL_WORKFLOW.read_text()),
        _declared_gh_names(REAL_MANIFEST),
    )
    assert "TORTOISE_SESSION_LLM_MODEL" in unconditional
    # The two names the deploy ADOPTED — their GitHub Actions secrets already
    # existed, so declaring the deploy the source makes them rotatable from
    # version control.
    assert declared["SUPABASE_URL"] == "gh-secret:SUPABASE_URL"
    assert declared["SUPABASE_SERVICE_ROLE_KEY"] == "gh-secret:SUPABASE_SERVICE_KEY"

    debt = {n for n, s in declared.items() if s == "unmanaged"}
    real_secrets = _fixture(
        "real-manifest-names.json",
        json.dumps([{"name": n} for n in sorted(declared)]),
    )
    r = _run(
        real_secrets,
        manifest=REAL_MANIFEST,
        workflow=REAL_WORKFLOW,
        toml=REAL_FLY_TOML,
        # The real runner's view: the GitHub secrets the manifest declares. That
        # deliberately EXCLUDES TORTOISE_SESSION_LLM_MODEL (declared `workflow`,
        # whose GitHub secret does not exist) — which is exactly the state the
        # versioned default was added for, so this fixture models production.
        present=_declared_gh_names(REAL_MANIFEST),
    )
    assert "STALE DECLARATION" not in r.stdout, r.stdout
    assert "UNDECLARED" not in r.stdout, r.stdout
    assert r.returncode == (1 if debt else 0), r.stdout + r.stderr
    if debt:
        assert f"{len(debt)} violation(s)" in r.stdout, r.stdout
        for name in sorted(debt):
            assert f"UNSOURCED — '{name}'" in r.stdout, r.stdout
