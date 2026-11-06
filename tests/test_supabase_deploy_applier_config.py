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


#: A REMOTE target selector. `--db-url` is included: it is a second door into production (measured
#: unguarded before this — a job running `supabase db push --db-url …` was not the applier at all).
_REMOTE_TARGET = re.compile(r"--(?:linked|remote)\b|--project-ref\b|--db-url\b")
#: …but a `--db-url` pointed at loopback is a LOCAL stack, the same exclusion `--local` gets.
_LOCAL_TARGET = re.compile(r"--local\b|--db-url[= ]+\S*(?:localhost|127\.0\.0\.1)")


def _mutates_hosted(step: dict) -> bool:
    """True when the step RUNS a hosted-mutating command against a REMOTE target."""
    for command in _commands_in(step):
        if not command.startswith(_MUTATING_ROOTS):
            continue
        if _LOCAL_TARGET.search(command) or not _REMOTE_TARGET.search(command):
            continue
        return True
    return False


def _applier_jobs() -> list[tuple[str, dict]]:
    """The job(s) that MUTATE the hosted project — the only door that applies production DDL.

    Selected by what the job MUTATES, not by what it INSTALLS (review cycle 7). Keying on
    `supabase/setup-cli` only MOVED the false RED: a legitimate job that installs the CLI to drive
    the local stack — exactly the `supabase start` / `db reset` run #11100 defers the v3 bump to —
    red the "exactly one installer" assertion. Selecting on the mutation closes that:
    such a job IS the applier, so the pin assertion below then requires it to install a pinned CLI.

    The vacuity guard is the `len(jobs) == 1` assertion in `_applier_steps()`, NOT `assert mutating`
    in the apply test — that one is unreachable by construction, because this selection and
    `_mutating_steps_with_job()` share one predicate. Stated so the next reader does not lean on it.

    Resident gap, stated rather than implied (and MEASURED, review cycle 11): a mutating command that
    does not begin with `supabase ` is not detected, so it would not make its job the applier —
    `npx supabase@2 db push --linked …`, `bash -c '… db push …'`, or a script wrapper all stay
    GREEN. `--db-url` IS covered (above). Closing it would mean matching the subcommand anywhere in
    a run body, which is exactly what makes a `::warning::` banner read as a command — the false RED
    cycles 7-9 spent their time on. Stated, not hidden.
    """
    return [
        (name, job)
        for name, job in (_workflow_doc().get("jobs") or {}).items()
        if any(_mutates_hosted(step) for step in (job.get("steps") or []))
    ]


def _applier_steps() -> list[dict]:
    """The steps of the applier job (the one that mutates the hosted project)."""
    jobs = _applier_jobs()
    assert len(jobs) == 1, (
        f"expected exactly one job that mutates the hosted project, found "
        f"{[n for n, _ in jobs]} — with more than one, the applier's guards silently check only "
        f"the first, and the others apply production DDL unguarded (#10896)"
    )
    return list(jobs[0][1].get("steps") or [])


#: The command roots that mutate the HOSTED project.
_MUTATING_ROOTS = ("supabase db push", "supabase functions deploy", "supabase migration repair")

#: A shell `run:` body can turn a failing apply into a green one. Enumerating the swallow tokens is
#: unbounded and was measured defeated by `|| exit 0`, `|| echo …`, `|| { …; exit 0; }`, `| tee p`
#: and a bare trailing `&` (review cycle 7) — the blacklist that closed `|| true` was still a
#: token list. So the rule is STRUCTURAL: a mutating step's body IS its invocation, and composing it
#: with a control operator is refused outright. Redirections (`2>/dev/null`) are harmless and allowed;
#: `set -e`/`set -euo pipefail` are allowed (and are what makes a bare command fail).
#:
#: The set below is the spellings that were MEASURED to substitute a NON-ZERO exit for a zero one:
#: `|| …` (any right-hand side, including `exit 0`), `| tee …`, `&& …`, `; …`, a trailing `&` (the
#: step returns 0 immediately, `--yes` or not), and command substitution, which can hide any of them
#: behind `$( )` or backticks. `trap 'exit 0' ERR` also swallows the failure with no operator at all
#: (measured), so it is refused separately. This is not claimed to be exhaustive — it is the set
#: measured, plus a refusal of the mechanism.
_CONTROL_OPERATOR = re.compile(r"[|&;]|\$\(|`")
#: A literal-false gate on a mutating step is the fourth measured no-op: `if: false` skips the apply
#: and the job still passes, because the dispatch step's implicit `success()` is satisfied.
_FALSE_CONDITION = re.compile(r"\s*(?:\$\{\{\s*)?false\s*(?:\}\})?\s*")

#: `trap … exit 0` on ERR/EXIT re-zeroes the status with no control operator to catch (measured, bug:
#: `set -e; trap 'exit 0' ERR; false` exits 0). A mutating step has no legitimate use for it.
_EXIT_ZERO_TRAP = re.compile(r"trap\s+[^;\n]*exit\s+0")
#: Redirections are NOT control composition: `2>&1`, `&> file`, `>> log`, `2>/dev/null` only move the
#: file descriptors and cannot change the exit status. They are stripped before the operator scan,
#: which otherwise false-RED `2>&1` (measured, review cycle 8).
#: ⛔ The TARGET class excludes `| & ; ( ) < >`: a greedy `\S+` swallowed an operator glued to the
#: target, so `>/dev/null||true`, `2>/dev/null|tee l` and `2>/dev/null||true` passed GREEN — the
#: swallow this whole check exists to catch (measured, review cycle 9).
#: ⛔ And the `>` in the noclobber form is REQUIRED, not optional: `>?\|` also consumed a BARE pipe,
#: so `--yes|tee /tmp/p` and `--yes|cat` were stripped as redirections and passed GREEN — a real
#: swallow (a pipeline takes the LAST command's status), re-opening a case cycle 9 had closed
#: (measured, review cycle 11).
_REDIRECTION = re.compile(r"(?:\d?>&\d?|&>>?|>\|\s*[^\s|&;()<>]+|\d?>>?\s*[^\s|&;()<>]+)")
#: A quoted span is DATA. `--memo "a&b"` is an argument, not a control operator, and scanning it
#: as shell false-RED a correct body (measured, review cycle 10). Blanked before the operator scan.
#: ⛔ The span must not cross a NEWLINE: `[^']*` did, so an apostrophe in one trailing comment paired
#: with one in a later comment and blanked the lines between them — including a real `|| true` on the
#: apply (measured GREEN, review cycle 11). Trailing comments are also stripped, below.
_QUOTED = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")

#: `set +e` / `set +o errexit` re-enable "keep going after a failure" — the errexit axis.
#: ⛔ The flag is not always `e` ALONE: `set +eu` and `set +ex` disable errexit in a combined word, and
#: `\+[eo]\b` cannot match `+e` followed by `u` (no word boundary), so both passed GREEN with a
#: trailing `exit 0` (measured, review cycle 10). The class now allows surrounding flags.
_ERRETIT_DISABLED = re.compile(r"(?m)^\s*set\s+\+[A-Za-z]*[eo][A-Za-z]*\b")


def _run_body(step: dict) -> str:
    """A step's `run:` body, shell line-continuations joined so a wrapped flag is still one command."""
    return str(step.get("run") or "").replace("\\\n", " ")


#: A printed REMEDY is identified by its command ROOT plus the flag that makes it the RUNNABLE form.
#: Anchoring only on a flag window could not tell a command from a MENTION of one: a note like
#: `echo "NOTE: supabase db push --linked is the manual door"` or
#: `echo "Use supabase secrets set to add a missing secret"` matched and RED, while
#: `echo "See the --status applied <version> --yes note."` matched and satisfied the assertion with no
#: command printed at all (both measured, review cycle 9). The distinguisher is what a *mention*
#: would not carry; `--yes` may then appear ANYWHERE in the match, so a valid
#: `migration repair --yes --linked --status applied <version>` is not false-RED for token order.
_REPAIR_REMEDY = re.compile(r"supabase\s+migration\s+repair[^\"\n]*--status\s+applied[^\"\n]*")
_DB_PUSH_REMEDY = re.compile(r"supabase\s+db\s+push\s+--linked[^\"\n]*--include-all[^\"\n]*")
_SECRETS_SET_REMEDY = re.compile(r"supabase\s+secrets\s+set[^\"\n]*--project-ref[^\"\n]*")


def _printed_remedies(text: str, pattern: str) -> list[str]:
    """Every occurrence of `pattern` on a line a reader could COPY out of the output.

    A printed remedy is any string literal that reaches an operator's terminal — `echo`, `printf`, a
    heredoc, a function that echoes. Selecting `echo`-prefixed lines was measured to MISS `printf`
    and heredoc forms (they stayed GREEN without `--yes`), so the filter is inverted: drop shell
    COMMENT lines and keep everything else. Comments are dropped because these files legitimately
    *describe* the commands (`check-migration-drift:38`, `:156`), and matching that prose false-RED a
    correct gate (measured, review cycles 7 and 8).

    Continuations are joined first, so a remedy wrapped across lines is still one remedy and a
    trailing `--yes` on the continuation is still seen.
    """
    found: list[str] = []
    for line in text.replace("\\\n", " ").splitlines():
        if line.lstrip().startswith("#"):
            continue
        found.extend(match.group(0) for match in re.finditer(pattern, _strip_trailing_comment(line)))
    return found


def _strip_trailing_comment(line: str) -> str:
    """Drop a trailing `# …`, quote-aware, so a NOTE beside a printed line is not read as output.

    `echo "…"  # cf. supabase migration repair --status applied <version>` had its comment scanned as
    a printed remedy and RED the suite for text that is never echoed (measured, review cycle 11).
    A `#` after the last quote closes a shell line and is a comment; a `#` INSIDE the quotes is
    content and is left alone. With no quotes at all, any `#` is a comment.
    """
    quote_at = max(line.rfind('"'), line.rfind("'"))
    hash_at = line.find("#", quote_at + 1)
    return line[:hash_at] if hash_at != -1 else line


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
        assert "--linked" in cmd, (
            f"the production apply must pass `--linked` ({cmd!r}). On the pinned v2 CLI `db push`'s "
            f"target flags are `--db-url` / `--linked` / `--local` / `--project-ref` — `--remote` is a "
            f"SUBCOMMAND (`db remote`), not a `db push` flag, so accepting it would bless a command that "
            f"dies at flag parse. Without a target flag the default has moved to the LOCAL stack "
            f"(127.0.0.1:54322) on the v3 line, which is exactly the migration this pin defers "
            f"(#10896, #11100)"
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
        # YAML parses a bare `false` into the BOOLEAN, so compare the value too — `str(False)` is
        # "False" and a lowercase-only regex missed it (measured, review cycle 10).
        assert step.get("if") is not False and not _FALSE_CONDITION.fullmatch(
            str(step.get("if") or "").lower()
        ), (
            f"{where} is gated `if: false`: the step never runs, the job is GREEN, and "
            f"`Dispatch deploy-hosted` (#3627) fires over an unapplied schema. No legitimate "
            f"condition on a mutating step is the constant `false` (measured, review cycle 10)"
        )
        assert not step.get("shell"), (
            f"{where} overrides `shell:`. A mutating step must run under the runner's default "
            f"`bash -e`; `shell: bash -c 'exit 0'` takes the body as `$0` and exits 0 without running "
            f"it — a silent no-op that reads as a successful apply (measured, review cycle 9)"
        )
        assert job.get("if") is not False and not _FALSE_CONDITION.fullmatch(
            str(job.get("if") or "").lower()
        ), (
            f"job {job_name!r} is gated `if: false`: the whole job — including the app flip — is "
            f"skipped and the run reads GREEN, while nothing was applied (measured, review cycle 11)"
        )
        assert not job.get("continue-on-error"), (
            f"job {job_name!r} sets `continue-on-error` and runs a hosted mutation: a FAILED apply "
            f"then produces a GREEN RUN, and `Dispatch deploy-hosted` (#3627) fires over an "
            f"unapplied schema — the inversion this test exists to catch, with every flag above "
            f"still intact (#10896)"
        )
        # Comment lines are dropped first, exactly as `_printed_remedies` does: a comment is prose,
        # so the most natural note on this very step ("# NOTE: v3 consent-gates this; keep --yes")
        # must not be read as shell and red a correct step (measured, review cycle 9).
        body = "\n".join(
            line for line in _run_body(step).splitlines() if not line.lstrip().startswith("#")
        )
        assert not _ERRETIT_DISABLED.search(body), (
            f"{where} disables errexit (`set +e` / `set +o errexit`): the apply can then fail and "
            f"the step still exits 0, so the co-move dispatch fires over an unapplied schema "
            f"(#10896, #3627)"
        )
        assert not _EXIT_ZERO_TRAP.search(body), (
            f"{where} installs a `trap … exit 0`: that re-zeroes the apply's exit status with no "
            f"control operator to catch (`set -e; trap 'exit 0' ERR; false` exits 0 — measured), so "
            f"the co-move dispatch fires over an unapplied schema (#10896, #3627)"
        )
        operator = _CONTROL_OPERATOR.search(_REDIRECTION.sub(" ", _QUOTED.sub('""', body)))
        assert operator is None, (
            f"{where} composes its apply with the shell control operator {operator.group(0)!r}: "
            f"that is how a failed apply is turned into a green one — `|| true`, `|| exit 0`, "
            f"`|| {{ …; exit 0; }}`, `| tee`, `&&`, `;`, a trailing `&`, or a `$( )` that hides any "
            f"of them. The step must BE its invocation and fail on its own exit status; if you "
            f"genuinely need an `echo` around it, put it in a previous step (#10896, #3627)"
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
    The match is anchored on the command ROOT plus the flag that makes it the RUNNABLE form (see
    `_REPAIR_REMEDY`), so a MENTION of the command is not a remedy. It is per printed LINE, so a
    remedy split across two printed lines is not asserted — the one site that was split was reflowed
    into a single `echo`, which is also what makes it copy-pasteable.
    """
    governed = {
        ".github/scripts/check-migration-drift": (REPO_ROOT / ".github" / "scripts" / "check-migration-drift"),
        ".github/scripts/check-migration-append-only": (
            REPO_ROOT / ".github" / "scripts" / "check-migration-append-only"
        ),
    }
    for name, path in governed.items():
        sites = _printed_remedies(path.read_text(encoding="utf-8"), _REPAIR_REMEDY)
        assert sites, (
            f"{name} must still print a `migration repair --status applied <version>` remedy: an "
            f"operator shown OUT OF ORDER versions needs a command to run (#1095). A silent "
            f"disappearance of the remedy must not pass vacuously."
        )
        lacking_repair = [site for site in sites if "--yes" not in site]
        assert not lacking_repair, (
            f"{name}: {len(lacking_repair)} of {len(sites)} printed `--status applied <version>` "
            f"remedies still lack `--yes` ({lacking_repair}) — CLI v3 consent-gates `migration "
            f"repair` and cannot prompt, so the copy-paste remedy an operator takes from the gate "
            f"output repairs nothing and says nothing (#10896, #2240)"
        )
    # The printed manual fallback for the apply itself (drift gate, remediation (a)).
    drift = governed[".github/scripts/check-migration-drift"].read_text(encoding="utf-8")
    # PER OCCURRENCE on the RUNNABLE form (`--linked`), not as a whole-file substring: the old
    # substring form was measured GREEN when a SECOND printed `--linked` remedy without `--yes` was
    # added beside the fixed one. `_printed_remedies` keeps only copyable lines, so the gate's own
    # prose about `db push` (comments at :38, :156; the echo at :336/:411) cannot false-RED it —
    # measured for a comment that mentions `--linked` (cycle 7) and for `printf`/heredoc forms that an
    # `echo`-prefixed filter missed entirely (cycle 8).
    pushes = _printed_remedies(drift, _DB_PUSH_REMEDY)
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


def test_printed_secrets_set_remedy_passes_yes_too() -> None:
    """The workflow PRINTS a `secrets set` remedy; a printed remedy must run unattended too (#10896).

    Removing `--yes` from `supabase-deploy.yml`'s `::error::Set them with: supabase secrets set …`
    was measured GREEN — the file's banner remedy was pinned by the test below while its *secrets*
    remedy, printed in the same step family for the same reason, was pinned by nothing. `secrets set`
    is in CLI v3's consent table (docs -> 'Migrating to Supabase CLI v3'), so the copy-paste an
    operator takes from a failing run would fail the same way and say nothing.
    """
    printed = _printed_remedies(WORKFLOW.read_text(encoding="utf-8"), _SECRETS_SET_REMEDY)
    assert printed, (
        "the workflow must still PRINT a runnable `supabase secrets set` remedy — it is the only "
        "instruction an operator gets when a required secret is missing (#1121), and its "
        "disappearance must not pass vacuously"
    )
    lacking = [cmd for cmd in printed if "--yes" not in cmd]
    assert not lacking, (
        f"{len(lacking)} of {len(printed)} printed `supabase secrets set` remedies lack `--yes` "
        f"({lacking}): CLI v3 consent-gates `secrets set` and cannot prompt, so the instruction an "
        f"operator copies out of a failed deploy sets nothing (#10896, #1121)"
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
