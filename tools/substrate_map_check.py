#!/usr/bin/env python3
"""The substrate map's own drift guard (#5086).

`docs/architecture/SUBSTRATE-REBUILD-MAP.md` is the organizing artifact for the
substrate rebuild: which epic owns an issue, which **file family** each lane
owns, and what changes hands between components. It replaces the issue-only form
of that map, which went stale **three** times (2026-09-25 ×2, 2026-10-26) — every
time because nothing in the tree could tell that it had.

This tool is the half of the map that CAN be checked offline. It re-measures the
machine-checkable claims the document makes and fails when the document and the
tree disagree:

  1. every **file family** a lane row declares resolves on disk;
  2. every **symbol** the interface map places is still declared in the file it
     names — AND the document still names that symbol (so the two cannot drift
     apart in either direction);
  3. the D10 retirement MECHANISM is still in place — `_DOC_RETIRED_KEYS` in the
     writers and `relabel_legacy_documents` in the graph migration (both are
     interface symbols, so they are covered by (2));
  4. the document is registered in `docs/00_index.md`;
  5. the document's `measured_at_commit` is an ancestor of `HEAD` — the map names
     the tree it was cut from (`tools/finding_provenance.py`'s rule, applied to a
     doc rather than to a finding). ⚠️ **(5) is the one claim that needs history.**
     `actions/checkout` fetches ONE commit by default, so in the `test` job the
     object is absent and the question is unanswerable THERE. That is reported as
     `UNVERIFIED` — a state error (exit 2), never a drift, because the document is
     not wrong, the clone is shallow. The claim is proved by the REQUIRED `docs`
     job: it checks out at `fetch-depth: 0` and runs `--check` unconditionally,
     which is also the only home a docs-only PR reaches (#4454's precedent).

⛔ What it deliberately does NOT check, and why:

- **whether any issue is open or closed.** That is the tracker's job and it needs
  the network; the document records the measurement command and a dated result
  instead (§1 / §9 of the doc).
- **whether `:Document` is still minted.** `:Document` occurs on 55 lines across
  17 files under `tortoise/` in the tree this was written against (`git grep -n`
  counts LINES, `git grep -l` counts FILES — the two are not the same number,
  and the doc's figure is the line one), and every one of those lines is prose
  naming the *retired* label (a docstring, a comment, an RST literal). A pattern
  loose enough to catch a mint catches those; one narrow enough to skip them still
  matches the RST literals. The honest options were a hand-maintained allowlist of
  docstrings or no regex check at all, so the label claim stays evidence in the
  doc (§5).

A guard that claimed either and silently could not deliver it would be the same
false PASS the document exists to prevent.

Usage:
    python tools/substrate_map_check.py           # same as --check
    python tools/substrate_map_check.py --check   # 0 clean · 1 drifted · 2 unverified
    python tools/substrate_map_check.py --json    # print the measurements read

Exit codes: 0 clean · 1 drifted · 2 the document, a measurement input, or the
object a claim names is unreadable/ABSENT here (a state error, NOT a pass).
2 therefore means "this checkout cannot answer the question" — a shallow clone
excuses nothing, it just moves the proof to the job that has the history.

Pinned by `tests/test_substrate_map.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"
INDEX = ROOT / "docs" / "00_index.md"

#: The bound on the one git probe (§5). Measured: 7 ms for a locally-present
#: object, 24 s for an absent one on a partial clone — see the docstring.
ANCESTRY_TIMEOUT_S = 10

#: (symbol, file it is declared in) — the interface-map claims (§5 of the doc).
#: Each must ALSO appear by name in the document; a symbol the doc stops naming
#: is a claim the doc no longer makes, and leaving it here would silently widen
#: the guard to something nobody reads.
INTERFACE_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("STRUCTURAL_REL_LABELS", "tortoise/projection/edges.py"),
    ("def stub_key", "tortoise/projection/edges.py"),
    ("_SOURCE_HANDLED", "tortoise/projection/entities.py"),
    ("_DOC_RETIRED_KEYS", "tortoise/projection/entities.py"),
    ("def relabel_legacy_documents", "tortoise/migrate_kinds.py"),
    ("def build_master_list", "tortoise/extractor_v2.py"),
    ("def compile_vocab", "tortoise/commit_schema.py"),
    ("def compile_value_brief", "tortoise/value_extractor.py"),
)

_FRONTMATTER_SHA = re.compile(r"^measured_at_commit:\s*([0-9a-f]{7,40})\s*$", re.M)
#: A backticked repo-relative path inside a lane row's file-family cell.
_BACKTICK_PATH = re.compile(r"`((?:tortoise|tools|config|tests|docs)/[^`\s]+)`")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _state_error(message: str) -> SystemExit:
    """A state error is exit **2** — never 1, which means the document drifted.

    `raise SystemExit("<message>")` prints the message and exits **1**, the code
    already reserved for drift — so an operator or a caller separating "the map
    drifted" from "the map is gone / git is broken" by exit code got the answer
    of the wrong branch, and was pointed at the document instead of the checkout
    (#5086 review P2-2). The tri-state below is only real if 2 is actually
    produced.
    """
    print(f"state error: {message}", file=sys.stderr)
    return SystemExit(2)


def _declaration_re(symbol: str) -> re.Pattern[str]:
    """The DECLARATION shape of *symbol*, not merely its name.

    A bare substring over the whole file is satisfied by prose or by a reader, not
    by a declaration: `_DOC_RETIRED_KEYS` also occurs on six non-declaration lines
    in `projection/entities.py` and `_SOURCE_HANDLED` on seven, so blanking the D10
    retirement allowlist itself — the mechanism §5 of the document says this guard
    holds, on the one claim the doc compensates for by dropping the `:Document`
    regex — still read as CLEAN. That is the false PASS this tool exists to
    prevent (#5086 review P2-1). A declaration is a `def` or an assignment
    (`NAME = …`, `NAME: T = …`) that STARTS a line.
    """
    if symbol.startswith("def "):
        return re.compile(rf"^\s*{re.escape(symbol)}\b", re.M)
    return re.compile(rf"^\s*{re.escape(symbol)}\s*(?::[^=\n]*)?=", re.M)


def check_lane_families(doc: str) -> list[str]:
    """§3/§4/: every backticked path under the lane and hotspot tables resolves.

    Scoped to the two tables that declare families (`## 3.` and `## 4.`) — the
    rest of the document names paths in prose, and a prose path is not a
    contract about a file's existence.
    """
    problems: list[str] = []
    section = _section(doc, "## 3.")
    section += _section(doc, "## 4.")
    if not section:
        return ["§3/§4 (lane + hotspot tables) not found in the document"]

    seen: set[str] = set()
    for raw in _BACKTICK_PATH.findall(section):
        path = raw.split(":")[0].rstrip("/")
        if (
            not path
            or path in seen
            or path.endswith((".md", ".yml"))
            or "*" in path
        ):
            # `.md`/`.yml` are cited as documents/registries, not as code
            # families; a GLOB (`tests/*.py`) is a rule about a directory, not a
            # family path. `docs/00_index.md`'s REGISTRATION is checked by (4)
            # instead — and nothing here checks that a cited `.yml` exists at all,
            # because the document cites it as a registry it DESCRIBES. (An
            # earlier version of this comment claimed `config/ci-surfaces.yml` was
            # "checked by (4) instead"; (4) is the index registration, so the
            # comment taught a check that does not exist — #5086 review P3.)
            continue
        seen.add(path)
        if not (ROOT / path).exists():
            problems.append(f"lane/hotspot family does not resolve: {path}")
    if not seen:
        problems.append("§3/§4 declare no code file families — the table shape changed")
    return problems


def check_interface_symbols(doc: str) -> list[str]:
    """§5: each placed symbol is declared in its file, and still named in the doc."""
    problems: list[str] = []
    for symbol, rel in INTERFACE_SYMBOLS:
        name = symbol.removeprefix("def ")
        if name not in doc:
            problems.append(f"interface symbol dropped from the document: {name}")
        target = ROOT / rel
        if not target.exists():
            problems.append(f"interface file does not resolve: {rel}")
            continue
        if not _declaration_re(symbol).search(_read(target)):
            problems.append(f"{name} declaration missing from {rel}")
    return problems


def check_registered(doc_text: str) -> list[str]:
    """The document is reachable from the docs index (an unindexed doc is unfound)."""
    if not INDEX.exists():
        return [f"{INDEX.relative_to(ROOT)} does not exist"]
    if DOC.name not in _read(INDEX):
        return [f"{DOC.relative_to(ROOT)} is not registered in docs/00_index.md"]
    return []


def _is_shallow_clone() -> bool | None:
    """Whether this checkout is shallow; `None` when git cannot say.

    Local and cheap (no network, no lazy fetch) — called only on the path where
    the ancestry probe already failed to resolve the object, to say WHY in the
    report instead of blaming the document.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=ANCESTRY_TIMEOUT_S,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.TimeoutExpired, OSError):  # pragma: no cover - defensive
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() == "true"


def check_measured_at_commit(doc: str) -> tuple[list[str], list[str]]:
    """`measured_at_commit` is an ancestor of HEAD — the map names its tree.

    Returns `(problems, unverified)`. A problem means the document and the tree
    disagree; *unverified* means THIS checkout cannot answer the question (the
    object is absent — a shallow or partial clone), which is a state error and
    never a drift: the document may be perfectly correct.

    ⛔ **Bounded on purpose.** `git merge-base --is-ancestor` is ~7 ms for an
    object that is present locally, but it attempts a lazy fetch for one that is
    not: on this box's partial clone an unknown sha took **24 s** and a network
    round trip to the promisor remote (measured, `GIT_NO_LAZY_FETCH=1` does not
    avoid it — the cost is CPU in ref enumeration, not the socket). An unbounded
    ancestry probe inside a CI gate is a hang waiting for a typo, so a timeout is
    a PROBLEM (the map names a tree nobody can verify), never a pass.
    """
    match = _FRONTMATTER_SHA.search(doc)
    if not match:
        return (["frontmatter carries no `measured_at_commit: <sha>`"], [])
    sha = match.group(1)
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=ANCESTRY_TIMEOUT_S,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
    except subprocess.TimeoutExpired:
        return (
            [
                f"measured_at_commit {sha} could not be verified within "
                f"{ANCESTRY_TIMEOUT_S}s (git is trying to fetch an object it does not have)"
            ],
            [],
        )
    except OSError as exc:  # pragma: no cover - git absent is a state error
        raise _state_error(f"git unavailable ({exc})") from exc
    if proc.returncode == 1:
        return ([f"measured_at_commit {sha} is not an ancestor of HEAD"], [])
    if proc.returncode != 0:
        # 128 = git does not HAVE the object. That says nothing about the
        # document: `actions/checkout` fetches one commit by default, so in the
        # `test` job this is the normal state of a perfectly correct map. Scoring
        # it as drift handed the reader the wrong instruction — "the document and
        # the tree disagree, do not delete the row to make this green" — so it is
        # reported as the state error it is: unverified, exit 2, never a pass
        # (#5086 review P1-1, P2-2).
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        why = detail[0] if detail else "git gave no detail"
        shallow = _is_shallow_clone()
        if shallow:
            hint = (
                "this checkout is SHALLOW (depth 1), so the object is absent by "
                "construction — the REQUIRED `docs` job runs this check at "
                "fetch-depth: 0, and that is where the claim is proved"
            )
        elif shallow is False:
            hint = (
                "this clone is not shallow but does not carry the object (a "
                "partial clone, or a sha that does not exist at all)"
            )
        else:  # pragma: no cover - git answered the ancestry probe but not this
            hint = "git could not say whether this checkout is shallow"
        return ([], [f"measured_at_commit {sha} cannot be verified here: {why} ({hint})"])
    return ([], [])


def _section(doc: str, marker: str) -> str:
    """The text from `marker` up to the next `## ` heading at column 0."""
    start = doc.find(marker)
    if start < 0:
        return ""
    rest = doc[start + len(marker):]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def measure() -> dict:
    """Read the claims; never raise for a drift (only for an unreadable input)."""
    if not DOC.exists():
        raise _state_error(f"{DOC.relative_to(ROOT)} does not exist")
    doc = _read(DOC)
    problems = (
        check_lane_families(doc)
        + check_interface_symbols(doc)
        + check_registered(doc)
    )
    ancestry_problems, unverified = check_measured_at_commit(doc)
    problems += ancestry_problems
    return {
        "document": str(DOC.relative_to(ROOT)),
        "checked": [
            "lane_and_hotspot_file_families",
            "interface_symbols",
            "docs_index_registration",
            "measured_at_commit_ancestry",
        ],
        "not_checked": [
            "issue_state (tracker — network; §1 records the command and a dated result)",
        ],
        "problems": problems,
        #: Claims this checkout cannot adjudicate. Non-empty => UNVERIFIED, and
        #: `main()` exits 2 — a state error, deliberately not a pass.
        "unverified": unverified,
        "verdict": (
            "DRIFTED" if problems else ("UNVERIFIED" if unverified else "CLEAN")
        ),
    }


def main(argv: list[str]) -> int:
    args = argv[1:]
    unknown = [a for a in args if a not in ("--check", "--json")]
    if unknown:
        print(f"usage: {Path(__file__).name} [--check|--json]", file=sys.stderr)
        return 2

    result = measure()

    if "--json" in args:
        print(json.dumps(result, indent=2))
    else:
        print(f"substrate map: {result['verdict']} ({result['document']})")
        for problem in result["problems"]:
            print(f"  ✗ {problem}")
        for entry in result.get("unverified", []):
            print(f"  ? {entry}")
        if result["problems"]:
            print(
                "\nThe document and the tree disagree. Re-measure the claim and correct "
                "whichever of the two is wrong — do not delete the row to make this green."
            )
    if result["problems"]:
        return 1
    # UNVERIFIED is a state error (2), never a pass: something this run needed was
    # not readable — or not present — HERE.
    return 2 if result.get("unverified") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
