# tests/test_12051_ci_pg_client_matches_server.py
"""#12051: the record drill's PostgreSQL **client** major must equal the servers' major.

The D8 drill dumps the record with `pg_dump` and restores it with `pg_restore`,
and `tortoise/record_backup.py` resolves BOTH from `PATH` (the parameters are
the bare names — `pg_dump: str = "pg_dump"`). The archive is not
version-neutral: `pg_dump` writes a preamble the RESTORE SERVER must be able to
execute, so a client whose major is newer than the restore target produces an
artifact that raises on its own header.

Measured 2026-11-12, job `130998952949` (PR #12030, head `3408c76c2b`):

    pg_restore: error: could not execute query: ERROR:  unrecognized
      configuration parameter "transaction_timeout"
    Command was: SET transaction_timeout = 0;

`transaction_timeout` is a **PostgreSQL 17** GUC, the runner's inherited client
was 17, and both provisioned servers are `pgvector/pgvector:pg16`. Nothing in
the diff caused it, and the message reads as a defect in the record path — which
is why the pin is ASSERTED in the job (`pg_dump --version` / `pg_restore
--version`, `::error::` + `exit 1`) rather than merely hoped for.

This guard pins SIX things about `test-record-postgres`, none of which the
other CI-surface guards cover:

1. **The two image pins agree** (the record server and D8's scratch server must
   be one major — the drill restores across them).
2. **The client pin names that same major**, and prepends its versioned
   `/usr/lib/postgresql/<major>/bin` to `$GITHUB_PATH` — an unversioned install
   would leave the runner's own, newer client first on `PATH`.
3. **Nothing else in the job puts another client major on the leg's PATH** —
   not a later `$GITHUB_PATH` write (job-scoped, last writer wins) and not the
   leg's own `export PATH=…` (a step-script assignment beats the prepend).
4. **The pin fails closed**, as an ARMED branch: a single `if [ "$major" !=
   "<major>" ]; then … fi` unit that announces and stops, in a step that can
   actually run and actually red the job (no `if:`, no `continue-on-error`) —
   and that is not sitting inside a block whose condition is a literal
   constant, which is present and inert at the same time. The "actually run
   and actually red" half is asserted at BOTH scopes (a JOB-level
   `continue-on-error: true` disarms every step, a JOB-level `if:` this guard
   cannot read can skip the whole job), the branch must be INSIDE the loop that
   covers the tools (a dedented `if … fi` asserts only the LAST tool), and its
   status must be OBSERVABLE.
5. **The pin runs BEFORE the leg**, because `$GITHUB_PATH` only affects
   subsequent steps: a pin placed after the pytest step is inert, and the job
   would red for a reason no diff explains.
6. **The refusal of an unreachable block fires on exactly the never-runs
   forms and on nothing else** (`REFUSAL_POLARITY` /
   `test_the_refusal_polarity_is_never_runs`). A scan that over-refuses reds a
   correct workflow, which is as damaging as one that under-refuses: the first
   cut here refused every bare constant, so `while true; do … break; done` (the
   ordinary retry loop) and `cmd && true || fallback` were reported as dead code
   that runs.

A behavioural test of the mismatch itself would need two live servers and two
client majors, so the assertion is made where the version is decided: the
workflow. Reproduced by hand (documented on #12051): a PG17 client dumping into
`pgvector/pgvector:pg16` fails with exactly the error quoted above, and the same
server's own PG16 client dumps and restores clean.

WHAT THIS GUARD CANNOT SEE — read this before trusting a green run.

This is a HERMETIC TEXT GUARD: it reads `.github/workflows/python-ci.yml` and
nothing else, and it cannot execute what it reads. Nine review rounds each closed
one way a text search could be satisfied by a different spelling of the same act
(a comment, a second write, a disjunction, an unread one, an `echo`-only
producer, a nested branch, the leg's own PATH export, a non-literal client dir,
a binary replaced out of the pinned directory). The class that REMAINS open is
not a tenth spelling — it is the one a text guard cannot reach:

- **Dead code reads as live.** A `$GITHUB_PATH` write (or the fail-closed
  branch) inside a `: <<'EOF'` heredoc, or inside a guard whose condition this
  module cannot read as a literal — `if [ 1 -eq 2 ]; then`, a
  runtime-derived test — is present in the text, so the rows that require it
  stay green although nothing is appended. Deciding whether a shell line
  EXECUTES needs a shell, not a regex.

  ⛔ The BARE-CONSTANT spellings are no longer in this class (#12069):
  `_unreachable_guards()` refuses an enclosing `if`/`elif`/`while` + `false`
  (or `! true`), `until` + `true` (or `! false`), `false && …`, and a
  statement-position `true || …` over `[pin, leg)` — and refuses NOTHING else,
  because `if true; then`, `until false; do`, `if ! false; then` and
  `while true; do … break; done` all RUN. Refusing those is a false RED, which
  is the same defect class as passing a broken row; `REFUSAL_POLARITY` below
  pins both directions as committed test cases. The compensating control for
  whatever is still unreachable is the pin's own in-job assertion: it prints
  `pg_dump --version`, so a pin that never took effect is visible in the log of
  the step that tried.
- **A path built at runtime is invisible.** `b=/usr/lib/postgresql; ln -sf
  "$b/17/bin/pg_dump" …/16/bin/pg_dump` names no major this guard can read. What
  IS refused is the mutating VERB carrying a client binary name, at or after the
  pin, however the source is spelled — so the plausible form is loud even when
  the arithmetic is not. The same applies to the runner's own command-file
  directory (`_runner_file_commands`): naming it is refused outright, but a
  directory reached through yet another variable is not.
- **A directory derived rather than spelled** (`ls -d /usr/lib/postgresql/*/bin
  | sort -V | tail -1`) is caught only because the rule treats a client dir it
  cannot read as an offender; the same derivation hidden inside an invoked
  script is not.
- **An invoked script or a `uses:` step is read as one line.** `run: bash
  x.sh` is that one line, and `uses: ./.github/actions/x` is not read at all
  — a composite action that appends to `$GITHUB_PATH` moves the leg's PATH
  with every row green (measured, round 10). This job already runs four
  composite actions; none touches PATH, and that is the invariant to keep.
  No in-repo script this job calls names or moves a client dir — kept that way, because
  this guard cannot follow one.
- **`shell:` is read at TWO scopes only** — the step's, and the job's
  `defaults.run.shell` (the workflow-level `defaults` is folded into the
  job because it applies here too). A `shell:` reached through a
  reusable workflow (`uses: ./.github/workflows/x.yml`) is outside this
  file and outside this guard.
- **`env:` and `$GITHUB_ENV` are read at file scope only (#12069 review P1).**
  `env:` was previously not read AT ALL — a job- (or workflow-) level
  `env: PATH: /usr/bin:/bin` left the whole file green while the leg resolved
  `pg_wrapper` → the newest client, i.e. #12051 restored in three lines
  (measured, `67 passed`). It is read now, at workflow, job and step scope, and
  `$GITHUB_ENV` — the second command file, which sets a variable for later steps
  the same way — is refused by NAME, because nothing in this job uses it (the
  same polarity as `_runner_file_commands`). What remains outside: an `env:` set
  by an INVOKED script (`run: bash x.sh`), by a composite action this job calls,
  or by a reusable workflow this job calls — the invoked-script blind spot
  above, in one more mechanism.
- **A loop whose list is a VARIABLE is refused, not resolved.** Any `$` in the
  list (`for tool in $TOOLS`, `${TOOLS:-a b}`) names nothing this guard can
  count, so the row reds with a message naming the accepted form (the literal
  tool names). Reading the variable failed in the FAIL-OPEN direction every time
  (#12069 reviews 14-16, each measured): the module's own tuple is not the
  shell's value; the code's first assignment is not its last
  (`TOOLS="a b"; TOOLS=a` iterates ONE tool); a conditional assignment may not
  run; and a DEFAULT is a literal the guard would credit while the shell
  iterates something else.
- **A JOB-level `if:` is accepted only in enumerated shapes** — a non-skipping
  condition, or `always() && needs.<id>.result == 'success'` with `<id>` in this
  job's own `needs:`. Any other job-level condition is reported, because its
  POLARITY is not readable from text: `needs.x.result == 'failure'` and
  `needs.x.outputs.docs_only != 'true'` are false on the healthy path, so the
  job would silently not run.
- **A PATH entry's PRECEDENCE is read from the value, not from the shell.**
  `_keeps_the_pin_first` requires the pinned dir (or the inherited `$PATH`) to be
  the FIRST entry, so a prepend is an offender. A value BUILT at runtime is
  refused too, but for a blunt reason rather than the right one: the value regex
  stops at the first space, so `PATH="$(dirname "$(command -v pg_dump)"):$PATH"`
  reads as the first entry `$(dirname` and is an offender. That is the safe
  direction (a derived prepend is refused, not blessed), and it is why this is a
  stated limit rather than a blind spot: what is genuinely invisible is a PATH
  moved WITHOUT a first entry of its own — an inherited `PATH` changed by an
  invoked script, or by a composite action's OWN body. (The YAML `env:` this job
  attaches to a `uses:` step IS read: `_env_path_replacements` reads every step's
  `env`, `uses:` steps included. #12069 round-15 review 2 caught an earlier draft
  of this bullet claiming otherwise — a stated limit the code contradicted.) The
  in-job `pg_dump --version` assertion is what makes a surviving blind spot
  visible in the log.
- **The mutating-verb rule costs some precision.** The client name is
  read only AFTER the verb, so `rm -f "$RUNNER_TEMP/pg_dump.log"` and
  `pg_dump --version | tee -a x` are not replacements — but a genuinely
  read-only command whose verb IS in the list (`rm`, `tee`, `install`,
  `chmod`) and which names a client binary after it is refused. That is
  the right polarity here: nothing legitimate rewrites those binaries,
  and the message names the accepted form. A REDIRECTION is graded
  separately (`REDIRECT_TARGET_RE`), because it writes a file with no verb at
  all: `: > /usr/lib/postgresql/16/bin/pg_dump` rewrote the pinned binary with
  every row green until #12069 review P1, and ``>`/`>>`/`| tee` into a client
  binary or a client `bin/` is now refused however the producer is spelled.
- **The extraction's arithmetic** (`sed -E 's/.*/16/'`) cannot be proven; the
  in-job assertion is what makes a constant visible.

So: a green run means "no plausible spelling of the regression is in this
text", not "the job provably installs and uses pg16". The job's own assertion is
the part that observes reality.
"""
from __future__ import annotations

import copy
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CI = ROOT / ".github" / "workflows" / "python-ci.yml"
JOB = "test-record-postgres"

#: `pgvector/pgvector:pg16` -> 16, and a plain `postgres:16` -> 16. The DIGEST is
#: stripped before matching (#12051 review round 12): `pgvector/pgvector:pg16@sha256:…`
#: is the strictly SAFER form of the same pin, and the `$` anchor refused it with
#: a message saying it did not name its major, which it does (measured, four rows
#: red). See `_postgres_image_majors`, which matches the reference without it.
#: `pgvector` prefix is not incidental: the record's vector migration needs the
#: extension (#11642), so a plain `postgres:NN` pin cannot substitute for one of
#: these — but reading BOTH forms keeps this guard's message about the major
#: rather than about the spelling.
#: Prefer a `pg<NN>` group anywhere in the tag, fall back to the leading digits —
#: `16`, `16.4`, `16.4-bookworm`, `pg16`, `pg16-trixie`, `0.8.0-pg16` all read 16.
#: The `$`-anchored original refused `postgres:16.4` and `0.8.0-pg16` with a
#: message saying they do not name a major, which they do (#12051 round 13).
IMAGE_RE = re.compile(r":(?:[A-Za-z0-9._-]*[-_.])?pg(\d+)(?:$|[-_.])|:(\d+)(?:$|[-_.])")

#: The CLIENT package as INSTALLED, not merely NAMED (#12051 review round 8):
#: the name alone is matched by a read-only diagnostic (`dpkg -l
#: postgresql-client-16 | tail -2`), which made the "exactly ONE pin step" rule
#: red four rows and call a correct tree a second installer (measured). An
#: install verb on the same line is what makes a step the pin.
#: Flags between the manager and the verb are ORDINARY (#12051 review round 12):
#: `apt-get -y install …` is the same command as `apt-get install -y …`, and the
#: first spelling made `_pin_step` find ZERO installers and raise "a second
#: installer makes the client whichever one ran last" about a tree with exactly
#: one correct installer (measured, four rows red).
CLIENT_PKG_RE = re.compile(
    r"(?:apt-get|apt|aptitude)\s+(?:-\S+\s+|--\S+\s+)*"
    r"(?:install|reinstall)[^\n]*?postgresql-client-(\d+)\b"
)

#: The runner's own command-file directory (#12051 review round 9): `$GITHUB_PATH`
#: is a PATH into this dir whose NAME carries a per-run guid, so a step that
#: globs it can append without ever mentioning `GITHUB_PATH` — measured GREEN
#: (`for f in "$RUNNER_TEMP"/_runner_file_commands/add_path_*; do echo /usr/bin
#: >> "$f"; done`). Nothing legitimate in this job names the directory, so the
#: name itself is refused.
RUNNER_FILE_COMMAND_RE = re.compile(r"_runner_file_commands")

#: A shell PATH ASSIGNMENT (#12051 review round 12). `_client_dir_mentions` only
#: refuses a line that NAMES a versioned client dir, so
#: `export PATH="/usr/local/bin:/usr/bin:/bin"` looked innocent — it names no
#: client dir — while `/usr/bin/pg_dump` is `pg_wrapper`, which this file's own
#: docstring says resolves to the image's NEWEST client (17). All five rows stayed
#: GREEN with the leg on pg17 (measured).
#: ⛔ THE INVARIANT IS PRECEDENCE, NOT CONTAINMENT (#12069 review P1): an
#: assignment keeps the pin only when the pinned dir (or the inherited `$PATH`)
#: is its FIRST entry. The earlier reading accepted any value that MENTIONED
#: either, so `export PATH="/usr/bin:$PATH"` — the pin on SECOND place, where a
#: `PATH` lookup never reaches it — stayed GREEN and restored #12051 (measured).
#: See `_keeps_the_pin_first`.
PATH_ASSIGN_RE = re.compile(r"\bPATH\s*=\s*(?P<quote>[\"']?)(?P<value>[^\s;\"']*)")

#: A command that REPLACES a binary rather than naming one (#12051 review round
#: 9). The pinned dir can be rewritten after the pin, and "may the job NAME
#: another major" cannot see it when the base path arrives in a variable:
#: measured GREEN, restoring #12051 —
#: `b=/usr/lib/postgresql; sudo ln -sf "$b/17/bin/pg_dump" "$b/16/bin/pg_dump"`
#: and a `find`-derived `cp "$src" …/16/bin/pg_dump`. Keying on the BINARY NAME
#: plus a mutating verb is independent of HOW the source path is spelled, which
#: is the axis that was open.
BINARY_MUTATION_RE = re.compile(
    r"\b(?:"
    r"ln|cp|mv|rm|unlink|chmod|chown|patch|tee|dd|"
    r"update-alternatives|install|sed[ \t]+-i|dpkg[ \t]+-i|perl[ \t]+-i"
    r")\b"
)
#: Only a binary NAME, never a filename that contains one: the `(?![\w.])` is
#: what keeps `rm -f "$RUNNER_TEMP/pg_dump.log"` (a tidy step) and
#: `pg_dump --version | tee -a "$RUNNER_TEMP/v.txt"` (the guard's OWN recommended
#: compensating control) from reading as replacements — both measured RED, round
#: 10. The call site also reads the name only AFTER the verb.
BINARY_NAME_RE = re.compile(r"\b(?:pg_dump|pg_restore|pg_ctl|pg_wrapper)(?![\w.])")

#: A REDIRECT whose TARGET is a client binary or the pinned bin directory
#: (#12069 review P1, measured at `5de9f4b5d7`). The mutating-verb rule above
#: needs a VERB, and a shell's ordinary way of writing a file is `>` — no verb at
#: all. So the same act was REFUSED through `tee`/`cp` and GREEN through `>`:
#:
#:     printf '#!/bin/sh\nexec /usr/bin/pg_dump "$@"\n' > /usr/lib/postgresql/16/bin/pg_dump
#:     : > /usr/lib/postgresql/16/bin/pg_dump
#:     echo boom > /usr/lib/postgresql/16/bin/pg_dump
#:
#: every row green (`171 passed`), while the identical bytes through
#: `| sudo tee …` / `cp` red `test_no_step_rewrites_the_client_path_to_another_major`.
#: The first line is the full #12051 regression: `…/16/bin/pg_dump` becomes a
#: wrapper that execs `/usr/bin/pg_dump` = `pg_wrapper` → the image's newest
#: client — and the in-job `pg_dump --version` assertion cannot see it, because
#: that lives in the PIN step, which runs BEFORE a later step can rewrite the
#: binary. The TARGET is what makes this narrow (`nothing legitimate redirects
#: INTO the pinned bin dir`), so the polarity is the mutating-verb rule's: refuse
#: the shape loudly rather than try to prove the writer's intent.
#:
#: `>`, `>>` and `>|` are the redirections that WRITE; `| tee [-a]` is the other
#: spelling and is matched so the two cannot disagree. A redirect to anything
#: else (`$GITHUB_PATH`, `$RUNNER_TEMP/…`, `/dev/null`, a `pg_dump.log`) is
#: spared — `BINARY_NAME_RE`'s `(?![\w.])` keeps `pg_dump.log` out, which is the
#: same reason the tidy step and the guard's own `| tee -a "$RUNNER_TEMP/v.txt"`
#: compensating control stay green.
REDIRECT_TARGET_RE = re.compile(
    r"(?:>>?\|?|\|\s*(?:sudo\s+)?tee\s+(?:-a\s+)?)\s*(?P<quote>['\"]?)"
    r"(?P<target>[^\s;&|'\"]+)"
)


#: The runner's own COMMAND FILES are the only redirect targets whose basename
#: is a bare expansion that is not an offence: each is governed by its own
#: classified-write rule (`$GITHUB_PATH` by the prepend unit, `$GITHUB_ENV` by
#: `GITHUB_ENV_MENTION_RE`), so an exemption here cannot hide a write.
COMMAND_FILE_TARGET_RE = re.compile(r"^\$\{?(?:GITHUB_PATH|GITHUB_ENV)\}?$")


def _redirects_into_a_client_binary(line: str) -> bool:
    """Does this line WRITE OVER a client binary by REDIRECTION? (#12069 P1)

    A redirection needs no verb, so the rule above cannot see it. A target is an
    offence when it is a client BINARY NAME, a path under a versioned client
    `bin/` — the pinned directory itself, which nothing legitimate writes into —
    or a BARE EXPANSION this guard cannot classify (`f=…/pg_dump; : > "$f"`).

    ⛔ THE VARIABLE TARGET IS NOT OPTIONAL (#12069 review round 2, measured):
    grading only the LITERAL target left the whole class open, which is exactly
    the fail-open rounds 6-8 closed for `$GITHUB_PATH`:

        f=/usr/lib/postgresql/16/bin/pg_dump; : > "$f"            185 passed
        : > "$(printf /usr/lib/postgresql/16/bin/pg_dump)"         185 passed

    The polarity is this file's own doctrine for a loop whose list is a VARIABLE
    — refuse what cannot be read rather than credit it. The exemption is the two
    runner COMMAND FILES, each already governed by its own classified-write rule;
    the shipped job's other redirect targets have literal basenames
    (`/dev/null`, `$SCRATCH/pytest.log`, `$SCRATCH/pytest-rc`), so this refuses
    the unclassifiable target and changes nothing about the real workflow
    (measured: 187 passed before and after, and `$GITHUB_PATH` still green).

    Read from EVERY redirect on the line, not the first: `a > ok b > …/pg_dump`
    is one line.
    """
    for match in REDIRECT_TARGET_RE.finditer(line):
        target = match.group("target")
        basename = target.rsplit("/", 1)[-1]
        if BINARY_NAME_RE.fullmatch(basename):
            return True
        if CLIENT_DIR_RE.search(target):
            return True
        if basename.startswith("$") and not COMMAND_FILE_TARGET_RE.match(basename):
            return True
    return False

#: The `$GITHUB_PATH` write as ONE unit: the versioned dir IS the value written.
#: `echo <dir> >> "$GITHUB_PATH"` — read the major from the
#: write line itself, never from an `export` elsewhere in the step.
GITHUB_PATH_PREPEND_RE = re.compile(
    r"echo\s+(?P<quote>['\"]?)/usr/lib/postgresql/(?P<major>\d+)/bin(?P=quote)"
    r"\s*>>\s*['\"]?\$\{?GITHUB_PATH\}?['\"]?"
)

#: ANY mention of `GITHUB_PATH` in the job's code (#12051 review rounds 6, 7
#: and 8). Round 6 detected the WRITE (`>`, `>>`, `tee`) and still missed both a
#: file-name indirection (`gp="$GITHUB_PATH"; echo … >> "$gp"`) and a
#: backslash-continued redirect (`>> \` then `"$GITHUB_PATH"`), each measured
#: GREEN while the leg resolved pg17. Round 7 matched `$GITHUB_PATH`; round 8
#: measured that dropping the `$` is not enough either — `printf … >> "$(printenv
#: GITHUB_PATH)"` and `n=GITHUB_PATH; … >> "${!n}"` were both GREEN. The token
#: WITHOUT a sigil is therefore the population: every mention must BE the one
#: classified write, and any mention the guard cannot classify is refused.
#: Measured against the source: this job's only mention is the pin's own append,
#: so the rule costs nothing.
#:
#: STATED LIMIT: this reads the job's inline `run:` script text (with comment
#: lines and trailing comments stripped, and backslash continuations joined). It
#: does NOT follow an invoked script — `run: bash .github/scripts/x.sh` where
#: `x.sh` appends to `$GITHUB_PATH` is invisible, and no in-repo script this job
#: calls does that today (`grep -n GITHUB_PATH .github/scripts/pytest-attribution.sh
#: .github/scripts/record-postgres-leg-gate.py` → none).
GITHUB_PATH_MENTION_RE = re.compile(r"\bGITHUB_PATH\b")

#: A versioned client directory named ANYWHERE in the job's code (#12051 review
#: rounds 7 and 8): the `$GITHUB_PATH` write is not the only way to put a client
#: on the leg's PATH — the LEG's own script can `export
#: PATH="/usr/lib/postgresql/17/bin:$PATH"` and, because the step's own
#: assignment runs after the runner composes the prepend (actions/runner
#: `Handler.AddPrependPathToEnvironment` → `ScriptHandler`), that export WINS.
#: Measured GREEN before this rule, with the in-job `pg_dump --version`
#: assertion still printing 16 because it lives in the PIN step. So no `run:`
#: line of this job — nor the `shell:` it is executed through — may name a
#: versioned client dir of another major.
#: ⛔ THE TOKEN IS NOT REQUIRED TO BE DIGITS (#12051 review round 8): matching
#: `\d+` let the leg derive the directory instead of spelling it —
#: `export PATH="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1):$PATH"`
#: and `…/1"7"/bin` were both measured GREEN. A token this guard cannot read as
#: a major is an offender in its own right, exactly like an unclassifiable write.
CLIENT_DIR_RE = re.compile(r"/usr/lib/postgresql/(?P<token>[^/\s]+)/bin")

#: The fail-closed branch as ONE unit (#12051 review rounds 4, 6 and 7): the
#: `if` line that compares `$major`, its body, and its `fi`. "`::error::` and
#: `exit 1` appear somewhere in the step" is NOT the claim — `if false && [
#: "$major" != "16" ]; then` keeps both tokens in the text and can never fire.
#: ⛔ THE BODY MAY NOT CONTAIN NESTED SHELL STRUCTURE. It used to stop at the
#: FIRST `fi`, so a nested `if false; then echo ::error::…; exit 1; fi` inside
#: the branch satisfied both tokens while the outer branch could never be
#: entered — round 4's disjunction one level down (measured GREEN, round 6).
#: Refusing the structure lines rather than counting nesting keeps this a text
#: guard: the branch must be FLAT, and a message that merely says "if" is not a
#: structure line.
#: ⛔ AND THE STOP IS ANCHORED, not a substring (#12051 review round 7):
#: `false && exit 1` and `bail() { exit 1; }` both CONTAIN the token while the
#: branch announces and continues — measured GREEN, step exit 0, leg on the
#: inherited client. `FAIL_CLOSED_ANNOUNCE_RE` / `_STOP_RE` below read whole
#: lines for that reason.
FAIL_CLOSED_BRANCH_RE = re.compile(
    r'^[ \t]*if[ \t]+\[[ \t]*"\$major"[ \t]*!=[ \t]*"(?P<major>\d+)"[ \t]*\][ \t]*;'
    r'[ \t]*then[ \t]*\n'
    r'(?P<body>(?:(?![ \t]*(?:if|elif|else|fi|for|while|until|case|esac|do|done)\b)'
    r'[^\n]*\n)*?)'
    r'[ \t]*fi[ \t]*$',
    re.MULTILINE,
)

#: The two things the armed branch must DO, each anchored to its own line so
#: that a line which merely contains the token does not satisfy it.
FAIL_CLOSED_ANNOUNCE_RE = re.compile(r'^[ \t]*echo[ \t]+["\']::error::', re.MULTILINE)
FAIL_CLOSED_STOP_RE = re.compile(r"^[ \t]*exit 1[ \t]*$", re.MULTILINE)

#: A guard whose CONDITION IS A LITERAL CONSTANT that makes the body NEVER RUN.
#: `FAIL_CLOSED_BRANCH_RE` refuses nested structure INSIDE the branch, but nothing
#: refused an ENCLOSING `if false; then … fi` around the prepend, around the
#: install, or around the whole fail-closed `for` loop: measured, all five rows
#: GREEN with the block body unchanged, step exit 0, leg on the inherited client —
#: the #12051 defect carried back in one line, and a FALSE PASS of exactly the
#: class this guard exists to prevent.
#:
#: ⛔ THE POLARITY IS "NEVER RUNS", NOT "NAMES A CONSTANT" (#12069 review). The
#: first cut refused `false|true` on every one of `if|elif|while|until`, which is
#: FIVE false REDs on correct shell — `if true; then` runs its body, `while true;
#: do … break; done` runs it and stops, `until false; do` runs it, and the negated
#: constants invert all of it. A guard that reds a correct tree is the same defect
#: class as one that passes a broken one, so the set below is exactly the
#: never-runs forms and nothing else:
#:   * `if` / `elif` / `while` with `false` or `! true`  -> body never entered
#:   * `until` with `true` or `! false`                  -> body never entered
#: `while true … break` is deliberately NOT refused: it is the ordinary
#: retry-loop spelling and it RUNS. Only when the condition is a BARE constant
#: (optionally negated); `if [ "$major" != "16" ]; then` is untouched, because
#: its condition is a `[` test whose truth nobody can read off the text.
#:
#: STATED LIMIT: this refuses the literal spellings that were MEASURED, not
#: reachability in general. Whether a shell block can run is not decidable by a
#: line regex, and six review rounds each demonstrated that by closing one
#: spelling and buying the next (`if false && [ … ]`, a nested `if false`, then
#: this enclosing block). The armed-branch row is therefore a text guard with a
#: named residual, not a proof; the in-job `pg_dump --version` assertion is what
#: makes a surviving blind spot visible in the log.
CONSTANT_GUARD_RE = re.compile(
    r"^[ \t]*(?:"
    r"(?:if|elif|while)[ \t]+(?:false|![ \t]*true)"
    r"|until[ \t]+(?:true|![ \t]*false)"
    r")[ \t]*(?:;|$)",
    re.MULTILINE,
)

#: A command chain that is dead BY CONSTRUCTION, at the position where the dead
#: command sits (#12069 review corrected the first cut TWICE, each time on a
#: correct line):
#:   * `false && <cmd>` — `<cmd>` never runs. Refused after `^`, `;` or `&&`:
#:     these are positions where the `false` is the LEFT operand of the `&&` that
#:     guards `<cmd>`, so the short-circuit is unconditional. (NOT an exhaustive
#:     list of such positions — `( false && <cmd> )`, `{ false && <cmd>; }` and
#:     `if false && …` are read as their own lines and are not refused; the guard
#:     scans line text, and rounding those up is the widening this branch stopped.)
#:     ⛔ NOT refused after `||`. `A || false && B` parses as `(A || false) && B`
#:     (`&&` and `||` are the same precedence, left-associative), so `B` RUNS
#:     whenever `A` succeeds — measured: `true || false && echo BODY` prints BODY.
#:     The first cut refused it and red three rows on a line that executes the
#:     write; the rationale "`&&` short-circuits on a false left operand wherever
#:     it appears" is false exactly here, because the `&&`'s left operand is
#:     `A || false`, not `false`.
#:   * `true || <cmd>` — `<cmd>` never runs. Refused ONLY at a STATEMENT position
#:     (`^` or after `;`). `cmd && true || <fallback>` is NOT a dead chain: the
#:     fallback DOES run when `cmd` fails, and it was measured as a false RED.
#: The HARMLESS spellings stay unmatched: `<cmd> || true` and `<cmd> && true`
#: still RUN `<cmd>` (they only choose the exit status).
DEAD_CHAIN_RE = re.compile(
    r"(?:^|;|&&)[ \t]*false[ \t]*&&"
    r"|(?:^|;)[ \t]*true[ \t]*\|\|"
)


@pytest.fixture(scope="module")
def job() -> dict:
    """The job, with the WORKFLOW-level `defaults` folded in.

    `defaults.run.shell` is valid at WORKFLOW scope and applies to every `run:`
    step of every job in the file (actions/runner `ScriptHandler.cs` falls back to
    `Global.JobDefaults`, which `JobExtension` fills from `message.Defaults`), so
    a shell set there is EXECUTED for this job's steps. Reading only the job's own
    `defaults` left

        defaults: {run: {shell: "bash -c 'export PATH=/usr/lib/postgresql/17/bin:$PATH; exec bash {0}'"}}

    GREEN with the leg on pg17 (measured, #12051 review round 10) — while the same
    shell at JOB scope correctly red.

    The fold is PER KEY, because GitHub's override is per NAME — "When more than
    one default setting is defined with the same name, GitHub uses the most
    specific default setting" (`actions/defaults-override`). An all-or-nothing
    fold let a job that sets only `defaults.run.working-directory` MASK the
    workflow's `shell`, which still `exec bash {0}`-wraps this job's `run:` steps
    at runtime: the same injector stayed GREEN behind two benign-looking edits
    (measured, round 11). A job key that is present wins; an absent one does not
    hide the workflow's.
    """

    workflow = yaml.safe_load(PYTHON_CI.read_text())
    assert JOB in workflow["jobs"], f"{JOB} is the record's only armed Postgres job"
    resolved = dict(workflow["jobs"][JOB])
    job_defaults = resolved.get("defaults") or {}
    resolved["defaults"] = job_defaults
    job_run = job_defaults.get("run") or {}
    job_defaults["run"] = job_run
    for key, value in ((workflow.get("defaults") or {}).get("run") or {}).items():
        job_run.setdefault(key, value)

    # `env:` is folded the same way and for the same reason (#12069 review P1):
    # workflow-scope `env` applies to every step of every job, so a `PATH` set
    # there moves the leg exactly as the job's own does — and reading only the
    # job's left the workflow-level form invisible. Per KEY, job wins, which is
    # GitHub's documented precedence ("the most specific default setting").
    resolved["env"] = dict(resolved.get("env") or {})
    for key, value in (workflow.get("env") or {}).items():
        resolved["env"].setdefault(key, value)
    return resolved


def _steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def _run_text(step: dict) -> str:
    return str(step.get("run") or "")


def _run_code(step: dict) -> str:
    """The step's shell with COMMENT LINES removed (#12051 review P2).

    Every assertion in this guard is a search over the step's TEXT, and a
    commented-out line is inert shell that reads identically to the live one.
    Measured: `# echo "/usr/lib/postgresql/16/bin" >> "$GITHUB_PATH"` (with the
    in-step `export` kept) and a commented-out `if [ "$major" != "16" ] … fi`
    block each left EVERY row green at the time it was measured (four then;
    five since round 5) while the leg went back to resolving the runner image's
    client — i.e. the guard could not distinguish the fix from its tombstone.
    Assertions therefore read this, never the raw body.

    STATED LIMIT: this strips COMMENT LINES only (a line whose first non-space
    character is `#`), and the fail-closed branch is matched structurally by
    `FAIL_CLOSED_BRANCH_RE` for the same reason: presence is not gating. A
    TRAILING comment on a live line is dropped by `_run_commands()` for every
    search that reads a line as a unit — measured, a trailing `# was: …`
    carrying the versioned write made a live `/usr/bin` line satisfy the
    prepend, and a correct `apt-get` line whose trailing comment named the
    capstone made the pin step look like the leg (`assert 4 < 4`, a false RED).
    """

    return "\n".join(
        line
        for line in _run_text(step).splitlines()
        if not line.lstrip().startswith("#")
    )


def _run_commands(step: dict) -> str:
    """`_run_code()` with trailing comments removed and continuations joined.

    ` #…` (whitespace before the `#`) is the shell's comment start outside
    quotes, which is what every call site needs: they match a whole write/entry
    line, and a trailing comment there is either a tombstone (the value it
    names is NOT what runs) or noise (the capstone's name in the pin step).

    A BACKSLASH CONTINUATION IS JOINED first (#12051 review round 7): measured,
    `echo "/usr/lib/postgresql/17/bin" >> \\` on one line and `"$GITHUB_PATH"`
    on the next left every row GREEN, because a per-line scan sees neither a
    write nor a mention. It is one shell command, so it is read as one line.

    STATED LIMIT: a `#` inside a quoted string is not special-cased — it is
    only reached here when a line quotes a literal `#` AND names one of the two
    paths, which then reds rather than silently passing.
    """

    joined = _run_code(step).replace("\\\n", " ")
    return "\n".join(
        re.split(r"\s+#", line, maxsplit=1)[0]
        for line in joined.splitlines()
    )


def _github_path_writes(steps: list[dict], job: dict | None = None) -> list[tuple[int, str, int | None]]:
    """Every `$GITHUB_PATH` MENTION in the job, as `(step, line, major | None)`.

    `major` is `None` when the guard cannot CLASSIFY the mention — a producer it
    does not read, a directory it cannot see, a value or FILE NAME built at
    runtime, a redirect split across lines. Round 6 detected only the WRITE
    (`>`, `>>`, `tee`) and spelled the producer `echo`-or-redirect, and two
    spellings were then measured GREEN while the leg ran on pg17: a file-name
    indirection (`gp="$GITHUB_PATH"; echo … >> "$gp"`) and a continued
    redirect. Detecting the mention needs no guess about either, and the
    assertions below require every mention to BE the one classified write.
    """

    return [
        (index, line.strip(), _classify_path_write(line))
        for index, step in enumerate(steps)
        for line in (_effective_shell(job, step) + "\n" + _run_commands(step)).splitlines()
        if GITHUB_PATH_MENTION_RE.search(line)
    ]


def _effective_shell(job: dict | None, step: dict) -> str:
    """The `shell:` a step's `run:` is EXECUTED through — job default, then step.

    Executed, not decoration: the runner splits it (`ParseShellOptionString`, on
    the first space only) and substitutes `{0}`, so
    `shell: env PATH=/usr/bin:/bin bash` or
    `shell: bash -c "export PATH=/usr/bin:/bin; exec bash {0}"` moves the leg's
    PATH. `_client_dir_mentions` has always scanned it; the PATH-assignment rule
    and the `$GITHUB_PATH` mention scan did NOT, so those two spellings left every
    row green (#12051 review round 13, measured).
    """

    default_shell = str(((job or {}).get("defaults") or {}).get("run", {}).get("shell") or "")
    return "\n".join([default_shell, str(step.get("shell") or "")])


def _executed_lines(job: dict | None, step: dict) -> list[str]:
    """Every line the step EXECUTES: its `shell:` argv plus its `run:` script."""

    return (_effective_shell(job, step) + "\n" + _run_commands(step)).splitlines()


def _client_dir_mentions(steps: list[dict], job: dict | None = None) -> list[tuple[int, str, int | None]]:
    """Every line that names a `/usr/lib/postgresql/<major>/bin` directory.

    The PATH that matters on the leg is not built by `$GITHUB_PATH` alone: the
    leg's own script may `export PATH="/usr/lib/postgresql/17/bin:$PATH"`, and a
    step-script assignment runs AFTER the runner composes the prepend, so it
    wins (actions/runner `Handler.AddPrependPathToEnvironment` / `ScriptHandler`).
    Measured GREEN before this rule, with the in-job `pg_dump --version`
    assertion still printing 16 because it lives in the pin step.

    `shell:` IS PART OF THE POPULATION (#12051 review round 9): it is EXECUTED
    argv, not decoration — the runner splits it (`ParseShellOptionString`, first
    space only) and substitutes `{0}`, so
    `shell: bash -c 'export PATH=/usr/lib/postgresql/17/bin:$PATH; exec bash {0}'`
    moves the leg's PATH while `_run_text()` never saw it. Measured GREEN at the
    step AND at `defaults.run.shell`; both are scanned here.
    """

    found: list[tuple[int, str, int | None]] = []
    for index, step in enumerate(steps):
        for line in (_effective_shell(job, step) + "\n" + _run_commands(step)).splitlines():
            for match in CLIENT_DIR_RE.finditer(line):
                token = match.group("token")
                found.append((index, line.strip(), int(token) if token.isdigit() else None))
    return found


def _classify_path_write(line: str) -> int | None:
    """The major a write puts on PATH, or `None` if this guard cannot tell."""

    match = GITHUB_PATH_PREPEND_RE.fullmatch(line.strip())
    return int(match.group("major")) if match else None


def _leg_indexes(steps: list[dict]) -> list[int]:
    """Steps that run the D8 record capstone — the leg the pin must precede."""

    return [
        index
        for index, step in enumerate(steps)
        if "test_9567_record_capstone.py" in _run_commands(step)
    ]


def _unreachable_guards(
    steps: list[dict], start: int, end: int, job: dict | None = None
) -> list[str]:
    """Lines in `[start, end)` that cannot run, as `step N: <line>`.

    The two rows that assert a construct is LIVE — the `$GITHUB_PATH` prepend and
    the armed fail-closed branch — both read this, because a construct sitting
    inside a block whose condition is a literal constant is PRESENT and INERT at
    the same time. Measured (#12051 review round 8): an enclosing `if false;
    then … fi` around the prepend, and again around the whole fail-closed `for`
    loop, left all five rows green while the leg went back to the runner image's
    client — i.e. #12051 restored in one line, with this guard's own message
    insisting it had not been.

    Read through `_executed_lines()` — the step's `shell:` argv AND its `run:`
    script (round 17) — like every other line-unit search here, so a tombstone
    comment naming a constant guard is inert text, not an offender. Reading the
    shell is what makes an inert `shell:` visible at all: `bash -c 'exit 0; exec
    bash {0}'` passes every structural row because the `run:` text is untouched,
    and `_inert_shell_reason()` is the rule that refuses it.

    ⛔ THE CONTRACT IS "THE BODY NEVER RUNS", NOT "THE LINE NAMES A CONSTANT"
    (#12069 review): a `while true; do … break; done` retry loop RUNS, and so does
    `if true; then` — refusing those reds a correct tree, which is the same
    defect class as passing a broken one. `CONSTANT_GUARD_RE` and
    `DEAD_CHAIN_RE` are therefore both scoped to the never-runs forms, and
    `REFUSAL_POLARITY` at the foot of this module pins both directions as a
    committed, parametrized test — deliberately NOT in an out-of-repo harness,
    because `.worktree-report/` is gitignored and a pin that is not committed
    pins nothing once the branch merges (#12069 review).
    """
    found: list[str] = []
    for index in range(start, end):
        if not 0 <= index < len(steps):
            continue
        inert_shell = _inert_shell_reason(job, steps[index])
        if inert_shell:
            found.append(f"step {index}: {inert_shell}")
        for line in _executed_lines(job, steps[index]):
            if CONSTANT_GUARD_RE.match(line) or DEAD_CHAIN_RE.search(line):
                found.append(f"step {index}: {line.strip()}")
            elif SUBSHELL_OPEN_RE.match(line) or SUBSHELL_CLOSE_RE.match(line):
                # ⛔ A SUBSHELL IS NOT A SCOPE, IT IS A STATUS (#12051 review round
                # 14): `( … exit 1 … ) || true` keeps every structural row green —
                # the branch is flat, armed, compares 16 — while `exit 1` now
                # terminates the subshell and `|| true` DISCARDS its status, so the
                # step succeeds after printing its `::error::`. Measured `58 passed`
                # with the leg on the inherited client, which is exactly what the
                # fail-closed row exists to prevent.
                found.append(
                    f"step {index}: SUBSHELL around the guarded block: {line.strip()}"
                )
    return found


def _replaces_a_client_binary(line: str) -> bool:
    """Does this line REPLACE a client binary? (#12051 review rounds 9-10)

    TWO ways in, both narrow on purpose:

    · a MUTATING VERB followed by a client binary name. The binary must be named
      AFTER the verb: reading the pair anywhere on the line made a tidy step
      (`rm -f "$RUNNER_TEMP/pg_dump.log"`) and the guard's OWN recommended
      compensating control (`pg_dump --version | tee -a "$RUNNER_TEMP/v.txt"`) read
      as replacements — both measured RED, with a message saying the step replaced
      a binary it only mentioned.
    · a REDIRECTION whose TARGET is a client binary or the pinned `bin/`
      (#12069 review P1). A redirect writes a file with NO verb, so the rule
      above could not see `: > /usr/lib/postgresql/16/bin/pg_dump` — measured
      green, and it is the full regression. See `REDIRECT_TARGET_RE`.

    The pin's own install line is excluded.
    """

    if CLIENT_PKG_RE.search(line):
        return False
    # ⛔ BEFORE the verb rule, because a redirection has NO verb (#12069 review
    # P1): `: > /usr/lib/postgresql/16/bin/pg_dump` rewrote the pinned binary with
    # every row green. See `REDIRECT_TARGET_RE`.
    if _redirects_into_a_client_binary(line):
        return True
    verb = BINARY_MUTATION_RE.search(line)
    if not verb:
        return False
    return bool(BINARY_NAME_RE.search(line, verb.end()))


#: A subshell opens here and is not closed on the same line — whatever runs inside
#: it is a DIFFERENT shell, so its `exit` cannot fail the step.
SUBSHELL_OPEN_RE = re.compile(r"^[ \t]*\((?!.*\))[ \t]*$")
#: …and closes with its status DISCARDED (`) || true`, `) ;`, `) && …`).
SUBSHELL_CLOSE_RE = re.compile(r"^[ \t]*\)[ \t]*(?:\|\||;|&&)")

#: A command that ENDS the shell it is typed in, when it appears BEFORE the
#: runner's `{0}` script in the `shell:` string. Quotes are separators here too:
#: the runner hands this text to `bash -c`, where `'exit 0'` IS the statement —
#: and a text guard that tried to tell a quoted mention from a command would be
#: reading meaning it cannot prove, so the narrow refusal wins.
TERMINATING_COMMAND_RE = re.compile(
    r"(?:^|[;&|()\s'\"])(?P<word>exit|return)(?=[\s;&|()'\"]|$)"
)


def _inert_shell_reason(job: dict | None, step: dict) -> str | None:
    """The `shell:` that never runs the step's script, or None (#12069 round 17).

    ⛔ A STEP'S `shell:` IS EXECUTED BEFORE THE SCRIPT IT CARRIES (#12069 review
    round 17, measured GREEN). The runner substitutes `{0}` with the step's script
    path and runs the WHOLE string, so

        shell: bash -c 'exit 0; exec bash {0}'
        run: |            # unchanged — and never executed
          …the entire client pin…

    makes the pin inert — never installed, never prepended, never asserted — while
    every row here reads the untouched `run:` text and stays green. Measured on
    the merged workflow: `140 passed`, the client never checked. This is the
    `_run_commands()`-only sibling of the round-13/14 `shell:` findings: those two
    scans learned to READ the shell; nothing asked whether it reaches the script.

    ACCEPTED: an interpreter running `{0}`, with no `exit`/`return` before it —
    including `bash -c 'echo hi; exec bash {0}'` (a statement before the exec is
    harmless) and a bare `bash` (the runner appends the script when `{0}` is
    absent). REFUSED: a terminator before the script, or `{0}` handed to something
    that is not an interpreter — the spellings measured below and pinned in
    `INERT_SHELL_POLARITY`.

    ⛔ THE EFFECTIVE SHELL IS THE STEP'S OWN **OR** THE DEFAULT, NEVER BOTH (#12069
    review round 18, measured). The first cut split `_effective_shell()`'s
    CONCATENATION on the first `{0}`, so a `defaults.run.shell: bash -e {0}` — a
    documented form this file folds into the job — hid the step's own inert shell
    behind it and the measured mutant went GREEN again. A step `shell:` REPLACES
    the default; only when the step has none does the default apply.

    ⛔ AND MENTIONING `{0}` IS NOT EXECUTING IT (#12069 review round 18,
    measured): `shell: bash -c 'cat {0} >/dev/null; exit 0'` has no terminator
    before `{0}`, so the first cut passed it while the step body never ran. The
    last non-flag word before `{0}` must therefore BE an interpreter
    (`SHELL_COMMANDS`), which `bash -e {0}`, `bash {0} || exit 1` and
    `bash -c '… exec bash {0}'` all satisfy.

    STATED LIMIT: this reads ONE spelling class (a terminator before `{0}`, or a
    non-interpreter runner of it); a shell that blocks forever, or one that execs
    a DIFFERENT script under a name it computes, is not decidable from text. The
    in-job `pg_dump --version` assertion is what makes a surviving blind spot
    visible in the log.
    """

    # THE STEP'S OWN SHELL REPLACES THE DEFAULT (runner semantics) — joining them
    # lets a correct default mask an inert step shell.
    shell = str(
        step.get("shell")
        or (((job or {}).get("defaults") or {}).get("run") or {}).get("shell")
        or ""
    )
    if not shell.strip():
        return None
    before, marker, _after = shell.partition("{0}")
    match = TERMINATING_COMMAND_RE.search(before)
    if match:
        return (
            f"`shell:` runs `{match.group('word')}` BEFORE the step's own script "
            f"({shell.strip()!r}), so the `run:` block never executes — the whole "
            "step is inert (never installed, prepended, or asserted) while its "
            "text satisfies every row here (#12069 review round 17)"
        )
    if marker and not _runs_the_script(before):
        return (
            f"`shell:` does not hand the step's script to a shell "
            f"({shell.strip()!r}) — the word in COMMAND position before `{{0}}` "
            "is not an interpreter, so mentioning `{0}` is not executing it: the "
            "`run:` block never runs while its text satisfies every row here "
            "(#12069 reviews 18-19)"
        )
    return None


#: The interpreters a `shell:` can legitimately use to RUN `{0}` — the runner's
#: own documented set, so `python {0}` and `pwsh -Command {0}` are not false-RED
#: here. `cat {0}` is.
SHELL_COMMANDS = frozenset(
    {
        "bash",
        "sh",
        "dash",
        "zsh",
        "ksh",
        "pwsh",
        "powershell",
        "python",
        "python3",
        "node",
        "cmd",
    }
)

#: Words in a `shell:` string's argv, with every shell separator and quote treated
#: as a boundary. (Local convention: `_runs_the_script` splits on its own, because
#: it must cut at the LAST separator first.)
SHELL_WORD_SPLIT_RE = re.compile(r"[\s;&|()'\"`]+")

#: Where ONE command's text ends and the next begins, plus the boundary a `-c`
#: inline script opens at. The LAST such boundary before `{0}` is where the command
#: that would receive `{0}` starts (#12069 round 19).
SHELL_STATEMENT_BOUNDARY_RE = re.compile(r"[;&|\n'\"`]")

#: Commands that RUN the next command rather than consuming the rest of the line.
SHELL_WRAPPERS = frozenset({"exec", "command", "env", "nohup", "sudo", "time", "builtin"})


def _runs_the_script(before: str) -> bool:
    """Is the word in COMMAND position before `{0}` an interpreter (#12069 r19)?

    ⛔ `{0}` MUST BE AN ARGUMENT OF AN INTERPRETER, NOT OF WHATEVER PRECEDES IT
    (#12069 review round 19, both directions measured). Two cuts of this rule got
    it wrong in opposite ways:

      · `words[-1] in SHELL_COMMANDS` false-RED `bash --noprofile --norc -eo
        pipefail {0}` — `pipefail` is a FLAG'S VALUE, not the word that runs the
        script — and `/bin/bash -e {0}`, whose argv[0] is a path. Both are correct
        spellings this repo asserts elsewhere (measured `2 failed` on the real
        pin step).
      · `words[-1]` also ACCEPTED `bash -c 'echo bash {0}; exit 0'`, where the
        word before `{0}` happens to be `bash` but the command that receives it
        is `echo` — the step body never ran and the row stayed GREEN.

    So grade the START of the statement `{0}` sits in: cut `before` at its LAST
    separator (a `;`, `&`, `|`, newline, or the quote that opens a `-c` script),
    skip variable-assignment prefixes and wrappers (`exec`, `command`, `env`, …),
    and require the first remaining word — basenamed, so `/bin/bash` counts — to be
    a shell. `bash -e {0}`, `bash --noprofile --norc -eo pipefail {0}`,
    `/bin/bash -e {0}`, `bash -c 'exec bash {0}'` and `python {0}` all pass;
    `cat {0}`, `echo bash {0}` and `printf %s bash {0}` do not.
    """

    cut = 0
    for boundary in SHELL_STATEMENT_BOUNDARY_RE.finditer(before):
        cut = boundary.end()
    words = [
        word
        for word in re.split(r"[\s'\"`]+", before[cut:])
        if word and not word.startswith("-")
    ]
    for index, word in enumerate(words):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            continue  # a VAR=value prefix is not the command
        if word.rsplit("/", 1)[-1] in SHELL_WRAPPERS and index < len(words) - 1:
            continue  # `exec bash …` / `env … bash …`: keep walking right
        return word.rsplit("/", 1)[-1] in SHELL_COMMANDS
    return False

#: A statement that LEAVES the current iteration from the loop body's TOP LEVEL.
#: It runs before anything below it, so the body it precedes is text the shell
#: never reaches. ⛔ THE LEVEL ARGUMENT IS PART OF THE STATEMENT (#12069 review
#: round 18, measured): `break 2` at a body's top level left a multi-tool loop
#: crediting both tools while the one-line spelling of the same statement was
#: refused — the two forms disagreed about one fact.
LOOP_TRUNCATING_RE = re.compile(
    r"^[ \t]*(?:break|continue)(?:[ \t]+[0-9]+)?[ \t]*(?:[;#].*)?$"
)


def _truncated_at_top_level(body: list[str]) -> bool:
    """Does this loop body LEAVE the iteration at its top level (#12069 round 17)?

    ⛔ NOTHING RUNS BELOW A BARE `break` (#12069 review round 17, measured GREEN):

        for tool in pg_dump pg_restore; do
          break
          version="$("$tool" --version)"
        done

    credits BOTH tools while nothing is version-checked. `_selects_on_var` closes
    the SELECTION half of the same defect class, but a bare `break` mentions no
    loop variable, so it is not a selection and that scan does not see it.

    A `break`/`continue` inside a NESTED block stays accepted (control row): it
    runs only when that block's condition holds, so the pass that reaches the
    condition still runs the body above it. Only lines at the body's MINIMUM
    indent — the top level — are refused.

    ⛔ AND ONLY WHEN SOMETHING FOLLOWS IT (#12069 review round 18, measured): a
    `break`/`continue` as the body's LAST significant line skips nothing — every
    tool above it was checked — so refusing it red a body that checks every tool
    (measured: `1 failed` on the real workflow). A terminator with code after it
    is the defect; a terminator with none is a no-op.
    """

    significant = [
        line for line in body if line.strip() and not line.lstrip().startswith("#")
    ]
    if not significant:
        return False
    top = min(len(line) - len(line.lstrip()) for line in significant)
    return any(
        len(line) - len(line.lstrip()) == top
        and LOOP_TRUNCATING_RE.match(line)
        and index < len(significant) - 1
        for index, line in enumerate(significant)
    )

#: `if:` conditions that CANNOT SKIP the step, normalised (no `${{ }}`, single
#: spaces). `success()` is the default; `always()` and its two aliases run the
#: step whenever the job is running at all — all strictly SAFER than an absent
#: `if:`, so refusing them reds a correct tree with a message asserting a false
#: condition. Round 10 measured `always()` itself; round 11 measured
#: `!cancelled()` and `success() || failure()`, which are the same condition
#: spelled the way GitHub's own docs spell it.
NON_SKIPPING_IF = frozenset(
    {"success()", "always()", "!cancelled()", "success() || failure()"}
)


def _normalise_if(value: object) -> str:
    """`${{ X }}`, spacing and digraph spelling collapse to ONE comparable form.

    ⛔ SPACING IS NOT MEANING IN A GITHUB EXPRESSION (#12069 review P2). Whitespace
    was collapsed but never REMOVED where it does not matter, so the two
    spellings GitHub's own docs use were read as conditions that cannot skip:
    `${{ ! cancelled() }}` normalised to `! cancelled()` and `success()||failure()`
    to itself, neither matched `NON_SKIPPING_IF`, and the row red the CORRECT
    `if:` with a message saying the condition could not skip the step (measured:
    both false REDs). `! cancelled()` and `!cancelled()`, `a()||b()` and
    `a() || b()` are the same expression, so they now normalise the same way.
    """

    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"^\$\{\{\s*|\s*\}\}$", "", text).strip()
    # PARENTHESES FIRST: tightening them afterwards would re-close the space the
    # `||` rule opens (`success() || failure()` -> `success()|| failure()`), which
    # is how the first cut of this fix still missed the set (measured).
    text = re.sub(r"\s*\(\s*", "(", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    text = re.sub(r"\s*(\|\||&&)\s*", r" \1 ", text)
    # Only a LEADING negation: `!` is meaningful there and nowhere else here, and
    # rewriting every `!` would touch `!=` and string literals.
    text = re.sub(r"^\s*!\s*", "!", text)
    return re.sub(r"\s+", " ", text).strip()


def _conditional_skip(step: dict) -> str | None:
    """The `continue-on-error` / `if:` VALUE that can skip this step, or None."""

    if step.get("continue-on-error"):
        return f"continue-on-error: {step['continue-on-error']!r}"
    if (
        "if" in step
        and step["if"] is not True
        and _normalise_if(step["if"]) not in NON_SKIPPING_IF
    ):
        return f"if: {step['if']!r}"
    return None


#: The ONE `needs.`-gated job-level `if:` whose polarity a text guard can read:
#: `always() && needs.<id>.result == 'success'` runs the job exactly when the
#: upstream SUCCEEDED, and a FAILED upstream reds the run — so the job can never
#: be skipped silently on the healthy path. The inverted spellings
#: (`== 'failure'`, `!= 'success'`) are FALSE on that path and are reported
#: (#12069 review round 15, measured: both graded GREEN before).
NEEDS_GATED_IF_RE = re.compile(
    r"always\(\)\s*&&\s*needs\.(?P<job>[A-Za-z_][\w-]*)\.result\s*==\s*'success'"
)


def _job_scope_skip(job: dict) -> str | None:
    """The JOB-level `if:` / `continue-on-error` that can hide the whole job.

    ⛔ Every row read the STEP and nothing read the JOB (#12069 review round 14,
    measured): a job-level `continue-on-error: true` disarms each step's `exit 1`,
    and a job-level `if: github.event_name == 'push'` skips the whole job on a PR
    while the RUN stays GREEN — the pin cannot red anything and the text is
    untouched.

    ⛔ A `needs.` mention is NOT a licence to skip (round 15, measured): accepting
    any condition containing `needs.` also accepted
    `needs.changes.result == 'failure'` and `!= 'success'`, which are FALSE on the
    healthy path. A condition's POLARITY is not readable from text, so the
    accepted forms are enumerated instead.
    """

    if job.get("continue-on-error"):
        return f"continue-on-error: {job['continue-on-error']!r} (JOB level)"
    condition = job.get("if")
    if condition is None or condition is True:
        return None
    key = _normalise_if(condition)
    if key in NON_SKIPPING_IF:
        return None
    raw_needs = job.get("needs")
    if isinstance(raw_needs, str):
        needs = {raw_needs}
    elif isinstance(raw_needs, (list, tuple, set)):
        needs = {str(entry) for entry in raw_needs}
    else:
        needs = set()
    match = NEEDS_GATED_IF_RE.fullmatch(key)
    if match and match.group("job") in needs:
        return None
    return f"if: {condition!r} (JOB level)"


def _asserting_loop_line_spans(code: str) -> list[tuple[int, int]]:
    """`(header line, `done` line)` for each loop that version-checks its variable.

    ⛔ The fail-closed assertion has to sit INSIDE the loop that covers the tools
    (#12069 review round 16, measured): dedenting the `if … fi` past `done` — a
    plain refactor — leaves `$major` asserted for the LAST tool only while every
    row stays GREEN. With a pg17 `pg_dump` and a pg16 `pg_restore` the pin then
    exits 0 and the drill reds later with the misattributed
    `transaction_timeout` error #12051 exists to remove.

    Only loops that name MORE THAN ONE tool are returned: a single-tool loop
    asserts that tool whether the branch is inside or after it.
    """

    lines = code.splitlines()
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        inline = LOOP_HEAD_INLINE_RE.match(lines[index])
        head = inline or LOOP_HEAD_OPEN_RE.match(lines[index])
        if not head:
            index += 1
            continue
        var = head.group("var")
        if "$" in head.group("names"):
            index += 1
            continue
        tools = {
            name
            for name in re.findall(r"[A-Za-z_][\w-]*", head.group("names"))
            if name in TOOLS
        }
        do_line = index
        if not inline:
            for probe in range(index + 1, len(lines)):
                if LOOP_DO_RE.match(lines[probe]):
                    do_line = probe
                    break
                if lines[probe].strip() and not lines[probe].lstrip().startswith("#"):
                    do_line = -1
                    break
        end = None
        if (
            do_line >= 0
            and inline is not None
            and re.search(r"(?:^|[;&])[ \t]*done\b", inline.group("rest") or "")
        ):
            end = index  # one-line loop: body and `done` share the header line
        if end is None and do_line >= 0:
            for probe in range(do_line + 1, len(lines)):
                if LOOP_END_RE.match(lines[probe]):
                    end = probe
                    break
        if end is None:
            index += 1
            continue
        body = "\n".join(lines[do_line : end + 1])
        if len(tools) > 1 and re.search(rf"\$\{{?{re.escape(var)}\b", body):
            spans.append((index, end))
        index = end + 1
    return spans


#: The body of a ONE-LINE loop (`for …; do break; …; done`) is on its header line,
#: so a top-level `break`/`continue` there is a whole-line statement only after the
#: terminating `do`.
INLINE_LOOP_TRUNCATING_RE = re.compile(r"(?:^|[;&])[ \t]*(?:break|continue)\b")


def _inline_truncates(rest: str, closes_here: bool = True) -> bool:
    """Does the loop body on the `do` LINE leave the iteration with code after it?

    The `do` line's own body (`rest`), down to `done`, split into statements. The
    same rule as `_truncated_at_top_level`, including its round-18 correction: a
    terminator with NO statement after it skips nothing and is accepted.

    ⛔ UNLESS THE BODY CONTINUES ON THE FOLLOWING LINES (#12069 review round 19,
    measured GREEN). On a `do` line that does NOT close the loop, nothing CAN
    follow the terminator on that line, so exempting it as "trailing" credited the
    whole body beneath it:

        for tool in pg_dump pg_restore; do break
          version="$($tool --version)"
        done                                            ->  164 passed, GREEN

    The exemption is therefore conditional on the loop CLOSING on this line; when
    it does not, a terminator here truncates the body below it by construction.
    """

    body = re.split(r"(?:^|[;&])[ \t]*done\b", rest, maxsplit=1)[0]
    statements = [part.strip() for part in body.split(";")]
    significant = [part for part in statements if part and not part.startswith("#")]
    return any(
        INLINE_LOOP_TRUNCATING_RE.match(part)
        and (not closes_here or index < len(significant) - 1)
        for index, part in enumerate(significant)
    )


def _truncated_loop_bodies(code: str) -> list[str]:
    """The multi-tool loops whose top level LEAVES the iteration before its checks.

    ⛔ A STATEMENT-LEVEL `break` IS NOT A SELECTION (#12069 review round 17,
    measured GREEN):

        for tool in pg_dump pg_restore; do
          break
          version="$("$tool" --version)"
        done

    left `140 passed` with neither client version-checked — a bare `break` names
    no loop variable, so `_selects_on_var` (which closes the SELECTION half of this
    defect class) cannot see it, and `_version_checked_tools` credits the header's
    names from a body the shell never reaches. This function is the NAMED refusal;
    `_version_checked_tools` also credits nothing for such a body, so the two rows
    cannot disagree about what runs.

    Scoped to loops that name more than one tool (`_asserting_loop_line_spans`),
    which is where the credit is a claim about COVERAGE; a single-tool loop is
    caught by the credit rule in `_version_checked_tools`.
    """

    lines = code.splitlines()
    offenders: list[str] = []
    for start, end in _asserting_loop_line_spans(code):
        header = lines[start]
        inline = LOOP_HEAD_INLINE_RE.match(header)
        rest = (inline.groupdict().get("rest") or "") if inline else ""
        if start == end:
            if _inline_truncates(rest):
                offenders.append(header.strip())
            continue
        # ⛔ A TERMINATOR ON THE `do` LINE OF A MULTI-LINE LOOP TRUNCATES THE BODY
        # BELOW IT (#12069 review round 19): `for tool in …; do break` credits
        # nothing that follows, and the trailing-terminator exemption is for a loop
        # that CLOSES on that line. Named here as well as in the credit rule, so
        # the row that reports it can say why.
        if rest.strip() and _inline_truncates(rest, closes_here=False):
            offenders.append(header.strip())
            continue
        body = [line for line in lines[start + 1 : end] if not LOOP_DO_RE.match(line)]
        if _truncated_at_top_level(body):
            offenders.append(header.strip())
    return offenders


def _unasserted_client_checks(code: str) -> list[int]:
    """The `--version` checks that NO armed branch follows before the next check.

    ⛔ The inside-loop rule presupposes a LOOP (#12069 review round 18, measured):
    the same row advertises "a `<tool> --version` line per tool" as accepted, so a
    pin that UNROLLS the loop is correct and has no multi-tool span at all. The
    per-tool shape is asserted THIS way instead: every check must be followed by
    an armed branch before the next check, which keeps two-checks-one-branch RED.

    ⛔ A PRINTED mention is not a check (round 19, measured): an announce naming
    the command it ran is skipped by the same predicate `_version_checked_tools`
    uses.
    """

    branch_lines = [
        code.count("\n", 0, match.start())
        for match in FAIL_CLOSED_BRANCH_RE.finditer(code)
    ]
    check_lines: list[int] = []
    for match in re.finditer(r"--version\b", code):
        line_start = code.rfind("\n", 0, match.start()) + 1
        if _command_word(code[line_start : match.start()]) in PRINTING_COMMANDS:
            continue
        check_lines.append(code.count("\n", 0, match.start()))
    if not check_lines:
        return []
    bounds = [*check_lines[1:], len(code.splitlines()) + 1]
    return [
        check_line
        for check_line, next_check in zip(check_lines, bounds, strict=True)
        if not any(check_line < branch_line < next_check for branch_line in branch_lines)
    ]


#: The two clients the record drill resolves from PATH (`record_backup.py`
#: defaults each to its bare name).
TOOLS = ("pg_dump", "pg_restore")

#: A loop header, in EVERY spelling a correct shell allows (#12069 review rounds
#: 14-18, each measured): `do` on the header line, the POSIX two-line form
#: (`for x in a b` then `do`), and the one-line form (`for x in a b; do cmd; done`
#: — the body starts on the header line, so it is captured in `rest`). Requiring
#: `do` at end-of-line red every other spelling with "the pin never
#: VERSION-CHECKS", whose body did.
LOOP_HEAD_INLINE_RE = re.compile(
    r"^[ \t]*for[ \t]+(?P<var>[A-Za-z_]\w*)[ \t]+in[ \t]+(?P<names>[^;]*?)"
    r"[ \t]*;?[ \t]*do\b(?P<rest>.*)$"
)
LOOP_HEAD_OPEN_RE = re.compile(
    r"^[ \t]*for[ \t]+(?P<var>[A-Za-z_]\w*)[ \t]+in[ \t]+(?P<names>[^;]*?)"
    r"[ \t]*;?[ \t]*$"
)
LOOP_DO_RE = re.compile(r"^[ \t]*do[ \t]*$")
LOOP_END_RE = re.compile(r"^[ \t]*done\b")

#: `${NAME}` -> `$NAME`. `COMMAND_WORD_RE` treats `{`/`}` as command SEPARATORS,
#: so a braced variable's command word came out empty and the `f"${{{loop_var}}}"`
#: acceptance in `scan()` was DEAD CODE — a correct pin writing
#: `version="$("${tool}" --version)"` red as "never VERSION-CHECKS" (#12069 review
#: round 17, measured). Normalising first keeps `{ … }` groups working, which
#: dropping `{}` from the separator class would have broken.
BRACED_VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)\}")

#: Commands that PRINT their argument instead of running it. A client named after
#: one of these is MENTIONED, not checked (#12051 review round 12): with the
#: earlier substring test, `version="$(echo "$tool --version")"` — which runs
#: nothing — covered both tools, and so did `printf '%s\n' "$tool --version"`
#: beside a body that only ever checked `pg_dump`.
PRINTING_COMMANDS = frozenset({"echo", "printf", "cat", "tee", "logger", "true", ":"})

#: The COMMAND WORD that governs a position: the first word after the nearest
#: command separator (`$(`, a backtick, `;`, `&&`, `||`, `|`, a bracket, or the
#: start of the line), skipping the wrappers that RUN another command.
#: ⛔ `sudo` TAKES FLAGS AND A WRAPPER TAKES AN OPERAND (review 4). The skip was
#: `sudo\s+` alone, so `version="$(sudo -E "$tool" --version)"` read its command
#: word as `-E` and `timeout 30 "$tool" --version` as `30`; the check was not
#: credited and the row red a body that checks every tool (measured false RED).
COMMAND_WORD_RE = re.compile(
    r"(?:^|\$\(|`|;|&&|\|\||\||[(){}])\s*"
    r"(?:(?:sudo|env)\s+(?:-\S+\s+)*|command\s+|timeout\s+\S+\s+|"
    r"exec\s+)*"
    r"([^\s;|&(){}]+)"
)


def _command_word(before: str) -> str:
    """The command word that would RUN whatever follows `before`."""

    words = COMMAND_WORD_RE.findall(before)
    return words[-1].strip("\"'") if words else ""


#: A comparison OPERATOR — the thing that makes a line a CONDITION rather than a
#: mention. `!=` is matched before `=`, and `-eq`/`-ne`/`-gt`/`-lt` are the
#: `test` spellings of the same.
COMPARISON_RE = re.compile(r"==|!=|=~|(?<![-\w])=|-eq|-ne|-gt|-lt")

#: The constructs that COMPARE or FILTER an operand. Two groups, because they need
#: different partners to be a SELECTION on the loop variable:
#:   * a CONDITION (`[`, `[[`, `test`) only selects when the loop variable is an
#:     OPERAND of it AND a comparison operator is inside the same CLAUSE — see
#:     `_comparison_clauses`; `[ -n "$version" ]` beside an `echo "$tool"` merely
#:     mentions the variable twice;
#:   * a PATTERN MATCHER (`case`, `grep` and its `egrep`/`fgrep` spellings, `awk`,
#:     `expr`) selects on its own argument, no operator needed.
#: ⛔ `\bgrep\b` ALONE MISSED `fgrep`/`egrep` — a word boundary cannot sit between
#: `f` and `grep` — so a body that selected with `fgrep -qx` was credited for both
#: tools with `pg_restore` never checked (measured, round-15 review 2).
PATTERN_MATCHER_RE = re.compile(r"\bcase\b|\b(?:(?:e|f)?grep|awk|expr)\b")

#: A copy of the loop variable into another name (`t=$tool`, `log="$tool"`).
#: ⛔ A COPY IS NOT A SELECTION ON ITS OWN (#12069 review 3). The previous cut
#: returned True on the copy LINE, so a body that used the variable as a FILE NAME
#: — `log="$tool"` then `>> "$blah/$log.txt"` — lost its coverage and the row red
#: a body that checks every tool unconditionally (measured false RED). What the
#: copy does is make the loop variable reachable under ANOTHER NAME, which the
#: line that actually COMPARES mentions (`[ "$t" = pg_dump ]`). So the copy is
#: resolved by substitution to a FIXED POINT (`_selects_on_var`), not refused.
#: Only an assignment whose WHOLE VALUE is the variable records a copy — the pin's
#: own `version="$("$tool" --version)"` assigns a command's OUTPUT, not the name.
#: The trailing separator is a LOOKAHEAD so the separator is not consumed: it is the
#: LEADING separator of the next copy on the same line, and eating it made
#: `t=$tool; u=$t` yield only `t` (#12069 review 5).
def _copied_to(line: str, sources: list[str]) -> list[str]:
    """Every alias THIS LINE copies one of `sources` into, in order.

    ⛔ EVERY COPY ON THE LINE, NOT THE FIRST (#12069 review 5). `search()`
    returned one alias per line, so a CHAIN written on a single line —
    `t=$tool; u=$t` — only ever yielded `t`, and every later pass yielded `t`
    again: already known, so the fixed point converged with `u` unresolved, `$u`
    never became `$tool`, the selection was invisible and BOTH tools were
    credited while `pg_restore --version` was never reached (measured fail-open,
    the #12051 class this row exists to refuse). The same chain split across two
    lines resolved correctly, which is why only this form was open.
    """

    alternative = "|".join(re.escape(source) for source in sources)
    ref = (
        r"(?:\$\{(?:" + alternative + r")\}|\$(?:" + alternative + r")(?!\w))"
    )
    return [
        match.group("alias")
        for match in re.finditer(
            r"(?:^|[;&|]|\b(?:local|export|declare|readonly|typeset)\b)\s*"
            r"(?P<alias>[A-Za-z_]\w*)=\s*[\"']?" + ref + r"[\"']?\s*(?=$|[;&|])",
            line,
        )
    ]


def _with_aliases(line: str, aliases: list[str], var: str) -> str:
    """Rewrite `$alias` / `${alias}` back to the loop variable, ONE level deep."""

    for alias in aliases:
        line = re.sub(
            r"\$\{" + re.escape(alias) + r"\}|\$" + re.escape(alias) + r"(?!\w)",
            "$" + var,
            line,
        )
    return line


def _with_aliases_each(body: list[str], var: str) -> list[str]:
    """The body with every loop-variable alias rewritten back to the variable.

    ⛔ ALIASES CHAIN, SO THE RESOLUTION RUNS TO A FIXED POINT (#12069 review 4).
    Rewriting one level left a SECOND hop unresolved — `t=$tool` then `u=$t` then
    `[ "$u" = pg_dump ]` — and an unresolved alias fails OPEN: the selection is
    invisible, both tools are credited, and the row passes a body that only checks
    one (measured, and a regression from the `_copies_loop_var` form it replaced,
    which refused the whole body). Known names are grown until no new alias
    appears, so a chain of any length resolves.
    """

    known = [var]
    seen = {var}
    changed = True
    while changed:
        changed = False
        for line in body:
            for alias in _copied_to(line, known):
                if alias not in seen:
                    seen.add(alias)
                    known.append(alias)
                    changed = True
    extra = [alias for alias in known if alias != var]
    return [_with_aliases(line, extra, var) for line in body]


#: How a CONDITION is delimited for grading: the shell's own list separators, plus
#: the keywords that end the test and begin a branch.
#: ⛔ A CONDITION IS GRADED CLAUSE BY CLAUSE, NEVER LINE BY LINE (#12069 review 5).
#: Two rounds of span heuristics traded one defect for the other, because a span is
#: a BRACKET-MATCHING question and a selection is an OPERAND question:
#:   * grading only `[ … ]` groups lost a selection in a second group (fail-open);
#:   * falling back to the WHOLE LINE when the brackets mis-split re-created the
#:     line-level test round 15 removed — a line that merely COMPARES something else
#:     and PRINTS the loop variable in its message branch reads as a selection:
#:         if [ "$major" != "16" ]; then echo "::error::$tool …"; fi
#:         [ "${#TARGETS[@]}" -gt 0 ] || { echo "::error::$tool …"; exit 1; }
#:     — both refused a body that checks every tool unconditionally (measured).
#: A CLAUSE is the separator-delimited run around each comparison, so the message
#: branch is its own clause, OPENS NO CONDITION, and is DROPPED; a selection in any
#: clause of a `&&`/`||` chain is still found. That is one rule for both directions
#: instead of a bracket-matching special case for each.
#: ⛔ A BRACE INSIDE A PARAMETER EXPANSION IS NOT A SEPARATOR (#12069 review 6).
#: Splitting on every `{` cut `${tool}` into `$` + `tool}`, so
#: `if [ "${tool}" = pg_dump ]` yielded the fragments `if [ "$` and `tool}" = pg_dump ]`
#: — the second holds the operator and `tool}` but no `$`, so `ref` could not match it,
#: `pg_restore` was never checked and the row went GREEN (measured; `5de9f4b5d7`, the head
#: before the clause rule, refused it). `(?<!\$)…` is the whole fix.
CLAUSE_SPLIT_RE = re.compile(
    r"&&|\|\||;|\b(?:then|else|elif|do|done|fi)\b|(?<!\$)\{|(?<!\$)\("
)

#: A clause is graded only when it OPENS a condition: a comparison operator alone is
#: not enough, because a MESSAGE can print one (#12069 review 6, all measured refusals
#: of a body that checks every tool unconditionally):
#:     printf '%s = %s\n' "$tool" "$version"
#:     echo "::notice::$tool = $version"
#:     echo "note: not -eq the other one for $tool"
#: The operand lookbehind rejects a tight `s=`, but a SPACED `=` in a message read as a
#: comparison. Shell has no comparison without a condition token (`[`, `[[`, `test`,
#: `((`), and clauses are already split AT the separators, so that token sits at the
#: clause's own start — optionally behind the keyword that introduces the test.
CONDITION_LEAD_RE = re.compile(
    r"\s*(?:(?:if|elif|while|until|!)\s+)*(?:\[\[?|\btest\b|\(\()"
)


#: ⛔ CONTINUATION IS TEST DEPTH, NOT A LINE ENDING (#12069 reviews 7-8). Two cuts
#: keyed continuation on the SHAPE OF THE LINE BREAK and each was wrong in one
#: direction:
#:   * no continuation at all failed OPEN on a condition split over two lines —
#:         if [[ -n "${ONLY:-}" &&
#:               "$tool" == pg_dump ]]; then …
#:     — the second line does not OPEN a condition (its `[[` is on the previous one);
#:   * continuing on a trailing `&&`/`||`/`\` failed the OTHER way, because those
#:     continue a shell LIST, whose right-hand side is an ordinary COMMAND:
#:         [ -n "$LOG" ] && \
#:           echo "::notice::$tool = $version"      # a message, not a condition
#:     — graded as a condition, so a body that checks every tool was REFUSED.
#: What actually continues a CONDITION is an unclosed test construct. `[[` and `((`
#: are counted (bare `[` is NOT: it appears inside `${T[0]}`, which is what made the
#: bracket-matching span rule wrong), quotes are stripped first so a message that
#: PRINTS `[[` opens nothing, and the depth carries across body lines.
_QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\"")


def _test_depth(text: str) -> int:
    """Unclosed `[[` / `((` constructs in this text, quotes stripped first."""

    bare = _QUOTED_RE.sub('""', text)
    return max(0, bare.count("[[") - bare.count("]]")) + max(
        0, bare.count("((") - bare.count("))")
    )


def _comparison_clauses(line: str, *, depth: int = 0) -> tuple[list[str], int]:
    """The clauses of `line` that OPEN a comparison, and the test depth after it.

    A clause is graded when it opens a condition ITSELF (`CONDITION_LEAD_RE`) or
    when it sits inside a test construct still OPEN — the carried `depth`, which is
    what makes a condition split over a line break, or a later clause of a single
    `[[ … ]]`, a condition rather than an orphan.
    """

    graded: list[str] = []
    for clause in CLAUSE_SPLIT_RE.split(line):
        opened = CONDITION_LEAD_RE.match(clause) is not None
        if (opened or depth > 0) and COMPARISON_RE.search(clause):
            graded.append(clause)
        depth = max(0, depth + _test_depth(clause))
    return graded, depth


def _matches_on(text: str, matcher: re.Match[str], ref: re.Pattern[str]) -> bool:
    """Does this PATTERN MATCHER operate on the loop variable or a client name?

    ⛔ A MATCHER ANYWHERE ON THE LINE IS NOT A SELECTION (#12069 review 3). The
    previous cut read `PATTERN_MATCHER_RE.search(line)` line-wide, so a body that
    merely PIPES THROUGH a tool —

        v="$("$tool" --version | awk '{print $3}')"     # a version extractor
        echo "::error::$tool is major $major - grep the log"

    — was read as selecting, both tools were discredited, and the row red a body
    that checks every tool unconditionally (measured). Only the matcher's OWN
    operand counts: from the matcher onward the line must name the loop variable
    or one of the client names. `grep -qx pg_dump` and `awk '$0=="pg_dump"'`
    still select; `awk '{print $3}'` and `grep -q "16"` do not.
    """

    tail = text[matcher.start() :]
    if ref.search(tail):
        return True
    return any(re.search(r"\b" + re.escape(tool) + r"\b", tail) for tool in TOOLS)


def _selects_on_var(body: list[str], var: str) -> bool:
    """Does this loop BODY select a subset of the loop's names on `$var`?

    ⛔ A LOOP DOES NOT COVER ITS HEADER'S NAMES IF ITS BODY CHOOSES BETWEEN THEM
    (#12069 review P2). `scan()` credited EVERY name in `for tool in pg_dump
    pg_restore; do` the moment the body invoked `$tool --version` — so a body
    that only checked one of them

        for tool in pg_dump pg_restore; do
          if [ "$tool" = pg_dump ]; then
            version="$("$tool" --version)"
          fi
        done

    still reported BOTH, with `pg_restore` never checked (measured), which is the
    same defect the loop body was introduced to fix (round 11), one level down.

    THREE CUTS OF THIS RULE EACH MISSED A SPELLING, and every miss was either
    fail-open (both tools credited, `pg_restore` unchecked) or a false RED on a
    body that DID check both:

        if [ "${tool%_restore}" = pg_dump ]; then … fi   # cut 1: a TRANSFORM of $tool
        if printf '%s' "$tool" | grep -qx pg_dump; …     # cut 1: no operator at all
        if printf '%s' "$tool" | fgrep -qx pg_dump; …    # cut 2: `\bgrep\b` misses fgrep
        t=$tool; if [ "$t" = pg_dump ]; then …          # cut 2: the copy is invisible
        [ -n "$version" ] || { echo "::error::$tool…"; exit 1; }   # cut 2: FALSE RED
        if [ -n "$PG" ] && [ "$tool" = pg_dump ]; …      # cut 3: only the FIRST span
        v="$("$tool" --version | awk '{print $3}')"     # cut 3: any mention of awk
        log="$tool"   # … then used as a filename       # cut 3: the copy itself

    The rule is therefore, per line — with loop-variable ALIASES substituted back
    in, to a FIXED POINT (`_copied_to` / `_with_aliases`), so a copy used only as a
    file name costs nothing:
      * a brace expansion that TRANSFORMS the value (`${tool%…}`, `${tool#…}`,
        `${tool/…}`) selects — every name takes it differently;
      * a PATTERN MATCHER (`case`, `grep`/`egrep`/`fgrep`, `awk`, `expr`) selects
        when ITS OWN operand names the loop variable or a client (`grep -qx
        pg_dump`), NOT merely because the word appears somewhere on the line;
      * a CONDITION (`[`, `[[`, `test`) selects when the loop variable is an
        OPERAND of a comparison in the same CLAUSE, so a selection in a second
        `&&`-joined group is not invisible — and a variable merely PRINTED in a
        branch's message is not read as one.

    Only a SELECTION on the LOOP VARIABLE counts: a condition on anything else
    (`if [ "$major" != "16" ]`, which is what this job actually writes) does not
    narrow the coverage, and reading it as one would red the correct tree. The
    brace arm is name-bounded, so `${tools_dir}` — a DIFFERENT variable sharing a
    prefix — is not read as a transform of `$tool` (measured false RED).
    """

    ref = re.compile(
        r"(?P<brace>\$\{" + re.escape(var) + r"(?![A-Za-z0-9_])[^}]*\})"
        r"|\$" + re.escape(var) + r"(?!\w)"
    )
    depth = 0
    for line in _with_aliases_each(body, var):
        match = ref.search(line)
        if match:
            brace = match.group("brace")
            if brace is not None and brace != "${" + var + "}":
                return True
            matcher = PATTERN_MATCHER_RE.search(line)
            if matcher and _matches_on(line, matcher, ref):
                return True
        clauses, depth = _comparison_clauses(line, depth=depth)
        for clause in clauses:
            if ref.search(clause):
                return True
    return False


def _version_checked_tools(code: str) -> set[str]:
    """Which client tools this code actually VERSION-CHECKS (#12051 rounds 11-12).

    Coverage is what the code RUNS, in two accepted shapes: a `for <var> in …; do`
    loop whose body invokes `$<var> --version`, and a `<tool> --version` command.
    A bare `for` HEADER naming both tools does not count (round 11: a body
    hard-coding `pg_dump --version` left `pg_restore` unchecked and every row
    green), and neither does a QUOTED mention (round 12: `$(echo "$tool
    --version")` runs nothing). Both are the token-in-text defect rounds 4 and 7
    fixed for `::error::` / `exit 1`.

    ACCEPTED SPELLING: a loop body that runs unconditionally. A body that
    SELECTS between the names on the loop variable (`if [ "$tool" = pg_dump ]`,
    `case "$tool" in`, `[ "$tool" = pg_restore ] && continue`) credits NOTHING,
    because how many of the names such a body reaches is not decidable from text
    (`_selects_on_var`, #12069 review P2). Check every tool unconditionally, or
    check each one on its own `<tool> --version` line — the two shapes this
    guard can read, and the second needs no loop at all.
    """

    checked: set[str] = set()
    lines = code.splitlines()

    def scan(line: str, loop_var: str | None, loop_names: set[str]) -> None:
        line = BRACED_VAR_RE.sub(r"$\1", line)
        for match in re.finditer(r"--version\b", line):
            word = _command_word(line[: match.start()])
            if not word or word in PRINTING_COMMANDS:
                continue
            if loop_var and word in (f"${loop_var}", f"${{{loop_var}}}"):
                checked.update(loop_names)
            elif word in TOOLS:
                checked.add(word)

    def loop_names(raw: str) -> set[str]:
        """The tools a header names LITERALLY. Any `$` in the list names NOTHING.

        ⛔ A variable list is REFUSED, not resolved (#12069 reviews 14-16, each
        measured): expanding `$TOOLS` to this module's tuple graded
        `TOOLS=pg_dump` + `for tool in $TOOLS` GREEN with `pg_restore` never
        checked; reading the code's assignment is not enough (the shell uses the
        LAST one); and a DEFAULT (`${TOOLS:-pg_dump pg_restore}`) is a literal
        the guard would credit while the shell iterates something else.
        """

        if "$" in raw:
            return set()
        return {
            name for name in re.findall(r"[A-Za-z_][\w-]*", raw) if name in TOOLS
        }

    for index, line in enumerate(lines):
        body_start = index + 1
        head = LOOP_HEAD_INLINE_RE.match(line)
        if not head:
            head = LOOP_HEAD_OPEN_RE.match(line)
            if head:
                # POSIX two-line form: `do` must open the loop on the NEXT line
                # that would run (blank and comment-only lines allowed).
                for offset in range(index + 1, len(lines)):
                    probe = lines[offset]
                    if LOOP_DO_RE.match(probe):
                        body_start = offset + 1
                        break
                    if probe.strip() and not probe.lstrip().startswith("#"):
                        head = None
                        break
                else:
                    head = None
        if not head:
            scan(line, None, set())
            continue
        names = loop_names(head.group("names"))
        var = head.group("var")
        body: list[str] = []
        rest = head.groupdict().get("rest") or ""
        # ⛔ THE ONE-LINE FORM'S BODY IS `rest` (#12069 review 3): the text after
        # `do` on the header line. It was scanned with `names` regardless, while
        # `_selects_on_var` only ever saw the lines BELOW — so a selection written
        # on the `do` line was invisible and both tools were credited although one
        # is skipped:
        #     for tool in pg_dump pg_restore; do [ "$tool" = pg_restore ] && continue
        # `rest` is part of the body for the selection question too.
        closes_here = bool(re.search(r"(?:^|[;&])[ \t]*done\b", rest))
        # ⛔ AND A TOP-LEVEL `break`/`continue` ON THE `do` LINE CREDITS NOTHING
        # (#12069 round 17): `for tool in pg_dump pg_restore; do break; "$tool"
        # --version; done` credits both names from a `--version` the shell never
        # reaches. A bare `break` names no loop variable, so the selection rule
        # above cannot see it.
        inline_truncated = _inline_truncates(rest, closes_here)
        if rest.strip() and closes_here:
            scan(
                rest,
                var,
                set()
                if inline_truncated or _selects_on_var([rest], var)
                else names,
            )
            continue  # closed on its own line: no body to follow
        for body_line in lines[body_start:]:
            if LOOP_END_RE.match(body_line):
                break
            body.append(body_line)
        # ⛔ A SELECTING BODY COVERS NONE OF THE HEADER'S NAMES (#12069 review P2).
        graded = ([rest] if rest.strip() else []) + body
        # ⛔ A TOP-LEVEL `break`/`continue` CREDITS NOTHING (#12069 review round
        # 17): it leaves the iteration before the body below it runs, so the
        # `--version` invocation is text the shell never reaches. Same polarity as
        # a selecting body — a body that does not run its checks covers none of
        # the header's names — and `_truncated_loop_bodies()` names the cause
        # where this row would otherwise only report the tool as unchecked.
        truncated = inline_truncated or _truncated_at_top_level(body)
        credited = set() if truncated or _selects_on_var(graded, var) else names
        if rest.strip():
            scan(rest, var, credited)
        for body_line in body:
            scan(body_line, var, credited)
    return checked


def _keeps_the_pin_first(value: str, pinned: str) -> bool:
    """Is the pinned dir — or the whole inherited `$PATH` — the FIRST entry?

    ⛔ CONTAINMENT IS NOT PRECEDENCE (#12069 review P1). The rule used to accept
    any value that merely MENTIONED the pinned dir or `$PATH`, so

        export PATH="/usr/bin:$PATH"

    was GREEN: the pin is still ON the value, but it is on SECOND. A `PATH`
    lookup takes the FIRST match, and `/usr/bin/pg_dump` is `pg_wrapper` — which
    this module's own docstring says resolves to the image's NEWEST client — so
    that one line restored #12051 with every row green (measured, and again at
    `${PATH}` and at `$PATHX`). The pin is therefore kept only when nothing
    outranks it: the first `:`-separated entry must BE the pinned dir, or a
    reference to the inherited `$PATH` (the runner has already prepended the pin
    to it, so `$PATH` first still puts the pin first).
    """

    if value.startswith("${"):
        # A PARAMETER EXPANSION CONTAINS ITS OWN COLON (`${PATH:-/usr/bin}`), so
        # splitting on `:` first would read the head as `${PATH`. Cut the whole
        # brace group instead, and accept only the forms that still EXPAND TO
        # `$PATH`: `${PATH}` and the default-if-unset spellings `${PATH:-…}` /
        # `${PATH-…}` (PATH is always set, so they are `$PATH`). `${PATH+…}` and
        # `${PATH:+…}` are NOT — they expand to the alternate value.
        end = value.find("}")
        head = value[: end + 1] if end != -1 else value.split(":", 1)[0]
        return head in (pinned, "${PATH}") or head.startswith(
            ("${PATH:-", "${PATH-")
        )
    head = value.split(":", 1)[0]
    return head == pinned or head == "$PATH"


def _path_assignments_that_drop_the_pin(
    steps: list[dict],
    start: int,
    server_major: int,
    job: dict | None = None,
    end: int | None = None,
) -> list[str]:
    """PATH assignments at or after the pin that no longer keep it FIRST.

    A value keeps the pin only when its FIRST entry is the pinned dir or
    `$PATH`/`${PATH}` — see `_keeps_the_pin_first` for why mere containment was
    not enough. So the pin's own
    `export PATH="/usr/lib/postgresql/16/bin:$PATH"` and every APPEND
    (`PATH="$PATH:/x"`, `PATH=${PATH}:/x`) stay green, while a PREPEND of any
    other directory (`PATH="/usr/bin:$PATH"`, `PATH="/usr/local/bin:$PATH"`)
    is an offender, and a diagnostic `echo "PATH=$PATH"` is not an assignment at
    all.

    `end` bounds the scan at the LEG step (inclusive), the same window
    `_env_path_replacements` takes and for the same reason: a step AFTER the leg
    cannot move a PATH a leg that already ran has resolved, so grading it reds
    the CORRECT tree. The two rules take one window so an author never gets two
    verdicts for one line.
    """

    pinned = f"/usr/lib/postgresql/{server_major}/bin"
    offenders: set[str] = set()
    window_end = len(steps) if end is None else min(end + 1, len(steps))
    for index in range(start, window_end):
        for line in (
            _effective_shell(job, steps[index]) + "\n" + _run_commands(steps[index])
        ).splitlines():
            for match in PATH_ASSIGN_RE.finditer(line):
                if _keeps_the_pin_first(match.group("value"), pinned):
                    continue
                if _command_word(line[: match.start()]) in PRINTING_COMMANDS:
                    continue
                offenders.add(
                    f"step {index} puts a directory ahead of the client PATH (or "
                    f"replaces it): {line.strip()!r}"
                )
    return sorted(offenders)


#: `$GITHUB_ENV` is the SECOND command file (#12069 review P1). `$GITHUB_PATH` is
#: not the only way to move a variable for later steps: a step that writes
#: `PATH=/usr/bin` to `$GITHUB_ENV` moves the leg's PATH exactly as `env:` does,
#: and nothing here read it. Nothing in this job names `$GITHUB_ENV`, so the name
#: itself is refused outright — the same polarity as `_runner_file_commands`, and
#: for the same reason: a write this guard cannot classify is an offender.
GITHUB_ENV_MENTION_RE = re.compile(r"\bGITHUB_ENV\b")


def _env_path_replacements(
    steps: list[dict],
    server_major: int,
    job: dict | None = None,
    start: int = 0,
    end: int | None = None,
) -> list[str]:
    """`env: PATH` entries that move the leg's PATH off the pinned dir.

    ⛔ `env:` WAS NEVER READ (#12069 review P1). Every PATH rule here read
    `run:` / `shell:` ASSIGNMENTS, and `env:` is a different mechanism entirely:
    GitHub applies it to the step's environment, so

        env:
          PATH: /usr/bin:/bin:/usr/local/bin

    replaces the runner's composed PATH — pin included — for that step, and for
    every later step at job scope. `/usr/bin/pg_dump` is `pg_wrapper` (the
    image's newest client), so this is #12051 restored with every row green
    (measured: the job-level form left the whole file `67 passed`). Workflow
    scope is folded into the job by the `job()` fixture, so job scope here
    covers both.

    ⛔ AND `$PATH` DOES NOT RESCUE AN `env:` VALUE: GitHub does not expand shell
    variables inside `env:` (`${{ }}` expressions are the only substitution), so
    `PATH: $PATH:/x` sets PATH to the LITERAL string `$PATH:/x` — the pin is
    gone, not kept. The pin is therefore kept only when the value literally
    BEGINS with the pinned dir, which is stricter than the shell-assignment rule
    above on purpose: there the shell does the expansion.

    ⛔ STEP SCOPE IS WINDOWED AT BOTH ENDS, `[start, end]` (#12069 round-15
    review P2, twice). A step-scope `env:` applies to THAT STEP ALONE
    (`jobs.<id>.steps[*].env`), so it can only move the leg if it sits between
    the pin and the step that RUNS the leg:
      * a PRE-PIN step cannot outrank a pin that runs later — grading it red the
        CORRECT tree (measured: a `PATH` on the pre-pin setup step, e.g. a
        venv-first install);
      * a POST-LEG step cannot reach a leg that already ran — grading it red the
        CORRECT tree too (measured: a `PATH` on the last, teardown step).
    `end` is the LEG step's index, INCLUSIVE: the leg's own `env:` is the most
    dangerous position there is. JOB and WORKFLOW scope are position-independent
    — they reach the leg from anywhere — so neither bound applies to them.

    ⛔ AND A `uses:` STEP'S `env:` IS *READ*: #12069 round-15 review 2 caught this
    docstring claiming the opposite ("a `uses:`-step `env:` … which this file
    cannot read at all") while `step.get("env")` reads it for every step. What
    is unreadable is the composite ACTION's own body, not the YAML `env:` this
    job attaches to it.
    """

    pinned = f"/usr/lib/postgresql/{server_major}/bin"
    window_end = len(steps) if end is None else min(end + 1, len(steps))
    found: set[str] = set()
    scopes: list[tuple[str, object]] = [("job", (job or {}).get("env"))]
    scopes += [
        (f"step {index}", steps[index].get("env"))
        for index in range(start, window_end)
    ]
    for where, env in scopes:
        if not isinstance(env, dict):
            continue
        for key, value in env.items():
            if str(key).upper() != "PATH":
                continue
            if str(value).split(":", 1)[0] == pinned:
                continue
            found.add(
                f"{where} sets `env: PATH` to {value!r}, which does not start "
                "with the pinned dir (`env:` is not shell-expanded, so `$PATH` "
                "there is the literal string)"
            )
    return sorted(found)


def _pin_step(steps: list[dict]) -> tuple[int, dict]:
    """The one step that INSTALLS a PostgreSQL client package.

    The install VERB is required (#12051 review round 8): matching the package
    name alone made `dpkg -l postgresql-client-16 | tail -2` — a read-only
    diagnostic — a "second installer", which red four rows on a correct tree
    with a message that sends the reader hunting an installer that does not
    exist (measured).

    Read through `_run_commands()`, like every other line-unit search here
    (`_run_code()` only drops whole comment LINES). Measured: ONE trailing
    comment in the leg step that names the install form — the capstone's own
    "was: `apt-get install postgresql-client-17`" note — made the leg a second
    installer and red the row with `found 2`, a false RED on a correct
    workflow, whose message then sends the reader hunting an installer that does
    not exist. The same read let an UNVERSIONED `apt-get install
    postgresql-client` pass with the versioned name carried only by a comment
    (`CLIENT_PKG_RE` found the comment, not the install).
    """
    hits = [
        (index, step)
        for index, step in enumerate(steps)
        if CLIENT_PKG_RE.search(_run_commands(step))
    ]
    # ⛔ EVERY installer, not the first (#12051 review round 13): `search()` graded
    # the FIRST mention, so a step that installs 16 and then falls back
    # (`… postgresql-client-16 || … postgresql-client-17`) read as a clean pin —
    # the view depended on LINE ORDER (17 first red, 17 second green, measured).
    for _, step in hits:
        majors = [
            int(match.group(1)) for match in CLIENT_PKG_RE.finditer(_run_commands(step))
        ]
        assert set(majors) == {majors[0]}, (
            f"the pin installs MORE THAN ONE client major ({sorted(set(majors))}) — "
            "a `||` fallback moves the client off the servers' major exactly when "
            "the pinned one is unavailable, which is #12051 with an alibi (#12051 "
            "review round 13, measured)"
        )
    assert len(hits) == 1, (
        "expected exactly ONE PostgreSQL-client pin step in "
        f"`{JOB}`; found {len(hits)} — a second installer makes the client "
        "whichever one ran last, which is the class #12051 closed"
    )
    return hits[0]


def _postgres_image_majors(job: dict) -> list[int]:
    """The major of every provisioned-Postgres image pin in this job."""
    majors: list[int] = []
    for step in _steps(job):
        image = str((step.get("with") or {}).get("image") or "")
        # `pgvector/pgvector:pg16` / `postgres:16` — matched by REPOSITORY, not
        # by a `"postgres" in image` substring: the pinned image this job uses
        # is named `pgvector`, which does not contain `postgres`.
        if "pgvector" not in image and "postgres" not in image:
            continue
        # The digest is not part of the TAG (`image:pg16@sha256:…` still pins 16),
        # and neither is the REGISTRY: `ghcr.io/mirror:5000/pgvector/pgvector:pg16`
        # read as major 5000 — the PORT — and red four rows on a correct pin with a
        # message describing a divergence that does not exist (round 14). Only the
        # last path component is the repository+tag.
        match = IMAGE_RE.search(image.split("@", 1)[0].rsplit("/", 1)[-1])
        assert match, (
            f"postgres image pin {image!r} does not name its major "
            "(`:pg<major>` or `:<major>`); an unpinned major cannot be matched "
            "by the client pin, which is how #12051 shipped"
        )
        majors.append(int(match.group(1) or match.group(2)))
    return majors


def test_the_two_provisioned_servers_are_one_major(job: dict) -> None:
    """D8 restores ACROSS the two servers, so their majors must not diverge."""
    majors = _postgres_image_majors(job)

    assert len(majors) == 2, (
        "expected TWO provisioned Postgres servers in "
        f"`{JOB}` (the record, and D8's scratch target — the drill REFUSES a "
        f"scratch target that is the record itself); found {len(majors)}"
    )
    assert len(set(majors)) == 1, (
        "the record server and the D8 scratch server must be the SAME major: "
        f"the drill restores one into the other, found {majors}"
    )


def test_the_client_pin_names_the_servers_major_and_prepends_it(job: dict) -> None:
    """`postgresql-client-<major>` AND `/usr/lib/postgresql/<major>/bin` ON `$GITHUB_PATH`.

    Both halves are load-bearing: the package supplies the binaries, and the
    prepend is what makes them win over the runner image's own (newer) client
    and over `postgresql-common`'s unversioned wrapper.

    ⛔ THE PREPEND IS ASSERTED AS ONE FACT, NOT TWO (#12051 review P2). The
    first cut checked "the versioned dir appears somewhere in the step" and
    "`GITHUB_PATH` is written somewhere in the step" — two independent
    disjunctions. Measured: a step whose `$GITHUB_PATH` write says `/usr/bin`
    while an intra-step `export PATH=/usr/lib/postgresql/16/bin:…` stays behind
    passed all four tests, and on the LEG that is the full revert — `/usr/bin`
    resolves through `pg_wrapper` to the image's newest client (17), while the
    in-step check still prints 16 because it runs under the export. The write
    line is therefore matched as a unit below.

    ACCEPTED SPELLING: exactly one — the literal
    `echo "/usr/lib/postgresql/<major>/bin" >> "$GITHUB_PATH"` line. An
    equivalent write of the SAME directory in another spelling (a trailing
    `/bin/`, `printf … | tee -a "$GITHUB_PATH"`, `echo … | sudo tee -a …`) is
    REFUSED, loudly, with this accepted form in the message (measured, round 7).
    That is the deliberate polarity: one spelling the guard can read beats a
    rule broad enough to accept a spelling it cannot.

    ⛔ AND THE WRITE NEED NOT BE IN THE INSTALL STEP (#12051 review round 8):
    the population is `[pin_index, leg_start)` — at or after the install, before
    the leg. Splitting "install" from "prepend + assert" is a correct edit (the
    prepend still reaches the leg) and used to red two rows with a message that
    misdescribed it (measured). Inside that window it is the same "later wins"
    precedence the position row already grades.
    """
    steps = _steps(job)
    image_majors = _postgres_image_majors(job)
    assert image_majors, "no pinned Postgres server image found — see the test above"
    server_major = image_majors[0]

    pin_index, pin = _pin_step(steps)
    text = _run_commands(pin)
    leg_indexes = _leg_indexes(steps)
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    leg_start = min(leg_indexes)

    # ⛔ A PREPEND INSIDE A CONSTANT-CONDITION BLOCK IS PRESENT AND INERT (#12051
    # review round 8). Every rule below reads a line as a unit, so an enclosing
    # `if false; then … fi` around the prepend satisfied all of them while the
    # write never executed — measured, five rows GREEN, leg on the inherited
    # client. Reachability is asserted before the construct.
    unreachable = _unreachable_guards(steps, pin_index, leg_start, job)
    assert not unreachable, (
        "a step between the client install and the record leg cannot RUN the "
        f"code it is graded on: {unreachable!r}. A guard whose condition is a "
        "literal constant (`if false`, `elif false`, `while false`, `until "
        "true`) or a dead chain (`false && …`, `true || …`) makes the prepend "
        "and the fail-closed branch present AND inert, so the leg resolves on "
        "the runner image's own client while every row here stays green "
        "(#12051)"
    )

    pkg = CLIENT_PKG_RE.search(text)
    assert pkg, f"the pin step does not install `postgresql-client-<major>`: {text!r}"
    assert int(pkg.group(1)) == server_major, (
        f"the client pin installs major {pkg.group(1)} but the provisioned "
        f"servers are pg{server_major}. A client NEWER than the target server "
        "dumps a preamble the target cannot execute — `SET transaction_timeout` "
        "from a PG17 client into a PG16 server is #12051"
    )

    # ONE regex over the WHOLE command: the versioned dir must be the value
    # WRITTEN TO `$GITHUB_PATH`. A bare `CLIENT_PATH_RE`/`"GITHUB_PATH" in text`
    # pair is satisfiable by two different lines and by an inert `export`.
    pin_mentions = [
        write for write in _github_path_writes(steps, job) if pin_index <= write[0] < leg_start
    ]
    assert pin_mentions, (
        "no step between the client install and the record leg writes a "
        "`/usr/lib/postgresql/<major>/bin` entry to `$GITHUB_PATH`; a prepend "
        "that only exists as an `export` inside a step cannot reach the pytest "
        "leg, so the leg would resolve `pg_dump` from the runner image instead "
        f"(install step text: {text!r})"
    )
    # ⛔ EVERY write, not just the first (#12051 review round 5). `search()`
    # returns the FIRST match, so a SECOND append naming another major was
    # invisible: measured, `/usr/lib/postgresql/16/bin` followed by
    # `/usr/lib/postgresql/17/bin` left every row GREEN while the leg ran on
    # pg17 — the #12051 defect, restored. The runner reads the file in ORDER and
    # does `PrependPath.RemoveAll(x => x == line); PrependPath.Add(line);` per
    # line (actions/runner `FileCommandManager.AddPathFileCommand.
    # ProcessCommand`), so the LAST line is the one that wins on PATH.
    # ⛔ And round 6 made this a MENTION scan: the write is detected whatever
    # its producer or redirect, then required to be classifiable, so
    # `printf`/`tee`/`/usr/bin`/a continued redirect cannot slip past unseen.
    unreadable = [line for _, line, major in pin_mentions if major is None]
    assert not unreadable, (
        "a step between the client install and the record leg writes "
        "`$GITHUB_PATH` in a form this guard cannot read, so it cannot prove "
        f"which client major wins on the leg: {unreadable!r}. Write the "
        "versioned dir as a literal "
        f'`echo "/usr/lib/postgresql/<major>/bin" >> "$GITHUB_PATH"` line.'
    )
    wrong = [str(major) for _, _, major in pin_mentions if major != server_major]
    assert not wrong, (
        f"a step between the client install and the record leg writes "
        f"pg{', pg'.join(wrong)} to `$GITHUB_PATH` alongside "
        f"its pg{server_major} entry — the runner adds each line in file order, "
        "so the LAST one wins on the leg and the versioned binaries lose to "
        "another major (measured, round 5: a second append naming pg17 left "
        "every row green; this is how #12051 came back)"
    )


def test_no_step_rewrites_the_client_path_to_another_major(job: dict) -> None:
    """No line in this job may name a client major other than the servers'.

    `$GITHUB_PATH` is JOB-scoped and last-writer-wins, so a LATER step appending
    `/usr/lib/postgresql/<other>/bin` neutralises the pin completely while every
    other row here stays green — measured, round 5: a step inserted between the
    pin and the leg that appends pg17 left every row PASS while `pg_dump` on
    the leg resolved from pg17, i.e. exactly the artifact #12051 shipped. The
    pin step's own write is asserted above; this asserts nobody else undoes it.

    ⛔ AND `$GITHUB_PATH` IS NOT THE ONLY WAY (#12051 review round 7). The leg's
    OWN script can `export PATH="/usr/lib/postgresql/<other>/bin:$PATH"`, and a
    step-script assignment runs after the runner composes the prepend, so it
    WINS (actions/runner `Handler.AddPrependPathToEnvironment` →
    `ScriptHandler`). Measured GREEN before this rule, with the in-job
    `pg_dump --version` assertion still printing 16 because it lives in the PIN
    step. So the population here is `_client_dir_mentions()` — every line that
    names a versioned client dir, whatever it does with it, in the step's `run:`
    script or the `shell:` it executes through.

    ⛔ AND THE BINARY ITSELF CAN BE REPLACED (#12051 review round 9), which no
    "may the job NAME another major" rule can catch when the base path arrives in
    a variable: `b=/usr/lib/postgresql; sudo ln -sf "$b/17/bin/pg_dump"
    "$b/16/bin/pg_dump"` and a `find`-derived `cp …/16/bin/pg_dump` were both
    measured GREEN, and both leave the leg resolving the pg17 binary out of the
    pinned dir. So a MUTATING verb carrying a client binary name is refused at
    or after the pin, however the source is spelled. The pin's own
    `postgresql-client-<n>` install line is the one exclusion.

    Read through `_run_commands()` for the same reason as every other search
    here: a commented-out line is inert shell that reads identically to the live
    one.

    ACCEPTED SPELLING / PRECISION: a `$GITHUB_PATH` mention the guard cannot
    classify is an offender only at or AFTER the pin — a pre-pin write of
    something unrelated (`echo "$HOME/.local/bin" >> "$GITHUB_PATH"`) cannot
    outrank a later write, so it stays green (measured, round 7). A mention that
    NAMES another major is an offender wherever it lives.

    STATED LIMIT: a step that merely INVOKES a script (`run: bash x.sh`) is read
    as that one line; a versioned dir chosen inside `x.sh` is invisible. No
    in-repo script this job calls names one — the pin's dir is inline.
    """
    steps = _steps(job)
    server_major = _postgres_image_majors(job)[0]
    pin_index, _ = _pin_step(steps)
    # The window's upper bound is the step that RUNS the leg: a PATH set after a
    # leg that already ran cannot move it (#12069 round-15 review 2).
    leg_start = min(_leg_indexes(steps))

    mentions = _github_path_writes(steps, job)
    assert mentions, (
        "no step in this job writes a `/usr/lib/postgresql/<major>/bin` entry to "
        "`$GITHUB_PATH` — see the pin-step assertion above"
    )

    # Only a write at or AFTER the pin can outrank it (later wins, and within a
    # step the script's own assignments win over the prepend), so a pre-pin
    # write of something else is NOT an offender — the precision costs one
    # comparison and keeps a correct tree green. A write that names ANOTHER
    # major is an offender wherever it lives.
    offenders = sorted(
        {
            f"step {index} writes `$GITHUB_PATH` as {line!r}, "
            + (
                f"putting pg{major} on PATH"
                if major is not None
                else "which this guard cannot classify"
            )
            for index, line, major in mentions
            if major != server_major and (major is not None or index >= pin_index)
        }
    )
    other_majors = sorted(
        {
            (
                f"step {index} names pg{major}: {line!r}"
                if major is not None
                else f"step {index} names a client dir whose major this guard cannot read: {line!r}"
            )
            for index, line, major in _client_dir_mentions(steps, job)
            if major != server_major
        }
    )
    # ⛔ `shell:` IS EXECUTED ARGV HERE TOO (#12051 review rounds 13-14). Round 13
    # taught the executed shell to three scans and left these two reading `run:`
    # only, so the rule whose whole point is spelling-independence ("however the
    # source is spelled", above) was defeated by one `shell:` line: measured
    # `58 passed` with the leg's `shell:` running
    # `ln -sf "$b/17/bin/pg_dump" "$b/16/bin/pg_dump"` — and the identical line in
    # `run:` correctly red — plus the same for the `_runner_file_commands` glob and
    # at workflow-level `defaults.run.shell`.
    replacements = sorted(
        set(_path_assignments_that_drop_the_pin(steps, pin_index, server_major, job, leg_start))
        # ⛔ `env:` AND `$GITHUB_ENV` MOVE THE SAME PATH (#12069 review P1). Both
        # were unread, so both restored #12051 with every row green. STEP scope is
        # windowed from the pin, like `_path_assignments_that_drop_the_pin` above.
        | set(_env_path_replacements(steps, server_major, job, pin_index, leg_start))
        | {
            f"step {index} mentions `$GITHUB_ENV`: {line!r}"
            for index, step in enumerate(steps)
            for line in _executed_lines(job, step)
            if GITHUB_ENV_MENTION_RE.search(line)
        }
        | {
            f"step {index} REPLACES a client binary: {line!r}"
            for index, step in enumerate(steps)
            # ⛔ NOT WINDOWED FROM THE PIN (#12069 review round 2, measured). The
            # PATH rows share that window because a PRE-PIN write is outranked by
            # the later prepend — a binary REWRITE is not: `apt-get install
            # postgresql-client-16` on a box that already has the package is a
            # NO-OP, so a rewrite above the pin survives it. Measured: appending
            # `: > /usr/lib/postgresql/16/bin/pg_dump` to the pre-pin install
            # step's `run:` left `187 passed`. The pin's own install line stays
            # excluded by `CLIENT_PKG_RE` inside `_replaces_a_client_binary`.
            for line in _executed_lines(job, step)
            if _replaces_a_client_binary(line)
        }
        | {
            f"step {index} writes the runner's own command file: {line!r}"
            for index, step in enumerate(steps)
            for line in _executed_lines(job, step)
            if RUNNER_FILE_COMMAND_RE.search(line)
        }
    )
    assert not (offenders or other_majors or replacements), (
        "a `$GITHUB_PATH` write, a step's own PATH entry, a PATH ASSIGNMENT that "
        "drops the pinned dir or puts something AHEAD of it, an `env: PATH` that "
        "does not begin with the pinned dir, a `$GITHUB_ENV` write, or a "
        "REPLACEMENT of a client binary changes which client the leg runs: "
        + "; ".join(offenders + other_majors + replacements)
        + ". Later writes and in-step assignments both WIN over the pin, and a "
        "replaced binary is what the pinned dir resolves, so the leg would run "
        "on that client instead (#12051)."
    )


def test_the_client_pin_fails_closed_on_a_mismatch(job: dict) -> None:
    """An inherited client must RED the job, not silently produce a bad artifact.

    Without this the pin degrades on a runner-image change: the artifact would
    still be dumped by the newer client, and D8 would red with a message that
    blames the record path (#12051's whole complaint).

    ⛔ THE CONDITION IS ASSERTED, NOT JUST ITS MESSAGE (#12051 review P3).
    `"::error::" in text and "exit 1" in text` cannot tell an ARMED branch from
    a disarmed one: measured, `if [ "$major" != "16" ]; then` → `if false; then`
    left all four tests green while the job would never fail closed. The
    comparison literal is therefore pinned to the servers' major, and the
    announce/stop pair is matched as WHOLE LINES rather than substrings (round
    7): `false && exit 1` and `bail() { exit 1; }` contain the stop and never
    take it.

    ACCEPTED SPELLING: exactly one — the condition and `then` on the SAME line,
    a single-bracket `[ … ]` test, a flat body, and `fi` alone on its own line:

        if [ "$major" != "<major>" ]; then
          echo "::error::…"
          exit 1
        fi

    Three equivalents are REFUSED, each measured as a false RED at 4/5: `then`
    on its own line, the whole branch on one line (`if …; then …; fi`), and the
    bash-only `[[ … ]]`. That is the same deliberate polarity as the prepend's
    accepted form — one spelling the guard can read beats a rule broad enough
    to accept a spelling it cannot — and the refusal message names the accepted
    form above.

    ⛔ AND AN ARMED BRANCH IS NOT ARMED IF ITS ENCLOSING BLOCK CANNOT RUN
    (#12051 review round 8). Nothing refused an ENCLOSING `if false; then … fi`
    around the prepend or around the whole fail-closed `for` loop: measured,
    all five rows GREEN with the branch itself untouched. `_unreachable_guards()`
    is asserted here for that reason.

    ⛔ AND THE REFUSAL IS SCOPED TO "NEVER RUNS" (#12069 review). The first cut of
    that scan refused any bare constant, so `while true; do … break; done` — the
    ordinary retry spelling, which RUNS — red this row on a correct tree. The
    accepted set is now exactly the forms whose body is never entered, and
    `REFUSAL_POLARITY` / `test_the_refusal_polarity_is_never_runs` below pins
    both directions — IN THIS MODULE, because a pin kept in `.worktree-report/`
    is `.gitignore`d and pins nothing once the branch merges.

    STATED LIMIT: the branch must be FLAT (`FAIL_CLOSED_BRANCH_RE`) — a nested
    conditional inside it is refused rather than reasoned about, because a
    hermetic text guard cannot prove a nested branch is reachable. An
    `if … else … fi` variant is refused for the same reason; there is no such
    branch in the real step.

    STATED LIMIT: this is a hermetic text guard, so it cannot prove the
    version-EXTRACTION arithmetic — a `sed` rewritten to a constant (`s/.*/16/`)
    still passes. The alternative is arming two real client majors in CI, which
    this job cannot do without provisioning a second client version; the
    in-job assertion (`pg_dump --version` printed and checked) is what makes a
    constant extraction visible in the log instead.

    ⛔ AND AN ARMED BRANCH IS NOT ARMED IF IT CANNOT RED (#12051 review round 8).
    `continue-on-error: true` on the pin step makes `exit 1` set
    `Result = TaskResult.Succeeded` (actions/runner
    `ExecutionContext.ApplyContinueOnError`), and `if: false` skips the branch
    outright — both measured GREEN. The check is on the steps that actually
    CARRY the write and the branch (round 9: keying it on the whole
    `[install, leg)` window red an unrelated step's `continue-on-error: true`
    while calling that step "a step carrying the client pin", which it is not),
    and it reads VALUES, not presence — `continue-on-error: false` and
    `if: success()` skip nothing, and YAML parses `if: false` as boolean
    `False`, so a truthiness read sees "absent".
    """
    steps = _steps(job)
    pin_index, _ = _pin_step(steps)
    server_major = _postgres_image_majors(job)[0]
    leg_indexes = _leg_indexes(steps)
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    leg_start = min(leg_indexes)

    # INERT-BUT-PRESENT first: an enclosing constant-condition block leaves every
    # structural assertion below satisfied and the branch unable to fire.
    unreachable = _unreachable_guards(steps, pin_index, leg_start, job)
    assert not unreachable, (
        "a step between the client install and the record leg cannot RUN the "
        f"code it is graded on: {unreachable!r}. `if false; then … fi` around "
        "this branch leaves its `::error::`/`exit 1` pair in the text and makes "
        "it unreachable, so the job runs the leg on the inherited client and "
        "exits 0 (#12051, measured round 8)"
    )

    # The branch must be able to RUN and able to RED, or it is not armed. Every
    # step in the window is checked, not only the install step: splitting
    # "install" from "prepend + assert" is a correct edit and the assertion
    # travels with the `major` it compares (round 8).
    hits = [
        (index, level)
        for index, level in (
            (index, FAIL_CLOSED_BRANCH_RE.search(_run_commands(step)))
            for index, step in enumerate(steps)
            if pin_index <= index < leg_start
        )
        if level
    ]
    skippable = [
        index
        for index in sorted(
            {
                index
                for index, _, _ in _github_path_writes(steps, job)
                # ⛔ WINDOWED like `hits` (#12069 review). A PRE-PIN write of an
                # unrelated directory cannot outrank the pin (later wins), and a
                # post-leg write cannot reach the leg — so a best-effort prepend
                # step before the pin that is ALLOWED TO FAIL is none of this
                # row's business. Job-wide, that unrelated step red this row
                # with a message saying it "carries the client pin and its
                # assertion", which it carries neither of (measured), and which
                # row 3's docstring explicitly documents as staying green.
                if pin_index <= index < leg_start
            }
            | {index for index, _ in hits}
        )
        if _conditional_skip(steps[index])
    ]
    assert not skippable, (
        "the step(s) carrying the client pin and its assertion are SKIPPABLE — "
        + "; ".join(
            f"step {index} sets {_conditional_skip(steps[index])}"
            for index in skippable
        )
        + ". `continue-on-error: true` makes `exit 1` set "
        "`Result = TaskResult.Succeeded` (actions/runner "
        "`ExecutionContext.ApplyContinueOnError`), and a false condition skips "
        "the pin and the assertion with it. Either way "
        "the leg runs on the inherited client and the job can stay green "
        "(#12051). Only steps CARRYING the write or the branch are checked — an "
        "unrelated step's `continue-on-error` is none of this row's business."
    )
    assert hits, (
        "no step between the client install and the record leg has an ARMED "
        'fail-closed branch — it must be one unit, `if [ "$major" != "<major>" ] '
        "; then … fi`, not a substring pair: "
        '`if false && [ "$major" != "16" ]; then` keeps both `::error::` and '
        "`exit 1` in the text while being unable to fire, which is the "
        "disjunction this assertion used to be (measured, #12051 round 4)"
    )
    # EVERY located branch must be armed against the servers' major, and must
    # announce and stop. Multiple branches are ACCEPTED when they AGREE (one per
    # tool is a correct layout): round 9 measured that requiring exactly one red
    # that correct tree with "two assertions can disagree", which they do not.
    for branch_index, branch in hits:
        assert int(branch.group("major")) == server_major, (
            f"the fail-closed branch at step {branch_index} compares against "
            f"major {branch.group('major')} but the provisioned servers are "
            f"pg{server_major} — a branch that can never be true is the same "
            "symptom as no branch"
        )
        assert FAIL_CLOSED_ANNOUNCE_RE.search(branch.group("body")), (
            f"the armed branch at step {branch_index} never ANNOUNCES a "
            "`::error::` on its own line — a branch that prints nothing and "
            "continues ships the bad artifact silently (measured, round 7)"
        )
        assert FAIL_CLOSED_STOP_RE.search(branch.group("body")), (
            f"the armed branch at step {branch_index} never STOPS on its own "
            "`exit 1` line — measured, round 7: `false && exit 1` and "
            "`bail() { exit 1; }` both contain the token while the branch "
            "announces and continues, so the job runs the leg on the inherited "
            "client and exits 0"
        )

    asserted = "\n".join(_run_commands(steps[index]) for index, _ in hits)
    truncated = _truncated_loop_bodies(asserted)
    assert not truncated, (
        "a version-check loop LEAVES its iteration at the top level before its "
        f"checks run: {truncated!r}. A `break`/`continue` on the body's own "
        "indent runs before everything below it, so the `\"$tool\" --version` line "
        "is text the shell never reaches while this row credits every name the "
        "header lists (measured GREEN, #12069 review round 17: `140 passed` with "
        "neither client version-checked). Put the condition inside a nested "
        "block if an early exit is intended, or delete it"
    )
    checked = _version_checked_tools(asserted)
    for tool in TOOLS:
        assert tool in checked, (
            f"the pin never VERSION-CHECKS `{tool}`: the drill resolves BOTH "
            "from PATH, and pinning only one leaves the other on the image's "
            "client. A `for tool in …; do` HEAD that NAMES the tool does not "
            "count — the body has to invoke the loop variable (measured, round "
            "11: `version=\"$(pg_dump --version)\"` under the unchanged header "
            "`for tool in pg_dump pg_restore; do` stayed GREEN with pg_restore "
            "never checked). Accepted forms: that loop with `\"$tool\" "
            "--version` in its body (any of the three `for`/`do` spellings), or a "
            "`<tool> --version` line per tool. A header whose LIST is a variable "
            "(`for tool in $TOOLS`) is refused rather than resolved: the shell's "
            "value is not readable from text, so write the names literally."
        )

    # ⛔ The assertion must be evaluated for EVERY tool, not for whichever one ran
    # last (#12069 review round 16, measured): a dedented `if … fi` asserts the
    # LAST tool only, and a pg17 `pg_dump` beside a pg16 `pg_restore` then exits 0.
    for branch_step in {index for index, _ in hits}:
        code = _run_commands(steps[branch_step])
        if len(_version_checked_tools(code)) < 2:
            continue
        spans = _asserting_loop_line_spans(code)
        if spans:
            for branch in FAIL_CLOSED_BRANCH_RE.finditer(code):
                branch_line = code.count("\n", 0, branch.start())
                assert any(start <= branch_line <= end for start, end in spans), (
                    f"the armed branch at step {branch_step} is OUTSIDE the loop "
                    "that version-checks the tools, so `$major` is asserted for "
                    "the LAST tool only — a pg17 `pg_dump` beside a pg16 "
                    "`pg_restore` then exits 0 and the drill reds later with an "
                    "error that blames the diff. Put the `if … fi` INSIDE the "
                    "`for … done` body (#12069 review round 16, measured GREEN "
                    "on the dedented form)."
                )
        else:
            unasserted = _unasserted_client_checks(code)
            assert not unasserted, (
                f"the client pin at step {branch_step} VERSION-CHECKS a tool and "
                f"never asserts its major: line(s) {unasserted} have no armed "
                "branch before the next check (two checks with one branch is the "
                "fail-open this row exists to refuse). Accepted layouts: one "
                "multi-tool `for … do` with the `if … fi` inside it, or ONE "
                "armed branch per `--version` check (#12069 review round 18)."
            )


def test_the_job_itself_can_run_and_fail(job: dict) -> None:
    """The pin's `exit 1` must be able to red the JOB, not only the step.

    Item 4 asserts the PIN STEP is unconditional; that is not enough (#12069
    review round 14, measured): a JOB-level `continue-on-error: true` disarms
    every step's failure, and a JOB-level `if:` that this guard cannot read can
    skip the whole job while the RUN stays green.
    """

    reason = _job_scope_skip(job)

    assert reason is None, (
        f"the JOB `{JOB}` sets {reason} — the client pin's `::error::` + `exit 1` "
        "cannot red a job that is skipped or that tolerates failure, so every row "
        "grades a pin that can never fire. A job-level `if:` is accepted only when "
        "it gates on an upstream `needs.` RESULT (a failed upstream reds the RUN) "
        "or is a non-skipping condition (#12069 review rounds 14-15, measured)."
    )


def test_the_pin_runs_before_the_record_leg(job: dict) -> None:
    """`$GITHUB_PATH` affects SUBSEQUENT steps only — position is part of the fix.

    The leg is located in the step's CODE, for the same reason every other
    assertion here reads the step's shell with comment lines stripped
    (`_run_code`, #12051 review round 3): with the raw body
    a commented-out `tests/test_9567_record_capstone.py` entry still counted as
    the leg (a false PASS — the pin's position would be graded against a
    line that never runs), and a stray COMMENT inside the pin step naming the
    capstone made the pin itself look like the leg (`assert 4 < 4`, a false RED
    on a correct workflow).
    """
    steps = _steps(job)
    pin_index, _ = _pin_step(steps)

    leg_indexes = _leg_indexes(steps)
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    assert pin_index < min(leg_indexes), (
        f"the client pin is at step {pin_index}, the record leg at "
        f"{min(leg_indexes)} — a `$GITHUB_PATH` prepend after the leg is inert, "
        "so the leg would run on the runner image's own client"
    )


#: The refusal's POLARITY, pinned where the guard SHIPS (#12069 review).
#:
#: `_unreachable_guards()` asks one question — "can the body this row grades ever
#: be entered?" — and answers it, for a bare-constant condition, from the text.
#: The contract is therefore "the body is NEVER ENTERED", not "the line names a
#: constant": refusing a block that RUNS is a false RED, and a false RED is the
#: same defect class as a false PASS, because it teaches the reader that a correct
#: workflow is broken. The first cut of this scan refused every bare constant and
#: got FIVE of the lines below wrong (`if true`, `until false`, `if ! false`,
#: `while true … break`, and `cmd && true || fallback`) — each measured as a real
#: red on a correct tree before it was corrected.
#:
#: ⛔ THIS TABLE IS A COMMITTED TEST, deliberately not an example in an external
#: harness: the lane's harness lives under `.worktree-report/`, which `.gitignore`
#: excludes, so a pin kept there evaporates the moment the branch merges and the
#: next editor is free to widen the regexes back to the false-RED form with every
#: row still green. Both directions are asserted, because only both directions
#: describe the contract.
REFUSAL_POLARITY: list[tuple[str, str, bool, str]] = [
    # (id, line, must_be_refused, why)
    ("if-false", "if false; then", True, "body never entered"),
    ("if-true", "if true; then", False, "body ALWAYS runs - refusing this is a false RED"),
    ("elif-false", "elif false; then", True, "body never entered"),
    ("elif-true", "elif true; then", False, "body runs whenever reached"),
    ("while-false", "while false; do", True, "body never entered"),
    ("while-true", "while true; do", False, "body runs"),
    (
        "while-true-break",
        "while true; do sudo apt-get update -qq && break; sleep 3; done",
        False,
        "the ordinary retry loop: it RUNS and it stops",
    ),
    ("until-true", "until true; do", True, "body never entered"),
    ("until-false", "until false; do", False, "body runs (forever)"),
    ("if-not-true", "if ! true; then", True, "negated constant -> body never entered"),
    ("if-not-false", "if ! false; then", False, "negated constant -> body always runs"),
    ("until-not-false", "until ! false; do", True, "negated constant -> body never entered"),
    ("until-not-true", "until ! true; do", False, "negated constant -> body runs"),
    ("dead-and", "false && echo x", True, "the right-hand command never runs"),
    (
        "harmless-or-false-and",
        'sudo apt-get update -qq || false && echo "x"',
        False,
        "`(A || false) && B`: the right-hand command RUNS whenever A succeeds",
    ),
    ("dead-or", "true || exit 1", True, "the right-hand command never runs"),
    (
        "dead-or-after-semicolon",
        "sudo echo hi; true || exit 1",
        True,
        "statement position: the right-hand command never runs",
    ),
    (
        "harmless-and-true-or-fallback",
        'sudo apt-get update -qq && true || echo "update failed, continuing"',
        False,
        "the graded command RUNS and the fallback runs when it fails",
    ),
    ("harmless-or-true", "sudo apt-get update -qq || true", False, "the graded command still runs"),
    (
        "real-prepend",
        'echo "/usr/lib/postgresql/16/bin" >> "$GITHUB_PATH"',
        False,
        "the pinned write itself",
    ),
    (
        "real-armed-branch",
        'if [ "$major" != "16" ]; then',
        False,
        "the armed branch: a `[` test, whose truth no text can read",
    ),
    ("real-loop-head", "for tool in pg_dump pg_restore; do", False, "the real loop head"),
]


#: (code, tools that code ACTUALLY version-checks, why) — the pin for
#: `_version_checked_tools`. A bare `for tool in …; do` HEADER used to satisfy the
#: tool assertion on its own, so a body that hard-coded one tool read as covering
#: both (measured, #12051 review round 11). Both directions are pinned here, in
#: the repo, because a table that lives only in a review transcript evaporates
#: with the branch.
VERSION_CHECK_POLARITY = [
    (
        'for tool in pg_dump pg_restore; do\n  version="$("$tool" --version)"\ndone',
        {"pg_dump", "pg_restore"},
        "the real loop: the body uses the loop variable, so both are checked",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  version="$(pg_dump --version)"\ndone',
        {"pg_dump"},
        "header names both, body hard-codes one: pg_restore is NEVER checked",
    ),
    (
        'echo "checking pg_restore too"\nfor tool in pg_dump; do\n  version="$("$tool" --version)"\ndone',
        {"pg_dump"},
        "a MENTION of the tool is not a check of it",
    ),
    (
        'pg_dump --version\npg_restore --version',
        {"pg_dump", "pg_restore"},
        "the accepted no-loop shape: one version command per tool",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  echo "$tool"\ndone',
        set(),
        "the loop variable is echoed, never version-checked",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  version="$(echo "$tool --version")"\ndone',
        set(),
        "a QUOTED `--version` argument runs nothing: the command word is `echo`",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  printf \'%s\\n\' "$tool --version"\n'
        '  version="$(pg_dump --version)"\ndone',
        {"pg_dump"},
        "printf mentions both, the only real check is pg_dump",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ "$tool" = pg_dump ]; then\n'
        '    version="$("$tool" --version)"\n'
        '  fi\ndone',
        set(),
        "#12069: a body that SELECTS on the loop variable covers no name",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  [ "$tool" = pg_restore ] && continue\n'
        '  version="$("$tool" --version)"\n'
        'done',
        set(),
        "#12069: a `continue` guard selects just as surely as an `if`",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  case "$tool" in\n'
        '    pg_dump) version="$("$tool" --version)" ;;\n'
        '  esac\n'
        'done',
        set(),
        "#12069: `case` on the loop variable is a selection too",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then exit 1; fi\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069: a condition on ANOTHER variable does NOT narrow the coverage",
    ),
    (
        'for tool in pg_dump pg_restore\ndo\n  version="$("$tool" --version)"\ndone',
        {"pg_dump", "pg_restore"},
        "the POSIX two-line `for` / `do` form is the same loop (#12069 round 14)",
    ),
    (
        'for tool in pg_dump pg_restore; do version="$("$tool" --version)"; done',
        {"pg_dump", "pg_restore"},
        "the ONE-LINE form: the body starts on the header line",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  version="$("${tool}" --version)"\ndone',
        {"pg_dump", "pg_restore"},
        "a BRACED loop variable is the same variable: `${tool}` == `$tool`",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  version="$(echo "${tool} --version")"\ndone',
        set(),
        "bracing does not turn an `echo` into a check",
    ),
    (
        '{ pg_dump --version; }',
        {"pg_dump"},
        "a `{ … }` group is not a braced variable",
    ),
    (
        'for tool in $TOOLS; do\n  version="$("$tool" --version)"\ndone',
        set(),
        "a VARIABLE list names NOTHING (#12069 rounds 14-16, measured)",
    ),
    (
        'TOOLS="pg_dump pg_restore"\nTOOLS="pg_dump"\n'
        'for tool in $TOOLS; do\n  version="$("$tool" --version)"\ndone',
        set(),
        "a narrower reassignment makes the shell iterate ONE tool: any read is a guess",
    ),
    (
        'for tool in ${TOOLS:-pg_dump pg_restore}; do\n  version="$("$tool" --version)"\ndone',
        set(),
        "a DEFAULT is a literal the guard must not credit",
    ),
    (
        'for tool in $TOOLSET; do\n  version="$("$tool" --version)"\ndone',
        set(),
        "`$TOOLSET` is not `$TOOLS`: a shared prefix is a different variable",
    ),
    (
        'for tool in pg_dump pg_restore\necho unrelated\ndo\n'
        '  version="$("$tool" --version)"\ndone',
        set(),
        "a `do` on a LATER line is another construct's: this header opens no loop",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ "${tool%_restore}" = pg_dump ]; then\n'
        '    version="$("$tool" --version)"\n'
        '  fi\ndone',
        set(),
        "#12069 round-15: a brace TRANSFORM of the loop variable is a selection",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        "  if printf '%s' \"$tool\" | fgrep -qx pg_dump; then\n"
        '    version="$("$tool" --version)"\n'
        '  fi\ndone',
        set(),
        "#12069 round-15(2): `fgrep`/`egrep` are pattern tests too — `\\bgrep\\b` missed them",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if awk \'$0=="pg_dump"{f=1} END{exit !f}\' <<< "$tool"; then\n'
        '    version="$("$tool" --version)"\n'
        '  fi\ndone',
        set(),
        "#12069 round-15(2): `awk` is a pattern test too",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  t=$tool\n'
        '  if [ "$t" = pg_dump ]; then version="$("$tool" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-15(2): a COPY of the loop variable is a selection this guard cannot follow",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  [ -n "$version" ] || { echo "::error::$tool produced no version"; exit 1; }\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-15(2): a `[ -n … ]` sanity check selects NOTHING — it stays credited",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("${tool}" --version)"\n'
        '  echo "${tools_dir}"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-15(2): `${tools_dir}` is a DIFFERENT variable, not a transform of `$tool`",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then echo "::error::$tool"; exit 1; fi\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-15(2): `$tool` MENTIONED in the branch is not an operand of the condition",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ -n "$PG" ] && [ "$tool" = pg_dump ]; then\n'
        '    version="$("$tool" --version)"\n'
        '  fi\ndone',
        set(),
        "#12069 round-15(3): a selection in a SECOND `&&`-joined group is not invisible",
    ),
    (
        'for tool in pg_dump pg_restore; do [ "$tool" = pg_restore ] && continue\n'
        '  version="$("$tool" --version)"\n'
        'done',
        set(),
        "#12069 round-15(3): a selection on the ONE-LINE `do` is part of the body",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  v="$("$tool" --version | awk \'{print $3}\')"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-15(3): a PIPE THROUGH awk is not a selection — the operand decides",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  log="$tool"\n'
        '  version="$("$tool" --version)"\n'
        '  printf \'%s\\n\' "$version" >> "$RUNNER_TEMP/$log.txt"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-15(3): a copy used as a FILE NAME selects nothing",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then echo "::error::$tool failed the test"; exit 1; fi\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-5: the WORD `test` in a message is not the `test` command",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  [ "${#TARGETS[@]}" -gt 0 ] || { echo "::error::$tool has no targets"; exit 1; }\n'
        '  version="$("$tool" --version)"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-5: an ARRAY-COUNT guard mentions `$tool` only in its message",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ "${TARGETS[0]}" = "$tool" ]; then version="$("$tool" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-5: a `]` inside a SUBSCRIPT must not hide the condition",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ -n "$CI" ] && test "$tool" = pg_dump; then\n'
        '    version="$("$tool" --version)"\n  fi\ndone',
        set(),
        "#12069 round-5: a `test` COMMAND beside an unrelated group still selects",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  t=$tool; u=$t\n'
        '  if [ "$u" = pg_dump ]; then version="$("$tool" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-5: an alias CHAIN on ONE line must resolve, not fail open",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ "${tool}" = pg_dump ]; then v="$("${tool}" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-6: `${tool}` is ONE token — a `{` inside an expansion is not a separator",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  echo "::notice::$tool = $version"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-6: an `=` PRINTED in a message opens no condition",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [[ -n "${ONLY:-}" &&\n'
        '        "$tool" == pg_dump ]]; then v="$("$tool" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-7: a condition CONTINUED over a line break is still a condition",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [[ -n "$X" && "$tool" == pg_dump ]]; then v="$("$tool" --version)"; fi\n'
        'done',
        set(),
        "#12069 round-8: a NON-FIRST clause inside one `[[ … ]]` is still that condition",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  version="$("$tool" --version)"\n'
        '  [ -n "$LOG" ] && \\\n'
        '    echo "::notice::$tool = $version"\n'
        'done',
        {"pg_dump", "pg_restore"},
        "#12069 round-8: `&&`/`\\` continue a LIST, whose right-hand command is no condition",
    ),
]


#: (job, can the JOB be skipped or disarmed, why) — the pin for `_job_scope_skip`.
JOB_SCOPE_POLARITY = [
    ({}, False, "an unconditional job"),
    ({"if": "always()"}, False, "`always()` runs the job whenever the run does"),
    ({"if": "success()"}, False, "the default condition"),
    ({"if": "!cancelled()"}, False, "the accepted spelling"),
    ({"if": True}, False, "a literal `true` is not a condition"),
    (
        {"needs": "changes", "if": "always() && needs.changes.result == 'success'"},
        False,
        "the REAL job: runs exactly when upstream SUCCEEDED, else the run is red",
    ),
    (
        {"needs": ["changes"], "if": "${{ always() && needs.changes.result == 'success' }}"},
        False,
        "the same shape, wrapped, with a list `needs:`",
    ),
    (
        {"needs": "changes", "if": "needs.changes.result == 'failure'"},
        True,
        "INVERTED polarity: FALSE on the healthy path, so the job never runs",
    ),
    (
        {"needs": "changes", "if": "needs.changes.result != 'success'"},
        True,
        "the same inversion, spelled with `!=`",
    ),
    (
        {"needs": "changes", "if": "false && needs.changes.result == 'success'"},
        True,
        "a dead chain to a `needs.` mention is still a dead chain",
    ),
    (
        {"needs": "changes", "if": "always() && needs.setup.result == 'success'"},
        True,
        "`needs.setup` is not in this job's `needs:`, so it evaluates null",
    ),
    (
        {"if": "${{ needs.changes.outputs.docs_only != 'true' }}"},
        True,
        "an OUTPUTS gate's polarity cannot be read from text",
    ),
    (
        {"needs": "changes", "if": "github.event_name == 'push'"},
        True,
        "the job NEVER RUNS on a PR and the run is GREEN",
    ),
    ({"if": "cancelled()"}, True, "skipped exactly when it matters"),
    ({"continue-on-error": True}, True, "JOB-level `continue-on-error` disarms every step"),
    ({"continue-on-error": "${{ true }}"}, True, "the expression spelling disarms it too"),
    ({"continue-on-error": False}, False, "explicitly armed"),
]


@pytest.mark.parametrize(
    ("job", "hides", "why"),
    JOB_SCOPE_POLARITY,
    ids=[why.replace(" ", "-")[:32] for _, _, why in JOB_SCOPE_POLARITY],
)
def test_the_job_scope_is_read_in_both_directions(
    job: dict, hides: bool, why: str
) -> None:
    """A job-level skip/disarm is read, and only where it can actually hide the pin."""

    reason = _job_scope_skip(job)

    assert (reason is not None) is hides, (
        f"{why} (measured, #12069 round 15): {job!r} -> {reason!r}"
    )


#: (code, is the `if … fi` inside the loop that covers the tools, why) — the pin
#: for `_asserting_loop_line_spans` (#12069 review round 16).
PIN_ASSERTION_POLARITY = [
    (
        'for tool in pg_dump pg_restore; do\n  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then\n    exit 1\n  fi\ndone',
        True,
        "the real shape: the branch is inside the loop body",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  version="$("$tool" --version)"\ndone\n'
        'if [ "$major" != "16" ]; then\n  exit 1\nfi',
        False,
        "DEDENTED past `done`: only the last tool's major is asserted",
    ),
    (
        'for tool in pg_dump pg_restore\ndo\n  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then\n    exit 1\n  fi\ndone',
        True,
        "the same, in the two-line `for` / `do` spelling",
    ),
    (
        'for tool in pg_dump; do\n  version="$("$tool" --version)"\ndone\n'
        'if [ "$major" != "16" ]; then\n  exit 1\nfi',
        False,
        "a SINGLE-tool loop yields no span: the row skips it, one tool is asserted",
    ),
]


@pytest.mark.parametrize(
    ("code", "inside", "why"),
    PIN_ASSERTION_POLARITY,
    ids=[why.replace(" ", "-")[:32] for _, _, why in PIN_ASSERTION_POLARITY],
)
def test_the_armed_branch_is_inside_the_covering_loop(
    code: str, inside: bool, why: str
) -> None:
    """`$major` must be asserted for EVERY tool, which means inside the loop."""

    branch = code.find('if [ "$major"')
    branch_line = code.count("\n", 0, branch)
    spans = _asserting_loop_line_spans(code)

    assert any(start <= branch_line <= end for start, end in spans) is inside, (
        f"{why} (measured, round 16): spans={spans}, branch at line {branch_line}"
    )


#: (code, does EVERY `--version` check get its own armed branch, why) — the
#: PER-TOOL half of the armed-branch row (#12069 review rounds 18-19).
PER_TOOL_ASSERTION_POLARITY = [
    (
        'version="$(pg_dump --version)"\nif [ "$major" != "16" ]; then\n  exit 1\nfi\n'
        'version="$(pg_restore --version)"\nif [ "$major" != "16" ]; then\n  exit 1\nfi',
        True,
        "the unrolled layout: one check, one branch, per tool",
    ),
    (
        'for tool in pg_dump; do\n  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then\n    exit 1\n  fi\ndone\n'
        'for tool in pg_restore; do\n  version="$("$tool" --version)"\n'
        '  if [ "$major" != "16" ]; then\n    exit 1\n  fi\ndone',
        True,
        "one single-tool loop per tool, branch inside each",
    ),
    (
        'version="$(pg_dump --version)"\nversion="$(pg_restore --version)"\n'
        'if [ "$major" != "16" ]; then\n  exit 1\nfi',
        False,
        "two checks, ONE branch: pg_dump's major is never compared",
    ),
    (
        'version="$(pg_dump --version)"\nversion="$(pg_restore --version)"\n'
        'if [ "$major" != "16" ]; then\n  exit 1\nfi\n'
        'if [ "$major" != "16" ]; then\n  exit 1\nfi',
        False,
        "both branches AFTER both checks: only the LAST client's major is asserted",
    ),
    (
        'version="$(pg_dump --version)"',
        False,
        "a check with no branch at all",
    ),
    (
        'version="$(pg_dump --version)"\nif [ "$major" != "16" ]; then\n'
        '  echo "::error::pg_dump --version reports $version"\n  exit 1\nfi',
        True,
        "an ANNOUNCE that names `--version` is a mention, not a check",
    ),
]


@pytest.mark.parametrize(
    ("code", "asserted", "why"),
    PER_TOOL_ASSERTION_POLARITY,
    ids=[why.replace(" ", "-")[:32] for _, _, why in PER_TOOL_ASSERTION_POLARITY],
)
def test_every_client_check_gets_its_own_armed_branch(
    code: str, asserted: bool, why: str
) -> None:
    """The per-tool layout must not red, and a shared branch must still red."""

    unasserted = _unasserted_client_checks(code)

    assert (not unasserted) is asserted, (
        f"{why} (measured, round 19): unasserted check line(s) {unasserted}"
    )


#: (line, does it DROP the pinned dir, why) — the pin for
#: `_path_assignments_that_drop_the_pin`. A PATH assignment that names no client
#: dir was invisible, and `/usr/bin/pg_dump` is `pg_wrapper`, i.e. the image's
#: newest client (#12051 review round 12, measured GREEN on all five rows).
#: ⛔ AND PRECEDENCE IS THE INVARIANT, NOT CONTAINMENT (#12069 review P1): the
#: pin being somewhere on the value does not help if something else is FIRST, so
#: `PATH="/usr/bin:$PATH"` is an offender even though `$PATH` is still there.
PATH_ASSIGNMENT_POLARITY = [
    ('export PATH="/usr/local/bin:/usr/bin:/bin"', True, "a bare replacement drops the pin"),
    ('PATH="/usr/bin:/bin" timeout -s INT bash x.sh', True, "a command-prefix assignment drops it"),
    ("env -i PATH=/usr/bin:/bin bash x.sh", True, "`env -i` drops it just as well"),
    (
        'export PATH="/usr/lib/postgresql/16/bin:$PATH"',
        False,
        "the pin's own assignment: the pin is FIRST",
    ),
    (
        'export PATH="$PATH:/usr/local/bin"',
        False,
        "an append cannot outrank what is already first",
    ),
    (
        'export PATH="${PATH}:/usr/local/bin"',
        False,
        "the braced append is the same append",
    ),
    (
        'PATH="/usr/local/bin:$PATH" bash x.sh',
        True,
        "a PREPEND outranks the pin, however harmless the directory looks",
    ),
    (
        'export PATH="/usr/bin:${PATH}"',
        True,
        "the same prepend, braced: /usr/bin/pg_dump is pg_wrapper, the NEWEST client",
    ),
    (
        'export PATH="${PATH:-/usr/bin}"',
        False,
        "a default-if-unset is still `$PATH`: PATH is always set",
    ),
    (
        'export PATH="${PATH:+x}"',
        True,
        "the ALTERNATE value expands to `x`, not to `$PATH`",
    ),
    (
        'export PATH="$PATHX:/bin"',
        True,
        "`$PATHX` is a DIFFERENT variable — the pin is dropped, not kept",
    ),
    ('echo "PATH=/usr/bin"', False, "a diagnostic PRINTS a value, it does not assign one"),
]


@pytest.mark.parametrize(
    ("line", "drops", "why"), PATH_ASSIGNMENT_POLARITY, ids=[c.replace(" ", "-")[:28] for c, _, _ in PATH_ASSIGNMENT_POLARITY]
)
def test_a_path_assignment_must_keep_the_pinned_dir(line: str, drops: bool, why: str) -> None:
    """Only a value that puts the pin FIRST keeps it — containment is not precedence."""

    offenders = _path_assignments_that_drop_the_pin([{"run": line}], 0, 16)
    assert bool(offenders) is drops, f"{why} (measured, round 12 / #12069): {line!r}"


#: (`env` mapping, does it move the leg's PATH, why) — the pin for
#: `_env_path_replacements`. ⛔ THIS RULE SHIPPED UNPINNED at first (#12069
#: round-15 review P2): deleting it AND its union arm left all 86 rows green,
#: because the REAL job sets no `env: PATH`, so nothing exercised it — a
#: regression no committed test could catch, which is the exact failure the
#: `REFUSAL_POLARITY` note in `_unreachable_guards` calls out (a pin kept in
#: `.worktree-report/` pins nothing once the branch merges).
ENV_PATH_POLARITY = [
    ({"PATH": "/usr/bin:/bin"}, True, "a bare PATH in `env:` replaces the composed one"),
    (
        {"PATH": "$PATH:/x"},
        True,
        "`env:` is NOT shell-expanded, so that is the literal string `$PATH:/x`",
    ),
    ({"PATH": "/usr/lib/postgresql/16/bin:$PATH"}, False, "the pin literally first"),
    ({"PATH": "/usr/lib/postgresql/16/bin"}, False, "the pin, alone"),
    (
        {"TORTOISE_TEST_CARVE_OUT": "1"},
        False,
        "an unrelated key is none of this rule's business",
    ),
]


@pytest.mark.parametrize(
    ("env", "moves", "why"),
    ENV_PATH_POLARITY,
    ids=[str(c).replace(" ", "")[:30] for c, _, _ in ENV_PATH_POLARITY],
)
def test_an_env_path_must_begin_with_the_pinned_dir(
    env: dict, moves: bool, why: str
) -> None:
    """`env: PATH` is not shell-expanded, so only a literal pin-first value keeps it."""

    found = _env_path_replacements([{"env": env}], 16)
    assert bool(found) is moves, f"{why} (#12069 round-15 review): {env!r} -> {found!r}"


def test_an_env_path_is_wired_into_the_row(job: dict) -> None:
    """The `env:` / `$GITHUB_ENV` rules are WIRED, not merely defined (#12069 P2).

    A helper that no assertion calls is not a guard: measured, deleting both new
    union arms left every row green. This drives the REAL row over a mutated copy
    of the REAL job, so a missing arm fails HERE — the same reason the `if:` and
    loop-selection polarity tables exist rather than a `/tmp` probe.
    """

    job_level = copy.deepcopy(job)
    job_level["env"] = dict(job_level.get("env") or {})
    job_level["env"]["PATH"] = "/usr/bin:/bin:/usr/local/bin"
    with pytest.raises(AssertionError, match="env: PATH"):
        test_no_step_rewrites_the_client_path_to_another_major(job_level)

    github_env = copy.deepcopy(job)
    pin_index, _ = _pin_step(_steps(github_env))
    github_env["steps"][pin_index]["run"] += (
        '\necho "PATH=/usr/bin:/bin" >> "$GITHUB_ENV"\n'
    )
    with pytest.raises(AssertionError, match="GITHUB_ENV"):
        test_no_step_rewrites_the_client_path_to_another_major(github_env)


def test_a_step_env_outside_the_pin_leg_window_is_not_an_offender() -> None:
    """STEP-scope `env:` is windowed at BOTH ends; JOB scope is position-free.

    `jobs.<id>.steps[*].env` reaches that ONE step, so it can only move the leg if
    it sits between the pin and the step that RUNS the leg:
      * a PRE-PIN step cannot outrank the pin (measured false RED: a venv-first
        PATH on the setup step);
      * a POST-LEG step cannot reach a leg that already ran (measured false RED:
        a PATH on the teardown step).
    The LEG'S OWN `env:` is inside the window and is the most dangerous position
    there is, so the upper bound is INCLUSIVE. JOB scope reaches the leg from
    anywhere, so neither bound applies to it.
    """

    steps = [
        {"env": {"PATH": "/usr/bin:/bin"}},  # 0: pre-pin
        {"run": "echo pin"},  # 1: the pin
        {},  # 2: the leg itself
        {"env": {"PATH": "/usr/bin:/bin"}},  # 3: post-leg
    ]
    assert not _env_path_replacements(steps, 16, start=1, end=2), (
        "neither a pre-pin nor a post-leg step's `env: PATH` can reach the leg"
    )
    assert _env_path_replacements(steps, 16, start=0, end=2), (
        "the same step env IS an offender once the pin is behind it"
    )
    assert _env_path_replacements(steps, 16, start=1, end=3), (
        "a step inside the window is an offender"
    )
    assert _env_path_replacements(
        steps, 16, job={"env": {"PATH": "/usr/bin:/bin"}}, start=1, end=2
    ), "JOB scope reaches the leg whatever the step positions are"

    leg_env = [{"run": "echo pin"}, {"env": {"PATH": "/usr/bin:/bin"}}]
    assert _env_path_replacements(leg_env, 16, start=0, end=1), (
        "the LEG's own `env: PATH` is inside the window (the bound is inclusive)"
    )


#: (`if:` value, may it SKIP the step, why) — the pin for `_normalise_if`.
#: ⛔ SPACING IS NOT MEANING IN A GITHUB EXPRESSION (#12069 review P2): the two
#: spellings GitHub's own documentation uses were normalised into forms that
#: matched no entry in `NON_SKIPPING_IF`, so the row red the CORRECT `if:` —
#: `${{ ! cancelled() }}` and `success()||failure()` (both measured false RED).
NON_SKIPPING_IF_POLARITY = [
    ("success()", False, "the default condition"),
    ("${{ success() }}", False, "the templated default"),
    ("always()", False, "runs whenever the job is running at all"),
    ("${{ always() }}", False, "templated `always()`"),
    ("!cancelled()", False, "the docs' spelling of `always()`"),
    (
        "${{ ! cancelled() }}",
        False,
        "the SAME expression with the spacing the docs print",
    ),
    ("success() || failure()", False, "runs unless cancelled"),
    (
        "${{ success()||failure() }}",
        False,
        "the same `||` with no spaces around it",
    ),
    ("failure()", True, "a REAL condition: it skips the step on success"),
    ("${{ github.event_name == 'push' }}", True, "a real condition, and it stays one"),
]


@pytest.mark.parametrize(
    ("value", "skips", "why"),
    NON_SKIPPING_IF_POLARITY,
    ids=[c.replace(" ", "-").replace("$", "")[:30] for c, _, _ in NON_SKIPPING_IF_POLARITY],
)
def test_an_if_condition_is_read_however_it_is_spaced(
    value: str, skips: bool, why: str
) -> None:
    """`! cancelled()` and `!cancelled()` are one condition, not two."""

    found = _conditional_skip({"if": value})
    assert bool(found) is skips, f"{why} (#12069 review): {value!r} -> {found!r}"


#: (command, the major it installs, why) — the pin for `CLIENT_PKG_RE`.
INSTALL_POLARITY = [
    ("sudo apt-get install -y --no-install-recommends postgresql-client-16", 16, "the real line"),
    ("sudo apt-get -y install --no-install-recommends postgresql-client-16", 16, "flags before the verb"),
    ("sudo apt-get --yes install postgresql-client-16", 16, "the long spelling of the same flag"),
    ("sudo apt-get install postgresql-client-17", 17, "a wrong major is still an install"),
    ("dpkg -l postgresql-client-16", None, "a read-only diagnostic is NOT an installer"),
    ('echo "sudo apt-get install postgresql-client-16"', 16, "a mention still reads as an install"),
]


@pytest.mark.parametrize(
    ("command", "major", "why"), INSTALL_POLARITY, ids=[c.replace(" ", "-")[:28] for c, _, _ in INSTALL_POLARITY]
)
def test_an_install_verb_reads_through_its_flags(command: str, major: int | None, why: str) -> None:
    """`apt-get -y install X` is the same command as `apt-get install -y X`."""

    match = CLIENT_PKG_RE.search(command)
    assert (int(match.group(1)) if match else None) == major, f"{why} (measured, round 12)"


#: (image reference, the major it names, why) — the pin for `IMAGE_RE`. A stricter
#: reading is not a better reading: `16.4` and `0.8.0-pg16` name their major as
#: plainly as `pg16`, and refusing them told the author they did not (#12051
#: review round 13, four rows red on a correct pin).
IMAGE_TAG_POLARITY = [
    ("pgvector/pgvector:pg16", 16, "the pin this job uses"),
    ("postgres:16", 16, "a plain tag"),
    ("postgres:16.4", 16, "a PATCH tag still names its major"),
    ("postgres:16.4-bookworm", 16, "tag + suite"),
    ("pgvector/pgvector:0.8.0-pg16", 16, "a version-first extension tag"),
    ("pgvector/pgvector:pg16-trixie", 16, "tag + suite, extension image"),
    ("ghcr.io/mirror:5000/pgvector/pgvector:pg16", 16, "a registry PORT is not the major"),
    ("localhost:5000/postgres:16", 16, "port + plain tag"),
    ("localhost:5000/postgres:17", 17, "port + wrong major is still read"),
    ("pgvector/pgvector:0.8.0-pg17", 17, "a wrong major is still read"),
    ("postgres:17", 17, "a wrong major is still read, plain tag"),
]


@pytest.mark.parametrize(
    ("image", "major", "why"),
    IMAGE_TAG_POLARITY,
    ids=[c.replace("/", "-").replace(":", "-")[:30] for c, _, _ in IMAGE_TAG_POLARITY],
)
def test_an_image_tag_names_its_major_however_it_is_spelled(
    image: str, major: int, why: str
) -> None:
    """`16`, `16.4`, `pg16`, `0.8.0-pg16` all read 16 — anything else REDs."""

    read = _postgres_image_majors({"steps": [{"with": {"image": image}}]})
    assert read == [major], f"{why} (measured, round 13): {image!r} read {read}"


#: (script, is it refused as unable to fail the step, why) — the pin for the
#: SUBSHELL half of `_unreachable_guards`. `exit 1` inside a subshell whose status
#: is discarded terminates the SUBSHELL, not the step (#12051 review round 14).
SUBSHELL_POLARITY = [
    ("(\n  exit 1\n) || true", True, "the status is discarded, so the step succeeds"),
    ("(\n  echo ::error::nope\n) ;", True, "`;` discards the status just as well"),
    ("(\n  exit 1\n) && echo ok", True, "`&&` grades the failure as success"),
    ("( cd /tmp && pg_dump --version )", False, "closed on the SAME line: no separate shell"),
    ('if [ "$major" != "16" ]; then', False, "a flat armed branch is the accepted form"),
    ("done", False, "a loop terminator is not a subshell"),
]


@pytest.mark.parametrize(
    ("script", "refused", "why"),
    SUBSHELL_POLARITY,
    ids=[c.replace(" ", "-").replace("\n", "")[:28] for c, _, _ in SUBSHELL_POLARITY],
)
def test_a_subshell_cannot_swallow_the_failure(script: str, refused: bool, why: str) -> None:
    """A guarded block must run in the STEP's shell, or its `exit 1` fails nothing."""

    found = _unreachable_guards([{"run": script}], 0, 1)
    assert bool(found) is refused, f"{why} (measured, round 14): {script!r}"


def test_a_fallback_installer_is_refused() -> None:
    """`install 16 || install 17` moves the client exactly when 16 is missing.

    `CLIENT_PKG_RE.search()` graded only the FIRST match, so the verdict depended
    on line order: 17 first red, 17 second green (measured, #12051 review round
    13). Every installer in the step is now graded.
    """

    clean = [{"run": "sudo apt-get install postgresql-client-16"}]
    assert _pin_step(clean)[0] == 0, "one installer of the servers' major is the pin"

    fallback = [
        {
            "run": "sudo apt-get install postgresql-client-16 "
            "|| sudo apt-get install postgresql-client-17"
        }
    ]
    with pytest.raises(AssertionError, match="MORE THAN ONE client major"):
        _pin_step(fallback)


def test_the_shell_a_step_executes_through_is_scanned() -> None:
    """`shell:` is executed argv, so a PATH reset spelled there moves the leg.

    The client-dir rule has always read it; the PATH-assignment rule and the
    `$GITHUB_PATH` mention scan did not, so `shell: env PATH=/usr/bin:/bin bash`
    left every row green with the leg on `pg_wrapper` → pg17 (measured, round 13).
    """

    step = {"shell": "env PATH=/usr/bin:/bin bash"}
    assert _path_assignments_that_drop_the_pin([step], 0, 16), (
        "a PATH reset in the step's `shell:` drops the pinned dir"
    )
    assert not _path_assignments_that_drop_the_pin(
        [{"shell": "env PATH=/usr/lib/postgresql/16/bin:$PATH bash"}], 0, 16
    ), "a `shell:` that keeps the pinned dir is not an offender"

    pin = {"name": "pin", "shell": 'bash -c "echo /usr/bin >> $GITHUB_PATH; exec bash {0}"'}
    writes = _github_path_writes([pin])
    assert writes and writes[0][2] is None, (
        "a `$GITHUB_PATH` write spelled in `shell:` is an unclassifiable mention, "
        "which is an offender at or after the pin"
    )


def test_an_image_digest_does_not_hide_its_major() -> None:
    """`image:pg16@sha256:…` is the STRICTLY SAFER form of the same pin."""

    digest = "sha256:" + "0" * 64
    majors = _postgres_image_majors(
        {"steps": [{"with": {"image": f"pgvector/pgvector:pg16@{digest}"}}]}
    )
    assert majors == [16], (
        "a digest-pinned image names its major; refusing it reds four rows and "
        "tells the author it does not name one (measured, round 12)"
    )


@pytest.mark.parametrize(
    ("code", "checked", "why"),
    VERSION_CHECK_POLARITY,
    ids=[case.replace(" ", "-")[:28] for case, _, _ in VERSION_CHECK_POLARITY],
)
def test_a_bare_loop_header_does_not_assert_the_tool(
    code: str, checked: set[str], why: str
) -> None:
    """The tool coverage is what the code CHECKS, not what it mentions (#12051).

    `assert tool in asserted` was a substring over the step, so the loop HEADER
    alone satisfied it: `version="$(pg_dump --version)"` under an unchanged
    `for tool in pg_dump pg_restore; do` left every row green while `pg_restore`
    was never checked — the exact defect class rounds 4 and 7 fixed for
    `::error::` / `exit 1`, one screen up. Each case is pinned in both
    directions so a future widening cannot silently restore it.
    """

    assert _version_checked_tools(code) == checked, f"{why} (measured, round 11)"


@pytest.mark.parametrize(
    ("line", "must_be_refused", "why"),
    [(line, must_be_refused, why) for _, line, must_be_refused, why in REFUSAL_POLARITY],
    ids=[case_id for case_id, _, _, _ in REFUSAL_POLARITY],
)
def test_the_refusal_polarity_is_never_runs(line: str, must_be_refused: bool, why: str) -> None:
    """The unreachable-block refusal fires on the never-runs forms and on NOTHING else.

    Both directions, or the pin is worthless: an under-refusing scan restores the
    #12051 defect (an enclosing `if false; then … fi` around the prepend left all
    five rows green), and an over-refusing scan reds a correct workflow — which is
    the same defect class, and is what the first cut of this scan did five times.
    """
    refused = bool(CONSTANT_GUARD_RE.match(line) or DEAD_CHAIN_RE.search(line))

    assert refused is must_be_refused, (
        f"`{line}` must be "
        f"{'REFUSED as unreachable' if must_be_refused else 'ALLOWED as runnable'} "
        f"({why}), but the scan {'refused' if refused else 'allowed'} it. "
        "`CONSTANT_GUARD_RE` refuses only a body that is NEVER ENTERED — "
        "`if`/`elif`/`while` + `false` or `! true`, `until` + `true` or `! false` — "
        "and `DEAD_CHAIN_RE` only `false && …` (after `^`, `;` or `&&`) and a "
        "statement-position `true || …`. Widening either to 'any bare constant' "
        "reds `while true; do … break; done`, which is the ordinary retry loop "
        "(#12069 review)"
    )



#: ⛔ A PIN THAT ONLY RUNS ONE DIRECTION IS NOT A PIN (#12069 review round 17).
#: The two rules below close the last measured fail-opens in this file — a `shell:`
#: that never reaches the script, and a loop body that leaves before its checks —
#: and BOTH are refused only in the spelling class measured. Each accepted row is a
#: CONTROL: a guard that reds `bash -e {0}` or a nested `break` has replaced a false
#: PASS with a false RED, which is the same defect class.
INERT_SHELL_POLARITY: list[tuple[str, bool, str]] = [
    (
        "bash -c 'exit 0; exec bash {0}'",
        True,
        "the measured fail-open: the step's whole `run:` block never executes",
    ),
    (
        "bash -c 'return 1; exec bash {0}'",
        True,
        "a `return` ends the shell the same way an `exit` does",
    ),
    (
        "env PATH=/usr/bin:/bin bash -c 'exit 0; exec bash {0}'",
        True,
        "an envelope command does not make the terminator harmless",
    ),
    (
        "bash -c 'echo hi; exec bash {0}'",
        False,
        "control: a statement before the exec still reaches the script",
    ),
    ("bash -e {0}", False, "control: the ordinary form"),
    ("bash", False, "control: no inline script, the runner appends the script"),
    ("bash -c 'exec bash {0}'", False, "control: the correct explicit form"),
    (
        "bash {0} || exit 1",
        False,
        "control: a terminator AFTER the script is not before it",
    ),
    (
        "bash -c 'cat {0} >/dev/null; exit 0'",
        True,
        "#12069 round 18: `{0}` MENTIONED is not `{0}` EXECUTED — the step body "
        "never ran and the first cut passed it",
    ),
    (
        "python {0}",
        False,
        "control: `python` is one of the runner's own shells, so it runs the script",
    ),
    (
        "bash --noprofile --norc -eo pipefail {0}",
        False,
        "#12069 round 19 control: `pipefail` is a flag's VALUE, not the word that "
        "runs the script — the first cut red this documented spelling",
    ),
    (
        "/bin/bash -e {0}",
        False,
        "#12069 round 19 control: argv[0] is a PATH, so it is basenamed before the "
        "comparison",
    ),
    (
        "bash -c 'echo bash {0}; exit 0'",
        True,
        "#12069 round 19: the word before `{0}` is `bash`, but the command that "
        "receives it is `echo` — command position, not token position",
    ),
    (
        "bash -c 'printf %s bash {0}'",
        True,
        "#12069 round 19: same root — `printf` is what would run, not bash",
    ),
]


@pytest.mark.parametrize(
    "shell, must_be_refused, why",
    INERT_SHELL_POLARITY,
    ids=[str(c).replace(" ", "_")[:34] for c, _, _ in INERT_SHELL_POLARITY],
)
def test_a_shell_that_cannot_reach_the_script_is_refused(
    shell: str, must_be_refused: bool, why: str
) -> None:
    """A `shell:` that ends before `{0}` makes the whole step inert.

    ⛔ THE SCRIPT RUNS THROUGH THE SHELL, NOT AFTER IT (#12069 review round 17).
    The runner substitutes `{0}` and runs the WHOLE string, so `bash -c 'exit 0;
    exec bash {0}'` never runs the step's `run:` block — the client is never
    installed, the PATH is never prepended, the major is never asserted — while
    every structural row reads the untouched `run:` text. Measured on the merged
    workflow at `603a9fa4fe`: `140 passed`, the client never checked.

    STATED LIMIT: this reads a TERMINATOR before `{0}`. A shell that blocks
    forever, or that execs a different script under a name it computes, is not
    decidable from text; the in-job `pg_dump --version` assertion is what makes a
    surviving blind spot visible in the log.
    """

    refused = _inert_shell_reason(None, {"shell": shell}) is not None

    assert refused is must_be_refused, (
        f"`shell: {shell}` must be "
        f"{'REFUSED as inert' if must_be_refused else 'ALLOWED as reaching the script'} "
        f"({why}), but the scan {'refused' if refused else 'allowed'} it. "
        "`TERMINATING_COMMAND_RE` reads the shell text BEFORE `{0}` and refuses "
        "only an `exit`/`return` in statement position there — an ordinary "
        "statement (`echo hi; exec bash {0}`) and a terminator AFTER the script "
        "are both accepted, because both still run it (#12069 review round 17)"
    )


def test_a_step_shell_is_graded_on_its_own_and_not_masked_by_the_default() -> None:
    """A correct `defaults.run.shell` must not hide an inert STEP shell (#12069 r18).

    ⛔ THE RUNNER USES ONE OF THEM, NOT BOTH. A step `shell:` REPLACES the job or
    workflow default; the first cut graded `_effective_shell()`'s CONCATENATION and
    split it on the first `{0}`, so a default `bash -e {0}` came first, the step's
    `exit 0` shell fell after the split point, and the measured mutant went GREEN
    again — the exact fail-open this row exists to catch, resurrected by a
    document-supported default.
    """

    inert = "bash -c 'exit 0; exec bash {0}'"
    default = {"defaults": {"run": {"shell": "bash -e {0}"}}}

    assert _inert_shell_reason(default, {"shell": inert}) is not None, (
        "a job default `shell: bash -e {0}` masked the step's own inert shell: a "
        "step `shell:` REPLACES the default, so the step's must be the one graded"
    )
    assert _inert_shell_reason(default, {}) is None, (
        "the DEFAULT alone is correct (`bash -e {0}`), so a step with no `shell:` "
        "must stay GREEN — refusing it would be a false RED"
    )
    assert _inert_shell_reason(
        {"defaults": {"run": {"shell": inert}}}, {}
    ) is not None, (
        "an INERT default applies to every step that does not override it, so it "
        "must be refused too"
    )


#: The same both-directions rule for the loop-body refusal. A nested
#: `break`/`continue` is a CONTROL: it runs only under that block's condition, so
#: the body above it still runs on the pass that reaches the condition. Refusing it
#: would red a correct workflow — and widening the rule to "any break anywhere" is
#: how the first cut of the enclosing-guard scan produced five false REDs.
TRUNCATING_LOOP_POLARITY: list[tuple[str, bool, str]] = [
    (
        'for tool in pg_dump pg_restore; do\n  break\n  "$tool" --version\ndone',
        True,
        "the measured fail-open: the check below the break never runs",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  continue\n  "$tool" --version\ndone',
        True,
        "`continue` leaves the iteration the same way `break` leaves the loop",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  break 2\n  "$tool" --version\ndone',
        True,
        "#12069 round 18: the LEVEL argument is part of the statement — `break 2` "
        "left the multi-tool loop crediting both while the one-line spelling red",
    ),
    (
        'for tool in pg_dump pg_restore; do break; "$tool" --version; done',
        True,
        "the one-line spelling is the same statement",
    ),
    (
        'for tool in pg_dump pg_restore; do break\n  "$tool" --version\ndone',
        True,
        "#12069 round 19: a terminator on a `do` line that does NOT close the loop "
        "truncates the body below it — the trailing exemption is for a loop that "
        "closes on that line",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  break  # bail\n  "$tool" --version\ndone',
        True,
        "#12069 round 19: a trailing COMMENT does not make the terminator inert",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  "$tool" --version\n  break\ndone',
        False,
        "#12069 round 18 control: a TRAILING break skips nothing — every tool above "
        "it was checked, and refusing it red a body that checks every tool",
    ),
    (
        'for tool in pg_dump pg_restore; do "$tool" --version; break; done',
        False,
        "control: the trailing break in the one-line form skips nothing either",
    ),
    (
        'for tool in pg_dump pg_restore; do\n'
        '  if [ "$QUICK" = 1 ]; then\n    break\n  fi\n'
        '  "$tool" --version\ndone',
        False,
        "control: a nested break runs only when its condition holds",
    ),
    (
        'for tool in pg_dump pg_restore; do\n  "$tool" --version\ndone',
        False,
        "control: the ordinary asserting loop",
    ),
    (
        'for tool in pg_dump pg_restore; do "$tool" --version; done',
        False,
        "control: the one-line asserting loop",
    ),
]


@pytest.mark.parametrize(
    "code, must_be_refused, why",
    TRUNCATING_LOOP_POLARITY,
    ids=[str(c).strip().splitlines()[0].replace(" ", "_")[:34] for c, _, _ in TRUNCATING_LOOP_POLARITY],
)
def test_a_loop_that_leaves_before_its_checks_is_refused(
    code: str, must_be_refused: bool, why: str
) -> None:
    """A top-level `break`/`continue` credits nothing, and says so by name.

    ⛔ A BARE `break` IS NOT A SELECTION (#12069 review round 17, measured GREEN):
    it mentions no loop variable, so `_selects_on_var` does not see it, and
    `_version_checked_tools` credited every name the header listed while neither
    client was version-checked (`140 passed` on the merged workflow).

    Both halves are asserted here: `_version_checked_tools` must credit NOTHING for
    such a body, and `_truncated_loop_bodies` must NAME it — a row that only says
    "pg_dump is unchecked" sends the next lane looking at the header.
    """

    refused = bool(_truncated_loop_bodies(code))
    assert refused is must_be_refused, (
        f"this loop must be "
        f"{'REFUSED as leaving before its checks' if must_be_refused else 'ALLOWED'} "
        f"({why}), but the scan {'refused' if refused else 'allowed'} it. "
        "`_truncated_at_top_level` refuses only a `break`/`continue` on the body's "
        "OWN indent — a nested one runs conditionally and stays accepted"
    )
    if must_be_refused:
        assert _version_checked_tools(code) == set(), (
            "a truncated loop body must credit NOTHING: "
            f"{sorted(_version_checked_tools(code))!r} were credited from lines the "
            "shell never reaches (measured #12069 review round 17)"
        )


# --------------------------------------------------------------------------
# FOLDED IN FROM `origin/main` (#12071 / #12080, merged as `8126970432`).
#
# A second lane hardened the SAME step concurrently: it pinned the JOB to a
# NAMED image (`runs-on: ubuntu-24.04`) and added an `apt-cache show` package
# precondition that fails closed with its own named error. Those two facts are
# part of the fix, so they must be graded here — the rebase is only correct if
# the newer artifact is still asserted.
#
# Adapted to THIS file's helpers rather than copied verbatim: main's
# `_code_only(text)` / `_normalised_code(text)` / `_version_branch(...)` are
# replaced by `_run_code(step)` (this file's comment-line stripper) and this
# file's existing structural branch reader (`FAIL_CLOSED_BRANCH_RE`, which
# already reads the fail action from the branch BODY — the property main's
# `_version_branch` was added for, which is why it is not carried over).
# --------------------------------------------------------------------------

#: A (release -> PostgreSQL client major) fact table, PROVEN and not assumed.
#: Which client a release's archive carries IS #12051 round 2: `ubuntu-latest`
#: moved to `ubuntu-26.04`, whose archive ships `postgresql-client-18` and NO 16,
#: so the pin died `E: Unable to locate package` before a single test ran. A new
#: release must be MEASURED and added here before it is adopted — this map is the
#: record of a measurement, not a shape check.
KNOWN_GOOD_IMAGE_MAJOR = {
    # `docker run --rm ubuntu:24.04 apt-cache policy postgresql-client-16`
    #   -> Candidate: 16.15-0ubuntu0.24.04.1
    "ubuntu-24.04": 16,
}

#: The package precondition, in either shell shape that actually fails closed: an
#: `if !` guard around the probe, or the probe short-circuiting into a `{ … }` /
#: `( … )` group. FACTS are extracted (probe verb either way, quoted or not, any
#: redirect spelling); the SPELLING is deliberately not pinned, because
#: equivalent rewrites are the same behaviour and a guard that reds them is a
#: false block — which teaches the next lane to widen the assertion instead of
#: trusting it.
#:
#: `_normalised_code` erases `${…}` expansions FIRST. Without that, a `}` inside
#: the shipped message (`${ImageOS:-unknown}`) truncates a `.*?\}` body match, so
#: the exact rewrite this guard promises to accept false-red as "does not
#: `exit 1`" — the most misleading possible failure.
_IF_PRECONDITION_RE = re.compile(
    r"^if\s+!\s*(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^;]*;\s*then(?P<body>.*?)^fi\s*$",
    re.DOTALL | re.MULTILINE,
)
_OR_PRECONDITION_RE = re.compile(
    r"(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^|\n]*\|\|\s*[({]"
    # The failing branch as a BOUNDED WINDOW of following lines, never up to a
    # matched brace: the shipped message contains `( )` and `{ }` of its own, so
    # brace-matching truncates the body and then reports the guard as missing —
    # the most misleading failure this file could produce.
    r"(?P<body>(?:[^\n]*\n){0,5}[^\n]*)",
    re.MULTILINE,
)

_PARAM_EXPANSION_RE = re.compile(r"\$\{[^}]*\}")


def _normalised_code(step: dict) -> str:
    """`_run_code(step)` with `${…}` expansions erased.

    Erasing them is control-flow-neutral (an expansion never opens or closes a
    block) and it is what makes a `}` inside a message string invisible to the
    block matchers. It builds on `_run_code` — the ONE comment-line stripper in
    this file — so a commented-out precondition cannot read as a live one.
    """
    return _PARAM_EXPANSION_RE.sub("", _run_code(step))


def _precondition(code: str) -> re.Match[str]:
    """The package-resolvability precondition, matched as a BLOCK, not a token.

    A bare `"apt-cache show" in text` is satisfied by a commented-out line and by
    a block that is present but unreachable, so the caller passes
    `_normalised_code(...)`. Reachability itself is NOT provable from text — that
    is what `test_the_precondition_actually_fires_when_the_package_is_absent`
    executes the committed step for.
    """
    match = _IF_PRECONDITION_RE.search(code) or _OR_PRECONDITION_RE.search(code)
    assert match, (
        "the pin does not precondition on the package being RESOLVABLE, so an "
        "image whose archive lacks the major reds as `E: Unable to locate "
        "package` (exit 100) — an opaque failure that reads as a typo in this "
        "step, which is how the 2026-11-12 image move hid behind a "
        "#12051-shaped red. Read from the step's CODE (comment lines stripped), "
        "because a substring pin is satisfied by commenting the body out."
    )
    return match


def _runs_on_labels(job: dict) -> list[str]:
    """`runs-on` as a label list — a one-element list is the same request."""
    runs_on = job.get("runs-on")
    if isinstance(runs_on, str):
        return [runs_on]
    assert isinstance(runs_on, list), (
        f"`{JOB}` must name its image(s) literally; found runs-on={runs_on!r}. An "
        "expression makes the client major depend on what the expression "
        "resolved to, which no assertion in this file can see."
    )
    return [str(label) for label in runs_on]


def test_the_pin_runs_on_a_named_image_that_carries_the_servers_major(job: dict) -> None:
    """The image is part of the artifact: it decides which client the archive has.

    Round 2 of #12051. The pin assumes the image's archive CARRIES
    `postgresql-client-<major>`, and that assumption is not free: MEASURED
    2026-11-12 (job `131022562781`, head `58b5a417ba`), `ubuntu-latest` had
    migrated to `Image: ubuntu-26.04`, whose archive ships `postgresql-client-18`
    and no 16 — so the install died `E: Unable to locate package` (exit 100)
    BEFORE the version assertion, and every leg below skipped.

    A floating label lets the runner image change the drill's client major with
    no diff, which is the class this PR closes. The (release -> major) pair is
    asserted rather than the label's SHAPE, because `ubuntu-26.04` is just as
    version-pinned as `ubuntu-24.04` and is the release that broke.
    """
    server_major = _postgres_image_majors(job)[0]
    labels = _runs_on_labels(job)

    assert "ubuntu-latest" not in labels, (
        "`ubuntu-latest` is a MOVING label and it moved: on 2026-11-12 it became "
        "ubuntu-26.04, whose archive has no `postgresql-client-16`, so this job "
        "reds before a single test runs (#12051). Name the release."
    )
    assert len(labels) == 1 and labels[0].startswith("ubuntu-"), (
        f"`{JOB}` asks for {labels!r}. This pin needs ONE named hosted image: the "
        "drill's PostgreSQL client major comes from that image's archive, so any "
        "second label (e.g. `self-hosted`) makes the client an inherited fact of "
        "whichever box answers — the exact inheritance #12051 removed. The "
        "in-step precondition fails closed on such a box; this assertion is the "
        "contract."
    )
    label = labels[0]
    assert label in KNOWN_GOOD_IMAGE_MAJOR, (
        f"`{JOB}` runs on {label!r}, whose PostgreSQL client major has not been "
        "measured. PROVE it — `apt-cache policy "
        f"postgresql-client-{server_major}` on that image — and add the pair to "
        "KNOWN_GOOD_IMAGE_MAJOR. The image supplies the drill's client, so "
        "adopting one unmeasured is how #12051 round 2 shipped."
    )
    assert KNOWN_GOOD_IMAGE_MAJOR[label] == server_major, (
        f"`{JOB}`'s two servers are pg{server_major}, but {label} carries a "
        f"pg{KNOWN_GOOD_IMAGE_MAJOR[label]} client: the archive would be dumped "
        "by a client whose major differs from the restore target (#12051)."
    )

    _, pin = _pin_step(_steps(job))
    code = _normalised_code(pin)
    precondition = _precondition(code)
    assert int(precondition.group("major")) == server_major, (
        f"the precondition probes postgresql-client-"
        f"{precondition.group('major')} but the servers are pg{server_major}"
    )
    body = precondition.group("body")
    assert "::error::" in body, (
        "the precondition fails silently: an absent package must be reported as "
        "its OWN named failure, not as the archive's opaque apt error"
    )
    assert re.search(r"\bexit\s+1\s*(?:;|\}|\)|$)", body), (
        "the precondition does not `exit 1`, so a missing client would fall "
        "through to the install and surface as exit 100 again. Any separator is "
        "accepted — the fact is the exit status, not the punctuation around it."
    )

    # ⛔ ORDER IS PART OF THE FIX. A precondition placed AFTER the install is
    # inert: the install has already died with the opaque `exit 100` this block
    # exists to replace.
    assert code.index("apt-cache") < code.index("apt-get install"), (
        "the package precondition runs AFTER `apt-get install`, so on an image "
        "whose archive lacks the major the job dies with the archive's opaque "
        "`E: Unable to locate package` and never reaches the named error"
    )

    # ⛔ THE INSTALL LINE IS WHAT PICKS THE CLIENT. Asserted on the install
    # itself, not on `CLIENT_PKG_RE.search(text)`: the precondition above names a
    # package too and sits earlier, so a first-match read would satisfy an
    # install-major claim by reading the PROBE (measured: switching the install
    # to -18 stayed green under the old read).
    install = re.search(
        r"apt-get\s+install[^\n]*postgresql-client-(?P<major>\d+)", code
    )
    assert install, "the pin never runs `apt-get install postgresql-client-<major>`"
    assert int(install.group("major")) == server_major, (
        f"the pin INSTALLS postgresql-client-{install.group('major')} while the "
        f"servers are pg{server_major}: the versioned PATH prepend would then "
        "point at a directory the install never created"
    )


def test_the_precondition_actually_fires_when_the_package_is_absent(
    job: dict, tmp_path: Path
) -> None:
    """REACHABILITY, executed — text cannot prove an armed guard from a dead one.

    Every other assertion in this file reads the step's text, and text does not
    distinguish "the guard is armed" from "the guard is present but unreachable".
    MEASURED, all of these satisfied the text assertions:
      · `... >/dev/null 2>&1 || true; then` — the probe's status thrown away
      · `exit 1` moved inside `if false; then … fi` — the token is still there
      · the whole block wrapped in `if [ -n "${NEVER_SET:-}" ]; then … fi` — the
        DISARMING spelling, which REDs this test (measured: absent stub → rc=0,
        `REACHED_PAST_PRECONDITION` printed, no `::error::`).

    ⚠️ NOT in that list: the same wrapper spelled `-z`. `[ -z "" ]` is TRUE, so
    the wrapper's body still runs and the guard stays ARMED — measured: absent
    stub → rc=1 with `::error::`, no marker, so BOTH arms stay GREEN. A wrapper
    the guard still runs is a blind spot in this docstring's framing, not a
    bypass; it is named here so nobody reads `-z` as a way past the guard.

    So this test RUNS the committed step's prefix — verbatim, from `run:`, up to
    (not including) the `apt-get install` line — under `bash`, with `sudo`,
    `apt-get` and `apt-cache` stubbed. Hermetic: no network, no apt, no sudo.
    The failing-stub run must STOP with a named `::error::`; the succeeding-stub
    run must NOT stop, or the job would red on every image that carries the
    client.
    """
    _, pin = _pin_step(_steps(job))
    lines = _run_text(pin).splitlines()
    install_at = next(
        (i for i, line in enumerate(lines) if re.search(r"apt-get\s+install", line)),
        None,
    )
    assert install_at is not None, "the pin has no `apt-get install` line to run up to"
    prefix = "\n".join(lines[:install_at]).rstrip()
    assert "apt-cache" in prefix, (
        "the precondition is not before the install in the committed step, so "
        "there is nothing to execute ahead of it (the order assertion above "
        "should already have caught this)"
    )

    def run_with(stub: str) -> subprocess.CompletedProcess[str]:
        script = tmp_path / "precondition.sh"
        # ⛔ `sudo` and `apt-get` are stubbed TOO. Without them the prefix runs
        # the HOST's real `sudo apt-cache` / `sudo apt-get update`: on a box where
        # sudo is passwordless and the package is known to apt the probe
        # SUCCEEDS, the failure branch is never taken, and the test reports a
        # disarmed guard that is actually armed — and reads the host's package
        # index while doing it.
        script.write_text(
            'sudo() { "$@"; }\n'
            "apt-get() { return 0; }\n"
            f"{stub}\n{prefix}\n"
            "echo REACHED_PAST_PRECONDITION\n",
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=60
        )

    absent = run_with(
        'apt-cache() { echo "E: Unable to locate package" >&2; return 100; }'
    )
    combined = absent.stdout + absent.stderr
    assert absent.returncode != 0, (
        "with `apt-cache` FAILING the committed precondition did not stop the "
        "step, so it is unreachable or disarmed (`|| true`, an `exit 1` inside a "
        "dead branch, or a wrapper that never runs). On a real image without the "
        f"client this reaches `apt-get install` and dies with exit 100. Output: {combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in absent.stdout, (
        "the precondition let the script continue past itself with the package "
        f"absent — it is not armed: {combined!r}"
    )
    assert "::error::" in combined, (
        "the precondition stopped the step but never NAMED the cause: the whole "
        f"point is that a missing client reads as THIS defect, not an apt error: {combined!r}"
    )

    present = run_with("apt-cache() { return 0; }")
    assert present.returncode == 0, (
        "with `apt-cache` SUCCEEDING the precondition stopped the step — the job "
        f"would red on every image that carries the client: {present.stderr!r}"
    )
    assert "REACHED_PAST_PRECONDITION" in present.stdout, (
        "the script never reached the line after the precondition even when the "
        "package resolved — the block is unconditional"
    )


#: A REDIRECTION that writes a client binary, both directions (#12069 review P1).
#:
#: `_replaces_a_client_binary` needed a mutating VERB, and a shell's ordinary way
#: of writing a file has none — so the same act was refused through `tee`/`cp` and
#: GREEN through `>`. MEASURED at `5de9f4b5d7` before the rule existed: all three
#: of the first three rows below left `171 passed`, i.e. the row that exists to
#: stop the pinned binary being rewritten could be satisfied by a redirection.
#:
#: Both directions are pinned because a rule that reds a CORRECT step is the same
#: defect class as one that passes a broken one: this job legitimately redirects
#: into `$GITHUB_PATH`, into `$RUNNER_TEMP`, into `/dev/null`, and pipes the pin's
#: own `pg_dump --version` into `tee -a`.
REDIRECT_POLARITY = [
    # (line, must_be_refused, why)
    (
        "printf '#!/bin/sh\\nexec /usr/bin/pg_dump \"$@\"\\n' > /usr/lib/postgresql/16/bin/pg_dump",
        True,
        "a `>` writes the pinned binary into a wrapper: the full #12051 regression",
    ),
    ("          : > /usr/lib/postgresql/16/bin/pg_dump", True, "`:` with a redirect truncates it"),
    ("echo boom > /usr/lib/postgresql/16/bin/pg_dump", True, "any producer, one redirect"),
    ("echo x >> /usr/lib/postgresql/17/bin/pg_restore", True, "an append into another major's bin"),
    (
        "echo x | sudo tee /usr/lib/postgresql/16/bin/pg_dump",
        True,
        "`| tee` is the same write, so the two spellings cannot disagree",
    ),
    ('echo "/usr/lib/postgresql/16/bin" >> "$GITHUB_PATH"', False, "the pin's own prepend"),
    ('sudo apt-get install -y postgresql-client-16', False, "the install line names no client binary"),
    ("pg_dump --version | tee -a \"$RUNNER_TEMP/v.txt\"", False, "the guard's own compensating control"),
    ('rm -f "$RUNNER_TEMP/pg_dump.log"', False, "a filename that CONTAINS a binary name"),
    ('echo "$SCRATCH" > "$RUNNER_TEMP/pg_dump.log"', False, "a log file, not a binary"),
    ("cmd >/dev/null 2>&1", False, "discarding output is not a write into a client dir"),
    ('echo "14 > 16" >> "$SCRATCH/report.txt"', False, "`>` inside a message string"),
    # ⛔ THE TARGET AXIS (#12069 review round 2, measured): grading only the
    # LITERAL target left `f=…/pg_dump; : > "$f"` GREEN — the same fail-open
    # rounds 6-8 closed for `$GITHUB_PATH`. A bare expansion is refused, not
    # resolved; the two runner COMMAND FILES are exempt because each already has
    # its own classified-write rule.
    ('f=/usr/lib/postgresql/16/bin/pg_dump; : > "$f"', True, "a variable target is unclassifiable"),
    ('f=/x/pg_dump; printf x > "${f}"', True, "the braced spelling is the same target"),
    (': > "$(printf /usr/lib/postgresql/16/bin/pg_dump)"', True, "a command-substituted target"),
    ('echo "X=1" >> "${GITHUB_ENV}"', False, "the second command file has its own rule"),
    ('bash x.sh > "$SCRATCH/pytest-rc"', False, "a literal basename under a variable dir"),
    ('cmd > "$RUNNER_TEMP/v.txt"', False, "the guard's own recommended redirect"),
]


@pytest.mark.parametrize(
    "line,refused,why",
    REDIRECT_POLARITY,
    ids=[why.replace(" ", "-")[:32] for _, _, why in REDIRECT_POLARITY],
)
def test_a_redirect_into_a_client_binary_is_a_replacement(
    line: str, refused: bool, why: str
) -> None:
    """A redirection has no verb, so the verb rule cannot see it (#12069 P1)."""

    assert _replaces_a_client_binary(line) is refused, (
        f"{'REFUSED' if refused else 'ALLOWED'} expected ({why}), but the rule "
        f"{'allowed' if refused else 'refused'} {line!r}. The rule is "
        "`_redirects_into_a_client_binary`: a redirect whose TARGET is a client "
        "binary name or a path under a client `bin/`"
    )


def test_the_redirect_rule_reads_every_redirect_on_the_line() -> None:
    """`a > ok b > …/pg_dump` is ONE line; grading the first redirect misses it."""

    line = 'echo notes > "$SCRATCH/x.txt" && echo boom > /usr/lib/postgresql/16/bin/pg_dump'
    assert _replaces_a_client_binary(line), (
        "only the FIRST redirect on the line was graded, so a second one could "
        "rewrite the pinned binary with every row green (#12069 review P1)"
    )
