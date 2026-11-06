"""The only automated path that applies PRODUCTION DDL must not break silently (#10896).

MEASURED FAILURE this test exists to prevent
--------------------------------------------
`supabase-deploy.yml` → job `deploy` → step `Apply migrations` (the sole automated
`supabase db push` against production) exited **1 within ~1 s** on run `42091073635`
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

#: The CLI MAJOR this workflow deliberately pins. A bump is a deliberate change (#10593 / #11100):
#: it must move this constant in the same commit, so the decision is visible in the diff instead of
#: arriving as a routine upstream release. Both v2 and v3 are MEASURED to load the committed config
#: (and both accept `--yes`) — see the argument at `test_the_production_applier_is_version_pinned`.
_PINNED_CLI_MAJOR = 2
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


def _workflow_doc() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "workflow did not parse as a mapping"
    return doc


def _all_steps() -> list[dict]:
    """Every step in every job (parsed, not grepped)."""
    return [
        step
        for job in (_workflow_doc().get("jobs") or {}).values()
        for step in (job.get("steps") or [])
    ]


def _applier_jobs() -> list[tuple[str, dict]]:
    """The job(s) that INSTALL the CLI — the only door that applies production DDL."""
    return [
        (name, job)
        for name, job in (_workflow_doc().get("jobs") or {}).items()
        if any(
            "supabase/setup-cli" in str(step.get("uses", ""))
            for step in (job.get("steps") or [])
        )
    ]


def _applier_steps() -> list[dict]:
    """The steps of the APPLIER JOB (the one that installs `supabase/setup-cli`).

    Scoped to that job deliberately. Walking EVERY job made an ordinary local-stack smoke step
    (`supabase db push --local --yes` added to a new job) a false RED against the remote-target
    assertion below — but that assertion is about the PRODUCTION apply, and a second job doing local
    work is not it. Reddening a correct file is a defect in a guard: measured, review cycle 6.
    """
    jobs = _applier_jobs()
    assert len(jobs) == 1, (
        f"expected exactly one job installing supabase/setup-cli, found {[n for n, _ in jobs]} — "
        f"the applier's guards would otherwise silently apply to the wrong job(s)"
    )
    return list(jobs[0][1].get("steps") or [])


#: The command roots that mutate the HOSTED project.
_MUTATING_ROOTS = ("supabase db push", "supabase functions deploy", "supabase migration repair")

#: Ways a shell `run:` body can turn a failing apply into a green one. `continue-on-error` is the
#: YAML form; these are the shell forms, and a guard asserting only the YAML form is defeatable by
#: two tokens — measured GREEN before this was added (review cycle 6).
_FAILURE_SWALLOWERS = (
    re.compile(r"\|\|\s*(?:true|:)\b"),
    re.compile(r"(?m)^\s*set\s+\+e\b"),
    re.compile(r"(?m)^\s*set\s+\+o\s+errexit\b"),
    re.compile(r"(?m);\s*true\s*$"),
)


def _run_body(step: dict) -> str:
    """A step's `run:` body, shell line-continuations joined so a wrapped flag is still one command."""
    return str(step.get("run") or "").replace("\\\n", " ")


def _mutating_steps_with_job() -> list[tuple[str, dict, dict]]:
    """`(job name, job, step)` for each step that RUNS a hosted-mutating CLI command."""
    return [
        (name, job, step)
        for name, job in _applier_jobs()
        for step in (job.get("steps") or [])
        if any(c.startswith(_MUTATING_ROOTS) for c in _commands_in(step))
    ]



def _commands_in(step: dict) -> list[str]:
    """The CLI invocations one step runs (shell line-continuations joined, prose skipped)."""
    return [
        line.strip()
        for line in _run_body(step).splitlines()
        if line.strip().startswith("supabase ")
    ]


def _applier_commands() -> list[str]:
    """The Supabase CLI commands the workflow actually RUNS, one string per invocation.

    Selected by *invocation*, not by mention: a `run:` body's command lines are the ones whose
    stripped text starts with `supabase ` (shell line-continuations are joined first). A comment
    or a `echo "… db push …"` fallback note is documentation — the same class that false-reds a
    correct file when a matcher is broad enough to read prose as a command, which is why the seam
    is the command position rather than a substring anywhere in the body.
    """
    return [command for step in _applier_steps() for command in _commands_in(step)]


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
        # An exact pin is not enough — the MAJOR must also be a deliberate choice, so a downgrade
        # OR an upgrade moves the constant in the same commit and shows up in the diff. ⛔ Do NOT
        # "correct" the reasons below back into a claim about flag or config incompatibility: they
        # were MEASURED on this exact tree (2026-11-05), running the workflow's own command shape
        # `db push --linked --include-all --yes` under `--dry-run`:
        #
        #   npx supabase@2.122.1 … -> AccessTokenRequiredError   (config LOADED, all flags accepted)
        #   npx supabase@3.0.0   … -> AccessTokenRequiredError   (config LOADED, all flags accepted)
        #
        # So BOTH majors load this config and BOTH accept `--yes`: `--yes` is a GLOBAL flag in
        # v2.122.1 too ("answer yes to all prompts"), and the only config change #10896 makes is
        # `[inbucket]` → `[local_smtp]`, which v2.108.0+ already reads. `[db.migrations]` /
        # `[storage.vector]` predate this PR and load fine under v2. The reason to stay on v2 is
        # therefore NOT incompatibility — it is #11100's ruling that the major moves in its own PR
        # with a full local run (`supabase start` / `db reset` + the local-auth harnesses), because
        # this config is not yet v3-NATIVE: v3 emits `auth.email.enable_signup` and
        # `auth.sms.enable_signup` deprecations against it.
        major = int(re.match(r"^v?(\d+)", pin).group(1))
        assert major == _PINNED_CLI_MAJOR, (
            f"the applier's CLI pin is on major {major} ({pin!r}) but _PINNED_CLI_MAJOR is "
            f"{_PINNED_CLI_MAJOR} — a CLI major changes the `supabase/config.toml` schema and the "
            f"consent behaviour of every hosted mutation, on the ONLY door that applies production "
            f"DDL. If you are moving it deliberately, change _PINNED_CLI_MAJOR in this commit and "
            f"validate the new schema locally (`supabase start` / `db reset` + the local-auth "
            f"harnesses) — see #10593 / #11100 / #10896."
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
        # The flag set alone does not prove the applier APPLIES: `--dry-run` keeps both flags and
        # exits 0 having applied nothing, and a `db push` with no remote selector silently targets
        # the LOCAL stack (`Target: local stack (127.0.0.1:54322)` → DbConnectError). Either edit is
        # one token, and either leaves a green suite over an applier that did nothing — the exact
        # "reads as a deploy" inversion this module exists to catch.
        assert not re.search(r"--dry[-_]run", cmd), (
            f"`--dry-run` must never reach the production applier ({cmd!r}): it prints the plan and "
            f"applies nothing, and it exits 0 — a deploy that looks successful and left the schema "
            f"unapplied (#10896, #3627)"
        )
        assert re.search(r"--(linked|remote)\b", cmd), (
            f"the production apply must name its REMOTE target ({cmd!r}): without `--linked`/"
            f"`--remote`, `db push` addresses the local stack (127.0.0.1:54322) and applies nothing "
            f"to production (#10896)"
        )
        if "--linked" in cmd:
            assert any(
                c.startswith("supabase link") for c in commands
            ), (
                "`db push --linked` needs a linked project: the workflow must still run "
                "`supabase link --project-ref …` before it, or the push dies with "
                "ProjectRefNotLinkedError (#10896)"
            )
    fn_deploys = [c for c in commands if c.startswith("supabase functions deploy")]
    assert fn_deploys, "the edge-function deploy steps must still exist (#3627 co-move)"
    for cmd in fn_deploys:
        assert "--yes" in cmd, (
            "`supabase functions deploy` is consent-gated in CLI v3; without `--yes` the schema "
            "applies and the app half never flips — the mismatched pair #3627 exists to prevent"
        )
    # A failed applier must still FAIL. `continue-on-error: true` on any mutating step satisfies the
    # job's implicit `success()`, so `Dispatch deploy-hosted` (the last step, #3627) fires over an
    # unapplied schema — a broken deploy that reads as a successful one, with every flag above
    # intact. `test_comove_dispatch_3627` pins this on the DISPATCH step only, never on the applies.
    # …and the silencing must be closed at ALL THREE levels it can be set, not just the step. Each
    # of the four mutations below was measured GREEN against the step-attribute-only form:
    # `|| true` appended to the push, `set +e` prepended, a JOB-level `continue-on-error: true`, and
    # an `if:`-wrapped push. The first three are asserted here; assert-don't-swallow is the contract.
    mutating = _mutating_steps_with_job()
    assert mutating, (
        "the applier job must still RUN a hosted mutation — finding none means the selection broke, "
        "and every assertion below would pass vacuously (#10896)"
    )
    for job_name, job, step in mutating:
        where = f"step {step.get('name')!r} in job {job_name!r}"
        assert not step.get("continue-on-error"), (
            f"{where} runs a hosted mutation and must not set `continue-on-error`: a failed apply "
            f"would stop blocking, and the app flip at the end of the same job would fire over an "
            f"unapplied schema (#10896, #3627)"
        )
        assert not job.get("continue-on-error"), (
            f"job {job_name!r} sets `continue-on-error` and runs a hosted mutation: a FAILED apply "
            f"then produces a GREEN RUN, and `Dispatch deploy-hosted` (#3627) fires over an "
            f"unapplied schema — the inversion this test exists to catch, with every flag above "
            f"still intact (#10896)"
        )
        body = _run_body(step)
        for swallow in _FAILURE_SWALLOWERS:
            assert not swallow.search(body), (
                f"{where} silences its own failure (matched {swallow.pattern!r}): the apply becomes "
                f"a no-op that reads as success, and the co-move dispatch fires over an unapplied "
                f"schema. Assert the exit status — never swallow it (#10896, #3627)"
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
    # PER OCCURRENCE on the RUNNABLE form (`--linked`), not as a whole-file substring. The old
    # substring form was measured GREEN when a SECOND printed `--linked` remedy without `--yes` was
    # added beside the fixed one. The anchor is `--linked` because the file ALSO says
    # `supabase db push --include-all` in PROSE describing what the deploy runs — documentation, not
    # a remedy to copy — and asserting on that would false-RED a correct gate.
    pushes = re.findall(r"supabase db push --linked[^\"\n]*", drift)
    assert pushes, (
        "the drift gate must still print a runnable `supabase db push --linked` apply remedy: an "
        "operator resolving drift needs a command to run (#1095), and its disappearance must not "
        "pass vacuously"
    )
    lacking = [cmd for cmd in pushes if "--yes" not in cmd]
    assert not lacking, (
        f"{len(lacking)} of {len(pushes)} printed `supabase db push --linked` remedies lack `--yes` "
        f"({lacking}): CLI v3 consent-gates `db push` and cannot prompt, so the copy-paste apply an "
        f"operator takes from the gate applies nothing and says nothing (#10896)"
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
    # One message per `::warning::` rather than one per PHYSICAL LINE, shell line-continuations
    # JOINED first, and the selector matched with a whitespace-tolerant regex: all three narrow a
    # false-red found in review. A cosmetic reflow is semantically identical (the shell deletes
    # `\` + newline) but would separate `migration repair` from `--status applied` — or `--yes`
    # from the remedy — on the next physical line and red a CORRECT banner. A future banner that
    # merely EXPLAINS the repair in prose is still excluded by the command root, and `_applier_steps()`
    # keeps one YAML traversal to maintain.
    banners = [
        "::warning::" + part
        for step in _all_steps()
        for part in str(step.get("run") or "").replace("\\\n", " ").split("::warning::")[1:]
    ]
    remedies = [
        banner
        for banner in banners
        if re.search(r"migration\s+repair", banner) and re.search(r"--status\s+applied", banner)
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
