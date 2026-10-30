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
  5. every §5 row whose `depends on` cell says `*leaf*` really imports nothing under
     `tortoise/` at MODULE level (a lazy in-function import does not count: it is
     how `source_identity` and `quota` deliberately break their cycles). A
     **module-scope** block body counts as module level — `try:` / `if:`
     (`else:` included) / `with:` / `for:` / `while:` / `match:` / `class` —
     because it still executes at import time, so a `try: from . import x` or a
     class-body import creates exactly the cycle the cell denies; only the BODY
     of `if TYPE_CHECKING:` (which does not run) and in-function imports are
     exempt;
  6. the document's `measured_at_commit` is an ancestor of `HEAD` — the map names
     the tree it was cut from (`tools/finding_provenance.py`'s rule, applied to a
     doc rather than to a finding). ⚠️ **(6) is the one claim that needs history.**
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
excuses nothing, it just moves the proof to the job that has the history. A
pre-3.12 interpreter is that same case and exits 2 as well (never 1, which is
reserved for drift).

Pinned by `tests/test_substrate_map.py`.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
#
# ⛔ `print` + `SystemExit(2)`, not `raise SystemExit(<message>)`: CPython exits
# **1** for a string argument, and 1 is this tool's DRIFT code — on the Apple CLT
# `python3` (3.9.6) that #5128 was filed from, `--check` would report *the
# document and the tree disagree* about a document it never read. An old
# interpreter is the same class as a missing object: this checkout cannot answer
# the question, so it is exit 2. The #5128 test explicitly accepts this shape
# for a guard whose exit code is itself meaningful (`SystemExit(<int>)` carries
# no message, so the message is printed; the refusal is still attributable).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/substrate_map_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/substrate_map_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import ast
import json
import os
import re
import subprocess
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
#: WIDENED off a five-prefix allowlist (#5086 review P3, cycle 7): a family
#: under any other top-level directory (`battery/`, `graph-scripts/`,
#: `integrations/`, `operations/`, `packs/`, `supabase/`, `validation/` all exist
#: in this repo) was not in the match at all, so it never reached the existence
#: test AND never entered `seen` — a lane row could name a family nobody checks
#: while the guard reported CLEAN. A path this cannot adjudicate is drift, never
#: silence, which is what matching every `dir/` prefix buys.
_BACKTICK_PATH = re.compile(r"`((?:[\w.-]+/)[^`\s]*)`")


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


#: §5's `depends on` cell for a module that imports nothing under `tortoise/`.
_LEAF_CELL = "*leaf*"

#: Module-scope statements whose BODIES still execute at import time. An import
#: wrapped in one of these is module level for the `*leaf*` claim: `try: from
#: .security import redact_error / except ImportError: ...` is neither lazily
#: deferred nor cycle-free, so reading only `tree.body`'s direct children let the
#: exact construct below (`quota.py`'s in-function `try`) pass at module scope
#: (#5086 review P3, cycle 6). `ast.TryStar` (`except*`) executes identically.
_IMPORT_TIME_BLOCKS: tuple[type, ...] = (ast.Try, ast.TryStar, ast.With)

#: Module-scope statements whose body is a BLOCK that runs at import time, one
#: iteration/case at a time. A `for:`/`while:`/`match:` body is executed when the
#: module is imported, so an internal import inside it creates the same cycle a
#: column-0 import does (#5086 review P3, cycle 7).
_IMPORT_TIME_LOOP_BLOCKS: tuple[type, ...] = (ast.For, ast.AsyncFor, ast.While)


def _is_type_checking_guard(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` — the one exempt block.

    Its body does NOT run at import time, so it creates no runtime cycle — which
    is the property the `*leaf*` cell is about.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _walk_module_scope(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement that RUNS at import time, descending into block bodies.

    Descends into `try`/`with` (always), into the `for`/`while`/`match` bodies
    (they are blocks executed at import time — the language reference §4.1 is
    explicit that a block is "a piece of Python program text that is executed as
    a unit"), and into `class` bodies, which are blocks too: a class-body import
    runs when the module is imported and creates exactly the cycle the `*leaf*`
    cell denies (#5086 review P3, cycle 7). `async with`/`async for` cannot occur
    at module scope (they are a syntax error outside a coroutine), so they need
    no arm here.

    `def`/`async def` bodies are NOT descended into: those are the lazy-import
    case the `*leaf*` claim explicitly allows, and are how `source_identity` and
    `quota` deliberately break their cycles.

    For the `TYPE_CHECKING` guard only the BODY is exempt (it does not run); its
    `else:` arm runs, so it is still walked — skipping the whole `ast.If` dropped
    a real import while its own justification was that the body does not run.
    """
    found: list[ast.stmt] = []
    for node in body:
        if isinstance(node, _IMPORT_TIME_BLOCKS):
            found += _walk_module_scope(node.body)
            for handler in getattr(node, "handlers", ()):
                found += _walk_module_scope(handler.body)
            found += _walk_module_scope(getattr(node, "orelse", []))
            found += _walk_module_scope(getattr(node, "finalbody", []))
        elif isinstance(node, _IMPORT_TIME_LOOP_BLOCKS):
            found += _walk_module_scope(node.body)
            found += _walk_module_scope(getattr(node, "orelse", []))
        elif isinstance(node, ast.Match):
            for case in node.cases:
                found += _walk_module_scope(case.body)
        elif isinstance(node, ast.If):
            if not _is_type_checking_guard(node.test):
                found += _walk_module_scope(node.body)
            found += _walk_module_scope(node.orelse)
        elif isinstance(node, ast.ClassDef):
            found += _walk_module_scope(node.body)
        else:
            found.append(node)
    return found


def _module_level_internal_imports(path: Path) -> list[str]:
    """Internal imports that EXECUTE at module level in `path`.

    Module level is the level the `*leaf*` claim is about: a module-level cycle is
    what serializes two lanes, while a lazy in-function import (`source_identity.py`'s
    one, `quota.py`'s eleven) breaks the cycle deliberately and does not. A
    `TYPE_CHECKING`-guarded import does not run, so it creates no runtime cycle
    either — the property at stake.

    "Module level" therefore means "executes at import time", not "is a direct
    child of the module body": a module-scope `try:`/`if:`/`with:`/`for:`/
    `while:`/`match:`/`class` body runs too, so a wrapped internal import is
    reported (#5086 review P3, cycles 6-7).
    """
    tree = ast.parse(_read(path))
    found: list[str] = []
    for node in _walk_module_scope(tree.body):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = "." * node.level + (node.module or "")
                if node.module is None:
                    # `from . import value_gate` carries the name in the ALIAS, so
                    # reporting the bare module stem would render as `.` — an
                    # operator needs the name that creates the edge.
                    found += [f"{base}{alias.name}" for alias in node.names]
                else:
                    found.append(base)
            elif node.module and node.module.split(".")[0] == "tortoise":
                found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [
                alias.name for alias in node.names if alias.name.split(".")[0] == "tortoise"
            ]
    return sorted(set(found))


def check_leaf_rows(doc: str) -> list[str]:
    """§5: a `*leaf*` `depends on` cell really means "imports nothing internal".

    Every other row of the interface table lists the modules it depends on, so the
    `*leaf*` cells are the only ones asserting EMPTINESS — and emptiness is the claim
    a lane plans a rebase on. Shipped wrong once: the `vet_gate.py` row said `*leaf*`
    while that file opens with a module-level `from . import value_gate`
    (`vet_gate.py:149`), a defect the inbound-count fix beside it did not reach
    (#5086 review cycle 5, P2).
    """
    section = _section(doc, "## 5.")
    if not section:
        return ["§5 (the interface map) not found in the document"]
    problems: list[str] = []
    leaves = 0
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[1] != _LEAF_CELL:
            continue
        names = re.findall(r"`([^`]+)`", cells[0])
        if not names:
            problems.append(f"§5 leaf row names no module: {line.strip()}")
            continue
        for name in names:
            target = ROOT / "tortoise" / name
            if not target.exists():
                problems.append(f"§5 leaf row names a file that does not resolve: tortoise/{name}")
                continue
            imports = _module_level_internal_imports(target)
            if imports:
                problems.append(
                    f"§5 calls tortoise/{name} a *leaf*, but it imports at module "
                    f"level: {', '.join(imports)}"
                )
            leaves += 1
    if not leaves:
        problems.append(
            "§5 carries no `*leaf*` depends-on row — the table shape changed, so this "
            "check is measuring nothing"
        )
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
    ancestry probe inside a CI gate is a hang waiting for a typo, so it is
    **bounded** — and the bound is classified with the status arm, not against
    it: a timeout is the 128 condition arriving slowly (the same absent object,
    the same lazy fetch), so it is UNVERIFIED, exit 2, never a pass and never
    drift (#5086 review P2, cycle 4).

    The split keys on git's own STATUS, which is what the platform guarantees:
    `--is-ancestor` *"exit[s] with status 0 if true, or with status 1 if not.
    Errors are signaled by a non-zero status that is not 1."* A timeout is not a
    git status at all, so it cannot be put in the status-1 (drift) channel — an
    operator told "the document and the tree disagree" on a slow clone is handed
    a wrong instruction about a correct document.
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
        # Not drift: the claim was never adjudicated. Same cause as the status-128
        # arm below, arriving slowly — see this function's docstring.
        #
        # ⛔ ONE reporting shape across both unverified arms: the token
        # "cannot be verified here" is what a caller (and the CLI's stdout) is
        # told to look for, so the timeout arm prefixes it too rather than
        # leaving two wordings for one verdict — the integration tests asserted
        # only the 128 wording and therefore FAILED (not skipped) the moment the
        # bound tripped (#5086 review P2, cycle 6).
        return (
            [],
            [
                f"measured_at_commit {sha} cannot be verified here: could not be "
                f"verified within {ANCESTRY_TIMEOUT_S}s (git is trying to fetch an "
                "object it does not have, so this checkout cannot answer the claim)"
            ],
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
        + check_leaf_rows(doc)
        + check_registered(doc)
    )
    ancestry_problems, unverified = check_measured_at_commit(doc)
    problems += ancestry_problems
    return {
        "document": str(DOC.relative_to(ROOT)),
        "checked": [
            "lane_and_hotspot_file_families",
            "interface_symbols",
            "interface_leaf_rows",
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
