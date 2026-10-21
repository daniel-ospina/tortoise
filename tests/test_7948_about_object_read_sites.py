"""#7948 — the §13 `aboutObject` read-site enumeration must be EXECUTABLE, not prose.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13 carries the evidence the
owner's 2026-09-23 **KEEP the stored edges** ruling rests on: an enumeration
of the code that READS the `aboutObject` edge, plus the count the document
itself calls decisive — *"the count is the whole argument"*.

That enumeration is hand-maintained prose, and it has been wrong three times
already:

  * #7948's body asserted the measurement did not exist at all;
  * review finding B2.7 enumerated 22 files and **two** customer-callable
    sites (`REVIEW-CONSOLIDATED-2026-09-23.md:122` — itself corrected there);
  * the `#7956` correction (PR `#7986`) applied in §13 found **four**.

Re-derived against the code at `26aff163c`, the table was stale again, in the
two directions that matter.

**The reader SYMBOLS had rotted** — the table's defence is *"the symbols are
authoritative, the line number is a convenience"*, and nothing checked that:

  * row 2 named `topic_summarize`, which does not read the edge; the reader
    is `_retrieve_topic_neighborhood`, which builds the edge NAME at run time
    (`edge_type = "aboutSubject" if … else "aboutObject"`) so no grep sees it;
  * row 4 named no symbol at all — `search(order_by="graph")` is a DOOR — and
    its two line numbers point at unrelated code (`ranking.py:714` is
    `eid = row[0]`); the reader is `GraphRanker._fetch_event_signals`;
  * row 8 named `collect_slices`, which never mentions the edge: it delegates
    the read to `spine_rows`, which lives inside `docker_walker_port`;
  * row 9 named `build_subgraph`, which reads none of it — the reads are the
    module constants `_CYPHER_ABOUT` / `_CYPHER_SIBLINGS`.

**The list was still incomplete, and the missing part was the load-bearing
kind** — the customer-callable count was **8 sites across 5 doors, not 4**:

  * the entire **`recall_state`** path was absent, and it reads the edge on
    **four** paths: `StateRanker._fetch_point_signals` (a literal alternation),
    `StateRanker._fetch_object_signals`, `TortoiseSDK._state_related_objects`
    and `TortoiseSDK._state_related_points` — the last three name the edge in
    an `about*` alternation CONSTANT (`ABOUT_EDGE_TYPES`,
    `TortoiseSDK._ABOUT_TYPES`), so neither the literal `aboutObject` nor a
    bare `MATCH (n)-[r]->(m)` appears in them. Same blind spot that made
    `recall_subgraph`'s every-relationship walk the site no grep can find,
    one layer further out;
  * `session_schema.audit_session_retrofit` (ops audit),
    `projection/edges._EdgeHandlers._count_about_out` (the `#8398` restore
    path) and `TortoiseSDK.supersede_point` (the supersede rewire) were
    unnamed;
  * the counts the document states elsewhere had drifted too: *"36 test files
    assert the edge at the pin"* measured 59 at `26aff163c`.

**A second re-derivation found three MORE readers, all inside modules the
census already named** — which is why the census below is keyed on
`module.py::symbol` and not on the module:

  * `navigation._hop_query` — the SECOND all-edge walk (`MATCH (n)-[r]->(m)`,
    no rel-type filter), behind MCP `tortoise_entity_profile` and
    `tortoise_traverse`. Exactly the rule that makes row 3 a row, so it is
    either a row or row 3 is not;
  * `mcp_server._preview_supersede` — the supersede DRY RUN matches every
    `SUPERSEDE_STRUCTURAL_RELS` member (MCP `tortoise_supersede`);
  * `projection.FalkorProjection._rebuild_all_replay` — the replay delete-leg matches
    `[r:{etype}]` where `etype` is a RUN-TIME name gated on
    `DERIVABLE_STRUCTURAL_RELS`.

A module-level census cannot see any of them: each module was already named by
a row, and no row count changes when a reader is added beside it.

**A FOURTH re-derivation — this time of the class the section declares, not of
the customer doors — found two MORE, and neither is reachable from a customer
surface:** the whole-graph incident-edge COUNT in `subgraph.py`
(`MATCH (o)-[r]-()`: no rel-type filter, so it reads the edge without naming it,
and it COUNTS it, so a `SET`-free derive silently changes the `hub_degree` the
`#3011` engine ranks on) and the read-only reference census in
`definite_description_backfill.py` (an unlabelled inbound-edge count whose own
docstring says a referent is an Object if *anything* points at it, `aboutObject`
included). Both are the same rule that admits rows 3, 22, 24 and 25 — *an
unfiltered walk reads the edge without naming it* — and both were invisible to
every earlier pass because each one asked "which customer door reaches this?"
instead of "which code reads every edge?". §13 says the class is *enumerated,
not excluded*, so they are rows 26–27.

The two documents that carry this census (§13 and `EXTRACTOR-V4-ARCHITECTURE.md`
§13.8) also disagreed with each other — the sibling still said FOUR — which is
how an under-count survives a review: each copy looks confirmed by the other.
They disagreed AGAIN in the count's own enumeration: §13.8 stated TWELVE and
listed thirteen, the extra one (`supersede_point`) being a row §13 classes
`write path` — so the numeral agreed with §13 while the list under it did not.

WHAT THIS PINS
--------------
1. `test_every_row_names_a_symbol_that_exists` — each row's
   `` `module.py::symbol` `` resolves in the code (dotted: class attribute).
   A rename or removal reds here instead of leaving the evidence stale.
2. `test_every_named_reader_still_reads_the_edge` — the named symbol really
   reads the edge, by any of the three ways a reader can name it: a Cypher
   **read** clause (`MATCH`/`OPTIONAL MATCH`) naming it literally; a carrier
   CONSTANT whose value names it; or — for the readers that COMPUTE the
   edge name, and for the supersede/replay rows — the CONSUMING line, which
   is the code fact that makes them reads (`rel_pattern` empty for
   `completeness="full"`; `` -[:{edge_type}]-> `` with `edge_type`'s
   else-branch `"aboutObject"`).
3. `test_the_computed_name_readers_are_still_complete_reads` — the
   every-relationship walk keeps its empty-pattern default, which is what
   makes `recall_subgraph` a reader at all.
4. `test_every_computed_name_pin_owns_a_row` — the other half of the census,
   and the one that was FAIL-OPEN: a `_NON_LITERAL` pin with no §13 row was
   never read, so dropping a computed-name row (row 3, row 22) and lowering
   the stated counts left every test green. Pins must be carried by rows.
5. `test_no_read_site_lacks_a_row` — the census half, keyed on
   `module.py::symbol`. Every read clause under `tortoise/` that names the edge
   must be absorbed by a row naming the symbol it sits in. ⚠️ **Module
   granularity is not enough, and that was the second false PASS**: the
   historical under-count lived INSIDE already-named modules (`ranking.py`,
   `sdk.py`), and a new `MATCH (n)-[:aboutObject]->(o)` appended to either one
   moved no module set and no row count.
6. `test_the_prose_counts_match_the_table` — the arithmetic (§13's stated site
   count, and its stated customer-callable count) equals the table. This is
   the figure that was wrong twice, and it is the figure the ruling turns on.
7. `test_every_carrier_constant_a_row_reads_through_still_names_the_edge` —
   the constant IS the read for the carrier rows: if `aboutObject` leaves
   `ABOUT_EDGE_TYPES` / `_ABOUT_TYPES` / `SUPERSEDE_STRUCTURAL_RELS` /
   `DERIVABLE_STRUCTURAL_RELS`, the row still resolves while the site stops
   reading the edge. The constant is resolved through the reader's IMPORT too
   (`mcp_server.py` takes `SUPERSEDE_STRUCTURAL_RELS` from `sdk.py`;
   `projection/__init__.py` takes `DERIVABLE_STRUCTURAL_RELS` from
   `projection/edges.py`), because a same-module scan would leave those rows
   unasserted.
8. `test_every_customer_callable_row_reaches_a_declared_door` — each
   customer-callable row names a door that exists: ``SDK `<method>` `` must be
   a public `TortoiseSDK` method, ``MCP `<tool>` `` a registered
   `ToolDefinition`. That is what makes "customer-callable" a claim about the
   product surface rather than an adjective.
9. `test_the_extractor_document_agrees_on_the_count` — the sibling document
   states the SAME fact as §13, and "fact" is the NUMBER **and the
   ENUMERATION**: §13.8 said TWELVE and listed thirteen (the extra being a
   `write path` row), and the count is stated in two word orders, so the guard
   reads both and asserts set-equality between the readers it names and the
   customer-callable rows. It also names the state-recall readers the four-site
   census missed.
10. `test_section_13_carries_no_line_citations` — §13 says *"Line numbers are
   deliberately **not** carried any more … the lines had all rotted"*, so it
   must carry none. Two survived underneath that sentence
   (`projection/entities.py:785+`, `test_index_restore.py:373-403`) and BOTH
   had rotted: 785 is inside `_connector_content_hash`'s docstring, and
   373-403 is the unjournalled-`Document` drop, not the `aboutObject` count.
   A claim a document makes about itself is one a guard can hold it to.
11. `test_the_stated_door_count_matches_the_table` — the blast-radius DOOR
   figure is DERIVED from the customer-callable reach cells (and its per-kind
   breakdown stated), in both documents. It was `twelve` while the table named
   sixteen, and nothing read it.
12. `test_the_edge_pattern_sees_the_whole_about_family` — the census pattern
   must see a LONG `about*` alternation, not only one that fits a
   character window. The pattern decides the census, the row check and the
   module pre-filter at once, so a narrow window was a three-way false PASS.

⚠️ **What this deliberately does NOT claim.** The census is a LOWER BOUND: it
sees a literal relationship pattern naming the edge, so a reader that
computes or carries the name (`_neighbors`, `_hop_query`,
`_retrieve_topic_neighborhood`, `_preview_supersede`, `_rebuild_all_replay`,
`supersede_point`, the `recall_state` constant readers) is invisible to it and
is pinned by (2)/(7) instead, and (4) keeps a dropped row for one of them
from going unnoticed (its pin would dangle). There is NO carrier fallback in
(2): a row is a literal read, a pinned computed-name read, or red — the old
fallback ("any `MATCH` in the span plus a bare mention of a carrier-shaped
constant") decided no current row and would have greened a future one that read
nothing. Rows 26–27 are unfiltered walks, so they are pinned the same way (2)
pins rows 3 and 22 — a `MATCH` with no rel-type filter, which no grep for the edge
name can see. Line NUMBERS are not asserted — the symbol is authoritative and a symbol that merely moved
passes. And the enumeration is of READERS of the edge as data; writers that
MERGE it are not rows here.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
PKG = ROOT / "tortoise"

#: The edge named inside a Cypher relationship bracket — `[:aboutObject]`,
#: `[r:aboutObject]`, `[:aboutSubject|aboutObject]`, `[r1:aboutObject]`.
#:
#: ⚠️ **The bracket is bounded by the STRING, not by a character count.** The
#: first form of this pattern allowed 60 characters on each side of the edge
#: name, which matched a short alternation and missed a long one: measured,
#: `[r:aboutSubject|aboutAction|aboutEvent|aboutPoint|aboutDocument|aboutObject]`
#: (63 characters before the name — the full six-member `about*` family in the
#: order `ranking.ABOUT_EDGE_TYPES` uses) was invisible to EVERY consumer of
#: this pattern. Because the same pattern decides both the census
#: (`_literal_read_sites`) and whether a row's symbol reads the edge
#: (`_reads_naming_the_edge`), a reader written in the repo's own established
#: alternation style was in neither — a false PASS of the file's completeness
#: claim, and exactly the class this guard exists to close. Cypher relationship
#: brackets do not contain `]`, so bounding on that is both sufficient and
#: exact; the newline exclusion keeps the match inside one folded string.
#:
#: ⚠️ **And a bracket alone is not enough — it must be a RELATIONSHIP bracket.**
#: Bounding by `]` alone made a JSON array a false positive: measured, the
#: grounding prompt in `tortoise/value_extractor.py` carries
#: `"connections": [{"to": …, "edge": "aboutObject|IMPL|…"}]` inside ONE Python
#: string, which the bare form read as a Cypher read site. Cypher delimits a
#: relationship with `-` on either side (`(n)-[r]->(o)`, `(n)<-[r]-(o)`,
#: `(n)-[r]-(o)`), so the pattern anchors on that and the prompt string drops
#: out.
_EDGE_PATTERN = re.compile(r"(?:<-|-)\s*\[[^\]\n]*aboutObject[^\]\n]*\]")

#: Literal Cypher brackets the census MUST see. The last one is the case the
#: old 60-character window missed; the short forms are the regression floor, so
#: the pattern cannot be narrowed again without reddening here first.
_EDGE_PATTERN_MUST_MATCH: tuple[str, ...] = (
    "MATCH (n)-[:aboutObject]->(o:Object)",
    "MATCH (n)-[r:aboutObject]->(o)",
    "OPTIONAL MATCH (n)-[:aboutSubject|aboutObject]->(o)",
    "MATCH (n)-[r1:aboutObject]->(o)",
    "MATCH (n)<-[:aboutObject]-(o)",
    "MATCH (n)-[r:aboutSubject|aboutAction|aboutEvent|aboutPoint|aboutDocument|"
    "aboutObject]->(o)",
)

#: Strings the census must NOT read as a Cypher site. The first is real: it is
#: the grounding prompt's JSON example in `tortoise/value_extractor.py`, where
#: the `aboutObject|…` alternation is a payload VALUE inside a JSON array and
#: not a relationship at all.
_EDGE_PATTERN_MUST_NOT_MATCH: tuple[str, ...] = (
    '"connections": [{"to": "existing-id", "edge": "aboutObject|IMPL|NAND|MITIGATES", '
    '"note": null}]',
    "aboutObject is load-bearing, and this sentence is not a query",
    "MATCH (n)-[\n:aboutObject]->(o)",
)

#: Clauses that decide whether the pattern is read or written.
_CLAUSE = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE|SET|DELETE|DETACH)\b", re.I
)
_READ_CLAUSES = {"MATCH", "OPTIONAL MATCH"}

#: A table row: `| 3 | `module.py::Symbol.attr` | reads for | **reach** |`.
_ROW = re.compile(
    r"^\s*\|\s*(\d+)\s*\|\s*`([\w/]+\.py)::([\w.]+)`\s*\|(.*?)\|(.*?)\|\s*$", re.M
)

#: The declared reach vocabulary. A row's reach cell must OPEN with one of
#: these; free prose may follow.
REACH_VOCAB: tuple[str, ...] = (
    "customer-callable",
    "write path",
    "journal replay",
    "ops audit",
    "off by default",
    "eval harness",
)

#: Readers whose edge name is COMPUTED, so no literal pattern can carry it.
#: Each maps to the code facts that make the read real — asserted literally and
#: IN THE READER'S OWN SPAN, because these rows are the table's sharpest claim
#: (a grep cannot find them, which is precisely why the enumeration was
#: understated twice). A module-wide substring test would let the fact survive
#: somewhere else in the file; the span is what makes it this reader's read.
_NON_LITERAL: dict[str, tuple[str, ...]] = {
    "ranking.py::SubgraphExpander._neighbors": (
        'f"[r{rel_pattern}]"',
    ),
    "topic_summarization.py::_retrieve_topic_neighborhood": (
        'edge_type = "aboutSubject" if entity_label == "Subject" else "aboutObject"',
        "MATCH (p:Point)-[:{edge_type}]->",
    ),
    # The supersede path on BOTH sides — the SDK write and the MCP dry-run
    # preview — plus the replay delete-leg. All three consume the rel family
    # through a constant or a run-time name, so none of them spells the edge;
    # the LOOP THAT CONSUMES IT is what makes each one a read. Row 20 was the
    # last row decided by the carrier fallback alone (a bare constant MENTION
    # plus any `MATCH` in a 350-line span), which is the same false PASS the
    # `_fetch_object_signals` ordering fix closed for row 17.
    "sdk.py::TortoiseSDK.supersede_point": (
        'structural_rels = SUPERSEDE_STRUCTURAL_RELS',
        'f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(target) "',
    ),
    "mcp_server.py::_preview_supersede": (
        'for rel in SUPERSEDE_STRUCTURAL_RELS:',
        'f"MATCH (o:Point {{id:$old}})-[r:{rel}]->(t) "',
    ),
    "projection/__init__.py::FalkorProjection._rebuild_all_replay": (
        'if (isinstance(etype, str) and etype in DERIVABLE_STRUCTURAL_RELS',
        'or etype not in DERIVABLE_STRUCTURAL_RELS',
        'f"MATCH (old:Point {{id:$old}})-[r:{etype}]->(t) "',
    ),
    # An ALL-EDGE walk: no rel-type filter anywhere, so the edge is read
    # without ever being named. Same rule as `SubgraphExpander._neighbors`
    # (row 3), one module further out — and the reason that row's inclusion
    # cannot be a one-off.
    "navigation.py::_hop_query": (
        'f"MATCH (n:{label} {{{key}:$eid}})-[r]->(m) "',
        'f"MATCH (n:{label} {{{key}:$eid}})<-[r]-(m) "',
    ),
    # The `tortoise_recall(mode="state")` readers: the predicate is the `about*`
    # ALTERNATION, so `aboutObject` is read as one member and the literal string
    # never appears at the site. This is the form that under-counted the
    # customer-callable surfaces three times (#7956's erratum, B2.7, #7948).
    "ranking.py::StateRanker._fetch_object_signals": (
        'about_types = "|".join(ABOUT_EDGE_TYPES)',
        '"OPTIONAL MATCH (p)-[a:" + about_types + "]->(o) "',
    ),
    "sdk.py::TortoiseSDK._state_related_objects": (
        '"MATCH (n:Point)-[a:" + self._ABOUT_TYPES + "]->(t) "',
    ),
    "sdk.py::TortoiseSDK._state_related_points": (
        '"MATCH (p:Point)-[a:" + self._ABOUT_TYPES + "]->(o:Object) "',
    ),
    # Two ALL-EDGE walks found by the third re-derivation: both walk with no
    # rel-type filter (so the edge is read without being named) and both are
    # behind CUSTOMER doors — the delete blast-radius preview under three
    # `dry_run` MCP tools, and the org export behind a REST route. Same rule as
    # rows 3 and 22, which is why neither could be excluded: an unfiltered walk
    # that reports or ships the edges it finds is a read site the ruling's count
    # has to carry.
    "mcp_server.py::_preview_delete_edges": (
        '"MATCH (a)-[r]-(b) WHERE ID(a) IN $nids OR ID(b) IN $nids "',
    ),
    "hosted_api.py::_export_graph_snapshot": (
        '"MATCH (a)-[r]->(b) RETURN labels(a), coalesce(a.id, a.name), "',
    ),
    # Rows 26-27 — the fourth re-derivation, and the same unfiltered-walk class
    # as rows 3, 22, 24 and 25 one layer out: neither names the edge, so neither
    # is visible to the literal census, and both COUNT what they walk (so a
    # `SET`-free derive silently changes the number they report). Not customer
    # doors, so neither was found by asking what a customer can reach: they were
    # found by applying the section's own rule (*an unfiltered walk reads the
    # edge without naming it*) to the code, which is what "the class is
    # enumerated, not excluded" has to mean.
    "subgraph.py::_CYPHER_HUB_DEGREE": (
        '"MATCH (o)-[r]-() "',
        '"RETURN o.id AS hub_id, count(r) AS hub_degree"',
    ),
    "definite_description_backfill.py::CENSUS_QUERY": (
        "OPTIONAL MATCH (o)<-[r]-()",
        "collect(DISTINCT type(r)) AS ref_edges",
    ),
}

#: Facts OUTSIDE the row's own symbol that make a computed-name reader a
#: COMPLETE read, keyed by the row they belong to: `row key -> (the symbol the
#: facts live in, the facts)`. The owner is named explicitly because the pin was
#: keyed on `_neighbors` while the two facts live in `SubgraphExpander.expand`
#: (which computes `rel_pattern` and defaults `completeness`); the assertion was
#: module-wide, so a failure would have blamed `_neighbors` for a default it does
#: not own. Naming the owner makes the assertion span-precise AND the message
#: true.
_NON_LITERAL_SIBLINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ranking.py::SubgraphExpander._neighbors": (
        "SubgraphExpander.expand",
        (
            'rel_pattern = ":IMPL|NAND" if completeness == "core" else ""',
            'completeness: str = "full"',
        ),
    ),
}

_UNITS = (
    "ZERO",
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "ELEVEN",
    "TWELVE",
    "THIRTEEN",
    "FOURTEEN",
    "FIFTEEN",
    "SIXTEEN",
    "SEVENTEEN",
    "EIGHTEEN",
    "NINETEEN",
)

#: Tens, so a count above nineteen can be spelled rather than counted out
#: one word at a time — `_words()` assembles `TWENTY-THREE`, which the table's
#: own prose now needs. A guard that tops out at TWENTY does not fail silently
#: (it asserts), but it would force the document into digits to be readable by
#: its own instrument, which is the wrong way round.
_TENS = {"TWENTY": 20, "THIRTY": 30, "FORTY": 40}

_NUMBER_WORDS: dict[str, int] = {
    word: value for value, word in enumerate(_UNITS)
} | dict(_TENS)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section_13() -> str:
    """§13 only — the rulings section, so a stray table elsewhere cannot pass."""
    text = _doc()
    start = text.index("## 13. Filed from this document")
    end = text.index("## 14. What this document does not decide")
    return text[start:end]


def _rows() -> list[dict[str, object]]:
    rows = [
        {
            "n": int(m.group(1)),
            "module": m.group(2),
            "symbol": m.group(3),
            "reads_for": m.group(4).strip(),
            "reach": m.group(5).strip(),
        }
        for m in _ROW.finditer(_section_13())
    ]
    assert rows, (
        "§13 carries no `module.py::symbol` read-site rows. The table's rows are "
        "the ruling's evidence and the guard parses them; restoring them in the "
        "old `file.py:NNNN` form silently disables this guard."
    )
    # Hand-maintained numbering is this table's documented drift class, and these
    # two directions were the only ones left unpinned: a COPY-PASTE duplicate
    # (same number, or a fresh number with the same reader) raised `len(rows)` —
    # the figure the ruling turns on — while every other test stayed green,
    # because a duplicated row still resolves, still reads, and still satisfies
    # its door. Uniqueness is what makes the count a count of READERS.
    numbers = [int(row["n"]) for row in rows]
    repeated_numbers = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not repeated_numbers, (
        "§13's table re-uses these row numbers: "
        f"{repeated_numbers} — the numeral is what `test_the_prose_counts_match_"
        "the_table` compares its total to, so a copy-pasted row would inflate the "
        "stated count without adding a reader."
    )
    keys = [f"{row['module']}::{row['symbol']}" for row in rows]
    repeated_readers = sorted({key for key in keys if keys.count(key) > 1})
    assert not repeated_readers, (
        "§13 carries more than one row for these readers: "
        f"{repeated_readers} — one reader is one row, or the site count stops "
        "being a count of distinct places the edge is read."
    )
    return rows


def _reach(row: dict[str, object]) -> str:
    cell = re.sub(r"[*`]", "", str(row["reach"])).strip().lower()
    for token in REACH_VOCAB:
        if cell.startswith(token):
            return token
    raise AssertionError(
        f"row {row['n']} ({row['module']}::{row['symbol']}) has reach "
        f"{row['reach']!r}, which does not open with a declared class "
        f"({', '.join(REACH_VOCAB)})"
    )


def _module_path(rel: str) -> Path:
    path = PKG / rel
    assert path.exists(), f"§13 names {rel}, which does not exist under tortoise/"
    return path


def _module_tree(path: Path) -> ast.Module:
    return _parsed(str(path))


@functools.cache
def _parsed(path: str) -> ast.Module:
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _bound_name(node: ast.AST) -> str | None:
    """The name a definition or assignment binds (`C.attr` resolves one level)."""
    name = getattr(node, "name", None)
    if isinstance(name, str):
        return name
    target = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    if isinstance(target, ast.Name):
        return target.id
    return None


def _span(tree: ast.Module, dotted: str) -> tuple[int, int] | None:
    """Resolve `name` or `Class.attr` to its source span, or None."""
    head, _, tail = dotted.partition(".")
    for node in tree.body:
        if _bound_name(node) != head:
            continue
        if not tail:
            return (node.lineno, node.end_lineno or node.lineno)
        if not isinstance(node, ast.ClassDef):
            continue
        for inner in node.body:
            if _bound_name(inner) == tail:
                return (inner.lineno, inner.end_lineno or inner.lineno)
    return None


def _reads_naming_the_edge(tree: ast.Module, lo: int, hi: int) -> bool:
    """True when a Cypher READ clause inside [lo, hi] names the edge literally.

    Read vs write is decided by the LAST clause preceding the pattern inside
    the same string literal, so `MATCH … MERGE (p)-[:aboutObject]->(o)` is a
    WRITE (the MERGE is what the pattern belongs to) and
    `MATCH (e) OPTIONAL MATCH (e)-[:aboutObject]->(o)` is a READ.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not lo <= getattr(node, "lineno", 0) <= hi:
            continue
        match = _EDGE_PATTERN.search(node.value)
        if match is None:
            continue
        clauses = _CLAUSE.findall(node.value[: match.start()])
        if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
            return True
    return False


def _about_family_dominates(elements: tuple[str, ...]) -> bool:
    """Is `about*` the constant's FAMILY, rather than one word among many?

    A rel-type alternation is mostly `about*` members (`ABOUT_EDGE_TYPES`
    6/6, `SUPERSEDE_STRUCTURAL_RELS` 6/8, `TortoiseSDK._ABOUT_TYPES` 6/6).
    A payload-key tuple or an English regex that merely happens to contain the
    word "about" is not (`_RESERVED_EMIT_CALL_PROPS` 1/4 — `aboutLinks`;
    `_BOUND_PHRASES` 1/52; `_RANGE_FILLER` 1/19). A prefix hit on ANY element
    admits those as carrier-shaped candidates; that is a semantic wrongness in
    the candidate set, and it WOULD red the first §13 row whose span mentioned
    one, on a constant that carries no edge at all. ⚠️ Measured: no row's span
    mentions any of the three today, so the red is latent, not observed —
    reverting to the prefix rule keeps the suite at 8 passed. The narrowing is
    therefore a correctness fix for the candidate set, not a repair of a
    currently red test.

    ⚠️ The threshold is measured against `aboutObject` REMOVED, which is the
    state the caller must still detect: dropping it leaves 5/6 of
    `ABOUT_EDGE_TYPES`, so a majority still holds and the membership assertion
    still fires.
    """
    about = sum(1 for element in elements if element.startswith("about"))
    return bool(elements) and about * 2 > len(elements)


@functools.cache
def _carrier_shaped_constants(rel: str) -> dict[str, tuple[str, ...]]:
    """module/class-level constants that can be SPLICED INTO a rel pattern.

    Two shapes qualify: a rel-type list whose elements are all string literals
    (`ABOUT_EDGE_TYPES`, `SUPERSEDE_STRUCTURAL_RELS`) and an `a|b|c`
    alternation string (`TortoiseSDK._ABOUT_TYPES`). Both are what a reader
    interpolates into `-[a:<value>]->`, which is exactly why the edge name can
    live in the constant and nowhere in the query.

    ⛔ Deliberately NOT filtered by `aboutObject`. A selector that keeps only
    constants whose source ALREADY contains the edge name can never fail the
    membership assertion — removing `aboutObject` from
    `ranking.ABOUT_EDGE_TYPES` left the suite green while the mutation the PR
    body claimed reds the carrier test. Candidate SHAPE here is decided by the
    VALUE's form plus the `about*` family, both of which survive `aboutObject`
    itself leaving the constant; membership is then asserted by the caller,
    where it can actually fail.

    The family test is a MAJORITY, not a prefix hit on any element — see
    `_about_family_dominates()` for why the loose form is wrong.
    """

    def splices(node: ast.AST | None) -> tuple[str, ...] | None:
        # `frozenset({...})` / `set({...})` counts too: `DERIVABLE_STRUCTURAL_RELS`
        # is the replay's rel filter and is spelled that way.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"frozenset", "set", "tuple", "list"}
            and len(node.args) == 1
        ):
            return splices(node.args[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return tuple(node.value.split("|")) if "|" in node.value else None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            elements: list[str] = []
            for element in node.elts:
                if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                    return None
                elements.append(element.value)
            return tuple(elements)
        return None

    path = _module_path(rel)
    tree = _parsed(str(path))
    carriers: dict[str, tuple[str, ...]] = {}

    def scan(body, prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body, prefix=f"{node.name}.")
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = _bound_name(node)
                if name is None:
                    continue
                elements = splices(node.value)
                if not elements or not _about_family_dominates(elements):
                    continue
                carriers[prefix + name] = elements

    scan(tree.body)
    return carriers


def _imported_module(rel: str, node: ast.ImportFrom) -> str | None:
    """The `tortoise/`-relative module an `from … import` reads from, or None."""
    module = (node.module or "").replace(".", "/")
    base = Path(rel).parent
    if node.level:
        for _ in range(node.level - 1):
            base = base.parent
    elif module.startswith("tortoise/"):
        module = module[len("tortoise/") :]
    for candidate in (PKG / base / f"{module}.py", PKG / base / module / "__init__.py"):
        if candidate.exists():
            return str(candidate.relative_to(PKG))
    return None


def _carrier_declarations(rel: str) -> dict[str, tuple[str, str]]:
    """Names in scope that carry a spliced rel family, and where they are DECLARED.

    `bare name in this module -> (module that declares it, qualified name)`.
    The import half is not a nicety: `mcp_server.py` takes
    `SUPERSEDE_STRUCTURAL_RELS` from `sdk.py` and `projection/__init__.py`
    takes `DERIVABLE_STRUCTURAL_RELS` from `projection/edges.py`, so a
    same-module scan finds no carrier for those rows and their "the constant IS
    the read" claim goes unasserted — the exact shape of gap this module
    exists to close.
    """
    declarations: dict[str, tuple[str, str]] = {
        qualified.split(".")[-1]: (rel, qualified)
        for qualified in _carrier_shaped_constants(rel)
    }
    for node in ast.walk(_parsed(str(_module_path(rel)))):
        if not isinstance(node, ast.ImportFrom):
            continue
        origin = _imported_module(rel, node)
        if origin is None:
            continue
        for alias in node.names:
            if alias.name not in _carrier_shaped_constants(origin):
                continue
            declarations.setdefault(
                alias.asname or alias.name, (origin, alias.name)
            )
    return declarations


def _literal_read_sites() -> dict[str, list[int]]:
    """Census: module → line numbers of each literal Cypher READ of the edge."""
    found: dict[str, list[int]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Cheap pre-filter: a module that never writes the edge inside a
        # relationship bracket cannot contain a literal read of it.
        if not _EDGE_PATTERN.search(text):
            continue
        tree = _parsed(str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            match = _EDGE_PATTERN.search(node.value)
            if match is None:
                continue
            clauses = _CLAUSE.findall(node.value[: match.start()])
            if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
                found.setdefault(str(path.relative_to(PKG)), []).append(
                    getattr(node, "lineno", 0)
                )
    return found


def _row_symbols(tree: ast.Module) -> list[tuple[str, int, int]]:
    """Every symbol a row may name, with its span: top level plus one class level.

    `Class.attr` is included because that is how rows are keyed; a read inside a
    NESTED function resolves to its enclosing top-level symbol as well (any
    candidate in `named` is enough), which is what keeps
    `assembly.py::docker_resolver_port` a legitimate key for a read inside its
    `alias_objects` closure.
    """
    symbols: list[tuple[str, int, int]] = []
    for node in tree.body:
        name = _bound_name(node)
        if name is None:
            continue
        symbols.append((name, node.lineno, node.end_lineno or node.lineno))
        if isinstance(node, ast.ClassDef):
            for inner in node.body:
                inner_name = _bound_name(inner)
                if inner_name is not None:
                    symbols.append(
                        (
                            f"{name}.{inner_name}",
                            inner.lineno,
                            inner.end_lineno or inner.lineno,
                        )
                    )
    return symbols


def _words(cell: str) -> int:
    cell = cell.strip().upper()
    if cell.isdigit():
        return int(cell)
    if "-" in cell:  # TWENTY-THREE
        tens, _, units = cell.partition("-")
        assert (
            tens in _TENS and units in _NUMBER_WORDS and _NUMBER_WORDS[units] < 10
        ), f"{cell!r} is not a spelled number this guard knows"
        return _TENS[tens] + _NUMBER_WORDS[units]
    assert cell in _NUMBER_WORDS, f"{cell!r} is not a spelled number this guard knows"
    return _NUMBER_WORDS[cell]


# ── 1a. the pattern that decides everything else ──────────────────────────


def test_the_edge_pattern_sees_the_whole_about_family():
    """The census pattern must see a LONG relationship alternation.

    ⚠️ **This was a false PASS, and it was the sharpest one in the guard.**
    `_EDGE_PATTERN` is not a detail: the same pattern decides the census
    (`_literal_read_sites`), whether a row's symbol reads the edge
    (`_reads_naming_the_edge`) and whether a module is worth parsing at all. A
    60-character window on each side of the edge name matched
    `[:aboutSubject|aboutObject]` (15 characters before it) and did NOT match
    the six-member `about*` family `ranking.ABOUT_EDGE_TYPES` actually uses
    (63 before it) — so a reader written in this repo's own established
    alternation style was in neither the census nor any row check, while every
    test stayed green. The docstring's "lower bound" disclaimer covers readers
    that COMPUTE or CARRY the name; this one spells it out, late in the
    bracket.
    """
    unseen = [
        sample
        for sample in _EDGE_PATTERN_MUST_MATCH
        if _EDGE_PATTERN.search(sample) is None
    ]
    assert not unseen, (
        "the census pattern no longer sees these literal Cypher brackets:\n  - "
        + "\n  - ".join(unseen)
        + "\nA relationship bracket is bounded by `]`; do not bound it by a "
        "character count — a long `about*` alternation is a literal read that "
        "nothing else in this file can see."
    )
    # ...and the widening must not become a wildcard: a bare mention is not a
    # bracket, a JSON array is not a relationship, and a bracket cannot span a
    # newline inside a folded query.
    overreach = [
        sample
        for sample in _EDGE_PATTERN_MUST_NOT_MATCH
        if _EDGE_PATTERN.search(sample) is not None
    ]
    assert not overreach, (
        "the census pattern matches strings that are NOT Cypher read sites:\n  - "
        + "\n  - ".join(overreach)
        + "\nA relationship bracket is dash-delimited; without that anchor a "
        "JSON payload or a prose mention becomes a phantom read site."
    )


# ── 1. every row names a symbol that exists ────────────────────────────────


def test_every_row_names_a_symbol_that_exists():
    missing = []
    for row in _rows():
        tree = _module_tree(_module_path(str(row["module"])))
        if _span(tree, str(row["symbol"])) is None:
            missing.append(f"row {row['n']}: {row['module']}::{row['symbol']}")
    assert not missing, (
        "§13's read-site table names symbols that do not exist at HEAD:\n  "
        + "\n  ".join(missing)
        + "\nThe document's rule is that the SYMBOL is authoritative, so a row "
        "whose symbol cannot be resolved is a stale row: re-derive it from the "
        "code rather than moving the line number."
    )


# ── 2. every named reader still reads the edge ─────────────────────────────


def test_every_named_reader_still_reads_the_edge():
    stale = []
    for row in _rows():
        key = f"{row['module']}::{row['symbol']}"
        module_path = _module_path(str(row["module"]))
        tree = _module_tree(module_path)
        span = _span(tree, str(row["symbol"]))
        if span is None:
            stale.append(f"row {row['n']}: {key} — symbol does not resolve")
            continue
        if _reads_naming_the_edge(tree, span[0], span[1]):
            continue
        span_text = "\n".join(
            module_path.read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        # A computed-name row is decided by ITS OWN FACTS, IN ITS OWN SPAN —
        # and there is NO carrier fallback behind it any more.
        #
        # Order mattered and was wrong once: `_fetch_object_signals` sits in a
        # module whose `ABOUT_EDGE_TYPES` constant carries the edge name, so a
        # carrier branch that ran FIRST went green on a name that merely
        # APPEARS in the symbol — two unrelated `MATCH (o:Object)` /
        # `ABOUT_EDGE_TYPES` lines were enough. The consuming line
        # (`"OPTIONAL MATCH (p)-[a:" + about_types + "]->(o) "`) could be
        # deleted and every test stayed green, leaving row 17 naming code that
        # no longer reads the edge.
        #
        # That fallback is now DELETED, not just ordered after the facts: it was
        # a latent fail-open for any FUTURE row (a bare mention of a
        # carrier-shaped constant plus any `MATCH` anywhere in a 300-line span
        # was enough), and it decided none of the rows that exist — every carrier
        # row is pinned in `_NON_LITERAL` by its CONSUMING line instead. Deleting
        # it makes this test fail CLOSED: a row is now either a literal read, a
        # pinned computed-name read, or red.
        if key in _NON_LITERAL:
            missing = [fact for fact in _NON_LITERAL[key] if fact not in span_text]
            if missing:
                stale.append(
                    f"row {row['n']}: {key} — computed-name reader lost the "
                    f"fact that makes it a read: {missing!r}"
                )
            continue
        stale.append(
            f"row {row['n']}: {key} — the named symbol does not read the edge, "
            "and it has no computed-name pin"
        )
    assert not stale, (
        "§13's table names readers that no longer read `aboutObject`:\n  "
        + "\n  ".join(stale)
        + "\nA consumer-callable surface listed here is the ruling's argument; a "
        "row pointing at code that does not read the edge is how the enumeration "
        "was wrong before (#7948 body, B2.7, #7956)."
    )


def test_the_computed_name_readers_are_still_complete_reads():
    """The rows no grep can find must keep the facts that make them reads.

    The facts are asserted INSIDE the span of the symbol that OWNS them —
    `SubgraphExpander.expand` computes `rel_pattern` and defaults
    `completeness`, while row 3 names `_neighbors`, which only receives the
    pattern. Module-wide, the assertion fired but blamed the wrong symbol; the
    owner is named in the pin so this both narrows the check and makes the
    failure message true.
    """
    for row_key, (owner, facts) in _NON_LITERAL_SIBLINGS.items():
        module_rel, _, _ = row_key.partition("::")
        path = _module_path(module_rel)
        tree = _module_tree(path)
        span = _span(tree, owner)
        assert span is not None, (
            f"{module_rel} no longer defines `{owner}`, the symbol that owns the "
            f"facts making {row_key} a computed-name read — re-derive the row."
        )
        span_text = "\n".join(
            path.read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        for fact in facts:
            assert fact in span_text, (
                f"`recall_subgraph`'s default `completeness=\"full\"` is what "
                f"makes `SubgraphExpander._neighbors` read `aboutObject` without "
                f"naming it. {fact!r} is gone from `{module_rel}::{owner}` (the "
                "symbol that computes it), so the row's reach claim must be "
                "re-derived."
            )


def test_every_computed_name_pin_owns_a_row():
    """A pinned computed-name reader with NO row is silently IGNORED.

    ⚠️ **This direction was the fail-open half of the guard, and it was found by
    mutation.** The census (`test_no_read_site_lacks_a_row`) holds the
    code→row direction for *literal* readers only — it cannot see the readers
    that make up `_NON_LITERAL` at all, which is the entire reason that table
    exists. Nothing held the reverse for them: `_NON_LITERAL` was read only
    WHILE visiting a row, so a key with no row was never looked at. Delete row
    3 (`SubgraphExpander._neighbors`) and row 22 (`_hop_query`) from §13, lower
    the two stated counts to match, and every other test stayed GREEN — the
    table then omits two customer-callable read sites, including both all-edge
    walks that stayed invisible through three manual passes. That is the exact
    under-count this file exists to end, reachable through the guard itself.

    So: every pin must name a row that carries it. Deleting a row without
    deleting its pin now reds HERE instead of quietly shrinking the census.
    """
    named = {f"{row['module']}::{row['symbol']}" for row in _rows()}
    pins = set(_NON_LITERAL) | set(_NON_LITERAL_SIBLINGS)
    assert pins, (
        "both computed-name pin tables are empty — the guard has stopped "
        "pinning the readers no grep can find, and this direction now measures "
        "nothing."
    )
    orphans = sorted(pins - named)
    assert not orphans, (
        "these computed-name pins name readers that no §13 row carries:\n  "
        + "\n  ".join(orphans)
        + "\nA pin with no row pins nothing: the census is blind to these "
        "readers (they never spell the edge), so a dropped row is otherwise "
        "silent. Restore the row, or delete the pin if the reader is gone."
    )


# ── 3. no module reads the edge without a row ──────────────────────────────


def test_no_read_site_lacks_a_row():
    """Every literal READ of the edge is absorbed by a row — BY SYMBOL.

    ⚠️ **Module granularity was a false PASS and this is the fix.** The
    historical under-count lived INSIDE already-named modules: `ranking.py` and
    `sdk.py` were both named by the pre-fix table while four `recall_state`
    readers were missing from them. A module-level census is blind to exactly
    that shape — appending a `MATCH (p:Point)-[:aboutObject]->(o:Object)` method
    to `sdk.py` adds a line to `census["sdk.py"]`, `sdk.py` is already named,
    `len(rows)` is unchanged, and every test stayed green while §13's decisive
    figure (the pre-fix “TWENTY read sites”) was wrong. Keyed on `module.py::symbol`, the
    same edit is red until a row names the new symbol.
    """
    named = {f"{row['module']}::{row['symbol']}" for row in _rows()}
    census = _literal_read_sites()
    assert census, (
        "the census found NO literal Cypher read of `aboutObject` anywhere under "
        "tortoise/ — the pattern or the clause rule was changed, and this guard "
        "has stopped measuring anything."
    )
    unnamed: list[str] = []
    for module, lines in sorted(census.items()):
        symbols = _row_symbols(_module_tree(_module_path(module)))
        for line in lines:
            candidates = [name for name, lo, hi in symbols if lo <= line <= hi]
            if any(f"{module}::{name}" in named for name in candidates):
                continue
            unnamed.append(
                f"{module}:{line}  (inside {', '.join(candidates) or 'module level'})"
            )
    assert not unnamed, (
        "these READ sites of the `aboutObject` edge are absorbed by no §13 row:\n  "
        + "\n  ".join(unnamed)
        + "\n§13's list is the evidence the KEEP ruling rests on. A reader added "
        "beside an existing row is the drift B2.7 and #7956 both under-reported, "
        "and it is invisible to a module-level census."
    )


# ── 4. the prose counts equal the table ────────────────────────────────────


def test_the_prose_counts_match_the_table():
    section = _section_13()
    rows = _rows()
    sites = re.search(r"\b([A-Z]+(?:-[A-Z]+)?|\d+) read sites\b", section)
    assert sites, "§13 must state how many read sites it enumerates"
    callable_sites = re.search(
        r"\b([A-Z]+(?:-[A-Z]+)?|\d+) of them are CUSTOMER-CALLABLE\b", section
    )
    assert callable_sites, "§13 must state how many of those are customer-callable"

    assert _words(sites.group(1)) == len(rows), (
        f"§13 says {sites.group(1)} read sites and its table carries {len(rows)} "
        "rows — the count is the ruling's argument, so it is asserted, not "
        "prose (it was wrong twice: B2.7 said two customer-callable sites, "
        "#7956 corrected it to four)."
    )
    counted = sum(1 for row in rows if _reach(row) == "customer-callable")
    assert _words(callable_sites.group(1)) == counted, (
        f"§13 says {callable_sites.group(1)} customer-callable read sites and "
        f"{counted} rows are classed customer-callable"
    )


# ── 5. every customer-callable row reaches a declared door ─────────────────

#: A door, as a customer-callable reach cell declares one. The three forms are
#: the three shapes counted, and each is INDEPENDENTLY CALLABLE: an MCP tool, a
#: public SDK method, or a served REST route.
_DOOR_SDK = re.compile(r"SDK `([A-Za-z_]\w*)`")
_DOOR_MCP = re.compile(r"MCP `([A-Za-z_]\w*)`")
_DOOR_REST = re.compile(r"REST `((?:GET|POST|PUT|PATCH|DELETE) ([^`]+))`")

#: Any backticked span that LOOKS like a hosted REST path. Used to refuse a door
#: written so loosely that the resolver cannot check it (the shape row 5 had:
#: *"hosted REST (`/v1/…/topic`)"*, which no route check ever saw).
_REST_LOOKING = re.compile(r"`([^`]*/v1/[^`]*)`")


def _doors(cell: str) -> dict[str, str]:
    """The doors a reach cell declares: `identifier -> kind`.

    ⚠️ **Each door is counted SEPARATELY, and that rule is stated in both
    documents** — because the alternative is a hand-count nobody can reproduce.
    An MCP tool and the SDK method it forwards to are two independently
    callable entry points (a customer may call either), and the three `dry_run`
    delete tools are three. Folding some pairs and not others is how *"TWELVE
    distinct doors"* came to describe a table that names sixteen: nothing
    derived it, so nothing could disagree with it.
    """
    doors = {f"MCP {name}": "mcp" for name in _DOOR_MCP.findall(cell)}
    doors |= {f"SDK {name}": "sdk" for name in _DOOR_SDK.findall(cell)}
    doors |= {f"REST {match.group(2)}": "rest" for match in _DOOR_REST.finditer(cell)}
    return doors


def _loose_rest_doors(cell: str) -> list[str]:
    """Backticked `/v1/…` paths NOT written in the strict ``REST `VERB path` `` form."""
    strict = [match.span(1) for match in _DOOR_REST.finditer(cell)]
    return [
        match.group(1)
        for match in _REST_LOOKING.finditer(cell)
        if not any(lo <= match.start(1) and match.end(1) <= hi for lo, hi in strict)
    ]


def _customer_callable_doors() -> dict[str, list[int]]:
    """Door identifier -> the row numbers that reach the edge through it."""
    doors: dict[str, list[int]] = {}
    for row in _rows():
        if _reach(row) != "customer-callable":
            continue
        for door in sorted(_doors(str(row["reach"]))):
            doors.setdefault(door, []).append(int(row["n"]))
    return doors



def _public_sdk_methods() -> set[str]:
    tree = _module_tree(PKG / "sdk.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                inner.name
                for inner in node.body
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not inner.name.startswith("_")
            }
    raise AssertionError("TortoiseSDK not found in tortoise/sdk.py")


def _registered_tools() -> set[str]:
    tree = _module_tree(PKG / "tool_registry.py")
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ToolDefinition"
        ):
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(str(keyword.value.value))
    assert names, "no ToolDefinition names found in tortoise/tool_registry.py"
    return names


def _rest_routes() -> set[str]:
    """The paths this repo serves over its own hosted REST surface.

    A customer-callable row may reach the edge through REST instead of an SDK
    method or an MCP tool: §13 row 25's reader is the org export, served by
    `@app.get("/v1/organizations/{org_id}/export")` in `hosted_api.py`. Without
    this the door test would either refuse a legitimate REST-only row or be
    widened to `assert path in module text`, which is an existence check on a
    string rather than on a route — the weaker form that let row 1 name a door
    that could not reach its reader.
    """
    tree = _module_tree(PKG / "hosted_api.py")
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "app"
                and func.attr in {"get", "post", "put", "patch", "delete"}
            ):
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.add(str(decorator.args[0].value))
    assert routes, "no `@app.<verb>(path)` routes found in tortoise/hosted_api.py"
    return routes


def test_every_customer_callable_row_reaches_a_declared_door():
    sdk_methods = _public_sdk_methods()
    tools = _registered_tools()
    routes = _rest_routes()
    broken: list[str] = []

    for row in _rows():
        if _reach(row) != "customer-callable":
            continue
        label = f"row {row['n']} ({row['module']}::{row['symbol']})"
        cell = str(row["reach"])
        doors = _doors(cell)
        # ⚠️ FAIL CLOSED on a REST door written in no form this test can resolve.
        # Measured before this arm existed: rewriting row 5's *"hosted REST
        # (`/v1/…/topic`)"* to `/v1/…/nonexistent` left ALL ten tests green,
        # because the strict form was the only one parsed and the loose one was
        # silently skipped — the "weaker form that let row 1 name a door that
        # could not reach its reader" this arm exists to prevent, one shape over.
        broken.extend(
            f"{label} — `{span}` is a REST path in NO form this test can "
            "resolve: write the door as REST `GET /v1/…` so it is checked "
            "against the routes in hosted_api.py"
            for span in _loose_rest_doors(cell)
        )
        if not doors:
            if not _loose_rest_doors(cell):
                broken.append(
                    f"{label} — names no door (SDK/MCP/REST backticked name)"
                )
            continue
        for door in sorted(doors):
            kind, name = door.split(" ", 1)
            if kind == "SDK" and name not in sdk_methods:
                broken.append(
                    f"{label} — SDK `{name}` is not a public TortoiseSDK method"
                )
            elif kind == "MCP" and name not in tools:
                broken.append(f"{label} — MCP `{name}` is not a registered tool")
            elif kind == "REST" and name not in routes:
                broken.append(
                    f"{label} — REST `{name}` is not a route in hosted_api.py"
                )

    assert not broken, (
        "§13 classes these rows customer-callable without naming a door that "
        "exists:\n  " + "\n  ".join(broken)
    )


#: A stated door count: `SIXTEEN distinct doors`, `sixteen doors`, `SIXTEEN
#: doors`. A HISTORICAL figure ("`#7956` corrected it to four *doors*") is
#: deliberately not matched — it carries emphasis between the numeral and the
#: noun, and it describes a past state rather than the table in front of it.
_STATED_DOOR_COUNT = re.compile(r"\b([A-Z]+(?:-[A-Z]+)?|\d+) (?:distinct )?doors?\b")


def _as_count(token: str) -> int | None:
    """`SIXTEEN` / `16` / `TWENTY-SIX` -> int; anything else (a NOUN that
    happens to precede the word "door" — *"no MCP door exists"*) -> None."""
    cell = token.strip().upper()
    if cell.isdigit():
        return int(cell)
    if "-" in cell:
        tens, _, units = cell.partition("-")
        if tens in _TENS and units in _NUMBER_WORDS and _NUMBER_WORDS[units] < 10:
            return _TENS[tens] + _NUMBER_WORDS[units]
        return None
    return _NUMBER_WORDS.get(cell)


def _stated_door_counts(section: str) -> set[int]:
    counts = {
        count
        for match in _STATED_DOOR_COUNT.finditer(section)
        if (count := _as_count(match.group(1))) is not None
    }
    return counts


def _spelled(value: int) -> str:
    for word, number in _NUMBER_WORDS.items():
        if number == value:
            return word
    raise AssertionError(f"{value} has no spelled form this guard knows")


def _section_13_8() -> str:
    """`EXTRACTOR-V4-ARCHITECTURE.md` §13.8 — the sibling that carries §13's fact."""
    text = (ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    start = text.index("### 13.8 What this does to our design")
    return text[start : text.index("## 14. Not established", start)]


def test_the_stated_door_count_matches_the_table():
    """The blast-radius DOOR figure must be derived from the table, or it is prose.

    ⚠️ **"TWELVE distinct doors" did not reconcile with the table it certifies,
    and nothing guarded it.** The 12 customer-callable reach cells name SIXTEEN
    distinct door identifiers; the folding rule was applied inconsistently
    (`search`/`tortoise_fts_query` folded into one door, the three `dry_run`
    delete tools left as three); and `§13`'s own justification — *"TWELVE sites
    behind those twelve doors, because `recall_state` alone reads the edge on
    FOUR code paths"* — requires doors < sites and then asserts doors == sites.
    Measured: rewriting the figure to `four` (STORAGE) or `TEN` (EXTRACTOR) in
    both documents left all ten tests green, which is exactly the class of
    unguarded hand-count this file exists to end.

    So the rule is now STATED in both documents — *a door is one independently
    callable entry point: an MCP tool, a public SDK method, or a served REST
    route; counted once per site that reaches it* — and both documents' stated
    total, and the per-kind breakdown, are asserted against the table.
    """
    doors = _customer_callable_doors()
    kinds: dict[str, int] = {}
    for door in doors:
        kind = door.split(" ", 1)[0].lower()
        kinds[kind] = kinds.get(kind, 0) + 1
    total = len(doors)
    spelled = _spelled(total)

    section = _section_13()
    stated = _stated_door_counts(section)
    assert stated, (
        "§13 no longer states a door count — the blast-radius argument rests on "
        "it: restore the figure (derived from this table), or delete this test "
        "with a reason."
    )
    assert stated == {total}, (
        f"§13 states {sorted(stated)} distinct door(s) and its customer-callable "
        f"reach cells name {total} ({spelled}): "
        + ", ".join(f"{len(rows)}× {door}" for door, rows in sorted(doors.items()))
        + "\nThe figure is the ruling's blast-radius argument, so it is derived "
        "from the table, not hand-counted."
    )
    # The breakdown must sit in the SAME sentence as the total: a bare "SIXTEEN
    # doors" with no fold stated is the hand-count this replaced.
    tails = [
        section[match.end() : match.end() + 320].lower()
        for match in re.finditer(rf"{spelled} distinct doors", section, re.I)
    ]
    assert tails, (
        f"§13 states no `{spelled} distinct doors` sentence — the total must be "
        "stated in prose a reader can find, not only asserted here."
    )
    missing = [
        f"{_spelled(kinds.get(kind, 0))} {noun}"
        for kind, noun in (
            ("mcp", "MCP tools"),
            ("sdk", "SDK methods"),
            ("rest", "REST routes"),
        )
        if not any(
            f"{_spelled(kinds.get(kind, 0))} {noun}".lower() in tail for tail in tails
        )
    ]
    assert not missing, (
        "§13 must state the door breakdown beside the total — "
        + ", ".join(f"`{phrase}`" for phrase in missing)
        + " is missing from every sentence carrying the count. The breakdown is "
        "what STATES the folding rule (MCP tool and SDK twin counted separately, "
        "the three `dry_run` delete tools counted as three); without it the total "
        "is not reproducible from the table."
    )
    sibling = _stated_door_counts(_section_13_8())
    assert sibling == {total}, (
        f"EXTRACTOR-V4-ARCHITECTURE.md §13.8 states {sorted(sibling)} distinct "
        f"door(s) and §13's table derives {total} ({spelled}) — the two "
        "documents must state ONE fact."
    )


# ── 6. the carrier constants really carry the edge ─────────────────────────


def test_every_carrier_constant_a_row_reads_through_still_names_the_edge():
    """The constant IS the edge name — its membership is the read, not a detail.

    Three `recall_state` helpers, the supersede path (SDK and its MCP dry
    run) and the replay delete-leg name `aboutObject` in a constant, never in a
    query, so a literal census (and any grep) misses them. That is how the
    customer-callable count stayed at four through three reviews. If the edge
    leaves one of these constants, the §13 row keeps resolving while the site
    stops reading the edge — an under-count in the direction that matters.

    The candidates come from `_carrier_shaped_constants()` (shape + the `about*`
    family — never filtered on the edge name this test asserts). Deleting the
    edge from `ABOUT_EDGE_TYPES` therefore reds here — measured:
    `TORTOISE_TEST_CARVE_OUT=1 uv run pytest
    tests/test_7948_about_object_read_sites.py -q` → 1 failed with
    `row 17: … ABOUT_EDGE_TYPES`.
    """
    broken: list[str] = []
    for row in _rows():
        module = str(row["module"])
        tree = _module_tree(_module_path(module))
        span = _span(tree, str(row["symbol"]))
        if span is None:
            continue  # covered by the symbol test
        source = "\n".join(
            _module_path(module).read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        for bare, (declaring, qualified) in sorted(
            _carrier_declarations(module).items()
        ):
            if not re.search(rf"\b{re.escape(bare)}\b", source):
                continue
            declared_tree = _module_tree(_module_path(declaring))
            declaration = _span(declared_tree, qualified)
            assert declaration is not None, f"{declaring}::{qualified} does not resolve"
            declared = "\n".join(
                _module_path(declaring)
                .read_text(encoding="utf-8")
                .splitlines()[declaration[0] - 1 : declaration[1]]
            )
            if "aboutObject" not in declared:
                broken.append(
                    f"row {row['n']}: {module}::{row['symbol']} reads through "
                    f"{declaring}::{qualified}"
                )
    assert not broken, (
        "these readers name the edge through a constant that NO LONGER contains "
        "`aboutObject`:\n  " + "\n  ".join(broken)
    )


# ── 7. the sibling document states the same fact ───────────────────────────

#: Where §13.8's customer-callable ENUMERATION starts and stops. The first is the
#: sentence that states the figure; the second is the next claim in the same
#: paragraph, so everything between them is the list the figure is about. The two
#: markers are asserted, not `.index()`-ed blind: a reworded section fails with a
#: reason here instead of an IndexError.
_13_8_ENUMERATION = (
    "sites are customer-callable",
    "Two more are default-ON on the ingestion path",
)

#: `` `token` `` — the unit §13.8 names a reader (or a door) with.
_BACKTICKED = re.compile(r"`([^`\n]+)`")


def _extractor_doc() -> str:
    return (
        ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
    ).read_text(encoding="utf-8")


def _reader_reach() -> dict[str, tuple[int, str]]:
    """§13's base reader name (`_neighbors`) -> (row number, reach class).

    The base name is the symbol's last dotted component, which is how §13.8 names
    a reader; a duplicate base across rows would make membership ambiguous, so it
    is asserted instead of silently overwritten.
    """
    reach: dict[str, tuple[int, str]] = {}
    for row in _rows():
        base = str(row["symbol"]).rsplit(".", 1)[-1]
        assert base not in reach, (
            f"rows {reach.get(base, (None,))[0]} and {row['n']} both resolve to "
            f"`{base}`, so §13.8 cannot name one of them unambiguously — key the "
            "reader by its full `module.py::symbol` in the document."
        )
        reach[base] = (int(row["n"]), _reach(row))
    return reach


def _readers_named(text: str, reach: dict[str, tuple[int, str]]) -> list[str]:
    """The §13 reader base names a passage names, in order of first appearance."""
    named: list[str] = []
    for token in _BACKTICKED.findall(text):
        token = re.sub(r"\(\)$", "", token.strip())
        # `module.py::Symbol.attr` and `Klass.attr` both reduce to `attr` — the
        # module prefix must be dropped BEFORE the last-dot split, or a
        # `module.py::name` token reduces to `py::name`.
        base = token.split("::", 1)[-1].rsplit(".", 1)[-1]
        if base in reach and base not in named:
            named.append(base)
    return named


def test_the_extractor_document_agrees_on_the_count():
    """§13.8 states the same fact as §13 — the NUMBER and the ENUMERATION.

    Two live documents stating one fact is how this under-count survived three
    reviews: a repair that corrects §13 and leaves §13.8 asserting the old
    number re-creates exactly the contradiction `#7956` had to correct. So the
    sibling must state the §13 count, and must name the state-recall readers that
    the four-site census missed.

    ⚠️ **The number alone was not enough, and that is the fail-open this closes.**
    §13.8 stated TWELVE and then ENUMERATED thirteen: it listed
    `supersede_point` (`sdk.TortoiseSDK.supersede_point`) among the
    customer-callable sites while §13's authoritative table classes that reader
    `write path` (row 20), the argument running *"the transfer it previews"*. The
    numeral matched, the enumeration contradicted it, and every test stayed
    green — the sibling had one more customer-callable site than the count the
    ruling turns on. Membership is therefore asserted in BOTH directions:

    * every customer-callable row's reader is named in §13.8's enumeration, and
    * §13.8's enumeration names no reader §13 classes as anything else.

    The word-order half is the other false PASS: the count is stated twice, in two
    orders — *"TWELVE sites are customer-callable"* (§13.8) and *"TWELVE
    *customer-callable* sites DO"* (§13.6) — and the original pattern matched only
    the first, so editing one and leaving the other stale stayed green. Both
    orders are read, and both must equal §13's count.
    """
    extractor = _extractor_doc()
    reach = _reader_reach()
    callable_rows = {base for base, (_, cls) in reach.items() if cls == "customer-callable"}
    counted = len(callable_rows)
    assert counted, "no §13 row is classed customer-callable — the guard is vacuous"
    word = next(word for word, value in _NUMBER_WORDS.items() if value == counted)

    # (a) the stated figure, in EITHER word order the sibling uses.
    stated = {
        _words(match.group(1))
        for pattern in (
            r"\b([A-Z]+(?:-[A-Z]+)?|\d+) (?:sites?|of them) (?:are|is) \*?customer-callable",
            r"\b([A-Z]+(?:-[A-Z]+)?|\d+) \*?customer-callable\*? (?:sites?|read sites?)\b",
        )
        for match in re.finditer(pattern, extractor, re.I)
    }
    assert stated == {counted}, (
        f"EXTRACTOR-V4-ARCHITECTURE.md states customer-callable counts {sorted(stated)} "
        f"and §13's table counts {counted} ({word}) — the two documents must state "
        "ONE fact, and it must be the one the table enumerates. Both word orders are "
        "read: `N sites are customer-callable` and `N *customer-callable* sites`."
    )

    # (b) the ENUMERATION, which the number alone cannot hold.
    start_marker, end_marker = _13_8_ENUMERATION
    assert start_marker in extractor, (
        f"§13.8 no longer states its count with {start_marker!r} — this guard "
        "reads the enumeration that follows it; re-point the marker or restore the "
        "sentence."
    )
    start = extractor.index(start_marker)
    assert end_marker in extractor[start:], (
        f"§13.8's customer-callable enumeration is no longer closed by "
        f"{end_marker!r}, so this guard cannot tell where the list ends — "
        "restore the marker or re-point it."
    )
    enumeration = extractor[start : extractor.index(end_marker, start)]
    named = set(_readers_named(enumeration, reach))

    extra = sorted(named - callable_rows)
    assert not extra, (
        "EXTRACTOR-V4-ARCHITECTURE.md §13.8 names these readers among the "
        f"{word} customer-callable sites and §13's table classes them otherwise:\n  - "
        + "\n  - ".join(
            f"`{base}` — §13 row {reach[base][0]} is `{reach[base][1]}`" for base in extra
        )
        + "\nThe two documents must carry the same reach for the same reader: "
        "either drop it from the sibling's list, or reclassify the row in §13 and "
        "move BOTH counts."
    )
    missing = sorted(callable_rows - named)
    assert not missing, (
        f"§13.8 says {word} customer-callable sites and does not name these "
        "customer-callable readers §13's table carries:\n  - " + "\n  - ".join(missing)
        + "\nA count is only as good as the list under it — the sibling is the "
        "second copy of this ruling, so it must enumerate the same readers."
    )


# ── 9. §13 carries the line numbers its own sentence says it does not ───────

#: A `file.py:NNNN` — or `file.py:NNNN-MMM` — line citation, the form §13's own
#: sentence retires. A symbol citation (`module.py::symbol`) does NOT match: its
#: colon is doubled, so it is never followed by a digit.
_LINE_CITATION = re.compile(r"[A-Za-z0-9_./-]+\.py:[0-9]+")


def test_section_13_carries_no_line_citations():
    """§13 states it carries no line numbers, so it must carry none.

    The sentence is load-bearing — it is what tells a reader the SYMBOL is
    authoritative and the line is no longer kept — and two citations survived
    underneath it, both already rotted. A guard cannot check that a symbol sits
    at the right line in a document that says it stopped recording lines; it
    can check the document does what it says.
    """
    section = _section_13()
    assert "Line numbers are deliberately **not** carried" in section, (
        "§13 no longer carries the sentence this guard exists to enforce — "
        "re-read the paragraph: restore it, or delete this test with a reason."
    )
    assert _rows(), "§13's symbol-keyed rows are missing (see `_rows`)."
    text = _doc()
    offset = text.index("## 13. Filed from this document")
    surplus = [
        (text.count("\n", 0, offset + match.start()) + 1, match.group(0))
        for match in _LINE_CITATION.finditer(section)
    ]
    assert not surplus, (
        "§13 states it carries no line numbers, and it still carries these — "
        "symbol-key them (`module.py::symbol`), which is what the sentence "
        "promises, because a line in a design document rots and nothing "
        "re-derives it:\n  - "
        + "\n  - ".join(f"line {line}: `{cite}`" for line, cite in surplus)
    )
