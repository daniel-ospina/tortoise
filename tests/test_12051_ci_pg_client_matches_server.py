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
   constant, which is present and inert at the same time.
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
- **The mutating-verb rule costs some precision.** The client name is
  read only AFTER the verb, so `rm -f "$RUNNER_TEMP/pg_dump.log"` and
  `pg_dump --version | tee -a x` are not replacements — but a genuinely
  read-only command whose verb IS in the list (`rm`, `tee`, `install`,
  `chmod`) and which names a client binary after it is refused. That is
  the right polarity here: nothing legitimate rewrites those binaries,
  and the message names the accepted form.
- **The extraction's arithmetic** (`sed -E 's/.*/16/'`) cannot be proven; the
  in-job assertion is what makes a constant visible.

So: a green run means "no plausible spelling of the regression is in this
text", not "the job provably installs and uses pg16". The job's own assertion is
the part that observes reality.
"""
from __future__ import annotations

import re
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
#: GREEN with the leg on pg17 (measured). An assignment KEEPS the pin if its value
#: names the pinned dir or still references `$PATH` (an append or a re-order cannot
#: drop what is already on it); anything else REPLACES it.
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


def _unreachable_guards(steps: list[dict], start: int, end: int) -> list[str]:
    """Lines in `[start, end)` that cannot run, as `step N: <line>`.

    The two rows that assert a construct is LIVE — the `$GITHUB_PATH` prepend and
    the armed fail-closed branch — both read this, because a construct sitting
    inside a block whose condition is a literal constant is PRESENT and INERT at
    the same time. Measured (#12051 review round 8): an enclosing `if false;
    then … fi` around the prepend, and again around the whole fail-closed `for`
    loop, left all five rows green while the leg went back to the runner image's
    client — i.e. #12051 restored in one line, with this guard's own message
    insisting it had not been.

    Read through `_run_commands()` like every other line-unit search here, so a
    tombstone comment naming a constant guard is inert text, not an offender.

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
        for line in _run_commands(steps[index]).splitlines():
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

    The verb and the binary must both be present, and the binary must be named
    AFTER the verb: reading the pair anywhere on the line made a tidy step
    (`rm -f "$RUNNER_TEMP/pg_dump.log"`) and the guard's OWN recommended
    compensating control (`pg_dump --version | tee -a "$RUNNER_TEMP/v.txt"`) read
    as replacements — both measured RED, with a message saying the step replaced
    a binary it only mentioned. The pin's own install line is excluded.
    """

    if CLIENT_PKG_RE.search(line):
        return False
    verb = BINARY_MUTATION_RE.search(line)
    if not verb:
        return False
    return bool(BINARY_NAME_RE.search(line, verb.end()))


#: A subshell opens here and is not closed on the same line — whatever runs inside
#: it is a DIFFERENT shell, so its `exit` cannot fail the step.
SUBSHELL_OPEN_RE = re.compile(r"^[ \t]*\((?!.*\))[ \t]*$")
#: …and closes with its status DISCARDED (`) || true`, `) ;`, `) && …`).
SUBSHELL_CLOSE_RE = re.compile(r"^[ \t]*\)[ \t]*(?:\|\||;|&&)")

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
    """`${{ X }}` / `X` / extra whitespace collapse to one comparable spelling."""

    text = re.sub(r"\s+", " ", str(value)).strip()
    return re.sub(r"^\$\{\{\s*|\s*\}\}$", "", text).strip()


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


#: The two clients the record drill resolves from PATH (`record_backup.py`
#: defaults each to its bare name).
TOOLS = ("pg_dump", "pg_restore")

LOOP_HEAD_RE = re.compile(
    r"^[ \t]*for[ \t]+(?P<var>[A-Za-z_]\w*)[ \t]+in[ \t]+(?P<names>[^;]*);?[ \t]*do[ \t]*$"
)
LOOP_END_RE = re.compile(r"^[ \t]*done\b")

#: Commands that PRINT their argument instead of running it. A client named after
#: one of these is MENTIONED, not checked (#12051 review round 12): with the
#: earlier substring test, `version="$(echo "$tool --version")"` — which runs
#: nothing — covered both tools, and so did `printf '%s\n' "$tool --version"`
#: beside a body that only ever checked `pg_dump`.
PRINTING_COMMANDS = frozenset({"echo", "printf", "cat", "tee", "logger", "true", ":"})

#: The COMMAND WORD that governs a position: the first word after the nearest
#: command separator (`$(`, a backtick, `;`, `&&`, `||`, `|`, a bracket, or the
#: start of the line), skipping `sudo` and `env` prefixes.
COMMAND_WORD_RE = re.compile(
    r"(?:^|\$\(|`|;|&&|\|\||\||[(){}])\s*"
    r"(?:sudo\s+|env\s+(?:-\S+\s+)*)*"
    r"([^\s;|&(){}]+)"
)


def _command_word(before: str) -> str:
    """The command word that would RUN whatever follows `before`."""

    words = COMMAND_WORD_RE.findall(before)
    return words[-1].strip("\"'") if words else ""


def _version_checked_tools(code: str) -> set[str]:
    """Which client tools this code actually VERSION-CHECKS (#12051 rounds 11-12).

    Coverage is what the code RUNS, in two accepted shapes: a `for <var> in …; do`
    loop whose body invokes `$<var> --version`, and a `<tool> --version` command.
    A bare `for` HEADER naming both tools does not count (round 11: a body
    hard-coding `pg_dump --version` left `pg_restore` unchecked and every row
    green), and neither does a QUOTED mention (round 12: `$(echo "$tool
    --version")` runs nothing). Both are the token-in-text defect rounds 4 and 7
    fixed for `::error::` / `exit 1`.
    """

    checked: set[str] = set()
    lines = code.splitlines()

    def scan(line: str, loop_var: str | None, loop_names: set[str]) -> None:
        for match in re.finditer(r"--version\b", line):
            word = _command_word(line[: match.start()])
            if not word or word in PRINTING_COMMANDS:
                continue
            if loop_var and word in (f"${loop_var}", f"${{{loop_var}}}"):
                checked.update(loop_names)
            elif word in TOOLS:
                checked.add(word)

    for index, line in enumerate(lines):
        head = LOOP_HEAD_RE.match(line)
        if not head:
            scan(line, None, set())
            continue
        names = {
            name
            for name in re.findall(r"[A-Za-z_][\w-]*", head.group("names"))
            if name in TOOLS
        }
        var = head.group("var")
        for body_line in lines[index + 1 :]:
            if LOOP_END_RE.match(body_line):
                break
            scan(body_line, var, names)
    return checked


def _path_assignments_that_drop_the_pin(
    steps: list[dict], start: int, server_major: int, job: dict | None = None
) -> list[str]:
    """PATH assignments at or after the pin that can no longer see the pinned dir.

    Only a value that neither names `/usr/lib/postgresql/<major>/bin` nor keeps
    `$PATH` can DROP it, which is what keeps the rule precise: the pin's own
    `export PATH="/usr/lib/postgresql/16/bin:$PATH"` and every append
    (`PATH="$PATH:/x"`, `PATH=/x:$PATH`) stay green, and a diagnostic
    `echo "PATH=$PATH"` is not an assignment at all.
    """

    pinned = f"/usr/lib/postgresql/{server_major}/bin"
    offenders: set[str] = set()
    for index in range(start, len(steps)):
        for line in (
            _effective_shell(job, steps[index]) + "\n" + _run_commands(steps[index])
        ).splitlines():
            for match in PATH_ASSIGN_RE.finditer(line):
                value = match.group("value")
                if pinned in value or "$PATH" in value or "${PATH}" in value:
                    continue
                if _command_word(line[: match.start()]) in PRINTING_COMMANDS:
                    continue
                offenders.add(f"step {index} REPLACES the client PATH: {line.strip()!r}")
    return sorted(offenders)


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
    unreachable = _unreachable_guards(steps, pin_index, leg_start)
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
        set(_path_assignments_that_drop_the_pin(steps, pin_index, server_major, job))
        | {
            f"step {index} REPLACES a client binary: {line!r}"
            for index, step in enumerate(steps)
            if index >= pin_index
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
        "drops the pinned dir, or a REPLACEMENT of a client binary changes which "
        "client the leg runs: "
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
    unreachable = _unreachable_guards(steps, pin_index, leg_start)
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
            "--version` in its body, or a `<tool> --version` line per tool."
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
]


#: (line, does it DROP the pinned dir, why) — the pin for
#: `_path_assignments_that_drop_the_pin`. A PATH assignment that names no client
#: dir was invisible, and `/usr/bin/pg_dump` is `pg_wrapper`, i.e. the image's
#: newest client (#12051 review round 12, measured GREEN on all five rows).
PATH_ASSIGNMENT_POLARITY = [
    ('export PATH="/usr/local/bin:/usr/bin:/bin"', True, "a bare replacement drops the pin"),
    ('PATH="/usr/bin:/bin" timeout -s INT bash x.sh', True, "a command-prefix assignment drops it"),
    ("env -i PATH=/usr/bin:/bin bash x.sh", True, "`env -i` drops it just as well"),
    ('export PATH="/usr/lib/postgresql/16/bin:$PATH"', False, "the pin's own assignment"),
    ('export PATH="$PATH:/usr/local/bin"', False, "an append cannot drop what is already there"),
    ('PATH="/usr/local/bin:$PATH" bash x.sh', False, "a re-order keeps `$PATH` intact"),
    ('echo "PATH=/usr/bin"', False, "a diagnostic PRINTS a value, it does not assign one"),
]


@pytest.mark.parametrize(
    ("line", "drops", "why"), PATH_ASSIGNMENT_POLARITY, ids=[c.replace(" ", "-")[:28] for c, _, _ in PATH_ASSIGNMENT_POLARITY]
)
def test_a_path_assignment_must_keep_the_pinned_dir(line: str, drops: bool, why: str) -> None:
    """Only a value that neither names the pin nor keeps `$PATH` can DROP it."""

    offenders = _path_assignments_that_drop_the_pin([{"run": line}], 0, 16)
    assert bool(offenders) is drops, f"{why} (measured, round 12): {line!r}"


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

