"""#7948 — the `aboutObject` read-site declaration must be COMPLETE, not just
well-formed.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13's read-site table is the
**whole evidence base** for a P1-severe storage ruling — the document says so in
its own words: *"⚠️ The count is the whole argument — the table must name every
customer-callable surface"*. The ruling it supports (KEEP the stored edge,
owner, 2026-09-23) is safe; the **table** is not.

It is a hand-maintained prose list of `file.py:NNNN (symbol)` pinned to
`c79ba1cf2`, and it has been found incomplete **three separate times**:

1. the issue body (`#7948`, 13 sites claimed to be the envelope);
2. review finding `B2.7` (`REVIEW-CONSOLIDATED-2026-09-23.md`) — named three
   further readers and under-classed two of them;
3. the `#7956` erratum on `B2.7`, which found that finding *itself* incomplete
   in the direction that matters (the customer-callable count was four, not
   two).

At the head this file was written against it is incomplete **again**, and one
row cites a symbol that does not perform the read at all:

* row 8 cites `collect_slices` — which never traverses the edge. The Cypher
  lives in `assembly.docker_walker_port.spine_rows`.
* row 9 cites `build_subgraph` — the Cypher lives in `subgraph._collect_raw`,
  which consumes the module constants `_CYPHER_ABOUT` / `_CYPHER_SIBLINGS`.
* row 4 names no symbol at all, so the two degree-centrality readers
  (`GraphRanker._fetch_event_signals`, `StateRanker._fetch_point_signals`)
  cannot be re-verified.
* `_EdgeHandlers._count_about_out` and `session_schema.audit_session_retrofit`
  are absent entirely.

THE ROOT CAUSE, AND WHY THIS IS A CENSUS RATHER THAN A LONGER LIST
------------------------------------------------------------------
Nothing executable compared the table to the code. A **symbol resolution** check
cannot see a *missing* row, and two of the sites cannot be found by any grep at
all (below) — so the table could only ever be audited by hand, and hand audits
are what produced the three passes above.

WHAT THIS PINS
--------------
1. **`test_every_code_site_that_traverses_the_edge_is_declared`** — COMPLETENESS.
   The census below is derived from the source's AST, not from a list: every
   string literal that contains a Cypher clause traversing the `aboutObject`
   relationship *by name* is a site, and every site must appear in §13's table.
   A new reader added anywhere under `tortoise/` reds this until it is declared.
2. **`test_every_declared_symbol_still_performs_the_read`** — ROT. Every symbol
   the table names must be a census site, or one of the two documented
   un-derivable sites (which must still exist in its file). This is the arm that
   catches row 8's `collect_slices`: a symbol that resolves, but does not read.
   `EXTRACTOR-V4-ARCHITECTURE`'s own rule is *"the symbol name is authoritative,
   the line number is a convenience"* — so a row whose symbol does not read is a
   row that misleads every re-verifier.
3. **`test_the_declaration_covers_every_known_un_derivable_site`** — the two
   sites no census can derive are declared **with their reason**, and still
   exist. `recall_subgraph` reads the edge through `MATCH (n)-[r]->(m)` (its
   default `completeness="full"` sets an empty relationship pattern) and
   `topic_summarize` builds the relationship name into a variable, so both are
   invisible to a literal scan — which is exactly why naming them is mandatory
   rather than optional.
4. **`test_the_table_is_not_vacuous`** — the census, the table and the
   customer-callable count are all non-trivially populated, so none of the arms
   above can pass by finding nothing.

NOT promised, and deliberately so:
* Line numbers are NOT asserted. The documents' own rule makes the symbol the
  authority and the line a convenience, and pinning line numbers would make this
  guard red on every unrelated edit — a different way to be dead.
* Whether a declared site is *reachable* (default-ON, flag-gated, eval-only) is
  NOT asserted. That column is prose with reasons; a regex over it would pin
  incidents of wording rather than behaviour.
* Write sites are out of scope. The table enumerates **readers**; a `MERGE` that
  mints the edge is not one, and the two are separated by the governing Cypher
  clause, not by a keyword appearing anywhere in the statement.

Static, stdlib-only, no network / DB / graph — it reads source text and a
document.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORTISE = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b")
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(r"[-\[][^\n]{0,140}?aboutObject", re.IGNORECASE)

#: The sites NO census can derive, each with the reason it cannot be derived.
#: Both are load-bearing: `recall_subgraph` is one of the FOUR customer-callable
#: surfaces the ruling rests on, and it is un-greppable by construction.
UN_DERIVABLE: tuple[tuple[str, str, str], ...] = (
    (
        "topic_summarization.py",
        "_retrieve_topic_neighborhood",
        "builds the relationship name into a variable (`edge_type`) and hands it "
        "to a later query, so no Cypher literal names the edge",
    ),
    (
        "ranking.py",
        "SubgraphExpander._neighbors",
        "`completeness=\"full\"` sets an empty relationship pattern, so the "
        "query walks EVERY relationship type via `MATCH (n)-[r]->(m)` and "
        "reads the edge without ever naming it",
    ),
)

#: The table header that identifies §13's declaration, and the row shape.
_TABLE_HEADER = "| # | site |"


def _site_symbols(node: ast.Constant, tree: ast.Module, parents: dict) -> list[str]:
    """The symbol(s) a Cypher literal belongs to.

    The innermost function/method is the site; a module-level constant is
    attributed to the function(s) that consume it, because that is where the
    read happens (`subgraph._CYPHER_ABOUT` is executed by `_collect_raw`).
    """
    chain: list[str] = []
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur.name)
        cur = parents.get(cur)
    if chain:
        # innermost function wins; a `Klass.method` qualifier is a convenience
        # path, not a symbol (the same rule `citation_rot` applies).
        return [chain[0]]

    const_name = None
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if any(t is node for t in ast.walk(stmt.value)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    const_name = target.id
    if const_name is None:
        return ["<module>"]

    users: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(n, ast.Name) and n.id == const_name
            for n in ast.walk(fn)
        ):
            users.append(fn.name)
    return sorted(set(users)) or [f"<module:{const_name}>"]


@lru_cache(maxsize=1)
def _census() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site under `tortoise/` whose Cypher traverses the edge by name.

    `{(relative_path, symbol): (line numbers)}` — READ sites only. A string
    literal is a site when at least one of its `aboutObject` traversals is
    governed by `MATCH`/`OPTIONAL MATCH`; one governed by `MERGE`/`CREATE` mints
    the edge and is a writer.

    Cached: three tests read it and it parses the whole package with `ast`.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for path in sorted(TORTISE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "aboutObject" not in source:
            continue
        tree = ast.parse(source)
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = node.value
            if "aboutObject" not in text:
                continue
            reads = False
            for match in _TRAVERSAL.finditer(text):
                clauses = _CLAUSE.findall(text[: match.start()])
                if not clauses:
                    continue
                governing = " ".join(clauses[-1].split()).replace("\n", " ").upper()
                if governing in ("MATCH", "OPTIONAL MATCH"):
                    reads = True
            if not reads:
                continue
            rel = str(path.relative_to(ROOT))
            for symbol in _site_symbols(node, tree, parents):
                sites.setdefault((rel, symbol), []).append(node.lineno)
    return {key: tuple(value) for key, value in sites.items()}


@lru_cache(maxsize=1)
def _declared_rows() -> tuple[tuple[int, str, str, list[str], list[str]], ...]:
    """§13's table, parsed: `(row_number, site_cell, reach_cell, files, symbols)`.

    The document is the declaration — there is no second manifest to drift.
    """
    lines = STORAGE.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[int, str, str, list[str], list[str]]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not in_table:
            if stripped.startswith(_TABLE_HEADER):
                in_table = True
            continue
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        number, site_cell, reach_cell = int(cells[0]), cells[1], cells[3]
        # `file.py` / `file.py:NNNN` citations. Line numbers are never asserted
        # (see the docstring), so a row may carry one or omit it.
        files: list[str] = []
        for token in re.findall(r"([A-Za-z0-9_/]+\.py)(?::\d+)?", site_cell):
            files.append(token)
        # Backticked symbols: no `.py`, no `#NNNN`, no assignment/citation text.
        symbols = [
            tok
            for tok in re.findall(r"`([^`]+)`", site_cell)
            if ".py" not in tok and not tok.startswith("#") and ":" not in tok
        ]
        rows.append((number, site_cell, reach_cell, files, symbols))
    return tuple(rows)


def _last(symbol: str) -> str:
    """The base symbol: `ranking.SubgraphExpander._neighbors` -> `_neighbors`."""
    return re.sub(r"\(\)$", "", symbol).rsplit(".", 1)[-1]


def test_the_table_is_not_vacuous():
    """Every arm below must be able to find something, or all of them pass by
    finding nothing — the failure mode that let three hand audits conclude the
    list was complete."""
    sites = _census()
    rows = _declared_rows()
    customer_callable = [
        r for r in rows if "customer-callable" in r[2].lower()
    ]

    assert len(sites) >= 13, (
        f"the census derived only {len(sites)} read sites from tortoise/ — the "
        "traversal shape changed (or the parser broke) and the completeness arm "
        "would pass vacuously. Sites: "
        + ", ".join(sorted(f"{f}::{s}" for f, s in sites))
    )
    assert len(rows) >= 13, (
        f"§13's table parsed to {len(rows)} rows — the table moved out of the "
        "shape this guard reads, so nothing below is being checked."
    )
    assert len(customer_callable) >= 4, (
        "§13 must name at least the FOUR customer-callable surfaces the KEEP "
        "ruling rests on (belief_timeline, topic_summarize, recall_subgraph, "
        f"search(order_by='graph')); the table declares {len(customer_callable)}."
    )


def test_every_code_site_that_traverses_the_edge_is_declared():
    """COMPLETENESS: the table must name every reader the code actually has.

    This is the arm that no symbol-resolution check can replace — a *missing*
    row has no symbol to fail to resolve.
    """
    sites = _census()
    declared = _declared_rows()
    declared_by_file: dict[str, set[str]] = {}
    for _n, _site, _reach, files, symbols in declared:
        for f in files:
            # Basename on BOTH sides: every reader lives under `tortoise/`, and
            # §13 may cite either `sdk.py` or `projection/edges.py`.
            for sym in symbols:
                declared_by_file.setdefault(f.rsplit("/", 1)[-1], set()).add(_last(sym))

    undeclared = []
    for (path, symbol), lines in sorted(sites.items()):
        basename = path.rsplit("/", 1)[-1]
        if symbol in declared_by_file.get(basename, set()):
            continue
        undeclared.append((path, symbol, lines))

    assert not undeclared, (
        "these sites traverse the `aboutObject` edge by name and §13's table "
        "does not name them. That table is the evidence base for the KEEP "
        "ruling, and the document's own rule is that the count IS the argument "
        "— so an omitted reader is an unmeasured one (add the row, with the "
        "symbol, to `## 13` of STORAGE-ARCHITECTURE.md):\n  - "
        + "\n  - ".join(
            f"{path}::{symbol} (lines {lines})" for path, symbol, lines in undeclared
        )
    )


def test_every_declared_symbol_still_performs_the_read():
    """ROT: a row whose symbol resolves but does not read is a false row.

    The three prior audits all failed in this direction — a symbol that exists,
    is quoted, and is not where the traversal happens — so the check is that the
    symbol is a **census site**, not merely present in the file.
    """
    sites = _census()
    # The census keys on the path relative to the repo root; §13's table cites
    # the basename (every reader lives under `tortoise/`).
    site_symbols: set[tuple[str, str]] = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in sites
    }
    un_derivable = {
        (f, _last(sym))
        for f, sym, _reason in UN_DERIVABLE
    }

    problems: list[str] = []
    for number, _site_cell, _reach, files, symbols in _declared_rows():
        for f in files:
            for sym in symbols:
                key = (f.rsplit("/", 1)[-1], _last(sym))
                if key in site_symbols:
                    continue
                if key in un_derivable:
                    # Derivable-by-design, but the symbol must still exist —
                    # otherwise the row rotted silently and `UN_DERIVABLE` is
                    # masking it.
                    candidates = [p for p in TORTISE.rglob(f)]
                    if not candidates:
                        problems.append(
                            f"row {number}: uncovered site {f} does not exist"
                        )
                    elif not any(
                        _last(sym) in p.read_text(encoding="utf-8") for p in candidates
                    ):
                        problems.append(
                            f"row {number}: un-derivable symbol `{sym}` is not in {f}"
                        )
                    continue
                problems.append(
                    f"row {number}: `{sym}` ({f}) is declared as a reader but no "
                    "Cypher in it traverses `aboutObject`"
                )

    assert not problems, (
        "§13's table names these as readers of the `aboutObject` edge, and they "
        "are not — a re-verifier following the row finds no read and concludes "
        "the passage is wrong (the exact failure EXTRACTOR-V4-ARCHITECTURE.md's "
        "citation guard exists for, one document over):\n  - "
        + "\n  - ".join(problems)
    )


def test_the_declaration_covers_every_known_un_derivable_site():
    """The two readers no census can find must be declared BY NAME, with the
    reason, and must still exist — a literal scan is structurally blind to them,
    so a dropped row has no other way to be noticed."""
    declared: set[tuple[str, str]] = set()
    for _n, _site, _reach, files, symbols in _declared_rows():
        for f in files:
            for sym in symbols:
                declared.add((f, _last(sym)))

    missing = []
    for filename, symbol, reason in UN_DERIVABLE:
        assert _last(symbol) in "".join(
            p.read_text(encoding="utf-8") for p in TORTISE.rglob(filename)
        ), f"`{symbol}` no longer exists in {filename} — re-verify the row"
        if (filename, _last(symbol)) not in declared:
            missing.append(f"{filename}::{symbol} — {reason}")

    assert not missing, (
        "these sites read the `aboutObject` edge but cannot be derived by any "
        "source scan, so §13 must name them explicitly (it is the only place "
        "they are recorded):\n  - " + "\n  - ".join(missing)
    )
