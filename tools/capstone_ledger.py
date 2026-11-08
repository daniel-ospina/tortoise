#!/usr/bin/env python3
"""#9664 — make "is the uncovered list empty?" a COMPUTED answer, not a fresh read.

THE PROBLEM THIS TOOL EXISTS FOR
--------------------------------
`#9664`'s second acceptance criterion is, verbatim:

    Every step has a verdict; the "not covered" list is explicit
    (an empty uncovered list is the only clean exit).

That question has been re-derived by hand on **every** pass since the capstone
opened, and the passes disagree because each one re-tallies prose:

  * 2026-10-26 run index: 14 rows, 1 FAIL, 2 BLOCKED, 3 NOT MEASURED
  * 2026-11-06 itemisation (comment 6692762022): "18 items — 7 green / 1 red /
    1 never-run / 8 partial / 1 n/a-gate"
  * 2026-11-06 §D append (comment 6692766222): "22 items — 9 green / 1 red /
    1 never-run / 10 partial / 1 n/a-gate"

Three different denominators for the same issue, none of them wrong and none of
them comparable. A reader asking "may this capstone close?" cannot answer from
the artifact; they must re-read 37 comments and re-decide what counts as a step.
The failure mode is a **false PASS**: a lane reports "every step has a verdict"
while the record carries uncovered rows, and nothing contradicts it (#9664's own
history has this shape — the 2026-11-05 status comment said the run had "not
been executed" over a published run).

WHAT THIS TOOL IS, AND WHAT IT IS NOT
-------------------------------------
The issue says the acceptance is *"Not a script. Not a CI job."* — that ruling
is about how the EVIDENCE IS PRODUCED, and this tool does not touch that.

  * It does **not** drive a journey, open a browser, or call a surface.
  * It does **not** decide a verdict. A runner records the verdict it reached;
    this tool only refuses a record that is internally inconsistent.
  * It is **not** selected by CI.

What it does is the one mechanical thing the capstone lacks: it validates the
recorded verdicts against a closed vocabulary and against the tree (every cited
evidence path must EXIST), then **computes** the uncovered list and exits on it.
The judgement stays with the human/agent run; the rollout becomes mechanical.
This is the same division `docs/evidence/9664-capstone/probes/step_premise_census.py`
already established for premises — a probe that measures, not a run that decides.

Record format (committed at `docs/evidence/9664-capstone/verdict-ledger.yml`):

    version: 1
    issue: 9664
    criteria_expected: 22            # REQUIRED, TOP LEVEL: pins the denominator
    measured_at: {ref: origin/main, sha: <40 hex>, date: YYYY-MM-DD}
    criteria:
      - id: C1
        text: "<the criterion, VERBATIM from the issue>"
        verdict: verified            # verified | failed | blocked | not-covered
        evidence: [<tracked repo path>, "cmd:<command>"]
                                    # >=1 TRACKED PATH required when verified;
                                    # a `cmd:` entry is a RECORD and confers nothing
        evidence_kind: screenshot    # screenshot | trace | command | server-observation
        reason: "<required unless verified>"

⛔ Every cited path must be a **TRACKED** file inside the tree. A row that names a
path which is not there — or which exists only on the authoring box, e.g. a
gitignored lane artifact — is REFUSED rather than counted, so a ledger cannot be
made green by describing evidence instead of holding it. (A row that is NOT
claimed as verified keeps its reason instead, and its citations are not
validated — the record is not asserting them as held evidence.)
`evidence_kind` grants NO waiver — it is checked for membership in a closed
vocabulary only. `server-observation` names the SHAPE of step 5's evidence, which
is a server-side observation with no screenshot by nature (#9664 comment
6692766222 §D); that row still needs a tracked artifact to be `verified`.
⛔ YAML merge keys (`<<`) are refused by name: `SafeConstructor` splices a merged
value's pairs in without constructing that node, so the duplicate-key rule cannot
see inside it, and this record's rule is that every row states its OWN verdict.

⛔ `criteria_expected` is REQUIRED. The ledger's denominator is pinned IN THE
RECORD, not derived from it, because a tool that computes the denominator from
the file it is auditing will faithfully report `uncovered: 0` for a file someone
trimmed — the 14/18/22 drift this tool exists to end, reproduced by the tool.
Without the key the check was silently absent, so "drop the rows AND delete this
block" was a second route to a clean exit; it now takes a deliberate, visible
edit. A non-integer (`true`, `1.9`) is refused rather than coerced, since Python
would truncate both to a passing one-row pin.

Exit codes (a CLOSED set — a caller may branch on them):
    0  CLEAN       the uncovered list is empty; every row valid
    1  INVALID     the ledger cannot be trusted (fail-closed; nothing is computed)
    2  UNCOVERED   valid ledger, uncovered list non-empty (the real finding)
    3  DISABLED    the off-switch was set; NO verdict was computed
    4  ADVISORY    the report was rendered and NOT enforced
    5  USAGE       `--help` or a bad invocation; NO verdict was computed

Off-switch: `TORTOISE_CAPSTONE_LEDGER=off` (kill-switch — exits 3, never 0, so
"disabled" can never be mistaken for "clean"), or `=advisory` (prints the full
report and exits **4** — NOT 0). Advisory used to return EXIT_CLEAN, which made
the one mode whose whole point is "do not treat this as a verdict" identical, to
any caller branching on the exit status, to a ledger with an empty uncovered
list. A review measured exactly that: 11 uncovered rows, exit 0. The code is
distinct now because the caller cannot be assumed to read stdout.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — an environment
# refusal must not be unattributable. This file imports nothing that would fail
# at IMPORT time on 3.9 (no `datetime`), so the guard is here for the reason the
# census gives every runnable entry point: a tool that dies on an old
# interpreter must say which file needs what, not raise a bare SyntaxError or
# ImportError from a later edit. The status is 1 and that is DELIBERATE: the set
# 0-4 below is CLOSED and fully spent, so there is no spare code, and
# `EXIT_INVALID` is the fail-closed neighbour a refusal can share.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/capstone_ledger.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/capstone_ledger.py`"
    )

import argparse
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Final, NoReturn

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER: Final[Path] = (
    REPO_ROOT / "docs" / "evidence" / "9664-capstone" / "verdict-ledger.yml"
)

#: A CLOSED vocabulary. An unrecognised word is INVALID, never "not verified":
#: a typo must not silently become a pass, and an invented status (`partial`,
#: `green`, `done`) must not silently become a pass either — the three passes
#: in this issue's history each invented their own word for the same row.
VERDICTS: Final[frozenset[str]] = frozenset({"verified", "failed", "blocked", "not-covered"})

#: The only verdict that counts toward closing. Everything else is uncovered.
CLEAN_VERDICT: Final[str] = "verified"

#: `evidence_kind` is checked for MEMBERSHIP ONLY — no kind grants a waiver. It
#: names the shape of the evidence for the reader (step 5 is a server-side
#: observation, so a screenshot can never carry it); the row still needs a TRACKED
#: artifact to be `verified`.
EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"screenshot", "trace", "command", "server-observation"}
)

EXIT_CLEAN: Final[int] = 0
EXIT_INVALID: Final[int] = 1
EXIT_UNCOVERED: Final[int] = 2
EXIT_DISABLED: Final[int] = 3
#: Advisory renders the report and enforces nothing. It must NOT reuse
#: `EXIT_CLEAN`: a caller that branches on the exit code would then read
#: "advisory, 11 rows uncovered" as "clean".
EXIT_ADVISORY: Final[int] = 4
#: Argparse's own outcomes were colliding with two verdict codes: `--help` exits 0
#: (indistinguishable from CLEAN) and a usage error exits 2 (indistinguishable from
#: UNCOVERED). Both mean "no verdict was computed", so both get their own code and
#: the other five keep one meaning each.
EXIT_USAGE: Final[int] = 5


class _Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that never borrows a VERDICT code for a usage outcome.

    Without this, `capstone_ledger.py check --nope` exits **2** — the code a caller
    branches on to mean "valid ledger, uncovered rows exist" — and `--help` exits
    **0**, the code that means "every row is covered". The closed set must not carry
    two meanings for one number.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        # `--help`/`-h` reaches here with status 0; a help screen is not a verdict.
        super().exit(EXIT_USAGE if status == 0 else status, message)


_CMD_PREFIX: Final[str] = "cmd:"


class InvalidLedger(Exception):
    """The ledger cannot be trusted, so no verdict may be computed from it."""


def load_ledger(path: Path) -> dict[str, Any]:
    """Read the YAML ledger. A missing or unreadable file is INVALID, not empty.

    ⛔ The loader REJECTS DUPLICATE KEYS. `yaml.safe_load` does not: a mapping with
    two `reason:` keys loads fine, silently keeping the LAST one, so a row can
    carry two contradictory justifications and the second one is the only one
    anyone — including this validator — ever sees. That is precisely the
    "ambiguity is a refusal" rule this module advertises, and MEASURED it was
    unenforced: a duplicate `reason:` block left by a copy-paste edit passed
    `check` (exit 2, the whole suite green) while being invalid YAML per any strict
    reader. The default parser is therefore replaced, not trusted.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - yaml is a hard dep of the repo
        raise InvalidLedger(f"PyYAML is unavailable, so no ledger can be read: {exc}") from exc

    class _NoDuplicateKeys(yaml.SafeLoader):
        """A `SafeLoader` whose mappings refuse a repeated key."""

        def construct_mapping(self, node, deep=False):
            # Merge keys (`<<: *anchor`) are legal YAML and a natural way to share
            # a verdict across rows. They are REFUSED rather than flattened — see
            # the rule below — so there is no flatten step for this scan to run
            # after. (An earlier comment here said they must be flattened FIRST;
            # that described a design this code does not implement.)
            seen: set[Any] = set()
            for key_node, _ in node.value:
                # ⛔ MERGE KEYS ARE REFUSED BY NAME. `<<` is not "just another key":
                # `SafeConstructor.construct_mapping` splices the merged pairs in
                # WITHOUT constructing that node, so any scan over THIS node's pairs
                # cannot see them — the duplicate rule included. The refusal is a
                # deliberate NARROWING: every row states its OWN verdict, so a shared
                # anchor that can carry a verdict the row does not say is not a shape
                # this record accepts.
                #
                # ⚠️ Stated precisely, because an earlier revision of this note
                # overclaimed a false PASS. With THIS `SafeLoader` subclass an
                # unrefused `<<` still fails to load (`could not determine a
                # constructor for the tag`), so the tag alone did not yield a green
                # row here; what it yielded was a scan blind to the merged pairs, in
                # a shape a future constructor would silently make green. The refusal
                # closes the SHAPE and names the defect instead of leaving the parser
                # to blame itself. It is not a reproduction of a demonstrated pass.
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise InvalidLedger(
                        f"{path} line {key_node.start_mark.line + 1} uses a YAML merge "
                        "key (`<<`) — every criterion row must state its own keys "
                        "literally, because a merged anchor can carry a verdict the "
                        "row does not say and the duplicate-key rule cannot see inside it"
                    )
                key = self.construct_object(key_node, deep=deep)
                if key in seen:
                    raise InvalidLedger(
                        f"{path} repeats the key {key!r} in one mapping "
                        f"(line {key_node.start_mark.line + 1}) — an ambiguous "
                        "row is a refusal, and the default parser would have "
                        "silently kept only the last value"
                    )
                seen.add(key)
            return super().construct_mapping(node, deep=deep)

    if not path.is_file():
        raise InvalidLedger(f"no ledger at {path} — a missing ledger is not a clean one")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_NoDuplicateKeys)
    except InvalidLedger:
        raise
    except Exception as exc:  # any parse failure is INVALID
        raise InvalidLedger(f"{path} is not parseable YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidLedger(f"{path} must be a mapping, got {type(data).__name__}")
    return data


#: A `cmd:` entry is a RECORD of what was run, not a proof: the validator never
#: executes it, so it never satisfies `verified` ON ITS OWN — a verified row must
#: also hold at least one artifact IN THE TREE (see `validate`). What the entry
#: itself must be is COMMAND-SHAPED, and the shape is where it may START.
#:
#: Where a `cmd:` entry is allowed to START. The first token must be a program
#: that RUNS something, because the attack a token scan cannot see is a sentence
#: that merely MENTIONS a file in the tree: measured, `cmd:verified manually by
#: reading pyproject.toml` satisfied `verified` and emptied the whole uncovered
#: list, as did `cmd:NOT RUN - see tools/capstone_ledger.py`. Requiring the
#: command SHAPE (runner first) is what separates a command from a claim.
#: Adding a runner is a one-line change; that is deliberate — the alternative is
#: guessing at prose.
RUNNERS: Final[frozenset[str]] = frozenset(
    {
        "uv",
        "python",
        "python3",
        "pytest",
        "node",
        "npm",
        "npx",
        "bun",
        "deno",
        "bash",
        "sh",
        "make",
        "docker",
        "git",
        "curl",
        "timeout",
        "env",
        "uvx",
        "poetry",
        # A verifier this repo actually runs, and a legitimate SECOND command in a
        # compound record (`uv run pytest … && ruff check <path>`). Without it that
        # tail segment is prose by the every-segment rule and the whole row is
        # refused — a false block on a real record.
        "ruff",
    }
)

#: Words that may sit BETWEEN a runner and its target: a runner CHAIN
#: (`uv run python …`, `poetry run pytest …`). Skipping these is what keeps
#: `uv run python -m pytest tests/x.py` accepted without letting a scanner wander
#: off into the rest of a sentence.
#:
#: ⛔ DELIBERATELY NOT EXTENDED to subcommand verbs (`ruff check <path>`,
#: `docker build <path>`) or to a "the next token is a program" skip. Both were
#: tried and both reopen the hole this arm exists to close: with them,
#: `cmd:uv run not run - see tools/capstone_ledger.py` is accepted, because every
#: word before the path happens to be a chain/subcommand word. A false BLOCK on
#: `ruff check <path>` is friction; a false PASS is the failure mode this whole
#: artifact exists to refuse, and that trade is not close.
_CHAIN_WORDS: Final[frozenset[str]] = frozenset({"run", "exec", "x"})

#: The ONE way a subcommand verb is admitted: **per runner**, never as a global
#: word set. `cmd:ruff check <path>` / `cmd:ruff format --check <path>` were REFUSED
#: (`'check' is not a file in this tree`) at `0c259a39f3` and on `origin/main` at
#: `5ef5805f0` — exit 1, which prints no report, so a lane recording the lint gate it
#: actually ran lost the whole uncovered list.
#:
#: ⛔ What this does NOT admit, stated so the next lane does not read the fix as
#: wider than it is: `.github/workflows/main-lint.yml:215` runs a bare `ruff check .`,
#: and `cmd:ruff check .` is STILL refused — `.` resolves to the repository root and
#: names everything, which is the vacuity `cmd:pytest .` is refused for
#: (`_names_artifact`, "a bare `*`/`.`/`/` is not a citation"). A lane recording that
#: shape must spell the target as a path (`ruff check tools/`, `ruff check <file>`).
#: Pinned as REFUSED by
#: `test_a_subcommand_verb_is_admitted_per_runner_and_nowhere_else`. Admitting `.`
#: under a consumed subcommand is a defensible narrowing (it would confer nothing,
#: like `cmd:make`), but it weakens a load-bearing vacuity rule and belongs in its
#: own change with its own measurement — not folded in here.
#:
#: ⛔ And it was not fixable by skipping subcommand verbs GENERALLY, which is what a
#: GLOBAL word set does — measured under that mutant, it newly admits
#: `cmd:make check` and `cmd:docker format` (a bare word behind ANY runner becomes
#: skippable, i.e. the one-word claim this gate exists to refuse). Keyed by the
#: runner ACTUALLY being invoked (the last RUNNER token in the chain, so
#: `uv run ruff …` resolves to `ruff`), those stay refused.
#:
#: ⛔ SCOPED TO THE RUNNERS THIS REPO WRITES DOWN — one entry, added because a real
#: record was refused without it. A subcommand word is a NON-ARTIFACT argument, so
#: every entry weakens the arm slightly; extend by measurement, never by analogy.
_RUNNER_SUBCOMMANDS: Final[dict[str, frozenset[str]]] = {
    "ruff": frozenset({"check", "format", "rule", "config", "linter", "clean"}),
    # `uv lock --check` is the documented lockfile-drift gate (`AGENTS.md`). With no
    # target it is a RECORD that confers nothing — the same arm that admits
    # `cmd:make`. Without this entry `lock` was read as an ARTIFACT and refused, a
    # false block on a shape `AGENTS.md` hands a lane (measured by the second review
    # cycle). It buys no strictness back: `cmd:uv lock NOT RUN` still put `NOT` in
    # artifact position and is still REFUSED, so only the SUBCOMMAND is recognised
    # and the argument grading after it is unchanged.
    "uv": frozenset({"lock"}),
}

#: A runner's own non-artifact arguments, which must not be mistaken for the target:
#: a bare duration (`timeout 300 …`) and an environment assignment (`env FOO=1 …`).
#: Both are literal by construction, so allowing them costs no strictness.
_NUMBER_ARG: Final = re.compile(r"^[0-9]+(?:\.[0-9]+)?[smhd]?$")
_ENV_ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: One leading `NAME=value` assignment and the whitespace after it, with a QUOTED
#: value allowed (`TORTOISE_DB_URI='docker://user:pass@host:6379/graph'`). Stripped
#: from the STRING by `_command_body`, never from a token list, so the quoting of
#: the rest of the command survives.
_LEADING_ASSIGNMENT: Final = re.compile(
    r"""^[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"]*"|[^\s'"])+(?:\s+|$)"""
)

#: A `cd <dir>` WRAPPER segment (no `;`/flags — the compound-separator forms are not
#: normalised, only the plain `cd X && …` this repo's own recipes use).
_CD_SEGMENT: Final = re.compile(r"^cd\s+(\S[^;|&]*)$")

#: The operand a shell CONTROL builtin accepts (`man bash`): one optional integer,
#: OR the shell's own status expansion. `exit 1`, `return 0`, `shift 2`, `break`,
#: `continue 3`, `exit $?`, and — measured, `bash -c 'shift $#'` → rc 0 — `$#`.
#: A prose argument (`exit "I did not run this"`) does not match, which is the point:
#: the control builtins were added to `_WRAPPER_VERBS` for `|| exit 1`, and without an
#: argument grammar they admitted the very prose carriage that set exists to refuse.
_CONTROL_OPERAND: Final = re.compile(r"^(?:[+-]?\d{1,10}|\$\?|\$\{\?\}|\$#|\$\{#\})$")

#: The shell CONTROL builtins admitted as wrappers. Named separately because the
#: `exit`-position rule must treat them as non-inert: they terminate rather than
#: cite, so a later one is not evidence that the graded command ran.
_CONTROL_BUILTINS: Final[frozenset[str]] = frozenset(
    {"exit", "return", "shift", "break", "continue"}
)
#: A bare NAME — an `export`/`unset` operand that is not an assignment.
_ENV_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: ONE array ELEMENT of a NAME, e.g. `unset 'A[0]'`. ⛔ `unset` takes this form and
#: bash exits 0 for it, so requiring a bare NAME refused it — a false block on a
#: record bash accepts, and exit 1 hides the whole uncovered report. Deliberately
#: NOT extended to `export`: `export 'A[0]'` is a real bash error ("not a valid
#: identifier"), so admitting it there would be a false PASS. ⛔ The SUBSCRIPT is
#: arbitrary inert data (`bash -c "unset 'A[I did not run this]'"` → rc 0) and is
#: NOT graded — declared as an open carriage in the ledger.
_ENV_ARRAY_ELEMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\[[^\]]+\]$")

#: The option NAMES `set -o`/`set +o` accept — `bash -c 'set -o'` on this fleet.
#: ⛔ VALIDATED, because an UNKNOWN name is an ERROR (`bash -c 'set -o NOTRUN'`
#: → rc 2): the `&&` short-circuits and the graded command provably never ran, so
#: consuming any bare word here admitted `set -o "I did not run this" && <cmd>` as
#: CLEAN. That is a false PASS, which is worse than a false block.
_SET_OPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "allexport",
        "braceexpand",
        "emacs",
        "errexit",
        "errtrace",
        "functrace",
        "hashall",
        "histexpand",
        "history",
        "ignoreeof",
        "interactive-comments",
        "keyword",
        "monitor",
        "noclobber",
        "noexec",
        "noglob",
        "nolog",
        "notify",
        "nounset",
        "onecmd",
        "physical",
        "pipefail",
        "posix",
        "privileged",
        "verbose",
        "vi",
        "xtrace",
    }
)

#: The single-letter flags `set` accepts (`man bash`:
#: `set [-abefhkmnptuvxBCEHPT] [-o option-name] [--] [-] [arg …]`). ⛔ Validated for
#: the same reason as `_SET_OPTION_NAMES`: `set -ZZZ` exits 2, so a record for
#: `set -ZZZ && <cmd>` names a command that never ran. `-` and `+` alone are
#: valid (`set -` clears -x/-v) and are handled before this set is consulted.
#: ⛔ `o` IS in the cluster set even though `man bash`'s synopsis lists `-o`
#: separately: bash accepts `-euo pipefail` as the cluster `-e -u -o pipefail`, so
#: omitting `o` here false-blocked the canonical `set -euo pipefail` preamble —
#: measured, not assumed.
_SET_SHORT_FLAGS: Final[frozenset[str]] = frozenset("abefhkmnoptuvxBCEHPT")

#: The flag LETTERS `export` and `unset` accept, MEASURED (not assumed):
#: `export -f/-n/-p` → rc 0/0/0 while `export -e/-x/-v` → rc 2; `unset -f/-n/-v`
#: → rc 0/0/0 while `unset -e/-p` → rc 2. ⛔ Validated for the same reason as the
#: `set` sets: a rejected flag makes bash exit 2, so `export -e NOT RUN && <cmd>`
#: is a record for a command that never ran.
_ENV_FLAGS: Final[dict[str, frozenset[str]]] = {
    "export": frozenset("fnp"),
    "unset": frozenset("fnv"),
}

#: A `umask` mask: octal, or the symbolic form. ⛔ The symbolic form is a
#: COMMA-SEPARATED CLAUSE LIST (`u=rwx,g=rx,o=`), and the first grammar matched only
#: ONE clause. ⛔ The ONLY symbolic mode characters bash's `umask` accepts are
#: `rwx` — MEASURED: `umask a+s` / `g+s` / `o+t` / `u=X` all exit 1 ("invalid
#: symbolic mode character"), so admitting `s`/`t`/`X` here (as `chmod` allows)
#: admitted records for a command that never ran. ⛔ Octal is up to FIVE digits:
#: `umask 07777` exits 0, so `{1,4}` false-blocked it.
_UMASK_ARG: Final = re.compile(r"^(?:[0-7]{1,5}|[ugoa]*[+\-=][rwx]*(?:,[ugoa]*[+\-=][rwx]*)*)$")


def _fails_before_a_chain(head: str, args: list[str]) -> bool:
    """Does ``head args`` FAIL at script level, so a `&&` after it short-circuits?

    ⛔ MEASURED, not inferred (fifth review cycle). The previous guard asked only
    about `exit`, and justified exempting `return` with a measurement taken using
    the WRONG SEPARATOR: `bash -c 'return 0 ; echo RAN-AFTER'` does print
    RAN-AFTER, because `;` continues past a failure — but a record chains with
    `&&`, where the same failure short-circuits. Measured on this box, each
    followed by `&& echo RAN`:

        exit 0       rc 0   no RAN     (terminates — the original case)
        return 0/1   rc 2   no RAN     !!! admitted before this function
        shift 5      rc 1   no RAN     !!! admitted before this function
        shift        rc 1   no RAN     (bare `shift` is `shift 1`)
        shift 0      rc 0   RAN        (succeeds, so NOT terminating)
        break        rc 0   RAN        (warns, then continues)
        continue     rc 0   RAN        (warns, then continues)

    So a control builtin is TERMINATING unless it can succeed at script level,
    which for `shift` means an explicit `0` operand and for `break`/`continue` is
    always.

    ⛔ THE GUARD IS SEPARATOR-BLIND, AND THAT IS A DECLARED FALSE BLOCK (#11520).
    Only a `&&` short-circuits a failure: measured, `bash -c 'return 0 ; echo RAN'`
    and `bash -c 'return 0 || echo RAN'` both print RAN, rc 0. `_split_segments`
    discards the operator it split on, so this predicate cannot see which one
    follows, and a `;`/`||` record bash really executes with is refused too (exit 1
    prints no report, so it loses that row's uncovered list). Telling them apart
    needs the splitter to hand the operator through; the refusal is kept on the
    conservative side until it does, because a fail-OPEN here is the P1 this guard
    was written for. The message therefore names the CONDITION, not a `&&` the
    record may not contain.

    ⛔ `set -o <invalid-option>` USED to be recorded here as an open
    residual on the grounds that refusing it needs the closed set of bash option
    NAMES "which this module deliberately does not carry" — that is no longer true:
    the module DOES carry `_SET_OPTION_NAMES`, and `_wrapper_argument_defect`
    REFUSES `set -o NOTRUN` / `set -o "I did not run this"` (measured). Leaving the
    old sentence in place told a maintainer a live guard was dead weight, which is
    the same false-attestation class as an over-claiming ledger — corrected, not
    deleted, so the next reader sees that the guard exists and where it lives.
    """
    if head in {"exit", "return"}:
        return True
    if head == "shift":
        # `shift 0` is the only spelling that succeeds with no positional params.
        return not (len(args) == 1 and args[0] == "0")
    # `break` / `continue` warn and CONTINUE at script level (measured rc 0).
    return False


def _wrapper_argument_defect(head: str, args: list[str], cwd: Path, root: Path) -> str | None:
    """Why ``head args`` is not a well-formed shell WRAPPER, or `None` if it is.

    ⛔ A wrapper used to be admitted on its HEAD alone, so its arguments were never
    read — measured on the merged tree, `cmd:export NOT RUN && uv run pytest …`,
    `cmd:source NOT RUN && …` and `cmd:echo NOT RUN … && …` were ACCEPTED while the
    naked `cmd:NOT RUN && …` was REFUSED: the same disclaimer, admitted by one
    word. The wrapper runs nothing that is being cited, so admitting it is right;
    admitting it WITHOUT READING ITS ARGUMENTS is what made it a bypass.

    Each wrapper here has a grammar the shell itself enforces, so the argument is
    checked. `export`/`unset` take NAMEs, `set` takes options, `umask` takes a
    mask, and `source` takes a file that must EXIST (a failing `source` short-
    circuits the `&&`, so no later segment can have run).

    ⛔ A DECORATIVE PRINTER is the genuinely undecidable half — `echo` may print
    any text and `… && echo PASS` must stay legal — so it is named as a residual at
    `_DECORATIVE` rather than left for a reader to infer as closed.
    """
    # ⛔ CONTROL builtins take at most ONE optional integer operand (`man bash`:
    # `exit [n]`, `return [n]`, `shift [n]`, `break [n]`, `continue [n]`). WITHOUT
    # this branch they fell through to the `return None` below and their arguments
    # were NEVER READ, so `… && exit "I did not run this"` reached `verified` /
    # `uncovered: 0` / exit 0 behind every control head — and the ledger's own "the
    # WRAPPER half of that root is closed" sentence became false (fifth review
    # cycle). Measured: `export`/`unset`/`umask`/`set` refused the same carriage;
    # only the newly-added builtins admitted it.
    if head in _CONTROL_BUILTINS:
        # ⛔ AT MOST ONE. `man bash` gives `exit [n]` / `return [n]` / `shift [n]` /
        # `break [n]` / `continue [n]`, and the shell ENFORCES the arity: measured,
        # `bash -c 'exit 0 0 && echo RAN'` prints "exit: too many arguments" and the
        # chain status is 1, so the graded command never ran. Validating each
        # argument without bounding the COUNT accepted `cmd:exit 0 0 && uv run
        # pytest …` (sixth review cycle) — a record admitted for a command the shell
        # refuses, which is the same class of false PASS this branch exists to close.
        if len(args) > 1:
            return f"takes at most one integer argument, got {len(args)}"
        for arg in args:
            if not _CONTROL_OPERAND.match(arg):
                return f"{_short(arg)} is not a numeric argument"
        return None
    if head in {"export", "unset"}:
        for arg in args:
            if arg == "--":
                continue
            if arg.startswith("-") or arg.startswith("+"):
                # ⛔ VALIDATED, not skipped. An invalid flag is an ERROR
                # (`export -e NOTRUN` → rc 2, `unset -p X` → rc 2), so the `&&`
                # short-circuits and the graded command PROVABLY never ran.
                # Skipping dashed tokens admitted `export -e NOT RUN && <cmd>` as
                # `verified` / `uncovered: 0` / CLEAN — a false PASS, not a
                # convenience. The accepted letters are measured, not assumed.
                # ⛔ A BARE `-`/`+` is not a flag: `export -` exits 1, so it is
                # refused; `unset -` exits 0, so it is admitted.
                if not arg[1:]:
                    if head == "unset" and arg == "-":
                        continue
                    return f"{_short(arg)} is not a `{head}` flag"
                if not set(arg[1:]) <= _ENV_FLAGS[head]:
                    return f"{_short(arg)} is not a `{head}` flag"
                continue
            if _ENV_NAME.match(arg) or _ENV_ASSIGNMENT.match(arg):
                continue
            # ⛔ `unset 'A[0]'` removes ONE array ELEMENT and bash exits 0 for it;
            # requiring a bare NAME (or `NAME=`) refused it as prose. `unset` only:
            # see `_ENV_ARRAY_ELEMENT` for why `export` keeps the stricter rule.
            # The SUBSCRIPT is arbitrary inert data and is NOT graded — declared as
            # an open carriage in `docs/evidence/9664-capstone/verdict-ledger.yml`,
            # because an unlisted carriage reads as closed.
            if head == "unset" and _ENV_ARRAY_ELEMENT.match(arg):
                continue
            return f"{_short(arg)} is neither a NAME nor a NAME=value assignment"
        return None
    if head == "set":
        # ⛔ `man bash`: `set [-abefhkmnptuvxBCEHPT] [-o option-name] [--] [-] [arg …]`.
        # The option NAME after `-o`/`+o` is a BARE operand, and the whole line is the
        # canonical bash preamble — every `.ci-checks/*.sh` in this repo opens with
        # it. Requiring every operand to start with `-`/`+` REFUSED `set -euo
        # pipefail` and `set -o pipefail`, i.e. a false block on the most ordinary
        # wrapper there is (found by the second review cycle of this branch).
        pending_name = False
        for arg in args:
            # ⛔ `--` ENDS option parsing: every operand after it is a POSITIONAL
            # PARAMETER, not an option (`man bash`: `set … [--] [-] [arg …]`).
            # Requiring each of those to start with `-`/`+` refused `set -- a b`,
            # which bash accepts (rc 0) — a false block on a record that ran, and
            # exit 1 hides the whole uncovered report. The parameters are inert (they
            # are ASSIGNED, never executed), so their text is not graded — declared
            # as an open carriage in the ledger rather than left to read as closed.
            if arg == "--":
                return None
            if arg in {"-", "+"}:
                pending_name = False
                continue
            is_flag = arg.startswith("-") or arg.startswith("+")
            if pending_name and not is_flag:
                pending_name = False
                # ⛔ The option NAME is VALIDATED. `bash -c 'set -o NOTRUN'` exits 2,
                # so the `&&` short-circuits and the graded command provably never
                # ran; consuming any bare word admitted `set -o "I did not run
                # this" && <cmd>` as CLEAN — a false PASS.
                if arg not in _SET_OPTION_NAMES:
                    return f"{_short(arg)} is not a shell option name"
                continue
            if not is_flag:
                return f"{_short(arg)} is not a shell option"
            # ⛔ The FLAG LETTERS are validated for the same reason as the option
            # name: `set -ZZZ` exits 2, so a record for `set -ZZZ && <cmd>` names a
            # command that never ran.
            if not set(arg[1:]) <= _SET_SHORT_FLAGS:
                return f"{_short(arg)} is not a shell option"
            # ⛔ `o` ANYWHERE in the cluster takes the next BARE word as the option
            # name — not only a trailing `o`. MEASURED: `set -oe pipefail`,
            # `set -eox pipefail` and `set -ou pipefail` all exit 0, so an
            # `endswith("o")` test false-blocked them, while `set -o -e` and
            # `set -o --` exit 0 too and are handled by the `is_flag` / `--` arms
            # above rather than being read as option names.
            pending_name = "o" in arg[1:]
        return None
    if head == "umask":
        # ⛔ `man bash`: `umask [-p] [-S] [mode]` — the FLAGS precede the mask, so
        # `umask -S` was refused as "not a umask mask".
        # ⛔ `--` ends option parsing here too (`bash -c 'umask -- 022'` → rc 0).
        # `set` got `--` before `umask` did, so the SAME asymmetry left `umask --
        # 022` refused while `set -- a b` was accepted — the false block halved.
        for arg in args:
            if arg in {"-p", "-S", "-pS", "-Sp", "--"}:
                continue
            if not _UMASK_ARG.match(arg):
                return f"{_short(arg)} is not a umask mask"
        return None
    # ⛔ `source` is graded on EXISTENCE only, and the limit is named rather than
    # implied closed. `is_file()` proves the target is there, NOT that sourcing it
    # SUCCEEDS — `source tests/test_9664_capstone_verdict_ledger.py` is a real file
    # bash refuses (exit 2), so the `&&` short-circuits and the graded command
    # provably never ran. That residual is PINNED as OPEN
    # (`test_source_of_an_existing_but_unsourceable_file_is_a_known_gap`).
    #
    # The alternative — requiring the target to be TRACKED and `bash -n` clean —
    # closes it, but it REFUSES `source .venv/bin/activate`, which is a command a
    # lane really records and which this branch already pins as a legitimate
    # record. The module's own rule is that "a refusal that loses the finding is
    # worse than the weaker arm", so non-existence is what is refused here. THIS
    # DOES make the verdict box-dependent for `source` wrappers (`.venv` exists on
    # the authoring box and in no clone); that is the cost of not taking a false
    # block, it is disclosed here, and it is tracked on #11520.
    # ⛔ `.` is the POSIX spelling of `source`, so it gets the SAME arm. Refusing it
    # while admitting `source .venv/bin/activate` is incoherent — the module's own
    # rationale for that admission applies verbatim to `. .venv/bin/activate`.
    # Measured false block: `cmd:. .venv/bin/activate && uv run pytest …` was ACCEPTED
    # on `origin/main` and REFUSED here as "neither a command nor a shell wrapper"
    # (third review cycle).
    if head in {"source", "."}:
        if not args:
            return None
        target = args[0].strip("'\"")
        try:
            # ⛔ `~` MUST be expanded before the join: `cwd / "~/.bashrc"` is
            # `<repo>/~/.bashrc`, a path that exists on NO box, so a record a lane
            # really writes (`source ~/.bashrc && …`, `source ~/.nvm/nvm.sh && …`)
            # was refused with "does not name a file" on every machine — not the
            # box-dependence disclosed below, but a definite non-resolution.
            # Measured false block; `origin/main` accepted the shape (third review
            # cycle). An absolute expanded path wins the join, which is the intent.
            resolved = (cwd / Path(target).expanduser()).resolve()
        except (OSError, RuntimeError, ValueError):
            resolved = None
        if resolved is None or not resolved.is_file():
            return (
                f"{_short(target)} does not name a file, and a failing `{head}` "
                "short-circuits the `&&` — no later segment of this record can have run"
            )
        return None
    return None


def _split_flag(token: str) -> tuple[str, str]:
    """Split one dashed token into its FLAG and any value ATTACHED to it.

    `-c` -> `('-c', '')`, `-cBODY` -> `('-c', 'BODY')`, `--eval=BODY` ->
    `('--eval', 'BODY')`, `--check` -> `('--check', '')`. The ATTACHED form is what
    `bash -c'…'`, `bash -e'…'` and `--eval="…"` produce, and it is the reason
    `token in _SCRIPT_FLAGS` was not enough: shlex returns the flag and its body as
    ONE token, so the body was never graded. See the call site for the measurement.

    ⛔ A SHORT-FLAG CLUSTER is why this is not `token[:2]`. `man bash` lets the
    operand-taking flag sit anywhere the group ends (`bash -ec CMD`, `bash -euc
    CMD`), so taking the first two characters graded the leftover `c`/`uc` as the
    SCRIPT BODY and refused a legitimate record with a nonsense reason — "script 'c'
    is not a command". The split lands on the LAST script flag in the cluster and
    the remainder after it is the body; when nothing follows it, the body is the
    NEXT token and the caller's `consumed_script` hand-off applies.
    """
    if token.startswith("--"):
        flag, sep, value = token.partition("=")
        return flag, (value if sep else "")
    found = None
    for position, letter in enumerate(token[1:], start=1):
        # ⛔ SCAN ONLY THE LEADING FLAG CLUSTER — stop where the BODY begins (review
        # cycle 5, P1). Scanning the WHOLE token let a `-c`/`-e`-looking LETTER
        # INSIDE THE BODY win: measured, `bash -c'uv run pytest
        # tools/capstone_ledger.py'` set `found` on the `e` of "ledger", graded
        # `'r.py'` as the script and REFUSED a legitimate record (a false block, and
        # exit 1 hides the uncovered list) — and the prose spelling was refused by
        # that same mis-split rather than by any rule. The cluster ends at the first
        # character that is neither a shell single-letter flag NOR a script flag,
        # which is exactly where `-cBODY`'s body starts; `-ec` / `-euc` / `-cu` stay
        # inside the cluster, so the operand-taking flag is still found in it and the
        # body still comes from the NEXT token.
        if f"-{letter}" in _SCRIPT_FLAGS:
            found = position
            continue
        if letter not in _SHORT_SHELL_FLAGS:
            break
    if found is not None:
        remainder = token[found + 1 :]
        # ⛔ A PURE OPTION CLUSTER IS MORE FLAGS, NOT A BODY. Measured against real
        # bash: `bash -eu -c CMD` prints its script (it is `-e -u -c CMD`), and
        # `bash -cu CMD` prints its script too (`-c -u CMD` — the operand-taking
        # flag takes the NEXT token, not the rest of the group). Reading the
        # leftover `u` as the script body refused BOTH with "script 'u' is not a
        # command" (third review cycle): a false block, and exit 1 prints no
        # uncovered list, so it cost the whole report. The old split only held when
        # the operand-taking flag happened to be LAST in the group (`-ec`, `-euc`).
        # ⛔ BUT "all lowercase letters" IS NOT "an option cluster" — `-cverified`
        # is all lowercase and is a CLAIM, not flags (fourth review cycle: it
        # reached `verified` / exit 0). The remainder is flags only when every
        # character is a letter a shell actually accepts (`_SHORT_SHELL_FLAGS`).
        # Anything else is a real attached body and is still graded as a script —
        # that is the bypass this arm exists to close, and `bash -c'NOT RUN'`
        # must stay refused.
        if remainder and set(remainder) <= _SHORT_SHELL_FLAGS:
            for letter in token[1:]:
                if f"-{letter}" in _OPERAND_SCRIPT_FLAGS:
                    return f"-{letter}", ""
            return token, ""
        return f"-{token[found]}", remainder
    if len(token) > 2:
        return token[:2], token[2:]
    return token, ""


#: A `-c`/`-e` script may itself contain a `-c`. Bounded so a crafted record cannot
#: recurse the validator; anything past this is a refusal, not a hang.
_MAX_SCRIPT_DEPTH: Final[int] = 3


#: ⛔ EVERY shell separator splits a record, not just `&&`. Splitting only on `&&`
#: left the whole false-PASS class reachable one separator over: measured at the
#: round-2 head, `cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q;
#: NOT RUN` (and the `||`, `|`, `&` and newline spellings, and the same text inside
#: a `bash -c` body) were ACCEPTED, and a 22-row ledger relabelled that way
#: reported `verified: 22  uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0.
#: Two-character operators are matched before their one-character prefixes.
_TWO_CHAR_SEPARATORS: Final[tuple[str, ...]] = ("&&", "||")
_ONE_CHAR_SEPARATORS: Final[frozenset[str]] = frozenset({";", "|", "&", "\n"})


def _is_redirection(text: str, index: int) -> bool:
    """Is the `&`/`|` at `index` part of a REDIRECTION rather than a separator?

    The shell's rule is adjacency to `>`: `&>`/`&>>` put the `&` before it;
    `2>&1`, `>&2`, `N>&M`, `N>&-` and bash's noclobber override `>|` put it after.
    Anything else — `a & b`, `a | b`, `a |& b` — is a separator, and the empty
    middle segment it leaves behind is skipped.

    ⛔ `|&` is NOT "a pipe plus a background marker" (a false statement this
    docstring used to make): per `man bash` §"Pipelines", it is a pipeline control
    operator whose stderr is also piped — shorthand for `2>&1 |`. It still
    separates, so the split is right; the justification was not, and a wrong reason
    in a paragraph whose job is to make the next reader trust the splitter invites
    the wrong inference (that something is backgrounded, so its status stops gating
    the `&&` — which it does not).
    """
    if text[index] not in "&|":
        return False
    return text[index - 1 : index] == ">" or text[index + 1 : index + 2] == ">"


def _split_segments(text: str) -> list[str]:
    """Split a compound command line on every UNQUOTED shell separator.

    A plain `text.split("&&")` splits inside quotes too, so `bash -c 'a && b'` —
    the only way the docstring says a script can be recorded — came back as
    `bash -c 'a` and was refused as unparseable, which is exit 1, which hides the
    entire uncovered list. Only a separator the SHELL would treat as one splits
    here.

    ⛔ An unquoted `#` at a WORD BOUNDARY ends the command LINE (#11506 review,
    cycle 2). The shell starts a comment there, so no separator on that line
    splits — but `\n` is still a separator, so the scan RESUMES after the newline
    (cycle 3: `break` discarded the rest of the RECORD, which re-opened the
    newline fail-open this PR had just closed). Measured, `… -q  # ran; 194 passed`
    used to come back as the segment `194 passed`, exit 1, and NO uncovered list;
    `origin/main` ACCEPTED it, so the every-segment rule had introduced another
    false block, and the per-segment whitespace-preceded-hash retry further down
    could never undo a split already taken here. `a#b` is NOT a comment (the `#`
    must follow whitespace, start, or a separator), and a quoted `#` is not either
    — `-k 'issue#123'` still parses.

    ⛔ A backslash escapes in TWO places, not one, and the shipped code handles
    both — the docstring said only the first for a round after the second landed,
    which is how a maintainer deletes the branch that keeps a legitimate record
    whole. (1) inside DOUBLE quotes it must take the escaped character with it —
    otherwise `bash -c "echo \\" && echo x"` ends the quote early and the command
    is split at the wrong place (ported from the peer lane's `_split_on_chain` at
    35ea7658e, which handles that case; this branch's first version did not).
    (2) UNQUOTED, `\\` + newline is a LINE CONTINUATION: bash joins the lines, so
    `cmd:… -q \\` NEWLINE `  --maxfail=1` is ONE command and splitting it there is a
    measured false block (exit 1, and exit 1 prints no uncovered list). Both are
    pinned in `test_the_commands_agents_md_hands_a_lane_are_not_false_blocked`
    (the `… -q \\` NEWLINE and `… && \\` NEWLINE params).
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(text):
                current.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
            current.append(char)
        elif char == "\\" and index + 1 < len(text):
            # An UNQUOTED backslash (#11506 review, cycle 4). The shell's rule is not
            # "only inside double quotes": an unquoted `\` escapes the next
            # character, and `\` + NEWLINE is a LINE CONTINUATION — the shell JOINS
            # the lines, so that newline is NOT a separator. Untreated, a record
            # pasted from a wrapped terminal command was torn at the `\` and
            # REFUSED: measured, `cmd:uv run pytest … -q \` NEWLINE `  --maxfail=1`
            # exited 1 with NO uncovered list, while `origin/main` ACCEPTED it — a
            # regression of exactly the class this ruleset exists to remove.
            # The backslash is preserved for any OTHER escaped character, so the
            # later `shlex.split` still sees the quoting the record was written with.
            following = text[index + 1]
            if following == "\n":
                index += 2
                continue
            current.append(char)
            current.append(following)
            index += 2
            continue
        elif char == "#" and (index == 0 or text[index - 1] in " \t\n;&|"):
            # A shell COMMENT starts here: the rest of the LINE is not a command, so
            # no separator on this line may split — but `\n` is a separator, so the
            # scan resumes AFTER the newline rather than ending the record (#11506
            # review, cycle 3).
            #
            # ⛔ `break` here was a P1 FAIL-OPEN: it discarded the rest of the
            # STRING, not the line, so a comment on line 1 made every following
            # line invisible — and since this PR made `\n` a separator precisely to
            # close `cmd:<cmd>` NEWLINE `NOT RUN`, a comment re-opened exactly that
            # hole. Measured at `e7776efc7` with a `|` block scalar:
            #
            #   cmd:uv run pytest … -q  # ran; 194 passed
            #   NOT RUN - I did not run this
            #
            # → `uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0; the same
            # record was INVALID at the parent commit. Flushing the pending segment
            # and resuming after the newline keeps the cycle-2 false block closed
            # (a comment with no newline still ends the entry) AND grades what
            # follows it.
            segments.append("".join(current))
            current = []
            newline = text.find("\n", index)
            if newline < 0:
                break
            index = newline + 1
            continue
        elif any(text.startswith(op, index) for op in _TWO_CHAR_SEPARATORS):
            segments.append("".join(current))
            current = []
            index += 2
            continue
        elif char in "&|" and _is_redirection(text, index):
            # ⛔ A REDIRECTION, not a separator (#11506 review). The splitter runs
            # over every `;` `|` `&` on the line, so an ordinary recorded command
            # carrying a redirection was torn in two and REFUSED: measured at the
            # pre-fix head, `… -q 2>&1 | tail -20`, `… -q > /tmp/o.txt 2>&1`,
            # `… -q &> /tmp/o.txt`, `… -q | tee log` AND `… -q >| out.txt` all exited
            # 1 — and exit 1 prints NO report, so the whole uncovered list vanished.
            # Every one of them was ACCEPTED on `origin/main`, so the every-segment
            # rule had introduced a false block of exactly the class this PR exists
            # to remove. `>|` was the one spelling the first fix missed: the SAME
            # defect in the SAME function, one separator over.
            #
            # The shell's own rule is ADJACENCY TO `>`: `&>`/`&>>` put the `&` before
            # it; `2>&1`, `>&2`, `N>&M`, `N>&-` and bash's noclobber override `>|`
            # put it after. A `&`/`|` that is not adjacent still separates — `a & b`
            # backgrounds `a`, `a | b` pipes (both pinned).
            current.append(char)
        elif char in _ONE_CHAR_SEPARATORS:
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments]


#: Flags whose VALUE is a SCRIPT BODY, not an artifact path: `bash -c '<script>'`,
#: `sh -c '<script>'`, `python -c '<code>'`, `node -e '<code>'`. The value cannot be
#: graded as a citation — it is not a path — so the entry is a RECORD and the held
#: artifact carries the verdict. Without this, every `bash -c '…'` row is a false
#: block, and `exit 1` computes nothing, so it would hide the uncovered list too.
#: The single-letter options a SHELL accepts on its own command line (`man bash`:
#: `set [-abefhkmnptuvxBCEHPT]`, plus bash's own `-i -l -r -s -D -O`). A leftover
#: remainder is "more flags" ONLY when EVERY character is one of these.
#: ⛔ Testing merely `isalpha() and islower()` ADMITTED `cmd:bash -cverified` and
#: `cmd:bash -cnotrun` as "a cluster of flags" — a one-word CLAIM reaching `verified`
#: / `uncovered: 0` / exit 0, i.e. the exact bypass `_split_flag` exists to close,
#: re-opened by the previous cycle's own fix (fourth review cycle). `verified`
#: carries `d`, which no shell accepts, so it is graded as a BODY again.
_SHORT_SHELL_FLAGS: Final[frozenset[str]] = frozenset("abefhkmnptuvxBCEHPTilrsDO")

#: The short/long flags that actually TAKE A SCRIPT OPERAND. Split out from
#: `_SCRIPT_FLAGS` because the two questions are different: `_SCRIPT_FLAGS` also
#: carries `-e`, which takes NO operand and is listed only because a shlex-attached
#: `-eNOT RUN` was a measured bypass. Whether the CLUSTER hands the script off to the
#: next token ("is an operand-taking flag present?") must not be answered by `-e`.
_OPERAND_SCRIPT_FLAGS: Final[frozenset[str]] = frozenset({"-c", "--command", "--eval"})

_SCRIPT_FLAGS: Final[frozenset[str]] = frozenset({"-c", "-e", "--command", "--eval"})

#: ⛔ THERE IS DELIBERATELY NO "project label" ARM. An earlier revision of this
#: branch accepted a lone bare word for `make`/`npm`/`docker` (`make test`), on the
#: reasoning that a subcommand is not a path. Measured, that arm re-admitted exactly
#: the one-word claim the gate exists to refuse: `cmd:make NOT-RUN`, `cmd:make
#: failed`, `cmd:make verified`, `cmd:npm not_run` and `cmd:docker DONE` were all
#: ACCEPTED, and a 22-row ledger using `cmd:make NOT-RUN` reported `uncovered: 0`,
#: `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0. It also bought nothing HERE: this repo
#: has no `Makefile` and no root `package.json` (see `AGENTS.md`), so `make test`
#: names no artifact and no target that exists. A runner with NO argument at all
#: (`cmd:make`) stays accepted as a record that confers nothing; a runner with a
#: non-path argument does not.
#: Shell WRAPPER verbs that may sit in their own `&&` segment in front of the real
#: command. They run nothing that is being cited; they prepare the shell. Measured
#: false blocks when they were graded as the command: `set -e && uv run pytest …`,
#: `export FOO=1 && …`, `source .venv/bin/activate && …` were all ACCEPTED on
#: `origin/main` and REFUSED once only `cd`/`NAME=value` were normalised.
_WRAPPER_VERBS: Final[frozenset[str]] = frozenset(
    {
        "set",
        "export",
        "source",
        ".",
        "umask",
        "unset",
        # ⛔ Shell CONTROL builtins, admitted for the same reason as the rest: they
        # run nothing that is being cited and they cite nothing themselves. A
        # `|| exit 1` TAIL is this repo's OWN spelling — `AGENTS.md`:
        # `uri="$(uv run python tools/test_lane.py uri)" || exit 1` — so refusing the
        # tail asserted the record "may carry commands and wrappers, not prose"
        # while refusing a COMMAND (third review cycle). Measured false block;
        # `origin/main` accepted all three shapes, and exit 1 hid the entire
        # uncovered list. ⛔ That AGENTS.md line is a PREAMBLE, not a record: entered
        # on its own it is correctly refused as "names no command", because it runs
        # no runner at all. What `exit` admission buys is the tail of a REAL record
        # (`uv run pytest … || exit 1`).
        # ⛔ Their ARGUMENTS are graded in `_wrapper_argument_defect` — admitting a
        # head without reading its arguments is the exact bypass that function
        # exists to close, and doing it here let `… && exit "I did not run this"`
        # reach `verified` / exit 0 (fifth review cycle).
        # `pushd`/`popd` are DELIBERATELY absent: they manipulate a directory
        # STACK whose contents this parser cannot model, so the working directory
        # after them is UNKNOWABLE and no later artifact can be proven to have
        # resolved from it. (Their operand being a directory is not the reason — the
        # `cd` arm grades a directory operand too, and carries the same disclosed
        # box-dependence as `source`.) A record using them is refused as prose — the
        # same known-open false block as `mkdir` (#11520), and pinned by
        # `test_a_pushd_or_popd_segment_is_a_known_false_block`.
        "exit",
        "return",
        "shift",
        "break",
        "continue",
    }
)

#: DECORATIVE segments: a tail like `… && echo PASS`, or a reader that shows the run
#: (`… | tail -20 log`, `… && grep -E "passed" out.txt`). They cite nothing; they
#: announce or display what the real command produced. Refusing them was a measured
#: false block — `… && tail -20 .pytest.log` and `… && cat .pytest.log` were
#: ACCEPTED on `origin/main` and REFUSED by the every-segment rule, and exit 1
#: computes NOTHING, so the whole uncovered list disappeared. Every name here is a
#: read or a print; none can be read as a claim that the work was done.
_DECORATIVE: Final[frozenset[str]] = frozenset(
    {
        ":",
        "awk",
        "cat",
        "cut",
        "echo",
        "grep",
        "head",
        "jq",
        "logger",
        "ls",
        "printf",
        "sed",
        "sort",
        "tail",
        "tee",
        "tr",
        "true",
        "uniq",
        "wait",
        "wc",
    }
)

_WILDCARDS: Final = "*?["


def _glob_names_a_directory(text: str, root: Path) -> bool:
    """A glob may only count if it names a literal DIRECTORY inside the tree.

    `*` and `*.yml` resolve against the repo root, so they name *everything* and
    therefore nothing: `cmd:sh *` emptied a 22-row uncovered list to a clean exit.
    Requiring a `/` plus a wildcard-free directory component was not enough — the
    same vacuity is spellable with `.`/`..`, which a raw text split treats as
    ordinary components: `./*`, `docs/../*`, `tests/..//*` and `*/../*` all passed
    while resolving to the ROOT (measured: each exited 0 on a one-row `verified`
    ledger). So the literal prefix is RESOLVED and must be a directory strictly
    below the root.

    `docs/./*` is deliberately NOT refused: it normalises to `docs/*`, so it names
    the `docs` directory and is a legitimate citation. Refusing it would be a
    false block on a shape someone types by accident.

    ⛔ A `..` AT OR AFTER THE FIRST WILDCARD is refused, because the resolvable
    prefix stops at that wildcard and the rest cannot be checked statically:
    `docs/*/../../*` resolves every match to the repo ROOT — the exact vacuity
    above, one wildcard deep (measured: exit 0 on a one-row `verified` ledger).
    `docs/../tests/*.py` stays legal because its `..` sits
    inside the prefix that IS resolved.
    """
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if len(parts) < 2:
        return False
    prefix: list[str] = []
    for part in parts[:-1]:
        if any(wildcard in part for wildcard in _WILDCARDS):
            break
        prefix.append(part)
    if not prefix:
        return False
    if any(part == ".." for part in parts[len(prefix) :]):
        return False
    base = (root / Path(*prefix)).resolve()
    return base.is_relative_to(root) and base != root and base.is_dir()


def _names_artifact(token: str, cwd: Path, root: Path) -> tuple[bool, str]:
    """Does this ONE token name a TRACKED file of the tree? A bare path, a pytest
    node-id, or a glob over a named directory.

    A pytest node-id names the file MORE precisely than a bare path, so
    `tests/x.py::TestY::test_z` counts as naming `tests/x.py`; refusing it would be
    a false block on the most precise citation shape there is.

    `cwd` is the directory the command RAN in (`cd tools && …`): a relative target
    is resolved there, then required to stay inside `root`. Resolving it at `root`
    instead refused exactly the paths that make a `cd` worth writing.

    ⛔ The TRACKED rule applies here as well as to a bare path entry — the two are
    the same claim ("an artifact a reader's clone holds"), and checking only
    `is_file()` on a `cmd:` target accepted a gitignored `WORKTREE-REPORT.md`
    through the command arm while refusing it through the path arm.
    """
    text = token.split("::", 1)[0]
    if not text:
        return False, "it names nothing"
    try:
        if any(wildcard in text for wildcard in _WILDCARDS):
            if Path(text).is_absolute():
                return False, (
                    f"the glob {text!r} is ABSOLUTE — a citation must name a pattern "
                    "inside this tree, and an absolute glob cannot be resolved against it"
                )
            if not _glob_names_a_directory(text, root):
                return False, (
                    f"the glob {text!r} names no literal directory inside the tree, so "
                    "it names everything and therefore no artifact (a bare `*`/`*.yml`/"
                    "`./*` is not a citation)"
                )
            # ⛔ A glob is satisfied by ANY tracked match, not by the FIRST one.
            # Returning False on the first untracked match made the verdict depend on
            # ENUMERATION ORDER: a tree holding one tracked and one untracked file
            # under the same pattern was accepted or refused depending on which name
            # `Path.glob` yielded first (measured with `docs/aaa_scratch.md` +
            # `docs/zzz_tracked.md` → refused, reversed names → accepted). The rule is
            # "this pattern names a held artifact", and one held match answers it.
            # A pattern with NO tracked match is still refused.
            untracked_seen = False
            unrunnable_seen = False
            unmeasured_seen: list[object] = []
            for match in cwd.glob(text):
                resolved_match = match.resolve()
                if not (resolved_match.is_file() and resolved_match.is_relative_to(root)):
                    continue
                # ⛔ THREE answers, not two. `None` is "nothing was measured about this
                # path" — and that has TWO causes: the probe could not be SPAWNED, or it
                # ran and failed to ANSWER (see `_is_tracked`). Folding it in with `False`
                # here told a reader that every file a glob
                # matches "is untracked by git" when git had never been asked — the same
                # guess-the-cause defect this change removes from the other two call
                # sites. A measured "no match is tracked" is still reported as that.
                verdict = _is_tracked(resolved_match, root)
                if verdict is True:
                    return True, ""
                if verdict is None:
                    unrunnable_seen = True
                elif verdict is False:
                    untracked_seen = True
                else:
                    # ⛔ THE FOURTH ANSWER (#11474 review): `is True … is None … else`
                    # filed every OTHER truthy return (`1`, `"yes"`) under
                    # `untracked_seen`, i.e. it reported a value git never measured as
                    # the MEASURED sentence "every file the glob … is untracked by
                    # git". Fail-closed either way, but the same guess-the-cause defect
                    # the two branches above exist to remove, one arm over — and this
                    # arm is the third of the three `_is_tracked` call sites, so a guard
                    # at two of three is a guard with a hole the next caller change
                    # walks through.
                    unmeasured_seen.append(verdict)
            if unmeasured_seen:
                # A value that is not a measurement cannot be folded into a sentence
                # about what WAS measured, so it is named on its own and takes
                # precedence: the reader gets the answer git actually gave.
                return False, (
                    f"cannot be verified: the TRACKED check answered "
                    f"{unmeasured_seen[0]!r} for a file the glob {text!r} matches, "
                    "which is not a measurement that it is in the tree"
                )
            # ⛔ AND THE MIXED CASE NEEDS BOTH CAUSES (#11474 review). `untracked_seen`
            # winning outright re-emitted the universal sentence "EVERY file the glob
            # matches is untracked by git" INCLUDING the matches git was never asked
            # about — the same guess-the-cause defect the comment above says this
            # branch removes. A measured cause is not a licence to speak for an
            # unmeasured one: name each.
            if untracked_seen and unrunnable_seen:
                return False, (
                    f"cannot be verified: some files the glob {text!r} matches are "
                    "untracked by git, and for the rest the TRACKED check did not "
                    "answer here — so a reader's clone is not shown to have them"
                )
            if untracked_seen:
                return False, (
                    f"every file the glob {text!r} matches is untracked by git, so a "
                    "reader's clone does not have them"
                )
            if unrunnable_seen:
                # ⛔ "did not answer", NOT "could not be RUN": `None` now carries two
                # causes — a probe that could not be SPAWNED and a probe that ran and
                # FAILED (exit 128 on an unreadable index). The per-path UNMEASURED line
                # names which; this row-level sentence must not pick one, or it
                # borrows a cause it did not measure (the defect this PR removes).
                return False, (
                    f"cannot be verified: the TRACKED check did not answer here, so no "
                    f"file the glob {text!r} matches is shown to be in the tree"
                )
            return False, f"no file of this tree matches the glob {text!r}"
        candidate = Path(text)
        resolved = (candidate if candidate.is_absolute() else cwd / text).resolve()
        if resolved.is_file() and resolved.is_relative_to(root):
            tracked = _is_tracked(resolved, root)
            if tracked is None:
                # "did not answer", not "could not be RUN" — `None` carries both a
                # probe that could not be spawned and one that ran and failed; the
                # per-path UNMEASURED line names which. See the glob arm above.
                return False, (
                    f"{text!r} cannot be verified: the TRACKED check did not answer here"
                )
            if tracked is False:
                return False, (
                    f"{text!r} is not TRACKED by git, so a reader's clone does not have it"
                )
            if tracked is not True:
                # ⛔ `False` and `None` are the two answers `_is_tracked` DECLARES
                # below `True` (`True` is handled above); anything else is not one of
                # them. They do NOT share a cause — `False` is a MEASUREMENT by git,
                # `None` is nothing measured at all — which is why each has its own
                # renderer and this guard only decides "in neither shape".
                # `_is_tracked` is annotated `bool | None`, so this is
                # unreachable today — and it must stay unreachable rather than be
                # READ as tracked: an `is False … is None … return True` shape accepts
                # every future truthy return (`1`, `"yes"`) as the measurement "this
                # artifact is in the tree", which is a fail-open waiting for a caller
                # change (#11474 review).
                return False, (
                    f"{text!r} cannot be verified: the TRACKED check answered "
                    f"{tracked!r}, which is not a measurement that it is in the tree"
                )
            return True, ""
        if resolved.is_dir() and resolved.is_relative_to(root) and resolved != root:
            # ⛔ A DIRECTORY IS A LEGITIMATE TARGET, and refusing it was a measured
            # false block on the command this repo's own `AGENTS.md` hands a lane:
            # `uv run pytest tests/ -v` was INVALID, so a lane recording what it
            # actually ran lost the entire uncovered list (exit 1 computes nothing).
            # It counts only if a reader's clone holds something under it, and the
            # ROOT is refused above — `.` names EVERYTHING, the same vacuity as a
            # bare `*`.
            if resolved == cwd:
                # ⛔ THE SAME VACUITY ONE LEVEL DOWN. `cmd:sh .` was already refused
                # (it resolves to the root), but `cmd:cd docs && uv run pytest .`
                # was ACCEPTED while `.` there names every file under `docs` — the
                # claim "an artifact of the tree" with nothing in particular cited.
                # A target that resolves to the directory the command RAN in is the
                # working directory, not an artifact.
                return False, (
                    f"{text!r} resolves to the directory the command ran in, so it "
                    "names everything under it and therefore no artifact (the same "
                    "vacuity as a bare `*`)"
                )
            dir_tracked = _is_tracked_dir(resolved, root)
            if dir_tracked is None:
                # ⛔ THE SAME THREE ANSWERS AS THE FILE ARM (#11474 review). `None` is
                # NOTHING MEASURED, so the sentence below may not be the measured-`no`
                # one: before this guard the caller rendered every falsy answer as
                # "holds no TRACKED file", which is a cause git never gave when the
                # directory fork failed to spawn or the index was unreadable.
                return False, (
                    f"{text!r} cannot be verified: the TRACKED check did not answer here"
                )
            if dir_tracked is False:
                return False, (
                    f"{text!r} is a directory holding no TRACKED file, so it holds "
                    "nothing a reader's clone has"
                )
            if dir_tracked is not True:
                # Unreachable while the annotation holds, and kept for the same reason
                # as the file arm's fourth-answer guard: a future truthy return must not
                # be read as "this directory holds a TRACKED file".
                return False, (
                    f"{text!r} cannot be verified: the TRACKED check answered "
                    f"{dir_tracked!r}, which is not a measurement that it is in the tree"
                )
            return True, ""
        if resolved.is_dir() and resolved == root:
            # Name the VACUITY, not "not a file": the reviewer's point is that a
            # runner told only "not a file in this tree" cannot tell why `.` is
            # refused while `cmd:sh tests` is accepted.
            return False, (
                f"{text!r} resolves to the repository root, so it names everything "
                "and therefore no artifact (a bare `*`/`.`/`/` is not a citation)"
            )
        return False, f"{text!r} is not a file in this tree"
    except (OSError, ValueError, NotImplementedError) as exc:
        # An entry that cannot even be RESOLVED is a defect in the RECORD, not a
        # crash in the tool: `validate`'s contract is "the rows, or InvalidLedger
        # naming every defect". Measured escapes before this guard: an absolute
        # glob raised NotImplementedError out of `Path.glob`, and a NUL byte raised
        # ValueError out of `resolve` — both printed a traceback instead of the
        # `CAPSTONE-LEDGER: INVALID` marker a log reader or caller keys on.
        return False, f"{text!r} cannot be resolved against this tree ({type(exc).__name__}: {exc})"


def _is_tracked_dir(resolved: Path, root: Path) -> bool | None:
    """Does this in-tree DIRECTORY hold at least one TRACKED file?

    A directory is a legitimate `cmd:` target — `uv run pytest tests/ -v` is the
    command this repo's own `AGENTS.md` hands a lane — but it counts only when a
    reader's clone actually holds something under it: an empty or untracked
    directory names nothing. (The ROOT is refused a level up, because `.` names
    EVERYTHING — the same vacuity as a bare `*`.)

    ⛔ THE SAME THREE ANSWERS AS `_is_tracked` (#11503 Option 1), FOR THE SAME REASON
    (#11474 review). `#11506` added this probe beside `_is_tracked` and carried BOTH
    causes that change removes there; once the two arms are in one file the divergence
    is a fail-open this tool measures against itself:

        True   a reader's clone holds a TRACKED file under it (MEASURED);
        False  git ran and ANSWERED that it holds none (MEASURED);
        None   NOTHING was measured — so the row may not be counted.

    ⛔ `OSError` IS NOT AN ABSTENTION HERE. It was (`except OSError: return
    _tracked_unprovable()`), and `_tracked_unprovable()` is `True`, so a fork that never
    ran — `git` absent, or EMFILE/EAGAIN under load — ACCEPTED the directory and let a
    `verified` row reach exit **0 CLEAN**. That is verbatim the shape this PR closes one
    function over, reached through the guard meant to make it safe: under `EMFILE` the
    `cat-file -e` and file-shaped `ls-files` forks can succeed while this one does not,
    so `_commit_state` answers `exists`, the row counts, and nothing was measured. A
    spawn failure is a REFUSAL (`_tracked_dir_unrunnable`), never an abstention.

    ⛔ `returncode == 0` WITH OUTPUT IS THE MEASUREMENT. This form's ONE "no" answer is
    **rc 0 with empty stdout** (measured on git 2.43.0 against an empty directory and an
    untracked one), and it is a measurement **only
    once git is shown to be answering for a WORK TREE**, and the gate on that is the
    same `rev-parse --is-inside-work-tree` probe the file arm uses. ⛔ THAT GATE WAS
    MISSING (#11474 round-3 review, the parity this docstring claimed but the code did
    not deliver): it answered from the exit code alone, so in the tree this file's
    own ABSTAINING pin describes the two probes judged ONE tree TWO WAYS. MEASURED on
    git 2.43.0 over a bare repo reached by `GIT_DIR` (the pin's own fixture):

        git ls-files -- ':(literal)held'          -> rc 0, EMPTY stdout
        git rev-parse --is-inside-work-tree       -> `false`, rc 0
        git ls-files --error-unmatch -- ':(literal)held/inside.txt' -> rc 1

    so the FILE arm abstains there (`_tracked_unprovable(probe)` -> `True`, the
    #11503 Option 1 answer, pinned end to end) while this arm read that rc 0 as a
    MEASURED "holds no TRACKED file" and REFUSED the row — with a cause git never gave:
    git could not answer about a work tree at all, and a reader's clone does hold those
    files. Fail-closed, so not a fail-open, but it is exactly the fork the `pytest tests/`
    paragraph below forbids, produced by this probe. The no-answer now routes through
    the probe: `true` -> measured `False` (today's behaviour, preserved), anything else
    -> `_tracked_unprovable(probe)`, so the verdict follows the recorded decision
    instead of this arm inventing its own.

    ⛔ **rc 1 IS NOT A MEASURED "no" FOR THIS FORM, AND AN EARLIER REVISION OF THIS
    FUNCTION SAID IT WAS (#11474 review).** That revision read `proc.returncode in (0,
    1)` and justified rc 1 as *"`git ls-files`' documented 'nothing matched'"*. The
    manual does not say that: `man git-ls-files` on git 2.43.0 carries exactly ONE
    `return 1`, and it is **`--error-unmatch`'s** ("If any `<file>` does not appear in
    the index, treat this as an error (return 1)") — the form the FILE arm passes, not
    this one. MEASURED against a temp repo: this form (`git ls-files -- ':(literal)nope'`,
    no `--error-unmatch`) exits **0** with empty stdout; the same pathspec under
    `--error-unmatch` exits 1. So rc 1 carries no documented meaning here, and blaming
    it as "holds no TRACKED file" prints a cause git never gave — the exact defect this
    PR removes, one exit code over. `returncode == 0` is now the ONLY measured miss;
    every other non-zero (1, 128, 129 — an unreadable index, a bad repo, a usage error)
    is routed to `_tracked_dir_unanswered`, which prints the measured exit code and
    refuses the row. ⛔ NOT to the measured sentence: with a corrupt `GIT_INDEX_FILE`,
    `ls-files -- tests/` exits 128 while `rev-parse --is-inside-work-tree` still answers
    `true` rc 0, so reading a non-zero as "holds no TRACKED file" tells the reader a
    cause git never gave. The verdict is fail-closed either way — only the sentence it
    prints changes, and it now names the measurement instead of an inference.

    ⛔ `-- <rel>` IS A PATHSPEC HERE TOO, AND THAT DIRECTION WAS LIVE (#11474 review,
    the same defect the file probe's `:(literal)` closes). MEASURED on git 2.43.0: with
    `docs/ev-axb/f.md` TRACKED and a real, untracked directory literally named
    `docs/ev-*`, `git ls-files -- 'docs/ev-*'` prints `docs/ev-axb/f.md` and exits 0 —
    so this probe answered "a reader's clone holds a TRACKED file under it" for a
    directory no clone holds anything under, borrowing a SIBLING directory's match.
    `:(literal)` makes the name a name: the same call answers nothing with rc 0.
    (`*` matching across `/` is git's pathspec rule, not a shell rule — see
    gitglossary "pathspec"; and `:(literal)` is documented there as the way to make
    `*`/`?`/`[` literal.) A pre-2.11 git degrades CLOSED here: `:(literal)` is a usage
    error (129), which routes to `_tracked_dir_unanswered` and refuses the row.
    """
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        # Not a git answer: git is never asked about a path outside `root`. Every call
        # site pre-checks `is_relative_to(root)`, so this is a structural refusal kept
        # as a guard — the same arm `_is_tracked` keeps.
        return False
    try:
        proc = subprocess.run(
            # `:(literal)` is load-bearing, not decoration: without it `rel` is a
            # PATHSPEC and a directory name carrying `*`/`?`/`[` borrows a SIBLING's
            # tracked file — measured, `docs/ev-*` (untracked) answered rc 0 because
            # `docs/ev-axb/f.md` is tracked. See the docstring.
            ["git", "ls-files", "--", f":(literal){rel}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _tracked_dir_unrunnable(exc, rel)
    if proc.returncode == 0 and proc.stdout.strip():
        # Git ran and MATCHED: a reader's clone holds a TRACKED file under `rel`. A
        # `yes` needs no probe — the file arm does not ask for one either.
        return True
    # ⛔ NEITHER "NO" ANSWER IS A MEASUREMENT UNTIL GIT IS SHOWN TO BE ANSWERING FOR A
    # WORK TREE. `_is_tracked` distinguishes "git answered and refused" from "git
    # cannot answer here", so this must too — otherwise a git-less build context
    # refuses `pytest tests/` while accepting a single cited file, which is the
    # same row judged two ways by the tool's own doctrine. The pair is REACHABLE, not
    # theoretical: over a bare repo via `GIT_DIR` this form exits 0 with EMPTY stdout
    # while the probe answers `false` (measured, git 2.43.0 — see the docstring), which
    # is exactly the abstaining tree `_is_tracked` accepts.
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _tracked_dir_unrunnable(exc, rel)
    if probe.returncode == 0 and probe.stdout.strip() == "true":
        if proc.returncode == 0:
            # Git IS here, it ran, and it ANSWERED "nothing matched": for this form
            # (no `--error-unmatch`) the miss is rc 0 with empty stdout, and it is the
            # ONLY exit the manual documents as an answer here. Blame it.
            return False
        # Git IS here but exited non-zero for this directory. Nothing was measured, and
        # the row may not borrow the measured-"holds no TRACKED file" sentence: rc 1
        # carries no documented meaning for this form (measured 2026-11-08, git
        # 2.43.0: a miss is rc 0), and 128/129 are failures to answer at all.
        return _tracked_dir_unanswered(proc.returncode, rel)
    # Hand the probe to the renderer so the NOTE names the cause THAT WAS MEASURED.
    return _tracked_unprovable(probe)


def _tracked_dir_unrunnable(exc: OSError, rel: Path) -> None:
    """The DIRECTORY probe could not RUN for `rel`. Nothing was measured, so refuse.

    ⛔ NOT `_tracked_unprovable()`, which answers `True` — that fold is the fail-open
    this function exists to remove (see `_is_tracked_dir`), and NOT the file-shaped
    `_tracked_unrunnable` sentence either: that one says the path "is not shown to be
    in the tree", which is a claim about a FILE. What is unmeasured here is what the
    directory HOLDS, so that is what the line says.

    Its marker is `UNMEASURED` like the file arm's, even though a directory refusal
    currently always refuses the row: that is the CALLER's invariant, not this
    renderer's, and a marker encoding it would silently become a lie the day a
    directory-shaped glob arm lands (#11474 review). One rule, four renderers.
    """
    print(
        "CAPSTONE-LEDGER: UNMEASURED — the TRACKED check could not be RUN here "
        f"({exc.__class__.__name__}: {exc}), so nothing was measured about what {rel} "
        "holds: no TRACKED file under it is shown to be in the tree",
        file=sys.stderr,
    )
    return None


def _tracked_dir_unanswered(returncode: int, rel: Path) -> None:
    """Git ran and did NOT answer for the DIRECTORY `rel`. Nothing was measured.

    The third refusal, kept distinct for the reason `_tracked_dir_unrunnable` is: the
    cause is git present, having answered for the TREE, failing to answer for this
    path — and rendering it as "holds no TRACKED file" is the guess-the-cause defect.
    ⛔ THIS FUNCTION IS NOW REACHED BY rc 1 (#11474 review). This form's (`ls-files --
    ':(literal)<rel>'`, no `--error-unmatch`) ONLY measured miss is **rc 0 with empty
    stdout**; every non-zero, 1 included, is a failure to answer, because
    `man git-ls-files` documents a single `return 1` and it belongs to
    `--error-unmatch` — the FILE arm's form, not this one. The marker is
    `UNMEASURED` for the reason stated on `_tracked_dir_unrunnable`.
    """
    print(
        "CAPSTONE-LEDGER: UNMEASURED — the TRACKED check did not get an ANSWER here "
        f"(`git ls-files` exited {returncode}), so nothing was measured about what "
        f"{rel} holds: no TRACKED file under it is shown to be in the tree",
        file=sys.stderr,
    )
    return None


def _commit_state(sha: str, repo_root: Path = REPO_ROOT) -> str:
    """`exists` | `absent` | `unprovable` | `unmeasurable` for a measured-at sha.

    The 40-hex shape check alone lets `measured_at.sha: deadbeef…` through, and
    `render()` then prints "measured at origin/main@deadbeef… on <date>" as fact.

    ⛔ The third outcome is the point. In a SHALLOW clone — and CI test jobs check
    out at `fetch-depth: 1`, so that is every shard — an ancestor sha is simply not
    in the object database, and a check that only knows `exists`/`absent` reports a
    LEGITIMATE ledger as forged and reds the required check (measured: a depth-1
    clone makes `git cat-file -e <ancestor>^{commit}` exit 128). The convention is
    the sibling capstone's (`tools/capstone_preregistration_check.py`: "a verdict it
    cannot support is never given", #9075 r4 P1): a shallow checkout CANNOT answer,
    so it answers `unprovable` and the record is not blamed.

    ⛔ `unmeasurable` is NOT `unprovable`, and folding the two together was a
    fail-open. `unprovable` is a MEASURED answer about this checkout (it is
    shallow, so the object genuinely is not here). "git could not be asked at
    all" is a different fact: with no `git` on PATH, a broken `GIT_DIR`, or a
    `.git`-less source tree, this function returned `unprovable` and a
    FABRICATED 40-hex sha rendered as provenance with no diagnostic — the exact
    claim this function exists to stop. It answers `unmeasurable` now, and
    `validate` refuses on it, which is what the pre-shallow revision did.

    `repo_root` is honoured for the same reason `_evidence_is_real` honours it: a
    validator asked to judge a DIFFERENT tree must not answer against this one.

    ⛔ `GIT_NO_LAZY_FETCH=1` on the object probe (#11217) — and what it does NOT buy.
    A PARTIAL clone (`--filter=blob:none`) reports `--is-shallow-repository` as
    FALSE, so the shallow probe below does not cover it, and an absent sha makes
    `cat-file -e` reach for the origin: measured 2m29s here before exiting 128.

    **It removes LATENCY, not a verdict, and that distinction was wrong in an
    earlier draft of this comment (found in review).** The measured fetch exited
    128, so the state was `absent` WITH the refetch and `absent` WITHOUT it — the
    env cannot clear a false RED here, because there was none to clear. The 2m29s
    is the whole observable difference on this shape.

    ⛔ The flip it DOES introduce is the opposite one, stated rather than denied:
    suppressing the fetch also stops git materialising an object the promisor
    remote holds but this clone's refspec does not, and this function then answers
    `absent` — so `validate` refuses a sha that does exist upstream. It is NARROW:
    a `blob:none` clone carries every commit, and the ledger cites a mainline sha
    whose ref the clone already has. But it is a real cost of the env, and a
    comment that claims the fetch "buys nothing" would hide it from the next
    reader.
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        if proc.returncode == 0:
            return "exists"
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unmeasurable"
    if shallow.returncode != 0:
        return "unmeasurable"
    return "unprovable" if shallow.stdout.strip() == "true" else "absent"


def _evidence_is_real(
    entry: str,
    repo_root: Path = REPO_ROOT,
    depth: int = 0,
    run_cwd: Path | None = None,
) -> tuple[bool, str]:
    """A path must be a TRACKED FILE inside the tree; a `cmd:` entry must be a COMMAND.

    ⛔ This is the anti-forgery rule and the reason the tool is worth having: a
    row that *describes* its evidence in prose used to count. Now the file has
    to be in the tree, or the row does not count at all — and "in the tree" is
    enforced literally: an absolute path outside the checkout, a directory, and
    `.` are all REFUSED, because `Path.exists()` accepted every one of them and
    a reviewer measured each returning a clean exit on a `verified` row.

    The `cmd:` arm gets the same treatment, for the same reason: accepting any
    non-empty string meant `cmd:done` — or a sentence explaining that the work
    was done — satisfied the strongest verdict in the vocabulary, so one word
    defeated the whole record. A command entry must now be COMMAND-SHAPED — its
    first token is a program that runs something, and its FIRST ARTIFACT position
    names a tracked artifact (a file, a directory holding tracked files, a pytest
    node-id, or a glob over a named directory) — "first artifact position" rather
    than "first argument position" because ONE subcommand verb immediately after a
    NAMED runner may lead the list (`ruff check <path>` / `ruff format <path>`,
    scoped per runner in `_RUNNER_SUBCOMMANDS`). THREE shapes are deliberately
    admitted and confer nothing on their own: a runner with NO argument
    (`cmd:make`), a runner followed only by its own subcommand verb
    (`cmd:ruff check`), and a `-c` script body, which is graded as a command in its
    own right rather than trusted. Every segment of a compound record must be a
    shell
    wrapper, a decorative reader/printer, or runner-headed; a segment that is none
    of those is prose and is REFUSED, so a BARE-prose segment cannot ride on
    either side of the command. ⛔ NOT closed, and deliberately not claimed: a
    disclaimer carried as a TRUSTED HEAD's ARGUMENT is accepted too — a
    decorative/wrapper segment is admitted without grading one argument, so
    `… && echo "I did not run this"` still exits 0 (#11520 root 4; the wrapper
    half is closed, so `export "I did not run this" && …` is refused). And a LATER
    runner-HEADED segment is admitted without grading its argument position, so
    `… && bash I did not run this` is still accepted (#11520). Grading every
    runner segment the way the first is graded re-introduced a false block on
    `… && git status --short` / `… && git log --oneline`, whose subcommand is not a
    path — and a false block is the harm this ruleset exists to remove.

    ⛔ These two arms are one rule seen from two sides, and NEITHER alone is
    enough — both were measured as fail-open on their own:

    * a `cmd:`-only rule (accept any non-empty text, require a tracked path
      elsewhere) still validates a 22-row ledger whose every row says
      `cmd:NOT RUN - see tools/capstone_ledger.py` plus the always-available
      `docs/evidence/9664-capstone/README.md`, reporting `uncovered: 0`, exit 0;
    * a `cmd:`-shape rule that only checks the artifact EXISTS (not that a reader
      has it) accepts a gitignored `WORKTREE-REPORT.md` — present on the authoring
      box and in no clone.

    So the shape is what makes a `cmd:` entry a record rather than a claim, the
    TRACKED path is what makes the row's evidence reachable by a reader, and a
    `verified` row needs both.

    ⚠️ RESIDUAL, stated rather than hidden: this arm proves the entry is
    COMMAND-SHAPED and cites a real artifact there. It does **not** prove the
    command RAN it — `cmd:uv run python --help <path>` names the artifact and is
    accepted, because the tool must never execute anything. The rule is "names an
    artifact", not "ran it"; do not read a green ledger row as the stronger claim.

    ⛔ WHAT THIS DOES NOT PROVE: a held artifact proves the file is IN the tree,
    not that it supports the row citing it (22 rows citing one trace would
    validate). `verified` therefore means "an artifact in this tree is named as
    its evidence", not "the evidence was audited". The runner records a verdict
    and stands behind it; the tool refuses a record that is malformed, ambiguous,
    or cites what it does not hold.
    """
    root = repo_root.resolve()
    # The directory the command is recorded as having RUN in: the repo root, or the
    # `cd` target of the script that invoked it. Never outside the tree.
    base = run_cwd if run_cwd is not None and run_cwd.is_relative_to(root) else root
    if entry.startswith(_CMD_PREFIX):
        raw = entry[len(_CMD_PREFIX) :].strip()
        if not raw:
            return False, "`cmd:` evidence names no command"
        command, cmd_cwd, shape_problem = _command_body(raw, base, root)
        if shape_problem:
            return False, f"`cmd:` evidence is not a command line: {shape_problem}"
        if not command:
            return False, f"`cmd:` evidence names no command: {raw!r}"
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False, f"`cmd:` evidence is not a parseable command: {command!r}"
        if not tokens:
            return False, f"`cmd:` evidence names no command: {raw!r}"
        runner = Path(tokens[0]).name
        if runner not in RUNNERS:
            # ⛔ This is the arm that kills prose, and it is the whole reason this
            # PR exists. A token scan alone does not: both `cmd:verified manually
            # by reading pyproject.toml` and `cmd:NOT RUN - see
            # tools/capstone_ledger.py` name a file that exists, and both satisfied
            # `verified` — one of them saying the work was NOT done. The command
            # SHAPE is the discriminator, and it is checked on the NORMALISED body
            # so that the shell wrapper around a real command is not a false block.
            return False, (
                "`cmd:` evidence must START with a program that runs something "
                f"({sorted(RUNNERS)}), got {tokens[0]!r} in {command!r}"
            )
        consumed_script = False
        #: The runner actually being invoked: the LAST RUNNER token in the chain
        #: (`uv run ruff check <path>` → `ruff`), because a subcommand verb belongs
        #: to that runner and to nothing else (see `_RUNNER_SUBCOMMANDS`).
        current_runner = Path(tokens[0]).name
        subcommand_allowed = True
        #: A token that is unreadable PROSE, remembered rather than refused ON THE
        #: SPOT. A quoted flag+value (`pytest "-k not slow" <tracked path>`) is a
        #: legitimate record whose FIRST token is prose-shaped, so refusing at the
        #: token would be a false block on a command a lane really runs — and exit 1
        #: prints no report. The refusal is therefore deferred to the END of the
        #: scan, where the question "did this record name an artifact at all?" is
        #: answerable: `pytest "-q I did not run this"` names nothing and stays
        #: REFUSED, which is the fail-open this guard exists for.
        unread_prose: str | None = None
        for token in tokens[1:]:
            if consumed_script:
                # ⛔ The value of a `-c`/`-e` flag is a SCRIPT BODY. It is not a
                # citation, so it cannot be graded as a path — but LEAVING IT
                # UNGRADED was a one-word bypass of the whole gate: measured,
                # `cmd:bash -c 'NOT RUN - see tools/capstone_ledger.py'` and
                # `cmd:uv run python -c 'nothing was executed'` both returned True,
                # i.e. the exact prose this arm refuses naked was accepted in a
                # wrapper. The script is therefore graded as a command in its own
                # right: `bash -c 'uv run pytest tests/x.py'` names an artifact,
                # `bash -c 'NOT RUN - see …'` is a claim. Depth is bounded because a
                # script may contain another `-c`.
                consumed_script = False
                if depth >= _MAX_SCRIPT_DEPTH:
                    return False, ("`cmd:` evidence nests `-c` scripts too deeply to grade")
                ok, why = _evidence_is_real(f"{_CMD_PREFIX}{token}", repo_root, depth + 1, cmd_cwd)
                if ok:
                    return True, ""
                return False, f"`cmd:` evidence script {token!r} is not a command: {why}"
            if token.startswith("-"):
                # ⛔ THE ATTACHED-SCRIPT ARM RUNS **FIRST**, BEFORE the whitespace
                # deferral (review cycle 5, P1). `-c'NOT RUN - see <path>'` is ONE
                # shlex token that CONTAINS whitespace, so the prose test below
                # matched it and `continue`d — the arm was never reached for exactly
                # the bodies that carry a sentence. Measured at the previous head,
                # `cmd:bash -c'NOT RUN - see tools/capstone_ledger.py' tests/…py`
                # reported `verified: 1 uncovered: 0` / CLEAN / exit 0, while the
                # SAME value WITHOUT the trailing path was refused: the deferred
                # prose was then CLEARED by the artifact token.
                #
                # ⛔ AND THE ARM REFUSES — it does NOT grade — because for a SHELL an
                # ATTACHED body is a command that CANNOT EXECUTE (review cycle 6, P2;
                # primary source, measured on this box):
                #
                #   bash -c'echo hi'   -> rc 1, stderr `bash: - : invalid option`,
                #                         body never runs; STDOUT carries the 26-line
                #                         `set -o` table, because `o` parses as `-o`
                #   bash -c 'echo hi'  -> prints `hi`, rc 0
                #   sh   -c'echo hi'   -> `Illegal option -h`, rc 2
                #
                # With no space, `-cecho hi` is ONE argv word and the shell reads what
                # follows `-c` as MORE OPTION LETTERS. ⛔ NOT UNIVERSAL, and an earlier
                # revision of this comment over-claimed it: for CPython and node the
                # attached value IS the documented, runnable form (`python3 -c'print(3)'`
                # -> `3`, rc 0; `node --eval='console.log(1)'` -> `1`). Those are still
                # refused — the body is a python/JS PROGRAM and this arm grades a body
                # as a SHELL command line, which it can never be — and that refusal is a
                # declared known-open FALSE BLOCK (#11520), not a claim that the
                # spelling is unrunnable. So for a shell, grading the body recorded a
                # `verified` row for a command that cannot have executed (the same
                # false-PASS class as the disclaimer it refuses); for python/node the
                # refusal costs a record but asserts nothing false.
                # Refusing is also what makes the verdict independent of the cluster
                # split: `-cmake x`, `-cec x`, `-cverified` and `--eval=…` are ALL
                # refused, so a mis-split can neither admit a body nor drop a prefix.
                # The SPACED form (`bash -c '<script>'`, body in the NEXT token) is the
                # spelling a shell actually runs and is graded by the `consumed_script`
                # arm below.
                flag, attached = _split_flag(token)
                if flag in _SCRIPT_FLAGS and attached:
                    # ⛔ QUOTE THE TOKEN, NOT `_split_flag`'s REMAINDER (review cycle 7,
                    # P2). The cluster scan consumes the leading flag letters, so for
                    # `-cec uv run pytest <path>` the remainder is `' uv run pytest
                    # <path>'` — the `ec ` the arm exists to reject was DROPPED from the
                    # error text itself, so the message named a different body than the
                    # record contained. The token is what the author wrote.
                    return False, (
                        f"`cmd:` evidence puts a script body in the SAME token as its "
                        f"flag ({_short(token)}). For a SHELL (`bash`/`sh`) the body "
                        "after `-c` is read as more OPTION LETTERS, so that spelling "
                        "cannot execute (measured: `bash -c'echo hi'` ends rc 1 with "
                        "stderr `bash: - : invalid option` and the body never runs — "
                        "stdout carries the `o`-table, because `o` parses as `-o`). Put "
                        "the script in its OWN token (`bash -c '<script>'`), which is "
                        "the spelling a shell runs. For `python`/`node` an attached "
                        "`-cBODY` DOES run, but the body is a python/JS PROGRAM, not a "
                        "shell command line, and this arm grades a body as a shell "
                        "command line — so it is refused here too: a declared "
                        "known-open FALSE BLOCK (#11520)"
                    )
                # ⛔ A dashed token WITHOUT a script flag is a FLAG only when it is
                # ONE WORD. A token that CONTAINS whitespace was quoted by the
                # author — `shlex` preserves whitespace only INSIDE a quote — so it
                # is an argument wearing a dash, not a flag, and skipping it as one
                # reached the runner-with-no-target arm. Measured, `cmd:uv run pytest
                # "-q I did not run this"` reported `verified: 1 uncovered: 0` and
                # exited 0, i.e. a record that states on its own line that the work
                # was NOT run satisfied the strongest verdict in the vocabulary. The
                # same shape with a real artifact after it (`pytest "-k not slow"
                # <path>`) is a legitimate record, so the refusal is DEFERRED (see
                # `unread_prose`) instead of taken here.
                #
                # ⛔ KNOWN-OPEN (#11520 root 5), pinned: a quoted disclaimer ALSO
                # followed by a real artifact stays ACCEPTED (`pytest "-q I did not
                # run this" <tracked path>`), because the deferral below cannot tell
                # a quoted FLAG+VALUE from a quoted SENTENCE — `"-k not slow"` and
                # `"-q I did not run this"` are syntactically identical, and
                # discriminating them is a semantic judgement, not a grammar (WHICH
                # flag takes a value). The arm keeps the weaker side on purpose: the
                # module's own rule, recorded twice, is that "a refusal that loses the
                # finding is worse than the weaker arm", and exit 1 prints no report.
                # WITHOUT an artifact the same spelling is still REFUSED (the
                # `unread_prose` arm at the end of this scan), so the naked case stays
                # closed. Pinned by
                # `test_the_known_residual_false_PASSes_are_pinned_as_OPEN`.
                if any(char.isspace() for char in token):
                    if unread_prose is None:
                        unread_prose = token
                    continue
                consumed_script = flag in _SCRIPT_FLAGS
                continue
            if token in _CHAIN_WORDS or Path(token).name in RUNNERS:
                # Still inside the runner chain: `uv` `run` `python` `-m` `pytest`.
                if Path(token).name in RUNNERS:
                    current_runner = Path(token).name
                subcommand_allowed = True
                continue
            if subcommand_allowed and token in _RUNNER_SUBCOMMANDS.get(current_runner, frozenset()):
                # ⛔ PER RUNNER, never a global word set — see `_RUNNER_SUBCOMMANDS`.
                # ONE subcommand verb, immediately after its runner: a second bare
                # word is back in artifact position and must name something.
                subcommand_allowed = False
                continue
            if _NUMBER_ARG.match(token) or _ENV_ASSIGNMENT.match(token):
                # `timeout 300 …`, `env FOO=1 …` — a runner's own literal argument.
                continue
            # ⛔ THE DECISION IS MADE HERE, at the FIRST artifact position, and is
            # never deferred to a later token. Scanning the whole sentence for
            # "any token that happens to resolve" is what let
            # `cmd:sh NOT RUN - see tools/capstone_ledger.py` satisfy `verified`
            # and empty the uncovered list: one runner word prepended to a sentence
            # that merely NAMES a file. A command's target is its argument, so the
            # first thing in argument position either IS the artifact or the entry
            # is a claim.
            ok, why = _names_artifact(token, cmd_cwd, root)
            if ok:
                return True, ""
            return False, (
                f"`cmd:` evidence names no artifact of this tree: {command!r} — the first "
                f"thing in argument position after the runner must NAME the artifact "
                f"(a tracked file, a directory holding tracked files, a pytest node-id, "
                f"or a glob over a named directory), and {why}. A command entry must "
                "NAME an artifact; prose names nothing."
            )
        # A runner with NO target — `make`, `uv run pytest`, `npm test`. It is a
        # legitimate RECORD and it confers nothing: `validate` already requires a
        # verified row to hold at least one TRACKED path, so the entry cannot carry
        # a verdict on its own. Returning False here was a measured false block
        # (`cmd:make` beside a held artifact → exit 1) and exit 1 computes NOTHING,
        # so it also hid the entire uncovered list — a refusal that loses the
        # finding is worse than the weaker arm.
        #
        # ⛔ UNLESS the record was carrying quoted PROSE and named nothing: then the
        # "no target" is not a runner's own bare invocation, it is a sentence that
        # was never read — `pytest "-q I did not run this"`. That is the fail-open,
        # and it is refused here rather than at the token so the legitimate quoted
        # flag+value above is not caught with it.
        if unread_prose is not None:
            return False, (
                f"`cmd:` evidence carries the quoted value {_short(unread_prose)} where an "
                "artifact must be — a token containing whitespace was quoted by the author, "
                "so it is an argument, not a flag, and prose names nothing of this tree"
            )
        return True, ""
    candidate = Path(entry)
    try:
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    except (ValueError, OSError) as exc:
        # `Path.resolve()` raises ValueError on an embedded NUL byte: a traceback,
        # not the named refusal `validate()` promises every defect will be.
        return False, f"evidence path cannot be resolved ({exc}): {_short(entry)}"
    if not resolved.is_relative_to(root):
        return False, f"evidence path escapes the tree (`{entry}` resolves outside it)"
    try:
        is_file = resolved.is_file()
    except (OSError, ValueError, NotImplementedError) as exc:
        # ENAMETOOLONG and friends, and an absolute glob's NotImplementedError: a
        # named refusal, not a traceback.
        return False, f"evidence path cannot be inspected ({exc}): {_short(entry)}"
    if not is_file:
        return False, f"evidence path is not a file in the tree: {entry}"
    tracked = _is_tracked(resolved, root)
    if tracked is False:
        # `is_file()` alone accepted a GITIGNORED artifact (measured:
        # `WORKTREE-REPORT.md`, 68 KB of machine-local lane output) — so the row
        # validated on the authoring box and was INVALID for every reader, which is
        # the one thing "an artifact IN THE TREE" must not mean.
        return False, (
            f"evidence path is not TRACKED by git, so a reader's clone does not have it: {entry}"
        )
    if tracked is None:
        # Nothing was measured. Do NOT report this as "not TRACKED": the file may be
        # tracked and present in every clone, and the UNMEASURED line on stderr already
        # names the real cause.
        return False, (
            f"evidence path cannot be verified: the TRACKED check did not answer here: {entry}"
        )
    if tracked is not True:
        # ⛔ The same fourth answer as `_names_artifact`'s bare-path arm: only `True`
        # is the measurement "tracked", and an `is False … is None … return True`
        # shape reads every other truthy value as one. Unreachable under the declared
        # `bool | None` return, pinned so it stays that way (#11474 review).
        return False, (
            f"evidence path cannot be verified: the TRACKED check answered {tracked!r}, "
            f"which is not a measurement that the file is tracked: {entry}"
        )
    return True, ""


def _command_body(raw: str, base: Path, root: Path) -> tuple[str, Path, str]:
    """The command a `cmd:` entry records, the directory it ran in, and — when the
    record is a compound line carrying something OTHER than commands — why not.

    A record is written by whoever ran the thing, quoting what they ran, so the
    entry routinely carries the SHELL WRAPPER around the command. Each of these
    was measured as a false block against the raw shape test, on a legitimate
    record:

        `uv run pytest tests/x.py -q`            markdown backticks around it
        TORTOISE_TEST_CARVE_OUT=1 uv run …       a leading environment assignment
        TORTOISE_DB_URI='docker://…' uv run …    same, with a quoted value
        cd tools && uv run pytest tests/x.py     a directory change first
        set -e && uv run pytest …                a shell-safety preamble
        source .venv/bin/activate && uv run …    an activation step

    ⛔ EVERY `&&` SEGMENT IS GRADED, on both sides of the command. That is the
    whole rule, and it is load-bearing in two measured directions:

    * skipping the segments BEFORE the command is the P1 fail-open: a 22-row
      ledger citing the always-available tracked `docs/evidence/9664-capstone/
      README.md` plus `cmd:NOT RUN && uv run pytest tests/…` reported
      `uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0;
    * skipping the segments AFTER it is the SAME fail-open mirrored: `cmd:uv run
      pytest tests/… && I did not run this` was ACCEPTED and produced the same
      clean exit. A record that disclaims the work must not be gradable as having
      done it, whichever side of the `&&` the disclaimer sits on.

    A segment is therefore admissible only as one of: a WRAPPER (`cd`, a leading
    `NAME=value`, `set`/`export`/`source`/`umask`/`unset`), a DECORATIVE printer
    (`echo`/`printf`/`true`/`:`), or a RUNNER. Anything else is prose, and prose is
    refused by name. `… && echo PASS` and `… && ruff check <path>` stay legal, so
    the two measured false blocks above do not come back.

    ⛔ Only WRAPPERS are removed. The shape test still runs on what is left, which
    is what keeps `cmd:NOT RUN - see tools/capstone_ledger.py` refused: no
    normalisation here can turn a sentence into a program.

    ⛔ The SECOND return value is why `cd` is normalisation and not a false block:
    dropping the `cd` and then resolving the target against the repo root refuses
    every path that is relative TO THE `cd` TARGET — which is the only reason
    anyone writes it. Measured: `cd tools && uv run pytest ../tests/x.py -q` was
    refused while `cd tools && uv run pytest tests/x.py` was accepted, i.e. the
    accepted form names a path that does not exist at that cwd. The effective
    directory is returned so the artifact check resolves where the command ran.
    """
    text = raw.strip()
    # A quoted command is written the way it was written down; one matching pair
    # of backticks/outer quotes is presentation, not part of the command.
    for quote in ("`", '"', "'"):
        if len(text) > 1 and text.startswith(quote) and text.endswith(quote):
            text = text[len(quote) : -len(quote)].strip()
            break
    # `a && b` chains: the FIRST segment that is not a wrapper is THE command, and
    # it is graded whether or not it looks like one.
    #
    # ⛔ NOT "scan for the first RUNNER-shaped segment". That rule silently DROPS
    # prose, which is a measured FAIL-OPEN at this head: `cmd:NOT RUN && uv run
    # pytest tests/test_9664_capstone_verdict_ledger.py` reported `criteria: 22
    # verified: 22 uncovered: 0`, printed `CAPSTONE-LEDGER-RESULT: CLEAN` and exited
    # 0, because the segment saying the work was NOT done was skipped and the real
    # command after it was graded. The comment that used to sit here justified the
    # skip with "prose … never contains one at all", which is false the moment prose
    # can be chained in FRONT of a command — and it can.
    #
    # Prose therefore cannot be dropped, and it cannot sit on EITHER side of the
    # command: any of those makes the record say something it did not do. A segment
    # that is neither a wrapper, a decorative printer nor runner-headed is prose and
    # is refused, so a BARE-prose segment after the `&&` (`… && I did not run this`)
    # is refused exactly as one before it is, and so is the same thing spelled with
    # `;`, `||`, `|`, `&` or a newline — those were measured ACCEPTED at the round-2
    # head and produced the same clean 22-row exit. Wrappers and decorative
    # segments are still admitted (`… && echo PASS`, `… | tail -20 log`).
    # ⛔ RESIDUAL (#11520): a LATER segment that is RUNNER-HEADED skips the argument
    # grading below, so `… && bash I did not run this` and `… && python -c 'NOT RUN'`
    # are accepted. Closing it by grading every runner segment false-blocked
    # `… && git status --short`, so it is tracked rather than bought at that price.
    segments = [segment.strip() for segment in _split_segments(text)]

    #: Index of the LAST segment that can actually RUN something. A TERMINATING
    #: control builtin (`exit`) is only sound in that position — see the `exit` guard
    #: in the loop below. ⛔ IT IS NOT THE LAST *SEGMENT*: an empty segment, a lone
    #: `NAME=value`, and a `cd` execute nothing that is being cited, so
    #: `cmd:uv run pytest … ; exit 0 ; A=1` — where the graded command DID run — was
    #: refused because `A=1` counted as a later segment (tenth review cycle;
    #: `origin/main` accepted it and bash proves the runner executed).
    def _runs_nothing(segment: str) -> bool:
        if not segment:
            return True
        if re.match(r"^cd(\s|$)", segment):
            return True
        match = _LEADING_ASSIGNMENT.match(segment)
        if match and not segment[match.end() :].strip():
            return True
        # A WRAPPER- or DECORATIVE-headed segment runs nothing that is being cited
        # (that is the definition of both sets), so it cannot be the segment that
        # proves the graded command ran — `… ; exit 0 ; export FOO=1` is as inert as
        # `… ; exit 0 ; A=1`. A RUNNER- or prose-headed segment is NOT inert and
        # still makes the `exit` non-final, which is the false PASS the guard exists
        # for.
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            return True
        # ⛔ A CONTROL BUILTIN IS NOT INERT FOR THIS QUESTION. `exit` is a member of
        # `_WRAPPER_VERBS`, so classifying wrappers as inert made `exit` itself inert
        # and therefore never last — which refused every trailing `… || exit 1`
        # (caught by this branch's own tests). The question here is "does a later
        # segment RUN the cited command?", and a later control builtin is another
        # terminator, not evidence, so it is treated as non-inert.
        if tokens[0] in _CONTROL_BUILTINS:
            return False
        # ⛔ USE THE LOOP'S OWN HEAD DERIVATION (\`Path(...).name\`). Classifying on
        # the raw token judged `/bin/echo ok` as running something while the loop
        # below calls it DECORATIVE — the two halves disagreeing about the head of the
        # same token list, which is what produced the previous cycle's false block
        # (final review pass; `origin/main` accepted the record).
        head = Path(tokens[0]).name or tokens[0]
        if head in _WRAPPER_VERBS or head in _DECORATIVE:
            return True
        # A whitespace-separated ASSIGNMENT LIST (`A=1 B=2`) assigns and runs
        # nothing; `_LEADING_ASSIGNMENT` only folds ONE, so it fell through above.
        return all(_ENV_ASSIGNMENT.match(token) for token in tokens)

    _last_used = max((i for i, seg in enumerate(segments) if not _runs_nothing(seg)), default=-1)
    # The directory the graded command runs in: `cd X && …` moves it, and a later
    # `cd` moves it again. Resolved against `root` only, so a `cd ../../..` cannot
    # walk the check out of the tree.
    cwd = base
    command: str | None = None
    command_cwd = base
    for index, segment in enumerate(segments):
        if not segment:
            continue
        # Drop any number of leading `NAME=value` assignments ON THE STRING: they
        # set the environment for the command, they are not the command. Done with
        # a regex rather than by popping tokens, because a re-joined token list
        # loses the quoting shell needs (`bash -c 'a b'` came back as `bash -c a b`)
        # and then the outer `shlex.split` sees a different command than the one
        # that was recorded.
        body = segment
        while True:
            stripped = _LEADING_ASSIGNMENT.sub("", body, count=1)
            if stripped == body:
                break
            body = stripped.lstrip()
        if not body:
            continue
        # `cd <dir>` is a WRAPPER, not the command: consumed, and it moves the
        # directory the following segment is graded in.
        cd = _CD_SEGMENT.match(body)
        if cd:
            target = cd.group(1).strip()
            # ⛔ STRIP THE OPTIONS, so every spelling of one destination grades
            # identically. `cd -P /tmp`, `cd -PL /tmp` and `cd /tmp` all mean /tmp.
            # ⛔ `\s+` BETWEEN GROUPS IS LOAD-BEARING. Allowing a zero-width separator
            # made `-P-L` strip as the two groups `-P` and `-L`, so
            # `cmd:cd -P-L tests && uv run pytest test_9664_…py -q` was ACCEPTED —
            # while bash answers `cd: --: invalid option`, rc 2, so the `&&`
            # short-circuits and the graded command NEVER RAN (seventh review cycle:
            # the false PASS this branch exists to close, and `origin/main` refuses
            # it). Measured on this box: `cd -P tests`, `cd -L tests`, `cd -PL tests`,
            # `cd -LP tests`, `cd -Pe tests` and `cd -- tests` are rc 0; `cd -P-L`,
            # `cd -L-P`, `cd -P--`, `cd -P@`, `cd -e-L`, `cd -@` are rc 2. So a group is
            # `-[PLe]+` or `--` and groups are SEPARATED BY WHITESPACE; anything else
            # dashed is left in the target and refused below.
            # ⛔ `--` ENDS OPTION PARSING, so consume option groups only UNTIL it and
            # take the mode from the groups BEFORE it. The plain regex kept consuming
            # option-shaped groups after `--`, which made `cd -P -- -L ""` strip to an
            # empty target; reading the last of {L,P} across the whole prefix then
            # treated the post-`--` `-L` as an OPTION, so `physical` came out False and
            # the record was admitted — while bash exits 1 with "too many arguments"
            # (`-L` is a second operand there) and the graded command never ran.
            # ⛔ MEASURED, so the scope is not overstated: over the 584-spelling matrix
            # (1-3 groups of `-P -L -PL -LP -Pe -Le -e --`) the PREVIOUS head admitted
            # 128 spellings whose `cd` bash rejects, and every one of those 128 contains
            # a `--` — they are all this class. This head admits 0 of them and agrees
            # with bash on all 584. ⛔ The count is RE-MEASURED, not carried: this line
            # first said "68", and re-running the identical matrix at `75d68eafca`
            # returned 128 (all 128 containing `--`, 0 without) and 0 at this head. The
            # qualitative claim was right and the number was not — the failure a reader
            # cannot detect without redoing the sweep.
            _prefix = ""
            while True:
                _group = re.match(r"^(?:-[PLe]+|--)(?:\s+|$)", target)
                if not _group:
                    break
                _token = _group.group(0).strip()
                _prefix += _token + " "
                target = target[_group.end() :]
                if _token == "--":
                    break
            _modes = [ch for ch in _prefix.split("--")[0] if ch in "LP"]
            physical = bool(_modes) and _modes[-1] == "P"
            target = target.strip()
            try:
                words = shlex.split(target)
            except ValueError:
                words = [target]
            if words == [""]:
                if physical:
                    return (
                        "",
                        base,
                        f"the `cd` segment {segment!r} does not name a directory, and a "
                        "failing `cd` short-circuits the `&&` — no later segment of "
                        "this record can have run (`-P` disables bash's null-name "
                        'rule, so `cd -P ""` exits 1)',
                    )
                # ⛔ bash: "A null directory name is the same as the current
                # directory." `cd ""` is a NO-OP, not an unknowable cwd — measured
                # `bash -c 'cd ""; echo rc=$?'` → rc 0, cwd unchanged — so the later
                # segment provably ran from `cwd` and the record is sound (ninth
                # review cycle; this branch's empty-target arm refused it while
                # `origin/main` accepted it). Deliberately distinct from `cd --`
                # (`$HOME`) and `cd -` (`$OLDPWD`), which fall through to the refusal
                # below.
                continue
            if len(words) == 1:
                target = words[0]
            elif len(words) > 1:
                # An unquoted space is not a valid single `cd` operand; keep it whole
                # so the directory test fails rather than silently taking words[0].
                target = " ".join(words)
            # ⛔ A target that is STILL empty or dashed has an UNKNOWABLE working
            # directory (`cd --` is `$HOME`, `cd -` is `$OLDPWD`, and an unrecognised
            # option is neither). Grading a later artifact from the unmoved `cwd`
            # would admit a record for a command the shell runs somewhere else, so
            # this is refused instead — the one case where refusing is the
            # fail-closed answer rather than the false block.
            if not target or target.startswith("-"):
                return (
                    "",
                    base,
                    f"the `cd` segment {segment!r} does not name a directory this "
                    "ledger can resolve (it is `$HOME`/`$OLDPWD` or an unrecognised "
                    "option), so no later segment can be proven to have run from it",
                )
            try:
                moved = (root / target).resolve()
            except (OSError, ValueError):
                moved = None
            # ⛔ THE PRECONDITION IS THE SHELL'S, NOT CONTAINMENT. `cd X` fails iff
            # X does not exist or is not a directory; when it SUCCEEDS the later
            # segments ran, so refusing a resolvable `cd /tmp` asserted a "failing
            # `cd`" that did not fail and threw away the whole uncovered list
            # (fifth review cycle; `origin/main` accepted the record). Containment
            # needs no test here: a moved `cwd` outside the tree is still
            # overridden by the ARTIFACT rule, which requires every named target to
            # live inside the tree (`_names_artifact`), so `cd /tmp && pytest
            # tests/x` is refused for naming nothing — the right reason.
            # ⛔ `is_dir()` is load-bearing: `(root / target).resolve()` does NOT
            # fail on a name that does not exist — it returns the joined path — so
            # an existence test alone accepted `cd I did not run this` and graded
            # the real command afterwards.
            # ⛔ This DOES make the verdict box-dependent for `cd` wrappers (a
            # `.venv` target exists on the authoring box and in no clone), the same
            # disclosed cost `source` carries. Tracked with it on #11520.
            # ⛔ NOT silently ignored. In the shell a FAILING `cd` short-circuits the
            # `&&`, so no later segment can have run — and this arm used to
            # `continue` regardless, leaving `cwd` unchanged and grading the real
            # command anyway. Measured, `cmd:uv run pytest
            # tests/test_9664_capstone_verdict_ledger.py -q && cd I did not run
            # this` was ACCEPTED while the naked `… && I did not run this` was
            # REFUSED: the same disclaimer, admitted by prefixing it with a `cd`
            # that cannot succeed.
            if moved is None or not moved.is_dir():
                return (
                    "",
                    base,
                    f"the `cd` segment {segment!r} does not name a directory, and a "
                    "failing `cd` short-circuits the `&&` — no later segment of "
                    "this record can have run",
                )
            cwd = moved
            continue
        try:
            _tokens = shlex.split(body)
        except ValueError:
            # ⛔ A shell-VALID record must not invalidate the ledger. `shlex` rejects
            # `pytest … -q  # don't rerun` while `bash -n` accepts it, and exit 1
            # computes NOTHING — so one apostrophe in a trailing comment hid all 13
            # uncovered rows. An unquoted `#` starts a shell comment, so retry with
            # the comment removed. If it STILL will not parse it is not a command
            # (prose with an apostrophe is the realistic case), and it is REFUSED
            # rather than skipped — skipping it is the fail-open above.
            without_comment = re.sub(r"\s+#.*$", "", body).strip()
            if without_comment and without_comment != body:
                body = without_comment
                try:
                    _tokens = shlex.split(body)
                except ValueError:
                    _tokens = []
            else:
                _tokens = []
        # ⛔ EVERY segment is graded, on BOTH sides of the command. A segment that
        # is neither a shell wrapper, a decorative printer, nor a runner is prose:
        # refusing it is what stops `NOT RUN && <cmd>` (prose BEFORE) and
        # `<cmd> && I did not run this` (prose AFTER) from being recorded as work.
        # Both were measured fail-opens on the merged tree, each producing
        # `CAPSTONE-LEDGER-RESULT: CLEAN` and exit 0 from a 22-row ledger.
        if _tokens:
            # ⛔ `Path(".").name` is `""`, not `"."` — so the POSIX `source` spelling
            # never matched `_WRAPPER_VERBS` and `cmd:. .venv/bin/activate && …` was
            # refused as prose (third review cycle). Fall back to the raw token when
            # the path component is empty; every other head resolves as before.
            head = Path(_tokens[0]).name or _tokens[0]
            if head in _WRAPPER_VERBS or head in _DECORATIVE:
                # ⛔ `exit` TERMINATES THE SHELL, so no LATER segment can have run.
                # Admitting it as a harmless wrapper made `cmd:exit 0 ; uv run pytest
                # …` and `cmd:exit 0 || uv run pytest …` reach `verified` /
                # `uncovered: 0` / exit 0 while bash proves the runner was never
                # reached (`bash -c 'exit 0 ; echo RAN-AFTER'` prints nothing) — a
                # false PASS of exactly the class the `cd` guard beside it closes
                # (eighth review cycle; `origin/main` refuses both). A TRAILING
                # `… ; exit $?` / `… || exit 1` stays legal, which is the spelling
                # this repo actually records.
                #
                # ⛔ `return` IS terminating HERE, and the previous measurement that
                # said otherwise was taken with the WRONG SEPARATOR (review cycle 5,
                # P1). `bash -c 'return 0 ; echo RAN-AFTER'` does print RAN-AFTER —
                # a `;` continues past a failure — but a record chains with `&&`,
                # where the SAME failure SHORT-CIRCUITS: measured, `bash -c 'return 0
                # && echo RAN-AFTER'` prints only "return: can only `return' from a
                # function or sourced script", rc 2, NO RAN-AFTER. Generalising a
                # `;` measurement to `&&` admitted `cmd:return 0 && uv run pytest
                # …`, which was measured CLEAN / exit 0 for a runner bash never
                # reached. `shift` is the same shape (`shift 0` succeeds, bare
                # `shift` and `shift 5` fail rc 1); `break`/`continue` genuinely do
                # continue (rc 0) and are NOT terminating. See
                # `_fails_before_a_chain`.
                if index != _last_used and _fails_before_a_chain(head, _tokens[1:]):
                    return (
                        "",
                        base,
                        f"the `{head}` segment {segment!r} FAILS at script level, so no "
                        "later segment of this record can be shown to have run — it "
                        "may only be the LAST segment (a `&&` after a failed builtin "
                        "short-circuits; refusing the `;`/`||` spelling too is a "
                        "known-open false block, #11520)",
                    )
                # ⛔ The HEAD alone is not enough: read the wrapper's own arguments
                # (see `_wrapper_argument_defect` for the measured bypass this
                # closes, and for the decorative-printer residual it cannot).
                why = _wrapper_argument_defect(head, _tokens[1:], cwd, root)
                if why is not None:
                    return (
                        "",
                        base,
                        f"the segment {segment!r} is not a valid `{head}`: {why}",
                    )
                continue
            if head in RUNNERS:
                if command is None:
                    command, command_cwd = body, cwd
                continue
        return (
            "",
            base,
            f"the segment {segment!r} is neither a command nor a shell wrapper (`cd`, "
            f"`NAME=value`, {sorted(_WRAPPER_VERBS)}) nor decorative "
            f"({sorted(_DECORATIVE)}) — a compound `cmd:` record may carry commands and "
            "wrappers, not prose",
        )
    if command is None:
        return "", base, ""
    return command, command_cwd, ""


def _short(text: str, limit: int = 120) -> str:
    """Cap an entry quoted into an error line (a 5 KB path is not a message)."""
    return repr(text if len(text) <= limit else text[:limit] + "…")


def _is_tracked(resolved: Path, root: Path) -> bool | None:
    """`True` tracked · `False` measured-not-tracked · `None` nothing was measured.

    ⛔ `None` HAS TWO CAUSES and names neither: the probe could not be SPAWNED
    (`_tracked_unrunnable`), or git RAN and failed to answer for the path
    (`_tracked_unanswered` — exit 128 on an unreadable index). It is one value because
    the decision is the same — nothing was measured, so the row may not be called
    tracked — and the per-path UNMEASURED line is where the cause is named.

    ⛔ `False` covers TWO measured facts, and only the first is git's own answer: git
    said "no", OR the path is not under `root` at all (the `ValueError` arm below) —
    a structural fact about this repo, true of every clone, and therefore not a claim
    about what git measured. It is not spelled `None` because `None` reports a
    MEASUREMENT FAILURE — nothing could be answered — and no probe failed here: git was
    never asked, because the path is outside this repo and the question does not apply.
    The `False` sentence ("is not TRACKED by git, so a reader's clone does not have
    it") is TRUE of an outside-`root` path, so it is the honest rendering; `None` would
    invent a malfunction. It is not raised because `_evidence_is_real` calls this
    outside its `try`, and the arm is unreachable by construction anyway, since all
    three call sites pre-check `is_relative_to(root)` (#11474 review).

    The third value exists so the CALLER can tell the two refusals apart in the message
    it prints: `False` is a measurement ("not TRACKED by git"), `None` is the absence of
    one. Collapsing them produced a marker line telling a reader a file present in every
    clone is "not TRACKED by git" on a box where `git` was never asked — the same
    guess-the-cause defect this function was fixed for, one layer up.

    The abstention is PRINTED rather than silent, because a CLEAN verdict
    byte-identical to one where the rule ran is how a check quietly stops
    checking.

    ⛔ `subprocess.run` raises `OSError` for exactly one reason — the probe could not
    be SPAWNED (`git` absent from PATH, or EMFILE/ENOMEM/EAGAIN under load). Nothing
    was measured, so no verdict may be given and the row is REFUSED. Folding it into
    `_tracked_unprovable()` is a measured FAIL-OPEN and it shipped: with only the
    `ls-files` probe raising `OSError(EMFILE)`, a `.gitignore`d `WORKTREE-REPORT.md`
    answered `(True, '')` and the record exited **0 CLEAN** — the precise inverse of
    the rule this function exists to enforce, reached through the guard meant to make
    it safe. "git answered NO" is the `returncode == 1` path below and never raises.

    ⛔ `returncode == 1` IS THE ONLY EXIT THAT IS AN ANSWER. `git ls-files
    --error-unmatch` returns 1 for "no match" and 128 when it could not read its own
    index (`fatal: … index file smaller than expected`) — and the follow-up
    `rev-parse --is-inside-work-tree` still answers `true` rc 0 in that state, so the
    old `returncode != 0` test called a file git never answered about "untracked by
    git". Measured on a corrupt `GIT_INDEX_FILE` against a TRACKED path: ls-files →
    128, probe → `true`, `_is_tracked -> False`, and the criteria line told the reader
    `tools/capstone_ledger.py` "is not TRACKED by git, so a reader's clone does not
    have it". That is this same guess-the-cause defect one cause over, so any non-1
    failure is routed to `_tracked_unanswered` instead of being read as a `no`.

    ⛔ WHAT IT MEASURES: `git ls-files` reads the INDEX, so a staged-but-uncommitted
    file, an intent-to-add file, and a symlink RESOLVED to a tracked target all answer
    "tracked" while a reader's clone has none of them. Pre-existing, filed as #11499;
    the STAGED / INTENT-TO-ADD half is unchanged here.

    ⛔ `-- <rel>` IS A PATHSPEC, AND THE WILDCARD DIRECTION WAS LIVE (#11474 review). An
    earlier revision of this note dismissed it on the PREFIX direction alone ("only
    reachable for a DIRECTORY-shaped name — `docs` answers 0, `docs/ab` answers 1 —
    and no call site reaches this function with a directory"). That argument does not
    cover the WILDCARD direction, which needs nothing but a metacharacter in the
    FILENAME: with `docs/ev-axb.md` tracked and `docs/ev-a*b.md` present but in no
    index, `git ls-files --error-unmatch -- 'docs/ev-a*b.md'` prints `docs/ev-axb.md`
    and exits 0, so `_is_tracked` answered `True` and an artifact no clone has was
    accepted as evidence — the fail-open this function exists to refuse, reached
    through the probe itself. The pathspec is therefore `:(literal)`, which makes the
    name a name rather than a pattern (measured on that same repo: rc 1, "did not
    match any file(s) known to git").

    ⛔ IT DOES **NOT** CHANGE THE DIRECTORY DIRECTION, and that direction was never
    live: measured, `git ls-files --error-unmatch -- ':(literal)docs'` answers
    `docs/00_index.md` with rc 0 — byte-identical behaviour to the bare form. What
    closes it is the `is_file()` pre-check at every call site — in
    `_names_artifact`'s glob arm, in its bare-path arm, and in `_evidence_is_real` —
    and NOT this line, so a future caller that ever passed a DIRECTORY would need that
    guard rather than believe this pathspec supplies it. (Those sites are named by
    FUNCTION, not by line number, on purpose: this paragraph cited `:478`, `:544` and
    `:809` for three rounds and none of those lines held an `is_file()` call (#11474
    review). A cite that is wrong is worse than one that is absent — a reader who
    opens it concludes the guard was deleted.) The wildcard hazard is also
    cheaper to reach than "a file present but unindexed": `docs/00_inde?.md`, which
    need not exist on disk at all, answered rc 0 by borrowing a tracked
    `docs/00_index.md`.
    """
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        # NOT a git answer: git is never asked about a path outside `root`. Unreachable
        # by construction — every call site pre-checks `is_relative_to(root)` before
        # calling — so this is a structural refusal kept as a guard, and the sentence it
        # produces downstream ("is not TRACKED by git, so a reader's clone does not have
        # it") is TRUE of an outside-root path. See the docstring's `False` note: the
        # value is the MEASURED-not-tracked one, of which git's "no" is only one cause.
        return False
    try:
        proc = subprocess.run(
            # `:(literal)` is load-bearing, not decoration: without it `rel` is a
            # PATHSPEC, and a filename carrying `*`/`?`/`[` matches a DIFFERENT
            # tracked file — measured, `docs/ev-a*b.md` (untracked) answered rc 0
            # because `docs/ev-axb.md` is tracked. See the docstring.
            ["git", "ls-files", "--error-unmatch", "--", f":(literal){rel}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _tracked_unrunnable(exc, rel)
    if proc.returncode == 0:
        return True
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _tracked_unrunnable(exc, rel)
    if probe.returncode == 0 and probe.stdout.strip() == "true":
        if proc.returncode == 1:
            # Git IS here, it ran, and it ANSWERED "no match": the file is untracked.
            # Blame it. (1 is the only exit that is an answer — see the docstring.)
            return False
        # Git IS here but the probe FAILED rather than answered (128/129: an
        # unreadable index, a bad repo, a usage error). Nothing was measured, so no
        # line may claim git said this path is untracked.
        return _tracked_unanswered(proc.returncode, rel)
    # Hand the probe to the renderer so the NOTE names the cause THAT WAS MEASURED:
    # `false` with rc 0 is git present and answering, not git absent.
    return _tracked_unprovable(probe)


def _tracked_unrunnable(exc: OSError, rel: Path) -> None:
    """The probe could not RUN for `rel`. Nothing was measured, so that path is refused.

    ⛔ NOT `_tracked_unprovable()`: that one is for a MEASURED "git says this is not
    a work tree", where the record is not blamed. A spawn failure is the opposite
    fact — nothing was answered — and abstaining on it is a measured fail-open.

    ⛔ AND IT MAY NOT ANNOUNCE THE ROW'S FATE (#11474 reviews). This is a PER-PATH
    fact, and the caller decides what it does to the row: `_names_artifact`'s glob
    arm accepts as soon as ANY match is tracked, so a line claiming "the row does
    not count" printed for an earlier, unmeasured match would be true of nothing
    while the ledger exits CLEAN — a printed refusal on a passing row, which is the
    legibility failure this function exists to remove, one layer down. The row-level
    consequence is the CALLER's sentence to write (the criteria line refuses); this
    one states only what was measured, and about which path.

    ⛔ THAT INCLUDES THE MARKER, which is why it reads `UNMEASURED` and not a verdict
    word (#11474 review). The marker is the first and loudest token in the line, so
    naming a fate there announces the row's outcome as surely as a sentence would —
    and this function CAN fire on a row that still passes, because a later glob match
    can accept it (measured: the `test_a_glob_accepted_by_a_later_match…` pin had to
    assert `ok is True` and a refusal marker in the SAME run). A marker naming the
    MEASUREMENT is the only one true in both outcomes. All four per-path renderers
    carry it, so the rule cannot rot into "a verdict, except where a row survives".
    """
    print(
        "CAPSTONE-LEDGER: UNMEASURED — the TRACKED check could not be RUN here "
        f"({exc.__class__.__name__}: {exc}), so nothing was measured about {rel}: it "
        "is not shown to be in the tree",
        file=sys.stderr,
    )
    return None


def _tracked_unanswered(returncode: int, rel: Path) -> None:
    """Git ran and did NOT answer for `rel`. Nothing was measured, so that path is refused.

    ⛔ A THIRD refusal, with its own cause. `_tracked_unrunnable` is a probe that
    could not be SPAWNED (`OSError`); `_tracked_unprovable` is git unable to answer for
    the TREE — no repository at all, or a MEASURED "this is not a work tree" (two
    causes of its own; see its docstring); this one is git present, answering for the
    TREE, and failing to answer for the PATH — `git ls-files --error-unmatch` exits 128
    on an unreadable index while the work-tree probe still says `true`. Rendering any
    of the three with another's sentence is the guess-the-cause defect this function
    exists to remove.

    Only exit 1 is a `no` ("no match"); every other non-zero is a failure to answer.
    The message names the exit code rather than pointing at an undiagnosed git error,
    because the exit code is the whole of what was measured here.

    The marker is `UNMEASURED`, not a verdict word, for the reason stated on
    `_tracked_unrunnable` (#11474 review).
    """
    print(
        "CAPSTONE-LEDGER: UNMEASURED — the TRACKED check did not get an ANSWER here "
        f"(`git ls-files` exited {returncode}), so nothing was measured about {rel}: it "
        "is not shown to be in the tree",
        file=sys.stderr,
    )
    return None


def _tracked_unprovable(probe: subprocess.CompletedProcess[str]) -> bool:
    """Git cannot answer here at all (no `.git`, a tarball, a build context).

    The record is not blamed — but the abstention is PRINTED, because a CLEAN
    verdict byte-identical to one where the rule ran is how a check quietly stops
    checking. (`_commit_state` takes the same doctrine; see its docstring.)

    ⛔ TWO CAUSES REACH THIS, AND THEY ARE NOT THE SAME FACT (#11474 review). A tarball
    (no repository) leaves git unable to answer at all; a BARE repo reached over
    `GIT_DIR` leaves git **present, having RUN and ANSWERED `false`** to
    `rev-parse --is-inside-work-tree`. Both abstain — that verdict is #11503 Option 1
    and is deliberately unchanged — but printing *"git is unavailable here"* for the
    second tells a reader git was never asked when it was asked and replied. That is
    the guess-the-cause defect this change removes one function over, and it was
    FROZEN by this PR's own `#11503` pin, whose docstring says "git is present, ran,
    and answered" while its assertion demanded the opposite sentence. `probe` is what
    the caller measured.

    ⛔ AND THE TARBALL CASE IS THE THIRD CAUSE, NOT THE FIRST (#11474 review). MEASURED
    in a directory with no `.git` on a box carrying git 2.43.0: `git ls-files` exits
    **128** (`fatal: not a git repository`) and so does `rev-parse
    --is-inside-work-tree` — git was PRESENT and RAN, and said "there is no repository
    here". The default sentence below said "git is unavailable here" for that case,
    which is the one thing the probe in hand disproves — and because the genuinely
    git-ABSENT case now routes to `_tracked_unrunnable`, that falsified default was
    left as the SOLE production rendering of this renderer. So it is not a default any
    more: an unanswered probe states what the probe actually measured.

    ⛔ `probe` IS REQUIRED, AND THAT IS THE SECOND HALF OF THE SAME FIX (#11474
    review). The paragraph above claimed "it is not a default any more" while the
    signature still made the argument optional and the "git is unavailable here"
    branch still hung off it — a docstring asserting what the code no longer did,
    which is this PR's own defect class. No production caller ever omitted it
    (`_is_tracked` and `_is_tracked_dir` both pass what they measured), so the branch
    was dead in production while a pin kept it alive; dropping the argument was the
    fail-open this PR removes, one refactor away. The signature now makes that
    omission a `TypeError` instead of a silent abstention.

    The three answers it renders are total: rc 0 + `false` (git present and
    answering), any non-zero (git ran and did not answer `true`, with git's own
    stderr), and rc 0 with stdout that is neither — which git does not produce, but
    which must still name the BYTES rather than borrow the unavailable sentence.
    """
    if probe.returncode == 0 and probe.stdout.strip() == "false":
        print(
            "CAPSTONE-LEDGER: NOTE — git is present and says this is not a work tree, "
            "so the TRACKED rule cannot be evaluated; cited paths were checked for "
            "existence only",
            file=sys.stderr,
        )
        return True
    # git RAN and did not answer `true`. ⛔ NAME THE MEASUREMENT, NOT AN
    # INFERENCE FROM IT: a non-zero exit does NOT mean "there is no repository
    # to ask" — `fatal: detected dubious ownership in repository at '<path>'`
    # exits 128 IN a repository that plainly exists, and saying otherwise quotes
    # git naming the repository while claiming there is none (#11474 review).
    # git's own first stderr line is what distinguishes the tarball from the
    # refusal, so it is carried verbatim rather than summarised.
    first_line = (probe.stderr or "").strip().splitlines()
    detail = f": {first_line[0]}" if first_line else ""
    if probe.returncode == 0:
        # rc 0 with an answer that is neither `true` (the caller only reaches this
        # renderer when `true` was NOT printed) nor `false`: the exit code says
        # nothing here, so the bytes are what is named.
        detail = f"; it printed {(probe.stdout or '').strip()!r}"
    print(
        "CAPSTONE-LEDGER: NOTE — git is present but `rev-parse "
        f"--is-inside-work-tree` did not answer `true` (exited {probe.returncode}"
        f"{detail}), so the TRACKED rule cannot be evaluated here; cited paths "
        "were checked for existence only",
        file=sys.stderr,
    )
    return True


def validate(data: dict[str, Any], repo_root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    """Return the criteria rows, or raise InvalidLedger naming every defect found.

    Every check is fail-closed: an unreadable or ambiguous row is a refusal, not
    a skipped row. A skipped row would be an uncovered criterion that never
    appears on the uncovered list — the exact silent no-op this tool exists to
    prevent.
    """
    problems: list[str] = []

    if data.get("version") != 1:
        problems.append(f"`version` must be 1, got {data.get('version')!r}")

    if data.get("issue") != 9664:
        problems.append(f"`issue` must be 9664, got {data.get('issue')!r}")

    measured_at = data.get("measured_at")
    if not isinstance(measured_at, dict):
        problems.append("`measured_at` must be a mapping with ref/sha/date")
    else:
        sha = str(measured_at.get("sha") or "")
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            problems.append(f"`measured_at.sha` must be a 40-hex sha, got {sha!r}")
        else:
            state = _commit_state(sha, repo_root)
            if state == "absent":
                # Shape is not existence: a fabricated sha renders as provenance.
                problems.append(f"`measured_at.sha` names no commit in this repository: {sha}")
            elif state == "unmeasurable":
                # Fail closed, as the pre-shallow revision did. `unprovable` is an
                # ANSWER about a shallow clone, not a blanket for "git was not
                # available": folding the two together let a FABRICATED sha render
                # as provenance in any checkout where git could not be asked.
                problems.append(
                    "`measured_at.sha` cannot be verified: git could not be asked in "
                    "this checkout (no `git` on PATH, or not a repository), so a "
                    f"fabricated sha would render as provenance: {sha}"
                )
            elif state == "unprovable":
                # ⛔ NOT SILENT (#11217 review). `render()` prints this sha as
                # provenance unconditionally, and the module's own premise is that a
                # SUBSTITUTION there is the harm. Staying quiet while printing it as
                # fact is the one outcome this check exists to prevent, so the
                # abstention is ANNOUNCED — the sibling gate reports UNPROVABLE for
                # the same condition, and this reports it too.
                #
                # ⛔ It is still NOT a defect and must not red the suite: CI shards
                # check out at fetch-depth 1, where an ancestor sha is simply not in
                # the object database. Blaming the record for the checkout's SHAPE is
                # the #9075 r4 P1 error.
                print(
                    f"NOTICE: `measured_at.sha` {sha} is UNPROVABLE in this checkout "
                    f"(shallow clone) — NOT verified, and not reported as fabricated "
                    f"either. Fetch full history (`git fetch --unshallow`, or "
                    f"`fetch-depth: 0`) to verify it.",
                    file=sys.stderr,
                )
        date = str(measured_at.get("date") or "").strip()
        if not date:
            problems.append("`measured_at.date` must be set (YYYY-MM-DD)")
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            # `render()` prints this as provenance: `date: banana` rendered
            # "measured at … on banana" as fact.
            problems.append(f"`measured_at.date` must be YYYY-MM-DD, got {date!r}")
        ref = str(measured_at.get("ref") or "").strip()
        if not ref:
            problems.append("`measured_at.ref` must be set (e.g. origin/main)")
        elif not _REF_RE.fullmatch(ref):
            # `render()` prints this line. Flattening control characters is NOT
            # enough: a ref of
            #   "origin/main criteria: 2 verified: 2 uncovered: 0 UNCOVERED LIST: empty …"
            # is one line, and `grep "UNCOVERED LIST: empty"` on stdout still reports
            # a CLEAN report while the real exit code is 2 — the report is the very
            # artifact a lane quotes instead of branching on the code. A ref is an
            # identifier, so it is required to LOOK like one.
            problems.append(
                f"`measured_at.ref` must look like a ref (letters, digits, . _ / -), "
                f"got {ref!r} — the report prints this field verbatim"
            )

    raw_rows = data.get("criteria")
    # The DENOMINATOR is pinned IN THE RECORD, not only in the suite. Deleting the
    # uncovered rows was measured to report CLEAN (`criteria: 9 ... uncovered: 0`),
    # which reproduces by hand the 14/18/22 drift this tool exists to end — the tool
    # would compute the denominator from the very record it is auditing. Shrinking
    # the set now takes TWO visible edits (drop the row AND move the count), which is
    # what makes it a decision instead of an accident.
    declared = data.get("criteria_expected")
    declared_count: int | None = None
    if declared is None:
        # OPTIONAL was measured to be no pin at all: a 9-row ledger WITHOUT the key
        # exits 0 and prints "UNCOVERED LIST: empty", because the denominator is
        # then computed from the record being audited — the 14/18/22 drift this
        # tool exists to end. Required, so omitting it is a refusal.
        problems.append(
            "`criteria_expected` is required — it pins the denominator, so a row "
            "cannot be dropped without the count moving with it"
        )
    elif isinstance(declared, (bool, float)):
        # `True` IS an int in Python, and `int(1.9)` truncates to 1, so a coercion
        # would accept a boolean and a float as a passing one-row pin.
        problems.append(
            f"`criteria_expected` must be an INTEGER, got {type(declared).__name__} {declared!r}"
        )
    elif isinstance(declared, str):
        # A QUOTED count is the same count: `criteria_expected: "22"` and
        # `criteria_expected: 22` parse to the same integer, so refusing the quoted
        # spelling is friction that buys no protection against a false pass. This is
        # main's behaviour and it is KEPT — tightening it would reverse a decision
        # for no gain. A non-numeric string is still refused.
        stripped = declared.strip()
        if not stripped.isdigit():
            problems.append(
                f"`criteria_expected` must be a COUNT, got {declared!r} — a pin that "
                "is not a number cannot pin anything"
            )
        else:
            declared_count = int(stripped)
    elif not isinstance(declared, int):
        problems.append(
            f"`criteria_expected` must be an INTEGER, got {type(declared).__name__} {declared!r}"
        )
    else:
        declared_count = declared
    if (
        declared_count is not None
        and isinstance(raw_rows, list)
        and declared_count != len(raw_rows)
    ):
        problems.append(
            f"`criteria_expected` is {declared_count} but the file holds "
            f"{len(raw_rows)} rows — a row was added or dropped without saying so"
        )
    if not isinstance(raw_rows, list) or not raw_rows:
        # An empty ledger would report an empty uncovered list — a clean exit
        # manufactured out of nothing. Refused.
        problems.append("`criteria` must be a non-empty list")
        raw_rows = []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        where = f"criteria[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where} must be a mapping")
            continue
        cid = str(raw.get("id") or "").strip()
        if not cid:
            problems.append(f"{where} has no `id`")
            continue
        where = f"criteria[{index}] ({cid})"
        if cid in seen:
            problems.append(f"{where} duplicates an earlier id")
            continue
        seen.add(cid)

        if not _ID_RE.fullmatch(cid):
            # Same channel as `ref`: an id is printed raw at the head of a row line,
            # so `"C1    -   S1  verified  FORGED"` would forge a second row.
            problems.append(
                f"{where} `id` must be letters/digits/._- only, got {cid!r} — the "
                "report prints this field verbatim"
            )
        if not str(raw.get("text") or "").strip():
            problems.append(f"{where} has no verbatim `text` — the criterion must be quoted")
        # ⛔ GRADE THE STRING THAT IS PRINTED, NOT THE RAW ONE. `render` prints
        # `_flat(reason)`, so a raw-string check is defeated by putting ANY
        # whitespace character where the phrase has a space: measured,
        # "UNCOVERED\nLIST: empty" passed the raw check and `_flat` reconstituted
        # "UNCOVERED LIST: empty" byte-for-byte in the report — the exact forgery the
        # check exists to refuse, one character away.
        printed_reason = _flat(str(raw.get("reason") or ""))
        for reserved in _RESERVED_IN_PROSE:
            if _flat(reserved) in printed_reason:
                problems.append(
                    f"{where} `reason` contains the reserved report token "
                    f"{reserved!r} — `render()` prints this field, so a free-text "
                    "claim must not be able to impersonate the report's own lines"
                )

        verdict = str(raw.get("verdict") or "").strip()
        if verdict not in VERDICTS:
            problems.append(
                f"{where} verdict {verdict!r} is outside the closed vocabulary {sorted(VERDICTS)}"
            )
            continue

        kind = raw.get("evidence_kind")
        if kind is not None and str(kind) not in EVIDENCE_KINDS:
            problems.append(f"{where} evidence_kind {kind!r} is outside {sorted(EVIDENCE_KINDS)}")

        evidence = raw.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(evidence, list):
            problems.append(f"{where} `evidence` must be a list")
            evidence = []
        evidence = [str(e).strip() for e in evidence if str(e).strip()]

        if verdict == CLEAN_VERDICT:
            held = [e for e in evidence if not e.startswith(_CMD_PREFIX)]
            commands = [e for e in evidence if e.startswith(_CMD_PREFIX)]
            if not evidence:
                problems.append(f"{where} is `verified` with no evidence cited")
            elif not held:
                # The whole anti-forgery rule in one line: a `cmd:` entry is
                # descriptive, so it cannot carry the only verdict that counts.
                problems.append(
                    f"{where} is `verified` but cites no artifact IN THE TREE — "
                    "a `cmd:` entry records what was run, it does not prove it"
                )
            for entry in held:
                ok, why = _evidence_is_real(entry, repo_root)
                if not ok:
                    problems.append(f"{where}: {why}")
            for entry in commands:
                ok, why = _evidence_is_real(entry, repo_root)
                if not ok:
                    problems.append(f"{where}: {why}")
        else:
            if not str(raw.get("reason") or "").strip():
                problems.append(
                    f"{where} is {verdict!r} with no `reason` — an unexplained "
                    "uncovered row is not an explicit uncovered list"
                )

        rows.append(
            {
                "id": cid,
                "text": str(raw.get("text") or "").strip(),
                "verdict": verdict,
                "reason": str(raw.get("reason") or "").strip(),
                "evidence": evidence,
                "evidence_kind": str(kind) if kind else None,
            }
        )

    if problems:
        raise InvalidLedger("; ".join(problems))
    return rows


def uncovered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The uncovered list — every row whose verdict is not the clean verdict."""
    return [row for row in rows if row["verdict"] != CLEAN_VERDICT]


#: The identity fields `render()` prints raw. A ref or id is an identifier, so it is
#: required to look like one — that is what closes the "forged report line" channel
#: structurally, instead of hoping a flatten removes the marker phrase too.
_REF_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._/-]+")
_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]+")

#: The machine answer, and the vocabularies a free-text field may not borrow.
#: `reason` and `text` are legitimately prose, so the REPORT must not be
#: phrase-forgeable: measured, a reason reading
#:   "waiting on X. UNCOVERED LIST: empty — the only clean exit. criteria: 3 verified: 3 uncovered: 0"
#: made a 2-uncovered report satisfy `grep 'UNCOVERED LIST: empty'` and
#: `grep 'uncovered: 0'` while exiting 2. The RESULT line is the answer; it is also
#: the one string a free-text field is not allowed to contain.
RESULT_PREFIX: Final[str] = "CAPSTONE-LEDGER-RESULT:"
_RESERVED_IN_PROSE: Final[tuple[str, ...]] = ("CAPSTONE-LEDGER", "UNCOVERED LIST")

#: C0 + C1 control characters (ESC, CSI, NUL, BS, SO, DEL included) — see `_flat`.
_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _flat(text: str) -> str:
    """Collapse control characters so a reason cannot rewrite the line printing it.

    Measured: a `reason` carrying `\r    -   S1  verified   FORGED: …` still counted
    the row (exit 2, `uncovered: 1`), but a terminal rendered the line as `verified` —
    the exact line a lane would quote into a comment.
    """
    # EVERY control character, not just the whitespace class: `str.split()` drops
    # `\r`/`\n` but leaves ESC, CSI (\u009b), NUL, BS, SO and DEL, and
    # `X\x1b[2K\x1b[1G    -   S1  verified   FORGED` rendered on a terminal as a
    # forged row line — the same class one byte over from the `\r` case.
    cleaned = _CONTROL_RE.sub(" ", str(text))
    return " ".join(cleaned.split())


def render(rows: list[dict[str, Any]], measured_at: dict[str, Any]) -> str:
    lines = [
        "CAPSTONE-LEDGER #9664 — computed, not re-read",
        f"  measured at {_flat(str(measured_at.get('ref')))}@"
        f"{_flat(str(measured_at.get('sha')))} on {_flat(str(measured_at.get('date')))}",
        f"  criteria: {len(rows)}   verified: {len(rows) - len(uncovered(rows))}   "
        f"uncovered: {len(uncovered(rows))}",
    ]
    if not uncovered(rows):
        lines.append("  UNCOVERED LIST: empty — the only clean exit")
        lines.append(f"{RESULT_PREFIX} CLEAN")
        return "\n".join(lines)
    lines.append("  UNCOVERED LIST (each with the reason the runner recorded):")
    for row in uncovered(rows):
        lines.append(f"    - {_flat(row['id']):>4}  {row['verdict']:<11}  {_flat(row['reason'])}")
    lines.append(f"{RESULT_PREFIX} UNCOVERED({len(uncovered(rows))})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Compute #9664's uncovered list from the committed verdict ledger."
    )
    parser.add_argument("command", nargs="?", default="check", choices=["check"])
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER,
        help=f"ledger to validate (default: {DEFAULT_LEDGER.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args(argv)

    mode = os.environ.get("TORTOISE_CAPSTONE_LEDGER", "").strip().lower()
    if mode == "off":
        # Never exit 0: "disabled" must be distinguishable from "clean" by the
        # exit code alone, because the caller cannot be assumed to read stdout.
        print(
            "CAPSTONE-LEDGER: DISABLED (TORTOISE_CAPSTONE_LEDGER=off) — no verdict was computed",
            file=sys.stderr,
        )
        return EXIT_DISABLED

    try:
        data = load_ledger(args.ledger)
        rows = validate(data)
    except InvalidLedger as exc:
        print(f"CAPSTONE-LEDGER: INVALID — {exc}", file=sys.stderr)
        return EXIT_INVALID

    print(render(rows, data.get("measured_at") or {}))

    remaining = uncovered(rows)
    if mode == "advisory":
        print(
            "CAPSTONE-LEDGER: ADVISORY (TORTOISE_CAPSTONE_LEDGER=advisory) — "
            f"verdict not enforced; {len(remaining)} uncovered row(s) reported",
            file=sys.stderr,
        )
        return EXIT_ADVISORY
    return EXIT_UNCOVERED if remaining else EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
