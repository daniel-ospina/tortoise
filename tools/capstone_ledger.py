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
    criteria_expected: 22          # the DENOMINATOR, pinned in the record itself
    measured_at: {ref: origin/main, sha: <40 hex>, date: YYYY-MM-DD}
    criteria:
      - id: C1
        text: "<the criterion, VERBATIM from the issue>"
        verdict: verified            # verified | failed | blocked | not-covered
        evidence: [<repo path>, "cmd:<command>"]   # required when verified
        evidence_kind: screenshot    # screenshot | trace | command | server-observation
        reason: "<required unless verified>"

⛔ Every path cited by a `verified` row must exist in the tree. A `verified` row
that names a file which is not there is REFUSED rather than counted, so a ledger
cannot be made green by describing evidence instead of holding it. (A row that is
NOT claimed as verified keeps its reason instead, and its citations are not
validated — the record is not asserting them as held evidence.) `evidence_kind:
server-observation`
carries an explicit waiver (it is the shape of step 5, which is a server-side
observation with no screenshot by nature, #9664 comment 6692766222 §D).

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

import argparse
import os
import re
import shlex
import subprocess
import sys
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

#: `server-observation` is the declared waiver: step 5 is a server-side
#: observation (`connection-written` / `harness-connected` do not happen
#: on a screen), so it can never carry a screenshot. Naming the kind makes the
#: waiver explicit and per-row instead of an unwritten convention.
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
#: the other four keep one meaning each.
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
    `check` (exit 2, all 44 tests green) while being invalid YAML per any strict
    reader. The default parser is therefore replaced, not trusted.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - yaml is a hard dep of the repo
        raise InvalidLedger(f"PyYAML is unavailable, so no ledger can be read: {exc}") from exc

    class _NoDuplicateKeys(yaml.SafeLoader):
        """A `SafeLoader` whose mappings refuse a repeated key."""

        def construct_mapping(self, node, deep=False):
            seen: set[Any] = set()
            for key_node, _ in node.value:
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
    `docs/*/../../*` expands to 1085 files and every one is at the repo ROOT — the
    exact vacuity above, one wildcard deep (measured: exit 0 on a one-row
    `verified` ledger). `docs/../tests/*.py` stays legal because its `..` sits
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


def _names_artifact(token: str, root: Path) -> tuple[bool, str]:
    """Does this ONE token name a file of the tree? A bare path, a pytest node-id,
    or a glob over a named directory.

    A pytest node-id names the file MORE precisely than a bare path, so
    `tests/x.py::TestY::test_z` counts as naming `tests/x.py`; refusing it would be
    a false block on the most precise citation shape there is.
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
            for match in root.glob(text):
                resolved_match = match.resolve()
                if resolved_match.is_file() and resolved_match.is_relative_to(root):
                    return True, ""
            return False, f"no file of this tree matches the glob {text!r}"
        candidate = Path(text)
        resolved = (candidate if candidate.is_absolute() else root / text).resolve()
        if resolved.is_file() and resolved.is_relative_to(root):
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
    """`exists` | `absent` | `unprovable` for a measured-at sha.

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
        return "unprovable"
    if shallow.returncode != 0:
        return "unprovable"
    return "unprovable" if shallow.stdout.strip() == "true" else "absent"


def _evidence_is_real(entry: str, repo_root: Path = REPO_ROOT) -> tuple[bool, str]:
    """A path must be a FILE inside the tree; a `cmd:` entry must name an artifact.

    ⛔ This is the anti-forgery rule and the reason the tool is worth having: a
    row that *describes* its evidence in prose used to count. Now the file has
    to be in the tree, or the row does not count at all — and "in the tree" is
    enforced literally: an absolute path outside the checkout, a directory, and
    `.` are all REFUSED, because `Path.exists()` accepted every one of them and
    a reviewer measured each returning a clean exit on a `verified` row.

    The `cmd:` arm gets the same treatment, for the same reason: accepting any
    non-empty string meant `cmd:done` — or a sentence explaining that the work
    was done — satisfied the strongest verdict in the vocabulary, so one word
    defeated the whole record. A command entry must now NAME an artifact of this
    tree in argument position; prose names nothing and is refused.

    ⚠️ RESIDUAL, stated rather than hidden: this arm proves the entry is
    COMMAND-SHAPED and cites a real artifact there. It does **not** prove the
    command RAN it — `cmd:uv run python --help <path>` names the artifact and is
    accepted, because the tool must never execute anything. The rule is "names an
    artifact", not "ran it"; do not read a green ledger row as the stronger claim.
    """
    root = repo_root.resolve()
    if entry.startswith(_CMD_PREFIX):
        command = entry[len(_CMD_PREFIX) :].strip()
        if not command:
            return False, "`cmd:` evidence names no command"
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False, f"`cmd:` evidence is not a parseable command: {command!r}"
        if len(tokens) < 2:
            return False, f"`cmd:` evidence must be a COMMAND, not a word: {command!r}"
        runner = Path(tokens[0]).name
        if runner not in RUNNERS:
            # This is the arm that kills prose. A token scan alone does not: both
            # `cmd:verified manually by reading pyproject.toml` and
            # `cmd:NOT RUN - see tools/capstone_ledger.py` name a file that exists,
            # and both satisfied `verified` — one of them saying the work was NOT
            # done. The command SHAPE is the discriminator.
            return False, (
                "`cmd:` evidence must START with a program that runs something "
                f"({sorted(RUNNERS)}), got {tokens[0]!r} in {command!r}"
            )
        for token in tokens[1:]:
            if token.startswith("-"):
                # A flag (and the `--` separator). A flag's VALUE may still be the
                # artifact; it is reached on the next iteration.
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
            ok, why = _names_artifact(token, root)
            if ok:
                return True, ""
            return False, (
                f"`cmd:` evidence names no artifact of this tree: {command!r} — the first "
                f"thing in argument position after the runner must NAME the artifact "
                f"(a bare path, a pytest node-id, or a glob over a named directory), and "
                f"{why}. A command entry must NAME an artifact; prose names nothing."
            )
        return False, (
            f"`cmd:` evidence names no artifact of this tree: {command!r} — it is a "
            "runner with no target at all (no script or test file it ran), so prose "
            "cannot stand in for held evidence"
        )
    candidate = Path(entry)
    try:
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        # ⛔ `is_file()` is INSIDE the guard, not after it: it stats the path, so a
        # name that is too long raises `OSError(ENAMETOOLONG)` and an unreadable
        # directory component raises `PermissionError`. Both escaped `validate`'s
        # "rows, or `InvalidLedger`" contract and printed a traceback with no
        # `CAPSTONE-LEDGER: INVALID` marker (measured on a 400-char component).
        if not resolved.is_relative_to(root):
            return False, f"evidence path escapes the tree (`{entry}` resolves outside it)"
        if not resolved.is_file():
            return False, f"evidence path is not a file in the tree: {entry}"
    except (OSError, ValueError, NotImplementedError) as exc:
        return False, f"evidence path cannot be resolved ({type(exc).__name__}: {exc})"
    return True, ""


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
            # `unprovable` (a shallow checkout) is NOT a defect and must not red the
            # suite: CI shards check out at fetch-depth 1, where an ancestor sha is
            # simply not in the object database. Blaming the record for that is the
            # #9075 r4 P1 shape.
        if not str(measured_at.get("date") or "").strip():
            problems.append("`measured_at.date` must be set (YYYY-MM-DD)")

    raw_rows = data.get("criteria")
    # The DENOMINATOR is pinned IN THE RECORD, not only in the suite. Deleting the
    # uncovered rows was measured to report CLEAN (`criteria: 9 ... uncovered: 0`),
    # which reproduces by hand the 14/18/22 drift this tool exists to end — the tool
    # would compute the denominator from the very record it is auditing. Shrinking
    # the set now takes TWO visible edits (drop the row AND move the count), which is
    # what makes it a decision instead of an accident.
    #
    # ⛔ REQUIRED, not optional. While it could be omitted, "drop the uncovered rows
    # AND delete this block" was a second, invisible route to a clean exit: the pin
    # held only because the committed artifact happens to carry the key, which is a
    # property of that file rather than of the tool.
    declared = data.get("criteria_expected")
    declared_count: int | None = None
    if declared is None:
        problems.append(
            "`criteria_expected` must be declared — the denominator is pinned IN the "
            "record, or the tool derives it from the file it is auditing"
        )
    elif isinstance(declared, bool) or not isinstance(declared, int):
        # `True` IS an int in Python, and `int(1.9)` truncates to 1, so a coercion
        # would accept a boolean and a float as a passing one-row pin.
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

        if not str(raw.get("text") or "").strip():
            problems.append(f"{where} has no verbatim `text` — the criterion must be quoted")

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
            if not evidence:
                problems.append(f"{where} is `verified` with no evidence cited")
            for entry in evidence:
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


def render(rows: list[dict[str, Any]], measured_at: dict[str, Any]) -> str:
    lines = [
        "CAPSTONE-LEDGER #9664 — computed, not re-read",
        f"  measured at {measured_at.get('ref')}@{measured_at.get('sha')} on {measured_at.get('date')}",
        f"  criteria: {len(rows)}   verified: {len(rows) - len(uncovered(rows))}   "
        f"uncovered: {len(uncovered(rows))}",
    ]
    if not uncovered(rows):
        lines.append("  UNCOVERED LIST: empty — the only clean exit")
        return "\n".join(lines)
    lines.append("  UNCOVERED LIST (each with the reason the runner recorded):")
    for row in uncovered(rows):
        lines.append(f"    - {row['id']:>4}  {row['verdict']:<11}  {row['reason']}")
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
