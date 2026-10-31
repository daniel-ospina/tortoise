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
    `STRUCTURAL_REPOINT_RELS` (the snapshot-derivable family PLUS the
    ID-ADDRESSED family, `#9836`) — and the `delete_only` guard beside it is
    gated on that SAME union (`or etype not in STRUCTURAL_REPOINT_RELS`), because
    since #9836 review round 2 the producer emits that descriptor for the
    ID-ADDRESSED family too (the no-self-edge arm, `sdk.py`
    `if rel in STRUCTURAL_REPOINT_RELS and rel not in emitted_self_deletes:`).
    For that family the descriptor is the RECORD leg's only delete path, not a
    graph resurrection — the graph deletes the self-edge at the transfer itself.

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
   it can miss a bad door, and it can invent a hop through a repeated attribute or
   builtin name; a repeated function-local VARIABLE target no longer resolves
   (`#9817` — the NAME of a nested `def`/`class` still does, and that residue is
   declared in the note at `_DOOR_REACH_HOPS`), the
   budget is a BOUND (3, with two
   measured exceptions), and its acceptance COUNT is pinned by (22) so it cannot
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
   relation is a BOUND, so the COUNT it accepts is pinned as its own number: at
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
   `test_the_gate_walk_capture_reads_every_position_a_duration_is_written` +
   `test_the_gate_walk_anchors_name_the_commit_that_carries_the_figure` +
   `test_the_gate_walk_comment_asserts_no_unmeasured_magnitude` +
   `test_the_gate_walk_magnitude_ratios_recompute_from_the_rows_they_name` +
   `test_the_narrow_gate_walk_rebuild_buys_nothing` — the listed
   `#9506`/`#9522`/`#9501` arms, and the only ones here about this instrument's OWN
   prose rather than §13's. (No count: a bare number is the same claim in a third
   alphabet, which is the defect this file removes from the census comment — an
   enumerated list of names is checkable, a total is not.) The third is the `#9522` capture arm: it replays the
   removed figure
   through each of the three SYNTHETIC positions (docstring, trailing comment, `#` block
   above the `def`), then holds the LIVE function by containment (its docstring's first
   line is in the capture) and by width (strictly more lines than the `#`-line
   projection), plus the `#`-line floor that the capture widening would otherwise have
   retired. The
   gate-walk comment in `_about_call_time_census.py` carried a duration a re-verifier
   cannot settle — `measured 10.36 s of the control's 10.85 s` at
   `fa84a473d:990-992`, the last main commit to carry it (NOT the merge base: measured
   at this PR's base, `git show origin/main:tests/_about_call_time_census.py | grep -c
   10.36` is `0`, `#9516` already removed the figure), and
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
   neither the floor nor the capture can be a tautology. The FOURTH is the citation
   arm: the prose that names where the pre-`#9516` figure came from is read back as
   PROSE (module docstring + that arm's docstring), and it must not name a TREE as
   that carrier — both forms now listed in `_STALE_GATE_WALK_ANCHORS` shipped in this
   file and were false at the head that shipped them — it must name the
   commit `fa84a473d:990-992`, and — where this checkout can reach the objects,
   skipped rather than shallow-ified otherwise — that blob must carry the figure while
   `origin/main`'s does not.
   ⚠️ **The FIFTH arm is the same rule in the OTHER ALPHABET (`#9501`).** `#9516`
   removed the FIGURES and left the MAGNITUDE in words — *"No magnitude is pinned: the
   rebuild is a small fraction of this arm's cost, and a wall-clock number in a comment
   is the claim that rots"* — which `_unframed_durations` cannot see, because it matches
   a NUMBER and a UNIT and a proportion spelled in words carries neither. So the
   sentence survived the guard written to delete exactly that
   claim, and it did not reproduce: measured at `81f5594dc` — the merge base when the
   figures were taken; `main` has advanced since, so a `base` label would describe a
   tree the measurement never saw (fresh process, `_literal_files()` warm, two runs) —
   the rebuild is 2.5–3.0 s of
   `_narrow_gate_walk_sites`'s own 3.9–4.4 s, and the percentages recomputed from that
   row are the claim — not the word form the sentence used. (`_unsettleable_magnitudes`'
   docstring carries both rows and the percentages recomputed from them, and an arm
   there holds the two prose sites to the same rows. The first measurement belongs to a
   DIFFERENT tree, `69390341f`, and `#9549` changed the census universe between them, so
   neither set is a re-measurement of the other. The `69390341f` row is not restated
   here: a figure copied into this paragraph is a figure no arm recomputes, and the
   `81f5594dc` pair above is recomputed only because `_BASE_MEASUREMENT_PROSE` holds it
   to that row — `#9528` re-review, which caught this sentence claiming both were
   unchecked when one of them is the most tightly held prose in the file.)
   `_unsettleable_magnitudes()` states that rule once for the word form
   (a magnitude — digit or word — is allowed only with the framing that produced it),
   and `test_the_gate_walk_comment_asserts_no_unmeasured_magnitude` applies it to the
   live comment, with its non-vacuity — and the legs that pin its per-line framing
   window — in `test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced` (below).
   ⚠️ The phrase may WRAP (the sentence it exists to red on is broken as `…a small
   fraction of this` / `# arm's cost…`), so the search window is the phrase's line plus
   the NEXT one (`_flatten_comment`), and FRAMING is decided over that same pair by
   `_framed` (`#9501` review, cycles 2–3: a whole-text framing test cleared every
   magnitude in the function, a line-by-line search was green on the file's real
   comment, and the two-line window is pinned by its own leg so the accepted cost stays
   one line wide and visible).
   `_unframed_durations` is deliberately NOT flattened: its framing window is the
   number's own LINE by design, and the wrap residual there is filed as `#9531`.
   ⚠️ **The SIXTH arm is the third alphabet, and the one no predicate can see.** The
   comment had also come to pin a bare count two lines below a sentence saying no
   magnitude was pinned ("the same 29 `_File`s" next to "No magnitude is pinned: the
   rebuild is a small fraction of this arm's cost"): true, deterministic, and measured
   by NOTHING, since bare digits carry no UNIT for `_unframed_durations` to match and
   are not words for `_unsettleable_magnitudes`. The count is gone rather than
   restated, and the claim it stood for is EXECUTED by
   `test_the_narrow_gate_walk_rebuild_buys_nothing`: it substitutes a rebuild of the
   same `_File`s for `_literal_files()` and runs the REAL `_narrow_gate_walk_sites`
   body, so "same values, same order, same derivations" is measured through the walk
   the comment sits in rather than written down beside it.

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
26. `test_a_walk_inside_an_already_registered_statement_is_unbounded` — the
   per-statement bound is still one level too coarse: the derivation keys a site
   on `(symbol, line)`, so a walk appended INSIDE an already-registered query
   string (`… properties(n) UNION MATCH (x)-[rr]->(y) …`) is the SAME site, the
   count does not move, and the first walk's fact vouched for it. Measured on
   that cut, the copy shipped green; the per-WINDOW net now reds naming the
   window, and the fixture pins both halves (one derived site, two walk
   occurrences, the second unbounded).
27. `test_a_stated_fact_no_site_carries_reds_the_entry` — the reverse direction
   an any-fact-per-site loop cannot express. It is REQUIRED for the two-site
   entries (each site carries only its own fact) and it silently dropped 4 of
   the 12 recorded grounds: retyping `_DEGREE`'s anchor from
   `(old:Source {url:$u})` to `(old)` left the arm green while the walk started
   observing `aboutObject`, where `origin/main`'s all-facts rule reded. Every
   recorded fact must now be carried by SOME site of its entry.
28. `test_the_walk_window_ends_at_a_union_and_at_a_comma` — the per-CLAUSE cut
   of item 26 was still open in TWO directions, both measured GREEN: a second
   pattern of the SAME `MATCH` (`… (a)-[r]->(b), (x)-[rr]->(y) …`) produces no
   new clause segment, and a `UNION` arm that REUSES a variable name from the
   first arm (`… RETURN type(r), labels(n)[0] … UNION MATCH (n)-[rr]->(m) …`)
   satisfied the old earlier-clause escape's variable overlap. The window ends at
   both boundaries now: two windows per query, the registered walk's window
   carries the fact and the copy's does not. Pinned through the real derivation
   too (the copy is still ONE site, so the count cannot see either shape).
29. `test_the_live_fact_corpus_is_the_stripped_source` — item 24 exercises
   `_code_lines` DIRECTLY, so nothing pinned its only production consumer.
   Measured: reverting `_site_fact_text` to raw source left every other arm green,
   because no live fact is comment-satisfiable today — the hole reverts one line
   away and silently. The arm asserts the LIVE corpus is comment-free and that at
   least one live site's statement carries a `#` comment in the raw source, so
   the discriminator is live rather than hypothetical.

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
nothing. The UNTYPED walks — every row whose query carries a relationship with NO type written, in any of the three spellings §13 derives (the bracketed `-[r]->`, the bracket-less `(n)--()`, and a pattern comprehension), whether that is its whole read or a walk beneath a constant-carried one — are NOT a lower bound any more, and are deliberately not enumerated here: (16) derives them from the tree, which is what stopped the class moving on every pass. Line NUMBERS are not asserted — the symbol is authoritative and a
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
import subprocess
import tokenize
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

#: The two boundaries a walk's bindings cannot cross (`_top_level_offsets` finds
#: them outside brackets and strings): a `UNION` arm binds its OWN variables, and
#: a comma-separated pattern is a second, independently matched walk.
#:
#: ⚠️ **The comma boundary is decided by CLAUSE, not by every top-level comma.**
#: `_top_level_offsets(text, _COMMA)` finds the commas of a `WITH` projection list
#: too, and there a comma separates ITEMS of one projection — evaluated in ONE
#: scope — not two patterns. Splitting there cut `WITH r, n`'s window in two, so
#: the registered fact fell outside the walk's window and `_scope_survives` (which
#: correctly reports the projection as CARRYING `r`) was never consulted: `WITH r,
#: n` / `WITH n, r` were reported ungrounded, the exact carrying projection
#: `_walk_binding` and `_scope_survives` document as staying green (#9544 review
#: P2). `_pattern_comma_offsets` decides each comma by the clause it sits in.
_UNION_CLAUSE = re.compile(r"\bUNION\b", re.I)
_COMMA = re.compile(r",")
#: Clauses whose top-level commas separate INDEPENDENT patterns — the only commas a
#: walk's fact cannot cross. In every other clause (`WITH`, `RETURN`, `SET`, …) a
#: comma separates ITEMS of one projection, which are evaluated in one scope, so it
#: ends no window. `CREATE`/`MERGE` are included because their `(a), (b)` patterns
#: are independent matches in exactly the way a second `MATCH` pattern is.
_PATTERN_CLAUSES = frozenset({"MATCH", "OPTIONAL MATCH", "MERGE", "CREATE"})

#: A SCOPE boundary — the third way a NAME stops meaning the same relationship. A
#: `WITH` re-projects the variables in scope: a name it does not carry is GONE, so a
#: later `MATCH` re-binding that name binds a FRESH relationship and walks a fresh
#: set of types. ⚠️ `WITH` is the ONLY scope boundary in Cypher's subquery story: a
#: `CALL { … }` block IMPORTS the outer bindings (that is how state is passed in),
#: so the block itself ends nothing — what ends the identity is a `WITH` INSIDE it,
#: which is what `_scope_boundary_offsets` descends to find. An earlier revision
#: made the block a blanket barrier on the inverted premise ("imports nothing it
#: is not given"); see `_scope_boundary_offsets` for why no valid query can exhibit
#: that shape and what the barrier cost instead.
_SCOPE_BOUNDARY = re.compile(r"\bWITH\b", re.I)
#: A `CALL { … }` subquery OPENER. Its body is descended into by
#: `_scope_boundary_offsets`, never refused wholesale: `_top_level_offsets` cannot
#: see a `WITH` at brace depth 1.
_CALL_SUBQUERY = re.compile(r"\bCALL\s*\{", re.I)
#: Where a `WITH`'s projection list ends — the next clause keyword of its OWN block.
#: `CALL` is a clause too, so `WITH n CALL { … }` ends its projection at the `CALL`;
#: without it the projection ran on into the subquery body and every such query read
#: as a barrier (#9544 review P2).
_PROJECTION_END = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE|SET|DELETE|DETACH|WHERE|WITH|UNWIND|"
    r"RETURN|ORDER\s+BY|SKIP|LIMIT|UNION|CALL)\b",
    re.I,
)
#: `WITH *` carries EVERY name into the next scope, so it is not a barrier. It is
#: matched as a STANDALONE projection ITEM (`_projection_items`), never as "any `*`
#: in the projection": `WITH count(*) AS c` and `WITH a * 2 AS c` drop every grouping
#: variable, and an unanchored `*` search read both as `WITH *` (#9493 review P2-2).
_PROJECTS_EVERYTHING = "*"

#: A walk's relationship SLOT, read at the offset `_walk_occurrences` reported:
#: the bracket (`-[r]->`, `<-[r {c:$c}]-`, `-[]->`). The bracket-less spelling is
#: read from the census's own `_BARE_TRAVERSAL` match spans rather than by a second
#: pattern here: a CONSUMING `(node)--(node)` pattern cannot see the second hop of a
#: chained `(a)--(b)--(c)` (it eats `(b)` and resumes after it), which is how
#: `_walk_binding` came to raise "the walk net and the derivation have drifted
#: apart" over a shape with no drift in it (#9544 review P3).
_WALK_SLOT = re.compile(r"[-<]\s*\[([^\]]*)\]")
#: The Cypher name a relationship pattern binds (`r` in `[r]`, `[r {c:$c}]`).
_REL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

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
        'if (isinstance(etype, str) and etype in STRUCTURAL_REPOINT_RELS',
        # ⚠️ Widened from `DERIVABLE_STRUCTURAL_RELS` by #9836 review round 2:
        # the ID-ADDRESSED family's `delete_only` descriptors are applied here
        # too, so the gate is the UNION of the two families. The witness moves
        # with the code — a row pinned to the retired subset would have gone on
        # PASSING only because the string survived elsewhere in the file, which
        # is the false PASS this table's `_NON_LITERAL` spans exist to stop.
        'or etype not in STRUCTURAL_REPOINT_RELS',
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


def _class_body_targets(rel: str, function: str, cls: str) -> frozenset[str]:
    """What a class body DECLARES, read from the SOURCE: the `Assign`/`AnnAssign`
    target names of `cls` (a class defined directly inside `function`, in
    `tortoise/<rel>`) that carry a VALUE and whose targets are bare `Name`s —
    `_symbol_index`'s own condition, applied to a class that is itself
    function-local.

    ⚠️ These are exactly the names the index must NOT contain for this class: a
    class local to a function has a local BODY, so `index()` walks it with
    `in_function=True` and mints none of them (that absence is what the paired
    assertion pins). This helper reports the SOURCE side of the pair, so a red on it
    means the SOURCE moved — a rename, or a declaration re-shaped into a form the
    index skips — and the pin needs re-pointing. It does NOT mean `in_function`
    stopped propagating.

    ⚠️ The condition must be the index's own, not a looser one, or the presence
    check defeats itself: an annotation-only `x: list` (no value) would satisfy
    PRESENT while the index cannot mint the id the next assertion forbids —
    `assert <id> not in set()`, green and testing nothing. Nested `def`/`class`
    names are excluded for a DIFFERENT reason: the index mints a nested definition
    node regardless of `in_function`, so including them would red the ABSENCE pin
    with the wrong diagnosis ("`in_function` is not propagating") when what changed
    was a name collision.
    """
    tree = _parsed(str(PKG / rel))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function:
            continue
        for inner in node.body:
            if isinstance(inner, ast.ClassDef) and inner.name == cls:
                names: set[str] = set()
                for statement in inner.body:
                    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        continue
                    if statement.value is None:
                        continue
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    names.update(t.id for t in targets if isinstance(t, ast.Name))
                return frozenset(names)
    return frozenset()


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

    This is the SOURCE-spelling corpus — the net that says a recorded ground is
    still in the code. The walk net deliberately reads a different corpus,
    `_site_query_texts`, which is the RESOLVED query: comments and prose cannot
    reach it at all, and a walk the resolver assembles out of parts is visible
    there where no source-level scan of the bracket is.
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
        if (node.lineno, node.end_lineno) == span:
            continue  # the statement IS the constant; its body is already in `text`
        text += "\n" + "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
    return text


@functools.cache
def _file_by_rel() -> dict:
    """The census's own parsed modules, keyed the way the register spells a file.

    The same `_File` objects the derivation walked, so a fact check cannot parse a
    DIFFERENT tree than the site came from (a second parse could drift on a
    generated file, and would silently measure the wrong source).
    """
    return {file.rel: file for file in _call_time_census()._files()}


def _site_query_texts(file, symbol: str, line: int) -> tuple[str, ...]:
    """The RESOLVED Cypher of the walk(s) derived at this site — the executed text.

    ⚠️ **The corpus for the walk net is the resolved query, not the source, and
    that is what makes it the same object the derivation saw.** `_resolve` folds
    adjacent literals, `+` concatenation, f-string parts and module constants, so
    a walk the derivation attributes to `(symbol, line)` appears here exactly as
    it will run — including the part-assembled spellings (`"MATCH (n)-" +
    "[r]->(m)"`) that no source-level scan of the bracket can see.

    Docstrings are excluded for the reason the derivation excludes them: prose
    quoting a pattern is not an executed read.
    """
    census = _call_time_census()
    docstrings = census._docstrings(file)
    texts: list[str] = []
    for node in census._roots(file):
        if id(node) in docstrings or getattr(node, "lineno", 0) != line:
            continue
        if symbol not in census._site_symbol(node, file):
            continue
        texts.extend(census._resolve(node, census._scope(file, node), file))
    return tuple(dict.fromkeys(texts))


def _top_level_offsets(text: str, needle: re.Pattern[str]) -> list[int]:
    """Offsets where `needle` matches OUTSIDE brackets and quoted strings.

    Brackets are counted (`()`, `[]`, `{}` all nest in Cypher), so the commas of a
    property map, a label list or a comprehension are not boundaries, and a quoted
    string cannot introduce one. Shared by both boundaries below, so `UNION` and
    the comma are measured the same way a query is read.
    """
    hits: list[int] = []
    depth = 0
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and (match := needle.match(text, index)) is not None:
            hits.append(index)
            index = match.end()
            continue
        index += 1
    return hits


def _pattern_comma_offsets(text: str) -> list[int]:
    """Top-level commas that separate PATTERNS — never a projection's ITEM commas.

    ⚠️ **`_top_level_offsets(text, _COMMA)` is not the pattern boundary, and
    treating it as one cut a carrying projection in half.** Measured on the
    pre-fix `_walk_spans`: `MATCH (s:Subject {id:$sid})-[r]->(n) WITH n, r MATCH
    (x)-[r]->(y) RETURN type(r)` produced the windows `('… WITH n', 'r')` and
    `(', r MATCH …', 'r')`, so the registered fact sat in the FIRST window, the
    walk in the second, and `_scope_survives` — which correctly reports that
    `WITH n, r` CARRIES `r` — was never consulted (#9544 review P2). `WITH r, n`
    behaved identically.

    A comma separates patterns only inside a clause that MATCHES them
    (`_PATTERN_CLAUSES`); in a `WITH`/`RETURN` projection it separates items of
    ONE projection, which are evaluated in one scope and cross no binding. The
    clause is the nearest preceding TOP-LEVEL clause keyword, read by the same
    `_PROJECTION_END` scanner that bounds a projection, so the two cannot agree
    about the clause a comma belongs to and disagree about the boundary.

    A comma with no clause keyword in front of it is treated as a boundary: no
    valid Cypher query has one, and the conservative direction there is the
    fail-open closure rather than a false red.
    """
    marks: list[tuple[int, str]] = []
    for at in _top_level_offsets(text, _PROJECTION_END):
        match = _PROJECTION_END.match(text, at)
        assert match is not None, f"`_PROJECTION_END` matched nothing at {at}"
        marks.append((at, " ".join(match.group(1).upper().split())))
    marks.extend((at, "") for at in _top_level_offsets(text, _COMMA))
    offsets: list[int] = []
    clause = ""
    for at, keyword in sorted(marks):
        if keyword:
            clause = keyword
        elif not clause or clause in _PATTERN_CLAUSES:
            offsets.append(at)
    return offsets


def _walk_binding(query: str, start: int) -> str:
    r"""What IDENTIFIES the walk at `start` — its relationship VARIABLE, or its own text.

    ⚠️ **A fact that merely SITS in a walk's window is not a fact ABOUT that walk,
    and that gap shipped a live fail-open.** With the window alone as the bound, a
    second all-edge walk appended with no `UNION` and no top-level comma between
    it and the registered one — `… -[r]->(n) MATCH (x)-[rr]->(y) …`, a CHAINED hop
    `… -[r]->(n)-[rr]->(y) …`, or a comprehension in a `WHERE`
    (`WHERE size([(x)-[rr]-(y) | rr]) > 0`) — landed in the registered walk's
    window and inherited its fact. Measured with the pre-fix `_unbounded_walks`:
    all three returned `[]`, i.e. green, while the register went on claiming the
    class is closed.

    The token is read straight off the text at the offset `_walk_occurrences`
    reported, so this net can never disagree with the derivation about what a walk
    is or where it starts:

    * a bracketed walk that binds a name (`-[r]->`, `-[r {c:$c}]->`) → the name —
      `r`. A fact bounds it by NAMING `r`, which is exactly what every recorded
      ground does (`WHERE type(r) IN [...]`, `-[r]->(n:Point)`, ...). Reusing a
      name inside one arm is Cypher's own way of saying *the same relationship*,
      so it is not a second walk; a `UNION` arm re-binding the name IS one, and
      the window boundary is what separates those two cases. ⚠️ **A name only
      means the same relationship for as long as some scope CARRIES it**, which
      is the third case and is `_scope_survives`'s job rather than a window
      boundary's: `MATCH (s)-[r]->(n) WITH n MATCH (x)-[r]->(y)` sits in ONE
      window (no `UNION`, no top-level comma) and reuses the name `r`, yet the
      `WITH n` dropped `r`, so the second `MATCH` binds a FRESH relationship and
      walks a fresh set of types — a fact about the first cannot bound it. `WITH *`
      or a projection that carries the name (`WITH r, n`) keeps the identity, and
      those stay green;
    * a bracketed walk that binds NOTHING (`-[]->`) → its own relationship slot
      text, since there is no name a fact could carry;
    * a bracket-less walk (`(n)--()`, and each hop of a chained
      `(a)--(b)--(c)`) → its own `(node)…(node)` pattern text, for the same
      reason. The extent comes from the census's `_BARE_TRAVERSAL` match at the
      reported offset — the SAME match the derivation reported, so the two cannot
      disagree about where the hop ends — and never from a consuming re-scan of
      the query (a `\(node\)--\(node\)` pattern eats the middle node of a chain,
      so its next `finditer` step starts past the second hop and finds nothing).
    """
    slot = _WALK_SLOT.match(query, start)
    if slot is not None:
        name = _REL_NAME.match(slot.group(1).strip())
        return name.group(0) if name is not None else slot.group(0)
    dashes = _call_time_census()._BARE_TRAVERSAL.match(query, start)
    if dashes is not None and start > 0 and query[start - 1] == ")":
        # A node in this class carries no brackets of its own (`[^()]*`), so the
        # nearest `(` before the hop and the nearest `)` after it are its ends.
        left = query.rfind("(", 0, start)
        right = query.find(")", dashes.end())
        if left != -1 and right != -1:
            return query[left : right + 1]
    raise AssertionError(
        f"`_walk_occurrences` reported a walk at offset {start} of {query!r}, and "
        "neither spelling matches there — the walk net and the derivation have "
        "drifted apart, so every fact assertion about this query is measuring "
        "text no walk was derived from"
    )


def _names_binding(fact: str, binding: str) -> bool:
    """Does `fact` bound the walk identified by `binding` — not merely sit near it?

    A NAME is matched as a whole identifier: `\br\b` by hand would read the `r` of
    `[rr]` as a mention of `r`, which is precisely the mutation this net must
    catch (`MATCH (x)-[rr]->(y)` beside the registered `-[r]->` walk). A walk that
    binds no name is matched LITERALLY, because its pattern text is the only thing
    that can identify it.
    """
    if not _REL_NAME.fullmatch(binding):
        return binding in fact
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(binding)}(?![A-Za-z0-9_])", fact
        )
        is not None
    )


def _walk_windows(query: str) -> tuple[tuple[str, str], ...]:
    """One entry per WALK OCCURRENCE in one resolved query — its WINDOW and its BINDING.

    ⚠️ **The unit is the window, not the query, not the clause, and not the
    statement, and each coarser unit was MEASURED to leak.** The derivation keys a
    site on `(symbol, line)`, so a walk appended inside an already-registered
    query string produces no new site at all; what the fact check has to do is
    decide how much text around the walk a fact may vouch for. Three answers were
    tried and the first two shipped a live fail-open:

    * a STATEMENT (the P1 cut) — `… properties(n) " "UNION MATCH (x)-[rr]->(y)
      …` appended to `sdk.provenance` shipped green: the fact about the first walk
      excused the second;
    * a CLAUSE, plus an "an earlier clause counts when it binds a variable this
      walk uses" escape — two escapes stayed green: `, (x)-[rr]->(y)` as a second
      pattern of the same `MATCH` (no new clause, so no new segment), and a
      `UNION` arm that REUSED a variable name from the first arm (the escape's
      overlap came from the second arm's own `RETURN type(r)`);
    * a WINDOW — the boundaries below. Two hard boundaries, both of them things a
      walk's bindings cannot cross: a `UNION` (each arm binds its OWN variables, so
      a constraint in one says nothing about the other) and a top-level COMMA in a
      clause that MATCHES patterns (a second pattern in one `MATCH` matches
      independently, exactly as the escape's own justification said). A comma in a
      projection list is NOT a boundary: it separates the ITEMS of one projection,
      which are evaluated in one scope — `_pattern_comma_offsets` decides by clause,
      because splitting there discarded the registered fact of `WITH n, r` before
      the scope rule was ever asked (#9544 review P2).

    ⚠️ **A window is necessary and NOT sufficient,** which is the second half of
    this rule: a fact inside the window can still be a fact about a DIFFERENT walk
    in it. Three boundary-free appends (`MATCH (x)-[rr]->(y)` after the
    registered `MATCH`, a chained hop `-[r]->(n)-[rr]->(y)`, a comprehension in a
    `WHERE`) all stayed inside the registered walk's window and shipped green on
    that rule alone. So each occurrence also carries its BINDING
    (`_walk_binding`), and `_unbounded_walks` demands a fact that NAMES it.

    The walk itself is `_walk_occurrences`, the census's positional form of
    `_is_empty_pattern_read` — the same definition the derivation uses, so the net
    can never check a shape the derivation does not call a walk (a bracket-less
    `(n)--()`, or a comprehension after a `SET`).

    ⚠️ **A window is a SPAN, not a scope.** The third way a fact stops being about
    a walk is a scope boundary INSIDE one window: `MATCH (s)-[r]->(n) WITH n
    MATCH (x)-[r]->(y)` has no `UNION` and no top-level comma, so both walks share
    one window, but the `WITH n` dropped `r` — the second `MATCH` binds a FRESH
    relationship and the fact about the first one says nothing about it. That
    check needs the walk's OFFSET, not only its window text, which is why
    `_walk_spans` is the primitive and this function is the text view of it.
    """
    return tuple(
        (query[low:high], binding)
        for low, high, _start, binding in _walk_spans(query)
    )


def _walk_spans(query: str) -> tuple[tuple[int, int, int, str], ...]:
    """`(window_low, window_high, walk_offset, binding)` — one row per WALK OCCURRENCE.

    The offset is what a scope check needs: whether a `WITH` between a fact and
    the walk CARRIED the walk's binding cannot be answered from the window text
    alone when the same name appears more than once in it.
    """
    census = _call_time_census()
    arm_bounds = sorted({0, *_top_level_offsets(query, _UNION_CLAUSE), len(query)})
    windows = [
        (arm_bounds[i], arm_bounds[i + 1]) for i in range(len(arm_bounds) - 1)
    ]
    spans: list[tuple[int, int]] = []
    for low, high in windows:
        local = [
            low + offset for offset in _pattern_comma_offsets(query[low:high])
        ]
        bounds = sorted({low, *local, high})
        spans.extend((bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1))
    return tuple(
        (low, high, start, _walk_binding(query, start))
        for start in census._walk_occurrences(query)
        for low, high in spans
        if low <= start < high
    )


def _scope_survives(query: str, fact_at: int, walk_at: int, binding: str) -> bool:
    """Does `binding` still name the SAME relationship across the gap fact → walk?

    ⚠️ **Reusing a relationship name is only "the same relationship" for as long
    as some scope CARRIES the name.** `MATCH (s:Subject {id:$sid})-[r]->(n) WITH n
    MATCH (x)-[r]->(y) RETURN type(r)` sits in ONE window — no `UNION`, no
    top-level comma — and reuses `r`, so the window-and-binding rule read the
    registered fact as a ground for a walk over a relationship the fact never
    mentioned. It is a genuinely unconstrained all-edge walk (`x`/`y` unbound, so
    `aboutObject` is walked and its targets returned), i.e. the exact class this
    net exists to refuse, shipped green.

    The barrier is decided on the text a `WITH` projects (`_PROJECTION_END` bounds
    it), by `_projection_carries`: `WITH n` drops `r` → the name is gone → a later
    `MATCH (x)-[r]->(y)` is a FRESH relationship; `WITH r, n` and a standalone
    `WITH *` carry it → not a barrier, so that shape stays green. A rename or a
    derivation (`WITH r AS rel`, `WITH type(r) AS t`, `WITH r.id AS rid`) and an
    aggregate that drops the grouping variables (`WITH count(*) AS c`) are all
    barriers: the relationship itself does not survive them.

    ⚠️ **A `CALL { … }` block is NOT a barrier — a `WITH` inside it is, FOR THAT
    BODY'S SCOPE ONLY.** An earlier revision refused every walk inside a subquery
    on the premise that a subquery "imports nothing it is not given"; Cypher says
    the opposite (the block imports every binding in scope, which is how state is
    passed in, and the one thing it refuses is a RE-declaration). That inverts the
    rule: the shape the blanket barrier claimed to catch cannot be written as a
    valid query at all, while a `WITH` inside the body — which really does end the
    identity — was out of reach, because it sits at brace depth 1 where
    `_top_level_offsets` does not look. `_scope_boundary_offsets` descends into
    the body instead, so the same projection reader decides the subquery case and
    the top-level one.

    ⚠️ **And the descent is BLOCK-SCOPED, not query-wide.** The first cut of it
    handed every body-internal `WITH` to every walk in the query, so a walk AFTER
    the block was refused by a projection that never had it in scope: measured,
    `MATCH …-[r]->(n) CALL { WITH n RETURN n AS nn } MATCH (x)-[r]->(y)` reported
    the post-`CALL` walk as ungrounded, though the subquery's `WITH` projects only
    inside the subquery and the outer `r` is still in scope after the block
    (#9544 review P3). `_scope_boundary_spans` pairs each boundary with the block
    it belongs to, and a walk outside that block is untouched by it.
    """
    low, high = sorted((fact_at, walk_at))
    for at, block_low, block_high in _scope_boundary_spans(query):
        if not block_low <= walk_at < block_high:
            # A `WITH` INSIDE a `CALL { … }` body ends that BODY's scope only; the
            # enclosing scope's binding survives the block (a subquery imports the
            # outer bindings, it does not consume them).
            continue
        if not low < at < high:
            continue
        word = _SCOPE_BOUNDARY.match(query, at)
        assert word is not None, f"`_SCOPE_BOUNDARY` matched nothing at {at}"
        projection = query[word.end() : _projection_end(query, at, high)]
        if _projection_carries(projection, binding):
            continue
        return False
    return True


def _block_span(query: str, at: int) -> tuple[int, int]:
    """The span of the innermost `CALL { … }` BODY containing `at`, else the whole query.

    A `WITH`'s projection list ends where the next clause OF ITS OWN BLOCK begins.
    Clause keywords inside a subquery body sit at brace depth 1, so
    `_top_level_offsets` cannot see them — a projection read on block bounds would
    run past the body and out into the outer query. `{}` is the only brace Cypher
    has here (a property map cannot contain a clause keyword), so the innermost
    enclosing pair is the block.
    """
    opens: list[int] = []
    quote = ""
    index = 0
    while index < len(query):
        char = query[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "{":
            opens.append(index)
        elif char == "}" and opens:
            opened = opens.pop()
            if opened < at < index:
                return opened + 1, index
        index += 1
    return 0, len(query)


def _scope_boundary_offsets(query: str) -> list[int]:
    """Every `WITH` that can END a relationship's identity — subquery bodies included.

    Two readers, one rule: `_top_level_offsets` finds the block-level `WITH`s, and
    each `CALL { … }` body is descended into with the body's OWN text so a `WITH`
    inside it is found by the same reader (recursively, so a nested subquery is
    covered by the same descent rather than by a second rule).

    ⚠️ **The descent is what makes the subquery case pinnable, and the blanket
    barrier it replaces was not.** A `WITH` inside the body is a real, valid query
    (`CALL { WITH s MATCH (x)-[r]->(y) RETURN y }` drops `r` by projecting only
    `s`, so the inner `[r]` is a FRESH relationship) and the walk inside it binds
    the SAME name the fact names — so `_scope_survives` is genuinely consulted and
    the arm in `test_a_walk_rebinding_a_name_a_with_dropped_is_a_FRESH_walk` can
    tell this rule from its absence. A subquery WITHOUT a `WITH` re-binding a name
    is a redeclaration, which Cypher rejects, so there is no valid query to pin
    the opposite direction with — which is exactly why the barrier could never be
    pinned and had to go (#9544 review P2).

    ⚠️ **The offset alone is not enough: a boundary belongs to a BLOCK.** A `WITH`
    inside a body ends that body's scope and nothing else, so the caller needs to
    know which block each offset came from — that pairing is
    `_scope_boundary_spans`, and this function stays the flat offset view of it.
    """
    offsets = _top_level_offsets(query, _SCOPE_BOUNDARY)
    for match in _CALL_SUBQUERY.finditer(query):
        low, high = _block_span(query, match.end())
        if not low <= match.end() < high:
            continue
        offsets.extend(
            low + offset for offset in _scope_boundary_offsets(query[low:high])
        )
    return sorted(offsets)


def _scope_boundary_spans(query: str) -> list[tuple[int, int, int]]:
    """`(offset, block_low, block_high)` per `WITH` — the BLOCK whose scope it ends.

    ⚠️ **A `WITH` inside a `CALL { … }` body ends that BODY's scope, not the
    enclosing one — and applying it query-wide is a false red.**
    `_scope_boundary_offsets` descends into bodies because a `WITH` at brace depth
    1 is invisible to `_top_level_offsets`; the first cut of that descent then
    handed every body-internal `WITH` to every walk in the query. Measured:
    `MATCH (s:Subject {id:$sid})-[r]->(n) CALL { WITH n RETURN n AS nn } MATCH
    (x)-[r]->(y) RETURN type(r)` reported the post-`CALL` walk as ungrounded,
    though the subquery's `WITH` projects only inside the subquery and the outer
    `r` is still in scope after the block (#9544 review P3).

    `_block_span` supplies the pairing: for a `WITH` at brace depth 0 it answers
    the whole query, so a top-level boundary reaches every walk exactly as before,
    and for a body-internal one it answers that body's span, so the boundary
    reaches only the walks the projection actually had in scope.
    """
    return [(at, *_block_span(query, at)) for at in _scope_boundary_offsets(query)]


def _projection_items(projection: str) -> tuple[str, ...]:
    """The `WITH` projection split into ITEMS at its top-level commas.

    A comma inside `count(*)` or `r {c: $c}` is not an item separator, so the
    split tracks bracket depth. `DISTINCT` is a keyword of the projection, not of
    its first item, so it is stripped before the items are read.
    """
    text = projection.strip()
    if text.upper().startswith("DISTINCT"):
        text = text[len("DISTINCT") :].strip()
    items: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))
    return tuple(item.strip() for item in items if item.strip())


def _projection_carries(projection: str, binding: str) -> bool:
    """Does this `WITH` carry the RELATIONSHIP a fact bound under `binding`?

    ⚠️ **Decided from the projection's TARGETS, never from a bare-token search of
    its text** (#9493 review P2-1). A token search said `WITH r AS rel` carries
    `r` because the substring `r` is in it — but `AS rel` RENAMES the name, so a
    later `MATCH (x)-[r]->(y)` binds a FRESH relationship and the fact about the
    first one grounds nothing. `WITH type(r) AS t` (`r` inside a function call)
    and `WITH r.id AS rid` (`r` followed by a property access) were read the same
    way, and all three are unconstrained all-edge walks.

    So: a standalone `*` item carries everything; a bare `name` item carries
    `name`; and an `expr AS alias` item carries the RELATIONSHIP only when no
    alias is introduced at all — a renamed or derived value is not the same edge,
    even when the alias happens to be spelled `alias`. The conservative
    direction is a false red on a query that re-projects a relationship without
    renaming it, which is the shape a reader can see and fix; the other direction
    is a fail-open on the class this net exists to refuse.
    """
    return any(
        item in (_PROJECTS_EVERYTHING, binding)
        for item in _projection_items(projection)
    )


def _projection_end(query: str, at: int, limit: int) -> int:
    """Where the `WITH` projection starting at `at` ends — the next clause of its BLOCK.

    Bounded on BOTH sides. Below by the enclosing block's span (`_block_span`),
    because a clause inside a `CALL { … }` body sits at brace depth 1 and
    `_top_level_offsets` would run the projection past the body and out into the
    outer query. Above by `limit` (the walk's own window end) so a projection is
    never read into a clause that belongs to another window: the question is only
    what THIS `WITH` carried forward.
    """
    low, high = _block_span(query, at)
    ends = [
        low + offset
        for offset in _top_level_offsets(query[low:high], _PROJECTION_END)
        if low + offset > at
    ]
    return min([*[off for off in ends if off <= limit], limit])


def _unbounded_walks(query: str, facts: tuple[str, ...]) -> list[tuple[str, str]]:
    """The walk WINDOWS of `query` that NO stated fact bounds — walk by walk.

    Each item is `(window, binding)` — the window the walk sits in and the token
    that identifies the walk itself, so a failure can say WHICH walk was left
    unstated instead of only which stretch of text it sits in.

    A fact bounds a walk only when BOTH hold: the fact is inside that walk's own
    window, and the fact NAMES that walk's own binding (`_names_binding`). The
    first half is what stops a fact crossing a `UNION` or a comma; the second is
    what stops a fact about one walk vouching for its NEIGHBOUR inside one window,
    which is the shape that survived the window rule alone (`MATCH (x)-[rr]->(y)`
    appended with no boundary in front of it). There is deliberately NO
    cross-window escape: the two forms of one (statement-wide, and
    earlier-clause-on-variable-overlap) were each measured open, and a walk whose
    bound is written in a DIFFERENT arm or pattern of the same query is a walk
    nobody has stated a ground for.

    ⚠️ **And a name only carries that identity while a scope CARRIES it** — a
    `WITH` that drops the binding ends it, so a later `MATCH` re-binding the same
    name is a fresh relationship and the fact about the old one does not bound it
    (`_scope_survives`). The third half of the test, for the same reason as the
    second: the name alone is not the walk.

    This is the per-walk half of the fact check, and it exists because the
    site-oriented half cannot see a second walk written into an already-registered
    query string: the derivation keys a site on `(symbol, line)`, so such a copy
    produces no new site, and a fact found ANYWHERE in the statement excused it.
    """
    unbounded: list[tuple[str, str]] = []
    for low, high, start, binding in _walk_spans(query):
        window = query[low:high]
        bounded = False
        for fact in facts:
            fact_at = query.find(fact, low, high)
            if fact_at == -1 or fact_at + len(fact) > high:
                continue
            if _names_binding(fact, binding) and _scope_survives(
                query, fact_at, start, binding
            ):
                bounded = True
                break
        if not bounded:
            unbounded.append((window, binding))
    return list(dict.fromkeys(unbounded))


def _uncarried_facts(facts: tuple[str, ...], text: str) -> list[str]:
    """The stated facts that appear NOWHERE in the text they are recorded about.

    The direction an any-fact-per-site loop cannot express: it is required for the
    two-site entries (each site carries only its own fact, so "all facts in every
    site" is unsatisfiable), but on its own it lets an entry keep STATING a fact
    whose ground the code lost, as long as one other fact still matches.
    """
    return [fact for fact in facts if fact not in text]


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
#   * **The relation is PERMISSIVE, so it can miss a bad door; and it can INVENT
#     one hop through a repeated identifier.** Resolution is by name, so a hop
#     resolves a bare name to EVERY symbol of that name: a call through a computed
#     attribute (`getattr(obj, name)`) is invisible (permissive, the harmless
#     direction), and a common name — an attribute (`list`, `.delete`) or a
#     builtin — makes two unrelated sites adjacent (measured: `MCP tortoise_search`
#     reaches `hosted_backup.py::BackupStorage.list` in 2 hops, the last of which is
#     nothing but the repeated name `list`). Function-local ASSIGNMENT targets no longer do:
#     since the `#9817` fix they are not symbol definitions at all (see
#     `_symbol_index`), and that class was the one that produced a WRONG attribution
#     rather than an extra hop. ⚠️ The NAME of a nested `def`/`class` is still a
#     symbol — what the local-class rule drops is its BODY's assignments, and the
#     class node itself stays (both directions are pinned in
#     `test_a_local_class_body_is_not_a_source_of_symbols`) — so this residual
#     is about local VARIABLES, not about every local definition.
#     The budget and the pinned count are what keep the residue small; the arm
#     still does not prove a call graph.
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
#     measured against substitutions that keep a REAL tool in the cell, it accepted
#     the largest share of the (row, table-door) pairs, and three such substitutions
#     are refused at a uniform 3 (rows 22/24's reach cells swapped, rows 28-31's
#     backup doors swapped, and row 28's verb changed to the sibling `GET` that dumps
#     nothing) — but at `3f2f48932` only TWO of them discriminate 5 from 3: the
#     backup swap is already refused at 5, because row 31's swapped cell is 7 hops
#     (and under the shipped predicate row 29's is 5 against that reader's 4-hop
#     exception) — the per-door figures are in the `_DOOR_REACH_HOPS` note below.
#     ⚠️ **The 5-versus-3 comparison above is made at THAT commit, against its own
#     pre-`#9817` index**: under the current index all three are refused even at a
#     uniform 5 (the four substituted backup cells are 11, 12, `None`, `None`), so a
#     lane that re-measures at HEAD and finds no discrimination there has not
#     refuted it.
#     The SHIPPED predicate — the exceptions in `_DISTANCE_EXCEPTIONS`
#     included — accepts the COUNT the arm below pins (`_ACCEPTED_PAIRS_AT_THIS_HEAD`
#     over the derived population), because a figure restated in prose rots: this
#     sentence still read 63/340 = 18.5 % after the table grew.
#     Depth still trades both ways — at a UNIFORM 2 hops nine legitimate rows
#     false-red (seven if the `_DISTANCE_EXCEPTIONS` budgets are honoured, i.e.
#     only the global budget is lowered) — and the only two true attributions
#     beyond 3 are named in `_DISTANCE_EXCEPTIONS` with the code fact
#     that earns each. ⚠️ This is why the bound is pinned on BOTH sides: the
#     acceptance count is itself an arm
#     (`test_the_attribution_predicate_does_not_swallow_the_whole_table`), so the
#     predicate cannot quietly go permissive again.
#   * **Falsifiability is a SEPARATE arm**
#     (`test_the_door_attribution_refuses_a_door_that_reaches_another_reader`),
#     not a comment here: it runs the live registered tools through this same
#     predicate, so the gap cannot silently re-open as "latent".

#: How many reference hops a door→reader attribution may take. See the residual
#: note above: this is a BOUND, not a proof.
#:
#: ⚠️ **Three, and this is a BOUND, not a proof.** Re-measured 2026-10-24 at
#: `d07c9e99e`, with function-local ASSIGNMENT targets excluded from the symbol
#: index (see `_symbol_index`; the NAME of a nested `def`/`class` is still indexed)
#: and the per-reader exceptions DISABLED, so these are the PURE
#: relation's figures over the 418 pairs: a uniform 2 accepts 24, a uniform 3 34,
#: a uniform 4 43, a uniform 5 52. The SHIPPED predicate — the two exceptions in
#: `_DISTANCE_EXCEPTIONS` added — accepts 36, i.e. the exceptions are worth exactly
#: the 34 -> 36 above, stated separately so neither figure hides the other. Every
#: one of the 31 true attributions is within 3 hops except the two named in
#: `_DISTANCE_EXCEPTIONS`. (Counts, not percentages, on purpose: this file's own
#: rule is that a rate restated against a fixed denominator rots on the next table
#: growth — a sentence two screens up still read `63/340 = 18.5 %` after the table
#: grew. The denominator is 19 rows x 22 doors at this head.)
#: ⚠️ **The figures an earlier wording here carried were re-measured, not merely
#: re-labelled.** It reported `16.5 %` for a uniform 5 and `15.6 %` for a uniform 3,
#: and justified the tighter number with three substitutions it said stayed green
#: at 5 and red at 3 (rows 22/24's reach cells swapped, rows 28-31's backup doors
#: swapped, row 28's verb changed). At `3f2f48932` — the commit that wrote that
#: sentence — the figures reproduce **against that commit's own, still-defective index** (it
#: had no `in_function` flag at all, so function locals were symbols): uniform
#: 2/3/4/5 = 26/59/90/170 of 418 and the shipped predicate 69, which is why
#: `16.5 %` (69/418) reproduces there and `15.6 %` reproduces on NO index (a
#: uniform 3 was 59/418 = 14.1 %, and the uniform-5 rate was 170/418, which is
#: the "170 of the 418 (row, table-door) pairs" the arm BELOW states). ⚠️ The
#: index matters: the CURRENT sweep above, locals excluded, applied to that same
#: tree measures 24/34/42/52 and a shipped 36 — the difference is the defect,
#: not the tree.
#: The DISCRIMINATION the old wording claimed was only PARTLY true there, and the
#: per-DOOR distances are what show it, not a per-cell minimum. Measured there, per
#: door and per CELL — a swapped DOOR changes every cell that carries it, so the
#: backup swap is FOUR cells (rows 28/29 carry `POST /v1/backups`, rows 30/31 its
#: `restore` sibling), not the two the old wording named: row 24's reader via row
#: 22's two doors 5, 5; row 22's reader via row 24's three doors 3, 4, 4; rows 28/29
#: via `POST /v1/backups/restore` 5, 5; rows 30/31 via `POST /v1/backups` 5, 7; row
#: 28's verb `GET` 5. **Every one of those cells is refused at a uniform 3** —
#: row 22's because two of its three substituted doors sit at 4, since the arm
#: refuses a CELL when ANY of its doors exceeds the budget (so the leading `3` is
#: one green door inside a refused cell, never a green cell). ⚠️ **So only two of
#: the three substitutions discriminate 5 from 3**: at a uniform 5 the backup swap
#: is ALREADY refused, on row 31's 7-hop cell (and, with the exceptions on, on row
#: 29's 5-hop cell against that reader's 4-hop budget) — its green cells are rows
#: 28/29/30 only. What changed is that the local-symbol fix removes the collisions
#: those substitutions were exploiting: under the current index all three are
#: REFUSED even at a uniform 5.
#: The budget stays at 3 — it is a bound, and nothing here depends on the old
#: rates.
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

#: How far the search itself may walk. Above every BUDGET in play (3, plus the
#: two exceptions whose budgets are 4 and 5) — which is NOT the same as above
#: every CHAIN, and the difference is measured, not assumed: at this head
#: `MCP tortoise_search` reaches `hosted_backup.py::_independent_counts` and
#: `_DUMP_EDGE_PAGE_QUERY` in 13 hops and `_COUNT_ALL_EDGES_QUERY` in 14, so
#: `_door_distance` returns `None` for them at this cap. **`None` therefore means
#: "not found within `_DISTANCE_SEARCH_CAP` hops", never "unreachable"**, and
#: every message that consumes the value says which of the two it is — a short
#: cap reports a long chain as no chain at all, so a caller must not read `None`
#: as proof of absence.
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

    ⚠️ **A LOCAL variable is NOT a symbol, and indexing one INVENTS reach.** The
    indexer used to register every `x = …` it walked past, function bodies
    included, so a local `ids`/`edges`/`result` in one module was a definition
    node that a same-named local ANYWHERE else resolved to — a hop through
    nothing but a repeated identifier. Measured on this tree before the rule was
    added: `MCP tortoise_search` acquired an attribution to
    `hosted_backup.py::_COUNT_ALL_EDGES_QUERY` over the chain
    `tortoise_fts_query` → `_review_prune.ids` →
    `hosted_backup._independent_counts.edges` → the constant, where the two
    middle hops are the locals `ids` and `edges` in functions that have nothing
    to do with each other (`#9720` added the `ids` local, which shortened the
    same collision from 5 hops to 4 and took the accepted count from 69 pairs
    to 70). The relation's own note now states what the code enforces: a
    repeated ATTRIBUTE or builtin name can still buy a hop (`list`, `.delete`),
    while a repeated function-local VARIABLE target no longer resolves (the NAME
    of a nested `def`/`class` still does — the residual note above declares that
    remaining permissive direction). The `#9817`
    local-VARIABLE defect was the class that produced a WRONG attribution rather
    than an extra hop, so those definition nodes are excluded here and the
    negative probe for that exact pair lives in
    `test_the_door_attribution_refuses_a_door_that_reaches_another_reader`.
    A local CLASS body is a weaker case, measured separately: it supplies extra
    hop CANDIDATES and moves no accepted pair at all — which is why it needed a
    pin of its own (`test_a_local_class_body_is_not_a_source_of_symbols`)
    rather than resting on the two ceilings. A class whose body is walked from a
    function carries `in_function` through unchanged.
    Values of a local assignment are still REACHED from the enclosing function
    (`mentions` walks every `Assign` value), so dropping the target nodes loses
    no real reference.
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

    def index(body, rel: str, prefix: str, *, in_function: bool = False) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                node_id = f"{rel}::{qual}"
                defs.setdefault(node.name, set()).add(node_id)
                refs[node_id] = mentions(node)
                index(node.body, rel, f"{qual}.", in_function=True)
            elif isinstance(node, ast.ClassDef):
                defs.setdefault(node.name, set()).add(f"{rel}::{prefix}{node.name}")
                # A class BODY still declares symbols (`Class.ATTR` constants) —
                # the class ITSELF is always a symbol (a nested `def`/`class` is
                # indexed whether or not it is inside a function), but a class
                # local to a function has a local BODY, so only its `Assign`
                # targets are dropped and the flag carries through unchanged (see
                # `test_a_local_class_body_is_not_a_source_of_symbols`).
                index(node.body, rel, f"{prefix}{node.name}.", in_function=in_function)
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and node.value is not None
                and not in_function
            ):
                # Module- and class-level only: a function-local target is not a
                # symbol (see the docstring). Nested `def`s above are still
                # indexed either way.
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
    """Shortest reference distance from `door` to `reader`; `None` = NOT FOUND
    within `_DISTANCE_SEARCH_CAP` hops.

    ⚠️ `None` is a TRUNCATION, not a proof of absence — a chain longer than the
    cap reports `None` exactly like one that does not exist (measured at this
    head: `_COUNT_ALL_EDGES_QUERY` is 14 hops against a cap of 12), which is why
    the two are never conflated in a message.

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
    # ⚠️ This door needs the SAME non-vacuity guard as `MCP tortoise_search`
    # below: `_door_entry_ids` yields `frozenset()` for a handler that no longer
    # resolves, `_door_distance` then returns `None`, and every `not
    # _door_reaches(...)` in the loop would pass while testing nothing.
    assert _door_entry_ids("MCP tortoise_suggest_entry_points"), (
        "this probe's door does not resolve, so the `not _door_reaches(...)` loop "
        "below would be vacuous — a door that is not in the index never reaches "
        "anything (measured: a renamed tool yields frozenset() and refuses every "
        "reader)"
    )
    for wrong in (
        "mcp_server.py::_preview_supersede",
        "sdk.py::TortoiseSDK.belief_timeline",
    ):
        # The READER needs the guard too, not just the door: a reader id that no
        # longer resolves is absent from the index, `_door_distance` returns
        # `None`, and the assertion below passes while testing NOTHING — the same
        # vacuity the door guard above closes, one operand over.
        assert wrong in _symbol_index()[1], (
            f"the probe reader `{wrong}` is not a node in the symbol index, so the "
            "`not _door_reaches(...)` assertion below would pass on a reader that "
            "cannot be reached because it does not exist"
        )
        assert not _door_reaches("MCP tortoise_suggest_entry_points", wrong), (
            f"MCP `tortoise_suggest_entry_points` is attributed to `{wrong}`, which it "
            "does not reach — the door→reader predicate answers True too freely, so "
            "`test_every_customer_callable_row_reaches_a_declared_door` would pass a "
            "substituted door."
        )

    # ⚠️ A chain built out of a repeated FUNCTION-LOCAL name is not reach, and
    # this pair is the measured case: `MCP tortoise_search` was attributed to row
    # 29's backup count constant over `tortoise_fts_query` → `_review_prune.ids` →
    # `_independent_counts.edges` → the constant — hops two and three being the
    # locals `ids` and `edges` in unrelated functions, because the indexer used to
    # register function-local assignments as symbols. None of the dumps' own symbols
    # is inside this door's budget and that stays so — measured, `dump_graph` 12 hops
    # and `create_backup` 11 are far past that budget and still INSIDE the cap (so
    # `_door_distance` returns their distance and the pair is refused on the budget
    # alone), `_independent_counts` and
    # `_DUMP_EDGE_PAGE_QUERY` are 13 and `_COUNT_ALL_EDGES_QUERY` 14 (those three are
    # beyond the 12-hop `_DISTANCE_SEARCH_CAP`, so `_door_distance` reports `None` for
    # them — "further than we looked", not "unreachable"), and `restore_graph` /
    # `_restore_copy_settled` are unreachable AT CAP 24 (the cap was raised for
    # that measurement) — "unreachable" is not claimed for caps beyond it. (Other
    # `hosted_backup.py`
    # symbols ARE reachable at 2-3 hops through ATTRIBUTE names — `list`, `delete` —
    # which is the relation's declared permissive direction: a repeated identifier
    # can buy a hop, so only the pair the door is FOR is pinned here. If it reds,
    # either a local is a symbol again or the search path grew a real edge into the
    # backup's whole-graph count — name which before tightening anything.)
    assert _door_entry_ids("MCP tortoise_search"), (
        "this probe's door does not resolve, so `not _door_reaches(...)` below would be "
        "vacuous — a door that is not in the index never reaches anything (measured: a "
        "renamed tool yields frozenset() and refuses every reader)"
    )
    # The READER needs the same guard as the door: a reader id that no longer
    # resolves is absent from the index, `_door_distance` answers `None`, and the
    # `not _door_reaches(...)` assertion below passes while testing nothing.
    assert "hosted_backup.py::_COUNT_ALL_EDGES_QUERY" in _symbol_index()[1], (
        "the probe reader `hosted_backup.py::_COUNT_ALL_EDGES_QUERY` is not a node in "
        "the symbol index, so the assertion below would pass on a reader that cannot "
        "be reached because it no longer exists — re-point the pin at the renamed "
        "constant rather than letting it go silent"
    )
    assert not _door_reaches("MCP tortoise_search", "hosted_backup.py::_COUNT_ALL_EDGES_QUERY"), (
        "the search door is attributed to `hosted_backup.py::_COUNT_ALL_EDGES_QUERY` "
        "INSIDE this reader's 4-hop budget. At the pinned head that pair is 14 hops "
        "— past the 12-hop search cap, so `_door_distance` answers `None`, and far "
        "past the budget — so this arm reds only once the pair has come INSIDE the "
        "budget: the attribution then walked a bare NAME (`ids`/`edges`) through a "
        "function-LOCAL assignment, so any file that reuses a common variable name "
        "becomes reachable from any door. Locals must not be symbol definitions "
        "(`_symbol_index`)."
    )


def test_a_local_class_body_is_not_a_source_of_symbols():
    """The `in_function` flag reaches a class BODY, and only the local one is dropped.

    ⚠️ The class NAME is NOT what this arm forbids: a class defined inside a
    function remains a symbol (pinned below), which is why the arm is named for the
    BODY — what must not be indexed is that body's assignments.

    `_symbol_index`'s `ClassDef` branch used to hard-code `in_function=False`, so a
    class defined INSIDE a function had its own body indexed as if it were
    module-level: `index_walk.py::_getmntinfo_table._StatFS._fields_` and
    `projection/__init__.py::journal_epistemic_existence._Node.__slots__` were
    registered under the BARE names `_fields_` / `__slots__`, so a same-named
    assignment anywhere resolved to them — the same defect class as #9817's local
    variables, one nesting level down. The flag now propagates.

    ⚠️ **Why this needs its own arm.** Nothing else in the file can catch that
    branch: the two ceilings (`_ACCEPTED_PAIRS_AT_THIS_HEAD`,
    `_WORST_ROW_AT_THIS_HEAD`) are `<=` and this change moves NO pair (measured —
    the accepted total is unchanged), so reverting `in_function=in_function` to
    `in_function=False` leaves every other arm green. Both directions are pinned
    here: the local class body is gone, AND a module-level `Class.ATTR` constant
    is still indexed, so the arm cannot be satisfied by deleting the whole
    `ClassDef` branch either.
    """
    defs, _ = _symbol_index()

    # A module-level `Enum`'s member is an `Assign` with a value at
    # `in_function=False` — it must SURVIVE (dropping class bodies wholesale would
    # also make §13's constant rows unattributable).
    assert "alert_store.py::ResolveOutcome.RESOLVED" in defs.get("RESOLVED", set()), (
        "the module-level `Class.ATTR` constant `ResolveOutcome.RESOLVED` is no "
        "longer a symbol — the `ClassDef` branch must still index a class body "
        "whose class is NOT local. Present in `_symbol_index`'s output: "
        f"{sorted(defs.get('RESOLVED', ()))}. If `ResolveOutcome` moved, re-point "
        "this pin rather than deleting it — it is the positive half of the pair."
    )

    # …and the body of a class local to a FUNCTION must not be. ⚠️ Node-id precise,
    # like the `__slots__` arm below and for the same reason: the NAME may also be
    # defined at module level (as `__slots__` routinely is), and a whole-tree
    # `not defs.get(name)` would then red on a legitimate module-level definition
    # while blaming `in_function` propagation.
    # ⚠️ The SUBJECT's own locality is pinned first: hoisting `_StatFS` to module
    # scope is behaviour-preserving and would move the offending id to
    # `index_walk.py::_StatFS._fields_`, leaving the arm below GREEN while it no
    # longer exercises the local-class rule at all. Pinned here so a hoist reds as
    # "re-point this pin", not as silence or as a false `in_function` diagnosis.
    assert "index_walk.py::_getmntinfo_table._StatFS" in defs.get("_StatFS", set()), (
        "this pin's SUBJECT has moved: `_StatFS` is no longer a class defined inside "
        "`index_walk._getmntinfo_table` (present nodes: "
        f"{sorted(defs.get('_StatFS', ()))}). If it was hoisted to module scope, a "
        "module-level `Class.ATTR` is legitimate and the `_fields_` arm below no "
        "longer tests the local-class rule — re-point both at another class that is "
        "still defined inside a function (or delete the pair, and the coverage with "
        "it), rather than loosening them."
    )
    # ⚠️ The ATTRIBUTE is pinned PRESENT from source before it is pinned ABSENT
    # from the index. Without this half, renaming `_fields_` in `index_walk.py`
    # makes the next assertion read `assert <id> not in set()` — green, and testing
    # nothing — which is the same silence the SUBJECT pin above refuses for a
    # hoist. A rename must red as "re-point this pin".
    local_targets = _class_body_targets("index_walk.py", "_getmntinfo_table", "_StatFS")
    assert "_fields_" in local_targets, (
        "this pin's ATTRIBUTE has moved: the body of `_StatFS` in "
        "`tortoise/index_walk.py` declares no `_fields_` target (targets present: "
        f"{sorted(local_targets)}), so the absence check below is `not in set()` and "
        "tests nothing. Re-point BOTH halves at the attribute the local class now "
        "declares, rather than deleting the pair and the coverage with it."
    )
    assert "index_walk.py::_getmntinfo_table._StatFS._fields_" not in defs.get("_fields_", set()), (
        "a function-local class body is indexed as a symbol again — "
        "`index_walk.py::_getmntinfo_table._StatFS._fields_` is a definition node, and "
        "`_StatFS` is a class defined INSIDE `_getmntinfo_table`, so `in_function` is "
        "not propagating into the `ClassDef` branch and a local class's attributes "
        "become reachable bare names (`_symbol_index`). `_fields_` now resolves to "
        f"{sorted(defs.get('_fields_', ()))}. (If the id reads `index_walk.py::_StatFS."
        "_fields_` instead, the class was HOISTED — re-point the pin above rather "
        "than treating it as a propagation defect.)"
    )
    # The `__slots__` half gets the same PRESENT-from-source guard as `_fields_`:
    # a rename would leave the absence check below asserting `not in set()`.
    node_targets = _class_body_targets(
        "projection/__init__.py", "journal_epistemic_existence", "_Node"
    )
    assert "__slots__" in node_targets, (
        "this pin's ATTRIBUTE has moved: the body of `_Node` in "
        "`tortoise/projection/__init__.py` declares no `__slots__` target (targets "
        f"present: {sorted(node_targets)}), so the absence check below is "
        "`not in set()` and tests nothing. Re-point BOTH halves at the attribute "
        "the local class now declares, rather than deleting the pair."
    )
    assert "projection/__init__.py::journal_epistemic_existence._Node.__slots__" not in defs.get(
        "__slots__", set()
    ), (
        "the function-local `_Node` class body is indexed again — "
        "`journal_epistemic_existence._Node.__slots__` is a definition node, so a "
        "bare `__slots__` anywhere resolves to it (`in_function` must carry into "
        "the `ClassDef` branch with its value UNCHANGED). If the id reads "
        "`projection/__init__.py::_Node.__slots__` instead, `_Node` was HOISTED out "
        "of `journal_epistemic_existence` — re-point this pin at a class that is "
        "still function-local rather than treating a legitimate module-level "
        "`__slots__` as a propagation defect."
    )
    # The class ITSELF is still a symbol — only its body's assignments are dropped.
    # Stated so the arm above cannot be greened by dropping local nested defs too.
    assert "projection/__init__.py::journal_epistemic_existence._Node" in defs.get("_Node", set()), (
        "the local `_Node` CLASS is no longer a symbol — a nested `def`/`class` "
        "is still indexed whether or not it is inside a function; only its body's "
        "`Assign` targets are local. Present: "
        f"{sorted(defs.get('_Node', ()))}"
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
#: ⛔ RE-DERIVED 2026-10-24: 69 -> 36, and the FALL is the whole point — the 70th
#: pair was REFUSED, not pinned. `_symbol_index` was registering a function-LOCAL
#: assignment as a symbol definition, so a hop could resolve the bare name to a
#: local in an unrelated function. The 70th pair was exactly that:
#: `MCP tortoise_search` -> row 29's `hosted_backup.py::_COUNT_ALL_EDGES_QUERY`
#: over `tortoise_fts_query` ->(ids)-> `_review_prune.ids` ->(edges)->
#: `_independent_counts.edges` -> the constant, where hops two and three are the
#: locals `ids` and `edges` in functions that share no reference at all (`#9720`
#: added the `ids` local, which shortened that same collision from 5 hops to 4 and
#: took the count 69 -> 70; the pair was never reach). Locals are not symbols any
#: more, and re-measuring the corpus shows how much of the 70 was the defect:
#: **34 of the 70 pairs the predicate accepted at that head were collision chains**
#: (33 of the pinned 69, plus the 70th itself), so the honest figure is 36. Every
#: row's OWN declared door still attributes, the three measured substitutions
#: still red, and the 31-cell floor still holds — which is what makes this a
#: tightening, not a relaxation. The refused pair is pinned as a negative probe in
#: `test_the_door_attribution_refuses_a_door_that_reaches_another_reader`.
_ACCEPTED_PAIRS_AT_THIS_HEAD = 36
#: 15 -> 17 on 2026-10-21: the TABLE's door set grew by the two SDK doors the new
#: call-time rows name, and `_hop_query` (an all-edge walk) legitimately reaches
#: more of a larger set. It is the same relation over a larger population, not a
#: looser predicate — no door was added to any row's own reach cell.
#: ⛔ 17 -> 4 on 2026-10-24, with the pair count above and for the same reason: a
#: row's accepted set was measured over collision hops as well as references, so
#: the old 17 counted the defect too. Rows 2 and 17 now lead at 4 accepted doors.
_WORST_ROW_AT_THIS_HEAD = 4


def test_the_attribution_predicate_does_not_swallow_the_whole_table():
    """The bound above is only a check if the predicate still DISCRIMINATES.

    The REJECTED `_DOOR_REACH_HOPS = 5` accepted 170 of the 418 (row, table-door)
    pairs — and the population is 418 pairs at BOTH commits, so the difference
    from today's 52 is the INDEX, not the table — measured against the
    PRE-`#9817` index that stood when it was rejected (`3f2f48932`, the commit
    that wrote the old wording). At THAT index two of the three substitutions
    that keep a REAL tool in a cell stayed green under it: the backup-doors swap
    was ALREADY refused at 5, on row 31's 7-hop cell (under the CURRENT index
    all three are refused even at 5; the per-door figures are in the
    `_DOOR_REACH_HOPS` note). The figure is a COUNT over that population, not a
    rate, for the reason the `_DOOR_REACH_HOPS` note gives: a rate restated
    against a fixed denominator rots on the next table growth (the `63/340`
    precedent). A bound is not a proof, so the acceptance COUNT is
    pinned as its own number: the predicate may accept, over the table's own
    doors, at most `_ACCEPTED_PAIRS_AT_THIS_HEAD` pairs and at most
    `_WORST_ROW_AT_THIS_HEAD` for any single row. A predicate that starts
    accepting everything moves these and reds here — which is the tripwire the
    residual note describes in prose, expressed as an arm for the same reason
    (16) gives about the fragment census.

    ⚠️ **Both figures are MEASURED, and neither is to be nudged.** A RISE means
    the relation loosened (fix the relation, not the ceiling); a FALL means it
    tightened, and the new figure should be re-derived and the reason recorded.
    ⛔ **The 2026-10-24 red is the precedent, and it was a RISE that was REFUSED.**
    The pin read 69 and measured 70; the 70th pair was traced (the arm's own
    enumeration, diffed against the previous head) to `MCP tortoise_search` ->
    `hosted_backup.py::_COUNT_ALL_EDGES_QUERY`, a chain of two bare-name hops
    through function LOCALS in unrelated functions — not reach. The relation was
    tightened, the pair became a negative probe, and the re-derivation that
    followed found 34 of the 70 accepted pairs were the same defect (33 of the
    pinned 69, plus the 70th), so the pin FELL to 36. A
    ceiling raised past a pair nobody can name is the failure this arm exists for;
    a ceiling LOWERED with the diff recorded is the arm working.
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
                if distance is None:
                    shortfall = (
                        f"is not found within the {_DISTANCE_SEARCH_CAP}-hop search cap. "
                        "This arm cannot tell 'no chain' from 'a chain longer than it "
                        "looked', so the fix is to raise `_DISTANCE_SEARCH_CAP` (and "
                        "re-derive the pinned figures) or to shorten the chain. ⛔ A "
                        "`_DISTANCE_EXCEPTIONS` entry cannot help: it raises the "
                        "reader's BUDGET, and the search stops at the cap before any "
                        "budget is consulted. So this door is UNVERIFIED here, not "
                        "wrong: it may be the right door on a chain longer than the cap."
                    )
                else:
                    shortfall = (
                        f"needs {distance} references and this row's budget is {hops}, so "
                        "it is not a door to THIS reader (if the chain is legitimately "
                        "longer now, give the reader an entry in `_DISTANCE_EXCEPTIONS` "
                        "with the reason, rather than loosening the budget for every row). "
                        "The blast-radius count would be counted from the wrong tool."
                    )
                broken.append(f"{label} — {door} exists but {shortfall}")

    # A predicate that silently stops resolving its entries turns this arm green
    # on an assertion it never made — so the floor is asserted, not assumed.
    #
    # The floor is DERIVED from the table rather than typed: a typed numeral is a second
    # hand-count of the thing the table already says, and it reds the moment a row is
    # added or removed for a reason that has nothing to do with this arm (the #7948
    # table lost its SDK traversal row when that SDK method was deleted, 31 -> 30).
    # DERIVED and EQUALITY, not `>=`: an arm that over-counts is as broken as one that
    # silently stops resolving, and `>=` cannot see the over-count.
    expected = sum(
        len(_doors(str(row["reach"])))
        for row in _rows()
        if _reach(row) == "customer-callable"
    )
    assert expected, "no customer-callable row names a door — the arm is vacuous"
    assert attributed == expected, (
        f"{attributed} door attributions were attempted and §13's "
        f"customer-callable cells name {expected} door OCCURRENCES — any gap lets a "
        f"dead door KIND parser (every SDK door, say) pass this arm silently"
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
    # "Outside this eighteen, ..." to stop a re-verifier counting those five inside the
    # enumeration. The numeral in it is a LOCATOR, not the figure under test: the figure is
    # asserted in (a) from every "N ... customer-callable" statement, so this marker moving
    # with it cannot make a wrong count pass.
    "Outside this eighteen, two are default-ON on the ingestion path",
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
    must be covered by the entry's facts.

    ⚠️ **THREE nets, because each covers a blind spot the others cannot.** Bound
    per statement alone, and per SITE alone, both still failed:

    * the SITE COUNT (`sites` fifth element) catches a walk added on its own line —
      a copy at the same `(symbol, line)` produces no new site, so the number cannot
      move, and that gap shipped a second walk on the first walk's fact;
    * the WALK net (`_unbounded_walks`) checks every walk OCCURRENCE of the site's
      RESOLVED query against a fact that sits in the walk's OWN window
      (`_walk_windows`: a `UNION` arm or a top-level comma ends a window) AND that
      NAMES the walk's own relationship binding (`_walk_binding`), so a walk
      appended INSIDE an already-registered query string has to carry a fact of
      its own — the `UNION MATCH (x)-[rr]->(y)` copy of `sdk.provenance` reds
      here, where the number stayed quiet, and so do the two escapes the earlier
      per-CLAUSE form left open (a comma-separated pattern beside the registered
      walk, and a `UNION` arm that reuses a variable name from the first arm).
      ⚠️ Requiring only the WINDOW was itself measured open — `MATCH (x)-[rr]->(y)`
      appended with no boundary in front of it, a chained hop
      `-[r]->(n)-[rr]->(y)`, and a comprehension in the walk's `WHERE` all stayed
      inside the registered walk's window and shipped green — which is why the
      fact must NAME the walk and not merely sit beside it. ⚠️ And a NAME is only
      an identity while a scope CARRIES it: `MATCH …-[r]->(n) WITH n MATCH
      (x)-[r]->(y)` re-binds `r` after a `WITH` dropped it, so it is a FRESH walk
      and the fact about the first one reds as a ground for it
      (`_scope_survives`; `WITH *` and a projecting `WITH r` stay green);
    * the UNION net (`_uncarried_facts`) requires every recorded FACT to appear in
      some site, which is the direction an any-fact-per-site loop cannot express
      and which is what a retyped `_DEGREE` anchor (`(old:Source …)` → `(old)`)
      needs in order not to hide behind the fact that still matches.

    The facts are asserted as plain substrings of the site's statement plus the
    module-level constants it names, over source with COMMENTS and PROSE STRINGS
    blanked (`_site_fact_text`): a fact satisfied by a comment is not a fact about
    executed code. The clause net reads the resolved query instead
    (`_site_query_texts`), because that is the text the walk actually runs, and it
    bounds each walk OCCURRENCE by a fact inside its own window AND naming its own
    binding — both coarser units (statement, clause) and the window-alone form
    were measured to let a second walk ride on the first walk's fact.
    """
    census = _call_time_census()
    assert census.EMPTY_PATTERN, (
        "the `EMPTY_PATTERN` register is empty — this arm would then assert "
        "nothing, which is how an unread prose reason ships green"
    )
    derived = census._empty_pattern_sites()
    files = _file_by_rel()
    declared_sites = sum(entry[4] for entry in census.EMPTY_PATTERN)
    derived_sites = sum(
        len(derived.get((rel, symbol), ()))
        for rel, symbol, *_rest in census.EMPTY_PATTERN
    )
    assert derived_sites == declared_sites, (
        f"the register declares {declared_sites} site(s) and the derivation finds "
        f"{derived_sites} under those keys — a site was added beside the "
        "registered ones (or went missing), and the entry cannot vouch for a walk "
        "it was not written about. This number is asserted BEFORE anything is "
        "appended to the failure list, because a count checked after the fact is "
        "a count that can never fire"
    )
    walks_checked = 0
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
        file = files[rel]
        source_texts: list[str] = []
        entry_walks = 0
        for line in lines:
            source_texts.append(_site_fact_text(rel, line))
            for query in _site_query_texts(file, symbol, line):
                windows = _walk_windows(query)
                entry_walks += len(windows)
                walks_checked += len(windows)
                for window, binding in _unbounded_walks(query, facts):
                    failures.append(
                        f"  - {key}:{line} walks `{binding}` in the window "
                        f"`{window[:90]}` and NO stated fact bounds THAT walk — "
                        "a second walk written into an already-registered query "
                        "string (another `UNION` arm, a comma-separated pattern, "
                        "a chained hop, a comprehension in its `WHERE`, or a "
                        "re-binding after a `WITH` that dropped the name) is a "
                        "new reader of the edge, and the fact that bounds the "
                        "walk beside it does not NAME this one"
                    )
        if not entry_walks:
            failures.append(
                f"  - {key} derives site(s) at {list(lines)} and the clause net "
                "finds no walk in any of their queries — the reason is then "
                "checked against text that carries no walk at all, which is how a "
                "bound can be asserted about nothing"
            )
        for fact in _uncarried_facts(facts, "\n".join(source_texts)):
            failures.append(
                f"  - {key} states `{fact}` and NO site of the entry carries it — "
                "the ground it records is gone from the code, and an any-fact "
                "check hides that behind whichever fact still matches"
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
    assert walks_checked >= len(census.EMPTY_PATTERN), (
        f"the walk net examined {walks_checked} walk occurrence(s) over "
        f"{len(census.EMPTY_PATTERN)} entries — an entry whose walk the net "
        "cannot see is an entry whose reason nothing checks, so this floor is "
        "what keeps the per-clause half from going silent"
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


def test_a_walk_inside_an_already_registered_statement_is_unbounded():
    """A second walk in the SAME query string is a second WALK, not the same site.

    ⚠️ **Measured on this head before the clause net existed.** The derivation keys
    a site on `(symbol, line)`, so a walk appended INSIDE an already-registered
    query string — `sdk.provenance`'s
    `"… properties(n) " "UNION MATCH (x)-[rr]->(y) RETURN type(rr), …"`, which
    `_resolve` folds into ONE candidate at ONE line — produces no new site. The
    site's count therefore does not move, and a fact found ANYWHERE in the
    statement excused it: a genuine new reader of `aboutObject` (untyped, so it
    walks `aboutObject` and `RETURN type(rr)` reports it) shipped green, where
    `origin/main`'s clause-scoped guard reded.

    Two halves, because the hole is the gap between them: the derivation must
    report ONE site (that is why the count net cannot see the copy), and the
    clause net must report TWO walk-bearing clauses with the second one unbounded.
    """
    census = _call_time_census()
    facts = ("MATCH (s:Subject {id:$sid})-[r]->(n)",)
    query = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], "
        "properties(n) UNION MATCH (x)-[rr]->(y) RETURN type(rr), labels(y)[0], "
        "properties(y)"
    )
    walks = _walk_windows(query)
    assert len(walks) == 2, (
        f"the walk net found {len(walks)} walk occurrence(s) in a query that "
        "carries two — a walk added beside a registered one inside ONE query "
        "string would then be invisible, which is the fail-open this net closes"
    )
    assert any(fact in walks[0][0] for fact in facts), (
        f"the FIRST walk is not the one the fact bounds: {walks[0]!r}"
    )
    unbounded = _unbounded_walks(query, facts)
    assert unbounded == [walks[1]], (
        f"the appended walk is not reported as unbounded ({unbounded!r}) — the "
        "fact written about the first walk is vouching for it again"
    )
    # The other half: the SITE COUNT cannot see this copy, so the clause net is
    # the only thing that can. Asserted through the real derivation on a synthetic
    # module, not by reading the site key.
    source = (
        "def provenance(query):\n"
        "    box = (\n"
        '        "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r) "\n'
        '        "UNION MATCH (x)-[rr]->(y) RETURN type(rr)"\n'
        "    )\n"
        "    return query(box)\n"
    )
    file = census._File(Path("synthetic_same_statement.py"), "synthetic.py", source)
    sites = census._empty_pattern_sites_in(file)
    assert sites == {("provenance", 3)}, (
        f"the same-statement copy produced {sorted(sites)} — if it produced TWO "
        "sites the COUNT would catch it and the clause net's premise would be "
        "wrong; if it produced none this fixture is measuring nothing"
    )
    derived_queries = _site_query_texts(file, "provenance", 3)
    assert derived_queries, (
        "`_site_query_texts` resolved no query for a site the derivation reports — "
        "the clause net would then check no walk at all"
    )
    assert all(_unbounded_walks(q, facts) for q in derived_queries), (
        f"the appended walk is not unbounded through the site's RESOLVED query "
        f"({derived_queries!r}) — the net is reading source text, or the wrong line"
    )


def test_the_walk_window_ends_at_a_union_and_at_a_comma():
    """The two escapes the per-CLAUSE form left open, each a live mutation shape.

    ⚠️ **Measured on the per-CLAUSE cut, and BOTH shipped green there.** The clause
    net segmented only at the census's `_CLAUSE` keyword, and let a fact in an
    EARLIER clause count when that clause bound a variable the walk's text used.
    The two shapes below are what that rule could not see:

    * (a) a second pattern in the SAME `MATCH` — `, (x)-[rr]->(y)` introduces no
      clause keyword, so no new segment was produced, and the whole segment carried
      the registered walk's fact;
    * (b) a `UNION` arm that REUSES a variable name from the first arm — the
      first arm's `… RETURN type(r), labels(n)[0] …` re-mentions `n` and `r`, so the
      overlap the escape tested was non-empty even though the second arm binds its
      own `n` and matches its walk independently.

    Each is asserted as a WINDOW (two windows, the second unbounded) AND through the
    real derivation on a synthetic module — the site count cannot see either copy,
    because both land at the same `(symbol, line)` as the registered walk.
    """
    census = _call_time_census()
    facts = ("MATCH (s:Subject {id:$sid})-[r]->(n)",)

    comma = (
        "MATCH (s:Subject {id:$sid})-[r]->(n), (x)-[rr]->(y) "
        "RETURN type(r), labels(n)[0], type(rr)"
    )
    windows = _walk_windows(comma)
    assert len(windows) == 2, (
        f"the second pattern of one `MATCH` produced {len(windows)} window(s) — "
        "it is a separate walk and the fact about its neighbour does not bound it"
    )
    assert any(fact in windows[0][0] for fact in facts), (
        f"the registered walk is not the first window: {windows[0]!r}"
    )
    assert not any(fact in windows[1][0] for fact in facts), (
        f"the comma-separated walk's window carries the neighbour's fact: "
        f"{windows[1]!r} — the window boundary is not a top-level comma"
    )
    assert _unbounded_walks(comma, facts) == [windows[1]], (
        f"the comma-separated walk is not reported unbounded "
        f"({_unbounded_walks(comma, facts)!r}) — a second pattern beside a "
        "registered one ships green again"
    )

    union = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], "
        "properties(n) UNION MATCH (n)-[rr]->(m) RETURN type(rr), labels(m)[0], "
        "properties(m)"
    )
    union_windows = _walk_windows(union)
    assert len(union_windows) == 2, (
        f"the second `UNION` arm produced {len(union_windows)} window(s) — an arm "
        "binds its OWN variables, so the first arm's fact cannot bound it"
    )
    unbounded = _unbounded_walks(union, facts)
    assert unbounded == [union_windows[1]], (
        f"the reused-variable `UNION` arm is not reported unbounded "
        f"({unbounded!r}) — a variable name shared with the first arm's `RETURN` "
        "is being read as a bound"
    )

    comprehension = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), "
        "[(x)-[rr]->(y) | type(rr)] AS pairs"
    )
    comp_windows = _walk_windows(comprehension)
    assert len(comp_windows) == 2, (
        f"a comprehension appended to the `RETURN` produced {len(comp_windows)} "
        "window(s) — the census derives comprehensions as walks whatever clause "
        "introduces them, so the net must demand a fact for this one too"
    )
    assert _unbounded_walks(comprehension, facts) == [comp_windows[1]], (
        "the appended comprehension is vouched for by the registered walk's fact: "
        f"{_unbounded_walks(comprehension, facts)!r}"
    )

    # The derivation half: no site-count can see any of these, because the copy
    # lands on the already-registered line. Asserted on a synthetic module through
    # the real derivation and the real resolver.
    source = (
        "def provenance(query):\n"
        "    box = (\n"
        '        "MATCH (s:Subject {id:$sid})-[r]->(n), (x)-[rr]->(y) "\n'
        '        "RETURN type(r), type(rr)"\n'
        "    )\n"
        "    return query(box)\n"
    )
    file = census._File(Path("synthetic_comma.py"), "synthetic.py", source)
    sites = census._empty_pattern_sites_in(file)
    assert sites == {("provenance", 3)}, (
        f"the comma-separated copy produced {sorted(sites)} — if it produced TWO "
        "sites the COUNT would catch it and this fixture's premise would be wrong; "
        "if it produced none the fixture is measuring nothing"
    )
    derived_queries = _site_query_texts(file, "provenance", 3)
    assert derived_queries, (
        "`_site_query_texts` resolved no query for a site the derivation reports — "
        "the walk net would then check no walk at all"
    )
    assert all(_unbounded_walks(q, facts) for q in derived_queries), (
        f"the comma-separated walk is not unbounded through the site's RESOLVED "
        f"query ({derived_queries!r}) — the net is reading source text, or the "
        "wrong line"
    )


def test_a_walk_is_bounded_only_by_a_fact_that_NAMES_IT():
    """A fact bounds the walk it was WRITTEN ABOUT — not its neighbours in a window.

    ⚠️ **Measured on the per-WINDOW cut this arm was added for, and all three
    shipped green there.** A window ends only at a `UNION` or a top-level comma, so
    a second all-edge walk appended with neither boundary in front of it stayed
    inside the registered walk's window and inherited its fact. Applied to
    `tortoise/sdk.py::provenance`'s registered query, each of the three is a
    genuine new reader of `aboutObject` (untyped, so every relationship type —
    `aboutObject` included — is walked):

    * (a) a second `MATCH` CLAUSE — `… -[r]->(n) MATCH (x)-[rr]->(y) RETURN …`;
    * (b) a CHAINED hop on the registered pattern — `… -[r]->(n)-[rr]->(y) …`;
    * (c) a pattern COMPREHENSION in the walk's own `WHERE` —
      `WHERE size([(x)-[rr]-(y) | rr]) > 0`.

    All three shipped green, and every other net stayed quiet too: the site count
    cannot move (the copy is on the registered line, so it is the same
    `(symbol, line)` site), and the census's `24 keys / 36 rows` derivation is
    unchanged. So the fact has to NAME the walk — its own relationship variable —
    and not merely sit beside it.

    ⚠️ **The converse is deliberate, not an oversight:** REUSING a variable name is
    not a second walk. Inside one arm, `MATCH (s)-[r]->(n) MATCH (x)-[r]->(y)` binds
    the SAME relationship `r` again, so it traverses no new type — there is no
    unbound reader to state a ground for. Across a `UNION` the arm binds its OWN
    variables, so a name reused by the second arm is a NEW walk, and the window
    boundary is what reds it (pinned below, and in
    `test_the_walk_window_ends_at_a_union_and_at_a_comma`). Keeping both halves
    asserted is what stops the next reader "fixing" the reuse case into a
    false red on every real query.
    """
    facts = ("MATCH (s:Subject {id:$sid})-[r]->(n)",)
    registered = "MATCH (s:Subject {id:$sid})-[r]->(n)"
    shapes = {
        "a": f"{registered} MATCH (x)-[rr]->(y) RETURN type(r), labels(n)[0], type(rr)",
        "b": "MATCH (s:Subject {id:$sid})-[r]->(n)-[rr]->(y) "
        "RETURN type(r), type(rr)",
        "c": f"{registered} WHERE size([(x)-[rr]-(y) | rr]) > 0 RETURN type(r)",
    }
    for label, query in shapes.items():
        unbounded = _unbounded_walks(query, facts)
        assert len(unbounded) == 1, (
            f"shape ({label}) reports {len(unbounded)} unbounded walk(s) "
            f"({unbounded!r}) — the registered `-[r]->` walk is bounded and the "
            "appended `-[rr]->` walk has to be the one reported. `[]` here is "
            "the fail-open: the appended walk inherits the registered walk's "
            "fact because both sit in ONE window"
        )
        window, binding = unbounded[0]
        assert binding == "rr", (
            f"shape ({label}) reports `{binding}` as the unstated walk — the "
            "binding is not being read off the walk the derivation found, so the "
            "net is comparing facts against the wrong token"
        )
        assert "[rr]" in window, (
            f"shape ({label}) reports a window that does not contain the "
            f"appended walk: {window!r} — the net is vouching for text it never "
            "looked at"
        )
    # The token comparison is an IDENTIFIER comparison, not a substring one: the
    # registered fact mentions `r`, and `r` is a substring of `rr`.
    assert not _names_binding(f"{registered}", "rr"), (
        "a fact stating `-[r]->` is being read as naming `rr` — the binding "
        "match is a substring test, so `MATCH (x)-[rr]->(y)` rides on it again"
    )
    assert not _names_binding("MATCH (x)-[rr]->(y) RETURN type(rr)", "r"), (
        "a fact that states the `-[rr]->` walk is being read as naming `r` — "
        "the match is a SUBSTRING test, so a fact about a neighbour's `rr` walk "
        "would bound every `-[r]->` walk in the same window"
    )
    assert _names_binding(registered, "r"), (
        "a fact stating the registered walk does not name its own variable `r` — "
        "then every live entry is a false red and the check is unusable"
    )
    assert _names_binding("WHERE type(r) IN ['IMPL','NAND']", "r"), (
        "a TYPE-RESTRICTION ground does not name the walk it bounds — the live "
        "`hosted_api._load_commit_graph_state` entry rests on exactly this shape, "
        "so this red would be a false one"
    )
    # The bracket-less spelling binds no name, so its own pattern text is the
    # token: that is what the live `validate_chain_integrity` ground states.
    assert _names_binding("AND NOT (n)--()", "(n)--()"), (
        "a fact that STATES the bracket-less walk does not bound it — the live "
        "`orphaned_draft` entry would be a false red"
    )
    # Derivation half: none of the three shapes produces a new site, which is why
    # the count net cannot see them and the walk net is the only thing that can.
    census = _call_time_census()
    source = (
        "def provenance(query):\n"
        "    box = ("
        '"MATCH (s:Subject {id:$sid})-[r]->(n) MATCH (x)-[rr]->(y) "'
        '"RETURN type(r), type(rr)"'
        ")\n"
        "    return query(box)\n"
    )
    file = census._File(Path("synthetic_boundary_free.py"), "synthetic.py", source)
    sites = census._empty_pattern_sites_in(file)
    assert len(sites) == 1 and next(iter(sites))[0] == "provenance", (
        f"the boundary-free second `MATCH` produced {sorted(sites)} — if the "
        "derivation DOES see it the count would catch it and this fixture's "
        "premise (one `(symbol, line)` site for BOTH walks) is wrong"
    )
    site_line = next(iter(sites))[1]
    derived_queries = _site_query_texts(file, "provenance", site_line)
    assert derived_queries, (
        "`_site_query_texts` resolved no query for the boundary-free site — the "
        "walk net would then check no walk at all"
    )
    assert all(len(_unbounded_walks(q, facts)) == 1 for q in derived_queries), (
        f"the boundary-free walk is vouched for through the site's RESOLVED query "
        f"({derived_queries!r}) — the net is reading source text, or the wrong line"
    )


def test_a_walk_rebinding_a_name_a_with_dropped_is_a_FRESH_walk():
    """A name is an identity only while a SCOPE carries it — `WITH` can end it.

    ⚠️ **Measured on the window-and-binding rule this arm's neighbour installed,
    and it shipped green there.** Both walks sit in ONE window (no `UNION`, no
    top-level comma) and both are spelled `[r]`, so `_walk_binding` returns `r`
    for both and the registered fact — which NAMES `r` — read as a ground for
    the second walk. But `WITH n` did not carry `r`: the name is GONE, the later
    `MATCH (x)-[r]->(y)` binds a FRESH relationship, and `x`/`y` unbound makes it
    a genuinely unconstrained all-edge walk (so `aboutObject` is walked and its
    targets returned). That is the class the register claims is closed, and no
    other net sees it: the copy is on the registered line, so the site count
    cannot move.

    The barrier is scoped, not blanket — the converse shapes below stay green,
    because a standalone `WITH *` and a bare `WITH r` really do keep the same
    relationship, and reddening them would be a false red on every real query.
    What is NOT carrying is a projection that renames, derives or aggregates:
    `WITH r AS rel` renames the name (so the later `[r]` is a NEW relationship),
    `WITH type(r) AS t` projects a string, `WITH r.id AS rid` projects a value,
    and `WITH count(*) AS c` drops every grouping variable — each a fresh
    all-edge walk, each asserted below (#9493 review P2-1/P2-2). A `CALL { … }`
    subquery IMPORTS the outer bindings (the block itself is not a barrier), so the
    arm below asserts the opposite direction: a `WITH` INSIDE the body is what ends
    the identity, for that body's scope.
    """
    census = _call_time_census()
    facts = ("MATCH (s:Subject {id:$sid})-[r]->(n)",)

    def pre_fix(query: str, given: tuple[str, ...]) -> list[tuple[str, str]]:
        """The window-and-binding rule WITHOUT the scope check — what shipped green."""
        return [
            (window, binding)
            for window, binding in dict.fromkeys(_walk_windows(query))
            if not any(
                fact in window and _names_binding(fact, binding) for fact in given
            )
        ]

    dropped = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) WITH n MATCH (x)-[r]->(y) "
        "RETURN type(r)"
    )
    assert pre_fix(dropped, facts) == [], (
        "the pre-fix predicate already reports the post-`WITH` walk — then this "
        "fixture is not measuring the scope escape and the arm proves nothing"
    )
    unbounded = _unbounded_walks(dropped, facts)
    assert len(unbounded) == 1, (
        f"the walk re-binding `r` after `WITH n` reports {len(unbounded)} "
        f"unbounded walk(s) ({unbounded!r}) — `[]` here is the fail-open: the "
        "`WITH` dropped `r`, so the second `MATCH` walks a DIFFERENT "
        "relationship and the fact about the first one says nothing about it"
    )
    window, binding = unbounded[0]
    assert binding == "r" and "[r]" in window, (
        f"the reported walk is not the post-`WITH` one ({window!r}, "
        f"binding {binding!r}) — the net is vouching for, or blaming, text it "
        "never looked at"
    )

    # The barrier is a DROPPED name, not a `WITH`: carrying the name forward keeps
    # the relationship, so these are the same walk and must stay bounded.
    for label, carrying in {
        "*": "WITH * MATCH (x)-[r]->(y) RETURN type(r)",
        "r": "WITH r MATCH (x)-[r]->(y) RETURN type(r)",
        "DISTINCT r": "WITH DISTINCT r MATCH (x)-[r]->(y) RETURN type(r)",
    }.items():
        query = f"MATCH (s:Subject {{id:$sid}})-[r]->(n) {carrying}"
        assert _unbounded_walks(query, facts) == [], (
            f"`{label}` carries the binding forward, so the second `MATCH` is "
            f"the SAME relationship and the registered fact bounds it — "
            f"{_unbounded_walks(query, facts)!r} is a false red on a real query"
        )

    # …and the shapes that RENAME, DERIVE or AGGREGATE do NOT carry it: the name is
    # either a different entity or gone, so the later `[r]` is a fresh walk. Each of
    # these was GREEN before the projection-target rule (#9493 review P2-1/P2-2).
    for label, renaming in {
        "r AS rel": "WITH r AS rel MATCH (x)-[r]->(y) RETURN type(r)",
        "type(r) AS t": "WITH type(r) AS t MATCH (x)-[r]->(y) RETURN type(r)",
        "r.id AS rid": "WITH r.id AS rid MATCH (x)-[r]->(y) RETURN type(r)",
        "count(*) AS c": "WITH count(*) AS c MATCH (x)-[r]->(y) RETURN type(r)",
        "a * 2 AS c": "WITH 2 * 1 AS c MATCH (x)-[r]->(y) RETURN type(r)",
    }.items():
        query = f"MATCH (s:Subject {{id:$sid}})-[r]->(n) {renaming}"
        reported = _unbounded_walks(query, facts)
        assert len(reported) == 1 and reported[0][1] == "r", (
            f"`{label}` does not carry the RELATIONSHIP forward — the later "
            f"`MATCH (x)-[r]->(y)` binds a fresh all-edge walk and the fact about "
            f"the first one grounds nothing, so it must be reported "
            f"({reported!r}); an empty list here is the fail-open"
        )

    # ⚠️ A `CALL { … }` block IMPORTS the outer bindings, so the block is NOT a
    # barrier — a `WITH` INSIDE it is, and that `WITH` sits at brace depth 1 where
    # `_top_level_offsets` cannot look. The arm is therefore written with `[r]`,
    # the SAME name the fact names: a `[rr]` fixture would never consult the scope
    # check at all (`_names_binding(fact, "rr")` is False, so the binding rule
    # alone refuses it) — that is how the previous revision of this arm passed
    # while pinning nothing (#9544 review P2).
    subquery = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) "
        "CALL { WITH s MATCH (x)-[r]->(y) RETURN y } RETURN type(r)"
    )
    assert _names_binding(facts[0], "r"), (
        "the subquery fixture's inner walk must bind the name the fact NAMES, or "
        "the binding rule decides it and the scope rule is never consulted"
    )
    assert pre_fix(subquery, facts) == [], (
        "the window-and-binding rule already reports the subquery walk — then "
        "this fixture is not measuring the subquery scope escape"
    )
    reported_subquery = _unbounded_walks(subquery, facts)
    assert len(reported_subquery) == 1 and reported_subquery[0][1] == "r", (
        "the `WITH s` INSIDE a `CALL { … }` body drops `r`, so the inner "
        "`MATCH (x)-[r]->(y)` binds a FRESH all-edge walk and must be reported "
        f"({reported_subquery!r}) — an empty list here is the fail-open, and it "
        "is what the previous blanket-`CALL`-barrier arm could not tell apart"
    )

    # ⚠️ …and the descent is BLOCK-SCOPED: a `WITH` inside a body ends THAT body's
    # scope, not the enclosing one. The first cut of the descent handed every
    # inner `WITH` to every walk in the query, so a walk AFTER the block was
    # refused by a projection that never had it in scope — a false red on a
    # legitimate query (the block imports `r`; it does not consume it).
    after_block = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) "
        "CALL { WITH n RETURN n AS nn } MATCH (x)-[r]->(y) RETURN type(r)"
    )

    def pre_fix_boundary_spans(query: str) -> list[tuple[int, int, int]]:
        """`_scope_boundary_spans` as it first shipped: every `WITH` spans the query."""
        return [
            (at, 0, len(query)) for at in _scope_boundary_offsets(query)
        ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(globals(), "_scope_boundary_spans", pre_fix_boundary_spans)
        assert _unbounded_walks(after_block, facts), (
            "the query-wide boundary rule does not report the post-`CALL` walk — "
            "then this fixture is not measuring the block-scoping escape and the "
            "arm below proves nothing"
        )
    assert _unbounded_walks(after_block, facts) == [], (
        "the `WITH n` sits INSIDE the `CALL { … }` body, so it ends that body's "
        "scope only: the outer `r` is still in scope after the block, the "
        f"post-`CALL` `[r]` is the SAME relationship — "
        f"{_unbounded_walks(after_block, facts)!r} is a false red on a real query"
    )
    # …while a `WITH` at the TOP level after the block still ends the identity.
    after_block_dropped = (
        "MATCH (s:Subject {id:$sid})-[r]->(n) "
        "CALL { WITH n RETURN n AS nn } WITH nn MATCH (x)-[r]->(y) RETURN type(r)"
    )
    dropped_after = _unbounded_walks(after_block_dropped, facts)
    assert len(dropped_after) == 1 and dropped_after[0][1] == "r", (
        "a TOP-LEVEL `WITH nn` after the block does drop `r`, so the later "
        f"`MATCH (x)-[r]->(y)` is a fresh all-edge walk ({dropped_after!r}) — the "
        "block scoping must not reach a boundary that belongs to the outer scope"
    )

    # The derivation half: the copy is on the registered line, so the SITE COUNT
    # still cannot see it — the walk net is the only net that can.
    source = (
        "def provenance(query):\n"
        "    box = (\n"
        '        "MATCH (s:Subject {id:$sid})-[r]->(n) "\n'
        '        "WITH n MATCH (x)-[r]->(y) RETURN type(r)"\n'
        "    )\n"
        "    return query(box)\n"
    )
    file = census._File(Path("synthetic_with_scope.py"), "synthetic.py", source)
    sites = census._empty_pattern_sites_in(file)
    assert len(sites) == 1 and next(iter(sites))[0] == "provenance", (
        f"the post-`WITH` re-binding produced {sorted(sites)} — if the derivation "
        "DOES see it the count would catch it and this fixture's premise (one "
        "`(symbol, line)` site for BOTH walks) is wrong"
    )
    site_line = next(iter(sites))[1]
    derived_queries = _site_query_texts(file, "provenance", site_line)
    assert derived_queries, (
        "`_site_query_texts` resolved no query for the `WITH`-scoped site — the "
        "walk net would then check no walk at all"
    )
    assert all(_unbounded_walks(q, facts) for q in derived_queries), (
        f"the post-`WITH` walk is vouched for through the site's RESOLVED query "
        f"({derived_queries!r}) — the net is reading source text, or the wrong line"
    )


def test_a_with_projection_that_carries_beside_another_item_stays_green():
    """A PROJECTION comma is not a pattern boundary — `WITH r, n` keeps the binding.

    ⚠️ **Measured on the post-round-2 head, where the two nets disagreed.**
    `_walk_spans` cut a `UNION` arm at every top-level comma, projection lists
    included, so `WITH n, r` / `WITH r, n` produced the windows `('… WITH n', 'r')`
    and `(', r MATCH …', 'r')`. The registered fact sat in the FIRST window, the
    walk in the SECOND, and `_scope_survives` — which correctly reports that
    `WITH n, r` CARRIES `r`, exactly as `_walk_binding` and `_scope_survives`
    document — was never consulted. Both orders were reported ungrounded, so the
    shipped contract in those two docstrings was false and nothing reded when it
    broke (#9544 review P2).

    In Cypher a projection comma separates ITEMS of one projection, evaluated in
    ONE scope; a comma separates PATTERNS only inside a clause that matches them
    (`MATCH`, `OPTIONAL MATCH`, `MERGE`, `CREATE`), which is what
    `_pattern_comma_offsets` decides. This arm pins the whole CARRYING direction —
    `*`, `r`, `DISTINCT r`, `r, n`, `n, r` — and the DROPPED/RENAMED direction
    beside another item, so the closure the comma boundary existed for is not lost
    along with the false red.
    """
    census = _call_time_census()
    facts = ("MATCH (s:Subject {id:$sid})-[r]->(n)",)

    def pre_fix_spans(query: str) -> tuple[tuple[int, int, int, str], ...]:
        """`_walk_spans` as it shipped: EVERY top-level comma ends a window."""
        arm_bounds = sorted(
            {0, *_top_level_offsets(query, _UNION_CLAUSE), len(query)}
        )
        spans: list[tuple[int, int]] = []
        for index in range(len(arm_bounds) - 1):
            low, high = arm_bounds[index], arm_bounds[index + 1]
            bounds = sorted(
                {
                    low,
                    *[
                        low + at
                        for at in _top_level_offsets(query[low:high], _COMMA)
                    ],
                    high,
                }
            )
            spans.extend((bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1))
        return tuple(
            (low, high, start, _walk_binding(query, start))
            for start in census._walk_occurrences(query)
            for low, high in spans
            if low <= start < high
        )

    carrying = {
        "*": "WITH * MATCH (x)-[r]->(y) RETURN type(r)",
        "r": "WITH r MATCH (x)-[r]->(y) RETURN type(r)",
        "DISTINCT r": "WITH DISTINCT r MATCH (x)-[r]->(y) RETURN type(r)",
        "r, n": "WITH r, n MATCH (x)-[r]->(y) RETURN type(r)",
        "n, r": "WITH n, r MATCH (x)-[r]->(y) RETURN type(r)",
    }
    queries = {
        label: f"MATCH (s:Subject {{id:$sid}})-[r]->(n) {carrying_forward}"
        for label, carrying_forward in carrying.items()
    }

    # The NON-VACUITY half: the pre-fix window split still reports the two
    # projection-comma shapes, so the green below is this rule's work and not a
    # fixture that measures nothing.
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(globals(), "_walk_spans", pre_fix_spans)
        for label in ("r, n", "n, r"):
            reported = _unbounded_walks(queries[label], facts)
            assert len(reported) == 1 and reported[0][1] == "r", (
                f"the pre-fix window split does not report `WITH {label}` — then "
                "this fixture is not measuring the projection-comma escape and "
                f"the arm below proves nothing ({reported!r})"
            )

    for label, query in queries.items():
        assert _unbounded_walks(query, facts) == [], (
            f"`WITH {label}` CARRIES `r` forward, so the later `MATCH (x)-[r]->(y)` "
            "is the SAME relationship and the registered fact bounds it — "
            f"{_unbounded_walks(query, facts)!r} is the false red this fix removes"
        )

    # The CLOSURE half: a projection that drops or renames the binding still ends
    # it, INCLUDING when it sits beside another item — the comma fix must not turn
    # a multi-item projection into a blanket pass.
    barrier = {
        "n, m": "WITH n, m MATCH (x)-[r]->(y) RETURN type(r)",
        "n, r AS rel": "WITH n, r AS rel MATCH (x)-[r]->(y) RETURN type(r)",
        "n, type(r) AS t": "WITH n, type(r) AS t MATCH (x)-[r]->(y) RETURN type(r)",
    }
    for label, carrying_forward in barrier.items():
        query = f"MATCH (s:Subject {{id:$sid}})-[r]->(n) {carrying_forward}"
        reported = _unbounded_walks(query, facts)
        assert len(reported) == 1 and reported[0][1] == "r", (
            f"`WITH {label}` does NOT carry `r` forward — the later "
            "`MATCH (x)-[r]->(y)` binds a fresh all-edge walk and must be reported "
            f"({reported!r}); an empty list here is a fail-open the comma fix "
            "would have introduced"
        )

    # And the comma boundary a second PATTERN needs is untouched: `MATCH …, (x)`
    # matches independently, so a fact in the first pattern reaches no further.
    second_pattern = (
        "MATCH (s:Subject {id:$sid})-[r]->(n), (x)-[rr]->(y) "
        "RETURN type(r), type(rr)"
    )
    assert len(_walk_windows(second_pattern)) == 2, (
        "the second pattern of one `MATCH` no longer gets its own window — the "
        "clause-aware comma rule must keep pattern separators and drop only "
        f"projection commas ({_walk_windows(second_pattern)!r})"
    )
    assert _pattern_comma_offsets(second_pattern) == [second_pattern.index(",")], (
        "`_pattern_comma_offsets` did not keep the comma that separates the two "
        f"`MATCH` patterns ({_pattern_comma_offsets(second_pattern)!r})"
    )
    assert _pattern_comma_offsets(
        "MATCH (a)-[r]->(b) WITH r, a RETURN type(r), labels(a)"
    ) == [], (
        "a `WITH`/`RETURN` projection comma is not a pattern boundary — "
        "`_pattern_comma_offsets` must find none here "
        f"({_pattern_comma_offsets('MATCH (a)-[r]->(b) WITH r, a RETURN type(r), labels(a)')!r})"
    )


def test_the_live_fact_corpus_is_the_stripped_source():
    """The fact corpus is `_code_lines` — WIRED, not only unit-tested.

    ⚠️ **`test_a_stated_fact_cannot_be_satisfied_by_prose` calls the stripper
    directly, so nothing pinned its only production consumer.** Measured: reverting
    `_site_fact_text`'s `lines = _code_lines(path)` to
    `lines = tuple(path.read_text(encoding="utf-8").splitlines())` left every arm
    green, because no live fact is comment-satisfiable TODAY — so the hole the
    stripper closes would reopen silently on the next refactor of that function,
    and this file's map item 24 would become false prose.

    Asserted on the LIVE corpus: every live site's fact text is comment-free, and at
    least ONE live site's statement carries a `#` comment in the raw source — so the
    discriminator is live, and the arm reds if it ever stops being.
    """
    census = _call_time_census()
    raw_carriers: list[str] = []
    for rel, symbol, *_rest in census.EMPTY_PATTERN:
        for line in census._empty_pattern_sites()[(rel, symbol)]:
            path = PKG / rel.removeprefix("tortoise/")
            span = _statement_span(_module_tree(path), line)
            assert span is not None, (
                f"`{_site_key(rel, symbol)}:{line}` has no statement span — the "
                "raw-vs-stripped comparison below would measure nothing"
            )
            raw = path.read_text(encoding="utf-8").splitlines()
            if "#" in "\n".join(raw[span[0] - 1 : span[1]]):
                raw_carriers.append(f"{_site_key(rel, symbol)}:{line}")
            text = _site_fact_text(rel, line)
            assert "#" not in text, (
                f"`{_site_key(rel, symbol)}:{line}` fact text carries a COMMENT "
                "— the fact corpus is raw source again, so a fact can be "
                "satisfied by prose that never runs. `_site_fact_text` must build "
                "its text from `_code_lines`."
            )
    assert raw_carriers, (
        "no live site's STATEMENT carries a comment, so the assertion above cannot "
        "discriminate raw source from stripped source — the live discriminator "
        "has moved, and this arm needs a new one before it can be trusted"
    )


def test_a_stated_fact_no_site_carries_reds_the_entry():
    """Every recorded FACT must be carried, not just ONE of them (the reverse net).

    ⚠️ **Measured on the pre-fix arm, and this is the narrowing the PR introduced.**
    The per-site model needs an ANY-fact rule (the two-site entries carry one fact
    each, so "all facts in every site" is unsatisfiable), but the loop that
    implements it (`for fact in facts: if fact in text: break`) has no reverse
    direction: on the entries that carry MORE facts than sites, retyping one anchor
    hides behind whichever fact still matches. Measured: retype
    `session_source_migration._DEGREE` from `(old:Source {url:$u})` to `(old)` and
    the walk starts observing an `aboutObject` edge while the arm stays green —
    where `origin/main`, which required every fact, reded.

    Asserted on the live register, because the register is what has to stay true:
    the entry's facts are all carried today, and the mutated fact set — which the
    any-fact loop would have accepted — is reported missing.
    """
    census = _call_time_census()
    entry = next(e for e in census.EMPTY_PATTERN if e[1] == "_degree")
    rel, symbol, _why, facts, _sites = entry
    lines = census._empty_pattern_sites()[(rel, symbol)]
    text = "\n".join(_site_fact_text(rel, line) for line in lines)
    assert not _uncarried_facts(facts, text), (
        f"`{_site_key(rel, symbol)}` no longer carries its own recorded fact "
        f"({_uncarried_facts(facts, text)!r}) — the reason is false about its code"
    )
    retyped = (facts[0].replace(":Source ", " ", 1), *facts[1:])
    assert retyped != facts, (
        f"the fixture's mutation did not change the fact set ({facts!r}) — it "
        "would then assert the unmutated entry reds, which is a false red"
    )
    assert _uncarried_facts(retyped, text) == [retyped[0]], (
        "the retyped anchor is not reported as uncarried: "
        f"{_uncarried_facts(retyped, text)!r} — the arm is back to accepting ONE "
        "of an entry's facts, which is how a lost ground ships green"
    )
    assert any(fact in text for fact in retyped), (
        "the mutated entry carries NONE of its facts, so the any-fact loop would "
        "have red too — the mutation is only discriminating while at least one "
        "fact still matches"
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


#: The RETIRED anchored form of the split-traversal filter, kept ONLY as the
#: control for the arm below: it is the predicate the widening replaced, so
#: "nothing admitted before is dropped" is measured against the real thing rather
#: than against a paraphrase of it.
_RETIRED_SPLIT_TRAVERSAL = re.compile(
    r"\[[A-Za-z_][^\]\n,\"']*\]\s*-|\[[A-Za-z_][^\]\n,\"']*\]\s*[\"']\s*\+\s*[\"']\s*-"
)


def test_the_module_filter_is_a_strict_superset_of_the_anchored_form():
    """The widening drops nothing, admits something, and stays inside the walk.

    ⚠️ **This arm replaces numerals that had already rotted.** The census comment
    used to state the module counts behind the widening ("242 modules … 76 …
    211 …"), and a sibling merge added a module inside this PR's lifetime, so the
    numbers were wrong while the prose still called them measured. A count in a
    comment has no falsifier; the RELATION does, so it is what is asserted here
    and the comment states no number.

    Three assertions, and ONE of them measures the thing the arm is named for —
    the other two are CONTROLS that cannot fail by construction, stated as such so
    the next reader does not cite them as measurements:

    * **no drops** (control, structurally true) — every module the retired
      anchored form admitted is still admitted. It cannot fail: each alternative of
      the retired form embeds the `[` + name + `]` run the unanchored pattern
      matches, so this holds for ANY corpus. It is here as the guard against the
      predicate being re-anchored later, not as evidence of anything measured;
    * **not vacuous** (MEASURED) — the widening admits modules the anchored form
      refused, which is the assertion with teeth: it fails if the predicate drifts
      back onto the anchored shape, and it is what makes the control above
      non-trivially satisfiable;
    * **inside the walk** (control, structurally true) — `_may_traverse_every_type`
      is one of the clauses that ADMITS a module, so `widened <= walked` holds by
      construction. It is the tripwire for that wiring changing, not a measurement
      of the corpus.

    The derivation pin in `test_the_module_filter_admits_a_split_traversal` (the
    `(keys, rows)` pair that arm asserts) is the second measuring assertion: it is
    what proves the widening added no site. ⚠️ The pair is named, not repeated: the
    figures written here once went stale while the arm's own pin moved for an
    unrelated reason (`#9604`).
    """
    census = _call_time_census()
    universe = census._universe()
    anchored = {
        path for path, source in universe.items() if _RETIRED_SPLIT_TRAVERSAL.search(source)
    }
    widened = {
        path for path, source in universe.items() if census._SPLIT_TRAVERSAL.search(source)
    }
    assert anchored and widened, (
        f"the comparison is measuring an empty set ({len(anchored)} anchored / "
        f"{len(widened)} widened over {len(universe)} modules) — a subset claim "
        "about nothing is what this arm exists to refuse"
    )
    assert not anchored - widened, (
        "the widened filter REFUSES modules the anchored form admitted: "
        f"{sorted(str(p) for p in anchored - widened)} — a filter narrower than "
        "the one it replaced drops a module before the derivation can see the "
        "walk inside it, which is the one direction this predicate cannot fail in"
    )
    assert widened - anchored, (
        "the widened filter admits nothing the anchored form refused, so the "
        "subset assertion above is trivially true and the widening is "
        "unmeasured — either the third spelling is no longer what the predicate "
        "is for, or this control has drifted off the retired form"
    )
    walked = {file.path for file in census._files()}
    assert widened <= walked, (
        "the module filter admits modules the gate then refuses to WALK: "
        f"{sorted(str(p) for p in widened - walked)} — admitting a module and "
        "never parsing it makes `_files()` the narrower instrument again. "
        "(Structurally true — `_SPLIT_TRAVERSAL` is one of `_files()`'s own "
        "admission clauses — so this is a tripwire on that wiring, not a "
        "measured property of the corpus; the docstring says so.)"
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
    assert (len(sites), sum(len(lines) for lines in sites.values())) == (26, 39), (
        "the empty-pattern derivation moved off 26 symbol keys / 39 site rows "
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


def test_a_chained_bracket_less_walk_binds_its_OWN_hop():
    """A chain `(a)--(b)--(c)` is TWO walks, and each binds its own hop.

    ⚠️ **The second hop used to crash the net instead of answering it** (#9544
    review P3). `_walk_occurrences` reports both `--` offsets, but `_walk_binding`
    re-matched the query with a CONSUMING `(node)--(node)` pattern: it ate
    `(a)--(b)` and resumed after `(b)`, so it could never see the second dash pair
    and raised "the walk net and the derivation have drifted apart" — a diagnosis
    of drift, over a shape with no drift in it, greeting the first person to
    register a chained bracket-less site. The extent is now read from the same
    `_BARE_TRAVERSAL` match the derivation reported, so a chain answers.
    """
    census = _call_time_census()
    chain = "MATCH (a)--(b)--(c) RETURN a"
    assert census._walk_occurrences(chain) == (9, 14), (
        f"the derivation reports {census._walk_occurrences(chain)!r} for a chain, "
        "not the two dash pairs this arm is written about"
    )
    bound = [_walk_binding(chain, start) for start in census._walk_occurrences(chain)]
    assert bound == ["(a)--(b)", "(b)--(c)"], (
        f"a chained bracket-less walk binds {bound!r} — each hop must carry its "
        "OWN `(node)…(node)` text, or a fact about one hop vouches for the other"
    )
    # The point of the token: a fact naming ONE hop does not bound the other.
    assert _unbounded_walks(chain, ("MATCH (a)--(b)",)) == [
        ("MATCH (a)--(b)--(c) RETURN a", "(b)--(c)")
    ], (
        "a chained walk inherits its neighbour's fact — the binding token is not "
        "per-hop, so the second hop is silently covered by a ground about the first"
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
    `was skipped`, `was omitted` and `is unavailable` spellings. What stays framed is
    only what the SENTENCE END puts out of reach: `never done here` after a `.`, or
    `--durations=0. wasn't really used`, is framed — and dropping that `.`
    (`--durations=0 wasn't really used`, a single sentence) reds the same words. The
    list is a blacklist of spellings, not a parse, so a new denial phrasing is a
    one-line addition, not a rewording dodge.
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


def _flatten_comment(comment: str) -> str:
    """`comment` as ONE line, so a line break cannot hide a phrase (`#9501`).

    ⚠️ **The defect this exists for: a guard blind to where the prose WRAPS.** A
    comment is judged as the text a reader reads, and that text is wrapped to the
    repo's line length, so a break landing INSIDE a phrase splits it into two lines
    that no single alternation matches and no backtracking recovers.

    Measured, and it was the whole hole: the sentence this file exists to red on —
    *"the rebuild is a small fraction of this arm's cost"* — is wrapped at this repo's
    width as `…a small fraction of this` / `# arm's cost…`. The first version of
    `_unsettleable_magnitudes` was therefore **GREEN on the comment `origin/main`
    actually carries**, and RED only on the re-wrapped copy the arm fed it: one moved
    line break was the entire difference between RED and GREEN, so the arm was vacuous
    with respect to the defect it was written for (`#9501` review).

    ⚠️ The word-form predicate flattens; `_unframed_durations` deliberately does NOT.
    Its framing window is the number's own LINE by design (`_framed`), and flattening
    there would let a marker on ANY line frame every figure in the comment — the
    whole-text hole `#9522` closed. So a duration broken before its unit stays silent,
    and that residual is filed with the line-vs-number coarseness as `#9531` rather
    than bought back by widening the framing window.
    """
    return " ".join(line.strip().lstrip("#").strip() for line in comment.splitlines())


#: A size QUALIFIER in front of the subject noun — `the ENTIRE arm's cost`, `the whole
#: walk`. Measured by the `#9501` review: the determiners were enumerated and the
#: qualifier slot did not exist, so `# a small fraction of the entire arm's cost` — the
#: sentence this chain produced with ONE word inserted — was GREEN. Deliberately a
#: CLOSED list rather than a `\w+` slot, because a generic word slot buys back this
#: false negative by turning every noun phrase into a candidate (`# most of these
#: modules are run in the suite` red on `run`); a qualifier outside this list is a
#: stated GREEN residual in `#9531`, the same disposition as an unknown quantity noun.
_MAGNITUDE_QUALIFIER = r"(?:entire|whole|full|overall|complete|remaining|aforementioned)"

#: ⚠️ The subject's DETERMINER slot is two layers, not one (`#9528` re-review round 3).
#: It was `the|this|that|these|those`, so `that`/`those` were added first and then the
#: QUANTIFIERS (`all|every|each|both|some|any|no`) — and each round closed one spelling
#: while its neighbour stayed GREEN, because a quantifier can sit IN FRONT OF an article
#: or a possessive (`# the rebuild is half of all its cost`, `# half of every run pays
#: the parse`). One layer could not express both, so the slot is now
#: quantifier-then-article, and the two vocabularies are this file's single lists rather
#: than a shape (`\w+\s+` would swallow `no such` and every innocent noun phrase).
_MAGNITUDE_QUANTIFIER = r"(?:all|every|each|both|some|any|no)"
_MAGNITUDE_ARTICLE = r"(?:the|this|that|these|those|its|their|our|his|her|your|my|a|an)"

#: The quantities a proportion claim is ABOUT — the arm, its cost, its work, in the
#: plural too ("most of the walks pay the parse"), or an ANAPHOR standing in for one
#: ("a small fraction of it", "… of its cost"). Every alternative below BINDS to one of
#: these, because the claim class is "a share of the work", not "any phrase containing
#: `most of`": `# most of these modules are literal carriers` asserts no magnitude at
#: all, and a merge-gating arm that reds on it reports "state the framing that produced
#: it" for a sentence holding no figure — a false positive, not a policy (`#9501` review).
#: ⚠️ The DETERMINER slot is `the|this|that|these|those` plus the QUANTIFIERS
#: (`all|every|each|both|some|any|no`): `# the rebuild is half of all its cost` and
#: `# half of every run pays the parse` were GREEN while the `these` spelling red, so one
#: determiner still retired the guard (`#9528` re-review). The slot is a closed list and
#: every spelling added to it is a GREEN leg's neighbour, which is why it is one list for
#: the whole predicate rather than a shape (`\w+\s+` would swallow `no such` and every
#: innocent noun phrase in this repo's comments).
#: ⚠️ The ANAPHOR is the cycle-3 P0: without it `# the rebuild is a small fraction of it`
#: was GREEN, one pronoun away from the sentence `#9516` left behind. `it`/`its`/`them`/
#: `their`/`itself`/`this one` bind as subjects, and so does a DEMONSTRATIVE with a
#: measured noun after it (`# most of that arm`, `# most of those arms`): `that`/`those`
#: were missing from the determiner slot, so swapping one determiner turned the same
#: sentence GREEN (`#9528` re-review). A demonstrative naming anything else stays the
#: innocent shape this binding exists to keep green (`# most of these modules`,
#: `# most of this file is parsed once`), and a BARE demonstrative with no noun at all
#: (`# most of that`) is a stated residual in `#9531`, not an oversight.
#: ⚠️ Two more shapes are deliberately ABSENT: a bare `bulk` with no subject (measured: it
#: red `# reads them in bulk from the disk`), and the bare noun `time` (measured: it red
#: `# most of the time this is fine`, an idiom, not a figure). The cost of the second is
#: a time-share claim in words going GREEN, which is the direction a merge gate should
#: err in; both are listed with the other residuals in `#9531`.
#:
#: Possessive PRONOUNS on a measured quantity (`#9528` re-review). Defined here because
#: BOTH predicates read one vocabulary: the share veto below and the subject slot here.
#: A determiner strip that consumed `its`/`their`/`our` as if they were `the` put the
#: measurement noun at position 0 of a tail, where the dropped-apostrophe check is
#: suppressed — so `# the second half of its cost is the parse` read GREEN while
#: `… of the arm's cost` red, one pronoun away from the sentence the veto exists for.
#: And on the SUBJECT side the old anaphor list was `it|its|itself|them|their`, so
#: `# most of our total` matched nothing at all — the vocabulary is one set now, so a
#: pronoun cannot bind in one predicate and be missing from the other.
_MAGNITUDE_POSSESSIVE_PRONOUNS = frozenset(
    {"its", "itself", "their", "theirs", "our", "ours", "his", "her", "hers", "your", "yours", "my"}
)

#: The ANAPHORS that stand in for a measured quantity — the object pronouns plus the
#: whole possessive vocabulary above. `them` is not a possessive and stays: `# most of
#: them` is a share of the things the walk reads.
_MAGNITUDE_ANAPHOR = "|".join(
    sorted(
        {"it", "them", "this\\s+one", "that\\s+one"} | _MAGNITUDE_POSSESSIVE_PRONOUNS,
        key=len,
        reverse=True,
    )
)

_MAGNITUDE_SUBJECT = (
    rf"(?:(?:{_MAGNITUDE_QUANTIFIER}\s+)?(?:{_MAGNITUDE_ARTICLE}\s+)?(?:{_MAGNITUDE_QUALIFIER}\s+)?"
    r"(?:\w+(?:[- ]\w+)*['’]s\s+|\w+(?:[- ]\w+)*s'\s+)?"
    r"(?:arms?|costs?|wall[- ]?clocks?|runs?|suites?|totals?|works?|budgets?|walks?)\b"
    rf"|(?:{_MAGNITUDE_ANAPHOR})\b)"
)

#: A magnitude asserted in WORDS — the form `_unframed_durations` cannot see, because
#: it matches digits. Each alternative names a proportion without naming a number: the
#: same unsettleable claim in a different alphabet.
#: ⚠️ The ADJECTIVE is OPTIONAL on purpose. The sentence this chain produced read *"the
#: redundant rebuild is a fraction of this arm"*, and the first version of this pattern
#: required `small|large|…` before the noun — so it was GREEN on the exact spelling the
#: defect took (`#9501` review). What makes the phrase a claim is the quantity it binds
#: to, never the adverb in front of it.
#: ⚠️ The ORDINALS are matched as their PRODUCTIVE MORPHOLOGY, never as the bare `\w+th`
#: suffix: that shape is not a closed class in English (measured by the `#9501` review:
#: it read `length`, `path` and `depth` as proportions, so `# the length of the walk
#: grows` red the gate). The closed shapes are `\w+teenths?` (thirteenth … nineteenth),
#: `\w+tieths?` (twentieth … ninetieth) and the magnitude-scale `hundredth` …
#: `trillionth`, each with its plural — no ordinary English noun ends in `-teenth` or
#: `-tieth` — plus the enumerated `third` … `twelfth`. An ENUMERATION that stopped at
#: `thousandths?` was itself GREEN on `a twentieth of the arm` and `a millionth of the
#: arm` (`#9501` review, the same "the list is complete" claim that was false twice):
#: the list is closed only where the SPELLING is, which is these shapes.
#: ⚠️ The MULTIPLICATIVE forms are a separate alternative (cycle-2 review): `twice as long
#: as the walk`, `doubles this arm's cost`, `2× the walk`, `an order of magnitude`,
#: `a factor of two`, `three in four of the walks` are each a magnitude bound to a
#: measured quantity — a one-keystroke rewording of the sentence `#9516` left behind —
#: and every one of them was GREEN. Each is BOUND (to a subject, to `as … as`, to
#: `of <subject>`, or to a `than <subject>` comparison) so `# doubles entries in the
#: list`, `# reads 2x per site`, `# the rebuild is 2x faster` and `# 0x1f` stay GREEN.
#: ⚠️ …and the NUMERAL is a SHAPE, not a hand-list (cycle-3 review): `4 times`,
#: `10 times`, `quadruples`, `threefold`, `3 in 4`, `9 out of 10` and `3/4` were all
#: GREEN while their neighbours red, which is the "closed" claim being false by one
#: keystroke. `Nx`/`N×` must be TIGHT (`2×`) because `3 x 4` is a grid, and `factor of`
#: takes a NUMERAL because `a factor of safety` is not a magnitude — both measured.
#: ⚠️ `Nx`/`N×` also BINDS, like every other alternative (`#9501` review measured it
#: reding `# reads 2x per site` and `# the rebuild is 2x faster` with the remediation
#: "state the framing that produced it" for a sentence holding no figure): it takes a
#: subject (`2× the walk`) or a `than <subject>` comparison (`2x faster than the walk`,
#: `2x longer than the run`) — prose naming neither stays GREEN.
#: A number written as a WORD, for the percentage slot below. ⚠️ The slot was
#: DIGIT-ONLY, so `# forty per cent of the arm is the re-parse` was GREEN while its
#: digit twin REDded — the `#9528` review's round-2 P2, and the same one-word-swap
#: retirement the whole guard exists to refuse. It is a closed vocabulary (tens,
#: their hyphenated compounds, teens, units, and the `hundred` forms) rather than
#: `[a-z]+`, because a generic word slot turns every `<word> per cent` into a hit.
_WORD_NUMERAL = (
    r"(?:(?:a\s+|one\s+|two\s+)?hundred"
    r"|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|ten|one|two|three|four|five|six|seven|eight|nine)"
)

#: A multiplier / ratio / multiple slot — a DIGIT or a number WORD, and the WORD is
#: `_WORD_NUMERAL` rather than its own list, so a numeral cannot be closed in the
#: percentage slot and open in the multiplicative one — which is exactly what happened:
#: this slot stopped at `ten`, so `eleven times the walk`, `a factor of eleven`,
#: `twentyfold the walk` and `three in eleven of the walks` were GREEN while their
#: `ten` twins red (`#9528` re-review round 4). Declared here, AFTER `_WORD_NUMERAL`,
#: because it now reads that vocabulary.
_MAGNITUDE_NUMERAL = rf"(?:\d+(?:\.\d+)?|{_WORD_NUMERAL})"

#: A percentage figure — a DIGIT or a number WORD — in every spelling the file
#: closes. One constant serves both the word-form guard and the ratio arm's bare
#: sweep, so a spelling cannot be closed in one and open in the other — including the
#: numeral's CASE and its UNIT'S PUNCTUATION.
#: ⚠️ CASE is part of the closure: the guard compiles with `re.IGNORECASE`, so the sweep
#: must pass the same flag (it did not, and every sentence-initial `Forty percent`
#: escaped it — `#9528` review round 3, P2).
#: ⚠️ PUNCTUATION too: `per-cent` and `per  cent` are the same claim as `per cent`, and a
#: unit alternation spelled `per\s?cent` left both open in both predicates while
#: `40-percent` was closed in the guard and open in the sweep — the "one constant serves
#: both" claim, false by one hyphen (`#9528` review round 4).
#: STATED MISSES: `_WORD_NUMERAL` is the units, the teens, the tens (hyphen or
#: space) and the `hundred` forms. It does NOT carry `zero` or the thousand scale
#: (`one thousand`, `a million`); with `%` / `per cent` / `pct` those spellings are open
#: in both predicates, and with the `percent` noun they are caught only by the bare noun
#: at the top of `_MAGNITUDE_WORDS`. Deliberately not chased: a numeral vocabulary wide
#: enough to reach them starts matching ordinary prose, and the cost of the miss is one
#: spelling a reader can already see.
#: The per/cent separator is capped at TWO characters (`?` twice rather than a counted
#: quantifier, because this is an f-string and `{0,2}` would be eaten as a format
#: field), so `per   cent` with three spaces stays open in both predicates — the same
#: disposition as above.
_PERCENTAGE_FIGURE = rf"(?:\d+(?:\.\d+)?|{_WORD_NUMERAL})[-\s]*(?:%|per[-\s]?[-\s]?cent|pct)"

_MAGNITUDE_WORDS = re.compile(
    rf"\b(?P<proportion>"
    rf"(?:(?:small|large|tiny|negligible|modest|minor|major|significant|"
    rf"substantial|smallish)\s+)?"
    rf"(?:third|thirds|half|halves|quarter|quarters|fourths?|fifths?|sixths?|"
    rf"sevenths?|eighths?|ninths?|tenths?|elevenths?|twelfths?|\d+(?:st|nd|rd|th)s?|"
    rf"\w+teenths?|"
    rf"\w+tieths?|(?:hundred|thousand|million|billion|trillion)ths?|"
    rf"fraction|share|portion|part|proportion|percentage|percent|"
    rf"ratio|majority|minority|sliver|chunk))\s+(?:of|in)\s+{_MAGNITUDE_SUBJECT}"
    rf"|\b(?P<percent>{_PERCENTAGE_FIGURE})"
    rf"\s+(?:of|in)\s+{_MAGNITUDE_SUBJECT}"
    rf"|\b(?P<bulk>(?:the\s+)?(?:bulk|lion's\s+share))\s+of\s+{_MAGNITUDE_SUBJECT}"
    rf"|\b(?P<share>(?:most|half)\s+(?:of\s+)?{_MAGNITUDE_SUBJECT})"
    rf"|\b(?P<multiple>"
    rf"an?\s+order\s+of\s+magnitude"
    rf"|an?\s+factor\s+of\s+{_MAGNITUDE_NUMERAL}"
    rf"|(?:twice|{_MAGNITUDE_NUMERAL}\s+times)\s+(?:as\s+\w+\s+as\b|"
    rf"{_MAGNITUDE_SUBJECT})"
    rf"|(?:doubles?|triples?|quadruples?|quintuples?|halves?)\s+(?:the\s+)?"
    rf"{_MAGNITUDE_SUBJECT}"
    rf"|{_MAGNITUDE_NUMERAL}[- ]?fold\b"
    rf"|\d+(?:\.\d+)?\s?[x×](?![\w.])\s+(?:of\s+)?{_MAGNITUDE_SUBJECT}"
    rf"|\d+(?:\.\d+)?\s?[x×](?![\w.])\s+"
    rf"(?:faster|slower|longer|bigger|larger|smaller|higher|lower)\s+than\s+"
    rf"{_MAGNITUDE_SUBJECT}"
    rf"|{_MAGNITUDE_NUMERAL}\s+(?:in|out\s+of)\s+{_MAGNITUDE_NUMERAL}\s+"
    rf"(?:of\s+)?{_MAGNITUDE_SUBJECT}"
    rf"|\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s+of\s+{_MAGNITUDE_SUBJECT}"
    rf")",
    re.IGNORECASE,
)

#: A POSITIONAL qualifier in front of a share noun (`# the second half of the walk`,
#: `# the left half of the suite`, `# the second part of the walk`) — which phase of
#: something runs, not what share of a measured quantity it is. Measured by the
#: `#9501` review: both red without this, on the walk's own vocabulary (`part` names one
#: of the three passes).
#:
#: ⛔ The set is PHASES only (`#9528` review round 5, P1). `upper`, `lower` and `other`
#: were in it and they are SHARE adjectives: with them here, one word retired the guard
#: and `# the other half of the arm's cost` / `# the upper half of the arm` both read
#: GREEN. Stated cost, not discovered later: `# the upper half of the walk` now REDs,
#: and the remediation is one edit.
_MAGNITUDE_POSITIONAL = re.compile(
    r"\b(?:first|second|third|left|right|top|bottom|last|final)\s+$",
    re.IGNORECASE,
)

#: The TAIL that turns a positional `half` back into a SHARE (`#9528` review round 5,
#: P1). The phase reading needs the divided thing named WITHOUT a possessive, so
#: `# the second half of the walk` is a phase while `# the second half of the walk's
#: cost` is a share of a measured quantity. Read off the TAIL and not from the subject
#: slot, because that slot only swallows an ASCII `'s`: a typographic apostrophe
#: (`walk’s cost`, what macOS and Word produce), a plural possessive (`walks' cost`) and
#: a dropped apostrophe (`walk cost`) each leave the subject unmatched, and without this
#: veto each of them kept the exemption.
#: The nouns that make a dropped apostrophe a MEASUREMENT (`walk cost`): a bare
#: measurement noun right after the divided thing is the only way to spell the share
#: without punctuation.
_MAGNITUDE_MEASUREMENT_NOUNS = frozenset(
    {"cost", "costs", "work", "time", "runtime", "wall-clock", "wallclock"}
)

#: How many words of the tail are scanned for a possessive. The divided thing is a
#: noun PHRASE (`the gate walk's cost`), not a sentence: three words covers a determiner
#: or qualifier plus the head noun plus one modifier, and a possessive past that is not
#: one about THIS share. ⚠️ It was six, which reached into the sentence's NEXT clause:
#: `# the second half of the walk re-reads the arm's output` and `# the second half of the
#: suite validates the work of the extractor` both RED — two spellings of an innocent
#: phase sentence, differing from their GREEN twins only by a later clause's noun
#: (`#9528` re-review). The price is stated: a possessive three words into the divided
#: thing (`the entire first gate walk's cost`) is now GREEN, and the dropped-apostrophe
#: RED legs the file pins sit at position 1 — the multi-word spelling at position 2 is the
#: STATED GREEN residual (`# the second half of the gate walk cost`), not an oversight.
_MAGNITUDE_TAIL_WORDS = 3


def _MAGNITUDE_PHASE_SHARE_TAIL(tail: str) -> bool:
    """Does the text AFTER a positional share noun name a MEASUREMENT (`#9528` P1)?

    `# the second half of the walk` is a PHASE; `# the second half of the walk's cost`
    is a SHARE of a measurement wearing a phase's clothes. The possessive is read HERE,
    off the tail, and in CODE rather than one regex, for two measured reasons:

    * the subject slot (`_MAGNITUDE_SUBJECT`) only swallows a single-word ASCII `'s`,
      so a typographic apostrophe (`walk’s cost`, what macOS and Word produce), a plural
      possessive (`walks' cost`) and a dropped apostrophe (`walk cost`) leave it
      unmatched;
    * it also accepts a MULTI-WORD possessive (`the gate walk's cost`), so a one-token
      tail check was GREEN on `# the second half of the whole arm's cost` and on every
      `the gate walk's cost` spelling — the same one-word-retires-the-guard shape, moved
      from the adjective slot to the divided-thing slot.

    The DEMONSTRATIVE determiner is consumed as a determiner, not as the divided thing:
    without that, `# the second half of the work re-reads the sources` read RED because
    `the` stood in for the share's subject and `work` looked like a dropped-apostrophe
    measurement — the documented-GREEN parallel of `the second half of the walk re-reads
    the sources`.

    ⚠️ A POSSESSIVE PRONOUN is NOT consumed (`#9528` re-review). It names an owner, not
    a thing: `the second half of its cost` is a share of a measurement exactly as
    `of the arm's cost` is, and stripping the pronoun as a determiner landed the
    measurement noun at position 0 — where the dropped-apostrophe check is suppressed —
    so the whole tail read GREEN. The pronoun now reds on its own, which is the fourth
    constraint this veto states, applied to the pronoun spelling of it.

    Stated residual, pinned by the GREEN legs beside this arm: a DEMONSTRATIVE is
    stripped exactly as `the` is, so a phase of a thing named that way
    (`# the second half of this cost`, `# the second half of these costs`) keeps the
    exemption the same as `# the second half of the costs` — the boundary this veto
    draws is POSSESSIVES, and a demonstrative names the thing rather than owning it.
    `# the second half of these costs` used to read RED only because `these` was not in
    the strip, while `# the second half of the costs` read GREEN: an inconsistency
    between two spellings of the same sentence, not a stricter guard (`#9528`
    re-review). An anaphor with no possessive (`# the second half of it`) is the same
    shape.
    """
    head = re.match(r"\s*of\s+", tail, re.IGNORECASE)
    if not head:
        return False
    rest = tail[head.end() :]
    # A QUANTIFIER can sit in front of the divided thing's determiner (`of all its cost`,
    # `of both their runs`), and one strip layer could not consume both: the pronoun
    # landed at position 1 and the whole share read GREEN (`#9528` re-review round 4).
    quantifier = re.match(rf"(?:{_MAGNITUDE_QUANTIFIER})\s+", rest, re.IGNORECASE)
    if quantifier:
        rest = rest[quantifier.end() :]
    determiner = re.match(
        r"(?:the|this|that|these|those|a|an)\s+",
        rest,
        re.IGNORECASE,
    )
    if determiner:
        rest = rest[determiner.end() :]
    words = re.findall(r"[\w’'-]+", rest)[: _MAGNITUDE_TAIL_WORDS]
    for position, word in enumerate(words):
        lowered = word.lower()
        # The pronoun owns the DIVIDED THING only as its determiner — the first word of
        # the tail. Scanning the whole window red `# the second half of the walk
        # re-reads its sources`, where `its` owns something in a LATER clause and the
        # divided thing carries no possessive at all: a false RED with a remediation
        # ("state the framing that produced it") describing a figure the sentence does
        # not contain (`#9528` re-review).
        if position == 0 and lowered in _MAGNITUDE_POSSESSIVE_PRONOUNS:
            return True
        if lowered.endswith(("'s", "’s")) or lowered.endswith("s'"):
            return True
        # a dropped apostrophe: the measurement noun RIGHT AFTER the divided thing —
        # position 1, not "somewhere in the window", which is what the code did and red
        # `# the second half of the suite validates the work of the extractor` on a
        # `work` three words out (`#9528` re-review).
        if position == 1 and lowered in _MAGNITUDE_MEASUREMENT_NOUNS:
            return True
    return False


def _unsettleable_magnitudes(comment: str) -> list[str]:
    """Magnitude claims in `comment` that no number makes settleable (`#9501`).

    ⚠️ **This is the digit guard's rule in the other alphabet, and it is the hole
    `#9516` left.** That change removed the *figures* from the gate-walk comment but
    kept the magnitude in WORDS: *"No magnitude is pinned: the rebuild is a small
    fraction of this arm's cost, and a wall-clock number in a comment is the claim that
    rots."* A predicate that matches a NUMBER and a UNIT (`10.36 s`, `30 seconds`,
    `2.6 min`) cannot see it: a proportion spelled in words carries no number for it to
    match, so the sentence survived the guard written to delete exactly that claim — and
    it is **false at the
    natural reading.** Measured with `_literal_files()` replaced by a rebuild over
    `_sources().items()`, fresh process, `_literal_files()` warm, two runs per tree
    (`arm` = `_narrow_gate_walk_sites`'s own cost, `ungated` = the pass it gates):

    - `69390341f` (the `#9516` merge): rebuild 1.70 s / 1.81 s, arm 3.35 s / 3.14 s,
      ungated 10.81 s / 9.90 s.
    - `81f5594dc` (`#9549`; the merge base when the figures were taken): rebuild 2.50 s / 3.00 s, arm 3.90 s / 4.40 s. `#9549` changed the census universe, so these absolute times are NOT the
      ones above; the direction is the same on both trees, and the percentages
      recomputed from these rows below are the claim.

    Read against the ARM the comment sits in, the rebuild is **over half** of it, not a
    small fraction. Each percentage below is RECOMPUTED from the row above it by
    `test_the_gate_walk_magnitude_ratios_recompute_from_the_rows_they_name`, so a claim
    and the figures it is made from cannot drift apart again (`#9501` review: the
    percentages here were hand-computed once, and one of them was attributed to a
    reading — and a tree — it did not belong to):

    - `69390341f` arm 51 % / 58 %, ungated 16 % / 18 %, folded 12 % / 14 %.
    - `81f5594dc` arm 64 % / 68 %.

    The ungated and FOLDED readings are the only ones that make it look small, and
    neither is the arm the comment sits in: a ratio whose truth depends on an unstated
    reading of "this arm" is the same unsettleable claim as `10.36 s of 10.85 s`,
    written so the duration guard misses it.

    One rule, stated once and applied to both forms: a magnitude — digit or word — is a
    fact asserted about a measurement, and it is allowed only when the comment also
    states the framing that produced it. A framed figure therefore admits the word form
    too; an unframed one admits neither.

    ⚠️ **The framing window is the phrase's own line and the ONE AFTER IT, and reuses
    `_framed`; the first version used the WHOLE TEXT and that was a merge-gating false
    negative** (measured by the `#9501` review, cycle 2). It short-circuited on
    `any(marker in comment …)`, so ONE framing mention anywhere in the function — an
    unrelated `whole arm`, three lines away — cleared every word-form magnitude in it.
    That is the whole-text hole `#9522` closed for durations, reopened in the other
    alphabet. Reusing `_framed` also buys its DENIAL veto, so
    `# whole arm was never measured: a small fraction of the arm` REDs instead of
    framing itself.

    ⚠️ **The search window is that same pair of lines**, because the phrase this
    predicate exists to red on is wrapped INSIDE itself: `origin/main` breaks it as
    `…a small fraction of this` / `# arm's cost…`, so a line-by-line match was GREEN on
    the comment the file actually carries and RED only on a re-wrapped copy of it
    (`_flatten_comment`). Cost, stated and PINNED by an arm rather than implied: a
    marker on the NEXT line frames a complete same-line phrase, so the hole is one line
    wide (two lines of text) where the first version's was the whole function. Narrowing
    it to one line would red a legitimate framed-but-wrapped claim; both cells are filed
    with the duration predicate's own line-vs-number coarseness as `#9531`.

    ⚠️ Coarse in BOTH directions, and both are documented rather than discovered:

    * GREEN where the phrase is an OPEN-CLASS quantity noun no alternative knows
      ("the rebuild eats a hair of the arm", "a good deal of the arm's cost"), a share
      of something that is not a measured quantity ("most of these modules are literal
      carriers", "the bulk of these modules"), a bare `bulk` with no subject, a bare
      `this`/`that` demonstrative (`# most of this file is parsed once` — the shapes the
      subject binding exists to keep green), a POSITIONAL share ("the second half of
      the walk", "the left half of the suite", "the second part of the walk" — a phase,
      not a share, and GREEN only while the divided thing is named WITHOUT a possessive
      and the qualifier is a PHASE word: `upper`, `lower` and `other` are share
      adjectives, so `the upper half of the walk` and `the other half of the arm's cost`
      are RED), or a PERCENTAGE WITH NO SUBJECT AT ALL (a bare figure whose value
      is written and whose object is not — written without a concrete number here, so
      this description is not itself a bare figure).
      ⚠️ The phase exemption's NOUN set is closed to the PHASE-NEUTRAL nouns
      (`half|halves|most|part|parts|portion|portions|share|shares|fraction|fractions`),
      so a positional qualifier in front of a SHARE noun is refused:
      `# the first third of the walk`, `# the last quarter of the run` and
      `# the final quarter of the run` are refused
      even though they name a phase, because a position plus a share noun is exactly the
      shape of a share of a measurement (compare `# the first third of the arm's cost`,
      which is RED for both reasons) and the guard cannot tell a phase of three from a
      proportion — so it errs closed. Stated cost, pinned by RED legs, and the remedy is
      one edit (`the first part of the walk`). ⚠️ The exemption was NOT protecting
      `# a third of the arm`: that sentence has no positional qualifier, so it is
      refused by the proportion rule itself — the earlier rationale here claimed
      otherwise and was measured false (`#9528` re-review round 4).
      That last cell is the stated PRICE OF THE BINDING rather than an oversight: the
      `percent` alternative requires `of|in <subject>`, because a bare percentage is
      overwhelmingly an unpacked figure (a coverage or a share of something unnamed)
      and reding it would reword honest comments with no measurement behind the demand.
      The illustration above is deliberately figure-free: a concrete number here would
      be a bare percentage in this very docstring, which the ratio arm reds on. What IS closed is the productive
      morphology — the ordinal SHAPES (`-teenth`, `-tieth`, `hundredth` … `trillionth`,
      each with its plural) together with the enumerated `third` … `twelfth`, each
      percentage SPELLING (the `N %` / `N%` / `N per cent` / `N pct` forms — written
      without a concrete number here, so this description is not itself a bare figure),
      the pronoun ANAPHORS, and
      the multiplicative forms in every numeral SPELLING (`4 times`, `quadruples`,
      `threefold`, `3 in 4`, `9 out of 10`, `3/4`, `2× the walk`) — because those are
      the spellings a rewording reaches for first, and this guard has already been
      retired by a rewording three times. That is why the live comment carries no
      proportion at all rather than a phrasing a list must chase.
    * RED on a real magnitude REGARDLESS of the adjective in front of it ("a fraction of
      this arm" as much as "a small fraction of this arm"), on a size-qualified subject
      ("the entire arm's cost") as much as a bare one, and on `half the arm` as
      much as `half of the arm` — the spellings an adjective-gated, `of`-gated list
      was green on, one of which is the exact sentence this chain produced.
    * RED on a positional share whose divided thing is named with a POSSESSIVE: `the
      second half of the walk's cost` is a share of a measurement, `the second half of
      the walk` is a phase, and the possessive is read off the TAIL rather than inferred
      from the subject slot — that slot only swallowed an ASCII `'s` on a single word.
      The tail read (rather than a one-token pattern) is what closes the MULTI-WORD
      case, which a one-token veto was GREEN on: `the second half of the control arm's
      cost`, `the second half of the gate walk's cost`. (A `whole arm` spelling is
      exempt for a different reason — `_framed` treats `whole arm` as a framing
      marker, so that sentence states its own frame and the exemption is the framing
      rule's, not this one's.) `_MAGNITUDE_SUBJECT` accepts the
      typographic (`walk’s cost`) and plural (`walks' cost`) possessives too, so those
      red for the same reason. The qualifier set is phase words only
      (`first`/`second`/`third`/`left`/`right`/`top`/`bottom`/`last`/`final`);
      `upper`/`lower`/`other` are share adjectives. Stated costs of the tightening, both measured rather than
      discovered later: `the upper half of the walk` reds (a phase sentence with a share
      adjective), and so does any possessive on the divided thing, measurement or not
      (`the second half of the run's output`).
    * GREEN residual, named so it is a stated limit and not a false PASS: a dropped
      apostrophe on a MULTI-WORD divided thing (`the second half of the gate walk cost`)
      matches nothing at all — `_MAGNITUDE_SUBJECT` needs either a possessive or a bare
      measurement noun immediately after the determiner — so it stays GREEN, and the
      phrase carries a figure only if a number is also written. Pinned as a GREEN leg
      with that note, so a later widening reds here and has to update this bullet.
    """
    lines = comment.splitlines()
    found: list[str] = []
    for index in range(len(lines)):
        pair = lines[index : index + 2]
        # Framing is `_framed`'s per-line decision, taken over the same PAIR the phrase
        # is searched in: a phrase that wraps can state its framing on either half.
        if any(_framed(part) for part in pair):
            continue
        window = _flatten_comment("\n".join(pair))
        for match in _MAGNITUDE_WORDS.finditer(window):
            # A POSITIONAL `half` is a phase, not a share (`# the second half of the
            # walk re-reads the sources`) — the qualifier sits outside the match, so
            # this is a post-filter rather than a lookbehind of shifting width.
            # The share NOUN's own extent, not the whole match: `_MAGNITUDE_WORDS` may
            # swallow the subject too (`half of the walk's cost`), so the tail has to be
            # taken after the noun or the veto reads the words that follow the share.
            head = re.match(
                r"(?:half|halves|most|part|parts|portion|portions|share|shares|"
                r"fraction|fractions)\b",
                match.group(),
                re.IGNORECASE,
            )
            if (
                head
                and _MAGNITUDE_POSITIONAL.search(window[: match.start()])
                # …and the divided thing must be named WITHOUT a possessive, or this is
                # a share of a measurement wearing a phase's clothes.
                and not _MAGNITUDE_PHASE_SHARE_TAIL(window[match.start() + head.end() :])
            ):
                continue
            found.append(match.group(match.lastgroup))
    return list(dict.fromkeys(found))


#: One MEASURED row in `_unsettleable_magnitudes`'s paragraph: a COMMIT, and the three
#: wall-clock pairs it produced. The ratio arm below RECOMPUTES every percentage claim
#: from these, so a claim and its figures cannot drift apart (`#9501` review).
_MAGNITUDE_MEASURED_ROW = re.compile(
    r"^[ \t]*-[ \t]*`(?P<sha>[0-9a-f]{7,40})`[^\n]*?:[ \t]*rebuild "
    r"(?P<rebuild>[0-9.]+ s\s*/\s*[0-9.]+ s),\s*arm "
    r"(?P<arm>[0-9.]+ s\s*/\s*[0-9.]+ s)"
    r"(?:,\s*ungated (?P<ungated>[0-9.]+ s\s*/\s*[0-9.]+ s))?\.",
    re.MULTILINE,
)

#: One CLAIM row: the same commit, and the rebuild as a percentage of each reading.
#: `ungated`/`folded` are omitted where the measured row carries no figures for them.
_MAGNITUDE_CLAIM_ROW = re.compile(
    r"^[ \t]*-[ \t]*`(?P<sha>[0-9a-f]{7,40})` arm "
    r"(?P<arm>[0-9]+ % / [0-9]+ %)"
    r"(?:, ungated (?P<ungated>[0-9]+ % / [0-9]+ %))?"
    r"(?:, folded (?P<folded>[0-9]+ % / [0-9]+ %))?\.",
    re.MULTILINE,
)

#: A stated percentage is rounded to a whole point, so recomputation agrees within HALF
#: a point; the full point of slack is for a row whose figures are themselves rounded.
#: ⚠️ It was 1.5 while the comment claimed "within one", and that admitted a materially
#: wrong figure: `51 %` (true value 50.7) written as `52 %` passed (`#9528` re-review,
#: measured) — a tolerance wider than the rounding it excuses is a hole, not slack.
_MAGNITUDE_RATIO_TOLERANCE_PP = 1.0

#: The module docstring's base-anchored figures, which must be the SAME set the
#: `81f5594dc` measurement row carries — the two prose sites state one measurement.
_BASE_MEASUREMENT_PROSE = re.compile(
    r"measured at `(?P<sha>[0-9a-f]{7,40})`.*?the rebuild is "
    r"(?P<rlo>[0-9.]+)–(?P<rhi>[0-9.]+) s of\s+`_narrow_gate_walk_sites`'s own "
    r"(?P<alo>[0-9.]+)–(?P<ahi>[0-9.]+) s",
    re.DOTALL,
)


def _seconds(text: str) -> list[float]:
    """The numbers out of a `1.70 s / 1.81 s` (or `51 % / 58 %`) pair."""
    return [float(number) for number in re.findall(r"\d+(?:\.\d+)?", text)]


def _magnitude_ratios(figures: dict[str, list[float]], framing: str) -> list[float]:
    """The two runs' percentages of the rebuild under ONE reading, row order kept."""
    rebuild = figures["rebuild"]
    if framing == "arm":
        total = figures["arm"]
    elif framing == "ungated":
        total = figures["ungated"]
    elif figures["ungated"]:
        total = [
            own + ungated
            for own, ungated in zip(figures["arm"], figures["ungated"], strict=True)
        ]
    else:
        total = []
    if len(total) != len(rebuild):
        return []
    return [
        100.0 * rebuild_run / total_run
        for rebuild_run, total_run in zip(rebuild, total, strict=True)
    ]


#: The percentage SPELLINGS a bare claim can hide in — a DIGIT or a number WORD, in
#: every spelling the word-form guard closes (`_PERCENTAGE_FIGURE`), so a spelling
#: cannot be closed in one predicate and open in the other. It used to match only the
#: `N %` (space) form, so `17%` / `17 per cent` / `17 pct` outside a claim row were
#: silent (`#9528` review P2), and it used to require a DIGIT, so `sixty-four percent`
#: in the guarded paragraph was unrecomputed (round-2 P3) — one word swapped either way.
_BARE_PERCENTAGE = _PERCENTAGE_FIGURE


def _magnitude_ratio_findings(prose: str) -> list[str]:
    """What is wrong with the ratio claims in `prose`, recomputed from its own rows.

    Three ways to be wrong, all MEASURED rather than argued: a percentage with no
    commit-anchored figures behind it, a percentage that disagrees with the figures of
    the commit it names, and a percentage written OUTSIDE a claim row — which no arm
    recomputes, so it is free to rot. That last shape is the defect this replaces: the
    paragraph it quotes attached a folded-in figure to a reading whose own rows do not
    give it (the folded percentage is the smaller of the three readings, and the
    small-looking one belongs to the ungated pass alone — both recomputed from the rows
    below by the arm that calls this function, never restated here).
    """
    findings: list[str] = []
    measured: dict[str, dict[str, list[float]]] = {}
    for row in _MAGNITUDE_MEASURED_ROW.finditer(prose):
        measured[row.group("sha")] = {
            "rebuild": _seconds(row.group("rebuild")),
            "arm": _seconds(row.group("arm")),
            "ungated": _seconds(row.group("ungated") or ""),
        }
    if not measured:
        findings.append(
            "the prose states no commit-anchored measurement row, so every "
            "percentage in it is unrecomputable"
        )
    claims = list(_MAGNITUDE_CLAIM_ROW.finditer(prose))
    if not claims:
        findings.append(
            "the prose states no recomputed percentage row, so its ratio claims name "
            "neither the commit nor the reading they are a percentage OF"
        )
    for row in claims:
        sha = row.group("sha")
        figures = measured.get(sha)
        if figures is None:
            findings.append(
                f"`{sha}` is named in a percentage row but no measurement row carries "
                "it, so those percentages have no figures behind them"
            )
            continue
        for framing in ("arm", "ungated", "folded"):
            stated = row.group(framing)
            computed = _magnitude_ratios(figures, framing)
            if stated is None:
                if computed:
                    findings.append(
                        f"`{sha}` carries the figures for the {framing} reading and "
                        "the percentage row omits it"
                    )
                continue
            stated_runs = _seconds(stated)
            if not computed:
                findings.append(
                    f"the percentage row states a {framing} reading for `{sha}`, "
                    "whose measurement row carries no figures for it"
                )
                continue
            if len(stated_runs) != len(computed):
                findings.append(
                    f"`{sha}` {framing}: {len(stated_runs)} stated figure(s) for "
                    f"{len(computed)} measurement(s)"
                )
                continue
            for state, recomputed in zip(stated_runs, computed, strict=True):
                if abs(state - recomputed) > _MAGNITUDE_RATIO_TOLERANCE_PP:
                    findings.append(
                        f"`{sha}` {framing}: the prose states {state:g} %, its own "
                        f"figures give {recomputed:.1f} %"
                    )
    # ⚠️ `re.IGNORECASE`: `_MAGNITUDE_WORDS` is case-insensitive, so a DIGIT-ONLY flag
    # here left every SENTENCE-INITIAL `Forty percent` unrecomputed — the sweep and the
    # guard disagreeing about the numeral's case, i.e. one predicate closing a spelling
    # the other leaves open (`#9528` review round 3, P2; 65 of 75 capitalised cells
    # GREEN).
    for bare in re.finditer(_BARE_PERCENTAGE, prose, re.IGNORECASE):
        if not any(row.start() <= bare.start() < row.end() for row in claims):
            findings.append(
                f"{bare.group()!r} is stated in prose that no arm recomputes, so it "
                "is free to rot: state it in a percentage row"
            )
    return findings


#: The measurement paragraph of `_unsettleable_magnitudes`' docstring VERBATIM as it
#: shipped before this correction — figures anchored to "this PR's base" with
#: `69390341f`'s numbers in the parentheses, and `~17 %` attributed to the FOLDED
#: reading, which that arithmetic gives 12 % / 14 % for. Quoted rather than re-created,
#: so the non-vacuity leg below is a statement about the shipped text.
_PREFIX_MAGNITUDE_PARAGRAPH = """\
Measured at this PR's base (fresh process, `_literal_files()`
warm, two runs) with `_literal_files()` replaced by a rebuild over
`_sources().items()`:
the rebuild costs 2.5–3.0 s of `_narrow_gate_walk_sites`'s own 3.9–4.4 s — **over
half** the arm, not a small fraction of it. (The first measurement, at
`69390341f`, read 1.70 s / 1.81 s of 3.35 s / 3.14 s — the same over-half
direction on a box ~15 % faster, so a slightly lower ratio.) It reaches ~17 % only
if "this arm" is read as the whole gate walk, with the ungated pass folded in —
which is not the arm the comment sits in. A ratio whose truth depends on an unstated
reading of "this arm" is the same unsettleable claim as `10.36 s of 10.85 s`,
written so the duration guard misses it.
"""


def test_the_gate_walk_magnitude_ratios_recompute_from_the_rows_they_name():
    """The word form's percentages are computed from the figures beside them (`#9501`).

    ⚠️ **A hand-computed percentage is a claim, and this paragraph shipped one that was
    wrong.** The `#9501` review found it attaching a folded-in figure to the reading
    "with the ungated pass folded in" — a reading that arithmetic does not give: the
    folded percentage is the smaller one, and the small-looking figure belongs to the
    ungated pass ALONE. (Both readings are recomputed by this arm from the rows below
    and never restated here — not even the shipped figure, which the quoted paragraph in
    `_PREFIX_MAGNITUDE_PARAGRAPH` carries verbatim.) (An earlier revision of this paragraph is
    where the `1.70 s / 1.81 s of 3.35 s / 3.14 s` pair and the base-labelled
    `2.5–3.0 s of 3.9–4.4 s` pair sat in two different trees; in the text quoted by
    `_PREFIX_MAGNITUDE_PARAGRAPH` below, the first pair is already parenthesised to
    `69390341f`, so no base mislabelling survives there to find.) A figure whose
    provenance does not survive recomputation is the defect class this file exists to
    close, one alphabet over from the comment it guards.

    So the paragraph carries its measurements as commit-anchored ROWS and its claims as
    rows this arm recomputes: each percentage must equal the rebuild over the reading it
    names, for the commit it names, from figures stated in the same paragraph — and no
    percentage may stand outside such a row.

    Non-vacuity, three legs: (1) the paragraph as it shipped before this correction,
    quoted verbatim in `_PREFIX_MAGNITUDE_PARAGRAPH`, produces findings — it states a
    percentage nothing recomputes; (2) that shipped percentage re-written as a `folded`
    row reds on the ARITHMETIC (the row's own figures give a folded reading well below
    the figure it claimed), so the comparison is live rather than the row-shape check
    alone; (3) the
    module docstring's base-anchored figures must be the same set the `81f5594dc` row
    carries, so the two prose sites cannot name different trees for one measurement
    again.
    """
    prose = inspect.getdoc(_unsettleable_magnitudes) or ""
    findings = _magnitude_ratio_findings(prose)
    assert findings == [], (
        "the magnitude paragraph's ratio claims do not recompute against the figures "
        f"it states: {findings}"
    )

    stale = _magnitude_ratio_findings(_PREFIX_MAGNITUDE_PARAGRAPH)
    assert stale, (
        "the paragraph as it shipped before this correction passes the recomputation "
        "check, so this arm pins nothing"
    )
    assert any("no recomputed percentage row" in finding for finding in stale), (
        f"the shipped text reds for the wrong reason: {stale}"
    )

    folded_claim = (
        "- `69390341f` (the `#9516` merge): rebuild 1.70 s / 1.81 s, "
        "arm 3.35 s / 3.14 s, ungated 10.81 s / 9.90 s.\n"
        "- `69390341f` arm 51 % / 58 %, ungated 16 % / 18 %, folded 17 % / 17 %."
    )
    mismatched = _magnitude_ratio_findings(folded_claim)
    assert any("folded" in finding and "figures give" in finding for finding in mismatched), (
        "a folded percentage that disagrees with its own row's figures passes, so the "
        f"recomputation is not comparing them: {mismatched}"
    )

    # ⛔ …and a percentage that is WRONG BY MORE THAN ROUNDING must red, which is what
    # the tolerance is FOR: at 1.5 points `51 %` (true value 50.7) could be written
    # `52 %` and the arm stayed green, while the comment beside the constant claimed the
    # recomputation "agrees within one" (`#9528` re-review, measured). The tolerance is
    # now the rounding it excuses, and this leg pins the direction.
    drifted = prose.replace("arm 51 % / 58 %", "arm 52 % / 59 %")
    assert drifted != prose, (
        "the magnitude paragraph no longer carries `arm 51 % / 58 %`, so this leg pins "
        "nothing — retarget it at the row it does carry"
    )
    drift = _magnitude_ratio_findings(drifted)
    assert any("arm:" in finding and "figures give" in finding for finding in drift), (
        "a stated percentage 1.3 points off its own row passes, so the tolerance admits "
        f"a materially wrong figure rather than the rounding it exists for: {drift}"
    )

    # ⛔ …and every shape `_magnitude_ratio_findings` can report must hold on EVERY prose
    # site in this file, not only on the paragraph this arm points it at (`#9528`
    # re-review rounds 5-6). The sweep ran on `_unsettleable_magnitudes.__doc__` alone,
    # so the defect class this branch exists to close could be re-inserted into the module
    # docstring and into this arm's OWN docstring: measured, a bare percentage reds now,
    # but so did a CLAIM ROW whose percentage disagrees with its own figures (T4) and a
    # percentage row naming a commit no measurement row carries — both stayed GREEN while
    # this leg selected only the `free to rot` shape. The two row-ABSENCE findings are the
    # only ones dropped here, because these prose sites legitimately carry no claim row;
    # every other shape is kept.
    ROW_ABSENCE = (
        "the prose states no commit-anchored measurement row",
        "the prose states no recomputed percentage row",
    )
    for site, text in (
        ("the module docstring", __doc__ or ""),
        (
            "this arm's own docstring",
            inspect.getdoc(test_the_gate_walk_magnitude_ratios_recompute_from_the_rows_they_name)
            or "",
        ),
        (
            "the `_unsettleable_magnitudes` docstring",
            inspect.getdoc(_unsettleable_magnitudes) or "",
        ),
    ):
        findings_here = [
            finding
            for finding in _magnitude_ratio_findings(text)
            if not finding.startswith(ROW_ABSENCE)
        ]
        assert not findings_here, (
            f"{site} states a percentage that no recomputed claim row holds up: "
            f"{findings_here} — a prose site no arm reads is where this branch's own "
            "defect class lives"
        )

    # The bare-percentage sweep must close every spelling, not one of them: a rotated
    # figure re-spelt `43%` / `43 per cent` / `43 pct` sat outside every row and was
    # silent, which is the rot this third shape exists to catch (`#9528` review P2).
    # The figure is one the paragraph does not carry, and the assertion names it, so the
    # leg reds on the APPENDED text rather than on any pre-existing bare figure.
    for spelling in ("~43 %", "~43%", "~43 per cent", "~43 pct"):
        rotated = f"{prose}\nIt reaches {spelling} only if read as the whole walk.\n"
        rotated_findings = _magnitude_ratio_findings(rotated)
        assert any("43" in finding and "free to rot" in finding for finding in rotated_findings), (
            f"a percentage written `{spelling}` outside every claim row passes the "
            f"sweep, so the closure misses that spelling: {rotated_findings}"
        )

    # …and the same closure in the WORD alphabet: the sweep needed a DIGIT, so a
    # percentage spelled out could be appended to this very paragraph and stay green
    # while its digit twin red (`#9528` review round 2, P3) — and it needed the numeral's
    # CASE too, because `_MAGNITUDE_WORDS` is case-insensitive while `finditer` was not,
    # so every SENTENCE-INITIAL spelling escaped (round 3, P2).
    for spelled_out in (
        "It is sixty-four percent of the arm.",
        "- Forty percent of the arm is the re-parse.",
        "- Sixty-four percent of the arm is the re-parse.",
        "- One hundred per cent of the arm is the re-parse.",
        # the UNIT's punctuation is the same claim: a hyphen or a doubled space must not
        # retire the sweep while the guard reads it (`#9528` review round 4)
        "- The rebuild is 40 per-cent of the arm.",
        "- The rebuild is forty per  cent of the arm.",
        "- The rebuild is 40-percent of the arm.",
    ):
        findings = _magnitude_ratio_findings(f"{prose}\n{spelled_out}\n")
        assert any("free to rot" in finding for finding in findings), (
            f"{spelled_out!r} writes a percentage with a number WORD outside every claim "
            f"row and passes the sweep, so the rot closure is not the same in both "
            f"predicates (or not case-insensitive): {findings}"
        )

    base = _BASE_MEASUREMENT_PROSE.search(__doc__ or "")
    assert base is not None, (
        "the module docstring no longer states the base measurement in the form "
        "`measured at `<sha>` (…, the merge base when the figures were taken) the "
        "rebuild is A–B s of "
        "`_narrow_gate_walk_sites`'s own C–D s`, so the two prose sites cannot be held "
        "to the same rows"
    )
    rows = {row.group("sha"): row for row in _MAGNITUDE_MEASURED_ROW.finditer(prose)}
    assert base.group("sha") in rows, (
        f"the module docstring anchors its figures to `{base.group('sha')}`, and no "
        "measurement row in `_unsettleable_magnitudes`' docstring carries that commit"
    )
    row = rows[base.group("sha")]
    for stated, measured in (
        ((base.group("rlo"), base.group("rhi")), row.group("rebuild")),
        ((base.group("alo"), base.group("ahi")), row.group("arm")),
    ):
        assert sorted(round(float(value), 1) for value in stated) == sorted(
            round(value, 1) for value in _seconds(measured)
        ), (
            f"`{base.group('sha')}`: the module docstring states {stated} and the "
            f"measurement row carries {measured!r} — the two prose sites describe "
            "different measurements of the same tree"
        )

    # ⚠️ The paragraph also makes the claim in WORDS — "the rebuild is over half of it" —
    # and a claim in words is not recomputed by a percentage check. Derive it from the
    # same rows: a re-measurement that puts the rebuild at or below half now reds instead
    # of leaving the sentence standing while every percentage arm stays green
    # (`#9528` re-review round 3, which measured exactly that: a consistent re-measurement
    # at 25 % left both ratio arms and the base-prose arm green).
    for sha, measured_row in rows.items():
        rebuild = _seconds(measured_row.group("rebuild"))
        arm_seconds = _seconds(measured_row.group("arm"))
        assert rebuild and arm_seconds and len(rebuild) == len(arm_seconds), (
            f"`{sha}`: the row carries {measured_row.group('rebuild')!r} of "
            f"{measured_row.group('arm')!r}, so the over-half claim cannot be derived"
        )
        assert all(
            100.0 * value / total > 50.0
            for value, total in zip(rebuild, arm_seconds, strict=True)
        ), (
            f"`{sha}`: the paragraph calls the rebuild 'over half' the arm and these rows "
            f"put it at or below half ({measured_row.group('rebuild')} of "
            f"{measured_row.group('arm')}) — the words and the figures disagree"
        )


def test_the_gate_walk_comment_asserts_no_unmeasured_magnitude():
    """A magnitude in WORDS is still a claim, and this one did not reproduce (`#9501`).

    ⚠️ **The gap `#9516` left, executed rather than argued.** Its guard holds the
    comment to its DURATIONS; the comment it produced pins a *proportion* in words
    instead — "the rebuild is a small fraction of this arm's cost" — which does not
    reproduce against the row that names its figures' tree, `81f5594dc`
    (`_unsettleable_magnitudes`' docstring carries the rows and the percentages
    recomputed from them; no figure is copied here, because a copy is what nothing
    recomputes — `#9528` re-review: this docstring said "at this head", which is a tree
    the measurement never saw). So the guard moved
    the figure out of the sentence and the sentence kept the claim, which is the defect
    class this issue chain exists to end: a magnitude stated in prose with nothing
    measuring it. The rule is the same one the duration arm states, applied to the other
    alphabet.

    Non-vacuity is its own leg in
    `test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced` (below): the sentence
    `#9516` left behind REDs, a magnitude word carrying its framing stays GREEN, a
    framing marker ELSEWHERE in the comment does not clear it, and the live comment is
    GREEN under the same predicate.
    """
    census = _call_time_census()
    comment = _gate_walk_comment()
    # ⚠️ The floor reads the `#`-line projection, not `comment`: `comment` is the whole
    # SOURCE (`_prose_of`), so `assert comment` could never fail and this arm would
    # advertise an anti-vacuity floor it does not have — the defect main documents at
    # `_gate_walk_comment_lines` and reached again here (cycle-2 review).
    assert _gate_walk_comment_lines(census._narrow_gate_walk_sites), (
        "`_narrow_gate_walk_sites` carries no `#` comment any more, so this arm "
        "asserts nothing — the mechanism it states was the whole point of the change"
    )
    assert not _unsettleable_magnitudes(comment), (
        "the gate-walk comment pins a magnitude in WORDS, which no number makes "
        f"settleable ({_unsettleable_magnitudes(comment)}) — state the figure with "
        "the framing that produced it (whole arm / control step / `--durations=0` / "
        "in-suite), or state only the mechanism. A proportion without a number is "
        "not a weaker claim than a duration without framing, it is the same claim."
    )


def test_the_gate_walk_comment_pins_no_wall_clock_figure():
    """A pinned duration must carry the framing that produced it (`#9506`).

    ⚠️ **The `#9506` defect was a claim a re-verifier cannot settle.** The
    refactor's comment read *"No magnitude is stated on purpose: the redundant
    rebuild is a fraction of this arm (a re-verifier measured ~2.6 s of ~17 s on one
    box)"* — an assertion that no magnitude is stated, immediately followed by one,
    on the exact axis the change exists to fix — and its predecessor at
    `fa84a473d:990-992`, the last main commit to carry it (NOT the merge base, which
    greps `0` for `10.36`), read
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
    """Non-vacuity for `_unframed_durations` AND `_unsettleable_magnitudes`, on the
    PRE-`#9516` MAIN — not an intermediate commit of this branch (`#9506`, `#9501`).

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

    ⚠️ **The word-form legs at the end of this arm are the same non-vacuity for the
    other ALPHABET (`#9501`), and they carry their own premise.** Each feeding string is
    first shown to be INVISIBLE to `_unframed_durations` (no digit, no unit), because a
    word-form leg whose input the digit guard already reds would prove nothing about the
    word form. The live comment is checked under that predicate too, so the two live arms
    cannot diverge from their non-vacuity arm unnoticed.
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
    # ⚠️ The SENTENCE-END boundary the docstring STATES, executed. The `#9522` review
    # measured a prose defect here: the docstring said `--durations=0 wasn't really
    # used` "split across a `.`" is FRAMED while printing that example with no period,
    # where `_DENIAL_STRONG` reads `wasn't` and the line REDS. Prose and predicate
    # disagreed in the same sentence and no arm could tell, so the FRAMED example is now
    # read out of the docstring itself and both sides of the boundary are run.
    docstring_prose = " ".join((_unframed_durations.__doc__ or "").split())
    assert "`--durations=0. wasn't really used`" in docstring_prose, (
        "the docstring no longer states the boundary this arm executes — the FRAMED "
        "example must carry the `.`, because the unpunctuated form is a single "
        "sentence and reds"
    )
    for new_sentence in (
        # the `.` ends the marker's sentence, so a strong denial beyond it does not
        # reach the marker: this is the GREEN side the docstring states
        "# 10.36 s --durations=0. wasn't really used",
        "# 10.36 s --durations=0. never done here",
    ):
        assert not _unframed_durations(new_sentence), (
            f"{new_sentence!r} states its denial in a NEW sentence, so the marker's "
            f"own sentence frames the figure ({_unframed_durations(new_sentence)}) — "
            "that `.` is the only reason the docstring's example is framed"
        )
    for one_sentence in (
        "# 10.36 s --durations=0 wasn't really used",
        "# 10.36 s --durations=0 never done here",
    ):
        assert _unframed_durations(one_sentence) == ["10.36 s"], (
            f"{one_sentence!r} is the SAME words with the `.` dropped, so the denial "
            f"sits in the marker's own sentence and must red "
            f"({_unframed_durations(one_sentence)}) — otherwise the boundary the "
            "docstring states is not the one the predicate implements"
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

    # ── the SECOND predicate's non-vacuity (`#9501`): the same rule in the OTHER
    # ALPHABET. `_unframed_durations` matches digits, so every string below is invisible
    # to it — and that premise is ASSERTED, not assumed, before each word-form leg leans
    # on it, because a word-form leg whose input the digit guard already reds proves
    # nothing about the word form.
    #
    # This is the sentence `#9516` left behind, VERBATIM as `origin/main` wraps it
    # (`git show origin/main:tests/_about_call_time_census.py`), WRAP INCLUDED: it breaks
    # between `this` and `arm's`, so a predicate that matches per line is GREEN on the
    # real defect while RED only on a re-wrapped copy of it.
    survived = (
        "# `_literal_files()` is already parsed and cached; re-reading `_sources()` and\n"
        "# rebuilding the same 29 `_File`s is redundant work for byte-identical output —\n"
        "# the same `_File` values over the same `_sources().items()` order, so only object\n"
        "# identity changes. No magnitude is pinned: the rebuild is a small fraction of this\n"
        "# arm's cost, and a wall-clock number in a comment is the claim that rots."
    )
    assert not _unframed_durations(survived), (
        "the premise of this leg: the digit guard is BLIND to the sentence `#9516` left "
        "behind — if it were not, the word-form arm would be redundant"
    )
    assert _unsettleable_magnitudes(survived) == ["small fraction"], (
        "the word-form predicate must red on the sentence `#9516` left behind, as it is "
        "WRAPPED on `origin/main` (`the rebuild is a small fraction of this` / `# arm's "
        "cost`) — a predicate that only matches this phrase on one long line passes on "
        "the comment the file actually carries, which is the fail-open shape `#9501` is"
    )
    # ⚠️ This leg carries the lesson from the first version of the arm: its fixture bound
    # the proportion to `it`, which is NOT in `_MAGNITUDE_SUBJECT`, so the predicate
    # matched nothing and the assertion passed with the framing exemption deleted
    # outright — green for a reason other than the one its message claims. The phrase is
    # bound to THE ARM here, so the framing marker is the only thing clearing it, and the
    # unframed twin below is executed to prove exactly that.
    framed_magnitude = (
        "# the arm measured 14.4 s whole arm, --durations=0: a small fraction of the arm"
    )
    assert not _unsettleable_magnitudes(framed_magnitude), (
        "a magnitude word that carries the framing of its figure must stay allowed — "
        "otherwise this is a style rule against adverbs rather than a rule about "
        "settleable claims"
    )
    assert _unsettleable_magnitudes(
        "# the arm measured 14.4 s: a small fraction of the arm"
    ) == ["small fraction"], (
        "the leg above must be green BECAUSE OF THE FRAMING, not because the phrase is "
        "outside the predicate's reach — this is the identical sentence with the two "
        "framing markers (`whole arm`, `--durations=0`) removed, and it must red"
    )

    # ⚠️ Two spellings the FIRST version of the predicate was GREEN on, and the first of
    # them is the sentence this chain produced verbatim. The adjective was mandatory
    # (`small fraction`) and alternative 3 required the literal `of`, so dropping one
    # word — or stating the refuted magnitude in the author's own measured phrasing
    # (`over half the arm`) — retired the guard. Executed here rather than argued,
    # because "the list covers its class" is the claim that was false.
    for spelling in (
        "# the redundant rebuild is a fraction of this arm, and a wall-clock number is "
        "the claim that rots.",
        "# the rebuild is over half the arm, so reusing the parsed files is the fix.",
    ):
        assert not _unframed_durations(spelling), (
            "the premise of this leg: neither spelling carries a duration, so the digit "
            "guard is blind to it and only the word-form predicate can red"
        )
        assert _unsettleable_magnitudes(spelling), (
            f"the word-form predicate must red on {spelling!r} — `a fraction of this "
            "arm` is the exact sentence this chain produced and `half the arm` is the "
            "measured truth it was refuted with; a pattern that needs an adjective, or "
            "needs the literal `of`, is green on the defect one word away"
        )

    # ⚠️ The percentage slot was DIGIT-ONLY, so a proportion re-spelt with a number WORD
    # retired the guard while its digit twin red — the same one-word swap, one alphabet
    # over (`#9528` review round 2, P2: `# forty per cent of the arm` was GREEN, and it
    # could be pasted into the live comment with 48 tests still passing).
    for spelled, expected in (
        ("# 40 per cent of the arm is the re-parse.", ["40 per cent"]),
        ("# forty per cent of the arm is the re-parse.", ["forty per cent"]),
        ("# 40 pct of the arm is the re-parse.", ["40 pct"]),
        ("# forty pct of the arm is the re-parse.", ["forty pct"]),
        ("# 40% of the arm is the re-parse.", ["40%"]),
        ("# sixty-four percent of the arm is the re-parse.", ["sixty-four percent"]),
        ("# one hundred per cent of the arm is the re-parse.", ["one hundred per cent"]),
    ):
        assert not _unframed_durations(spelled), (
            "the premise of this leg: a percentage carries no digit-and-unit duration, "
            "so only the word-form predicate can red it"
        )
        assert _unsettleable_magnitudes(spelled) == expected, (
            f"the word-form predicate must red on {spelled!r} as {expected!r} "
            f"({_unsettleable_magnitudes(spelled)}) — a percentage whose NUMERAL is a "
            "word is the identical claim to its digit twin, and a slot that needs a "
            "digit is retired by one word"
        )

    # …and the other direction, so this is a rule about CLAIMS rather than about
    # adverbs: prose about WHICH modules are walked asserts no magnitude, and reding on
    # it would fail a merge gate over a sentence holding no figure (with "state the
    # framing that produced it" as the remediation).
    for prose in (
        "# most of these modules are literal carriers, so the index reads them first.",
        "# most of the 29 files take the second branch; the rest fall through.",
        "# the bulk of these modules are literal carriers, so the index reads them.",
        "# reads them in bulk from the disk",
        "# the length of the walk grows with the module set",
        "# the path of the walk goes through the constant",
        "# the depth of the walk is bounded by the package",
        "# most of the time this is the second branch",
        "# most of this file is parsed once",
        "# the run is cached",
        "# half of these modules are synthetic",
        "# doubles entries in the list before it walks them",
        # the NUMERAL-adjacent shapes a shape-only pattern gets wrong (cycle-3 review):
        "# the grid is 3 x 4 sites",
        "# the viewport is 1024 x 768",
        "# the matrix is 2 × 2",
        "# a factor of safety on the walk",
        "# 0x1f is the mask",
        "# a 2x2 matrix",
        # a POSITIONAL share is a phase, not a share (cycle-3 review):
        "# the second half of the walk re-reads the sources",
        "# the left half of the suite is unused",
        "# the second part of the walk is the ungated pass",
        "# the first half of the work is the ungated pass",
        # ...and the STATED RESIDUAL (#9528 review round 6): a dropped apostrophe on a
        # MULTI-WORD divided thing matches nothing — no possessive to bound the subject
        # and no bare measurement noun right after the determiner — so it stays GREEN.
        # Widening `_MAGNITUDE_SUBJECT` to see it must red this leg and update the
        # docstring's residual bullet in the same change.
        "# the second half of the gate walk cost is the parse",
        # ...and the STATED RESIDUAL the `#9528` re-review added beside it: a
        # DEMONSTRATIVE names the divided thing rather than owning it, so a phase of it
        # keeps the exemption — the same boundary as `the second half of the walk`. The
        # possessive PRONOUN spelling of the same sentence is RED below, and the two
        # legs are what keep the difference from being quietly widened one way or the
        # other.
        "# the second half of this cost is the parse",
        "# the second half of that work is the parse",
        "# the second half of these costs is the parse",
        "# the second half of the costs is the parse",
        "# the second half of a run re-reads the sources",
        "# the second half of all the work re-reads the sources",
        # ...and a positional `last`/`final` is a PHASE word, not a share adjective
        # (`# the last half of the walk` was refused one word from its pinned twin; `#9528`
        # re-review round 4).
        "# the last half of the walk is the ungated pass",
        "# the final half of the walk is the ungated pass",
        "# the second half of it is the parse",
        # ...and a possessive pronoun that owns something in a LATER clause is not a
        # possessive on the divided thing: scanning the whole tail window red this
        # sentence, one word from the GREEN leg above (`#9528` re-review). The pronoun
        # check is scoped to the divided thing's own determiner position.
        "# the second half of the walk re-reads its sources",
        # ...and the SAME false positive in the two branches the re-review found still
        # position-blind: an apostrophe or a measurement noun in a LATER clause. Each is
        # now GREEN, and each differed from a RED leg only by one word's position
        # (`#9528` re-review round 3).
        "# the second half of the walk re-reads the arm's output",
        "# the second half of the suite validates the work of the extractor",
        # a proportion bound to something that is not a measured quantity:
        "# e.g. 3 in 4 cases",
        "# most of these modules are run in the suite",
        # the NUMERAL-adjacent shapes a shape-only pattern gets wrong — `N x` BINDS to a
        # subject or to a `than <subject>` comparison, so prose naming neither stays
        # GREEN (`#9501` review measured all three reding with the remediation "state
        # the framing that produced it" for a sentence holding no figure):
        "# reads 2x per site",
        "# 2x the modules are walked",
        "# the rebuild is 2x faster",
    ):
        assert not _unsettleable_magnitudes(prose), (
            f"{prose!r} asserts no magnitude, so the predicate must stay GREEN on it — "
            "an unscoped `bulk`, an `\\w+th` ordinal shape, a bare `most of the time`, "
            "a bare `N x M`, a `factor of safety` or a positional `half` reds here and "
            "the diagnosis it prints describes a figure the sentence does not carry"
        )

    # ⛔ …and the SAME positional shape is a SHARE the moment the divided thing is named
    # with a possessive, or the qualifier is a share adjective (`#9528` review round 5,
    # P1 — every one of these measured GREEN before the tail veto, i.e. one word in front
    # of the noun retired the guard). Both directions are pinned, because the fix for one
    # is what reds the other: `# the upper half of the walk` is now RED on purpose.
    for share in (
        "# the other half of the arm's cost is the parse",
        "# the upper half of the walk",
        "# the second half of the walk's cost is the parse",
        "# the second half of the walk’s cost is the parse",  # typographic apostrophe
        "# the second half of the walks' cost is the parse",  # plural possessive
        "# the second half of the walk cost is the parse",  # dropped apostrophe
        # ...and the MULTI-WORD divided thing, which a one-token tail veto was GREEN on:
        # the possessive sits one word further out, which is the whole reason the tail
        # is read in code rather than matched against a single token.
        "# the second half of the control arm's cost is the parse",
        "# the second half of the gate walk's cost is the parse",
        "# the second half of the gate walk’s cost is the parse",
        "# the second half of the gate walks' cost is the parse",
        # ...and a possessive on a NON-measurement: the stated cost of the rule, not a
        # shape it is trying to catch, and it is a share of a thing either way.
        "# the second half of the run's output is the parse",
        # ...and a POSSESSIVE PRONOUN on the divided thing (`#9528` re-review): the
        # determiner strip consumed `its`/`their`/`our` as if they were `the`, which put
        # the measurement noun at position 0 — where the dropped-apostrophe check is
        # suppressed — and read the whole share GREEN. `of its output` carries no
        # measurement noun at all, so the pronoun has to red on its own.
        "# the second half of its cost is the parse",
        "# the second half of their work is the parse",
        "# the second half of our total is the parse",
        "# the second half of its output is the parse",
        # ...and a QUANTIFIER in front of that pronoun (`of all its cost`, `of both their
        # runs`) defeated the strip one layer down — the same shape as the subject slot's
        # quantifier layer, fixed the same way (`#9528` re-review round 4).
        "# the second half of all its cost is the parse",
        "# the second half of both their runs is the parse",
        "# the second half of all our cost is the parse",
    ):
        assert _unsettleable_magnitudes(share), (
            f"{share!r} is a SHARE of a measured quantity wearing a phase's clothes — a "
            "possessive on the divided thing (ASCII, typographic, plural or dropped) or a "
            "share adjective is what separates it from `the second half of the walk`, and "
            "without both constraints one word in front of the noun retires this guard"
        )

    # ⚠️ STATED COST of the CLOSED phase-noun set, pinned rather than implied: a
    # positional qualifier in front of a SHARE noun names a phase and is refused anyway,
    # because admitting `third`/`quarter` to the phase set would retire the guard for
    # `# a third of the arm` — the share class this chain enumerates. `the walk` has
    # three passes, so `the first third of the walk` is the natural spelling and the
    # remedy is one edit (`the first part of the walk`) (`#9528` re-review round 3).
    for refused_in_theory_only in (
        "# the first third of the walk is the ungated pass",
        "# the last quarter of the run is the cache",
        "# the final quarter of the run is the cache",
    ):
        assert _unsettleable_magnitudes(refused_in_theory_only), (
            f"{refused_in_theory_only!r} names a PHASE and is refused anyway — that is "
            "the stated cost of the closed phase-noun set, so if it ever goes GREEN the "
            "docstring's residual bullet is stale and the share class it protects "
            "(`# a third of the arm`) has been admitted to the exemption"
        )

    # ⚠️ The PRODUCTIVE proportion spellings are closed, not only the adjectives in front
    # of them. Making the adjective optional fixed `a fraction of this arm`; the
    # vocabulary was still a hand-list, so `two thirds of the arm` escaped — the same
    # one-synonym-away fail-open, one vocabulary over. The ORDINAL SET and the percentage
    # spellings are enumerated because they are closed classes (the `\\w+th` SUFFIX is
    # not — cycle-2 review measured it reading `length`, `path` and `depth` as
    # proportions, hence the GREEN legs above). ⚠️ This does NOT make the list closed — an
    # open-class quantity noun ("a hair of the arm", "a good deal of the arm's cost")
    # still passes, and it is documented rather than claimed otherwise: what is closed is
    # the morphology, not the vocabulary.
    for spelling in (
        "# two thirds of the arm is the re-parse.",
        "# the majority of the arm's cost is the AST parse.",
        "# the rebuild is a sliver of this walk's work.",
        "# two fifths of the arm is the re-parse.",
        "# nine tenths of this arm's cost is the ungated walk.",
        "# 80% of the walk's work is the AST parse.",
        "# 40 per cent of the arm is the re-parse.",
        "# 40 pct of the walk's work is the AST parse.",
        "# the bulk of the walk is the re-parse",
        "# the lion's share of the arm's cost is the parse",
        # ⚠️ The ORDINAL MORPHOLOGY, not an enumeration (`#9501` review): an enumerated
        # list that stopped at `thousandths?` was GREEN on `a twentieth of the arm` and
        # `a millionth of the arm` — the same "the list is complete" claim that was
        # already false twice. `-teenth` / `-tieth` / the magnitude scale end no ordinary
        # English noun, so those SHAPES close the class `length`/`depth`/`path` cannot.
        "# a twentieth of the arm is the re-parse.",
        "# a thirtieth of this arm's cost is the AST parse.",
        "# a fortieth of the walk is the re-parse.",
        "# a nineteenth of this walk's work is the parse.",
        "# a millionth of the arm",
        # ⚠️ The SUBJECT's qualifier slot (`#9501` review): one word inserted into the
        # defect sentence — `the ENTIRE arm's cost` — was GREEN, and a multi-word
        # possessive (`the gate walk's cost`) was too.
        "# the rebuild is a small fraction of the entire arm's cost",
        "# a small fraction of the full arm's cost",
        "# the rebuild is a small fraction of the gate walk's cost",
        # …and the RATIO family in every numeral SPELLING (`#9501` review: the
        # `one|two|three|four in two|…|five` window was GREEN on `one in ten`).
        "# two in ten of the walks pay the parse.",
        "# five in six of the walks pay the parse.",
        # ⚠️ The DETERMINER slot is a closed list, and closing one spelling at a time is
        # how this guard was retired before: `all|every|each|both|some|any|no` were
        # missing, so `# half of all runs pay the parse` shipped GREEN while the `these`
        # spelling red (`#9528` re-review round 3).
        "# half of all runs pay the parse.",
        "# half of every run pays the parse.",
        "# the rebuild is half of all its cost.",
        # ⚠️ The indefinite article is a determiner too: `# half of a run is the parse`
        # shipped GREEN while `# half of runs pay the parse` red (`#9528` re-review
        # round 4).
        "# half of a run is the parse",
        "# most of a walk is the parse",
        # …and the DIGIT ordinal is the same claim in the other alphabet: the alphabetic
        # morphology is closed (`-teenth`/`-tieth`/`hundredth`…), so `# a 10th of the arm`
        # was GREEN beside `# a tenth of the arm` (`#9528` re-review round 4).
        "# a 10th of the arm is the re-parse.",
        "# a 3rd of the arm is the re-parse",
        # …and the MULTIPLICATIVE slot stopped its numeral vocabulary at `ten` while the
        # percentage slot knew the teens and tens — one numeral open in one predicate and
        # closed in the other, the failure this file's "one constant serves both" rule
        # exists to prevent. `_MAGNITUDE_NUMERAL` now reads `_WORD_NUMERAL`.
        "# re-parsing is eleven times the walk",
        "# the rebuild is a factor of eleven over the walk's work.",
        "# the re-parse is twentyfold the walk",
        "# three in eleven of the walks pay the parse.",
        # ⚠️ The DEMONSTRATIVE determiner (`#9528` re-review): `that`/`those` were
        # missing from the subject slot's determiner list, so ONE determiner swapped a
        # RED for a GREEN on the same sentence — the one-synonym-away fail-open, one
        # vocabulary over.
        "# the rebuild is a small fraction of that arm",
        "# most of those arms is the parse",
        "# the rebuild is most of that run",
    ):
        assert _unsettleable_magnitudes(spelling), (
            f"{spelling!r} states a proportion of a measured quantity and must red — a "
            "list that knows only `fraction|share|portion|part` is one synonym away "
            "from the fail-open that retired the previous version of this guard"
        )

    # ⚠️ The ANAPHOR is the cycle-3 P0: the proportion bound to a PRONOUN — one word from
    # the sentence `#9516` left behind — was GREEN, and the earlier fix had *re-bound the
    # fixture* to `the arm` rather than closing the slot, which made the arm green for a
    # reason other than the one its message claims. Both directions here.
    for anaphoric in (
        "# the rebuild is a small fraction of it",
        "# the rebuild is a small fraction of its cost",
        "# the rebuild is most of them",
    ):
        assert _unsettleable_magnitudes(anaphoric), (
            f"{anaphoric!r} binds the proportion to a PRONOUN standing in for the arm, "
            "which is the sentence `#9516` left behind with one word changed — a "
            "subject list that knows only the nouns is green on it"
        )

    # ⚠️ The MULTIPLICATIVE forms: each binds to a measured quantity, each is a closed
    # class, and each was GREEN (`cycle-2 review`). `twice as long as the walk` is one
    # keystroke from the sentence `#9516` left behind, so a guard that knows only
    # `a fraction of` is retired by the next re-wording. ⚠️ The NUMERAL is a shape, not a
    # hand-list (`cycle-3 review`): `4 times`, `10 times`, `quadruples`, `threefold`,
    # `3 in 4`, `9 out of 10` and `3/4` were all GREEN while their neighbours red.
    for spelling in (
        "# the rebuild takes twice as long as the walk.",
        "# the rebuild doubles this arm's cost.",
        "# the rebuild is 2× the walk.",
        "# re-parsing is 4 times the work of the walk",
        "# re-parsing is four times the work of the walk",
        "# re-parsing is 10 times the walk",
        "# re-parsing quadruples the work",
        "# re-parsing quintuples the arm's cost",
        "# the re-parse is threefold the walk",
        "# the re-parse is 3-fold",
        "# this is an order of magnitude more than the walk.",
        "# the rebuild is a factor of two over the walk's work.",
        "# three in four of the walks pay the parse.",
        "# 3 in 4 of the walks pay the parse",
        "# 9 out of 10 runs pay the parse",
        "# 3/4 of the arm is the re-parse",
        "# 2x faster than the walk",
        "# the walk is twice as fast as the gate",
    ):
        assert _unsettleable_magnitudes(spelling), (
            f"{spelling!r} states a magnitude bound to a measured quantity and must red "
            "— a multiplicative comparison is the same unsettleable claim as a share, "
            "and every spelling of its numeral is the same claim in another alphabet"
        )

    # ⚠️ **The framing window is PER LINE, and this leg is the cycle-2 P0.** The first
    # version short-circuited on `any(marker in comment …)`, so ONE framing mention
    # anywhere in the function cleared every word-form magnitude in it — the whole-text
    # hole `#9522` closed for durations, reopened in the other alphabet. Measured then:
    # the refuted sentence with `(measured once whole arm)` three lines below came back
    # GREEN.
    assert _unsettleable_magnitudes(
        "# the rebuild is a small fraction of this\n"
        "# arm's cost, so reuse the parsed files.\n"
        "#\n"
        "# (measured once whole arm)"
    ) == ["small fraction"], (
        "a framing marker on ANOTHER line must not clear this line's magnitude — "
        "otherwise one `whole arm` anywhere in a comment frames every proportion in it, "
        "which is the whole-text window the duration predicate's per-line `_framed` "
        "exists to refuse"
    )
    # …and the DENIAL veto is inherited rather than reimplemented: a marker whose own
    # sentence denies it is not framing, so this REDs instead of framing itself.
    assert _unsettleable_magnitudes(
        "# whole arm was never measured: a small fraction of the arm"
    ), (
        "a DENIED framing marker must not frame a magnitude — `_framed` is what decides "
        "framing here, precisely so the `_denied` veto and its three windows come with "
        "it instead of being re-derived"
    )
    # ⚠️ **The ACCEPTED COST of the wrap window, pinned so it cannot silently widen.**
    # The search window is the phrase's line plus the one after it, because the refuted
    # sentence wraps INSIDE the phrase — so a marker on the NEXT line frames a complete
    # same-line magnitude. That is one line wide where the first version's hole was the
    # whole function (`cycle-3 review` measured this cell and asked for it to be pinned
    # rather than left implicit, which is what this leg does). If a later edit widens the
    # window to ±2 lines, this leg stays GREEN and the three-line leg above reds — which
    # is the pair that makes the boundary executable in both directions.
    assert not _unsettleable_magnitudes(
        "# the rebuild is a small fraction of the arm\n"
        "# whole arm timing lives in the issue"
    ), (
        "the wrap window is the phrase's line plus the NEXT one, so a framing marker "
        "there clears it — this is the ACCEPTED cost (`#9531`). If this leg reds, the "
        "window was narrowed to one line and a framed-but-wrapped claim now reds; if "
        "the three-line leg above reds instead, the window widened"
    )
    assert not _unsettleable_magnitudes(_gate_walk_comment()), (
        "the live comment must be green under the WORD-form predicate too — the same "
        "divergence guard the duration leg above applies, for the predicate the other "
        "live arm asserts"
    )


def test_the_narrow_gate_walk_rebuild_buys_nothing():
    """The gate-walk comment's MECHANISM, executed — no figure left to rot (`#9501`).

    ⚠️ **The comment pinned a bare count while denying it pinned anything.** On
    `origin/main` it read "`_literal_files()` is already parsed and cached; re-reading
    `_sources()` and rebuilding the same 29 `_File`s is redundant work for
    byte-identical output … No magnitude is pinned: the rebuild is a small fraction of
    this arm's cost" — so a number with NO UNIT sat two lines from a sentence claiming
    no magnitude was pinned, and no arm measured it: the first module added to or
    removed from the module set made the count false with every arm still green. (The
    same sentence also carried the refuted *word-form* proportion the arm above reds
    on, which is the same defect in the second alphabet.)

    The counts are DETERMINISTIC, so the fix is to execute the claim they stood for
    rather than to restate them: walking `_sources().items()` and rebuilding the same
    `_File`s yields the same values in the same order, so only object identity differs.
    That is measured here by substituting the rebuild for `_literal_files()` and running
    the REAL `_narrow_gate_walk_sites` body — the derivation is not copied into this arm,
    so it cannot drift from the walk the comment describes (the blindness channel
    `test_the_ungated_walk_reuses_the_gated_derivations` exists to catch).

    Falsifiable in one half: the substitute must actually have run, or this compares a
    walk to itself. The identity half is a HARNESS PREMISE, not a measurement — the
    `_File`s are built in this body, so identity with the cached ones is impossible by
    construction and that assertion cannot fail while the harness is what it says it is.
    It is kept, and labelled as the premise the `rebuilt == reused` reading depends on,
    rather than presented as a second falsifiable direction (`#9501` review).
    """
    census = _call_time_census()
    reused = census._narrow_gate_walk_sites()
    original = census._literal_files
    made: list[tuple] = []

    def _rebuild_from_sources():
        # The alternative the comment rules out, spelled the way `_literal_files()`
        # spells the reuse: same `EDGE` filter, same `_sources().items()` order.
        files = tuple(
            census._File(path, str(path.relative_to(census.ROOT)), source)
            for path, source in census._sources().items()
            if census.EDGE in source
        )
        made.append(files)
        return files

    try:
        census._literal_files = _rebuild_from_sources
        rebuilt = census._narrow_gate_walk_sites()
    finally:
        census._literal_files = original

    assert made, (
        "`_narrow_gate_walk_sites` no longer reads `_literal_files()`, so the rebuild "
        "substituted above never ran and the comparison below is a walk against itself"
    )
    reuse_objects = original()
    assert [file.rel for file in made[-1]] == [file.rel for file in reuse_objects], (
        "the rebuild visits a different module set or order than `_literal_files()` "
        f"({len(made[-1])} vs {len(reuse_objects)} modules) — the comment's claim is "
        "that it is the SAME values over the SAME order, so this premise no longer holds"
    )
    # HARNESS PREMISE, not a measurement: these `_File`s are built in this body, so they
    # cannot be the cached objects. Kept as the premise that makes `rebuilt == reused`
    # mean "same values, different objects" — if an edit ever made the substitute hand
    # back the cached files, this arm would silently measure a walk against itself.
    assert not any(a is b for a in made[-1] for b in reuse_objects), (
        "the substitute handed back the REUSED `_File` objects, so the premise this arm "
        "is read under no longer holds — `rebuilt == reused` would then mean nothing"
    )
    assert rebuilt == reused, (
        "walking `_sources().items()` and rebuilding the same `_File`s does NOT yield "
        f"the same derivations ({[len(part) for part in rebuilt]} vs "
        f"{[len(part) for part in reused]}) — the gate-walk comment's mechanism claim "
        "is false at this head and the comment must be restated"
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


#: The last `main` commit whose `tests/_about_call_time_census.py` blob carries the
#: pre-`#9516` figure `measured 10.36 s of the control's 10.85 s`.
#:
#: ⚠️ **Named by COMMIT, not by "the merge base" or by `origin/main`** (`#9522`
#: re-review). Measured at this PR's base: `git show origin/main:tests/
#: _about_call_time_census.py | grep -c 10.36` is `0` — `#9516` already removed the
#: figure — so naming the base as its carrier points a re-verifier at a tree that
#: does not say what the arms below claim it says. That is the citation-rot class
#: `test_the_gate_walk_anchors_name_the_commit_that_carries_the_figure` pins.
_GATE_WALK_FIGURE_CARRIER = "fa84a473d"

#: Sentences that name a TREE (the merge base) as the carrier of that figure. Both
#: forms shipped in this file's own prose and both were false at the head that
#: shipped them; the carrier is a COMMIT.
_STALE_GATE_WALK_ANCHORS = ("on the merge base", "merge-base predecessor")


def _git_blob(rev: str, path: str) -> str | None:
    """`git show <rev>:<path>`, or None where THIS checkout cannot reach it.

    ⛔ No `git fetch` fallback, deliberately: `test_the_gate_walk_anchors_…`'s
    sibling in `tests/test_9397_about_edge_properties.py` MEASURED that
    `git fetch --depth=N` on a full clone REWRITES it shallow (6106 commits of
    `origin/main` cut to 120), a destructive side effect on the checkout under test
    from an arm that runs in `core`. An unreachable rev is reported as unverified by
    the caller instead.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover — no git binary
        return None
    return done.stdout if done.returncode == 0 else None


def test_the_gate_walk_anchors_name_the_commit_that_carries_the_figure():
    """The prose anchors name a tree that still has the text (`#9522`).

    ⚠️ **This file's own claim labels rotted into the same false anchor twice.** §23
    item 23 said the pre-`#9516` figure was `measured 10.36 s of the control's 10.85
    s` "on the merge base", and the docstring of
    `test_the_gate_walk_comment_pins_no_wall_clock_figure` called it "its merge-base
    predecessor" — while 12 lines apart §23 names `fa84a473d:990-992` as the last main
    commit to carry it. Both anchors cannot be true: `git show
    origin/main:tests/_about_call_time_census.py | grep -c 10.36` is `0` at this base.
    The sibling arm `test_the_gate_walk_comment_arm_reds_on_the_text_it_replaced` was
    already corrected for exactly this ("the ANCHOR is named by COMMIT, not by
    `origin/main`"); these two sentences were the ones it left behind, so a re-verifier
    following them landed on a tree that contradicts the arm.

    Three assertions, because a prose pin that can be satisfied by DELETION is not a
    pin: (1) no carrier sentence names a tree, (2) the commit anchor IS named, and
    (3) the named commit really carries the figure while `origin/main` does not — the
    measurement the prose states, executed where this checkout can reach the objects.

    Non-vacuity: on the pre-fix text of this file, assertion (1) reds on BOTH strings
    (measured by re-inserting each into the module docstring). The `git` half cannot
    red for that edit — it is the anchor's VERIFICATION, not the defect — and it
    SKIPS (with a message saying so) where `fa84a473d` is unreachable, e.g. a shallow
    clone, rather than being silently green.
    """
    prose = "\n".join(
        (
            __doc__ or "",
            inspect.getdoc(test_the_gate_walk_comment_pins_no_wall_clock_figure) or "",
        )
    )
    for stale in _STALE_GATE_WALK_ANCHORS:
        assert stale not in prose, (
            f"this file's prose names a TREE (`{stale}`) as the carrier of the "
            "pre-`#9516` figure, and the tree does not carry it — measured at this "
            "PR's base: `git show origin/main:tests/_about_call_time_census.py | "
            f"grep -c 10.36` is `0`. Name the commit ({_GATE_WALK_FIGURE_CARRIER}:990-992)"
        )
    assert _GATE_WALK_FIGURE_CARRIER in prose, (
        "no sentence here names the COMMIT that carries the pre-`#9516` figure any "
        "more — the anchor was deleted rather than corrected, so the arms below and "
        "§23 no longer tell a re-verifier where the quoted text came from"
    )
    carrier = _git_blob(_GATE_WALK_FIGURE_CARRIER, "tests/_about_call_time_census.py")
    if carrier is None:
        pytest.skip(
            f"{_GATE_WALK_FIGURE_CARRIER} is not reachable in this checkout, so the "
            "commit anchor above is UNVERIFIED here (a shallow clone cannot reach it "
            "and is not rewritten to try) — the prose pin still ran"
        )
    assert "measured 10.36 s of the control's 10.85 s" in carrier, (
        f"{_GATE_WALK_FIGURE_CARRIER}:tests/_about_call_time_census.py does not carry "
        "the figure the arms below quote from it, so the anchor names a commit that "
        "does not say what they claim"
    )
    base = _git_blob("origin/main", "tests/_about_call_time_census.py")
    if base is not None:
        assert "10.36" not in base, (
            "`origin/main`'s census blob carries the pre-`#9516` figure again, so the "
            "measurement this file states (`grep -c 10.36` is `0` at the base) is "
            "stale and the anchor sentence above should be re-derived"
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
