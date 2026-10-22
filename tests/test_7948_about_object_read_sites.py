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
   customer-callable row names a door that exists **and REACHES that row's own
   reader**: ``SDK `<method>` `` must be a public `TortoiseSDK` method,
   ``MCP `<tool>` `` a registered `ToolDefinition`, ``REST `<VERB path>` `` a live
   route — and then, past existence, the reader must be within the door's
   reference-distance BUDGET (`_DOOR_REACH_HOPS`, with per-reader
   `_DISTANCE_EXCEPTIONS`). That is what makes "customer-callable" a claim about
   the product surface rather than an adjective. ⚠️ **Existence alone was measured
   fail-open:** substituting a different REGISTERED tool on rows 4 and 21 left
   every arm green, and the blast-radius door count is derived from those very
   cells. The residual is named there — the closure is name-based (permissive:
   it can miss a bad door, never invent reach), the budget is a BOUND (3, with two
   measured exceptions), and its acceptance RATE is pinned by (22) so it cannot
   loosen again silently. **The REST door is keyed on `(verb, path)`, not path
   alone** — a cell naming the sibling `GET` of a `POST` route is a different
   door, and was green before the verb was checked.
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
16. `test_the_empty_pattern_class_is_derived_and_declared` — the
   UNTYPED-WALK class is DERIVED from the tree, not hand-listed: every
   relationship pattern naming NO type — the bracketed `-[r]->`, the
   bracket-less `(n)--()`, and a pattern comprehension
   (`size([(n)-[r]->(m) | r])`) whatever clause introduces it — keyed on its
   enclosing symbol, must own a §13 row or an `EMPTY_PATTERN` entry in
   `tests/_about_call_time_census.py` saying why the walk cannot observe the
   edge. This is the class that was wrong five times running — each pass
   re-applied §13's inclusion rule by hand and reached a different count, the
   last one finding 27 sites where the tree had 33 (the backup's all-edge dump
   and the `backfill_v25` count among the six). `#9493` folded the guard's own
   second derivation of the same class (`_EXCLUDED_UNTYPED`, bracket-only,
   module-keyed, blind to the bracket-less spelling that was live at the merge)
   into the `#9398` derivation the support module already carries, so there is
   ONE derivation and ONE register; a walk split across string parts still
   enters the census through the consumer the resolver follows.
17. `test_no_empty_pattern_entry_outlives_its_site` — an exclusion is not a
   promise: an `EMPTY_PATTERN` entry whose site no longer derives is stale and
   reds here (it would excuse a read that is not there and keep the class
   silently smaller than it is), and `test_no_empty_pattern_entry_is_also_a_row`
   reds the dead-entry shape — a site that is BOTH a §13 row and a register
   entry hides nothing today and would hide a real walk tomorrow.
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
   EXAMINED something (`assert census`, `assert pins`, `assert register`,
   and the carrier arm's `assert checked`). A predicate that
   silently stops recognising its candidates turns the arm green on an assertion
   it never made — the carrier arm was exactly that before its floor.
21. `test_the_door_attribution_refuses_a_door_that_reaches_another_reader` —
   (8)'s predicate must be able to REFUSE, so it is pointed at the LIVE
   registered tools: a real tool that reads nothing
   (`tortoise_suggest_entry_points`) must not be attributed to rows 4/21's
   readers, while the true doors must attribute. Without this arm the gap
   (8) closes could re-open as a predicate that answers True everywhere, and
   (8) would then green every substituted door — falsifiability stated as an
   ARM rather than as a comment, the same correction (16) records for the
   fragment census that existed for three cycles and was never CALLED.
22. `test_the_attribution_predicate_does_not_swallow_the_whole_table` — (8)'s
   relation is a BOUND, so the RATE it accepts is pinned as its own number: at
   most `_ACCEPTED_PAIRS_AT_THIS_HEAD` of the (row, table-door) pairs and at
   most `_WORST_ROW_AT_THIS_HEAD` for any one row, with a floor at the cell
   occurrences §13 states, so a predicate that breaks the other way also reds.
   The counts live in those constants and in §13; restating them here is how this
   paragraph came to cite 63 pairs of 340 with a floor of 29 after the table grew
   to 19 rows over 22 doors (measured at this head). The
   inputs are the TABLE's twenty-two doors, not the 99-tool registry, so adding a tool
   does not move them — which is what makes them pinnable without churn.
23. `test_the_gate_walk_comment_pins_no_wall_clock_figure` +
   `test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced` — the `#9506` arms,
   and the only ones here about this instrument's OWN prose rather than §13's. The
   gate-walk comment in `_about_call_time_census.py` carried a duration a re-verifier
   cannot settle — `measured 10.36 s of the control's 10.85 s` on the merge base, and
   `~2.6 s of ~17 s` on an intermediate head of the branch — neither with the framing
   that produced it (whole arm? control step? `--durations=0`? which box?), the
   defect class three earlier rounds removed from that file. The rule is stated once
   as `_unframed_durations()`: a duration is a claim, and it is allowed only when the
   comment also carries the framing that produced it. ⚠️ The first version of the
   guard keyed on the self-denial PHRASE instead (`no (magnitude|wall-clock)` AND a
   duration), which was **green on the merge base** — the text the PR exists to
   replace names no denial — and would go green again on any rewording of the denial,
   so it retired itself on the next prose edit. The predicate no longer reads the
   denial at all, and the second arm is its non-vacuity: the merge-base string and
   the intermediate string RED, a duration carrying its framing stays GREEN, and a
   duration with only "one box" for company (hardware is not framing) REDs.

24. `test_a_stated_fact_cannot_be_satisfied_by_prose` — the fact check reads
   source with comments and docstrings BLANKED (`_code_lines`), because a fact
   in a comment is not a fact about executed code. Measured: weaken
   `sdk.provenance`'s query to `MATCH (s)-[r]->(n)` and leave the old
   `MATCH (s:Subject {id:$sid})-[r]->(n)` in a comment above it, and on raw
   source the arm stays GREEN while the walk observes `aboutObject` with no
   `:Subject` bound. The stripper is column-exact on comments (a trailing
   comment must not take its code line with it) and blanks a whole bare-string
   statement.
25. `test_a_stated_fact_is_bound_to_one_site_not_to_its_symbol` — the register
   key is `(file, symbol)` and the derivation is per-SITE, so a symbol-wide
   fact span lets one walk's ground vouch for its neighbour AND for anything
   appended later. Measured: one unconstrained all-edge walk added inside
   `sdk.provenance` (`MATCH (x)-[rr]->(y) RETURN count(rr)`) kept every arm
   green, while `origin/main`'s per-walk guard reded on it. `mining.verify_no_
   auto_wire`'s two walks are the live fixture: each site must carry a fact,
   and the two must not resolve to the SAME fact set.

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
import inspect
import io
import re
import tokenize
from pathlib import Path

import pytest

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


@functools.cache
def _code_lines(path: Path) -> tuple[str, ...]:
    """The module's source with COMMENTS and PROSE STRINGS blanked out.

    ⚠️ **A fact check over raw source is satisfiable by text that never runs, and
    that is measured, not hypothetical.** Leaving the old query in a comment
    above a weakened one — `# MATCH (s:Subject {id:$sid})-[r]->(n)` over
    `MATCH (s)-[r]->(n)` — keeps every substring assertion green while the walk
    observes `aboutObject`, which is the exact fail-open this arm exists to
    close, one edit away. A docstring is the same hole: it is prose (item 18 of
    this file's map pins that rule for the literal census, and the fact check
    must not be the one place that forgot it).

    Comments are cut by `tokenize`, which is the only thing that knows where a
    `#` is inside a string and where it is not, and the cut is COLUMN-EXACT so
    code sharing a line with a trailing comment survives. A bare string
    statement is any `ast.Expr` wrapping a `Constant` str — docstrings are only
    its first-statement special case — and its lines are blanked whole. Line
    count and line numbers are preserved, so every span below is still a span
    into the file the AST came from.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        row, start, end = token.start[0], token.start[1], token.end[1]
        line = lines[row - 1]
        lines[row - 1] = line[:start] + " " * (end - start) + line[end:]
    for node in ast.walk(_module_tree(path)):
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        for row in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            lines[row - 1] = ""
    return tuple(lines)


def _statement_span(tree: ast.Module, line: int) -> tuple[int, int] | None:
    """The span of the STATEMENT the given line belongs to — innermost, exact.

    `ast.stmt` nodes nest (`For` → `Expr` → the call), so the innermost one
    containing the line is the statement the walk is written in. Equal spans are
    indistinguishable (the same lines are the same text), so the tie-break only
    has to be deterministic.
    """
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        low, high = node.lineno, node.end_lineno or node.lineno
        if not low <= line <= high:
            continue
        if best is None or high - low < best[1] - best[0] or low > best[0]:
            best = (low, high)
    return best


def _site_fact_text(rel: str, line: int) -> str:
    """The source ONE stated exclusion FACT must appear in, or raise.

    ⚠️ **The span is the SITE's statement, never the whole registered symbol, and
    that is the difference between a register and a voucher.** The register key
    is `(file, symbol)` while the derivation is per-SITE, so a symbol-keyed fact
    check silently owns every FUTURE walk added beside a registered one: measured
    on the pre-fix arm, adding one fully unconstrained all-edge walk to
    `sdk.provenance` (`MATCH (x)-[rr]->(y) RETURN count(rr)`) kept the whole file
    green, while `origin/main`'s per-walk guard reded on it. `#9493` replaced
    that guard's per-walk bound with a per-symbol one, which is strictly weaker.

    The statement span, plus — ONE level, no recursion — the body of every
    module-level constant that span NAMES. The second half is load-bearing and
    was measured, not assumed: three of the nine entries keep their Cypher in a
    query CONSTANT their statement executes (`session_source_migration._DEGREE`
    behind `_degree`, `_OUT_TYPES`/`_IN_TYPES` behind `_rel_types`), and the
    derived SITE for those is the constant's own line. Without it the arm would
    have had to weaken those facts to the constant's NAME — a check that the
    entry still mentions a symbol, which is the unread claim it replaces.

    `rel` is spelled from the REPO root (`tortoise/projection/entities.py`,
    as the register writes it), while a §13 row spells it from the package
    (`projection/entities.py`); both forms resolve here.
    """
    path = PKG / rel.removeprefix("tortoise/")
    lines = _code_lines(path)
    tree = _module_tree(path)
    span = _statement_span(tree, line)
    assert span is not None, (
        f"`{rel}:{line}` — the derivation attributed a site to a line this module "
        "has no statement at, so its stated exclusion cannot be checked against "
        "any code"
    )
    text = "\n".join(lines[span[0] - 1 : span[1]])
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        name = _bound_name(node)
        if name is None or not re.search(rf"\b{re.escape(name)}\b", text):
            continue
        text += "\n" + "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
    return text


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
        if _call_time_census()._inside_pattern_comprehension(value, match.start()):
            return True
        clauses = _CLAUSE.findall(value[: match.start()])
        if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
            return True
    return False


def _reads_naming_the_edge(tree: ast.Module, lo: int, hi: int) -> bool:
    """True when a Cypher READ clause inside [lo, hi] names the edge literally.

    ⚠️ **A DOCSTRING is prose, not a query, and it is skipped here** — the same
    `_docstring_ids()` filter the `#9398` derivation applies (`_docstrings` in
    `tests/_about_call_time_census.py`), threaded
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
        # ⚠️ A row in either DERIVED class is not decided here: a call-time type
        # slot (`-[r:{rel}]->`) and an empty relationship pattern are exactly the
        # shapes this literal/computed-name test cannot see, which is why they
        # have their own derivations (see `test_the_call_time_shape_is_derived_
        # and_declared` / `test_the_empty_pattern_class_is_derived_and_
        # declared`). One definition per class: this arm decides rows whose read
        # is a written name, those arms decide rows whose read is a shape.
        census = _call_time_census()
        shape_owned = (
            _derived(census._census())
            | _derived(census._call_time_sites())
            | _derived(census._empty_pattern_sites())
        )
        if _site_key(str(row["module"]), str(row["symbol"])) in shape_owned:
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
    # ⚠️ The VERB is part of the door key. Stripping it (the earlier shape) made the
    # verb uncheckable: rows 28-31 all share a path with a sibling route that reads
    # no edge, so a cell could name the wrong verb and every arm stayed green.
    doors |= {f"REST {match.group(1)}": "rest" for match in _DOOR_REST.finditer(cell)}
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


@functools.cache
def _route_table() -> dict[tuple[str, str], frozenset[str]]:
    """`(VERB, path) -> the handler functions decorated with exactly that route`.

    ONE tree-wide table, consumed by BOTH legs of the door check (existence and
    attribution), because two tables is two definitions of "this door is a
    route" — and they already disagreed here once. Measured before this shape:
    the existence leg read `tortoise/hosted_api.py` only while the attribution leg
    scanned the whole tree, so a route legitimately moved into another module
    passed attribution and red-ed existence with "is not a route in
    hosted_api.py". Today both sets are the same 132 paths, so that divergence was
    only latent; one table makes it impossible rather than currently harmless.

    ⚠️ **The VERB is part of the key, and that is the half that was missing.**
    `_doors()` used to keep only the path, and this function used to union every
    verb's handler at that path, so a cell stating `REST `POST /v1/backups`` was
    never checked for `POST` — measured, rewriting row 28's door to
    `REST `GET /v1/backups`` (the list endpoint, which dumps no edge at all) left
    all arms green. Keyed on `(verb, path)` that substitution reds, because the
    `GET` handler is a different function with a different reference closure.

    Scanned across EVERY module so that a legitimate move is not a false red, and
    the row's reach cell is the only place a door may be spelled.
    """
    table: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(PKG))
        for node in ast.walk(_parsed(str(path))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"
                    and func.attr in {"get", "post", "put", "patch", "delete"}
                ):
                    continue
                if isinstance(decorator.args[0], ast.Constant):
                    key = (func.attr.upper(), str(decorator.args[0].value))
                    table.setdefault(key, set()).add(f"{rel}::{node.name}")
    assert table, "no `@app.<verb>(path)` routes found anywhere under tortoise/"
    return {key: frozenset(ids) for key, ids in table.items()}


# ── door → reader ATTRIBUTION ─────────────────────────────────────────────
#
# ⚠️ **An existence check is not an attribution, and that gap was measured.**
# The arm below originally asserted only that each named door EXISTS — a
# registered tool, a public `TortoiseSDK` method, a live route. Measured on the
# shipped guard (head docs, mutation applied, all arms re-run, mutation
# reverted), substituting row 21's `MCP `tortoise_supersede`` for
# `MCP `tortoise_suggest_entry_points`` left **every arm green** — and likewise
# for row 4. Both substituted doors pass the existence check, and neither reads
# the edge (`tortoise_suggest_entry_points` reads no `aboutObject`; that is
# provable from this file's own greenness, since a literal read would red
# `test_no_read_site_lacks_a_row`). The blast-radius figure — *"reached through
# TWENTY-TWO doors"* — is DERIVED from these cells, so an existence-only check let a
# **false door** carry a **real count**: the half of the argument that survives
# re-derivation would be the half nothing re-derived.
#
# The relation is a bounded REFERENCE CLOSURE over `tortoise/**/*.py`. From the
# door's entry symbol it follows what a body actually REACHES — a call target, a
# function passed as a VALUE (`_safe(_preview_supersede, …)`, which is how the
# supersede dry run is reached and which a call-target-only graph misses), a
# carrier constant, or an imported name with `from x import y as _y` resolved
# back to `y` (how `sdk.topic_summarize` reaches its reader at all). The reader
# must be in that set.
#
# ⚠️ **Declared residuals — each MEASURED here, not assumed:**
#
#   * **The relation is PERMISSIVE, so it can miss a bad door; it cannot invent
#     reach.** Resolution is by name, so a call through a computed attribute
#     (`getattr(obj, name)`) is invisible. That direction is chosen: a
#     permissive relation false-REDS nothing, and this arm's job is to stop a
#     *substituted* door, not to prove a call graph.
#   * **A door that ENTERS the shared search path is attributed to the readers
#     that path touches — measured, not merely possible.**
#     `tortoise_suggest_entry_points` reaches row 2's reader in three hops
#     (`tortoise_suggest_entry_points` → `sdk.TortoiseSDK.tortoise_fts_query` →
#     `StateRanker.rerank` → `_fetch_point_signals`), so it is a TRUE door for that
#     row and is deliberately not used as a negative probe. The arm therefore
#     catches a door on a DIFFERENT surface path (the two measured bypasses
#     below), and does NOT certify that a named door is the CANONICAL door — a
#     substitution into the same shared lane would still pass. Stated because the
#     alternative is a comment claiming more than the predicate proves.
#   * **`_DOOR_REACH_HOPS` is a BOUND, and the number is a MEASUREMENT, not a
#     guess — the first one was WRONG and a review caught it.** A uniform budget
#     of 5 attributed every row of the table as it stood then, so it looked safe;
#     measured against substitutions that keep a REAL tool in the cell it accepted
#     it swallowed the largest share of the (row, table-door) pairs, and three such
#     substitutions stayed GREEN (rows 22/24's reach cells swapped, rows 28-31's
#     backup doors swapped, and row 28's verb changed to the sibling `GET` that
#     dumps nothing). At a uniform 3 hops the same sweep accepts far less and all
#     three red; the SHIPPED predicate — the exceptions in `_DISTANCE_EXCEPTIONS`
#     included — accepts the rate the arm below pins (`_ACCEPTED_PAIRS_AT_THIS_HEAD`
#     over the derived population), because a rate restated in prose is a count
#     that rots: this sentence still read 63/340 = 18.5 % after the table grew.
#     Depth still trades both
#     ways — at 2 hops nine legitimate rows false-red — and the only two true
#     attributions beyond 3 are named in `_DISTANCE_EXCEPTIONS` with the code fact
#     that earns each. ⚠️ This is why the bound is pinned on BOTH sides: the
#     acceptance rate is itself an arm
#     (`test_the_attribution_predicate_does_not_swallow_the_whole_table`), so the
#     predicate cannot quietly go permissive again.
#   * **Falsifiability is a SEPARATE arm**
#     (`test_the_door_attribution_refuses_a_door_that_reaches_another_reader`),
#     not a comment here: it runs the live registered tools through this same
#     predicate, so the gap cannot silently re-open as "latent".

#: How many reference hops a door→reader attribution may take. See the residual
#: note above: this is a BOUND, not a proof.
#:
#: ⚠️ **Three, and the tighter number is what makes the arm discriminate.** A
#: uniform budget of 5 was measured to accept **16.5 % of the 418
#: (row, table-door) pairs** — three substitutions that keep a REAL tool in a
#: cell were green under it (rows 22/24's reach cells swapped, rows 28/30's REST
#: doors swapped, and row 28's verb changed). A uniform 3 accepts **15.6 %**, and
#: all three of those red; the shipped predicate, with the two exceptions below,
#: accepts 69/418 = **16.5 %**. Every one of the 31 true attributions is within 3
#: hops except the two named in `_DISTANCE_EXCEPTIONS`.
_DOOR_REACH_HOPS = 3

#: Readers whose door chain is legitimately LONGER than `_DOOR_REACH_HOPS`,
#: keyed by the reader symbol (the same key the table uses — a row NUMBER would
#: churn every time the table is renumbered). Each entry states the code fact that
#: earns it, so this is a register of measured exceptions rather than a place to
#: park a failing row: an entry silences ONE row's budget, and the arm's message
#: asks for exactly this rather than for a looser global bound.
_DISTANCE_EXCEPTIONS: dict[str, tuple[int, str]] = {
    "hosted_backup.py::_COUNT_ALL_EDGES_QUERY": (
        4,
        "the dump's verify leg is `_paged_rows(g, _DUMP_EDGE_PAGE_QUERY, 4)` — the "
        "count constant is reached through the pager, which the query-declaring "
        "handler reaches one reference later than the query it pages",
    ),
    "hosted_backup.py::_restore_copy_settled": (
        5,
        "the settle leg sits behind the restore orchestrator AND its settle helper "
        "(`restore_graph` -> `_restore_copy_settled`), one further than the restore "
        "graph itself",
    ),
}

#: How far the search itself may walk. Deliberately above every budget in play, so
#: a `None` means UNREACHABLE rather than "further than we looked" — the message
#: distinguishes the two, and a short search cap would report a long chain as no
#: chain at all.
_DISTANCE_SEARCH_CAP = 12


@functools.cache
def _symbol_index() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """`(symbol name -> defining node ids, node id -> names its body mentions)`.

    A node id is `module.py::Qualname`, the SAME spelling §13's table uses for a
    reader, so an attribution compares like with like. Module- and class-level
    CONSTANTS are indexed too: rows 28-29 name `_DUMP_EDGE_PAGE_QUERY` /
    `_COUNT_ALL_EDGES_QUERY`, which are strings rather than functions, and a
    function-only index would have reported those two rows as unreachable — a
    false red produced by the instrument, not by the table.
    """
    defs: dict[str, set[str]] = {}
    refs: dict[str, frozenset[str]] = {}

    def mentions(subtree: ast.AST) -> frozenset[str]:
        """What a body REACHES — calls and references, not every attribute name.

        Tightened from an all-`Name`/all-`Attribute` sweep because that spelling
        follows incidental mentions (`x.some_attr` in a comparison, a keyword of
        an unrelated call) and so over-reaches by a hop or more. Call targets and
        call ARGUMENTS stay, the latter because a function is routinely passed as
        a value rather than called in place.
        """
        names: set[str] = set()

        def reference(node: ast.AST | None) -> None:
            if node is None:
                return
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name):
                    names.add(inner.id)
                elif isinstance(inner, ast.Attribute):
                    names.add(inner.attr)

        for inner in ast.walk(subtree):
            if isinstance(inner, ast.Call):
                func = inner.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
                for argument in list(inner.args) + [kw.value for kw in inner.keywords]:
                    reference(argument)
            elif isinstance(inner, ast.ImportFrom):
                # `from .m import reader as _r` REFERENCES `reader`.
                names.update(alias.name for alias in inner.names)
            elif isinstance(inner, ast.Import):
                names.update(alias.name.split(".")[-1] for alias in inner.names)
            elif isinstance(inner, (ast.Assign, ast.AnnAssign)):
                reference(inner.value)
        return frozenset(names)

    def index(body, rel: str, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                node_id = f"{rel}::{qual}"
                defs.setdefault(node.name, set()).add(node_id)
                refs[node_id] = mentions(node)
                index(node.body, rel, f"{qual}.")
            elif isinstance(node, ast.ClassDef):
                defs.setdefault(node.name, set()).add(f"{rel}::{prefix}{node.name}")
                index(node.body, rel, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        node_id = f"{rel}::{prefix}{target.id}"
                        defs.setdefault(target.id, set()).add(node_id)
                        refs[node_id] = mentions(node.value)

    for path in sorted(PKG.rglob("*.py")):
        index(_parsed(str(path)).body, str(path.relative_to(PKG)), "")

    assert defs, "the symbol index is empty — no functions were found under tortoise/"
    return (
        {name: frozenset(ids) for name, ids in defs.items()},
        refs,
    )


@functools.cache
def _door_entry_ids(door: str) -> frozenset[str]:
    """The symbols a door STARTS from. Empty means the door cannot be resolved."""
    kind, name = door.split(" ", 1)
    if kind == "SDK":
        return frozenset({f"sdk.py::TortoiseSDK.{name}"})
    if kind == "REST":
        route = tuple(name.split(" ", 1))
        assert len(route) == 2, (
            f"REST door {name!r} carries no verb — §13 must spell a REST door as "
            "REST `VERB /v1/…` so the verb can be checked against the route table"
        )
        return _route_table().get((route[0].upper(), route[1]), frozenset())
    # MCP: the entry point is the module-level handler in `mcp_server.py` named
    # exactly as the tool (`def tortoise_supersede(...)`). ⚠️ The `sdk_method`
    # binding a `ToolDefinition` declares is deliberately NOT added to the entry
    # set: measured, it changes none of the MCP attributions (the handler's own
    # closure already reaches the SDK method it forwards to), and it is not a
    # substitute for the handler — only the handler carries the dry-run preview,
    # so a binding-only index false-reds row 21.
    handler = f"mcp_server.py::{name}"
    return frozenset({handler}) if handler in _symbol_index()[1] else frozenset()


@functools.cache
def _door_distance(door: str, reader: str) -> int | None:
    """Shortest reference distance from `door` to `reader`; `None` = unreachable.

    Returns the DISTANCE rather than a boolean so the failure can say how far the
    chain actually is — "needs 4 references, budget is 3" is actionable, "not
    reachable" for a chain that exists is not.
    """
    if not door or not reader:
        return None
    defs, refs = _symbol_index()
    frontier = set(_door_entry_ids(door))
    if reader in frontier:
        return 0
    seen = set(frontier)
    for distance in range(1, _DISTANCE_SEARCH_CAP + 1):
        nxt: set[str] = set()
        for node_id in frontier:
            for name in refs.get(node_id, frozenset()):
                nxt |= defs.get(name, frozenset()) - seen
        if not nxt:
            return None
        if reader in nxt:
            return distance
        seen |= nxt
        frontier = nxt
    return None


def _distance_budget(reader: str) -> int:
    """How many references this reader's door is allowed to take."""
    exception = _DISTANCE_EXCEPTIONS.get(reader)
    return exception[0] if exception else _DOOR_REACH_HOPS


def _door_reaches(door: str, reader: str) -> bool:
    """Does `door` reach `reader` within this reader's budget?"""
    distance = _door_distance(door, reader)
    return distance is not None and distance <= _distance_budget(reader)


def test_the_door_attribution_refuses_a_door_that_reaches_another_reader():
    """The attribution arm is only a CHECK if its predicate can REFUSE.

    Runs the LIVE registered tools through the same `_door_reaches` the arm
    above uses, so this is not a fixture that agrees with itself: swap the names
    for real ones and a permissive-by-accident predicate (one that answers True
    for everything) reds here instead of quietly greening every row above.

    The two substitutions are the measured bypass from the attribution note: each
    is a REGISTERED tool, so each passed the existence check, and each left all
    arms green before attribution existed.

    ⚠️ **The probes are deliberately limited to doors on a DIFFERENT surface
    path.** `tortoise_suggest_entry_points` is NOT used as a probe against row
    2's reader: measured, it genuinely reaches it in three hops
    (`tortoise_suggest_entry_points` → `sdk.TortoiseSDK.tortoise_fts_query` →
    `StateRanker.rerank` → `_fetch_point_signals`), so asserting it is refused
    there would be asserting a code fact that is false.
    """
    # The true door reaches its own reader…
    assert _door_reaches("MCP tortoise_supersede", "mcp_server.py::_preview_supersede")
    assert _door_reaches("MCP tortoise_recall", "ranking.py::StateRanker._fetch_point_signals")
    assert _door_reaches(
        "REST GET /v1/organizations/{org_id}/export", "hosted_api.py::_export_graph_snapshot"
    ), "the REST door does not resolve to its decorated handler at all"

    # …and a door that exists but is on another surface path must not be
    # attributed to a reader it does not reach.
    for wrong in (
        "mcp_server.py::_preview_supersede",
        "sdk.py::TortoiseSDK.belief_timeline",
    ):
        assert not _door_reaches("MCP tortoise_suggest_entry_points", wrong), (
            f"MCP `tortoise_suggest_entry_points` is attributed to `{wrong}`, which it "
            "does not reach — the door→reader predicate answers True too freely, so "
            "`test_every_customer_callable_row_reaches_a_declared_door` would pass a "
            "substituted door."
        )


#: Measured at this head: how many of the table's own TWENTY-TWO doors the attribution
#: predicate accepts, summed over the nineteen customer-callable rows, and the
#: worst single row. Inputs are the TABLE (whose doors change only when the
#: document does) rather than the 99-tool registry, so a new tool does not move
#: these — which is what makes them pinnable.
#: Re-derived 2026-10-21 when rows 34-37 landed: 63 -> 69. The added
#: pairs are NEW rows with their own doors (`TortoiseSDK.traverse`,
#: `TortoiseSDK.ingest`), not a looser relation — no existing row's accepted set
#: grew, and no existing row gained a door, which is what distinguishes this from
#: the tripwire's failure mode.
_ACCEPTED_PAIRS_AT_THIS_HEAD = 69
#: 15 -> 17 on 2026-10-21: the TABLE's door set grew by the two SDK doors the new
#: call-time rows name, and `_hop_query` (an all-edge walk) legitimately reaches
#: more of a larger set. It is the same relation over a larger population, not a
#: looser predicate — no door was added to any row's own reach cell.
_WORST_ROW_AT_THIS_HEAD = 17


def test_the_attribution_predicate_does_not_swallow_the_whole_table():
    """The bound above is only a check if the predicate still DISCRIMINATES.

    The REJECTED `_DOOR_REACH_HOPS = 5` accepted roughly two fifths of the
    (row, table-door) pairs *as the table stood then* — and three substitutions
    that keep a REAL tool in a cell stayed green under it. (The figure is stated as
    a proportion on purpose: the population has grown since, and a percentage
    restated against a fixed denominator is the rot this file retires.) A bound is not a proof, so the acceptance RATE is
    pinned as its own number: the predicate may accept, over the table's own
    doors, at most `_ACCEPTED_PAIRS_AT_THIS_HEAD` pairs and at most
    `_WORST_ROW_AT_THIS_HEAD` for any single row. A predicate that starts
    accepting everything moves these and reds here — which is the tripwire the
    residual note describes in prose, expressed as an arm for the same reason
    (16) gives about the fragment census.

    ⚠️ **Both figures are MEASURED, and neither is to be nudged.** A RISE means
    the relation loosened (fix the relation, not the ceiling); a FALL means it
    tightened, and the new figure should be re-derived and the reason recorded.
    """
    rows = [row for row in _rows() if _reach(row) == "customer-callable"]
    table_doors = sorted({door for row in rows for door in _doors(str(row["reach"]))})
    assert rows and table_doors, "the sweep found no customer-callable rows or no doors"

    accepted: dict[int, list[str]] = {}
    for row in rows:
        reader = f"{row['module']}::{row['symbol']}"
        accepted[int(row["n"])] = [door for door in table_doors if _door_reaches(door, reader)]

    total = sum(len(doors) for doors in accepted.values())
    worst_row, worst = max(accepted.items(), key=lambda item: len(item[1]))

    assert total <= _ACCEPTED_PAIRS_AT_THIS_HEAD, (
        f"the door→reader predicate now accepts {total} of the "
        f"{len(rows) * len(table_doors)} (row, table-door) pairs, up from "
        f"{_ACCEPTED_PAIRS_AT_THIS_HEAD} — it has loosened, so substitutions it used "
        f"to refuse now pass `test_every_customer_callable_row_reaches_a_declared_door`. "
        f"Worst row: {worst_row} accepting {len(worst)}/{len(table_doors)} "
        f"({', '.join(worst)}). Tighten the relation (or its budget) rather than "
        "raising this ceiling."
    )
    assert len(worst) <= _WORST_ROW_AT_THIS_HEAD, (
        f"row {worst_row} ({rows[[int(r['n']) for r in rows].index(worst_row)]['module']}::"
        f"{rows[[int(r['n']) for r in rows].index(worst_row)]['symbol']}) has "
        f"{len(worst)}/{len(table_doors)} of the table's doors attributed to it, up "
        f"from {_WORST_ROW_AT_THIS_HEAD} — for that row the arm has stopped "
        f"discriminating: {', '.join(worst)}"
    )
    # The tripwire must not be satisfied by a predicate that accepts NOTHING:
    # every true attribution is inside this set by construction.
    assert total >= 31, (
        f"only {total} pairs are accepted although §13's cells name 31 door "
        "occurrences (the count the table arm pins) — the predicate has broken the "
        "other way"
    )


def test_every_customer_callable_row_reaches_a_declared_door():
    sdk_methods = _public_sdk_methods()
    tools = _registered_tools()
    routes = _route_table()
    broken: list[str] = []
    attributed = 0

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
            "against the route table"
            for span in _loose_rest_doors(cell)
        )
        if not doors:
            if not _loose_rest_doors(cell):
                broken.append(
                    f"{label} — names no door (SDK/MCP/REST backticked name)"
                )
            continue
        reader = f"{row['module']}::{row['symbol']}"
        for door in sorted(doors):
            kind, name = door.split(" ", 1)
            if kind == "SDK" and name not in sdk_methods:
                broken.append(
                    f"{label} — SDK `{name}` is not a public TortoiseSDK method"
                )
                continue
            if kind == "MCP" and name not in tools:
                broken.append(f"{label} — MCP `{name}` is not a registered tool")
                continue
            if kind == "REST":
                verb, _, path = name.partition(" ")
                if (verb.upper(), path) not in routes:
                    # The PATH may well exist under another verb — that is a different
                    # door, and saying which verb it carries is what tells a reader
                    # whether the row is stale or the route was renamed.
                    other_verb = sorted(v for v, p in routes if p == path)
                    hint = (
                        f" — the path exists, but under {'/'.join(other_verb)}"
                        if other_verb
                        else " — no route carries that path under any verb"
                    )
                    broken.append(
                        f"{label} — REST `{name}` is not a route: no "
                        f"`@app.{verb.lower()}({path!r})` decorator exists anywhere under "
                        "tortoise/ (the VERB is checked, not just the path — a sibling "
                        f"route on the same path is a different door){hint}"
                    )
                    continue
            # ⚠️ EXISTENCE IS NOT ATTRIBUTION — see the note above this section.
            # A door that exists but reaches a DIFFERENT reader is not this row's
            # door, and the blast-radius count would be counted from it.
            attributed += 1
            hops = _distance_budget(reader)
            distance = _door_distance(door, reader)
            if distance is None or distance > hops:
                reached = "does not reach it at all" if distance is None else f"needs {distance} references"
                broken.append(
                    f"{label} — {door} exists but {reached} to reach `{reader}`, and this "
                    f"row's budget is {hops} — the door is not a door to THIS reader, so "
                    "the blast-radius count would be counted from the wrong tool "
                    "(if the chain is legitimately longer now, give the reader an entry in "
                    "`_DISTANCE_EXCEPTIONS` with the reason, rather than loosening the budget "
                    "for every row)"
                )

    # A predicate that silently stops resolving its entries turns this arm green
    # on an assertion it never made — so the floor is asserted, not assumed.
    assert attributed >= 31, (
        f"only {attributed} door attributions were attempted, and §13's "
        "customer-callable cells name 31 door OCCURRENCES — a lower floor let a dead "
        "door KIND parser (all six SDK doors, say) pass this arm silently"
    )
    assert not broken, (
        "§13 classes these rows customer-callable without naming a door that "
        "exists AND reaches the row's own reader:\n  " + "\n  ".join(broken)
    )


#: A stated door count: `SIXTEEN distinct doors`, `sixteen doors`, `SIXTEEN
#: doors`. A HISTORICAL figure ("`#7956` corrected it to four *doors*") is
#: deliberately not matched — it carries emphasis between the numeral and the
#: noun, and it describes a past state rather than the table in front of it.
_STATED_DOOR_COUNT = re.compile(r"\b([A-Z]+(?:-[A-Z]+)?|\d+) (?:distinct )?doors?\b")

#: Case-INSENSITIVE, for the one place the figure is stated inside a sentence
#: (`... across twelve doors`): the residue that survived the first fix was
#: lower-case and the capital-only pattern above could not see it.
#: ⚠️ The compound form must be in the pattern, or a HYPHENATED figure is read as
#: its last word: `TWENTY-TWO doors` matched `TWO doors` and yielded 2, which is a
#: guard defect that only shows up once a count crosses twenty (it did, when the
#: table grew to 22 doors). `_STATED_DOOR_COUNT` always carried the hyphen form;
#: this capital-insensitive twin did not.
_ANY_DOOR_COUNT = re.compile(r"\b([A-Z]+(?:-[A-Z]+)?|\d+) (?:distinct )?doors?\b", re.I)


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
    """The document's spelling of a count — `NINETEEN`, `TWENTY-TWO`, `THIRTY-SEVEN`.

    ⚠️ The counts are spoken in the document as COMPOUND words, and the first cut
    of this helper knew only the tens, so raising the count past 29 redded with
    *"22 has no spelled form this guard knows"* — a guard failure about the
    guard. The compound form is now generated from `_TENS` + `_NUMBER_WORDS`
    (the same tables `_words` reads back), which is what made the gap possible.
    """
    for word, number in _NUMBER_WORDS.items():
        if number == value:
            return word
    for tens_word, tens_value in _TENS.items():
        units = value - tens_value
        if 0 < units < 10:
            for word, number in _NUMBER_WORDS.items():
                if number == units:
                    return f"{tens_word}-{word}"
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
    route; counted ONCE however many of the nineteen sites reach it* — and both
    documents' stated total, and the per-kind breakdown, are asserted against the
    table. ⚠️ **The first cut of that rule said "counted once per site that
    reaches it", which does not produce the figure it certifies**: applied
    literally it yields the CELL-OCCURRENCE total (31), not the distinct door
    total (22), and a reader following it cannot reproduce either number. The
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
        # ⚠️ EVERY sentence that states the total must carry the breakdown — not
        # just one of them. `any(...)` over `tails` let a corrected sentence at
        # the headline vouch for a stale one a few lines below (measured: the
        # blast-radius sentence still said "six SDK methods" and "the seventeen
        # sites" while the headline was right, and the suite stayed green).
        if any(
            f"{_spelled(kinds.get(kind, 0))} {noun}".lower() not in tail
            for tail in tails
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
    # / `recall_state` alone land in several rows, so the number of distinct doors and
    # the number of CELLS differ, which is why the document must state both.
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
    sibling arms (`assert census`, `assert pins`, `assert register`)
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
    # ⚠️ the marker follows the sentence that ACTUALLY starts the tail — it was re-worded to
    # "Outside this nineteen, ..." to stop a re-verifier counting those five inside the nineteen
    "Outside this nineteen, two are default-ON on the ingestion path",
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


# ---------------------------------------------------------------------------
# The two classes this guard does NOT re-derive: a relationship type supplied at
# CALL TIME, and an EMPTY relationship pattern in either legal spelling.
#
# ⚠️ **They are derived — just not here.** `#9398` landed those two derivations
# as the tests of its own table, pinned to that table's prose (a row count and a
# `<from> → <to> sites` change chain). The table was re-keyed onto
# `module.py::symbol` and the narration moved to the changelog, so the pins could
# not survive the merge — but the derivations are the part that mattered: a
# reader that writes its type as an expression (`-[r:{rel}]->`) or omits the type
# altogether (`-[r]->`, `(n)--()`) is invisible to every name-based scan, which is
# the exact blind spot §13 exists to close. They now live in
# `tests/_about_call_time_census.py` (moved verbatim from that guard) and are
# consumed here, so there is ONE derivation and the pins stay with the table.
#
# Both registers are checked in BOTH directions, which is what makes the class
# fail-closed rather than declared: a derived site must be a row or a register
# entry (a NEW reader of the shape cannot ship green), and a register entry must
# still derive (a stale entry cannot hide the next reader).
# ---------------------------------------------------------------------------

@functools.cache
def _call_time_census():
    """The `#9398` derivation module, imported by path (it is not a test module)."""
    import importlib.util

    path = Path(__file__).with_name("_about_call_time_census.py")
    assert path.exists(), (
        f"{path.name} carries the call-time and empty-pattern derivations and is "
        "missing — without it those two classes are unguarded, which is the "
        "regression this arm exists to prevent"
    )
    spec = importlib.util.spec_from_file_location("_about_call_time_census", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The DERIVED side of the same fact: the resolver attributes a read to the innermost
#: `def` or to the function that executes a query constant, while the table names the
#: symbol a re-verifier should open. Each entry is grounded — `test_the_derived_aliases_
#: are_grounded` asserts both halves exist in the module — so an alias cannot become a
#: way of naming nothing.
_DERIVED_ALIASES: dict[str, str] = {
    # nested defs: the row names the port that CALLS them
    "assembly.py::alias_objects": "assembly.py::docker_resolver_port",
    "assembly.py::spine_rows": "assembly.py::docker_walker_port",
    # one consumer of two query constants: the row names the constants
    "subgraph.py::_collect_raw": "subgraph.py::_CYPHER_ABOUT",
}

#: A row may key on a module-level query CONSTANT where the derivation attributes the
#: read to the function that EXECUTES it — both are true, and the row names the thing a
#: re-verifier can find. Each alias says which consumer runs the constant, so the
#: pairing is a stated fact rather than a fuzzy match.
_SITE_ALIASES: dict[str, str] = {
    "definite_description_backfill.py::CENSUS_QUERY": "definite_description_backfill.py::census_rows",
    # #9493: rows that name the CONSTANT an all-edge walk lives in, while the
    # derivation attributes the site to the function that EXECUTES it — the same
    # stated-fact pairing the entry above established.
    "hosted_backup.py::_DUMP_EDGE_PAGE_QUERY": "hosted_backup.py::dump_graph",
    "hosted_backup.py::_COUNT_ALL_EDGES_QUERY": "hosted_backup.py::_independent_counts",
    "subgraph.py::_CYPHER_HUB_DEGREE": "subgraph.py::_hub_degrees",
}


def _site_key(rel: str, symbol: str) -> str:
    """`file.py::Last` — the table keys on `module.py::Symbol`, the derivation on the
    bare name, so both sides are normalised to the LAST dotted component."""
    return f"{Path(rel).name}::{symbol.rsplit('.', 1)[-1]}"


def _derived(keys: dict[tuple[str, str], tuple[int, ...]]) -> dict[str, tuple[int, ...]]:
    """`module.py::symbol` → the lines, from the support module's `(file, symbol)` keys."""
    return {_site_key(rel, symbol): tuple(lines) for (rel, symbol), lines in keys.items()}


def _register_keys(entries) -> set[str]:
    # `EMPTY_PATTERN` entries carry a fourth element (the code facts their reason
    # rests on) and a fifth (the site count), `CALL_TIME_REL` entries carry
    # neither — both key the same way.
    return {_site_key(rel, symbol) for rel, symbol, *_rest in entries}


def _declared_keys() -> set[str]:
    """The rows' keys, plus each alias's target so a constant-keyed row matches."""
    keys: set[str] = set()
    for row in _rows():
        key = _site_key("x/" + str(row["module"]), str(row["symbol"]))
        keys.add(key)
        keys.add(_SITE_ALIASES.get(key, key))
        keys.add(_SITE_ALIASES.get(key, ""))
    return {k for k in keys if k}


def test_every_named_census_site_owns_a_row():
    """The named census's FORWARD direction — the half that was missing.

    ⚠️ **`link_entity` was pinned only by a prose numeral.** Its `aboutObject` is a
    PARAMETER DEFAULT (`edge_type: str = "aboutObject"`), not a literal in a read
    clause, so the literal census cannot see it and the call-time class does not own
    it either (the resolver resolves the default) — it landed in the named census,
    which was consumed only as a permissive whitelist. Measured: deleting row 34 AND
    writing `THIRTY-SIX` for `THIRTY-SEVEN` left all 26 arms green, i.e. one of the
    four rows this change exists to restore was enforced by a single word. The
    control — the same deletion for a literal reader (`belief_timeline`) — reds.

    A register is only as good as its completeness direction, so this arm requires
    every site the named census derives to own a row, an alias, or a register entry.
    """
    census = _call_time_census()
    derived = _derived(census._census())
    assert derived, (
        "the named census derived nothing — the resolver stopped resolving "
        "parameter defaults and loop variables, which is what makes it the only "
        "route to a reader like `link_entity`"
    )
    declared = _declared_keys()
    registered = _register_keys(census.CALL_TIME_REL) | _register_keys(census.EMPTY_PATTERN)
    # ⚠️ Keep the DICT: the failure message indexes it, so rebinding the name to a
    # set made the diagnostic raise `TypeError: 'set' object is not subscriptable`
    # on exactly the path it exists for (a reviewer's mutation found it).
    derived = {
        _DERIVED_ALIASES.get(key, key): lines for key, lines in derived.items()
    }
    unowned = sorted(set(derived) - declared - registered)
    assert not unowned, (
        "the resolver sees these sites reading the `aboutObject` edge through a "
        "value it had to RESOLVE (a parameter default, a loop variable, a dict "
        "lookup), and §13 neither names them nor declares them out of scope:\n"
        + "\n".join(f"  - {key} — lines {derived[key]}" for key in unowned)
        + "\nAdd the row, or an alias in `_SITE_ALIASES` when a row names the "
        "CONSTANT (or the enclosing function) the site consumes."
    )

def test_the_call_time_shape_is_derived_and_declared():
    """A type slot holding an EXPRESSION must be a row or a stated exclusion.

    ⚠️ **This is the class no literal scan can see, and it is not hypothetical:**
    `TortoiseSDK.traverse` fills the type from a CALLER ARGUMENT (gated only by
    `validate_rel_type` over `KNOWN_REL_TYPES`, which carries the about* family)
    and `TortoiseSDK.ingest` fills it from the bundle's `relation`, allowlisted
    against `_VALID_EDGE_PREDICATES`. Both are customer doors. A table that
    enumerated only literal spellings dropped them while still claiming its count
    was the whole argument.
    """
    census = _call_time_census()
    derived = _derived(census._call_time_sites())
    assert derived, (
        "the call-time derivation found no site at all — it resolves the type slot "
        "through loop variables, `for rel in CONSTANT` loops and parameter "
        "defaults, so an empty result means the resolver stopped working and the "
        "class is unguarded again"
    )
    declared = _declared_keys()
    registered = _register_keys(census.CALL_TIME_REL)
    unowned = sorted(set(derived) - declared - registered)
    assert not unowned, (
        "these sites read the edge through a relationship type supplied at CALL "
        "TIME, and §13 neither names them nor declares them out of scope:\n"
        + "\n".join(f"  - {key} — lines {derived[key]}" for key in unowned)
        + "\nAdd a row with the symbol, or a `CALL_TIME_REL` entry in "
        "`tests/_about_call_time_census.py` stating the code fact that keeps "
        f"`aboutObject` off that slot ({len(derived)} derived, "
        f"{len(registered)} registered, {len(_rows())} rows)"
    )


def test_no_call_time_entry_outlives_its_site():
    """The reverse direction: a register entry that no longer derives is stale.

    A one-way check is the fail-open half — an entry kept after the code changed
    would excuse a site that no longer exists AND keep the class silently
    smaller than it is.
    """
    census = _call_time_census()
    derived = set(_derived(census._call_time_sites()))
    stale = sorted(_register_keys(census.CALL_TIME_REL) - derived)
    assert not stale, (
        "`CALL_TIME_REL` still declares these sites, and the derivation no longer "
        f"finds them: {stale} — the entry excuses a read that is not there and "
        "must be deleted, or the resolver has regressed"
    )


def test_the_empty_pattern_class_is_derived_and_declared():
    """A traversal that names NO type must be a row or a stated exclusion.

    ⚠️ **Both legal spellings are derived** — the bracketed `-[r]->` and the
    bracket-less `(n)--()`. The bracket-less one was outside the walk once, and a
    live member sat undeclared while the document claimed the class was complete
    (`domain_validators.validate_chain_integrity`'s `AND NOT (n)--()`, the
    `orphaned_draft` degree check).
    """
    census = _call_time_census()
    derived = _derived(census._empty_pattern_sites())
    assert derived, "the empty-pattern derivation found no site at all"
    declared = _declared_keys()
    registered = _register_keys(census.EMPTY_PATTERN)
    unowned = sorted(set(derived) - declared - registered)
    assert not unowned, (
        "these sites walk EVERY relationship type (no type is written, in either "
        "spelling) and §13 neither names them nor declares them out of scope:\n"
        + "\n".join(f"  - {key} — lines {derived[key]}" for key in unowned)
        + "\nAdd a row, or an `EMPTY_PATTERN` entry stating the code fact that "
        f"bounds the walk ({len(derived)} derived, {len(registered)} registered)"
    )


def test_no_empty_pattern_entry_outlives_its_site():
    """The reverse direction for the empty-pattern register."""
    census = _call_time_census()
    derived = set(_derived(census._empty_pattern_sites()))
    stale = sorted(_register_keys(census.EMPTY_PATTERN) - derived)
    assert not stale, (
        "`EMPTY_PATTERN` still declares these sites, and the derivation no longer "
        f"finds them: {stale} — delete the entry, or the resolver has regressed"
    )




def test_no_empty_pattern_entry_is_also_a_row():
    """An exclusion that ALSO has a §13 row is dead — one of the two hides nothing.

    The row says "this reader is enumerated"; the register entry says "this
    walk cannot observe the edge". A site carrying both makes the opposite
    claims at once, and whichever is stale silently shields the other's drift:
    the `#9400` remerge left the class with exactly this shape of ambiguity
    across TWO registers, and `#9493` folded them into one — this arm is what
    keeps the folded register from regrowing a dead entry.
    """
    census = _call_time_census()
    registered = _register_keys(census.EMPTY_PATTERN)
    assert registered, (
        "the `EMPTY_PATTERN` register is empty — this direction now measures "
        "nothing, and every derived site of the class would have to own a row "
        "instead."
    )
    both = sorted(registered & _declared_keys())
    assert not both, (
        "these sites are BOTH a §13 row and an `EMPTY_PATTERN` entry — one of "
        f"the two is dead: {both}"
    )


def test_every_empty_pattern_entry_states_a_true_fact():
    """Each entry's REASON is checked against EVERY site it claims to describe.

    ⚠️ **This arm restores the half the `#9493` fold deleted, and its absence was
    a fail-open in the direction this class exists to close.** An entry is
    `(file, symbol, why, facts, sites)`; the other three arms read only
    `(file, symbol)`, so the prose — the register's ONLY record of *why* a walk is
    out — was never checked against anything. Measured on the pre-arm tree: retyping
    `session_source_migration._OUT_TYPES` from `(old:Source {url:$u})` to
    `(old)`, or dropping `:Subject` from `sdk.provenance`, makes the walk start
    observing an `aboutObject` edge while EVERY arm stays green — the entry goes
    on excusing it, and §13's two legal grounds no longer hold for that site.

    ⚠️ **The check is per SITE, and the first cut of this arm was per SYMBOL — which
    is a voucher, not a register.** The register key is `(file, symbol)` while the
    derivation is per-site, so a symbol-keyed fact check owns anything added
    inside that symbol later. Measured on that cut: adding one fully unconstrained
    all-edge walk to `sdk.provenance` (`MATCH (x)-[rr]->(y) RETURN count(rr)`) left
    all 43 arms green, while `origin/main`'s per-walk guard reded on it — a new
    un-excluded reader of `aboutObject` shipped with the register vouching for it,
    falsifying `_about_call_time_census.py`'s *"a NEW member of the class cannot
    appear silently"*. So each derived `(rel, symbol, line)` of a registered key
    must carry a stated fact inside ITS OWN statement (`_site_fact_text`), and the
    entry's `sites` count must equal the derivation's — the second is the cheap
    net that catches a duplicated site even when the copy carries the fact.

    The facts are asserted as plain substrings of that statement plus the
    module-level constants it names, over source with COMMENTS and PROSE STRINGS
    blanked: a fact satisfied by a comment is not a fact about executed code.
    """
    census = _call_time_census()
    assert census.EMPTY_PATTERN, (
        "the `EMPTY_PATTERN` register is empty — this arm would then assert "
        "nothing, which is how an unread prose reason ships green"
    )
    derived = census._empty_pattern_sites()
    checked = 0
    sites_checked = 0
    failures: list[str] = []
    for rel, symbol, _why, facts, sites in census.EMPTY_PATTERN:
        key = _site_key(rel, symbol)
        assert facts, (
            f"`{key}` carries no code fact — its reason is prose nothing can "
            "falsify, so weakening the walk would leave it excusing a site that "
            "now reads the edge"
        )
        lines = derived.get((rel, symbol), ())
        if len(lines) != sites:
            failures.append(
                f"  - {key} declares {sites} site(s) and the derivation finds "
                f"{len(lines)} at lines {list(lines)} — a site was added beside "
                "the registered ones, and the entry cannot vouch for a walk it "
                "was not written about"
            )
            continue
        for line in lines:
            sites_checked += 1
            text = _site_fact_text(rel, line)
            for fact in facts:
                checked += 1
                if fact in text:
                    break
            else:
                failures.append(
                    f"  - {key}:{line} states none of `{'` / `'.join(facts)}` — "
                    "its symbol is excluded, but THIS walk carries none of the "
                    "facts that exclusion rests on"
                )
    assert not failures, (
        "these `EMPTY_PATTERN` entries excuse a walk on a fact their code no "
        "longer carries, so the walk may now be observing an `aboutObject` edge "
        "while the register keeps it out of §13:\n"
        + "\n".join(failures)
        + "\nRe-derive the reason from the site: either restore the bound (a "
        "`type(r) IN [...]` restriction, an endpoint label outside every "
        "`aboutObject` triple, or a degree/existence count that returns no "
        "edge), or move the site to a §13 row."
    )
    assert sites_checked == sum(entry[4] for entry in census.EMPTY_PATTERN), (
        f"the register declares {sum(entry[4] for entry in census.EMPTY_PATTERN)} "
        f"sites and {sites_checked} were checked — the arm stopped at the first "
        "site of a symbol again, which is the per-symbol shape that vouched for "
        "a walk nobody described"
    )
    assert checked >= sites_checked, (
        f"{sites_checked} sites produced only {checked} fact assertions — every "
        "site must be checked against at least one fact, or the register's "
        "reasons are unverified again"
    )

def test_a_stated_fact_cannot_be_satisfied_by_prose(tmp_path):
    """A comment or a docstring is not code, so a fact there is not a fact.

    ⚠️ **Measured on the first cut, which read RAW source.** Weakening
    `sdk.provenance`'s query to `MATCH (s)-[r]->(n)` and leaving
    `MATCH (s:Subject {id:$sid})-[r]->(n)` in a comment above it kept the arm
    GREEN: the walk observes `aboutObject`, the ground is false, and a plain
    substring test cannot tell prose from code. No live fact depended on it (all
    sixteen resolve to real code), so it was defeatable-by-future-edit rather
    than false — which is exactly the shape a fixture has to close, because the
    edit that exploits it is the same edit that weakens a walk.

    A synthetic file, because the property is about the READER and not about any
    one entry: the same query in the body must survive, and the same query as a
    docstring or a comment must not.
    """
    path = tmp_path / "prose.py"
    path.write_text(
        "def _walk(query):\n"
        '    """The docstring copy: MATCH (s:Subject {id:$sid})-[r]->(n)."""\n'
        "    # the comment copy: MATCH (s:Subject {id:$sid})-[r]->(n)\n"
        '    unused = 1  # a trailing copy: MATCH (s:Subject {id:$sid})-[r]->(n)\n'
        '    return query("MATCH (s)-[r]->(n)")\n',
        encoding="utf-8",
    )
    text = "\n".join(_code_lines(path))
    assert 'return query("MATCH (s)-[r]->(n)")' in text, (
        "the stripper removed EXECUTED code — every fact assertion would then be "
        "measured against a file it did not read, which is a green with no subject"
    )
    assert "unused = 1" in text, (
        "a code line sharing a line with a trailing comment went with the "
        "comment — the cut must be column-exact, not whole-line"
    )
    assert "Subject" not in text, (
        "the fact survives in a COMMENT or a DOCSTRING, so a weakened walk can be "
        "excused by prose that never runs — the exact mutation that kept this "
        "check green before the stripper existed"
    )


def test_a_stated_fact_is_bound_to_one_site_not_to_its_symbol():
    """A registered symbol may own TWO walks, and each is bound to its OWN statement.

    ⚠️ **This is the shape P1 reported, asserted where it lives.** The register key
    is `(file, symbol)`; the derivation is per-SITE. A fact check over the whole
    symbol therefore lets ONE walk's ground vouch for its neighbour — and by
    extension for anything appended later, which is how an unconstrained all-edge
    walk added to `sdk.provenance` shipped green.

    `mining.verify_no_auto_wire` is the live two-site fixture, and no line number
    is written down: its walks are taken from the derivation, and the two site
    texts must each carry a fact and must NOT carry the same one — a symbol-wide
    span collapses them into the same set and reds here.
    """
    census = _call_time_census()
    entry = next(e for e in census.EMPTY_PATTERN if e[1] == "verify_no_auto_wire")
    rel, symbol, _why, facts, sites = entry
    lines = census._empty_pattern_sites()[(rel, symbol)]
    assert len(lines) == sites == 2, (
        f"`{_site_key(rel, symbol)}` derives {len(lines)} site(s) against {sites} "
        "declared — the two-site fixture this arm rests on has moved, so the "
        "assertion below would compare nothing"
    )
    carried = [
        [fact for fact in facts if fact in _site_fact_text(rel, line)]
        for line in lines
    ]
    assert all(carried), (
        "a site of `verify_no_auto_wire` carries NONE of the entry's facts: "
        f"{carried} — the walk is no longer bounded by the ground the register "
        "records for it"
    )
    assert carried[0] != carried[1], (
        f"both sites resolve to the same fact set ({carried[0]}) — the fact text "
        "spans the whole SYMBOL again, so one walk's ground vouches for the "
        "other and for any walk added beside them"
    )


def test_a_part_assembled_walk_is_derived():
    """The assembled spelling is DERIVED, not merely admitted by the gate.

    ⚠️ **Two properties, one fixture, because they are two halves of one bypass.**
    `_WALK = "-[r]->"` plus `"MATCH (n)" + _WALK + "(m)"` is a walk over every
    relationship type whose dash, bracket and node patterns sit in three
    different literals. §13 claims such a walk *"enters the census through the
    consumer the resolver follows"*; that is true ONLY because `_resolve()` folds
    `ast.BinOp/Add` (`tests/_about_call_time_census.py`), and until this arm
    existed nothing asserted it — a change to `_resolve` would have reopened the
    bypass silently while the document kept claiming the property. The GATE half
    is `_may_traverse_every_type`, which must admit the module before any of this
    runs (`test_the_module_filter_admits_a_split_traversal`).
    """
    census = _call_time_census()
    source = (
        '_WALK = "-[r]->"\n'
        "\n"
        "\n"
        "def _assembled(query):\n"
        '    return query("MATCH (n)" + _WALK + "(m)")\n'
        "\n"
        "\n"
        "def _typed(query):\n"
        '    return query("MATCH (n)-[r:aboutObject]->(m)")\n'
    )
    file = census._File(Path("synthetic_split.py"), "synthetic_split.py", source)
    derived = {symbol for symbol, _line in census._empty_pattern_sites_in(file)}
    assert "_assembled" in derived, (
        "a walk assembled from a fragment constant (`\"MATCH (n)\" + _WALK + "
        "\"(m)\"`) is not derived — `_resolve()` no longer folds `ast.BinOp/Add`, "
        "so every walk built from parts ships green, and §13's claim that it "
        "enters the census through the resolver is false"
    )
    assert "_typed" not in derived, (
        "a typed walk derived as an empty-pattern site — the fold must not "
        "swallow the half that belongs to the named class"
    )


def test_the_module_filter_admits_a_split_traversal():
    """The file gate must admit every walk the RESOLVER can reassemble.

    ⚠️ Measured on the pre-fix filter: both assembled spellings returned `False`
    while the one-literal spelling returned `True`, so a module holding only the
    assembled form was dropped before the derivation — which can see it — ran.
    `test_the_file_filter_un_sees_nothing` cannot catch this: the corpus holds no
    such file today, so the difference it compares stays empty either way. The
    assertion is therefore synthetic, on the shapes a `+` concatenation
    produces.

    ⚠️ **The first cut of this arm measured the two spellings the CHANGELOG names
    and still missed the third:** with the bracket in one constant and the dash in
    another (`_A = "-[r]"`, `_B = "->"`, `"MATCH (n)" + _A + _B + "(m)"`) the
    resolver reassembles the walk and derives it, while a regex demanding the `-`
    right after the `]` refused the module — the gate narrower than the thing it
    gates, which is the one direction a filter cannot fail in. So the filter no
    longer requires dash adjacency, and the derivation count below pins that the
    widening changed nothing it derives.
    """
    census = _call_time_census()
    cases = {
        'q("MATCH (n)-" + "[r]->(m)")': "the bracket is its own literal",
        'q("MATCH (n)-[r]" + "->(m)")': "the dash is its own literal",
        '_A = "-[r]"\n_B = "->"\nq("MATCH (n)" + _A + _B + "(m)")': (
            "the bracket and the dash are in DIFFERENT constants"
        ),
    }
    for source, shape in cases.items():
        assert census._may_traverse_every_type(source), (
            f"the module filter refuses the assembled spelling ({shape}): "
            f"{source!r} — the file is never walked, so the walk inside it is "
            "invisible however good the resolver is"
        )
    assert not census._may_traverse_every_type("no cypher here at all"), (
        "the filter admits a module with no traversal in it — as a filter that "
        "is free, but it also means the assertions above measure nothing"
    )
    # The widening is only safe if it is DERIVATION-NEUTRAL: the unanchored form
    # admits more modules (Python subscripts match too), and the count below is
    # what proves none of them contributed a site — a module admitted and then
    # deriving nothing costs a parse, not a false claim.
    sites = census._empty_pattern_sites()
    assert (len(sites), sum(len(lines) for lines in sites.values())) == (24, 36), (
        "the empty-pattern derivation moved off 24 symbol keys / 36 site rows "
        f"({len(sites)} keys, "
        f"{sum(len(lines) for lines in sites.values())} rows). The register's "
        "`sites` counts and BOTH completeness directions are stated against "
        "those numbers, so a move is a corpus change to re-derive — never a "
        "number to retype"
    )


def test_the_empty_pattern_type_slot_rules():
    """Variable-length and property-map brackets name NO type — `MERGE` is a write.

    Three rules this class rests on, each correct and each unpinned once the
    retired guard's `_EMPTY_PATTERN_CASES` table went with the fold:

    * `[r*1..2]` — a variable-length traversal has no type slot, so it walks every
      type (the `*` is a length, not a type);
    * `[r {idx:0}]` — a property map is not a type, and the colon inside `{}` is a
      map key;
    * `MERGE (n)-[r]-(m)` / `CREATE` — the pattern MINTS the edge, so it is a
      writer, not a reader of it. Deleting the clause test would inflate the
      class with every write site and force an entry for each.
    """
    census = _call_time_census()
    source = (
        "def _ranged(query):\n"
        '    return query("MATCH (n)-[r*1..2]->(m)")\n'
        "\n"
        "\n"
        "def _mapped(query):\n"
        '    return query("MATCH (n)-[r {idx:0}]->(m)")\n'
        "\n"
        "\n"
        "def _minted(query):\n"
        '    return query("MERGE (n)-[r]-(m)")\n'
        "\n"
        "\n"
        "def _created(query):\n"
        '    return query("CREATE (n)-[r]-(m)")\n'
    )
    file = census._File(Path("synthetic_slots.py"), "synthetic_slots.py", source)
    derived = {symbol for symbol, _line in census._empty_pattern_sites_in(file)}
    for symbol in ("_ranged", "_mapped"):
        assert symbol in derived, (
            f"`{symbol}` names no relationship TYPE (a length, or a property "
            "map, is not a type) so it walks every type — a bracket this walk "
            "cannot see is a reader that ships green"
        )
    for symbol in ("_minted", "_created"):
        assert symbol not in derived, (
            f"`{symbol}` derives as an empty-pattern READ — `MERGE`/`CREATE` "
            "mint the edge, so the pattern is a writer, and admitting writers "
            "inflates the class with sites that never read a referent"
        )


def test_the_two_halves_of_the_register_are_measured():
    """One symbol can own TWO sites — the derivation is per-site, the key per-symbol.

    ⚠️ A census that collapsed its sites to a set of SYMBOLS would lose the
    difference: `mining.verify_no_auto_wire` walks the edge twice (1409 and
    1435 at this head), `session_source_migration._rel_types` twice (its two
    query constants). Every register key is therefore a symbol that may hide
    more than one read, and both counts are asserted so that neither side can
    quietly become the other: the KEYS are what the register matches, the ROWS
    are what the class is.
    """
    census = _call_time_census()
    sites = census._empty_pattern_sites()
    keys = len(sites)
    rows = sum(len(lines) for lines in sites.values())
    assert keys > 2, f"the empty-pattern derivation found {keys} symbols — nothing to measure"
    assert rows > keys, (
        f"{keys} symbols own {rows} sites — the multiplicity is gone, so either "
        "the derivation now stops at the first site of each symbol (and a second "
        "walk beside a registered one ships green) or the class shrank"
    )
    assert rows - keys >= 2, (
        f"{keys} symbols over {rows} sites leaves only {rows - keys} extra "
        "site(s); at least TWO symbols are known to own two sites each, so a "
        "collapse to one is a derivation regression"
    )


def test_the_site_aliases_are_grounded():
    """BOTH halves of every `_SITE_ALIASES` row must resolve (#9493).

    ⚠️ **This table turns a deleted entry into a row-owned site, so a mis-pairing
    is a hole with a footnote.** Each row key is a constant that owns a §13 row
    (`hosted_backup._DUMP_EDGE_PAGE_QUERY`); each target is the consumer the
    derivation actually attributes the site to (`dump_graph`). The three pairs
    this change adds are correct — but a row key that names no row, or a target
    that names no derived site, would let an unrowed site be excused by an
    UNRELATED row, which is the stale-shield shape `test_no_empty_pattern_entry_
    is_also_a_row` closes on the register side. The grounding arm above covers
    the other table (`_DERIVED_ALIASES`); this one is its twin and was missing.
    """
    census = _call_time_census()
    assert _SITE_ALIASES, "`_SITE_ALIASES` is empty — this arm would assert nothing"
    declared = {
        _site_key("x/" + str(row["module"]), str(row["symbol"])) for row in _rows()
    }
    derived = set(_derived(census._empty_pattern_sites()))
    for row_key, target in sorted(_SITE_ALIASES.items()):
        assert row_key in declared, (
            f"`_SITE_ALIASES` maps `{row_key}` to `{target}`, and §13 has no row "
            "under that key — the alias stands for a row that does not exist, so "
            "it excuses nothing and hides that fact"
        )
        assert target in derived, (
            f"`_SITE_ALIASES` maps `{row_key}` to `{target}`, and the empty-pattern "
            "derivation attributes no site to that name — the alias points at a "
            "symbol that reads nothing, so an UNROWED site elsewhere can be "
            "matched by the row key while the alias supplies the missing key"
        )


def test_a_comprehension_only_walk_derives():
    """The comprehension half of the class, asserted on a synthetic source (#9493).

    The retired guard's untyped census was the only derivation that treated a
    pattern comprehension as a read whatever clause introduces it; the rule
    moved into `_is_empty_pattern_read` with the class, and this fixture is
    what keeps it falsifiable — `SET n.deg = size([(n)-[r]->(m) | r])` carries
    no `MATCH` at all, so the governing-clause rule alone would discard the
    walk over every relationship type as a write.
    """
    census = _call_time_census()
    source = (
        'def _probe_deg(query):\n'
        '    return query("SET n.deg = size([(n)-[r]->(m) | r])")\n'
        '\n'
        'def _probe_typed(query):\n'
        '    return query("SET n.deg = size([(n)-[r:aboutObject]->(m) | r])")\n'
        '\n'
        'def _probe_bare(query):\n'
        '    return query("RETURN size([(n)-[r]-(m) | r]) AS deg")\n'
    )
    file = census._File(Path("synthetic.py"), "synthetic.py", source)
    derived = census._empty_pattern_sites_in(file)
    assert ("_probe_deg", 2) in derived, (
        "a pattern comprehension introduced by `SET` is not derived — a walk "
        "over every relationship type was discarded as a write, the fail-open "
        "the comprehension rule exists to close"
    )
    assert ("_probe_bare", 8) in derived, (
        "a comprehension with no clause before it at all (`RETURN size([...])`) "
        "is not derived — the rule must survive carrying no clause, not only "
        "carrying a non-reading one"
    )
    assert not any(symbol == "_probe_typed" for symbol, _line in derived), (
        "a comprehension whose bracket carries a type (`-[r:aboutObject]->`) "
        "derived as an empty-pattern site — the typed half belongs to the "
        "named/call-time classes, and the rule must not swallow it"
    )

def test_the_bracket_less_spelling_is_inside_the_walk():
    """The bracket-less traversal is the half that was invisible; pin the detector.

    ⚠️ A regex that only finds `[…]` reproduces the original defect exactly, and
    it looks correct in review — so the shape is asserted on a synthetic string
    rather than inferred from the corpus, which happens to contain one member.
    """
    census = _call_time_census()
    assert census._BARE_TRAVERSAL.search("MATCH (n) WHERE NOT (n)--() RETURN n"), (
        "the bracket-less traversal `(n)--()` is not recognised — every reader "
        "written in that spelling is invisible again"
    )
    assert not census._BARE_TRAVERSAL.search("MATCH (n)-[r]->(m)"), (
        "the bracket-less detector also matches the bracketed spelling, so it is "
        "not measuring the shape it claims"
    )

def test_the_derived_aliases_are_grounded():
    """Every alias names two symbols that EXIST, so it cannot excuse nothing.

    An alias table is a hole in a census if its entries are unchecked — the exact
    shape `_NON_LITERAL`'s deleted carrier fallback had. Both halves are resolved
    from the module's AST: the derived symbol, and the row symbol it stands for.
    """
    for derived_key, declared_key in sorted(_DERIVED_ALIASES.items()):
        for key in (derived_key, declared_key):
            module, _, symbol = key.partition("::")
            tree = _module_tree(_module_path(module))
            assert (
                _span(tree, symbol) is not None
                or symbol in _module_path(module).read_text(encoding="utf-8")
            ), f"`{key}` does not exist at HEAD — the alias is stale"

def test_the_file_filter_un_sees_nothing():
    r"""The file gate must admit every module a walk derives a site in.

    ⚠️ **A narrowed gate is the quietest failure this instrument can have:** the walks
    still work, every arm still passes, and the reader they no longer reach simply
    stops being covered. Measured before this arm walked UNGATED: deleting
    `navigation.py` from `_files()` left the entire guard green while §13's row for
    `_hop_query` stopped being derived by anything.

    The comparison is therefore against a walk that IGNORES the gate — the gate cannot
    be interrogated from its own output, because a module it skips is by definition
    absent from it. The CONTROL runs the same comparison through the NARROW gate
    (`_literal_files()`, the one predicate the gate started from): it must miss sites,
    or the measurement cannot see a narrowing and its silence proves nothing.
    """
    census = _call_time_census()
    labels = ("named census", "empty-pattern class", "call-time class")

    ungated = census._ungated_walk_sites()
    gated = (census._census(), census._empty_pattern_sites(), census._call_time_sites())
    gated_keys = [set(keys) for keys in gated]

    assert len(census._sources()) > len(census._files()), (
        "the gate admits every module, so there is no excluded module to measure and "
        "this arm passes vacuously — either the gate was widened to `True` or the "
        "package shrank to the gated set"
    )

    problems = []
    for label, every, kept in zip(labels, ungated, gated_keys, strict=True):
        missed = every - kept
        if missed:
            shown = sorted({f"{Path(rel).name}::{sym}" for rel, sym in missed})[:5]
            problems.append(
                f"{label}: {len(missed)} site(s) are derived by the UNGATED walk but "
                f"not by the gated one, so the gate skips a module that reads the "
                f"edge: {shown}"
            )
    assert not problems, (
        "the module gate is narrower than the walks it feeds, so readers outside it "
        "are silently uncovered:\n  " + "\n  ".join(problems)
    )

    # CONTROL — the same comparison through the NARROW gate must DISAGREE.
    narrow = census._narrow_gate_walk_sites()
    assert any(every - kept for every, kept in zip(ungated, narrow, strict=True)), (
        "the narrow gate hides no site from any walk, so this measurement cannot "
        "detect a narrowing — the control has gone blind and the arm above is a "
        "PASS about nothing"
    )


def test_the_ungated_walk_reuses_the_gated_derivations():
    """The ungated walk must WALK the gate, not re-implement the derivations.

    ⚠️ **A second, hand-maintained copy of a derivation is a blindness channel in the
    instrument that exists to un-blind.** `_ungated_walk_sites` originally inlined the
    exact bodies of the empty-pattern and call-time walks while `_narrow_gate_walk_sites`
    called the per-module helpers, so the two halves of the comparison the arm above
    turns on were maintained independently: narrow the inline copy and a genuine gate
    widening UNDER-detects (green); change the gated copy alone and the arm reds
    spuriously — after which its red no longer means "the gate is narrow". The only
    legitimate difference between the walks is WHICH modules are visited.

    Falsifiable by construction: the per-module helpers are instrumented and the walk
    is run over a truncated module set, so an inline copy records NOTHING and reds.
    """
    census = _call_time_census()
    sources = census._universe()
    assert len(sources) > 2, "the package has too few modules to run this arm"
    subset = dict(sorted(sources.items())[:2])

    recorded: dict[str, set[str]] = {
        "_sites_in": set(),
        "_empty_pattern_sites_in": set(),
        "_call_time_sites_in": set(),
    }
    originals = {name: getattr(census, name) for name in (*recorded, "_universe")}

    def _recording(name: str, real):
        def wrapper(file):
            recorded[name].add(file.rel)
            return real(file)

        return wrapper

    patched: dict[str, object] = {
        name: _recording(name, getattr(census, name)) for name in recorded
    }
    patched["_universe"] = lambda: subset
    try:
        for name, value in patched.items():
            setattr(census, name, value)
        ungated = census._ungated_walk_sites()
    finally:
        for name, value in originals.items():
            setattr(census, name, value)

    # The walk that ran is the SUBSET (no cache to invalidate — it is not cached).
    expected = {str(path.relative_to(census.ROOT)) for path in subset}
    for name, seen in sorted(recorded.items()):
        assert seen == expected, (
            f"`_ungated_walk_sites` did not run `{name}` over every module — it reached "
            f"{sorted(seen)} of {sorted(expected)}, so the ungated side of the gate "
            "comparison is a second, independently maintained copy of the derivation "
            "and the comparison can drift instead of measuring the gate"
        )

    # …and the values it reports are exactly what those helpers return per module.
    for label, helper, walked in (
        ("named", originals["_sites_in"], ungated[0]),
        ("empty-pattern", originals["_empty_pattern_sites_in"], ungated[1]),
        ("call-time", originals["_call_time_sites_in"], ungated[2]),
    ):
        rebuilt = set()
        for path, source in subset.items():
            file = census._File(path, str(path.relative_to(census.ROOT)), source)
            rebuilt |= {(file.rel, symbol) for symbol, _line in helper(file)}
        assert walked == rebuilt, (
            f"the ungated walk's {label} class disagrees with the per-module derivation "
            "it is supposed to be a loop over — the walk carries logic the gated side "
            "does not"
        )


def test_a_module_constant_is_attributed_to_its_consumer():
    """Both halves of `_site_symbol`'s module-constant rule, on synthetic sources.

    The tree contains neither shape, so an arm that only walked the corpus could not
    tell the branches apart — which is how `ast.AnnAssign` came to be unhandled while
    the `<module>` fallback was presented as unreachable. Two input classes, one per
    branch:

    * an ANNOTATED module constant (`NAME: str = "..."`) must be attributed to the
      function that consumes it — the `ast.AnnAssign` branch;
    * a root that sits in NO module-level constant statement (an `if` block, a class
      body) has no constant to attribute and falls back to `<module>` — the branch the
      first case does NOT reach.

    Any pre-`AnnAssign` version of `_site_symbol` returns `<module>` for the first
    source, so this arm fails on it.
    """
    census = _call_time_census()
    annotated = (
        'PATTERN: str = "MATCH (n)-[:aboutObject]->(m) RETURN m"\n'
        "\n"
        "\n"
        "def reader(query):\n"
        "    return query(PATTERN)\n"
    )
    file = census._File(Path("synthetic_annotated.py"), "synthetic_annotated.py", annotated)
    derived = {symbol for symbol, _line in census._sites_in(file)}
    assert derived == {"reader"}, (
        "an ANNOTATED module constant is not attributed to the function that consumes "
        f"it — derived {sorted(derived)}. `<module>` here means the `ast.AnnAssign` "
        "branch is gone and §13 loses the reader behind a constant written with a "
        "type annotation"
    )

    nested = (
        "if True:\n"
        '    PATTERN = "MATCH (n)-[:aboutObject]->(m) RETURN m"\n'
        "\n"
        "\n"
        "def reader(query):\n"
        "    return query(PATTERN)\n"
    )
    file = census._File(Path("synthetic_if.py"), "synthetic_if.py", nested)
    derived = {symbol for symbol, _line in census._sites_in(file)}
    assert derived == {"<module>"}, (
        "a root in no module-level constant statement must fall back to `<module>`, "
        f"derived {sorted(derived)} — either the fallback is unreachable again (the "
        "line the review flagged) or the rule now attributes an unattributable read"
    )


def _gate_walk_comment() -> str:
    """The `#` lines of `_narrow_gate_walk_sites` — the comment `#9506` is about."""
    census = _call_time_census()
    source = inspect.getsource(census._narrow_gate_walk_sites)
    return "\n".join(
        line.strip() for line in source.splitlines() if line.lstrip().startswith("#")
    )


#: A duration, as a comment pins one (`10.36 s`, `~2.6 s`, `850 ms`).
_DURATION = re.compile(r"\d+(?:\.\d+)?\s?(?:ms|s)\b")

#: What makes a pinned duration SETTLEABLE — the framing that produced it.
#: Each entry names the measurement a re-verifier must reproduce to check the
#: number; "one box" deliberately is NOT here, because hardware is the condition
#: a number is taken under, not the measurement it was taken OF.
_DURATION_FRAMING = (
    "--durations",
    "whole arm",
    "control step",
    "in-suite",
)


def _unframed_durations(comment: str) -> list[str]:
    """Durations `comment` pins WITHOUT the framing that produced them (`#9506`).

    ⚠️ **This predicate is the fix to a FAIL-OPEN guard, not a rewording of one.**
    The first version of the arm red on the co-occurrence of a self-denial phrase
    (`no magnitude` / `no wall-clock`) AND a duration. That made it **green on the
    merge base** — `git show origin/main:tests/_about_call_time_census.py` carried
    *"re-reading `_sources()` and rebuilding the same 29 `_File`s measured 10.36 s
    of the control's 10.85 s for byte-identical output"*, which says no denial at
    all, so there was nothing for the guard to fire on: the arm passed on precisely
    the text this PR deletes, and would pass again if that figure were re-introduced.
    It was also fail-open on REWORDING — turning "No magnitude is pinned" into "No
    timing figure is named" silently retired it.

    So the rule now reads the CLAIM rather than a phrase about it: a duration is a
    fact asserted about a measurement, and it is acceptable only when the comment
    also states the framing that produced it. This reds on the merge-base text and
    on the intermediate `~2.6 s of ~17 s` sentence alike, and no rewording can
    retire it because it never reads the denial.

    Coarse in one documented direction: the framing marker is matched over the whole
    comment, not per number, so a comment that carries its framing may also mention
    a second figure. That is the harmless direction — it can only stay GREEN where
    the framing IS stated, which is the state the reviewer asked to keep allowed.
    """
    found = _DURATION.findall(comment)
    if any(marker in comment for marker in _DURATION_FRAMING):
        return []
    return found


def test_the_gate_walk_comment_pins_no_wall_clock_figure():
    """A pinned duration must carry the framing that produced it (`#9506`).

    ⚠️ **The `#9506` defect was a claim a re-verifier cannot settle.** The
    refactor's comment read *"No magnitude is stated on purpose: the redundant
    rebuild is a fraction of this arm (a re-verifier measured ~2.6 s of ~17 s on one
    box)"* — an assertion that no magnitude is stated, immediately followed by one,
    on the exact axis the change exists to fix — and its merge-base predecessor read
    *"measured 10.36 s of the control's 10.85 s"*, a ratio that reproduces under no
    framing the reviewer constructed (the redundant rebuild is ~2.6 s of a ~17 s
    arm; the arm's dominant cost is the 242-module ungated walk, not the control).
    Either way the framing (whole arm? control step? which box?) was absent, so a
    re-verifier running the control step alone measures something else and cannot
    tell which reading was meant.

    Both directions, so the arm is not a style rule: a duration WITH its framing is
    allowed (the reviewer's alternative fix), but a duration without it is red. The
    predicate is `_unframed_durations`; its non-vacuity — that it reds on the text
    this PR replaced — is its own arm below, because a guard that is green on the
    pre-fix code guards nothing (the defect the round-5 review found here).
    """
    comment = _gate_walk_comment()
    assert comment, (
        "`_narrow_gate_walk_sites` carries no `#` comment any more, so this arm "
        "asserts nothing — the mechanism it states was the whole point of the change"
    )
    assert not _unframed_durations(comment), (
        "the gate-walk comment pins a duration without the framing that produced it "
        f"({_unframed_durations(comment)}) — a claim a re-verifier cannot settle. "
        "Drop the figure, or state it with the framing that produced it (whole arm / "
        "control step / `--durations=0` / in-suite). Note that \"on one box\" is not "
        "framing: it is the condition, not the measurement."
    )


def test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced():
    """Non-vacuity for `_unframed_durations`, on the MERGE BASE — not an
    intermediate commit of this branch (`#9506`).

    ⚠️ **The round-5 review found the first guard green on `origin/main`.** It red
    only on `says_none AND duration`, and the base text says no denial, so the arm
    passed on the very text this PR exists to delete. This arm feeds the same
    three states through the same predicate the guard uses — the base string, the
    intermediate string, and a framed duration — so "the guard reds on what it
    replaced" is executed rather than asserted in a docstring, the shape this file
    already uses elsewhere ("mutation applied, all arms re-run").
    """
    merge_base = (
        "# `_literal_files()` is already parsed and cached; re-reading `_sources()` "
        "and\n# rebuilding the same 29 `_File`s measured 10.36 s of the control's "
        "10.85 s for\n# byte-identical output."
    )
    intermediate = (
        "# ... only object identity changes. No magnitude is stated on purpose: the\n"
        "# redundant rebuild is a fraction of this arm (a re-verifier measured "
        "~2.6 s of\n# ~17 s on one box), and a wall-clock number pinned into a "
        "comment is the\n# claim that rots."
    )
    assert _unframed_durations(merge_base) == ["10.36 s", "10.85 s"], (
        "the predicate must red on the MERGE BASE text (`measured 10.36 s of the "
        "control's 10.85 s`) — that is the figure this PR removes, so an arm that "
        "passes on it is vacuous: it would let the identical figure be re-introduced"
    )
    assert _unframed_durations(intermediate), (
        "the predicate must red on the intermediate `~2.6 s of ~17 s` sentence, "
        "which is the state the earlier round called \"pre-fix\" — it is a commit on "
        "this branch, never on main"
    )
    assert not _unframed_durations(
        "# the arm measured 14.4 s whole arm, --durations=0, one box"
    ), (
        "a duration that carries its framing must stay allowed (the reviewer's "
        "alternative fix) — otherwise this is a style rule that would red on a "
        "settleable figure"
    )
    assert not _unframed_durations(_gate_walk_comment()), (
        "the live comment must be green under the same predicate the two arms above "
        "use — this is the arm the guard asserts, restated so a divergence between "
        "the predicate and its non-vacuity arm cannot go unnoticed"
    )


def _pin_universe_to_disk(census, on_disk: set[Path]) -> None:
    """The completeness pin's BODY — factored out so its FLOOR is pinnable too.

    ⚠️ The floor is not decoration. Both set differences below are vacuously EMPTY
    when `on_disk` is empty, so an enumeration that resolves to nothing (the package
    moved or renamed, `TORTISE` mis-resolved, a packaging change) would let this arm
    report green having asserted nothing — the exact defect
    `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md` records for the carrier arm
    ("it could report green having asserted nothing … It now counts the pairs it
    examined"). For the one arm whose purpose is "the universe is complete", silence
    on an empty universe is the failure it exists to prevent, so it reds here with its
    own diagnosis. The sibling arms in this file carry the same shape
    (`assert len(sources) > 2`, `assert len(census._sources()) > len(census._files())`).
    """
    assert len(on_disk) > 2, (
        f"the package enumeration found {len(on_disk)} module(s) under "
        f"{census.TORTISE} — this arm cannot assert completeness against an empty "
        "disk set, so it reds here instead of passing vacuously"
    )

    for name in ("_universe", "_sources"):
        observed = set(getattr(census, name)())
        hidden = sorted(
            str(path.relative_to(census.TORTISE)) for path in on_disk - observed
        )
        assert not hidden, (
            f"`{name}()` omits {len(hidden)} module(s) that exist on disk, so every "
            f"derivation silently stops covering them: {hidden[:5]}"
        )
        assert not (observed - on_disk), (
            f"`{name}()` names paths that do not exist — the universe was built from "
            "something other than this package"
        )


def test_the_census_universe_is_every_module_on_disk():
    """The UNIVERSE must be every module under `tortoise/`, not a filtered subset.

    ⚠️ This is the arm the `#9501` review earned, and it is about the gate one function
    UPSTREAM of `_files()`. The file gate is interrogated from an ungated walk, but a
    universe that silently drops a module removes its reader from every derivation
    while every other arm stays green — measured: filtering one module out of the old
    single source left all 29 arms green while §13's row 22
    (`navigation.py::_hop_query`) stopped being derived.

    Both halves are pinned against the DISK, not against each other: `_universe()` is
    the disk enumeration, and `_sources()` is the set the derivations consume. If
    either is narrowed, one of the two assertions reds — and the floor in
    `_pin_universe_to_disk` means an EMPTY enumeration reds too, instead of passing
    having asserted nothing. The mutation arms below make BOTH halves executable, one
    per name: the floor arm reaches only the floor, so it cannot stand in for them.
    """
    census = _call_time_census()
    _pin_universe_to_disk(census, {path for path in census.TORTISE.rglob("*.py")})


def test_the_universe_pin_reds_on_an_empty_disk_set():
    """The FLOOR: an enumeration that resolves to NOTHING must red, not pass.

    Without the floor both of the pin's set differences are empty on an empty
    `on_disk`, so the arm reports green having asserted nothing. Constructing that
    state directly is the only way to prove the floor is load-bearing rather than
    decorative: a `TORTISE` that resolves to nothing is exactly what a package move,
    rename or packaging change produces, and it must red with its own diagnosis.
    """
    census = _call_time_census()
    with pytest.raises(AssertionError, match="cannot assert completeness"):
        _pin_universe_to_disk(census, set())


def test_the_universe_pin_reds_when_a_module_is_dropped():
    """Mutation: narrowing `_universe()` must RED — the pin is not vacuous.

    This is the measurement the `#9504`/`#9501` review established by hand and the PR
    body states in prose ("`_universe()` drops one module → RED — the universe pin").
    Made executable here.

    ⚠️ `_call_time_census()` is `@functools.cache`d, so every arm in this file shares
    ONE module object — the patch below is process-wide and MUST be reverted, or an
    arm that runs after this one reads a universe with a module missing (measured: 241
    of 242, and the completeness arm reds on `migrate_db.py`). The `try/finally`
    restore is the file's own convention (the reuse pin above does the same with its
    `originals` snapshot), not belt-and-braces.
    """
    census = _call_time_census()
    on_disk = {path for path in census.TORTISE.rglob("*.py")}
    full = census._universe()
    dropped = sorted(full)[len(full) // 2]
    original = census._universe
    try:
        census._universe = lambda: {k: v for k, v in full.items() if k != dropped}
        with pytest.raises(
            AssertionError, match=re.escape(str(dropped.relative_to(census.TORTISE)))
        ):
            _pin_universe_to_disk(census, on_disk)
    finally:
        census._universe = original


def test_the_universe_pin_reds_when_the_derivation_universe_is_dropped():
    """Mutation: narrowing `_sources()` must RED — the OTHER half of the same pin.

    `_universe()` and `_sources()` are two names for one contract: the disk
    enumeration and the set the DERIVATIONS consume. Narrowing the second is the
    `#9501` defect itself (a filter arriving on the gated side), and it is the arm the
    PR body records by hand ("`_sources()` drops one module → RED — the universe
    pin"). Executable here: `_sources()` is normally `@lru_cache(maxsize=1)`-warm from
    earlier arms, so the patch replaces the module ATTRIBUTE — the only place the pin
    can observe a narrowed derivation universe — and `_universe()` is left intact, so
    the `_sources` half is the one that must speak.

    The patch is reverted in `finally`: `_call_time_census()` is shared process-wide.
    """
    census = _call_time_census()
    on_disk = {path for path in census.TORTISE.rglob("*.py")}
    full = census._sources()
    dropped = sorted(full)[len(full) // 3]
    original = census._sources
    try:
        census._sources = lambda: {k: v for k, v in full.items() if k != dropped}
        with pytest.raises(
            AssertionError,
            match=r"`_sources\(\)` omits 1 module\(s\)",
        ):
            _pin_universe_to_disk(census, on_disk)
    finally:
        census._sources = original
