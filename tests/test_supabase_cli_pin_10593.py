"""The production migration door must not float the Supabase CLI's MAJOR version (#10593).

Measured failure, 2026-11-05T19:45:57Z, `supabase-deploy` run `42091073635` (the dispatch that
was supposed to carry `20261102000001`/`20261102000002` to production, and with them the
`point.search_keys` fix of #10620/#10593):

    [inbucket] in supabase/config.toml is no longer supported. Use [local_smtp] instead.
    Try rerunning the command with --debug to troubleshoot the error.
    ##[error]Process completed with exit code 1.

`supabase/setup-cli` was asked for `version: latest`, which resolved to **3.0.0** — a MAJOR bump —
and v3 refuses to load this repo's `supabase/config.toml` because it still declares `[inbucket]`.
The CLI therefore exits 1 at CONFIG LOAD, before `db push` contacts the database at all, so **no
migration can be applied to production by any means the workflow offers**. That is why
`deploy-hosted`'s drift gate has been red since 2026-11-03T02:53:49Z naming exactly those two
migrations: the remedy the gate prescribes (`gh workflow run supabase-deploy.yml --ref main`)
could never succeed.

Reproduced here at the time of writing, offline, against a throwaway project link — so this is a
measurement, not a reading of the log:

    $ npx supabase@3.0.0 status                         # what `latest` resolves to
    {"_tag":"Error","error":{"code":"StatusConfigLoadError","message":"[inbucket] in
      supabase/config.toml is no longer supported. Use [local_smtp] instead."}}   rc=1
    $ npx supabase@2.122.1 status                       # the pin
    WARN: config section [inbucket] is deprecated and will be removed in v3, use [local_smtp]
    {"_tag":"Error","error":{"code":"StatusDbInspectError", ...}}            rc=1  # config LOADED

⚠️ `status` is the probe above on purpose: `db push --dry-run --linked` does NOT reach config load
at all — it fails first with `ProjectRefNotLinkedError` on BOTH the old and the new config, so it
cannot discriminate and was the wrong command to cite here.

The second arm fails only for want of the access token that CI supplies; the config gate — the
thing that was blocking every apply — is gone. So the pin, not a config rewrite, is the minimal
root-cause repair: a production deploy path must not depend on an unpinned MAJOR of a tool whose
major versions change the config schema this repo commits.

⚠️ What this does NOT say: that 3.x is bad, or that `[local_smtp]` is wrong. Migrating
`supabase/config.toml` to v3's schema is a deliberate change that needs a full local v3 run
(`supabase start`/`db reset`, the local-auth harnesses) and its own review — pinning first restores
delivery without betting production on an unvalidated config migration. This test is written so
that the two stay coupled — but the coupling is on **v3 READINESS**, not on the section name alone:
v3 also consent-gates every hosted mutation and fails unattended without `--yes`, so a major-3 pin
that merely loads the config would still apply nothing. Hence two arms below: the config-load arm
retires when `[inbucket]` leaves the config, and the consent arm keeps refusing a major-3 pin until
every mutating command carries `--yes`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "supabase-deploy.yml"
CONFIG = ROOT / "supabase" / "config.toml"

#: A pin is an exact x.y.z. `latest`, `v2`, `2`, `2.x` all float the MAJOR, which is the defect;
#: a major-bounded range (`^2.122.1`, `~2.122`) cannot cross the major, but it still takes PATCH
#: upgrades that no diff records — and this workflow is the only door to production migrations.
#: (`v2.stable` was considered for exactly that reason and rejected: it is the same unrecorded-
#: patch-uptake trade, on a line this repo does not own. If a later lane prefers it, change this
#: regex deliberately rather than discovering it as a red in the deploy pin.)
_EXACT_PIN = re.compile(r"^\d+\.\d+\.\d+$")

#: The CLI major that raises `DbConfigLoadError` on a config still carrying `[inbucket]`.
_INBUCKET_HOSTILE_MAJOR = 3

#: The CLI major that CONSENT-GATES every hosted mutation. Same blast radius as the config-load
#: break, different string: a non-TTY run fails with `HostedMutationConsentRequiredError` unless
#: `--yes` is passed, so the apply exits non-zero having applied nothing and the drift gate stays
#: red. A major-3 pin that loads the config is therefore NOT yet safe to merge on.
_HOSTED_MUTATION_CONSENT_MAJOR = 3

#: A command this workflow runs that writes to the hosted project (a migration apply or an
#: edge-function deploy). Both are on v3's consent table.
_MUTATING_COMMAND = re.compile(r"\bsupabase\s+(?:db\s+push|functions\s+deploy)\b")


def _setup_cli_step(workflow_text: str) -> dict:
    """The `supabase/setup-cli` step of the migration job, as a dict."""
    doc = yaml.safe_load(workflow_text)
    assert isinstance(doc, dict), "workflow did not parse as a mapping"
    steps = [
        step
        for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if str(step.get("uses", "")).startswith("supabase/setup-cli")
    ]
    assert steps, "no `supabase/setup-cli` step found — the migration door changed shape"
    assert len(steps) == 1, f"expected exactly one setup-cli step, found {len(steps)}"
    return steps[0]


def _requested_version(workflow_text: str) -> str:
    step = _setup_cli_step(workflow_text)
    version = (step.get("with") or {}).get("version")
    assert version is not None, (
        "the setup-cli step no longer names a version — the action then falls back to its own "
        "default (the root lockfile's version, else `latest`), which is the float this pin exists "
        "to remove"
    )
    return str(version).strip()


def _config_declares_inbucket(config_text: str) -> bool:
    """True when `supabase/config.toml` still carries an `[inbucket]` table.

    The trailing-comment arm is load-bearing: TOML reads `[inbucket]   # local email` as the same
    table, and so does the CLI — a detector that missed it would green-light a major-3 pin while
    the deploy still dies at config load, which is the false PASS this file exists to prevent.
    """
    return any(
        re.match(r"^\s*\[inbucket\]\s*(?:#.*)?$", line)
        for line in config_text.splitlines()
    )


def _logical_lines(workflow_text: str) -> list[str]:
    """Workflow lines with full-line comments dropped and `\\` continuations joined.

    Comments must go: this workflow explains itself in prose that QUOTES `supabase db push`, and a
    scanner that read those lines would demand `--yes` inside a comment. Continuations must be
    joined: the `functions deploy` commands put some of their flags on the wrapped lines, so a
    per-physical-line test would report a false missing `--yes`.
    """
    joined: list[str] = []
    buffered = ""
    for raw in workflow_text.splitlines():
        line = raw.strip()
        if not buffered and line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffered += line[:-1] + " "
            continue
        joined.append(buffered + line)
        buffered = ""
    if buffered:
        joined.append(buffered)
    return joined


def _consent_missing(workflow_text: str) -> list[str]:
    """Every hosted-mutation command on the co-move path that does not pass `--yes`."""
    return [
        line
        for line in _logical_lines(workflow_text)
        if _MUTATING_COMMAND.search(line) and "--yes" not in line
    ]


def _violations(workflow_text: str, config_text: str) -> list[str]:
    """Every way the workflow floats a CLI this repo's committed config cannot load."""
    found: list[str] = []
    version = _requested_version(workflow_text)
    if not _EXACT_PIN.match(version):
        found.append(
            f"setup-cli `version: {version}` is not an exact x.y.z pin — a reference that floats "
            "the MAJOR can change the config schema under the deploy, and one that floats only "
            "the PATCH takes upgrades no diff records; this is how #10593's apply was lost"
        )
        return found
    major = int(version.split(".", 1)[0])
    if major >= _INBUCKET_HOSTILE_MAJOR and _config_declares_inbucket(config_text):
        found.append(
            f"setup-cli pins {version} (major {major}) while supabase/config.toml still "
            "declares [inbucket]; CLI major 3 raises DbConfigLoadError at config load, so "
            "`db push` exits 1 before it touches the database"
        )
    if major >= _HOSTED_MUTATION_CONSENT_MAJOR:
        missing = _consent_missing(workflow_text)
        if missing:
            found.append(
                f"setup-cli pins {version} (major {major}), which consent-gates every hosted "
                "mutation: a non-TTY run fails with HostedMutationConsentRequiredError unless "
                "`--yes` is passed, so the apply exits non-zero having applied nothing and the "
                "drift gate stays red. Mutating command(s) with no `--yes`: "
                + "; ".join(line.strip()[:90] for line in missing)
            )
    return found


# ── the live tree: green-on-landing proof ──────────────────────────────────────


def test_the_live_workflow_pins_a_cli_this_repos_config_can_load() -> None:
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing"
    problems = _violations(
        WORKFLOW.read_text(encoding="utf-8"),
        CONFIG.read_text(encoding="utf-8"),
    )
    assert not problems, "\n".join(problems)


# ── mutation proofs: the check must go RED on each half of the defect ──────────


def test_a_floating_version_is_refused() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    # The exact pre-fix line, not a paraphrase: this is what the deploy ran on.
    mutated = re.sub(
        r"(?m)^(\s*version:\s*).*$",
        r"\1latest",
        workflow,
        count=1,
    )
    assert "version: latest" in mutated, "mutation did not apply — this test would be vacuous"
    problems = _violations(mutated, config)
    assert problems, "a floating `version: latest` passed the check"


@pytest.mark.parametrize("floating", ["latest", "v2", "2", "2.x", "^2.122.1", "~2.122"])
def test_every_floating_shape_is_refused(floating: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated = re.sub(r"(?m)^(\s*version:\s*).*$", rf"\g<1>{floating}", workflow, count=1)
    assert _violations(mutated, CONFIG.read_text(encoding="utf-8")), (
        f"`version: {floating}` passed the check"
    )


def test_a_major_the_committed_config_cannot_load_is_refused() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    if not _config_declares_inbucket(config):
        pytest.skip(
            "config.toml no longer declares [inbucket] — the CONFIG-LOAD half of the coupling is "
            "retired; the consent half still guards the major-3 pin (#10896, PR #11104)"
        )
    mutated = re.sub(
        r"(?m)^(\s*version:\s*).*$",
        r"\g<1>3.0.0",
        workflow,
        count=1,
    )
    assert "version: 3.0.0" in mutated, "mutation did not apply — this test would be vacuous"
    problems = _violations(mutated, config)
    assert problems, "pinning major 3 against an [inbucket] config passed the check"
    assert "DbConfigLoadError" in problems[0], problems[0]


def test_a_major_three_pin_requires_consent_on_every_mutating_command() -> None:
    """The second v3 break, and the reason the config rename must not relax the guard.

    A major-3 pin that LOADS the config is not safe on its own: v3 consent-gates every hosted
    mutation and cannot prompt in CI, so `db push` / `functions deploy` exit non-zero having
    applied nothing unless `--yes` is passed. Measured against the tree before this arm existed:
    with the config migrated and the pin mutated to 3.0.0, the suite reported `9 passed, 1 skipped`
    — a false PASS over an applier that would apply zero migrations.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated, "mutation did not apply — this test would be vacuous"
    # The migrated config: the config-load half no longer fires, so only the consent arm can.
    migrated_config = re.sub(r"(?m)^\[inbucket\]\s*$", "[local_smtp]", CONFIG.read_text("utf-8"))
    problems = _violations(mutated, migrated_config)
    assert problems, (
        "a major-3 pin passed with no `--yes` on the mutating commands — v3 would apply nothing "
        "behind HostedMutationConsentRequiredError"
    )
    assert "HostedMutationConsentRequiredError" in problems[0], problems[0]
    assert _consent_missing(mutated), "the tree already carries --yes; this arm proves nothing"


def test_the_coupling_disappears_once_the_config_leaves_inbucket() -> None:
    """A pin of major 3 is fine once BOTH halves hold: [inbucket] has left the config AND every
    mutating command passes `--yes`. The check is a v3-readiness coupling, not a permanent ban."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    migrated_config = re.sub(r"(?m)^\[inbucket\]\s*$", "[local_smtp]", CONFIG.read_text("utf-8"))
    # Add `--yes` to every logical mutating command, then pin major 3. Both edits assert their own
    # mutation: a substitution that silently missed would leave the assertions below proving
    # nothing about the coupling.
    consented: list[str] = []
    for line in workflow.splitlines():
        if _MUTATING_COMMAND.search(line) and "--yes" not in line:
            line = line.rstrip("\\").rstrip() + " --yes" + (" \\" if line.rstrip().endswith("\\") else "")
        consented.append(line)
    consented_workflow = "\n".join(consented) + "\n"
    assert not _consent_missing(consented_workflow), "the --yes mutation did not apply"
    mutated_workflow = re.sub(
        r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", consented_workflow, count=1
    )
    assert "version: 3.0.0" in mutated_workflow, "mutation did not apply — this test would be vacuous"
    assert not _violations(mutated_workflow, migrated_config)
