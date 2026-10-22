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
   `test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced` +
   `test_the_gate_walk_capture_reads_every_position_a_duration_is_written` — the three
   `#9506`/`#9522` arms, and the only ones here about this instrument's OWN prose rather
   than §13's. The third is the `#9522` capture arm: it replays the removed figure
   through each of the three SYNTHETIC positions (docstring, trailing comment, `#` block
   above the `def`), then holds the LIVE function by containment (its docstring's first
   line is in the capture) and by width (strictly more lines than the `#`-line
   projection), plus the `#`-line floor that the capture widening would otherwise have
   retired. The
   gate-walk comment in `_about_call_time_census.py` carried a duration a re-verifier
   cannot settle — `measured 10.36 s of the control's 10.85 s` on the merge base, and
   `~2.6 s of ~17 s` on an intermediate head of the branch — neither with the framing
   that produced it (whole arm? control step? `--durations=0`? which box?), the
   defect class three earlier rounds removed from that file. The rule is stated once
   as `_unframed_durations()`: a duration is a claim, and it is allowed only when the
   comment also carries the framing that produced it. ⚠️ The first version of the
   guard keyed on the self-denial PHRASE instead (`no (magnitude|wall-clock)` AND a
   duration), which was **green on the pre-`#9516` main** — `fa84a473d:990-992`, the
   text the PR exists to replace, names no denial — and would go green again on any
   rewording of the denial, so it retired itself on the next prose edit. The predicate
   now reads the CLAIM, and the only denial it reads is a negation of the framing
   marker ITSELF: a STRONG denial (`not`, `never`, `without`, `n't`, `skipped`, …) AFTER
   the marker is searched to the end of the sentence (a `,` `;` or `—` does not stop it),
   while BEFORE the marker it is read only inside the marker's own clause
   (`_CLAUSE_BREAK` ends its reach and `_DENIAL_WINDOW` caps it) — so
   `# never taken; 10.36 s with --durations=0` is FRAMED while its mirror reds, and that
   before-side cut is what keeps `# no gating change: 14.4 s whole arm, --durations=0`
   framed. A WEAK quantifier (`no`, `none`) is read only to the next clause break, and a
   weak quantifier that PREDICATES a measurement noun (`no framing was taken`) counts as
   strong. ⚠️ That is a copy-level
   heuristic, and its boundaries are stated rather than implied: it does not decide WHICH
   marker a denial governs, so `# the control step was 14.4 s, not the whole arm` reds a
   settleable figure, `i.e.`/`e.g.` read as sentence ends, and a denial more than
   `_DENIAL_WINDOW` characters after its marker is framed — all filed as `#9531` with the
   measurements. The second arm is the non-vacuity: the pre-`#9516`
   string and the intermediate string RED, a duration carrying its framing stays GREEN,
   and a duration with only "one box" for company (hardware is not framing) REDs.
   ⚠️ **Three later corrections, each found by putting the removed string somewhere
   else and watching the arm stay green.** (1) The capture kept only `#`-leading
   lines, so the same figure in the function DOCSTRING, in a TRAILING comment, or in
   the `#` block above the `def` went unnoticed — it now reads the function's whole
   source plus that CONTIGUOUS block. (2) Framing was matched over the whole text, so
   a comment that merely NAMED `--durations` while denying it passed; the window is
   now the number's own line, and a marker whose OWN sentence denies it — before it
   (`not measured with …`) or after it (`… was never taken`) — is not framing.
   (3) The duration unit was `ms|s`, so `30 seconds` and `2.6 sec` — the same claim
   re-spelled — was GREEN; every second-and-below unit word is in (`h`/`hours`
   included), comma-grouped and double-spaced figures are read whole (`1,000 s`
   reports as `1,000 s`), and `sha256s` is still refused.
   ⚠️ **What those corrections do NOT close, measured by the `#9522` review (`#9531`).**
   Framing is matched on the number's LINE, not per NUMBER, so a bare MENTION of a
   marker on that line frames the figure — `… measured 10.36 s …, unlike the whole arm`
   is GREEN — and a `#` block separated from the `def` by one blank line is not
   captured at all. Both are filed as `#9531`, with the measurements, because binding
   framing per number reds on legitimate prose (a figure whose framing wraps) and
   folding blank lines in would absorb a PEER function's comment block. A third
   re-spelling is silent for the same structural reason: a compound `min:sec` figure
   (`2m36s`) never matches, because the `36s` start is preceded by `m`, so the
   lookbehind refuses it — the same class, recorded with `#9531`.
   The THIRD arm in this class,
   `test_the_gate_walk_capture_reads_every_position_a_duration_is_written`, is the one
   that EXECUTES the capture: it writes the removed figure into the DOCSTRING, a
   TRAILING comment and the `#` block above the `def` of synthetic functions and
   requires the capture to read all three, and then falsifies the live function's own
   floor (`#`-line projection non-empty) and its `getsource` coverage (the docstring
   first line present, and strictly more lines than the projection plus a `def`), so
   neither the floor nor the capture can be a tautology.

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
import re
from collections.abc import Callable
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


def _owning_span(tree: ast.Module, symbol: str) -> tuple[int, int] | None:
    """`_span`, plus one CLASS level down for a bare method name.

    The census attributes a read to the innermost `def`, so a register entry may
    name `_materialize_connector_source` without its class — `_span` only
    resolves that spelling when the entry writes `Class.attr`. The register does
    not, and a fact check that silently found NO span would be checking nothing.
    """
    found = _span(tree, symbol)
    if found is not None:
        return found
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for inner in node.body:
            if _bound_name(inner) == symbol:
                return (inner.lineno, inner.end_lineno or inner.lineno)
    return None


def _symbol_fact_text(rel: str, symbol: str) -> str:
    """The source a stated exclusion FACT must appear in, or raise.

    The symbol's own span, plus — ONE level, no recursion — the body of every
    module-level constant that span NAMES. The second half is load-bearing and
    was measured, not assumed: three of the nine entries have no Cypher in their
    own span at all, because the walk lives in a query CONSTANT their body
    executes (`session_source_migration._DEGREE` behind `_degree`,
    `_OUT_TYPES`/`_IN_TYPES` behind `_rel_types`). Without it the arm would have
    had to weaken those facts to the constant's NAME — a check that the entry
    still mentions a symbol, which is the unread claim it exists to replace.

    `rel` is spelled from the REPO root (`tortoise/projection/entities.py`,
    as the register writes it), while a §13 row spells it from the package
    (`projection/entities.py`); both forms resolve here.
    """
    path = PKG / rel.removeprefix("tortoise/")
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = _module_tree(path)
    span = _owning_span(tree, symbol)
    assert span is not None, (
        f"`{rel}::{symbol}` — the register names a symbol this module does not "
        "bind, so its stated exclusion cannot be checked against any code"
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
    # rests on), `CALL_TIME_REL` entries do not — both key the same way.
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
    """Each entry's REASON is checked against the code it claims to describe.

    ⚠️ **This arm restores the half the `#9493` fold deleted, and its absence was
    a fail-open in the direction this class exists to close.** An entry is
    `(file, symbol, why, facts)`; the other three arms read only `(file, symbol)`,
    so the prose — the register's ONLY record of *why* a walk is out — was never
    checked against anything. Measured on the pre-arm tree: retyping
    `session_source_migration._OUT_TYPES` from `(old:Source {url:$u})` to
    `(old)`, or dropping `:Subject` from `sdk.provenance`, makes the walk start
    observing an `aboutObject` edge while EVERY arm stays green — the entry goes
    on excusing it, and §13's two legal grounds no longer hold for that site.

    The facts are asserted as plain substrings of the symbol's own span plus the
    module-level constants that span names (`_symbol_fact_text`), so a fact
    cannot be satisfied by an unrelated reader elsewhere in the file — which is
    what the old guard's cross-clause formatter existed for, and what a
    package-wide `grep` would have broken.
    """
    census = _call_time_census()
    assert census.EMPTY_PATTERN, (
        "the `EMPTY_PATTERN` register is empty — this arm would then assert "
        "nothing, which is how an unread prose reason ships green"
    )
    checked = 0
    failures: list[str] = []
    for rel, symbol, _why, facts in census.EMPTY_PATTERN:
        assert facts, (
            f"`{_site_key(rel, symbol)}` carries no code fact — its reason is "
            "prose nothing can falsify, so weakening the walk would leave it "
            "excusing a site that now reads the edge"
        )
        text = _symbol_fact_text(rel, symbol)
        for fact in facts:
            checked += 1
            if fact not in text:
                failures.append(f"  - {_site_key(rel, symbol)} no longer states `{fact}`")
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
    assert checked >= len(census.EMPTY_PATTERN), (
        f"{len(census.EMPTY_PATTERN)} entries produced only {checked} fact "
        "assertions — every entry must carry at least one, or the register's "
        "reasons are unverified again"
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
    """The file gate must admit a walk whose dash is outside the bracket literal.

    ⚠️ Measured on the pre-fix filter: both assembled spellings returned `False`
    while the one-literal spelling returned `True`, so a module holding only the
    assembled form was dropped before the derivation — which can see it — ran.
    `test_the_file_filter_un_sees_nothing` cannot catch this: the corpus holds no
    such file today, so the difference it compares stays empty either way. The
    assertion is therefore synthetic, on the two shapes a `+` concatenation
    produces.
    """
    census = _call_time_census()
    cases = {
        'q("MATCH (n)-" + "[r]->(m)")': "the bracket is its own literal",
        'q("MATCH (n)-[r]" + "->(m)")': "the dash is its own literal",
    }
    for source, shape in cases.items():
        assert census._may_traverse_every_type(source), (
            f"the module filter refuses the assembled spelling ({shape}): "
            f"{source} — the file is never walked, so the walk inside it is "
            "invisible however good the resolver is"
        )
    assert not census._may_traverse_every_type("no cypher here at all"), (
        "the filter admits a module with no traversal in it — as a filter that "
        "is free, but it also means the assertions above measure nothing"
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
    """Every PROSE line `_narrow_gate_walk_sites` carries — the text `#9506` is about.

    ⚠️ **This used to keep only lines that start with `#`, and that made the arm blind
    to the exact string the PR removes** — measured by the round-6 review, which put
    `measured 10.36 s of the control's 10.85 s` in three other positions inside the
    same function and got GREEN on both arms each time: the function DOCSTRING (which
    `inspect.getsource` returns and the `#` filter dropped), a TRAILING comment on a
    code line (`for file in _literal_files():  # measured 10.36 s …`), and a `#`
    comment above the `def` (which `getsource` does not return at all). A guard that
    reads one of the four places a duration can be written is a guard with three
    documented ways past it.

    So the capture is: the function's WHOLE source (docstring, inline code and
    trailing comments) plus the CONTIGUOUS `#` block immediately above the `def`.
    Within that span nothing is projected away, so a duration is dropped only by being
    absent — and the two boundaries are stated rather than implied (`#9531`): a `#`
    block separated from the `def` by a blank line is NOT captured (folding it in would
    absorb a peer function's prose), and neither is prose elsewhere in the file (a
    module docstring, a sibling's comment). Both were measured by the `#9522` review.
    """
    census = _call_time_census()
    return _prose_of(census._narrow_gate_walk_sites)


def _gate_walk_comment_lines(fn: Callable[..., object]) -> str:
    """The `#` lines `fn` carries — the PRE-`#9522` capture, kept as the arm's FLOOR.

    ⚠️ **The widened capture retired the arm's own anti-vacuity floor, and this puts it
    back** (measured by the `#9522` review).
    `test_the_gate_walk_comment_pins_no_wall_clock_figure` opens with `assert comment`
    — "the function still carries the mechanism comment" — and that was falsifiable
    only while the capture was `#`-only. Once `_prose_of` returns the function's whole
    source, the assertion can NEVER fail: measured by deleting all five `#` lines from
    `_narrow_gate_walk_sites` and watching the arm stay GREEN, i.e. the guard would pass
    on a function whose mechanism prose was gone, which is the state `#9506` exists to
    prevent. So the floor reads THIS projection: the duration scan may see everything,
    but "the comment is still there" is a question only the `#` lines answer.

    ⚠️ Its STRENGTH is "any `#` line survives", stated here rather than implied
    (measured by the cycle-2 review): replacing the mechanism prose with
    `# TODO: revisit this later` satisfies the floor while the mechanism claim is gone.
    Pinning a phrase would red on a legitimate re-wording of a true comment — the
    re-word-to-get-past-the-guard failure this file keeps re-learning — so the boundary
    is named here and the phrase-level check is deliberately not attempted.
    """
    return "\n".join(
        line.strip()
        for line in inspect.getsource(inspect.unwrap(fn)).splitlines()
        if line.lstrip().startswith("#")
    )


def _prose_of(fn: Callable[..., object]) -> str:
    """Every prose line `fn` carries, plus the contiguous `#` block above its `def`.

    ⚠️ **Factored out of `_gate_walk_comment` so the four POSITIONS a duration can be
    written in are executable, not prose** (`#9522`). A guard over a comment is only
    as good as its capture, and the capture was green on three of the four positions —
    measured by splicing the removed figure into each one and watching both arms pass.
    Taking a CALLABLE makes the capture testable on synthetic functions that write the
    figure in each position, which is what
    `test_the_gate_walk_capture_reads_every_position_a_duration_is_written` does.

    `inspect.unwrap` is not decoration: TEN of `_narrow_gate_walk_sites`'s siblings in
    `_about_call_time_census.py` are `@lru_cache`d (measured: `_universe`, `_sources`,
    `_literal_files`, `_files`, `_module_consts`, `_attr_consts`,
    `_rel_constant_names`, `_census`, `_empty_pattern_sites`, `_call_time_sites`).
    `getsource` and `getsourcelines`
    unwrap for you, but `inspect.getsourcefile(fn)` — the call below, after the
    unwrap, which locates the `#` block — raises `TypeError: … got _lru_cache_wrapper`, a message that names
    neither the guard nor the comment (found by the `#9522` review; the first version
    of this paragraph blamed `getsource`). The assertion below names the contract for
    anything that is not a function at all. ⚠️ `getsourcelines` scans BACK OVER
    DECORATORS, so for the functions this paragraph is about its line number is the
    first DECORATOR line, not the `def` (measured by the cycle-5 review) — the `#` block
    is therefore read from above the decorator, which is the right place and not the
    place the sentence used to claim.
    """
    fn = inspect.unwrap(fn)
    assert inspect.isfunction(fn) or inspect.ismethod(fn), (
        f"`_prose_of` needs a plain function to read source from, got "
        f"{type(fn).__name__} — a `functools.cache` / `lru_cache` wrapper hides the "
        "source, so unwrap it (or pass the inner function)"
    )
    source = inspect.getsource(fn)
    path = inspect.getsourcefile(fn)
    first = inspect.getsourcelines(fn)[1]
    above: list[str] = []
    if path:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        index = first - 2  # the line before the `def`
        while index >= 0 and lines[index].lstrip().startswith("#"):
            above.append(lines[index].strip())
            index -= 1
    return "\n".join([*reversed(above), source])


#: A duration, as a comment pins one (`10.36 s`, `~2.6 s`, `850 ms`, `30 seconds`,
#: `1,000 s`, `1.5 h`).
#:
#: ⚠️ **Three reviews changed this regex, and every change is about the same thing: a
#: guard whose unit list or number shape is narrow is retired by a RE-SPELLING.** The
#: round-6 review measured `30 seconds of the control's 60 seconds` and `2.6 sec of a
#: 17 sec arm` going GREEN — the identical claim, re-spelled — so every
#: second-and-below unit word is here, `h`/`hours`/`days` included AND the word forms
#: (`milliseconds`, `microseconds`, `nanoseconds`), which were still silent in the
#: previous list (cycle-4 review: `850 milliseconds` reported nothing while `850 ms` did).
#:
#: The `#9522` review then measured the OPPOSITE failure in the first version of the
#: lookbehind: `1,000 s`, `10,36 s` (decimal comma), `2,048 ms`, `1e3 s` and
#: `10.36  s` (double space) all went GREEN — not half-matched (`000 s`), not matched
#: AT ALL — so a grouped or re-separated figure was undetectable, which is strictly
#: worse than quoting a sub-token. Grouped numbers and BOTH decimal separators are
#: therefore matched whole, `\s*` absorbs a doubled space, and an exponent form is read
#: as one number. A GROUPED number with a fraction composes too (`1,000.5 s` matched
#: nothing at all in the first version — cycle-4 review). Unit words are
#: case-insensitive.
#:
#: And the match must stay WHOLE in one direction: `sha256s` must not report `256s`,
#: and `x1,000 s` must not report `000 s` — the lookbehind refuses a number that starts
#: mid-identifier, mid-digit-run or right after a SEPARATOR. The second lookbehind is
#: `\d,` and nothing broader, because a comma between two figures is a normal way to
#: write them (`# arms were 10.36s,10.85s` must report BOTH; `(?<![\w.,])` dropped every
#: figure after such a comma — measured by the cycle-3 review).
#:
#: Residual, recorded rather than silently accepted (`#9531`): a compound `min:sec`
#: figure (`2m36s`) goes SILENT — the `36s` start is preceded by `m`, so the same
#: lookbehind that refuses `sha256s` refuses it. That is the re-spelling class again,
#: and the filed home is `#9531` with the line-vs-number coarseness.
_DURATION = re.compile(
    r"(?<![\w.])(?<!\d,)"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:[.,]\d+)?(?:e[+-]?\d+)?\s*"
    r"(?:"
    r"milliseconds?|msecs?|usecs?|microseconds?|µsecs?|μsecs?|nsecs?|nanoseconds?|"
    r"ms|us|µs|μs|ns|seconds?|secs?|s|minutes?|mins?|hours?|hrs?|h|days?"
    r")\b",
    re.IGNORECASE,
)

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


#: A word that DENIES a framing marker when it governs it (`no --durations run`,
#: `not measured with --durations`, `--durations=0 was never taken`).
#:
#: ⚠️ **A blacklist of SPELLINGS, not a parse** — the boundary is stated, not implied
#: (measured by the `#9522` re-review: `--durations=0 wasn't taken` was GREEN while
#: `was never taken` was RED, a one-apostrophe dodge). Contractions (`n't`), and the
#: verbs a comment actually uses to say a measurement was not taken (`skipped`,
#: `omitted`, `unavailable`, `unmeasured`, `unframed`, `unknown`) are in; a negation
#: this list does not know is a one-line addition here, and the arm beside it pins the
#: spellings rather than the intent.
#:
#: It is SPLIT IN TWO, and that split is load-bearing (cycle-4 review):
#:
#: * `_DENIAL_STRONG` — a denial that can only be read as denying its own clause
#:   (`not`, `never`, `without`, `n't`, `skipped`, `omitted`, `unavailable`, …). It is
#:   searched ACROSS a parenthesis and a coordinated `and`, because
#:   `--durations=0 (never taken)` and `--durations=0 and never taken` deny the marker
#:   just as plainly as the un-parenthesised form, and the previous single list made
#:   all seven of those spellings SILENT — a fail-open introduced by widening the
#:   after-side boundary.
#: * `_DENIAL_WEAK` — a subject-less quantifier (`no`, `none`, `nothing`, `nobody`),
#:   which very often governs a LATER noun in a side remark (`(no flakes)`, `(no
#:   failures)`, `and no flakes`). It honours the wider boundary, so those stay framed.
_DENIAL_STRONG = re.compile(
    r"\b(?:not|never|without)\b"
    r"|\b\w+n['’]t\b"
    r"|\b(?:skipped|omitted|unavailable|unmeasured|unframed|unknown)\b",
    re.IGNORECASE,
)

_DENIAL_WEAK = re.compile(r"\b(?:no|none|nothing|nobody)\b", re.IGNORECASE)

#: A WEAK quantifier that is nevertheless DENYING a measurement — `and no framing was
#: taken`, `(no measurement exists)` — is a denial, not a side remark, because its object
#: is the measurement vocabulary. Searched across the same breaks as `_DENIAL_STRONG`
#: (cycle-4 review: `--durations=0 and no framing was taken` was otherwise SILENT, since
#: `no` is weak and `and` starts the side-remark window at once). `(no flakes)` and
#: `(no failures)` stay framed — their object is not a measurement.
_DENIAL_GOVERNING = re.compile(
    r"\b(?:no|none|nothing|nobody)\b[^.;]{0,40}?"
    r"\b(?:framing|measure\w*|figure|duration|timing|numbers?|wall-clock|magnitude|split)\b"
    # ...and it must PREDICATE that noun (`no framing WAS TAKEN`, `no measurement
    # EXISTS`). Without the lookahead, a parenthetical that denies a DIFFERENT quantity
    # reds a framed figure — `# 14.4 s whole arm (no spread figure)` and
    # `(no per-file timing)` were both RED at the previous head (cycle-5 review), which is
    # the true-comment-gets-reworded failure this file keeps re-learning.
    r"(?=\s*(?:was|is|were|are|exists?|available|taken|run|measured|pinned|stated|recorded))",
    re.IGNORECASE,
)

#: Every denial symbol — what the BEFORE side searches, because a negation in front of
#: a marker governs it whichever of the two lists it is in.
_DENIAL = re.compile(
    rf"(?:{_DENIAL_STRONG.pattern})|(?:{_DENIAL_WEAK.pattern})", re.IGNORECASE
)

#: How far a denial reaches from its marker, in characters, WITHIN one clause.
_DENIAL_WINDOW = 40

#: A clause boundary — a separator that ends the reach of a denial, so
#: `--durations=0 — no other change to the control step` does not deny `--durations`.
#: A DECIMAL POINT (`10.36 s`) is not a boundary: `.` counts only when a space or the
#: line end follows it.
_CLAUSE_BREAK = re.compile(r"[,;:—–]|[.;](?=\s|$)")

#: A boundary for a denial that FOLLOWS its marker — a superset of `_CLAUSE_BREAK`.
#:
#: ⚠️ **Asymmetric on purpose, and each half is measured.** A parenthesis or a bare
#: `and` after the marker is a SIDE REMARK, so `# whole arm 14.4 s (no flakes)` and
#: `# 14.4 s whole arm and no flakes` must stay framed — both were RED with
#: `_CLAUSE_BREAK` alone (cycle-3 review), i.e. a true comment was being reworded to
#: pass the guard. In front of the marker the same words are the governing clause, so
#: `# (no --durations run): 10.36 s` stays RED and is pinned as such: only this
#: after-side boundary knows about parentheses.
#:
#: ⚠️ And it applies ONLY to `_DENIAL_WEAK` (cycle-4 review). The boundary only ever
#: SHORTENS the text a denial is searched in, so applying it to the strong denials made
#: `--durations=0 (never taken)` and `--durations=0 and never taken` SILENT — a
#: fail-open with no compensating red. A strong denial is searched across these breaks.
_CLAUSE_BREAK_AFTER = re.compile(r"[,;:—–()]|\band\b|[.;](?=\s|$)")

#: A real sentence end — where the search for a STRONG denial stops, because a strong
#: denial after the marker is about the marker however it is CLAUSE-punctuated.
#:
#: ⚠️ `;` is NOT here (cycle-6 review): it is a clause separator, and including it made
#: `--durations=0; never taken` SILENT — a one-character substitution for the hole the
#: veto exists to close, with no arm able to tell the two behaviours apart. `.` keeps its
#: `(?=\s|$)` guard so a decimal (`10.36 s`) is not a sentence end. Residual, stated:
#: an ABBREVIATION dot (`i.e. never taken`, `e.g.`) still reads as a sentence end, so a
#: strong denial after one is framed — it is in `#9531` with the other residuals.
_SENTENCE_END = re.compile(r"[.](?=\s|$)")


def _marker_spans(line: str) -> list[tuple[int, int]]:
    """Every framing-marker occurrence on `line`, as `(start, end)` spans."""
    lowered = line.lower()
    spans: list[tuple[int, int]] = []
    for marker in _DURATION_FRAMING:
        start = lowered.find(marker)
        while start >= 0:
            spans.append((start, start + len(marker)))
            start = lowered.find(marker, start + 1)
    return spans


def _denied(line: str, start: int, end: int) -> bool:
    """Is the marker at `[start, end)` denied by its OWN clause?

    ⚠️ **Per OCCURRENCE, in both directions, because the first version was wrong both
    ways** — both measured by the `#9522` review:

    * FAIL-CLOSED (red on legitimate prose): the veto was one line-level search, so any
      negation anywhere on the line killed the line's framing —
      `# 14.4 s whole arm, --durations=0 — no other change to the control step` RED,
      with two of its three markers un-negated.
    * FAIL-OPEN (green on denied prose): the regex only searched `negation … marker`, so
      `# … measured 10.36 s of the control's 10.85 s — --durations=0 was never taken`
      was GREEN — one word-order swap from the hole the veto exists to close.

    So a denial counts only within `_DENIAL_WINDOW` characters of the marker and only
    inside the marker's own clause: `_CLAUSE_BREAK` (`,` `;` `:` `—` `–` and a real
    sentence `.`) ends its reach on either side. That is what makes both measured cases
    come out right — `no other change to the control step` is behind a dash and an
    unrelated noun phrase, while `was never taken` sits in the marker's own clause.

    ⚠️ **A STRONG denial after the marker is searched past a parenthesis and past a
    coordinated `and`** (`_DENIAL_STRONG` vs `_DENIAL_WEAK`): `--durations=0 (never
    taken)` and `--durations=0 and never taken` are denials of the marker, while
    `(no flakes)` and `and no failures` are side remarks about something else. Applying
    the wider boundary to both lists made the first pair SILENT (cycle-4 review), which
    is the fail-open direction and the worse one.
    """
    before = line[max(0, start - _DENIAL_WINDOW) : start]
    breaks = list(_CLAUSE_BREAK.finditer(before))
    if breaks:
        before = before[breaks[-1].end() :]
    if _DENIAL.search(before):
        return True
    after = line[end : end + _DENIAL_WINDOW]
    # ⚠️ THREE windows, because the three lists are denied by different shapes:
    #   * `_DENIAL_STRONG` is searched to the end of the SENTENCE — `--durations=0 — never
    #     taken` and `--durations=0, never taken` deny the marker as plainly as the
    #     parenthesised form, and cutting at `,`/`—` made both SILENT (cycle-5 review);
    #   * `_DENIAL_GOVERNING` reads the SAME sentence window (its own predicate lookahead
    #     is what keeps `(no spread figure)` green), and `_DENIAL_WEAK` is searched only to
    #     the next clause break, so `— no other change to the control step` (a weak
    #     quantifier over a different noun) stays a side remark;
    #   * `_DENIAL_WEAK` is searched inside the narrower after-side boundary again, so
    #     `(no flakes)` and `and no failures` stay framed.
    sentence = _SENTENCE_END.search(after)
    strong_window = after[: sentence.start()] if sentence else after
    if _DENIAL_STRONG.search(strong_window):
        return True
    # The GOVERNING list also reads the sentence window: its own lookahead is what keeps
    # `(no spread figure)` green (no predicate follows the noun), so it does not need the
    # clause cut — and with the cut, `— no measurement was ever taken` went SILENT.
    if _DENIAL_GOVERNING.search(strong_window):
        return True
    stop_after = _CLAUSE_BREAK_AFTER.search(after)
    weak_window = after[: stop_after.start()] if stop_after else after
    return bool(_DENIAL_WEAK.search(weak_window))


def _framed(line: str) -> bool:
    """Does `line` frame its durations — does it carry one marker that is NOT denied?

    Per marker OCCURRENCE, so one denied marker cannot sink a line whose other markers
    are not denied. Cost, stated: a denial that is not separated from a marker by a
    clause boundary still reaches it — `# no framing was taken, 10.36 s --durations=0`
    is ambiguous prose and comes out FRAMED (the comma cuts the denial off), which is
    the fail-open direction and is filed with the line-vs-number coarseness as `#9531`.
    """
    return any(
        not _denied(line, start, end) for start, end in _marker_spans(line)
    )


def _unframed_durations(comment: str) -> list[str]:
    """Durations `comment` pins WITHOUT the framing that produced them (`#9506`).

    ⚠️ **This predicate is the fix to a FAIL-OPEN guard, not a rewording of one.**
    The first version of the arm red on the co-occurrence of a self-denial phrase
    (`no magnitude` / `no wall-clock`) AND a duration. That made it **green on the
    pre-`#9516` main** — `fa84a473d:tests/_about_call_time_census.py:990-992` carried
    *"re-reading `_sources()` and rebuilding the same 29 `_File`s measured 10.36 s
    of the control's 10.85 s for byte-identical output"* (`git show origin/main:… |
    grep -c 10.36` is `0` at this base — the figure is gone), which says no denial at
    all, so there was nothing for the guard to fire on: the arm passed on precisely
    the text this PR deletes, and would pass again if that figure were re-introduced.
    It was also fail-open on REWORDING — turning "No magnitude is pinned" into "No
    timing figure is named" silently retired it.

    So the rule now reads the CLAIM rather than a phrase about it: a duration is a
    fact asserted about a measurement, and it is acceptable only when the text also
    states the framing that produced it. This reds on the pre-`#9516` main text and on
    the intermediate `~2.6 s of ~17 s` sentence alike, and no REWORDING of the claim
    itself retires it, because it never reads a denial PHRASE — the only denial it
    reads is a negation of the framing marker, scoped to that marker's own clause and
    checked on BOTH sides of it (`_denied`). ⚠️ The denial VOCABULARY is open, and that
    boundary is stated rather than implied (measured by the `#9522` re-review):
    `--durations=0 was never taken` reds, and so do the contraction (`wasn't taken`),
    `was skipped`, `was omitted` and `is unavailable` spellings, but a phrase the list
    does not know (`never done here` in a new SENTENCE, `--durations=0 wasn't really
    used` split across a `.`) is framed. The list is a blacklist of spellings, not a
    parse, so a new denial phrasing is a one-line addition, not a rewording dodge.
    Two costs are stated rather than found later: `_CLAUSE_BREAK` ends a denial's reach
    at a sentence `.`, so `# 10.36 s --durations=0. Never taken.` is FRAMED (the denial
    is a new sentence), and a denial separated from its marker by a comma does not
    reach it (see `_framed`).

    ⚠️ **The framing is matched on the NUMBER'S OWN LINE, and that is the second fix.**
    Matching it over the whole text (the first version) passed a comment that merely
    NAMED `--durations` somewhere — `… 10.36 s of the control's 10.85 s — not measured
    with --durations=0, so no framing was ever taken` was GREEN, and the docstring's
    claim that it "can only stay GREEN where the framing IS stated" was false. A
    framed number and a framed comment are different things, so the window is the line.
    Cost, stated: a duration whose framing wraps onto the NEXT line reds, and the fix
    is to put them on one line — the direction that keeps a claim settleable.

    ⚠️ **Residual coarseness, restored and now HONEST (`#9531`).** The window is the
    line, not the number, so a bare MENTION of a marker on a duration's line frames
    it — `… measured 10.36 s …, unlike the whole arm` is GREEN, and a second figure on
    a framed line (`# the whole arm is 14.4 s; a stale 99 s …`) rides on the first
    one's framing. The earlier version of this docstring claimed that direction "can
    only stay GREEN where the framing IS stated", which the `#9522` review measured to
    be FALSE; a per-number binder reds on legitimate prose (a figure whose framing
    wraps, `# 14.4 s, --durations=0`), so the bypass is filed as `#9531` instead of
    being described as harmless.
    """
    found: list[str] = []
    for line in comment.splitlines():
        if _framed(line):
            continue
        found.extend(match.group(0).strip() for match in _DURATION.finditer(line))
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
    # ⚠️ The FLOOR reads the `#`-only projection, NOT the capture. While the capture was
    # `#`-only, `assert comment` was falsifiable in the direction this arm exists for —
    # delete the mechanism comment and the arm reds. The `#9522` capture widening made it
    # unfalsifiable (a function's source is never empty), measured by deleting all five
    # `#` lines from `_narrow_gate_walk_sites` and watching this arm stay GREEN. Its
    # strength is "a `#` line survives, of any content" — see `_gate_walk_comment_lines`.
    assert _gate_walk_comment_lines(_call_time_census()._narrow_gate_walk_sites), (
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
    """Non-vacuity for `_unframed_durations`, on the PRE-`#9516` MAIN — not an
    intermediate commit of this branch (`#9506`).

    ⚠️ **The round-5 review found the first guard green on `origin/main`.** It red
    only on `says_none AND duration`, and the base text says no denial, so the arm
    passed on the very text this PR exists to delete. This arm feeds the same
    three states through the same predicate the guard uses — the pre-`#9516` string,
    the intermediate string, and a framed duration — so "the guard reds on what it
    replaced" is executed rather than asserted in a docstring, the shape this file
    already uses elsewhere ("mutation applied, all arms re-run").

    ⚠️ The ANCHOR is named by COMMIT, not by `origin/main` (measured by the `#9522`
    re-review): `git show origin/main:tests/_about_call_time_census.py | grep -c 10.36`
    returns `0` at this PR's base — `#9516` already removed the figure — so the earlier
    wording pointed a re-verifier at a tree that no longer says what this arm claims.
    The string below is `fa84a473d:tests/_about_call_time_census.py:990-992`, the last
    main commit to carry it.
    """
    pre_9516_main = (
        "# `_literal_files()` is already parsed and cached; re-reading `_sources()` "
        "and\n# rebuilding the same 29 `_File`s measured 10.36 s of the control's "
        "10.85 s for\n# byte-identical output."
    )
    # Verbatim from `git show aded3145d:tests/_about_call_time_census.py` lines 990-995.
    # ⚠️ The `#9522` review measured the PREVIOUS content of this literal to be an
    # INVENTED string carrying this very citation: no commit of that file has ever
    # contained `8.14 s` or `25.34 s` (`git log --all -S` returns only the lanes that
    # wrote this comment), while the real lines 990-995 are the `~2.6 s of ~17 s` block
    # below. So the fix whose commit message calls out "an ABRIDGED PARAPHRASE labelled
    # 'the intermediate string'" had replaced it with a fabricated QUOTATION — a
    # quotation that is not the thing it quotes, the defect this file keeps re-learning.
    # The blob's six lines (990-995), DE-INDENTED — that is the only transformation;
    # nothing is re-wrapped. Pinned by line count and prefix below, because the cycle-1
    # review measured that the abridged 4-line paraphrase this replaced returns the same
    # `['2.6 s', '17 s']` and would pass a wording-only assertion (the "claim not
    # executed" shape this file keeps re-learning).
    intermediate = (
        "# `_literal_files()` is already parsed and cached; re-reading `_sources()` and\n"
        "# rebuilding the same 29 `_File`s is redundant work for byte-identical output —\n"
        "# the same `_File` values over the same `_sources().items()` order, so only object\n"
        "# identity changes. No magnitude is stated on purpose: the redundant rebuild is a\n"
        "# fraction of this arm (a re-verifier measured ~2.6 s of ~17 s on one box), and a\n"
        "# wall-clock number pinned into a comment is the claim that rots."
    )
    # `~` is decorative — the span reported is the number and its unit, not the tilde.
    assert _unframed_durations(intermediate) == ["2.6 s", "17 s"], (
        "the predicate must red on the VERBATIM intermediate `~2.6 s of ~17 s` block "
        "from `aded3145d` — it is the state the earlier round called \"pre-fix\", and "
        f"the quoted literal must be that blob's text, not a paraphrase ({_unframed_durations(intermediate)})"
    )
    assert len(intermediate.splitlines()) == 6 and intermediate.startswith(
        "# `_literal_files()` is already parsed and cached; re-reading `_sources()` and"
    ), (
        "the quoted intermediate must be the blob's SIX lines, de-indented — an abridged "
        "or re-wrapped paraphrase returns the same durations and so cannot be caught by "
        f"the duration assertion ABOVE (got {len(intermediate.splitlines())} lines)"
    )
    assert _unframed_durations(pre_9516_main) == ["10.36 s", "10.85 s"], (
        "the predicate must red on the PRE-`#9516` main text (`measured 10.36 s of the "
        "control's 10.85 s`) — that is the figure this PR removes, so an arm that "
        "passes on it is vacuous: it would let the identical figure be re-introduced"
    )
    # ── the denial is PER OCCURRENCE and BOTH SIDES; both failures measured by the
    # `#9522` review, one in each direction. A denial AFTER the marker used to be GREEN
    # (`--durations=0 was never taken` — the hole the veto exists to close, one word
    # order away); a denial somewhere else on the line used to red legitimate prose
    # (`… whole arm, --durations=0 — no other change to the control step`).
    assert _unframed_durations(
        "# measured 10.36 s of the control's 10.85 s — `--durations=0` was never taken"
    ) == ["10.36 s", "10.85 s"], (
        "a denial written AFTER the marker must red as surely as one written before "
        "it — the marker is named only to say it was never taken"
    )
    # ⚠️ The denial VOCABULARY, pinned as spellings: the cycle-2 review measured that a
    # contraction (`wasn't taken`) or a verb the list did not know (`was skipped`,
    # `is unavailable`) went SILENT while `was never taken` red — a one-apostrophe dodge.
    for denied in (
        "# 10.36 s --durations=0 wasn't taken",
        "# 10.36 s --durations=0 was skipped",
        "# 10.36 s --durations=0 was omitted",
        "# 10.36 s --durations=0 is unavailable",
        "# 10.36 s --durations=0 unmeasured",
    ):
        assert _unframed_durations(denied) == ["10.36 s"], (
            f"{denied!r} denies the framing it names, so it must red "
            f"({_unframed_durations(denied)}) — a denial spelling the list does not "
            "know is a silent way past the guard, so each spelling is an arm here"
        )
    assert not _unframed_durations(
        "# 14.4 s whole arm, --durations=0 — no other change to the control step"
    ), (
        "an unrelated negation on an otherwise framed line must NOT red it — the veto "
        "is per marker occurrence, or a true comment gets reworded to pass the guard"
    )
    assert not _unframed_durations(
        "# no gating change: 14.4 s whole arm, --durations=0"
    ), (
        "a denial in front of an unrelated clause must not veto framing stated after "
        "it on the same line"
    )
    # ⚠️ A parenthesis or a bare `and` AFTER the marker is a SIDE REMARK, not a denial
    # of it — both were RED with `_CLAUSE_BREAK` alone (cycle-3 review), i.e. a true
    # comment would have been reworded to pass the guard. The parenthetical BEFORE the
    # marker is the opposite case and must stay RED, which is why the after-boundary is
    # a superset rather than one list.
    for side_remark in (
        "# whole arm 14.4 s (no flakes)",
        "# 14.4 s in-suite (no failures)",
        "# 14.4 s whole arm --durations=0 (no failures)",
        "# 14.4 s whole arm and no flakes",
        # a parenthetical that denies a DIFFERENT quantity: the measurement noun alone
        # was enough to red these at the previous head (cycle-5 review), so the governing
        # list now requires a PREDICATE after the noun
        "# 14.4 s whole arm (no spread figure)",
        "# 14.4 s whole arm (no per-file timing)",
        "# whole arm 14.4 s and no figure for the per-file split",
    ):
        assert not _unframed_durations(side_remark), (
            f"{side_remark!r} frames its figure and adds an unrelated parenthetical or "
            f"coordinated negation ({_unframed_durations(side_remark)}) — a true "
            "comment reding here is how a guard teaches an editor to reword it"
        )
    assert _unframed_durations("# (no --durations run): 10.36 s") == ["10.36 s"], (
        "a negation in front of the marker governs it even inside a parenthesis — the "
        "after-side boundary must not shield it"
    )
    # ⚠️ A STRONG denial AFTER the marker reaches across a parenthesis and across a
    # coordinated `and` — the after-side boundary only ever SHORTENS the searched text,
    # so applying it to the strong list made all of these SILENT (cycle-4 review), a
    # fail-open with no compensating red. The weak quantifiers above stay framed.
    for strong_denial in (
        "# 10.36 s --durations=0 (never taken)",
        "# 10.36 s --durations=0 (not run)",
        "# 10.36 s --durations=0 (skipped)",
        "# 10.36 s --durations=0 and never taken",
        "# 10.36 s --durations=0 and wasn't taken",
        "# 10.36 s --durations=0 and no framing was taken",
        # a strong denial is about the marker however it is punctuated: the sentence-end
        # window is what makes `, never taken` and `— never taken` red, and cutting at the
        # separator made both SILENT (cycle-5 review)
        "# 10.36 s --durations=0 — never taken",
        "# 10.36 s --durations=0, never taken",
        "# 10.36 s --durations=0 — no measurement was ever taken",
        # `;` is a CLAUSE separator: including it in the sentence-end window made this
        # silent, a one-character substitution for the hole the veto exists to close
        "# 10.36 s --durations=0; never taken",
        "# measured 10.36 s with --durations=0; the run was never taken",
    ):
        assert _unframed_durations(strong_denial) == ["10.36 s"], (
            f"{strong_denial!r} denies the framing it names, parenthesised or "
            f"coordinated or not ({_unframed_durations(strong_denial)}) — a strong "
            "denial is not a side remark, so the after-side boundary must not stop it"
        )

    # ── the SECOND fix this predicate claims: framing is per LINE, not whole-text.
    # Cycle-4 review measured that no arm executed it — reverting `_framed` to a
    # whole-`comment` test left the file green, because the only multi-line input was
    # the `intermediate` literal, which carries no marker on any line.
    assert _unframed_durations(
        "# 10.36 s of the control's 10.85 s\n# the whole arm was re-run last week"
    ) == ["10.36 s", "10.85 s"], (
        "a marker on a DIFFERENT line must not frame this line's figure — that is the "
        "whole-text hole the per-line window closes, and nothing else here executes it"
    )
    assert not _unframed_durations("# the whole arm is 14.4 s\n# nothing else"), (
        "the same-line case must stay GREEN, or the arm above can be satisfied by "
        "making everything red"
    )
    for word_form, expected in (
        ("# the arm took 850 milliseconds", ["850 milliseconds"]),
        ("# the arm took 850 microseconds", ["850 microseconds"]),
        ("# the arm took 850 nanoseconds", ["850 nanoseconds"]),
        ("# the arm took 850 msec", ["850 msec"]),
        ("# the arm took 900 usec", ["900 usec"]),
        ("# the arm took 900 usecs", ["900 usecs"]),
        ("# the arm took 900 usecs of the arm", ["900 usecs"]),
        ("# the arm took 900 µsec", ["900 µsec"]),
        ("# the arm took 900 nsecs", ["900 nsecs"]),
        ("# cost 1,000.5 s of the arm", ["1,000.5 s"]),
        ("# cost 2,048.25 ms of the arm", ["2,048.25 ms"]),
    ):
        assert _unframed_durations(word_form) == expected, (
            f"{word_form!r} writes a duration the unit/number list claims to cover "
            f"({_unframed_durations(word_form)}) — a silent re-spelling is how the "
            "guard gets retired by an editor's keyboard"
        )
    # ── re-spellings of the same figure: every one of these was GREEN when the number
    # had to be a plain decimal or the unit had to be `ms|s`, and a GREEN figure here
    # means the guard is retired by an editor's keyboard, not by a false claim.
    for respelled, expected in (
        ("# measured 1,000 s of the control's 2,000 s", ["1,000 s", "2,000 s"]),
        ("# measured 10,36 s of the control's 10,85 s", ["10,36 s", "10,85 s"]),
        ("# measured 1e3 s of the 2e3 s arm", ["1e3 s", "2e3 s"]),
        ("# measured 10.36  s of the control's 10.85  s", ["10.36  s", "10.85  s"]),
        ("# cost 1.5 h of the arm's 2 h", ["1.5 h", "2 h"]),
        ("# cost 900 us of the arm", ["900 us"]),
    ):
        assert _unframed_durations(respelled) == expected, (
            f"{respelled!r} must report the durations it writes, verbatim "
            f"({_unframed_durations(respelled)}) — a re-spelt figure that goes SILENT "
            "is worse than one quoted imperfectly: nothing ever reds again"
        )
    assert not _unframed_durations("# sha256s and 1,000 of them, no duration here"), (
        "the lookbehind must still refuse a token that is not a duration — `sha256s` "
        "and a bare count must not be reported as one"
    )
    assert _unframed_durations("# x1,000 s of whatever") == [], (
        "`x1,000` is not a duration: the number must be refused from its first digit "
        "and never reported from a fragment after the group separator — measured by "
        "the cycle-2 review as `['000 s']`, a span that is not what was written"
    )
    assert _unframed_durations("# arms were 10.36s,10.85s") == ["10.36s", "10.85s"], (
        "a comma BETWEEN two figures is not a reason to refuse the second one "
        f"({_unframed_durations('# arms were 10.36s,10.85s')}) — measured by the "
        "cycle-3 review, where the group-separator lookbehind silently dropped it"
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


def _figure_in_the_docstring() -> None:
    """A duration in the position `getsource` RETURNS and the `#` filter DROPPED:

    measured 10.36 s of the control's 10.85 s.
    """


def _figure_as_a_trailing_comment() -> None:  # measured 10.36 s of the control's 10.85 s
    """A duration on a code line's TRAILING comment — also dropped by the filter."""


# measured 10.36 s of the control's 10.85 s
# (contiguous, so the backward scan has to reach past the `def`)
def _figure_in_the_block_above_the_def() -> None:
    """A duration in the `#` block ABOVE the `def` — `getsource` never returns it."""
    # the mechanism line the floor looks for (a `#`-leading line IN the source)


def test_the_gate_walk_capture_reads_every_position_a_duration_is_written():
    """The capture is not a `#`-only projection (`#9522`).

    ⚠️ **A guard over a comment is only as good as its capture, and the capture was
    blind to three of the four places a duration can be written.** `#9516` merged the
    comment change and left the arm reading `line.lstrip().startswith("#")`, which
    drops the function DOCSTRING and TRAILING comments (`inspect.getsource` returns
    both) and never sees the `#` block above the `def` (`getsource` does not return
    it at all). Measured by splicing `measured 10.36 s of the control's 10.85 s` —
    the exact string the `#9506` change deletes — into each position on `69390341f`:
    all three stayed GREEN, so the guard had three documented ways past it.

    The three synthetic functions below put the figure in each position THROUGH the
    same `_prose_of` the guard uses, so "the capture reads every position" is executed
    rather than asserted in a docstring. This arm reds on `69390341f` and greens here;
    it is the missing half of the `#9522` fix, which changed the capture without an
    arm that could tell the two captures apart.
    """
    for fn in (
        _figure_in_the_docstring,
        _figure_as_a_trailing_comment,
        _figure_in_the_block_above_the_def,
    ):
        assert _unframed_durations(_prose_of(fn)) == ["10.36 s", "10.85 s"], (
            f"`{fn.__name__}` writes the `#9506` figure in a position the capture "
            "projects away, so the guard stays GREEN on the very text it exists to "
            "delete — the capture must be the function's whole source plus the `#` "
            "block above the `def`"
        )

    census = _call_time_census()
    # ⚠️ `inspect.unwrap` and the plain-function contract were both UNEXECUTED — the live
    # function is undecorated, so neither could run (cycle-5 review). A `@lru_cache`d
    # sibling is the natural probe, and it fails on the ASSERT first (`got
    # _lru_cache_wrapper` is not a function); only if that assert is weakened too does
    # `inspect.getsourcefile` raise its own `TypeError` (cycle-6 review corrected this
    # comment, which named the TypeError for a mutation that actually trips the assert).
    unwrapped = _prose_of(census._census)
    assert inspect.getdoc(census._census).splitlines()[0].strip() in unwrapped, (
        "a `@lru_cache`d sibling's capture does not contain its docstring, so the "
        "`inspect.unwrap` is not doing its job — `getsourcefile` would raise on the wrapper"
    )
    with pytest.raises(AssertionError, match="needs a plain function"):
        _prose_of(len)  # type: ignore[arg-type]
    # ⚠️ The FLOOR that the capture widening retired, executed rather than described:
    # `_gate_walk_comment_lines` is the `#`-only projection the arms used to read, so a
    # function with NO `#` line must come back EMPTY. Without this arm the floor in
    # `test_the_gate_walk_comment_pins_no_wall_clock_figure` can only be checked by
    # hand — measured by the `#9522` review deleting all five `#` lines from
    # `_narrow_gate_walk_sites` and watching that arm stay GREEN.
    assert _gate_walk_comment_lines(_figure_in_the_docstring) == "", (
        "the `#`-line floor reports a `#` line for a function that carries none, so it "
        "cannot tell a comment-less function from a commented one — it would pass on "
        "the exact state `#9506` exists to prevent"
    )
    assert _gate_walk_comment_lines(_figure_in_the_block_above_the_def) == (
        "# the mechanism line the floor looks for (a `#`-leading line IN the source)"
    ), (
        "the floor must still SEE a `#` line where one exists, and ONLY the ones "
        "`getsource` returns (the block above the `def` is not in it) — otherwise the "
        "floor is a constant and the arm above it is vacuous for the wrong reason"
    )
    stripped = _gate_walk_comment_lines(census._narrow_gate_walk_sites)
    assert stripped, (
        "the live function carries no `#` line, so the mechanism comment is gone and "
        "the pin arm would be asserting nothing"
    )
    # And the capture must be MORE than that projection — otherwise the docstring and
    # trailing-comment positions are invisible on the LIVE function too.
    #
    # ⚠️ **The previous version of this check could never fail** (measured by the
    # `#9522` review): it asked whether ANY captured line differs from the projection's
    # lines, and a capture is `inspect.getsource` at minimum, whose first line is the
    # `def` — so the answer was `True` for every implementation of `_prose_of`, INCLUDING
    # a deliberately BLIND one (`#` lines + the `def` line, docstring and trailing
    # comments still invisible). The two checks below are the falsifiable pair: the
    # DOCSTRING is derived from the function object (no literal to reword), and the
    # capture must strictly exceed the `#`-only projection plus that one `def` line —
    # the smallest blind shape there is.
    doc_lines = (inspect.getdoc(census._narrow_gate_walk_sites) or "").splitlines()
    live = _prose_of(census._narrow_gate_walk_sites)
    assert doc_lines, (
        "`_narrow_gate_walk_sites` carries no docstring any more, so the DOCSTRING "
        "position cannot be checked on the live function — the arm would be asserting "
        "the capture covers a position that no longer exists"
    )
    assert doc_lines[0].strip() in live, (
        "the live capture does not contain the function's own DOCSTRING first line "
        f"({doc_lines[0].strip()!r}), so `getsource`'s docstring position is projected "
        "away and a figure written there is invisible to the duration scan"
    )
    assert len(live.splitlines()) > len(stripped.splitlines()) + 1, (
        f"the live capture is {len(live.splitlines())} line(s) for a "
        f"{len(stripped.splitlines())}-line `#` projection — that is the projection "
        "plus at most a `def` line, i.e. the smallest BLIND capture, so the docstring "
        "and trailing-comment positions are invisible to the duration scan"
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
