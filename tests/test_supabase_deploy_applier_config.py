"""The only automated path that applies PRODUCTION DDL must not break silently (#10896).

MEASURED FAILURE this test exists to prevent
--------------------------------------------
`supabase-deploy.yml` → job `deploy` → step `Apply migrations` (the sole automated
`supabase db push` against production) exited **1 after 0.5 s** on run `42091073635`
(2026-11-05T19:45:57Z), having applied nothing:

    Target: project premise-labs (ybetwichurajbfswfeqa) · production
    [inbucket] in supabase/config.toml is no longer supported. Use [local_smtp] instead.
    ##[error]Process completed with exit code 1.

Two independent couplings made that possible, and neither is visible in review because
neither file changed:

1. `supabase/config.toml` carried the `[inbucket]` section, which the Supabase CLI **v3.0.0**
   rejects outright (a config parse, BEFORE any migration is read).
2. The workflow installed that binary as `supabase/setup-cli@v1  version: latest`, so an
   upstream major release selected itself.

The blast radius is why this is a test and not a comment: with the applier dead, the two
migrations sat unapplied, `deploy-hosted`'s fail-closed drift gate refused every deploy for
18+ runs, and the record dropped every point carrying search keys — while `/health` read `ok`.
A broken applier reads as "the deploy is blocked", which is the opposite diagnosis.

These assertions are static (file reads, no network, no DB) on purpose: they are the part the
lane can prove without a project credential.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "supabase-deploy.yml"
CONFIG = REPO_ROOT / "supabase" / "config.toml"


def _config_doc() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_config_tolerates_the_removed_experimental_orioledb_key() -> None:
    """A SECOND, same-class landmine in the same file (measured, not assumed).

    CLI v3.0.0 tolerates `[experimental] orioledb_version` only while it is EMPTY; any value
    fails config load with `experimental.orioledb_version in supabase/config.toml is no longer
    supported. Use db.orioledb_version instead.` — the identical load-error shape that killed
    `db push`. Setting that key is a plausible future edit ("turn OrioleDB on"), so guard it.
    """
    experimental = _config_doc().get("experimental") or {}
    assert not experimental.get("orioledb_version"), (
        "[experimental] orioledb_version must stay empty or be removed: Supabase CLI >= 3.0.0 "
        "rejects any value there and fails config load — the same break that stopped the "
        "production applier (#10896). The v3 location is [db] orioledb_version."
    )


def test_config_has_no_section_the_cli_rejects() -> None:
    """`[inbucket]` is a hard CLI error from v3.0.0 — it aborts `db push` before any migration."""
    doc = _config_doc()  # a parse failure here is itself a defect: the CLI would reject it too
    assert "inbucket" not in doc, (
        "supabase/config.toml declares [inbucket], which Supabase CLI >= 3.0.0 rejects with "
        "exit 1 — `supabase db push` then applies NOTHING (#10896)."
    )
    assert "local_smtp" in doc, (
        "the local mail catcher section must be spelled [local_smtp] for CLI >= 3.0.0 (#10896)"
    )
    # v3 scans every `[remotes.<name>.*]` scope for the removed name too, so the nested form is
    # the same break — and a top-level-only check would miss it.
    for name, remote in (doc.get("remotes") or {}).items():
        assert "inbucket" not in remote, (
            f"[remotes.{name}.inbucket] is rejected by Supabase CLI >= 3.0.0 (#10896) — rename it "
            f"to [remotes.{name}.local_smtp]"
        )


def test_local_smtp_keeps_the_mail_catcher_settings() -> None:
    """The rename must not drop the settings the local dev workflow reads (#801/#863)."""
    # Deliberately asserts on the PARSED doc, never on the raw text: what this test guards is
    # *settings*, and TOML keys are literal, so `doc["local_smtp"]` proves the same thing a
    # substring search would — without a second read of a file `_config_doc()` already read
    # (a redundant re-read is F841, not an assertion: ruff check . is a required check, #11104).
    doc = _config_doc()
    assert "local_smtp" in doc, "[local_smtp] section not found (#10896)"
    section = doc["local_smtp"]
    assert section.get("enabled") is True, (
        "the local SMTP section must keep `enabled = true` — local dev relies on the mail "
        "catcher for the signup-confirmation branch (#801)"
    )
    assert section.get("port") == 54324, (
        "the local SMTP UI port; the CLI default is 54324 and local-dev docs point at it"
    )


def _applier_steps() -> list[dict]:
    """Every workflow step that installs or invokes the Supabase CLI (parsed, not grepped)."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict] = []
    for job in (doc.get("jobs") or {}).values():
        steps.extend(job.get("steps") or [])
    return steps


def _applier_commands() -> list[str]:
    """The Supabase CLI commands the workflow actually RUNS, one string per invocation.

    Selected by *invocation*, not by mention: a `run:` body's command lines are the ones whose
    stripped text starts with `supabase ` (shell line-continuations are joined first). A comment
    or a `echo "… db push …"` fallback note is documentation — the same class that false-reds a
    correct file when a matcher is broad enough to read prose as a command, which is why the seam
    is the command position rather than a substring anywhere in the body.
    """
    commands: list[str] = []
    for step in _applier_steps():
        body = str(step.get("run") or "").replace("\\\n", " ")
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("supabase "):
                commands.append(stripped)
    return commands


def test_the_production_applier_is_version_pinned() -> None:
    """`version: latest` let an upstream major release disable prod migration with no repo change."""
    installers = [s for s in _applier_steps() if "supabase/setup-cli" in str(s.get("uses", ""))]
    assert installers, "supabase/setup-cli must install the CLI that applies production DDL"
    for step in installers:
        pin = str((step.get("with") or {}).get("version", "")).strip("'\"")
        assert pin, "supabase/setup-cli must be configured with an explicit `version:`"
        assert pin != "latest", (
            "the production DDL applier must not be pinned to `latest`: the v2 -> v3.0.0 release "
            "changed the config schema AND made hosted mutations consent-gated, either of which "
            "makes `db push` exit 1 before applying anything (#10896). Bump the pin deliberately."
        )
        assert re.match(r"^v?\d+\.\d+\.\d+$", pin), (
            f"expected an exact release version for the DDL applier, got {pin!r} (#10896)"
        )
        # An exact pin is not enough: the commands and the config this same module pins are v3
        # requirements. v2 rejects both (`--yes` → unknown flag; `[db.migrations].enabled` /
        # `[storage].vector` → invalid keys) and v4 removes `--linked`, which every command here
        # passes. Asserting only `x.y.z` greens a downgrade to a CLI that cannot run the applier —
        # a false PASS on the axis this test exists to guard, so the MAJOR is pinned too.
        major = int(re.match(r"^v?(\d+)", pin).group(1))
        assert major == 3, (
            f"the applier's CLI pin must stay on major 3, got {pin!r}: v2 rejects `--yes` and the "
            f"v3 config keys this workflow and config.toml now require, and v4 removes `--linked`, "
            f"which every command below passes. A major bump is a deliberate change that must "
            f"update these guards with it (#10896)"
        )


def test_the_apply_and_function_steps_pass_yes() -> None:
    """Guard the remedy itself: the mutating commands must still be able to run unattended.

    v3.0.0 consent-gates every hosted mutation and CANNOT prompt in CI, so a missing `--yes`
    turns the applier into a no-op with a different error string — the exact failure class this
    module exists to catch. An earlier fix added the pin and the schema rename but omitted
    `--yes`, so this asserts the flags on the commands the workflow actually RUNS (selected by
    command position, not by mention: see `_applier_commands`).
    """
    commands = _applier_commands()
    push = [c for c in commands if c.startswith("supabase db push")]
    assert push, "the apply step must still run `supabase db push` (#10896)"
    for cmd in push:
        assert "--include-all" in cmd, "the apply step must keep `--include-all` (#10896)"
        assert "--yes" in cmd, (
            "`supabase db push` on a hosted target is consent-gated in CLI v3 and cannot prompt "
            "in CI: without `--yes` it applies ZERO migrations (#10896)"
        )
    fn_deploys = [c for c in commands if c.startswith("supabase functions deploy")]
    assert fn_deploys, "the edge-function deploy steps must still exist (#3627 co-move)"
    for cmd in fn_deploys:
        assert "--yes" in cmd, (
            "`supabase functions deploy` is consent-gated in CLI v3; without `--yes` the schema "
            "applies and the app half never flips — the mismatched pair #3627 exists to prevent"
        )


def test_printed_remediations_pass_yes_too() -> None:
    """A remedy the repo PRINTS is code: it must run unattended, the same way the workflow does.

    Review cycles found this half of the defect twice — the first commit fixed the command the
    workflow RUNS and left `migration repair` in the drift gate's own remediation without
    `--yes`, so the option it offered an operator failed exactly like the bug being closed.
    Both are consent-gated hosted mutations in CLI v3, and no existing test pins them (the
    drift-gate suite asserts substrings that pass either way).

    Asserted PER OCCURRENCE, not as a whole-file substring: a substring assertion is satisfied by
    any one site, so reverting `--yes` at one of several printed remedies passed the whole-file
    form — a false PASS in the guard whose doctrine is that a printed remedy must be runnable.
    The window is the token immediately after the remedy (`--status applied <version>`), which
    prose cannot accidentally enter and a line reflow cannot split.
    """
    governed = {
        ".github/scripts/check-migration-drift": (REPO_ROOT / ".github" / "scripts" / "check-migration-drift"),
        ".github/scripts/check-migration-append-only": (
            REPO_ROOT / ".github" / "scripts" / "check-migration-append-only"
        ),
    }
    for name, path in governed.items():
        text = path.read_text(encoding="utf-8")
        sites = re.findall(r"--status applied <version>(\s+--yes)?", text)
        assert sites, (
            f"{name} must still print a `migration repair --status applied <version>` remedy: an "
            f"operator shown OUT OF ORDER versions needs a command to run (#1095). A silent "
            f"disappearance of the remedy must not pass vacuously."
        )
        missing = sites.count("")
        assert missing == 0, (
            f"{name}: {missing} of {len(sites)} printed `--status applied <version>` remedies still "
            f"lack `--yes` — CLI v3 consent-gates `migration repair` and cannot prompt, so the "
            f"copy-paste remedy an operator takes from the gate output repairs nothing and says "
            f"nothing (#10896, #2240)"
        )
    # The printed manual fallback for the apply itself (drift gate, remediation (a)).
    drift = governed[".github/scripts/check-migration-drift"].read_text(encoding="utf-8")
    assert "supabase db push --linked --include-all --yes" in drift, (
        "the drift gate's printed manual apply must carry `--yes` (#10896)"
    )


def test_the_drift_banner_remedy_passes_yes_too() -> None:
    """The THIRD site of the same sweep: the `::warning::` banner an operator copies (#10896).

    Cycle 5 left this as its one residual, non-blocking: `test_printed_remediations_pass_yes_too`
    pins the drift GATE's printed remedy, but the `check-drift` job's own `::warning::` — the
    string that actually reaches the Actions summary — offered `migration repair --linked --status
    applied <version>` with no `--yes`. The consequence is the same failure class the PR closes,
    one level out: the copy-paste remedy dies on the v3 consent gate, so an operator resolving
    OUT OF ORDER versions repairs nothing and cannot tell why.

    Asserted on the PARSED workflow (this file's convention), and only on banner messages that
    print the consent-gated remedy, so a banner carrying prose alone cannot false-red.
    """
    # One message per `::warning::` rather than one per PHYSICAL LINE, and the selector names the
    # command root, not just its flag: both narrow a false-red found in review. A cosmetic shell
    # line-continuation reflow is semantically identical (the shell deletes `\` + newline), but it
    # would separate `--yes` from the remedy on the next physical line and red a CORRECT banner;
    # and a future banner that merely EXPLAINS the repair in prose would otherwise be selected as
    # if it were one. Reuses `_applier_steps()` so there is one YAML traversal to keep in sync.
    banners = [
        "::warning::" + part
        for step in _applier_steps()
        for part in str(step.get("run") or "").split("::warning::")[1:]
    ]
    remedies = [
        banner
        for banner in banners
        if "migration repair" in banner and "--status applied" in banner
    ]
    assert remedies, (
        "the drift banner must still name the `migration repair --status applied` remedy: an "
        "operator shown OUT OF ORDER versions needs a command to run (#1095)"
    )
    for banner in remedies:
        assert "--yes" in banner, (
            "the drift banner's printed remedy must carry `--yes`: `migration repair` against a "
            "hosted project is consent-gated in CLI v3 (docs → 'Migrating to Supabase CLI v3') "
            "and fails unattended without it, so the operator's copy-paste repairs nothing and "
            "says nothing (#10896)"
        )
