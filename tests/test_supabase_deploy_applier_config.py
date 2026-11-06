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

import copy
import re
import shlex
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The CLI MAJOR this workflow deliberately pins. A bump is a deliberate change (#10593 / #11100):
#: it must move this constant in the same commit, so the decision is visible in the diff instead of
#: arriving as a routine upstream release. Both v2 and v3 are MEASURED to load the committed config
#: (and both accept `--yes`) — see the argument at `test_the_production_applier_is_version_pinned`.
_PINNED_CLI_MAJOR = 2
#: The PRODUCTION Supabase project the applier must apply to. Pinned as a literal on purpose: until
#: review cycle 11 the only target comparison was "a link step exists", so pointing `env:
#: SUPABASE_PROJECT_REF` (`:170`) or the link step (`:252`) at another project left the whole battery
#: GREEN (measured, 32 passed) while `db push --linked` applied production DDL to a DIFFERENT
#: project — a green run over an unapplied production schema, worse than the dead applier this PR
#: fixes because nothing is red. A project move is a deliberate change: move this constant in the
#: same commit so it shows in the diff instead of arriving as a one-token edit.
_PRODUCTION_PROJECT_REF = "ybetwichurajbfswfeqa"
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
    """True when the step RUNS a hosted-mutating command against a REMOTE target.

    This is the JOB-SELECTION question ("is this the applier?") and it deliberately requires a
    remote selector. It is NOT the APPLY-CONTRACT question — see `_mutates_production`.
    """
    for command in _commands_in(step):
        if not command.startswith(_MUTATING_ROOTS):
            continue
        if _LOCAL_TARGET.search(command) or not _REMOTE_TARGET.search(command):
            continue
        return True
    return False


def _mutates_production(step: dict) -> bool:
    """True when the step RUNS a hosted-mutating command whose target is not the LOCAL stack.

    ⛔ DIFFERENT QUESTION FROM `_mutates_hosted`, AND THE DIFFERENCE IS A FALSE PASS (#11135, fourth
    review). Job selection must demand a REMOTE selector — a job with no remote-targeted mutation is
    not "the applier". The APPLY contract must not, because "no target flag" is not "not
    production":

    * `supabase db push --linked` and `supabase db push` are the same command against the same
      project on the pinned v2 CLI, which only WARNS that the local default is a FUTURE major;
    * `supabase functions deploy <name>` targets the linked project with no target flag at all
      (`--help`: "Deploy a Function to the linked project").

    So `_mutates_hosted` returns **False** for both untargeted forms — measured — and gating the
    contract on it (or on the containing job's membership, the same predicate one level up) leaves a
    step that really mutates production free to set `continue-on-error: true` and `exit 0`: MEASURED
    GREEN.

    ⛔ BUT "NO TARGET FLAG" IS NOT ONE DIRECTION (#11135, fifth review). `db reset`, `migration
    up`/`down`/`squash` are LOCAL BY DEFAULT — RAN against the pinned CLI: "Connecting to local
    database", `"isLocal":true`, "supabase start is not running" — while `migration repair` is NOT
    (bare, it answers `ProjectRefNotLinkedError`, so it addresses the linked project). Each local
    root takes `--linked` / `--project-ref` / `--db-url` as its REMOTE form. Treating those as production would
    put the applier's body-IS-its-invocation rule on a local harness step (`supabase db reset` then
    `supabase start`, the #11100 shape `_applier_jobs` anticipates) and red a correct workflow — the
    false-RED class this file spent four cycles removing. So the exemption is: an EXPLICIT local
    target, or a LOCAL-BY-DEFAULT root with NO remote selector. Everything else — including an
    untargeted `db push` / `functions deploy` / `secrets set` / `config push` — counts as production,
    fail-closed.
    """
    for command in _commands_in(step):
        if not command.startswith(_MUTATING_ROOTS):
            continue
        if _LOCAL_TARGET.search(command):
            continue
        if command.startswith(_LOCAL_BY_DEFAULT_ROOTS) and not _REMOTE_TARGET.search(command):
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


#: The command roots that mutate the HOSTED project — the v3 CONSENT TABLE, not just the three the
#: applier happens to run today (#11104 review P2). It was narrower by measurement: adding
#: `supabase config push`, `supabase secrets unset …` or `supabase db reset --linked` to the applier
#: job left the suite green, because `_mutating_steps_with_job` — which enforces "the body IS its
#: invocation", no `|| true`, no `set +e` — only ever examines a step whose command starts with one
#: of these roots. `config push` and `secrets set/unset` are the plausible next additions to THIS
#: workflow: it exists to sync schema, and it already prints a `secrets set` remedy. None of these
#: appears as an executed command today, so widening the tuple introduces no false RED.
#:
#: ⛔ THIS TUPLE SELECTS THE APPLIER JOB (`_mutates_hosted` → `_applier_jobs` → the
#: `len(jobs) == 1` assert), so a root here may only be one that CHANGES the hosted project (#11135).
#: `db pull` / `db diff` / `config pull` are consent-gated but READ-ONLY: listing them here made
#: `_mutates_hosted({"run": "supabase db pull --linked"})` true, so a future read-only drift job
#: would have been selected as "the applier" and red the suite naming the wrong cause. They belong in
#: `_CONSENT_GATED_ROOTS` below, which the SWEEP reads and the selector does not.
_MUTATING_ROOTS = (
    "supabase db push",
    "supabase db reset",
    "supabase functions deploy",
    "supabase functions delete",
    "supabase migration repair",
    "supabase migration up",
    "supabase migration down",
    "supabase migration squash",
    "supabase secrets set",
    "supabase secrets unset",
    "supabase config push",
)

#: Consent-gated commands that do NOT change the hosted project. The v3 consent gate covers them, so
#: a step that RUNS one must still pass `--yes` — but such a step must never make its job "the
#: applier" (see `_MUTATING_ROOTS`). The sweep reads `_MUTATING_ROOTS + _CONSENT_GATED_ROOTS`; the
#: job selector reads only `_MUTATING_ROOTS`. Two questions, two sets (#11135).
_CONSENT_GATED_ROOTS = (
    "supabase db pull",
    "supabase db diff",
    "supabase config pull",
)

#: What the SWEEP walks: a step that RUNS any consent-gated hosted command, mutating or not. The
#: job SELECTOR reads `_MUTATING_ROOTS` alone; the APPLY contract is decided per STEP, not per job
#: (see `_applier_contract`) — three predicates, three questions, no nesting assumption (#11135).
_SWEPT_ROOTS = _MUTATING_ROOTS + _CONSENT_GATED_ROOTS

#: Mutating roots that act on the LOCAL stack unless a remote selector is passed. MEASURED by RUNNING
#: the pinned v2 CLI in an unlinked directory, not read off help prose (that is how the first version
#: of this tuple went wrong): `migration up` and `db reset` print "Connecting to local database" /
#: "supabase start is not running", and `migration squash` reports `"isLocal":true`.
#:
#: ⛔ `migration repair` IS NOT ONE OF THEM (#11135, sixth review). Bare
#: `supabase migration repair --status applied <v> --yes` answers `ProjectRefNotLinkedError: Cannot
#: find project ref. Have you run supabase link?` — it targets the LINKED project, i.e. the
#: fail-open direction — and the repo's own printed remedy passes `--linked`. Its help line
#: ("Not needed with --local or --db-url") describes the ACCESS-TOKEN requirement, not the default
#: target, and reading it as the latter put a production mutation behind the same exemption as a
#: local harness. The remaining mutating roots (`db push`, `functions deploy` / `delete`,
#: `secrets set` / `unset`, `config push`) address the project outright and take a target only to
#: OVERRIDE it, so for all of those "no target flag" is production.
_LOCAL_BY_DEFAULT_ROOTS = (
    "supabase db reset",
    "supabase migration up",
    "supabase migration down",
    "supabase migration squash",
)

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
# `if: false` is not the only way to write a skip: a COMPOUND condition carrying a false operand is
# skipped just as thoroughly, and `_FALSE_CONDITION.fullmatch` sees neither. Measured GREEN (review
# cycle 13): `if: github.event_name == 'workflow_dispatch' && env.SUPABASE_ACCESS_TOKEN != '' && false`
# on the apply step — the step is skipped, the job is GREEN, and `Dispatch deploy-hosted` (#3627)
# carries no status function, so the app flips over an unapplied schema.
# Only the two shapes that actually force falsity are matched: `false && …` (short-circuits the whole
# expression) and `… && false` (the last operand kills it). `A || false` is NOT matched, because it is
# equivalent to `A` — matching it would be a false RED. `… && false || B` is also not matched; that
# spelling is deliberately left to the allowlist rewrite (#11120), since it is far rarer than the two
# above and a regex that guessed at operator precedence would be the kind of check that reads as
# protection while missing its own case.
_FALSE_OPERAND = re.compile(
    r"(?:\A|&&)\s*(?:\$\{\{\s*)?false\s*(?:\}\})?\s*(?:&&|\Z)"
)

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


#: `set -n` (noexec) parses the body and executes NOTHING, then the step exits 0 — ONE CHARACTER
#: from the allowed `set -e`. Measured at the shell: a body of `set -n` followed by `exit 3` exits 0,
#: while `set -e` followed by `exit 3` exits 3 (review cycle 11).
def _disables_execution(body: str) -> str | None:
    """The `set` token that puts the shell in noexec, if the body carries one.

    Matched on FLAG WORDS, not on the literal `-n`. `set -en`, `set -ne`, `set -e -n` and
    `set -euon pipefail` all disable execution (`bash -c 'set -en; echo ran; exit 3'` prints nothing
    and exits 0) and all passed a `-n`-literal check — measured GREEN over an applier that never ran,
    which is the same widening `_ERRETIT_DISABLED` already needed for `set +eu` (review cycle 13).
    A bare `-o` is a word of its own whose VALUE is the flag name, so it is read as a pair.
    """
    for line in body.splitlines():
        words = line.split()
        if not words or words[0] != "set":
            continue
        rest = words[1:]
        for index, word in enumerate(rest):
            if word == "-o" and index + 1 < len(rest) and rest[index + 1] == "noexec":
                return "set -o noexec"
            if word.startswith("-") and not word.startswith("--") and "n" in word[1:]:
                return f"set {word}"
    return None


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
# The banner remedy omits the `supabase` prefix (it is printed as `'migration repair …'`), so it needs
# its own anchor rather than `_REPAIR_REMEDY`.
_BANNER_REPAIR = re.compile(r"migration\s+repair")


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
        found.extend(match.group(0) for match in re.finditer(pattern, _strip_shell_comment(line)))
    return found


def _mutating_steps_with_job() -> list[tuple[str, dict, dict]]:
    """`(job name, job, step)` for each step that RUNS a consent-gated hosted CLI command.

    The subject is the CONSENT GATE, not mutation: `_MUTATING_ROOTS + _CONSENT_GATED_ROOTS`, because
    v3 asks before a hosted READ too.

    ⛔ EVERY job, not `_applier_jobs()` (#11135, second review). Sweeping only the mutating job left
    the union's read-only half INERT: a consent-gated read in a SECOND job was guarded by nothing.
    It used to red there by accident — the old tuple made that job a second "applier" — so narrowing
    the selector silently traded one defect (a false RED naming the wrong cause) for another (a
    `supabase db pull --linked || true` drift job that pulls nothing reading green). Job SELECTION
    stays narrow: `_applier_jobs()` reads `_MUTATING_ROOTS` alone.

    ⚠️ The widening's OWN residual, stated rather than implied (#11135, third review): a step that
    only runs a consent-gated READ must NOT have the APPLY contract applied to it ("the body IS its
    invocation", no `continue-on-error`, no `shell:`, no false `if:`, no disabled errexit). Measured
    false REDs, all on legitimate read-only shapes: a snapshot job running `supabase db pull --linked
    --yes` then `git diff --exit-code supabase/migrations`; an informational pull with
    `continue-on-error: true`; and a `supabase db diff --linked --yes > /tmp/drift.sql` prepended to
    this repo's own warn-only drift step (`check-drift`, deliberately `set +e` … `exit 0`). A guard
    that reds those teaches the cheapest escape — delete the read — which is the blindness the
    widening ended.

    ⛔ BUT THE EXEMPTION IS PER STEP, NOT PER JOB (#11135, fourth review — this lane's own fix). The
    first attempt keyed it on `job_name in _applier_jobs()`, and that is WRONG in the dangerous
    direction: `_mutates_hosted` also demands a REMOTE TARGET selector, so a step running
    `supabase db push --include-all --yes` (no `--linked`) in a second job is SWEPT but its job is
    NOT an applier — measured `_mutates_hosted(...) is False`, sweep `True` — and the whole apply
    contract was skipped for a step that really mutates production (`db push` targets the LINKED
    project by default on the v2 pin; `functions deploy` targets the linked project with no flag at
    all). Measured at the previous head: `db push --yes` + `exit 0` + `continue-on-error: true` in a
    second job was RED, and went GREEN once the contract was gated on job membership. The predicate
    is therefore `_mutates_production(step)` — a mutation whose target is not EXPLICITLY local — so a
    read is exempt and an untargeted mutation is not. (`_mutates_hosted`, which job selection uses,
    is the WRONG predicate here: it demands a remote selector, so it is False for exactly the
    untargeted forms this paragraph is about — measured, not assumed.)
    """
    return [
        (name, job, step)
        for name, job in (_workflow_doc().get("jobs") or {}).items()
        for step in (job.get("steps") or [])
        if any(c.startswith(_SWEPT_ROOTS) for c in _commands_in(step))
    ]



def _strip_shell_comment(line: str) -> str:
    """Drop one line's UNQUOTED `#` comment (quotes respected).

    Inside a `run: |` block scalar a `#` is LITERAL text (only a plain scalar gets YAML comment
    stripping). Without this, a flag living only in a comment satisfied the flag assertion while the
    command applied nothing — the 18-run incident re-created with a green suite (measured, cycle 11)
    — and a TRAILING comment could smuggle an operator past the control scan.
    """
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
        i += 1
    return line


#: A `VAR=value` prefix in front of a command (`SUPABASE_ACCESS_TOKEN="$T" supabase …`) — an
#: ordinary way to scope an env var to one command, and one that must not hide the command from the
#: guard (measured: the whole flag battery went RED with a message about a missing `db push`).
_ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_PROJECT_REF_ASSIGNMENT = re.compile(r"^(?:export\s+)?SUPABASE_PROJECT_REF=")


def _command_text(line: str) -> str:
    """One shell line as the command it EXECUTES: comment and `VAR=value` prefix removed."""
    return _ENV_PREFIX.sub("", _strip_shell_comment(line).strip()).strip()


def _commands_in(step: dict) -> list[str]:
    """The CLI invocations one step RUNS (continuations joined; prose, comments, env prefixes gone)."""
    return [
        text
        for text in (_command_text(line) for line in _run_body(step).splitlines())
        if text.startswith("supabase ")
    ]


def _argv(command: str) -> tuple[str, ...]:
    """One command line as WORDS. A flag is a word, never a substring of a line."""
    try:
        return tuple(shlex.split(_strip_shell_comment(command)))
    except ValueError:  # unbalanced quotes: split on whitespace rather than crash the scan
        return tuple(_strip_shell_comment(command).split())


_CONSENT_TRUE = frozenset({"true", "yes", "on", "1", "y"})


def _carries_consent(argv: tuple[str, ...]) -> bool:
    """Whether this argv conveys CONSENT — `--yes` present and NOT inverted.

    ONE predicate for every `--yes` assertion in this file, for the same reason
    `_strip_shell_comment` has one owner: a second spelling of "the flag is there" is how two
    answers to one question diverge.

    ⛔ There are TWO failure directions here, both measured, and the predicate must reject only
    the second:

    * `--yes=false` is NOT consent — the CLI PARSES the value and treats it as no, so a substring
      check that counts it is a false PASS over a dead applier (the defect this file exists for);
    * `--yes=true` / `--yes=1` / `--yes=yes` ARE consent — `--yes` accepts
      true|yes|on|1|y|false|no|off|0|n (measured against 2.110.0). A check that accepts only the
      bare token false-REDs a future CORRECT edit, i.e. a false block on a green applier. That
      was a real review finding against the bare-token form, not a hypothetical.

    An UNRECOGNISED value is not consent: for a consent flag, "not proven" must never read as
    "yes". Stated residual, and it is the exception to that rule: `shlex` strips quotes, so
    `--memo "--yes"` arrives here as the argv `('…', '--memo', '--yes')` — a bare `--yes` that is
    the VALUE of another flag is indistinguishable from the flag and therefore READS AS CONSENT.
    That is pinned as a documented residual (test at the end of this file), not a property, and it
    is unreachable with the flag sets this file governs.
    """
    consent = False
    for word in argv:
        token = word.strip("'\"")
        if token == "--yes":
            return True
        if token.startswith("--yes="):
            consent = consent or token.partition("=")[2].strip("'\"").lower() in _CONSENT_TRUE
    return consent


def _remedy_command(banner: str, anchor: re.Pattern[str]) -> str:
    """The COMMAND a banner prints, sliced out of its quotes — never the whole banner.

    A whole-banner match is defeated by PROSE, measured GREEN: delete `--yes` from the copy-paste
    command and mention it in a sentence of the SAME banner, and a `--yes` search still matches
    while the operator's paste repairs nothing. A printed remedy is a delimited span
    (quoted/backticked) in every banner this repo prints, so slice to the delimiter and assert on
    the command's OWN argv.

    A banner that does not delimit its command is cut at its first CLAUSE boundary
    (`,;` / " and " / newline), never run to the end: running to the end is NOT "the strictest
    reading available" — it is fail-OPEN, and it was measured GREEN. With the command stripped of
    `--yes` and the sentence "… and note that --yes is required on CLI v3" appended in the same
    banner: the quoted form reds, and the run-to-end form passed, because the prose's flag was the
    one the matcher found (#11135). A clause cut keeps prose out of the command either way.

    Declared residual, and it is narrow: the cut runs only on the two fallbacks. A banner whose PROSE
    carries an apostrophe before the anchor takes the delimited path instead (the quote looks like an
    opening delimiter), so an undelimited command with an apostrophe earlier in the sentence still
    slices to the end. No banner this repo prints has that shape — measured against all four sites —
    and the new test pins the shape that does occur. Stated, not implied.
    """

    def _clause(tail: str) -> str:
        return re.split(r"[,;]|\s+and\s+|\n", tail, maxsplit=1)[0].strip()

    match = anchor.search(banner)
    if match is None:
        return banner
    start = match.start()
    opened = max(banner.rfind(quote, 0, start) for quote in "'`\"")
    if opened == -1:
        return _clause(banner[start:])
    closes = [banner.find(quote, start) for quote in "'`\""]
    closed = min((idx for idx in closes if idx != -1), default=len(banner))
    delimited = banner[opened + 1 : closed]
    return delimited if anchor.search(delimited) else _clause(banner[start:])


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
        # Asserted on the PARSED argv, never as a substring of the line: inside a `run: |` block
        # scalar a trailing `#` is LITERAL text, so `db push --linked --include-all   # --yes
        # lands later` satisfied a substring assertion while CLI v3 would apply NOTHING — measured
        # GREEN over a broken applier (cycle 11). A flag is a WORD.
        argv = _argv(cmd)
        assert "--include-all" in argv, "the apply step must keep `--include-all` (#10896)"
        assert _carries_consent(argv), (
            "`supabase db push` on a hosted target is consent-gated in CLI v3 and cannot prompt "
            "in CI: without `--yes` it applies ZERO migrations (#10896)"
        )
        # The flag set alone does not prove the applier APPLIES: `--dry-run` keeps both flags and
        # exits 0 having applied nothing, and a `db push` with no remote selector silently targets
        # the LOCAL stack (`Target: local stack (127.0.0.1:54322)` → DbConnectError). Either edit is
        # one token, and either leaves a green suite over an applier that did nothing — the exact
        # "reads as a deploy" inversion this module exists to catch.
        assert not [f for f in argv if f.startswith(("--dry-run", "--dry_run"))], (
            f"`--dry-run` must never reach the production applier ({cmd!r}): it prints the plan and "
            f"applies nothing, and it exits 0 — a deploy that looks successful and left the schema "
            f"unapplied (#10896, #3627)"
        )
        assert "--linked" in argv, (
            f"the production apply must pass `--linked` ({cmd!r}). On the pinned v2 CLI `db push`'s "
            f"target flags are `--db-url` / `--linked` / `--local` / `--project-ref` — `--remote` is a "
            f"SUBCOMMAND (`db remote`), not a `db push` flag, so accepting it would bless a command that "
            f"dies at flag parse. Without a target flag the default has moved to the LOCAL stack "
            f"(127.0.0.1:54322) on the v3 line, which is exactly the migration this pin defers "
            f"(#10896, #11100)"
        )
        if "--linked" in argv:
            assert any(
                c.startswith("supabase link") for c in commands
            ), (
                "`db push --linked` needs a linked project: the workflow must still run "
                "`supabase link --project-ref …` before it, or the push dies with "
                "ProjectRefNotLinkedError (#10896)"
            )
    # THE TARGET. `db push --linked` applies to whatever `supabase link` bound, and until review
    # cycle 11 NOTHING read that value: a one-token edit to the job's `env: SUPABASE_PROJECT_REF` or
    # to the link step's `--project-ref` left the whole battery GREEN (measured, 32 passed) while
    # production DDL landed on a DIFFERENT project — a green run over an unapplied production schema,
    # which is worse than the dead applier this PR fixes: nothing is red.
    for job_name, job in _applier_jobs():
        declared = str((job.get("env") or {}).get("SUPABASE_PROJECT_REF") or "").strip()
        assert declared == _PRODUCTION_PROJECT_REF, (
            f"job {job_name!r} must declare `env: SUPABASE_PROJECT_REF: "
            f"{_PRODUCTION_PROJECT_REF}` — that is the project `db push --linked` applies to. If "
            f"the production project is moving deliberately, move _PRODUCTION_PROJECT_REF in the "
            f"same commit so the change is visible in the diff (#10896)"
        )
        targets = [
            argv[index + 1] if token == "--project-ref" else token.split("=", 1)[1]
            for step in (job.get("steps") or [])
            for command in _commands_in(step)
            if command.startswith("supabase link")
            for argv in [_argv(command)]
            for index, token in enumerate(argv)
            if (token == "--project-ref" and index + 1 < len(argv))
            or token.startswith("--project-ref=")
        ]
        assert targets, (
            f"job {job_name!r} must run `supabase link --project-ref …` before the apply: the link "
            f"is what gives `db push --linked` its target, and an unread target is how a green run "
            f"applies to the wrong project (#10896)"
        )
        for target in targets:
            assert target in ("$SUPABASE_PROJECT_REF", _PRODUCTION_PROJECT_REF), (
                f"job {job_name!r} links the CLI to {target!r}, which is neither the job's own "
                f"`$SUPABASE_PROJECT_REF` nor the pinned production project "
                f"{_PRODUCTION_PROJECT_REF!r}: `db push --linked` would apply production DDL to "
                f"that project while this run reports success (#10896)"
            )
        # …and the value that reference RESOLVES to must not be re-pointed one level down. A GitHub
        # Actions step-level `env:` takes precedence over job `env:`, so the three assertions above
        # were all satisfied while `env: SUPABASE_PROJECT_REF: someoneelsesproject01` on the Link
        # step sent `db push --linked` to another project — measured GREEN (review cycle 12). Every
        # `--project-ref VALUE` anywhere in the applier job must resolve to production too, which
        # also covers `functions deploy --project-ref …` (a hosted mutation the flag sweep missed).
        for step in job.get("steps") or []:
            override = (step.get("env") or {}).get("SUPABASE_PROJECT_REF")
            # …and the same re-pointing done in the BODY instead of `env:` — the Link step decides
            # the target and is not a mutating step, so the "body must BE its invocation" rule never
            # ran over it: `SUPABASE_PROJECT_REF=someoneelsesproject01` then
            # `supabase link --project-ref "$SUPABASE_PROJECT_REF"` measured GREEN (review cycle 13).
            # A body-level assignment IS visible to the following line at runtime (measured), unlike
            # the prefix form, which bash expands before applying.
            body_assignments = [
                line
                for line in (
                    _command_text(raw) for raw in _run_body(step).splitlines()
                )
                if _PROJECT_REF_ASSIGNMENT.match(line)
            ]
            for line in body_assignments:
                assert line.split("=", 1)[1].strip().strip("'\"") in (
                    "${{ env.SUPABASE_PROJECT_REF }}",
                    _PRODUCTION_PROJECT_REF,
                ), (
                    f"step {step.get('name')!r} in job {job_name!r} re-points the target in its "
                    f"body ({line!r}): a body assignment is visible to the next line, so a `link` "
                    f"in this step binds that project and `db push --linked` applies production "
                    f"DDL there while the run reports success (#10896)"
                )
            # …and `if:` is the same genus: an always-false condition on ANY step of the applier
            # job is a skipped step, and in this job a skipped step is never benign. `if: false` was
            # closed only for MUTATING steps (cycle 10), so the main-only REFUSAL — the step whose
            # whole job is to stop a non-main dispatch from applying that branch's DDL — could be
            # disabled with one token while the entire 9-file workflow battery stayed green (565
            # passed, measured in review cycle 14). A guard protecting a safety check must govern
            # the safety check itself.
            condition = str(step.get("if") or "").lower()
            assert step.get("if") is not False and not _FALSE_CONDITION.fullmatch(
                condition
            ) and not _FALSE_OPERAND.search(condition), (
                f"step {step.get('name')!r} in job {job_name!r} is gated on an always-false "
                f"condition ({step.get('if')!r}): the step never runs and the job reads GREEN. In "
                f"the applier job a skipped step is a skipped CHECK — most of all the main-only "
                f"refusal, which is what stops a non-main dispatch applying that branch's "
                f"migrations to production (#10896)"
            )
            # The tree that is CHECKED OUT is what `db push` applies. The workflow refuses a
            # non-main dispatch by reading `github.ref`, but nothing read what checkout actually
            # fetched — `with: {ref: some-other-branch}` measured GREEN (review cycle 13), and that
            # branch's migrations land on production while the app half is dispatched at main.
            if str(step.get("uses") or "").startswith("actions/checkout"):
                pinned_ref = str((step.get("with") or {}).get("ref") or "").strip()
                assert pinned_ref in ("", "${{ github.ref }}", "${{ github.sha }}"), (
                    f"step {step.get('name')!r} in job {job_name!r} checks out {pinned_ref!r} "
                    f"instead of the dispatched ref: `db push` applies the migrations in THAT tree "
                    f"to production while the app half is dispatched at main — the check the "
                    f"main-only refusal at the top of this workflow is meant to make (#10896)"
                )
            assert override is None or str(override).strip() in (
                "${{ env.SUPABASE_PROJECT_REF }}",
                _PRODUCTION_PROJECT_REF,
            ), (
                f"step {step.get('name')!r} in job {job_name!r} overrides `SUPABASE_PROJECT_REF` "
                f"with {override!r}: a step-level `env:` SHADOWS the job's value, so the link and "
                f"every hosted mutation in that step would target that project while the guard "
                f"reads the job env and stays green (#10896)"
            )
            for argv in (_argv(command) for command in _commands_in(step)):
                for index, token in enumerate(argv):
                    if token == "--project-ref" and index + 1 < len(argv):
                        value = argv[index + 1]
                    elif token.startswith("--project-ref="):
                        value = token.split("=", 1)[1]
                    else:
                        continue
                    assert value in ("$SUPABASE_PROJECT_REF", _PRODUCTION_PROJECT_REF), (
                        f"step {step.get('name')!r} in job {job_name!r} passes "
                        f"`--project-ref {value}`: every hosted mutation in the applier job must "
                        f"target the pinned production project (or resolve the job's own "
                        f"`$SUPABASE_PROJECT_REF`), or a green run mutates a different project "
                        f"(#10896)"
                    )
    fn_deploys = [c for c in commands if c.startswith("supabase functions deploy")]
    assert fn_deploys, "the edge-function deploy steps must still exist (#3627 co-move)"
    for cmd in fn_deploys:
        # Asserted on the PARSED argv, not as a substring (review finding, 2026-11-06): a substring
        # is satisfied by `--yes=false`, a spelling the CLI PARSES and treats as NO consent
        # (`--yes` accepts true|yes|on|1|y|false|no|off|0|n — measured against 2.110.0). Then the
        # three function deploys fail consent while the schema applies and `Dispatch deploy-hosted`
        # is skipped by its implicit `success()` — the mismatched pair #3627 exists to prevent —
        # with this suite GREEN. `_carries_consent` accepts an honoured value (`--yes=true`) and
        # refuses an inverted one (`--yes=false`) — rejecting only the second is the whole point.
        assert _carries_consent(_argv(cmd)), (
            "`supabase functions deploy` is consent-gated in CLI v3; without `--yes` the schema "
            "applies and the app half never flips — the mismatched pair #3627 exists to prevent"
        )
    # Consent is the OTHER half of the widened sweep, and until now the widening bought it for the
    # applier's own commands only: `_applier_commands()` reads the applier job ALONE, so a
    # consent-gated READ in a second job was GREEN with no `--yes` at all (#11135, third review) —
    # although `_CONSENT_GATED_ROOTS` exists precisely because v3 asks before a hosted READ too.
    # Asserted PER OCCURRENCE over the swept steps (the shape `test_printed_remediations_pass_yes_too`
    # uses), so reverting one of several sites cannot ride on a sibling's flag.
    for job_name, _job, step in _mutating_steps_with_job():
        for command in _commands_in(step):
            if not command.startswith(_CONSENT_GATED_ROOTS):
                continue
            assert _carries_consent(_argv(command)), (
                f"step {step.get('name')!r} in job {job_name!r} RUNS {command!r}: `db pull`, "
                f"`db diff` and `config pull` are consent-gated against a hosted project on CLI v3 "
                f"— the same gate that made every hosted mutation fail (#10896) — so without "
                f"`--yes` the step exits 1 or, where it swallows, reads green having pulled "
                f"nothing at all"
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
        "the workflow must still RUN a consent-gated hosted command — finding none means the sweep "
        "broke, and every assertion below would pass vacuously (#10896)"
    )
    def _applier_contract(job_name: str, job: dict, step: dict) -> bool:
        """The APPLY contract — asserted for every swept step that MUTATES production.

        ⛔ THE PREDICATE IS THE STEP, NOT THE JOB, AND NOT `_mutates_hosted` (#11135, fourth review).
        The first version of this gate tested `job_name in _applier_jobs()`, which is wrong in the
        dangerous direction: `_mutates_hosted` also requires a REMOTE TARGET selector, so
        `supabase db push --include-all --yes` (no `--linked`) in a second job is SWEPT but its job is
        not an applier — measured, `_mutates_hosted(...)` is False while the sweep is True — and the
        contract was skipped for a step that really mutates production (`db push` targets the LINKED
        project by default on the v2 pin; `functions deploy` targets it with no flag at all), so
        `continue-on-error: true` + `exit 0` on such a step was GREEN. Swapping in
        `_mutates_hosted(step)` does NOT close it — that function is False for the same untargeted
        forms, also measured — so the predicate is `_mutates_production`, whose only exemption is an
        EXPLICIT local target.

        Every assertion here is about an APPLY: a body that must BE its invocation so a failed apply
        fails, and an `if:` / `shell:` / `continue-on-error` / disabled-errexit that must not skip or
        mask one. Applying it to a step that only READS was MEASURED to red three legitimate shapes,
        and a guard that reds those teaches the cheapest escape — delete the read:

        * a snapshot job: `supabase db pull --linked --yes` then `git diff --exit-code
          supabase/migrations` (GitHub's default `bash -e` aborts on a failed pull, so the second
          line cannot mask one);
        * an informational `supabase db pull` with `continue-on-error: true`, in a job with no apply
          and no flip;
        * a `supabase db diff --linked --yes > /tmp/drift.sql` prepended to this repo's OWN
          warn-only `check-drift` step (`set +e` … `exit 0`, documented as "warn-only … not a
          failure").

        The failure-swallow checks are NOT gated this way — they stay on both halves, in the caller,
        and on the read half it is the CONTROL-OPERATOR scan that narrows to the swept INVOCATION
        (`set -n` / `trap … exit 0` stay whole-body on both halves, since neither can appear in a
        line that begins with `supabase `).
        """
        if not _mutates_production(step):
            return False
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
        ) and not _FALSE_OPERAND.search(str(step.get("if") or "").lower()), (
            f"{where} is gated on a condition carrying a false operand "
            f"({step.get('if')!r}): the step never runs, the job is GREEN, and "
            f"`Dispatch deploy-hosted` (#3627) fires over an unapplied schema. `if: false` was "
            f"closed in cycle 10; `&& false` is the same skip one operator away (measured, review "
            f"cycle 13)"
        )
        assert not step.get("shell"), (
            f"{where} overrides `shell:`. A mutating step must run under the runner's default "
            f"`bash -e`; `shell: bash -c 'exit 0'` takes the body as `$0` and exits 0 without running "
            f"it — a silent no-op that reads as a successful apply (measured, review cycle 9)"
        )
        assert job.get("if") is not False and not _FALSE_CONDITION.fullmatch(
            str(job.get("if") or "").lower()
        ) and not _FALSE_OPERAND.search(str(job.get("if") or "").lower()), (
            f"job {job_name!r} is gated on a condition carrying a false operand "
            f"({job.get('if')!r}): the whole job — including the app flip — is skipped and the run "
            f"reads GREEN, while nothing was applied (measured, review cycle 13)"
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
        body = "\n".join(_strip_shell_comment(line) for line in _run_body(step).splitlines())
        # A MUTATING STEP MUST BE ITS INVOCATION. `exit 0`, `exec true`, `return 1` and a bare
        # `:` are unconditional no-ops with no control operator, no trap and no `set +…` for any
        # of the checks below to see: the step exits 0 having applied nothing and the co-move
        # dispatch (#3627) fires over an unapplied schema — measured GREEN (review cycle 12), the
        # same effect as `set -n` but one sentence shorter. The body may hold its CLI invocation(s)
        # and errexit statements; anything else is a second statement, which is what this refuses.
        for raw_line in _run_body(step).splitlines():
            line = _command_text(raw_line)
            if not line:
                continue
            assert line.startswith("supabase ") or re.fullmatch(
                r"set\s+-[A-Za-z]+(?:\s+pipefail)?", line
            ), (
                f"{where} contains a command line that is not its CLI invocation ({line!r}): a "
                f"mutating step must BE its invocation. An unconditional `exit 0` / `exec true` / "
                f"`return 1` (or any other second statement) makes the step exit 0 having applied "
                f"nothing, with no control operator for the scan below to catch — the #3627 "
                f"inversion, measured green (#10896)"
            )
        assert not _ERRETIT_DISABLED.search(body), (
            f"{where} disables errexit (`set +e` / `set +o errexit`): the apply can then fail and "
            f"the step still exits 0, so the co-move dispatch fires over an unapplied schema "
            f"(#10896, #3627)"
        )
        return True

    for job_name, job, step in mutating:
        where = f"step {step.get('name')!r} in job {job_name!r}"
        # ONE predicate, one place: `_applier_contract` answers "did this step owe the apply
        # contract?" and that answer is what narrows the operator scan below. (An earlier version
        # tested the predicate in BOTH the caller and the callee, which made the callee's gate
        # unreachable and its deletion unobservable — measured on that commit's 15-test tree: 15
        # passed with the callee's gate removed.)
        applies = _applier_contract(job_name, job, step)
        # Comment lines are dropped first, exactly as `_printed_remedies` does: a comment is prose,
        # so the most natural note on this very step ("# NOTE: v3 consent-gates this; keep --yes")
        # must not be read as shell and red a correct step (measured, review cycle 9).
        whole_body = "\n".join(_strip_shell_comment(line) for line in _run_body(step).splitlines())
        # ⛔ `set -n` AND `trap … exit 0` ARE SCANNED ON THE WHOLE BODY, ON BOTH HALVES (#11135, fifth
        # review). They can never appear on a line that begins with `supabase `, so narrowing the
        # read-half scan to the invocation made them structurally invisible there — measured, the
        # first fix turned `set -n` + a pull, `set -en` + a pull and `trap 'exit 0' ERR` + a pull
        # from RED to GREEN. Only the CONTROL-OPERATOR scan is narrowed (below), because that is the
        # one a read step's ordinary shell (`if …; then`) false-REDs on.
        noexec = _disables_execution(whole_body)
        assert noexec is None, (
            f"{where} puts the body in noexec (`set -n` / `set -o noexec`): bash parses and executes "
            f"NOTHING and the step exits 0 — on the applier the apply never runs and the co-move "
            f"dispatch (#3627) flips the app over an unapplied schema; on a read-only step a drift "
            f"check checks nothing. Measured at the shell: `set -n` then `exit 3` exits 0, while "
            f"`set -e` then `exit 3` exits 3 — and `set -n` is ONE CHARACTER from the allowed "
            f"`set -e` (#10896)"
        )
        assert not _EXIT_ZERO_TRAP.search(whole_body), (
            f"{where} installs a `trap … exit 0`: that re-zeroes the command's exit status with no "
            f"control operator to catch (`set -e; trap 'exit 0' ERR; false` exits 0 — measured), so "
            f"a failed apply — and, on a read-only step, a failed pull — still reads green "
            f"(#10896, #3627)"
        )
        # ⛔ THE CONTROL-OPERATOR SCAN IS THE ONLY NARROWED ONE (#11135, fourth + fifth review). A
        # read step's body is ordinary shell — this repo's own `check-drift` is `set +e` … `if
        # [ "$rc" -eq 1 ]; then` … `exit 0`, whose `;` is not composition with the CLI call — so on
        # the read half the scan reads the swept INVOCATION (`supabase db pull --linked || true`). A
        # step that mutates production keeps the whole-body scan.
        operator_text = (
            whole_body
            if applies
            else "\n".join(
                _strip_shell_comment(command)
                for command in _commands_in(step)
                if command.startswith(_SWEPT_ROOTS)
            )
        )
        operator = _CONTROL_OPERATOR.search(
            _REDIRECTION.sub(" ", _QUOTED.sub('""', operator_text))
        )
        assert operator is None, (
            f"{where} composes its invocation with the shell control operator "
            f"{operator.group(0)!r}: that is how a failure is turned into a green run — `|| true`, "
            f"`|| exit 0`, `|| {{ …; exit 0; }}`, `| tee`, `&&`, `;`, a trailing `&`, or a `$( )` "
            f"that hides any of them. The step must BE its invocation and fail on its own exit "
            f"status; if you genuinely need an `echo` around it, put it in a previous step "
            f"(#10896, #3627)"
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
        # The operator's manual fallback for EXACTLY this incident: `supabase-deploy.yml:53` names it,
        # yet no test read it and `ci_selection.py --changed-files supabase/README.md` maps it to no
        # surface at all. Removing `--yes` from its remedies left the suite GREEN (measured, cycle 11).
        "supabase/README.md": REPO_ROOT / "supabase" / "README.md",
    }
    for name, path in governed.items():
        sites = _printed_remedies(path.read_text(encoding="utf-8"), _REPAIR_REMEDY)
        assert sites, (
            f"{name} must still print a `migration repair --status applied <version>` remedy: an "
            f"operator shown OUT OF ORDER versions needs a command to run (#1095). A silent "
            f"disappearance of the remedy must not pass vacuously."
        )
        # PARSED argv, never a substring: `--yes=false` parses as NO consent in the CLI, so a
        # substring assertion would bless a printed remedy that repairs nothing.
        lacking_repair = [site for site in sites if not _carries_consent(_argv(site))]
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
    lacking = [cmd for cmd in pushes if not _carries_consent(_argv(cmd))]
    assert not lacking, (
        f"{len(lacking)} of {len(pushes)} printed `supabase db push --linked` remedies lack `--yes` "
        f"({lacking}): CLI v3 consent-gates `db push` and cannot prompt, so the copy-paste apply an "
        f"operator takes from the gate applies nothing and says nothing (#10896)"
    )
    # The README's manual-fallback block is the OTHER printed apply path, and it selects its target
    # with `--project-ref` rather than `--linked`, so `_DB_PUSH_REMEDY` cannot see it (measured: it
    # stayed GREEN with `--yes` removed, review cycle 11). Its anchor is the COMMAND ROOT at the
    # start of a line (continuations joined): prose such as "First `supabase db push`: …" does not
    # start the line with the command, so documentation cannot false-RED and a remedy cannot hide
    # behind prose.
    readme = governed["supabase/README.md"].read_text(encoding="utf-8").replace("\\\n", " ")
    readme_remedies = [
        line.strip()
        for line in readme.splitlines()
        if line.strip().startswith(("supabase db push", "supabase functions deploy"))
    ]
    assert readme_remedies, (
        "supabase/README.md must keep its manual-fallback apply block: it is the operator's "
        "copy-paste path when the automated co-move has already failed (#3627)"
    )
    # Asserted on the STRIPPED command, not the raw line: `db push --project-ref …   # add --yes later`
    # kept a substring assertion satisfied while an operator copy-pasting it would hit the v3 consent
    # gate and apply nothing — the same shape closed for the workflow one branch above (measured,
    # review cycle 13).
    #
    # ⛔ ONE scanner, not two. The first attempt at this called a second helper,
    # `_strip_trailing_comment`, which anchored on the LAST quote in the line: a comment that itself
    # contained a quote (`# don't forget --yes`) came back UNCHANGED, so the assertion passed on text
    # no operator can run (measured GREEN, review cycle 14). `_strip_shell_comment` tracks quote STATE
    # as it scans, so it answers for every comment shape; keeping both was two owners of one predicate,
    # and that is how they diverged.
    unconsented = [
        remedy for remedy in readme_remedies if not _carries_consent(_argv(remedy))
    ]  # `_argv` strips the comment itself, so the two cannot disagree
    assert not unconsented, (
        f"supabase/README.md prints {len(unconsented)} manual-fallback command(s) without `--yes` "
        f"({unconsented}): CLI v3 consent-gates `db push` and `functions deploy` and cannot prompt "
        f"in a copy-paste, so the documented recovery fails exactly like the bug it recovers from "
        f"(#10896, #11107)"
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
    lacking = [cmd for cmd in printed if not _carries_consent(_argv(cmd))]
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
        # The COMMAND, not the banner (found by a fresh-context review of this file, 2026-11-06).
        # Two earlier forms were each measured GREEN over a broken remedy:
        #   1. a substring — `--yes=false` parses as NO consent and satisfied it;
        #   2. a word-boundary regex over the WHOLE banner — defeated by prose: delete `--yes` from
        #      the quoted command and add `--yes is required on CLI v3` to the same banner, and it
        #      still matched while the operator's paste repaired nothing.
        # `_argv` straight on the banner was ALSO measured to false-RED the correct banner (the
        # remedy sits inside prose, so shlex meets an unbalanced apostrophe). So: slice the quoted
        # command out of the banner, then parse THAT — prose is not code, and a mention is not a
        # remedy (the same doctrine `_printed_remedies` applies to the governed files).
        command = _remedy_command(banner, _BANNER_REPAIR)
        assert re.search(r"--status\s+applied", command), (
            "the drift banner must still print the `migration repair --status applied` command, not "
            "merely mention it: an operator shown OUT OF ORDER versions needs a command to run (#1095)"
        )
        assert _carries_consent(_argv(command)), (
            "the drift banner's printed remedy must carry `--yes`: `migration repair` against a "
            "hosted project is consent-gated in CLI v3 (docs → 'Migrating to Supabase CLI v3') "
            "and fails unattended without it, so the operator's copy-paste repairs nothing and "
            "says nothing (#10896)"
        )


def test_the_consent_predicate_refuses_inverted_yes_and_accepts_an_honoured_value() -> None:
    """The predicate itself, not only its call sites — both failure directions, both measured.

    A bare-token check (`"--yes" in argv`) rejects `--yes=true`, `--yes=1`, `--yes=yes`: spellings
    the CLI HONOURS (`--yes` accepts true|yes|on|1|y|false|no|off|0|n — measured against 2.110.0),
    so a future CORRECT edit to a value-bearing form would go RED — a false block on a green
    applier, which is a defect in its own right (found by a fresh-context review of this file).
    A substring check accepts `--yes=false`, which the CLI parses as NO. The predicate must refuse
    exactly the second and nothing else, and must never read "not proven" as "yes": an unrecognised
    value (`--yes=maybe`, `--yes=`) is not consent.
    """
    base = "supabase functions deploy deck-view --linked --project-ref x"
    for honoured in ("--yes", "--yes=true", "--yes=1", "--yes=yes", "--yes=on", "--yes=TRUE"):
        assert _carries_consent(_argv(f"{base} {honoured}")), honoured
    for inverted in ("--yes=false", "--yes=no", "--yes=off", "--yes=0", "--yes=", "--yes=maybe"):
        assert not _carries_consent(_argv(f"{base} {inverted}")), inverted
    assert not _carries_consent(_argv(base)), "no flag at all must never read as consent"
    # ⚠️ RESIDUAL, recorded rather than papered over: an argv token that IS the string `--yes`
    # because it is the VALUE of another flag (`--memo "--yes"`) is indistinguishable from the flag
    # once shlex has stripped the quotes — separating them needs per-flag arity knowledge, and it is
    # unreachable with the flag sets this file governs (`db push`, `functions deploy`,
    # `migration repair`, `secrets set` take no value-bearing flag that accepts `--yes`). Pinned as a
    # known limit so it is not re-discovered as a defect.
    assert _carries_consent(_argv(f'{base} --memo "--yes"')), "documented residual, not consent logic"


def test_the_drift_banner_remedy_is_the_COMMAND_not_prose_that_mentions_the_flag() -> None:
    """The banner site, attacked the way the review attacked it: flag moved from command to prose.

    Measured GREEN before this test existed: deleting `--yes` from the quoted remedy and adding the
    sentence "note that --yes is required on CLI v3" to the SAME banner left the suite green,
    because the assertion matched the whole banner. A printed remedy is the delimited COMMAND; a
    mention is not a remedy — the doctrine `_printed_remedies` already applies to the governed files,
    applied here to the one banner that carries its command inside prose.
    """
    banner = (
        "Migrations pending — resolve OUT OF ORDER versions first (forward migration, or "
        "'migration repair --linked --status applied <version>') and note that --yes is required "
        "on CLI v3 before the next app deploy."
    )
    command = _remedy_command(banner, _BANNER_REPAIR)
    assert re.search(r"--status\s+applied", command), "the slice must still be the repair command"
    assert not _carries_consent(_argv(command)), (
        "the flag lives only in prose here; a whole-banner match would bless a remedy that repairs "
        "nothing (#10896)"
    )


def test_an_undelimited_banner_remedy_does_not_inherit_the_prose_flag() -> None:
    """The same attack on a banner that does NOT quote its command (#11135).

    Measured GREEN before this test existed: `_remedy_command`'s undelimited fallback returned
    anchor-to-END, prose included, so deleting `--yes` from the command and mentioning it in the
    sentence after it still matched. The docstring called that fallback "the strictest reading
    available without quotes"; it was the opposite — fail-OPEN — and only the quoted dialect ever
    had a test. A clause cut keeps the sentence's flag out of the command.
    """
    banner = (
        "Migrations pending — resolve OUT OF ORDER versions first; a lane may run migration "
        "repair --linked --status applied <version> and note that --yes is required on CLI v3."
    )
    command = _remedy_command(banner, _BANNER_REPAIR)
    assert re.search(r"--status\s+applied", command), "the slice must still be the repair command"
    assert not _carries_consent(_argv(command)), (
        "the prose after the clause boundary mentions --yes; reading it as the command's flag is "
        "the fail-open this test closes (#11135)"
    )


def test_the_sweep_reaches_a_read_only_job_and_never_makes_it_the_applier(monkeypatch) -> None:
    """The WIDENING itself, pinned — without this a one-line revert to an applier-only sweep is GREEN.

    Measured (#11135, third review): substituting the pre-widening body and running every test in
    this module gave **11 passed**. So the false PASS the widening closed — a
    `supabase db pull --linked || true` step in a SECOND job, guarded by nothing — could come back
    by a one-line revert while `_mutating_steps_with_job`'s own docstring kept claiming the
    opposite, and this file pins every other hole it closes with a test. This one fails in three
    directions at once, which is what makes it a pin rather than a description:

    (a) the step is SWEPT, under its own job name — the widening itself;
    (b) `_applier_jobs()` is still exactly `['deploy']` — the widening must not leak into
        SELECTION (that was #11135's first hole: a read-only job reclassified as "the job that
        mutates the hosted project", which reds the suite naming the wrong cause and demands a
        pinned CLI from a job that only reads);
    (c) the sweep's failure-swallow assertion still FIRES on it — a consent-gated read composed
        with `|| true` is the green-but-pulled-nothing shape the widening exists to catch.
    """
    doc = copy.deepcopy(_workflow_doc())
    assert "schema-drift-check" not in (doc.get("jobs") or {})
    doc["jobs"]["schema-drift-check"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Pull the production schema",
                "run": "supabase db pull --linked --yes || true",
            }
        ],
    }
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)

    assert any(
        name == "schema-drift-check" and step.get("name") == "Pull the production schema"
        for name, _job, step in _mutating_steps_with_job()
    ), (
        "(a) a consent-gated READ in a second job must be swept: sweeping only the applier is how a "
        "`db pull --linked || true` drift job that pulls nothing reads green (#11135)"
    )
    assert [name for name, _ in _applier_jobs()] == ["deploy"], (
        "(b) the widening must not leak into SELECTION: a read-only job is not the job that mutates "
        "the hosted project, and selecting it reds the suite naming the wrong cause (#11135)"
    )
    with pytest.raises(AssertionError, match=re.escape("'|'")):
        test_the_apply_and_function_steps_pass_yes()


def test_read_only_jobs_are_swept_without_the_appliers_step_contract(monkeypatch) -> None:
    """The GATING, pinned in the direction that matters: legitimate read-only shapes stay GREEN.

    `_mutating_steps_with_job()` sweeps every job, but the APPLY contract ("the body IS its
    invocation", no `continue-on-error`, no `shell:`, no false `if:`, no disabled errexit) belongs
    to the steps that APPLY. Applying it to a swept READ was measured to red all three shapes below
    (#11135, third review). A guard that reds a correct read teaches the cheapest escape — delete
    the read — which restores exactly the blindness the widening ended, so the false RED is pinned
    as carefully as the false PASS:

    1. a snapshot job: `supabase db pull --linked --yes` then `git diff --exit-code …` (GitHub's
       default `bash -e` aborts on a failed pull, so the second line cannot mask one);
    2. an informational pull with `continue-on-error: true`, in a job with no apply and no flip;
    3. a `db diff` prepended to **this repo's own `check-drift` body, verbatim** — `set +e`, `rc=$?`,
       `if [ "$rc" -eq 1 ]; then` … `exit 0`. ⛔ The first version of this test used a three-line
       stand-in (`db diff` / `set +e` / `exit 0`) and therefore passed while the REAL body still red,
       because `if …; then` contains a `;` and the swallow scan ran over the whole shell body
       (#11135, fourth review). The scan now reads the swept INVOCATION on the read half, so the
       real shape is what is pinned here.
    """
    doc = copy.deepcopy(_workflow_doc())
    doc["jobs"]["schema-snapshot"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Snapshot the production schema",
                "run": "supabase db pull --linked --yes\ngit diff --exit-code supabase/migrations",
            }
        ],
    }
    doc["jobs"]["catalog-report"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Informational pull",
                "continue-on-error": True,
                "run": "supabase db pull --linked --yes",
            }
        ],
    }
    doc["jobs"]["warn-only-diff"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Check migration drift (warn-only, like check-drift)",
                # ⛔ The REAL `check-drift` body, not a stand-in: its `if [ "$rc" -eq 1 ]; then`
                # carries a `;`, which a whole-body control-operator scan reads as composition.
                "run": (
                    "supabase db diff --linked --yes > /tmp/drift.sql\n"
                    "set +e\n"
                    "bash .github/scripts/check-migration-drift\n"
                    "rc=$?\n"
                    "set -e\n"
                    "if [ \"$rc\" -eq 1 ]; then\n"
                    '  echo "::warning::Migrations pending"\n'
                    "  exit 0\n"
                    "fi\n"
                    'exit "$rc"\n'
                ),
            }
        ],
    }
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)

    # The subject is unchanged (the real applier is still the only one), so the whole guard — apply
    # contract plus swallow checks — must pass over a workflow carrying three legitimate reads.
    assert [name for name, _ in _applier_jobs()] == ["deploy"]
    test_the_apply_and_function_steps_pass_yes()


def test_an_untargeted_mutating_step_still_owes_the_apply_contract(monkeypatch) -> None:
    """The predicate is the STEP's mutation, not the JOB's membership — the hole the first fix opened.

    Measured (#11135, fourth review): keying the apply contract on `job_name in _applier_jobs()` let
    a second job's `supabase db push --include-all --yes` — no `--linked`, so `_mutates_hosted` is
    False and that job is NOT an applier, while the sweep still reaches the step — carry
    `continue-on-error: true` and `exit 0` GREEN (it was RED at the parent head). That is a real
    production mutation, not a read: on the v2 pin `db push` targets the LINKED project by default
    (the CLI warns that the local default is a future major), and `functions deploy` targets the
    linked project with no target flag at all.

    Both rows were measured RED before the gate existed and GREEN under the membership predicate;
    they must stay RED.
    """
    for name, command in (
        ("untargeted-push", "supabase db push --include-all --yes"),
        ("untargeted-function", "supabase functions deploy tenant-provision --no-verify-jwt --yes"),
    ):
        doc = copy.deepcopy(_workflow_doc())
        doc["jobs"][name] = {
            "runs-on": "ubuntu-latest",
            "steps": [
                {
                    "name": f"{name} step",
                    "continue-on-error": True,
                    "run": f"{command}\nexit 0",
                }
            ],
        }
        monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda doc=doc: doc)
        # The three premises that made the membership predicate miss it.
        assert _mutates_production({"run": command}) is True, "premise: mutates production"
        assert _mutates_hosted({"run": command}) is False, "premise: no remote target selector"
        assert [n for n, _ in _applier_jobs()] == ["deploy"], "premise: not the applier job"
        assert any(n == name for n, _j, _s in _mutating_steps_with_job()), "premise: swept anyway"
        with pytest.raises(AssertionError, match="continue-on-error"):
            test_the_apply_and_function_steps_pass_yes()


def test_a_local_by_default_mutation_is_not_a_production_apply(monkeypatch) -> None:
    """`db reset` / `migration up` / `migration down` / `migration squash` are LOCAL by default.

    Measured by RUNNING the pinned v2 CLI in an unlinked directory — not from help prose, which is
    how the first version of `_LOCAL_BY_DEFAULT_ROOTS` acquired a fifth member it should not have
    had: `migration up` and `db reset` print "Connecting to local database" / "supabase start is not
    running", `migration squash` reports `"isLocal":true`, and each root's REMOTE form is `--linked`
    / `--project-ref` / `--db-url`. Treating those as production would put the applier's
    body-IS-its-invocation rule on a local harness step — `supabase db reset` then `supabase start`,
    the shape `_applier_jobs`' docstring anticipates for #11100 — and red a correct workflow, which
    is the false-RED class this file spent four cycles removing.

    ⛔ `migration repair` is NOT one of them and is asserted the other way (#11135, sixth review):
    bare, it answers `ProjectRefNotLinkedError` ("Cannot find project ref"), so it addresses the
    LINKED project, and the repo's own printed remedy passes `--linked`. It sat in this tuple for one
    commit, which made a production repair with `continue-on-error: true` read GREEN.

    The remote form must still be production: `db reset --linked` mutates the hosted database, so a
    swallow on it has to red.
    """
    doc = copy.deepcopy(_workflow_doc())
    doc["jobs"]["local-harness"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "Reset the local stack and start it",
                "run": "supabase db reset --yes\nsupabase start",
            }
        ],
    }
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)
    # ALL FOUR tuple members are asserted, not just `db reset`: the first version of this tuple had a
    # fifth member (`migration repair`) that is REMOTE by default, and pinning only one entry is what
    # let that survive (#11135, sixth review). Each of these was RAN against the pinned CLI.
    for local_root in (
        "supabase db reset --yes",
        "supabase migration up",
        "supabase migration down --last 1",
        "supabase migration squash",
    ):
        assert _mutates_production({"run": local_root}) is False, local_root
        assert _mutates_production({"run": f"{local_root} --linked"}) is True, local_root
    # ⛔ ...and `migration repair` is NOT local-by-default: bare, it answers ProjectRefNotLinkedError
    # (`Cannot find project ref`), so it addresses the linked project. Measured, and it is the shape
    # that reads green if the tuple is trusted from help prose instead.
    assert _mutates_production({"run": "supabase migration repair --status applied 1 --yes"}) is True
    assert _mutates_production({"run": "supabase db reset --yes"}) is False
    assert _mutates_production({"run": "supabase db reset --linked --yes"}) is True
    assert _mutates_production({"run": "supabase db push --include-all --yes"}) is True
    test_the_apply_and_function_steps_pass_yes()

    doc["jobs"]["deploy"]["steps"].append(
        {
            "name": "Repair migration history",
            "continue-on-error": True,
            "run": "supabase migration repair --status applied 20240101000000 --yes\nexit 0",
        }
    )
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)
    with pytest.raises(AssertionError, match="continue-on-error"):
        test_the_apply_and_function_steps_pass_yes()

    # Drop the repair step again, so the next scenario fails on ITS defect and not this one.
    doc["jobs"]["deploy"]["steps"].pop()
    doc["jobs"]["deploy"]["steps"].append(
        {"name": "Reset the linked project", "run": "supabase db reset --linked --yes || true"}
    )
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)
    with pytest.raises(AssertionError, match="control operator"):
        test_the_apply_and_function_steps_pass_yes()


def test_a_read_only_step_is_still_scanned_for_noexec_and_the_exit_zero_trap(monkeypatch) -> None:
    """`set -n` and `trap … exit 0` are scanned on the WHOLE body, on BOTH halves.

    Measured (#11135, fifth review): narrowing the read-half scan to the swept invocation made both
    mechanisms structurally invisible there — neither can appear on a line that begins with
    `supabase ` — so `set -n` + a pull, `set -en` + a pull and `trap 'exit 0' ERR` + a pull went from
    RED (parent) to GREEN. Only the CONTROL-OPERATOR scan narrows; these two must not.
    """
    for name, body in (
        ("read-noexec", "set -n\nsupabase db pull --linked --yes"),
        ("read-noexec-flag", "set -en\nsupabase db pull --linked --yes"),
        ("read-trap", "trap 'exit 0' ERR\nsupabase db pull --linked --yes"),
    ):
        doc = copy.deepcopy(_workflow_doc())
        doc["jobs"][name] = {
            "runs-on": "ubuntu-latest",
            "steps": [{"name": f"{name} step", "run": body}],
        }
        monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda doc=doc: doc)
        with pytest.raises(AssertionError, match=r"noexec|trap"):
            test_the_apply_and_function_steps_pass_yes()


def test_a_read_only_step_must_still_carry_consent(monkeypatch) -> None:
    """The union's CONSENT half, pinned — the widening is worthless if only its swallow half holds.

    Measured GREEN before this existed (#11135, third review): a third job running
    `supabase db pull --linked` with no `--yes` at all passed every test in this module, because
    the only `--yes` source was `_applier_commands()` — the applier job ALONE — and the swept
    steps were never consent-checked. The swallow checks cannot catch it either: an unconsented
    pull is not composed with an operator, it simply exits 1 (or, in a job that tolerates the
    failure, reports nothing and reads green) — which is why `_CONSENT_GATED_ROOTS` exists.
    """
    doc = copy.deepcopy(_workflow_doc())
    doc["jobs"]["schema-drift-check"] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {"name": "Pull the production schema", "run": "supabase db pull --linked"}
        ],
    }
    monkeypatch.setattr(sys.modules[__name__], "_workflow_doc", lambda: doc)
    with pytest.raises(AssertionError, match="consent-gated"):
        test_the_apply_and_function_steps_pass_yes()
