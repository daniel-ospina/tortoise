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
    measured_at: {ref: origin/main, sha: <40 hex>, date: YYYY-MM-DD}
    criteria:
      - id: C1
        text: "<the criterion, VERBATIM from the issue>"
        verdict: verified            # verified | failed | blocked | not-covered
        evidence: [<repo path>, "cmd:<command>"]   # required when verified
        evidence_kind: screenshot    # screenshot | trace | command | server-observation
        reason: "<required unless verified>"

⛔ Every cited path must exist in the tree. A row that names a file which is not
there is REFUSED rather than counted, so a ledger cannot be made green by
describing evidence instead of holding it. `evidence_kind: server-observation`
carries an explicit waiver (it is the shape of step 5, which is a server-side
observation with no screenshot by nature, #9664 comment 6692766222 §D).

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


def _commit_exists(sha: str) -> bool:
    """Is `sha` an object in THIS repository? A sha nobody can resolve is a claim.

    The 40-hex shape check alone lets `measured_at.sha: deadbeef…` through, and
    `render()` then prints "measured at origin/main@deadbeef… on <date>" as fact.
    The one claim binding 22 verdicts to a tree is therefore the cheapest thing
    in the record to fabricate, so it is checked against the object database.
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


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
    tree (the script or test file it ran); prose names nothing and is refused.
    """
    root = repo_root.resolve()
    if entry.startswith(_CMD_PREFIX):
        command = entry[len(_CMD_PREFIX) :].strip()
        if not command:
            return False, "`cmd:` evidence names no command"
        tokens = command.replace("`", " ").replace("'", " ").replace('"', " ").split()
        if len(tokens) < 2:
            return False, f"`cmd:` evidence must be a COMMAND, not a word: {command!r}"
        for token in tokens[1:]:
            candidate = Path(token)
            resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
            if resolved.is_file() and resolved.is_relative_to(root):
                return True, ""
        return False, (
            f"`cmd:` evidence names no artifact of this tree: {command!r} — a command "
            "entry must name the script or test file it ran, so prose cannot stand in "
            "for held evidence"
        )
    candidate = Path(entry)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        return False, f"evidence path escapes the tree (`{entry}` resolves outside it)"
    if not resolved.is_file():
        return False, f"evidence path is not a file in the tree: {entry}"
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
        elif not _commit_exists(sha):
            # Shape is not existence: a fabricated sha renders as provenance.
            problems.append(f"`measured_at.sha` names no commit in this repository: {sha}")
        if not str(measured_at.get("date") or "").strip():
            problems.append("`measured_at.date` must be set (YYYY-MM-DD)")

    raw_rows = data.get("criteria")
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
