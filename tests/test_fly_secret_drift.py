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

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

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
    # #11122 review F9: the ledger's seams are as exportable as the rest, and an
    # exported `FLY_MITIGATION_TODAY` silently moves the expiry boundary of every
    # unrelated test in this file.
    "FLY_MITIGATION_LEDGER",
    "FLY_MITIGATION_TODAY",
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
#: The ledger a FIXTURE run reads unless it says otherwise (#11332 review round 6).
#:
#: `ledger_path()` falls back to the gate's `DEFAULT_LEDGER` — the SHIPPED, tracked
#: `.github/scripts/fly-secret-mitigations.txt` — when `FLY_MITIGATION_LEDGER` is unset.
#: A fixture manifest paired with the shipped ledger is not a fixture: the gate
#: validates every shipped entry against the manifest it was handed, so the FIRST
#: real entry (the exact action the ledger header tells an operator to take
#: mid-incident) exits 2 across every `_run` call that passes a fixture manifest —
#: measured: 62 failures, on the change whose purpose is to stop the next deploy
#: re-arming the mitigation. The coupling was found and fixed ONCE, at one call site
#: (the F4 comment below); `_run` now pins the CLASS, and the empty fixture is what
#: makes a fixture run hermetic. A test that wants the shipped ledger opts in
#: through `extra_env`.
EMPTY_LEDGER = _fixture("no-ledger.txt", "# empty\n")
REAL_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
REAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"
REAL_FLY_TOML = REPO_ROOT / "fly.toml"
#: The orientation table an operator reads before §2's rotation recipes. Its
#: GitHub Actions column is a CLAIM about the manifest's `gh-secret:` names.
REAL_RUNBOOK = REPO_ROOT / "docs" / "infra-runbook.md"


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


#: A comment starts at `#` ONLY at line start or after whitespace — the gate's own
#: `_COMMENT_RE` (`(?:^|\s)#`), reproduced here on purpose. A plainer
#: `split("#", 1)` truncates the PAYLOAD of a `fly-only:#13209` declaration to
#: `fly-only:`, so an offender message would print a token no manifest line
#: contains — two readers of one grammar disagreeing in the one string an
#: operator is meant to act on (#13370 review, P3).
_MANIFEST_COMMENT_RE = re.compile(r"(?:^|\s)#")


def _manifest_sources(manifest: Path) -> dict[str, str]:
    """`{Fly variable: source token}` for every declaration in the manifest.

    The Fly NAME is the key (not the `gh-secret:<GH_NAME>` payload): a doc row
    names the Fly variable, and for `GITHUB_CLIENT_ID ← gh-secret:GH_CLIENT_ID`
    the two differ — keying on the payload would silently skip that row.
    """
    sources: dict[str, str] = {}
    for raw in manifest.read_text().splitlines():
        marker = _MANIFEST_COMMENT_RE.search(raw)
        parts = (raw[: marker.start()] if marker else raw).split()
        if len(parts) == 2:
            sources[parts[0]] = parts[1]
    return sources


#: A Secrets Matrix row: `| NAME | tortoise-y4mjjq | FalkorDB Cloud | GitHub |`.
_SECRETS_MATRIX_ROW_RE = re.compile(
    r"^\|\s*([A-Z][A-Z0-9_]*)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$",
    re.MULTILINE,
)

#: A Secrets Matrix GitHub Actions cell declares a GitHub home only when it
#: carries the matrix's home marker (`✅`). Everything else — the em dash, ASCII
#: `-`, `n/a`, `none`, an empty cell, or the dash with a trailing note — tells an
#: operator the same thing as `—` does, and for a `gh-secret:` name that is
#: false. Requiring the POSITIVE marker is fail-closed: the earlier `"—" in
#: github` substring test let every other no-home spelling slip through (#8614
#: review, P2), and an unrecognised cell now reds instead of passing by
#: omission. Pinned by
#: `test_the_secrets_matrix_guard_rejects_every_no_home_spelling`.
_GITHUB_HOME_MARKER = "✅"


def _declares_github_home(github_cell: str) -> bool:
    """True when a Secrets Matrix GitHub Actions cell names a GitHub home."""
    return _GITHUB_HOME_MARKER in github_cell

#: A `fly secrets set <NAME>=<value>` whose value is a COMMAND SUBSTITUTION.
#: For a `gh-secret:` name that value cannot be the GitHub secret's (it did not
#: exist when the line ran), so the next deploy re-stages GitHub's value over it.
#: Only `$(…)` is matched — `fly secrets set NAME="$NEW"` (a value first written
#: to GitHub) is the correct shape and must not trip this.
#:
#: The COMMAND is matched in every equivalent spelling the runbook itself uses:
#: `fly` and `flyctl` (`docs/infra-runbook.md` documents the deploy's own call as
#: `flyctl secrets set --stage … --app tortoise-y4mjjq`), and any flags between
#: `set` and the assignment (`--stage`, `-a <app>`, …). The earlier
#: `set\s+([A-Z…])=` anchor crossed neither, so `flyctl secrets set NAME=$(…)`
#: and `fly secrets set --stage NAME=$(…)` both passed the guard named for them
#: (#8614 review, P2 — the silent-revert class). The widening is itself pinned by
#: `test_the_fly_minted_guard_rejects_every_equivalent_spelling`.
_INLINE_GENERATED_SECRET_RE = re.compile(
    r"\bfly(?:ctl)?\s+secrets\s+set\b[^\n]*?([A-Z][A-Z0-9_]*)\s*=\s*\"?\$\("
)


def _secrets_matrix_rows() -> list[tuple[str, str, str, str]]:
    """(name, fly, falkordb, github) for the runbook's Secrets Matrix rows.

    Scoped to the section, so a table elsewhere in the 2k-line runbook cannot
    satisfy (or fail) this test by accident.
    """
    text = REAL_RUNBOOK.read_text()
    section = text.split("## Secrets Matrix", 1)
    assert len(section) == 2, "docs/infra-runbook.md has no `## Secrets Matrix` section"
    body = section[1].split("\n## ", 1)[0]
    return [
        (name, fly.strip(), falkordb.strip(), github.strip())
        for name, fly, falkordb, github in _SECRETS_MATRIX_ROW_RE.findall(body)
    ]


def _referenced_secret_names(workflow: Path) -> set[str]:
    """Every ``secrets.X`` a fixture workflow references.

    A fixture's contract is the all-present case, and a guard may read a secret
    that is not itself DECLARED (it gates a name declared with another source) —
    e.g. the fixture's `MULTILINE_FLAG` is gated on `A_KEY`/`B_KEY`. The
    per-run skip rule reads the payload the RUN builds, so those guards must be
    satisfied for the fixture to mean what it says.
    """
    return set(re.findall(r"secrets\.([A-Za-z0-9_]+)", workflow.read_text()))


def _run_env(
    secrets: Path,
    *,
    manifest: Path = MANIFEST,
    workflow: Path = WORKFLOW,
    toml: Path = FLY_TOML,
    present: set[str] | None = None,
    drop_present: bool = False,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """The environment one fixture run is executed with.

    Split out of `_run` so the harness's own seams are assertable directly: a
    ledger left to the gate's `DEFAULT_LEDGER` fallback is invisible in the
    subprocess result until the shipped ledger is non-empty (#11332 round 6).
    """
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
    env.update(
        FLY_SECRETS_FILE=str(secrets),
        FLY_MANAGED_SECRETS_FILE=str(manifest),
        DEPLOY_WORKFLOW=str(workflow),
        FLY_TOML=str(toml),
        # Hermetic by default — see `EMPTY_LEDGER`. Never omitted: an unset seam
        # resolves to the SHIPPED tracked ledger, which is not a fixture input.
        FLY_MITIGATION_LEDGER=str(EMPTY_LEDGER),
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
    return env


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
    env = _run_env(
        secrets,
        manifest=manifest,
        workflow=workflow,
        toml=toml,
        present=present,
        drop_present=drop_present,
        extra_env=extra_env,
    )
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


def test_secret_valued_step_env_is_modelled():
    """An `env:` value carrying `${{ secrets.X }}` must be modelled in the samples.

    `X` need not appear in the `run:` body, so the "every secret present" sample
    used to substitute the env value as EMPTY while the run sample (built from the
    real secret set) had it — the samples disagreed and a propagated name was
    misreported STALE, hard-blocking a correct deploy (#4259 review).
    """
    body = (
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        '          if [ -n "$FLAG" ]; then ARGS="$ARGS GATED=${{ secrets.GATED }}"; fi\n'
        "          flyctl secrets set --stage $ARGS\n"
    )
    manifest = _fixture(
        "secret-env-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nGATED  gh-secret:GATED\n",
    )
    wf = _fixture(
        "secret-env.yml",
        "name: w\njobs:\n  j:\n    steps:\n      - env:\n"
        "          FLAG: ${{ secrets.ENV_SECRET }}\n"
        f"        run: |\n{body}",
    )
    # The run carries ENV_SECRET (the workflow references it in `env:`), so FLAG
    # is non-empty and GATED IS assigned — the declaration is honoured.
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY", "GATED"], "secret-env.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_global_flags_before_the_subcommand_are_detected():
    """`flyctl --app X secrets set …` is the same payload and must be classified.

    Detection required the subcommand to follow the binary immediately, so a
    block with a leading global flag was invisible — in a multi-block workflow its
    names escaped the partition entirely (#4259 review).
    """
    wf = _fixture(
        "global-flags.yml",
        "name: w\njobs:\n  j:\n    steps:\n      - run: |\n"
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        "          flyctl --app fixture-app secrets set --stage $ARGS\n",
    )
    manifest = _fixture(
        "global-flags-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "global-flags.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_unmodelled_command_in_the_payload_fails_closed():
    """A payload whose control flow depends on an external command cannot be classified.

    The stub PATH carries only fly/flyctl, so `grep` (or any other command) is
    absent; under `set -e` a command-not-found inside an `if`/`!` condition is NOT
    fatal, so the sample silently guessed the empty branch and certified a name
    the real deploy skips (#4259 review).
    """
    wf = _fixture(
        "unmodelled-cmd.yml",
        "name: w\njobs:\n  j:\n    steps:\n      - run: |\n"
        '          ARGS="FASTAPI_INTERNAL_KEY=${{ secrets.FASTAPI_INTERNAL_KEY }}"\n'
        "          if ! flyctl secrets list --json | grep -q FOO; "
        'then ARGS="$ARGS GATED=1"; fi\n'
        "          flyctl secrets set --stage $ARGS\n",
    )
    manifest = _fixture(
        "unmodelled-cmd-manifest.txt",
        "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\nGATED  workflow\n",
    )
    r = _run(
        _secrets_file(["FASTAPI_INTERNAL_KEY"], "unmodelled-cmd.json"),
        manifest=manifest,
        workflow=wf,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "does not model" in r.stderr



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


#: Reading `fly.toml [env]` needs a TOML parser; `module.fly_toml_env_keys`
#: returns only NAMES (that is all the drift gate needs), so the VALUE of a
#: declaration is read here.
def _fly_toml_env_value(name: str) -> str:
    import tomllib

    env = tomllib.loads(REAL_FLY_TOML.read_text(encoding="utf-8"))["env"]
    assert name in env, f"{name} is not assigned in fly.toml [env]"
    value = env[name]
    assert isinstance(value, str), f"{name} in fly.toml [env] is not a string"
    return value


def _client_identity_module():
    """`tortoise/client_identity.py`, loaded by path.

    Loaded by path (not `import tortoise.client_identity`) so this infra suite
    keeps its zero-tortoise-package import shape; the module itself is stdlib
    only, so the direct load is sound.
    """
    import importlib.util

    path = REPO_ROOT / "tortoise" / "client_identity.py"
    spec = importlib.util.spec_from_file_location("client_identity_for_drift", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered BEFORE exec: `client_identity` declares a @dataclass, and
    # dataclasses resolves `cls.__module__` through sys.modules during exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client_identity_resolution(monkeypatch, value: str) -> dict[str, str]:
    """What `tortoise/client_identity.py` resolves for a GIVEN `[env]` value.

    The knob is set to the shipped value, so this is the code's own verdict on
    the exact string the deployed machine will carry — the only comparison that
    can catch BOTH drift directions without forbidding the documented one.
    """
    module = _client_identity_module()
    monkeypatch.setenv(module.ATTESTED_CLIENTS_ENV, value)
    return module.attested_clients()


def _assert_allowlist_is_the_value_the_code_acts_on(value: str, monkeypatch) -> dict[str, str]:
    """The shipped allowlist must be one the CODE ACTS ON, not one it drops.

    Two properties, and deliberately no equality against the empty default
    (#10472 review, F1 — the earlier `== attested_clients()` form was *always*
    `{}`, i.e. a hard prohibition on the activation `fly.toml` documents, and it
    could not tell a deliberate attestation from a typo).

    1. **Shape** — a JSON object of non-empty string keys to non-empty names. A
       truncated edit, a list, a bare string or a blank name reds here.
    2. **The code's own resolution equals the shipped value** — if the module
       would DROP or REWRITE an entry (a blank name, a non-string), the operator
       attested a client the server silently renders \"unverified\" for, which
       is precisely the divergence this assertion exists to catch.

    Empty `{}` (attest nobody, the shipped default) satisfies both; so does a
    deliberate `{\"ct_…\": \"Claude\"}` entry. A malformed value satisfies
    neither.
    """
    shipped = json.loads(value)
    assert isinstance(shipped, dict), (
        f"the versioned allowlist in fly.toml [env] must be a JSON OBJECT of "
        f"{{client_id: display name}}; it parses as {type(shipped).__name__}")
    for client_id, display_name in shipped.items():
        assert isinstance(client_id, str) and client_id.strip(), (
            f"fly.toml [env] allowlist key {client_id!r} is not a non-empty "
            f"client_id")
        assert isinstance(display_name, str) and display_name.strip(), (
            f"fly.toml [env] allowlist entry for {client_id!r} has no display "
            f"name — `tortoise/client_identity.py` DROPS such an entry, so the "
            f"client it means to attest would render unverified")
    resolved = _client_identity_resolution(monkeypatch, value)
    assert resolved == shipped, (
        "the versioned allowlist in fly.toml [env] is not the set "
        "`tortoise/client_identity.py` resolves from it: it attests "
        f"{sorted(shipped)} but the code applies {sorted(resolved)} — the entry "
        "is malformed, or the module's parsing rules changed under it")
    return shipped


def test_shipped_manifest_matches_the_real_workflow_and_recorded_debt(monkeypatch):
    """End-to-end over the SHIPPED artifacts, not fixtures.

    Seeds the secret list from the real manifest's own names and runs the guard
    against the real workflow + real fly.toml. A typo in the shipped manifest
    (a `gh-secret` whose GH name does not exist, a declared name the workflow no
    longer assigns, a `fly-toml-env` that is not an [env] key, a Fly variable the
    workflow can assign but nothing declares) fails HERE — in review — instead
    of hard-blocking the deploy in CI.

    ⛔ The exit code IS pinned now, and the policy is NOT "any violation fails":
    `open_route_b` (defined at :1904) records the route-(b) transitions that are OPEN,
    and for a name in it a `STALE DECLARATION` carrying the `which shadows [env]`
    clause is tolerated and the code pinned to 1 — with a withheld-shadow control
    that must come back 0, so the two fixtures cannot collapse into one run. Every
    OTHER `STALE`/`UNDECLARED` on the shipped artifacts still fails here. ⛔ Do NOT
    restore the older shape (`assert "STALE DECLARATION" not in r.stdout`): that is
    what made this suite green over a shadowed state the deploy gate refuses
    (measured: rc=0 with the assertion, rc=1 with the shadow), which is the false
    PASS this file exists to prevent. `unmanaged` debt is still derived, not pinned:
    the recorded set is what must stay empty.
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
    # The LIVE Fly app after #4126's fix, which is what the gate reads: a
    # `fly-toml-env` name is BY CONTRACT not a Fly secret (a Fly secret of the
    # same name shadows `[env]` — the gate reports that as STALE), so the fixture
    # must not seed those names. Seeding every declared name modelled the pre-fix
    # state and would make this test assert a violation that no longer exists.
    #
    # ⛔ ...EXCEPT the name whose route-(b) transition is STILL OPEN, which is why
    # this fixture carries it. `TORTOISE_EMBEDDER_WARMUP` IS a live Fly secret
    # today: the `fly-toml-env` declaration and the `[env]` value are merged while
    # the secret is unset LAST, after `[env]` is on the machine (#13923, the
    # manifest's "ORDER MATTERS" block). Excluding it modelled the state AFTER the
    # unset — a production state that does not exist yet — so the suite read GREEN
    # over a state the gate REFUSES, which is exactly the false PASS that let a
    # merged branch claim it had unblocked deploys (#13941 owner direction check,
    # measured: RC=0 without the shadow, RC=1 with it). A fixture that assumes the
    # transition away cannot certify the change that depends on it, so it is seeded
    # and its verdict is pinned BY NAME instead of asserted away.
    #
    # ⛔ The tolerated name comes from THIS RECORDED SET, not from the fixture seed.
    # Keyed to the seed it was vacuous: seeding a different `fly-toml-env` name moved the
    # tolerance with it and the mutant passed, tolerating a shadow nobody recorded as the
    # open interval (measured). The set names the issue that carries each transition, so a
    # second open route-(b) cannot appear here without being written down.
    open_route_b = {"TORTOISE_EMBEDDER_WARMUP": "#13923"}
    for name in open_route_b:
        assert declared.get(name) == "fly-toml-env", (
            f"{name} is recorded here as an OPEN route-(b) transition but this manifest "
            f"declares it {declared.get(name)!r}"
            + (
                " — i.e. it is no longer declared at all, so the emergency switch "
                f"lost its provenance line; restore `{name} fly-toml-env`, with "
                f"`{name} = \"0\"` in fly.toml [env]. "
                if declared.get(name) is None
                else ". "
            )
            + "A transition that is not declared `fly-toml-env` has nothing left to "
            "unset, so this arm would be pinning a state the branch does not ship — "
            "re-read the manifest before editing either. ⛔ Removing the lever on "
            "purpose is a DIFFERENT change and not this assertion's to block: delete "
            "the [env] row AND this declaration in ONE merged change, and drop this "
            "name from `open_route_b` here together with its withheld-shadow "
            "control, or the two fixtures collapse into one run."
        )
    living = [n for n in sorted(declared) if not declared[n].startswith("fly-toml-env")]
    real_secrets = _fixture(
        "real-manifest-names.json",
        json.dumps([{"name": n} for n in [*living, *open_route_b]]),
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
        # #11122 review F4: this fixture models a Fly app carrying every declared
        # name, which is exactly the state in which a REAL ledger entry means
        # `MITIGATION NOT IN EFFECT`. Left reading the shipped ledger, this test
        # would assert `returncode == 0` against a state the FIRST real entry makes
        # illegal — so the operator writing one mid-incident would have to edit
        # tests before the mitigation could land, and the cheapest green is to
        # weaken the assertion that models production. The ledger gets its own tests
        # (`TestTheMitigationLedger` — it drives the SHIPPED workflow over a non-empty
        # ledger FIXTURE, and parses the shipped ledger against the shipped manifest;
        # the joint case, shipped workflow over the SHIPPED ledger, is
        # `test_the_shipped_workflow_honours_the_shipped_ledger` — #11372); here it is
        # pinned out of the way.
        extra_env={"FLY_MITIGATION_LEDGER": str(_fixture("no-ledger.txt", "# empty\n"))},
    )
    # ⛔ NOT `assert "STALE DECLARATION" not in r.stdout` while the shadow is live:
    # that assertion is what made the suite green over a state the deploy gate
    # refuses. What this test is entitled to require is narrower and mechanical —
    # a STALE violation is tolerated ONLY for the recorded open transition, only
    # with its shadow clause, and only as the BYPASSABLE exit 1 (an exit 2 can
    # never be bypassed, so it must not pass for any state). Every other kind of
    # violation on the shipped artifacts is still a defect and still fails here.
    # ⛔ THE TOLERANCE HAS NO EXPIRY: nothing here can observe the live Fly app, so a
    # production `secrets unset` changes nothing in this file. When #13923's step 3
    # lands, DELETE this entry from `open_route_b` AND the
    # `r.returncode != control.returncode` control TOGETHER — emptying the set alone
    # makes the subject and the control the SAME fixture, so the control reds with a
    # message that would push a maintainer to weaken the very check the false PASS
    # was fixed with. Until then the suite models the open transition on purpose.
    assert "UNDECLARED" not in r.stdout, r.stdout
    stale = [line for line in r.stdout.splitlines() if "STALE DECLARATION" in line]
    # ⛔ IDENTITY, not a substring: the gate prints the name in single quotes, and a
    # family name already exists in this tree (`TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S`),
    # so `n in line` would tolerate an unrecorded `…_GRACE_S` shadow as if it were this one.
    foreign = [line for line in stale if not any(f"'{n}'" in line for n in open_route_b)]
    assert not foreign, (
        "the shipped artifacts carry a STALE declaration for a name that is not the open "
        f"route-(b) transition — that is a real defect, not the recorded interval:\n{foreign}"
    )
    if stale:
        # ONE assert, because there is one predicate. The message carries BOTH diagnoses,
        # because the other state it can meet — `not an assigned key in fly.toml's [env]
        # table`, i.e. the `[env]` row was deleted — is a real state with a real fix, not a
        # renamed gate message, and it reds HERE first (measured: deleting that row never
        # reaches a later membership assert, which is why none is re-asserted below).
        assert "which shadows [env]" in stale[0], (
            "the tolerated STALE for the open transition is no longer the shadow refusal, so "
            "this test would tolerate a DIFFERENT breakage of the same name. Two states reach "
            "here: (a) the gate RENAMED its message — re-derive it from the gate and update the "
            "runbook, do not widen this; (b) it says `not an assigned key in fly.toml's [env] "
            "table`, which means the `[env]` ROW IS GONE — restore "
            "`TORTOISE_EMBEDDER_WARMUP = \"0\"` in fly.toml [env].\n"
            f"{stale[0]}"
        )
        assert r.returncode == 1, (
            f"the open transition must be the BYPASSABLE exit 1, not {r.returncode} — an exit 2 "
            "cannot be bypassed, so a deploy would be hard-blocked rather than transitioned"
        )
    # Non-vacuity: the arm above must be able to tell the two states apart. With the
    # shadow withheld the SAME fixture must come back CLEAN (exit 0, no STALE), so a
    # gate that stopped sampling the secret list — or a fixture edit that dropped it —
    # reds here instead of passing this test's tolerances silently.
    without_shadow = _fixture(
        "real-manifest-names-no-shadow.json",
        json.dumps([{"name": n} for n in living]),
    )
    control = _run(
        without_shadow,
        manifest=REAL_MANIFEST,
        workflow=REAL_WORKFLOW,
        toml=REAL_FLY_TOML,
        present=_declared_gh_names(REAL_MANIFEST),
        extra_env={"FLY_MITIGATION_LEDGER": str(_fixture("no-ledger.txt", "# empty\n"))},
    )
    # `returncode == 0` is the WHOLE control predicate: the gate prints a violation line only
    # inside the branch that returns 1, so a separate "no STALE in stdout" assert could never
    # fire (it lived here until review round 9 — the same dead-assert class this file deletes
    # elsewhere). If a future gate ever prints without counting, add the belt WITH a comment
    # saying that is what it is.
    assert control.returncode == 0, control.stdout + control.stderr
    assert r.returncode != control.returncode, (
        "the two fixtures must produce DIFFERENT verdicts, or every arm above is vacuous. "
        "If `open_route_b` was just emptied because the transition landed, delete THIS "
        "control with it — the subject and the control are the same fixture then, and a "
        "weaker control is not the fix."
    )
    # #4126's seven recorded-debt names, pinned by KIND so a revert to
    # `unmanaged` (or a dropped declaration) fails HERE, in review, instead of
    # hard-blocking the deploy in CI.
    config_names = {
        # #10093: the ACL layer's declared posture. Undeclared, it is invisible
        # to check-fly-machine-env.py (which iterates ONLY declared
        # `fly-toml-env` names and runs after the release — #4568), so a stale
        # machine can silently revert this lane to `strict` with nothing red.
        # The cost is the BOUNDARY, not a returned 503: #10094 made a refused
        # credential non-fatal, and on this lane the credential IS refused (the
        # runbook's custom ACL omits `+MODULE|LIST`, so the probe is refused and
        # `ACL SETUSER` is never reached). Declared `off`, the reconcile reports
        # `absent_by_design` — a deliberate boundary — with zero server contact;
        # undeclared, every mint probes, is refused, and the reconcile reports
        # `denied`, the credential-FAULT counter. The report then cannot tell a
        # boundary from a broken credential, which is the one thing this lane
        # exists to make tellable.
        # Its REMOVAL must therefore fail here, in review, not in production.
        "TORTOISE_ACL_LAYER",
        "TORTOISE_CONTROL_PLANE",
        "TORTOISE_REAUTH_WINDOW_SECONDS",
        "TORTOISE_MANUAL_LINKING_ENABLED",
    }
    env_keys = module.fly_toml_env_keys(REAL_FLY_TOML.read_text())
    workflow_refs = _referenced_secret_names(REAL_WORKFLOW)
    for name in sorted(config_names):
        assert declared[name] == "fly-toml-env", declared[name]
        assert name in env_keys, f"{name} is not an assigned key in fly.toml [env]"
    # ── #8218 / #10472 review: the consent screen's client-identity allowlist ──
    # `TORTOISE_OAUTH_ATTESTED_CLIENTS` is an AUTHORIZATION-SERVER knob: its only
    # reader is `tortoise/client_identity.py`, which runs inside THIS Fly app. It
    # had been declared in `config/required-bindings.yml` — the CLOUDFLARE PAGES
    # manifest, project `tortoise-dashboard` — where no Function imports it, so it
    # was a `recommended` warning no deploy could ever satisfy, pointing an
    # operator at a deployment that cannot read the value. Its home is here, and
    # these assertions are what make the move load-bearing rather than cosmetic:
    # declared `fly-toml-env`, the deploy applies the versioned value and
    # `check-fly-machine-env.py` asserts the RUNNING machine carries it, while a
    # Fly SECRET of that name (which would shadow `[env]`) fails this very gate.
    attested = "TORTOISE_OAUTH_ATTESTED_CLIENTS"
    assert declared[attested] == "fly-toml-env", declared[attested]
    assert attested in env_keys, f"{attested} is not an assigned key in fly.toml [env]"
    # ... and the DECLARED value is one the code ACTS ON, so the versioned config
    # and `tortoise/client_identity.py` cannot silently diverge. A value added
    # only here but malformed would attest a client the code drops; a value
    # added only in code would never reach the deployed machine (the
    # `fly-toml-env` declaration above is what closes that direction).
    # ⛔ NOT `== attested_clients()` with the knob UNSET: that right-hand side is
    # always `{}`, which forbids the very activation `fly.toml [env]` documents
    # and cannot distinguish a reviewed attestation from a typo (#10472 review F1).
    _assert_allowlist_is_the_value_the_code_acts_on(
        _fly_toml_env_value(attested), monkeypatch)
    for name in ("TORTOISE_AUDIT_DSN", "TORTOISE_LINK_INTENT_SECRET", "SENTRY_DSN"):
        assert declared[name] == f"gh-secret:{name}", declared[name]
        assert name in workflow_refs, f"deploy-hosted.yml never references secrets.{name}"
        assert name in module.workflow_secret_refs(REAL_WORKFLOW.read_text())
    # ── #13721 review Issue 1: the CONSENT KEY SET's AS half ──────────────────
    # §2.3 step 1 instructs an operator to add the new kid to `CONSENT_TOKEN_KEYS`
    # "on **both** halves". Before this arm existed neither new name had a managing
    # source on the Fly app — `CONSENT_TOKEN_SECRET` was the only consent name
    # declared — so that instruction had no route, and the one remaining route (a
    # hand-set value on the app) is exactly what THIS gate refuses as UNDECLARED.
    # The promised rotation window was therefore built in Python and TypeScript and
    # undeliverable to the deployment it exists for, with every check green.
    #
    # The names are READ FROM the module that consumes them, so a rename in
    # `tortoise/consent_token.py` reds here instead of silently orphaning the
    # declaration and re-opening the same hole.
    from tortoise import consent_token as _ct

    assert declared[_ct._SECRET_ENV] == f"gh-secret:{_ct._SECRET_ENV}", declared[_ct._SECRET_ENV]
    assert declared[_ct._KEYS_ENV] == f"gh-secret:{_ct._KEYS_ENV}", declared[_ct._KEYS_ENV]
    assert _ct._KEYS_ENV in workflow_refs, (
        "deploy-hosted.yml never references the key set's GitHub secret, so the set can "
        "only reach the AS by hand — i.e. §2.3 step 1 still has no operable route "
        "(#13721 review, Issue 1)")
    assert _ct._CURRENT_KID_ENV not in workflow_refs, (
        f"{_ct._CURRENT_KID_ENV} is a kid NAME, not secret material — staging it as a Fly "
        "secret would shadow the versioned `[env]` value and freeze the rotation")
    # The kid NAME is a `[A-Za-z0-9_-]{1,64}` name, not secret material, so it takes
    # the versioned `[env]` route — which is also what makes a Fly SECRET of that
    # name fail as a shadow. The KEY MATERIAL takes the opposite route: a set written
    # into `fly.toml` would be a credential in the repository.
    assert declared[_ct._CURRENT_KID_ENV] == "fly-toml-env", declared[_ct._CURRENT_KID_ENV]
    assert _ct._CURRENT_KID_ENV in env_keys, (
        f"{_ct._CURRENT_KID_ENV} is declared fly-toml-env but is not an assigned key in "
        "fly.toml [env] — the declaration cannot be honoured")
    assert _ct._KEYS_ENV not in env_keys, (
        f"{_ct._KEYS_ENV} carries KEY MATERIAL and must never be versioned in fly.toml")
    # The one decision-backed exception — and it must stay an exception, i.e. a
    # ref to the issue carrying the ruling (#661's OVERRIDES marker).
    assert declared["REGISTRY_STREAM_KEY"] == "fly-only:#661"
    assert "REGISTRY_STREAM_KEY" not in workflow_refs, (
        "REGISTRY_STREAM_KEY must stay OUT of the GitHub trust boundary (#661)"
    )
    # ── #13209: the hosted READ-MODE lever, the second decision-backed exception ─
    # `TORTOISE_RECORD_SERVE` is armed by an operator (`flyctl secrets set`) and
    # reverted by the same lever (`flyctl secrets unset`) — the runbook's arming
    # surface, which states the ruling verbatim: "Arming is an OWNER action.
    # Nothing in the code arms it, and no deploy sets it."
    #
    # Pinned by KIND and by DIRECTION, because each wrong route is one plausible
    # edit away and only one of them reds the gate immediately:
    #   * `gh-secret:` would make the deploy the managing source (reversing the
    #     ruling) AND fail closed today — no such GitHub Actions secret exists;
    #   * `fly-toml-env` would version today's value into `fly.toml [env]`, which
    #     both re-introduces a deploy-set value and breaks the published revert
    #     (`fly secrets unset` would return the [env] value, not the shipped
    #     `off` default);
    #   * `unmanaged` is the #4126 defect the gate exists to refuse.
    # So this asserts BOTH halves of "out-of-band": declared `fly-only:`, and
    # present in NEITHER the deploy payload NOR `fly.toml [env]`. A revert to
    # `unmanaged` fails HERE, in review, and so does a "helpful" move of the value
    # into version-controlled env.
    record_serve = "TORTOISE_RECORD_SERVE"
    assert declared[record_serve] == "fly-only:#13209", declared[record_serve]
    assert record_serve not in workflow_refs, (
        "TORTOISE_RECORD_SERVE must stay OUT of the GitHub trust boundary — arming "
        "is an operator action and no deploy sets it (#13209)"
    )
    # A BELT, not a second surface: `workflow_refs` above is built by this file's
    # `_referenced_secret_names` and this line calls the GATE's
    # `workflow_secret_refs`, but both scan with the same `secrets\.([A-Za-z0-9_]+)`
    # regex, so no mutant reds one without the other (measured: the payload mutant
    # co-reds the line above). Kept because the two readers live in different
    # files and a future divergence in EITHER should red HERE — do not read it as
    # independent evidence of a second surface.
    assert record_serve not in module.workflow_secret_refs(REAL_WORKFLOW.read_text())
    assert record_serve not in env_keys, (
        "TORTOISE_RECORD_SERVE is versioned in fly.toml [env], so it is NOT "
        "out-of-band any more — REVERT the [env] entry: the `fly-only:` "
        "declaration IS the recorded decision (#13209), and `fly-toml-env` — the "
        "route its OVERRIDES marker names and refuses — would re-introduce a "
        "deploy-set value"
    )

    # ── #13674 / #13926: the embedder warm-up's EMERGENCY SWITCH ──────────────
    # The route is `fly-toml-env` (route (b) of the #4126 block): the value is
    # version-controlled in `fly.toml [env]` and the manifest names that source,
    # so a flip is a merged change. ⛔ NOT `fly-only:` — that token is RESERVED for
    # a permanent out-of-band secret with a recorded ruling, and #13926 rejected it
    # for exactly this reason. Pinned in both directions, because each wrong route
    # is one plausible "helpful" edit away:
    #   * deleting the declaration, or moving it back to `fly-only:`/`unmanaged` — BOTH
    #     are refused by the `open_route_b` guard ABOVE, which is the live one and
    #     carries both diagnoses (including the removal path). It runs FIRST, so a
    #     separate membership assert below it could never be the thing that reds —
    #     two of them lived here until review round 5, dead, with messages that could
    #     never print. Do not reintroduce a second route assert: extend that guard.
    embedder_warmup = "TORTOISE_EMBEDDER_WARMUP"
    # ⛔ The `[env]`-row membership is NOT re-asserted here. `env_keys` is computed by the
    # same `fly_toml_env_keys` the gate's `fly-toml-env` arm uses, so a deleted `[env]` row
    # reds FIRST as the tolerance's `not an assigned key` refusal (which now carries the
    # restore instruction) — an assert here could never be the one that fires, and that is
    # the dead-assert class review round 5 removed a few lines above this comment.
    assert embedder_warmup not in workflow_refs, (
        "TORTOISE_EMBEDDER_WARMUP must stay OUT of the deploy payload — a value "
        "the deploy sets is not version-controlled config and not an emergency "
        "switch (#13674)"
    )

    assert debt == set(), f"recorded debt remains: {sorted(debt)}"
    # ⛔ The shipped artifacts' exit code FOLLOWS the open transition, and it is the
    # point of the shape above: while the shadow is live the deploy gate refuses this
    # name (exit 1, bypassable only by a per-run input), so a branch that merges this
    # route before the `[env]`-then-unset order is PERFORMED AND VERIFIED cannot claim
    # it unblocked deploys — the claim is now measured here instead of asserted in a
    # PR body over a fixture that omitted the live secret (#13941 owner direction
    # check). The day this entry leaves `open_route_b` this asserts 0 — it does not
    # happen by itself.
    assert r.returncode == (1 if stale else 0), r.stdout + r.stderr


# ── The #13674 line citations (#13814) ───────────────────────────────────────
# ONE artifact still cites `embeddings.py` BY LINE — the runbook's operator
# section. It used to be two: the manifest's `#13674` block carried the same
# numbers until #13926 took that name to route (b) and the deleted block took
# them with it (see SCOPE below) — so a merge that grows that module silently
# turns a cite into a pointer at unrelated code while every sentence around it
# still reads as true. Measured: the origin/main integration that brought #13819
# inserted 102 lines into and deleted 11 from `tortoise/embeddings.py`
# (`git show --numstat c3309ec656 -- tortoise/embeddings.py` -> `102  11`;
# 2695 -> 2786 lines, net +91), and the three gate cites all moved by +65. No
# other check in CI reads a line number, and a reader who follows one lands
# nowhere.
#
# That copy is pinned by the checker below, and it is the one that needed the pin
# most: it is written for an OPERATOR under incident pressure, and no other check
# in CI reads a line number out of it — so without this pin it rots silently
# while the suite stays green.
#
# ⛔ SCOPE AFTER #13926. The manifest's copy is no longer declared by THIS PR: the
# name went to `fly-toml-env` (route (b), `fly.toml [env]`) and the manifest's own
# route-(b) block now documents it, so its cites are that change's to mind. What
# remains pinned here is the RUNBOOK's operator section — the copy an operator
# reads mid-incident, which nothing else in CI reads a line number from. The
# checker is artifact-parameterized (`_reader_citations(artifact=…)`) precisely so
# re-pointing it at another artifact, or at the manifest again, is a marker change
# rather than a second implementation.

_READER_CITE_RE = re.compile(r"`(?::|(?:tortoise/)?[a-z_]+\.(?:py|sh):)(\d+)(?:-(\d+))?`")
#: What a cited line has to contain to be the thing the citation says it is. The
#: SHELL reads its own copy of the variable, so it is spelled out there.
_READER_NEEDLE = {
    "embeddings.py": "_embedder_warmup_enabled",
    "hosted_api.py": "_embedder_warmup_enabled",
    "entrypoint.sh": "TORTOISE_EMBEDDER_WARMUP",
}
#: A numbered reader entry STARTS a new claim, so its symbol is its own. Two
#: spellings are tolerated — a `#   1. …` comment block (the manifest's old copy, gone
#: with the `fly-only:#13674` block) and a `> 1. …` blockquote (the runbook, the only
#: copy left). The ordinal is CAPTURED, because the prose below binds its range cites
#: to it.
_READER_ENTRY_ORDINAL_RE = re.compile(r"^\s*(?:#|>)?\s*(\d+)\.\s")
#: The sentence that ENDS the numbered claims in the runbook (its manifest twin went
#: with the `fly-only:#13674` block this change deletes): the prose after it names no
#: function, so nothing in it may inherit reader 5's symbol. Markdown emphasis is
#: stripped before matching — the runbook bolds "**function names**".
_READER_SYMBOL_RESET_RE = re.compile(
    r"function names? (?:above )?are the invariant", re.IGNORECASE
)
#: The sentence that names, BY ORDINAL, which readers a pair of range cites below
#: belongs to — the runbook says "Number 2 and 3" (the manifest's "Readers 2 and 3" copy
#: went with the block this change deletes).
#: Without this the ranges could only be needle-checked, and the needle passes a
#: range re-pinned onto ANY flag-reading line, the wrong reader included.
_READER_ORDINAL_SENTENCE_RE = re.compile(r"(?:Readers?|Numbers?)\s+(\d+)\s+and\s+(\d+)")
#: The COUNT each artifact states for itself ("FIVE read sites", "gates **five** read
#: sites", "= five read sites"). Bound to the parse below: the stated word must equal
#: the number of numbered entries, because a list that LOST an entry still reads as a
#: claim about every reader the prose names — and `uncovered` cannot see it (the entry
#: took its own ordinal with it). A QUOTED phrase is a mention, not a claim (the runbook
#: says `"three read sites" omits the bound pair` while discussing the wrong unit), so
#: the lookbehind refuses a word inside quotes.
_READER_STATED_COUNT_RE = re.compile(
    "(?<![\"\u201c'`])\\b(one|two|three|four|five|six|seven|eight|nine|ten|\\d{1,2})\\b[\\s*]*read[\\s-]+sites?",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _count_value(word: str) -> int:
    """The integer a stated count spells, in words or digits.

    DIGITS ARE ACCEPTED because the alternative is a silent hole, measured in round 8:
    the runbook states its count twice, so `gates **6** read sites` beside the intact
    `= five read sites` was read as "no word-form claim here" and left the list and the
    prose contradicting each other with the suite green. A digit-only region reds on
    `assert stated` instead — which is the same defect wearing the other face.
    """
    return int(word) if word.isdigit() else _NUMBER_WORDS[word.lower()]


#: A parenthesised list of ordinals in the prose (`(1, 4, 5)`, `(2, 3)`): the runbook
#: describes its own list that way, so these are references to entries and must point
#: at entries that still exist. Numbers in a normal sentence are not matched — the
#: form is specific on purpose, because a guard that reads every digit in prose is a
#: false-red machine.
_READER_ORDINAL_LIST_RE = re.compile(r"\(\s*(\d+(?:\s*,\s*\d+)+)\s*\)")


def _stated_count_text(lines: list[str], start: int, end: int) -> str:
    """The region's prose, UNWRAPPED, with fenced code blocks removed.

    Two normalizations, both earned by a measured false pass:

    * a count inside a fence is an EXAMPLE, not a claim about this list, so fences
      go; the count regex additionally refuses a backticked or quoted mention.
    * the runbook is a WRAPPED blockquote, so `read` and `sites` routinely land on
      different lines — and the count regex used to require them adjacent. Measured,
      round 9: `gates **six** read` / `> sites:` beside the intact `= five read
      sites` stayed green (the contradicting count was simply not matched), and the
      same wrap on a CORRECT artifact reddened as "states no count". Joining the
      lines (dropping the `> ` continuation marker) makes the phrase findable
      wherever the wrap falls.
    """
    kept: list[str] = []
    fenced = False
    for line in lines[start:end]:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return re.sub(r"\s*\n\s*(?:>\s*)?", " ", "\n".join(kept))


#: The two claims the runbook's prose makes about a NUMBERED entry in words rather
#: than by number — `Number 4 is the one an operator is most likely to miss: it is the
#: production pre-warm, not the SDK path`, and `three warm-up surfaces (1, 4, 5) + two
#: readiness bounds (2, 3)`. Bound to the FILE the referenced entry actually cites,
#: because existence is not identity: swapping entries 4 and 5 whole (labels travel
#: with their readers) left every other arm green while `Number 4` pointed at the SHELL
#: pre-warm (measured, round 9). The vocabulary is the artifact's own, mapped to the
#: file it names — deliberately explicit, so a reworded sentence reds instead of
#: silently unbinding.
_READER_ORDINAL_CLAIM_FILE = {
    "production": "hosted_api.py",
    "hosted": "hosted_api.py",
    "readiness": "embeddings.py",
    "bound": "embeddings.py",
    "bounds": "embeddings.py",
}
_READER_ORDINAL_CLAIM_RE = re.compile(
    r"\bNumbers?\s+(\d+)\b([^.\n]{0,120})", re.IGNORECASE
)
#: The GROUP form states the class BEFORE the numbers — `two readiness bounds (2, 3)`
#: — so the words are read from in front of the parentheses, never from behind them
#: (behind is where the NEXT group's class sits, which mis-bound entries 4 and 5 to
#: `embeddings.py` the first time this was written).
_READER_ORDINAL_GROUP_CLAIM_RE = re.compile(
    r"([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,4})\s*\(\s*(\d+(?:\s*,\s*\d+)+)\s*\)",
    re.IGNORECASE,
)


def _resolver_definition_span() -> tuple[int, int]:
    """1-based (first, last) line of the flag resolver's own definition.

    DERIVED FROM THE SOURCE, never written as a literal here: the runbook tells a
    maintainer that "the line numbers are a convenience and they move whenever
    `embeddings.py` grows — re-pin them on a merge", so a hard-coded tuple turns
    that documented maintenance step into a FALSE RED (#13814 review round 7: a
    correct re-pin after a 5-line insertion reddened here while the runbook copy —
    which names the function inline — passed; the manifest's parallel copy, which is
    what made the asymmetry confusing, went with this change).
    """
    body = (REPO_ROOT / "tortoise" / "embeddings.py").read_text()
    # `ast`, not indentation heuristics: the span must be the function's OWN body,
    # so a blank line, a comment block or a decorator below it can never extend it.
    for node in ast.parse(body).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_embedder_warmup_enabled":
            assert node.end_lineno is not None
            return node.lineno, node.end_lineno
    raise AssertionError(
        "tortoise/embeddings.py has no `def _embedder_warmup_enabled` — the resolver "
        "this whole block documents is gone, so every cite below is stale"
    )


def _reader_citations(
    lines: list[str], start: int, end: int, artifact: str,
    identity_claims_required: bool = False,
) -> list[tuple[str, int, int, str | None]]:
    """(file, start, end, symbol) for every cite in `lines[start:end]`.

    Parsed from the SHIPPED text — the artifact a reviewer or an operator reads —
    not from a copy here, so a cite edited out of the artifact cannot stay green
    in the test. `start`/`end` are the CALLER's bound: each artifact slices its
    own region, so one parser serves both and neither can drift into a slightly
    different checker. A region that comes back inverted raises rather than
    returning an empty list.

    ⛔ DELETION IS COVERED BY A COVERAGE INVARIANT, NOT BY A COUNT FLOOR. A
    non-vacuity floor (`len(cites) >= N`) passes a list with a reader's own cite
    edited out, because the floor was always BELOW the real count — so the
    article's claim "re-reads every cite above" was vacuous for whichever cite
    vanished (#13814 review round 6). Every NUMBERED ENTRY must therefore still
    contribute at least one cite of its own, asserted below against the parse —
    the entry LINES this function sees — rather than against a number in the test.

    ⛔ AND THE INVARIANT IS KEYED ON THE ENTRY LINE, NEVER ON THE `::name`. Keyed
    on the inline function name it was vacuous in the one direction that matters:
    an entry that loses its `::name` AND its cite leaves the named-reader set and
    the coverage check silently agrees with itself (measured, round 7: three
    natural-reading edits, all green). A numbered entry is a claim whether or not
    it still spells the function, so the entry LINE is what must carry a cite.

    `symbol` is the function named for that cite (`::start_warm_up` and so on), or
    `None` where none is named INLINE. The numbered-list symbol is tracked per
    ENTRY, so reader 2's cite cannot inherit reader 1's function. The two prose
    range cites name no function inline; they are bound to the reader their own
    sentence names by ORDINAL ("Readers 2 and 3" / "Number 2 and 3"), so a range
    re-pinned onto another reader reds. Only the `:40-50` resolver cite is left
    with `symbol is None`, and for it alone the needle is the check.
    """
    assert start < end, (start, end)
    cites: list[tuple[str, int, int, str | None]] = []
    current = "embeddings.py"
    symbol: str | None = None
    ordinal: int | None = None
    #: numbered ENTRY -> the reader that entry names, so the prose's ranges can be
    #: bound to the readers ITS OWN SENTENCE names for them.
    by_ordinal: dict[int, str] = {}
    #: every ordinal a numbered ENTRY line declared, whether or not that entry still
    #: spells its function: the set the coverage invariant is measured against.
    entry_ordinals: set[int] = set()
    #: numbered ENTRY -> how many cites IT contributed. An entry whose own cite was
    #: edited out has 0 here, which is the deletion the count floor could never see.
    entry_cites: dict[int, int] = {}
    #: every ordinal a numbered ENTRY declared, in order: a LIST, not a set, because a
    #: renumbered entry (two `4.`s) dedupes to a contiguous set and would otherwise read
    #: as healthy while the list has silently lost a number.
    entry_order: list[int] = []
    #: readers named by ordinal whose range cites have not been reached yet. The
    #: sentence wraps across lines in BOTH artifacts, so this outlives one line.
    pending: list[str] = []
    ordinal_bound = 0
    #: numbered ENTRY -> the files its own cites land in. Identity, not existence: see
    #: the ordinal-claim checks below.
    entry_files: dict[int, set[str]] = {}
    #: region-relative line index of every cite, parallel to `cites`.
    cite_offsets: list[int] = []
    #: where the numbered list STARTS, so the definition it is built on can be told
    #: apart from a reader entry that merely re-cites it.
    first_entry_offset: int | None = None
    for offset, ln in enumerate(lines[start:end]):
        # A numbered reader entry starts a new claim: its symbol is its own. The
        # invariant sentence below the list ends them, so nothing after it may
        # inherit reader 5's symbol. The entry regex accepts BOTH artifacts'
        # spellings (`#   1. …` in the manifest, `> 1. …` in the runbook) and the
        # reset is markdown-insensitive (the runbook bolds "**function names**").
        entry = _READER_ENTRY_ORDINAL_RE.match(ln)
        if entry:
            symbol = None
            ordinal = int(entry.group(1))
            entry_ordinals.add(ordinal)
            entry_order.append(ordinal)
            if first_entry_offset is None:
                first_entry_offset = offset
        if _READER_SYMBOL_RESET_RE.search(ln.replace("*", "")):
            symbol = None
            ordinal = None
        for token in ("hosted_api.py", "entrypoint.sh", "embeddings.py"):
            if token in ln:
                current = token
                break
        named = re.search(
            r"(?:tortoise/)?(?:embeddings\.py|hosted_api\.py|entrypoint\.sh)::([A-Za-z_]\w*)", ln
        )
        if named:
            symbol = named.group(1)
            if ordinal is not None:
                by_ordinal.setdefault(ordinal, symbol)
        prose = _READER_ORDINAL_SENTENCE_RE.search(ln)
        if prose:
            for group in prose.groups():
                named_ordinal = int(group)
                assert named_ordinal in by_ordinal, (
                    f"{artifact}: the prose names reader {named_ordinal}, but its numbered "
                    f"list has no such entry (it has {sorted(by_ordinal)})"
                )
                pending.append(by_ordinal[named_ordinal])
        for m in _READER_CITE_RE.finditer(ln):
            lo = int(m.group(1))
            hi = int(m.group(2) or lo)
            bound = symbol
            if bound is None and pending:
                bound = pending.pop(0)
                ordinal_bound += 1
            if ordinal is not None:
                # The cite belongs to the numbered ENTRY it sits under — the entry
                # LINE, not its `::name` (see the docstring: keying on the name let
                # an entry drop both and leave this check vacuously satisfied).
                entry_cites[ordinal] = entry_cites.get(ordinal, 0) + 1
                entry_files.setdefault(ordinal, set()).add(current)
            cites.append((current, lo, hi, bound))
            cite_offsets.append(offset)
    # DELETION: every numbered entry must still carry at least one cite of its own.
    # Editing a reader's cite out leaves the artifact telling a reviewer that N
    # readers are pinned while the list pins N-1 — the shape a count floor cannot
    # detect (#13814 review round 6: deleting reader 3's `:1592` from the manifest
    # numbered list left all 142 tests green). Measured against the ENTRY LINES,
    # not against `by_ordinal` (round 7: keyed on the `::name`, an entry that lost
    # its name AND its cite left this check vacuously satisfied — three green
    # mutants, including "the HOSTED lifespan pre-warm in `tortoise/hosted_api.py`").
    uncovered = sorted(entry_ordinals - {n for n, count in entry_cites.items() if count})
    assert not uncovered, (
        f"{artifact}: numbered entry/entries {uncovered} carry no cite at all, so the list "
        f"claims a reader it no longer pins — a cite (or the whole entry's citation) was "
        f"edited out (per-entry cites: {dict(sorted(entry_cites.items()))})"
    )
    assert entry_ordinals, f"{artifact}: found no numbered entries at all — the region reader is wrong"
    # ⛔ AND THE LIST ITSELF MUST STILL BE ONE PER READER. Deleting an entry WHOLE
    # (its number, its name and its cite together) leaves `entry_ordinals` minus that
    # ordinal and `entry_cites` minus the same key, so the coverage check above agrees
    # with itself — while the paragraph around the list still says how many readers
    # there are. Measured, round 7: deleting the manifest's reader 4 (`the HOSTED
    # (production) lifespan pre-warm` — the one that runs on the deployed box) left the
    # whole suite green. Two checks, both derived from the artifact:
    #   (a) the ordinals are exactly 1..N in order — a renumbering or a gap reds;
    #   (b) the artifact's OWN count word equals N — a lost entry reds even when the
    #       survivors still read 1..N, because the prose still claims the old number.
    assert entry_order == list(range(1, len(entry_order) + 1)), (
        f"{artifact}: the numbered reader entries are not 1..N in order — {entry_order} — "
        f"so two entries share a number (or one skipped a number) and the list no longer "
        f"pins one cite per reader"
    )
    # ⛔ AND EACH ENTRY MUST NAME A DIFFERENT READER. Counting cites per entry says an
    # entry HAS one; it cannot say the entry is still its own reader. Measured, round 8:
    # replacing the manifest's reader 4 (the HOSTED production lifespan pre-warm) with a
    # copy of entry 1 left ordinals 1..5, `FIVE read sites`, every needle and every
    # name↔number binding green — and `hosted_api.py` gone from the region entirely,
    # i.e. the reader "that runs on the deployed box" silently deleted from the list a
    # reviewer reads. Only entries 2 and 3 were identity-bound (by the prose sentence),
    # so distinctness is asserted for all of them, plus coverage of the files the block
    # says carry readers.
    repeated = sorted(
        {sym for sym in by_ordinal.values() if list(by_ordinal.values()).count(sym) > 1}
    )
    assert not repeated, (
        f"{artifact}: two numbered entries name the SAME reader — {repeated} — so the "
        f"list claims {len(entry_order)} read sites while pinning fewer distinct ones "
        f"(entries: {dict(sorted(by_ordinal.items()))}). A reader is not a slot"
    )
    cited_files = {fname for fname, _lo, _hi, _sym in cites}
    fallen_out = sorted(set(_READER_NEEDLE) - cited_files)
    assert not fallen_out, (
        f"{artifact}: the reader list no longer cites any line in {fallen_out} — that "
        f"surface's reader was dropped or replaced by a duplicate, so the list is no "
        f"longer the blast radius it claims to be (cited files: {sorted(cited_files)})"
    )
    # ⛔ AND THE PROSE THAT POINTS AT ENTRIES BY ORDINAL MUST POINT AT ENTRIES THAT
    # EXIST. The runbook explains its own list as `(1, 4, 5)` warm-up surfaces plus
    # `(2, 3)` readiness bounds, and says `Number 4 is … the production pre-warm` — so
    # deleting entry 4 and renumbering 5 to 4 (with both count words edited to match)
    # left every earlier check green while the prose pointed at the shell pre-warm as
    # though it were the production one. Measured, round 8.
    region_text = _stated_count_text(lines, start, end)
    referenced = {
        int(number)
        for group in _READER_ORDINAL_LIST_RE.findall(region_text)
        for number in group.split(",")
    } | {int(n) for n in re.findall(r"\bNumbers?\s+(\d+)\b", region_text)}
    dangling = sorted(referenced - entry_ordinals)
    assert not dangling, (
        f"{artifact}: the prose refers to reader(s) {dangling} by ordinal, but the list "
        f"has no such entr(y/ies) ({sorted(entry_ordinals)}) — an entry was deleted or "
        f"renumbered and the sentences that describe what each one is were not updated"
    )
    # ⛔ EXISTENCE IS NOT IDENTITY. A `Number 4 is … the production pre-warm` sentence,
    # and a `(1, 4, 5) warm-up surfaces + (2, 3) readiness bounds` list, are claims about
    # WHICH reader sits at that number. Number reuse is invisible to everything above:
    # swapping entries 4 and 5 WHOLE (each label travelling with its own reader) keeps
    # ordinals 1..N, the count, distinctness and file coverage all green — measured,
    # round 9 — while `Number 4` then points at the SHELL pre-warm. So the sentence's own
    # vocabulary is mapped to the file it names, and the entry's cites must be in it.
    def _cited_files(entry_number: int) -> set[str]:
        return entry_files.get(entry_number, set())

    claims: list[tuple[int, str, str]] = []
    for number, tail in _READER_ORDINAL_CLAIM_RE.findall(region_text):
        for word, expected_file in _READER_ORDINAL_CLAIM_FILE.items():
            if re.search(rf"\b{word}\b", tail, re.IGNORECASE):
                claims.append((int(number), word, expected_file))
                break
    for phrase, group in _READER_ORDINAL_GROUP_CLAIM_RE.findall(region_text):
        for number in group.split(","):
            for word, expected_file in _READER_ORDINAL_CLAIM_FILE.items():
                if re.search(rf"\b{word}\b", phrase, re.IGNORECASE):
                    claims.append((int(number), word, expected_file))
                    break
    misdirected = sorted(
        (number, word, expected, sorted(_cited_files(number)))
        for number, word, expected in claims
        if expected not in _cited_files(number)
    )
    assert not misdirected, (
        f"{artifact}: the prose describes reader(s) by NUMBER and by what they ARE, and "
        f"these no longer agree — (entry, word, file the word names, files the entry "
        f"actually cites): {misdirected}. The entries were reordered or renumbered under "
        f"sentences that still describe the old reader"
    )
    # Non-vacuity, PER ARTIFACT: identity is bound by DIFFERENT sentences, so the
    # requirement is passed in by the caller that knows which sentences its copy carries.
    # The runbook states `Number 4 … the production pre-warm` and binds 2/3 with
    # `Number 2 and 3`; the manifest's copy (which named each entry's file inline and
    # bound 2/3 with `Readers 2 and 3`) went with the `fly-only:#13674` block this change
    # deletes, so the parameter stays at ONE caller on purpose — a future artifact must
    # pass `identity_claims_required` for itself rather than inherit the runbook's arm.
    # Without it, deleting the binding sentence silently removes the only check on
    # number reuse.
    assert claims or not identity_claims_required, (
        f"{artifact}: the section no longer says which reader sits at which NUMBER (the "
        f"'Number N is …' sentence, or a '<class> (N, M)' group), so nothing binds entry "
        f"identity to its number and a reorder would read green — restore it, or this "
        f"guard is reading a section it no longer recognises"
    )
    stated = [
        (m.group(1).lower(), _count_value(m.group(1)))
        for m in _READER_STATED_COUNT_RE.finditer(_stated_count_text(lines, start, end))
    ]
    assert stated, (
        f"{artifact}: the region states no '<N> read sites' count — this guard binds the "
        f"stated count to the numbered entries, so a section that stops stating it makes "
        f"the list's completeness unverifiable"
    )
    wrong_counts = sorted({w for w, n in stated if n != len(entry_order)})
    assert not wrong_counts, (
        f"{artifact}: the region says {wrong_counts} read site(s) but lists "
        f"{len(entry_order)} numbered reader entries — an entry was deleted (with its own "
        f"number, which is why the coverage check above cannot see it) or added without "
        f"the count"
    )
    assert by_ordinal, f"{artifact}: no entry named its reader inline — the region reader is wrong"
    # Non-vacuity, and it is the whole point: if that sentence is deleted or
    # reworded the ranges silently fall back to the needle-only check, which
    # passes a range re-pinned onto ANY flag-reading line — the wrong reader
    # included. The one artifact this runs for (the runbook; the manifest's
    # `#13674` block and its copy went with #13926 / this change) carries the
    # sentence, so it must bind two ranges.
    assert ordinal_bound == 2, (
        f"{artifact}: the sentence naming the range cites' readers by ordinal bound "
        f"{ordinal_bound} of them, not 2 — reworded, or the ranges moved off it? A range "
        f"left unbound is only needle-checked, and the needle passes a range re-pinned "
        f"onto another reader"
    )
    # Counting BINDS is not counting COVERAGE. `ordinal_bound` says two cites consumed
    # the queue; it does not say WHICH two, so a decoy cite line above the real pair can
    # consume both and leave the real one needle-only, and an ordinary reflow that leaves
    # a cite on the line ABOVE its `::name` silently unbinds THAT cite. Either way a
    # re-pin onto another reader's byte-identical gate line passes the needle, so the
    # unbound SET is the thing to pin: only the resolver's own definition may stay
    # unbound, because the block names no reader for `:40-50` anywhere.
    unbound = {(fname, lo, hi) for fname, lo, hi, sym in cites if sym is None}
    # ⛔ The resolver's span is DERIVED from the `def`, not pinned as a literal here:
    # a literal would make the documented re-pin (the runbook says the numbers move
    # when `embeddings.py` grows) a false red (#13814 review round 7). What the
    # invariant keeps is the
    # property that matters: a cite naming no reader must land in the RESOLVER, so
    # re-pointing it at another reader's gate line still reds.
    resolver_lo, resolver_hi = _resolver_definition_span()
    # Non-vacuity for the ceiling below: with NO cite resolving to the resolver the
    # `outside` check passes on an empty set, so a block that lost its resolver cite
    # altogether would read clean. The runbook — the only artifact left with these
    # cites — names the function inline (bound), while the manifest's copy carried a
    # BARE span (unbound) until #13926/this change removed it, so the floor is
    # presence, not unboundedness.
    resolver_cites = sorted(
        (fname, lo, hi)
        for fname, lo, hi, _sym in cites
        if fname == "embeddings.py" and lo >= resolver_lo and hi <= resolver_hi
    )
    assert resolver_cites, (
        f"{artifact}: no cite lands on the flag resolver's own definition "
        f"(`tortoise/embeddings.py:{resolver_lo}-{resolver_hi}`, derived from the `def` at "
        f"check time) — the block no longer points at the code that decides what the flag "
        f"is, so the list of readers has no subject"
    )
    outside = sorted(
        (fname, lo, hi)
        for fname, lo, hi in unbound
        if fname != "embeddings.py" or lo < resolver_lo or hi > resolver_hi
    )
    assert not outside, (
        f"{artifact}: a cite that names no reader must land inside the flag resolver's own "
        f"definition (`tortoise/embeddings.py:{resolver_lo}-{resolver_hi}`, derived from the "
        f"`def` at check time, not from a literal in this test). These do not: {outside} — "
        f"either an entry's cite lost its reader (reflowed above its `::name`, or edited "
        f"out), or the resolver span was pointed at another reader's gate line"
    )
    # ⛔ AND THE DEFINITION IS NOT ONE OF THE READERS. A numbered entry may legally cite
    # the resolver's span — its `def` line contains the flag's name, so the needle passes
    # and the `sym is None` arm above never sees it (the entry names the symbol inline).
    # That let an entry be REPLACED by the definition itself: entry 1 retuned to
    # `::_embedder_warmup_enabled` at the `def` line left every check green while the SDK
    # engine-init warm-up reader was gone and the block still claimed five read sites
    # (measured, round 9). The definition belongs ABOVE the list, which is where both
    # artifacts put it.
    resolver_as_reader = sorted(
        (fname, lo, hi)
        for (fname, lo, hi, _sym), offset in zip(cites, cite_offsets, strict=True)
        if first_entry_offset is not None
        and offset >= first_entry_offset
        and fname == "embeddings.py"
        and lo >= resolver_lo
        and hi <= resolver_hi
    )
    assert not resolver_as_reader, (
        f"{artifact}: a NUMBERED reader entry cites the resolver's own definition "
        f"(`tortoise/embeddings.py:{resolver_lo}-{resolver_hi}`) — the definition is what "
        f"the list is built ON, not one of the sites that reads it, so this entry no "
        f"longer pins a reader: {resolver_as_reader}"
    )
    return cites


def _region_marker(
    lines: list[str], marker: str, artifact: str, what: str, after: int = 0,
) -> int:
    """The index of the line carrying `marker`, or a NAMED failure.

    A bare `next(...)` raises `StopIteration` — a red with no diagnosis, which reads as
    a broken test rather than as "the region this guard slices is gone, re-pin the
    marker". The runbook arm has always raised a named `AssertionError`, and it is this
    helper's ONLY caller since #13926 / this change deleted the manifest's `#13674`
    block — keep the failure NAMED here, or a re-pin reads as a broken test.
    """
    index = next((i for i in range(after, len(lines)) if marker in lines[i]), None)
    if index is None:
        raise AssertionError(
            f"{artifact}: no line carries {marker!r}, so {what} cannot be sliced — the "
            f"paragraph was reworded or deleted, and this guard is now reading nothing "
            f"(re-pin the marker to text that still exists, or restore the paragraph)"
        )
    return index


def _embedder_warmup_runbook_citations() -> list[tuple[str, int, int, str | None]]:
    """The cites in the RUNBOOK's operator section — the copy an operator reads.

    Same numbers, same readers, read mid-incident by an operator instead of a
    reviewer — that is why this copy needs its own pin: the merge that moved
    `embeddings.py` moved every copy at once, and a pin on one artifact reds while
    the others rot silently.

    The region stops at `**When to flip it.**`: everything above it cites a flag
    READER, everything below it cites the cold-load timing (`embeddings.py:894`,
    `entrypoint.sh:422-423`), which is not a reader and would red a needle check
    for the wrong reason.
    """
    lines = REAL_RUNBOOK.read_text().splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("### The embedder warm-up switch")),
        None,
    )
    if start is None:
        raise AssertionError(
            "docs/infra-runbook.md has no '### The embedder warm-up switch' section — "
            "the operator surface this citation list belongs to is gone"
        )
    end = _region_marker(
        lines,
        "**When to flip it",
        "docs/infra-runbook.md",
        "the end of the operator section (the '**When to flip it.**' paragraph)",
        after=start,
    )
    return _reader_citations(
        lines, start, end, "docs/infra-runbook.md", identity_claims_required=True
    )


def _assert_every_citation_reads_the_flag(
    cites: list[tuple[str, int, int, str | None]], artifact: str,
) -> None:
    """Every cite must land on code that reads the flag, in the function named.

    Kept as ONE checker for every artifact that carries these readers: they hold
    the SAME numbers, and a second, slightly different checker is how two copies
    drift apart while both look pinned. Since #13926 the runbook's operator
    section is the only artifact pinned through it — the manifest's copy went with
    its `#13674` block (see SCOPE at the top of this section) — but the parameter
    stays, so re-pointing it at another artifact is a marker change rather than a
    second implementation.
    """
    # Non-vacuity: a parser that found nothing would clear every assertion below.
    assert len(cites) >= 8, f"{artifact}: the reader list's citations could not be read: {cites}"
    for fname, lo, hi, symbol in cites:
        assert fname in _READER_NEEDLE, f"{artifact}: unattributed citation: {fname}:{lo}-{hi}"
        path = (REPO_ROOT / fname) if fname == "entrypoint.sh" else (REPO_ROOT / "tortoise" / fname)
        body = path.read_text().splitlines()
        assert 1 <= lo <= hi <= len(body), (
            f"{artifact}: {fname}:{lo}-{hi} is outside the file ({len(body)} lines)"
        )
        span = "\n".join(body[lo - 1:hi])
        assert _READER_NEEDLE[fname] in span, (
            f"{artifact}: {fname}:{lo}-{hi} is cited as a TORTOISE_EMBEDDER_WARMUP "
            f"reader, but the cited lines do not read it — a merge moved the code "
            f"and the cite went stale. Re-pin the numbers in {artifact} (the "
            f"function names are the invariant; the numbers are a convenience). "
            f"Cited lines: {span[:300]!r}"
        )
        if symbol is None:
            # No function is named for this cite, so there is nothing to bind it
            # to (the resolver definition `:40-50`, and the range cites in prose).
            continue
        # NAME↔NUMBER: the cited line must sit inside the function named for it.
        # Without this, a swap between two byte-identical gate lines
        # (`if not _embedder_warmup_enabled():`) passes the needle check.
        enclosing = _enclosing_definition(body, lo, symbol)
        assert enclosing == symbol, (
            f"{artifact}: {fname}:{lo} is cited as {symbol}, but the function "
            f"enclosing that line is {enclosing!r} — the number was re-pinned against "
            f"the wrong reader, or the names and numbers have drifted apart."
        )


def test_the_embedder_warmup_runbook_citations_resolve_to_the_flag_readers():
    """The runbook's operator section cites the flag's readers BY LINE — pin them.

    #13814 pinned the manifest copy (NAME↔NUMBER, mutation-proved) and left the
    operator section's copy unguarded, so the next merge that grows
    `tortoise/embeddings.py` red the manifest and silently rotted the runbook —
    the copy an operator reads mid-incident, when the flag has to be flipped on
    the box. Measured at the head before this test: `:1361` / `:1466` / `:1592`
    are cited in both artifacts, and only the manifest's three were read by any
    test.

    ⛔ After #13926 the manifest's copy is route (b)'s to mind (the manifest's
    block is `fly-toml-env`, not a `fly-only:` entry), and THIS arm is the one
    that remains: the runbook is the operator surface, and nothing else in CI
    reads a line number out of it.

    The brief's own recorded defect class — "guards that cannot be reddened" —
    is why this arm is written as the same three properties (the numbers, the
    needle and the NAME↔NUMBER binding) on the ONE artifact left: #13926 took
    the name to route (b), this change's `fly-only:` block went with it, and the
    manifest's copy plus `test_the_embedder_warmup_citations_resolve_to_the_flag_readers`
    went with that block. The mutants that used to red that arm red this one.
    """
    _assert_every_citation_reads_the_flag(
        _embedder_warmup_runbook_citations(), "docs/infra-runbook.md"
    )


def _enclosing_definition(body: list[str], line_no: int, symbol: str) -> str | None:
    """Whether `symbol` encloses `line_no` (1-based); returns the enclosing name.

    A NESTED definition is inside another one, so the nearest `def` above a cited
    line is not necessarily the reader the block names — `hosted_api.py`'s
    `_lifespan` contains a `_prewarm_embeddings` helper. The enclosing chain is
    therefore read by INDENTATION: the nearest definition at the same indent as
    the named one is the one that encloses the cite.

    Only a real DEFINITION counts, in either the Python form (`def`/`async def`)
    or the shell forms (`name() {`, `function name {`, and the brace on the line
    after the signature) — a bare `name()` CALL must not register as one, which
    is why every arm requires the opening brace.
    """
    defs: list[tuple[int, str]] = []  # (indent, name), in file order, above line_no
    for idx, raw in enumerate(body[: max(line_no, 0)]):
        py = re.match(r"(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)", raw)
        if py:
            defs.append((len(py.group(1)), py.group(2)))
            continue
        sh = re.match(r"(\s*)(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{", raw)
        if sh:
            defs.append((len(sh.group(1)), sh.group(2)))
            continue
        sh_kw = re.match(r"(\s*)function\s+([A-Za-z_]\w*)\s*\{", raw)
        if sh_kw:
            defs.append((len(sh_kw.group(1)), sh_kw.group(2)))
            continue
        # `name()` / `function name` with the opening brace on the next line.
        sh_open = re.match(r"(\s*)(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*$", raw)
        if sh_open and idx + 1 < len(body) and body[idx + 1].lstrip().startswith("{"):
            defs.append((len(sh_open.group(1)), sh_open.group(2)))
            continue
    target = next((d for d in reversed(defs) if d[1] == symbol), None)
    if target is None:
        return None
    indent = target[0]
    for d_indent, d_name in reversed(defs):
        if d_indent == indent:
            return d_name
    return None


# ── The allowlist shape predicate, on its own (#10472 review F1) ─────────────
# The shipped-artifact test above runs the predicate once, against whatever
# `fly.toml` holds today (an empty allowlist). That alone cannot show the
# predicate is a PROPERTY rather than the prohibition it replaced: an assertion
# of the form `== {}` is green on today's file and red the moment an operator
# does what `fly.toml:305-312` documents. Pinned here so the two arms the
# predicate exists for are both exercised — the deliberate attestation it must
# ACCEPT, and the malformed values it must REFUSE.

def test_the_allowlist_predicate_accepts_the_activation_fly_toml_documents(monkeypatch):
    """A reviewed first-party attestation is the ACTIVATION, not drift.

    `fly.toml [env]` tells the operator "attesting a real first-party client
    means adding its minted `ct_…` client_id here". Under the previous
    `json.loads(value) == attested_clients()` form this input red the gating
    suite on `main` — the one edit that makes the discrimination half of #8218
    reachable was forbidden by the guard that shipped with it.
    """
    attestation = '{"ct_a1b2c3": "Claude"}'
    assert _assert_allowlist_is_the_value_the_code_acts_on(
        attestation, monkeypatch) == {"ct_a1b2c3": "Claude"}


def test_the_allowlist_predicate_refuses_a_value_the_code_would_drop(monkeypatch):
    """Every shape `tortoise/client_identity.py` silently narrows reds HERE.

    A blank name is the case that matters: the module drops the entry and
    fail-closes, so the deploy is still SAFE — but the operator attested a
    client that then renders "An unverified app (…)", which is the divergence
    the assertion exists to report. The empty-object default is included as the
    control: it must keep passing.
    """
    assert _assert_allowlist_is_the_value_the_code_acts_on("{}", monkeypatch) == {}
    for malformed in (
            '{"ct_abc": ""}',           # blank name: the module DROPS the entry
            '{"ct_abc": "   "}',        # whitespace-only name: same
            '{"ct_abc": 7}',             # non-string name: same
    ):
        with pytest.raises(AssertionError):
            _assert_allowlist_is_the_value_the_code_acts_on(malformed, monkeypatch)
    for not_an_object in ('["ct_abc"]', '"ct_abc"', 'null'):
        with pytest.raises(AssertionError):
            _assert_allowlist_is_the_value_the_code_acts_on(not_an_object, monkeypatch)


# ── The probe ↔ manifest lockstep (#4411 residual 1) ─────────────────────────
# The gate cannot list repository secrets, so the deploy TELLS it which ones the
# run carries — and a name missing from that block reads as ABSENT, which fails
# the deploy for that name. The lockstep was previously untested: a probe line
# gated on one secret while appending another (or an appended name with no
# `env:` binding, so the shell sees an unset variable) would over-report presence
# and let a hand-managed Fly value pass — the #4126 shape, green. Pinned here
# rather than left to a future deploy to discover, because #4126's own fix
# depends on it: three new `gh-secret:` declarations arrived with three new probe
# lines and three new `env:` bindings.

_PROBE_LINE_RE = re.compile(
    r'\[\s*-n\s+"\$(?P<tested>[A-Z0-9_]+)"\s*\]\s*&&\s*'
    r'GH_PRESENT="\$GH_PRESENT (?P<appended>[A-Z0-9_]+)"'
)


def _provenance_gate_step() -> dict:
    """The workflow step that invokes this gate.

    Parsed, not line-matched: this is the same reason the gate itself parses —
    an `env:` or an `if:` is a YAML key, and its position relative to `run:` is
    not a contract.
    """
    import yaml

    doc = yaml.safe_load(REAL_WORKFLOW.read_text())
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "check-fly-secret-drift" in str(step.get("run") or ""):
                return step
    raise AssertionError("the provenance gate step is not wired into the real deploy workflow")


def _payload_step() -> dict:
    """The workflow step that builds the Fly secrets payload."""
    import yaml

    doc = yaml.safe_load(REAL_WORKFLOW.read_text())
    found = [
        step
        for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and "secrets set" in str(step.get("run") or "")
    ]
    assert found, "no `fly secrets set` payload step in the real deploy workflow"
    return found[0]


def test_gh_secret_declarations_are_bound_from_their_own_github_secret():
    """A name bound to the WRONG secret reads as present while the deploy skips it.

    The probe/env-name lockstep is not enough. A binding of
    `SENTRY_DSN: ${{ secrets.POSTHOG_API_KEY }}` still names the key, so the probe
    reports `SENTRY_DSN` PRESENT from POSTHOG's value while `secrets.SENTRY_DSN`
    may not exist in the repo — the gate certifies the declaration, the real
    deploy's guard is a no-op, and the hand-managed Fly value survives. Same for
    the propagation step: a mis-bound `$NAME` guard silently skips the assignment.
    Both steps must bind each declared GitHub secret name to ITSELF (#4523 review,
    T5).
    """
    gate_env = {
        str(k): str(v).strip() for k, v in (_provenance_gate_step().get("env") or {}).items()
    }
    payload_env = {str(k): str(v).strip() for k, v in (_payload_step().get("env") or {}).items()}
    for name in sorted(_declared_gh_names(REAL_MANIFEST)):
        assert gate_env.get(name) == f"${{{{ secrets.{name} }}}}", (
            f"the gate step binds {name} to {gate_env.get(name)!r} — the presence "
            "probe would report that name from another secret's value"
        )
        assert payload_env.get(name) == f"${{{{ secrets.{name} }}}}", (
            f"the payload step binds {name} to {payload_env.get(name)!r} — the "
            "propagation guard would read the wrong secret and skip the assignment"
        )


def test_probe_block_and_manifest_gh_secret_names_are_in_lockstep():
    """Every probe line tests the name it appends, is `env:`-bound, and is declared."""
    step = _provenance_gate_step()
    pairs = _PROBE_LINE_RE.findall(step["run"])
    assert pairs, "no GH_SECRETS_PRESENT probe lines found in the gate step"
    for tested, appended in pairs:
        # A line that tests $A and appends B reports B as present on A's value:
        # B is declared `gh-secret:B`, the deploy's guard reads an unset $B, the
        # assignment is skipped every run, and Fly keeps whatever was hand-set.
        assert tested == appended, (
            f"probe line tests ${tested} but appends {appended} — the declared secret "
            "for that name reads ABSENT and fails the deploy"
        )
    probed = {appended for _, appended in pairs}
    env_keys = {str(k) for k in (step.get("env") or {})}
    assert not probed - env_keys, (
        "probed names with no `env:` binding on the gate step are unset shell "
        f"variables, so they read as ABSENT: {sorted(probed - env_keys)}"
    )
    # The KEY is not enough — the VALUE must come from the name's own GitHub
    # secret. `SENTRY_DSN: ${{ secrets.POSTHOG_API_KEY }}` still binds the key, so
    # the probe reports SENTRY_DSN present from POSTHOG's value while the real run
    # may not carry SENTRY_DSN at all: the gate certifies a declaration whose
    # assignment the deploy skips — the #4126 shape, green (#4523 review, T5).
    for name in sorted(probed):
        binding = str((step.get("env") or {})[name]).strip()
        assert binding == f"${{{{ secrets.{name} }}}}", (
            f"the gate step binds {name} to {binding!r} instead of its own GitHub "
            "secret, so the presence probe reports that name from another secret's "
            "value"
        )
    declared_gh = _declared_gh_names(REAL_MANIFEST)
    assert probed == declared_gh, (
        "the probe block and the manifest's `gh-secret:` declarations disagree — "
        f"probe-only: {sorted(probed - declared_gh)}, "
        f"manifest-only: {sorted(declared_gh - probed)}"
    )


# ── The `fly-only:` category — a declared exception, NOT a fail-open ─────────
#
# ⛔ NOT "a decision-backed exception", and not because of any one member: this
# checker is SHAPE ONLY and hermetic, so it can neither read the issue a ref names
# nor know whether a ruling exists. Every member the shipped manifest declares does
# carry one (#661, #13209), but "all of them today" is not something THIS layer can
# verify, and its summary line is printed on a GREEN run — the word would then be an
# unverified claim in the one place an operator looks for the verdict (#13814 review
# rounds 8, 11-12; the same word was removed from the summary line and the STALE
# message under that rule).
#
# #661 is a CLOSED recorded decision: the registry-stream key must never enter
# the GitHub trust boundary, so the ABSENCE of a version-control source IS the
# security property. The gate needs a way to say a source was deliberately
# REFUSED — without becoming a second spelling of `unmanaged`, which must keep
# failing. These tests pin both halves: the category works, and it cannot be
# used to launder a bare unmanaged name or an unpointable one.

_FLY_ONLY_LINE = "FLY_ONLY_KEY  fly-only:#661\n"
_FLY_ONLY_MANIFEST = _MANIFEST + _FLY_ONLY_LINE
_FLY_ONLY_SECRETS = [*_ALL_DECLARED, "FLY_ONLY_KEY"]


def test_the_test_side_manifest_reader_keeps_a_fly_only_payload(tmp_path):
    """`_manifest_sources` must agree with the GATE about where a comment starts.

    A plainer `split("#", 1)` truncates `fly-only:#13209` to `fly-only:`. The
    CLASSIFICATION survived that (`startswith("fly-only:")` still holds, so the
    guard passed), and only the string an operator reads diverged — an offender
    message naming a token no manifest line contains. Two readers of one grammar
    disagreeing in the one place a human acts on it is worth a pin, because the
    failure is invisible until someone is already debugging (#13370 review, P3).
    """
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(
        "# a full-line comment\n"
        "REGISTRY_STREAM_KEY    fly-only:#661\n"
        "TORTOISE_RECORD_SERVE  fly-only:#13209   # trailing note\n"
        "SENTRY_DSN             gh-secret:SENTRY_DSN   # error tracking\n"
    )
    assert _manifest_sources(manifest) == {
        "REGISTRY_STREAM_KEY": "fly-only:#661",
        "TORTOISE_RECORD_SERVE": "fly-only:#13209",
        "SENTRY_DSN": "gh-secret:SENTRY_DSN",
    }


def _fly_only_manifest(name: str, line: str, base: str = _MANIFEST) -> Path:
    return _fixture(name, base + line)


def test_fly_only_declaration_is_accepted_for_a_live_unassigned_secret():
    """The positive half: a declared, live, unassigned out-of-band name passes."""
    r = _run(
        _secrets_file(_FLY_ONLY_SECRETS, "fly-only-clean.json"),
        manifest=_fly_only_manifest("manifest-fly-only.txt", _FLY_ONLY_LINE),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # ⛔ NOT "decision-backed": a summary that counts every `fly-only:` name as
    # decision-backed asserts a fact this layer cannot check — the checker is SHAPE
    # ONLY and hermetic by design (it never reads an issue) — on a GREEN run, which
    # is the one line an operator reads to learn the gate passed (#13814 review
    # round 8, P2). The decision half belongs to a reviewer, and the manifest's
    # entry-point paragraph routes them to it.
    assert "1 fly-only" in r.stdout, r.stdout


def test_fly_only_accepts_a_qualified_owner_repo_ref():
    """A decision recorded in another repo must be pointable too."""
    r = _run(
        _secrets_file(_FLY_ONLY_SECRETS, "fly-only-qualified.json"),
        manifest=_fly_only_manifest(
            "manifest-fly-only-qualified.txt",
            "FLY_ONLY_KEY  fly-only:daniel-ospina/tortoise#661\n",
        ),
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_fly_only_without_a_well_formed_issue_ref_is_exit_2():
    """The SAFEGUARD, first half: no usable ref → nothing to name → fail-closed.

    Every one of these is `unmanaged` in disguise: the declaration names no
    ruling, so it must not be accepted as one. Exit 2 (could-not-determine), not
    a pass — and not exit 1 either, because a manifest this checker cannot read
    is the same fail-closed class as a malformed `gh-secret` entry.

    `#0` and the Arabic-Indic `#٦٦١` are here because the first cut used
    `#\\d+`: `\\d` is Unicode-aware, so a non-ASCII numeral was accepted, and issue
    numbers start at 1, so `#0` names nothing. Both exited 0 (#4523 review, T2).
    """
    for index, ref in enumerate(
        [
            "fly-only:",  # empty
            "fly-only:#",  # bare marker
            "fly-only:#abc",  # no number
            "fly-only:#661abc",  # trailing junk
            "fly-only:661",  # no # — not an issue reference
            "fly-only:owner/repo",  # qualified but no number
            "fly-only:owner/repo#",  # qualified, no number
            "fly-only:#0",  # issue numbers start at 1 — names nothing
            "fly-only:owner/repo#0",  # same, qualified
            "fly-only:#٦٦١",  # non-ASCII digits are not an issue number
            "fly-only:#-1",  # not a number
            "fly-only:../..#661",  # `..` is not an owner/repo prefix
            "fly-only:/repo#661",  # no owner
            "fly-only:owner/#661",  # no repo
            "fly-only:owner//repo#661",  # empty segment
            "fly-only:owner/repo#661/extra",  # trailing path
        ]
    ):
        manifest = _fly_only_manifest(
            f"manifest-fly-only-bad-{index}.txt", f"FLY_ONLY_KEY  {ref}\n"
        )
        r = _run(_secrets_file(_FLY_ONLY_SECRETS, f"fly-only-bad-{index}.json"), manifest=manifest)
        assert r.returncode == 2, f"ref={ref!r} -> {r.returncode}\n{r.stdout}{r.stderr}"
        assert "cannot determine secret provenance" in r.stderr, r.stderr
        # The `__main__` backstop also exits 2, so a regression that turned the
        # ref validation into an UNCAUGHT exception would satisfy the two
        # assertions above while the manifest path was never actually validated.
        # The sibling gh-secret test guards the same distinction.
        assert "unexpected" not in r.stderr, r.stderr


def test_fly_only_on_a_fly_toml_env_key_is_stale():
    """The exception is for a name whose value is NOWHERE in version control.

    The fixture fly.toml assigns `ENV_ONLY_KEY` in `[env]`. Declaring that name
    `fly-only:` would claim the value is deliberately out-of-band while the value
    is committed — and the Fly secret shadows the committed one, which is exactly
    the state the `fly-toml-env` route exists to reject. It must fail.
    """
    base = _MANIFEST.replace(
        "ENV_ONLY_KEY          fly-toml-env\n",
        "ENV_ONLY_KEY          fly-only:#661\n",
    )
    assert base != _MANIFEST
    r = _run(
        _secrets_file([*_ALL_DECLARED, "ENV_ONLY_KEY"], "fly-only-env-key.json"),
        manifest=_fly_only_manifest("manifest-fly-only-env-key.txt", "", base),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION — 'ENV_ONLY_KEY'" in r.stdout, r.stdout
    assert "assigned key in fly.toml's [env] table" in r.stdout, r.stdout


def test_fly_only_on_a_name_the_deploy_assigns_is_stale():
    """The SAFEGUARD, second half: the exception is only for an UNMANAGED name.

    A `fly-only:` declaration on a name `deploy-hosted.yml` assigns is false —
    the deploy manages it — so it must fail rather than pass. This is what stops
    the category from becoming a blanket escape from the contract.
    """
    base = _MANIFEST.replace(
        "STRIPE_PRICE_IDS      gh-secret:STRIPE_PRICE_IDS\n",
        "STRIPE_PRICE_IDS      fly-only:#661\n",
    )
    assert base != _MANIFEST
    r = _run(
        _secrets_file(_ALL_DECLARED, "fly-only-assigned.json"),
        manifest=_fly_only_manifest("manifest-fly-only-assigned.txt", "", base),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION — 'STRIPE_PRICE_IDS'" in r.stdout, r.stdout
    assert "assigns the Fly variable" in r.stdout, r.stdout


def test_fly_only_on_a_name_absent_from_fly_is_stale():
    """There is nothing out-of-band to except once the name is gone."""
    r = _run(
        _secrets_file(_ALL_DECLARED, "fly-only-absent.json"),
        manifest=_fly_only_manifest("manifest-fly-only-absent.txt", _FLY_ONLY_LINE),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STALE DECLARATION — 'FLY_ONLY_KEY'" in r.stdout, r.stdout


def test_bare_unmanaged_still_fails_beside_a_valid_fly_only_entry():
    """MUTATION GUARD: the new category must not rescue `unmanaged`.

    Both are "a Fly secret with no CI source" on the surface; only one carries a
    ruling. Declaring them side by side in the SAME manifest makes the contrast
    executable: the `fly-only:#661` name is accepted and the bare `unmanaged`
    name is still UNSOURCED. If the fly-only branch ever grew a fall-through that
    admits `unmanaged`, this test goes red.
    """
    manifest = _fly_only_manifest(
        "manifest-unmanaged-beside-fly-only.txt",
        _FLY_ONLY_LINE + "UNMANAGED_KEY  unmanaged\n",
    )
    r = _run(
        _secrets_file([*_FLY_ONLY_SECRETS, "UNMANAGED_KEY"], "unmanaged-beside-fly-only.json"),
        manifest=manifest,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNSOURCED — 'UNMANAGED_KEY'" in r.stdout, r.stdout
    assert "FLY_ONLY_KEY" not in r.stdout, r.stdout


def test_fly_only_ref_hash_is_not_treated_as_a_comment():
    """`fly-only:#661` keeps its `#`: a comment leader only follows whitespace.

    A plain `split('#', 1)` stripped the reference and failed the whole manifest
    as unreadable — the category could not have been declared at all.
    """
    r = _run(
        _secrets_file(_FLY_ONLY_SECRETS, "fly-only-hash.json"),
        manifest=_fly_only_manifest(
            "manifest-fly-only-trailing-comment.txt",
            "FLY_ONLY_KEY  fly-only:#661   # the #661 ruling, referenced here\n",
        ),
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_secrets_matrix_github_column_agrees_with_the_manifest():
    """A `gh-secret:` name must not be shown as having NO GitHub home.

    The Secrets Matrix is the orientation table an operator reads before §2's
    rotation recipes; `.github/scripts/fly-managed-secrets.txt` is the declared
    inventory the deploy's drift gate enforces. A row whose **GitHub Actions**
    column reads "—" while the manifest declares `gh-secret:` for that Fly name
    states the opposite of the gate — and it is not cosmetic: until the #8614
    review finding, `FASTAPI_INTERNAL_KEY` and `TORTOISE_SECRET_PEPPER` were
    printed that way, so the Fly-only rotation recipe looked CORRECT for exactly
    those two names (a Fly-only rotation is silently reverted by the next
    deploy). `BILLING_FROM_EMAIL` read the same way.

    Derived from the manifest on every run — never from a list maintained here —
    so a new `gh-secret:` declaration that lands with a no-home GitHub cell
    fails at review time instead of misleading an operator mid-rotation. The
    "no home" test is the CELL: a row declares a GitHub home only by carrying
    the matrix's `✅` marker, so `—`, ASCII `-`, `n/a`, `none`, an empty cell and
    the dash with a trailing note all red alike (#8614 review, P2 — matching only
    the em dash let the other spellings merge green).
    """
    sources = _manifest_sources(REAL_MANIFEST)
    gh_names = {n for n, s in sources.items() if s.startswith("gh-secret:")}
    assert gh_names, "the shipped manifest declares no gh-secret: name — the reader is wrong"

    rows = _secrets_matrix_rows()
    # Non-vacuity: a parser that found no rows would pass this test forever.
    assert len(rows) >= 10, f"parsed only {len(rows)} Secrets Matrix rows"
    seen = {name for name, *_ in rows}
    # A row DELETED to silence this test must not pass it either.
    for required in ("FASTAPI_INTERNAL_KEY", "TORTOISE_SECRET_PEPPER"):
        assert required in seen, f"{required} is missing from the Secrets Matrix"

    offenders = [
        f"{name}: manifest says {sources[name]!r} but the GitHub Actions column "
        f"reads {github!r}"
        for name, _fly, _fdb, github in rows
        if name in gh_names and not _declares_github_home(github)
    ]
    assert not offenders, (
        "the Secrets Matrix tells an operator these names have no GitHub Actions "
        "home, while the manifest declares them `gh-secret:` (and §2 rotates them "
        "GitHub-first):\n  " + "\n  ".join(offenders)
    )


def _runbook_with_matrix_github_cell(name: str, cell: str) -> str:
    """The runbook with `name`'s Secrets Matrix GitHub Actions cell set to `cell`.

    Used to prove the matrix guard reds on each no-home spelling; the row is
    located through the same parser the guard uses, and a missing name raises
    rather than returning an unmutated runbook (which would make the probe
    vacuous).
    """
    lines = REAL_RUNBOOK.read_text().splitlines()
    for i, line in enumerate(lines):
        row = _SECRETS_MATRIX_ROW_RE.match(line)
        if row and row.group(1) == name:
            lines[i] = (
                f"| {name} | {row.group(2).strip()} | {row.group(3).strip()} | {cell} |"
            )
            return "\n".join(lines)
    raise AssertionError(f"{name} is not in the Secrets Matrix — the probe is stale")


def test_the_secrets_matrix_guard_rejects_every_no_home_spelling(monkeypatch):
    """The widened "no GitHub home" test must red on every equivalent spelling.

    #8614 review (P2): the guard matched only the em dash, so a `gh-secret:` row
    whose GitHub Actions column read ASCII `-`, `n/a`, `none` or nothing said
    the same false thing and merged green. This pins the widening end-to-end —
    the real guard, run against the runbook with that one cell changed — so
    going back to a substring/regex test reds here. The same run asserts a real
    GitHub home (`✅`) does NOT red, so the cell test cannot be satisfied by
    flagging every row.
    """
    module = sys.modules[__name__]
    for index, cell in enumerate(
        ("—", "-", "–", "n/a", "N/A", "none", "", "— (not managed)")
    ):
        fixture = _fixture(
            f"runbook-matrix-{index}.md",
            _runbook_with_matrix_github_cell("FASTAPI_INTERNAL_KEY", cell),
        )
        monkeypatch.setattr(module, "REAL_RUNBOOK", fixture)
        try:
            test_secrets_matrix_github_column_agrees_with_the_manifest()
        except AssertionError:
            continue
        raise AssertionError(
            f"a `gh-secret:` row whose GitHub Actions column reads {cell!r} was "
            "not caught — the offender still merges green (#8614 review, P2)"
        )
    # Control: the real shape (`✅`) is a GitHub home, not an offence.
    control = _fixture(
        "runbook-matrix-home.md",
        _runbook_with_matrix_github_cell("FASTAPI_INTERNAL_KEY", "✅"),
    )
    monkeypatch.setattr(module, "REAL_RUNBOOK", control)
    test_secrets_matrix_github_column_agrees_with_the_manifest()


def _runbook_with_matrix_fly_cell(name: str, cell: str) -> str:
    """The runbook with `name`'s Secrets Matrix **Fly.io** cell set to `cell`.

    The mirror of `_runbook_with_matrix_github_cell`, for the mutation probe that
    pins the Fly-column guard. A missing name raises rather than returning an
    unmutated runbook, which would make the probe vacuous.
    """
    lines = REAL_RUNBOOK.read_text().splitlines()
    for i, line in enumerate(lines):
        row = _SECRETS_MATRIX_ROW_RE.match(line)
        if row and row.group(1) == name:
            lines[i] = (
                f"| {name} | {cell} | {row.group(3).strip()} | {row.group(4).strip()} |"
            )
            return "\n".join(lines)
    raise AssertionError(f"{name} is not in the Secrets Matrix — the probe is stale")


def test_a_fly_only_name_is_not_shown_as_absent_from_the_app():
    """A `fly-only:` name IS on the app — its Fly.io cell must say so, not `—`.

    The GitHub-column guard above, on the OTHER column. `fly-only:` means "on
    Fly, managed out of band, deliberately absent from version control": the
    checker REFUSES such a declaration unless the name really is on the app
    (`check-fly-secret-drift.py`'s `fly-only:` arm), so a `—` cell states the
    opposite of the declaration an operator can read in the manifest.

    This is the #13209 review's P2-1, generalized rather than corrected once:
    `TORTOISE_RECORD_SERVE` read "— (a mode flag, not a credential: no secret to
    provision)" sixty lines above this PR's own "it is a live Fly secret,
    declared `fly-only:#13209`" paragraph — the orientation row an operator
    reads BEFORE the recipes contradicted both the manifest and the gate.

    Positive-marker test, for the same measured reason as the GitHub column: the
    em dash, ASCII `-`, `n/a`, `none`, an empty cell and a dash with a trailing
    note all read as "not here", and only `✅` says "on the app" (#8614 review,
    P2 — a substring test merged every other spelling green).
    """
    sources = _manifest_sources(REAL_MANIFEST)
    fly_only = {n for n, s in sources.items() if s.startswith("fly-only:")}
    assert fly_only, "the shipped manifest declares no fly-only: name — the reader is wrong"

    rows = _secrets_matrix_rows()
    seen = {name for name, *_ in rows}
    # Non-vacuity: neither the manifest dropping its fly-only members nor the
    # table dropping their rows may silently disarm this guard.
    assert fly_only & seen, (
        "no `fly-only:` name has a Secrets Matrix row, so this guard proves "
        f"nothing — manifest fly-only names: {sorted(fly_only)}"
    )

    offenders = [
        f"{name}: declared {sources[name]!r} (so it IS on the app) but its "
        f"Fly.io column reads {fly!r}"
        for name, fly, _fdb, _github in rows
        if name in fly_only and _GITHUB_HOME_MARKER not in fly
    ]
    assert not offenders, (
        "the Secrets Matrix tells an operator these names are not on the app, "
        "while the manifest declares them `fly-only:` — which the provenance "
        "gate only accepts for a name that IS on Fly:\n  " + "\n  ".join(offenders)
    )


def test_every_fly_only_member_is_documented_in_the_runbook():
    """A declared out-of-band lever named NOWHERE in the runbook has no operator surface.

    `fly-only:` exists because the name is deliberately unversioned, so
    `docs/infra-runbook.md` is the only place an operator can learn it exists,
    what it gates, and how to revert it (#13814 review, F4 — measured with
    `git grep -n TORTOISE_EMBEDDER_WARMUP -- docs/infra-runbook.md` -> no match).
    Nothing red on that absence: the Fly-column guard above asserts
    `fly_only & seen`, an INTERSECTION, and the existing rows satisfied it for the
    members that already had them.

    So the predicate is the review's own: **every** `fly-only:` member must be
    NAMED in the runbook, derived from the manifest on every run — never a list
    here. It is deliberately a mention test and not a matrix-row test: the tokens
    document themselves in the place their own mechanics live (§8.3 for #661,
    §"Arming the READ path" for #13209), so requiring one particular shape would
    red on a correct file. Red on the pre-fix tree, where the new member's name
    is absent from the runbook entirely.
    """
    sources = _manifest_sources(REAL_MANIFEST)
    fly_only = {n for n, s in sources.items() if s.startswith("fly-only:")}
    assert fly_only, "the shipped manifest declares no fly-only: name — the reader is wrong"

    text = REAL_RUNBOOK.read_text()
    # Non-vacuity: a runbook read that returned the wrong file, or a truncated
    # one, must not pass by naming nobody.
    assert "Secrets Matrix" in text, "REAL_RUNBOOK did not read docs/infra-runbook.md"

    missing = sorted(name for name in fly_only if name not in text)
    assert not missing, (
        "these names are declared `fly-only:` — deliberately unversioned, so the "
        "runbook is the operator's ONLY surface for them — yet they appear "
        f"nowhere in docs/infra-runbook.md: {missing}. Document the lever where an "
        "operator would look for it: what it gates, the polarity, the "
        "`fly secrets set`/`unset` pair, and the rule that unsetting it must delete "
        'the declaration in the same change (see §"Arming the READ path" and §8.3).'
    )


def _reviewer_entry_point() -> str:
    """The manifest's `ENTRY-POINT FOR A REVIEWER` paragraph, as one string.

    The block runs from its heading until the next block separator (a bare `#`)
    or the next `⛔` block — the shape the file uses, so the reader follows the
    file rather than a line range that drifts under every edit above it.
    """
    lines = REAL_MANIFEST.read_text().splitlines()
    # `next(...)` rather than a `for`/`break`: the heading's INDEX is used after
    # the loop, so a loop-control variable would be a `B007` ("not used within
    # loop body") — the lint that reddened `agent-infra-ci / lint` on the first
    # push of this probe. Same fail-closed behaviour: `None` when the heading is
    # gone.
    #
    # ⛔ The heading must be the BLOCK'S OWN line, not a prose mention of it. A
    # prose note that names this paragraph is a MENTION, and a containment match
    # takes the FIRST one — which slices a note instead of the entry point and
    # then reports that the entry point names no `fly-only:` member at all. Two
    # such notes lived in this manifest (the `DECLARED GAP` paragraph and the
    # `#13674` entry block, both deleted BY #13941's dedupe — #13926 had already taken
    # the name to route (b), and that block landed on top of it 7 minutes later, which
    # is what produced the duplicate),
    # so the artifact carries no live example — the anchor stays because the next
    # note written above this block re-creates the trap. Measured while fixing
    # #13814's review: adding one note that named the paragraph cost a full
    # failure of
    # `test_the_reviewer_entry_point_names_every_fly_only_member` (all three names
    # reported missing). The same trap was fixed for `_declared_gap_paragraph`'s
    # anchor in the commit that reconciled the two P2 fixes; this is the twin the
    # reconciliation left on containment.
    heading = next(
        (
            index
            for index, line in enumerate(lines)
            if line.lstrip("#").strip().lstrip("\u2500").strip().startswith(
                "ENTRY-POINT FOR A REVIEWER"
            )
        ),
        None,
    )
    if heading is None:
        raise AssertionError(
            "the manifest has no reviewer entry point — the probe is stale, and a "
            "stale probe would pass vacuously"
        )
    block: list[str] = []
    for line in lines[heading + 1:]:
        if line.startswith("# ⛔"):
            break
        stripped = line.lstrip("#").strip()
        if not stripped:
            break
        block.append(stripped)
    return " ".join(block)


def _reserved_members_paragraph() -> str:
    """The manifest's `RESERVED for a decision...` paragraph (the members list), as one string.

    Same reader shape as `_reviewer_entry_point` — from the paragraph's opening
    until the next block separator (a bare `#`) — so it follows the file rather
    than a line range that drifts under every edit above it.
    """
    lines = REAL_MANIFEST.read_text().splitlines()
    heading = next(
        (index for index, line in enumerate(lines) if "RESERVED for a decision" in line),
        None,
    )
    if heading is None:
        raise AssertionError(
            "the manifest has no RESERVED-members paragraph — the probe is stale, and a "
            "stale probe would pass vacuously"
        )
    block: list[str] = []
    for line in [lines[heading], *lines[heading + 1:]]:
        stripped = line.lstrip("#").strip()
        if not stripped:
            break
        block.append(stripped)
    return " ".join(block)


def _declared_gap_paragraph() -> str:
    """The manifest's `DECLARED GAP` paragraph — the `fly-only:` entries with NO ruling.

    Same reader shape as `_reserved_members_paragraph` — from the paragraph's
    opening until the next block separator (a bare `#`) — so it follows the file
    rather than a line range that drifts under every edit above it. The heading is
    matched as `⚠️ <COUNT> DECLARED GAP(S)` at the start of the stripped line —
    `re.match` is start-anchored, so a prose line that merely CONTAINS "ONE
    DECLARED GAP" still does not anchor — rather than on `DECLARED GAP` alone. ⛔ The
    COUNT is part of the anchor, not the literal `ONE` — that part stands on its own: a
    correctly
    declared second gap heads its paragraph `⚠️ TWO DECLARED GAPS`, and pinning
    `ONE` made that artifact red while demanding a gap it had already declared
    (#13814 review rounds 10-11).

    Returns `""` when the paragraph is ABSENT, which is the correct and expected
    end state once every `fly-only:` member carries its recorded ruling: the
    paragraph exists only while a gap does.
    """
    lines = REAL_MANIFEST.read_text().splitlines()
    # The heading must OPEN its line, not merely contain the phrase: a prose note
    # that NAMES this paragraph (the `#13674` entry block did — `… the `ONE DECLARED
    # GAP` paragraph above …`) would let a containment match anchor the reader
    # inside that note the moment the real paragraph is deleted — which is exactly
    # the stale-removal case this reader exists to report, so a containment match
    # would make it pass. Both that block and the paragraph are gone now — deleted by
    # #13941's dedupe, NOT by #13926: the block landed 7 minutes AFTER #13926 merged,
    # which is exactly how the duplicate got in,
    # so the line anchor is what makes this reader report the absence instead.
    #
    # ⛔ THE COUNT IS PART OF THE ANCHOR, not the literal `ONE`. Pinning `⚠️ ONE
    # DECLARED GAP` made a CORRECTLY declared SECOND gap invisible: measured (#13814
    # review round 10) on a fully consistent two-gap artifact — a second unruled
    # member, heading `⚠️ TWO DECLARED GAPS`, both refs named, gap count `TWO`, entry
    # point `FOUR`, runbook row — the suite was `1 failed, 144 passed`, and the one
    # red demanded a gap the artifact had ALREADY declared ("appear neither in the
    # RESERVED-members paragraph … nor in the DECLARED GAP paragraph"). A misled lane
    # then promotes the unruled entry into the ruled set — the exact weakening this
    # guard exists to prevent. The count arm this reader feeds already accepts
    # `ONE|TWO|…|SIX`, so a literal anchor contradicted the arm it feeds.
    heading = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(
                r"⚠️ (?:ONE|TWO|THREE|FOUR|FIVE|SIX) DECLARED GAPS?\b",
                line.lstrip("#").strip(),
            )
        ),
        None,
    )
    if heading is None:
        return ""
    block: list[str] = []
    for line in [lines[heading], *lines[heading + 1:]]:
        stripped = line.lstrip("#").strip()
        if not stripped:
            break
        block.append(stripped)
    return " ".join(block)


def test_the_reserved_member_paragraph_names_every_fly_only_member():
    """The contract paragraph and the gap paragraph must not be traded for each other.

    Two failures, both read off the ARTIFACT rather than off a review label.

    (1) The paragraph drifted: it declared `#661` and `#13209` while this manifest
    also declared a third `fly-only:` member (`TORTOISE_EMBEDDER_WARMUP`, `#13674`),
    contradicting the `ENTRY-POINT FOR A REVIEWER` paragraph a few lines below it
    which names all three. Prose is why it recurs — no test read it while the
    entry-point paragraph has had a reader since `#13370`.

    (2) The repair for (1) then did the one thing the paragraph must never allow:
    it REPLACED the contract sentence *"each with its own recorded ruling"* with
    *"their rulings do NOT all exist yet"*, i.e. it amended the reservation to
    admit a member that does not satisfy it — and this test pinned the weakened
    wording (#13814 review P2). That is the failure mode worth guarding hardest:
    the sentence is what makes `fly-only:` mean "a ruling exists" rather than
    "nobody declared a source" (`unmanaged`, the #4126 defect), and a guard that
    accepts the amended version locks the weakening in.

    So the properties asserted here are:

    * the contract sentence is present in the RESERVED paragraph, **unqualified** —
      a "not yet recorded" / "do not all exist" / "without a recorded ruling"
      qualifier inside it REDS, however it is re-wrapped;
    * an unruled member is declared in its OWN `DECLARED GAP` paragraph, which
      states that the entry is not a member of the ruled set;
    * the two paragraphs PARTITION this manifest's `fly-only:` refs — no ref in
      both (two paragraphs contradicting each other), none in neither (a member
      whose status nobody declared);
    * each paragraph's spelled count equals its own set, so a declared gap that is
      silently promoted, or a member silently dropped, reds on the artifact
      disagreeing with itself;
    * no entry BORROWS another member's ref — a fourth entry spelled
      `NEW_LEVER fly-only:#13209` adds a `fly-only:` declaration whose ruling lives
      on someone else's issue, which the REF partition above cannot see.

    Every ref and both counts are derived FROM the manifest — never a list in the
    test. Measured reds: against the pre-#13674 manifest it reds on the missing
    ref (`#13674`); against the weakened paragraph it introduced it reds on the
    contract sentence and on the "not yet recorded" qualifier inside it.
    """
    sources = _manifest_sources(REAL_MANIFEST)
    refs = sorted({source.split(":", 1)[1] for source in sources.values() if source.startswith("fly-only:")})
    assert refs, "the shipped manifest declares no fly-only: name — the reader is wrong"

    reserved = _reserved_members_paragraph()
    gap = _declared_gap_paragraph()

    # (1) The contract sentence, unqualified. The forbidden spellings are the
    #     weakenings themselves, not a paraphrase of them: each one is a phrase
    #     that admits an unruled member into the set the sentence defines.
    assert "each with its own recorded ruling" in reserved.lower(), (
        "the RESERVED-members paragraph no longer carries the contract sentence "
        "'each with its own recorded ruling', so the reservation no longer says what "
        "it reserves for — a `fly-only:` entry whose ruling does not exist would then "
        f"be indistinguishable from `unmanaged`:\n--- paragraph ---\n{reserved}"
    )
    weakenings = (
        "not yet recorded",
        "do not all exist",
        "not all exist",
        "without a recorded ruling",
        "no recorded ruling",
    )
    present = [phrase for phrase in weakenings if phrase in reserved.lower()]
    assert not present, (
        "the RESERVED-members paragraph QUALIFIES its own contract sentence, i.e. it "
        "has been amended to admit a member that does not satisfy it — the reservation "
        "is then unfalsifiable and `fly-only:` collapses towards `unmanaged`. An "
        "unruled member belongs in the separate DECLARED GAP paragraph; the contract "
        f"sentence stays unqualified. Offending phrase(s): {present}\n"
        f"--- paragraph ---\n{reserved}"
    )

    # (2) Partition: each `fly-only:` ref is either ruled (named above the
    #     separator) or a declared gap (named below it) — never both, never neither.
    ruled_refs = [ref for ref in refs if ref in reserved]
    gap_refs = [ref for ref in refs if ref in gap]
    both = [ref for ref in refs if ref in reserved and ref in gap]
    assert not both, (
        "a `fly-only:` ref is named as a RULED member and as a DECLARED GAP at the "
        "same time, so the manifest states two different things about one entry — "
        "the two paragraphs disagree, which is how the members list drifted before: "
        f"{both}\n--- reserved ---\n{reserved}\n--- gap ---\n{gap}"
    )
    undeclared = [ref for ref in refs if ref not in reserved and ref not in gap]
    assert not undeclared, (
        "the manifest declares `fly-only:` ref(s) that appear neither in the "
        "RESERVED-members paragraph (the ruled set) nor in the DECLARED GAP "
        "paragraph (the unruled ones), so the artifact no longer says whether their "
        "rulings exist — which is the whole content of the token. Declare the "
        f"ruling (a reviewer reads it) or the gap: {undeclared}\n"
        f"--- reserved ---\n{reserved}\n--- gap ---\n{gap}"
    )

    # (2b) ONE ENTRY PER REF. (2)'s partition is over REFS, so an entry that
    #      BORROWS another member's ref satisfies it: `NEW_LEVER fly-only:#13209`
    #      puts a RULED ref in the set without adding a ruling, and every other
    #      prose guard here stays green — measured (#13814 review round 8) by
    #      adding exactly that entry with the entry-point count bumped to FOUR and
    #      the name mentioned in the runbook: 5 tests passed. The promises above
    #      and in the manifest ("a fourth `fly-only:` entry that is neither ruled
    #      nor declared a gap REDS") then hold for the REF list while a fourth
    #      ENTRY is ruled by nobody. The reservation is per ENTRY — "each with its
    #      OWN recorded ruling" — so a shared ref is a BORROWED ruling, not a
    #      second one.
    entries = [
        (name, source.split(":", 1)[1])
        for name, source in sources.items()
        if source.startswith("fly-only:")
    ]
    ref_counts: dict[str, int] = {}
    for _name, ref in entries:
        ref_counts[ref] = ref_counts.get(ref, 0) + 1
    borrowed = sorted(f"{name} -> {ref}" for name, ref in entries if ref_counts[ref] > 1)
    assert not borrowed, (
        "two or more `fly-only:` ENTRIES share one decision ref, so at most one of "
        "them can be backed by that ruling and the other is riding on it. The "
        "partition above counts REFS and cannot see this, so a borrowed ref keeps "
        "every other guard here green while the artifact still reads as if each "
        "entry had its own ruling. Give each entry its OWN ref: "
        f"{borrowed}\n--- reserved ---\n{reserved}\n--- gap ---\n{gap}"
    )

    number_words = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6}
    member_match = re.search(r"\b(ONE|TWO|THREE|FOUR|FIVE|SIX)\b\s+members?\b", reserved, re.IGNORECASE)
    assert member_match, (
        "the RESERVED-members paragraph no longer states how many members the RULED "
        f"set is, so its count can drift unnoticed: {reserved!r}"
    )
    stated_members = number_words[member_match.group(1).upper()]
    assert stated_members == len(ruled_refs), (
        f"the RESERVED-members paragraph says {member_match.group(1).upper()} ruled "
        f"member(s), but {len(ruled_refs)} `fly-only:` ref(s) are named there and are "
        f"not declared gaps: {ruled_refs}"
    )

    # (3) The gap paragraph is present exactly when a gap exists, and its count is
    #     its own: a partial revert that deletes the member but keeps the paragraph
    #     (or promotes it without deleting the paragraph) reds here.
    if not gap_refs:
        assert not gap, (
            "the manifest carries a DECLARED GAP paragraph while no `fly-only:` ref "
            "is declared a gap — the paragraph is stale, and a stale paragraph is "
            f"what a reader trusts instead of re-measuring: {gap!r}"
        )
        return
    gap_match = re.search(r"\b(ONE|TWO|THREE|FOUR|FIVE|SIX)\b\s+DECLARED\b", gap, re.IGNORECASE)
    assert gap_match, (
        "the DECLARED GAP paragraph no longer states how many gaps it is describing, "
        f"so a second unruled member could join it unnoticed: {gap!r}"
    )
    stated_gaps = number_words[gap_match.group(1).upper()]
    assert stated_gaps == len(gap_refs), (
        f"the DECLARED GAP paragraph says {gap_match.group(1).upper()} gap(s), but "
        f"{len(gap_refs)} `fly-only:` ref(s) are named there: {gap_refs}"
    )


def test_the_reserved_member_paragraph_keeps_the_recorded_ruling_precondition():
    """A `fly-only:` precondition is a CONTRACT; a contract is not amended to fit a member.

    `#13814` declared a third `fly-only:` member whose ruling did not exist yet.
    That member is HISTORY, not shipped state: #13926 took the name to route (b) and
    this change deleted the member and its `⚠️ ONE DECLARED GAP` paragraph, so the
    manifest declares TWO `fly-only:` members and this arm now runs with an EMPTY gap
    set — it still asserts the rule's sentence, which is the point.
    The way to declare a gap is a GAP named against an UNCHANGED rule. The way not
    to declare it is to re-word the rule into a status report — which is what this
    file said at `d63e5a9e8e`, *"THREE members are declared below, and their
    rulings do NOT all exist yet"*, replacing `origin/main`'s *"each with its own
    recorded ruling"*. Under the weakened wording a FOURTH member needs no ruling
    either, and the sentence the token exists to enforce asserts nothing
    enforceable, so nothing can ever red on it. The member count and the per-ref
    membership are the sibling test's property; THIS one is the sentence.

    It is written against the regression, not against either review's preferred
    prose: it is GREEN on `origin/main`'s paragraph (the rule, plus two members
    that meet it) and on the restored paragraph (the same rule, an explicit gap
    for the third), and RED only on the `d63e5a9e8e` re-wording. So it neither
    pre-empts the owner's decision on `#13674` nor needs editing when that
    decision lands under either outcome — a ruling keeps the entry beside the
    unchanged rule, and a version-control re-route deletes the entry and leaves
    the same rule standing.

    What it deliberately does NOT do is check that a ruling EXISTS on the issue
    the ref names. The checker is hermetic and offline by design — `fly-only:` is
    SHAPE ONLY, and the manifest's own note assigns the existence half to a
    reviewer — so a check here could only re-read the same prose it is asserting
    about, which is how the paragraph drifted twice already.
    """
    reserved = _reserved_members_paragraph()
    assert "each with its own recorded ruling" in reserved, (
        "the RESERVED-members paragraph no longer carries the precondition that makes "
        "a `fly-only:` declaration meaningful — that each member's issue carries its "
        "own RECORDED ruling. A member whose ruling is still owed must be declared as a "
        "GAP against this unchanged sentence; re-wording the sentence so it admits an "
        "unruled member makes the one property this paragraph exists for unfalsifiable "
        "(#13814 review P2: 'their rulings do NOT all exist yet').\n"
        f"--- paragraph ---\n{reserved}"
    )

    # ⛔ PRESENCE OF THE PREDICATE IS NOT THE PROPERTY. The regression has a SECOND
    # route: keep the sentence and concede, beside it in the same paragraph, that it
    # is not met ("…each with its own recorded ruling. Not all of the members'
    # rulings exist yet."). The rule's words are then still there while the
    # paragraph states the rule is already false — the same unfalsifiable state, and
    # the assertion above passes it. This arm enforces the sentence's QUALIFICATION
    # directly — and no manifest prose promises it any more, because THIS change deleted
    # the note that used to ("the sentence must be present UNQUALIFIED …"; it lived in the
    # `ONE DECLARED GAP` paragraph of the `fly-only:#13674` block, which landed seven
    # minutes AFTER #13926 merged — #13926 never touched it), so do not re-derive the
    # contract from a citation that is gone: the check IS the contract. Measured on
    # this tree: that wording passes the assertion above and reds only here.
    #
    # Several spellings, because the defect is the SPEECH ACT (the paragraph
    # conceding that the precondition is not met), not one string — a guard that knew
    # only the review's exact wording would pass every paraphrase of it.
    concession = next(
        (
            match
            for pattern in (
                r"\bdo(?:es)?\s+not\s+all\s+(?:exist|carry|have|record|land)\b",
                r"\bnot\s+all\b[^.]{0,60}\b(?:exist|exists|recorded|present|carry|carries|have|has|land)\b",
                r"\bnot\s+every\b[^.]{0,60}\b(?:exists?|recorded|present|carries|carry|has|have|lands?)\b",
                r"\b(?:rulings?|markers?)\b[^.]{0,40}\bdo(?:es)?\s+not\s+all\b",
            )
            if (match := re.search(pattern, reserved, re.IGNORECASE)) is not None
        ),
        None,
    )
    assert concession is None, (
        "the RESERVED paragraph still carries the precondition's WORDS but concedes, in "
        f"the same breath, that it is not met ({concession.group(0)!r}). A sentence that "
        "admits the rulings are not all recorded cannot be falsified by any future "
        "member, so the reservation stops refusing anything while still reading as a "
        "guard. The gap is already declared in its OWN paragraph — leave this one "
        f"asserting the contract only.\n--- paragraph ---\n{reserved}"
    )


def test_the_reviewer_entry_point_names_every_fly_only_member():
    """The entry point is the ONE place a reviewer learns which names are out of band.

    `fly-only:`'s existence half is a reviewer's job (the checker validates the
    ref's SHAPE only, by design), so a member the entry point does not name is a
    member review never opens — the gate stays green on it and nobody reads the
    ruling it points at. That has now happened twice: #13370's P2-2 corrected
    this same paragraph from "the one name" to two, and #13814 added a third
    member while the paragraph still said TWO.

    Prose is why it recurred, so this derives both halves FROM the manifest —
    never a list in the test: every `fly-only:` member must be named, and the
    spelled-out count must equal the number of members. It reds on the pre-fix
    paragraph in BOTH directions (the missing name, and `TWO` against three).
    """
    sources = _manifest_sources(REAL_MANIFEST)
    fly_only = sorted(n for n, s in sources.items() if s.startswith("fly-only:"))
    assert fly_only, "the shipped manifest declares no fly-only: name — the reader is wrong"

    entry = _reviewer_entry_point()
    missing = [name for name in fly_only if name not in entry]
    assert not missing, (
        "the reviewer entry point does not name every `fly-only:` member, so a "
        "reviewer following it never opens their blocks (the checker validates the "
        f"ref's shape only): {missing}\n--- entry point ---\n{entry}"
    )

    number_words = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6}
    match = re.search(r"\b(ONE|TWO|THREE|FOUR|FIVE|SIX)\b\s+names?\b", entry, re.IGNORECASE)
    assert match, f"the entry point no longer states how many names are out of band: {entry!r}"
    stated = number_words[match.group(1).upper()]
    assert stated == len(fly_only), (
        f"the reviewer entry point says {match.group(1).upper()} name(s) are NOT managed "
        f"from version control, but the manifest declares {len(fly_only)}: {fly_only}"
    )


#: The two violation LEADERS an operator meets while moving this name between its
#: sources. They are the SEARCH KEYS for the runbook's quotes, never the whole
#: assertion: the test below also drives the real gate for each state and requires
#: the quote to be a prefix of the message that run ACTUALLY prints (#13814 review
#: P2-2 — a leader occurs many times in the checker, so a leader-only pin cannot
#: see a renamed message).
#:
#: ⛔ The pair is `fly-toml-env`'s, NOT `fly-only:`'s: #13926 declared this name
#: `fly-toml-env` (route (b), `fly.toml [env]` + the manifest line) and explicitly
#: rejected the `fly-only:` token for it, so the states a reader meets are the ones
#: that route can produce — a live Fly secret shadowing `[env]`, and the
#: declaration gone while that secret is still live.
_WARMUP_REVERT_REDS = ("UNDECLARED —", "STALE DECLARATION —")


def _embedder_warmup_runbook_section() -> str:
    """The runbook's `### The embedder warm-up switch` section, as one string.

    Sliced by HEADINGS, not by a line range: the runbook is appended to by other
    changes, so a numeric range here would be stale on the next merge — the exact
    defect class `test_the_embedder_warmup_runbook_citations_resolve_to_the_flag_readers`
    exists to catch.
    """
    lines = REAL_RUNBOOK.read_text().splitlines()
    heading = next(
        (i for i, ln in enumerate(lines) if ln.startswith("### The embedder warm-up switch")),
        None,
    )
    if heading is None:
        raise AssertionError(
            "docs/infra-runbook.md has no '### The embedder warm-up switch' section — "
            "the operator surface this test guards is gone"
        )
    end = next((i for i in range(heading + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
    return "\n".join(lines[heading:end])


def _warmup_revert_red_states() -> dict[str, str]:
    """The EXACT violation the real gate prints for each state of this name.

    Driven END-TO-END through `check-fly-secret-drift.py`, one fixture per state,
    because a message is identified by more than its leader: `#13814` review P2-2
    measured that reading only the LEADER out of the source cannot show what an
    operator will see, and both leaders are non-unique in the checker (11x
    `STALE DECLARATION —`, 2x `UNDECLARED —`), so renaming the leader of the exact
    message the runbook quotes left the previous version of this guard GREEN.

    The `UNDECLARED` fixture's manifest is literally named
    `fly-managed-secrets.txt`, because that message quotes the file NAME and the
    runbook quotes the message — a fixture with any other name would bind a
    sentence an operator can never see.
    """
    name = "TORTOISE_EMBEDDER_WARMUP"
    # State 1: declared `fly-toml-env`, an assigned [env] key, AND still a Fly
    # secret -> the shadow refusal (the state #13923's transition is IN).
    shadowed = _run(
        _secrets_file([*_ALL_DECLARED, name], "warmup-shadowed.json"),
        manifest=_fixture("warmup-shadowed.txt", _MANIFEST + f"{name}  fly-toml-env\n"),
        toml=_fixture("warmup-shadowed.toml", _FLY_TOML + f'  {name} = "0"\n'),
    )
    # State 2: the declaration is gone, the Fly secret is still live -> UNDECLARED.
    undeclared_manifest = Path(tempfile.mkdtemp(prefix="fly-secret-drift-undeclared-")) / (
        "fly-managed-secrets.txt"
    )
    undeclared_manifest.write_text(_MANIFEST)
    live_undeclared = _run(
        _secrets_file([*_ALL_DECLARED, name], "removal-live-undeclared.json"),
        manifest=undeclared_manifest,
    )

    emitted: dict[str, str] = {}
    for proc, leader in (
        (shadowed, "STALE DECLARATION —"),
        (live_undeclared, "UNDECLARED —"),
    ):
        assert proc.returncode == 1, proc.stdout + proc.stderr
        line = next(
            (
                candidate.split("  - ", 1)[1]
                for candidate in proc.stdout.splitlines()
                if candidate.startswith("  - ")
                and candidate.split("  - ", 1)[1].startswith(leader)
                and name in candidate
            ),
            None,
        )
        assert line is not None, (
            f"the gate printed no {leader!r} violation naming {name!r} — either its "
            "message or its output shape moved, and this probe is stale. Update this "
            f"probe and the runbook's removal guidance together:\n{proc.stdout}"
        )
        emitted[leader] = line
    return emitted


#: The IDENTIFIERS each state's runbook quote must carry, on top of the leader.
#: A leader alone IS a prefix of the emitted message, so a prefix assertion on its
#: own accepts a quote reduced to the bare leader — after which both gate bodies can
#: be reworded with the suite still green (#13814 review round 6, reproduced). These
#: are the three things the operator needs in order to act on the message: WHICH
#: variable, which SOURCE it is declared against, and WHICH file to edit.
_WARMUP_REVERT_QUOTE_TOKENS = {
    "STALE DECLARATION —": ("TORTOISE_EMBEDDER_WARMUP", "fly-toml-env"),
    "UNDECLARED —": ("TORTOISE_EMBEDDER_WARMUP", "fly-managed-secrets.txt"),
}


def _runbook_quoted_red_states(section: str) -> dict[str, str]:
    """The verbatim violation text the runbook quotes, per half of the revert."""
    quotes: dict[str, str] = {}
    for line in section.splitlines():
        for leader in _WARMUP_REVERT_REDS:
            if leader in quotes:
                continue
            match = re.search("`(" + re.escape(leader) + "[^`]*)`", line)
            if match:
                quotes[leader] = match.group(1)
    return quotes


def test_the_warmup_revert_guidance_names_both_red_states():
    """The two states a move between this name's sources can be RED in, and the runbook must name both.

    The gate holds both halves of the `fly-toml-env` declaration: while the live
    Fly secret shadows `[env]` the declaration describes a source that loses
    (`STALE DECLARATION`), and if the declaration is deleted while the secret is
    still live the name is undeclared (`UNDECLARED`). An operator who splits a
    revert across two changes meets the second in the window between them — and
    the previous text named only the first, so that order's failure read as
    "the revert broke something" (#13674, measured on the issue).

    ⛔ Not "red in EITHER order", which this docstring asserted until review
    round 2 and which is FALSE on this route: with the `fly-toml-env`
    declaration standing, UNSETTING the secret is GREEN on this name
    (`fly.toml [env]` alone satisfies the declaration — measured, three states,
    against the shipped manifest / `fly.toml` / workflow). That order is
    prescribed by the manifest's route-(b) block for a BEHAVIOURAL reason (flag
    unset ⇒ warm-up ON ⇒ ~196 s of extra boot per boot until a deploy applies
    `[env]`, #13926), so no red here catches it and this guard must not claim
    one does. The runbook says the same thing in the same place.

    The runbook's quotes are bound to the gate's ACTUAL output, end-to-end, so this
    cannot stay green while it quotes a sentence the gate no longer prints. THREE
    relations, because any one alone has a hole: the quote must carry the IDENTIFIERS
    an operator acts on (the variable, and the issue ref or the file); it must be a
    prefix of the message that run emits for that order; and it must cover the
    message's first `;`-delimited clause, which is the one that STATES the condition.
    A prefix alone is satisfied by the bare leader; identifiers alone would not notice
    a reworded clause; and identifiers + prefix together are satisfied by a quote that
    stops at the identifiers (#13814 review rounds 6 and 7).
    (`#13814` review P2-2: the previous version read only the two LEADERS out of
    the source, and both leaders occur many times there, so renaming the leader of
    the exact quoted message — or rewording its body — left this guard green.)
    """
    section = _embedder_warmup_runbook_section()
    # Non-vacuity: a section read that picked up the wrong region must not pass.
    # BODY-ONLY marker, deliberately: the section always CONTAINS the name (it is in
    # the heading the slice starts at), so a name-based "non-vacuity" check could
    # never fail. This sentence exists only in the body.
    assert "Number 4 is the one an operator is most likely to miss" in section, (
        "the section reader found the wrong region — the body marker is gone"
    )

    quoted = _runbook_quoted_red_states(section)
    missing = [leader for leader in _WARMUP_REVERT_REDS if leader not in quoted]
    assert not missing, (
        "the runbook's `fly-toml-env` red-state guidance names only some of the red "
        "states a move between this name's sources can meet. The SECOND is the "
        "declaration deleted while the Fly secret is still live (`UNDECLARED`) — "
        "naming one leaves the other reading as a regression: "
        f"missing {missing}"
    )

    emitted = _warmup_revert_red_states()
    for leader in _WARMUP_REVERT_REDS:
        # ⛔ A prefix is satisfied by the LEADER ALONE, so the prefix relation cannot
        # be the whole binding: the quote must still name the variable, and the
        # issue ref / the file an operator has to act on. Without this, an edit that
        # reduces both quotes to the bare leader lets every gate body be reworded
        # with the suite green (#13814 review round 6).
        absent = [tok for tok in _WARMUP_REVERT_QUOTE_TOKENS[leader] if tok not in quoted[leader]]
        assert not absent, (
            f"the runbook's {leader!r} quote no longer identifies what the operator has to "
            f"act on: {absent} missing. A quote reduced to the leader still PREFIXES the "
            f"emitted message, so the prefix check below cannot catch this on its own.\n"
            f"  quoted:   {quoted[leader]}\n  emitted:  {emitted[leader]}"
        )
        assert emitted[leader].startswith(quoted[leader]), (
            f"the runbook quotes a {leader!r} state the gate no longer prints. The runbook "
            "is read by an operator mid-revert, so it must quote the message they will "
            f"actually see.\n  quoted:   {quoted[leader]}\n  emitted:  {emitted[leader]}"
        )
        # ⛔ AND A PREFIX THAT STOPS AT THE IDENTIFIERS IS STILL TOO SHORT: the tokens
        # above are the first two clauses, so a quote ending right after them prefixes
        # the emitted message and passes both arms — leaving the STATING clause (what
        # state the name is in) free to be reworded under a green suite. Measured, round
        # 7: truncating the STALE quote at its declaration token AND rewording the gate's
        # whole tail was 142 passed. The emitted message's first `;`-delimited clause is
        # the condition; the quote must cover it. (The REMEDY lives in the clause after
        # the `;` and is not claimed here — an earlier version of this comment said it
        # was, which is exactly the prose/enforcement drift this test exists to refuse.)
        state_clause = emitted[leader].split(";", 1)[0]
        assert quoted[leader].startswith(state_clause), (
            f"the runbook's {leader!r} quote stops before the clause that states the "
            f"condition — an operator sees the identifiers but not what state the name is "
            f"in. Quote at least through {state_clause!r}."
            f"\n  quoted:   {quoted[leader]}\n  emitted:  {emitted[leader]}"
        )


def test_the_route_b_landing_precondition_lives_where_the_operator_reads_it():
    """#13941: the transition is the requirement, and the LANDING is its precondition — both in the runbook.

    The direction check on #13941 recorded, on the PR, that the route-(b) state is
    exit-1 by construction while the Fly secret lives — and a PR comment is exactly
    the artifact the next lane does NOT read. The failure mode is measured, not
    hypothetical: the branch's evidence block once reported green because its
    fixture omitted the live name.

    ⛔ The requirement is NOT "do not land this before the unset", which is what this
    docstring and the sentence it pinned said until review round 3 — that reading is
    inverted. `main`'s duplicate pair is exit **2**, which NO bypass reaches
    (`deploy-hosted.yml:743` gates RC=1 only), so the transition's own step 2 (a
    release carrying `[env]`) cannot run until the duplicate is gone (#13923:
    *"Steps 2-4 are blocked until the duplicate is removed"*). The landing is the
    precondition OF the transition; what must not be read as done is the exit-1
    state it lands in.

    So both halves are stated in the runbook's operator section and pinned here: a
    future edit that drops either sentence reds instead of quietly re-authorising
    "merge it and the release train is fine".
    """
    section = _embedder_warmup_runbook_section()
    # Non-vacuity: a section read that picked up the wrong region must not pass.
    # BODY-ONLY marker, deliberately: the section always CONTAINS the name (it is in
    # the heading the slice starts at), so a name-based "non-vacuity" check could
    # never fail. This sentence exists only in the body.
    assert "Number 4 is the one an operator is most likely to miss" in section, (
        "the section reader found the wrong region — the body marker is gone"
    )
    assert "the transition must be PERFORMED AND VERIFIED" in section, (
        "the runbook no longer states that the route-(b) transition must be performed and "
        "verified — without it the exit-1 state reads as the finished repair."
    )
    assert "PRECONDITION of the transition" in section, (
        "the runbook no longer states that removing the duplicate is the PRECONDITION of "
        "the transition. Without it the section reads as 'do not land this until the "
        "unset', which is inverted: exit-2 has NO bypass, so no deploy can carry `[env]` "
        "to the box while the duplicate stands (#13923 steps 2-4)."
    )
    assert "not the green one" in section, (
        "the runbook no longer warns that the landed state is the BYPASSABLE class and "
        "not a green one — the exact misreading the three measured exit codes exist for."
    )


#: The clause this entry ADDS for the live-secret state — the sentence #13941's review round
#: closed the value question with. Pinned as a NAME (not re-typed at the assert) so the arm and
#: its mutation probe cannot drift apart: a probe holding its own copy of the literal would keep
#: reddening after the arm was narrowed, or stop reddening after it was widened.
#:
#: ⛔ It must not be satisfied by the entry's OTHER `SHADOWS` sentences. The slice already
#: contains "A Fly secret of this name SHADOWS `[env]`" (carried unchanged from `main`), so an
#: `or "SHADOWS" in entry` disjunct let this PR's whole added sentence be DELETED with the arm
#: still green — measured, not argued (#13941 review; the probe
#: `test_the_shadow_clause_arm_reds_on_the_sentence_it_names` now reds for it).
_WARMUP_SHADOW_CLAUSE = "the value and `[env]` is SHADOWED"


def _embedder_warmup_manifest_entry() -> str:
    """The manifest's `TORTOISE_EMBEDDER_WARMUP` entry, its heading line through its declaration.

    Sliced by the entry's own opening line and its declaration line, NOT by a
    numeric range — the manifest is edited above this entry by other changes, and
    a range here would silently read a different block (the same defect class
    `_embedder_warmup_runbook_section` documents for the runbook).
    """
    lines = REAL_MANIFEST.read_text().splitlines()
    heading = next(
        (i for i, ln in enumerate(lines) if ln.startswith("# TORTOISE_EMBEDDER_WARMUP —")),
        None,
    )
    if heading is None:
        raise AssertionError(
            "the manifest has no `# TORTOISE_EMBEDDER_WARMUP — …` entry heading — the "
            "probe is stale, and a stale probe would pass vacuously"
        )
    declaration = next(
        (
            i
            for i in range(heading + 1, len(lines))
            if lines[i].split()[:1] == ["TORTOISE_EMBEDDER_WARMUP"]
        ),
        None,
    )
    if declaration is None:
        raise AssertionError(
            "the `TORTOISE_EMBEDDER_WARMUP` entry block never reaches its declaration "
            "line — the probe is stale, and a stale probe would pass vacuously"
        )
    return "\n".join(lines[heading : declaration + 1])


def test_the_warmup_entries_value_claim_is_a_measurement_not_a_standing_fact():
    """`0` is what was MEASURED at the move — not what `[env]` supplies today.

    #13941's cross-lane note asked for this line to be owned by the PR that lands
    the direction, and the reviewer recorded it as the last present-tense survivor
    of the block (`fly.toml` had already been corrected to call the same value a
    measurement). The claim is load-bearing, not cosmetic: while the live Fly
    secret of this name exists it SHADOWS `[env]`, so the sentence "the `[env]`
    value is the LIVE one" is FALSE in exactly the state the operator reads the
    block in — and step 4 of the route-(b) block above tells them to verify the
    value rather than assume it. A present-tense claim beside that instruction is
    the one sentence that can let them skip the verification (#13938's stated risk:
    a carried value that diverged from the secret's would flip the boot pre-warm
    off while the entry still claimed to be behaviour-preserving).

    ⛔ The absence arm reads the WHOLE manifest, not just this entry, so the claim
    cannot come back in a neighbouring note either; and it is asserted on the
    pre-fix text, where it is present, so this arm is not vacuous.
    """
    entry = _embedder_warmup_manifest_entry()
    # Non-vacuity: an empty or declaration-only slice would satisfy every arm below
    # while binding nothing, so the slice must actually span the commentary AND end
    # on the declaration it annotates.
    assert entry.rstrip().endswith("fly-toml-env"), (
        "the entry slice does not reach the declaration line it annotates — the reader "
        f"is stale and these arms would bind nothing:\n{entry}"
    )
    assert "MEASURED at the move" in entry, (
        "the entry no longer states that `0` was a MEASUREMENT taken at the move, which "
        "is what keeps it from being read as the value `[env]` supplies today"
    )
    assert _WARMUP_SHADOW_CLAUSE in entry, (
        "the entry no longer says that the live Fly secret supplies the value and `[env]` "
        "is SHADOWED — without it `0` reads as in force while the secret is the source "
        "(#13938's divergence risk). The entry's pre-existing sentence that says a secret "
        "of this name shadows `[env]` is NOT a substitute: it predates this change and "
        "says which route the entry takes, not that the value the operator reads is the "
        "secret's."
    )
    standing = "is the LIVE one"
    assert standing not in REAL_MANIFEST.read_text(), (
        f"the manifest asserts {standing!r} in the present tense. While a Fly secret of "
        "this name is live IT supplies the value, so the sentence is false in the state "
        "the operator reads this block in, and step 4 above tells them to verify rather "
        "than assume. State it as the value measured at the move (#13941 review)."
    )


def _manifest_with_the_warmup_shadow_clause_dropped() -> str:
    """The real manifest with the entry's added shadow clause replaced by a weaker sentence.

    The mutation is the one the arm NAMES — the sentence that says the live secret supplies
    the value — and it deliberately leaves every other `SHADOWS` occurrence in place, because
    that is exactly the shape the old `or` disjunct could not see. A missing clause raises
    rather than returning an unmutated manifest, which would make the probe vacuous.
    """
    text = REAL_MANIFEST.read_text()
    replacement = "the value and `[env]` is what the app reads"
    mutated = text.replace(_WARMUP_SHADOW_CLAUSE, replacement)
    assert _WARMUP_SHADOW_CLAUSE not in mutated, (
        f"the manifest no longer contains {_WARMUP_SHADOW_CLAUSE!r} — the mutation anchor "
        "is stale, so this probe would pass vacuously"
    )
    return mutated


def test_the_shadow_clause_arm_reds_on_the_sentence_it_names(monkeypatch):
    """The shadow arm must red when the sentence it names is dropped (#13941 review).

    The review's finding, reproduced before it was believed: at the pre-fix head the arm read
    `assert "SHADOWED" in entry or "SHADOWS" in entry`, and the entry's slice also spans
    `main`'s pre-existing "A Fly secret of this name SHADOWS `[env]`" line — so deleting this
    PR's entire sentence left the arm GREEN. An arm that cannot see the mutation it names is
    a false PASS, which is the class this file's other probes exist to close.

    This drives the REAL arm against the real manifest with that one clause changed, so the
    widening cannot come back without this reding. The untouched arm is asserted green in
    `test_the_warmup_entries_value_claim_is_a_measurement_not_a_standing_fact` above, so the
    pair cannot be satisfied by reddening every entry.
    """
    module = sys.modules[__name__]
    control = _fixture(
        "manifest-warmup-shadow-clause-dropped.txt",
        _manifest_with_the_warmup_shadow_clause_dropped(),
    )
    monkeypatch.setattr(module, "REAL_MANIFEST", control)
    try:
        test_the_warmup_entries_value_claim_is_a_measurement_not_a_standing_fact()
    except AssertionError:
        return
    raise AssertionError(
        "the entry's added shadow sentence was dropped and the arm stayed GREEN — the "
        "assertion is not pinning the clause it names (#13941 review: an `or \"SHADOWS\"` "
        "disjunct is satisfied by the entry's pre-existing sentence)"
    )


def test_the_fly_column_guard_rejects_every_no_home_spelling(monkeypatch):
    """The Fly-column guard must red on every equivalent "not here" spelling.

    End-to-end against the real guard with that one cell changed, so reverting to
    an em-dash-only test reds here. The control asserts a real `✅` cell does NOT
    red, so the guard cannot be satisfied by flagging every row.
    """
    module = sys.modules[__name__]
    for index, cell in enumerate(
        ("—", "-", "–", "n/a", "N/A", "none", "", "— (a mode flag, no secret)")
    ):
        fixture = _fixture(
            f"runbook-flymatrix-{index}.md",
            _runbook_with_matrix_fly_cell("TORTOISE_RECORD_SERVE", cell),
        )
        monkeypatch.setattr(module, "REAL_RUNBOOK", fixture)
        try:
            test_a_fly_only_name_is_not_shown_as_absent_from_the_app()
        except AssertionError:
            continue
        raise AssertionError(
            f"a `fly-only:` row whose Fly.io column reads {cell!r} was not "
            "caught — the offender still merges green (#13209 review, P2-1)"
        )
    # Control: the real shape (`✅`) names the app, not an absence.
    control = _fixture(
        "runbook-flymatrix-home.md",
        _runbook_with_matrix_fly_cell("TORTOISE_RECORD_SERVE", "✅ (armed `prefer`)"),
    )
    monkeypatch.setattr(module, "REAL_RUNBOOK", control)
    test_a_fly_only_name_is_not_shown_as_absent_from_the_app()


def _fly_minted_offenders(runbook_text: str, gh_names: set[str]) -> list[str]:
    """Runbook lines that set a `gh-secret:` name on Fly from a value minted there.

    Factored out of the guard so the recipe matcher can be pinned against every
    equivalent spelling (`fly`/`flyctl`, flags before the assignment) and against
    the correct shape, which must stay unflagged.
    """
    offenders = []
    for lineno, line in enumerate(runbook_text.splitlines(), 1):
        match = _INLINE_GENERATED_SECRET_RE.search(line)
        if match and match.group(1) in gh_names:
            offenders.append(f"line {lineno}: {line.strip()}")
    return offenders


def test_no_gh_secret_name_is_given_a_value_generated_only_on_fly():
    """A `gh-secret:` name must never be set on Fly from a value minted there.

    Any spelling of `fly secrets set NAME=$(openssl rand -hex 32)` — `flyctl`,
    flags before the assignment — writes a value only Fly holds: the GitHub
    secret is not touched, and the next `deploy-hosted` run re-stages GitHub's
    value over it — a rotation that silently reverts. This was the #8614 review
    finding in BOTH places it occurs (§1's provisioning block and §2's rotation
    recipes), so the two are bound together here rather than corrected one at a
    time. The runbook's own deploy command is the `flyctl secrets set --stage …`
    spelling, so the recipe matcher accepts it (see
    `test_the_fly_minted_guard_rejects_every_equivalent_spelling`).

    Only names the manifest declares `gh-secret:` are checked — the premise is
    the manifest, never a list maintained in this test — and a name with another
    declared source is a different contract (§8.3), so it is out of scope by
    construction.
    """
    sources = _manifest_sources(REAL_MANIFEST)
    gh_names = {n for n, s in sources.items() if s.startswith("gh-secret:")}
    assert gh_names, "the shipped manifest declares no gh-secret: name — the reader is wrong"

    offenders = _fly_minted_offenders(REAL_RUNBOOK.read_text(), gh_names)
    assert not offenders, (
        "these lines give a `gh-secret:` name a value that exists only on Fly — the "
        "GitHub secret is not updated, so the next deploy reverts the rotation:\n  "
        + "\n  ".join(offenders)
    )


def test_the_fly_minted_guard_rejects_every_equivalent_spelling(monkeypatch):
    """The runbook recipe matcher must red on every equivalent spelling.

    #8614 review (P2): the pattern anchored the name immediately after
    `fly secrets set`, so `flyctl secrets set NAME=$(…)` and
    `fly secrets set --stage NAME=$(…)` — the very defect the guard is named for,
    in spellings the runbook itself uses (`flyctl secrets set --stage …` is
    documented as the deploy's own command) — both passed. This runs the real
    guard against the real runbook with each recipe appended, and asserts the
    correct shape (`NAME="$NEW"`, the value written to GitHub first by the `NEW=…`
    line §2 teaches) is NOT flagged, so a matcher widened into a false positive
    fails here too.
    """
    sources = _manifest_sources(REAL_MANIFEST)
    gh_names = {n for n, s in sources.items() if s.startswith("gh-secret:")}
    assert gh_names, "the shipped manifest declares no gh-secret: name — the reader is wrong"
    name = "FASTAPI_INTERNAL_KEY"
    assert name in gh_names, f"{name} is no longer a gh-secret: name — the probe is stale"

    runbook = REAL_RUNBOOK.read_text()
    module = sys.modules[__name__]
    for spelling in (
        f"fly secrets set {name}=$(openssl rand -hex 32) -a tortoise-y4mjjq",
        f"flyctl secrets set {name}=$(openssl rand -hex 32) -a tortoise-y4mjjq",
        f"fly secrets set --stage {name}=$(openssl rand -hex 32) -a tortoise-y4mjjq",
        f"flyctl secrets set --stage {name}=$(openssl rand -hex 32) -a tortoise-y4mjjq",
    ):
        fixture = _fixture("runbook-fly-minted.md", runbook + "\n" + spelling + "\n")
        monkeypatch.setattr(module, "REAL_RUNBOOK", fixture)
        try:
            test_no_gh_secret_name_is_given_a_value_generated_only_on_fly()
        except AssertionError:
            continue
        raise AssertionError(
            f"this spelling sets a `gh-secret:` name on Fly from a value minted "
            f"there but the guard does not match it — the silent revert it exists "
            f"to catch merges green (#8614 review, P2): {spelling!r}"
        )

    # Control: the correct recipe (§2's `NEW=…` then `NAME="$NEW"`) must pass.
    correct = runbook + f'\nfly secrets set {name}="$NEW" --stage -a tortoise-y4mjjq\n'
    monkeypatch.setattr(module, "REAL_RUNBOOK", _fixture("runbook-correct.md", correct))
    test_no_gh_secret_name_is_given_a_value_generated_only_on_fly()


def _runbook_workflow_scope_paragraph() -> str:
    """The §2 paragraph that scopes which names GitHub manages.

    Anchored on its opening sentence, not a line number, so the guard keeps
    reading the same paragraph as the runbook grows around it.
    """
    text = REAL_RUNBOOK.read_text()
    start = text.index("The scope is the `gh-secret:` token")
    end = text.index("**One value, two homes", start)
    return text[start:end]


#: An assignment into the secrets-set payload: `ARGS+=(<NAME>=…`.
_WORKFLOW_ARG_ASSIGN_RE = re.compile(r"ARGS\+=\(([A-Z][A-Z0-9_]*)=")


def _workflow_arg_staging(workflow: Path) -> dict[str, str]:
    """Classify every ARGS staging in the deploy as `unconditional`/`guarded`.

    A name is UNCONDITIONAL when every path through the `Set all app secrets on
    Fly.io` step assigns it into `ARGS` (so the next deploy always re-stages
    it), and GUARDED when some run leaves it out — `BACKUP_SWEEP_ENABLED` is
    assigned only inside the all-backup-secrets conjunction, so an incomplete
    secret set skips the assignment and Fly keeps whatever value was there.

    Derived from the workflow's own shell (its `if`/`else` structure and any
    `&& ARGS+=(…)` inline guard), never from a list kept here, so §2's
    "re-staged on every run" claim is bound to what the deploy does (#8614
    review).
    """
    frames: list[dict] = []
    by_id: dict[int, dict] = {}
    #: name -> one list of enclosing-frame ids per assignment site
    sites: dict[str, list[list[int]]] = {}
    for line in workflow.read_text().splitlines():
        stripped = line.strip()
        if stripped == "fi":
            if frames:
                frames.pop()
            continue
        if re.match(r"^(else|elif)\b", stripped):
            if frames:
                frames[-1]["branch"] = "else"
            continue
        if re.match(r"^if\b", stripped) and re.search(r";\s*then\s*$", stripped):
            frame = {"branch": "then", "branches": {"then": set(), "else": set()}}
            frames.append(frame)
            by_id[id(frame)] = frame
            continue
        match = _WORKFLOW_ARG_ASSIGN_RE.search(line)
        if not match:
            continue
        name = match.group(1)
        inline_guarded = "&&" in line[: match.start()]
        ids = []
        for frame in frames:
            frame["branches"][frame["branch"]].add(name)
            ids.append(id(frame))
        sites.setdefault(name, []).append(ids)
        if inline_guarded:
            sites[name].append([-1])  # sentinel: inline `&&` guard, never unconditional
    staging: dict[str, str] = {}
    for name, id_lists in sites.items():
        guarded = any(
            fid == -1
            or name not in by_id[fid]["branches"]["then"]
            or name not in by_id[fid]["branches"]["else"]
            for ids in id_lists
            for fid in ids
        )
        staging[name] = "guarded" if guarded else "unconditional"
    return staging


def test_workflow_names_with_a_github_override_are_not_called_secretless():
    """§2 must not deny the GitHub secret a `workflow` name actually has.

    The round-2 scoping paragraph replaced "every secret on Fly is managed from
    GitHub" with per-token statements — but ended with "None of them has a
    GitHub Actions secret to update", which is the same false universal for the
    `workflow` token: the manifest's own comment above
    `TORTOISE_SESSION_LLM_MODEL` (the only `workflow` name with a GitHub
    counterpart) says "a GitHub secret may still override it",
    `deploy-hosted.yml` reads exactly such a secret, and §4.6 of this same
    document documents that override. Derived from the manifest + the real
    workflow, never from a list kept here, so a future `workflow` name the
    deploy reads from `secrets.X` fails this at review time instead of
    misleading an operator (#8614 review).

    The same paragraph must not deny that re-staging either: the head under
    review said "none is re-staged the way a `gh-secret:` name is", which the
    deploy contradicts for the very name the paragraph is about — it assigns
    `TORTOISE_SESSION_LLM_MODEL` into the same `flyctl secrets set --stage`
    payload every `gh-secret:` name rides, so an operator would read a Fly-side
    change to it as surviving when the next deploy reverts it (the #4126
    confusion in the opposite direction).
    """
    sources = _manifest_sources(REAL_MANIFEST)
    workflow_names = {n for n, s in sources.items() if s == "workflow"}
    assert workflow_names, (
        "the shipped manifest declares no `workflow` name — the reader is wrong"
    )
    workflow_text = REAL_WORKFLOW.read_text()
    overridden = {
        n for n in workflow_names if re.search(rf"secrets\.{re.escape(n)}\b", workflow_text)
    }
    assert overridden, (
        "no `workflow` name is read from a GitHub secret in deploy-hosted.yml — "
        "the manifest reader or this rule is stale, so the guard would pass vacuously"
    )

    para = _runbook_workflow_scope_paragraph()
    for name in sorted(overridden):
        assert name in para, (
            f"§2 tells an operator the `workflow` names have no GitHub secret, but "
            f"`{name}` is read from `secrets.{name}` by the deploy — name the override "
            f"(or drop the absolute) so the two statements cannot be read as alternatives"
        )
    assert "None of them has a GitHub Actions secret to update" not in para, (
        "the §2 scope paragraph asserts no `workflow` name has a GitHub Actions "
        f"secret to update, while the manifest + deploy define one for {sorted(overridden)} "
        "(#8614 review)"
    )

    # The deploy RE-STAGES a `workflow` name when it assigns it into the
    # secrets-set payload — the same `flyctl secrets set --stage "${ARGS[@]}"` a
    # `gh-secret:` name rides. So §2 must not tell an operator a Fly-only value
    # for one of those names survives. Derived from the workflow (both branches
    # of the model conditional use the same `ARGS+=(<NAME>=` shape), so a name
    # that stops being re-staged drops out instead of making this vacuous.
    restaged = {
        n for n in overridden if re.search(rf"ARGS\+=\({re.escape(n)}=", workflow_text)
    }
    assert restaged, (
        "no overridden `workflow` name is assigned into the secrets-set payload — "
        "the workflow reader is stale, so this half of the guard would pass vacuously"
    )
    assert "none is re-staged the way a `gh-secret:` name is" not in para, (
        "the §2 scope paragraph says no `workflow` name is re-staged the way a "
        f"`gh-secret:` name is, but the deploy assigns {sorted(restaged)} into the "
        "same `flyctl secrets set --stage` payload — a Fly-only value for one of "
        "them is reverted by the next deploy exactly like a `gh-secret:` one "
        "(#8614 review)"
    )
    sentences = re.split(r"(?<=\.)\s+", para)
    for name in sorted(restaged):
        assert any(name in s and "re-stag" in s for s in sentences), (
            f"the §2 scope paragraph does not say `{name}` is re-staged by the "
            "deploy — an operator reading it would believe a Fly-only value for "
            "that name survives when the next deploy overwrites it (#8614 review)"
        )


def test_workflow_names_whose_staging_is_conditional_are_not_called_unconditional():
    """§2 must not claim a conditionally-staged `workflow` name is re-staged.

    The round-4 text said the deploy "still re-stages them on every run", and
    closed with "a Fly-only value set for a `workflow` name is reverted by the
    next deploy exactly like a `gh-secret:` one". That is false for
    `BACKUP_SWEEP_ENABLED`, a `workflow` name the same sentence enumerates: the
    deploy assigns it only inside the all-backup-secrets conjunction, so an
    incomplete secret set skips the assignment and Fly keeps the old value
    (fail-closed, #4259 review / `docs/durability-posture.md`). Derived from
    the workflow, so the claim is bound to the deploy and this guard fails
    closed if no `workflow` name is guarded any more (#8614 review).
    """
    sources = _manifest_sources(REAL_MANIFEST)
    workflow_names = {n for n, s in sources.items() if s == "workflow"}
    assert workflow_names, (
        "the shipped manifest declares no `workflow` name — the reader is wrong"
    )
    staging = _workflow_arg_staging(REAL_WORKFLOW)
    staged = {n for n in workflow_names if n in staging}
    assert staged, (
        "no `workflow` name is assigned into the secrets-set payload — the "
        "workflow reader is stale, so this guard would pass vacuously"
    )
    guarded = {n for n in staged if staging[n] == "guarded"}
    unconditional = staged - guarded
    assert guarded, (
        "no `workflow` name is staged only inside a guard — the workflow reader "
        "is stale, so the conditional-staging clause §2 must carry would pass "
        "vacuously"
    )
    assert unconditional, (
        "no `workflow` name is staged unconditionally — the workflow reader is "
        "stale, so the unconditional half of §2 would pass vacuously"
    )

    para = _runbook_workflow_scope_paragraph()
    sentences = re.split(r"(?<=\.)\s+", para)
    for name in sorted(guarded):
        touched = [s for s in sentences if name in s]
        assert touched, (
            f"§2 does not mention the conditionally-staged `workflow` name `{name}` — "
            "an operator cannot tell that its staging rule differs (#8614 review)"
        )
        assert not any(
            re.search(r"every run|exactly like a `gh-secret:`", s) for s in touched
        ), (
            f"§2 claims `{name}` is re-staged on every run / reverted exactly like "
            "a `gh-secret:` one, but the deploy assigns it only inside a guard and "
            "otherwise leaves the Fly value untouched (#8614 review)"
        )
        assert any(
            re.search(r"only when|only if|otherwise|unless", s) for s in touched
        ), (
            f"§2 names `{name}` but does not state its staging condition — an "
            "operator would read a Fly-only value for it as reverted when the "
            "deploy actually leaves it as-is (#8614 review)"
        )


# ── #11122 — the mitigation ledger ────────────────────────────────────────────
#
# `fly-managed-secrets.txt` declares what the deploy MANAGES. The ledger declares
# what it must NOT manage, on purpose: a secret an operator removed as a live
# mitigation, which two consecutive deploys put back on 2026-11-05/06 because the
# sync step asserts a desired state and cannot tell "unconfigured" from
# "deliberately removed 40 minutes ago".
#
# The exception is enforced at SYNC time in `deploy-hosted.yml` (a ledgered name
# is dropped from the payload argv). These tests pin BOTH halves: the filter
# really suppresses the name, and the gate refuses every ledger state the
# machinery cannot honour — so the filter cannot be dropped from the workflow
# while the ledger keeps reading as a decision.

_LEDGER_MANIFEST = """\
# fixture manifest for #11122
FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY
SENTRY_DSN            gh-secret:SENTRY_DSN
OPT_SECRET            gh-secret:OPT_SECRET
ENV_ONLY_KEY          fly-toml-env
GIT_SHA               workflow
"""

# The filter is the REAL block, extracted from the shipped step — not a
# transcription. A copy drifts (the first draft of this fixture already used
# different variable names and a different default path), and a copy can be
# broken without any test noticing, which is precisely the failure the test below
# exists to catch: with a hand-copied filter the suite proves that the COPY
# filters. Extracting it means a regression in the real block turns these tests
# red (review F5).
_LEDGER_ENV_BLOCK_START = "# ⛔ #11122 — THE MITIGATION LEDGER, APPLIED TO THE ENVIRONMENT"
_LEDGER_ENV_BLOCK_END = "# ⛔ #11122 — END OF THE ENVIRONMENT BLOCK"
_LEDGER_TAIL_BLOCK_START = "# ⛔ #11122 — THE MITIGATION LEDGER, APPLIED TO THE PAYLOAD"
_LEDGER_BLOCK_END = '          flyctl secrets set --stage "${ARGS[@]}" --app tortoise-y4mjjq'


def _line_of(lines: list[str], marker: str, after: int = 0) -> int:
    """Index of the first LINE at or after `after` that contains `marker`."""
    for i in range(after, len(lines)):
        if marker in lines[i]:
            return i
    raise AssertionError(
        f"{marker!r} not found in deploy-hosted.yml at or after line {after} — a ledger "
        "region moved or was dropped; a fixture that executes it would otherwise pass "
        "vacuously against a copy"
    )


def _ledger_regions(text: str | None = None) -> tuple[str, str]:
    """The TWO shipped ledger regions, verbatim and anchored (fail-loud).

    An ENVIRONMENT block before the payload is composed (it blanks each ledgered name,
    so derived values like `BACKUP_SWEEP_ENABLED` or the versioned model default are
    computed from the deliberate absence) and a PAYLOAD block after it (the belt that
    removes the name from ARGS even when an unconditional default re-adds it). They
    are not contiguous, so they are sliced separately — a hand-copy of either would
    drift, and the first draft of this fixture used different variable names and a
    different default path.
    """
    lines = (REAL_WORKFLOW.read_text() if text is None else text).splitlines(keepends=True)
    env_start = _line_of(lines, _LEDGER_ENV_BLOCK_START)
    env_end = _line_of(lines, _LEDGER_ENV_BLOCK_END, after=env_start)
    tail_start = _line_of(lines, _LEDGER_TAIL_BLOCK_START, after=env_end)
    tail_end = _line_of(lines, _LEDGER_BLOCK_END, after=tail_start)
    assert env_start < env_end < tail_start < tail_end, (
        "marker drift: the ledger regions moved, or the environment block now runs "
        "AFTER the payload composition — which would restore the derived-value "
        "inconsistency the ordering exists to prevent"
    )
    # ⛔ POSITION, not just internal order (#11332 review round 3, P1). Without this
    # the assert below is INERT: moving the environment block to just above the tail
    # region leaves `env_start < env_end < tail_start < tail_end` true, every fixture
    # still passes (they impose their own order), and the deploy regresses to
    # composing derived values from the UNFILTERED environment — the exact fail-open
    # the two-region split exists to close, and it reads GREENER than the correct
    # order because the emptied derived name then never trips the STALE arm.
    first_compose = _line_of(lines, "ARGS=(FASTAPI_INTERNAL_KEY=")
    assert env_end < first_compose, (
        "the ledger ENVIRONMENT block now runs AFTER the payload composition starts "
        f"(composition line {first_compose + 1}, ledger block ends {env_end + 1}) — "
        "derived values such as BACKUP_SWEEP_ENABLED would be computed from the "
        "UNFILTERED environment while the tail filter removed the name from argv, "
        "shipping a payload that contradicts itself"
    )
    env_block = "".join(lines[env_start : env_end + 1])
    tail_block = "".join(lines[tail_start:tail_end])
    return env_block, tail_block


def _real_ledger_filter() -> str:
    """The whole ledger mechanism, in the order the deploy runs it."""
    env_block, tail_block = _ledger_regions()
    return env_block + tail_block


def _without_ledger_regions(text: str) -> str:
    """`text` with BOTH ledger regions cut out.

    The regions are not contiguous (the payload composition sits between them), so the
    composed `_LEDGER_FILTER` never appears verbatim in the workflow and a
    `str.replace(_LEDGER_FILTER, "")` silently removes nothing — which would leave the
    "filter removed" arm running the shipped filter and prove nothing. Whole LINES are
    cut so the `run: |` scalar keeps its indentation; the `flyctl secrets set` call is
    kept, because removing it would make the payload stage nothing and the arm would
    pass for the wrong reason.
    """
    lines = text.splitlines(keepends=True)
    env_start = _line_of(lines, _LEDGER_ENV_BLOCK_START)
    env_end = _line_of(lines, _LEDGER_ENV_BLOCK_END, after=env_start)
    tail_start = _line_of(lines, _LEDGER_TAIL_BLOCK_START, after=env_end)
    tail_end = _line_of(lines, _LEDGER_BLOCK_END, after=tail_start)
    assert env_start < env_end < tail_start < tail_end
    return "".join(lines[:env_start] + lines[env_end + 1 : tail_start] + lines[tail_end:])


_LEDGER_FILTER = _real_ledger_filter()

_LEDGER_WORKFLOW = f"""\
name: Deploy Hosted API
jobs:
  deploy:
    steps:
      - name: Set all app secrets on Fly.io
        run: |
          ARGS=(FASTAPI_INTERNAL_KEY="${{{{ secrets.FASTAPI_INTERNAL_KEY }}}}")
          ARGS+=(GIT_SHA="${{GITHUB_SHA}}")
          [ -n "${{{{ secrets.SENTRY_DSN }}}}" ] && ARGS+=(SENTRY_DSN="${{{{ secrets.SENTRY_DSN }}}}")
          [ -n "${{{{ secrets.OPT_SECRET }}}}" ] && ARGS+=(OPT_SECRET="${{{{ secrets.OPT_SECRET }}}}")
{_LEDGER_FILTER}\
          flyctl secrets set --stage "${{ARGS[@]}}"
"""

# The same payload with NO filter — the state a future edit would produce, and the
# one the gate must refuse rather than certify.
_LEDGER_WORKFLOW_UNFILTERED = _LEDGER_WORKFLOW.replace(_LEDGER_FILTER, "")

LEDGER_MANIFEST = _fixture("ledger-manifest.txt", _LEDGER_MANIFEST)
LEDGER_WORKFLOW = _fixture("ledger-workflow.yml", _LEDGER_WORKFLOW)
LEDGER_WORKFLOW_UNFILTERED = _fixture(
    "ledger-workflow-unfiltered.yml", _LEDGER_WORKFLOW_UNFILTERED
)
REAL_LEDGER = REPO_ROOT / ".github" / "scripts" / "fly-secret-mitigations.txt"

#: The entry's `expires` is the LAST authorised day (UTC), so "today" is a seam.
_TOMORROW = "2099-01-01"
_EXPIRES = "2026-11-20"


def _ledger(text: str, name: str = "ledger.txt") -> Path:
    return _fixture(name, text)


def _ledger_run(
    ledger: Path,
    *,
    workflow: Path = LEDGER_WORKFLOW,
    fly_names: list[str] | None = None,
    today: str = "2026-11-07",
    present: set[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the gate over the ledger fixtures (Fly list + ledger + workflow)."""
    secrets = _secrets_file(
        fly_names if fly_names is not None else ["FASTAPI_INTERNAL_KEY", "GIT_SHA"],
        "ledger-secrets.json",
    )
    return _run(
        secrets,
        manifest=LEDGER_MANIFEST,
        workflow=workflow,
        present=present,
        extra_env={
            "FLY_MITIGATION_LEDGER": str(ledger),
            "FLY_MITIGATION_TODAY": today,
        },
    )


def _entry(name: str = "SENTRY_DSN", expires: str = _EXPIRES, reason: str = "#11092 storm") -> str:
    return f"{name} owner=daniel expires={expires} reason={reason}\n"


def _manifest_fly_names(path: Path) -> list[str]:
    """The names the real manifest declares as Fly SECRETS (`fly-toml-env` are not)."""
    names: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1].startswith("fly-toml-env"):
            continue
        names.append(parts[0])
    return sorted(set(names))


def _drift_module():
    """`check-fly-secret-drift.py` loaded by path (it is a script, not a module).

    Loaded fresh per call so a test can read the SHIPPED ledger through the
    gate's OWN parser — a fixture that re-implemented the grammar could disagree
    with the reader the deploy uses, which is the divergence #11122 is about.
    """
    sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_fly_secret_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _shipped_ledger_names() -> list[str]:
    """The names `.github/scripts/fly-secret-mitigations.txt` records (via the gate)."""
    module = _drift_module()
    entries = module.read_mitigation_ledger(
        REAL_LEDGER, module.read_manifest(REAL_MANIFEST)
    )
    return sorted(entries)


def _names_the_ledger_empties(ledger: Path, gh_present: set[str]) -> set[str]:
    """The names `ledger` removes from the payload — the app state it REQUIRES.

    #11332 round 9 (P1): the fixture used to subtract only the entries' own names,
    "every declared Fly secret LIVE minus whatever the ledger records". That models an
    app the gate CORRECTLY refuses, because the payload does not merely copy env vars
    into `ARGS`: it COMPOSES. `BACKUP_SWEEP_ENABLED=true` is staged only when a
    nine-secret conjunction is present (a `workflow` declaration), so a ledger entry
    for ANY member empties that flag from the payload too — and `flyctl secrets set`
    is ADDITIVE, so leaving the flag live on Fly keeps the value the omission was for,
    which the gate reports as `MITIGATION NOT IN EFFECT` (exit 1). Measured: 9 of the
    48 ledgerable names reddened the sibling test, including both mitigations
    `docs/infra-runbook.md` §8.1 recommends by name.

    DERIVED, not restated: both halves are the SHIPPED gate's own
    `payload_partition`, executed against the SHIPPED workflow once with `ledger` and
    once with an EMPTY one. A copy of the rule here could disagree with the gate the
    deploy runs, which is the divergence class this whole PR is about.
    """
    module = _drift_module()
    blocks = module.extract_propagation_blocks(REAL_WORKFLOW.read_text())
    with tempfile.TemporaryDirectory(prefix="ledger-effect-") as tmp:
        empty = Path(tmp) / "empty-ledger.txt"
        empty.write_text("# no entries\n")
        a0, _u0, live0, _ = module.payload_partition(
            blocks, gh_present, ledger_override=empty
        )
        assigned, _, live, _ = module.payload_partition(
            blocks, gh_present, ledger_override=ledger
        )
    # ⛔ EXACTLY the gate's own `unmitigated`: the all-present world plus what THIS
    # run carried before the ledger and no longer does. Unioning the NO-SECRETS sample
    # would subtract `-z`-guarded names the ledger never emptied, i.e. model an app
    # state the ledger does not require — the over-wide subtraction the gate had to
    # stop doing (#11332 round 10, P2). Dropping `live0 - live` instead loses names a
    # mixed guard (`A present AND B absent`) really does empty (#11332 round 11, P3).
    return (a0 | (live0 - live)) - assigned


class TestTheMitigationLedger:
    """#11122 — a deliberate absence is a RECORDED state, enforced at sync time."""

    def test_a_ledgered_name_is_suppressed_at_sync_time(self):
        """The load-bearing property: the payload does not carry the name.

        Asserted through the gate's own EXECUTION of the payload (the stub argv),
        not by reading the YAML — a filter that is present but ineffective (wrong
        variable, wrong loop, filtered after the call) must fail HERE.
        """
        proc = _ledger_run(_ledger(_entry()))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "MITIGATED — 1 secret(s)" in proc.stdout
        assert "SENTRY_DSN (owner daniel, expires 2026-11-20)" in proc.stdout
        assert "LEDGER NOT HONOURED" not in proc.stdout

    def test_every_entry_is_suppressed_not_only_the_last(self):
        """N>1 is the NORMAL case, and nothing else pins it.

        An accumulator that OVERWRITES (`MITIGATED="$NAME "`) keeps only the last
        entry read and honours nothing else — while every structural assertion in
        `tests/test_deploy_workflow.py` stays green (the path, the `case`, the
        reassignment and the ordering are all still there). This drives BOTH entries
        through the SHIPPED filter and relies on the gate's own check (0): if only the
        last had been dropped, the first would still be in the payload's
        `assigned | unconditional | live` and the gate would answer `LEDGER NOT
        HONOURED` (exit 2) instead of 0 — which is exactly what
        `test_a_ledgered_name_the_payload_still_stages_fails_the_deploy` proves the
        instrument detects.
        """
        ledger = _ledger(_entry("SENTRY_DSN") + _entry("OPT_SECRET"))
        proc = _ledger_run(ledger)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "MITIGATED — 2 secret(s)" in proc.stdout, proc.stdout
        assert "SENTRY_DSN (owner daniel" in proc.stdout
        assert "OPT_SECRET (owner daniel" in proc.stdout
        assert "LEDGER NOT HONOURED" not in proc.stdout, (
            "only one of the two entries was dropped from the payload — an "
            "accumulator that overwrites keeps the LAST entry and honours nothing else"
        )

    def test_the_same_payload_without_the_ledger_stages_the_name(self):
        """The suppression is the LEDGER's doing, not an accident of the fixture."""
        proc = _ledger_run(_ledger("# empty\n"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "MITIGATED —" not in proc.stdout
        # …and the deploy is about to CREATE it, which the belt says out loud.
        assert "SENTRY_DSN" in proc.stdout
        assert "will CREATE" in proc.stdout

    def test_the_DEFAULT_ledger_path_is_the_one_production_takes(self, tmp_path):
        """The shipped block, executed with NO seam — the resolution a deploy uses.

        Every other test sets `FLY_MITIGATION_LEDGER` (the gate's stub harness seeds
        it so the sample it classifies is filtered by the same file), so the
        `${FLY_MITIGATION_LEDGER:-${GITHUB_WORKSPACE:-.}/…}` DEFAULT branch was never
        executed anywhere: a wrong directory there would pass the suite and, before
        the fail-loud guard landed, silently stage every mitigated secret in
        production (review round 2, P2). This runs the real block with the seam
        UNSET and `GITHUB_WORKSPACE` pointing at a tree that holds a ledger with an
        entry — and asserts, on the block's own emitted argv, that the name is gone.
        """
        repo = tmp_path / "workspace"
        (repo / ".github" / "scripts").mkdir(parents=True)
        (repo / ".github" / "scripts" / "fly-secret-mitigations.txt").write_text(_entry())
        script = (
            'ARGS=(FASTAPI_INTERNAL_KEY=k SENTRY_DSN=dsn OPT_SECRET=o)\n'
            + _LEDGER_FILTER
            + 'printf "%s\\n" "${ARGS[@]}"\n'
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            # ⛔ NO FLY_MITIGATION_LEDGER — that is the whole point of this test.
            "GITHUB_WORKSPACE": str(repo),
        }
        assert "FLY_MITIGATION_LEDGER" not in env
        proc = subprocess.run(
            ["bash", "-e", "-u", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        staged = [line.split("=", 1)[0] for line in proc.stdout.splitlines() if "=" in line]
        assert "SENTRY_DSN" not in staged, (
            "the DEFAULT ledger path resolved to nothing, so the shipped block staged "
            f"a name the ledger records as deliberately absent: {staged}"
        )
        assert "FASTAPI_INTERNAL_KEY" in staged and "OPT_SECRET" in staged, (
            f"the block staged nothing at all — the harness broke, not the filter: {staged}"
        )

    def test_a_workflow_binding_of_the_seam_is_what_the_payload_is_filtered_by(self):
        """The classified payload must mirror the DEPLOY, not the harness (#11332 P1).

        The harness seeds `FLY_MITIGATION_LEDGER` so the sample it classifies is
        filtered by a known file, but the deploy's own shell resolves
        `${FLY_MITIGATION_LEDGER:-<default>}` — so if a workflow ever BINDS that
        seam, the binding is what the real step uses. Seeding the harness's value
        first made `env.setdefault` a no-op for this one key, so the payload under
        test filtered by `/dev/null` (staging every mitigated secret) while the
        harness filtered by the fixture: the `LEDGER NOT HONOURED` tripwire stayed
        GREEN over exactly the edit it exists to catch. A seam that cannot be
        declared is a fail-open, so this pins the ordering.

        Bound to an EMPTY ledger with a ledgered `SENTRY_DSN` on the gate's side:
        the payload stages the name and the gate records it as deliberately
        absent, which is the re-arm this whole change exists to refuse.
        """
        bound_ledger = _ledger("# bound by the workflow, deliberately empty\n", name="bound.txt")
        bound_workflow = _LEDGER_WORKFLOW.replace(
            "      - name: Set all app secrets on Fly.io\n",
            "      - name: Set all app secrets on Fly.io\n"
            "        env:\n"
            f"          FLY_MITIGATION_LEDGER: {bound_ledger}\n",
        )
        assert bound_workflow != _LEDGER_WORKFLOW, (
            "the step header moved — this fixture no longer binds the seam"
        )
        proc = _ledger_run(
            _ledger(_entry("SENTRY_DSN")),
            workflow=_fixture("ledger-workflow-bound-seam.yml", bound_workflow),
        )
        assert proc.returncode == 2, (
            "the payload was classified against the harness's ledger instead of the "
            "one the workflow binds, so a deploy filtering by a different (empty) "
            f"ledger was certified OK: {proc.stdout}"
        )
        assert "LEDGER NOT HONOURED" in proc.stdout
        assert "SENTRY_DSN" in proc.stdout

    def test_an_unreadable_ledger_fails_the_deploy_instead_of_staging_everything(self, tmp_path):
        """No `else` on the `-r` guard was a silent "no mitigations" (review round 2).

        Executed, not read: the shipped block with a seam pointing at a path that does
        not exist must FAIL, never fall through to an unfiltered payload.
        """
        script = 'ARGS=(FASTAPI_INTERNAL_KEY=k SENTRY_DSN=dsn)\n' + _LEDGER_FILTER
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "FLY_MITIGATION_LEDGER": str(tmp_path / "not-here.txt"),
        }
        proc = subprocess.run(
            ["bash", "-e", "-u", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode != 0, (
            "an unreadable ledger was read as 'no mitigations' — the payload staged "
            f"every secret and nothing failed: {proc.stdout}"
        )
        assert "could not be read" in proc.stdout
        assert "#11122" in proc.stdout

    def test_a_DIRECTORY_at_the_ledger_path_fails_the_deploy_too(self, tmp_path):
        """`[ -r ]` is TRUE for a directory, and the loop's redirect does not fail loudly.

        `-r` cannot tell a ledger from a directory at the same path, and the skipped
        loop is NOT caught by the shell GitHub actually uses: measured, `bash -e`
        with `done < <dir>` prints `read error: 0: Is a directory`, runs NO loop
        body and exits **0** with `MITIGATED=" "` — every mitigated secret staged,
        nothing failed, which is the exact state the guard's own comment says it
        rules out (#11332 review, P2).

        ⛔ RUN WITH `bash -e` ONLY, NOT `-e -u`: the harness's `-u` turns the same
        state into an unbound `$LEDGER_LINE` and exits 1 by ACCIDENT, so a `-e -u`
        test passes with or without the `-f` guard and pins nothing. Production is
        `bash -e`, so that is the shell this arm must fail under.
        """
        as_dir = tmp_path / "a-directory"
        as_dir.mkdir()
        script = 'ARGS=(FASTAPI_INTERNAL_KEY=k SENTRY_DSN=dsn)\n' + _LEDGER_FILTER
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "FLY_MITIGATION_LEDGER": str(as_dir),
        }
        proc = subprocess.run(
            ["bash", "-e", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode != 0, (
            "a directory at the ledger path was read as 'no mitigations' and the "
            "deploy exited 0 — every mitigated secret staged: "
            f"{proc.stdout}{proc.stderr}"
        )
        assert "could not be read as a FILE" in proc.stdout, (
            "the guard fired for the wrong reason (or not at all) — it must name the "
            f"directory case: {proc.stdout}{proc.stderr}"
        )

    def test_the_ledger_ENVIRONMENT_block_precedes_the_payload_composition(self):
        """POSITION, not just internal order (#11332 review round 3, P1).

        The other ordering assertions say `env_start < env_end < tail_start <
        tail_end`, which stays TRUE if the environment block is moved to just above
        the tail region — and every fixture imposes its own order, so the whole suite
        passed over a workflow where the ledger ran AFTER the payload was composed.
        That is the fail-open the two-region split exists to close, and it is not a
        hypothetical: the gate certified the reordered workflow rc 0 while the
        classified payload carried `BACKUP_SWEEP_ENABLED=true` and omitted the
        ledgered name.

        A named test so the regression has one place to fail, rather than taking the
        module down at import through `_ledger_regions`.
        """
        lines = REAL_WORKFLOW.read_text().splitlines(keepends=True)
        env_end = _line_of(lines, _LEDGER_ENV_BLOCK_END)
        compose = _line_of(lines, "ARGS=(FASTAPI_INTERNAL_KEY=")
        assert env_end < compose, (
            "the ledger ENVIRONMENT block ends at line "
            f"{env_end + 1} but the payload composition starts at {compose + 1} — the "
            "ledger now runs AFTER compose, so derived values such as "
            "BACKUP_SWEEP_ENABLED are computed from the UNFILTERED environment while "
            "the tail filter removes the name from argv, shipping a payload that "
            "contradicts itself"
        )

    def test_a_ledgered_name_is_blanked_IN_THE_ENVIRONMENT_not_only_in_ARGS(self, tmp_path):
        """The ledger must be applied BEFORE the payload COMPOSES anything (#11332 P1).

        The payload does not just copy env vars into ARGS: it composes derived values
        from them — `BACKUP_SWEEP_ENABLED=true` only when a conjunction of nine secrets
        is present. A tail-only filter left those computed from the UNFILTERED values,
        so a ledger entry for `TORTOISE_BACKUP_KEY` shipped `BACKUP_SWEEP_ENABLED=true`
        in the same argv that did NOT carry the key. At boot
        `tortoise/backup_config.py` raises a ConfigError whose text asserts the now
        FALSE "deploy-hosted.yml sets BACKUP_SWEEP_ENABLED=true only when all are
        present", `alert_channel.sweep_config_safe` swallows it, and the backup sweep
        plus the staleness watcher start DISABLED with only a warning — a green gate
        over a payload that contradicts itself.

        Executed against the SHIPPED environment block and the SHIPPED conjunction (both
        sliced from `deploy-hosted.yml`, not transcribed), so a regression in either
        turns this red.
        """
        run = REAL_WORKFLOW.read_text().splitlines(keepends=True)
        start = next(
            i
            for i, line in enumerate(run)
            if 'if [ -n "$TORTOISE_BACKUP_KEY" ] && [ -n "$R2_ACCOUNT_ID" ]' in line
        )
        end = next(i for i in range(start + 1, len(run)) if run[i].strip() == "fi")
        conjunction = "".join(run[start : end + 1])
        assert "BACKUP_SWEEP_ENABLED=true" in conjunction, (
            "the shipped conjunction no longer stages BACKUP_SWEEP_ENABLED — the anchor "
            "drifted, so this fixture would prove nothing"
        )
        env_block, _tail = _ledger_regions()

        def staged(ledger_text: str, name: str) -> list[str]:
            ledger = _fixture(name, ledger_text)
            script = (
                # every input the shipped conjunction reads, all PRESENT.
                'TORTOISE_BACKUP_KEY="bk"\n'
                'R2_ACCOUNT_ID="a"\nR2_ACCESS_KEY_ID="b"\nR2_SECRET_ACCESS_KEY="c"\n'
                'R2_BUCKET="d"\nTELEGRAM_BOT_TOKEN="e"\nTELEGRAM_CHAT_ID="f"\n'
                'DR_ISSUES_PAT="g"\nBACKUP_ALERT_ASSIGNEE="h"\n'
                "ARGS=()\n" + env_block + conjunction + 'printf "%s\\n" "${ARGS[@]}"\n'
            )
            proc = subprocess.run(
                ["bash", "-e", "-u", "-c", script],
                capture_output=True,
                text=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(tmp_path),
                    "FLY_MITIGATION_LEDGER": str(ledger),
                },
                timeout=30,
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr
            return [line.split("=", 1)[0] for line in proc.stdout.splitlines() if line]

        assert "BACKUP_SWEEP_ENABLED" in staged("# empty\n", "derived-control.txt"), (
            "the CONTROL case staged nothing — the fixture no longer reproduces the "
            "conjunction, so the assertion below would prove nothing"
        )
        assert "BACKUP_SWEEP_ENABLED" not in staged(
            _entry("TORTOISE_BACKUP_KEY"), "derived-mitigated.txt"
        ), (
            "a ledger entry for TORTOISE_BACKUP_KEY removed the ARG but NOT the "
            "environment, so BACKUP_SWEEP_ENABLED=true shipped in the same payload that "
            "omitted the key — the app then boots into a ConfigError asserting an "
            "invariant this payload just broke (#11332 review, P1)"
        )

    def test_a_ledgered_name_does_not_suppress_a_SHORTER_name_it_extends(self, tmp_path):
        """The `case` DELIMITERS are the discrimination feature (#11332 review, P2).

        The ledger is matched as `" $NAME "` — leading AND trailing space — inside a
        space-delimited accumulator. Drop either delimiter and `FOO_BAR` also matches
        `FOO`, so a ledger entry for `TORTOISE_BACKUP_KEY_PREVIOUS` would suppress
        `TORTOISE_BACKUP_KEY` as well — a REAL pair in this payload
        (`deploy-hosted.yml:1270` / `:1275`, pinned by
        `tests/test_deploy_workflow.py::test_every_citation_of_the_backup_key_pair_resolves`),
        where the previous backup key is the
        entry an operator is most likely to write after a rotation. The consequence
        is not a re-arm but the mirror-image defect: the LIVE key silently stops
        syncing on every deploy, so rotation breaks (#2318) and no gate reports it.
        Suppression WIDER than the record is a false exception in the other
        direction, and it is the direction the polarity of `for ARG in "${ARGS[@]}"`
        makes reachable: the shorter name is compared against the longer ledger entry.

        Every other fixture uses names that are not prefixes of one another, so
        nothing in the suite pinned the delimiters — weakening the pattern to
        `*"$ARG_NAME"*` passed the whole suite before this test.
        """
        ledger = _ledger(_entry("TORTOISE_BACKUP_KEY_PREVIOUS"), name="prefix-ledger.txt")
        script = (
            "ARGS=(FASTAPI_INTERNAL_KEY=k TORTOISE_BACKUP_KEY=v TORTOISE_BACKUP_KEY_PREVIOUS=p)\n"
            + _LEDGER_FILTER
            + 'printf "%s\\n" "${ARGS[@]}"\n'
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "FLY_MITIGATION_LEDGER": str(ledger),
        }
        proc = subprocess.run(
            ["bash", "-e", "-u", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        staged = [line.split("=", 1)[0] for line in proc.stdout.splitlines() if "=" in line]
        assert "TORTOISE_BACKUP_KEY_PREVIOUS" not in staged, (
            f"the ledger entry for TORTOISE_BACKUP_KEY_PREVIOUS did not suppress it: {staged}"
        )
        assert "TORTOISE_BACKUP_KEY" in staged, (
            "a ledger entry for TORTOISE_BACKUP_KEY_PREVIOUS also suppressed the "
            "shorter TORTOISE_BACKUP_KEY — the match is not delimited by the "
            "space-separated accumulator, so the LIVE backup key silently stops "
            f"syncing and rotation breaks: {staged}"
        )
        assert "FASTAPI_INTERNAL_KEY" in staged, (
            f"the block staged nothing at all — the harness broke, not the filter: {staged}"
        )

    def test_a_ZERO_guarded_ledgered_name_is_caught_when_the_filter_is_removed(self):
        """The `unconditional` leg of check (0) is load-bearing, and it was unpinned.

        Check (0) asks whether a ledgered name is still STAGED, over
        `assigned | unconditional | live`. With the shipped filter working, all three
        sets exclude the name, so the legs only separate when the filter is GONE —
        which is precisely the regression the check exists to catch. A `-z`-guarded
        assignment (`[ -z "$X" ] && ARGS+=(X=…)`) runs only on the runs where the
        GitHub secret is ABSENT, so the name lands in `unconditional` and NOT in
        `assigned`: narrowing the check to `mitigated & assigned` leaves the whole
        suite green while re-arming exactly the names a mitigation removed, and it is
        the `#4126` shape this gate was built around. This fixture is that case.
        """
        zero_guarded = _LEDGER_WORKFLOW_UNFILTERED.replace(
            '[ -n "${{ secrets.OPT_SECRET }}" ] && ARGS+=(OPT_SECRET="${{ secrets.OPT_SECRET }}")',
            '[ -z "${{ secrets.OPT_SECRET }}" ] && ARGS+=(OPT_SECRET="${{ secrets.OPT_SECRET }}")',
        )
        assert zero_guarded != _LEDGER_WORKFLOW_UNFILTERED, (
            "the OPT_SECRET assignment moved — this fixture no longer produces the "
            "`-z` shape it exists to exercise"
        )
        proc = _ledger_run(
            _ledger(_entry("OPT_SECRET")),
            workflow=_fixture("ledger-workflow-zero-unfiltered.yml", zero_guarded),
            # OPT_SECRET ABSENT from GitHub, which is what turns the `-z` guard ON.
            present={"FASTAPI_INTERNAL_KEY", "SENTRY_DSN"},
        )
        assert proc.returncode == 2, (
            "a `-z`-guarded ledgered name is staged on exactly the runs where the "
            f"GitHub secret is absent, and the gate did not refuse it: {proc.stdout}"
        )
        assert "LEDGER NOT HONOURED" in proc.stdout
        assert "OPT_SECRET" in proc.stdout

    def test_a_ledgered_name_the_payload_still_stages_fails_the_deploy(self):
        """Dropping the filter from the workflow must not be a silent re-arm.

        Without this the ledger keeps reading as a decision while the deploy does
        the opposite — the "declaration the deploy no longer honours" rot the
        manifest half already refuses.
        """
        proc = _ledger_run(
            _ledger(_entry()), workflow=LEDGER_WORKFLOW_UNFILTERED
        )
        # EXIT 2, not 1 (#11122 review F1): exit 1 is the deploy's documented bypass
        # lane, so classifying a broken sync filter there lets an operator's
        # `skip-fly-secret-provenance` (armed for an unrelated stale declaration)
        # re-stage every mitigated secret — silently, because that bypass's report
        # speaks about declarations, never about mitigations.
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "LEDGER NOT HONOURED" in proc.stdout
        assert "re-arm the mitigation" in proc.stdout
        assert "never bypassable" in proc.stdout

    def test_a_ledgered_name_live_on_fly_fails(self):
        """The entry would be a false record, and it freezes that secret's rotation."""
        proc = _ledger_run(
            _ledger(_entry()),
            fly_names=["FASTAPI_INTERNAL_KEY", "GIT_SHA", "SENTRY_DSN"],
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "MITIGATION NOT IN EFFECT" in proc.stdout
        assert "flyctl secrets unset SENTRY_DSN" in proc.stdout

    def test_the_expiry_is_inclusive_and_a_lapsed_entry_keeps_suppressing(self):
        """`expires` is the LAST authorised day; past it the deploy is RED, not re-armed.

        A lapsed entry must STILL suppress (the alternative — silently restoring on
        a date — is the same unannounced overwrite with extra steps), so the RED
        state and the continuing suppression are asserted together.
        """
        ledger = _ledger(_entry())
        on_last_day = _ledger_run(ledger, today=_EXPIRES)
        assert on_last_day.returncode == 0, on_last_day.stdout + on_last_day.stderr
        assert "MITIGATION LAPSED" not in on_last_day.stdout

        lapsed = _ledger_run(ledger, today="2026-11-21")
        assert lapsed.returncode == 1, lapsed.stdout + lapsed.stderr
        assert "MITIGATION LAPSED" in lapsed.stdout
        assert "expired on 2026-11-20" in lapsed.stdout
        assert "LEDGER NOT HONOURED" not in lapsed.stdout, (
            "a lapsed entry must KEEP suppressing — re-arming it on the expiry date "
            "is the same unannounced overwrite this ledger exists to stop"
        )

    def test_the_reason_may_cite_an_issue(self):
        """`#` inside an entry is DATA — the reason names the incident (#11092).

        Comment-stripping at the first `#` (the manifest's rule, which this file
        deliberately does not inherit) would truncate the field that makes an entry
        reviewable.
        """
        proc = _ledger_run(_ledger(_entry(reason="#11092 storm; see #11122")))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "SENTRY_DSN (owner daniel, expires 2026-11-20): #11092 storm; see #11122" in proc.stdout

    def test_a_mandatory_secret_cannot_be_ledgered(self):
        """Removing it is an outage, not a mitigation — exit 2, never bypassable."""
        proc = _ledger_run(_ledger(_entry("FASTAPI_INTERNAL_KEY")))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "MANDATORY" in proc.stderr
        assert "outage, not a mitigation" in proc.stderr

    def test_an_undeclared_name_cannot_be_ledgered(self):
        proc = _ledger_run(_ledger(_entry("NOT_IN_THE_MANIFEST")))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "not declared in the managed-secrets manifest" in proc.stderr

    def test_a_non_gh_secret_source_cannot_be_ledgered(self):
        """`fly-toml-env` / `workflow` / `fly-only:` are not this deploy's to withhold."""
        proc = _ledger_run(_ledger(_entry("ENV_ONLY_KEY")))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "gh-secret:" in proc.stderr

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("SENTRY_DSN expires=2026-11-20 reason=x\n", "expected '<NAME> owner="),
            ("SENTRY_DSN owner=daniel reason=x\n", "expected '<NAME> owner="),
            ("SENTRY_DSN owner=daniel expires=2026-11-20\n", "expected '<NAME> owner="),
            ("SENTRY_DSN owner=daniel expires=20-11-2026 reason=x\n", "YYYY-MM-DD"),
            ("SENTRY_DSN owner=daniel expires=20261120 reason=x\n", "YYYY-MM-DD"),
        ],
    )
    def test_a_malformed_entry_is_unreadable_state(self, line, expected):
        """Exit 2 (fail-closed), never exit 1 (bypassable): an entry the machinery
        cannot parse reads as a decision while the deploy does the opposite."""
        proc = _ledger_run(_ledger(line))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert expected in proc.stderr

    def test_a_duplicate_entry_is_unreadable_state(self):
        proc = _ledger_run(_ledger(_entry() + _entry(expires=_TOMORROW)))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "duplicate ledger entry" in proc.stderr

    def test_a_missing_ledger_is_exit_2_not_no_mitigations(self):
        """The file is tracked; its absence means every entry is unenforced.

        Reading a missing ledger as "nothing is mitigated" is the fail-open shape
        this whole issue is about, so it is exit 2 (never bypassable) — and it
        runs BEFORE the Fly call, so a blocked deploy has touched nothing.
        """
        proc = _ledger_run(_TMP / "does-not-exist.txt")
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "mitigation ledger unusable" in proc.stderr

    def test_a_comment_only_ledger_suppresses_nothing(self):
        proc = _ledger_run(_ledger("# nothing here\n\n   \n# SENTRY_DSN owner=x expires=2026-11-20 reason=y\n"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "0 mitigated" in proc.stdout

    def test_an_indented_entry_is_refused_not_passed_on_to_the_wrong_fault(self):
        """The two readers must accept the same lines (#11122 review F6).

        The sync filter tokenises with bash's `[[:space:]]` against the RAW line, so
        an indented entry names an empty secret and is silently ignored; this reader
        used to `strip()` first and accept it. The pair then produced the worst
        possible report — the gate saw the payload still stage the name and told the
        operator to fix `deploy-hosted.yml`, a fault that is not there — whose
        plausible remedy (arm the bypass) re-arms the mitigation.
        """
        for prefix in ("  ", "\t"):
            proc = _ledger_run(_ledger(prefix + _entry()))
            assert proc.returncode == 2, (prefix, proc.stdout + proc.stderr)
            assert "starts with whitespace" in proc.stderr, proc.stderr
            assert "column 0" in proc.stderr, proc.stderr
            assert "LEDGER NOT HONOURED" not in proc.stdout, (
                "the indentation must be reported as the fault it is, not blamed "
                "on the sync step"
            )

    def test_a_cr_only_ledger_is_refused_not_blamed_on_the_sync_step(self):
        """The two readers must agree about LINE BOUNDARIES too (round-2 F1).

        `Path.read_text()` opens in universal-newline mode, so a CR-only ledger used
        to arrive stripped-and-resplit into two entries here while bash's `read` saw
        ONE line: the gate then reported `LEDGER NOT HONOURED` against
        `deploy-hosted.yml` — a false accusation, and exit 2 is not even bypassable,
        so the operator had no way past the wrong message.
        """
        two_entries = _entry() + _entry("BACKUP_ALERT_ASSIGNEE")
        for name, text in (
            ("cr-only", two_entries.replace("\n", "\r")),
            ("vertical-tab", two_entries.replace("\n", "\v")),
        ):
            proc = _ledger_run(_ledger(text, name=f"ledger-{name}.txt"))
            assert proc.returncode == 2, (name, proc.stdout + proc.stderr)
            assert "line contains" in proc.stderr, (name, proc.stderr)
            assert "LEDGER NOT HONOURED" not in proc.stdout, (
                f"{name}: the line boundary must be reported as the ledger's fault,"
                " not the sync step's"
            )

    def test_a_ledgered_name_that_fly_toml_env_also_assigns_is_refused(self):
        """`[env]` is a SECOND delivery path (round-2 F4).

        Unsetting the secret does not remove a value `fly.toml` still assigns, so the
        entry would be a false record with a GREEN gate: the app keeps reading the
        value while every line of the ledger says the name is deliberately absent.
        `fly-toml-env` and `fly-only:` are already refused `name in env_keys`; a
        `gh-secret:` name has to be caught by the ledger's own check.
        """
        toml = _fixture(
            "ledger-env.toml",
            _FLY_TOML.replace(
                "[env]\n", '[env]\n  SENTRY_DSN = "https://example.invalid/1"\n', 1
            ),
        )
        assert '"SENTRY_DSN"' in toml.read_text() or "SENTRY_DSN =" in toml.read_text()
        proc = _run(
            _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "ledger-env-secrets.json"),
            manifest=LEDGER_MANIFEST,
            workflow=LEDGER_WORKFLOW,
            toml=toml,
            present={"SENTRY_DSN", "FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry())),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "assigns it a NON-EMPTY value" in proc.stdout, proc.stdout

    @pytest.mark.parametrize(
        ("literal", "allowed"),
        [
            ('""', True),  # the real fly.toml placeholder form
            ("''", True),  # single-quoted empty — same value, different quoting
            ('"0"', False),  # falsy-looking but a REAL value the deploy applies
            ('"false"', False),  # ditto
            ('"   "', False),  # whitespace is a value, not an absence
        ],
    )
    def test_only_an_EMPTY_fly_toml_env_value_lets_the_entry_through(self, literal, allowed):
        """`[env] = ""` is a PLACEHOLDER the Fly secret SHADOWS — a value is not.

        `fly.toml` carries five empty ones (`TORTOISE_BACKUP_KEY`, `R2_*`) purely to
        document the names. A Fly secret shadows `[env]`, so with the secret ABSENT
        the app reads the empty string — which IS the deliberate absence the ledger
        records, and not a second delivery path. Treating any `[env]` KEY as blocking
        made the five most likely incident mitigations (unset the backup key, unset an
        R2 credential) UNRECORDABLE at exit 2, never bypassable, while the ledger
        header and the runbook used `TORTOISE_BACKUP_KEY` as their worked example
        (#11332 review, P1).

        ⛔ Parametrized over the shapes a truthiness test would get WRONG: quoting is
        normalised (`''` is empty too), but `"0"`, `"false"` and whitespace are REAL
        values the deploy applies, so they must still block. A test that only covered
        `""` would pass on an implementation that stripped quotes and nothing else.
        """
        toml = _fixture(
            f"ledger-env-{literal.strip(chr(34) + chr(39)) or 'empty'}-{len(literal)}.toml",
            _FLY_TOML.replace("[env]\n", f"[env]\n  SENTRY_DSN = {literal}\n", 1),
        )
        assert f"SENTRY_DSN = {literal}" in toml.read_text()
        proc = _run(
            _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "ledger-env-value-secrets.json"),
            manifest=LEDGER_MANIFEST,
            workflow=LEDGER_WORKFLOW,
            toml=toml,
            present={"SENTRY_DSN", "FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry())),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        if allowed:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert "LEDGER NOT HONOURED" not in proc.stdout, proc.stdout
            assert "SENTRY_DSN (owner daniel" in proc.stdout, proc.stdout
        else:
            assert proc.returncode == 2, proc.stdout + proc.stderr
            assert "LEDGER NOT HONOURED" in proc.stdout, proc.stdout
            assert "assigns it a NON-EMPTY value" in proc.stdout, proc.stdout

    def test_EVERY_legal_TOML_spelling_of_an_env_assignment_is_read_the_same(self):
        """#11332 rounds 11–12 — the `[env]` reader is `tomllib`, so the CLASS is closed.

        `[env]` is a SECOND delivery path: a ledger entry says a name is deliberately
        absent, and check (0b-ii) refuses it when `fly.toml [env]` still supplies a
        value. Every spelling this reader fails to recognise therefore becomes a
        **false PASS** — `rc=0 MITIGATED` over a mitigation that is not in effect.

        This was a hand-rolled line scanner for four review rounds, and each round
        closed one spelling: a header with a trailing comment (round 5), a value the
        scanner could not see (round 6), a quoted key or quoted header (round 11). The
        last escape was demonstrated end to end — `"\\u0053ENTRY_DSN" = "…"` is the
        same key as `SENTRY_DSN` (`tomllib` agrees), and the scanner produced
        `rc=0 MITIGATED` where the bare spelling produced `rc=2 LEDGER NOT HONOURED`.
        So the fix is not another arm: the reader is now `tomllib`, the reference
        parser, and every spelling below resolves to the same `[env]` entry Fly reads.

        Every row must produce the SAME verdict, so this is a statement about the
        SPELLING and nothing else. `bare` is the control.
        """
        # (label, whole-file rewrite of the shared fixture)
        spellings = {
            "bare": lambda toml: toml.replace(
                "[env]\n", '[env]\n  SENTRY_DSN = "https://real.invalid/dsn"\n', 1
            ),
            "quoted-key": lambda toml: toml.replace(
                "[env]\n", '[env]\n  "SENTRY_DSN" = "https://real.invalid/dsn"\n', 1
            ),
            "literal-key": lambda toml: toml.replace(
                "[env]\n", "[env]\n  'SENTRY_DSN' = 'https://real.invalid/dsn'\n", 1
            ),
            "escaped-key": lambda toml: toml.replace(
                "[env]\n",
                '[env]\n  "\\u0053ENTRY_DSN" = "https://real.invalid/dsn"\n',
                1,
            ),
            "quoted-header": lambda toml: toml.replace(
                "[env]\n", '["env"]\n  SENTRY_DSN = "https://real.invalid/dsn"\n', 1
            ),
            "spaced-header": lambda toml: toml.replace(
                "[env]\n", "[ env ]\n  SENTRY_DSN = \"https://real.invalid/dsn\"\n", 1
            ),
            # These two carry the table WITHOUT a header, so they replace it outright.
            "dotted-root": lambda toml: toml.replace(
                "[env]\n    ENV_ONLY_KEY".replace("    ", "  "),
                'env.SENTRY_DSN = "https://real.invalid/dsn"\n  ENV_ONLY_KEY',
                1,
            ),
            "inline-table": lambda toml: toml.replace(
                "[env]\n    ENV_ONLY_KEY".replace("    ", "  "),
                'env = { SENTRY_DSN = "https://real.invalid/dsn", ENV_ONLY_KEY = "1" }'
                "\n  #",
                1,
            ),
        }
        for label, rewrite in spellings.items():
            text = rewrite(_FLY_TOML)
            assert text != _FLY_TOML, (
                f"the {label} fixture did not take — the assertion below would be "
                "about the unmodified file"
            )
            toml = _fixture(f"ledger-env-{label}.toml", text)
            proc = _run(
                _secrets_file(
                    ["FASTAPI_INTERNAL_KEY", "GIT_SHA"], f"spelling-{label}.json"
                ),
                manifest=LEDGER_MANIFEST,
                workflow=LEDGER_WORKFLOW,
                toml=toml,
                present={"SENTRY_DSN", "FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
                extra_env={
                    "FLY_MITIGATION_LEDGER": str(_ledger(_entry())),
                    "FLY_MITIGATION_TODAY": "2026-11-07",
                },
            )
            assert proc.returncode == 2, (
                f"the {label} spelling was not read as an `[env]` assignment — "
                "`fly.toml` still supplies SENTRY_DSN, so the gate certified a "
                "mitigation that is not in effect: " + proc.stdout + proc.stderr
            )
            assert "LEDGER NOT HONOURED" in proc.stdout, proc.stdout
            assert "assigns it a NON-EMPTY value" in proc.stdout, proc.stdout

    def test_an_UNPARSEABLE_fly_toml_fails_closed(self):
        """A `fly.toml` this gate cannot PARSE must certify nothing — exit 2.

        ⛔ CORRECTION OF A ROUND-6 CLAIM, made when the reader became `tomllib`. The
        `SENTRY_DSN =` / newline / `"https://…"` shape this test used to build is NOT
        valid TOML — `tomllib`, the reference parser, rejects it (`Invalid value` at
        the line after the `=`), and Fly's own parser would reject it too. So the
        round-6 finding described an input that cannot exist in a real `fly.toml`;
        the scanner's lenient reading of it could only ever produce a REFUSAL, never
        a false PASS. The test is kept because the SAFETY PROPERTY it should have
        pinned is real and now holds for every unparseable file:

        a `[env]` table this guard cannot classify is an unclassifiable DELIVERY PATH,
        and check (0b-ii) must not certify a deliberate absence against it.

        ⛔ `LEDGER NOT HONOURED` is deliberately NOT asserted: failing to parse comes
        earlier, so the gate never reaches the `[env]` comparison. Asserting the
        specific verdict here would re-pin the old, wrong premise.
        """
        toml = _fixture(
            "ledger-env-unparseable.toml",
            _FLY_TOML.replace(
                "[env]\n",
                '[env]\n  SENTRY_DSN =\n    "https://real.invalid/dsn"\n',
                1,
            ),
        )
        assert 'SENTRY_DSN =\n    "https://real.invalid/dsn"' in toml.read_text()
        proc = _run(
            _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "ledger-env-ml-secrets.json"),
            manifest=LEDGER_MANIFEST,
            workflow=LEDGER_WORKFLOW,
            toml=toml,
            present={"SENTRY_DSN", "FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry())),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        assert proc.returncode == 2, (
            "an unparseable fly.toml did not fail closed: " + proc.stdout + proc.stderr
        )
        assert "cannot determine secret provenance" in proc.stderr, proc.stderr
        assert "MITIGATED" not in proc.stdout, (
            "the gate certified a mitigation against a `[env]` table it could not "
            "classify: " + proc.stdout
        )

    def test_an_env_HEADER_with_a_trailing_comment_still_parses(self):
        """`[env] # versioned config` is legal TOML — it must not disable the table.

        Requiring the stripped line to END with `]` left `in_env` False for the whole
        file, so `fly_toml_env_assignments` returned `{}` and the ledger accepted an
        entry for a name `[env]` assigns a real value to. Round 5 fixed it; nothing
        pinned it, so a revert was silent (#11332 review round 6, P2).
        """
        toml = _fixture(
            "ledger-env-header-comment.toml",
            _FLY_TOML.replace(
                "[env]\n",
                '[env] # versioned config\n  SENTRY_DSN = "https://real.invalid/dsn"\n',
                1,
            ),
        )
        assert "[env] # versioned config" in toml.read_text()
        proc = _run(
            _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "ledger-env-hc-secrets.json"),
            manifest=LEDGER_MANIFEST,
            workflow=LEDGER_WORKFLOW,
            toml=toml,
            present={"SENTRY_DSN", "FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry())),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        assert proc.returncode == 2, (
            "a trailing comment on the `[env]` header disabled the table, so a name it "
            f"assigns was accepted as mitigaable: {proc.stdout}"
        )
        assert "LEDGER NOT HONOURED" in proc.stdout, proc.stdout

    def test_a_workflow_binding_of_PATH_cannot_break_the_classification(self):
        """The harness keeps its OWN PATH/HOME, so a step env cannot empty the stubs.

        `_capture_payload` merges the step/job `env:` with `env.setdefault`, which
        leaves PATH/HOME authoritative — and that is load-bearing: the payload is
        classified on a stub PATH holding only `fly`/`flyctl`, so a workflow that
        bound `PATH: /usr/bin` would make `flyctl` unfindable and the payload
        unclassifiable. The guard after the merge raises `ValueError` (exit-2 class,
        never the bypassable exit 1) if that ever stops being true.

        Measured: with `PATH` bound in the step env the run is a normal rc 0 whose
        payload was actually captured — which is only possible through the stubs, so
        this is the pin that the harness wins. (#11332 review round 6 raised both the
        guard and its class; the guard is unreachable TODAY, which is the property
        worth pinning.)
        """
        bound = _fixture(
            "path-bound-workflow.yml",
            _LEDGER_WORKFLOW.replace(
                "      - name: Set all app secrets on Fly.io\n",
                "      - name: Set all app secrets on Fly.io\n"
                "        env:\n"
                "          PATH: /usr/bin:/bin\n"
                "          HOME: /tmp\n",
                1,
            ),
        )
        assert "PATH: /usr/bin:/bin" in bound.read_text()
        proc = _ledger_run(_ledger(_entry(), name="pb.txt"), workflow=bound)
        assert proc.returncode == 0, (
            "a workflow binding of PATH/HOME leaked into the classification — the stub "
            f"`fly`/`flyctl` would be unfindable: rc={proc.returncode} "
            f"{proc.stdout}{proc.stderr}"
        )
        # A classification actually happened (so the stubs ran), and it is not the
        # harness's failure path.
        assert "MITIGATED — 1 secret(s)" in proc.stdout, proc.stdout
        assert "cannot determine secret provenance" not in proc.stdout + proc.stderr

    def test_a_ledgered_CONJUNCTION_MEMBER_does_not_report_the_derived_name_STALE(self):
        """The ledger emptying a derived name is the MITIGATION, not a stale declaration.

        `BACKUP_SWEEP_ENABLED` is declared `workflow`: the payload assigns it only when
        a conjunction of secrets is present. Blanking ONE ledgered member makes that
        conjunction false, so the name is never assigned — and the STALE arm then
        reported "declared workflow-set but deploy-hosted.yml never assigns it" with
        the remedy "manage it from deploy-hosted.yml", whose only reading is to make
        the assignment UNCONDITIONAL. That destroys the fail-closed default the
        conjunction exists for (#545), so the recorded mitigation could not be deployed
        without breaking the property it protects (#11332 review, P1).

        The gate now classifies the same payload against an EMPTY ledger; a `workflow`
        name only that world assigns is reported as LEDGER-EMPTIED, not STALE.
        """
        manifest = _fixture(
            "derived-manifest.txt", _LEDGER_MANIFEST + "BACKUP_SWEEP_ENABLED  workflow\n"
        )
        # The shipped filter is KEPT (so the ledgered name really is suppressed), the
        # step binds SENTRY_DSN through `env:` so the derived guard can read it, and
        # the derived assignment reads the SHELL VARIABLE — which is what the
        # environment block blanks.
        workflow = _fixture(
            "derived-workflow.yml",
            _LEDGER_WORKFLOW.replace(
                "      - name: Set all app secrets on Fly.io\n",
                "      - name: Set all app secrets on Fly.io\n"
                "        env:\n"
                "          SENTRY_DSN: ${{ secrets.SENTRY_DSN }}\n",
                1,
            ).replace(
                '          flyctl secrets set',
                '          if [ -n "${SENTRY_DSN-}" ]; then\n'
                "            ARGS+=(BACKUP_SWEEP_ENABLED=true)\n"
                "          fi\n"
                "          flyctl secrets set",
                1,
            ),
        )
        assert "BACKUP_SWEEP_ENABLED" in workflow.read_text()
        common = {
            "manifest": manifest,
            "workflow": workflow,
            "present": {"FASTAPI_INTERNAL_KEY", "OPT_SECRET", "SENTRY_DSN"},
        }

        def run(ledger: Path, secrets_name: str):
            return _run(
                _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], secrets_name),
                **common,
                extra_env={
                    "FLY_MITIGATION_TODAY": "2026-11-07",
                    "FLY_MITIGATION_LEDGER": str(ledger),
                },
            )

        control = run(_ledger("# empty\n", name="d0.txt"), "derived-control.json")
        assert control.returncode == 0, control.stdout + control.stderr
        assert "STALE DECLARATION" not in control.stdout, (
            "the CONTROL run must be clean — the derived name IS assigned when nothing "
            f"is ledgered, so this is what the assertion below rests on: {control.stdout}"
        )
        proc = run(_ledger(_entry("SENTRY_DSN"), name="d1.txt"), "derived-mitigated.json")
        assert proc.returncode == 0, (
            "ledgering a member of the conjunction reported the DERIVED name STALE, so "
            "the operator cannot record the mitigation without making the assignment "
            f"unconditional and losing the fail-closed default: {proc.stdout}"
        )
        assert "STALE DECLARATION" not in proc.stdout, proc.stdout
        assert "LEDGER-EMPTIED" in proc.stdout and "BACKUP_SWEEP_ENABLED" in proc.stdout, (
            "the explanation must be reported, not silently swallowed: " + proc.stdout
        )

    def test_a_PERSISTED_derived_flag_the_ledger_empties_is_a_FALSE_RECORD(self):
        """`flyctl secrets set` is ADDITIVE — an omission does not clear the old value.

        `BACKUP_SWEEP_ENABLED=true` is staged `true` on every healthy deploy. A ledger
        entry for a member of its conjunction empties it from the payload, and Fly
        KEEPS the `true` already there: the app boots enabled-with-missing-secrets,
        `tortoise/backup_config.py` raises a ConfigError that
        `alert_channel.sweep_config_safe` swallows, and the sweep plus the staleness
        watcher start disabled. The ledger's absence is then NOT in effect, so the
        entry is a false record — reported as such with the one-command remedy, rather
        than certified as "the mitigation working" (#11332 review round 4, P1).
        """
        manifest = _fixture(
            "derived-live-manifest.txt", _LEDGER_MANIFEST + "BACKUP_SWEEP_ENABLED  workflow\n"
        )
        workflow = _fixture(
            "derived-live-workflow.yml",
            _LEDGER_WORKFLOW.replace(
                "      - name: Set all app secrets on Fly.io\n",
                "      - name: Set all app secrets on Fly.io\n"
                "        env:\n"
                "          SENTRY_DSN: ${{ secrets.SENTRY_DSN }}\n",
                1,
            ).replace(
                "          flyctl secrets set",
                '          if [ -n "${SENTRY_DSN-}" ]; then\n'
                "            ARGS+=(BACKUP_SWEEP_ENABLED=true)\n"
                "          fi\n"
                "          flyctl secrets set",
                1,
            ),
        )
        proc = _run(
            _secrets_file(
                ["FASTAPI_INTERNAL_KEY", "GIT_SHA", "BACKUP_SWEEP_ENABLED"],
                "derived-live-secrets.json",
            ),
            manifest=manifest,
            workflow=workflow,
            present={"FASTAPI_INTERNAL_KEY", "OPT_SECRET", "SENTRY_DSN"},
            extra_env={
                "FLY_MITIGATION_TODAY": "2026-11-07",
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry("SENTRY_DSN"), name="dl.txt")),
            },
        )
        assert proc.returncode == 1, (
            "the ledgered input emptied a derived flag that is ALREADY live on Fly, so "
            "the omission does not take effect — the run must not be green: "
            + proc.stdout
        )
        assert "MITIGATION NOT IN EFFECT" in proc.stdout, proc.stdout
        assert "BACKUP_SWEEP_ENABLED" in proc.stdout, proc.stdout
        assert "flyctl secrets unset BACKUP_SWEEP_ENABLED" in proc.stdout, proc.stdout

        # ⛔ THE SECOND DELIVERY PATH: a non-empty `fly.toml [env]` value supplies the
        # name whatever the secret list says, so the omission is not in effect either —
        # and testing only `fly_names` left `LEDGER-EMPTIED` GREEN over exactly that
        # state, which is the false record the `mitigated` half already refuses
        # (`env_assignments`, check 0b-ii). Latent today only because no `workflow`
        # name is in `[env]`; the asymmetry is the bug (#11332 review round 5, P1).
        toml = _fixture(
            "derived-live-env.toml",
            _FLY_TOML.replace(
                "[env]\n", '[env]\n  BACKUP_SWEEP_ENABLED = "true"\n', 1
            ),
        )
        assert 'BACKUP_SWEEP_ENABLED = "true"' in toml.read_text()
        via_env = _run(
            _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "derived-env-secrets.json"),
            manifest=manifest,
            workflow=workflow,
            toml=toml,
            present={"FASTAPI_INTERNAL_KEY", "OPT_SECRET", "SENTRY_DSN"},
            extra_env={
                "FLY_MITIGATION_TODAY": "2026-11-07",
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry("SENTRY_DSN"), name="de.txt")),
            },
        )
        assert via_env.returncode == 1, (
            "a NON-EMPTY fly.toml [env] value keeps supplying the derived name after "
            "the payload drops it, so the recorded absence is not in effect — the run "
            "must not be green: " + via_env.stdout
        )
        assert "MITIGATION NOT IN EFFECT" in via_env.stdout, via_env.stdout
        assert "BACKUP_SWEEP_ENABLED" in via_env.stdout, via_env.stdout

    def test_the_gate_runs_over_the_REAL_workflow_with_a_non_empty_ledger(self):
        """The other end-to-end test pins the ledger EMPTY (round-2 F5).

        `test_shipped_manifest_matches_the_real_workflow_and_recorded_debt` seeds every
        declared name as live on Fly, which is exactly the state a real entry makes
        illegal — so it has to run with an empty ledger, and that left NO test
        executing the gate over the real `deploy-hosted.yml` with a non-empty one. A
        second staging path added to the real step would have passed the whole suite.
        """
        declared = _manifest_fly_names(REAL_MANIFEST)
        target = "SENTRY_DSN"
        assert target in declared, "the real manifest no longer declares SENTRY_DSN"
        proc = _run(
            _secrets_file([n for n in declared if n != target], "real-ledger-secrets.json"),
            manifest=REAL_MANIFEST,
            workflow=REAL_WORKFLOW,
            toml=REAL_FLY_TOML,
            present=_declared_gh_names(REAL_MANIFEST),
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry(target, expires=_TOMORROW))),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "MITIGATED — 1 secret(s)" in proc.stdout, proc.stdout
        assert "LEDGER NOT HONOURED" not in proc.stdout, (
            "the SHIPPED sync step no longer suppresses a ledgered name — a second "
            "staging path, or a filter that stopped matching"
        )

    def test_the_suppression_is_the_REAL_filter_not_a_copy(self):
        """The fixture executes the block that ships (review F5).

        `_LEDGER_FILTER` is sliced out of `deploy-hosted.yml` by its own markers, so
        a regression in the real block — a dropped trailing space in the name set, a
        `continue` that becomes `break`, the assignment back to `ARGS` removed — turns
        this red. With a hand-copied fixture the suite proved only that the COPY
        filtered, and the shipped filter was never executed with a non-empty ledger at
        all (the other end-to-end test runs it against the real ledger, which is
        deliberately empty).
        """
        assert "MITIGATION_LEDGER" in _LEDGER_FILTER, (
            "the fixture filter is not the shipped block — the anchors in "
            "`_real_ledger_filter` drifted, so these tests would prove nothing"
        )
        assert "STAGED_ARGS" in _LEDGER_FILTER
        proc = _ledger_run(_ledger(_entry()))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "LEDGER NOT HONOURED" not in proc.stdout
        # …and with the block removed, the SAME fixture stages the name — which is
        # what makes the assertion above about the filter rather than about the ledger.
        unfiltered = _ledger_run(_ledger(_entry()), workflow=LEDGER_WORKFLOW_UNFILTERED)
        assert unfiltered.returncode == 2, unfiltered.stdout + unfiltered.stderr

    def test_the_belt_warns_only_about_names_the_ledger_could_record(self):
        """The fallback warning must be ACTIONABLE: one aggregated line, and only
        `gh-secret:` names — a `workflow` name cannot be ledgered (the validator
        refuses it), so telling the reader to record it would be wrong advice.

        ⛔ `GIT_SHA` is the arm that makes the `gh-secret:` restriction
        non-vacuous, and it was NOT one until #11332 review observation 1: the
        fixture's default Fly list carries it, so it was never in
        `live - fly_names` and deleting the restriction left this row green
        (measured). It is now driven OFF the Fly app (`fly_names` below), which is
        the only state in which the restriction is the thing keeping it out of the
        warning: `GIT_SHA` is `workflow`-declared, unconditionally assigned by the
        payload, and absent from the app — a member of the candidate set that only
        `.startswith("gh-secret:")` excludes.
        """
        # Premise, re-derived from the fixture manifest rather than restated: the
        # excluded name is a `workflow` declaration (the kind the ledger refuses).
        sources = _manifest_sources(LEDGER_MANIFEST)
        assert sources.get("GIT_SHA") == "workflow", (
            "GIT_SHA is no longer the fixture's `workflow` declaration — this row's "
            "non-vacuity arm is stale"
        )
        proc = _ledger_run(_ledger("# empty\n"), fly_names=["FASTAPI_INTERNAL_KEY"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        warning = [
            line for line in proc.stdout.splitlines() if "will CREATE" in line
        ]
        assert len(warning) == 1, "exactly one aggregated warning line, not N annotations"
        assert "SENTRY_DSN" in warning[0]
        assert "GIT_SHA" not in warning[0], (
            "a `workflow` name is not the ledger's to record — and it IS in the "
            "`live - fly_names` candidate set here, so only the `gh-secret:` "
            "restriction keeps it out of the warning"
        )

        # A ledgered name is not "about to be created" — it is deliberately absent.
        suppressed = _ledger_run(_ledger(_entry()), fly_names=["FASTAPI_INTERNAL_KEY"])
        assert "will CREATE" in suppressed.stdout  # OPT_SECRET is still absent
        for line in suppressed.stdout.splitlines():
            if "will CREATE" in line:
                assert "SENTRY_DSN" not in line

    def test_the_shipped_workflow_honours_the_shipped_ledger(self):
        """#11372 — the JOINT case: the SHIPPED workflow driven by the SHIPPED ledger.

        No test covered the pair. The sibling end-to-end test pins the ledger EMPTY (a
        real entry means `MITIGATION NOT IN EFFECT` against its all-names-live fixture),
        the non-empty test uses a ledger FIXTURE, and the shipped-ledger test only
        PARSES it. So the first real entry could be first validated by a blocked
        production deploy — exit 2, never bypassable — during exactly the incident
        window the header tells the operator to write it in.

        The fixture is the Fly app the operator is told to produce: every declared Fly
        secret LIVE **minus every name the shipped ledger empties from the payload** —
        the entries themselves AND the `workflow`-composed values an entry empties (see
        `_names_the_ledger_empties`). So the assertion holds for any future ledger
        content, empty or not. Today the ledger is entry-free and the subtraction is a
        no-op; the day an entry lands this test keeps meaning the same thing without an
        edit — which is the point, because the `MITIGATION NOT IN EFFECT` arm it used to
        trip on is the gate telling the operator to unset the composed flag.

        `FLY_MITIGATION_TODAY` is deliberately NOT pinned: this runs the gate exactly as
        the deploy does, so an entry that has LAPSED reds here as it reds the deploy.
        """
        ledgered = _shipped_ledger_names()
        declared = _manifest_fly_names(REAL_MANIFEST)
        present = _declared_gh_names(REAL_MANIFEST)
        # …minus every name the ledger removes from the PAYLOAD, which is not the same
        # set as the entries themselves: the payload COMPOSES (see the helper).
        # Subtracting only the entries modelled an app the gate correctly refuses, so
        # the FIRST entry on any of the conjunction's nine members reddened this test
        # for every PR (#11332 round 9, P1).
        emptied = _names_the_ledger_empties(REAL_LEDGER, present)
        live = [n for n in declared if n not in emptied]
        proc = _run(
            _secrets_file(live, "shipped-ledger-secrets.json"),
            manifest=REAL_MANIFEST,
            workflow=REAL_WORKFLOW,
            toml=REAL_FLY_TOML,
            present=present,
            extra_env={"FLY_MITIGATION_LEDGER": str(REAL_LEDGER)},
        )
        assert "LEDGER NOT HONOURED" not in proc.stdout, (
            "the shipped ledger records a name the shipped `Set all app secrets on "
            "Fly.io` step still stages — the deploy would re-arm the mitigation. "
            + proc.stdout
        )
        assert proc.returncode == 0, (
            "the shipped ledger's entry is not honoured by the shipped deploy — if the "
            "gate says `MITIGATION LAPSED`, an entry's `expires` has passed: delete it "
            "or re-affirm it. The clock is UNPINNED here on purpose (the deploy's own "
            "resolution), so that red is the mechanism working, not this test rotting.\n"
            + proc.stdout
            + proc.stderr
        )
        # …and the gate named the shipped file it read and how many entries it acted
        # on. That pins "the gate read the shipped LEDGER", not "the seam was
        # honoured": REAL_LEDGER is the same path as the gate's DEFAULT_LEDGER, so this
        # line reads identically if a seam were ignored. The seam itself is pinned by
        # `test_the_DEFAULT_ledger_path_is_the_one_production_takes`.
        assert f"{len(ledgered)} mitigated by {REAL_LEDGER.name}" in proc.stdout, proc.stdout

        # ── non-vacuity: the same assertion about the SAME real workflow, with only
        # the shipped filter removed, must go RED. Without this the test above would
        # also pass on a payload that stages nothing, or on a gate that silently read
        # no ledger at all.
        unfiltered = _fixture(
            "real-workflow-unfiltered.yml",
            _without_ledger_regions(REAL_WORKFLOW.read_text()),
        )
        assert unfiltered.read_text() != REAL_WORKFLOW.read_text(), (
            "the shipped ledger block was not removed from the copy — the anchors in "
            "`_real_ledger_filter` drifted, so the arm below would prove nothing"
        )
        target = ledgered[0] if ledgered else "SENTRY_DSN"
        assert target in declared, f"the real manifest no longer declares {target}"
        red = _run(
            _secrets_file([n for n in declared if n != target], "real-unfiltered-secrets.json"),
            manifest=REAL_MANIFEST,
            workflow=unfiltered,
            toml=REAL_FLY_TOML,
            present=_declared_gh_names(REAL_MANIFEST),
            extra_env={
                "FLY_MITIGATION_LEDGER": str(_ledger(_entry(target, expires=_TOMORROW))),
                "FLY_MITIGATION_TODAY": "2026-11-07",
            },
        )
        assert red.returncode == 2, red.stdout + red.stderr
        assert "LEDGER NOT HONOURED" in red.stdout, red.stdout

    def test_a_ledgered_CONJUNCTION_member_models_the_app_the_gate_asks_for(self):
        """#11332 round 9 (P1) — the first REAL entry, executed, not just reasoned about.

        The sibling test reads the SHIPPED ledger, which is entry-free by design, so it
        cannot exercise the case the mechanism exists for. This one drives the SHIPPED
        workflow with a single entry on a member of the payload's own conjunction — the
        entry shape `docs/infra-runbook.md` §8.1 recommends by name — and pins both
        directions:

        1. the app state the ledger REQUIRES (the member absent, and the composed flag it
           empties absent too) is GREEN, where the ledger is honoured and the omission
           has an effect; and
        2. the app state the OLD fixture modelled (the member absent, the composed flag
           still LIVE) is RED with `MITIGATION NOT IN EFFECT` — which is what makes
           (1) a statement about the derivation rather than a relaxed assertion.

        ⛔ (2) is not a hypothetical: `flyctl secrets set` is ADDITIVE, so on that app
        the flag keeps its old `true` while the payload stops stating it, and the boot
        then reads enabled-with-missing-secrets — the ConfigError
        `tortoise/backup_config.py` raises and `alert_channel` swallows. The gate is
        right to refuse it; the FIXTURE was wrong to model it.
        """
        # The entry's name is DERIVED from the shipped conjunction, so this cannot rot
        # into a name the payload does not compose from.
        run = REAL_WORKFLOW.read_text().splitlines(keepends=True)
        start = next(
            i
            for i, line in enumerate(run)
            if 'if [ -n "$TORTOISE_BACKUP_KEY" ] && [ -n "$R2_ACCOUNT_ID" ]' in line
        )
        end = next(i for i in range(start + 1, len(run)) if run[i].strip() == "fi")
        conjunction = "".join(run[start : end + 1])
        composed = re.search(r"ARGS\+=\((\w+)=", conjunction)
        assert composed, (
            "the shipped conjunction no longer stages a composed value — the anchor "
            "drifted, so this fixture would prove nothing"
        )
        member = re.search(r'\[ -n "\$(\w+)" \]', conjunction)
        assert member, "the shipped conjunction no longer reads a `$NAME` guard"
        name, flag = member.group(1), composed.group(1)

        declared = _manifest_fly_names(REAL_MANIFEST)
        present = _declared_gh_names(REAL_MANIFEST)
        assert name in declared, f"the conjunction reads {name}, which the manifest omits"
        assert flag in declared, (
            f"{flag} is composed by the payload but the manifest does not declare it — "
            "the `workflow` arm this test reasons about would not run"
        )

        ledger = _ledger(_entry(name, expires=_TOMORROW), "conjunction-member.txt")
        emptied = _names_the_ledger_empties(ledger, present)
        assert {name, flag} <= emptied, (
            f"a ledger entry for {name} should empty BOTH itself and the composed "
            f"{flag} from the payload — got {sorted(emptied)}. Without the composed "
            "half this test cannot tell a derived fixture from one that subtracts "
            "the entries alone"
        )

        def drive(live: list[str]) -> subprocess.CompletedProcess[str]:
            return _run(
                _secrets_file(live, "conjunction-secrets.json"),
                manifest=REAL_MANIFEST,
                workflow=REAL_WORKFLOW,
                toml=REAL_FLY_TOML,
                present=present,
                extra_env={"FLY_MITIGATION_LEDGER": str(ledger)},
            )

        # (1) The app the ledger requires: every declared Fly secret EXCEPT what the
        # entry empties from the payload. This is the state §8.1 tells the operator to
        # produce, and it must be a green deploy.
        required = drive([n for n in declared if n not in emptied])
        assert required.returncode == 0, required.stdout + required.stderr
        assert "MITIGATION NOT IN EFFECT" not in required.stdout

        # (2) Non-vacuity: hold the composed flag LIVE on Fly and the SAME run is
        # refused. This is the old fixture's app state, and it is the one the gate is
        # right to block — so (1) proves the derivation does the work.
        stale = drive([n for n in declared if n != name])
        assert stale.returncode == 1, stale.stdout + stale.stderr
        assert "MITIGATION NOT IN EFFECT" in stale.stdout, stale.stdout
        assert flag in stale.stdout, (
            f"the refusal no longer names {flag}, so this arm is no longer about the "
            "composed value"
        )
        assert "LEDGER NOT HONOURED" not in stale.stdout, (
            "the entry is being reported as unhonoured rather than as a false record — "
            "that is a different defect and the exit-2 class"
        )

    def test_a_CONDITIONAL_workflow_name_is_not_explained_away_by_an_unrelated_entry(self):
        """#11332 round 10 (P2) — `unmitigated` is the ALL-PRESENT sample, not a union.

        The `workflow` arm reads `if name not in assigned and name in unmitigated:` →
        "the ledger emptied it", which suppresses the `STALE DECLARATION` that would
        otherwise refuse the deploy. That sentence is only true of a name the payload
        WOULD have carried, so `unmitigated` must be the empty-ledger ALL-PRESENT
        sample. Unioning the other two samples made it true of names the ledger never
        touched — `u0` is the NO-SECRETS world, so it holds every `[ -z "$X" ]`-guarded
        assignment by construction — and the measured effect was that writing ONE true
        entry silenced an unrelated `STALE DECLARATION`: `rc=1` on an empty ledger,
        `rc=0` the moment an entry for a different name existed.

        ⛔ The fix must not narrow the set so far that the LEGITIMATE case breaks — a
        `workflow` name composed from a ledgered input, which really is "the ledger
        emptied it". That direction is already pinned against the SHIPPED payload by
        `test_a_ledgered_CONJUNCTION_member_models_the_app_the_gate_asks_for` (it
        asserts `BACKUP_SWEEP_ENABLED` is emptied and the deploy is green), so it is not
        re-modelled here: a fixture composition that reads `${{ secrets.X }}` directly
        is NOT equivalent to the shipped one, which reads the shell `$X` the ledger
        blanks, and a fixture that reproduced the wrong shape would prove nothing.
        """

        def subject(*, conditional: bool) -> tuple[Path, Path]:
            payload = (
                "name: Deploy Hosted API\n"
                "jobs:\n"
                "  deploy:\n"
                "    steps:\n"
                "      - name: Set all app secrets on Fly.io\n"
                "        run: |\n"
                '          ARGS=(FASTAPI_INTERNAL_KEY="${{ secrets.FASTAPI_INTERNAL_KEY }}")\n'
                '          ARGS+=(GIT_SHA="${GITHUB_SHA}")\n'
                '          [ -n "${{ secrets.OPT_SECRET }}" ] && ARGS+=(OPT_SECRET="${{ secrets.OPT_SECRET }}")\n'
            )
            extra = ""
            if conditional:
                # u0-only: assigned when the secret is ABSENT, never in a0.
                payload += '          [ -z "${{ secrets.SENTRY_DSN }}" ] && ARGS+=(GATED_FLAG="on")\n'
                extra += "GATED_FLAG             workflow\n"
            payload += f"{_LEDGER_FILTER}" + '          flyctl secrets set --stage "${ARGS[@]}"\n'
            tag = str(int(conditional))
            # `SENTRY_DSN` is declared by the shared fixture manifest, so it is only
            # declared by the fixture that actually READS it — otherwise its absence
            # from the other payload is a real stale declaration and that run would be
            # red for a reason unrelated to this test.
            base = _LEDGER_MANIFEST
            if not conditional:
                base = "".join(
                    line
                    for line in _LEDGER_MANIFEST.splitlines(keepends=True)
                    if not line.startswith("SENTRY_DSN")
                )
            return (
                _fixture(f"conditional-workflow-{tag}.yml", payload),
                _fixture(f"conditional-manifest-{tag}.txt", base + extra),
            )

        def drive(workflow: Path, manifest: Path, ledger_text: str) -> subprocess.CompletedProcess[str]:
            return _run(
                _secrets_file(["FASTAPI_INTERNAL_KEY", "GIT_SHA"], "conditional-secrets.json"),
                manifest=manifest,
                workflow=workflow,
                present={"FASTAPI_INTERNAL_KEY", "SENTRY_DSN", "OPT_SECRET"},
                extra_env={
                    "FLY_MITIGATION_LEDGER": str(
                        _ledger(ledger_text, "conditional-ledger.txt")
                    ),
                    "FLY_MITIGATION_TODAY": "2026-11-07",
                },
            )

        # (1) A `-z`-guarded declaration is stale intent in the ordinary world, and an
        # entry for an UNRELATED name must not change that verdict. The control arm is
        # what stops the second assertion being a tautology.
        wf, mf = subject(conditional=True)
        control = drive(wf, mf, "# empty\n")
        assert control.returncode == 1, control.stdout + control.stderr
        assert "STALE DECLARATION — 'GATED_FLAG'" in control.stdout, control.stdout

        unrelated = drive(wf, mf, _entry("OPT_SECRET"))
        assert "STALE DECLARATION — 'GATED_FLAG'" in unrelated.stdout, (
            "a ledger entry for an unrelated name silenced this name's STALE "
            "DECLARATION — `unmitigated` is unioning the no-secrets sample again, so a "
            "`-z` guard is being reported as 'the ledger emptied it'. " + unrelated.stdout
        )
        assert "LEDGER-EMPTIED" not in unrelated.stdout, unrelated.stdout

    def test_a_SHORT_ledgered_name_is_still_proven_suppressed(self):
        """#11332 round 13 (P3) — one name grammar, two readers, and they disagreed.

        `read_mitigation_ledger` accepts `[A-Z][A-Z0-9_]*` (ONE character or more), but
        the payload extractor demanded three. A ledger entry for a 1–2 character name
        was therefore recordable and UNPROVABLE: `assigned | unconditional | live` was
        empty for it, check (0) was vacuous, and the gate printed
        `MITIGATED … OK` over a payload that still staged the name — exit 0 where the
        identical run with a three-character name is exit 2 `LEDGER NOT HONOURED`.
        Measured, only the name's length differing.

        Reachability is low (no such name exists on the app, and Fly has no length
        floor that would stop one appearing), which is exactly why it is worth a test
        rather than a paragraph: the failure is SILENT, and the whole point of check
        (0) is that it cannot be.

        The filter is deliberately ABSENT here — this is "the sync filter was dropped",
        the regression check (0) exists to catch, for a name whose length used to hide
        it from the extractor.
        """
        workflow = _fixture(
            "short-name-workflow.yml",
            "name: Deploy Hosted API\n"
            "jobs:\n"
            "  deploy:\n"
            "    steps:\n"
            "      - name: Set all app secrets on Fly.io\n"
            "        run: |\n"
            '          ARGS=(FASTAPI_INTERNAL_KEY="${{ secrets.FASTAPI_INTERNAL_KEY }}")\n'
            '          ARGS+=(AB="${{ secrets.AB }}")\n'
            '          ARGS+=(ABC="${{ secrets.ABC }}")\n'
            '          flyctl secrets set --stage "${ARGS[@]}"\n',
        )
        manifest = _fixture(
            "short-name-manifest.txt",
            "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
            "AB                    gh-secret:AB\n"
            "ABC                   gh-secret:ABC\n",
        )

        def drive(name: str) -> subprocess.CompletedProcess[str]:
            return _run(
                _secrets_file(["FASTAPI_INTERNAL_KEY"], f"short-{name}.json"),
                manifest=manifest,
                workflow=workflow,
                present={"FASTAPI_INTERNAL_KEY", "AB", "ABC"},
                extra_env={
                    "FLY_MITIGATION_LEDGER": str(
                        _ledger(_entry(name), f"short-ledger-{name}.txt")
                    ),
                    "FLY_MITIGATION_TODAY": "2026-11-07",
                },
            )

        short = drive("AB")
        assert short.returncode == 2, (
            "a 2-character ledgered name was certified as suppressed while the payload "
            "still stages it — the extractor's name floor is wider than the ledger's, "
            "so check (0) never saw the assignment: " + short.stdout + short.stderr
        )
        assert "LEDGER NOT HONOURED" in short.stdout, short.stdout
        # …and the length is the ONLY difference: a name of the same shape that the
        # extractor always handled gets the same verdict.
        wider = drive("ABC")
        assert wider.returncode == 2, wider.stdout + wider.stderr
        assert "LEDGER NOT HONOURED" in wider.stdout, wider.stdout

    def test_a_MIXED_guard_name_the_ledger_empties_is_explained_not_refused(self):
        """#11332 round 11 (P3) — the `unmitigated` set must include THIS run's world.

        Round 10 narrowed `unmitigated` to the all-present sample to stop an unrelated
        entry silencing a `-z`-guarded declaration. That is correct for a name whose
        guard variable is PRESENT this run (it is in neither sample) — but too narrow
        for the shape `payload_partition`'s own docstring calls real: a guard NEITHER
        synthetic sample reproduces (`A present AND B absent`), which shows up only in
        the run sample. With `A` ledgered, such a name is gone from `live` BECAUSE OF
        THE LEDGER, and reporting that as a `STALE DECLARATION` hands the operator a
        remedy ("declare the managing source … or manage it from deploy-hosted.yml")
        whose only reading makes the assignment unconditional — destroying the
        fail-closed guard the composition exists for.

        So the set is `a0 | (live0 - live)`: the all-present world, plus what this run
        carried before the ledger and no longer does. Both halves are pinned here and
        in the sibling `-z` test, which must keep refusing.
        """
        workflow = _fixture(
            "mixed-guard-workflow.yml",
            "name: Deploy Hosted API\n"
            "jobs:\n"
            "  deploy:\n"
            "    steps:\n"
            "      - name: Set all app secrets on Fly.io\n"
            "        env:\n"
            "          OPT_SECRET: ${{ secrets.OPT_SECRET }}\n"
            "          SENTRY_DSN: ${{ secrets.SENTRY_DSN }}\n"
            "        run: |\n"
            # The shipped regions FIRST, in their shipped order: the environment
            # blank is what makes the composition read the deliberate absence.
            f"{_LEDGER_FILTER}"
            '          ARGS=(FASTAPI_INTERNAL_KEY="${{ secrets.FASTAPI_INTERNAL_KEY }}")\n'
            '          ARGS+=(GIT_SHA="${GITHUB_SHA}")\n'
            '          [ -n "$OPT_SECRET" ] && ARGS+=(OPT_SECRET="$OPT_SECRET")\n'
            '          [ -n "$OPT_SECRET" ] && [ -z "$SENTRY_DSN" ] && ARGS+=(MIXED_FLAG="on")\n'
            '          flyctl secrets set --stage "${ARGS[@]}"\n',
        )
        manifest = _fixture(
            "mixed-guard-manifest.txt",
            "FASTAPI_INTERNAL_KEY  gh-secret:FASTAPI_INTERNAL_KEY\n"
            "OPT_SECRET            gh-secret:OPT_SECRET\n"
            "GIT_SHA               workflow\n"
            "MIXED_FLAG            workflow\n",
        )

        def drive(ledger_text: str) -> subprocess.CompletedProcess[str]:
            return _run(
                _secrets_file(["FASTAPI_INTERNAL_KEY"], "mixed-guard-secrets.json"),
                manifest=manifest,
                workflow=workflow,
                # `SENTRY_DSN` is ABSENT on GitHub this run — that half of the guard is
                # what no synthetic sample reproduces.
                present={"FASTAPI_INTERNAL_KEY", "OPT_SECRET"},
                extra_env={
                    "FLY_MITIGATION_LEDGER": str(
                        _ledger(ledger_text, "mixed-guard-ledger.txt")
                    ),
                    "FLY_MITIGATION_TODAY": "2026-11-07",
                },
            )

        # CONTROL. With no entries the name is a stale declaration — the same rule
        # that makes a `-z`-guarded declaration stale: the `workflow` arm requires the
        # payload to assign it in the ALL-PRESENT world, and a condition that needs a
        # secret ABSENT never does. So this arm is not "the legitimate case already
        # worked"; it is what makes the arm below a statement about the LEDGER.
        control = drive("# empty\n")
        assert control.returncode == 1, control.stdout + control.stderr
        assert "STALE DECLARATION — 'MIXED_FLAG'" in control.stdout, control.stdout
        assert "LEDGER-EMPTIED" not in control.stdout, control.stdout

        emptied = drive(_entry("OPT_SECRET"))
        assert emptied.returncode == 0, (
            "a mixed-guard name the ledger genuinely empties is being REFUSED as a "
            "stale declaration — `unmitigated` is missing this run's world "
            "(`live0 - live`): " + emptied.stdout + emptied.stderr
        )
        assert "LEDGER-EMPTIED" in emptied.stdout, emptied.stdout
        assert "MIXED_FLAG" in emptied.stdout, emptied.stdout
        assert "STALE DECLARATION — 'MIXED_FLAG'" not in emptied.stdout, emptied.stdout

    def test_the_belt_is_SILENT_on_a_run_it_blocks(self):
        """The belt must not announce a CREATE on a run it then refuses.

        It sat above the `unhonourable`/`violations` returns, so a blocked run still
        printed "this deploy will CREATE N secret(s)" — a claim about a payload that
        was never staged, and a distraction from the violation the reader must act on.
        Moving it was necessary but not sufficient: every existing assertion about
        that warning is on a GREEN run, so moving it back above the returns left the
        whole suite green (#11332 review round 5, P2).
        """
        # exit 2 — a ledgered name the payload still stages.
        unhonourable = _ledger_run(
            _ledger(_entry()), workflow=LEDGER_WORKFLOW_UNFILTERED
        )
        assert unhonourable.returncode == 2, unhonourable.stdout
        assert "LEDGER NOT HONOURED" in unhonourable.stdout
        assert "will CREATE" not in unhonourable.stdout, (
            "the belt named a secret this blocked run would never stage: "
            + unhonourable.stdout
        )

        # exit 1 — an expired entry, with a createable absent name in the payload, so
        # the belt WOULD have fired had it still been above the returns.
        lapsed = _ledger_run(
            _ledger(_entry("OPT_SECRET", expires="2020-01-01")),
            present={"FASTAPI_INTERNAL_KEY", "SENTRY_DSN", "OPT_SECRET"},
        )
        assert lapsed.returncode == 1, lapsed.stdout + lapsed.stderr
        assert "MITIGATION LAPSED" in lapsed.stdout
        assert "will CREATE" not in lapsed.stdout, (
            "the belt named a secret this blocked run would never stage: "
            + lapsed.stdout
        )

    def test_the_shipped_ledger_exists_and_parses(self):
        """The tracked contract: a missing/rotted file hard-blocks every deploy."""
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import importlib.util

        spec = importlib.util.spec_from_file_location("check_fly_secret_drift_ledger", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        assert REAL_LEDGER.is_file(), (
            "#11122: .github/scripts/fly-secret-mitigations.txt is the ledger the "
            "deploy filters ARGS by and the gate reads — a missing one is exit 2"
        )
        entries = module.read_mitigation_ledger(
            REAL_LEDGER, module.read_manifest(REAL_MANIFEST)
        )
        for name in entries:
            assert name in module.read_manifest(REAL_MANIFEST), (
                f"the shipped ledger names {name}, which the manifest does not declare"
            )

    def test_a_fixture_run_never_resolves_to_the_shipped_ledger(self):
        """#11332 review round 6 — the CLASS, not the 74th call site.

        `ledger_path()` falls back to `DEFAULT_LEDGER` (the SHIPPED tracked ledger)
        when `FLY_MITIGATION_LEDGER` is unset, and the gate validates every shipped
        entry against the manifest it was handed. So a fixture manifest plus an
        UNSEAMED run is not a fixture: the first real ledger entry — the exact
        action the ledger header and runbook §8.1 instruct an operator to take
        mid-incident — exits 2 on every such call. Measured on this harness: 62
        failures, on the change whose purpose is to stop the next deploy re-arming
        the mitigation, with the cheapest green being to weaken these assertions.

        The defect was found and fixed once at one call site (the F4 comment);
        `_run_env` now names the ledger for the whole harness. This pins the class
        on the mechanism rather than on today's content: asserting a green run
        against the CURRENT (entry-free) shipped ledger proves nothing, which is
        precisely the accident being closed.
        """
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import importlib.util

        spec = importlib.util.spec_from_file_location("check_fly_secret_drift_hermetic", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        # The mechanism: with no seam, the gate reads the SHIPPED file — this is
        # what makes an unseamed fixture run non-hermetic.
        assert module.DEFAULT_LEDGER == REAL_LEDGER, (
            "the gate's fallback ledger is no longer the shipped file this test "
            "reasons about — re-derive the seam before trusting the assertions below"
        )
        original = os.environ.pop("FLY_MITIGATION_LEDGER", None)
        try:
            assert module.ledger_path() == REAL_LEDGER
        finally:
            if original is not None:
                os.environ["FLY_MITIGATION_LEDGER"] = original

        # The pin: every fixture run names a ledger, and that ledger parses to zero
        # entries whatever the shipped file holds — so no shipped entry can ever
        # change a fixture run's verdict.
        env = _run_env(_secrets_file(_ALL_DECLARED, "hermetic-ledger-secrets.json"))
        assert env["FLY_MITIGATION_LEDGER"] == str(EMPTY_LEDGER), (
            "a fixture run left FLY_MITIGATION_LEDGER unset (or pointed it at the "
            "shipped ledger): it is then validated against the shipped entries, and "
            "the first real entry reds the suite"
        )
        assert Path(env["FLY_MITIGATION_LEDGER"]).resolve() != REAL_LEDGER.resolve()
        assert module.read_mitigation_ledger(
            Path(env["FLY_MITIGATION_LEDGER"]), module.read_manifest(MANIFEST)
        ) == {}, "the harness's default ledger is not empty"

        # …and the run that env produces is green, i.e. the seam is not just named
        # but honoured by the subprocess.
        assert _run(_secrets_file(_ALL_DECLARED, "hermetic-ledger-secrets.json")).returncode == 0


# ── #13665/#14156: the DECLARED machine shape, pinned ───────────────────────
# The bug this closes is "fly.toml says 2 cores, the machine runs 4": `fly
# scale vm` is a MANUAL operation, so a versioned `cpus = 2` reverted every
# hand-resize on the next deploy, and nothing reddened for a whole measurement
# window. `fly.toml`'s shape is declarative, but "declarative" is exactly what a
# silent revert is invisible in — so the shape becomes the thing a diff must
# move deliberately.


def _real_fly_toml() -> dict:
    import tomllib

    return tomllib.loads(REAL_FLY_TOML.read_text(encoding="utf-8"))


def _services_region(text: str) -> str:
    """Everything before the `[[vm]]` TABLE (it also holds `[build]`, `[checks]`).

    Split on the table's OWN LINE, not on the bare token: the COST note mentions
    `` `[[vm]]` `` inline, so a bare-token split cuts inside that comment and
    keeps the label only by paragraph ordering (re-ordering the note would
    false-RED a correct file). Shared by the pin and its non-vacuity probe.

    SELF-CHECKING, because a failed split is not "fails toward red": if the
    `[[vm]]` line ever gains trailing content (`[[vm]]  # shape`) the pattern
    matches nothing, `re.split` returns ONE part, and this would hand back the
    WHOLE file — from which a later priced row (most plausibly the `[[vm]]`
    cost derivation) could satisfy the pin and green it while the note still
    prices a shape the block does not declare. So a missing split raises.
    """
    parts = re.split(r"(?m)^\[\[vm\]\]\s*$", text)
    assert len(parts) > 1, (
        "fly.toml has no bare `[[vm]]` line to split on: the `[[services]]` region "
        "would widen to the whole file and this pin could read a price row from "
        "below the table — re-derive the split rather than letting it widen"
    )
    return parts[0]


def _priced_shapes(services_region: str) -> set[int]:
    """Core counts the note PRICES — a PRICE ROW, not a mention.

    A shape counts only when it is written as a priced row —
    ``shared-cpu-Nx:NNNNMB =`` — whose figure (`$`) follows the ``=`` on the
    same line, or opens the next one. That is this file's COST-note shape
    (shape and ``=`` on one line, money on the next).

    Why the row form matters: counting any `shared-cpu-Nx` mention with a figure
    nearby let prose like "the block declares `shared-cpu-4x:4096MB`
    (~$26.76/mo)" enter the priced set, and the pin went GREEN while the note
    still priced 2 cores. A mention is not a price; a bare ``=`` with no figure
    behind it is not one either.
    """
    lines = services_region.splitlines()
    priced: set[int] = set()
    for i, line in enumerate(lines):
        row = re.search(r"shared-cpu-(\d+)x:\s*\d+\s*MB\s*=", line)
        if row is None:
            continue
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        priced_here = "$" in line[row.end():] or re.match(r"\s*#?\s*\$", next_line)
        if priced_here:
            priced.add(int(row.group(1)))
    return priced


def test_vm_block_declares_the_four_core_shape():
    """A revert of `cpus` to 2 — the #13665 regression — must redden this."""
    vm = _real_fly_toml()["vm"]
    assert isinstance(vm, list) and len(vm) == 1, (
        "fly.toml must declare exactly one [[vm]] block: a second process group "
        "left at the old size is the same declared-vs-live drift, one table over"
    )
    shape = vm[0]
    assert shape["cpu_kind"] == "shared", (
        "the measured 4-core shape is `shared`; `cpu_kind` is part of the "
        "declared shape, so changing it changes what a deploy provisions"
    )
    assert shape["cpus"] == 4, (
        "the box runs 4 cores (#13665, measured); a versioned 2 is the revert "
        "that made the hand-resize not survive a deploy"
    )
    assert shape["memory_mb"] == 4096, (
        "memory_mb is pinned separately: the file's history is OOM "
        "crash-loops at 2 GB (#545), so a reduction is a real regression"
    )


def test_a_cost_note_pricing_a_shape_the_vm_block_does_not_declare_says_so():
    """The F2 class (#14156 review): one cost claim, not two.

    After the 2→4 resize the `[[services]]` COST note still priced the 2-core
    machine, so a reader took a stale figure as current. Either the note prices
    the declared shape, or it says out loud that it is the pre-#13665 snapshot.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    declared_cpus = _real_fly_toml()["vm"][0]["cpus"]
    services_region = _services_region(text)

    # A PRICE, not a MENTION: `priced` comes from lines that carry a figure, so
    # the pin does not disarm the moment the note names the shape it declares
    # (e.g. writing "declares `shared-cpu-4x`").
    priced = _priced_shapes(services_region)
    assert priced, (
        "the [[services]] COST note names no `shared-cpu-Nx` shape any more, so "
        "this pin can no longer see what it prices — re-derive it against the "
        "note's new spelling rather than deleting it"
    )
    if priced != {declared_cpus}:
        assert "pre-#13665" in services_region.lower(), (
            f"the [[services]] COST note prices {sorted(priced)} cores while "
            f"[[vm]] declares {declared_cpus}, and never labels itself as the "
            "pre-#13665 historical shape — a reader acts on the stale figure "
            "(#14156 review F2)"
        )


# ── Non-vacuity, ENCODED (the pins above are hand-measured otherwise) ───────
# `20f8459795` established the pins and MEASURED that each reds on the pre-fix
# content. A measurement at authoring time is not a guard: if a later refactor
# softens either assertion, nothing reds and the pin reads green forever — the
# inert-enforcer class. These probes mutate the real file and require the pin to
# raise, so the discrimination is itself a test.
#
# EVERY assertion of `test_vm_block_declares_the_four_core_shape` is covered by
# one probe below — `cpus`, `memory_mb`, `cpu_kind`, and the single-`[[vm]]`
# cardinality — because a pin whose four claims share ONE probe is only a pin on
# that one: a later softening of the other three (`== 4096` → `in (4096, 2048)`, a
# `"shared"` → `"performance"`, a second `[[vm]]` left at the old size) would
# redden nothing and the pin would read green forever. That is the same
# inert-enforcer class the block above names, so it is closed rather than noted.
#
# Each `pytest.raises` is MATCHED against the pin's own message, not bare
# `AssertionError`: a bare catch stays green if some *other* assertion in the
# pin starts failing (e.g. `assert priced`), so the probe would then prove a
# different assertion than the one it names. The patterns below are substrings
# of the pin's failure text and of nothing the probe itself asserts — the probe's
# own staleness guards live OUTSIDE the `raises` block and fail loudly.


def test_the_shape_pin_reds_when_cpus_reverts(monkeypatch):
    """The #13665 regression, reproduced: `cpus = 4` → `cpus = 2` must redden."""
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    reverted = text.replace("  cpus = 4\n", "  cpus = 2\n", 1)
    assert reverted != text, (
        "fly.toml has no `  cpus = 4` line to revert — the probe is stale, so "
        "it is proving nothing about the pin"
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture("fly-cpus-reverted.toml", reverted),
    )
    with pytest.raises(AssertionError, match="the box runs 4 cores"):
        test_vm_block_declares_the_four_core_shape()


def test_the_shape_pin_reds_when_memory_reverts(monkeypatch):
    """`memory_mb = 4096` → `2048` must redden the pin's OWN memory message.

    A memory regression is the #545 OOM class, not a re-run of the core revert —
    so the mutant moves memory alone, and the match is the memory assertion's
    text, which the `cpus` probe's mutant cannot produce.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    reverted = text.replace("  memory_mb = 4096\n", "  memory_mb = 2048\n", 1)
    assert reverted != text, (
        "fly.toml has no `  memory_mb = 4096` line to revert — the probe is "
        "stale, so it is proving nothing about the pin"
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture("fly-memory-reverted.toml", reverted),
    )
    with pytest.raises(AssertionError, match="memory_mb is pinned separately"):
        test_vm_block_declares_the_four_core_shape()


def test_the_shape_pin_reds_when_cpu_kind_changes(monkeypatch):
    """`cpu_kind = "shared"` → `"performance"` must redden its own assertion.

    `cpu_kind` is half of what a deploy provisions and is priced differently, so
    a flip is a shape change even with `cpus` and `memory_mb` intact — the case
    the `cpus` probe cannot see.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    mutated = text.replace('  cpu_kind = "shared"\n', '  cpu_kind = "performance"\n', 1)
    assert mutated != text, (
        'fly.toml has no `  cpu_kind = "shared"` line to mutate — the probe is '
        "stale, so it is proving nothing about the pin"
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture("fly-cpu-kind-mutated.toml", mutated),
    )
    with pytest.raises(AssertionError, match="the measured 4-core shape is"):
        test_vm_block_declares_the_four_core_shape()


def test_the_shape_pin_reds_on_a_second_vm_block(monkeypatch):
    """A SECOND `[[vm]]` table must redden the cardinality assertion.

    Two declared shapes is the declared-vs-live drift one table over (#14159):
    the reader cannot tell which one a deploy applies, and the OLD size can be
    the one that wins. `cpus`/`memory_mb`/`cpu_kind` in the first table stay
    untouched, so only the cardinality assertion can fire.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    doubled = text + (
        '\n[[vm]]\n  cpu_kind = "shared"\n  cpus = 2\n  memory_mb = 4096\n'
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture("fly-two-vm-blocks.toml", doubled),
    )
    assert len(_real_fly_toml()["vm"]) == 2, (
        "the doubled fixture does not parse as two [[vm]] blocks — the probe is "
        "stale, so it is proving nothing about the cardinality assertion"
    )
    with pytest.raises(AssertionError, match="exactly one \\[\\[vm\\]\\] block"):
        test_vm_block_declares_the_four_core_shape()


def test_the_cost_note_pin_reds_when_the_historical_label_is_stripped(monkeypatch):
    """The F2 class reproduced: the COST note prices 2 cores, unlabelled.

    Only the `[[services]]` region is mutated (the `[[vm]]` table is kept
    verbatim), which is the reviewed head's own shape minus the label the fix
    added — exactly the state a reader acted on before #14156.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    services_region = _services_region(text)
    stripped = re.sub(r"(?i)pre-#13665", "historical", services_region)
    assert stripped != services_region, (
        "the `[[services]]` region carries no pre-#13665 label to strip — the "
        "probe is stale, so it is proving nothing about the pin"
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture(
            "fly-cost-note-unlabelled.toml",
            stripped + text[len(services_region):],
        ),
    )
    with pytest.raises(AssertionError, match="never labels itself"):
        test_a_cost_note_pricing_a_shape_the_vm_block_does_not_declare_says_so()


def test_the_cost_note_pin_reds_when_two_shapes_are_priced_unlabelled(monkeypatch):
    """The round-10 PREDICATE, guarded: one stale claim BESIDE one current one.

    `if priced != {declared_cpus}` is the assertion with no other mutant, and THIS
    input is the only one that sees it. Revert it to the older
    `if declared_cpus not in priced:` and the label-strip probe above still passes —
    but NOT because it sees the predicate: its priced set is `{2}`, so the old form
    (`4 not in {2}`) is TRUE, enters the label leg, and reds on the stripped label
    just as the new form does. This probe's input is the only one whose priced set
    CONTAINS the declared shape (`{2, 4}`), so there the old form skips the label
    leg entirely and the pin reads GREEN — which is the two-claims state, and the
    one #14156 review F2 is about, so it is reproduced here rather than read.
    (Corrected in the #14156 follow-up: the first version of this paragraph said
    "both probes above still pass, because the declared shape is then PRESENT in
    the priced set" — true of this input, false of the label-strip probe, which
    passes for the reason above. A fresh reviewer measured it.)

    Non-vacuity of the mutation itself is asserted first: the two-claims input
    must really parse as {2, 4}, or the probe would be proving the label leg
    again under a different name.
    """
    text = REAL_FLY_TOML.read_text(encoding="utf-8")
    services_region = _services_region(text)
    stripped = re.sub(r"(?i)pre-#13665", "historical", services_region)
    assert stripped != services_region, (
        "the `[[services]]` region carries no pre-#13665 label to strip — the "
        "probe is stale, so it is proving nothing about the pin"
    )
    two_claims = stripped + (
        "  #   shared-cpu-4x:4096MB =\n"
        "  #   $25.30/mo running (a second, current claim beside the stale one)\n"
    )
    assert _priced_shapes(two_claims) == {2, 4}, (
        "the mutation did not produce the two-claims state — `_priced_shapes` "
        "must see BOTH shapes, or this probe is not exercising the predicate"
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "REAL_FLY_TOML",
        _fixture("fly-cost-note-two-claims.toml", two_claims + text[len(services_region):]),
    )
    with pytest.raises(AssertionError, match="never labels itself"):
        test_a_cost_note_pricing_a_shape_the_vm_block_does_not_declare_says_so()
