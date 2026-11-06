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
that the two stay coupled — but the coupling is on the **MAJOR**, not on the section name: v3 also
consent-gates every hosted mutation and fails unattended without `--yes`, so a major-3 pin that
merely loads the config would still apply nothing. Retiring the `[inbucket]` refusal therefore must
not retire the guard: the ceiling below keeps refusing major 3 until the workflow is v3-ready, and
the PR that makes it so lifts the ceiling deliberately.
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

#: The CLI major that changed BOTH halves of this contract at once: it raises `DbConfigLoadError`
#: on a config still carrying `[inbucket]` (line above), and it consent-gates every hosted mutation,
#: failing a non-TTY run with `HostedMutationConsentRequiredError` unless `--yes` is passed. One
#: major, two breaks — which is why the guard below is a MAJOR-ceiling, not a check on either symptom.
_V3_MAJOR = 3


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
    """The CLI version the workflow pins, and proof that CI resolves the same value.

    ⛔ The reader has its own bypass surface, separate from the comparison (found in review): PyYAML
    implements the YAML MERGE KEY (`<<: {version: 2.122.1}`), which GitHub Actions does NOT — so a
    step could carry no `version:` line at all, PyYAML would synthesize one, and the
    `version is None` arm below would never fire while the action fell back to `latest` (3.x). The
    same class covers a duplicate `version:` key, where PyYAML takes the last one. So the resolved
    value must also be present as a LITERAL `version:` line, and merge keys are refused outright.
    """
    step = _setup_cli_step(workflow_text)
    with_ = step.get("with") or {}
    assert "<<" not in with_, (
        "the setup-cli step's `with:` uses a YAML merge key (`<<`). GitHub Actions does not implement "
        "merge keys, so the `version` PyYAML reads here is not the version CI resolves — the action "
        "would see no `version` input and fall back to its own default (`latest`), which is exactly "
        "the float this pin exists to remove"
    )
    version = with_.get("version")
    assert version is not None, (
        "the setup-cli step no longer names a version — the action then falls back to its own "
        "default (the root lockfile's version, else `latest`), which is the float this pin exists "
        "to remove"
    )
    resolved = str(version).strip()
    assert re.search(
        rf"(?m)^\s*version:\s*['\"]?{re.escape(resolved)}['\"]?\s*(?:#.*)?$", workflow_text
    ), (
        f"the pin `{resolved}` was not read from a literal `version:` line — PyYAML resolved it "
        "from a construct (merge key, duplicate key, alias) that GitHub reads differently, so the "
        "value asserted here is not necessarily the CLI CI installs"
    )
    return resolved


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


def _violations(workflow_text: str, config_text: str) -> list[str]:
    r"""Every way the workflow PINS a CLI this repo cannot safely run in production.

    DECLARED THREAT SURFACE — in scope, each covered by an arm below:
      (1) a floating pin (`latest`, `v2`, `^2.122.1`, …) — any shape that is not an exact x.y.z;
      (2) a pin this reader resolves differently from CI (merge key, duplicate key, alias), so the
          value asserted is not the value installed;
      (3) a major-3 pin — the config-load break while `[inbucket]` is declared, and the hosted-
          mutation consent gate once it is not.

    DECLARED OUT OF SCOPE (not silently assumed covered): a `run:` step that installs or invokes a
    v3 CLI by itself (`npx supabase@3.0.0 …`, `npm i -g supabase@…`). That is a code change, and it
    is reviewed as code; the mutating-command argument checks are open PR #11104's own suite. A
    reader of this file must not take its title as a claim about that surface.

    ⛔ The ceiling is deliberately a MAJOR comparison rather than a scan of the workflow's arguments.
    An earlier draft decided v3-readiness by looking for `--yes` in the mutating commands, and review
    defeated it five ways — `--yes` named in a trailing comment, `--yes=false`, a `--yes` belonging
    to another command, one commented out after a `\` continuation, and the global-flag form
    `supabase --debug db push` (valid in the CLI, and its own error text recommends `--debug`). An
    integer comparison has no such surface: the major either moves or it does not.

    The ceiling is lifted by the PR that makes the workflow v3-ready, not by a symptom moving.
    """
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
    if major < _V3_MAJOR:
        return found
    if _config_declares_inbucket(config_text):
        found.append(
            f"setup-cli pins {version} (major {major}) while supabase/config.toml still "
            "declares [inbucket]; CLI major 3 raises DbConfigLoadError at config load, so "
            "`db push` exits 1 before it touches the database"
        )
    else:
        found.append(
            f"setup-cli pins {version} (major {major}), but this workflow is not v3-ready: major 3 "
            "consent-gates every hosted mutation and cannot prompt in CI, so `db push` and "
            "`functions deploy` exit non-zero having applied nothing unless `--yes` is passed "
            "(HostedMutationConsentRequiredError). The config half is migrated (#10896); the "
            "consent half is open PR #11104, which owns those assertions in its own suite. Move "
            "the pin in the PR that lands it and lift this arm there deliberately — a red here is "
            "the coupling working, not a bug to relax."
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
            "retired; the major-3 ceiling still guards the pin (#10896, PR #11104)"
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


def test_a_pin_this_reader_cannot_see_literally_is_refused() -> None:
    """A pin must be a LITERAL `version:` line, not a YAML construct PyYAML resolves for it.

    PyYAML implements the merge key (`<<:`); GitHub Actions does not. Measured by review: a step
    whose `with:` is `<<: {version: 2.122.1}` carries no `version:` line anywhere in the file, yet
    PyYAML synthesizes `{'version': '2.122.1'}` — so the `version is None` arm never fired and the
    action would fall back to `latest` (3.x), a fail-open identical to #10593.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    merged = re.sub(
        r"(?m)^(\s*)version:\s*\S+\s*$",
        r"\1<<: {version: 2.122.1}",
        workflow,
        count=1,
    )
    assert "<<: {version:" in merged, "mutation did not apply — this test would be vacuous"
    assert not re.search(r"(?m)^\s*version:\s*2\.122\.1\s*$", merged), (
        "the mutation left a literal `version:` line behind — the arm would not test the reader"
    )
    with pytest.raises(AssertionError, match="merge key"):
        _requested_version(merged)


def test_a_major_three_pin_is_refused_even_after_the_config_is_migrated() -> None:
    """This rename retires the config-load arm — the major-3 ceiling must survive it.

    Measured before this arm existed: with the config migrated, mutating ONLY the pin to `3.0.0`
    made this suite report `9 passed, 1 skipped` — green — over a workflow that passes no `--yes`,
    i.e. over an applier that v3 consent-gates into applying zero migrations. That is the same
    blast radius as #10593 through a different string, so the ceiling is keyed on the MAJOR.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated, "mutation did not apply — this test would be vacuous"
    migrated_config = re.sub(r"(?m)^\[inbucket\]\s*$", "[local_smtp]", CONFIG.read_text("utf-8"))
    assert not _config_declares_inbucket(migrated_config), "the config mutation did not apply"
    problems = _violations(mutated, migrated_config)
    assert problems, (
        "a major-3 pin passed against a migrated config — the v3 trap is unguarded, and a major "
        "bump would apply nothing behind HostedMutationConsentRequiredError"
    )
    assert "HostedMutationConsentRequiredError" in problems[0], problems[0]


def test_the_coupling_moves_from_the_section_to_the_major() -> None:
    """The coupling MOVES when `[inbucket]` leaves the config; it does not disappear.

    Two distinct refusals are asserted, so the guard cannot degrade into one blanket message that
    would be right for the wrong reason: an `[inbucket]` config fails on its SECTION (`DbConfigLoadError`),
    a migrated config fails on the MAJOR (`HostedMutationConsentRequiredError`). A v2 pin is
    accepted against either — the boundary is the major, not the section name.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    live_config = CONFIG.read_text(encoding="utf-8")
    inbucket_config = re.sub(r"(?m)^\[local_smtp\]\s*$", "[inbucket]", live_config)
    assert _config_declares_inbucket(inbucket_config), "the config mutation did not apply"
    mutated_workflow = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    assert "version: 3.0.0" in mutated_workflow, "mutation did not apply — this test would be vacuous"

    from_section = _violations(mutated_workflow, inbucket_config)
    assert from_section and "DbConfigLoadError" in from_section[0], from_section

    from_major = _violations(mutated_workflow, live_config)
    assert from_major and "HostedMutationConsentRequiredError" in from_major[0], from_major

    # The live tree is green: a v2 pin against the migrated config.
    assert not _violations(workflow, live_config)
