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
made green by describing evidence instead of holding it.
`evidence_kind` grants NO waiver — it is checked for membership in a closed
vocabulary only. `server-observation` names the SHAPE of step 5's evidence, which
is a server-side observation with no screenshot by nature (#9664 comment
6692766222 §D); that row still needs a tracked artifact to be `verified`.
⛔ YAML merge keys (`<<`) are refused by name: `SafeConstructor` splices a merged
value's pairs in without constructing that node, so the duplicate-key rule cannot
see inside it, and this record's rule is that every row states its OWN verdict.

Exit codes (a CLOSED set — a caller may branch on them):
    0  CLEAN       the uncovered list is empty; every row valid
    1  INVALID     the ledger cannot be trusted (fail-closed; nothing is computed)
    2  UNCOVERED   valid ledger, uncovered list non-empty (the real finding)
    3  DISABLED    the off-switch was set; NO verdict was computed
    4  ADVISORY    the report was rendered and NOT enforced

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
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

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
            # Merge keys (`<<: *anchor`) are legal YAML and a natural way to share
            # a verdict across rows. They must be flattened BEFORE the duplicate
            # scan, or the anchor's tag has no constructor and the file is reported
            # as "not parseable YAML" — blaming the parser for our own check.
            seen: set[Any] = set()
            for key_node, _ in node.value:
                # ⛔ MERGE KEYS ARE REFUSED BY NAME. `<<` is not "just another key":
                # `SafeConstructor.construct_mapping` splices the merged pairs in
                # WITHOUT constructing that node, so this scan never sees them and
                # a duplicate inside a merge value is silently last-wins. Measured:
                # a row written as `<<: { …verdict: not-covered, verdict: verified}`
                # loaded as `verified` and emptied the uncovered list — a false PASS
                # reached through the one place the duplicate rule could not look.
                # Refusing the tag is also the honest reading of this ledger's rule:
                # every row states its OWN verdict, so a shared anchor that can carry
                # a verdict the row does not say is not a shape this record accepts.
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


#: A `cmd:` entry may carry ANY command text. It is a RECORD of what was run, not
#: a proof: the validator never executes it, so no shape heuristic can establish
#: that it ran, and every heuristic this tool has tried to grade it with has been
#: defeated by one more shell form (a quoted prose argument, an `&& echo PASS`
#: tail, a `python` in front of a sentence, a merge-key anchor ...). The
#: resolution is not a better heuristic — it is to stop claiming the arm proves
#: anything: **`cmd:` evidence does not satisfy `verified`.** A verified row must
#: hold at least one artifact IN THE TREE. `cmd:` entries stay in the record
#: because they are useful to a reader, and they are checked only for being a
#: recorded command rather than an empty string.
def _commit_state(sha: str) -> str:
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
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return "exists"
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=str(REPO_ROOT),
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
    """A path must be a TRACKED FILE inside the tree; a `cmd:` entry must be non-empty.

    ⛔ This is the anti-forgery rule and the reason the tool is worth having: a
    row that *describes* its evidence in prose used to count. Now the file has
    to be in the tree, or the row does not count at all — and "in the tree" is
    enforced literally: an absolute path outside the checkout, a directory, and
    `.` are all REFUSED, because `Path.exists()` accepted every one of them and
    a reviewer measured each returning a clean exit on a `verified` row.

    `cmd:` evidence is a different matter, and the difference is now a DECISION
    rather than a shape test: it records what was run, and this tool cannot know
    that it ran (it never executes it), so **it does not satisfy `verified`**. A
    verified row must hold at least one artifact that is IN THE TREE. Every shape
    heuristic tried here — a runner-first token, an existing-file token, backtick
    and `NAME=value` prefixes — was defeated by one more shell form in review
    (a quoted prose argument, an `&& echo PASS` tail, `python` in front of a
    sentence), which is the signal that the arm should prove less, not parse more.

    ⛔ WHAT THIS DOES NOT PROVE: a held artifact proves the file is IN the tree,
    not that it supports the row citing it (22 rows citing one trace would
    validate). `verified` therefore means "an artifact in this tree is named as
    its evidence", not "the evidence was audited". The runner records a verdict
    and stands behind it; the tool refuses a record that is malformed, ambiguous,
    or cites what it does not hold.
    """
    root = repo_root.resolve()
    if entry.startswith(_CMD_PREFIX):
        command = entry[len(_CMD_PREFIX) :].strip()
        if not command:
            return False, "`cmd:` evidence names no command"
        # NON-EMPTY IS THE WHOLE CHECK — no token, shape or parse rule. Every rule
        # tried here false-blocked a real record, and because exit 1 computes
        # NOTHING it also hid the entire uncovered list: `cmd:make` beside a held
        # artifact did it, and so did a shell-valid command carrying a `#` note with
        # an apostrophe (`shlex` rejects it while `bash -n` accepts it). An arm that
        # confers nothing must not be able to invalidate anything.
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
    except OSError as exc:  # ENAMETOOLONG and friends: a named refusal, not a traceback
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


def _short(text: str, limit: int = 120) -> str:
    """Cap an entry quoted into an error line (a 5 KB path is not a message)."""
    return repr(text if len(text) <= limit else text[:limit] + "…")


def _is_tracked(resolved: Path, root: Path) -> bool:
    """Is `resolved` tracked? `unprovable` (no git) does not blame the record."""
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return False
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(rel)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0 and probe.stdout.strip() == "true":
        # Git IS here and it refused this path: the file is untracked. Blame it.
        return False
    # Git cannot answer here at all (no `.git`, a tarball, a build context). The
    # record is not blamed — but the abstention is PRINTED, because a CLEAN verdict
    # byte-identical to one where the rule ran is how a check quietly stops
    # checking. (`_commit_state` takes the same doctrine; see its docstring.)
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
            state = _commit_state(sha)
            if state == "absent":
                # Shape is not existence: a fabricated sha renders as provenance.
                problems.append(f"`measured_at.sha` names no commit in this repository: {sha}")
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
    if declared is None:
        # OPTIONAL was measured to be no pin at all: a 9-row ledger WITHOUT the key
        # exits 0 and prints "UNCOVERED LIST: empty", because the denominator is
        # then computed from the record being audited — the 14/18/22 drift this
        # tool exists to end. Required, so omitting it is a refusal.
        problems.append(
            "`criteria_expected` is required — it pins the denominator, so a row "
            "cannot be dropped without the count moving with it"
        )
    declared_count: int | None = None
    if declared is not None:
        if isinstance(declared, (bool, float)):
            # `int(True) == 1` and `int(1.9) == 1`, so a bare `int()` casts a
            # non-count into a pin that happens to match.
            problems.append(f"`criteria_expected` must be an integer, got {declared!r}")
        elif isinstance(declared, str) and not declared.strip().isdigit():
            problems.append(f"`criteria_expected` must be an integer, got {declared!r}")
        else:
            try:
                declared_count = int(declared)
            except (TypeError, ValueError):
                problems.append(f"`criteria_expected` must be an integer, got {declared!r}")
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
    parser = argparse.ArgumentParser(
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
