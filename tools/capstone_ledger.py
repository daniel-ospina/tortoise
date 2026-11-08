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
    r"""^[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"]*"|[^\s'"])+\s+"""
)

#: A `cd <dir>` WRAPPER segment (no `;`/flags — the compound-separator forms are not
#: normalised, only the plain `cd X && …` this repo's own recipes use).
_CD_SEGMENT: Final = re.compile(r"^cd\s+(\S[^;|&]*)$")

#: A `-c`/`-e` script may itself contain a `-c`. Bounded so a crafted record cannot
#: recurse the validator; anything past this is a refusal, not a hang.
_MAX_SCRIPT_DEPTH: Final[int] = 3


def _split_chains(text: str) -> list[str]:
    """Split a command on UNQUOTED `&&` only.

    A plain `text.split("&&")` splits inside quotes too, so `bash -c 'a && b'` —
    the only way the docstring says a script can be recorded — came back as
    `bash -c 'a` and was refused as unparseable, which is exit 1, which hides the
    entire uncovered list. Only a separator the SHELL would treat as one splits
    here.

    A backslash escapes only inside DOUBLE quotes, and it must take the escaped
    character with it — otherwise `bash -c "echo \\" && echo x"` ends the quote
    early and the command is split at the wrong place. (Ported from the peer lane's
    `_split_on_chain` at 35ea7658e, which handles that case; this branch's first
    version did not.)
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
        elif text.startswith("&&", index):
            segments.append("".join(current))
            current = []
            index += 2
            continue
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
_SCRIPT_FLAGS: Final[frozenset[str]] = frozenset({"-c", "-e", "--command", "--eval"})

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
            for match in cwd.glob(text):
                resolved_match = match.resolve()
                if not (resolved_match.is_file() and resolved_match.is_relative_to(root)):
                    continue
                if _is_tracked(resolved_match, root):
                    return True, ""
                untracked_seen = True
            if untracked_seen:
                return False, (
                    f"every file the glob {text!r} matches is untracked by git, so a "
                    "reader's clone does not have them"
                )
            return False, f"no file of this tree matches the glob {text!r}"
        candidate = Path(text)
        resolved = (candidate if candidate.is_absolute() else cwd / text).resolve()
        if resolved.is_file() and resolved.is_relative_to(root):
            if not _is_tracked(resolved, root):
                return False, (
                    f"{text!r} is not TRACKED by git, so a reader's clone does not have it"
                )
            return True, ""
        return False, f"{text!r} is not a file in this tree"
    except (OSError, ValueError, NotImplementedError) as exc:
        # An entry that cannot even be RESOLVED is a defect in the RECORD, not a
        # crash in the tool: `validate`'s contract is "the rows, or InvalidLedger
        # naming every defect". Measured escapes before this guard: an absolute
        # glob raised NotImplementedError out of `Path.glob`, and a NUL byte raised
        # ValueError out of `resolve` — both printed a traceback instead of the
        # `CAPSTONE-LEDGER: INVALID` marker a log reader or caller keys on.
        return False, f"{text!r} cannot be resolved against this tree ({type(exc).__name__}: {exc})"


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
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
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
    defeated the whole record. A command entry must now be COMMAND-SHAPED (it
    starts with a program that runs something) and NAME an artifact of this tree
    in argument position; prose names nothing and is refused.

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
        command, cmd_cwd = _command_body(raw, base, root)
        if not command:
            return False, f"`cmd:` evidence names no command: {raw!r}"
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False, f"`cmd:` evidence is not a parseable command: {command!r}"
        if not tokens:
            return False, f"`cmd:` evidence names no command: {raw!r}"
        if Path(tokens[0]).name not in RUNNERS:
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
                # A flag (and the `--` separator). A flag's VALUE may still be the
                # artifact; it is reached on the next iteration.
                consumed_script = token in _SCRIPT_FLAGS
                continue
            if token in _CHAIN_WORDS or Path(token).name in RUNNERS:
                # Still inside the runner chain: `uv` `run` `python` `-m` `pytest`.
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
                f"(a bare path, a pytest node-id, or a glob over a named directory), and "
                f"{why}. A command entry must NAME an artifact; prose names nothing."
            )
        # A runner with NO target — `make`, `uv run pytest`, `npm test`. It is a
        # legitimate RECORD and it confers nothing: `validate` already requires a
        # verified row to hold at least one TRACKED path, so the entry cannot carry
        # a verdict on its own. Returning False here was a measured false block
        # (`cmd:make` beside a held artifact → exit 1) and exit 1 computes NOTHING,
        # so it also hid the entire uncovered list — a refusal that loses the
        # finding is worse than the weaker arm.
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
    if not _is_tracked(resolved, root):
        # `is_file()` alone accepted a GITIGNORED artifact (measured:
        # `WORKTREE-REPORT.md`, 68 KB of machine-local lane output) — so the row
        # validated on the authoring box and was INVALID for every reader, which is
        # the one thing "an artifact IN THE TREE" must not mean.
        return False, (
            f"evidence path is not TRACKED by git, so a reader's clone does not have it: {entry}"
        )
    return True, ""


def _command_body(raw: str, base: Path, root: Path) -> tuple[str, Path]:
    """The part of a `cmd:` entry that has to be a RUNNABLE command, and the
    directory it runs in.

    A record is written by whoever ran the thing, quoting what they ran, so the
    entry routinely carries the SHELL WRAPPER around the command. Each of these
    was measured as a false block against the raw shape test, on a legitimate
    record, and the five are the shapes this repo's own `AGENTS.md` hands a lane:

        `uv run pytest tests/x.py -q`            markdown backticks around it
        TORTOISE_TEST_CARVE_OUT=1 uv run …       a leading environment assignment
        TORTOISE_DB_URI='docker://…' uv run …    same, with a quoted value
        cd tools && uv run pytest tests/x.py     a directory change first
        make                                     a project target

    ⛔ Only the WRAPPER is removed. The shape test still runs on what is left,
    which is what keeps `cmd:NOT RUN - see tools/capstone_ledger.py` refused: no
    normalisation below can turn a sentence into a program.

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
    # `a && b` chains: grade the FIRST segment that starts with a program that
    # runs something. Grading a fixed END also false-blocked a real record (a
    # `&& echo PASS` tail), and grading the first segment false-blocked
    # `cd tools && uv run …`; scanning for the first RUNNER-shaped segment accepts
    # both and still refuses prose, which never contains one at all.
    segments = [segment.strip() for segment in _split_chains(text)]
    # The directory the graded command runs in: `cd X && …` moves it, and a later
    # `cd` moves it again. Resolved against `root` only, so a `cd ../../..` cannot
    # walk the check out of the tree.
    cwd = base
    for segment in segments:
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
            target = cd.group(1).strip().strip("'\"")
            if target and not target.startswith("-"):
                try:
                    moved = (root / target).resolve()
                except (OSError, ValueError):
                    moved = None
                if moved is not None and moved.is_relative_to(root):
                    cwd = moved
            continue
        try:
            tokens = shlex.split(body)
        except ValueError:
            # ⛔ A shell-VALID record must not invalidate the ledger. `shlex` rejects
            # `pytest … -q  # don't rerun` while `bash -n` accepts it, and exit 1
            # computes NOTHING — so one apostrophe in a trailing comment hid all 13
            # uncovered rows. An unquoted `#` starts a shell comment, so retry with
            # the comment removed. If it STILL will not parse it is not a command
            # (prose with an apostrophe is the realistic case), and a refusal that
            # cannot be turned into a clean exit is the fail-closed answer.
            without_comment = re.sub(r"\s+#.*$", "", body).strip()
            if without_comment == body:
                continue
            body = without_comment
            try:
                tokens = shlex.split(body)
            except ValueError:
                continue
        if tokens and Path(tokens[0]).name in RUNNERS:
            return body, cwd
    # No segment is runner-shaped. Return the first non-empty one so the error
    # message quotes what was actually refused rather than a fragment.
    for segment in segments:
        if segment:
            return _LEADING_ASSIGNMENT.sub("", segment).lstrip() or segment, root
    return "", root


def _short(text: str, limit: int = 120) -> str:
    """Cap an entry quoted into an error line (a 5 KB path is not a message)."""
    return repr(text if len(text) <= limit else text[:limit] + "…")


def _is_tracked(resolved: Path, root: Path) -> bool:
    """Is `resolved` tracked? An UNRUNNABLE probe REFUSES; it does not abstain.

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
    it safe. "git answered NO" is the `returncode != 0` path below and never raises.

    ⛔ WHAT IT MEASURES: `git ls-files` reads the INDEX, so a staged-but-uncommitted
    file, an intent-to-add file, and a symlink RESOLVED to a tracked target all answer
    "tracked" while a reader's clone has none of them — and `-- <rel>` is a PATHSPEC,
    so a cited name that prefix-matches a tracked path is answered TRACKED too.
    Pre-existing, unchanged here, filed as #11499.
    """
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return False
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(rel)],
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
        # Git IS here and it refused this path: the file is untracked. Blame it.
        return False
    return _tracked_unprovable()


def _tracked_unrunnable(exc: OSError, rel: Path) -> bool:
    """The probe could not RUN. Nothing was measured, so the row is REFUSED.

    ⛔ NOT `_tracked_unprovable()`: that one is for a MEASURED "git says this is not
    a work tree", where the record is not blamed. A spawn failure is the opposite
    fact — nothing was answered — and abstaining on it is a measured fail-open.
    """
    print(
        "CAPSTONE-LEDGER: REFUSED — the TRACKED check could not be RUN here "
        f"({exc.__class__.__name__}: {exc}), so {rel} is not shown to be in the tree "
        "and the row does not count",
        file=sys.stderr,
    )
    return False


def _tracked_unprovable() -> bool:
    """Git cannot answer here at all (no `.git`, a tarball, a build context).

    The record is not blamed — but the abstention is PRINTED, because a CLEAN
    verdict byte-identical to one where the rule ran is how a check quietly stops
    checking. (`_commit_state` takes the same doctrine; see its docstring.)
    """
    print(
        "CAPSTONE-LEDGER: NOTE — git is unavailable here, so the TRACKED rule cannot "
        "be evaluated; cited paths were checked for existence only",
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
            # `unprovable` (a shallow checkout) is NOT a defect and must not red the
            # suite: CI shards check out at fetch-depth 1, where an ancestor sha is
            # simply not in the object database. Blaming the record for that is the
            # #9075 r4 P1 shape.
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
