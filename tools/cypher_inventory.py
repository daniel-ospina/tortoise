#!/usr/bin/env python3
"""cypher-inventory — the Cypher surface of the PRODUCT, enumerated by SHAPE (#13098).

WHY THIS EXISTS
===============
`#13082` sequences a Cypher→Postgres migration by *shape*, not by file, and
`#13098` asks for the census that makes the sequence safe. Four counts of this
surface were already published and they do not agree:

| count | unit | method |
|---|---|---|
| `1,074` sites / `57` files, `697` anchored + `377` whole-graph | read sites | **unpublished** (`#7879`, `#7970`) |
| `1,525` literals / `89` files | string literals | a re-derivation attempt — a DIFFERENT unit |
| `458` / `50` files | mutating `.query(` sites | one regex scan (`#12894` D-01) |
| `3,015` / `123` files | "Cypher statements" | cited in `#13082`/`#13098`, **no published method** |

`docs/epics/2026-10-10-7879-postgres-record/01-test-design.md:71` already warns
that the first two **must not be compared or summed**. So this tool does not
adopt any of them: it re-measures with a method that is in the box, and every
count it prints names the predicate that produced it — including the `rg`-style
totals the doc reconciles against.

THE UNIT, DECLARED
==================
A **statement site** is a `file:line` where a Cypher statement is handed to a
query door (`<anything>.query(...)`, `.ro_query(...)`, `.execute(...)`) **in the
product surface** — `tortoise/**/*.py`. Tests, docs, `graph-scripts/`,
`archive/`, `eval/`, `battery/` are NOT the product surface: they are counted
separately (`--all-surface`) and never folded into the product number.

⛔ WHAT THIS TOOL CANNOT SEE, stated rather than implied (the `#9124` lesson —
a census that hides its blind spots reads as complete):

* a statement built by a helper (`query=_build(x)`) or a loop variable is
  returned as `unresolved` and COUNTED, never dropped — the classifier's
  polarity is "not provably resolvable is unresolved". The doc's reconciliation
  section prints that number.
* a statement passed through a variable reassigned in a branch resolves to the
  FIRST binding seen in the function; the classifier may then call it by the
  wrong shape. It is never silently mis-shaped as `plain` — an unresolvable
  binding resolves to `unresolved`.
* `.query(` calls whose statement is not Cypher at all (a Postgres `execute`,
  a driver call) are counted separately as `non-cypher door calls` and are
  excluded from every shape total.

SHAPE (precedence order, first match wins — the order encodes "what the
statement NEEDS", which is the issue's question)
================================================
`ddl` → `fulltext` → `vector` → `traversal` → `adjacency` → `ep` → `plain`

⛔ `ddl` is FIRST because a statement that CREATES a fulltext or vector index names
one (`CALL db.idx.fulltext.createNodeIndex(…)`), so the DDL arm has to claim it
before the `fulltext`/`vector` arms see the name — while a `queryNodes`/`query`
QUERY matches no DDL arm and falls through to `fulltext`/`vector`. The earlier
telling of this list put `fulltext` and `vector` ahead of `ddl`, which was the
reverse of what `classify()` does and of what `test_shape_precedence` pins
(`#13098` review F4).

* `ddl` — index/constraint creation and index listing.
* `fulltext` — `db.idx.fulltext.*`.
* `vector` — `db.idx.vector.*` (the move §3 already decided).
* `traversal` — a VARIABLE-LENGTH relationship (`-[…*1..2]`, `[*]`, `*..`) or
  `shortestPath`: needs a walk.
* `adjacency` — an edge pattern WITHOUT variable length: a pattern match over
  adjacency. `#13098` calls this "case by case, measured" and §3 keeps the
  multi-hop shapes, so the verdict is `measure`, never a flip.
* `ep` — the epistemic scalars (`ep_*`, `posterior_*`, `baseline_*`,
  `confidence`): recomputed in Python over a closed component (§6.3).
* `plain` — no edge pattern and none of the above: point lookups, listings,
  filters, aggregates, ordered reads. **These are SQL-shaped.**

ROLE separates a read from a write, because the verdict is shape × role: a
`plain` READ is a migration candidate; a `plain` WRITE maintains the derived
copy and stays until `#13083` makes the shape `record_only`.

VERDICT is a function of shape × role and encodes the recorded decisions in
`docs/architecture/SEARCH-ARCHITECTURE.md` §3 — it does not re-decide them:

* traversal / adjacency / fulltext / ep / ddl / graph writes → **stays** (or
  `measure` for adjacency), each naming the section that keeps it.
* plain read → **candidate**.
* `vector` → **moved** (§3's one decided move).

USAGE
=====
    uv run python tools/cypher_inventory.py                # summary + reconciliation
    uv run python tools/cypher_inventory.py --markdown     # the appendix table
    uv run python tools/cypher_inventory.py --json         # machine form
    uv run python tools/cypher_inventory.py --check        # exit 1 on pin drift (reads the SOURCE)
    uv run python tools/cypher_inventory.py --render       # write the document (source + appendix)
    uv run python tools/cypher_inventory.py --all-surface  # every surface, not just the product

⛔ THE DOCUMENT IS GENERATED, NOT COMMITTED. `docs/architecture/CYPHER-INVENTORY.src.md`
is the tracked source (prose + the 25 pins); `docs/architecture/CYPHER-INVENTORY.md` is
`--render`'s output — Appendix A, one `file:line` row per classified statement
site, so its length moves with the tree — restamps on any commit that moves a
statement, so committing it made every such PR carry its own copy and conflict
with every other one (#13098). ⛔ No row or line count is typed here: the
appendix's own length is the number, and a copy of it in prose rots.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import raises ImportError before a later guard could run.
#
# #4053/#11342: the refusal must not wear a VERDICT's status. This tool's
# answers are 0 (census produced) and 1 (`--check` reports doc drift), and
# `raise SystemExit(<message>)` exits 1 — so an old interpreter would report
# DRIFT against a doc nobody touched. 2 is this tool's own "could not run"
# (`check()` already returns it when the doc is absent), which is exactly what an
# old interpreter is, so the refusal is reused on purpose rather than invented.
# `SystemExit(<int>)` carries no message, so the refusal is printed explicitly.
# Pinned by `tests/test_entry_point_python_guard.py::REFUSAL_CODES`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/cypher_inventory.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/cypher_inventory.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import ast
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The hand-authored SOURCE — the prose and the 25 pins. This is the file git
#: tracks, and the file `--check` reads.
DOC_SRC = ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.src.md"

#: The rendered DOCUMENT — `DOC_SRC` plus this run's appendix table. It is a
#: BUILD ARTIFACT and is NOT tracked: the appendix carries one `file:line` row
#: per statement site, so committing it made every product commit that moved a
#: statement restamp every later row, and every open PR then carried its own
#: whole-census copy of it, stamped against a different tree. Measured
#: 2026-11-24 at `939e37f664`: it appeared in 8 of 11 open PRs and in 3 of the
#: last 20 commits on `main` (reproducible: `git rev-list -20 main` + a
#: `diff-tree` per commit), and it was in 6 of 6 then-conflicting PRs.
DOC_OUT = ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md"

#: A source that carries a census row: the defect this split exists to make
#: impossible (someone pastes the appendix back into the tracked file, and the
#: conflict engine returns). `--render` and `--check` both refuse it.
#:
#: ⛔ IT MATCHES THE GENERATED ROW, NOT "ANY TABLE THAT STARTS WITH A `file:line`".
#: The broad `^\| `file:line` \|` matched a TWO-cell hand-authored prose row as
#: readily as a six-cell generated one, so a source whose first table column held
#: a site address was refused by `--render` (exit 2, "Delete the rows") and read by
#: `--check` as DRIFT (exit 1) — with correct pins. The remedy the first message
#: prescribes is then to delete CORRECT PROSE, and `cypher-diff.yml` reads that 1 as
#: "the doc's numbers are wrong" (#13098 review, P2). The appended table's own
#: first five cells (`markdown_table`) are the shape nothing but the generator
#: emits, and they still match a pasted appendix — measured against the tracked
#: document on `main` BEFORE this split, at `a29b5f24ce` (`git show
#: a29b5f24ce:docs/architecture/CYPHER-INVENTORY.md` — that path leaves `main` with
#: this change, so the command is stamped to the sha it was measured at): every one
#: of its appendix rows matched, and no prose row did. ⛔ No row count is typed
#: here: the appendix's own length is the number.
#:
#: ⚠️ A CHANGE TO `markdown_table`'s COLUMNS MUST RE-CUT THIS PATTERN: the arm
#: `test_a_hand_authored_prose_row_is_not_read_as_a_census_row` grades the pattern
#: against `markdown_table`'s own output, so a generator change reds there instead
#: of leaving the guard silently blind.
CENSUS_ROW = re.compile(
    r"^\| `[^`]+:\d+` \| `[^`]*` \| [a-z_]+ \| [a-z_]+ \|", re.MULTILINE)

#: Files whose statements are the PRODUCT's. Everything else is measured, never
#: folded in — the census unit is named above.
PRODUCT_PREFIX = "tortoise/"

#: Other surfaces, measured separately. Order is presentation only.
OTHER_SURFACES = (
    "tests/",
    "graph-scripts/",
    "tools/",
    "battery/",
    "eval/",
    "integrations/",
    "apps/",
    "archive/",
    "benchmarks/",
    "docs/",
)

#: A query door. `execute` is included because the driver exposes it; a call
#: whose text is not Cypher is excluded from shape totals (see module docstring).
DOORS = frozenset({"query", "ro_query", "execute"})

#: Anything that makes a string "look like" a Cypher statement. `WITH` is a
#: Cypher clause AND a Python keyword — it is matched only at line start, which
#: is why the alternation is anchored.
CYPHER_MARK = re.compile(
    r"(?:\b(?:MATCH|MERGE|UNWIND|CALL|OPTIONAL\s+MATCH|DETACH|RETURN)\b)"
    r"|(?:^\s*WITH\s)"
    r"|(?:^\s*(?:CREATE|DROP|SET|DELETE|REMOVE)\b(?!\s+(?:TABLE|SCHEMA|ROLE|EXTENSION|VIEW|"
    r"FUNCTION|TRIGGER|POLICY|SEQUENCE|TYPE|DATABASE|USER|INDEX\s+ON|OR\s+REPLACE)\b))",
    re.IGNORECASE | re.MULTILINE,
)

#: A statement that carries SQL-only syntax and NO node/relationship pattern is
#: SQL handed to a shared `execute` door — not Cypher. Without this veto the
#: `WITH …` arm of CYPHER_MARK admitted a psycopg2 CTE
#: (`record_allocator.py:406`, `WITH observed AS (SELECT …) INSERT INTO public.…`)
#: and counted it as a `plain` migration candidate (`#13098` review).
SQL_VETO = re.compile(r"\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|VALUES\s*\(|JOIN)\b",
                      re.IGNORECASE)
#: What makes a statement provably graph-shaped: a node pattern, a relationship
#: pattern, or a Cypher-only clause.
NODE_PATTERN = re.compile(r"\(\s*`?\w*`?\s*:|<-\[|-\[|\[\w*\s*:\s*\w+\]")

VAR_LENGTH = re.compile(r"-\[[^\]]*\*[^\]]*\]-|<-\[[^\]]*\*[^\]]*\]-|shortestPath\s*\(", re.IGNORECASE)
#: A relationship pattern. ⛔ THE BRACKETLESS SPELLINGS ARE THE SECOND ARM.
#: Cypher accepts `-->`, `<--` and a bare `--` between two node patterns, and the
#: bracketed-only arm could not see any of them — so `MATCH (a)-->(b) RETURN b.id`
#: fell through every shape arm to `plain`/`read` and was published as a migration
#: CANDIDATE: the one direction the verdict flips (`#13098` review P3). There IS
#: one live site in this form — `tortoise/domain_validators.py:240`'s
#: `AND NOT (n)--()`, a real adjacency predicate — so this arm MOVED the census:
#: `adjacency`/`read` 189 → 190 and `plain`/`read` 571 → 570, totals unmoved. The
#: pins reflect it; `--check` proves below that nothing else followed.
#:
#: ⛔ IT IS CONTEXT-ANCHORED ON PURPOSE, and it is NOT added to `NODE_PATTERN`.
#:
#:  * `--` is ALSO SQL's comment introducer. A blanket `--` arm calls
#:    `SELECT * FROM public.point -- note` an `adjacency` statement. That is only
#:    harmless because `SQL_VETO` runs first in `enumerate_file` — so a rule that
#:    is correct ONLY because of a caller's ordering is a latent false candidate,
#:    which is the exact class this fix exists to remove. An arrowhead (`-->`,
#:    `<--`) is unambiguous, and a bare `--` counts only BETWEEN two node patterns
#:    (`)--(`, `]--[`), which no SQL statement's comment sits between.
#:  * `NODE_PATTERN`'s ONE caller is that SQL veto, so widening it with `--` would
#:    stop a SQL statement carrying a comment from being vetoed at all: the fix for
#:    a false candidate would create one.
EDGE_PATTERN = re.compile(
    r"(?:<)?-\[[^\]]*\]-?"          # bracketed: -[..]->, <-[..]-
    r"|<--+>?"                       # <-- and <-->
    r"|--+>"                         # -->
    r"|\)\s*-{2,}\s*\("              # bare -- between two node patterns
    r"|\]\s*-{2,}\s*\["              # …and between two list/bracket patterns
)
FULLTEXT = re.compile(r"db\.idx\.fulltext|fulltext\.query", re.IGNORECASE)
VECTOR = re.compile(r"db\.idx\.vector|vector\.query", re.IGNORECASE)
DDL = re.compile(
    # Only index/constraint CREATION or DROPPING is DDL. ⛔ The first version of
    # this pattern also matched `CALL db.idx.<anything>`, which swallowed every
    # fulltext and vector QUERY into `ddl` — a whole class silently re-labelled.
    r"^\s*(?:CREATE|DROP)\s+(?:INDEX|CONSTRAINT|FULLTEXT|VECTOR)"
    r"|^\s*CALL\s+db\.(?:indexes|constraints|awaitIndexes)"
    r"|^\s*CALL\s+db\.idx\.(?:fulltext|vector)\.(?:create|drop)",
    re.IGNORECASE | re.MULTILINE,
)
#: A `CALL` whose procedure name the TEXT CANNOT PROVE — folded by the resolver
#: (`CALL {}('Point')`, `CALL ???('Point')`) or outside the engine's own `db.*`
#: namespace (`CALL apoc.…`), where read-vs-write is the plugin's business and not
#: visible in the statement. ⛔ The live defect this closes (`#13098` review P1):
#: `tortoise/projection/__init__.py` loops over
#: `("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex")` and calls
#: `CALL {drop_proc}('Point')`, so the resolver folded the NAME — and a fulltext-index
#: DROP was classified `plain`/`read` and published as a migration CANDIDATE, the one
#: direction a verdict flips, inside the headline count. Its un-folded spellings both
#: classify `ddl`/`ddl`, which is what proves the loss was in the folding, not in DDL.
#: `db.*` procedures stay classifiable: that is how fulltext/vector READS reach their
#: own shapes, and a blanket "any CALL is unknown" would swallow them.
CALL_PROC_UNPROVABLE = re.compile(
    r"\bCALL\s+(?:\{\}|\?\?\?|(?!db\.)[\w.]+\s*\()",
    re.IGNORECASE,
)

EP_FIELD = re.compile(
    r"\b(?:ep_alpha|ep_beta|ep_[a-z_]+|posterior_[a-z_]+|baseline_(?:alpha|beta|source)|confidence)\b"
)
WRITE_VERB = re.compile(
    r"^\s*(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP)\b",
    re.IGNORECASE | re.MULTILINE,
)
#: A write clause that is part of `MATCH … SET` counts; the regex above is
#: anchored to a line start, and statements are multi-line, so an inline write
#: needs this second arm.
#:
#: ⛔ EVERY Cypher write clause is listed, not only the dotted `SET n.x = 1` the
#: first version handled. That version missed four shapes a real statement uses,
#: and each miss turned a graph WRITE into a migration CANDIDATE — the one
#: direction the verdict flips, so the headline number was inflated by exactly
#: these (`#13098` review P2):
#:   `SET n += $props` / `SET n = $props` / `SET n:Label`  → the target is
#:       followed by an operator, not a `.`/`[`;
#:   `SET {}` / `SET ???`                                → the SET target was
#:       folded out of the literal, so nothing follows the keyword;
#:   `MATCH … MERGE (a)-[:R]->(b)` / `MATCH … CREATE (…)` → the verb is inline,
#:       never at a line start, so the `^`-anchored arm above cannot see it;
#:   `MATCH … REMOVE {}`                                 → the same folded form.
WRITE_INLINE = re.compile(
    r"\bSET\s+(?:(?:`[^`]*`|[\w]+)\s*(?:[.\[]|\+=|=|:)|[{\[]|\?\?\?)"
    r"|\b(?:MERGE|CREATE)\s*\("
    r"|\bDETACH\s+DELETE\b"
    r"|\b(?:DELETE|REMOVE|DROP)\s+(?:(?:`[^`]*`|[\w]+)|[{\[]|\?\?\?)",
    re.IGNORECASE,
)

SHAPES = ("fulltext", "vector", "ddl", "traversal", "adjacency", "ep", "plain", "unresolved")

#: verdict by (shape, role). `STAYS`/`MEASURE`/`CANDIDATE`/`MOVED`/`UNCLASSIFIED`.
VERDICTS = {
    ("fulltext", "read"): "stays:text-index (§6.2; #13099 measured, decision stands)",
    ("fulltext", "write"): "stays:graph-maintenance",
    ("vector", "read"): "moved:record-vector (§3, #11642)",
    ("vector", "write"): "stays:graph-maintenance",
    ("ddl", "ddl"): "stays:graph-maintenance",
    ("traversal", "read"): "stays:traversal (§3)",
    ("traversal", "write"): "stays:graph-maintenance",
    ("adjacency", "read"): "measure:across shapes (#13083 owns placement)",
    ("adjacency", "write"): "stays:graph-maintenance",
    ("ep", "read"): "stays:ep-carveout (§6.3)",
    ("ep", "write"): "stays:ep-carveout (§6.3)",
    ("plain", "read"): "candidate:SQL-shaped",
    ("plain", "write"): "stays:graph-maintenance (candidate when #13083 makes the shape record_only)",
    ("unresolved", "unresolved"): "unclassified:statement not resolvable statically",
}


#: A test module living INSIDE the product tree (`tortoise/test_cross_ontology.py`)
#: is not a statement the product executes. Excluded by name, counted, and the
#: count is printed — an exclusion a reader cannot see is an omission.
TEST_MODULE = re.compile(r"(?:^|/)test_[^/]*\.py$")


@dataclass(frozen=True)
class Site:
    """One statement site — the census unit."""

    path: str
    line: int
    function: str
    door: str
    shape: str
    role: str
    digest: str
    partial: bool = False

    @property
    def verdict(self) -> str:
        return VERDICTS.get((self.shape, self.role), "unclassified")

    def as_row(self) -> dict:
        return {
            "file:line": f"{self.path}:{self.line}",
            "function": self.function,
            "shape": self.shape,
            "role": self.role,
            "verdict": self.verdict,
            "statement": self.digest,
            "partial": self.partial,
        }


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #
def surface_files(prefixes: tuple[str, ...]) -> list[str]:
    """Tracked Python files under the given prefixes — bounded by the git index.

    `git ls-files` reads the index, so this never walks `.worktrees/`,
    `node_modules/` or a build tree: the search-guard rule and the load policy.
    """
    # ⛔ `-z` EMITS RAW FILENAME BYTES, so `text=True` (strict UTF-8) RAISES on a
    # tracked name that is not valid UTF-8 — a `UnicodeDecodeError`, which is a
    # `ValueError`, NOT an `OSError`, so it slips through both of `main()`'s guards and
    # exits 1 (the DRIFT code) on the DEFAULT verb and on `--check`. Measured in a
    # scratch repo with a tracked `caf\xe9.py`: bare verb and `--check` both rc 1.
    # `surrogateescape` round-trips those bytes, so `ROOT / relpath` still opens the
    # file and the census reads it like any other.
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *prefixes],
        cwd=ROOT, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="surrogateescape",
    ).stdout
    files = [p for p in out.split("\0") if p.endswith(".py")]
    return sorted(files)
def _resolve_text(node: ast.AST | None, consts: dict[str, str], depth: int = 0,
                  partial: list[bool] | None = None) -> str | None:
    """Best-effort static text of a statement expression.

    Returns None when NOTHING of the expression resolves. When only PART of it
    does (a `+`-concatenation with a local, a `.format` on a computed string),
    the resolvable parts are kept and the rest becomes `???`, and `partial[0]`
    is set — because the SHAPE lives in the literal half
    (`"MATCH (op:Point)-[:" + safe_rel + "]->(t)"` is an adjacency read, and
    dropping it would hide a whole class in the `unresolved` bucket).
    """
    if partial is None:
        partial = [False]
    if node is None or depth > 6:
        partial[0] = True
        return None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        partial[0] = True
        return None
    if isinstance(node, ast.JoinedStr):  # f-string
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = _resolve_text(value.value, consts, depth + 1)
                partial[0] = True  # an interpolation is never fully static text
                parts.append(inner if inner is not None else "{}")
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_text(node.left, consts, depth + 1, partial)
        right = _resolve_text(node.right, consts, depth + 1, partial)
        if left is None and right is None:
            return None
        return (left if left is not None else "???") + (right if right is not None else "???")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        # `"...%s..." % (x,)` — keep the literal, drop the substitution.
        left = _resolve_text(node.left, consts, depth + 1, partial)
        if left is None:
            return None
        partial[0] = partial[0] or bool(node.right)
        return re.sub(r"%[sdr]", "{}", left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        recv = _resolve_text(node.func.value, consts, depth + 1, partial)
        if recv is None:
            return None
        partial[0] = partial[0] or bool(node.args or node.keywords)
        return re.sub(r"\{[^}]*\}", "{}", recv)
    if isinstance(node, ast.Name):
        text = consts.get(node.id)
        if text is None:
            partial[0] = True
        return text
    partial[0] = True
    return None


def _module_consts(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = <resolvable string>` bindings, folded in order."""
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    text = _resolve_text(stmt.value, consts)
                    if text is not None:
                        consts[target.id] = text
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            text = _resolve_text(stmt.value, consts)
            if text is not None:
                consts[stmt.target.id] = text
    return consts


def _own_nodes(scope: ast.AST):
    """Yield nodes belonging to `scope` itself — never descending into a nested
    def/class/lambda.

    ⛔ This is what keeps the census a CENSUS: iterating `ast.walk` once per
    `FunctionDef` counts a nested function's door calls once per enclosing
    scope, which inflated this tool's own first run by 60 % (2,678 sites against
    1,609 door-call lines). One traversal, one count.
    """
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # ⛔ `ast.Lambda` is NOT a scope boundary here: `lambda: _sdk.query("MATCH …")`
        # is 52 real product statements (23 `asyncio.to_thread(lambda: …)` in
        # hosted_api, 29 in sdk.py), and stopping at the lambda dropped every one
        # of them while the reconciliation's residual line absorbed the loss (#13098 review).
        stack.extend(ast.iter_child_nodes(node))


def _local_consts(func: ast.AST, consts: dict[str, str]) -> dict[str, str]:
    """Function-local `NAME = <string>` bindings (first binding wins)."""
    local = dict(consts)
    for node in _own_nodes(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in local:
                    text = _resolve_text(node.value, consts)
                    if text is not None:
                        local[target.id] = text
    return local


def _is_door(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in DOORS:
        return func.attr
    return None


def _statement_node(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg in {"q", "query", "statement", "cypher"}:
            return kw.value
    return call.args[0] if call.args else None


def _digest(text: str, limit: int = 68) -> str:
    """One-line, pipe-safe digest: the statement's leading clause, whitespace folded."""
    flat = re.sub(r"\s+", " ", text).strip()
    flat = flat.replace("|", "\\|").replace("`", "'")
    return flat[:limit] + ("…" if len(flat) > limit else "")


def classify(text: str) -> tuple[str, str]:
    """(shape, role) for a resolved statement."""
    if CALL_PROC_UNPROVABLE.search(text):
        # The polarity the census already applies to an unresolvable STATEMENT
        # applies one level down to an unresolvable PROCEDURE: not provably a read
        # is not a candidate. Counted and named, never dropped (`#9124`).
        return "unresolved", "unresolved"
    if DDL.search(text):
        return "ddl", "ddl"
    if FULLTEXT.search(text):
        shape = "fulltext"
    elif VECTOR.search(text):
        shape = "vector"
    elif VAR_LENGTH.search(text):
        shape = "traversal"
    elif EDGE_PATTERN.search(text):
        shape = "adjacency"
    elif EP_FIELD.search(text):
        shape = "ep"
    else:
        shape = "plain"
    writes = bool(WRITE_VERB.search(text) or WRITE_INLINE.search(text))
    if shape == "ep" and not writes:
        return shape, "read"
    return shape, ("write" if writes else "read")


def enumerate_file(relpath: str) -> tuple[list[Site], int, int]:
    """(sites, non_cypher_door_calls, unparsed) for one file — ONE traversal."""
    try:
        source = (ROOT / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return [], 0, 1
    consts = _module_consts(tree)
    sites: list[Site] = []
    non_cypher = 0

    def walk(scope: ast.AST, prefix: str, scope_consts: dict[str, str]) -> None:
        nonlocal non_cypher
        for node in _own_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            door = _is_door(node)
            if door is None:
                continue
            partial = [False]
            text = _resolve_text(_statement_node(node), scope_consts, partial=partial)
            if text is None:
                # A door call we cannot resolve: counted as unresolved, never
                # dropped — the over-report polarity (#9124).
                sites.append(Site(relpath, node.lineno, prefix.rstrip("."), door,
                                  "unresolved", "unresolved", "<unresolved>"))
                continue
            if not CYPHER_MARK.search(text):
                non_cypher += 1
                continue
            if SQL_VETO.search(text) and not NODE_PATTERN.search(text):
                # SQL-only syntax with no node/relationship pattern: a Postgres
                # statement sharing the `execute` door, not a Cypher statement.
                non_cypher += 1
                continue
            shape, role = classify(text)
            sites.append(Site(relpath, node.lineno, prefix.rstrip("."), door,
                              shape, role, _digest(text), partial=partial[0]))

    def descend(scope: ast.AST, prefix: str, scope_consts: dict[str, str]) -> None:
        walk(scope, prefix, scope_consts)
        # Then each nested scope gets ITS OWN traversal, with its own bindings.
        for node in _own_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}."
                descend(node, name, _local_consts(node, scope_consts))

    descend(tree, "", consts)
    return sites, non_cypher, 0


def census(prefixes: tuple[str, ...], *, drop_test_modules: bool = True) -> dict:
    files = surface_files(prefixes)
    # ⛔ The test-module exclusion is a PRODUCT-surface rule (`tortoise/test_*.py`).
    # Applying it in `--all-surface` mode excluded 1,468 files under `tests/` —
    # i.e. it would have measured every surface except the largest one.
    excluded = [f for f in files if drop_test_modules and TEST_MODULE.search(f)]
    if drop_test_modules:
        files = [f for f in files if not TEST_MODULE.search(f)]
    sites: list[Site] = []
    non_cypher = 0
    unparsed = 0
    for rel in files:
        file_sites, file_non_cypher, file_unparsed = enumerate_file(rel)
        sites.extend(file_sites)
        non_cypher += file_non_cypher
        unparsed += file_unparsed
    return {
        "prefixes": list(prefixes),
        "files_scanned": len(files),
        "files_with_sites": len({s.path for s in sites}),
        "excluded_test_modules": len(excluded),
        "sites": sites,
        "non_cypher_door_calls": non_cypher,
        "unparsed_files": unparsed,
        "ast_door_calls": len(sites) + non_cypher,
        "partial": sum(1 for s in sites if s.partial),
    }


# --------------------------------------------------------------------------- #
# the rg-style predicates, computed in-process so the doc reconciles against
# numbers this tool can reproduce without a shell
# --------------------------------------------------------------------------- #
def rg_totals(prefixes: tuple[str, ...], *, drop_test_modules: bool = True) -> dict:
    """Line/file totals for the predicates the doc shows as `rg` equivalents.

    The reconciliation is MEASURED, not inferred: the occurrences of the door
    pattern that sit on a line carrying no door CALL are counted here (comments
    and docstrings), so the arithmetic  regex − non-call = AST calls  closes on
    numbers this tool computed rather than on a plausible remainder.
    """
    files = surface_files(prefixes)
    if drop_test_modules:
        files = [f for f in files if not TEST_MODULE.search(f)]
    verb_line = re.compile(r"\b(?:MATCH|MERGE)\s*\(", re.IGNORECASE)
    door_line = re.compile(r"\.(?:ro_)?(?:query|execute)\s*\(")
    totals = {
        "py_files": len(files),
        "lines_match_or_merge": 0,
        "files_match_or_merge": 0,
        "lines_door_calls": 0,
        "files_door_calls": 0,
        "door_occurrences_without_a_call": 0,
        "door_occurrences_on_call_lines": 0,
        "lines_where_regex_and_ast_disagree": 0,
    }
    for rel in files:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        m = verb_line.findall(text)
        occurrences = door_line.findall(text)
        if m:
            totals["lines_match_or_merge"] += len(m)
            totals["files_match_or_merge"] += 1
        if occurrences:
            totals["lines_door_calls"] += len(occurrences)
            totals["files_door_calls"] += 1
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        ast_by_line = Counter(
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_door(node)
        )
        call_lines = set(ast_by_line)
        for lineno, line in enumerate(text.splitlines(), start=1):
            occurrences_here = len(door_line.findall(line))
            if lineno in call_lines:
                totals["door_occurrences_on_call_lines"] += occurrences_here
                if occurrences_here != ast_by_line[lineno]:
                    totals["lines_where_regex_and_ast_disagree"] += 1
            else:
                totals["door_occurrences_without_a_call"] += occurrences_here
    return totals


def shape_matrix(sites: list[Site]) -> dict:
    matrix = defaultdict(Counter)
    for site in sites:
        matrix[site.shape][site.role] += 1
    out = {}
    for shape in SHAPES:
        counts = matrix.get(shape)
        if counts:
            out[shape] = dict(sorted(counts.items()))
    return out


def report(result: dict) -> str:
    sites: list[Site] = result["sites"]
    matrix = shape_matrix(sites)
    lines = []
    add = lines.append
    add("Cypher inventory — " + ("EVERY tracked surface" if result["prefixes"] == ["."]
                                 else "the PRODUCT surface (tortoise/**/*.py)"))
    add("=" * 68)
    add(f"files scanned            : {result['files_scanned']}")
    add(f"files with statement sites: {result['files_with_sites']}")
    add(f"statement sites (census)  : {len(sites)}")
    add(f"non-Cypher door calls     : {result['non_cypher_door_calls']} (excluded from shape totals)")
    add(f"partially resolved sites  : {result['partial']} (literal half kept; `???` marks the rest)")
    add(f"unparsed files            : {result['unparsed_files']}")
    add(f"excluded test modules     : {result['excluded_test_modules']} (tortoise/test_*.py)")
    add("")
    add("by shape × role")
    add("-" * 68)
    for shape, roles in matrix.items():
        total = sum(roles.values())
        detail = ", ".join(f"{r}={c}" for r, c in roles.items())
        add(f"  {shape:<12} {total:>5}   ({detail})")
    add("")
    add("by verdict")
    add("-" * 68)
    verdicts = Counter(s.verdict for s in sites)
    for verdict, count in verdicts.most_common():
        add(f"  {count:>5}  {verdict}")
    add("")
    add("top files by site count")
    add("-" * 68)
    for path, count in Counter(s.path for s in sites).most_common(15):
        add(f"  {count:>5}  {path}")
    add("")
    add("reconciliation predicates (the `rg` equivalents)")
    add("-" * 68)
    totals = rg_totals(tuple(result["prefixes"]),
                       drop_test_modules=result["excluded_test_modules"] > 0)
    add(f"  py files under surface                         : {totals['py_files']}")
    add(f"  occurrences of \\.(ro_)?(query|execute)\\(      : {totals['lines_door_calls']}"
        f"  across {totals['files_door_calls']} files")
    add(f"  lines matching \\b(MATCH|MERGE)\\s*\\(            : {totals['lines_match_or_merge']}"
        f"  across {totals['files_match_or_merge']} files")
    add(f"  statement sites (this census, resolved or not) : {len(sites)}")
    add("")
    add("  how the two reconcile")
    add(f"    regex door-pattern occurrences          {totals['lines_door_calls']}")
    add(f"    − occurrences on a line with no door CALL (comments/docstrings)"
        f"  {totals['door_occurrences_without_a_call']}")
    add(f"    = occurrences on a line WITH a door CALL"
        f"                 {totals['door_occurrences_on_call_lines']}")
    add(f"    − of those, the AST did not record (residual)"
        f"         {totals['door_occurrences_on_call_lines'] - result['ast_door_calls']}")
    add(f"    = AST door calls                        {result['ast_door_calls']}")
    add(f"    = classified Cypher {len(sites)} + non-Cypher {result['non_cypher_door_calls']}"
        f"  {len(sites) + result['non_cypher_door_calls']}")
    return "\n".join(lines)


def markdown_table(sites: list[Site]) -> str:
    lines = [
        "| file:line | fn | shape | role | verdict | statement |",
        "|---|---|---|---|---|---|",
    ]
    for site in sorted(sites, key=lambda s: (s.path, s.line)):
        lines.append(
            f"| `{site.path}:{site.line}` | `{site.function or '-'}` | {site.shape} | {site.role} "
            f"| {site.verdict} | `{site.digest}` |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# drift check — the doc's pinned counts are the gate
# --------------------------------------------------------------------------- #
PINNED = re.compile(r"<!-- cypher-inventory:(\w+) = (\d+) -->")


def render(src: Path, result: dict) -> str:
    """`src` (prose + pins) with this run's appendix appended — the document.

    ⛔ THE APPENDIX IS APPENDED, NEVER MERGED: a source that already carries a
    census row is REFUSED with exit 2 (this module's "could not run") rather than
    rendered, because rendering it would silently duplicate rows — and
    re-committing those rows is the whole defect this split removes. (2, never 1:
    1 is the DRIFT verdict. `--check` on a row-carrying file is a DIFFERENT
    verdict — 1, naming the rows — because a committed appendix is a census
    defect, not a tool that could not run.)

    ⛔ THE SEPARATOR IS INSERTED, not assumed: the source ends after the Appendix
    heading with a SINGLE newline (two would be a markdownlint MD012 finding on
    the required `docs` check), and this function supplies the blank line that
    separates the heading from the table.
    """
    if not src.exists():
        print(f"render: no source at {src}", file=sys.stderr)
        raise SystemExit(2)
    # ⛔ A SOURCE THAT CANNOT BE READ IS "COULD NOT RUN" (2), NOT A CRASH (1) — the
    # twin of `check()`'s guard. `--render` has NO drift verdict, so an uncaught
    # PermissionError / IsADirectoryError / UnicodeDecodeError exits 1, the code
    # reserved for "the pins drifted", and §7 tells every lane to run this verb: a
    # lane would re-cut 25 correct pins over a file it never read.
    try:
        text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        print(f"render: could not read {src}: {err}", file=sys.stderr)
        raise SystemExit(2) from err
    rows = CENSUS_ROW.findall(text)
    if rows:
        print(
            f"render: refusing {src} — it carries {len(rows)} census row(s) "
            f"(first: {rows[0]!r}). The appendix is GENERATED: nobody commits it, "
            "and pasting it back into the source is the conflict this split exists "
            "to remove. Delete the rows; `--render` appends them from the tree.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return text.rstrip("\n") + "\n\n" + markdown_table(result["sites"]) + "\n"


def check(doc: Path, result: dict) -> int:
    if not doc.exists():
        print(f"check: no doc at {doc}", file=sys.stderr)
        return 2
    # ⛔ A DOC THAT IS PRESENT BUT UNREADABLE IS "COULD NOT RUN" (2), NOT DRIFT (1) —
    # same class as the census wrap in `main()`, in the same verb: an uncaught
    # PermissionError/EIO would exit 1, the code reserved for "the pins drifted", and a
    # lane would re-cut 25 pins that were correct.
    try:
        text = doc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # UnicodeDecodeError is a ValueError, NOT an OSError, so it needed naming:
        # a present-but-non-UTF-8 source exited 1 through this very guard.
        print(f"check: could not read {doc}: {err}", file=sys.stderr)
        return 2
    rows = CENSUS_ROW.findall(text)
    if rows:
        print(
            f"check: {doc} carries {len(rows)} census row(s) (first: {rows[0]!r}) "
            "— it is a RENDERED document, or a source someone pasted the rows "
            "into. The pins live in docs/architecture/CYPHER-INVENTORY.src.md, "
            "and the appendix is generated by `--render`; never committed",
            file=sys.stderr,
        )
        # 1, deliberately: a committed appendix is a RED, not "could not run".
        return 1
    pinned = {k: int(v) for k, v in PINNED.findall(text)}
    sites: list[Site] = result["sites"]
    actual = {
        "sites": len(sites),
        "files": result["files_with_sites"],
        # ⛔ `files_scanned` is pinned too: §0 publishes "295 files scanned" and
        # `files` is a DIFFERENT number (the files that CARRY a site — 97). Before
        # this key existed the doc's scanned count could drift by any amount while
        # `--check` stayed green (`#13098` review P3).
        "files_scanned": result["files_scanned"],
        "unresolved": sum(1 for s in sites if s.shape == "unresolved"),
        "candidates": sum(1 for s in sites if s.verdict.startswith("candidate")),
        # ⛔ §0 also PUBLISHES these four, and they were outside `actual`, so the doc
        # could have printed any value for them while `--check` stayed green — the
        # same vacuity the `files_scanned` key above closed, left open for its
        # neighbours (`#13098` review P3). `ast_door_calls` is the reconciliation's
        # headline (census + non-Cypher, §0's "= 1,557"), so it is pinned as the
        # SUM rather than as a second independent count that could disagree.
        "non_cypher_door_calls": result["non_cypher_door_calls"],
        "ast_door_calls": len(sites) + result["non_cypher_door_calls"],
        "partial": result["partial"],
    }
    matrix = shape_matrix(sites)
    for shape, roles in matrix.items():
        actual[f"shape_{shape}"] = sum(roles.values())
        # ⛔ The ROLE SPLIT is pinned for EVERY shape, not only `plain`. A role swap
        # leaves the shape TOTAL untouched, and §3 publishes `adjacency` 189/100 and
        # `ep` 21/11 as numbers no pin carried — so the exact regression the census
        # exists to catch (a graph write published as `read`, i.e. as a migration
        # candidate) was invisible for every shape except `plain` (`#13098` review
        # P2). `plain` was pinned first because it is the one whose reads ARE the
        # candidate list; the rule is not specific to it.
        for role, count in roles.items():
            actual[f"shape_{shape}_{role}"] = count
    drift = {
        key: (pinned.get(key), value)
        for key, value in actual.items()
        if pinned.get(key) != value
    }
    missing = [key for key in actual if key not in pinned]
    stale = [key for key in pinned if key not in actual]
    if drift or missing or stale:
        for key, (was, now) in sorted(drift.items()):
            print(f"DRIFT {key}: doc={was} tool={now}", file=sys.stderr)
        for key in sorted(missing):
            print(f"MISSING pin {key} (tool={actual[key]})", file=sys.stderr)
        for key in sorted(stale):
            print(f"STALE pin {key}={pinned[key]} — the tool no longer computes it",
                  file=sys.stderr)
        return 1
    print(f"check: {len(actual)} pinned counts match")
    return 0


def emit(text: str, stream=None) -> int:
    """Write one stdout payload; a write that cannot happen is 2, never 1.

    ⛔ `--markdown` IS the appendix table — the one verb a lane pipes (`| head`) or
    redirects, and the one whose payload is far larger than the stdout buffer. A
    closed pipe (`BrokenPipeError`) or a full disk (`OSError ENOSPC`) used to escape
    uncaught and exit **1**, the code this tool reserves for the DRIFT verdict, so a
    lane read "the pins drifted" and re-cut 25 correct pins. `--render`'s write and
    `check()`'s read already answer 2 for their own half of this class; this closes
    the stdout half for every emitting verb.

    The explicit `flush()` is load-bearing: a payload small enough to sit in the
    buffer raises at interpreter shutdown (exit **120**) instead of here, so without
    it the smaller verbs would keep answering something that is not "could not run".
    """
    out = sys.stdout if stream is None else stream
    try:
        out.write(text + "\n")
        out.flush()
    except BrokenPipeError:
        # Python's own advice: re-point fd 1 at devnull so the shutdown flush cannot
        # re-raise and turn the 2 below into 120. Guarded — a test double has no fd.
        _detach_stdout(out)
        print("census: could not write to stdout (closed pipe) — this is 'could not "
              "run', not drift", file=sys.stderr)
        return 2
    except OSError as err:
        # ⛔ THE SAME DETACH IS OWED HERE, and it is measured, not defensive: the failed
        # `flush()` leaves its bytes in the buffer, so the interpreter's shutdown flush
        # retries them and REPLACES the 2 with **120** (a full disk: `>/dev/full`). The
        # exit code is the whole point of this guard, so a code that says "could not
        # run" must survive its own teardown.
        _detach_stdout(out)
        print(f"census: could not write to stdout: {err} — this is 'could not run', "
              "not drift", file=sys.stderr)
        return 2
    return 0


def _detach_stdout(out) -> None:
    """Re-point a failed stdout at devnull so Python's shutdown flush cannot re-raise."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), out.fileno())
    except (OSError, ValueError, AttributeError):
        pass


def main(argv: list[str] | None = None) -> int:
    # ⛔ STDOUT MUST NOT BE ABLE TO FAIL ENCODING. A non-UTF-8 tracked filename reaches
    # the report/table as a surrogate escape, and under a real UTF-8 locale
    # (`en_US.UTF-8`, macOS's default) `sys.stdout.errors` is `strict`, so `print(...)`
    # raised `UnicodeEncodeError` and the bare verb exited 1 — the drift code — while
    # the same run under `C.UTF-8` exited 0. A locale-dependent exit status is worse
    # than a mis-rendered byte. (No effect where stdout has no `reconfigure`.)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="emit the appendix table")
    parser.add_argument("--json", action="store_true", help="emit machine form")
    parser.add_argument("--check", nargs="?", const=str(DOC_SRC.relative_to(ROOT)),
                        help="compare the SOURCE's pinned counts with this run (exit 1 on drift)")
    parser.add_argument("--render", nargs="?", const=str(DOC_OUT.relative_to(ROOT)),
                        help="write the document (source + this run's appendix) here")
    parser.add_argument("--all-surface", action="store_true",
                        help="census every tracked surface, not only the product")
    args = parser.parse_args(argv)

    # ⛔ NEITHER VERB MAY RUN THE WIDE CENSUS UNDER THE SOURCE'S PRODUCT PINS.
    # The source's 25 pins are PRODUCT-surface counts (`census(("tortoise",))`),
    # and `--render` appends the run's rows under those pins. Rendering the
    # all-surface census would write a document whose §0 `sites` pin — a
    # PRODUCT-surface count — sits above an appendix several times longer,
    # print success, and be self-contradictory — a false pass in
    # prose, which is the class this file exists to refuse. The refusal is loud
    # rather than a silent scope-narrowing: a lane that asked for the wide census
    # and got the product one would believe it had rendered the wide table.
    if args.all_surface and (args.render is not None or args.check is not None):
        verb = "--render" if args.render is not None else "--check"
        print(
            f"`{verb} --all-surface` contradicts the source's pins: the 25 pins are\n"
            "PRODUCT-surface counts, so the wide census can never satisfy --check and\n"
            "would sit under an appendix that contradicts it under --render. Take the\n"
            "wide census from `--markdown --all-surface` (or `--json`) instead.",
            file=sys.stderr,
        )
        return 2

    # ⛔ `--render` MUST NOT OVERWRITE A TRACKED PATH. The refusal inside `render()`
    # guards the SOURCE'S CONTENT (a source that already carries rows), never the
    # DESTINATION — so `--render docs/architecture/CYPHER-INVENTORY.src.md` read the
    # clean source, passed that refusal, and wrote source + the appendix rows over the tracked
    # file. That is self-locking: afterwards `--check` exits 1 ("carries N census
    # row(s)") and `--render` exits 2 (refuses the rowed source), so the tool can
    # neither grade nor repair the tree it just corrupted. A mistyped path is the
    # plausible route here — §7 tells every lane to run this verb — and the same
    # clobber applies to any other tracked doc. Refused before the census runs, so the
    # refusal costs nothing. `git ls-files` answers "is this path in the index", which
    # is exactly the invariant (`--render`'s output is never committed); outside a git
    # work tree (exit 128) it simply does not fire.
    if args.render is not None:
        # ⛔ AND A DESTINATION THAT CANNOT BE RESOLVED IS "COULD NOT RUN" (2) TOO.
        # `Path.resolve()` raises `RuntimeError("Symlink loop …")` on ELOOP — NOT an
        # OSError — and it runs before every guard, so the traceback escaped and the
        # interpreter exited 1, the DRIFT code, from a verb that has no drift verdict
        # (measured with a two-symlink loop: --render /tmp/loop/a/deep.md → rc 1).
        try:
            target = (ROOT / args.render).resolve()
            src_resolved = DOC_SRC.resolve()
        except (OSError, RuntimeError) as err:
            print(f"render: could not resolve {args.render}: {err}", file=sys.stderr)
            return 2
        if target == src_resolved:
            print(
                f"refusing to render over the SOURCE ({args.render}): the document is\n"
                "`--render`'s output and the source is hand-authored. Pick another\n"
                "path (the default writes the gitignored `CYPHER-INVENTORY.md`).",
                file=sys.stderr,
            )
            return 2
        try:
            # `ROOT.resolve()` is inside the guard too: a symlink loop on the tree root
            # raises `RuntimeError` here, and only `ValueError` (path outside ROOT) was
            # ever caught.
            rel = target.relative_to(ROOT.resolve()).as_posix()
        except (ValueError, OSError, RuntimeError):
            rel = None
        if rel is not None:
            # ⛔ A PROBE THAT CANNOT RUN IS "COULD NOT RUN" (2), never 1. An absent
            # `git` binary raises FileNotFoundError here, and nothing catches it: the
            # interpreter then exits 1, which this tool reserves for the DRIFT verdict
            # — so a lane reads "the census drifted" from a box that could not answer
            # at all (#13098 review, P2). Same class, same status as every other
            # refusal in this module.
            try:
                # ⛔ AND THIS PROBE MUST NOT BE ABLE TO FAIL DECODING EITHER. With
                # `core.quotePath=false` in the user's config, `git` emits RAW filename
                # bytes on stdout AND stderr, so a strict `text=True` decode raises
                # `UnicodeDecodeError` — a `ValueError`, not the `OSError` caught below
                # — and a deliberate "refusing to render over a TRACKED path" (2) became
                # a traceback and exit 1 (measured with `quotePath false` and a
                # `caf\xe9.md`). The payload is unused, so decoding is pure overhead
                # that must never fail.
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", "--", rel],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="surrogateescape",
                )
            except OSError as err:
                print(
                    f"render: could not probe {rel} for tracking: {err}",
                    file=sys.stderr,
                )
                return 2
            if tracked.returncode == 0:
                print(
                    f"refusing to render over a TRACKED path ({rel}): the document is\n"
                    "`--render`'s output, which is never committed. Pick an untracked\n"
                    "path (the default is gitignored).",
                    file=sys.stderr,
                )
                return 2

    prefixes: tuple[str, ...] = ("tortoise",)
    if args.all_surface:
        prefixes = (".",)

    # ⛔ EVERY MODE THAT CANNOT ENUMERATE ANSWERS 2, NEVER 1. `surface_files()` runs
    # `git ls-files … check=True`, so on a box without a `git` binary or without an
    # index it raises (`FileNotFoundError`, `CalledProcessError` 128) — and an
    # uncaught exception exits the interpreter with 1, the code this tool reserves
    # for the DRIFT verdict. `--render` has no drift verdict at all, and `--check`
    # would then say "the census drifted" about a box that could not answer, sending
    # a lane to re-cut 25 pins that were correct. The probe wrapper below closes this
    # class for `git ls-files --error-unmatch`; this one closes it for the census's
    # own enumeration, which runs on EVERY path.
    try:
        result = census(prefixes, drop_test_modules=not args.all_surface)
        # ⛔ `--json` NEEDS A SECOND, UNGUARDED-UNTIL-NOW ENUMERATION: `rg_totals()`
        # re-reads the index through `surface_files()`, so a PARTIAL failure (the first
        # walk succeeds, the second is refused — a transient EAGAIN, an index that
        # becomes unreadable mid-run) escaped this guard and exited 1, the DRIFT code,
        # from a verb that has no drift verdict. Measured with a `git` shim that
        # delegates the first call and exits 128 on the second. It is inside the guard
        # for the same reason as the first: this tool's "could not run" must be total.
        rg = (
            rg_totals(prefixes, drop_test_modules=not args.all_surface)
            if args.json
            else None
        )
    except (OSError, subprocess.CalledProcessError) as err:
        print(
            f"census: could not enumerate {prefixes}: {err} — this is 'could not "
            "run', not drift",
            file=sys.stderr,
        )
        return 2

    if args.check is not None:
        return check(ROOT / args.check, result)
    if args.render is not None:
        out = ROOT / args.render
        text = render(DOC_SRC, result)
        # ⛔ AND NEITHER DOES A WRITE THAT CANNOT HAPPEN. `--render` has NO drift
        # verdict, so an uncaught IsADirectoryError/PermissionError/OSError (read-only
        # mount, ENOSPC) exits 1 — the status this tool reserves for "the pins drifted"
        # — and §7 tells every lane to run this verb, so a lane that rendered into a
        # path it cannot write re-cuts 25 correct pins. "Could not run" is 2, and the
        # message names the path and the error (#13098 review, P2).
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            # ⛔ THE TABLE CAN CARRY A SURROGATE-ESCAPED PATH (a non-UTF-8 tracked
            # filename), which strict UTF-8 cannot ENCODE: `write_text` then raised
            # `UnicodeEncodeError` — a `ValueError`, not an `OSError` — and left an
            # EMPTY file behind while exiting 1, the drift code, from a verb with no
            # drift verdict. `backslashreplace` renders such a byte as text, and the
            # guard names `UnicodeError` so neither half can escape.
            out.write_text(text, encoding="utf-8", errors="backslashreplace")
        except (OSError, UnicodeError) as err:
            print(f"render: could not write {out}: {err}", file=sys.stderr)
            return 2
        print(f"render: wrote {len(text.splitlines())} lines to {out}")
        return 0
    if args.json:
        payload = {
            "prefixes": result["prefixes"],
            "files_scanned": result["files_scanned"],
            "files_with_sites": result["files_with_sites"],
            "sites": len(result["sites"]),
            "non_cypher_door_calls": result["non_cypher_door_calls"],
            "unparsed_files": result["unparsed_files"],
            "shape_matrix": shape_matrix(result["sites"]),
            "verdicts": dict(Counter(s.verdict for s in result["sites"])),
            "partial": result["partial"],
            "rg_totals": rg,
            "rows": [s.as_row() for s in sorted(result["sites"], key=lambda s: (s.path, s.line))],
        }
        return emit(json.dumps(payload, indent=2, sort_keys=True))
    if args.markdown:
        return emit(markdown_table(result["sites"]))
    # ⛔ AND THE BARE VERB ENUMERATES TWICE TOO: `report()` calls `rg_totals()`, which
    # re-reads the index — outside the guard above, so a PARTIAL failure exited 1, the
    # DRIFT code, from the DEFAULT verb (measured with a `git` shim that delegates the
    # first `ls-files` and refuses the second).
    try:
        text = report(result)
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"census: could not enumerate: {err} — this is 'could not run', not drift",
              file=sys.stderr)
        return 2
    return emit(text)


if __name__ == "__main__":
    raise SystemExit(main())
