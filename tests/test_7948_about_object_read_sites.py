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
13. `test_section_13_pins_every_count_to_a_ref` — a figure §13 states about a
   MOVING ref (the branch tip) is a figure with no pin, so it rots on the next
   merge and nothing here re-derives it. Measured: the test-file count was
   written down as "60" for the lane's tip and the merge head measured 62, the
   delta being one unrelated test file that landed on `main` — the same
   unguarded hand-copy this whole section exists to end, in the one sentence of
   the changed passage no other arm read. Now every count is quoted with the
   sha or date it was measured at, and a `… at the head` form reds.
14. `test_every_literal_bracket_is_classified_not_just_the_first` — a string
   literal may hold MORE than one `aboutObject` bracket, and write-vs-read is
   decided per bracket. Classifying only the first let a query shaped
   write-then-read hide its read from this whole file.
15. `test_the_count_reader_sees_every_statement_not_just_the_first` — §13
   states the customer-callable figure TWICE, so the arm that checks it
   collects with `finditer` and compares SETS. A `re.search` binds the first
   statement only, and the second copy is then read by nothing.
16. `test_no_untyped_walk_lacks_a_row_or_a_stated_exclusion` — the
   UNTYPED-WALK class is now DERIVED from the tree, not hand-listed: every
   relationship bracket carrying no type at all, in a read clause, must own a
   §13 row or appear in `_EXCLUDED_UNTYPED` **with the code fact that bounds
   that walk** — in its own clause, or in an earlier clause of the same query
   that binds one of its endpoints (Cypher matches each `MATCH` pattern
   independently, so a constraint written for another walk does not count). This
   is the class that was wrong five times running — each pass re-applied §13's
   inclusion rule by hand and reached a different count, the last one finding 27
   sites where the tree had 33 (the backup's all-edge dump and the
   `backfill_v25` count among the six). **The same arm also censuses the
   FRAGMENT half of that class** — a dash-delimited part carrying no clause of
   its own (`_WALK = "-[r]->"`, then `"MATCH (n)" + _WALK + "(m)"`), which the
   query-shaped census cannot see and which is owned by a row, an exclusion, or a
   `_NON_LITERAL` pin. That census existed for three review cycles and was never
   CALLED, so its docstring was a claim nothing executed and the bypass it
   describes stayed open; it is wired here, with a synthetic part-assembled
   fixture so the detection is falsifiable.
17. `test_every_stated_exclusion_is_still_true` — an exclusion is not a
   promise: its code facts are asserted inside the excluded symbol's span, and
   (16) requires the fact that bounds THIS walk to sit in the walk's own clause —
   so removing the constraint reds as an unowned read rather than silently
   re-entering the class, and a second walk cannot inherit the first one's
   exclusion.
18. `test_a_docstring_is_prose_and_not_a_read_site` — a docstring is prose, not
   a query. The literal halves used to accept every string constant while the
   untyped census filtered prose, so a row kept *"the named symbol really reads
   the edge"* after the code stopped reading it (paste the old query into the
   docstring — measured green on the pre-fix predicate). One rule now, both
   directions: prose is dropped, the same query in the body is still counted.
19. `test_the_prior_art_points_at_the_table_instead_of_restating_the_count` —
   B2.7 of `REVIEW-CONSOLIDATED-2026-09-23.md` is cited as prior art and stated
   the customer-callable count a third time; this PR corrected §13 and §13.8 and
   left that copy stale. The durable form is a POINTER, so the row must name
   §13's table and mark its own figure superseded.
20. The mechanical floors: every arm that iterates a derived set asserts it
   EXAMINED something (`assert census`, `assert pins`, `assert _EXCLUDED_UNTYPED`,
   `assert fragments`, and the carrier arm's `assert checked`). A predicate that
   silently stops recognising its candidates turns the arm green on an assertion
   it never made — the carrier arm was exactly that before its floor.

⚠️ **What this deliberately does NOT claim.** The LITERAL census is a LOWER
BOUND: it sees a relationship pattern naming the edge, so a reader that
computes or carries the name (`_neighbors`, `_hop_query`,
`_retrieve_topic_neighborhood`, `_preview_supersede`, `_rebuild_all_replay`,
`supersede_point`, the `recall_state` constant readers) is invisible to it and
is pinned by (2)/(7) instead, and (4) keeps a dropped row for one of them
from going unnoticed (its pin would dangle). There is NO carrier fallback in
(2): a row is a literal read, a pinned computed-name read, or red — the old
fallback ("any `MATCH` in the span plus a bare mention of a carrier-shaped
constant") decided no current row and would have greened a future one that read
nothing. The UNTYPED walks (rows 3, 22, 24–33) are NOT a lower bound any more:
(16) derives them from the tree, which is what stopped the class moving on
every pass. Line NUMBERS are not asserted — the symbol is authoritative and a
symbol that merely moved passes. And the enumeration is of READERS of the edge
as data; writers that MERGE it are not rows here.

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
    # Rows 28-33 — the FIFTH re-derivation, and the one that closed the class
    # instead of extending it by hand. Every row below is an ALL-EDGE read: the
    # pattern constrains no rel type at all, so the walk returns `aboutObject`
    # edges as data without ever naming them. Rows 26-27 established the rule
    # for the non-customer half (a count that a `SET`-free derive would silently
    # shrink); rows 28-32 are the same rule where the edge is DUMPED, SHIPPED or
    # COUNTED for a customer, which is why the figures the ruling calls decisive
    # moved again. Row 33 is the write-side twin.
    #
    # ⚠️ The backup rows are not a marginal addition: §13's own `#4240` bullet is
    # about this edge class's durability, and the backup IS the durability
    # mechanism — so "would a derive change what the customer can restore?" is
    # that bullet's question, and it was answerable only from a site the table
    # did not carry.
    "hosted_backup.py::_DUMP_EDGE_PAGE_QUERY": (
        '"MATCH (a)-[r]->(b) WHERE id(r) >= $cursor "',
        '"RETURN id(a), id(b), type(r), properties(r), id(r) "',
    ),
    "hosted_backup.py::_COUNT_ALL_EDGES_QUERY": (
        '_COUNT_ALL_EDGES_QUERY = "MATCH ()-[r]->() RETURN count(r)"',
    ),
    "hosted_backup.py::restore_graph": (
        '"MATCH ()-[r]->() RETURN count(r)").result_set[0][0])',
        "baseline_edges",
    ),
    "hosted_backup.py::_restore_copy_settled": (
        '"MATCH ()-[r]->() RETURN count(r)").result_set[0][0])',
        "dst_edges",
    ),
    "sdk.py::TortoiseSDK.backfill_v25": (
        'proj.g.query("MATCH ()-[r]->() RETURN count(r)")',
        'report["edge_count"]',
    ),
    "sdk.py::TortoiseSDK._promote_incident_operators": (
        '"MATCH (o:Point {id:$oid})-[r]->(s) "',
        "epistemic_disjunction('s')",
    ),
}

#: Untyped walks that CANNOT observe the edge, keyed by the symbol that owns
#: them: `row key -> (the code facts to assert in its span, why it is out).`
#:
#: ⚠️ **This table is what turns §13's *"the class is enumerated, not excluded"*
#: from a promise into a check, and it exists because the previous four hand
#: passes each produced a DIFFERENT answer.** Rows 24-27 were added by applying
#: the section's own rule (*an unfiltered walk reads the edge without naming
#: it*), and every subsequent reader of the section applied the same rule and
#: got a different count — because the rule admitted, and nothing said where it
#: STOPPED. An exclusion that is not written down is indistinguishable from an
#: omission, so each one below carries the code fact that earns it and a red when
#: that fact goes away.
#:
#: The two shapes that exclude are the two ways a walk can bound the RESULT SET
#: away from the edge:
#:
#: * the relationship TYPE is restricted downstream (`type(r) IN [...]`), or
#: * an ENDPOINT label excludes BOTH ends of every `aboutObject` triple.
#:
#: ⚠️ **The endpoint shape is stated against the SCHEMA, not against a memory of
#: it (#9400 review).** The first cut said "`aboutObject` starts at a Point" and
#: "its target is `:Object` (`STRUCTURAL_REL_LABELS`)" — the second half is true
#: of the TARGET only (`STRUCTURAL_REL_LABELS` maps rel -> the TARGET's label,
#: `projection/edges.py`), and the first half is FALSE: `session_link
#: .ENTITY_LINKED_TRIPLES` permits `aboutObject` from a `Point`, an `Event` AND a
#: `Session`, and `projection.create_about_edge` resolves its source across every
#: label. The conclusions below hold either way — but the register's rationale is
#: what a future exclusion copies, so it has to name the fact the schema actually
#: carries: **a `aboutObject` source is `Point`/`Event`/`Session` (never `Subject`,
#: never `Source`) and its target is `Object`.**
#:
#: A pattern-comprehension degree (`size([(old)-[x]-(y) | x])`) is the endpoint
#: shape: it is anchored on a `:Source`, and no `aboutObject` starts or ends at a
#: Source.
_EXCLUDED_UNTYPED: dict[str, tuple[tuple[str, ...], str]] = {
    "hosted_api.py::_load_commit_graph_state": (
        (
            "MATCH (o:Point {is_operator:true})-[r]->(t) ",
            "WHERE type(r) IN ['IMPL','NAND'] ",
        ),
        "the walked relationship type is restricted to the operator pair, so an "
        "`aboutObject` edge is filtered out before the row is returned",
    ),
    "mining.py::EpSafeCommit.verify_no_auto_wire": (
        (
            "MATCH (o:Point {is_operator:true})-[r]->(n:Point) ",
            "MATCH (o:Point {id:$oid})-[r]->(s:Point) ",
        ),
        "both endpoints are pinned to `:Point`, and `aboutObject`'s target is "
        "`:Object` (`STRUCTURAL_REL_LABELS`)",
    ),
    "projection/entities.py::_EntityHandlers._materialize_connector_source": (
        (
            "size([(old)-[x]-(y) | x]) AS deg",
            "MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) ",
        ),
        "a pattern-comprehension degree over a `:Source` re-point — no "
        "`aboutObject` starts or ends at a Source",
    ),
    "sdk.py::TortoiseSDK.provenance": (
        ("MATCH (s:Subject {id:$sid})-[r]->(n) ",),
        "the walk is OUTGOING from a `:Subject`, and no `aboutObject` triple has a "
        "`:Subject` source (`session_link.ENTITY_LINKED_TRIPLES`: the sources are "
        "`Point`, `Event` and `Session`, the target is `Object`)",
    ),
    "sdk.py::TortoiseSDK._connected_pairs": (
        (
            "MATCH (op:Point {is_operator:true})-[r1]->(a:Point), ",
            "MATCH (a:Point)-[r]-(b:Point) ",
        ),
        "BOTH walks pin every endpoint to `:Point` — the shared-operator pair and "
        "the direct-edge pair — and `aboutObject`'s target is `:Object`",
    ),
    "session_source_migration.py::_OUT_TYPES": (
        ("MATCH (old:Source {url:$u})-[r]->(t) ",),
        "the walk is OUTGOING from a `:Source`, which no `aboutObject` starts at",
    ),
    "session_source_migration.py::_IN_TYPES": (
        ("MATCH (n)-[r]->(old:Source {url:$u}) ",),
        "the walk is INCOMING to a `:Source`, which no `aboutObject` targets",
    ),
    "session_source_migration.py::_DEGREE": (
        (
            "size([(old)-[x]-(y) | x]) AS deg",
            "MATCH (old:Source {url:$u}) ",
        ),
        "a pattern-comprehension degree over a `:Source`",
    ),
    "session_source_migration.py::_delete_if_orphan": (
        (
            "size([(old)-[x]-(y) | x]) AS deg",
            "MATCH (old:Source {url:$u}) ",
        ),
        "a pattern-comprehension degree over a `:Source`",
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


def _reads_literally(value: str) -> bool:
    """True when ANY `aboutObject` bracket in this literal belongs to a READ.

    Read vs write is decided by the LAST clause preceding each bracket inside
    the same string literal, so `MATCH … MERGE (p)-[:aboutObject]->(o)` is a
    WRITE (the MERGE is what the bracket belongs to) and
    `MATCH (e) OPTIONAL MATCH (e)-[:aboutObject]->(o)` is a READ.

    ⚠️ **EVERY bracket is classified, not just the first.** A single
    `_EDGE_PATTERN.search` per literal was a fail-open in both directions this
    file exists to close: in a query shaped write-then-read —
    `MATCH … MERGE (p)-[:aboutObject]->(o) WITH p MATCH (x)-[:aboutObject]->(y)`
    — the first bracket's `MERGE` decided for the whole string, so the genuine
    READ after it entered neither the census nor any row check, and
    `test_no_read_site_lacks_a_row`'s "every read clause that names the edge"
    over-promised. Measured on the pre-fix rule: a new method returning only
    the FRAGMENT `"(n)-[:aboutObject]->(o)"` — the shape
    `SubgraphExpander._neighbors` already builds — kept every arm green (all
    thirteen of them at `c0c67fa34`, where that mutation was run; every arm in
    the file is green on the shipped rule, seventeen of them at `73e3c68b4`).
    """
    for match in _EDGE_PATTERN.finditer(value):
        if _in_pattern_comprehension(value, match.start()):
            return True
        clauses = _CLAUSE.findall(value[: match.start()])
        if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
            return True
    return False


def _reads_naming_the_edge(tree: ast.Module, lo: int, hi: int) -> bool:
    """True when a Cypher READ clause inside [lo, hi] names the edge literally.

    ⚠️ **A DOCSTRING is prose, not a query, and it is skipped here** — the same
    `_docstring_ids()` filter `_untyped_read_sites()` has always applied, threaded
    through so the two halves of this census agree on what a read is. Without it
    a row kept its *"the named symbol really reads the edge"* verdict after the
    code stopped reading it: paste the old query into the function's docstring and
    this arm went green on prose (measured on the pre-fix predicate — the row 9
    probe the review ran). The other direction matters too: an example query in a
    docstring would become a phantom SITE and force a spurious row or exclusion.
    """
    docstrings = _docstring_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if not lo <= getattr(node, "lineno", 0) <= hi:
            continue
        if _reads_literally(node.value):
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
    reverting to the prefix rule keeps the suite green, so the narrowing is a
    correctness fix for the candidate set, not a repair of a currently red test.

    ⚠️ The threshold is measured against `aboutObject` REMOVED, which is the
    state the caller must still detect: dropping it leaves 5/6 of
    `ABOUT_EDGE_TYPES`, so a majority still holds and the membership assertion
    still fires.
    """
    about = sum(1 for element in elements if element.startswith("about"))
    return bool(elements) and about * 2 > len(elements)


def _spliced_rel_family(node: ast.AST | None) -> tuple[str, ...] | None:
    """The rel family a constant's VALUE can be spliced into `-[a:<value>]->` as.

    Two forms qualify: a tuple/list/set of string literals and an `a|b|c`
    alternation string, optionally wrapped in `set(...)` / `frozenset(...)` /
    `tuple(...)` / `list(...)`. ⚠️ **Nothing else does — a CONCATENATION
    (`_SHARED + ("aboutObject",)`) returns None**, which is a real limit of the
    candidate set and the reason the carrier arm carries a non-vacuity FLOOR: a
    carrier refactored into a `BinOp` disappears from the set, and without the
    floor the arm would assert nothing while staying green.
    """
    # `frozenset({...})` / `set({...})` counts too: `DERIVABLE_STRUCTURAL_RELS`
    # is the replay's rel filter and is spelled that way.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set", "tuple", "list"}
        and len(node.args) == 1
    ):
        return _spliced_rel_family(node.args[0])
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


def _carrier_shaped_in(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """`_carrier_shaped_constants()` for an ALREADY-PARSED module.

    Split out so the shape rule can be asserted on a synthetic tree — the
    carrier arm's floor ("this arm examined at least one pair") is only
    meaningful beside a fixture that shows WHICH refactors empty the candidate
    set, and a filesystem-bound scan cannot be handed one.
    """
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
                elements = _spliced_rel_family(node.value)
                if not elements or not _about_family_dominates(elements):
                    continue
                carriers[prefix + name] = elements

    scan(tree.body)
    return carriers


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
    `_about_family_dominates()` for why the loose form is wrong. The shape rule
    itself is `_spliced_rel_family()`, and a candidate set that empties makes the
    carrying arm RED on its floor rather than silently vacuous.
    """
    return _carrier_shaped_in(_parsed(str(_module_path(rel))))


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


def _literal_read_lines(tree: ast.Module) -> list[int]:
    """Line numbers of each literal Cypher READ of the edge in one module.

    Split out of the census so the same rule can be asserted on a SYNTHETIC tree
    — the module walk is filesystem-bound, and an arm that decides whether
    docstring prose is a read has to be reachable from a fixture that can turn
    red. ⚠️ **Docstrings are filtered out** (`_docstring_ids()`), because the
    literal halves and the untyped census must not disagree about what a read
    is: prose inside a docstring is not a query the driver ever receives.
    """
    docstrings = _docstring_ids(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if not _reads_literally(node.value):
            continue
        lines.append(getattr(node, "lineno", 0))
    return lines


def _literal_read_sites() -> dict[str, list[int]]:
    """Census: module → line numbers of each literal Cypher READ of the edge."""
    found: dict[str, list[int]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Cheap pre-filter: a module that never writes the edge inside a
        # relationship bracket cannot contain a literal read of it.
        if not _EDGE_PATTERN.search(text):
            continue
        lines = _literal_read_lines(_parsed(str(path)))
        if lines:
            found[str(path.relative_to(PKG))] = lines
    return found


#: A Cypher relationship bracket with NO rel-type constraint: `[r]`,
#: `[r*1..2]`, `[x]`, `[r {idx:0}]`. A bracket carrying a `:` outside a
#: property MAP is either a TYPED read or a run-time type name — the
#: carrier/computed-name half, which `_NON_LITERAL` already decides — so the
#: colon is exactly the line between the two halves of this census. ⚠️ **The
#: property map has to come out first**: Cypher allows `-[r {idx:0}]->` on an
#: UNTYPED relationship, and the colon there is a key separator, so a bare
#: `":" in bracket` test made an unconstrained all-edge walk invisible to the
#: arm whose whole job is to derive the untyped class.
_UNTYPED_BRACKET = re.compile(r"(?:<-|-)\s*\[([^\]\n]*)\]\s*(?:-|->)")

#: The property map inside a relationship bracket — removed before the colon
#: test, because `{idx:0}` is not a type.
_PROPERTY_MAP = re.compile(r"\{[^}]*\}")

#: The stand-in for a run-time hole in a reconstructed query string. Marks where
#: a value is computed (a rel type, a label, an id) WITHOUT destroying the
#: surrounding Cypher, so `[r:{rel}]` still reads as a bracket that carries a
#: type while `[r]` still reads as one that carries none.
_HOLE = "\u00abexpr\u00bb"


def _docstring_ids(tree: ast.Module) -> set[int]:
    """The `id()`s of every docstring Constant — prose, not a query."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _query_text(node: ast.AST, docstrings: set[int]) -> tuple[str, int] | None:
    """A string-valued expression's text, with run-time values replaced by `_HOLE`.

    Cypher reaches the driver as `Constant`, `f"…{x}…"` and `"a" + b + "c"`
    indiscriminately, and the bracket this census decides can live in any of
    them: `_hop_query` builds `MATCH (n:{label} {{{key}:$eid}})-[r]->(m)` as an
    f-string, so a census that read only `ast.Constant` would be blind to the
    exact row it was written for. Reconstructing the whole expression keeps the
    clause before the bracket, which is what decides read-vs-write.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str) and id(node) not in docstrings:
            return node.value, node.lineno
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(_HOLE)
        return "".join(parts), node.lineno
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _query_text(node.left, docstrings)
        right = _query_text(node.right, docstrings)
        if left is None and right is None:
            return None
        text = (left[0] if left else _HOLE) + (right[0] if right else _HOLE)
        return text, (left or right)[1]
    return None


def _in_pattern_comprehension(query: str, start: int) -> bool:
    """Is the bracket at `start` inside a Cypher PATTERN COMPREHENSION?

    Cypher opens one with a `[` that is still unclosed when the relationship
    bracket is reached — `[(n)-[r]->(m) | r]`, `size([(n)-[r]->(m) | r])`. No
    other construct puts a relationship bracket inside an unclosed `[` of the
    same query string, so the delimiter test is exact rather than a heuristic.
    """
    depth = 0
    for char in reversed(query[:start]):
        if char == "]":
            depth += 1
        elif char == "[":
            if depth == 0:
                return True
            depth -= 1
    return False


def _clause_opening_at(query: str, start: int):
    """The LAST clause that starts the expression containing `start`.

    For a walk inside a pattern comprehension the comprehension is the thing
    that reads, so the segment is attributed to the clause that introduces the
    comprehension (`SET`, `WITH`, `RETURN`) rather than to no clause at all.
    """
    prior = [clause for clause in _CLAUSE.finditer(query) if clause.start() < start]
    return prior[-1] if prior else None


def _untyped_read_segments(query: str) -> list[tuple[int, str, tuple[str, ...]]]:
    """Every UNTYPED relationship bracket in a READ clause, with its CLAUSE segment.

    Returns `(bracket offset, this walk's segment, the segments before it)`.

    One rule, used by the census AND by the fixture that pins it: a bracket is
    untyped when it carries no type after its property map is removed, and it is
    a READ when the last Cypher clause before it is `MATCH`/`OPTIONAL MATCH`
    (`MERGE … MATCH` is a write, `MATCH … MERGE` is a write).

    ⚠️ **The unit is the CLAUSE, not the string literal.** A query can carry two
    walks, and a constraint written for one of them does not bound the other —
    Cypher matches each `MATCH` pattern independently. Attributing an exclusion
    to the whole literal let a second, unconstrained walk appended to the same
    query string inherit the first one's exclusion, which is the opposite of what
    the register exists to say.
    """
    clauses = list(_CLAUSE.finditer(query))
    found: list[tuple[int, str, tuple[str, ...]]] = []
    for match in _UNTYPED_BRACKET.finditer(query):
        if ":" in _PROPERTY_MAP.sub("", match.group(1)):
            continue  # a typed read, or a run-time type name
        prior = [clause for clause in clauses if clause.start() < match.start()]
        start = prior[-1] if prior else None
        readable = start is not None and (
            " ".join(start.group(1).upper().split()) in _READ_CLAUSES
        )
        if not readable:
            # ⚠️ A PATTERN COMPREHENSION is a read whatever clause introduces it.
            # In `SET n.deg = size([(n)-[r]->(m) | r])` the last clause before the
            # bracket is `SET`, so a walk that enumerates every relationship type
            # was discarded — a fail-open in the one direction this census exists
            # to close. A comprehension ENUMERATES the edges it walks. It also has
            # to survive carrying NO clause at all (`RETURN` is not in `_CLAUSE`),
            # which is why this is not folded into the clause test.
            if not _in_pattern_comprehension(query, match.start()):
                continue
            start = _clause_opening_at(query, match.start())
        if start is None:
            following = [clause for clause in clauses if clause.start() > match.end()]
            end = following[0].start() if following else len(query)
            found.append((match.start(), query[:end], ()))
            continue
        following = [clause for clause in clauses if clause.start() > match.end()]
        end = following[0].start() if following else len(query)
        before = [clause for clause in clauses if clause.start() < start.start()]
        earlier = tuple(
            query[clause.start() : (
                before[index + 1].start() if index + 1 < len(before) else start.start()
            )]
            for index, clause in enumerate(before)
        )
        found.append((match.start(), query[start.start() : end], earlier))
    return found


def _pattern_variables(text: str) -> set[str]:
    """Variable names bound in a Cypher pattern — `(old:Source …)`, `(a)`, `(op)`."""
    return {
        name
        for name in re.findall(r"\(\s*([A-Za-z_]\w*)\s*[:)\s]", text)
        if name.upper()
        not in {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "SET", "DELETE"}
    }


def _exclusion_bounds_the_walk(
    segment: str, earlier: tuple[str, ...], facts: tuple[str, ...]
) -> bool:
    """Does one of `facts` bound THIS walk?

    In the walk's own clause first — that is the clause whose pattern the walk
    is. A fact in an EARLIER clause of the same query also counts, but only when
    that clause binds a variable this walk's pattern uses: `MATCH (old:Source …)`
    bounds a later degree over `(old)-[x]-(y)`, and a constraint written for a
    different walk does not, because Cypher matches each `MATCH` pattern
    independently. That distinction is what a whole-literal check could not make.
    """
    if any(fact in segment for fact in facts):
        return True
    own = _pattern_variables(segment)
    return any(
        any(fact in prior for fact in facts) and bool(own & _pattern_variables(prior))
        for prior in earlier
    )


def _untyped_read_brackets(query: str) -> list[int]:
    """Start offsets of every UNTYPED relationship bracket in a READ clause."""
    return [offset for offset, _segment, _earlier in _untyped_read_segments(query)]


def _record_untyped_site(
    found: dict[str, list[tuple[int, str, tuple[str, ...]]]],
    module: str,
    lineno: int,
    segment: str,
    earlier: tuple[str, ...],
) -> None:
    """Record one untyped walk, deduped on `(line, clause)` — never on text alone.

    ⚠️ **The identity is the SITE, not the query string.** Deduping on the text
    dropped the second copy of an identical walk in another symbol of the same
    module — measured: `hosted_backup.py` already carries `MATCH ()-[r]->()
    RETURN count(r)` five times, so a sixth copy in a new helper vanished from
    the census while its text matched the row-29 constant. Deduping on the line
    alone collides two brackets inside ONE clause (`_connected_pairs`' operator
    pair), which is the same walk and should stay one entry. The pair is exact:
    same line and same clause is one walk; anything else is its own site.
    """
    sites = found.setdefault(module, [])
    if any((lineno, segment) == (line, clause) for line, clause, _ in sites):
        return
    sites.append((lineno, segment, earlier))


def _untyped_read_sites() -> dict[str, list[tuple[int, str, tuple[str, ...]]]]:
    """Census: module → `(line, walk segment, earlier segments)` of untyped reads.

    `_literal_read_sites` finds the reader that SPELLS the edge. This one finds
    the reader that CANNOT: a relationship bracket carrying no type at all
    (`[r]`, `[r*1..2]`, `[x]`, `[r {idx:0}]`), in a `MATCH`/`OPTIONAL MATCH`.
    Such a walk returns `aboutObject` edges as data and no `grep` for the edge
    name will ever see it.

    ⚠️ **This arm is the point of the whole file, applied to the LAST class that
    was still hand-maintained.** The literal census plus `_NON_LITERAL` made
    every NAMED reader executable. The untyped walk stayed a hand pass — and it
    is the hand pass that was wrong five times running: rows 24-27 were added by
    applying §13's own inclusion rule (*an unfiltered walk reads the edge without
    naming it*), and each later reader of the section re-applied the same rule
    and reached a different count, because the rule admitted everything and
    nothing recorded where it stopped. Measured at the head this arm was written
    for: the table carried 27 sites and the tree had 33, and the six missing ones
    included `POST /v1/backups` — the durability mechanism the ruling's own
    bullet is about.

    Membership is decided the same way as the literal census (by ENCLOSING
    SYMBOL, so a new walk added beside a row reds), and the sites that CANNOT
    observe the edge are carried by `_EXCLUDED_UNTYPED` with the code fact that
    earns the exclusion.

    ⚠️ **The WALK travels with each site, not just its line**, because an
    exclusion is a claim about a walk, not about a symbol or a string: without it,
    a second unconstrained walk added inside an excluded symbol — or appended to
    the same query string — is absorbed by the first walk's exclusion, the one
    direction where coarser granularity is backwards (a row says "this symbol
    reads the edge"; an exclusion says "this walk cannot reach it").
    """
    found: dict[str, list[tuple[int, str, tuple[str, ...]]]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Case-INSENSITIVE: `_CLAUSE` matches `match` in any case, so a
        # capital-only pre-filter skipped a module whose only read clause was
        # lower-cased — a fail-open in a guard whose purpose is not to fail open.
        if "match" not in text.lower():
            continue
        tree = _parsed(str(path))
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                continue
            rebuilt = _query_text(node, docstrings)
            if rebuilt is None:
                continue
            value, lineno = rebuilt
            for _offset, segment, earlier in _untyped_read_segments(value):
                _record_untyped_site(
                    found, str(path.relative_to(PKG)), lineno, segment, earlier
                )
    return found


def _fragment_brackets(value: str) -> list[int]:
    """Offsets of the UNTYPED, clause-free relationship brackets in `value`.

    The fragment rule, split out so the census and its fixture decide it the same
    way: untyped (no type once the property map is removed) and with NO Cypher
    clause before it, which is what makes it a PART rather than a query.
    """
    return [
        match.start()
        for match in _UNTYPED_BRACKET.finditer(value)
        if ":" not in _PROPERTY_MAP.sub("", match.group(1))
        and not _CLAUSE.findall(value[: match.start()])
    ]


def _fragment_sites_in(tree: ast.Module) -> list[tuple[int, str]]:
    """`(line, text)` of every FRAGMENT in ONE module that supplies an untyped walk.

    Split out of the census so the fixture can assert the detection on a
    SYNTHETIC tree: the bypass this closes is a shape (`_WALK = "-[r]->"` beside
    `"MATCH (n)" + _WALK + "(m)"`), and a fixture that builds exactly that shape
    is what proves the arm measures something. See `_untyped_fragment_sites`.
    """
    docstrings = _docstring_ids(tree)
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        rebuilt = _query_text(node, docstrings)
        if rebuilt is None:
            continue
        value, lineno = rebuilt
        if not _fragment_brackets(value):
            continue
        if all(lineno != line for line, _ in sites):
            sites.append((lineno, value))
    return sites


def _untyped_fragment_sites() -> dict[str, list[tuple[int, str]]]:
    """Census: module → `(line, text)` of a FRAGMENT that supplies an untyped walk.

    ⚠️ **This is the bypass a query-shaped census cannot see, and it was
    measured.** A walk assembled from parts — `_WALK = "-[r]->"`, then
    `"MATCH (n)" + _WALK + "(m)"` — keeps the bracket in a DIFFERENT string
    constant from its `MATCH`, so `_untyped_read_sites` finds no bracket at the
    query, and the fragment carries no clause of its own, so no clause rule
    applies to it either: the walk entered neither census and every test stayed
    green. So any string expression holding a dash-delimited relationship
    bracket with **no rel type and no clause at all** is itself a site, and its
    enclosing symbol must account for it (a row, an exclusion, or a
    `_NON_LITERAL` pin).

    Read-vs-write cannot be decided for a fragment, and that is the point: the
    requirement is unconditional ownership, so a part-assembled walk cannot be
    introduced silently.

    ⚠️ **This census is WIRED IN, not documentation.** It was defined and never
    called, which made its docstring a claim the suite never executed — the very
    defect class this file exists to end, inside this file. Its owner check (and
    a floor, so it cannot go vacuous) is in
    `test_no_untyped_walk_lacks_a_row_or_a_stated_exclusion`.

    **Limit, stated rather than implied.** This arm sees a DASH-DELIMITED
    fragment (`"-[r]->"`, `"(n)-[r]->(o)"`) — Cypher's own relationship
    delimiter, which is what makes the bracket a relationship at all. A fragment
    carrying only `"[r]"` is not distinguishable from a list index or a JSON
    array without knowing the caller, so it is not counted here; a reader built
    that way is held by the PINS (`_NON_LITERAL`), not by this census. That is
    why §13 states the derived class as *relationship brackets, in a read clause
    or assembled from a dash-delimited fragment* — not as *every possible
    assembly*.
    """
    found: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "match" not in text.lower() and "[" not in text:
            continue
        sites = _fragment_sites_in(_parsed(str(path)))
        if sites:
            found[str(path.relative_to(PKG))] = sites
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


def test_every_literal_bracket_is_classified_not_just_the_first():
    """A WRITE followed by a READ in one literal is a READ site.

    ⚠️ **Non-vacuity for `_reads_literally`, settled without touching
    `tortoise/`.** The classifier used to `search` once per literal, so the
    FIRST bracket's clause decided for the whole string: in a query shaped
    write-then-read the `MERGE` won, the later `MATCH … [:aboutObject]` entered
    neither the census nor any row check, and a new reader written that way
    needed no §13 row. The cases below are the four orderings the arm must get
    right; the first one is red under the pre-fix rule.
    """
    cases = {
        # write FIRST, read SECOND — the shape a first-bracket-only reader drops.
        "MATCH (p:Point {id:$id}) MERGE (p)-[:aboutObject]->(o:Object) "
        "WITH p MATCH (x)-[:aboutObject]->(y:Point) RETURN count(y)": True,
        # read first, write second — already a read, and it must stay one.
        "MATCH (n)-[:aboutObject]->(o:Object) SET o.seen = true": True,
        # write only: a MERGE is not a read, however many brackets there are.
        "MATCH (p:Point {id:$id}) MERGE (p)-[:aboutObject]->(o:Object)": False,
        "MERGE (p)-[:aboutObject]->(o:Object) "
        "MERGE (q)-[:aboutObject]->(r:Object)": False,
    }
    wrong = [
        f"{value!r} -> {_reads_literally(value)!r} (expected {expected!r})"
        for value, expected in cases.items()
        if _reads_literally(value) is not expected
    ]
    assert not wrong, (
        "the literal classifier mis-classified these queries:\n  - "
        + "\n  - ".join(wrong)
        + "\nRead vs write is the LAST clause before EACH bracket. Searching "
        "only the first bracket lets a `MERGE` decide for a literal whose read "
        "comes later in the same query, which is how a real read site stays "
        "out of the census and out of every §13 row."
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


def test_no_untyped_walk_lacks_a_row_or_a_stated_exclusion():
    """Every untyped walk is a row, or carries the code fact that excludes it.

    ⚠️ **The class this decides is the one that was wrong five times.** §13 says
    *the class is enumerated, not excluded*, and rows 24-27 were added by
    applying that rule to the code. Every subsequent pass applied the same rule
    and got a different answer, because a rule that admits every unfiltered walk
    admits more than any hand pass will find — the last one found 27 sites where
    there were 33, and the six it missed included `POST /v1/backups`.

    So the enumeration is now derived from the TREE, the same way the literal
    census is: every relationship bracket with no type at all, in a READ clause,
    keyed on its enclosing `module.py::symbol`. Each site must then either own a
    §13 row or appear in `_EXCLUDED_UNTYPED` **with the code fact that earns the
    exclusion present in that walk's own clause** — so an exclusion cannot be a
    claim, it cannot silently stop being true, and it cannot swallow a SECOND,
    unconstrained walk added either beside it or in the same query string.
    """
    # The bracket rule itself, before the census uses it: an UNTYPED walk is one
    # whose bracket carries no type, and a property map is not a type.
    must_match = (
        "MATCH (n)-[r]->(m)",
        "MATCH (n)-[r*1..2]->(m)",
        "MATCH (n)-[r {idx:0}]->(m)",
    )
    must_not_match = (
        "MATCH (n)-[r:aboutObject]->(m)",
        "MATCH (n)-[r:{rel}]->(m)",
        "MERGE (n)-[r]->(m)",
    )

    # ⚠️ A pattern comprehension ENUMERATES the edges it walks, so it is a READ
    # whatever clause introduces it. Before this arm, `SET n.deg = size([…])`
    # was discarded on the preceding `SET` — a walk over every relationship type
    # invisible to the census that exists to find it.
    must_match_comprehension = (
        "SET n.deg = size([(n)-[r]->(m) | r])",
        "RETURN size([(n)-[r]-(m) | r]) AS deg",
    )

    def _brackets(query: str) -> list[int]:
        return _untyped_read_brackets(query)

    for query in must_match:
        assert _brackets(query), (
            f"this census is blind to {query!r} — a bracket with no rel type is "
            "an ALL-EDGE walk, and a `{{…}}` property map must not read as a type."
        )
    for query in must_not_match:
        assert not _brackets(query), (
            f"this census reads {query!r} as untyped — a bracket that carries a "
            "type (`[r:aboutObject]`, `[r:{rel}]`) is the computed-name half, and "
            "a MERGE is a write."
        )
    for query in must_match_comprehension:
        assert _brackets(query), (
            f"this census reads {query!r} as a non-read — a PATTERN "
            "COMPREHENSION enumerates the edges it walks, so the clause that "
            "introduces it (`SET`, `RETURN`) cannot make it a write."
        )

    # The FRAGMENT rule, asserted directly: a clause-free relationship part is a
    # site, and a typed one is not. This is the shape that entered NO census.
    assert _fragment_brackets("-[r]->"), (
        "a clause-free relationship FRAGMENT (`-[r]->`) is not counted as a "
        'site — that is the part-assembled walk (`"MATCH (n)" + _WALK + "(m)"`) '
        "which stayed invisible to the query-shaped census."
    )
    assert not _fragment_brackets("-[r:aboutObject]->"), (
        "a TYPED relationship fragment is the computed-name half, not an untyped "
        "walk — the fragment rule must not swallow it."
    )

    # The census IDENTITY: one walk per (line, clause) — not per query string.
    # Two symbols carrying the SAME untyped query are two sites (a text-keyed
    # census dropped the second, which is how a sixth copy of
    # `MATCH ()-[r]->() RETURN count(r)` would vanish beside row 29's constant),
    # and two brackets inside ONE clause are one walk (`_connected_pairs`).
    synthetic: dict[str, list[tuple[int, str, tuple[str, ...]]]] = {}
    _record_untyped_site(synthetic, "m.py", 10, "MATCH (a)-[r]->(b) ", ())
    _record_untyped_site(synthetic, "m.py", 20, "MATCH (a)-[r]->(b) ", ())
    assert len(synthetic["m.py"]) == 2, (
        "two symbols carrying the same untyped query collapsed into one census "
        "entry — the identity must be the SITE, or a second walk is shielded by "
        "the first one's row/exclusion."
    )
    _record_untyped_site(synthetic, "m.py", 30, "MATCH (op)-[r1]->(a), (op)-[r2]->(b) ", ())
    _record_untyped_site(synthetic, "m.py", 30, "MATCH (op)-[r1]->(a), (op)-[r2]->(b) ", ())
    assert len(synthetic["m.py"]) == 3, (
        "two brackets inside ONE clause were recorded as two walks — that is one "
        "walk with two relationship positions, and splitting it inflates the "
        "census with a duplicate."
    )

    # The cross-clause arm of the exclusion rule, asserted directly (no live site
    # relies on it today, so this is what makes it falsifiable): a fact in an
    # EARLIER clause counts when it binds a variable this walk uses, and not
    # otherwise.
    assert _exclusion_bounds_the_walk(
        "MATCH (old:Source {url:$u}) WITH old, size([(old)-[x]-(y) | x]) AS deg ",
        (),
        ("MATCH (old:Source {url:$u}) ",),
    ), "the walk's own clause carries the binding but the exclusion was refused"
    assert _exclusion_bounds_the_walk(
        "WITH old, size([(old)-[x]-(y) | x]) AS deg RETURN deg ",
        ("MATCH (old:Source {url:$u}) ",),
        ("MATCH (old:Source {url:$u}) ",),
    ), "an earlier clause binds `old`, which this walk uses, but was refused"
    assert not _exclusion_bounds_the_walk(
        "MATCH (q)-[r2]->(w) RETURN type(r2), old.url",
        ("MATCH (old:Source {url:$u}) ",),
        ("MATCH (old:Source {url:$u}) ",),
    ), "an earlier clause binding an UNRELATED variable excused this walk"

    named = {f"{row['module']}::{row['symbol']}" for row in _rows()}
    census = _untyped_read_sites()
    assert census, (
        "the untyped-walk census found NOTHING under tortoise/ — the bracket "
        "pattern or the clause rule was changed and this arm has stopped "
        "measuring. Rows 22, 24-27 and 28-33 are all of this shape, so an empty "
        "census cannot be true."
    )

    unowned: list[str] = []
    for module, sites in sorted(census.items()):
        tree = _module_tree(_module_path(module))
        symbols = _row_symbols(tree)
        for line, segment, earlier in sites:
            candidates = [name for name, lo, hi in symbols if lo <= line <= hi]
            keys = [f"{module}::{name}" for name in candidates]
            if any(key in named for key in keys):
                continue
            excluded = [key for key in keys if key in _EXCLUDED_UNTYPED]
            if any(
                _exclusion_bounds_the_walk(segment, earlier, _EXCLUDED_UNTYPED[key][0])
                for key in excluded
            ):
                continue
            unowned.append(
                f"{module}:{line}  (inside {', '.join(candidates) or 'module level'})"
                + (
                    "  — its symbol is excluded, but THIS walk carries none of "
                    "the facts that exclusion rests on"
                    if excluded
                    else ""
                )
            )
    assert not unowned, (
        "these walks read EVERY relationship type (so they read `aboutObject`) "
        "and are neither absorbed by a §13 row nor covered by a stated "
        "exclusion:\n  " + "\n  ".join(unowned) + "\n§13's `the class is "
        "enumerated, not excluded` is only true if every such walk is one or the "
        "other. Add the row, or add an `_EXCLUDED_UNTYPED` entry carrying the "
        "code fact that puts the edge out of this walk's result set."
    )

    # ── the FRAGMENT half of the same class ────────────────────────────────
    #
    # ⚠️ **This census existed and was never called, so the bypass it documents
    # was still open and its docstring was an unbacked claim.** A walk assembled
    # from parts keeps the bracket in a different string constant from its
    # `MATCH`, so the query-shaped census above sees no bracket at all: append
    # `_WALK = "-[r]->"` beside `query("MATCH (n)" + _WALK + "(m)")` to any
    # module and all seventeen arms stayed green. It is now wired here, with the
    # ownership rule the docstring states (a row, an exclusion, or a
    # `_NON_LITERAL` pin — the last of which the query-shaped arm never
    # consults).
    part_assembled = ast.parse(
        '_WALK = "-[r]->"\n'
        "\n"
        "\n"
        "def _probe_part_assembled_walk(query):\n"
        '    return query("MATCH (n)" + _WALK + "(m)")\n'
    )
    assert _fragment_sites_in(part_assembled), (
        "a part-assembled walk (`\"MATCH (n)\" + _WALK + \"(m)\"`, with the "
        "bracket in its own clause-free constant) is not counted as a site — "
        "that is the bypass this half of the census exists to close, and the "
        "fixture is what makes it falsifiable."
    )
    assembled = ast.parse(
        "def _probe_assembled_walk(query):\n"
        '    return query("MATCH (n)-[r]->(m)")\n'
    )
    assert not _fragment_sites_in(assembled), (
        "an ASSEMBLED walk is being read as a FRAGMENT — the fragment rule must "
        "fire only where the bracket carries no clause of its own, or every "
        "ordinary query becomes a phantom site."
    )

    fragments = _untyped_fragment_sites()
    assert fragments, (
        "the fragment census found NOTHING under tortoise/ — either the rule "
        "changed or the part-assembled shape it exists for is gone. Re-point "
        "this arm, or delete it with the claim; do not leave it vacuous."
    )
    pins = set(_NON_LITERAL) | set(_NON_LITERAL_SIBLINGS)
    unowned_fragments: list[str] = []
    for module, sites in sorted(fragments.items()):
        symbols = _row_symbols(_module_tree(_module_path(module)))
        for line, text in sites:
            candidates = [name for name, lo, hi in symbols if lo <= line <= hi]
            keys = [f"{module}::{name}" for name in candidates]
            if any(key in named or key in _EXCLUDED_UNTYPED or key in pins for key in keys):
                continue
            unowned_fragments.append(
                f"{module}:{line}  (inside {', '.join(candidates) or 'module level'})"
                f"  — fragment {text!r}"
            )
    assert not unowned_fragments, (
        "these DASH-DELIMITED relationship fragments supply an untyped walk — "
        "the `aboutObject` edge reaches the result set of whatever assembles "
        "them — and no §13 row, stated exclusion or `_NON_LITERAL` pin owns the "
        "symbol they sit in:\n  " + "\n  ".join(unowned_fragments) + "\nA walk "
        "can be split across string parts to stay invisible to the query-shaped "
        "census; the ownership requirement is unconditional, so add the row (or "
        "the exclusion, with the code fact that earns it), or state the pin."
    )


def test_every_stated_exclusion_is_still_true():
    """An exclusion must stay earned: its code facts are asserted where they live.

    `_EXCLUDED_UNTYPED` is the register that makes *"not excluded by silence"*
    mean something, so it is held from both sides. A fact that no longer appears
    in the symbol's span means the constraint that excluded the edge was removed
    — and the site is then an unowned READ of the edge, which is exactly the
    under-count this file exists to end. A symbol that no longer exists, or that
    has since been given a row, is a dead entry: it hides nothing today and would
    hide a real walk tomorrow.
    """
    named = {f"{row['module']}::{row['symbol']}" for row in _rows()}
    assert _EXCLUDED_UNTYPED, (
        "the exclusion register is empty — this direction now measures nothing, "
        "and every untyped walk would have to own a row instead."
    )
    problems: list[str] = []
    for key, (facts, _reason) in sorted(_EXCLUDED_UNTYPED.items()):
        module_rel, _, symbol = key.partition("::")
        path = _module_path(module_rel)
        if not path.exists():
            problems.append(f"{key} — {module_rel} no longer exists")
            continue
        tree = _module_tree(path)
        span = _span(tree, symbol)
        if span is None:
            problems.append(f"{key} — the symbol no longer resolves")
            continue
        if key in named:
            problems.append(
                f"{key} — is excluded AND has a §13 row; one of the two is dead"
            )
            continue
        span_text = "\n".join(
            path.read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        lost = [fact for fact in facts if fact not in span_text]
        if lost:
            problems.append(
                f"{key} — the constraint that excluded the edge is gone: {lost!r}. "
                "The walk now reads `aboutObject` and needs a row."
            )
    assert not problems, (
        "§13's exclusion register no longer matches the code:\n  "
        + "\n  ".join(problems)
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


def test_a_docstring_is_prose_and_not_a_read_site():
    """Docstring prose is not a query, and the census must not count it as one.

    ⚠️ **The two halves of this file disagreed about what a read is.** The untyped
    census filtered prose through `_docstring_ids()`; the literal census and the
    row check accepted every string constant. Measured on the pre-fix predicate:
    replacing row 9's link query with `CONTAINS` and pasting the old query into
    `audit_session_retrofit`'s DOCSTRING left the whole suite green — a row kept
    its *"the named symbol really reads the edge"* verdict after the code stopped
    reading it, in the one arm whose stated job is exactly that claim. The other
    direction is a phantom site: a docstring EXAMPLE would force a spurious row or
    exclusion on the real census.

    Non-vacuous both ways: the same query MOVED from the docstring into the body
    is still counted, so the filter drops prose and only prose.
    """
    prose = ast.parse(
        "def f():\n"
        '    """MATCH (p:Point)-[:aboutObject]->(o:Object) RETURN o"""\n'
        "    return 1\n"
    )
    assert _docstring_ids(prose), (
        "`_docstring_ids()` no longer recognises a plain function docstring, so "
        "the filter below (and in the untyped census) measures nothing."
    )
    assert not _literal_read_lines(prose), (
        "a docstring carrying a Cypher read was counted as a READ SITE — the "
        "driver never receives a docstring, so this is prose masquerading as "
        "code, and it is how a row kept a stale verdict."
    )
    assert not _reads_naming_the_edge(prose, 1, 3), (
        "a row whose reader kept its old query only in its DOCSTRING is reported "
        "as still reading the edge — the row's claim is about code, not prose."
    )

    real = ast.parse(
        "def f():\n"
        '    return graph.query("MATCH (p:Point)-[:aboutObject]->(o:Object) RETURN o")\n'
    )
    assert _literal_read_lines(real), (
        "the SAME query in the function BODY is no longer counted — the docstring "
        "filter is dropping real reads, which would empty the census."
    )
    assert _reads_naming_the_edge(real, 1, 2), (
        "the SAME query in the function BODY no longer satisfies the row check."
    )


# ── 4. the prose counts equal the table ────────────────────────────────────

#: Every form §13 states the READ-SITE count in. Deliberately case-sensitive:
#: `customer-callable read sites` must fall to `_CALLABLE_SITE_COUNT` below and
#: NOT be read as a site count, because that sentence states the callable
#: figure, not the total.
_SITE_COUNT = re.compile(r"\b([A-Z]+(?:-[A-Z]+)?|\d+) read sites\b")

#: Every form §13 states the CUSTOMER-CALLABLE count in. The section states it
#: TWICE — derived once at its head (`N of them are CUSTOMER-CALLABLE`) and
#: quoted again in the blast-radius sentence (`N customer-callable read
#: sites`) — and BOTH are read. ⚠️ **`re.search` was a false PASS here**: it
#: binds the first statement only, so the blast-radius sentence could state a
#: different figure from the one §13's table carries and every arm stayed green
#: (measured at `c0c67fa34`: thirteen arms, this one included) — the same
#: "hand copy with no reader" shape
#: the door figure in that very sentence was fixed for. The sibling
#: `test_the_extractor_document_agrees_on_the_count` reads its document the same
#: way (`stated == {counted}`), so the two arms now agree on the rule too.
_CALLABLE_SITE_COUNT = re.compile(
    r"\b([A-Z]+(?:-[A-Z]+)?|\d+) "
    r"(?:of them are CUSTOMER-CALLABLE|customer-callable read sites)\b"
)


def _stated_counts(section: str) -> tuple[set[int], set[int]]:
    """(site counts, customer-callable counts) — EVERY statement in `section`.

    A set, not a first match: a section that states one figure twice must agree
    with the table twice, or the unread copy is how the number rotted.
    """
    sites = {_words(match.group(1)) for match in _SITE_COUNT.finditer(section)}
    callable_sites = {
        _words(match.group(1)) for match in _CALLABLE_SITE_COUNT.finditer(section)
    }
    return sites, callable_sites


def test_the_count_reader_sees_every_statement_not_just_the_first():
    """The count arms must read EVERY statement, not the one `re.search` binds.

    ⚠️ **Non-vacuity for the arm above, independent of the document's current
    wording.** §13 states the customer-callable figure twice in two different
    shapes, so a reader that binds only the first (`re.search`) leaves the
    second unguarded — measured on the pre-fix guard at `c0c67fa34`: rewriting
    the blast-radius sentence to `FIFTEEN customer-callable read sites` left
    every arm green (thirteen of them, at that sha). This fixture states the
    same figure twice, in both shapes, and requires both to be seen.
    """
    fixture = (
        "TWENTY-SEVEN read sites, and TWELVE of them are CUSTOMER-CALLABLE."
        " Deleting the edge would break **TWELVE customer-callable read sites**."
    )
    sites, callable_sites = _stated_counts(fixture)
    assert sites == {27}, (
        f"the site-count arm read {sorted(sites)} from a section that states "
        "TWENTY-SEVEN read sites — a first-match reader cannot see a second "
        "statement, and §13 states this figure twice."
    )
    assert callable_sites == {12}, (
        f"the customer-callable arm read {sorted(callable_sites)} from a "
        "section that states TWELVE twice (once as `TWELVE of them are "
        "CUSTOMER-CALLABLE`, once as `TWELVE customer-callable read sites`) — "
        "the second shape is the blast-radius sentence's hand copy, and it is "
        "read by nothing when this arm binds only the first match."
    )
    # ...and the two figures must not be confused for one another: the callable
    # sentence says "TWELVE … read sites", which is NOT the site total.
    assert 12 not in sites, (
        "the site-count arm read the CUSTOMER-CALLABLE figure (TWELVE) as a "
        "site count — `TWELVE customer-callable read sites` states the "
        "callable figure, so the site arm must not bind it."
    )


def test_the_prose_counts_match_the_table():
    section = _section_13()
    rows = _rows()
    sites, callable_sites = _stated_counts(section)
    assert sites, "§13 must state how many read sites it enumerates"
    assert callable_sites, "§13 must state how many of those are customer-callable"

    assert sites == {len(rows)}, (
        f"§13 states the read-site count as {sorted(sites)} and its table "
        f"carries {len(rows)} rows — the count is the ruling's argument, so it "
        "is asserted, not prose (it was wrong twice: B2.7 said two "
        "customer-callable sites, #7956 corrected it to four). Every "
        "statement is read, so a second copy cannot drift unguarded."
    )
    counted = sum(1 for row in rows if _reach(row) == "customer-callable")
    assert callable_sites == {counted}, (
        f"§13 states the customer-callable count as {sorted(callable_sites)} "
        f"and {counted} rows are classed customer-callable — §13 states this "
        "figure more than once, and every statement has to agree with the "
        "table or the unread copy is how it rots."
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

#: Case-INSENSITIVE, for the one place the figure is stated inside a sentence
#: (`... across twelve doors`): the residue that survived the first fix was
#: lower-case and the capital-only pattern above could not see it.
_ANY_DOOR_COUNT = re.compile(r"\b([A-Za-z]+|\d+) (?:distinct )?doors?\b", re.I)


def _blast_radius_door_count(document: str) -> int | None:
    """The door figure in the sentence that states the CONSEQUENCE of a derive.

    ⚠️ This is the number the ruling's argument turns on (*"a count that names
    only the doors understates the blast radius"*), and it was the one written
    in lower case — `across twelve doors` — after the total had been corrected
    to SIXTEEN, so the capital-only scan above could not see it. Scoped to the
    sentence that states the consequence, so the document's historical figures
    (*"five doors / eight sites"*, and a row reaching a reader through *"two or
    three doors"*) are not mistaken for the claim.
    """
    start = document.index("Deleting the edge")
    match = _ANY_DOOR_COUNT.search(document, start)
    return _as_count(match.group(1)) if match else None


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
    route; counted ONCE however many of the seventeen sites reach it* — and both
    documents' stated total, and the per-kind breakdown, are asserted against the
    table. ⚠️ **The first cut of that rule said "counted once per site that
    reaches it", which does not produce the figure it certifies**: applied
    literally it yields the CELL-OCCURRENCE total (29), not the distinct door
    total (20), and a reader following it cannot reproduce either number. The
    cell-occurrence total is therefore STATED as well (§13's blast-radius
    sentence) and asserted here, so the folding rule is checkable in both
    directions instead of being prose that happens to agree with a constant.
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
    # The OTHER half of the same rule: "counted ONCE however many sites reach it"
    # is only checkable if the cell-occurrence total is stated too — `tortoise_recall`
    # / `recall_state` alone land in five rows, so 20 distinct doors is 29 cells.
    occurrences = sum(len(rows) for rows in doors.values())
    stated_occurrences = {
        int(match.group(1))
        for match in re.finditer(r"\b(\d+) cell occurrences?\b", section)
    }
    assert stated_occurrences == {occurrences}, (
        f"§13 states {sorted(stated_occurrences) or 'no'} cell-occurrence "
        f"total(s); the table's customer-callable reach cells hold {occurrences} "
        f"door instance(s) over {total} distinct door(s). The sentence states the "
        "folding rule — a door is counted ONCE however many sites reach it — so "
        "both halves must be derivable from the table: restore the cell-occurrence "
        "figure beside the distinct total."
    )
    for name, document in (
        ("STORAGE-ARCHITECTURE.md §13", _doc()),
        ("EXTRACTOR-V4-ARCHITECTURE.md §13.8", _extractor_doc()),
    ):
        claim = _blast_radius_door_count(document)
        assert claim == total, (
            f"{name}'s blast-radius sentence states {claim!r} doors and the table "
            f"derives {total} ({spelled}) — the number the ruling's argument turns "
            "on is that sentence, so it is asserted there too (this is where a "
            "lower-case `twelve doors` survived a corrected total)."
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

    ⚠️ **`assert not broken` alone was a false PASS, and the FLOOR at the end is
    the fix.** The pairs are built by nested loops; if the candidate set empties
    (a carrier refactored to `ABOUT_EDGE_TYPES = _SHARED_ABOUT + ("aboutObject",)`
    is no longer SPLICE-shaped, so `_carrier_declarations()` yields `{}`) the
    loops run zero times, `broken` stays empty, and the arm reports green on an
    assertion it never made — while the `_NON_LITERAL` pins those rows carry
    survive the edge leaving the constant, so nothing else covers this claim. The
    sibling arms (`assert census`, `assert pins`, `assert _EXCLUDED_UNTYPED`)
    already guard the same way; this one now does too, and the synthetic fixture
    below pins WHICH shape empties the set.
    """
    # The shape rule, asserted where a fixture can falsify it: a tuple/set of
    # literals or an `a|b|c` string is carrier-shaped; a CONCATENATION is not.
    shaped = ast.parse('ABOUT_EDGE_TYPES = ("aboutSubject", "aboutObject")\n')
    assert "ABOUT_EDGE_TYPES" in _carrier_shaped_in(shaped), (
        "a tuple of string literals is no longer recognised as a carrier — the "
        "shape rule changed and every row that reads the edge through a constant "
        "is now unasserted."
    )
    concatenated = ast.parse(
        'ABOUT_EDGE_TYPES = _SHARED_ABOUT + ("aboutObject",)\n'
    )
    assert not _carrier_shaped_in(concatenated), (
        "a CONCATENATED carrier is being read as carrier-shaped — that changes "
        "what this arm's floor can detect; re-derive the shape rule (and update "
        "`_spliced_rel_family`'s stated limit) rather than widening it silently."
    )

    broken: list[str] = []
    checked = 0
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
            checked += 1
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
    assert checked, (
        "no row resolved a carrier-shaped constant — `_carrier_shaped_in()` (the "
        "shape rule) or `_about_family_dominates()` (the family rule) no longer "
        "recognises the constants rows 17-23 read through, so this arm is "
        "measuring NOTHING. Measured 7 (row, carrier) pairs at the head this "
        "floor was added; fix the rule, or delete this arm with its claim."
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


# ── 7b. the prior art points at the table, it does not restate it ─────────

#: The consolidated review this PR cites as prior art for the read-site census.
_PRIOR_ART = ROOT / "docs" / "architecture" / "REVIEW-CONSOLIDATED-2026-09-23.md"


def test_the_prior_art_points_at_the_table_instead_of_restating_the_count():
    """B2.7 is cited as prior art, so its figure must not read as current.

    ⚠️ **A THIRD copy of the count, and the one this PR left stale.** B2.7's
    erratum closed with *"⇒ the customer-callable count is four, not two"* while
    §13 and §13.8 state SEVENTEEN, and nothing read this file — the same defect
    this PR corrects twice, one copy further out, in the document the PR body
    names as prior art. §13.8's own paragraph names the mechanism: *each copy
    looked confirmed by the other*.

    The durable form is a POINTER, not a fourth number: restating the count here
    would mint a copy that rots on the next re-derivation. So the row must name
    §13's table and mark its own figure superseded — and this arm keeps both.
    """
    text = _PRIOR_ART.read_text(encoding="utf-8")
    row = next((line for line in text.splitlines() if line.startswith("| B2.7 |")), "")
    assert row, (
        "REVIEW-CONSOLIDATED-2026-09-23.md no longer carries the B2.7 row this "
        "guard reads — re-point the guard at the finding that carries the "
        "read-site count, or drop it with the citation that needs it."
    )
    assert "STORAGE-ARCHITECTURE.md" in row and "§13" in row, (
        "B2.7 states a customer-callable count without pointing at "
        "`STORAGE-ARCHITECTURE.md` §13's table, which is the authoritative, "
        "executable copy — a reader who lands on this row takes a figure nothing "
        "re-derives. Point at §13 instead of restating a number."
    )
    assert "SUPERSEDED" in row, (
        "B2.7's customer-callable figure is presented as CURRENT — §13's table "
        "supersedes it by a wide margin. Mark it superseded and point at §13, "
        "or delete the figure; a stale copy next to a corrected one is how this "
        "under-count survived three reviews."
    )


# ── 9. §13 carries the line numbers its own sentence says it does not ───────

#: A `file.EXT:NNNN` — or `file.EXT:NNNN-MMM` — line citation, the form §13's own
#: sentence retires. A symbol citation (`module.py::symbol`) does NOT match: its
#: colon is doubled, so it is never followed by a digit. ⚠️ EXTENSION-AGNOSTIC on
#: purpose (#9400 review): the first cut matched `.py:` only, so the ONE citation
#: that survived the rewrite — a `.sql:175` on a migration column — was invisible
#: to the very guard that exists to catch it, and the section both stated "no line
#: numbers" and carried one.
_LINE_CITATION = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|sql|ts|tsx|js|mjs|cjs|md|ya?ml|json|toml|sh):[0-9]+"
)


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


# ── §13 quotes every count with the ref it was measured at ─────────────────

#: A count anchored to a MOVING ref: a digit, then within the same clause a
#: `at this PR's head` / `at the merge head` / `at the head`. §13 may state a
#: number only with the sha or date it was measured at — a figure tied to a
#: branch tip carries no pin, so it rots on the next merge and nothing in this
#: file re-derives it. Measured: the test-file count was written down as 60 for
#: the lane's tip and the merge head measured 62 (one unrelated test file landed
#: on `main`), while every other figure in the passage was already sha-pinned.
_HEAD_RELATIVE_COUNT = re.compile(
    r"\b\d+\b[^.;\n]{0,80}?\bat\s+(?:this\s+PR(?:'s)?|the\s+merge|the)\s+head\b",
    re.IGNORECASE,
)


def test_section_13_pins_every_count_to_a_ref():
    """A stated count must name the ref it was measured at, never a moving head.

    ⚠️ **This is the one figure in the changed passage that nothing read.** §13
    pins "59 test files … at the `26aff163c` pin" correctly and then added the
    same fact for a MOVING ref — the lane's tip — and by the time the branch
    merged, that number was two files out (a test file landed on `main` in
    between, plus this census's own file). Every other count in the passage is
    either derived from the table by this file or quoted with its sha; this one
    was a hand copy with no pin, which is the document's own recorded defect
    class. The rule is therefore uniform: quote the sha or the date, or state
    the command and let the reader measure it.

    Non-vacuous: the pre-fix sentence (`60 at this PR's head`) reds here.
    """
    section = _section_13()
    assert "rg -l aboutObject tests/" in section, (
        "§13 no longer carries the test-file measurement (the command and its "
        "sha-pinned result) this guard exists to keep pinned — re-read the "
        "paragraph: restore it with its sha, or delete this test with a reason."
    )
    text = _doc()
    offset = text.index("## 13. Filed from this document")
    unpinned = [
        (text.count("\n", 0, offset + match.start()) + 1, match.group(0))
        for match in _HEAD_RELATIVE_COUNT.finditer(section)
    ]
    assert not unpinned, (
        "§13 states a count against a MOVING ref, so the number has no pin and "
        "nothing re-derives it — the test-file count written this way was two "
        "files out by the time the branch merged. Quote the sha or the date it "
        "was measured at, or state the command and let the reader measure it:\n  - "
        + "\n  - ".join(f"line {line}: {frag!r}" for line, frag in unpinned)
    )
