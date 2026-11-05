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

    $ npx supabase@3.0.0 db push --dry-run --linked      # what `latest` resolves to
    {"error":{"code":"DbConfigLoadError","message":"[inbucket] in supabase/config.toml is no
      longer supported. Use [local_smtp] instead."}}                    rc=1
    $ npx supabase@2.122.1 db push --dry-run --linked    # the pin
    {"error":{"code":"AccessTokenRequiredError", ...}}                  rc=1   # config LOADED

The second arm fails only for want of the access token that CI supplies; the config gate — the
thing that was blocking every apply — is gone. So the pin, not a config rewrite, is the minimal
root-cause repair: a production deploy path must not depend on an unpinned MAJOR of a tool whose
major versions change the config schema this repo commits.

⚠️ What this does NOT say: that 3.x is bad, or that `[local_smtp]` is wrong. Migrating
`supabase/config.toml` to v3's schema is a deliberate change that needs a full local v3 run
(`supabase start`/`db reset`, the local-auth harnesses) and its own review — pinning first restores
delivery without betting production on an unvalidated config migration. This test is written so
that the two stay coupled: the day `[inbucket]` leaves the config, the major-3 arm stops being
required here automatically.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "supabase-deploy.yml"
CONFIG = ROOT / "supabase" / "config.toml"

#: A pin is an exact x.y.z. `latest`, `v3`, `3`, `3.x`, `^3.0.0` and `>=3` all float the MAJOR,
#: which is the defect: the major is what changed the config schema under this repo.
_EXACT_PIN = re.compile(r"^\d+\.\d+\.\d+$")

#: The CLI major that raises `DbConfigLoadError` on a config still carrying `[inbucket]`.
_INBUCKET_HOSTILE_MAJOR = 3


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
    assert version is not None, "the setup-cli step no longer names a version: `latest` is implied"
    return str(version).strip()


def _config_declares_inbucket(config_text: str) -> bool:
    """True when `supabase/config.toml` still carries an `[inbucket]` table."""
    return any(
        re.match(r"^\s*\[inbucket\]\s*$", line)
        for line in config_text.splitlines()
    )


def _violations(workflow_text: str, config_text: str) -> list[str]:
    """Every way the workflow floats a CLI this repo's committed config cannot load."""
    found: list[str] = []
    version = _requested_version(workflow_text)
    if not _EXACT_PIN.match(version):
        found.append(
            f"setup-cli `version: {version}` is not an exact x.y.z pin — a floating reference "
            "can change MAJOR under the deploy, which is how #10593's apply was lost"
        )
    elif _config_declares_inbucket(config_text):
        major = int(version.split(".", 1)[0])
        if major >= _INBUCKET_HOSTILE_MAJOR:
            found.append(
                f"setup-cli pins {version} (major {major}) while supabase/config.toml still "
                "declares [inbucket]; CLI major 3 raises DbConfigLoadError at config load, so "
                "`db push` exits 1 before it touches the database"
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
        pytest.skip("config.toml no longer declares [inbucket] — the v3 migration landed")
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


def test_the_coupling_disappears_once_the_config_leaves_inbucket() -> None:
    """A pin of major 3 is fine the moment [inbucket] leaves the config — the check is a coupling,
    not a permanent ban on 3.x."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    mutated_workflow = re.sub(r"(?m)^(\s*version:\s*).*$", r"\g<1>3.0.0", workflow, count=1)
    migrated_config = re.sub(r"(?m)^\[inbucket\]\s*$", "[local_smtp]", CONFIG.read_text("utf-8"))
    assert not _violations(mutated_workflow, migrated_config)
