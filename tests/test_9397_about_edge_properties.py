r"""#9397 — the "property-free `aboutObject` edge" evidence is FALSE, and the ruling leans on it.

THE DEFECT THIS PINS
--------------------
The owner's 2026-09-23 ruling on stored-vs-derived (`#7948`) rests on a
*code-level* claim recorded in two live architecture documents:

  * `STORAGE-ARCHITECTURE.md` §13 — *"**no** `aboutObject` edge carries **any
    property** … we lose nothing semantically by deriving later"*;
  * `STORAGE-ARCHITECTURE.md` §11.1 — *"Our `aboutObject` links are
    **property-free**: the write path emits `MERGE (s)-[:aboutObject]->(t)` and
    **never a `SET`**"*;
  * and the same fact in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 and the
    decision row at `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`.

At HEAD an `about*` edge carries `confidence`. The writers do not all act on one
axis: some ORIGINATE the property (they write it first) while others write it in
the same NAMED form but REPRODUCE it on replay, so the causal and the write-form
axes are not the same axis (round 10 of the changelog records the confusion this
caused). The COUNTS are deliberately not restated here: a number in this header
is prose nothing compares, so the populations are pinned against the documents
by `test_the_document_population_counts_match_the_census` (which compares them to
`RECORDED_WRITERS` / `MAP_WRITERS`, so a change there forces a document fix) and,
for the causal composition alone, by
`test_the_writer_classification_separates_the_causal_axis_from_the_write_form`
— the same reason `RECORDED_WRITERS` states no split.
The ingestion path reaches one of the originators by default:

  1. `session_link.link_entity` — `MERGE (s)-[r:{edge_type}]->(t)
     SET r.confidence=$conf` (`edge_type` defaults to `aboutObject`); ORIGINATES.
  2. `subject_binding.bind_point_subjects` — `SET r.confidence=$conf` on an
     already-present about edge; the `object` slot role binds via `aboutObject`.
     ORIGINATES, and the `_extract_session_v2` ingestion path reaches it.
  3. `projection/entities._fold_entity_linked_reason` — the REPLAY fold
     re-SETs it, i.e. the property is journaled and reproduced (`live == rebuild`).
     RE-MATERIALIZES — it does not originate anything.

The remaining shipped paths therefore RE-MATERIALIZE (the replay fold above and
the ones below), and one of them adds a SECOND name, so an "exactly one name"
reading is one form short:
`cache_projection.relation_statement` (the record-layer projection) writes the
record row's whole property set by property MAP over a predicate allowlist that
includes `aboutObject`, and it also stamps `idx` — `public.relation.idx`, the
parallel-edge ordinal — as an INLINE pattern property, unconditionally; and
`hosted_backup.restore_graph` the dump's (`SET r = $p`). The measured set is
therefore `confidence` (originated) plus `idx` (re-materialized): one semantic
attribute and one identity coordinate, not one property name. **The measured
population splits by WRITE FORM — the NAMED form (`SET r.<prop>`) and the
property MAP — which is the axis the census reads, and on the CAUSAL axis it
splits differently again — and the census is blind to a SHIPPED site outside
its walk, a gap the count must not hide:** the numbers for both axes live in
`test_the_document_population_counts_match_the_census`, because a count written
here is prose nothing compares.
`session_source_migration._move_in` moves an edge by
`MERGE ({src})-[r2:`{rel}`]->(new) ON CREATE SET r2 = properties(r)`, i.e. it
copies the WHOLE property map, and `rel` is handed to it from
`MOVABLE_SOURCE_RELS` — which contains `aboutSource` and `aboutDocument`.
**It is a DETECTOR, not a PROOF of absence — stated as that, never as a
completeness claim, because every completeness claim written here has been
falsified by a shape the next reviewer measured.** What it does not read, each
measured against `_writers_in_module` rather than assumed:

1. **A predicate handed in as a PARAMETER** — the vocabulary lives in the
   caller, so the body never names it (`_move_in`, above; measured: adding
   `MOVABLE_SOURCE_RELS` to `_ABOUT_VOCAB` still yields `{}` for that module).
2. **An UNTYPED binding tested with `WHERE type(r) = 'about…'`** —
   `_PATTERN` (`\[(\w*):([^\]]{0,120})\]`) requires a TYPED relationship
   pattern, so an untyped `MATCH (s)-[r]->(t)` is `continue`d before any rule
   runs. Measured: `MATCH (s)-[r]->(t) WHERE type(r) = 'aboutObject'
   SET r.confidence = $c` -> `{}`. Already live style in the tree (`sdk.py` /
   `analyze.py` use `WHERE type(r) = 'NAND'`).
3. **An fn-call RHS** (`SET r = properties(x)`) — `_SET_MAP` (`$|{`) cannot
   read it, **even when the about predicate IS spelled in a typed pattern**.
   Measured: `MATCH (s:Point)-[r:aboutObject]->(t) SET r = properties(x)` ->
   `{}`, while the same writer with `SET r = $p` is recorded (`<map:$props>`).
4. **Spellings `_PATTERN` does not recognise** — whitespace before the
   relationship-type colon (`[r :aboutObject]`), whitespace after `[`
   (`[ r:aboutObject]`), and a `]` inside an inline map
   (`{tags:["a"],confidence:$c}` truncates the `[^\]]{0,120}` span). Each
   measured `{}` with a control recorded.
5. **About-named predicates outside `_ABOUT_REL`'s enumerated seven** — the
   guard's own comment claimed "an `about*` LITERAL", but `_ABOUT_REL` is
   `about(Subject|Object|Event|Point|Document|Action|Source)`; an `aboutClaim`
   edge measured `{}`.
6. **Any writer outside `tortoise/`** — the walk is `PKG.rglob("*.py")` over the
   package, while the retracted evidence came from a *full-repo* search;
   `tools/longmem_eval/` and `graph-scripts/` hold `aboutObject`/`aboutSubject`
   writes today (none currently property-bearing).

**So the honest statement is: the census is a regression tripwire over the
shapes it enumerates, inside `tortoise/`.** A property it does not see is not
evidence of absence, and this paragraph is the thing that says so.
`tests/test_9397_about_edge_properties.py` pins synthetic probes for gaps 1-5
(a control is recorded beside them), and the DOCUMENT wording is pinned by
its own leg. Gap 6 — a writer outside `tortoise/` — cannot be probed from
inside the walk, so it is STATED and not pinned, and no test here claims to
block every future absolute, because none can.

`sdk.supersede_point` WAS one of these until `#9417`. Its transfer to the
successor runs on the 2a-DIRECT leg, whose rows are discovered by
`MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t)`, so `rtype` is provably
`IMPL|NAND`; the about edge is transferred by a separate
`MERGE (new)-[nr:{rel}]->(t)` that **does** carry the old edge's properties
(`SET nr += $attrs`, whitelisted to the `RelationUpsert.attrs` vocabulary). So
since `#9417` that writer DOES write `confidence` on an about edge, and it is a
counted MAP-form writer now rather than an exemption.

⚠️ Every clause in that paragraph is about `supersede_point`. Earlier revisions
of this file ran the two functions together in ONE sentence — *"… is NOT: Its
`SET r += $attrs` is on the 2a-DIRECT leg … so since #9417 this writer DOES
write `confidence` on an about edge and is counted here"* — so
the nearest antecedent carried the supersede mechanism onto
`projection._rebuild_all_replay`, though none of it is that function's. That is
the "sentence that says the opposite of the table beside it" defect this census
exists to close, in the file that closes it. The replay's row WAS the declared
FALSE POSITIVE in `NOT_AN_ABOUT_WRITER` and left it in `#10139` — the REBUILD
half of `#9417`, which dropped the carried property on every `rebuild_all`. That
is the point of keeping the table rather than deleting it: a fix moves the row
into `MAP_WRITERS`.
`_rebuild_all_replay` carries TWO `SET r += $attrs` statements, and neither is
protected the same way:
the `DirectEdgeCreated` fold has TWO producers since `#9553` — `create_direct_edge`,
whose `op_type` allowlist is `{IMPL, NAND}` so its descriptor can never name an
`about*` edge, and `_record_structural_edge` (reached by the edge MINTS —
`create_edge`, `create_about_edge` and the three typed wrappers), which DOES
journal an `aboutSubject`/`aboutObject`/… predicate but emits no attribute from
the map's vocabulary at all. Both halves are load-bearing and both are pinned
(`test_every_direct_edge_created_producer_is_safe_for_an_about_edge`), because
the mechanism that actually protects this statement is the FIXED key vocabulary:
no `DirectEdgeCreated` in this package can reach an `about*` edge carrying one of
those keys. The structural-`DirectEdgeRepoint` fold's
`etype` IS allowlisted and can be `aboutObject`, so there too what carries that
statement is the map being built from a fixed attribute vocabulary — and since `#10139` that
map is NO LONGER EMPTY: the structural repoint descriptor carries the retired
edge's attributes (`sdk.supersede_point` emits them flat, the same keys
`DIRECT_EDGE_ATTR_KEYS` holds) and the about/structural transfer re-applies them
with `ON CREATE SET nr += $attrs`. So the statement that used to write nothing
on an about edge now carries `confidence` there, and the row is a counted
MAP-form writer.

The MECHANISM that table implements is kept, and kept honest while empty: the
two legs that read `NOT_AN_ABOUT_WRITER` iterate its rows, so an empty table
pins nothing by itself — what still runs on every suite is the MATCHER PROBE
inside `test_the_declared_false_positive_premises_hold_at_the_statement`, which
proves the per-STATEMENT reading reds where a function-wide one stayed green,
and the loop re-activates row-by-row the moment an exemption returns. A new row
must arrive with its own pinned statement premise, never as a bare dict entry.

⚠️ An EXEMPTION is a fail-open cell unless something compares against it, which
is why a row here was never exempt from the name-set comparison: it declares
`EXPECTED_NAMES` like a recorded writer, and its premise is asserted against the
STATEMENT it names — not against a function-wide text union, which is the very
blindness the row exists to explain. `_rebuild_all_replay` keeps that
`EXPECTED_NAMES` row after leaving the table, so a property arriving at that
function is still compared against the documents' claim.

ROOT CAUSE OF THE FALSE CLAIM — the method, not the measurement
---------------------------------------------------------------
The evidence was gathered with a Cypher **property-MAP** pattern,
``rg -n 'aboutObject \\{'``. A property can also be written by a
``SET r.<prop>`` clause on a relationship variable, and that form cannot match
a search for ``aboutObject {…}``. So the search returned zero — and a zero from
a pattern that structurally cannot match the writer was recorded as a measured
absence. That is why this file checks the *writer census*, not another grep.

THE SAME BLINDNESS, ONE LEVEL DOWN (review cycle on this file's first version)
-----------------------------------------------------------------------------
A census that reads only `SET r.<prop>` repeats the error: the property-MAP
form `SET r += $props` / `SET r = $p` is structurally invisible to it, and two
shipped sites already use that form on an allowlisted `aboutObject` predicate
(listed above) — one MORE uses it on an `IMPL|NAND` leg and is the
    declared false positive below, which is why the population it must NOT count is the
exemptions rather than the census rows (the pinned numbers are in
`test_the_document_population_counts_match_the_census`). So the census reads all three write forms **where `_PATTERN`
recognises the spelling** (the gap list above names the spellings it does not),
and an interpolated
predicate counts as about-bearing when its writer takes it from the shared about
/ predicate vocabulary — including the allowlist itself (`validate_rel_type`,
`KNOWN_REL_TYPES`, `_sanitize_label`), because "validated against the whole
allowlist, which contains `aboutObject`" is exactly how a map-form writer
names its edge without writing `aboutObject` anywhere.

⚠️ THAT is a heuristic, and the promise it supports is bounded by it: the census
cannot see a writer whose predicate comes from a vocabulary named nowhere in
`_ABOUT_VOCAB` (or whose module `_module_in_scope` skips). The two forms that
ARE provably read — the allowlist path and the inline pattern map — are pinned
by synthetic probes; the residual gap is a list of names, and a list cannot be
checked for a token nobody has written yet. Adding a predicate vocabulary is a
change to `_ABOUT_VOCAB`, not only to the code that uses it.

⚠️ AND THE CENSUS STOPS RATHER THAN SKIPS. A module `ast.parse` (or a strict
UTF-8 decode) rejects is NOT in that list of names: it raises `UnparsableModule`,
so the census fails CLOSED and names the file. Dropping such a module and
returning its `{}` would report every writer it contains as a measured absence —
the identical fail-open shape as the `rg 'aboutObject {'` zero, one level down
(third review cycle on this file).

WHAT IS PINNED (one entry per test, in file order)
-----------------------------------------------

1. `test_the_absolute_property_free_sentences_do_not_reappear` — the retracted
   sentences are matched MARKER-INSENSITIVELY (`*`, backticks, whitespace) against
   the live documents. Each entry records its own provenance — the pre-fix
   document it was measured in, or `RETRACTED_INTRODUCED_HERE` for wording this
   PR itself wrote and withdrew. Which class an entry is in is MEASURED against
   the pre-fix revision by `test_every_retracted_literal_declares_its_true_`
   `provenance`, because a label nothing compares against the
   text is the same unmeasured claim one level up.
2. `test_the_pin_index_names_every_leg` — this index is prose indexing
   code, so it re-stales: at `#9403` review it carried 24 entries against 25
   tests while its heading claimed "one entry per test, in file order", and the
   merge union that produced 25 tests from two 24-test parents re-derived
   neither the count nor the order. The leg compares the indexed SET to the
   module's actual test functions (so it names itself too), rejects a leg listed
   twice, and asserts the entries follow the FILE ORDER of `def test_` — which is
   what the heading claims and what a reader mapping a failure to its entry
   relies on.
3. `test_the_shallow_gate_precedes_the_fetch` — the gate that stops the
   provenance leg from shallow-ifying a full clone was unpinned: deleting it
   left every leg green, because the only nearby assertion checked that the
   strings `"fetch"` and `"--depth="` were still in the source. The leg parses
   `_ensure_commit` and requires the `_is_shallow` return-False guard to appear
   BEFORE the fetch loop, so the order is what is pinned. Structural, because
   the destructive path is unreachable where `PRE_FIX_REV` resolves.
4. `test_the_unmeasured_share_retraction_is_pinned` — §13's "a statement
   about the row's SHAPE, not about the population's share" points at §11.1's
   retraction of the share claim, and nothing pinned that sentence: deleting it
   left every leg green, so the cross-reference could outlive its referent.
   Section-scoped, mutation-checked (`#9403` review, P2).
5. `test_the_corrected_passages_name_the_property_bearing_class` — the
   corrected passages must POSITIVELY name the writers and the property, so a
   silent revert reds here. The assertions are SECTION-scoped (§11.1, §13,
   §13.8 q2), not file-wide: the literals appear in more than one section, so a
   file-wide membership test cannot red on one section's silent deletion
   (`#8639` review, the `test_entity_attachment_brief_claims.py` precedent). It
   also rejects an unmeasured population SHARE (`most entity links`, `for most
   links`, `bulk of the rows` …) and the retracted FORM COUNT on the originated
   set — both were live and green in the `#9403` head (`#9403` review, P1/P2).
6. `test_the_corrected_passages_carry_no_bare_edges_line_citation` — the
   rewritten passages cite the generic writer by SYMBOL, not by a bare
   `projection/edges.py:<n>`: a line number is only true of the revision it was
   measured at (`:344` was `create_about_edge` at the `c79ba1cf2` pin and is a
   comment block at HEAD). §11.1's copy keeps `:344` **at the pinned revision**,
   because there the qualifier is stated.
7. `test_the_references_paragraph_scopes_property_freedom_to_source_version` —
   §9.6's *"identity/mention and referential-containment `references` links stay
   property-free"* is the SAME unqualified absolute, above the §11.1 correction,
   for a different link class. It is retracted as an ABSOLUTE, not because such a
   link carries a property today (no record kind emits a `references` relation,
   so the reached-for `idx` reason was wrong). Scoped to the fact the paragraph
   establishes: those links carry **no `sourceVersion`**.
8. `test_the_cheapness_rule_states_the_measured_set_and_records_the_tension`
   — §11.4 rule 1 is the rule this correction rewrote IN PLACE, and nothing
   pinned it (`#9403` review: restoring its pre-fix imperative verbatim at the
   PR head left every leg green). Section-scoped to the RULE, not to §11.4, so
   it must say `near-bare`, must not re-assert the retracted absolute, must
   keep the `#1370` tension record journaled through `EntityLinked`, and must
   keep its `RECORDED, not resolved` qualifier — a guard may not let prose
   settle a design decision by deleting the words that mark it open.
9. `test_the_cost_argument_states_the_open_set_not_a_closed_pair` — §11.3 is
   where a reader decides what the migration does to this layer, and both its
   table row and its prose summarised the entity link as a CLOSED pair ("two
   ids, plus the binder's `confidence` … and the projection's `idx` ordinal")
   while the same section's §11.1 footnote and §13 say the set those names sit in
   is open. Pinned PER PASSAGE, because a section-wide marker test stayed green
   when only the table row was reverted (measured) — `#9403` review cycle 2.
10. `test_the_ledger_keep_row_names_both_recorded_properties` — the extractor
   ledger's KEEP row is the reason a reader takes the layer to be cheap, and it
   named one of the two measured properties (`confidence`, not the record
   projection's `idx`). Its RETRACTED wording was pinned; any other route back to
   the one-name absolute there was not.
11. `test_the_code_still_agrees_with_the_corrected_evidence` — each recorded
   writer still writes what the documents claim it writes (the named form on
   the originating writers; the property-MAP/inline forms on the re-materializing
   sites, `idx` included). If that code is removed, the document has re-rotted in the
   other direction and this reds instead of the doc quietly lying again.
12. `test_the_document_population_counts_match_the_census` — the numbers §13 and
   §11.1 state (population width, NAMED vs MAP split, causal split) are compared
   to `RECORDED_WRITERS` / `MAP_WRITERS` / `NOT_AN_ABOUT_WRITER`, so a writer
   added to the census cannot leave the documents claiming an old width.
13. `test_the_writer_classification_separates_the_causal_axis_from_the_write_form`
   — the documents counted 5 writers as `3 originating + 2 re-materializing`,
   and that composition is false: `projection/entities._fold_entity_linked_reason`
   reproduces on REPLAY what the journal already carried, which is the documents'
   own definition of RE-MATERIALIZING. The census can only see the WRITE FORM
   (3 named + 3 map since #9417), which is why the wrong number read plausibly; the causal
   split is 2 + 4, and the legs red if the fold is filed as an originator again
   (`#9403` review, P2).
14. `test_the_correction_changelog_qualifies_its_line_citations` — the entry's
   own round-5 rule (`at HEAD` on every number that is not true at the pin) is
   applied to the paragraph that MAKES the citations: three of them survived
   bare, and one (`security.py:85`) is false at the declared pin `c79ba1cf2`
   (`#9403` review, P2).
15. `test_the_named_and_map_form_writer_populations_are_disjoint` — no site is
   recorded twice, the map population has not shrunk below the shipped sites, no
   exemption is also a recorded writer, every exemption has a pinned statement
   premise, and every row (exemptions included) declares its exact name set.
16. `test_the_declared_false_positive_premises_hold_at_the_statement` — a
   `NOT_AN_ABOUT_WRITER` row is an EXEMPTION, and an exemption nobody compares
   against is a fail-open cell: each premise is asserted at the STATEMENT the
   census attributed the property to (the about transfer carries no `SET`, and
   the `SET` it does see names no `about*` predicate). EVERY matching statement
   is checked, not the first (`#9403` review cycle 2: `_rebuild_all_replay`
   carries TWO `SET r += $attrs` statements and the first-match version left the
   second — the structural-repoint fold, exactly the shape `#9417` touches — as
   a fail-open cell). The premise is also pinned to its mechanism: the statement
   is gated on a FIXED attribute vocabulary, so a descriptor carrying none of
   those keys writes nothing on the edge its interpolated `etype` names.
17. `test_no_unrecorded_property_bearing_about_edge_writer` — the census half:
   every property write on an `about*` relationship pattern (named variable or
   anonymous) — named (`SET r.<prop>`), map-assigned (`SET r += $props` /
   `SET r = $p`), or an inline pattern map (`[r:aboutObject {idx:$idx}]`, which
   assigns with no `SET` clause at all) — must be one of the recorded writers,
   each row must write exactly the names it declares, and an exemption must not
   VANISH from the census (a row recorded for code nobody can find protects
   nothing).
   ⚠️ **The bound on that promise, stated rather than assumed:** an `about*`
   edge is recognised by a literal in `_ABOUT_REL`'s enumerated set —
   `about(Subject|Object|Event|Point|Document|Action|Source)`, NOT the
   `about*` prefix as this comment originally claimed (an `aboutClaim` edge is
   unseen; measured) — or by an INTERPOLATED predicate in
   a function whose body names one of the `_ABOUT_VOCAB` vocabularies (and, at
   the file-walk level, in a module `_module_in_scope` admits). A writer that
   derives its predicate from a vocabulary named NOWHERE in that list is still
   invisible — so adding a predicate vocabulary, or a new way of naming one, is
   a change to `_ABOUT_VOCAB` / `_module_in_scope`, not just a change to the
   code. The remaining gap is a vocabulary list, and a list cannot be checked
   for a token nobody has written yet. It is NOT a parse failure:
   `test_the_census_fails_closed_on_a_module_it_cannot_parse` makes
   the census STOP rather than skip.
18. `test_the_census_reads_the_inline_pattern_map_form` — synthetic probe for the
   inline pattern map, named and anonymous.
19. `test_the_census_bound_is_pinned_as_a_gap_not_an_absence` — one probe per
   gap category 1-5, each asserting `{}` against `_writers_in_module`, beside
   a recorded control. No COUNT is stated here on purpose: an earlier
   revision of this list said "two shapes" while the leg asserted more, and
   a count in prose is a claim the code can outgrow silently.
20. `test_the_documents_state_the_bound_as_a_detector_not_a_proof` — the
   completeness claim is WITHDRAWN, not reworded a fifth time: the detector
   wording must be present in the guard's `__doc__` and at least twice in
   `STORAGE-ARCHITECTURE.md`, and the retracted absolutes absent. Measured
   without it, deleting the bound paragraphs or inserting a fresh absolute
   left every leg green — a pin that is named for a bound and does not red on
   its removal is not evidence.
21. `test_the_census_reads_a_writer_that_never_says_about` — synthetic probe for
   the allowlist-driven writer whose module never spells `about`.
22. `test_the_census_fails_closed_on_a_module_it_cannot_parse` — a module
   `ast.parse` (or the strict UTF-8 decode) rejects STOPS the census and names
   the file, instead of returning `{}` and reporting every writer inside it as a
   measured absence — the same fail-open shape as the `rg 'aboutObject {'` zero.
23. `test_the_default_ingestion_path_reaches_the_binder` — the corrected text
   says "default-ON, ingestion path"; if `_extract_session_v2` stops calling
   the binder, **or stops being the fall-through branch** of the
   `TORTOISE_SESSION_EXTRACTOR == "m2"` selection, that sentence becomes false
   while every other test stays green.
24. `test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live`
   — every RETRACTED literal is findable in the changelog that quotes it, or is
   declared pre-fix-only or introduced-and-retracted inside this PR
   (`RETRACTED_INTRODUCED_HERE`: three absolutes were written by an earlier
   revision of THIS PR, so claiming a pre-fix presence for them would be the
   same unmeasured provenance this file exists to stop). A pin that cannot match
   the pre-fix text protects nothing, and that is not a hypothesis: it shipped.
25. `test_every_retracted_literal_declares_its_true_provenance` — the
   CLASSIFICATION is measured, not merely declared: a literal listed in
   `RETRACTED_INTRODUCED_HERE` must be ABSENT from the pre-fix revision
   (`PRE_FIX_REV`) and every other entry PRESENT there. Without this the label
   is an assertion about history that nothing compares against history — and
   leg 1 then prints "was live pre-fix" for a sentence this PR wrote, in the
   diagnostic of the guard whose whole subject is unmeasured provenance. Skips
   (never passes silently) when the revision is unreachable.
26. `test_the_storage_changelog_records_the_correction` — the changelog is where
   a reader learns that a §11.1/§13 claim moved.
27. `test_the_guard_states_its_own_execution_and_probe_coverage_honestly` —
   this file's claims about ITSELF, re-derived from the source: the header may
   not say the suite is network-free while `_ensure_commit` fetches, it may not
   claim one probe per gap while gap 6 is disclosed rather than pinned, and it
   may not state a numeric leg count in prose. The pin-index coverage and order
   moved to `test_the_pin_index_names_every_leg`, which owns that claim alone.
28. `test_the_correction_ledger_numbers_each_round_once` — the ledger's round
   numbering is unique (it is the ledger's only index), and the fourth round's
   false record of §13's citation does not come back.
29. `test_the_pin_index_entry_for_the_9_6_retraction_states_no_measured_distance`
   — the entry ABOVE (7) is prose about a leg, and nothing pinned its CONTENT
   (`test_the_pin_index_names_every_leg` owns the SET and the FILE ORDER only), so
   it still carried both premises the leg had just retracted: the unmeasured line
   distance and the disproved `idx` reason. No index entry may state a line
   distance at all.
30. `test_the_retracted_references_absolute_is_gone_from_production_docstrings`
   — the retraction also has to reach the prose that CITES §9.6. Six production
   docstrings (`sdk.link_source_to_entity`, `search_engine.
   read_reference_currency_links`, `tools/source_currency.py`) still called those
   links "property-free" and now scope the claim to the anchor. SITE-scoped on
   BOTH halves, not file-wide: the positive half first asked whether each FILE
   still said "carries no `sourceVersion`", which two of these files satisfy
   twice, so one site of a pair could be emptied while the leg stayed green (the
   silent deletion the `#9450` review measured). Each row now carries an ANCHOR
   and the scoped fact that site must state.
31. `test_the_retracted_production_literals_are_measured_against_the_pre_fix_tree`
   — leg 30's six literals are pinned as retracted, and an absence check on a
   literal that was never there is permanently green: one mistyped character
   would unpin all six sites without a red. Each literal is measured PRESENT at
   `_RETRACTED_PRODUCTION_REV` (a `main` commit whose content stays immutable
   once this lands), skipping only where the history is unreachable.
32. `test_every_direct_edge_created_producer_is_safe_for_an_about_edge` — the
   `_rebuild_all_replay` creation fold does NOT filter on `edge_type`, so what
   keeps a property off an `about*` edge is the `attrs` map's FIXED vocabulary,
   and that gate holds only while every attribute-carrying producer provably
   cannot name one. `#9553` added a SECOND producer — one that journals the whole
   `about*` family — which falsified the "ONLY producer (`create_direct_edge`)
   refuses any `op_type` outside `{IMPL, NAND}`" premise three sites in this file
   rested on, while the conclusion stayed true for a different reason. The leg
   compares the map and the code in BOTH directions: a producer carrying a
   vocabulary key with no declared statement-level proof reds, a declared proof
   whose statement is gone reds, and so does the about-capable producer
   acquiring a key.
   ⚠️ THE SYMBOL MOVED, and it is named once here so the next reader does not
   re-derive it: the about-capable, attribute-free emission is
   `tortoise/sdk.py::_record_structural_edge` — the recorder the projection's
   edge mints call — NOT `sdk.create_edge`, whose door-level emission `#9827`
   added and this change replaced with the mint-level one.
33. `test_every_census_writer_carries_its_class_verdict_where_it_is_named` — the
   CLASS the tables put a writer in, stated in a sentence that actually names
   it. Every earlier leg compared the documents' NUMERALS to the tables, so a
   sentence could say the opposite of the table it sat next to and stay green;
   measured on one five-line behavioural fix, four review cycles were green
   while §13 called a counted writer an exemption. ⛔ The numeral is not typed
   here twice: `test_the_pin_index_states_the_9417_cycle_count_its_own_leg_does`
   reads this entry against the count the leg's own block states, so a count
   changed in one place cannot leave the other stale.
34. `test_no_corrected_passage_denies_a_verdict_the_census_counts` — the
   contradiction half: a passage may not call a counted writer an exemption or
   say it drops / carries no property, unless the sentence is explicitly about
   HISTORY (`before`/`until #9417`), which is true and must stay writable.
   This is the leg that reds on §13.8 q2's pre-`#9417` sentence, live on `main`.
35. `test_the_population_numerals_are_derived_from_the_census_tables` — the two
   statements that were TYPED rather than derived: the declared-false-positive
   count in every spelling, and the invisible site's ORDINAL (`total + 1`).
   "A sixth site is in the code today" outlived the 5 -> 6 move on a green
   suite, which is what a typed ordinal is worth.
36. `test_no_marker_is_written_in_a_spelling_the_flattener_cannot_produce` —
   every marker in the four vocabulary tuples must be a spelling `_flat` can
   produce, and must be either PRESENT in the flattened documents or declared
   absent-by-design with a reason. `_flat` turns `-` between word characters
   into a space, so `"MAP-form writer"`, `"MAP-form"`, `"NAMED-form"` and
   `"re-materializ"` were DEAD on arrival — 0 occurrences each, for ever — and
   an exemption asserted to "write the attribute set by property MAP" stayed
   green. A dead marker is worse than a missing one: it reads as coverage.
37. `test_the_producer_evidence_block_cites_the_emitters_by_symbol` — the
   `#:` block that justifies `DIRECT_EDGE_CREATED_ATTR_GUARDS` is the census's
   evidence pointer for *"exactly two emitters"*, and it cited `sdk.py` LINE
   NUMBERS that had moved onto `poll_events`, `list_batch` and `assess_source`.
   This file asserts the documents cite by SYMBOL and enforced that on the
   corrected passages (leg 6) while its own pointer carried bare numbers. The
   emitters are DERIVED from the AST and must be named in the block, and a
   re-typed `file.py:<N>` reds.
38. `test_the_pin_index_states_the_9417_cycle_count_its_own_leg_does` — a
   NUMERAL in the index that disagrees with the leg it indexes. This entry put
   that count at *five* where leg 33's block and the ledger both say four, and
   nothing red on it because the index legs pin the entry SET, the FILE ORDER
   and one entry's content at that entry's own subject. The count is typed ONCE
   and every place that states it — index, leg, ledger — must agree.
39. `test_the_ledger_records_every_review_cycle_the_record_cites` — the
   correction ledger must not stop before the artifact it indexes. Measured at
   the `#10200` review: the ledger ended at round 6 (cycle 7) while this module
   cited a cycle 9 review four times, so the rounds that introduced the widest
   denial frames and `EXEMPT_COUNTED_PATTERNS` had no entry at all. NEWER than
   the ledger is the defect; older is the label convention, not a gap.
40. `test_no_bare_line_citation_survives_in_this_files_record` — leg 37 banned a
   bare `file.py:<N>` in ONE `#:` block, and the census's own record carried one
   elsewhere anyway: the `MAP_WRITERS` block pointed the third write form at a
   line ~800 from `relation_statement`, and two diagnostic messages repeated it,
   so a lane auditing that row was sent to an unrelated branch. The ban now
   covers every COMMENT in this file (test DATA is exempt, because legs carry
   pre-fix spellings on purpose), and the one pointer the third row leans on may
   carry no line number anywhere in the file.

41. `test_the_rebuild_half_carries_what_the_live_half_carries` — `#10139`'s CODE
   half, at the STATEMENT, replacing the residual `MAP_WRITERS` used to state in
   prose: the census's whole-function union reaches that site through the
   2a-DIRECT `SET r += $attrs` leg (the structural `SET` rides an interpolated
   `_carry` fragment, gap 1 of the docstring's list). Pinned here: the descriptor
   JOURNALS the payload from the ONE shared `DIRECT_EDGE_ATTR_KEYS` vocabulary,
   the transfer statement INTERPOLATES the carry, and the carry is `ON CREATE` —
   `#9417`'s precedence rule, since the `EntityLinked` trailing sweep has already
   re-materialized the successor's own `confidence` when pass-2b runs, so a bare
   `SET` clobbers it (measured live at 0.42 replacing 0.91; `_carry = " SET nr +=
   $attrs"` now reds HERE, not only in the behaviour suites). The gated
   `SET r += $attrs` folds are still gated per statement, and the vocabulary is a
   CEILING inside `FALSE_POSITIVE_ATTR_VOCAB`.

NOT promised: this does not re-open stored-vs-derived (the ruling stands, and
the edge stays), and it does not decide §11.4 rule 1's tension with `#1370`
(that is a design question, recorded, not resolved here — what IS pinned is
that the rule keeps stating the measured set and keeps carrying the tension
record, test 5). Line numbers are not
asserted — the SYMBOL is authoritative, per the documents' own rule.

Static and stdlib-only, with one non-static corner: the provenance legs measure
claims about HISTORY, so they shell out. Exactly what each runs, because an
earlier revision of this paragraph said "ONE exception: `git show` … no network" and was
wrong on both halves: `git cat-file -e` (via `_has_commit`, on every `_git_show`
call) to test reachability, `git show <rev>:<path>` for the text, and — where the
rev is not already in the checkout — `git fetch --quiet
--depth={120,400,1500} origin main` (`_ensure_commit`), which on a shallow CI
checkout IS a NETWORK call (one per depth, each bounded by a 120 s timeout) and a
SIDE EFFECT on the checkout under test: it deepens that clone's object database.
The leg SKIPS, never passes, when the rev stays unreachable. No DB, no graph, no
server.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
EXTRACTOR_CHANGELOG = (
    ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md"
)
STORAGE_CHANGELOG = (
    ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE-CHANGELOG.md"
)

#: The revision the retracted literals' PRE-FIX provenance is measured against —
#: the last `main` commit before this correction's own documentation edits
#: (`#9384`'s merge), i.e. the tree the reviewers measured non-vacuity against.
#: The reading MOVES with this file and is re-measured at every revision, never
#: carried forward — a count in this prose is a claim the code outgrows
#: silently, which is exactly how this defect kept returning — the same sentence
#: went stale at one leg total, was corrected to a new total that was itself
#: stale, and a concurrent lane's re-measurement was out of date the moment two
#: more legs landed. That is why no total appears here.
#: TO RE-MEASURE: swap ALL FOUR live documents this guard reads for their
#: `01b5486ec` versions, run the file, read the split. A PARTIAL swap measures
#: something else — `STORAGE-ARCHITECTURE.md` alone gives a different reading
#: because the extractor and changelog legs then still read corrected text —
#: so the swap, not the number, is the durable instruction.
#: "Was live pre-fix" is a claim about HISTORY, so it is
#: checked against history instead of restated: an entry in
#: `RETRACTED_INTRODUCED_HERE` must be ABSENT here, every other entry PRESENT.
#: ⛔ NOT derived from `git merge-base HEAD origin/main`: the moment this lands
#: the merge-base IS the corrected tree, and the measurement would invert.
#: ⚠️ The gating CI job checks out at `fetch-depth: 1`, NOT with full history —
#: this comment claimed `fetch-depth: 0` and was false for the only job that
#: matters (`python-ci.yml:631`; the one `fetch-depth: 0` is the `changes` job at
#: `:224`). The rev is made reachable by `_ensure_commit`, which fetches the
#: `main` ref at a bounded depth; the provenance leg then SKIPS rather than passes on a
#: history it genuinely could not read, and the skip message says so.
PRE_FIX_REV = "01b5486ec"

#: label → (retracted literal, the live document it was measured in on the
#: PRE-FIX tree). The document is recorded per entry because it is a checkable
#: claim, not decoration: the `§13.8 q2` row in this file's first version pinned
#: an UNBOLDED literal where the document bolds it, so it matched nothing — a
#: dead entry that would have passed on a verbatim revert. The literal is the
#: document's own text, bold markers included. Three entries were introduced and
#: retracted INSIDE this PR (see `RETRACTED_INTRODUCED_HERE`) — their `home` is
#: still the document the claim belongs to, and `_provenance` keeps the message
#: from claiming a pre-fix presence that never existed. Which class an entry is
#: in is MEASURED against `PRE_FIX_REV` by
#: `test_every_retracted_literal_declares_its_true_provenance`, not taken on
#: trust: the
#: `§11.1` method entry was filed as pre-fix-live and is not (measured absent
#: at `01b5486ec`), which made leg 1's diagnostic print a false pre-fix claim.
RETRACTED: dict[str, tuple[str, str]] = {
    "§11.1 calls the links property-free and reports a zero search": (
        "Our `aboutObject` links are **property-free**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13 closes the structural door with 'any property'": (
        "**no** `aboutObject` edge carries **any property**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13's inference of semantic freedom": (
        "we lose nothing semantically by deriving later",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11.1's cost sentence presents the zero as measured": (
        "a full-repo search for a property-bearing edge finds **none**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11.3's table row still calls the rows property-free": (
        "**property-free rows**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11's estimate footnote leans on the property-free finding": (
        "the measured property-free finding",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13.8 q2 repeats the property-free claim (BOLDED, as written)": (
        "**no** `aboutObject` edge carries any property",
        "EXTRACTOR-V4-ARCHITECTURE.md",
    ),
    "§11.3's prose says the links stay bare": (
        "the entity links stay bare",
        "STORAGE-ARCHITECTURE.md",
    ),
    #: The IMPERATIVE in §11.4 rule 1, which this PR rewrote to `near-bare` and
    #: left unpinned (measured: restoring it verbatim at HEAD kept every leg
    #: green). It is the sharpest instance of the class — a sentence this PR
    #: CHANGED, so a verbatim revert is exactly what a later tidy-up produces.
    "§11.4 rule 1's imperative is property-free": (
        "**Keep entity links property-free.**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11.1's method paragraph counted the write forms as two": (
        "Two forms write to an edge, and both are in scope",
        "STORAGE-ARCHITECTURE.md",
    ),
    "the corrected claim's own 'exactly one name' absolute": (
        "The property set is exactly one name — `confidence`",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13.8 q2's 'never a second property name'": (
        "never a second property name",
        "EXTRACTOR-V4-ARCHITECTURE.md",
    ),
    "the decision ledger's reason is property-freedom": (
        "Cheap because edges are **property-free**",
        "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md",
    ),
}

#: Where each retracted sentence is still QUOTED once the live documents no
#: longer carry it: the append-only changelog, which is the one place the
#: retracted text is allowed to live. `test_every_retracted_literal_is_still_
#: findable_where_it_is_allowed_to_live` requires each of these to be findable
#: there, MARKER-INSENSITIVELY — because a literal that matches nothing is a
#: dead pin, and this dict shipped exactly one before (the unbolded §13.8 q2
#: literal, which matched neither the pre-fix nor the fixed document).
RETRACTED_QUOTED_IN: dict[str, Path] = {
    "§11.1 calls the links property-free and reports a zero search": (
        STORAGE_CHANGELOG
    ),
    "§13 closes the structural door with 'any property'": STORAGE_CHANGELOG,
    "§13's inference of semantic freedom": STORAGE_CHANGELOG,
    "§11.1's cost sentence presents the zero as measured": STORAGE_CHANGELOG,
    "§11.3's table row still calls the rows property-free": STORAGE_CHANGELOG,
    "§11's estimate footnote leans on the property-free finding": (
        STORAGE_CHANGELOG
    ),
    "§13.8 q2 repeats the property-free claim (BOLDED, as written)": (
        STORAGE_CHANGELOG
    ),
    "§11.3's prose says the links stay bare": STORAGE_CHANGELOG,
    "§11.4 rule 1's imperative is property-free": STORAGE_CHANGELOG,
    "§11.1's method paragraph counted the write forms as two": STORAGE_CHANGELOG,
    "the corrected claim's own 'exactly one name' absolute": STORAGE_CHANGELOG,
    "§13.8 q2's 'never a second property name'": STORAGE_CHANGELOG,
}

#: The one retracted sentence this correction REWROTE rather than quoted, so no
#: file holds it today: its pre-fix form is the extractor ledger's `aboutObject`
#: decision row (`git show d8d050099:docs/architecture/`
#: `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`). Declared here so that EVERY entry
#: above is either machine-checkable or explicitly un-checkable, instead of
#: silently being neither.
RETRACTED_PRE_FIX_ONLY: frozenset[str] = frozenset({
    "the decision ledger's reason is property-freedom",
})

#: Retractions of wording THIS PR itself introduced in an earlier revision and
#: then withdrew. Their literals were never live on the pre-fix tree, so listing
#: them under the entries above (which record a PRE-FIX provenance) made the
#: file's own metadata false — the class of unmeasured claim it exists to stop.
#: They are still findable in the changelog (`RETRACTED_QUOTED_IN`), so the pins
#: fire; only the provenance differs.
RETRACTED_INTRODUCED_HERE: frozenset[str] = frozenset({
    "the corrected claim's own 'exactly one name' absolute",
    "§13.8 q2's 'never a second property name'",
    # Measured: this literal is ABSENT from `01b5486ec`, i.e. it was written by
    # an earlier revision of this PR and withdrawn by a later one (the
    # changelog's "A **third** stale absolute survived in the same section").
    # Filed as pre-fix-live until `#9403` review, which made the guard's own
    # failure message claim a pre-fix presence that never existed.
    "§11.1's method paragraph counted the write forms as two",
})

#: The writers the corrected passages must NAME, and the code shape that makes
#: each one a property-bearing `about*` writer. The axis here is the WRITE FORM — a
#: literal `SET r.<name>` in the source — and nothing else. This comment used to
#: classify them causally ("the ORIGINATORS") and carried the retracted 3+2
#: split; it must not restate a split at all, because no leg compares THIS
#: comment to the causal classes, so any count written here is unpinned prose.
#: Causality is pinned where it belongs: by
#: `test_the_writer_classification_separates_the_causal_axis_from_the_write_form`,
#: against the documents.
RECORDED_WRITERS: dict[tuple[str, str], tuple[str, ...]] = {
    ("tortoise/session_link.py", "link_entity"): (
        'MERGE (s)-[r:{edge_type}]->(t) SET r.confidence=$conf',
    ),
    ("tortoise/subject_binding.py", "bind_point_subjects"): (
        'SET r.confidence=$conf',
        '"object": ("Object", "aboutObject")',
    ),
    ("tortoise/projection/entities.py", "_fold_entity_linked_reason"): (
        'MERGE (s)-[e:{rel}]->(t) SET e.confidence=$conf',
    ),
}

#: The RE-MATERIALIZING writers: they carry the edge's whole attribute set by
#: property MAP over an allowlisted predicate (which includes `aboutObject`).
#: They are recorded as the MAP form because a map assignment's NAMES are
#: schema-driven and unreadable statically — `_SET_MAP` cannot read them, and
#: inventing one would be the same guess this file exists to stop. ⚠️ One of
#: them ALSO writes a name in the INLINE pattern-map form
#: (`cache_projection.relation_statement` stamps `{idx:$idx}` in its own
#: `MERGE` template), and that `idx` is read by the census's inline rule — so
#: the documents' set is `confidence` (originated) plus `idx`
#: (re-materialized), not one name. ⛔ The SYMBOL is the pointer, never a line
#: number: the number this block carried landed ~800 lines from
#: `relation_statement` and read as current, which is the defect class this
#: census exists to close (see
#: `test_no_bare_line_citation_survives_in_this_files_record`).
MAP_WRITERS: dict[tuple[str, str], tuple[str, ...]] = {
    #: #9417: the structural transfer now CARRIES the old edge's properties
    #: (`SET nr += $attrs`, whitelisted to the RelationUpsert vocabulary), so an
    #: `about*` edge keeps the binder's `confidence`. It was a declared false
    #: positive until this landed. REBUILD is still bare — traced as #10139.
    ("tortoise/sdk.py", "supersede_point"): (
        "SET nr += $attrs",
    ),
    #: #10139: the REBUILD half of `#9417`. The pass-2b structural repoint used to
    #: `MERGE` the transferred `about*` edge BARE, so the property the live
    #: transfer carried was dropped on every `rebuild_all` — the `#9553`
    #: live != rebuild class, on a data-bearing property. The descriptor now
    #: carries the retired edge's attributes and the fold re-applies them with the
    #: SAME `ON CREATE SET` precedence the live transfer uses. This row was
    #: `NOT_AN_ABOUT_WRITER`'s LAST exemption; it is a counted MAP-form writer now.
    #: ⚠️ The census's own whole-function union sees this site through the
    #: 2a-DIRECT `SET r += $attrs` leg, NOT through the structural one — the
    #: transfer's `SET` is carried in an interpolated `_carry` fragment, which is
    #: gap 1 of the module docstring's list. The structural statement is pinned by
    #: its own `shape` needle below and, BEHAVIOURALLY, by the two replay tests in
    #: `tests/test_8398_aboutobject_replay.py`; this row does not claim the census
    #: reads an interpolated `SET`, and saying so here is cheaper than a reader
    #: trusting a union that cannot see it.
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"): (
        "ON CREATE SET nr += $attrs",
    ),
    ("tortoise/cache_projection.py", "relation_statement"): (
        "MERGE (src)-[r:{rel.rel_type} {{idx:$idx}}]->(tgt) SET r += $props",
    ),
    ("tortoise/hosted_backup.py", "restore_graph"): (
        'CREATE (a)-[r:{safe_type}]->(b) SET r = $p',
    ),
}

#: Census rows that are FALSE POSITIVES of the whole-function variable union: the
#: function writes through `r`, but on a DIFFERENT relationship type than the
#: about edge it also transfers. They are recorded here, not in `MAP_WRITERS`,
#: because a row there asserts a re-materialization that does not happen — the
#: false-POSITIVE version of this PR's own defect (an instrument that cannot
#: tell which edge the `SET` lands on).
#: ⚠️ EMPTY since `#10139`. Its last row, `_rebuild_all_replay`, became a REAL
#: writer when the pass-2b structural repoint started re-applying the carried
#: attributes (`ON CREATE SET nr += $attrs`) — the exemption's premise was "the
#: about/structural transfer is a bare `MERGE`", and that premise is now false.
#: The table is kept (with `FALSE_POSITIVE_STATEMENTS` and the legs that read
#: both) because an empty table is a STATE, not a retirement: the next exemption
#: needs the pinned statement premise this machinery exists to demand, and the
#: matcher probe still proves the per-statement reading reds where a
#: function-wide one did not. `#9417` (`supersede_point`) and `#10139`
#: (`_rebuild_all_replay`) are the two rows that have left it, each by fixing the
#: CODE rather than by deleting the row.
NOT_AN_ABOUT_WRITER: dict[tuple[str, str], str] = {}

#: For each declared false positive: the STATEMENT the census attributes the
#: property to (needle 1), and the BARE about transfer it does NOT (needle 2).
#: Two per row because the exemptions are different code shapes — an `$attrs`
#: copy on a direct edge, and a journal-event fold — so a single module-wide
#: membership test would be satisfied while a `SET` sat on the about transfer,
#: which is exactly the false negative these rows must not hide.
FALSE_POSITIVE_STATEMENTS: dict[tuple[str, str], tuple[str, str]] = {}

#: The FIXED attribute vocabulary the false positives' `SET r += $attrs` is
#: built from (`{k: … for k in (…)}` / `… if k in (…)}`). This is the mechanism
#: that carries the exemption where the predicate is INTERPOLATED: a structural
#: descriptor carries none of these keys, so the map is empty and nothing lands
#: on whatever `etype` resolves to. Pinned as a CEILING — a statement copying a
#: key from outside it would put that key on an interpolated edge, which is the
#: `#9417` shape this exemption is one change away from.
FALSE_POSITIVE_ATTR_VOCAB: frozenset[str] = frozenset(
    {"direction", "confidence", "weight", "label", "batch_id", "source_version"}
)

#: Every producer of a `DirectEdgeCreated` record in the package that emits ANY
#: key from `FALSE_POSITIVE_ATTR_VOCAB`, and the mechanism that keeps that
#: attribute off an `about*` edge.
#:
#: ⛔ This map is not bookkeeping (`#9553` review P2). The `_rebuild_all_replay`
#: creation fold's `SET r += $attrs` is gated ONLY by the fixed key vocabulary —
#: the fold does NOT filter on `edge_type` — so the gate protects an about edge
#: only while EVERY attribute-carrying producer provably cannot name one. Before
#: `#9553` that was a single fact (`create_direct_edge`'s `{IMPL, NAND}`
#: allowlist); `#9553` added a SECOND producer, `sdk._record_structural_edge`
#: (the recorder the projection's edge mints reach through
#: `_record_structural_edges`), which journals
#: `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutDocument`/`aboutSource`/
#: `aboutAction` as readily as `ownedBy` — and it is safe only because it emits
#: no attribute at all. ⛔ NOT `sdk.create_edge`: `#9827` added that door's own
#: emission and the mint-level one REPLACED it, so a census naming the door here
#: describes a producer the tree does not have (`#10200` review: the tree holds
#: exactly two emitters — `_record_structural_edge`'s
#: `_emit_event("DirectEdgeCreated", …)` and `create_direct_edge`'s — and
#: `create_edge` says so in place, in the note that opens *"ONE PRODUCER, ON
#: PURPOSE, AND THIS DOOR IS NOT IT"*). ⛔ The emitters are cited by SYMBOL, not
#: by `sdk.py:<N>`: a bare line number in this paragraph is the same unpinned
#: prose this PR removed from the documents, it reads as CURRENT the moment the
#: symbol moves, and the `#10200` review followed the three numbers it used to
#: carry into `poll_events`, `list_batch` and `assess_source`. A producer that started passing one would land that
#: property on a live and replayed about edge while every other leg stayed
#: green, so the two halves are pinned together by
#: `test_every_direct_edge_created_producer_is_safe_for_an_about_edge`,
#: and the citation rule by
#: `test_the_producer_evidence_block_cites_the_emitters_by_symbol`.
#:
#: The value is the STATEMENT-level proof for an attribute-carrying producer,
#: asserted against its own function body — not a claim about intent.
DIRECT_EDGE_CREATED_ATTR_GUARDS: dict[tuple[str, str], str] = {
    ("tortoise/sdk.py", "create_direct_edge"): (
        'op_type not in ("IMPL", "NAND")'
    ),
}

#: The property. One ORIGINATED name; `idx` is the second recorded name, written
#: by the record-layer projection as an INLINE pattern map (see the third-form
#: note below).
PROPERTY = "confidence"
#: The second name, and the form that writes it.
SECOND_PROPERTY = "idx"

#: Sentinel for an edge write whose property NAMES cannot be read statically:
#: a property-MAP assignment (`SET r += $props`) carries whichever columns the
#: record/edge schema has. For the bound entity links here that set includes
#: `confidence` (`record_projection.RelationUpsert.attrs`; `public.relation`
#: has a `confidence` column), so a map-form writer reproduces the SAME one
#: name — it does not introduce a second one. Recorded as a form, not a name.
MAP_FORM = "<map:$props>"

_SET_ON = re.compile(r"SET\s+(\w+)\.(\w+)\s*=")
#: A COMMA-CONTINUED assignment in the same SET clause —
#: `SET r.confidence=$c, r.weight=$w`. `_SET_ON` reads only the first one, so a
#: second property name written this way would be a measured absence in the very
#: guard that claims the property set is bounded (the tree already uses the form
#: on non-about edges, e.g. `hosted_api.py`, `ep.py`).
_SET_ON_MORE = re.compile(r",\s*(\w+)\.(\w+)\s*=")
#: Property-MAP assignment on a relationship variable: `SET r += $props`,
#: `SET r = $p`. Structurally invisible to `_SET_ON`, and — like the
#: `aboutObject {` grep this PR exists to correct — a census that cannot match
#: it reports a measured absence. Four shipped sites already use this form.
_SET_MAP = re.compile(r"SET\s+(\w+)\s*(\+=|=)\s*(\$|\{)")
#: The THIRD form: a property written by an INLINE pattern map —
#: `MERGE (s)-[r:aboutObject {idx:$idx}]->(t)`. Its NAMES are statically readable
#: (unlike `SET r += $props`), and it appears in NO `SET` clause (unlike
#: `SET r.<prop>`), so both rules above were blind to it while
#: `cache_projection.relation_statement` shipped it on every edge it
#: materializes — including an allowlisted `aboutObject` one. Found by the
#: third review cycle: the first census missed the MAP form, the second missed
#: this, and each time the census's own blindness was the defect.
#: ⛔ There is deliberately NO `INLINE_FORM` sentinel beside `MAP_FORM` here: the
#: inline form's property NAMES are statically readable (`_INLINE_NAME`), so the
#: census records the NAMES and never needed a form placeholder. One existed and
#: nothing reached it, which in this file is the defect it names — *a rule that
#: only lives in dead code is not a rule* (`#10200` review, cycle 6, measured: 0
#: callers).
_INLINE_BRACE = re.compile(r"\{([^{}]*)\}")
_INLINE_NAME = re.compile(r"(\w+)\s*:")

#: The names each recorded row is allowed to report, EXACTLY. `test_no_unrecorded_
#: property_bearing_about_edge_writer` rejects an extra name on a recorded
#: writer, because a subset test on the writer KEY alone lets a new property
#: appear under an already-recorded site while every "the measured set is
#: `confidence` plus `idx`" sentence quietly becomes false.
EXPECTED_NAMES: dict[tuple[str, str], frozenset[str]] = {
    ("tortoise/session_link.py", "link_entity"): frozenset({PROPERTY}),
    ("tortoise/subject_binding.py", "bind_point_subjects"): frozenset({PROPERTY}),
    ("tortoise/projection/entities.py", "_fold_entity_linked_reason"): (
        frozenset({PROPERTY})
    ),
    ("tortoise/cache_projection.py", "relation_statement"): (
        frozenset({SECOND_PROPERTY, MAP_FORM})
    ),
    ("tortoise/hosted_backup.py", "restore_graph"): frozenset({MAP_FORM}),
    #: `_rebuild_all_replay` is compared like any other row. It arrived here (and
    #: in the census) as a declared FALSE POSITIVE, with `MAP_FORM` from its
    #: `IMPL|NAND`-scoped `SET r += $attrs`; since `#10139` it is a real map-form
    #: writer. It stays in this table either way, because an exemption with no
    #: expected set is exactly where a new property would slip past `unexpected` —
    #: the comparison is keyed on `EXPECTED_NAMES`, so the row has to be HERE to
    #: stay measured, whichever table above names it.
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"): (
        frozenset({MAP_FORM})
    ),
    ("tortoise/sdk.py", "supersede_point"): frozenset({MAP_FORM}),
}
#: A relationship pattern, captured as (variable, relationship-token). The
#: variable may be EMPTY: `MERGE (s)-[:aboutObject {confidence:$c}]->(t)` is
#: valid Cypher that names no variable, and `(\w+)` here made that writer
#: invisible to every rule below — the fourth instance of this class (named-only,
#: then map-only, then inline-only, now anonymous), found by review.
_PATTERN = re.compile(r"\[(\w*):([^\]]{0,120})\]")
_ABOUT_REL = re.compile(
    r"about(Subject|Object|Event|Point|Document|Action|Source)"
)
#: The shared about-edge vocabularies. A relationship name built by INTERPOLATION
#: is about-bearing only when the writer validates/derives it from one of these
#: (`link_entity` → `ENTITY_LINKED_RELS`, the replay fold → the record's `rel`,
#: the binder → `_ROLE_TARGET`). Without this clause the census also catches
#: unrelated interpolated writes (`sdk.ingest`'s `IMPL|NAND|hasPart` `r.idx`),
#: and a false positive is how a guard gets deleted instead of fixed.
#:
#: The PREDICATE ALLOWLIST (`validate_rel_type` / `KNOWN_REL_TYPES`) and the
#: restorer's `_sanitize_label` belong here for the map-form writers: they do
#: not name `aboutObject` at the write site at all — they take the predicate
#: from the allowlist that contains it (or from the dump, sanitized). Verified
#: against the tree: adding them raises the census from 5 rows to exactly 7 ROWS (the two rows they add are the map-form writers) —
#: the 5 recorded writers (3 named-form + 2 map-form) plus the 2 declared false
#: positives, which are rows the census sees and does not count as writers.
_ABOUT_VOCAB: tuple[str, ...] = (
    "ENTITY_LINKED_RELS",
    "_ABOUT_REPLAY_RELS",
    "_VALID_ABOUT_EDGE_PREDICATES",
    "ABOUT_EDGE_TYPES",
    "_ABOUT_TYPES",
    "STRUCTURAL_REL_LABELS",
    "_ROLE_TARGET",
    "KNOWN_REL_TYPES",
    "validate_rel_type",
    "_sanitize_label",
)


def _doc(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


_MARKUP = re.compile(r"[*`]")
#: A hyphen between two word characters is decoration on a CLAIM, not part of
#: it: measured, `property free` (hyphen dropped by a human re-typing the
#: retracted absolute) matched no pin, so every literal here was one keystroke
#: from a dead pin. It normalises to a SPACE, not to nothing — the missing-hyphen
#: form is the one a person types, so `property-free` and `property free` must
#: compare equal. En/em-dash included; `\w` keeps the surrounding tokens apart.
_HYPHEN = re.compile(r"(?<=\w)[-\u2013\u2014](?=\w)")
_WHITESPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Marker-insensitive form of a sentence, for the RETRACTED pins.

    `**no** `aboutObject` edge carries any property` and the unbolded variant are
    the SAME claim; matching raw substrings is how one entry here became a dead
    pin that could never red. Emphasis and code markers are decoration on a
    claim, not part of it.
    """
    return _WHITESPACE.sub(" ", _HYPHEN.sub(" ", _MARKUP.sub("", text)))


def _wrong_ordinal_claim(text: str, wrong: str) -> bool:
    """True when `text` calls the re-materializing writer by the ordinal `wrong`.

    Case-insensitive on purpose, and that is a MEASURED requirement, not a
    convenience: the pin this replaces read
        `f"{wrong} NAMED form writer" not in storage.lower()`
    — a lowercased haystack against a needle carrying uppercase `NAMED`, i.e. a
    comparison that could never match. `_flat` does not case-fold, so the sides
    have to agree on case HERE, and `test_the_document_population_counts_match_
    the_census` probes this function against a synthetic sentence before it trusts
    the result (a pin that cannot be shown to red is not evidence).
    """
    return re.search(rf"\b{wrong} NAMED form writer\b", text, re.I) is not None


def _own_body(node: ast.AST, lines: list[str]) -> str:
    """The node's source with any NESTED definition blanked out."""
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = node.end_lineno or node.lineno  # type: ignore[attr-defined]
    seg = list(lines[start:end])
    for child in ast.walk(node):
        if child is node or not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        for i in range(child.lineno - 1, child.end_lineno or child.lineno):
            if start <= i < end:
                seg[i - start] = ""
    return "\n".join(seg)


def _qualified(tree: ast.Module):
    def walk(node: ast.AST, prefix: str = ""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield prefix + child.name, child
                yield from walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, prefix + child.name + ".")

    yield from walk(tree)


class UnparsableModule(Exception):
    """A module in the census's scope that `ast.parse` (or the strict UTF-8
    decode in `_about_edge_property_writers`) rejected.

    Raised rather than swallowed: a module nobody can read has an UNMEASURED
    property-write population, and reporting `{}` for it is how a census
    manufactures a measured absence. The message names the module so the
    reader knows which file to fix, and the leg that raises it reds.
    """


def _direct_edge_created_emissions(
    module: str, text: str | None = None
) -> list[tuple[str, int, set[str]]]:
    """Every `_emit_event("DirectEdgeCreated", …)` call in `module`.

    Returns `(symbol, lineno, kwarg_names)` — the KEYWORD NAMES the call passes,
    which is what decides whether the record can carry an attribute the fold's
    `attrs` map would copy onto an edge. SCOPED TO THE CALL, like
    `_query_calls_containing`: a module-wide text union cannot answer "does THIS
    emission carry `direction`".

    `symbol` is the last segment of the qualified name, so a method on
    `TortoiseSDK` reads as `create_direct_edge` (the census elsewhere keys on the
    same shape).
    """
    source = text if text is not None else (ROOT / module).read_text(encoding="utf-8")
    out: list[tuple[str, int, set[str]]] = []
    for name, node in _qualified(ast.parse(source)):
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            callee = (
                func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if callee != "_emit_event" or not call.args:
                continue
            head = call.args[0]
            if not (isinstance(head, ast.Constant)
                    and head.value == "DirectEdgeCreated"):
                continue
            out.append(
                (
                    name.split(".")[-1],
                    call.lineno,
                    {kw.arg for kw in call.keywords if kw.arg},
                )
            )
    # `_qualified` yields a nested function once per enclosing scope it is
    # reached through; dedupe on the call's own position so one emission is one
    # row.
    seen: set[tuple[int, str]] = set()
    unique: list[tuple[str, int, set[str]]] = []
    for symbol, lineno, kwargs in out:
        key = (lineno, symbol)
        if key in seen:
            continue
        seen.add(key)
        unique.append((symbol, lineno, kwargs))
    return unique


def _function_body(module: str, symbol: str) -> str:
    """The source of `symbol` in `module`, nested definitions blanked out."""
    text = (ROOT / module).read_text(encoding="utf-8")
    lines = text.splitlines()
    for name, node in _qualified(ast.parse(text)):
        if name.split(".")[-1] == symbol:
            return _own_body(node, lines)
    raise AssertionError(f"{module}::{symbol} no longer exists")


def _query_calls_containing(body: str, needle: str) -> list[str]:
    """EVERY `.query(...)` CALL in `body` that carries `needle`, in source order.

    SCOPED to the call, not the function or the module: a premise about WHICH
    statement a property lands in cannot be checked against a function-wide text
    union — that union is precisely the false positive `NOT_AN_ABOUT_WRITER`
    declares — and a module-wide membership test is weaker still.

    ⚠️ PLURAL, and that is the point (`#9403` review cycle 2, P1). The FIRST
    version returned the first match only, and
    `projection._rebuild_all_replay` carries TWO `SET r += $attrs` statements
    (the `DirectEdgeCreated` fold and the structural-repoint fold). The
    exemption leg inspected only the first, so a property reaching an about edge
    through the second stayed GREEN — a fail-open cell inside the leg whose whole
    job is to close one. A needle that matches N statements has N premises.
    """
    out: list[str] = []
    for match in re.finditer(r"\.query\(", body):
        start = match.end() - 1  # at the '('
        depth = 0
        for i in range(start, len(body)):
            char = body[i]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    call = body[start : i + 1]
                    if needle in call:
                        out.append(call)
                    break
    return out


def _nearest_map_assignment(
    func: ast.AST, call: ast.AST, text: str
) -> str:
    """The source of the map a `SET r += $attrs` call actually writes.

    `params={"attrs": <name>}` → the nearest PRECEDING assignment to `<name>` in
    the same function. Returned as source so the caller can assert on the shape
    (a dict comprehension over a FIXED key vocabulary) rather than on a name.
    """
    name = None
    for keyword in call.keywords:  # type: ignore[attr-defined]
        if keyword.arg != "params" or not isinstance(keyword.value, ast.Dict):
            continue
        for key, value in zip(
            keyword.value.keys, keyword.value.values, strict=True
        ):
            if (
                isinstance(key, ast.Constant)
                and key.value == "attrs"
                and isinstance(value, ast.Name)
            ):
                name = value.id
    assert name is not None, (
        "the `SET r += $attrs` call no longer passes its map as "
        "`params={'attrs': <name>}` — this premise measures nothing: "
        f"{ast.get_source_segment(text, call) or ''}"
    )
    best = None
    for node in ast.walk(func):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        if node.lineno > call.lineno:  # type: ignore[attr-defined]
            continue
        if best is None or node.lineno > best.lineno:  # type: ignore[attr-defined]
            best = node
    assert best is not None, (
        f"`{name}` (the map written by `SET r += $attrs`) is no longer assigned "
        "in this function, so the fixed-vocabulary premise cannot be checked"
    )
    return ast.get_source_segment(text, best.value) or ""


#: Where a NAMED key vocabulary is defined, in resolution order: the module that
#: imports it names it from this one (`projection/edges.py`, the single home the
#: `DERIVABLE_STRUCTURAL_RELS`/`STRUCTURAL_REL_LABELS` constants share).
_VOCABULARY_MODULES = ("tortoise/projection/edges.py",)


def _named_vocabulary(name: str) -> list[str]:
    """The literal string members a module-level NAME is bound to.

    ⛔ A NAME, not only a literal tuple (`#10139`). The gated folds filter on
    `DIRECT_EDGE_ATTR_KEYS` — ONE definition, imported by both halves of the
    carry, renamed from the structural-only spelling precisely so the live
    transfer and its replay cannot drift. A reader that understands only LITERAL
    tuples returns `[]` for that fold, i.e. "no fixed vocabulary", which is a
    measured absence about the very protection the per-statement leg asserts —
    and the exemption machinery would report it as a dead premise rather than as
    a reader that went blind.
    """
    for relative in _VOCABULARY_MODULES:
        path = ROOT / relative
        if not path.exists():  # pragma: no cover - the tree ships it
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == name
                       for target in targets):
                continue
            value = node.value
            if isinstance(value, ast.Subscript):  # `tuple((...))` / annotated forms
                value = value.slice
            if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
                return [
                    element.value
                    for element in value.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                ]
    return []


def _fixed_key_vocabulary(source: str) -> list[str]:
    """The key names a `{k: v for … if k in (…)}` map is built from.

    A fixed vocabulary is what makes the exemption hold where the predicate is
    INTERPOLATED: a descriptor carrying none of these keys yields an EMPTY map,
    and an empty map writes nothing on whatever `etype` resolves to.

    A NAME is resolved through `_named_vocabulary` (`#10139`) — the same claim,
    read through the constant the code now imports instead of a literal tuple
    spelled at the write site.
    """
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.DictComp):
            continue
        for generator in node.generators:
            wanted = [generator.iter, *(
                compare.comparators[0]
                for compare in generator.ifs
                if isinstance(compare, ast.Compare)
                and any(isinstance(op, ast.In) for op in compare.ops)
            )]
            for candidate in wanted:
                if isinstance(candidate, (ast.Tuple, ast.List, ast.Set)):
                    names.extend(
                        element.value
                        for element in candidate.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    )
                elif isinstance(candidate, ast.Name):
                    names.extend(_named_vocabulary(candidate.id))
    return names


def _setter_gate_premises(
    module: str, text: str, symbol: str, needle: str
) -> list[tuple[str, list[str]]]:
    """(enclosing-`if` test, fixed key vocabulary) for EVERY `needle` SET.

    SCOPED TO THE STATEMENT, and that is the correction (`#9403` review, P3):
    the previous version asserted `if k in (…)|if attrs:` against the whole
    FUNCTION BODY, so `_rebuild_all_replay`'s second `SET r += $attrs` — the
    structural-`DirectEdgeRepoint` fold, whose `etype` is an allowlisted
    predicate that CAN be `aboutObject` — was covered by the OTHER fold's gate.
    That is the same function-wide text union the plural
    `_query_calls_containing` exists to reject for the about literal, one level
    down. Each statement is walked to its OWN enclosing `if`, and the map it
    writes is walked to the assignment that builds it.

    Returns one pair per `needle` statement. Raises when a statement is
    UNGATED (no enclosing `if`) or its map is not built from a fixed literal
    key vocabulary — either way the exemption has stopped holding for that
    statement, which is the fail-open cell this leg exists to close.
    """
    tree = ast.parse(text)
    func = None
    for name, candidate in _qualified(tree):
        if name.split(".")[-1] == symbol:
            func = candidate
            break
    assert func is not None, f"{module}::{symbol} no longer exists"
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(func):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    out: list[tuple[str, list[str]]] = []
    # Only the calls that SPAN a line carrying the needle can be matches, and the
    # line index is built once: `get_source_segment` re-splits the whole module
    # per call, which turned a 16k-line module into ~17 s of O(n²) slicing in the
    # first version of this helper (measured).
    needle_lines = {
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if needle in line
    }
    for call in ast.walk(func):
        if not isinstance(call, ast.Call):
            continue
        span = range(
            call.lineno,  # type: ignore[attr-defined]
            (call.end_lineno or call.lineno) + 1,  # type: ignore[attr-defined]
        )
        if not needle_lines.intersection(span):
            continue
        if needle not in (ast.get_source_segment(text, call) or ""):
            continue
        gate = None
        walker: ast.AST | None = call
        while walker is not None:
            parent = parents.get(walker)
            if isinstance(parent, ast.If) and walker in parent.body:
                gate = parent
                break
            walker = parent
        assert gate is not None, (
            f"the `{needle}` statement in `{module}::{symbol}` is no longer "
            "gated by an enclosing `if` — the exemption's premise is that a "
            "descriptor carrying none of the fixed attribute keys writes "
            "nothing, and an UNCONDITIONAL write puts whatever the descriptor "
            "holds onto whatever its interpolated `etype` names"
        )
        assert not isinstance(gate.test, ast.Constant), (
            f"the `{needle}` statement in `{module}::{symbol}` is `if "
            f"{ast.dump(gate.test)[:40]}`-gated, and a CONSTANT test is not a "
            "gate: it says nothing about the attribute map, so the exemption "
            "would rest on a condition that can never be false"
        )
        vocabulary = _fixed_key_vocabulary(
            _nearest_map_assignment(func, call, text)
        )
        assert vocabulary, (
            f"the map written by `{needle}` in `{module}::{symbol}` is no longer "
            "built by filtering a FIXED literal key vocabulary ("
            "`{k: … for k in (…)}` / `… if k in (…)}`) — the exemption rests "
            "on that fixed list: a structural descriptor carrying none of "
            "those keys must yield an EMPTY map"
        )
        out.append((ast.get_source_segment(text, gate.test) or "", vocabulary))
    assert out, (
        f"no `{needle}` statement found in `{module}::{symbol}` — the census "
        "row was derived from it, and an exemption that describes code which "
        "moved protects nothing"
    )
    return out


def _query_call_containing(body: str, needle: str) -> str:
    """The FIRST `.query(...)` call carrying `needle`, for premises that are
    about a statement the census attributed a property to by construction
    (the single bare transfer). Prefer `_query_calls_containing` wherever the
    needle can match more than one statement."""
    calls = _query_calls_containing(body, needle)
    return calls[0] if calls else ""


#: Bounded history depths tried, in order, to make `PRE_FIX_REV` reachable in a
#: SHALLOW checkout. The gating `test` job in `python-ci.yml` uses a bare
#: `actions/checkout@v4` (default `fetch-depth: 1`) — only the `changes` job
#: unshallows (`:224`) — so a bare `git show <rev>` returns nothing there and the
#: provenance leg would SKIP on every CI run, which `skip-guard.py` does not
#: treat as a failure (it guards FalkorDB/embedder/module-collection reasons).
#: ⛔ Fetching the raw sha does NOT work: measured in a `--depth 1` clone,
#: `git fetch --depth=1 origin 01b5486ec` → *"couldn't find remote ref"* (the
#: server does not expose arbitrary reachable shas). Fetching the `main` REF at a
#: bounded depth DOES: measured in the same clone, `git fetch --depth=60 origin
#: main` makes `01b5486ec` reachable. Depth is raised only until the rev resolves,
#: so the cost is one shallow branch fetch and the bound is the point at which
#: `01b5486ec` is genuinely too old to reach — reported by the skip message.
#: One numbered entry of the "WHAT IS PINNED" index.
_PIN_INDEX_ENTRY = re.compile(r"^\d+\. `(test_[A-Za-z0-9_]+)`", re.M)

_SHALLOW_DEPTHS = (120, 400, 1500)


def _has_commit(rev: str) -> bool:
    """True when `rev` resolves to a commit in this checkout's history."""
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{rev}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return False
    return done.returncode == 0


def _is_shallow() -> bool:
    """True when this checkout is a SHALLOW clone."""
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


def _ensure_commit(rev: str) -> bool:
    """Make `rev` reachable IF the checkout is shallow — bounded, best effort.

    ⛔ `git fetch --depth=N` does not merely top up a full clone: MEASURED on a
    non-shallow clone of this PR, running it turned `--is-shallow-repository`
    from `false` to `true` and cut `git rev-list --count origin/main` from 6106
    to 120, creating `.git/shallow`. On a developer's full clone that TRUNCATES
    their history — a destructive side effect on the checkout under test, from a
    test that runs in `tier1`/`core`. So the fetch is gated: a non-shallow clone
    that lacks the rev SKIPS (honest — it cannot reach old history without being
    rewritten) and is never shallow-ified.
    """
    if _has_commit(rev):
        return True
    if not _is_shallow():
        return False
    for depth in _SHALLOW_DEPTHS:
        try:
            subprocess.run(
                [
                    "git", "-C", str(ROOT), "fetch", "--quiet",
                    f"--depth={depth}", "origin", "main",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return False
        if _has_commit(rev):
            return True
    return False


def _git_show(rev: str, path: str) -> str | None:
    """`git show <rev>:<path>`, or None where the history is unavailable."""
    _ensure_commit(rev)
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    return done.stdout if done.returncode == 0 else None


def _writers_in_module(
    module: str, text: str
) -> dict[tuple[str, str], set[str]]:
    """The census over ONE module's source. Split out so a SYNTHETIC module can be
    put through it: the two fail-open forms found in review (the inline pattern
    map, and a writer that names its predicate from the allowlist instead of
    spelling `about`) are only pinned non-vacuously by feeding this function a
    module that uses them — no shipped file has to exist for the rule to be
    proved live."""
    found: dict[tuple[str, str], set[str]] = {}
    if True:  # one flat body, so the file/rglob caller below stays a loop
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            # FAIL CLOSED, and name the file. Returning `{}` here would censor a
            # broken module as "writes no property on any about edge" — the
            # same measured-absence-from-an-unreadable-source defect as
            # `rg 'aboutObject {'`, and `# pragma: no cover` was wrong on both
            # counts: the module is readable text, and this is precisely the
            # branch `test_the_census_fails_closed_on_a_module_it_cannot_parse`
            # must cover (third review cycle).
            raise UnparsableModule(
                f"{module} could not be parsed, so the property writes it "
                f"contains are UNMEASURED — this is a STOP, not a measured "
                f"absence: {exc}"
            ) from exc
        lines = text.splitlines()
        for qualname, node in _qualified(tree):
            body = _own_body(node, lines)
            about_vars: set[str] = set()
            inline: dict[str, set[str]] = {}
            interpolated = "{" in body and any(
                vocab in body for vocab in _ABOUT_VOCAB
            )
            for var, rel in _PATTERN.findall(body):
                # INTERPOLATION counts only for a NAMED variable. An
                # anonymous pattern with an interpolated predicate is how the
                # EP operators are written — `MERGE (op)-[:{rtype} {idx:$idx}]
                # ->(new)` with `rtype` in {IMPL, NAND} — and admitting it made
                # `idx` a census false positive in `_rebuild_all_replay` and
                # `supersede_point`. An anonymous `about*` edge is still read:
                # its predicate is a literal there
                # (`-[:aboutObject {confidence:$c}]->`).
                if _ABOUT_REL.search(rel) or (
                    var and "{" in rel and interpolated
                ):
                    about_vars.add(var)
                    # An inline pattern map (`{idx:$idx}`) is a property write
                    # whose NAME is right there — read it rather than assume.
                    for brace in _INLINE_BRACE.findall(rel):
                        inline.setdefault(var, set()).update(
                            _INLINE_NAME.findall(brace)
                        )
            if not about_vars:
                continue
            for var, names in inline.items():
                # A brace with no `name: value` pair contributes nothing: record
                # no empty set, which would report a writer for writing nothing.
                if var in about_vars and names:
                    found.setdefault(
                        (module, qualname.split(".")[-1]), set()
                    ).update(names)
            for var, prop in _SET_ON.findall(body) + _SET_ON_MORE.findall(body):
                if var in about_vars:
                    found.setdefault(
                        (module, qualname.split(".")[-1]), set()
                    ).add(prop)
            # The property-MAP form (review finding on this file's first
            # version): `SET r += $props` / `SET r = $p` writes an edge's whole
            # attribute set and is invisible to `_SET_ON`. Recorded as the FORM
            # (`MAP_FORM`), because the names are schema-driven and unreadable
            # statically — see `MAP_WRITERS`.
            if any(var in about_vars for var, _op, _rhs in _SET_MAP.findall(body)):
                found.setdefault(
                    (module, qualname.split(".")[-1]), set()
                ).add(MAP_FORM)
    return found


def _module_in_scope(text: str) -> bool:
    """Can this module NAME an about predicate — by spelling one, or by taking it
    from the shared predicate vocabulary a map-form writer uses INSTEAD of
    spelling it (`validate_rel_type` / `KNOWN_REL_TYPES` / `_sanitize_label`)?

    The prefilter used to be the literal word "about", which is exactly the word
    `_ABOUT_VOCAB`'s own justification says such a writer does NOT contain — so
    the vocabulary widening never reached the files it exists for (third review
    cycle; measured: a map-form module with no "about" anywhere was skipped).
    Its own leg pins both halves, because a prefilter is a fail-open by
    construction: anything it skips is reported as a measured absence.
    """
    return "about" in text or any(v in text for v in _ABOUT_VOCAB)


def _about_edge_property_writers() -> dict[tuple[str, str], set[str]]:
    """{(module, symbol): {property, …}} for every property-bearing about-edge write.

    BOUND: an edge counts as `about*` by an `about*` literal in the relationship
    token, or by INTERPOLATION in a function body that names one of
    `_ABOUT_VOCAB` (and, above that, in a module `_module_in_scope` admits). A
    writer that names its predicate some other way is invisible here — the
    promise is "nothing NEW appears unrecorded among the forms and vocabularies
    we read", never "nothing can appear".
    """
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        # STRICT, not `errors="replace"`: a byte-compromised module would
        # otherwise be censused as DIFFERENT TEXT than leg 3 (which reads it
        # strictly) parses, and the two legs would be measuring two different
        # files while both reported green. A decode error raises, which is the
        # same fail-closed behaviour as an unparsable module.
        text = path.read_text(encoding="utf-8")
        if not _module_in_scope(text):
            continue
        found.update(_writers_in_module(path.relative_to(ROOT).as_posix(), text))
    return found


def _provenance(label: str, home: str) -> str:
    """The per-entry provenance a leg-1 failure prints.

    Not every retracted literal was live pre-fix: three absolutes were written
    by THIS PR's own earlier revision and withdrawn by a later one. Printing "was
    live pre-fix" for them would put an unmeasured claim in the diagnostic of
    the guard whose whole subject is unmeasured claims — which is exactly what
    happened to the `§11.1` method entry before
    `test_every_retracted_literal_declares_its_true_provenance` measured the split.
    """
    if label in RETRACTED_INTRODUCED_HERE:
        return (
            f"introduced and retracted inside #9397, never present pre-fix; "
            f"quoted in the changelog ({home} is where it was written)"
        )
    return f"was live in {home} pre-fix"


# ── 1. the retracted sentences ─────────────────────────────────────────────


def test_the_absolute_property_free_sentences_do_not_reappear():
    #: The LIVE design documents. A retracted absolute claim may not survive in
    #: them — not even as a quotation, which is the prior art's rule in
    #: `tests/test_extractor_v4_citation_rot.py` (the retracted text belongs in
    #: the TEST and in the CHANGELOG's correction entry, not in the design doc,
    #: where a reader would take it as current).
    live = {
        "STORAGE-ARCHITECTURE.md": _doc(STORAGE),
        "EXTRACTOR-V4-ARCHITECTURE.md": _doc(EXTRACTOR),
    }
    #: The decision ledger's own reason sentence is a live claim too, so it is
    #: checked — but only for ITS literal, because the same file legitimately
    #: quotes the retracted sentences inside this correction's entry.
    ledger = _doc(EXTRACTOR_CHANGELOG)
    #: `name` is the LIVE document the sentence survived in — printed, because
    #: `home` is the pre-fix document the entry was measured in, and a
    #: STORAGE-home literal that re-rots inside the extractor doc would
    #: otherwise report the wrong file in the diagnostic of a guard whose whole
    #: subject is the accuracy of provenance statements.
    survivors = [
        f"{label}: {sentence!r} in {name} ({_provenance(label, home)})"
        for label, (sentence, home) in RETRACTED.items()
        for name, text in live.items()
        if _flat(sentence) in _flat(text)
    ]
    ledger_literal = RETRACTED[
        "the decision ledger's reason is property-freedom"
    ][0]
    if _flat(ledger_literal) in _flat(ledger):
        survivors.append(
            "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md: the `aboutObject` decision "
            "row still gives property-freedom as its reason"
        )
    assert not survivors, (
        "a retracted 'property-free aboutObject edge' claim is live again:\n  "
        + "\n  ".join(survivors)
        + "\n\nAn `about*` edge carries `confidence` "
        f"({', '.join(f'{m}::{s}' for m, s in sorted(RECORDED_WRITERS))}). "
        "The claim was recorded from a search (`rg 'aboutObject {'`) that "
        "structurally cannot see a `SET r.<prop>` writer — re-deriving it with "
        "the same pattern would reproduce the defect."
    )


# ── 2. the corrected passages must say what is true ────────────────────────


def _section(text: str, start: str, end: str) -> str:
    """The slice of `text` from `start` up to (not including) `end`.

    A SECTION slice, not the whole file: `link_entity`, `bind_point_subjects`
    and `` `confidence` `` each appear in more than one section, so a file-wide
    membership test stays green when ONE section is silently reverted — the
    failure this leg exists to catch (`#8639` review, the
    `test_entity_attachment_brief_claims.py` precedent: *"the §5 row carries the
    same literals, so a file-wide membership test cannot red on §6.7's silent
    deletion"*).
    """
    i = text.find(start)
    # A moved/renumbered anchor is a plausible tidy-up in a draft document, and
    # a bare `ValueError: substring not found` reads as a BROKEN TEST rather than
    # a rotted guarantee — the diagnostic standard the §11.4 leg argues for. Fail
    # with the claim named, so every section-scoped leg inherits it.
    if i < 0:
        pytest.fail(
            f"the section anchor {start!r} no longer exists — it moved, so the "
            "claim this leg scopes is UNMEASURED (a moved anchor is not a pass)"
        )
    j = text.find(end, i + len(start))
    if j < 0:
        pytest.fail(
            f"the section end anchor {end!r} no longer follows {start!r} — the "
            "slice would run off the end, so the claim is UNMEASURED"
        )
    return text[i:j]


#: The corrected passages, located by heading/lead-in text (not by line number,
#: per the documents' own rule). Each must name both originating writers and the
#: property, INSIDE its own section.
CORRECTED_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "STORAGE-ARCHITECTURE.md §11.1",
        "### 11.1 Why the connections are not the cost problem",
        "### 11.2 What actually costs money",
        "STORAGE",
    ),
    (
        "STORAGE-ARCHITECTURE.md §13",
        "## 13. Filed from this document",
        "## 14. What this document does not decide",
        "STORAGE",
    ),
    (
        "EXTRACTOR-V4-ARCHITECTURE.md §13.8 q2",
        "2. **Should `aboutObject` be a stored edge or a derived join?**",
        "3. **Should every expansion have a hard fan-out cap**",
        "EXTRACTOR",
    ),
)


def test_the_pin_index_names_every_leg():
    """The "WHAT IS PINNED" index must name every test in this module.

    The index is prose, and prose that indexes code re-stales: measured at
    `#9403` review, it carried 24 entries against 25 tests (the share-retraction
    leg was missing) while its own heading claimed "one entry per test, in file
    order" — and neither the missing entry nor the order claim was pinned, so a
    reader trusting the index would not learn the module had grown. The merge
    union that produced 25 tests from two 24-test parents re-derived neither.
    This leg pins BOTH halves of the heading: the SET (every test exactly once)
    and the FILE ORDER — the order is what lets a reader map a failure message to
    its entry, and an appended index silently loses it.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    defined = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    # ⛔ The match must be confined to the INDEX BLOCK, not file-wide: measured
    # at `#9403` review, deleting an index entry while adding one
    # `^\d+\. \`test_…\`` line anywhere else (this module's docstring already
    # carries such a numbered list) left the leg GREEN — the drift it exists to
    # catch had a green path. And the count must be compared, not only the set:
    # `findall` collapses duplicates into a set, so two entries for one name and
    # none for another also passed.
    start = source.index("WHAT IS PINNED")
    block = source[start : source.index("NOT promised:", start)]
    matches = _PIN_INDEX_ENTRY.findall(block)
    indexed = set(matches)
    assert len(matches) == len(indexed), (
        "the pin index lists a leg more than once: "
        f"{sorted({n for n in matches if matches.count(n) > 1})}"
    )
    assert len(matches) == len(defined), (
        f"the pin index has {len(matches)} entries for {len(defined)} tests — "
        "one test per entry"
    )
    assert indexed <= defined, (
        "the pin index names legs that do not exist: "
        f"{sorted(indexed - defined)}"
    )
    assert not (defined - indexed), (
        "these legs are not in the WHAT IS PINNED index, so the index no longer "
        f"describes this module: {sorted(defined - indexed)}"
    )
    # The ORDER half of the heading. `findall` returns the entries in the order
    # they appear in the block, so compare it against the file's own `def test_`
    # order: an appended (or hand-moved) entry reds here instead of quietly
    # mapping a failure message to the wrong paragraph.
    in_file_order = [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    mismatch = next(
        (
            (position, want, got)
            for position, (want, got) in enumerate(
                zip(in_file_order, matches, strict=False), start=1
            )
            if want != got
        ),
        None,
    )
    assert mismatch is None, (
        "the WHAT IS PINNED index is out of FILE ORDER, so a reader maps a "
        f"failure to the wrong entry: position {mismatch[0]} lists "
        f"{mismatch[1]!r} but the file defines {mismatch[2]!r} there"
    )
    assert [int(number) for number in re.findall(r"^(\d+)\. ", block, re.M)] == (
        list(range(1, len(matches) + 1))
    ), (
        "the pin index's ordinals are not 1..N in order — they are its only "
        "index, so a gap or a duplicate makes position and number disagree"
    )


def test_the_shallow_gate_precedes_the_fetch():
    """`_ensure_commit` must consult `_is_shallow()` BEFORE it fetches, and `_is_shallow` must
    mean what its name says.

    The gate exists because of a measured destructive reading: `git fetch --depth=N` on a
    NON-shallow clone turned `--is-shallow-repository` from false to true and cut
    `rev-list --count origin/main` from 6106 to 120, creating `.git/shallow`. This file runs in
    `tier1`/`core`, so without the gate a test truncates a developer's history.

    And the gate was UNPINNED: deleting `if not _is_shallow(): return False` left every leg
    green (measured at `#9403` review), because the only nearby assertion checked that the
    strings `"fetch"` and `"--depth="` still existed in the source, which they still would, with
    the guard gone. This leg parses the function instead, so the ORDER is what is pinned: a
    conditional on `_is_shallow` returning False must appear before the `_SHALLOW_DEPTHS` loop.
    Structural, not behavioural, because the destructive path is unreachable in a checkout where
    `PRE_FIX_REV` already resolves, and deliberately so.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(source)
    funcs = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
    }
    assert {"_ensure_commit", "_is_shallow"} <= set(funcs), (
        "the provenance helpers are gone, so this pin has nothing to read"
    )
    body = funcs["_ensure_commit"].body
    gate_at = next(
        (
            index
            for index, node in enumerate(body)
            if isinstance(node, ast.If)
            and "_is_shallow" in ast.dump(node.test)
            and any(
                isinstance(inner, ast.Return)
                and isinstance(inner.value, ast.Constant)
                and inner.value.value is False
                for inner in node.body
            )
        ),
        None,
    )
    fetch_at = next(
        (
            index
            for index, node in enumerate(body)
            if isinstance(node, ast.For) and "_SHALLOW_DEPTHS" in ast.dump(node.iter)
        ),
        None,
    )
    assert gate_at is not None, (
        "`_ensure_commit` no longer returns False when the checkout is NOT shallow, so the "
        "fetch would shallow a full clone (measured: 6106 -> 120 commits, `.git/shallow`)"
    )
    assert fetch_at is not None, (
        "`_ensure_commit` no longer has the bounded `_SHALLOW_DEPTHS` fetch loop, so this pin "
        "is stale and the provenance leg now fails differently"
    )
    assert gate_at < fetch_at, (
        f"the `_is_shallow` gate is at statement {gate_at} and the fetch loop at {fetch_at}: "
        "the gate must come FIRST, or the fetch runs anyway"
    )
    assert "is-shallow-repository" in ast.dump(funcs["_is_shallow"]), (
        "`_is_shallow` no longer asks git `--is-shallow-repository`, so it cannot mean what its "
        "name and the gate above assume"
    )


def test_the_unmeasured_share_retraction_is_pinned():
    """§13 rests on a §11.1 sentence that nothing pinned.

    §13 says an unbound entity link's two ids are "a statement about the row's
    SHAPE, not about the population's share", pointing at §11.1's retraction of
    the share claim. Measured before this leg existed: deleting that sentence
    from §11.1 left every leg green (22 passed, 1 skipped), so §13 kept a
    cross-reference into a sentence that no longer existed — a retraction
    resting on an unpinned premise, which is the class this file exists to
    stop. Section-scoped via `_section` for the same reason as the corrected
    passages: a file-wide membership test cannot red on one section's deletion.
    """
    storage = (ROOT / "docs/architecture/STORAGE-ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    _label, start, end, which = CORRECTED_SECTIONS[0]
    assert which == "STORAGE"
    section = _flat(_section(storage, start, end))
    assert "not measured here" in section, (
        "§11.1 no longer states that the bare-row SHARE is not measured here — "
        "§13's cross-reference now points at nothing, and the cost estimate "
        "reads as if a share had been measured"
    )


def test_the_corrected_passages_name_the_property_bearing_class():
    docs = {
        "STORAGE": _doc(STORAGE),
        "EXTRACTOR": _doc(EXTRACTOR),
    }
    for label, start, end, which in CORRECTED_SECTIONS:
        section = _section(docs[which], start, end)
        for symbol in ("link_entity", "bind_point_subjects"):
            assert symbol in section, (
                f"{label} does not name `{symbol}` — the corrected evidence must "
                "name the writers that put a property on an about edge, IN the "
                "section that makes the claim"
            )
        assert f"`{PROPERTY}`" in section or f"r.{PROPERTY}" in section, (
            f"{label} does not name the `{PROPERTY}` property — the claim "
            "'property-free' is replaced by a named, bounded exception, not by "
            "silence"
        )
        # The SECOND recorded name, and the third write form: `confidence` alone
        # is the ORIGINATED set. A record-projected about edge also carries `idx`
        # (`cache_projection.relation_statement`, an inline pattern map), so a
        # section naming only `confidence` re-asserts an absolute the code
        # disproves — the exact defect this PR exists to correct.
        assert f"`{SECOND_PROPERTY}`" in section, (
            f"{label} no longer names `{SECOND_PROPERTY}` — the record-layer "
            "projection stamps it inline on an allowlisted `aboutObject` edge "
            "(`cache_projection.relation_statement`), so the measured set is "
            "`confidence` "
            "(originated) plus `idx` (re-materialized), not one name"
        )
    # The surviving true half must stay stated: the GENERIC writer sets nothing.
    storage = docs["STORAGE"]
    assert "create_about_edge" in storage, (
        "STORAGE-ARCHITECTURE.md no longer distinguishes the generic writer "
        "(`create_about_edge`, no SET) from the binder's property-bearing one"
    )
    # The MAP form must be named too, or the documents describe only the half of
    # the property-bearing population that the first census could see.
    for label in ("§13", "§13.8 q2"):
        section = next(
            _section(docs[w], s, e) for lab, s, e, w in CORRECTED_SECTIONS
            if lab.endswith(label)
        )
        assert "property MAP" in section or "SET r += $props" in section, (
            f"{label} no longer records the property-MAP form of the write — "
            "the documented property set then covers only `SET r.<prop>` "
            "writers, which is the blindness this file exists to stop"
        )
    # The UNMEASURED SHARE, in whatever spelling (`#9403` review, P1). §11.1 says
    # in as many words that *"Which SHARE of the rows is bare is not measured
    # here"*, and the same PR's corrected §13 / §13.8 q2 then said *"most entity
    # links are two ids"* and *"The structural door stays open **for most links**"*
    # — a population-share claim nothing measured, contradicting the paragraph
    # that withdraws it. Measured GREEN while all three were live. Share words
    # appear elsewhere in both documents for other claims, so the pin is
    # BAREness-specific rather than a word ban.
    share_claims = (
        "most entity links",
        "most links",
        "mostly stays open",
        "bulk of the rows",
        "most of the rows",
        "majority of the rows",
    )
    for label, start, end, which in CORRECTED_SECTIONS:
        section = _flat(_section(docs[which], start, end))
        found = [claim for claim in share_claims if _flat(claim) in section]
        assert not found, (
            f"{label} states an unmeasured population SHARE {found} — the row's "
            "shape is measured (an UNBOUND link is two ids), what SHARE of the "
            "rows is unbound is not, and §11.1 says so; scope the claim to the "
            "row shape or measure the share"
        )
    # The FORM COUNT on the originated set (`#9403` review, P2). §13 said the
    # ORIGINATED set is "written in two forms" and then listed three writers
    # that all use the NAMED form — the map form belongs to the RE-MATERIALIZING
    # class in the next sentence. Same defect the retraction pins in §11.1
    # (*"Two forms write to an edge, and both are in scope"*), left standing in
    # §13 and pinned by nothing (measured GREEN).
    form_counts = ("written in two forms", "it is written in two forms")
    storage_13 = _flat(
        next(
            _section(docs[w], s, e) for lab, s, e, w in CORRECTED_SECTIONS
            if lab.endswith("§13")
        )
    )
    survivors = [literal for literal in form_counts if _flat(literal) in storage_13]
    assert not survivors, (
        f"§13 reintroduces the retracted FORM COUNT {survivors} for the "
        "originated set — every originating writer uses the NAMED form "
        "`SET r.<prop>`; the property MAP belongs to the re-materializing "
        "class, which the next sentence already enumerates"
    )


#: The pre-fix spelling of §13's `create_about_edge` citation, kept as a
#: PROBE: the rule below must red on it, and a matcher that has stopped matching
#: protects nothing (the `RETRACTED` dead-pin lesson, same file).
STALE_CITATION_PROBE = (
    "The generic writer `create_about_edge` (`projection/edges.py:344`) emits "
    "`MERGE (s)-[:aboutObject]->(t)` with **no** `SET`."
)
_EDGES_LINE_CITE = re.compile(r"edges\.py:(\d+)")
_PINNED_REVISION = re.compile(r"pinned revision|at the pin\b", re.I)


def _unqualified_edges_line_citations(text: str) -> list[str]:
    """Every `projection/edges.py:<n>` citation in `text` that is NOT marked as
    belonging to a PINNED revision.

    This file — and the documents it guards — state that the SYMBOL is
    authoritative and line numbers are not asserted. At HEAD
    `create_about_edge` is `projection/edges.py:1447`; `:344` is a `#:` comment
    block that MENTIONS the symbol, which is exactly why a stale number reads as
    plausible: a reader who verifies the corrected §13 evidence lands on prose.
    The pre-fix text was right AT ITS PIN (`c79ba1cf2`), so the qualifier — not
    the number — is the part that is either true or missing.
    """
    cites: list[tuple[int, str]] = [
        (m.start(), m.group(0)) for m in _EDGES_LINE_CITE.finditer(text)
    ]
    # §11.1 spells the same citation as a SEPARATE token — "(`projection/edges.py`,
    # `create_about_edge` — `:344` at the pinned revision)" — so an
    # `edges.py:<n>`-adjacency matcher cannot see it. MEASURED at `#9403`
    # review: deleting " at the pinned revision" there left this leg green, and
    # the surviving bare `:344` then reads as CURRENT while `:344` is a `#:`
    # comment block and the symbol sits at `:1447`. So a number standing alone in
    # the window after an `edges.py` mention is a citation too.
    for mention in re.finditer(r"edges\.py", text):
        window = text[mention.end(): mention.end() + 200]
        number = re.search(r":(\d+)", window)
        if number and not re.match(r"[\w/]", window[number.start() - 1]):
            cites.append((mention.end() + number.start(), number.group(0)))
    out: list[str] = []
    for start, cite in sorted(cites):
        if cite in out:
            continue
        if not _PINNED_REVISION.search(text[start + len(cite): start + len(cite) + 120]):
            out.append(cite)
    return out


def test_the_corrected_passages_carry_no_bare_edges_line_citation():
    """The corrected §13 passage and §13.8 q2 re-asserted a STALE `file:line`
    (`projection/edges.py:344`) as current — the definition is at `:1447` at HEAD
    and `:344` is a comment that mentions the symbol (third review cycle). The
    claim itself was true and nothing rested on it, but a cited line that lands
    on prose is how a verified-looking sentence stops being verifiable.

    The qualifier is load-bearing, not decoration: §11.1's copy is `:344` **at
    the pinned revision**, which is true and stays."""
    assert _unqualified_edges_line_citations(STALE_CITATION_PROBE), (
        "the citation rule does not match the PRE-FIX §13 spelling "
        f"({STALE_CITATION_PROBE!r}), so it cannot red on a re-introduction"
    )
    docs = {"STORAGE": _doc(STORAGE), "EXTRACTOR": _doc(EXTRACTOR)}
    bare = [
        f"{label} cites {cite!r} as current — a line number without a "
        "'pinned revision' qualifier"
        for label, start, end, which in CORRECTED_SECTIONS
        for cite in _unqualified_edges_line_citations(
            _section(docs[which], start, end)
        )
    ]
    assert not bare, (
        "the corrected evidence cites a line number as if it were current:\n  "
        + "\n  ".join(bare)
        + "\n\nDrop the number and keep the SYMBOL (`projection/edges.py`), or "
        "qualify it with the revision it was measured at — `create_about_edge` "
        "is at `projection/edges.py:1447` at HEAD, and `:344` is a comment "
        "block that merely mentions it."
    )


def test_the_references_paragraph_scopes_property_freedom_to_source_version():
    """§9.6 said identity/mention and referential-containment `references` links
    "stay **property-free**" — the same unqualified absolute this PR retracts for
    `aboutObject`, in the §11.1 correction below (third review cycle).

    The absolute is retracted because it is an absolute, not because a
    `references` edge carries a property today. No writer that MINTS one of
    these links stamps a property on it: `link_source_to_entity`'s
    non-derivation branch is `MERGE (s)-[:references]->(e)` with no `SET`
    (`projection/edges.py`); so are the identity/mention writer
    (`projection/entities.py`'s connector link) and the containment one
    (`hosted_api.py`'s session→external MERGE); and so is
    `graph-scripts/backfill_references.py`, which mints BOTH classes — its
    docstring declares the `aboutEvent`/`aboutDocument` DERIVATION rows beside the
    identity ones — with a bare `MERGE` (it is an ops script, not a provenance
    writer, so its derivation rows are not auto-anchored either). The bare
    `create_edge` escape hatch stamps nothing by construction: it takes no
    property parameter. `session_source_migration._move_out` re-points an
    existing edge with `ON CREATE SET r2 = properties(r)` — it PRESERVES attrs,
    never mints one.

    `cache_projection.relation_statement` *does* stamp `{idx:$idx}` +
    `SET r += $props`, but only for record-projected `public.relation` rows, and
    no record kind emits a `references` relation: `EntityLinked` is about*-only,
    `DirectEdgeCreated` is IMPL/NAND, and `DirectEdgeRepoint` is IMPL/NAND plus
    the `SUPERSEDE_STRUCTURAL_RELS` transfer — a set that excludes `references`.
    So "a `references` link is not property-free" overstates: today it IS, which
    is exactly why the word must be scoped to the fact the paragraph establishes
    rather than to a general emptiness claim.

    The FACT the paragraph establishes is narrower and true: those forms carry
    no `sourceVersion`. The word is scoped here rather than the paragraph
    rewritten, so the derivation-anchor claim it is actually about is untouched.
    """
    text = _doc(STORAGE)
    start = "The anchor extends to the **derivation** `references` link only"
    end = "⚠️ **The mechanism discriminates on the target's LABEL"
    paragraph = _section(text, start, end)
    assert _flat("carry **no `sourceVersion`**") in _flat(paragraph), (
        "§9.6 no longer states the true, narrow fact for the identity/containment "
        "`references` links — that they carry no `sourceVersion`"
    )
    retracted_absolutes = [
        "links stay **property-free**",
        "links stay property-free",
    ]
    live = [
        literal for literal in retracted_absolutes
        if _flat(literal) in _flat(paragraph)
    ]
    assert not live, (
        "§9.6 still makes the retracted absolute claim "
        f"{live} — the word is retracted because it is an ABSOLUTE, not because "
        "an identity/mention or containment `references` edge carries a property "
        "today (no writer that mints one of THOSE stamps anything on it — the "
        "PROVENANCE derivation writers stamp `sourceVersion` by design, while the "
        "`create_edge` escape hatch and `graph-scripts/backfill_references.py` "
        "mint derivation edges and are not auto-anchored). Scope it to the fact "
        "the paragraph establishes: those links carry **no `sourceVersion`**."
    )


def test_the_cheapness_rule_states_the_measured_set_and_records_the_tension():
    """§11.4 rule 1 is the ONE rule this PR rewrote in place, and until this leg
    existed nothing pinned it (`#9403` review, P2):

    * its pre-fix imperative ("Keep entity links property-free") was not in
      `RETRACTED`, so the revert to the absolute could not red;
    * §11.4 was not in the corrected-passage pins (they reach §11.1, §13 and
      §13.8 q2), so a SILENT DELETION of the `#1370` tension record was green
      too — and that record is the only place the ruling's exception is
      written down as unresolved.

    Measured (the reviewer's reproduction, re-run here): restoring the pre-fix
    sentence verbatim at the PR head left every leg green. So the same absolute
    the correction exists to stop could be re-derived in the rule that gives
    the cheapness argument its name — the failure mode the changelog itself
    names ("a correction that fixes one wording and leaves three").

    SECTION-scoped to rule 1, not to §11.4: the section carries four rules, and
    a whole-section test would stay green on rule 1's revert.
    """
    text = _doc(STORAGE)
    rule = _section(
        _section(
            text,
            "### 11.4 The four rules that keep it cheap",
            "### 11.5 Fan-out cap",
        ),
        # The shared prefix, so the slice still resolves on the PRE-FIX
        # wording — a start anchor of `near-bare` would raise IndexError there
        # instead of reding on the claim, and an error is a worse diagnostic
        # than a named failure for the exact input this leg exists to catch.
        "1. **Keep entity links",
        "2. **Cap the fan-out**",
    )
    assert _flat("near-bare") in _flat(rule), (
        "§11.4 rule 1 no longer says the entity links are `near-bare` — the "
        "measured set is `confidence` on bound links plus the record "
        "projection's `idx` ordinal, so the rule cannot be stated as "
        "property-freedom in either wording"
    )
    retracted = [
        literal for literal in ("Keep entity links property-free",)
        if _flat(literal) in _flat(rule)
    ]
    assert not retracted, (
        f"§11.4 rule 1 states the RETRACTED absolute {retracted} — the claim "
        "§11.1/§13 retract cannot survive as the rule's own imperative "
        "(leg 1 pins the same literal file-wide; this names the section)"
    )
    assert "#1370" in rule and "EntityLinked" in rule, (
        "§11.4 rule 1 no longer carries the `#1370` tension record, journaled "
        "through `EntityLinked` — the binding `confidence` on a bound link is "
        "the one recorded exception to this rule, and deleting its record "
        "turns a known, flagged conflict back into a satisfied rule"
    )
    assert "RECORDED, not resolved" in rule, (
        "§11.4 rule 1's tension is no longer marked as RECORDED rather than "
        "resolved — whether a binding confidence belongs on the link or on "
        "the claim is an unrecorded design decision (`#9397`), and a guard "
        "must not let prose settle it by dropping the qualifier"
    )
    # The MEASURED SET, not only the qualifier vocabulary: measured, a rule
    # that kept `near-bare` while dropping both property names, or while
    # swapping `confidence` for a different noun, stayed green — a leg named
    # `…states_the_measured_set…` that pinned no name was overnamed, which is
    # the same defect as a document that overclaims. `weight` is called out
    # because it is a DIFFERENT property: no about-edge writer writes it.
    for name in (PROPERTY, SECOND_PROPERTY):
        assert f"`{name}`" in rule, (
            f"§11.4 rule 1 no longer names the measured property `{name}` — "
            "the rule may not state the exception as a bare qualifier, and a "
            "record-projected about edge carries the projection's ordinal"
        )
    # The OPEN SET, in the same breath as the two names (`#9403` review cycle 2,
    # P1). The rule said "two ids — plus the binder's `confidence` … and the
    # projection's `idx` ordinal", a CLOSED pair, while §11.1's own footnote
    # says the record-map writers' further attributes are an open set and §13
    # says the same. A record-projected about edge carries the row's placement
    # columns (`RelationUpsert.attrs`: direction, confidence, weight, label,
    # batch_id, source_version) and the open `props` tail beside `idx`, so a
    # closed pair misstates the population this rule is about.
    for marker in ("placement columns", "props", "open set"):
        assert marker in rule, (
            f"§11.4 rule 1 no longer states the OPEN SET ({marker!r}) — the "
            "two named properties sit in a set the map-form writers fill at "
            "runtime, so a closed pair is a population claim the code "
            "contradicts (§11.1's footnote, §13)"
        )
    assert "does carry a `weight`" not in rule, (
        "§11.4 rule 1 calls the `#1370` binding attribute a `weight` — the "
        "code sets `confidence`, and `weight` is a different property that no "
        "about-edge writer in the census writes"
    )
    # The POPULATION absolute (`turns 27,310 bare rows into …`), which the
    # retracted-imperative pin structurally cannot see: it re-asserts that the
    # WHOLE population is bare, forty lines after §11.1 says a record-projected
    # edge is not. Measured green on the pre-fix wording of this clause.
    population_absolutes = (
        # Lower-case: `_flat` does not case-fold, and the re-derivation this
        # catches ("The entity links carry no properties at all.") is a
        # mid-sentence phrase — measured GREEN before this list existed.
        "bare rows",
        "carry no properties",
        "property free",
        # `#9403` review, P1: the round-9 fix replaced `bare rows` with *"bare
        # apart from the projection's `idx` ordinal"* — the SAME population
        # absolute in a spelling no entry above could see, while the sentence
        # ten lines below it records that a BOUND link carries `confidence`.
        # Measured GREEN with the clause live. Scope the clause to the row's
        # shape, and pin the spelling.
        "bare apart from",
    )
    survivors = [
        literal for literal in population_absolutes
        if _flat(literal) in _flat(rule)
    ]
    assert not survivors, (
        f"§11.4 rule 1 states the population-level absolute {survivors} — an "
        "unqualified 'bare rows' claims the whole population is bare, which "
        "§11.1/§11.3 contradict for every record-projected edge; scope it to "
        "the rows that carry nothing but the id pair"
    )


def test_the_cost_argument_states_the_open_set_not_a_closed_pair():
    """§11.3 is where a reader decides what the migration does to this layer, and
    its table row plus its prose summarised the entity link as *"two ids, plus
    the binder's `confidence` on a *bound* link and the projection's `idx`
    ordinal"* — a CLOSED pair, while §11.1's footnote in the same section says
    the record-map writers' further attributes are an open set and §13 says the
    same (`#9403` review cycle 2, P1).

    Measured: reverting §11.3's row to the closed pair left every leg green —
    the population-absolute list is §11.4-scoped and the corrected-passage pins
    reach §11.1/§13/§13.8 q2, so §11.3 was pinned by nothing. The claim is the
    same one §11.4 rule 1 makes, so it is pinned in the place it is made.
    """
    section = _flat(
        _section(
            _doc(STORAGE),
            "### 11.3 Two different things, both called",
            "### 11.4 The four rules that keep it cheap",
        )
    )
    # Asserted PER PASSAGE, not section-wide: the section states the same fact
    # twice (the table row, then the prose below it), so a section-wide marker
    # test stayed GREEN when only the table row was reverted to the closed pair
    # — measured. Each passage is its own claim.
    passages = {
        "§11.3 table row": [
            line for line in _doc(STORAGE).splitlines()
            if "| **Entity links** (`aboutObject`, `aboutSubject`) |" in line
        ],
        "§11.3 prose": [
            line for line in _doc(STORAGE).splitlines()
            if "while the entity links stay near-bare" in line
        ],
    }
    for label, lines in passages.items():
        assert lines, (
            f"{label} is gone — this leg measures nothing (the passage could "
            "have moved, or been rewritten past the anchor)"
        )
        text = _flat(" ".join(lines))
        for marker in ("placement columns", "props", "open set"):
            assert _flat(marker) in text, (
                f"{label} no longer states the OPEN SET ({marker!r}) — the two "
                "named properties sit in a set the map-form writers fill at "
                "runtime (the record row's placement columns and the `props` "
                "tail), so a closed pair understates what a record-projected "
                "about edge carries"
            )
        assert _flat("two recorded properties") not in text, (
            f"{label} states a CLOSED count of the recorded properties — the "
            "set is open, and the count is the retracted absolute in a smaller "
            "font"
        )
    assert "placement columns" in _flat(section), (
        "§11.3 no longer states the open set anywhere — the closed-pair "
        "summary is back in every passage this leg anchors on"
    )


#: The extractor ledger's KEEP row, located by its first cell (no line numbers).
LEDGER_KEEP_ROW = "| **Keep the epistemic + entity connection layer** |"


def test_the_ledger_keep_row_names_both_recorded_properties():
    """The `#9397` correction row in the extractor ledger said the edges are
    *"bare apart from the binder's `confidence`"* — TRUE of the originated set
    and FALSE as the reason the layer is cheap, because a record-projected
    `aboutObject` edge also carries `idx` (the second name, written in the third
    form; `#9403` review called this the weaker half of the same gap).

    The row's RETRACTED wording (`Cheap because edges are **property-free**`) is
    pinned, so a revert to THAT reds. Before this leg, everything else was
    unpinned — dropping `idx`, or re-asserting a closed one-name reading in
    other words, kept every leg green. What this leg closes, stated as measured
    rather than as "any other route": the row must name BOTH measured
    properties, it must not present them as the whole exception (the record-map
    writers' further attributes are an open set), and five absolute spellings
    are rejected. A reformulation outside those five is still unpinned — the
    guard is a detector, and this file says so about itself.
    """
    rows = [
        line for line in _doc(EXTRACTOR_CHANGELOG).splitlines()
        if LEDGER_KEEP_ROW in line
    ]
    assert len(rows) == 1, (
        "the extractor ledger's KEEP row is gone or duplicated — "
        f"found {len(rows)} lines matching {LEDGER_KEEP_ROW!r}"
    )
    row = rows[0]
    # The row's "reason" CELL — column 4 of the table row, split on the pipe.
    cell = row.split("|")[4]
    for prop in (PROPERTY, SECOND_PROPERTY):
        assert f"`{prop}`" in row, (
            f"the ledger's KEEP row does not name `{prop}` — the measured set "
            "is `confidence` (originated) plus `idx` (re-materialized by "
            "`cache_projection.relation_statement`), and the row is the reason "
            "a reader takes the layer to be cheap"
        )
    # The OPEN SET the two names sit in: a row that presents `confidence` and
    # `idx` as the WHOLE exception turns a named set into a closed total, which
    # the record-map writers (`SET r += $props`, `SET r = $p`) do not support —
    # their names are schema-driven and not statically readable
    # (`EXPECTED_NAMES[relation_statement]` is the sentinel for exactly that).
    assert "props" in row and "placement columns" in row, (
        "the ledger's KEEP row presents `confidence` + `idx` as the whole "
        "exception — the record-map writers also carry the row's placement "
        "columns and open-set `props` tail, and a closed reading of an open "
        "set is the retracted defect in a smaller font"
    )
    # The RETRACTED reason, plus the one-name spellings that re-derive it. The
    # bare word `property-free` is deliberately NOT tested on its own: the row
    # NAMES the withdrawn wording in its own correction note ("the earlier
    # "property-free" was read from a search that cannot see a `SET r.<prop>`
    # writer"), so a word-level test would red on the note that RECORDS the
    # retraction. These spellings all assert a closed count instead.
    one_name_absolutes = (
        # The row's own retracted reason, and the closed-count spellings that
        # re-derive it. The BARE word `property free` is deliberately absent:
        # the row's correction note quotes the withdrawn wording ("the earlier
        # "property-free" was read from a search that cannot see a
        # `SET r.<prop>` writer"), so a bare-word test would red on the note
        # that RECORDS the retraction. These all assert a closed count.
        "Cheap because edges are property-free",
        "one semantic property",
        "only one property",
        "essentially property free",
        # A CLOSED COUNT in a different spelling: the row said "two recorded
        # properties" and then conceded, in the same sentence, that a
        # record-projected edge also carries the row's placement columns and an
        # open-set `props` tail (`#9403` review cycle 2, P2). Two names plus an
        # open set is not "two properties".
        "two recorded properties",
        "exactly two properties",
    )
    survivors = [
        literal for literal in one_name_absolutes
        if _flat(literal) in _flat(row)
    ]
    assert not survivors, (
        f"the ledger's KEEP row re-derives the retracted absolute {survivors} "
        "— the row is the reason a reader takes the layer to be cheap, so a "
        "closed count there is the same defect the correction exists to stop"
    )
    # The row must also RENDER: it carried 2 opening and 3 closing parentheses
    # and an odd (11) count of `**` markers at the `#9403` head, so the emphasis
    # span broke and a stray `)` appeared mid-sentence — in the row this leg's
    # own docstring calls "the reason a reader takes the layer to be cheap"
    # (`#9403` review, P2). Substring assertions cannot see that, and neither
    # can markdownlint: MD-emphasis checks do not run inside a table cell.
    assert cell.count("(") == cell.count(")"), (
        f"the ledger's KEEP row has unbalanced parentheses "
        f"({cell.count('(')} open, {cell.count(')')} close) — a table cell "
        "renders that as a stray bracket mid-sentence"
    )
    assert cell.count("**") % 2 == 0, (
        f"the ledger's KEEP row has an ODD number of `**` markers "
        f"({cell.count('**')}), so one emphasis span is unclosed and the rest "
        "of the cell renders bold or literal"
    )


# ── 3. the code must still agree with the corrected evidence ───────────────


def test_the_code_still_agrees_with_the_corrected_evidence():
    writers = _about_edge_property_writers()
    stale: list[str] = []
    recorded = {
        **{k: (shapes, PROPERTY) for k, shapes in RECORDED_WRITERS.items()},
        **{k: (shapes, MAP_FORM) for k, shapes in MAP_WRITERS.items()},
    }
    for key, (shapes, expected) in recorded.items():
        module, symbol = key
        text = (ROOT / module).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            stale.append(f"{module}::{symbol} — module does not parse")
            continue
        if not any(True for name, node in _qualified(tree)
                   if name.split(".")[-1] == symbol):
            stale.append(f"{module}::{symbol} — symbol no longer exists")
            continue
        for shape in shapes:
            if shape not in text:
                stale.append(f"{module}::{symbol} — lost {shape!r}")
        if expected not in writers.get(key, set()):
            stale.append(
                f"{module}::{symbol} — the census no longer sees it writing "
                f"{expected!r} on a relationship variable"
            )
    assert not stale, (
        "the CODE moved away from the corrected evidence — re-derive the "
        "documented claim instead of leaving it stale:\n  " + "\n  ".join(stale)
    )


def _unqualified_line_citations(text: str) -> list[str]:
    """Every `path.py:<n>` / `path.sql:<n>` citation in `text` that carries no
    revision qualifier (`at HEAD`, `at the pin`, `at the pinned revision`)
    within a short following window.

    Same rule as `_unqualified_edges_line_citations` below, generalised to every
    module rather than `projection/edges.py`: the `#9397` changelog entry uses a
    BARE line number as shorthand for "current", and the entry itself records
    that this exact convention was wrong three times (three `cache_projection`
    line citations, which is why this module's own record names SYMBOLS).
    """
    cite = re.compile(r"`[\w./-]+\.(?:py|sql):\d+`")
    qualifier = re.compile(r"at HEAD|at the pin\b|at the pinned revision", re.I)
    out: list[str] = []
    matches = list(cite.finditer(text))
    for i, m in enumerate(matches):
        # The window STOPS at the next citation. Measured: a neighbour's
        # `— at HEAD` inside a flat 120-character window qualified a bare
        # citation beside it, so removing `(at HEAD)` from the `security.py`
        # citation while the next clause carried one stayed GREEN (vacuous pin).
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # ... AND at the newline. Measured: a LATER bullet's "— at HEAD"
        # qualified a bare citation in an earlier line, because the window ran
        # past the paragraph break (`#9403` review cycle 2, P2).
        newline = text.find("\n", m.end())
        if 0 <= newline < end:
            end = newline
        if qualifier.search(text[m.end() : end]):
            continue
        out.append(m.group(0))
    return out


#: Cardinal and ordinal spellings, 1..12 — ONE table per spelling family, because
#: the documents state these counts in WORDS as well as digits (`#9403` review:
#: §13 wrote "ORIGINATED by two sites" and "written by three in the named form",
#: which the digit pins could not see). `#10149` extends the members so numerals
#: that are DERIVED from the tables — the invisible site's ordinal is `total + 1`
#: — can be stated at the population's own width instead of stopping at six.
NUMBER_WORDS: dict[int, str] = {
    #: ZERO is a legitimate COUNT and the table has to read it: since `#10139`
    #: `NOT_AN_ABOUT_WRITER` is EMPTY, so the documents' false-positive count is
    #: stated as "zero" and a reader without this member would report that no
    #: passage states the count at all. There is deliberately no ordinal member
    #: for it — the ordinal family starts at "second", which is why the span
    #: assertion below compares against the cardinals ABOVE zero.
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}
ORDINAL_WORDS: dict[int, str] = {
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
}


def test_the_document_population_counts_match_the_census():
    """§13's population numbers must equal what this file's tables actually hold.

    §13 tells a reader how wide the property-bearing population is and how it
    splits, and §11.1 says how much of it ORIGINATES the property. MEASURED at
    `#9403` review: editing §13 to "6 writers the census can see … sees four of
    the five" stayed GREEN, and adding a fourth NAMED-form writer to the census
    (the procedure this file's own failure message prescribes) also stayed
    green, leaving §13 and §11.1 false. So the numbers are pinned to the TABLES:
    a writer added or removed there now reds here and forces the documents to be
    corrected in the same commit.
    """
    named = len(RECORDED_WRITERS)
    mapped = len(MAP_WRITERS)
    total = named + mapped
    storage = _flat(_doc(STORAGE))

    assert f"{total} writers" in storage, (
        f"§13 no longer states the census population of {total} writers "
        f"(NAMED {named} + MAP {mapped}); the tables hold {total}"
    )
    assert f"{named} that write it in the NAMED form" in storage, (
        f"§13 no longer states {named} NAMED-form writers, which is what "
        f"`RECORDED_WRITERS` holds ({named})"
    )
    assert f"{mapped} that write it by property MAP" in storage, (
        f"§13 no longer states {mapped} property-MAP writers, which is what "
        f"`MAP_WRITERS` holds ({mapped})"
    )
    # The causal axis must PARTITION the same population, not exceed it.
    # the two counts are stated with their parenthetical explanations between them,
    # so the window has to allow prose: "2 ORIGINATING (the two writers that write it
    # first) and 4 RE-MATERIALIZING (the replay fold, …)"
    # `_flat` normalises the hyphen out, so match the space form too
    split = re.search(
        r"(\d+) ORIGINATING.{0,200}?(\d+) RE[ -]MATERIALIZING", storage, re.S
    )
    assert split, "§13 no longer states the causal split"
    originating, rematerializing = int(split.group(1)), int(split.group(2))
    assert originating + rematerializing == total, (
        f"§13's causal split {originating} + {rematerializing} does not add up "
        f"to the census population of {total}"
    )
    # The document states the same two numbers in a DIFFERENT spelling, in §13:
    # "ORIGINATED by two sites and written by three in the named form". (At this head
    # §11.1 spells them differently again — "the two that ORIGINATE it" and "a third
    # NAMED-form writer" — so §11.1 is not where those numerals live; an earlier
    # revision of this comment said it was, and the leg passed anyway because it
    # searches the whole flattened document.) That spelling was pinned by nothing:
    # measured at `#9403` review, editing it to "three sites" left the suite green
    # while the paragraph above contradicted it. Words, not digits, so both spellings
    # are checked against the tables.
    words = NUMBER_WORDS
    assert f"ORIGINATED by {words[originating]} sites" in storage, (
        f"the document no longer states 'ORIGINATED by {words[originating]} sites', which is the "
        f"ORIGINATING count {originating} the census split implies"
    )
    assert f"written by {words[named]} in the named form" in storage, (
        f"the document no longer states 'written by {words[named]} in the named form', which is "
        f"what `RECORDED_WRITERS` holds ({named})"
    )
    # ⛔ ONE table per spelling family — the rule the `NUMBER_WORDS`/`ORDINAL_WORDS`
    # comment above states, and the one this literal broke (`#10200` review,
    # cycle 11). It carried a THIRD ordinal copy reaching only `{2..6}` while
    # `ORDINAL_WORDS` reaches twelve, and `ordinals[named]` below is INDEXED from
    # it: the day `RECORDED_WRITERS` grows to seven this leg raises `KeyError: 7`
    # instead of reding with the actionable message the sibling leg gives
    # (`test_the_population_numerals_are_derived_from_the_census_tables` —
    # "extend `NUMBER_WORDS`/`ORDINAL_WORDS` rather than dropping the
    # derivation"). The span assertion below is what keeps a re-narrowed copy
    # from coming back: nothing else pinned `ORDINAL_WORDS` to the cardinal
    # family it mirrors, so a table that stopped short was drift nobody saw.
    ordinals = ORDINAL_WORDS
    #: The ordinal family starts at "second" (`ORDINAL_WORDS[1]` would be
    #: "first", which this instrument never writes), so the comparison runs
    #: against the cardinals ABOVE ZERO. `NUMBER_WORDS[0]` exists since `#10139`
    #: to read the documents' "zero declared false positives"; without this filter
    #: that legitimate member would shift the expected floor to `first` and red
    #: a table that is correct.
    cardinals_for_ordinals = {k: v for k, v in words.items() if k > 0}
    assert (min(ordinals), max(ordinals)) == (
        min(cardinals_for_ordinals) + 1, max(words)
    ), (
        "the ordinal family must span the same population as the cardinal "
        f"family (`NUMBER_WORDS` {min(words)}..{max(words)} vs `ORDINAL_WORDS` "
        f"{min(ordinals)}..{max(ordinals)}): a table that stops short raises "
        "`KeyError` on a grown population instead of reding with the message "
        "that says what to extend"
    )
    assert f"sees {words[named]} of the {words[total]}" in storage, (
        f"§13's census sentence no longer matches the tables: it must say "
        f"'sees {words[named]} of the {words[total]}' (RECORDED_WRITERS={named}, total={total})"
    )
    # `_flat` normalises the hyphen out of "NAMED-form", and the sentence appears twice
    # (lowercase once, capitalised once), so match the space form and require BOTH
    # occurrences to carry the ordinal the table implies.
    ordinal_hits = re.findall(rf"\b{ordinals[named]} NAMED form writer\b", storage, re.I)
    assert len(ordinal_hits) == 2, (
        f"the document must say '{ordinals[named]} NAMED form writer' in both places it names "
        f"that writer (RECORDED_WRITERS={named}); found {len(ordinal_hits)}"
    )
    # ⛔ The matcher is PROBED before it is used, because this loop was DEAD
    # (`#9403` review, P2): it read
    #     `f"{wrong} NAMED form writer" not in storage.lower()`
    # — a LOWERCASED haystack searched for a needle carrying uppercase `NAMED`,
    # so it could never match, and §11.1 could be edited to call that writer the
    # "second NAMED-form writer" with every leg green. `_flat` strips markup and
    # normalises hyphens; it does NOT case-fold. A pin that cannot be shown to
    # red is not evidence (this module's own rule), so the probe below asserts
    # the matcher SEES a wrong ordinal and does NOT fire on the right one: a
    # reintroduced case mismatch reds here instead of quietly pinning nothing.
    probe = _flat("a second NAMED-form writer, and the third NAMED form writer")
    assert _wrong_ordinal_claim(probe, "second"), (
        "the wrong-ordinal matcher cannot see 'second NAMED form writer' in a "
        "synthetic sentence, so the loop below pins nothing (a case mismatch "
        "between haystack and needle is how this pin went dead once already)"
    )
    control = _flat(f"the {ordinals[named]} NAMED-form writer")
    assert not _wrong_ordinal_claim(control, "second"), (
        f"the wrong-ordinal matcher fires on a sentence that only names the "
        f"CORRECT ordinal ({ordinals[named]!r}), so the loop below would red on "
        "the corrected document"
    )
    # `ORDINAL_WORDS` maps NUMBER -> word, so the wrong ordinals are its VALUES
    # whose number is not the table's; reading the keys instead is how a loop
    # over `ordinals.items()` can silently search for "2 NAMED form writer" and
    # match nothing — the same dead-pin shape this block exists to close.
    wrong_ordinals = [word for number, word in ordinals.items() if number != named]
    assert len(wrong_ordinals) >= 3, (
        "the wrong-ordinal set collapsed — it exists to catch a renumbered "
        f"writer, and only {wrong_ordinals} remain"
    )
    for wrong in wrong_ordinals:
        assert not _wrong_ordinal_claim(storage, wrong), (
            f"§11.1 still calls that writer the '{wrong}' NAMED-form writer while "
            f"RECORDED_WRITERS={named} makes it the {ordinals[named]}"
        )
    _fp = len(NOT_AN_ABOUT_WRITER)
    _fp_noun = "positive" if _fp == 1 else "positives"
    assert f"{words[_fp]} declared false {_fp_noun}" in storage, (
        "§13's declared-false-positive count no longer matches `NOT_AN_ABOUT_WRITER` "
        f"({len(NOT_AN_ABOUT_WRITER)})"
    )
    assert (originating == 2 and "the two that ORIGINATE it" in storage) or (
        f"the {originating} that ORIGINATE it" in storage
    ), (
        f"§11.1 no longer states that {originating} writers ORIGINATE the property"
    )
    assert len(NOT_AN_ABOUT_WRITER) == 0, (
        "the exemption table changed size; §13's population arithmetic and the "
        "declared-false-positive prose were written for ZERO exemptions — "
        "`_rebuild_all_replay` was the LAST one and left when #10139 gave its "
        "pass-2b structural repoint the carried attributes, so it moved into "
        "MAP_WRITERS. A new exemption here must also update §13's population, "
        "its causal split and its declared-false-positive count"
    )


def test_the_writer_classification_separates_the_causal_axis_from_the_write_form():
    """The documents counted 5 writers as *"3 originating + 2 re-materializing"*,
    and that composition is FALSE (`#9403` review, P2):

    `projection/entities._fold_entity_linked_reason` re-writes on REPLAY what the
    journal's `EntityLinked` record already carried — the documents' own
    definition of RE-MATERIALIZING, and the class they put
    `cache_projection.relation_statement` and `hosted_backup.restore_graph` in.
    The same PR's §13.8 q2 calls it *"reproduced by … the replay fold"* while
    §11.1 and §13 filed it under ORIGINATING, so the total (5) was right and its
    composition was wrong — two live documents disagreeing about the same site.

    The axes are separate, which is why the number read plausibly: the census
    can only see the WRITE FORM (named `SET r.<prop>` vs property MAP), and that
    split really is 3 + 4. The CAUSAL split is 2 originating + 5
    re-materializing. Nothing pinned either, so both readings stayed green.
    """
    storage = _doc(STORAGE)
    section_13 = _flat(
        _section(
            storage,
            "## 13. Filed from this document",
            "## 14. What this document does not decide",
        )
    )
    # The DESIGN doc may not state the wrong composition. The CHANGELOG is
    # deliberately exempt from the absence half: it is where the history is
    # RECORDED, and it quotes the old composition verbatim while saying it was
    # corrected — a substring ban there would forbid recording the fix, which is
    # the defect leg 6's own comment warns about ("the BARE word `property
    # free` is deliberately absent … so a word-level test would red on the note
    # that RECORDS the retraction"). The changelog is held to the POSITIVE half.
    assert _flat("3 originating + 2 re-materializing") not in section_13, (
        "STORAGE-ARCHITECTURE.md §13 files the replay fold as an ORIGINATING "
        "writer — `_fold_entity_linked_reason` re-writes on replay what the "
        "journal already carried, which is this document's own definition of "
        "RE-MATERIALIZING; the causal split is 2 + 5 (the #9417 supersede "
        "transfer is a fourth re-materializer, the #10139 pass-2b structural "
        "repoint a fifth)"
    )
    assert _flat("2 originating + 5 re-materializing") in _flat(
        _doc(STORAGE_CHANGELOG)
    ), (
        "STORAGE-ARCHITECTURE-CHANGELOG.md no longer states the CURRENT causal "
        "composition (2 originating + 5 re-materializing) — the ledger is where "
        "the count's current form is recorded, so a ledger whose newest entry "
        "stops at an older split is the history a reader mistakes for the claim. "
        "The `2 originating + 4 re-materializing` spelling stays in the `#9397` "
        "and `#9417` entries as HISTORY and is deliberately not removed; "
        "`#10139` added the fifth re-materializer"
    )
    # The correct split must be STATED, not merely absent: an absence-only pin
    # is satisfied by deleting the classification altogether.
    assert (
        _flat("2 ORIGINATING") in section_13
        and _flat("5 RE-MATERIALIZING") in section_13
    ), (
        "§13 no longer states the causal split (2 originating + 5 "
        "re-materializing) — the measured population's composition is a claim, "
        "and deleting it leaves the number a reader cannot check"
    )
    # And the write-FORM split is the axis the census actually reads, so it must
    # survive alongside it — a single number cannot carry both.
    assert _flat("originated, three sites") not in section_13, (
        "§13's one-line summary says `confidence` is *originated, three sites* "
        "— only TWO sites originate it; the third writes it in the named form "
        "but RE-MATERIALIZES it, which is what the same paragraph's split says "
        "(`#9403` review cycle 2, P2)"
    )
    assert "NAMED form" in section_13 and "property MAP" in section_13, (
        "§13 conflates the causal split with the write-form split — the census "
        "reads the FORM (named `SET r.<prop>` vs property MAP), and that is the "
        "axis its 3 + 2 belongs to"
    )
    # §11.1 must not file the fold as one of the writers that ORIGINATE the
    # property, and must say what it actually does.
    section_111 = _flat(
        _section(
            storage,
            "### 11.1 Why the connections are not the cost problem",
            "### 11.2 What actually costs money",
        )
    )
    assert "_fold_entity_linked_reason" in section_111, (
        "§11.1 no longer names the replay fold, so the third write site is "
        "silently dropped from the evidence base"
    )
    assert (
        _flat("RE-MATERIALIZES") in section_111
        or _flat("RE-MATERIALIZING") in section_111
    ), (
        "§11.1 lists `_fold_entity_linked_reason` without classifying it as a "
        "RE-MATERIALIZER — the pre-fix wording introduced it as one of 'the "
        "*originating* writers', which is the misclassification this leg pins"
    )


def test_the_correction_changelog_qualifies_its_line_citations():
    """The `#9397` entry's own rule is that a `file:line` citation is qualified
    when the number is not true at the document's declared pin — the rule it
    used to fix three `cache_projection` line citations (`#9403` review, P2).

    Three citations of the same class survived UNQUALIFIED in that same entry,
    and one of them is false at the pin both live documents declare
    (`c79ba1cf2`): bare `security.py:85` lands on `"INFORMED_BY", "PRODUCES"`
    there, the about allowlist being at `:77-78`, and `record_projection.py` and
    the `record_epistemic` migration do not exist at that commit at all — so
    those two numbers cannot be pinned to it either. This is the `edges.py:344`
    failure mode (a plausible-looking number that lands on something else), which
    is why the guard already bans it in the DESIGN docs.

    A citation is qualified when `at HEAD` / `at the pin` / `at the pinned
    revision` follows it within a short window.
    """
    entry = _doc(STORAGE_CHANGELOG)
    # Scoped to the paragraph that MAKES these citations (round 2), not to the
    # whole entry: the entry quotes historical citations verbatim while
    # narrating their repair, so a file-wide rule would red on its own record.
    paragraph = _section(
        entry,
        "**Second review-cycle correction",
        "**Third review-cycle correction",
    )
    # The rule must be able to MATCH the pre-fix spelling, or it protects nothing
    # (the `STALE_CITATION_PROBE` lesson): the `security.py` citation was bare.
    probe = "(`security.py:85`), on a relation row the projection builds"
    assert _unqualified_line_citations(probe) == ["`security.py:85`"], (
        "the citation rule no longer matches the pre-fix spelling "
        "`security.py:85`, so it cannot red on a re-introduction"
    )
    unqualified = _unqualified_line_citations(paragraph)
    assert not unqualified, (
        "the `#9397` changelog entry cites a line number with no revision "
        "qualifier:\n  "
        + "\n  ".join(unqualified)
        + "\n\nThe entry's own round-5 correction added `at HEAD` to three "
        "`cache_projection` line citations for exactly this reason: a bare "
        "number reads as current and lands somewhere else at the pin."
    )


def test_the_named_and_map_form_writer_populations_are_disjoint():
    #: A row in both dicts would mean one site is recorded twice, and the
    #: `MAP_FORM` sentinel would then be asserted against a named-form row (or
    #: the reverse) — the row would pass while checking nothing.
    both = sorted(set(RECORDED_WRITERS) & set(MAP_WRITERS))
    assert not both, (
        "these sites are recorded as BOTH a named-form and a map-form writer, "
        "so neither row pins what it claims:\n  "
        + "\n  ".join(f"{m}::{s}" for m, s in both)
    )
    assert len(MAP_WRITERS) >= 2, (
        "the map-form population shrank below the shipped sites the "
        "documents now record (7 writers the census sees: 3 named-form + 4 "
        "map-form, i.e. 2 originating + 5 re-materializing; no census hit is "
        "a declared false positive any more) — if a writer was "
        "genuinely removed, re-derive the documents' count with it"
    )
    #: A site cannot be both a real writer and a declared false positive.
    both_roles = sorted(set(NOT_AN_ABOUT_WRITER) & set(MAP_WRITERS))
    assert not both_roles, (
        "these sites are recorded as a map-form writer AND as a census false "
        "positive, so one of the two rows is lying:\n  "
        + "\n  ".join(f"{m}::{s}" for m, s in both_roles)
    )
    #: An exemption cannot also be a recorded writer, and it must have a
    #: STATEMENT premise pinned (see the leg below) — an exemption with no
    #: premise is a pin that can never discord.
    for key in NOT_AN_ABOUT_WRITER:
        assert key not in RECORDED_WRITERS and key not in MAP_WRITERS, key
    assert set(FALSE_POSITIVE_STATEMENTS) == set(NOT_AN_ABOUT_WRITER), (
        "a false-positive exemption has no pinned statement premise (or a "
        "premise names a site that is no longer exempted): "
        f"{sorted(set(FALSE_POSITIVE_STATEMENTS) ^ set(NOT_AN_ABOUT_WRITER))}"
    )
    #: Every recorded row must DECLARE its name set, or a new name on that site
    #: would slip past the subset test below.
    undeclared = sorted(
        f"{m}::{s}" for m, s in (
            *RECORDED_WRITERS, *MAP_WRITERS, *NOT_AN_ABOUT_WRITER
        )
        if (m, s) not in EXPECTED_NAMES
    )
    assert not undeclared, (
        "these recorded writers have no EXPECTED_NAMES row, so a second "
        "property name at that site would never be compared against the "
        "documents' claim:\n  " + "\n  ".join(undeclared)
    )


def test_the_declared_false_positive_premises_hold_at_the_statement():
    """`NOT_AN_ABOUT_WRITER` is an EXEMPTION, and an exemption nobody compares
    against is a fail-open cell.

    ⚠️ **The table is EMPTY since `#10139`, so the loop below is inert right
    now — stated rather than left for a reader to discover.** It is not a dead
    leg: the MATCHER PROBE inside `_setter_gate_premises` still runs on every
    suite and is what keeps the per-STATEMENT reading honest (proving it reds
    where a function-wide search stayed green), and a returning exemption
    re-activates the loop row-by-row with no edit here. What an empty table must
    never do is let the mechanism rot into a dict someone can add a row to
    without a pinned premise — which is why `FALSE_POSITIVE_STATEMENTS` must
    stay key-for-key with it (asserted in
    `test_the_named_and_map_form_writer_populations_are_disjoint`).

    The two rows this leg has actually compared, both of which LEFT the table by
    having their CODE fixed — the pattern the exemption exists to force:

      * `supersede_point` (`#9417`) — its `SET r += $attrs` is on the 2a-DIRECT
        leg (`rtype` is `type(r)` of the `IMPL|NAND` rows) while its about edge
        was transferred by a BARE `MERGE`, so on an about edge it wrote nothing
        and DROPPED `confidence`;
      * `_rebuild_all_replay` (`#10139`) — it carries TWO `SET r += $attrs`
        statements. The `DirectEdgeCreated` fold is protected by the FIXED key
        vocabulary (TWO producers since `#9553`: `create_direct_edge`, restricted
        to `{IMPL, NAND}`, and `_record_structural_edge`, reached by the edge
        mints, which CAN journal an `about*` predicate and emits no attribute
        from the map's vocabulary — see
        `test_every_direct_edge_created_producer_is_safe_for_an_about_edge`). The
        structural-`DirectEdgeRepoint` fold's interpolated `etype` IS allowlisted
        (`validate_rel_type`), its about/structural transfer WAS a bare `MERGE` —
        and that is exactly the premise `#10139` falsified: the descriptor now
        carries the attributes and the fold re-applies them, which is why the row
        is a counted writer and no longer an exemption.

    Each premise is asserted at the STATEMENT, which is where the property would
    land: a function-wide membership test is satisfied by a literal elsewhere in
    the body while a `SET` sits on the about transfer, and then the sentence
    claiming otherwise stays green while being false.

    ⚠️ **The repoint fold's protection was NOT the producer restriction** — that
    covers the creation fold only. It was the gate over a map built from a FIXED
    key vocabulary, and `_setter_gate_premises` therefore walks EACH statement to
    its OWN enclosing `if` and to the assignment building its map — a
    function-wide `if attrs:` search was satisfied by the creation fold's gate
    and said nothing about the repoint fold (`#9403` review, P3). That method is
    what made this defect visible: the gate was real, the map was empty BECAUSE
    nobody emitted the keys, and an empty map was the bug `#10139` fixes.
    """
    for key, (set_needle, transfer_needle) in FALSE_POSITIVE_STATEMENTS.items():
        module, symbol = key
        body = _function_body(module, symbol)
        transfer = _query_call_containing(body, transfer_needle)
        assert transfer, (
            f"`{module}::{symbol}` no longer transfers the about edge with a "
            f"bare `{transfer_needle}` — re-derive NOT_AN_ABOUT_WRITER "
            f"({NOT_AN_ABOUT_WRITER[key]}) and the documents' claim that it "
            "writes no property on an about edge"
        )
        assert "SET" not in transfer, (
            f"the about-edge transfer in `{module}::{symbol}` now writes a "
            f"property ({transfer.strip()!r}), so that row is NOT a census "
            "false positive: it carries that name on an `about*` edge, and the "
            "documents' 'it carries no property on an about edge' is now "
            "false. Re-derive the property set, move the row out of "
            "NOT_AN_ABOUT_WRITER, and give it an EXPECTED_NAMES row naming it."
        )
        # The `SET` the census DOES see must land on a statement whose
        # relationship patterns are all non-about — that asymmetry is the whole
        # exemption. EVERY matching statement is checked, not the first
        # (`#9403` review cycle 2, P1): `_rebuild_all_replay` carries TWO
        # `SET r += $attrs` statements and the first-match version inspected
        # only one, leaving the second — the structural-repoint fold — as a
        # fail-open cell in the leg that exists to close one.
        setters = _query_calls_containing(body, set_needle)
        assert setters, (
            f"`{module}::{symbol}` no longer carries the `{set_needle}` the "
            "census row was derived from — the exemption now describes code "
            "that moved"
        )
        for setter in setters:
            assert _ABOUT_REL.search(setter) is None, (
                f"a `{set_needle}` statement in `{module}::{symbol}` now names "
                f"an `about*` predicate ({setter.strip()!r}) — the property "
                "lands on an about edge, so the exemption is stale"
            )
        # The exemption's SECOND premise, asserted PER STATEMENT: the write is
        # gated, and the map it writes is built from a FIXED literal key
        # vocabulary — so a descriptor carrying none of those keys yields an
        # empty map and writes nothing on the edge its interpolated `etype`
        # names. Bound to each statement, not to the function body (`#9403`
        # review, P3): `_rebuild_all_replay`'s repoint fold can reach an
        # allowlisted `aboutObject` `etype`, and a function-wide gate search
        # said nothing about it.
        gates = _setter_gate_premises(
            module, (ROOT / module).read_text(encoding="utf-8"), symbol, set_needle
        )
        assert len(gates) == len(setters), (
            f"`{module}::{symbol}` carries {len(setters)} `{set_needle}` "
            f"statement(s) but {len(gates)} gate premise(s) — the two readings "
            "of the same function disagree, so this leg measures neither"
        )
        for gate_test, vocabulary in gates:
            assert gate_test, (
                f"a `{set_needle}` statement in `{module}::{symbol}` is gated "
                "by an `if` with no readable test — the gate IS the premise"
            )
            assert set(vocabulary) <= FALSE_POSITIVE_ATTR_VOCAB, (
                f"a `{set_needle}` statement in `{module}::{symbol}` copies a "
                f"key outside the fixed attribute vocabulary the exemption "
                f"rests on (gate {gate_test!r}, keys {sorted(vocabulary)}; the "
                f"fixed set is {sorted(FALSE_POSITIVE_ATTR_VOCAB)}) — a "
                "structural descriptor carrying one of those new keys would "
                "write it on the edge its interpolated `etype` names"
            )

    # ── the probe: the per-statement rule reds where a function-wide one did not.
    # A synthetic two-fold function — `_rebuild_all_replay`'s shape — in which
    # only the FIRST fold is gated. The previous revision of this leg searched
    # the whole body for `if attrs:`, which fold one satisfies, so deleting fold
    # two's gate left every leg green (measured).
    both_folds = (
        "def _folds(proj, events, direct_events):\n"
        "    for ev in events:\n"
        "        attrs = {k: ev[k] for k in ('confidence', 'weight') if k in ev}\n"
        "        if attrs:\n"
        "            proj.g.query('MERGE (a)-[r:T]->(b) SET r += $attrs',\n"
        "                         params={'attrs': attrs})\n"
        "    for ev in direct_events:\n"
        "        attrs = {k: ev[k] for k in ('confidence', 'weight') if k in ev}\n"
        "        proj.g.query('MERGE (a)-[r:T]->(b) SET r += $attrs',\n"
        "                     params={'attrs': attrs})\n"
    )
    assert re.search(r"if attrs:", both_folds), (
        "the probe's own premise is gone — it must carry a function-WIDE "
        "`if attrs:` that a body-scoped search would accept"
    )
    with pytest.raises(AssertionError, match="no longer gated"):
        _setter_gate_premises(
            "tortoise/x.py", both_folds, "_folds", "SET r += $attrs"
        )
    # A gate that can never be false is not a gate — the shape a lane reaches
    # for when a query "needs" to run unconditionally without deleting the `if`.
    constant_gate = (
        "def _folds(proj, events):\n"
        "    for ev in events:\n"
        "        attrs = {k: ev[k] for k in ('confidence', 'weight') if k in ev}\n"
        "        if True:\n"
        "            proj.g.query('MERGE (a)-[r:T]->(b) SET r += $attrs',\n"
        "                         params={'attrs': attrs})\n"
    )
    with pytest.raises(AssertionError, match="is not a gate"):
        _setter_gate_premises(
            "tortoise/x.py", constant_gate, "_folds", "SET r += $attrs"
        )
    # …and with BOTH folds gated the same source passes, so the probe pins the
    # RULE rather than a hard-coded failure (a pin that only ever reds proves
    # nothing about what it accepts).
    gated_both = (
        "def _folds(proj, events, direct_events):\n"
        "    for ev in events:\n"
        "        attrs = {k: ev[k] for k in ('confidence', 'weight') if k in ev}\n"
        "        if attrs:\n"
        "            proj.g.query('MERGE (a)-[r:T]->(b) SET r += $attrs',\n"
        "                         params={'attrs': attrs})\n"
        "    for ev in direct_events:\n"
        "        attrs = {k: ev[k] for k in ('confidence', 'weight') if k in ev}\n"
        "        if attrs:\n"
        "            proj.g.query('MERGE (a)-[r:T]->(b) SET r += $attrs',\n"
        "                         params={'attrs': attrs})\n"
    )
    assert both_folds.count("if attrs:") == 1 and gated_both.count("if attrs:") == 2, (
        "the probe no longer differs from its control by exactly one gate"
    )
    assert len(
        _setter_gate_premises(
            "tortoise/x.py", gated_both, "_folds", "SET r += $attrs"
        )
    ) == 2, "the probe did not report one premise per statement"

    # ── and the DOCUMENTS must carry the same two-fold story, because they are
    # the surface a reader trusts (`#9403` review, P3). §13 said the replay's
    # `SET r += $attrs` "belongs to the `DirectEdgeCreated` fold, whose only
    # producer is `{IMPL, NAND}`-only" and §13.8 q2 said "each of whose … is on
    # an `IMPL|NAND` leg" — true of the creation fold and the DIRECT function,
    # FALSE of the repoint fold, whose allowlisted `etype` can be `aboutObject`.
    # Neither document named `DirectEdgeRepoint` at all, so the fold `#9417`
    # would change was the one the docs did not describe.
    docs = {"STORAGE": _flat(_doc(STORAGE)), "EXTRACTOR": _flat(_doc(EXTRACTOR))}
    doc_sections = {}
    for label, start, end, which in CORRECTED_SECTIONS:
        if label.endswith("§11.1"):
            continue
        doc_sections[label] = (which, _flat(_section(_doc(STORAGE if which == "STORAGE" else EXTRACTOR), start, end)))
    for label, (_which, section) in doc_sections.items():
        assert "DirectEdgeRepoint" in section, (
            f"{label} no longer names the `DirectEdgeRepoint` fold. The replay "
            "carries TWO `SET r += $attrs` statements and only the creation "
            "fold is producer-restricted to `{IMPL, NAND}`; the repoint fold's "
            "`etype` is allowlisted and can be `aboutObject`, so an unnamed "
            "fold is a claim the exemption's protection does not cover"
        )
        assert "fixed attribute vocabulary" in section, (
            f"{label} no longer states the mechanism that carries the repoint "
            "fold — the map is built from a fixed attribute vocabulary that "
            "structural repoint descriptors never populate, so it is empty and "
            "nothing is written. The producer restriction alone does not "
            "reach that statement"
        )
        assert "each of whose" not in section, (
            f"{label} is back to the single-fold claim (`each of whose `SET r "
            "+= $attrs` is on an `IMPL|NAND` leg`) — the retracted absolute "
            "this round removed"
        )
    assert "DirectEdgeRepoint" in docs["STORAGE"], "the storage doc lost the fold name"
    assert "DirectEdgeRepoint" in docs["EXTRACTOR"], (
        "the extractor doc lost the fold name"
    )


# ── 4. no unrecorded property-bearing writer ───────────────────────────────


def test_no_unrecorded_property_bearing_about_edge_writer():
    writers = _about_edge_property_writers()
    assert writers, (
        "the census found no property-bearing about-edge writer at all — the "
        "relationship-variable/SET rule or the about* filter was changed, and "
        "this guard has stopped measuring anything"
    )
    #: Both forms must be present in the population, or the census has silently
    #: narrowed to one of them (how the first version of this file missed four
    #: shipped writers).
    assert any(PROPERTY in props for props in writers.values()), (
        "the census sees no NAMED property write (`SET r.<prop>`) at all"
    )
    assert any(MAP_FORM in props for props in writers.values()), (
        "the census sees no property-MAP write (`SET r += $props` / "
        "`SET r = $p`) at all — `_SET_MAP` or the widened `_ABOUT_VOCAB` was "
        "changed, and the map-form half of the population is unmeasured again"
    )
    #: The THIRD form. `cache_projection.relation_statement` stamps `idx` in an
    #: inline pattern map; if the census stops reading it, this population goes
    #: back to claiming an about edge carries one name — the version of this
    #: guard that missed four shipped writers, one form further along.
    assert writers[("tortoise/cache_projection.py", "relation_statement")] >= {
        SECOND_PROPERTY,
        MAP_FORM,
    }, (
        "the census no longer reports `idx` (the INLINE pattern-map form) on "
        "the record-layer projection — `MERGE (src)-[r:{rel.rel_type} "
        "{idx:$idx}]->(tgt) SET r += $props` — stamped by "
        "`cache_projection.relation_statement` — writes a SECOND property name "
        "on an allowlisted `aboutObject` edge, "
        "which is why the documents state `confidence` + `idx`"
    )
    assert any(SECOND_PROPERTY in props for props in writers.values()), (
        "no writer in the population is seen writing `idx` — the inline-map "
        "rule has stopped measuring anything"
    )
    #: NAMES, not just keys. A subset test on the writer key alone stays green
    #: when a SECOND property is added at an already-recorded site — which is
    #: exactly how "the measured set is `confidence` plus `idx`" would rot. The
    #: declared FALSE POSITIVE is in this comparison too (it has an
    #: `EXPECTED_NAMES` row), because an exemption the name comparison skips is
    #: a fail-open cell: a property added on that about edge would red nothing
    #: while the documents assert the edge carries none.
    unexpected = sorted(
        f"{module}::{symbol} SETs {sorted(props - EXPECTED_NAMES[(module, symbol)])}"
        for (module, symbol), props in writers.items()
        if (module, symbol) in EXPECTED_NAMES
        and props - EXPECTED_NAMES[(module, symbol)]
    )
    assert not unexpected, (
        "a recorded about-edge writer now writes a name the documents do not "
        "record:\n  " + "\n  ".join(unexpected)
        + "\nRe-derive the property set (STORAGE-ARCHITECTURE.md §11.1/§13) "
        "and extend EXPECTED_NAMES; an unrecorded name invalidates the "
        "ruling's cost-only bound. If the site is NOT_AN_ABOUT_WRITER, a name "
        "at it means the false positive has become a real writer — move it out "
        "and re-derive the documents' 'it drops `confidence`' claim."
    )
    unrecorded = sorted(
        f"{module}::{symbol} SETs {sorted(props)}"
        for (module, symbol), props in writers.items()
        if (module, symbol) not in RECORDED_WRITERS
        and (module, symbol) not in MAP_WRITERS
        and (module, symbol) not in NOT_AN_ABOUT_WRITER
    )
    assert not unrecorded, (
        "these functions put a property on an `about*` edge and are recorded "
        "nowhere:\n  " + "\n  ".join(unrecorded)
        + "\nRecord each one in the documents' corrected evidence (and in "
        "RECORDED_WRITERS/MAP_WRITERS here), because the ruling's *cost-only* "
        "argument depends on the property set being known and bounded."
    )
    #: The exemptions must still be SEEN. A rename (or a moved write) drops the
    #: row from the census, and an exemption recorded for code nobody can find
    #: is a pin that protects nothing — the rule this file applies to RETRACTED.
    vanished = sorted(
        f"{module}::{symbol} (declared a FALSE POSITIVE) is no longer in the "
        "census at all"
        for module, symbol in NOT_AN_ABOUT_WRITER
        if (module, symbol) not in writers
    )
    assert not vanished, (
        "a declared census false positive has VANISHED from the census, so the "
        "documents' claim that it writes no property on an about edge is no "
        "longer checked against anything:\n  " + "\n  ".join(vanished)
        + "\nRe-derive the row (and the documents) rather than leaving it dead."
    )


# ── 4b. the write forms and spellings the census was fail-open on (synthetic
#        probes) ───────────────────────────────────────────────────────────


def test_the_census_reads_the_inline_pattern_map_form():
    """A property stamped INSIDE the pattern (`-[r:aboutObject {c:$v}]->`) is a
    write; a census that only reads `SET` clauses reports it as a measured
    absence — the blindness this file exists to stop, one form along. Probed on
    a synthetic module so the rule is proved live without a shipped writer's
    cooperation, and so a regression reds here rather than in production."""
    probe = (
        "def link(g, s, t, c):\n"
        "    g.query('MERGE (s)-[r:aboutObject {confidence:$c}]->(t)', "
        "params={'c': c})\n"
    )
    found = _writers_in_module("tortoise/probe.py", probe)
    assert found == {("tortoise/probe.py", "link"): {"confidence"}}, (
        "the census does not read the INLINE pattern-map form — a property "
        f"written there is invisible again (found {found!r})"
    )
    # The same form with NO relationship variable. `(\w+)` in `_PATTERN` made
    # this writer invisible to every rule, which is how a census reports a
    # measured absence for a write that shipped.
    anonymous = (
        "def link(g, s, t, c):\n"
        "    g.query('MERGE (s)-[:aboutObject {confidence:$c}]->(t)', "
        "params={'c': c})\n"
    )
    found = _writers_in_module("tortoise/probe.py", anonymous)
    assert found == {("tortoise/probe.py", "link"): {"confidence"}}, (
        "the census does not read an ANONYMOUS relationship pattern with an "
        "inline property map (`-[:aboutObject {confidence:$c}]->`) — the "
        f"writer is invisible (found {found!r})"
    )


def test_the_census_bound_is_pinned_as_a_gap_not_an_absence():
    """The census's BOUND, measured on the shapes it structurally cannot read. The module docstring states the bound as a gap; these probes keep it
    from decaying into the absolute it replaces (*"no writer that spells an
    about vocabulary can add a property unseen"* was false on the tree and is
    now false on each synthetic writer below).

    Pinning an EMPTY result is deliberate: the census is not being asked to
    catch these, it is being asked not to CLAIM them. A shipped instance of
    shape 1 is `session_source_migration._move_in` (predicate handed in from
    `MOVABLE_SOURCE_RELS`, which contains `aboutSource`/`aboutDocument`).
    """
    # Gap 2: an UNTYPED binding whose predicate is tested in a WHERE clause.
    # `_PATTERN` needs a typed relationship pattern, so the function is
    # continued before any rule runs. This is already live style in the tree
    # (`WHERE type(r) = 'NAND'` in sdk.py / analyze.py).
    untyped = (
        "def link(g, s, t, c):\n"
        "    g.query(\"MATCH (s)-[r]->(t) WHERE type(r) = 'aboutObject' "
        "SET r.confidence = $c\", params={'c': c})\n"
    )
    found = _writers_in_module("tortoise/probe.py", untyped)
    assert found == {}, (
        "the census now reads an UNTYPED `WHERE type(r) = 'about…'` binding — "
        "if that is a real widening, the documented bound and the sixth-site "
        "disclosure must be updated in the same change (found "
        f"{found!r})"
    )
    # Gap 3: the predicate IS spelled in a typed pattern, but the write is an
    # fn-call RHS that `_SET_MAP` (`$|{`) cannot read. The parameter is
    # therefore NOT what makes `_move_in` invisible.
    fn_call_rhs = (
        "def link(g, s, t):\n"
        "    g.query('MATCH (s:Point)-[r:aboutObject]->(t) SET r = "
        "properties(s)')\n"
    )
    found = _writers_in_module("tortoise/probe.py", fn_call_rhs)
    assert found == {}, (
        "the census now reads an fn-call RHS (`SET r = properties(x)`) on a "
        "typed about pattern — if that is a real widening, the documented "
        "bound must be updated in the same change (found "
        f"{found!r})"
    )
    # Control, so the EMPTIES above are those shapes and not a broken
    # probe: the same writer with a readable RHS IS recorded.
    control = (
        "def link(g, s, t, p):\n"
        "    g.query('MATCH (s:Point)-[r:aboutObject]->(t) SET r = $p', "
        "params={'p': p})\n"
    )
    found = _writers_in_module("tortoise/probe.py", control)
    assert found == {("tortoise/probe.py", "link"): {"<map:$props>"}}, (
        "the control probe (a readable property-MAP write on a typed about "
        f"pattern) is no longer recorded — the census changed (found {found!r})"
    )
    # Gap 1: the predicate is a PARAMETER (the `_move_in` shape) — the
    # vocabulary is in the caller, so nothing in the body names it.
    parameterised = (
        "def move(g, rel, s, t):\n"
        "    g.query(f'MERGE (s)-[r2:`{rel}`]->(t) ON CREATE SET "
        "r2 = properties(s)')\n"
    )
    found = _writers_in_module("tortoise/probe.py", parameterised)
    assert found == {}, (
        "the census now reads a PARAMETERISED predicate — a real widening; "
        "update the documented gap list in the same change (found "
        f"{found!r})"
    )

    # Gaps 4 and 5, whose probes did not exist when the prose first claimed
    # they were "measured with a control recorded" — an unbacked provenance
    # claim, so the probes are here now.
    # Gap 4: spellings `_PATTERN` does not recognise. Each is a typed about
    # pattern with a readable write, i.e. exactly what the census claims to
    # cover, and each is invisible.
    spellings = {
        "space before the type colon": (
            "def link(g, s, t, c):\n"
            "    g.query('MATCH (s:Point)-[r :aboutObject]->(t) "
            "SET r.confidence = $c', params={'c': c})\n"
        ),
        "space after the opening bracket": (
            "def link(g, s, t, c):\n"
            "    g.query('MATCH (s:Point)-[ r:aboutObject]->(t) "
            "SET r.confidence = $c', params={'c': c})\n"
        ),
        "a `]` inside the inline map": (
            "def link(g, s, t, c):\n"
            "    g.query('MERGE (s)-[r:aboutObject {tags:[\\'a\\'],"
            "confidence:$c}]->(t)', params={'c': c})\n"
        ),
    }
    for why, probe in spellings.items():
        found = _writers_in_module("tortoise/probe.py", probe)
        assert found == {}, (
            f"the census now reads this spelling ({why}) — a real widening; "
            f"the gap list must be updated in the same change (found {found!r})"
        )
    # Gap 5: an about-NAMED predicate outside `_ABOUT_REL`'s enumerated seven.
    off_list = (
        "def link(g, s, t, c):\n"
        "    g.query('MATCH (s)-[r:aboutClaim]->(t) SET r.confidence = $c', "
        "params={'c': c})\n"
    )
    found = _writers_in_module("tortoise/probe.py", off_list)
    assert found == {}, (
        "the census now reads an `aboutClaim` edge — `_ABOUT_REL` widened, so "
        f"its comment and the gap list must say so (found {found!r})"
    )


#: The sentence that states the census's epistemic status. The bound is stated
#: as a DETECTOR rather than as a completeness claim, because every
#: completeness claim written in this file was falsified by the next reviewer's
#: measurement. The pin below exists so that drift is a RED, not a re-review:
#: measured without it, deleting the bound paragraphs or inserting a fresh
#: absolute left the suite green.
_BOUND_MARKER = "DETECTOR, not a PROOF of absence"

#: Absolutes this file used to assert and must never assert again.
_RETRACTED_BOUND_ABSOLUTES = (
    "all three are in scope",
    "no writer that SPELLS an about vocabulary",
)


def test_the_documents_state_the_bound_as_a_detector_not_a_proof():
    """The DOCUMENTS carry the non-exhaustive wording, and none of the
    retracted absolutes comes back.

    This leg exists because the probe test above pins the CENSUS and, on its
    own, pinned nothing about the prose: measured, deleting the bound
    paragraphs from §11.1/§13, or inserting a new absolute, left all 16 legs
    green — so the changelog's claim that the probes keep the bound from
    decaying was itself false.
    """
    assert _BOUND_MARKER in (__doc__ or ""), (
        "this module's docstring no longer states the census as a DETECTOR "
        "rather than a completeness claim"
    )
    storage = (ROOT / "docs/architecture/STORAGE-ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    # SECTION-scoped, not a file-wide count: a file-wide `count() >= 2` stays
    # green when §11.1's copy is deleted and §13 keeps two — the scoping defect
    # this file's own `_section` docstring names (`#8639`).
    for label, start, end, which in CORRECTED_SECTIONS:
        if which != "STORAGE" or label.endswith("§13.8 q2"):
            continue
        section = _flat(_section(storage, start, end))
        assert _BOUND_MARKER in section, (
            f"{label} no longer states the census as a {_BOUND_MARKER!r} — a "
            "file-wide check would stay green while this section drifted back "
            "to a completeness claim"
        )
    flat = _flat(storage)
    for absolute in _RETRACTED_BOUND_ABSOLUTES:
        assert absolute not in flat, (
            f"a retracted completeness claim is back in "
            f"`docs/architecture/STORAGE-ARCHITECTURE.md`: {absolute!r}"
        )


def test_the_census_reads_a_writer_that_never_says_about():
    """The module prefilter was the literal word "about" — which is exactly what
    `_ABOUT_VOCAB`'s justification says an allowlist-driven map writer does NOT
    contain, so the widened vocabulary never reached those files. The probe
    names no about predicate at all: it takes it from `validate_rel_type`, the
    allowlist that contains every `about*` type (`security.py:85`)."""
    probe = (
        "from tortoise.security import validate_rel_type\n"
        "def project_edge(g, rel_type, props):\n"
        "    validate_rel_type(rel_type)\n"
        "    g.query(f'MERGE (s)-[r:{rel_type}]->(t) SET r += $props', "
        "params={'props': props})\n"
    )
    assert "about" not in probe
    assert _module_in_scope(probe), (
        "the module prefilter still requires the literal word 'about', so the "
        "allowlist-driven map writer is skipped by the FILE WALK even though "
        "the census rule would see it"
    )
    assert not _module_in_scope("x = 1\n"), (
        "the prefilter admits every module — it no longer narrows anything, so "
        "an unrelated module's writes would count as about-edge writers"
    )
    found = _writers_in_module("tortoise/probe.py", probe)
    assert found == {("tortoise/probe.py", "project_edge"): {MAP_FORM}}, (
        "the census does not read a map-form writer whose predicate comes from "
        f"the allowlist instead of the word 'about' (found {found!r})"
    )


def test_the_census_fails_closed_on_a_module_it_cannot_parse():
    """A module `ast.parse` rejects used to be DROPPED from the census with no
    signal (`except SyntaxError: return found`), so every writer it contains was
    reported as a measured absence — mechanically the same defect as the
    `rg 'aboutObject {'` zero this PR exists to correct, one level down (third
    review cycle). The promise's stated bound was a list of VOCABULARY NAMES, so
    a reader was told the only gap was a token nobody had written yet; a parse
    failure is a second, machine-detectable gap and it must stop the census.

    Pinned on a synthetic module: no shipped file has to be broken for the rule
    to be proved live."""
    broken = (
        "def link(g, s, t):\n"
        "    g.query('MERGE (s)-[r:aboutObject {confidence:$c}]->(t)'\n"
    )
    try:
        found = _writers_in_module("tortoise/probe.py", broken)
    except UnparsableModule as exc:
        assert "tortoise/probe.py" in str(exc), (
            "the census fails closed but does not name the module it could not "
            f"read, so the reader cannot tell which file is unmeasured: {exc}"
        )
    else:
        raise AssertionError(
            f"the census returned {found!r} for a module it cannot parse — an "
            "unreadable module is censused as 'writes no property on any about "
            "edge', which is a measured absence manufactured from a file nobody "
            "read. Raise UnparsableModule and name the module."
        )


# ── 5. the reach claim the corrected text makes ────────────────────────────


def test_the_default_ingestion_path_reaches_the_binder():
    sdk = (PKG / "sdk.py").read_text(encoding="utf-8")
    extract = sdk[sdk.index("def _extract_session_v2"):]
    extract = extract[: extract.index("\n    def ", 10)]
    assert "bind_point_subjects(" in extract, (
        "`_extract_session_v2` no longer calls `bind_point_subjects` — the "
        "corrected evidence's reach claim ('default-ON, ingestion path') "
        "depends on this call"
    )
    # The DEFAULT-ON half is not in `_extract_session_v2` at all: it is the
    # extractor selection. `m2` is the opt-out and v2 is the fall-through, so
    # flipping the default would falsify the corrected sentence while the call
    # above stayed in place — an unpinned half of a claim this leg advertises.
    tree = ast.parse(sdk)
    selected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "TORTOISE_SESSION_EXTRACTOR" in ast.unparse(node.test)
        and "m2" in ast.unparse(node.test)
    ]
    assert selected, (
        "the `TORTOISE_SESSION_EXTRACTOR == 'm2'` selection is gone from "
        "`sdk.py` — re-derive the corrected reach claim before deleting the "
        "opt-out it describes"
    )
    node = selected[0]
    assert "_extract_session_llm(" in ast.unparse(node.body), (
        "the `m2` branch no longer selects `_extract_session_llm`, so 'm2 is "
        "the opt-out' is stale"
    )
    assert "_extract_session_v2(" in ast.unparse(node.orelse), (
        "`_extract_session_v2` is no longer the FALL-THROUGH extractor — the "
        "corrected evidence's 'default-ON, ingestion path' sentence is false"
    )


# ── 7. no dead retracted pin ──────────────────────────────────────────────


def test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live():
    #: A literal that matches nothing can never red. Every entry must therefore be
    #: either findable in the changelog that quotes it, or declared pre-fix-only.
    assert set(RETRACTED) == (
        set(RETRACTED_QUOTED_IN)
        | set(RETRACTED_PRE_FIX_ONLY)
        | set(RETRACTED_INTRODUCED_HERE)
    ), (
        "a RETRACTED entry is classified nowhere: it is neither quoted in a "
        "changelog (RETRACTED_QUOTED_IN), declared pre-fix-only "
        "(RETRACTED_PRE_FIX_ONLY), nor declared introduced-and-retracted "
        "inside this PR (RETRACTED_INTRODUCED_HERE) — so nothing proves it "
        "can ever match text"
    )
    dead = [
        f"{label}: {RETRACTED[label][0]!r} is not in {path.name}"
        for label, path in sorted(RETRACTED_QUOTED_IN.items())
        if _flat(RETRACTED[label][0]) not in _flat(_doc(path))
    ]
    assert not dead, (
        "these retracted sentences are pinned but findable nowhere — a pin "
        "that cannot match the pre-fix text cannot red on a re-rot:\n  "
        + "\n  ".join(dead)
    )


def test_every_retracted_literal_declares_its_true_provenance():
    """The provenance SPLIT itself, measured against the pre-fix revision.

    Leg 7 proves every retracted literal can be found SOMEWHERE. It cannot
    prove the entry's provenance is right, because it accepts the same changelog
    for a pre-fix-live literal and for one this PR wrote and withdrew — so a
    misclassified entry produced a leg-1 failure message claiming a pre-fix
    presence that never existed (`§11.1`'s method paragraph, measured absent at
    `01b5486ec`). That is the guard's own subject, one level up: a label that
    nothing compares against the text it describes.
    """
    pre_fix: dict[str, str] = {}
    for home in {home for _sentence, home in RETRACTED.values()}:
        text = _git_show(PRE_FIX_REV, f"docs/architecture/{home}")
        if text is None:
            pytest.skip(
                f"`git show {PRE_FIX_REV}:docs/architecture/{home}` is not "
                "reachable even after a bounded fetch of `origin main` "
                f"(depths {_SHALLOW_DEPTHS}) — no git, an unreachable "
                "remote, or the rev is older than the deepest bound — so the "
                "pre-fix provenance cannot be measured here. This leg only "
                "SKIPS, it never passes on a history it could not read; the "
                "gap is that this checkout could not supply the history, not "
                "that the provenance claim is verified"
            )
        pre_fix[home] = _flat(text)
    wrong: list[str] = []
    for label, (sentence, home) in RETRACTED.items():
        present = _flat(sentence) in pre_fix[home]
        declared_introduced = label in RETRACTED_INTRODUCED_HERE
        if present == declared_introduced:
            kind = (
                "introduced-and-retracted-inside-this-PR"
                if declared_introduced
                else "live-pre-fix"
            )
            state = "PRESENT" if present else "ABSENT"
            wrong.append(
                f"{label}: declared {kind} but the literal is {state} at "
                f"{PRE_FIX_REV}:docs/architecture/{home}"
            )
    assert not wrong, (
        "these retracted literals declare a provenance the pre-fix tree "
        "disproves — leg 1's failure message would then assert a history that "
        "never happened:\n  " + "\n  ".join(wrong)
        + "\nAn entry belongs in RETRACTED_INTRODUCED_HERE iff it is ABSENT at "
        f"{PRE_FIX_REV} (this PR wrote it and withdrew it); otherwise its "
        "'was live pre-fix' claim stands."
    )


# ── 6. the changelogs must carry the correction, not the old claim ─────────


def test_the_storage_changelog_records_the_correction():
    changelog = _doc(STORAGE_CHANGELOG)
    assert "#9397" in changelog, (
        "STORAGE-ARCHITECTURE-CHANGELOG.md does not record this correction — "
        "the changelog is where a reader learns a §11.1/§13 claim moved, and an "
        "unrecorded correction is the drift #9397 exists to stop"
    )


# ── 8. this file's description of ITSELF ──────────────────────────────────
#: The retracted self-description: the header claimed the suite made no network
#: call while `_ensure_commit` fetches `origin main`. Pinned by its own literal,
#: because a claim about what the suite DOES is exactly the class of claim this
#: file was written to stop asserting unmeasured — one level up.
_RETRACTED_NO_NETWORK = "No DB, no graph, no network."


def test_the_guard_states_its_own_execution_and_probe_coverage_honestly():
    """The header's claims about the suite's OWN behaviour are measured here.

    Two of them were false at the `#9403` head, and both were false in the same
    direction — understating what runs and overstating what is pinned:

    * *"ONE exception: `git show` … No DB, no graph, no network"* — the leg also
      runs `git cat-file -e`, and `_ensure_commit` runs `git fetch --depth=…
      origin main`, which on the shallow CI checkout is a network call whose
      side effect is to DEEPEN the clone under test.
    * *"pins one synthetic probe per gap below (with a recorded control)"* — the
      gap list has six entries, the probes cover five, and there is one control.

    Each assertion below re-derives the fact from the SOURCE rather than
    restating it: the docstring may only claim what `_ensure_commit` and
    `test_the_census_bound_is_pinned_as_a_gap_not_an_absence` actually do.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    header = _flat(__doc__ or "")

    # The code facts. If these vanish the claims below are being checked
    # against nothing, which is the failure mode this leg exists to catch.
    assert '"fetch"' in src and "--depth=" in src, (
        "`_ensure_commit` no longer fetches `origin main` — this leg's premise "
        "moved, so re-derive the header's execution claim before trusting it"
    )
    assert '"cat-file"' in src, (
        "`_has_commit` no longer shells out to `git cat-file` — the header's "
        "execution claim is stale"
    )

    assert _flat(_RETRACTED_NO_NETWORK) not in header, (
        f"the header still claims {_RETRACTED_NO_NETWORK!r} while "
        "`_ensure_commit` runs `git fetch --depth=… origin main` (a network "
        "call on a shallow checkout, and a side effect on this clone's object "
        "database)"
    )
    assert "git fetch" in header, (
        "the header does not name the `git fetch` that the provenance leg "
        "performs when the pre-fix rev is not already present — the suite is "
        "then not network-free, and a reader of a 'static' suite deserves to "
        "know before a network-less runner spends the fetch timeouts"
    )

    # Probe coverage: one probe per gap is NOT what the census leg holds.
    assert "one synthetic probe per gap" not in header, (
        "the header claims one synthetic probe per gap, but the gap list has "
        "SIX entries and the probes cover five (gap 6 is the file-scope gap, "
        "which no `_writers_in_module` probe can express)"
    )
    # The header claims probes "for gaps 1-5", and until this pin existed nothing
    # compared that claim to the probes: measured at `#9403` review, deleting the
    # gap-4 `spellings` table AND the gap-5 `aboutClaim` probe left every leg green
    # while the header still promised them. Pinned by ANCHOR, not by count — the
    # anchors are the comment that introduces each probe block plus the wildcard
    # that decides what counts as about-bearing, so a deletion takes its anchor too.
    # ⛔ Slice out THIS leg first: the anchor strings below appear verbatim in this very
    # assertion list, so a file-wide search matches its own text and the pin is vacuous
    # (the self-match this file records twice already). Measured: with the whole file
    # searched, deleting the gap-5 probe left this leg green.
    own_leg = src.find("def test_the_guard_states_its_own_execution_and_probe_coverage_honestly")
    assert own_leg > 0, "this pin cannot locate its own definition, so it cannot slice it out"
    outside = src[:own_leg]
    for gap_anchor in (
        "# Gap 1: the predicate is a PARAMETER",
        "# Gap 2: an UNTYPED binding",
        "# Gap 3: the predicate IS spelled in a typed pattern",
        "# Gap 4: spellings `_PATTERN` does not recognise",
        "# Gap 5: an about-NAMED predicate outside `_ABOUT_REL`",
        "MATCH (s)-[r:aboutClaim]->(t)",
        "a recorded control",
    ):
        assert gap_anchor in outside, (
            f"the header promises probes for gaps 1-5 with a recorded control, but this "
            f"probe anchor is gone: {gap_anchor!r}. Delete the claim or keep the probe."
        )

    # The gap-4 anchor above is the block's COMMENT, so it survives a per-entry
    # deletion: measured at `#9403` review, dropping the "space after the opening
    # bracket" spelling left every leg green while this header still said each
    # spelling was measured. So the needles are pinned by CONTENT too.
    for spelling_needle in (
        "-[r :aboutObject]->",   # whitespace before the type colon
        "-[ r:aboutObject]->",   # whitespace after the opening bracket
        "{tags:[",               # a `]` inside the inline property map
    ):
        assert spelling_needle in outside, (
            f"the gap-4 probe for this spelling is gone ({spelling_needle!r}), so the "
            "header's 'each measured' is false for it"
        )

    assert "Gap 6" in header and "STATED and not pinned" in header, (
        "the header no longer states that gap 6 is STATED rather than pinned — "
        "the asymmetry is the claim, so it is pinned as one"
    )

    # The 4b section header counted the forms wrong (two vs the three the
    # probes cover: inline map, anonymous pattern, allowlist prefilter).
    # Sliced to the HEADER rather than searched file-wide: this leg's own
    # message names the retracted wording, so a file-wide needle would match
    # itself — the self-match that makes a self-description pin vacuous.
    anchor = src.find("# ── 4b.")
    assert anchor >= 0, "the 4b section header is gone — this leg measures nothing"
    section_header = src[anchor : anchor + 160]
    assert "two forms" not in section_header, (
        "the 4b section header is back to counting two forms, but the section "
        "holds probes for THREE write forms"
    )

    # A LEG COUNT in the guard's own prose (`#9403` review, P2). The
    # `PRE_FIX_REV` comment said "**9 failed / 10 passed of the 19 legs at this
    # revision**" while the file collected 21 and the pre-fix run split 10/11 —
    # the same "a count in prose is a claim the code outgrows silently" failure
    # the round-8 correction declares eliminated, one paragraph above it. The
    # measured reading is re-derived at every revision (`PRE_FIX_REV` says how),
    # so the provenance comment must not carry a number at all.
    #
    # Scanned over the HEADER BLOCK (everything before the first `def`), not
    # `__doc__`: the stale count lived in a `#:` comment, which `__doc__` does
    # not carry, and not over the whole source either — a leg's docstring may
    # quote a PAST measurement ("left all 16 legs green") or this leg's own
    # message, and a file-wide scan would match itself.
    header_block = src[: src.find("\ndef ")]
    assert header_block, "the header block could not be located — this check measures nothing"
    numeric_leg_counts = re.findall(
        r"\b(?:of the )?\d+ legs\b|\b\d+ (?:failed|passed) / \d+ (?:failed|passed)\b",
        header_block,
    )
    assert not numeric_leg_counts, (
        "the module header states a numeric leg count "
        f"{numeric_leg_counts} — the split moves with every leg added or "
        "removed, so it is an unmeasured claim in the PROSE of a guard whose "
        "subject is unmeasured claims; state how to re-measure it instead"
    )

    # The pin-index coverage and ORDER are pinned by
    # `test_the_pin_index_names_every_leg`, which owns that claim: two legs
    # asserting one property is how a pair drifts apart.


def test_the_correction_ledger_numbers_each_round_once():
    """The ledger's only index is its round numbering, so a repeated ordinal
    makes the current correction unidentifiable — and the FOURTH round's false
    record of §13's citation must not come back.

    Measured at the `#9403` head: two sections were both titled *"Seventh
    review-cycle correction"*, and the fourth said §13's `create_about_edge`
    citation was *"also qualified (`:344` at the pinned revision) the way §11.1's
    copy already was"* while §13 carries no line number at all. Both are
    section-scoped here because a file-wide absence check would be satisfied by
    the later round's correction note quoting the sentence it corrects.
    """
    changelog = _doc(STORAGE_CHANGELOG)
    headings = re.findall(r"^\*\*(\w+) review-cycle correction", changelog, re.M)
    assert headings, (
        "no `**<Ordinal> review-cycle correction` heading found in the ledger — "
        "the rounding scheme moved and this leg measures nothing"
    )
    seen: dict[str, int] = {}
    for ordinal in headings:
        seen[ordinal] = seen.get(ordinal, 0) + 1
    repeats = {o: n for o, n in seen.items() if n > 1}
    assert not repeats, (
        "the correction ledger numbers a round more than once "
        f"({repeats}); the round number is the ledger's only index, so a reader "
        f"cannot tell which entry is current. Headings found: {headings}"
    )

    fourth = _section(
        changelog,
        "**Fourth review-cycle correction",
        "**Fifth review-cycle correction",
    )
    assert "is also qualified" not in _flat(fourth), (
        "the FOURTH correction again asserts that §13's `create_about_edge` "
        "citation is qualified with `:344` — measured, §13 asserts the symbol "
        "only and `:344` appears in §11.1 alone, so the record is false"
    )


#: The UNMEASURED line distance the pin index carried for the §9.6 retraction
#: before the `#9450` review (`seventy lines above the §11.1 correction`). A
#: distance like this is true only of the revision it was measured at, and this
#: one was wrong in every revision scanned — which is why the correction DELETES
#: it rather than re-measuring it. Written as a rule (not one literal) so a
#: re-typed count cannot evade it.
_PIN_INDEX_DISTANCE = re.compile(
    r"\b(?:\d[\d,]*|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|a hundred)\s+lines?\b",
    re.I,
)

#: The pin-index entry for that leg as it stood BEFORE the correction, verbatim
#: (the only honest way to prove the two rules below can red at all).
_PIN_INDEX_PRE_FIX_PROBE = (
    "7. `test_the_references_paragraph_scopes_property_freedom_to_source_version` —\n"
    "   §9.6's *\"identity/mention and referential-containment `references` links stay\n"
    "   property-free\"* is the SAME unqualified absolute, seventy lines above the\n"
    "   §11.1 correction, for a different link class (a record-projected `references`\n"
    "   edge carries `idx` too)."
)

#: The REASON that entry reached for and the code disproves.
_PIN_INDEX_STALE_REASON = "a record-projected `references` edge carries `idx` too"

#: The fact that replaced it.
_PIN_INDEX_TRUE_REASON = "no record kind emits a `references` relation"


def test_the_pin_index_entry_for_the_9_6_retraction_states_no_measured_distance():
    """The "WHAT IS PINNED" index is PROSE about each leg, and it rots like any
    other prose — measured at the `#9450` review: entry 7 still carried BOTH
    premises the leg it indexes had just retracted.

    * the unmeasured line DISTANCE (`seventy lines above the §11.1 correction`),
      which is false measured and which the correcting commit *said* it deleted
      "in both places it survived" — it survived in a THIRD place, in this file;
    * the DISPROVED reason (`a record-projected `references` edge carries `idx`
      too`), which the leg's own docstring now contradicts in as many words: no
      record kind emits a `references` relation.

    Nothing red on either, because `test_the_pin_index_names_every_leg` pins the
    entry SET and the FILE ORDER and never the entry CONTENT — so the index was
    the one place a refuted reason could outlive its own retraction. A reader who
    judges "is leg 7 still valid?" re-derives from this entry.

    SCOPE: entry CONTENT only. Coverage and order stay owned by
    `test_the_pin_index_names_every_leg`.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    start = source.index("WHAT IS PINNED")
    block = source[start : source.index("NOT promised:", start)]
    marks = list(_PIN_INDEX_ENTRY.finditer(block))
    assert marks, (
        "the pin index is not in the shape this leg reads — re-derive it before "
        "trusting the checks below"
    )
    chunks: dict[str, str] = {}
    for position, match in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(block)
        chunks[match.group(1)] = block[match.start() : end]

    # Anti-vacuity, in the shape this file already uses for its matchers: both
    # rules must FIRE on the pre-fix entry, or neither can red on a re-introduction.
    assert _PIN_INDEX_DISTANCE.search(_PIN_INDEX_PRE_FIX_PROBE), (
        "the distance rule does not match the PRE-FIX entry "
        f"({_PIN_INDEX_PRE_FIX_PROBE!r}), so it cannot red on a re-introduction"
    )
    assert _flat(_PIN_INDEX_STALE_REASON) in _flat(_PIN_INDEX_PRE_FIX_PROBE), (
        "the stale-reason rule does not match the PRE-FIX entry "
        f"({_PIN_INDEX_PRE_FIX_PROBE!r}), so it cannot red on a re-introduction"
    )

    # The distance half is file-wide WITHIN the index: an unmeasured count is not
    # a claim the index may carry in ANY entry, not only the one that had one.
    distances = {
        name: found
        for name, text in chunks.items()
        if (found := _PIN_INDEX_DISTANCE.findall(text))
    }
    assert not distances, (
        f"the pin index states a line DISTANCE: {distances}. Name the anchor "
        "(`above the §11.1 correction`) instead of counting to it — `seventy "
        "lines above` was wrong in every revision scanned, and a count estimated "
        "once and copied is what this correction exists to end"
    )

    leg = "test_the_references_paragraph_scopes_property_freedom_to_source_version"
    assert leg in chunks, (
        f"the pin index has no entry for `{leg}`, so the leg it indexes is "
        "unreachable from the index — re-derive this leg against the new index"
    )
    entry = _flat(chunks[leg])
    assert _flat(_PIN_INDEX_STALE_REASON) not in entry, (
        "the pin-index entry for the §9.6 retraction still gives the DISPROVED "
        f"reason ({_PIN_INDEX_STALE_REASON!r}): `cache_projection."
        "relation_statement` stamps `idx` only on record-projected "
        "`public.relation` rows, and no record kind emits a `references` "
        "relation. A reader who re-derives from the index re-derives the wrong "
        "reason — which is how a retracted claim outlives its retraction"
    )
    assert _flat(_PIN_INDEX_TRUE_REASON) in entry, (
        "the pin-index entry for the §9.6 retraction does not state the fact that "
        f"replaced the disproved reason ({_PIN_INDEX_TRUE_REASON!r})"
    )


#: The revision the six production literals below are MEASURED against: the
#: `main` commit that carries all six (the `#9484` merge, i.e. the tree the
#: `#9450` review measured them in) and that IMMUTABLY keeps carrying them once
#: this PR lands — a commit's content does not change when a later one edits the
#: file, so the pin cannot invert the way `git merge-base HEAD origin/main`
#: would. On `main`'s first-parent history, so `_ensure_commit`'s bounded fetch
#: of `main` can reach it while it is recent; the leg SKIPS (never passes) where
#: it has fallen out of that bound. TO RE-MEASURE: pick another `main` commit
#: that still carries all six literals under `_flat`, confirm with
#: `git show <rev>:<path>`, and swap it here.
_RETRACTED_PRODUCTION_REV = "8767b1c8b"

#: The six production docstrings that carried the retracted absolute (`#9450`
#: review, P2), as (file, retracted literal, site ANCHOR, scoped fact). Pre-fix
#: literals verbatim, so each is measurable against the revision it was removed
#: from — and MEASURED by
#: `test_the_retracted_production_literals_are_measured_against_the_pre_fix_tree`,
#: because "measurable" is not "measured": a mistyped literal here would be a
#: permanently green dead pin, inside the guard whose subject is dead pins.
#:
#: ⛔ The last two elements are what keep the POSITIVE half SITE-scoped. Two of
#: these files carry two sites each, and a per-FILE membership test collapses
#: each pair: as long as one passage still says "carries no `sourceVersion`",
#: the other can lose its statement entirely and the leg stays green — measured,
#: the `#9450` review's silent deletion (empty `link_source_to_entity`'s
#: containment clause while its anchor paragraph still states the fact). The
#: ANCHOR is a phrase that exists ONCE and precedes the site's own scoped fact,
#: so the assertion is bounded by the site rather than by the file.
_RETRACTED_PRODUCTION_ABSOLUTES = (
    (
        "tortoise/sdk.py",
        '``"Source"`` means referential containment and stays property-free.',
        'The alias is NOT equivalent to ``"Source"`` for the caller',
        '``"Source"`` means referential containment and carries no ``sourceVersion``.',
    ),
    (
        "tortoise/sdk.py",
        "``Object`` links stay property-free.",
        "Written ``ON CREATE`` only.",
        "``Object`` (identity/mention) and ``Source`` (containment) links carry no "
        "``sourceVersion``",
    ),
    (
        "tortoise/search_engine.py",
        "identity/mention ``Object`` link stays property-free",
        "owner ruling 2026-09-25, option A — ``Event``/``Document`` annotate",
        "an identity/mention ``Object`` link carries no ``sourceVersion``",
    ),
    (
        "tortoise/search_engine.py",
        "Its link is property-free by the same ruling",
        "AN ``Object`` READS ``unknown``, AND THAT IS THE ANSWER, NOT A BUG.",
        "Its link carries no ``sourceVersion`` by the same ruling",
    ),
    (
        "tools/source_currency.py",
        "keeps identity/mention ``references`` links property-free",
        "the 2026-09-25 owner ruling (option A) keeps",
        "identity/mention ``references`` links carrying no ``sourceVersion``",
    ),
    (
        "tools/source_currency.py",
        "its ``references`` link is property-free (owner ruling 2026-09-25",
        "An ``:Object`` reads ``unknown`` **by decision**, not by omission:",
        "its ``references`` link carries no ``sourceVersion`` (owner ruling 2026-09-25",
    ),
)

#: How far AFTER a site's anchor its scoped fact may sit, in flattened characters.
#: Measured with the widest row at 172, and bounded before the NEXT site's anchor
#: (the closest pair, in `tortoise/sdk.py`, is 488 apart), so a NEIGHBOUR passage
#: cannot satisfy this row — which is the whole point of the window: a window
#: wide enough to span the file would re-create the file-wide check it replaces.
_SITE_WINDOW = 400


def _unstated_scoped_facts(texts: dict[str, str]) -> list[str]:
    """Rows whose SITE `texts[path]` names no scoped fact within its own window.

    Split out of the leg so the anti-vacuity probe can run the SAME check over a
    MUTATED text: a probe that re-implemented the rule could pass while the rule
    it is supposed to prove was broken. Accepts raw source; `_flat` is applied
    here (and is idempotent, so a caller may pre-flatten).
    """
    unstated: list[str] = []
    for path, _literal, anchor, scoped in _RETRACTED_PRODUCTION_ABSOLUTES:
        flattened = _flat(texts[path])
        needle = _flat(anchor)
        at = flattened.find(needle)
        if at < 0:
            unstated.append(f"{path}: the site anchor {anchor!r} is gone")
            continue
        occurrences = flattened.count(needle)
        if occurrences > 1:
            unstated.append(
                f"{path}: the site anchor {anchor!r} matches {occurrences} places, so "
                "the window no longer identifies ONE site (re-pick the anchor)"
            )
            continue
        if _flat(scoped) not in flattened[at : at + _SITE_WINDOW]:
            unstated.append(f"{path}: the site {anchor!r} lost {scoped!r}")
    return unstated


def test_the_retracted_references_absolute_is_gone_from_production_docstrings():
    """The §9.6 retraction has to reach the prose that CITES it, or the next
    reader re-derives the absolute from the code they are reading.

    Measured at the `#9450` review: six production docstrings still said an
    identity/mention or containment `references` link is "property-free" —
    including `TortoiseSDK.link_source_to_entity`'s docstring, which cited
    `STORAGE-ARCHITECTURE.md` §9.6, a section that by then said the opposite, and
    `tools/source_currency.py`, which attributed the absolute to the very owner
    ruling that scopes it to the anchor. Those are pre-existing (the PR's stated
    indicator was `projection/edges.py`), and they are the same root: leaving
    them is exactly the "a correction that fixes one wording and leaves three"
    failure the changelog names.

    `_flat`-matched, so rewrapping a docstring cannot evade the check; each
    literal must also be absent from the PRODUCTION sources only — the test tree
    is out of this leg's scope on purpose, since a guard that also scanned tests
    would match its own table.

    ⛔ BOTH halves are SITE-scoped, and the positive one was not at first: it
    asked whether each FILE still said "carries no `sourceVersion`", which two of
    these files satisfy twice. The `#9450` review measured the deletion that
    survived it — empty `link_source_to_entity`'s containment clause while its
    anchor paragraph still states the fact, no retracted literal reappearing, leg
    green — so the table now pairs each site with an ANCHOR and the scoped fact
    that site must state, and the fact is read only inside that site's window.
    """
    assert _RETRACTED_PRODUCTION_ABSOLUTES, "the table is empty — nothing is pinned"
    texts = {path: _doc(ROOT / path) for path, *_ in _RETRACTED_PRODUCTION_ABSOLUTES}
    live = [
        f"{path}: {literal!r}"
        for path, literal, _anchor, _scoped in _RETRACTED_PRODUCTION_ABSOLUTES
        if _flat(literal) in _flat(texts[path])
    ]
    assert not live, (
        "a production docstring still states the retracted absolute for a "
        "`references` link:\n  " + "\n  ".join(live) + "\n\nIt is retracted as an "
        "ABSOLUTE, not because such a link carries a property today — scope it to "
        "the fact §9.6 establishes: those links carry no `sourceVersion`."
    )
    # The positive half: the scoped fact must be SAID at those sites, not merely
    # have the absolute removed — a silent deletion leaves the next reader with no
    # statement at all, which is how the absolute gets re-invented. SITE-scoped,
    # never file-wide: two of these files carry two sites each, so a per-file
    # `in text` test stays green while ONE of the pair is emptied — the `#9450`
    # review's silent deletion, and the same scoping defect the `_section`
    # docstring above records (#8639).
    unstated = _unstated_scoped_facts(texts)
    assert not unstated, (
        "these production SITES dropped the absolute without stating the scoped "
        "fact inside their own passage (the file still says it elsewhere, which is "
        "why a file-wide check was green):\n  " + "\n  ".join(unstated)
    )
    # Anti-vacuity, and MEASURED rather than argued: re-run BOTH halves of the old
    # positive half over the review's silent deletion — the containment clause of
    # `link_source_to_entity` emptied while its anchor paragraph still states the
    # fact. The file-wide form (the one this leg shipped first) is GREEN on it;
    # the site-scoped form reds. `_flat` is idempotent, so the probe mutates the
    # flattened text and the same helper still reads it.
    emptied = {path: _flat(text) for path, text in texts.items()}
    emptied["tortoise/sdk.py"] = emptied["tortoise/sdk.py"].replace(
        "referential containment and carries no sourceVersion",
        "referential containment",
    )
    assert emptied["tortoise/sdk.py"] != _flat(texts["tortoise/sdk.py"]), (
        "the anti-vacuity mutation did not apply — the containment clause's "
        "wording moved, so this probe no longer reproduces the silent deletion "
        "it exists to prove. Re-derive it against the clause, do not delete it"
    )
    file_wide = [
        path for path, text in emptied.items() if "no sourceVersion" not in _flat(text)
    ]
    assert not file_wide, (
        "the anti-vacuity probe's premise no longer holds: the emptied text ALSO "
        "loses the scoped fact from the site's neighbour, so a file-wide check "
        f"would red too ({file_wide}) and this probe would prove nothing about "
        "the scoping. Re-derive the mutation so only ONE of the pair is emptied"
    )
    assert _unstated_scoped_facts(emptied), (
        "the SITE-scoped half does not red on the review's silent deletion — the "
        "containment clause of `link_source_to_entity` emptied while its anchor "
        "paragraph still says \"carries no `sourceVersion`\" — so it is a dead "
        "pin exactly where a per-file check was"
    )


def test_the_retracted_production_literals_are_measured_against_the_pre_fix_tree():
    """The table above is only a pin if each literal can MATCH the text it was
    removed from — and this file's own discipline (legs 24/25, and leg 29's
    `_PIN_INDEX_PRE_FIX_PROBE`) is that a provenance claim nothing compares
    against history is exactly the defect the guard exists to stop.

    Leg 30 asserts each literal is ABSENT at HEAD. That is unfalsifiable against
    a literal that was never there: change one character and the assertion is
    permanently green, and the six production sites it is supposed to keep
    scoped lose their pin silently — inside the guard whose subject is dead
    pins, which is the shape the `#9450` review named ("measurable" is not
    "measured"). So each literal is measured PRESENT at
    `_RETRACTED_PRODUCTION_REV`: the `main` commit the six were live at, whose
    content is immutable once this PR lands.

    SCOPE: presence at that revision only — it cannot prove the literal was
    there for EVERY earlier revision, and it does not re-measure leg 30's HEAD
    side. Skips, never passes, where this checkout cannot supply the history
    (`_ensure_commit` is bounded, and a non-shallow clone missing the rev is
    never shallow-ified to reach it).
    """
    assert _RETRACTED_PRODUCTION_ABSOLUTES, "the table is empty — nothing is pinned"
    paths = sorted({path for path, *_ in _RETRACTED_PRODUCTION_ABSOLUTES})
    texts: dict[str, str] = {}
    missing: list[str] = []
    for path in paths:
        text = _git_show(_RETRACTED_PRODUCTION_REV, path)
        if text is None:
            missing.append(path)
        else:
            texts[path] = _flat(text)
    if missing:
        pytest.skip(
            f"`git show {_RETRACTED_PRODUCTION_REV}:<path>` is not reachable even "
            f"after a bounded fetch of `origin main` (depths {_SHALLOW_DEPTHS}) "
            f"for {missing} — no git, an unreachable remote, or the rev is older "
            "than the deepest bound, so the pre-fix presence cannot be measured "
            "here. The leg SKIPS, it never passes on history it could not read"
        )
    dead = [
        f"{path}: {literal!r}"
        for path, literal, _anchor, _scoped in _RETRACTED_PRODUCTION_ABSOLUTES
        if _flat(literal) not in texts[path]
    ]
    assert not dead, (
        "these literals are pinned as retracted but do NOT match the text at "
        f"{_RETRACTED_PRODUCTION_REV} — so leg 30's absence check cannot red on a "
        "re-introduction of the real wording, and the six production sites are "
        "unpinned:\n  " + "\n  ".join(dead) + "\nRe-quote the literal from "
        f"`git show {_RETRACTED_PRODUCTION_REV}:<path>` (or re-pin the revision)"
    )


def test_every_direct_edge_created_producer_is_safe_for_an_about_edge():
    """`_rebuild_all_replay`'s creation fold does NOT filter on `edge_type`, so
    what keeps a property off an `about*` edge is the map's FIXED vocabulary —
    and that gate holds only while every attribute-carrying producer provably
    cannot name one.

    `#9553` review P2 measured the hole this closes: three sites in this file
    justified the `_rebuild_all_replay` exemption with *"the `DirectEdgeCreated`
    fold, whose ONLY producer (`create_direct_edge`) refuses any `op_type`
    outside `{IMPL, NAND}`"*. `#9553` added a SECOND producer — one reached by
    the edge mints, which journals `aboutSubject`/`aboutObject`/… as readily as
    `ownedBy` — so
    that sentence became false while the conclusion happened to remain true,
    for a DIFFERENT reason (the new producer emits no attribute). The file's own
    doctrine is that an EXEMPTION is a fail-open cell unless something compares
    against it, and nothing compared against this one: the name-set comparison
    still passed. This leg is that comparison.

    Two facts, asserted separately, because either alone is insufficient:
      1. the producer that CAN name an `about*` edge carries NO key from the
         vocabulary — so nothing lands. That is `_record_structural_edge`, the
         recorder the edge MINTS call: it journals whatever predicate the mint
         wrote, which is the whole `about*` family, and it passes only
         `{id, src, tgt, edge_type, source_label, target_label}`; and
      2. the producer that DOES emit them (`create_direct_edge`: `direction`
         always, plus optional confidence/weight/label/batch_id) is restricted
         to `{IMPL, NAND}` AT ITS OWN STATEMENT — asserted in its function body,
         not restated as prose.

    A new producer carrying a vocabulary key without a declared proof reds here,
    and so does deleting a declared proof's statement — the map and the code are
    compared in both directions.
    """
    sites: list[tuple[str, str, int, set[str]]] = []
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "DirectEdgeCreated" not in text:
            continue
        module = path.relative_to(ROOT).as_posix()
        sites.extend(
            (module, symbol, lineno, kwargs)
            for symbol, lineno, kwargs in _direct_edge_created_emissions(
                module, text
            )
        )
    assert sites, (
        "the walk found no `DirectEdgeCreated` producer at all — the census has "
        "lost its subject, so nothing below means anything"
    )

    attr_sites = {
        (module, symbol)
        for module, symbol, _lineno, kwargs in sites
        if kwargs & FALSE_POSITIVE_ATTR_VOCAB
    }
    declared = set(DIRECT_EDGE_CREATED_ATTR_GUARDS)
    assert not (attr_sites - declared), (
        "these `DirectEdgeCreated` producers pass an attribute the fold's "
        "`attrs` map copies onto the edge, and are NOT declared with a proof "
        "that they cannot name an `about*` edge — so that property would land "
        "on a live AND replayed about edge while every other leg here stays "
        "green (the `#9553` review P2 fail-open cell):\n  "
        + "\n  ".join(sorted(f"{m}::{s}" for m, s in attr_sites - declared))
    )
    assert not (declared - attr_sites), (
        "these declared attribute-carrying producers no longer pass any "
        "vocabulary key, so the proof is about code that changed — re-derive "
        "`DIRECT_EDGE_CREATED_ATTR_GUARDS`:\n  "
        + "\n  ".join(sorted(f"{m}::{s}" for m, s in declared - attr_sites))
    )
    for (module, symbol), guard in DIRECT_EDGE_CREATED_ATTR_GUARDS.items():
        body = _function_body(module, symbol)
        assert guard in body, (
            f"`{module}::{symbol}` passes an attribute the fold copies onto the "
            f"edge, and the proof that keeps it off an `about*` edge "
            f"(`{guard}`) is no longer in its body — so its descriptor can now "
            "name an `about*` predicate"
        )

    # Half (1): the producer that CAN name an `about*` edge carries no attr.
    about_capable = {
        (module, symbol)
        for module, symbol, _lineno, kwargs in sites
        if not (kwargs & FALSE_POSITIVE_ATTR_VOCAB)
    }
    assert ("tortoise/sdk.py", "_record_structural_edge") in about_capable, (
        "`_record_structural_edge` — the recorder the structural-edge MINTS call "
        "— is no longer an attribute-free `DirectEdgeCreated` producer. It "
        "journals whatever the mint wrote, which includes the whole `about*` "
        "family, so ANY vocabulary key on that emission lands on an about edge. "
        "If it now passes one, it belongs in "
        "`DIRECT_EDGE_CREATED_ATTR_GUARDS` WITH a restriction on the relation, "
        "and the `NOT_AN_ABOUT_WRITER` row plus the documents' 'carries no "
        "property on an about edge' would have to be re-derived"
    )


# ── #10149: pin the CLAIMS, not only the numerals ────────────────────────────
#: What a sentence must SAY about a writer, keyed by the table the writer is in.
#: Every leg above compares the documents' NUMERALS to these tables, and a
#: numeral is satisfied by the right token anywhere in the section — so a
#: sentence could state the opposite of the table sitting next to it and stay
#: green. That is not hypothetical: four review cycles on a five-line fix
#: (`#9417`) found §13 calling a counted writer an exemption, twice, with the
#: suite green both times. These markers are the documents' OWN vocabulary for
#: each class, and they are derived from the classes the tables already encode —
#: a `MAP_WRITERS` row carries the edge's whole attribute set by property map, a
#: `RECORDED_WRITERS` row writes `SET r.<prop>`, and a `NOT_AN_ABOUT_WRITER` row
#: is an exemption that writes NOTHING. `_flat` is applied to SENTENCES, so every
#: marker is written in its FLATTENED spelling — `_flat` turns `-` between word
#: characters into a SPACE, which is why a hyphenated marker can never match.
#: ⛔ That is not a style note: `"MAP-form"`, `"NAMED-form"`, `"re-materializ"`
#: and `"MAP-form writer"` were all DEAD on arrival (`#10200` reviews measured 0
#: occurrences each in the flattened documents), and a marker that matches
#: nothing pins nothing while reading as if it did.
#: ⛔ And a probe per family is NOT enough: `#10200` review measured that
#: reverting three of the four flattened spellings left the suite green, because
#: a live neighbour covered the same sentence. The invariant is therefore static
#: and covers every member of every tuple —
#: `test_no_marker_is_written_in_a_spelling_the_flattener_cannot_produce`.
CLASS_VERDICT_MARKERS: dict[str, tuple[str, ...]] = {
    "MAP": (
        "property MAP",
        "MAP form",
        "re materializ",
        "SET r +=",
        "SET r = $p",
        "SET nr +=",
    ),
    "NAMED": (
        "NAMED form",
        "SET r.<prop>",
        "SET r.confidence",
        "originating",
        "originates",
    ),
    "EXEMPT": (
        "false positive",
        "writes no property",
        "carries no property",
        # The same claim in the verb form the owner's decision uses ("said to
        # drop / carry no property") — a denial vocabulary that only knows the
        # articled spelling leaves the other spelling green (`#10200` review).
        "carries nothing",
        "writes nothing",
    ),
}

#: Present-tense DENIALS. Every entry is the core of a sentence MEASURED false at
#: some head while this suite was green — `#10149`'s cycle 3 (*"`sdk.
#: supersede_point` is NOT one of them … it **drops** the binder's
#: `confidence`"*, 130 words before counting it as a writer) and the SAME
#: sentence still live in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 (*"neither
#: carries a property on an about edge … the guard declares both census FALSE
#: POSITIVES"*) — so this set is measured, not invented. A denial about HISTORY
#: is TRUE and must stay writable; that is the whole difference between "it
#: drops the property" and "it was a declared false positive before `#9417`",
#: and `HISTORY_MARKERS` is what tells the two apart.
DENIAL_MARKERS: tuple[str, ...] = (
    "false positive",
    "drops the",
    "drops confidence",
    "is not one of them",
    "neither carries",
    "transferred bare",
    "carries no property",
    # ⛔ THE OWNER'S OWN NOUN AND VERB (`#10149` scoping comment, quoted: *"a
    # counted writer may not be called **an exemption** or said to **drop** /
    # carry no property"*). Neither appeared in any tuple, so the exact
    # contradiction the decision names was green while the phrase the documents
    # happened to use was caught (`#10200` review, measured).
    # ⛔ The NOUN is pinned as the DENIAL PHRASES — the predicate and articled
    # forms — NOT the bare stem `exempt`. The bare stem was measured "safe"
    # (`#10200` review cycle 2, 0 red sentences in either document), but the reason
    # it red nothing is that the only `exempt*` spellings the documents write are
    # `exempts`, `exemptions` and `exempting` — the one spelling that is NOT a
    # denial — so its liveness was an ARTIFACT and its only live effect was a FALSE
    # RED: the documents' own §13 sentence *"the guard pins the shapes that make
    # both exemptions true instead of merely exempting them"* reds as `'exempt'`
    # the moment any census key is named in it (`#10200` review, cycle 5,
    # executed). A denial the documents' plural noun can satisfy is not a denial.
    # ⚠️ `is exempted` is listed although `is exempt` is a substring of it: the
    # POST-positioned-negation read (`_MARKER_NEGATION_AFTER_RE`) anchors at the
    # match END, so *"is exempted from the census no longer"* is only seen when the
    # longer spelling is matched. It is not decoration.
    "is exempt",
    "is exempted",
    "an exemption",
    "the exemption",
    "carries nothing",
    "writes nothing",
    # ⛔ THE NEGATED-AUXILIARY FORMS — the same denial with the writer as the
    # SUBJECT of the predicate instead of the object of a verb. The tuple above
    # lists only the AFFIRMATIVE phrasings (`carries no property`, `writes
    # nothing`), so changing one auxiliary escaped the owner's decision
    # VERBATIM: *"`sdk.supersede_point` does not carry the binder's `confidence`
    # on an about edge"* and *"`hosted_backup.restore_graph` itself never writes
    # the attribute set at all"* were both GREEN at the `#10200` head, while the
    # PR's own record claimed no passage may say it drops / carries no property
    # (`#10200` review, cycle 6, executed).
    # ⚠️ Each entry keeps the DETERMINER (`the` / `a` / `any`) on purpose: the bare
    # auxiliary is also how these documents say true things about OTHER subjects
    # (`ingest_corpus` "never writes a Source"), and a denial marker that fires on
    # a neighbour's object reds true prose.
    "does not carry the",
    "does not carry a",
    "does not write the",
    "never carries the",
    "never writes the",
    "never writes a",
    "no longer carries the",
    # ⛔ THE SAME POST-NEGATED FORM WITH THE OTHER VERB. `no longer carries the`
    # was a member and `no longer writes the` was not, so the denial survived by
    # swapping `carries` for `writes` — and `MARKERS_ABSENT_BY_DESIGN` recorded
    # the absence as covered ('its determiner form is recorded above'), which made
    # the gap read as a closed direction. Measured at the `#10200` review cycle 7:
    # *"`sdk.supersede_point` no longer writes the attribute set on an about
    # edge"* was GREEN while every other determiner form of the same sentence
    # reds.
    "no longer writes the",
)

#: Denials whose spelling VARIES too much for a literal: one FRAME per VOICE.
#: ⛔ A literal cannot pin these, and `#10200` review cycle 7 measured the gap the
#: transfer-bare frame was added for: *"`sdk.supersede_point` transfers the about
#: edge bare"* was GREEN while `transferred bare` — the PAST-tense tripwire for
#: the retracted sentence — was a member, so the live denial and the retracted one
#: were pinned in different tenses and only ONE of the two was pinned at all. The
#: words between the verb and `bare` are the writer's OBJECT and they vary (`it` /
#: `the edge` / `the about edge`), so the rule reads the FRAME — a transfer
#: predicate, then `bare` inside the same clause — instead of enumerating
#: determiner spellings, for the same reason `_HYPHENATED_MARKER` is a pattern
#: rather than four literals.
#:
#: ⛔ THE NEXT ROUND MEASURED FIVE MORE, AND ALL FIVE ARE ONE DEFECT: the
#: vocabulary was a list of SPELLINGS, so it knew only the spellings in it. Each of
#: these was GREEN against a counted MAP row at `a03951455b` (executed by splicing
#: the sentence into §13 and running this leg's own functions, with a control that
#: reds):
#:   * the BASE/INFINITIVE form of a denial verb the literals spell in the third
#:     person — *"is said to **drop** the property"* — including the exact wording
#:     of the owner's own decision (*"said to **drop** / carry no property"*,
#:     `#10149` scoping comment). The literals hold `drops the` / `carries no
#:     property`; a frame is what covers a verb whose INFLECTION is not pinned.
#:   * the property named by its own NAME — *"carries no `confidence`"* — which is
#:     the ONE spelling the guarded passages actually use (§13: *"keeps the
#:     binder's `confidence`"*), so the vocabulary missed the documents' house noun
#:     while pinning the generic word `property`.
#:   * the OBJECT voice — *"the guard **exempts** `supersede_point`"*, *"Rule 2
#:     **keeps** `supersede_point` **out of** the counted rows"*, *"the census
#:     **does not count** `supersede_point`"* — every literal puts the writer in
#:     SUBJECT position, and English puts the same claim's argument in `obj` as
#:     readily as in `nsubj` (UD encodes the role as a RELATION, not a surface
#:     order), so subject-position matching is a dialect, not a rule.
#:   * the PASSIVE — *"No property **is written by** `supersede_point`"*.
#:   * the ADJECTIVE — *"leaves its about edge **property free**"* — which is the
#:     retracted §11.1 claim itself, in the one part of speech no verb list holds.
#:
#: ⚠️ Bounded to the clause: `[^.;,]{0,40}?` cannot cross punctuation, so *"its
#: about/structural transfer is a separate BARE `MERGE`"* in a NEIGHBOURING clause
#: is not read as a denial of this writer (and that sentence belongs to the EXEMPT
#: row anyway, which `DENIAL_MARKERS` never governs). The VOICE frames use
#: `[^;,]` instead, because a census KEY contains a period (`sdk.supersede_point`)
#: and a period-bounded class cannot reach across its own subject — measured: the
#: first draft's `[^.;,]` left *"the guard exempts `sdk.supersede_point` from the
#: census"* and *"Rule 2 keeps `sdk.supersede_point` out of the counted rows"*
#: GREEN, i.e. the frame was dead on arrival against its own probe.
#: ⚠️ STATED LIMIT: the TRANSFER-bare frame reads the VERB forms only (`transfers`
#: / `transferred`). The NOUN form (*"the transfer is still bare"*) is deliberately
#: NOT read: the REBUILD note `#10139` requires — *"the REBUILD half of the
#: supersede transfer is still bare"* — is TRUE prose about a counted writer, so a
#: noun-matching pattern reds a sentence the documents are required to carry, and
#: the only way an editor could clear it is to delete that note. A verb-form rule
#: costs one paraphrase and does not touch the record.
#:
#: ⛔ EVERY ENTRY DECLARES ITS OWN PROBE PAIR — `(pattern, must_see, must_not_see)`.
#: A frame that cannot see the denial it was written for is the same DEAD
#: instrument as a hyphenated literal (leg 36), and `must_not_see` is the TRUE
#: prose the frame has to stay out of. The table used to be patterns alone with the
#: transfer-bare probes hard-coded in the leg, which meant a NEW frame got no probe
#: unless someone remembered to add one — the width belonged to the leg, not to the
#: entry. A frame with no control is a frame nobody measured.
_PROPERTY_NOUN = r"(?:propert(?:y|ies)|confidence|attributes?)"
DENIAL_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    # ── the transfer-bare form (`#10200` review, cycle 7) ──────────────────────
    (
        re.compile(r"\btransfer(?:s|red)\b[^.;,]{0,40}?\bbare\b"),
        ("transfers the about edge bare", "it transfers it bare"),
        "The REBUILD half of the supersede transfer is still bare (`#10139`)",
    ),
    # ── the BASE / INFINITIVE spelling of a denial verb ────────────────────────
    (
        re.compile(rf"\b(?:drop|drops)\b[^.;,]{{0,30}}?\b{_PROPERTY_NOUN}\b"),
        (
            "`sdk.supersede_point` is said to drop the property.",
            "`sdk.supersede_point` will drop the binder's `confidence`.",
        ),
        "`sdk.supersede_point` does not drop the property.",
    ),
    (
        # ⛔ THE OBJECT-NEGATION FRAME, in EVERY verb this record treats as
        # synonymous — not just `carry`. The frame was measured bound to
        # `carry|carries` alone while the file's OWN vocabulary used the sibling
        # verbs: `writes no property` is a member of
        # `CLASS_VERDICT_MARKERS["EXEMPT"]` (it is how §13 states the EXEMPTION's
        # verdict), and `does not write the` / `never writes the` / `write|writes
        # nothing` were already denials — so the same construction with the
        # subject swapped to a COUNTED writer was GREEN. Measured at
        # `6d40e5732` (`#10200` review): *"`sdk.supersede_point` writes no property
        # on an about edge"*, *"… writes no `confidence` …"* and *"… sets no
        # property …"* each left the whole module green, while the `carry` control
        # on the same code path red. One alternation, three reachable green paths.
        # ⚠️ `set|sets` is included because the MAP class's own spelling is `SET`
        # (`SET r += $p`); the property noun is still what the frame reads, so the
        # object — not the verb — remains the thing that separates a denial from
        # the record.
        re.compile(
            rf"\b(?:carry|carries|write|writes|set|sets)\s+"
            rf"(?:no\s+{_PROPERTY_NOUN}|nothing)\b"
        ),
        (
            "`sdk.supersede_point` is said to carry no property.",
            "`sdk.supersede_point` carries no `confidence` on an about edge.",
            "`sdk.supersede_point` is said to carry nothing.",
            "`sdk.supersede_point` writes no property on an about edge.",
            "`sdk.supersede_point` writes no `confidence` on an about edge.",
            "`sdk.supersede_point` sets no property on an about edge.",
        ),
        # ⛔ NOT the bare prefix `carry no` / `carries no`: measured, that reds at
        # least four TRUE §13 sentences that name a counted KEY (`DirectEdgeRepoint`
        # carries no `attrs`, *"carries none of direction/…"*, *"§13 carries no
        # line numbers"*, *"carries no recorded version read at all"*). The OBJECT
        # is what separates a denial from the record, so the object is what the
        # frame reads.
        "`DirectEdgeRepoint` carries no `attrs`.",
    ),
    (
        re.compile(r"\b(?:write|writes)\s+nothing\b"),
        ("`sdk.supersede_point` is said to write nothing.",),
        "The guard pins every shape that writes the binder's `confidence`.",
    ),
    # ── a POSITIVE predicate under negation, over the property/attribute object ─
    (
        re.compile(
            rf"\b(?:does\s+not|do\s+not|never|no\s+longer)\s+"
            rf"(?:count|counts|set|sets|write|writes|carry|carries)\b"
            rf"[^.;,]{{0,40}}?\b(?:{_PROPERTY_NOUN}|writers?|census|counted)\b"
        ),
        (
            "`sdk.supersede_point` never sets a property on the about edge.",
            "`sdk.supersede_point` does not count as a writer.",
        ),
        "`sdk.supersede_point` keeps the binder's `confidence` and never loses "
        "it on replay.",
    ),
    # ── the OBJECT voice: the writer is the verb's object, not its subject ─────
    (
        re.compile(r"\b(?:does\s+not|do\s+not|never)\s+count\b"),
        ("The census does not count `sdk.supersede_point`.",),
        "`sdk.supersede_point` counts as a writer of the attribute set.",
    ),
    (
        re.compile(
            r"\b(?:is|are)\s+not\s+(?:a\s+|one\s+of\s+the\s+)?"
            r"(?:writers?|counted|members?)\b"
        ),
        (
            "`sdk.supersede_point` and `hosted_backup.restore_graph` are not "
            "writers of the attribute set.",
        ),
        # `the ONLY writer` is quantified-out by `_QUANTIFIER_NEGATION_RE` on the
        # class-marker half; the object frame must leave it alone for the same
        # reason ("not the only writer" does not deny that it is a writer).
        "`sdk.supersede_point` is not the only writer that carries the set by "
        "property MAP.",
    ),
    (
        re.compile(
            r"\b(?:is|are)\s+excluded\s+from\s+the\s+(?:census|counted|writer)"
        ),
        ("`sdk.supersede_point` is excluded from the census of writers.",),
        "`sdk.supersede_point` is counted among the writers.",
    ),
    (
        # ⛔ `exempts` — the third-person VERB only. The bare stem `exempt` is NOT
        # a denial in this record: the documents write `exempts` / `exemptions` /
        # `exempting`, and §13's *"make both exemptions true instead of merely
        # exempting them"* is TRUE prose (`#10200` review, cycle 5).
        re.compile(r"\bexempts\b[^;,]{0,80}?\b(?:census|counted)\b"),
        ("The guard exempts `sdk.supersede_point` from the census.",),
        "The guard makes both exemptions true instead of merely exempting them, "
        "and `sdk.supersede_point` re-materializes the set by property MAP.",
    ),
    (
        re.compile(
            r"\b(?:keeps?|leaves?)\b[^;,]{0,80}?\bout\s+of\s+the\s+"
            r"(?:census|counted)\b"
        ),
        ("Rule 2 keeps `sdk.supersede_point` out of the counted rows.",),
        "`sdk.supersede_point` keeps the binder's `confidence` on the about edge.",
    ),
    # ── the PASSIVE ────────────────────────────────────────────────────────────
    (
        re.compile(
            rf"\bno\s+{_PROPERTY_NOUN}\s+(?:is|are|gets?)\s+"
            rf"(?:written|carried|set|stored)\b"
        ),
        ("No property is written by `sdk.supersede_point` on an about edge.",),
        "`sdk.supersede_point` carries the binder's `confidence` on the about edge.",
    ),
    # ── the ADJECTIVE / PREPOSITIONAL forms ────────────────────────────────────
    (
        re.compile(rf"\bwithout\s+(?:any\s+)?{_PROPERTY_NOUN}\b"),
        ("`sdk.supersede_point` writes the edge without properties.",),
        "`sdk.supersede_point` writes the edge with the binder's `confidence` "
        "and without a second name.",
    ),
    (
        re.compile(r"\bproperty\s+free\b"),
        ("`sdk.supersede_point` leaves its about edge property free.",),
        "`sdk.supersede_point` carries the binder's `confidence` on the about edge.",
    ),
)

#: What makes a denial a statement about HISTORY rather than about HEAD — read in
#: the CLAUSE that carries the denial, never across the whole sentence (see
#: `_history_in_clause`). ⛔ `#10139` is deliberately NOT a member: it is an
#: OPEN defect reference (*"The REBUILD half is still bare"*), not a statement that
#: the state changed, and it ends the very paragraph this leg guards — so an
#: editor restoring the retracted sentence in the same breath as the rebuild note
#: walked straight through the gate (`#10200` review, measured: the cycle-3
#: sentence + ` (\`#10139\`)` was green). Nothing true is lost: the one sentence
#: that may need an escape names no census key today.
HISTORY_MARKERS: tuple[str, ...] = (
    "before #9417",
    "until #9417",
    "pre-#9417",
    "was a declared",
    #: `#10139` moved the LAST exemption out of the table by fixing its code, so
    #: the documents now record that move — and the record is TRUE prose that the
    #: denial rule must stay out of, exactly as it does for `#9417`. A tuple that
    #: only knows the first issue number makes every later correction unwritable
    #: (measured while writing `#10139`'s entries: the two sentences recording the
    #: move red as present-tense denials).
    #: ONE spelling, not a family: the marker-liveness leg reds a marker no
    #: document uses, so a form is added here when (and only when) a document
    #: writes it. `#10139`'s entries use `until`; `before #10139` / `pre-#10139`
    #: are deliberately absent rather than declared absent-by-design, because
    #: they are not this file's vocabulary yet.
    "until #10139",
)

#: POLARITY IS READ, NOT ASSUMED. A class marker under a negator does not STATE the
#: class, it DENIES it — and a negation of the class is precisely the claim `#10149`
#: exists to forbid. Measured on `#10200`'s review: leg 1 counted *"`sdk.
#: supersede_point` is not a re-materializing writer"* as a stated verdict (it
#: contains `re-materializ`) while leg 2 had no marker for it, so the ONE-WORD
#: paraphrase of the retracted cycle-3 sentence was green on BOTH legs.
#: ⛔ The negator vocabulary is `_MARKER_NEGATION_BEFORE_RE` /
#: `_MARKER_NEGATION_AFTER_RE` below — the scan `_marker_itself_negated` actually
#: runs. The constant that used to sit here (`_NEGATOR_RE`, which additionally
#: listed `fails to`, `stops` and `n't`) was deleted with the window machinery in
#: `#10200` review cycle 5: nothing reached it. ⚠️ STATED LIMIT, not coverage:
#: `fails to` / `stops` / `n't` / a BEFORE-positioned `no longer` are therefore NOT
#: read as class negators today.
#: They are recorded here so a lane that widens the live scan knows what the dead
#: one claimed, rather than rediscovering it.

#: Where a CLAUSE ends. A history marker outside the denial's own clause says
#: nothing about that denial — the sentence-wide reading is what let a trailing
#: open-defect reference excuse the retracted sentence above it.
#: ⚠️ The COMMA is a boundary for the HISTORY escape (`#10200` review): without it a
#: comma-joined run is ONE clause, so *"was a declared false positive before
#: `#9417`, and it still drops the property"* — history kept, the denial NOT
#: corrected — was green. In English a comma between coordinated predicates
#: separates two units, so it is a clause boundary here even though it is not a
#: sentence boundary.
_CLAUSE_BREAKS: tuple[str, ...] = (";", "\u2014", ":", "|", ",")

#: Where the HISTORY escape's clause ends: `_CLAUSE_BREAKS` PLUS the coordinators
#: that join two PREDICATES. ⛔ PUNCTUATION ALONE WAS FAIL-OPEN: a comma-less
#: coordination put a history marker and a present-tense denial in ONE "clause",
#: so `_history_in_clause` excused the denial — *"`sdk.supersede_point` was a
#: declared false positive before `#9417` and it still drops the property on an
#: about edge"* was GREEN, one omitted comma away from the cycle-3 sentence this
#: leg exists for (`#10200` review, cycle 6, executed). `and` / `but` / `yet` /
#: `while` / `whereas` each start a new predication, which is exactly the unit the
#: history marker has to be in.
#: ⚠️ Deliberately NOT applied to `_CLAUSE_BREAKS` itself: the count rule reads its
#: window from that tuple, and `_declared_false_positive_counts` must still see a
#: numeral in a coordinated clause ("6 writers, 3 by property MAP, and 1 declared
#: false positive") rather than silently lose it.
_HISTORY_BREAKS: tuple[str, ...] = (
    *_CLAUSE_BREAKS,
    " and ",
    " but ",
    " yet ",
    " while ",
    " whereas ",
)

#: ⛔ SUPERSEDED — this block used to define the NEGATOR's break set
#: (`_NEGATOR_BREAKS`, `_CLAUSE_BREAKS` minus `,`) and a coordinating-comma
#: exception, consumed by `_clause_before` + `_has_class_negator`, plus their
#: negator vocabulary (`_NEGATOR_RE`). All of it is GONE, because the
#: punctuation-window form it implemented is wrong in BOTH directions, measured
#: (`#10200` review cycles 2 and 3): it CLEARED a real class negation whenever an
#: unrelated clause carried a negator (*"Unlike `create_direct_edge`, which cannot
#: name an about edge, `sdk.supersede_point` re-materializes the set by property
#: MAP"*), and it CLEARED the exemption direction's own contradiction.
#: `_marker_itself_negated` replaced it (see `_class_marker_negated`).
#: ⛔ THE DEFECT THIS DELETION CLOSES: once `_clause_before` stopped being called,
#: the window machinery became UNREACHABLE but still read as coverage — and
#: `_QUANTIFIER_NEGATION_RE`'s exclusion of *"not only"* / *"not solely"* ran only
#: inside `_has_class_negator`, i.e. never. The round-2 fix was RECORDED as done
#: while the false red it prevents was live on BOTH legs: *"`cache_projection.
#: relation_statement` is not only a MAP form writer but the canonical one"* red as
#: `not MAP form` (`#10200` review, cycle 5, executed). That rule now lives in
#: `_marker_itself_negated`, where it is actually reached. A rule that exists only
#: in dead code is not a rule.
#: The two facts worth keeping from the block: the HISTORY escape keeps the plain
#: comma in `_CLAUSE_BREAKS` (a comma-joined run is not one clause — round 2's
#: fix), and `while`/`whereas` are SUBORDINATORS, not conjunct boundaries.

#: A POST-positioned negator is still a negation of its predicate — English
#: negates from behind as well as from in front, and these documents use that voice
#: 14 times (`no longer`). Reading polarity only BACKWARD left *"`sdk.supersede_point`
#: is a re-materializing writer no longer"* stating the class on both legs, and the
#: exemption form *"… a declared false positive no longer: it re-materializes the
#: set"* calling the exemption counted — the direction the recorded decision says
#: may not happen (`#10200` review, measured).
#: ⚠️ Deliberately NOT a whole-clause scan: a negator belonging to a NEIGHBOURING
#: predicate reds true prose — measured on §13's *"A third NAMED form writer does
#: not originate it but re-produces it on replay"*, whose `not` belongs to
#: `originate`, not to the class. That rule is `_MARKER_NEGATION_AFTER_RE` (the
#: POST half of `_marker_itself_negated`), and the clause-wide scan this comment
#: used to introduce (`_POST_NEGATION_RE`) is DELETED: it ran over `_clause_at`, so
#: a neighbour's `no longer` suppressed a class marker it does not govern
#: (`#10200` review, cycle 6, executed).

#: Quantifier negations negate a COUNT, not the class: *"is not the ONLY writer
#: that carries the set by property MAP"* AFFIRMS that this writer carries it, and
#: *"Not only X but also Y carries it by property MAP"* affirms both. Reading them
#: as class negations red a TRUE sentence on BOTH legs, and the cheapest way for an
#: editor to clear that red is to weaken true prose — the failure mode the legs
#: exist to prevent, pointed the other way. Stripped by `_marker_itself_negated`
#: — the LIVE negator scan — before `_MARKER_NEGATION_BEFORE_RE` runs. ⛔ It sat
#: in `_has_class_negator` until `#10200` review cycle 5, i.e. in dead code, so
#: this exclusion never ran; see the SUPERSEDED note above.
_QUANTIFIER_NEGATION_RE = re.compile(
    r"\bnot\s+(?:the\s+)?(?:only|merely|just|simply)\b|\bnot\s+solely\b", re.I
)


def _class_marker_negated(sentence: str, match) -> bool:
    """Does a NEGATOR govern the class marker at `match` — in front or behind?

    The BEFORE half is predicate-scoped (`_marker_itself_negated`), not the
    punctuation-window form it used to be (see the SUPERSEDED note above).
    ⛔ The window form is wrong in BOTH directions, measured at `#10200` review
    cycle 3: it CLEARED a class negation whenever an unrelated clause carried a
    negator (*"Unlike `create_direct_edge`, which cannot name an about edge,
    `sdk.supersede_point` re-materializes the set by property MAP"* red as `not
    property MAP`), and it CLEARED the exemption direction's own contradiction
    (*"…which never loses a value, is a counted MAP form writer"* was green) —
    because three of this file's four call sites shared it. The AFTER half is
    PREDICATE-scoped too (`_MARKER_NEGATION_AFTER_RE`: ≤ THREE word-tokens behind
    the marker, never across the clause), because the clause-wide read red TRUE
    prose whenever a neighbour's `no longer` sat in the same clause — see that
    constant (`#10200` review, cycle 6, executed).
    """
    return _marker_itself_negated(sentence, match)


#: A declared false positive described as COUNTED — the same contradiction from
#: the other side: an exemption the census counts is a writer described as
#: writing nothing. No history escape: this row has NEVER been counted, so a "it
#: was counted" claim about it has no true instance to allow.
EXEMPT_COUNTED_MARKERS: tuple[str, ...] = (
    "counted",
    "is one of them",
    # ⛔ THE POST-NEGATED FORMS, which are the same claim in the documents' other
    # voice: *"is a declared false positive no longer"*, *"is no longer a declared
    # false positive"*, *"is no longer exempt"* all assert the exemption has BECOME
    # counted. They were green in every spelling while `post_exemption`'s probe
    # reported the direction as closed — its red came from a NEIGHBOUR marker
    # elsewhere in the sentence, not from the post-negation form it named
    # (`#10200` review, cycle 3, measured).
    "false positive no longer",
    "no longer a declared false positive",
    "no longer exempt",
    # FLATTENED — and wider claims than the word "counted": an exemption
    # ASSERTED to carry the set by property map, or to re-materialize it, states
    # the same contradiction. These were measured green at the `#10200` head:
    # `"MAP-form writer"` was a dead marker (0 flattened occurrences), *"writes
    # the attribute set by property MAP"* named no member at all, and *"… is a
    # declared false positive no longer: it re-materializes the set"* put the
    # whole MAP vocabulary behind the exemption marker and red nothing.
    # ⚠️ `re materializ` is safe here where it is not evidence of a COUNTED row:
    # the true §13.8 q2 sentence explains the exemption by quoting `SET r += $attrs`
    # under two protections, and never says the replay re-materializes.
    "MAP form writer",
    "attribute set by property map",
    "re materializ",
    # ⛔ THE VERB FORM of "counted", which the participle above does not cover: an
    # exemption ASSERTED to be one of the counted rows — *"`projection.
    # _rebuild_all_replay` counts as a writer of the attribute set"* — was GREEN
    # (`#10200` review, cycle 6, executed).
    "counts as a writer",
    "counts as one",
)

#: The POPULATION spelled out instead of pronominal: `is one of the six writers`.
#: ⛔ A LITERAL CANNOT PIN THIS ONE — the count varies with the table (`six` today,
#: and a stale count is still a claim that the exemption is counted), so the frame
#: is built from `NUMBER_WORDS` plus digits, the way `_COUNT_NUMBER_TOKEN` is,
#: rather than typed. Measured at `6d40e5732` (`#10200` review): the tuple above
#: held the PRONOUN form `is one of them` and the participle `counted`, so
#: *"`projection._rebuild_all_replay` is one of the six writers"* — the exemption
#: asserted to be a member of the six-writer population its own
#: `NOT_AN_ABOUT_WRITER` row exempts it from — was GREEN in words AND in digits,
#: while the pronominal control on the same code path red. The corpus writes the
#: COUNTED direction exactly this way (§13: *"`sdk.supersede_point` IS one of
#: them since `#9417`"*), so the mirror sentence about the exemption is the
#: documents' own idiom, not a paraphrase class this instrument never claimed.
#: ⚠️ Same shape as `DENIAL_PATTERNS`: every entry declares its own probes, and
#: `must_not_see` is the TRUE prose the frame must stay out of. No history escape
#: (this row has NEVER been counted), and `_marker_itself_negated` still applies —
#: *"is not one of the six writers"* denies the counting, it does not assert it.
_POPULATION_COUNT = "|".join(NUMBER_WORDS[index] for index in sorted(NUMBER_WORDS))
EXEMPT_COUNTED_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (
        re.compile(
            rf"\bis\s+one\s+of\s+the\s+(?:{_POPULATION_COUNT}|\d{{1,2}})\s+"
            rf"(?:writers|counted\s+rows?)\b"
        ),
        (
            "`projection._rebuild_all_replay` is one of the six writers.",
            "`projection._rebuild_all_replay` is one of the 6 writers.",
            "`projection._rebuild_all_replay` is one of the 7 counted rows.",
        ),
        # The documents' own COUNTED claim — pronominal, so this frame must not
        # read it. A widening to `is one of …` in general reds it.
        "`sdk.supersede_point` IS one of them since `#9417`.",
    ),
)

#: A DECLARED FALSE POSITIVE COUNT, in the spellings the documents use. ⛔ The
#: number is read from the token NEAREST the phrase, not from the leftmost one in a
#: window (`#10200` review, cycle 2): a window regex took *"The census counts 6
#: writers, 3 by property MAP, and 1 declared false positive"* as SIX, and it took
#: the documents' own *"… 2 ORIGINATING and 4 RE MATERIALIZING — with one declared
#: false positive"* as TWO — regressing a WORD-form spelling that the previous rule
#: read correctly. A stale *"2 declared false positives"* is `#10149` cycle 4's
#: defect in the documents' house spelling, so both the numeral and the word must be
#: read, attached to the count they belong to.
_COUNT_NUMBER_TOKEN = re.compile(
    # ⛔ GROUP 1 is the NUMBER (`_declared_false_positive_counts` reads it), so the
    # alternation is a capturing group, not a `(?:…)`.
    r"\b("
    # ⛔ DERIVED FROM THE TABLE, not typed. The reader stopped at `six` while
    # `NUMBER_WORDS` and `ORDINAL_WORDS` both reach twelve, so a count written in
    # WORDS above six was silently unread — and the failure message for it
    # (*"no corrected passage states how many declared false positives there
    # are"*) names a passage that plainly does state it (`#10200` review, cycle 7,
    # executed: `seven` … `twelve` all returned `[]`). Today `len(
    # NOT_AN_ABOUT_WRITER) == 0` — EMPTY since `#10139` retired its last row —
    # so this is a LATENT fail-open, not a live one, and the width
    # guard one leg up says "the tables already cover it" at 7–12, which is what
    # made the gap invisible instead of loud. Building the alternation from
    # `NUMBER_WORDS` makes the reader unable to be narrower than the table.
    + "|".join(NUMBER_WORDS[index] for index in sorted(NUMBER_WORDS))
    + r"|both|\d{1,2})\b",
    re.I,
)
_DECLARED_FALSE_POSITIVE_PHRASE = re.compile(r"\b(?:false positives?|positives)\b", re.I)
#: How far before the phrase a count may sit, and never across a sentence end.
_COUNT_WINDOW = 61
#: A SENTENCE BOUNDARY, and the ONE definition of it — `_claim_sentences` splits on
#: this and the count window tests it, so the two cannot drift apart. ⛔ The window
#: used to be bounded by `_CLAUSE_BREAKS` ALONE, which holds no full stop, so
#: *"The census counts 6 writers. The declared false positive is X"* was read as
#: stating SIX — a false red on two true sentences, and the exact over-claim this
#: module exists to refuse: the docstring above promised a bound the code did not
#: have (`#10200` review, cycle 15, executed both directions). The period is NOT
#: added to `_CLAUSE_BREAKS`: `_clause_at` and the history escape read that tuple,
#: and a sentence is a WIDER bound than a clause, so the two are kept apart on
#: purpose. The round is recorded in
#: `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md` as `review round 14 (cycle 15)`,
#: and citing it HERE is what makes
#: `test_the_ledger_records_every_review_cycle_the_record_cites` read in both
#: directions: a round the ledger omits is a bound the next lane relies on with
#: nothing describing it, and the leg could not see this one while the comments
#: carried no cycle number.
#: ⚠️ STATED, not implied: `[.!?]` followed by whitespace is the same candidate set
#: comparable sentence segmenters start from (NLTK Punkt's `sent_end_chars`), and
#: requiring the WHITESPACE is what keeps a dotted symbol in the same sentence
#: (`projection._rebuild_all_replay` never splits) — Punkt's own motivating example
#: is that a period alone is not a boundary (*"Mr. Smith"*). What this rule does NOT
#: do is Punkt's next step, suppressing abbreviations: `e.g. ` IS read as a boundary
#: here. That looseness is pre-existing in `_claim_sentences` and is retained rather
#: than half-fixed, because ONE definition of a sentence end is the property this
#: module needs — a second, hand-rolled splitter is how the two drift apart.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _crosses_a_sentence_end(boundaries: list[int], lo: int, hi: int) -> bool:
    """Does a SENTENCE boundary fall strictly between `lo` and `hi`?

    `boundaries` are `_SENTENCE_BOUNDARY` match starts — the first whitespace
    character after the full stop, so a numeral before it and a phrase after it
    are in DIFFERENT sentences and the numeral says nothing about that phrase.
    """
    return any(lo < boundary < hi for boundary in boundaries)
#: "both" IS a count of two — the exact spelling the stale header used.
_COUNT_WORD_VALUES: dict[str, int] = {"both": 2}

#: The invisible site's ORDINAL — the population's width plus one. TYPED rather
#: than derived, it outlived the 5 -> 6 move by a whole review cycle ("A sixth
#: site is in the code today" while the census saw six, so the site outside it
#: was the seventh) and every other leg stayed green.
_INVISIBLE_SITE_ORDINAL = re.compile(
    #: `An?` — not `A`. The ordinal is the population's width PLUS ONE, and at a
    #: seven-writer population that is "eighth", which correct English spells
    #: "An eighth site". A `\bA ` pattern cannot read "An", so the leg would have
    #: demanded ungrammatical prose (or, worse, found ZERO statements and
    #: reported the sentence as missing) exactly when the population grew past
    #: seven — the same class of silent narrowing this constant's comment records.
    r"\bAn? (second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh"
    r"|twelfth) site is in the code today\b"
)


def _census_classes() -> tuple[tuple[str, dict[tuple[str, str], object]], ...]:
    """The three tables, each with the class name its verdict vocabulary keys on."""
    return (
        ("MAP", MAP_WRITERS),
        ("NAMED", RECORDED_WRITERS),
        ("EXEMPT", NOT_AN_ABOUT_WRITER),
    )


def _census_sections() -> list[tuple[str, str]]:
    """The passages that make the population claim, labelled, in document order.

    The SAME `CORRECTED_SECTIONS` the presence leg scopes to — the passages
    that assert which writers exist, which is where a denial of a table's
    verdict does its damage. A sentence elsewhere in either document is out of
    this leg's scope on purpose: the census's claim is scoped to these passages,
    and a leg that scanned the whole tree would match other instruments' prose.
    """
    texts = {"STORAGE": _doc(STORAGE), "EXTRACTOR": _doc(EXTRACTOR)}
    return [
        (label, _section(texts[which], start, end))
        for label, start, end, which in CORRECTED_SECTIONS
    ]


def _claim_sentences(text: str) -> list[str]:
    """The sentences of a passage, flattened, in document order.

    A SENTENCE is the unit a reader acts on, and it is the window the retracted
    sentences of `#10149` lived and died in. ⚠️ Known looseness, stated rather
    than assumed: a markdown TABLE row ends in no full stop, so a run of rows
    arrives as one long "sentence". That weakens the AFFIRMATIVE half where the
    documents are tabular (a verdict stated in one row satisfies the run) and
    does not weaken the DENIAL half, which is the half that caught cycles 3 and
    4 — a denial anywhere in the run still reds it.
    ⛔ The same looseness applies to PROSE, and it is NOT merely a table artefact:
    measured at the `#10200` review (cycle 6), the guarded passages carry sentences
    of ~3.9k chars (§13) and ~780 (§11.1), so class vocabulary printed for ONE
    writer satisfies a DIFFERENT writer named later in the same sentence. A
    CLAUSE-scoped affirmative rule was measured as the alternative and is NOT
    available: 13 of the 17 live statements put the symbol and its verdict in
    different clauses (the verdict usually rides in the appositive or the following
    clause, e.g. "`_fold_entity_linked_reason` (the replay fold re-SETs it …)"), so
    clause-scoping would red most of the true prose this leg exists to protect.
    The bound is therefore the SENTENCE, the DENIAL half is per-occurrence, and the
    asymmetry is recorded here rather than claimed away.
    """
    flat = _flat(text)
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(flat) if part.strip()]


def _clause_at(sentence: str, index: int) -> str:
    """The whole clause CONTAINING `index`, both sides, up to the breaks."""
    lo = max((sentence.rfind(brk, 0, index) for brk in _CLAUSE_BREAKS), default=-1)
    hi = min(
        (pos for pos in (sentence.find(brk, index) for brk in _CLAUSE_BREAKS) if pos != -1),
        default=len(sentence),
    )
    return sentence[lo + 1:hi]


def _stated_markers(sentence: str, markers: tuple[str, ...]) -> list[str]:
    """Markers that POSITIVELY state something in this sentence.

    An occurrence under a negator in its own clause does not count: *"is not a
    re-materializing writer"* names the vocabulary and says the OPPOSITE of it,
    so accepting it as a stated verdict pins nothing (and, on a counted row,
    hides the very denial the second leg exists to catch).
    """
    low = sentence.lower()
    found: list[str] = []
    for marker in markers:
        pattern = re.compile(re.escape(marker.lower()))
        if any(
            not _class_marker_negated(low, match)
            for match in pattern.finditer(low)
        ):
            found.append(marker)
    return found


def _denied_markers(sentence: str, markers: tuple[str, ...]) -> list[str]:
    """Markers under a NEGATOR — in their own clause or behind them — denied."""
    low = sentence.lower()
    found: list[str] = []
    for marker in markers:
        pattern = re.compile(re.escape(marker.lower()))
        if any(
            _class_marker_negated(low, match)
            for match in pattern.finditer(low)
        ):
            found.append(marker)
    return found


def _history_in_clause(low: str, match) -> bool:
    """Is THIS occurrence's clause about HISTORY rather than about HEAD?

    A sentence-wide test excused EVERY denial in the sentence, so one trailing
    reference to an open defect made the retracted cycle-3 sentence green again
    (`#10200` review). A history marker says the state CHANGED, and the clause
    that carries the denial is the clause that says so.
    ⛔ The clause ends at a COORDINATOR as well as at punctuation
    (`_HISTORY_BREAKS`): punctuation alone left *"… before `#9417` and it still
    drops the property"* in ONE clause, so the historical hapt escaped the
    present-tense denial it did not qualify (`#10200` review, cycle 6, executed).
    ⛔ PER OCCURRENCE, not per marker. Quantifying per marker (`any` over the
    marker's occurrences, which is what this function did) let ONE history-marked
    occurrence excuse a second, present-tense denial of the same marker in the
    same sentence — *"`sdk.supersede_point` was a declared false positive before
    `#9417`, and the guard still declares it a false positive at HEAD"* was green,
    and it is exactly the `#10149` class this leg exists for: history kept, the
    denial NOT corrected (`#10200` review, cycle 5, executed).
    """
    lo = max((low.rfind(brk, 0, match.start()) for brk in _HISTORY_BREAKS), default=-1)
    hi = min(
        (pos for pos in (low.find(brk, match.start()) for brk in _HISTORY_BREAKS) if pos != -1),
        default=len(low),
    )
    clause = low[lo + 1:hi]
    return any(history.lower() in clause for history in HISTORY_MARKERS)


def _denies_the_class(sentence: str, marker: str) -> bool:
    """Does this sentence DENY the class with `marker` — decided PER OCCURRENCE?

    ⛔ BOTH escapes are quantified per OCCURRENCE here, because quantifying per
    MARKER is fail-OPEN in both directions and each direction was measured
    (`#10200` review, cycle 5):

      * ONE history-marked occurrence cancelled a second, present-tense denial of
        the same marker in the same sentence (`_history_in_clause`);
      * ONE negated occurrence cancelled a second, un-negated denial — *"`sdk.
        supersede_point` is not exempt from the census, and it is exempt"* was
        green, because `_denied_markers` answers "is ANY occurrence negated?".

    The sibling EXEMPT branch below is fail-CLOSED for the same reason and has
    always been (`any(not _marker_itself_negated(...))`: any un-suppressed
    occurrence flags it). The counted branch had inverted the quantifier; this
    restores the direction that reds.
    """
    low = sentence.lower()
    return any(
        not _history_in_clause(low, match)
        and not _class_marker_negated(low, match)
        for match in re.finditer(re.escape(marker.lower()), low)
    )


def _denies_the_class_by_pattern(sentence: str) -> list[str]:
    """The denials a LITERAL cannot pin — see `DENIAL_PATTERNS`.

    Same two escapes as `_denies_the_class`, applied to each PATTERN occurrence
    for the same reason: a history-marked transfer-bare claim is a true record of
    what changed, and a NEGATED one (*"it does not transfer the edge bare"*) is
    true of a counted writer — the double negation states the class. Both were
    measured on the guard's own vocabulary at `#10200` review cycle 7.
    """
    low = sentence.lower()
    hits: list[str] = []
    for pattern, _must_see, _must_not_see in DENIAL_PATTERNS:
        for match in pattern.finditer(low):
            if not _history_in_clause(low, match) and not _class_marker_negated(
                low, match
            ):
                hits.append(match.group(0))
    return hits

def _exempt_counted_by_pattern(sentence: str) -> list[str]:
    """The counted-claims a LITERAL cannot pin — see `EXEMPT_COUNTED_PATTERNS`.

    The exemption row gets NO history escape (it has never been counted), but the
    marker's own polarity still decides: *"is NOT one of the six writers"* denies
    the counting, so it must not red as if the sentence asserted it. Same reason
    `_marker_itself_negated` guards the literals in `_denied_verdicts`.
    """
    low = sentence.lower()
    hits: list[str] = []
    for pattern, _must_see, _must_not_see in EXEMPT_COUNTED_PATTERNS:
        for match in pattern.finditer(low):
            if not _marker_itself_negated(low, match):
                hits.append(match.group(0))
    return hits


#: A negator that governs THE MARKER ITSELF: the copula/auxiliary immediately
#: before it (optionally with one comma-set parenthetical in between, which is the
#: appositive shape probes 5/9 pin), or a post-positioned `no longer` immediately
#: after it. ⛔ DELIBERATELY NOT the punctuation-window test `_class_marker_negated`
#: uses: a negator anywhere in the comma-run does not negate the marker. Measured
#: (`#10200` review, cycle 2): the window form suppressed the denial in *"Unlike
#: `create_direct_edge`, which cannot name an about edge, `sdk.supersede_point` is
#: a declared false positive"* and in *"`sdk.supersede_point`, which never loses
#: the value, is a declared false positive"* — both RED at the previous head — so
#: that suppression re-opened two sentences rather than refining one. The
#: parenthetical alternation is what keeps `is not, however, an exemption` green,
#: and the ≤3 punctuation-free word-tokens are what keep the qualifier form
#: `is not a DECLARED false positive` green while still refusing to reach across a
#: comma or a conjunction (`#10200` review, cycle 2).
_MARKER_NEGATION_BEFORE_RE = re.compile(
    r"\b(?:not|never|cannot|nor|neither)\b(?:,\s*[a-z][^,]{0,40},)?\s*"
    r"(?:[\w'’\-]+\s+){0,3}$",
    re.I,
)
#: POST-positioned negation of the marker's OWN predicate: `no longer` AFTER
#: the marker, within ≤ THREE word-tokens of it, never across the clause.
#: ⛔ The old form searched the WHOLE CLAUSE in both directions, so a `no longer`
#: belonging to a NEIGHBOURING predicate suppressed a class marker it does not
#: govern — *"An unconditional `SET nr += $attrs` no longer applies to
#: `sdk.supersede_point`; it uses `ON CREATE`"* red as `not SET nr +=`, and that
#: is TRUE prose (it paraphrases §13's own sentence). The clause-wide read is the
#: same "negator of a neighbour" defect the BEFORE half's docstring cites as
#: measured, on the other side (`#10200` review, cycle 6, executed).
_MARKER_NEGATION_AFTER_RE = re.compile(
    r"\A(?:\s*[\w'\u2019\-]+){0,3}\s+no\s+(?:longer|more|further)\b", re.I
)
#: The OBJECT-negation voice of the SAME denial, AFTER the marker: English writes
#: *"it re-materializes **nothing** on an about edge"* and *"it writes **no
#: property**"*, and those are the spellings this file's own `DENIAL_PATTERNS`
#: already recognises for a counted writer (`nothing` / `no <property noun>`).
#: ⛔ The POST half knew only the adverbial `no longer`, so the identical claim put
#: its negation in the OBJECT — and every one of these was measured as a FALSE RED
#: on the EXEMPT row, because `EXEMPT_COUNTED_MARKERS` holds `re materializ` / `MAP
#: form writer` and an un-suppressed occurrence says the exemption is COUNTED:
#:   * *"`projection._rebuild_all_replay` re-materializes nothing on an about
#:     edge."*
#:   * *"`projection._rebuild_all_replay` re-materializes nothing on the rebuild
#:     pass, so it carries no property on an about edge."*
#: The exemption row's OWN vocabulary states the verdict as *"This writer carries
#: NO property on an about edge"*, so denying the counted class in the class's own
#: words was the one spelling that red — the direction that forces an editor to
#: weaken true prose (`#10200` review, cycle 13, executed).
#: ⚠️ Same ≤ THREE word-tokens, same no-punctuation crossing, and it additionally
#: requires that no PREDICATE TERMINATOR sits between the marker and the negator
#: (`_PREDICATE_TERMINATORS`): without that, *"drops the property **and** carries
#: nothing"* would read the second predicate's object as a denial of the first
#: marker and clear a real denial — the neighbour-negator defect this whole scan
#: exists to avoid, on the object side. Bounding negation scope with termination
#: terms is the field's own rule (`#10200` review's external rung: the ConText /
#: NegEx family scopes a cue and terminates it on a conjunction).
_PROPERTY_OBJECT_NEGATION_AFTER_RE = re.compile(
    rf"\A(?:\s*[\w'\u2019\-]+){{0,3}}\s+(?:nothing\b|no\s+{_PROPERTY_NOUN}\b)",
    re.I,
)
#: Words that END the marker's predicate, so a negator PAST one belongs to the next
#: predicate. Coordinators, the subordinators `_HISTORY_BREAKS` already treats as
#: predication boundaries, and the causal `because`/`so` pair these documents use
#: to attach the REBUILD note (`#10139`) to the clause before it.
_PREDICATE_TERMINATORS: tuple[str, ...] = (
    "and",
    "but",
    "or",
    "nor",
    "so",
    "yet",
    "while",
    "whereas",
    "because",
    "then",
)


def _object_negation_after(low: str, match) -> bool:
    """Does an OBJECT negation (`nothing` / `no <property>`) govern this marker?

    Split out of `_marker_itself_negated` so the terminator bound is testable on
    its own: a matched window that carries a `_PREDICATE_TERMINATORS` word is the
    NEXT predicate's negation, not this marker's, so it must not suppress it.
    """
    tail = _PROPERTY_OBJECT_NEGATION_AFTER_RE.match(low[match.end() :])
    if tail is None:
        return False
    window = re.findall(r"[\w'\u2019\-]+", tail.group(0).lower())
    return not any(word in _PREDICATE_TERMINATORS for word in window)


def _marker_itself_negated(low: str, match) -> bool:
    """Is THIS occurrence of a marker denied by the predicate that governs it?

    ⛔ `_QUANTIFIER_NEGATION_RE` is applied HERE, in the LIVE path. It used to sit
    in `_has_class_negator` — a helper the punctuation-window rewrite left
    unreferenced — so its exclusion of *"not the ONLY writer that carries the set
    by property MAP"* and *"not only X but also Y"* never ran: round 2 recorded
    the fix, and the false red it prevents was live on BOTH legs (`#10200`
    review, cycle 5, executed). A rule that only exists in dead code is not a
    rule.
    """
    prefix = _QUANTIFIER_NEGATION_RE.sub(" ", low[: match.start()])
    return (
        bool(_MARKER_NEGATION_BEFORE_RE.search(prefix))
        or bool(_MARKER_NEGATION_AFTER_RE.match(low[match.end() :]))
        # … and the OBJECT voice of the same trailing denial, which the
        # adverbial half above cannot hold (`#10200` review, cycle 13).
        or _object_negation_after(low, match)
    )


def _unstated_verdicts(sections: list[tuple[str, str]]) -> list[str]:
    """Writers a passage names without ever stating which class they are in.

    Split out of the leg so the anti-vacuity probes below run the SAME rule: a
    probe that re-implemented it could pass while the rule it is supposed to
    prove was broken (the shape `_unstated_scoped_facts` already uses here).
    """
    stated: set[str] = set()
    for _label, text in sections:
        for sentence in _claim_sentences(text):
            for role, table in _census_classes():
                for _module, symbol in table:
                    if symbol in sentence and _stated_markers(
                        sentence, CLASS_VERDICT_MARKERS[role]
                    ):
                        stated.add(symbol)
    return [
        f"{role} `{symbol}`"
        for role, table in _census_classes()
        for _module, symbol in table
        if symbol not in stated
    ]


def _denied_verdicts(
    sections: list[tuple[str, str]],
    classes: tuple[tuple[str, dict[tuple[str, str], object]], ...] | None = None,
) -> list[str]:
    """Sentences that contradict the table their named writer lives in.

    Three directions, because the defect has been measured in all three: a
    counted writer described as an exemption (a DENIAL marker), a counted
    writer whose OWN class is NEGATED (*"is not a re-materializing writer"* —
    the paraphrase that was green on both legs at `#10200`'s head), and a
    declared false positive described as counted (`EXEMPT_COUNTED_MARKERS`).
    A denial is excused only when the CLAUSE carrying it is about HISTORY — see
    `HISTORY_MARKERS`; the exemption row gets no such escape.

    ⚠️ STATED LIMITS — four, all measured, and none is an implied promise:
    1. **The denial is attributed to EVERY census key named in the SENTENCE.** The
       class-marker half is predicate-scoped (`_marker_itself_negated`), the
       denial half is not, so a sentence that names TWO keys and denies the class
       of one of them reds for BOTH. Measured at `#10200` review cycle 7: *"`sdk.
       supersede_point` is counted, while `projection._rebuild_all_replay` carries
       no property on an about edge"* reds `supersede_point` as `carries no
       property`. That is the fail-CLOSED direction and it is deliberate: the
       measured retracted sentence is itself multi-key (*"`_rebuild_all_replay`
       and `sdk.supersede_point` are not one of them, so **neither** carries a
       property"*), and a clause-scoped rule would have to resolve the PLURAL
       antecedent (`neither` / `both` / `that row`) to keep it red — no mechanical
       reading of that was found that does not either fail open on the retracted
       shape or depend on an English antecedent heuristic. The cost of being wrong
       in this direction is only a FALSE RED, and its resolution does not weaken
       the prose: state each writer's verdict in its OWN sentence (pinned by
       probe 12). Being wrong the other way would ship the defect.
    2. **A denial that names NO symbol is invisible.** Cycle 4's *"the guard
       declares that row a FALSE POSITIVE rather than counting it"* is the same
       sentence class and no key-anchored rule can reach it (`if symbol not in
       sentence: continue`). It is caught only when it also states a count, which
       the derived-numerals leg reads. Recorded here rather than in the issue that
       scoped the work (`#10149` out-of-scope (b)), because a bound that lives
       only in a tracker comment is invisible to the lane that must rely on it.
    3. **The denial vocabulary is a set of FRAMES AND MARKERS, not a parser.** It
       now reads the inflections and voices that were MEASURED green
       (`DENIAL_PATTERNS`: base/infinitive, object, passive, adjective, and the
       property named as `confidence` — each OBJECT construction in every verb of
       the frame, `carry`/`write`/`set`, because one verb of one construction was
       a measured green path four times over), and the literal list holds the
       third-person spellings the documents write — but a denial phrased with a
       verb, a noun or a construction in NEITHER place stays green. That is the
       same open vocabulary this leg has always had, and it is listed here because
       the limit enumeration above asserts it is COMPLETE: *"two"* was itself a
       coverage claim, and the round that closed the five measured spellings found
       it still said two (`#10200` review, cycle 7). A bound left out of this list
       is a bound a lane will rely on.
    4. **A POST-positioned denial is read on the PREDICATE, so its OBJECT voice is
       bounded too.** `nothing` / `no <property noun>` counts only within ≤ THREE
       word-tokens of the marker AND with no `_PREDICATE_TERMINATORS` word between
       (`and`/`but`/`or`/`nor`/`so`/`yet`/`while`/`whereas`/`because`/`then`), so
       *"drops the property and carries nothing"* keeps its denial — the second
       predicate's negation is not the first marker's. Past that window, or with a
       property noun outside `_PROPERTY_NOUN`, the marker reads as ASSERTED: on a
       counted row that is fail-OPEN — *"it is a re-materializing writer that
       emits nothing at all on the rebuild pass"* is measured GREEN, because
       `nothing` sits four word-tokens past the marker and `emits` is in no
       `DENIAL_PATTERNS` frame — which is why the window is as wide as the one the
       adverbial `no longer` half has always used and no wider, and why the OBJECT
       vocabulary is the same `_PROPERTY_NOUN` `DENIAL_PATTERNS` already reads. The
       other direction is the one that hurt: an exemption stated with the COUNTED
       class's own vocabulary and negated by an object (*"`projection.
       _rebuild_all_replay` re-materializes nothing on an about edge"*) red through
       `EXEMPT_COUNTED_MARKERS` until this bound was read at all — and it is the
       exemption's OWN wording (*"This writer carries NO property on an about
       edge"*), so the only way to clear it was to delete the record of why the row
       is exempt (`#10200` review, cycle 13, executed).
    """
    offenders: list[str] = []
    #: `classes` exists so a PROBE can run this rule against a table this head
    #: does not contain. `NOT_AN_ABOUT_WRITER` is EMPTY since `#10139` (the last
    #: exemption's code was fixed), and the EXEMPT direction of this rule is still
    #: load-bearing — the first returning exemption must be caught — so the probe
    #: INJECTS one row instead of asserting nothing (`#10139` review).
    for role, table in (
        _census_classes() if classes is None else classes
    ):
        for label, text in sections:
            for sentence in _claim_sentences(text):
                low = sentence.lower()
                for _module, symbol in table:
                    if symbol not in sentence:
                        continue
                    if role == "EXEMPT":
                        # ⛔ `_marker_itself_negated`, not the clause-wide
                        # `_denied_markers`: three of these markers SPELL a
                        # negator of their own (`false positive no longer`, `no
                        # longer a declared false positive`, `no longer exempt`),
                        # so a clause-wide `no longer` search suppressed the very
                        # claim it was pinning — every post-negated form stayed
                        # green (`#10200` review, cycle 3, measured).
                        hits = [
                            marker
                            for marker in EXEMPT_COUNTED_MARKERS
                            if marker.lower() in low
                            and any(
                                not _marker_itself_negated(low, match)
                                for match in re.finditer(re.escape(marker.lower()), low)
                            )
                        ]
                        # ⛔ THE POPULATION SPELLED OUT (`is one of the six
                        # writers`) — the same claim in the corpus's own counted
                        # direction spelling, which no literal above can hold
                        # because the count varies. Measured green at `6d40e5732`
                        # while the pronominal control red (`#10200` review).
                        hits += _exempt_counted_by_pattern(sentence)
                    else:
                        # ⛔ PER OCCURRENCE — see `_denies_the_class`. Both
                        # escapes (history, and the marker being itself negated)
                        # are decided there. One excused occurrence used to
                        # cancel a second, present-tense denial of the same
                        # marker in the same sentence (`#10200` review, cycle 5).
                        # A marker that is ITSELF negated still denies the
                        # denial, not the class: *"it is not an exemption, it
                        # re-materializes the set by property MAP"* is TRUE prose
                        # about a counted writer.
                        hits = [
                            marker
                            for marker in DENIAL_MARKERS
                            if _denies_the_class(sentence, marker)
                        ]
                        # ⛔ THE SPELLINGS A LITERAL CANNOT HOLD (the
                        # transfer-bare frame) — `DENIAL_PATTERNS`. Measured
                        # green for the present tense at `#10200` review cycle 7
                        # while the PAST-tense literal was a member.
                        hits += _denies_the_class_by_pattern(sentence)
                        # ⛔ POLARITY: a NEGATED class marker denies the very
                        # class it names, and no `DENIAL_MARKERS` entry spells
                        # that form — `#10149`'s claim stated with the class's
                        # own vocabulary.
                        hits += [
                            f"not {marker}"
                            for marker in _denied_markers(
                                sentence, CLASS_VERDICT_MARKERS[role]
                            )
                        ]
                    if hits:
                        offenders.append(
                            f"{label}: `{symbol}` is a {role} row, and this "
                            f"sentence describes it as {hits[0]!r}: "
                            f"{sentence[:180]!r}"
                        )
    return offenders


def test_every_census_writer_carries_its_class_verdict_where_it_is_named():
    """The class a writer is in must be STATED in a sentence that names it.

    `#10149`: every population leg in this file compares a NUMERAL to a table,
    and a numeral is satisfied by the right token anywhere in the section — so
    the sentence around it is unconstrained. Four review cycles on the five-line
    `#9417` fix were green while `STORAGE-ARCHITECTURE.md` §13 said
    `sdk.supersede_point` *"**drops** the binder's `confidence`"* 130 words
    before counting it as the fourth re-materializing writer. The tables already
    hold the exact keys and the exact class, so the claim is cheap to pin: a
    writer the census counts must be named in a sentence that says HOW it writes
    the property, and an exemption must be named in one that says it writes
    nothing. Probes below run the same rule, so a matcher that stopped matching
    reds here instead of quietly pinning nothing.
    """
    sections = _census_sections()
    for role, table in _census_classes():
        if not table:
            #: ZERO is allowed — and only for EXEMPT, whose emptiness is an
            #: MEASURED state since `#10139` (the last row's code was fixed, so it
            #: moved to `MAP_WRITERS`) and is pinned separately by
            #: `test_the_document_population_counts_match_the_census`
            #: (`len(NOT_AN_ABOUT_WRITER) == 0`, which also forces the documents'
            #: count). The two COUNTED classes must stay non-empty, or this leg's
            #: affirmative half has nothing to pin.
            assert role == "EXEMPT", (
                f"the {role} table is EMPTY — this leg would pass vacuously, "
                "and a population of zero is a measurable claim, not a green"
            )
    # Probe 1: a sentence that NAMES the site and says nothing about its class
    # must be a violation …
    silent = (("probe", "`hosted_backup.restore_graph` is mentioned here."),)
    assert any(
        "restore_graph" in item for item in _unstated_verdicts(list(silent))
    ), (
        "the rule cannot see a MAP-form writer named with no verdict, so the "
        "affirmative half pins nothing"
    )
    # … and Probe 2: the same site with its own write form is NOT a violation.
    stated = (
        (
            "probe",
            "*Re-materializing* writers carry the whole attribute set by property "
            "MAP: `hosted_backup.restore_graph` (`SET r = $p`).",
        ),
    )
    assert not any(
        "restore_graph" in item for item in _unstated_verdicts(list(stated))
    ), (
        "the rule fires on a sentence that DOES state the verdict, so it would "
        "red on a corrected document"
    )
    # … and Probe 3: a NEGATED class statement names the vocabulary and says the
    # OPPOSITE of it, so it must not count as stated (`#10200` review: this exact
    # sentence was green on leg 1 through its own marker).
    negated = (
        ("probe", "`sdk.supersede_point` is not a re-materializing writer."),
    )
    assert any(
        "supersede_point" in item for item in _unstated_verdicts(list(negated))
    ), (
        "a sentence that NEGATES the class counts as stating it, so the one-word "
        "paraphrase of the retracted `#10149` cycle-3 sentence stays green"
    )
    # … and Probe 4: a marker whose spelling `_flat` can never produce is a DEAD
    # marker, and a dead marker pins nothing while reading as if it did
    # (`#10200` review: `re-materializ`, `MAP-form`, `NAMED-form` and
    # `MAP-form writer` each matched 0 flattened occurrences). This probe names
    # the site with the HYPHENATED spelling a document actually writes, so it
    # reds if the marker goes back to a form `_flat` never emits.
    hyphenated = (
        (
            "probe",
            "`hosted_backup.restore_graph` is a RE-MATERIALIZING writer of the "
            "attribute set.",
        ),
    )
    assert not any(
        "restore_graph" in item for item in _unstated_verdicts(list(hyphenated))
    ), (
        "a `RE-MATERIALIZING` sentence is not read as stating the MAP class, so "
        "the marker is written in a spelling `_flat` cannot produce and pins "
        "nothing"
    )
    # … Probe 5: a NEGATOR separated from the class by a COMMA-SET PARENTHETICAL
    # is still a negation of it, so the affirmative half must NOT count it.
    # Round 2 put `,` into the shared break set to stop the history escape
    # spanning a conjunction, and that silently re-opened round 1's polarity
    # finding for ordinary English. The mechanism that closes it is the
    # parenthetical alternation in `_MARKER_NEGATION_BEFORE_RE` — a negator-aware
    # break set (`_NEGATOR_BREAKS`) was tried and DELETED, it belonged to the
    # window form `_marker_itself_negated` replaced. `#10200` review, cycle 5.
    appositive = (
        (
            "probe",
            "`sdk.supersede_point` is not, however, a re-materializing writer.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _unstated_verdicts(list(appositive))
    ), (
        "a comma-separated parenthetical hides the negator from the affirmative "
        "half, so the sentence counts as stating the class it negates"
    )
    # … Probe 6: a POST-positioned negator. English negates from behind as well
    # as from in front (`no longer` appears 14 times in these documents), so a
    # marker followed by one must not count as stating the class.
    post_negated = (
        ("probe", "`sdk.supersede_point` is a re-materializing writer no longer."),
    )
    assert any(
        "supersede_point" in item
        for item in _unstated_verdicts(list(post_negated))
    ), (
        "a negator AFTER the class marker is not read, so the class can be "
        "denied by moving one adverb to the end of the clause"
    )
    # … Probe 7: a comma before a COORDINATING conjunction ends the CONJUNCT, so
    # a negator in the previous conjunct does not make the class unstated in this
    # one. Without this the comma-inside-a-conjunct fix (probe 5) over-blocks: a
    # true MAP sentence in the documents' own idiom reds (`#10200` review).
    coordinated = (
        (
            "probe",
            "`projection._fold_entity_linked_reason` does not originate it, but "
            "`hosted_backup.restore_graph` re-materializes the set by property "
            "MAP.",
        ),
    )
    assert not any(
        "restore_graph" in item
        for item in _unstated_verdicts(list(coordinated))
    ), (
        "a negator in the FIRST conjunct hides the class marker in the SECOND, so "
        "a TRUE coordinated sentence reds as unstated and an editor's cheapest "
        "way out is to weaken it"
    )
    # … and a comma before a SUBORDINATOR is NOT a conjunct boundary: `while` /
    # `whereas` introduce a subordinate clause inside the SAME predicate, so the
    # negator still governs the class and the sentence must stay unstated. Listing
    # them as coordinators cleared this negation on both legs (`#10200` review,
    # cycle 2, measured).
    subordinated = (
        (
            "probe",
            "`sdk.supersede_point` is not, while the census says otherwise, a "
            "re-materializing writer.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _unstated_verdicts(list(subordinated))
    ), (
        "a comma-set SUBORDINATE clause is read as a conjunct boundary, so the "
        "class negation counts as stating the class it negates"
    )
    unstated = _unstated_verdicts(sections)
    assert not unstated, (
        "these census rows are named in a corrected passage that never states "
        f"their class: {unstated} — write the sentence that says how the site "
        "writes the property (or that it writes none), derived from the table"
    )


def test_no_corrected_passage_denies_a_verdict_the_census_counts():
    """The contradiction half, and the one that caught `#10149` cycle 3.

    A sentence that names a COUNTED writer may not call it an exemption or say
    it drops / carries no property, and a sentence that names the EXEMPTION may
    not call it counted. Both were measured green: cycle 3's §13 sentence
    (`sdk.supersede_point` *"is NOT one of them … it **drops** the binder's
    `confidence`"*) and cycle 4's (*"the guard declares that row a FALSE
    POSITIVE rather than counting it"*). The escape hatch is deliberate and
    narrow: a sentence about HISTORY — *"it was a declared false positive before
    `#9417`; it is a counted MAP-form writer now"* — is TRUE, and a leg that
    banned the words outright would force the documents to delete their own
    correction record.

    Measured at the `#10149` head, this leg's FIRST red is on `main`:
    `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 still carried the pre-`#9417`
    sentence — *"**not** `supersede_point` … so neither carries a property on an
    about edge, and the guard declares both census FALSE POSITIVES (`supersede_point`
    even DROPS the property)"* — which the numeral pins never saw, because the
    numerals it states are arithmetically consistent with the tables.
    """
    # Probe 1: the MEASURED cycle-3 sentence must red.
    cycle_3 = (
        (
            "probe",
            "`sdk.supersede_point` is NOT one of them: it **drops** the "
            "binder's `confidence` on the `about*` edge.",
        ),
    )
    assert any(
        "supersede_point" in item for item in _denied_verdicts(list(cycle_3))
    ), (
        "the rule cannot see the cycle-3 sentence, which is the sentence this "
        "leg exists for"
    )
    # Probe 2: the same writer described with HISTORY must NOT red, or the leg
    # forces the documents to delete a true record of what changed.
    history = (
        (
            "probe",
            "`sdk.supersede_point` was a declared FALSE POSITIVE before `#9417`; "
            "it is a counted MAP-form writer now.",
        ),
    )
    assert not any(
        "supersede_point" in item for item in _denied_verdicts(list(history))
    ), (
        "the rule fires on a HISTORY-marked denial, which is true — it would "
        "make the correction record unwritable"
    )
    # Probe 3: the other direction — an exemption described as counted.
    # ⚠️ INJECTED, because `NOT_AN_ABOUT_WRITER` is EMPTY since `#10139`: reading
    # the real table would make this probe assert nothing the moment the last
    # exemption left it, which is exactly when the rule still has to work. The
    # EXEMPT row below is synthetic and named as such; the RULE under probe is the
    # same object the real table runs through.
    injected_exempt = (
        ("MAP", MAP_WRITERS),
        ("NAMED", RECORDED_WRITERS),
        ("EXEMPT", {("probe/module.py", "_a_declared_exemption"): "probe row"}),
    )
    exemption = (
        ("probe", "`_a_declared_exemption` is a counted writer."),
    )
    assert any(
        "_a_declared_exemption" in item
        for item in _denied_verdicts(list(exemption), injected_exempt)
    ), (
        "the rule cannot see an exemption described as counted, so the "
        "contradiction is pinned in one direction only"
    )
    # … and the SAME contradiction in the documents' ordinary contrastive and
    # relative clauses. ⛔ These were green in every spelling while the exemption
    # direction still read polarity through the punctuation WINDOW: an unrelated
    # negator in the first clause cancelled the denial (`#10200` review, cycle 3,
    # measured). They now red through the same predicate-scoped rule the counted
    # direction uses.
    exemption_in_a_clause = (
        (
            "probe",
            "Unlike `create_direct_edge`, which cannot name an about edge, "
            "`_a_declared_exemption` is counted among the six writers.",
        ),
        (
            "probe",
            "`_a_declared_exemption`, which never loses a value, is a "
            "counted MAP form writer.",
        ),
    )
    assert all(
        any(
            "_a_declared_exemption" in item
            for item in _denied_verdicts([sentence], injected_exempt)
        )
        for sentence in exemption_in_a_clause
    ), (
        "an unrelated negator cancels the exemption-is-counted denial, so the "
        "contradiction ships in the documents' own contrastive/relative idiom "
        "and the probe above reads as if the direction were closed"
    )
    # … and the mirror is TRUE prose: a counted writer in a contrastive or
    # relative clause must NOT red as `not property MAP` just because another
    # clause carries a negator. Before the scoping fix these two were RED, and the
    # cheapest way for an editor to clear that is to weaken true prose.
    true_prose_in_a_clause = (
        (
            "probe",
            "Unlike `create_direct_edge`, which cannot name an about edge, "
            "`sdk.supersede_point` re-materializes the set by property MAP.",
        ),
        (
            "probe",
            "`sdk.supersede_point`, which never loses the value, carries the "
            "set by property MAP.",
        ),
        (
            "probe",
            "`projection._fold_entity_linked_reason` does not originate it, "
            "while `hosted_backup.restore_graph` re-materializes the set by "
            "property MAP.",
        ),
    )
    offenders_in_a_clause = [
        offender
        for sentence in true_prose_in_a_clause
        for offender in _denied_verdicts([sentence])
    ]
    assert not offenders_in_a_clause, (
        "a negator in another clause denies a class it does not govern, so TRUE "
        "prose reds and an editor must weaken it — the failure mode rounds 2 and "
        "3 filed at P2: " + repr(offenders_in_a_clause)
    )
    # Probe 4: the NEGATED form of the class — the one-word paraphrase that was
    # green on BOTH legs at `#10200`'s head — must red here. It names no
    # `DENIAL_MARKERS` entry, which is exactly why leg 1's polarity fix alone
    # would not be enough: this is the backstop.
    negated = (
        ("probe", "`sdk.supersede_point` is not a re-materializing writer."),
    )
    assert any(
        "supersede_point" in item for item in _denied_verdicts(list(negated))
    ), (
        "a NEGATED class statement about a counted writer is not seen as a "
        "denial, so `#10149`'s claim restated in the class's own vocabulary "
        "is green"
    )
    # Probe 4b: THE VOICE AND INFLECTION. The literal vocabulary is written in the
    # third person and in SUBJECT position, so the same claim in the base form, in
    # the object voice, in the passive, or with the property named as `confidence`
    # was GREEN — five measured spellings, one defect (`#10200` review, cycle 7).
    # ⛔ This probe is the END-TO-END half; `DENIAL_PATTERNS`' own declared
    # `must_see`/`must_not_see` pairs are the frame-level half.
    other_voices = (
        ("probe", "`sdk.supersede_point` is said to drop the property."),
        ("probe", "`sdk.supersede_point` carries no `confidence` on an about edge."),
        ("probe", "The guard exempts `sdk.supersede_point` from the census."),
        (
            "probe",
            "No property is written by `sdk.supersede_point` on an about edge.",
        ),
        ("probe", "`sdk.supersede_point` leaves its about edge property free."),
    )
    missed_voices = [
        sentence
        for sentence in other_voices
        if not any("supersede_point" in item for item in _denied_verdicts([sentence]))
    ]
    assert not missed_voices, (
        "the rule reads only the third-person, subject-position spelling, so the "
        "same denial in the base form / object voice / passive / under the "
        "property's own name is green — the owner's own wording (*said to drop*) "
        "is in that set: " + repr([sentence[:80] for _l, sentence in missed_voices])
    )
    # … and the mirror, as in every other probe above: the NEGATED form of the same
    # predicates is TRUE prose about a counted writer and must NOT red, or the only
    # way to clear the red is to weaken the record.
    negated_voices = (
        (
            "probe",
            "`sdk.supersede_point` does not drop the property: it re-materializes "
            "it by property MAP.",
        ),
        (
            "probe",
            "`sdk.supersede_point` is not the only writer that carries the set by "
            "property MAP.",
        ),
    )
    false_reds = [
        offender
        for sentence in negated_voices
        for offender in _denied_verdicts([sentence])
    ]
    assert not false_reds, (
        "the widened vocabulary reds TRUE prose (`does not drop …`, `not the ONLY "
        "writer`), so an editor clearing it must weaken the record: "
        + repr(false_reds)
    )
    # Probe 5: the MEASURED cycle-3 denial with a bare open-defect reference
    # appended. `#10139` is NOT a history marker (`#10200` review): it excused
    # every denial in the sentence, and it is the reference the corrected
    # paragraph already ends with — so restoring the retracted sentence beside
    # the rebuild note walked through the gate that exists for it.
    # ⚠️ The denial here is the ONE in the `#10139` clause. The full measured
    # sentence also carries *"is NOT one of them"* in an earlier clause, and that
    # clause reds either way — a probe built on it stays green with `#10139`
    # restored, i.e. it pins nothing.
    retracted_with_10139 = (
        (
            "probe",
            "`sdk.supersede_point` drops the binder's `confidence` on the "
            "`about*` edge (`#10139`).",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(retracted_with_10139))
    ), (
        "a bare `#10139` excuses the retracted sentence, so the escape is wider "
        "than the recorded `before`/`until #9417` and the leg loses its grip on "
        "the sentence class it was opened for"
    )
    # Probe 6: the escape is COMMA-blind if `_CLAUSE_BREAKS` has no comma, and a
    # comma-joined run is then ONE clause — so history kept with the denial NOT
    # corrected was green (`#10200` review).
    half_corrected = (
        (
            "probe",
            "`sdk.supersede_point` was a declared false positive before "
            "`#9417`, and it still drops the property on an about edge.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(half_corrected))
    ), (
        "a comma-joined present-tense denial is excused by a history marker "
        "earlier in the same run, so the escape spans a conjunction and a "
        "half-corrected sentence stays green"
    )
    # Probe 7: an exemption ASSERTED to carry the set by property map. The one
    # member that said this was a dead `_flat` spelling, so only the literal word
    # `counted` red the contradiction (`#10200` review).
    # ⚠️ Same INJECTION as probe 3 — the real table is EMPTY since `#10139`, and
    # the EXEMPT direction of this rule must stay proven rather than silently idle.
    exemption_as_map = (
        (
            "probe",
            "`_a_declared_exemption` writes the attribute set by "
            "property MAP.",
        ),
    )
    assert any(
        "_a_declared_exemption" in item
        for item in _denied_verdicts(list(exemption_as_map), injected_exempt)
    ), (
        "an exemption said to write the attribute set by property MAP is not "
        "seen, so the contradiction is caught only when the sentence uses the "
        "word 'counted'"
    )
    # … and the TRUE exemption sentence must stay writable, or the leg forbids
    # the documents from stating why the row is exempt at all.
    exemption_true = (
        (
            "probe",
            "`_a_declared_exemption` is a declared false positive: "
            "its about transfer is a bare `MERGE`, so it carries no property on "
            "an about edge.",
        ),
    )
    assert not any(
        "_a_declared_exemption" in item
        for item in _denied_verdicts(list(exemption_true), injected_exempt)
    ), (
        "the leg reds a TRUE statement of the exemption, so an editor clearing "
        "it would have to delete the record of why the row is exempt"
    )
    # Probe 8: a QUANTIFIER negation is not a class negation. ``is not the only
    # writer that carries the set by property MAP`` AFFIRMS the writer, and
    # `STORAGE-ARCHITECTURE.md` §13 already uses the idiom ("is not the only `SET`
    # in the function"); reading it as a denial reds TRUE prose, and the cheapest
    # way to clear that red is to weaken the prose (`#10200` review).
    quantifier = (
        (
            "probe",
            "`cache_projection.relation_statement` is not the only writer that "
            "carries the set by property MAP.",
        ),
    )
    assert not any(
        "relation_statement" in item
        for item in _denied_verdicts(list(quantifier))
    ), (
        "a quantifier negation ('not the only') is read as a class negation, so "
        "a true affirmative sentence reds and an editor would have to weaken it"
    )
    assert not any(
        "relation_statement" in item
        for item in _unstated_verdicts(list(quantifier))
    ), (
        "a quantifier negation makes the class count as UNSTATED, so the leg "
        "reds a sentence that does state the class"
    )
    # Probe 9: a NEGATOR separated from the class by a COMMA-SET PARENTHETICAL is
    # still a negation of it. Round 2 put `,` into the shared break set to stop the
    # history escape spanning a conjunction, and that silently re-opened round 1's
    # polarity finding for ordinary English (`#10200` review: green on BOTH legs).
    # Closed by the parenthetical alternation in `_MARKER_NEGATION_BEFORE_RE`; the
    # negator-aware break set (`_NEGATOR_BREAKS`) was deleted with the window form.
    appositive = (
        (
            "probe",
            "`sdk.supersede_point` is not, however, a re-materializing writer.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(appositive))
    ), (
        "a comma-separated parenthetical hides the negator, so an ordinary "
        "English class negation states the class instead of denying it"
    )
    # Probe 10: a POST-positioned negator. English negates from behind as well as
    # from in front, and these documents write `no longer` 14 times; reading
    # polarity only backward left the trailing form stating the class on both legs
    # (`#10200` review), including the exemption direction.
    post_negated = (
        ("probe", "`sdk.supersede_point` is a re-materializing writer no longer."),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(post_negated))
    ), (
        "a negator AFTER the class marker is not read, so the verdict can be "
        "denied by moving one adverb to the end of the clause"
    )
    # ⛔ THE POST-NEGATED FORMS, pinned WITHOUT a neighbour to carry the red. The
    # probe that used to sit here (*"… is a declared false positive no longer: it
    # re-materializes the set"*) reported this direction as closed while its red
    # came from `re materializ` in the trailing clause — measured: removing the
    # post-negation mechanism left it green, and the bare sentence below was green
    # in every spelling (`#10200` review, cycle 3). Each form is now its own
    # sentence, so no other marker can produce the verdict.
    post_exemption = (
        (
            "probe",
            "`_a_declared_exemption` is a declared false positive no "
            "longer.",
        ),
        (
            "probe",
            "`_a_declared_exemption` is no longer a declared false "
            "positive.",
        ),
        ("probe", "`_a_declared_exemption` is no longer exempt."),
    )
    assert all(
        any(
            "_a_declared_exemption" in item
            for item in _denied_verdicts([sentence], injected_exempt)
        )
        for sentence in post_exemption
    ), (
        "the exemption may be called counted by moving the negation after the "
        "exemption marker (`is a declared false positive no longer`, `is no "
        "longer a declared false positive`, `is no longer exempt`), which is the "
        "direction the recorded decision forbids"
    )
    # Probe 11: a comma before a COORDINATING conjunction separates CONJUNCTS, so
    # a negator in the first does not deny the class stated in the second. This is
    # the other half of probe 9: the appositive comma must stay inside the scope,
    # the coordinated comma must not (`#10200` review).
    coordinated = (
        (
            "probe",
            "`projection._fold_entity_linked_reason` does not originate it, and "
            "`hosted_backup.restore_graph` carries the set by property MAP.",
        ),
    )
    assert not any(
        "restore_graph" in item for item in _denied_verdicts(list(coordinated))
    ), (
        "a negator in the FIRST conjunct denies the class the SECOND conjunct "
        "states, so a true sentence in the documents' own coordinated idiom reds "
        "and an editor would have to weaken true prose to clear it"
    )
    # … and the mirror: a comma before a SUBORDINATOR does not end the predicate,
    # so the class negation in *"is not, while the census says otherwise, a
    # re-materializing writer"* must still deny the class (`#10200` review, cycle
    # 2: listing `while`/`whereas` as coordinators cleared it on BOTH legs).
    subordinated = (
        (
            "probe",
            "`sdk.supersede_point` is not, while the census says otherwise, a "
            "re-materializing writer.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(subordinated))
    ), (
        "a comma-set SUBORDINATE clause is read as a conjunct boundary, so a "
        "class negation is green on both legs"
    )
    # Probe 12: the OWNER'S OWN NOUN AND VERB. The recorded decision says a
    # counted writer "may not be called **an exemption** or said to **drop** /
    # carry no property", and neither `an exemption` nor the non-articled
    # property verb was in any tuple: only the spelling the documents happened to
    # use (`false positive`, `drops the`) was pinned, so the exact contradiction
    # the decision names was green (`#10200` review, measured).
    decision_noun = (
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP, and "
            "it is an exemption from the census.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(decision_noun))
    ), (
        "a counted writer called 'an exemption' is not seen, so the decision's "
        "own noun is unpinned and the documents can be edited back into the "
        "contradiction it forbids"
    )
    decision_verb = (
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP and "
            "then drops confidence on the about edge.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(decision_verb))
    ), (
        "the non-articled property verb ('drops confidence') is not seen, so a "
        "counted writer can be denied in the decision's own words"
    )
    # … and the NEGATION of that noun is TRUE prose about a counted writer, so
    # the marker must not fire on it. ⛔ This is why the trigger is the DEMONSTRATIVE
    # AND PREDICATE PHRASES (`an exemption`, `the exemption`, `is exempt`) rather
    # than a bare `exempt` substring: the documents' own *"make both exemptions
    # true instead of merely exempting them"* sentence sits in a run that names
    # counted writers, and it contains `exemptions` / `exempting` — the non-denial
    # spellings a stem match cannot tell from a denial (`#10200` review, cycle 5,
    # executed: the bare stem red that very sentence).
    not_an_exemption = (
        (
            "probe",
            "`sdk.supersede_point` is not an exemption: it re-materializes the "
            "set by property MAP.",
        ),
    )
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(list(not_an_exemption))
    ), (
        "'is not an exemption' is treated as a denial of the class, so TRUE "
        "prose about a counted writer reds and the cheapest way out is to "
        "delete the correction"
    )
    # … but the suppression must be scoped to the MARKER'S OWN predicate. ⛔ An
    # unrelated negator earlier in the run does not deny the marker: a contrastive
    # or relative clause is exactly how these documents write, and the
    # punctuation-window form of the guard re-opened both of these on leg 2 while
    # the previous head caught them RED (`#10200` review, cycle 2, measured).
    unrelated_negators = (
        (
            "probe",
            "Unlike `create_direct_edge`, which cannot name an about edge, "
            "`sdk.supersede_point` is a declared false positive.",
        ),
        (
            "probe",
            "`sdk.supersede_point`, which never loses the value, is a declared "
            "false positive.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in unrelated_negators
    ), (
        "a negator in an UNRELATED clause suppresses the denial, so a retracted "
        "claim restored in a contrastive or relative clause walks through the "
        "gate one commit after the class was closed"
    )
    # … and the inflected forms of the decision's noun, not just the articled one
    # (`is exempt`, `the exemption`). `#10200` review cycle 2 measured `an
    # exemption` alone leaving all three green; cycle 5 measured that the stem
    # `exempt` which replaced it reds 0 sentences only because the documents'
    # `exempt*` spellings are all NON-denial (`exemptions`, `exempting`) — i.e. it
    # was an artifact, and it false-red the documents' own §13 sentence (the
    # `not_only_affirms` and `documents_own_exemption_prose` probes below pin that
    # direction). The three denials are pinned as PHRASES.
    inflected_exemption = (
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP; it "
            "is exempt from the census.",
        ),
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP; it "
            "is the exemption in §13.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in inflected_exemption
    ), (
        "a counted writer called 'exempt' / 'the exemption' is not seen, so the "
        "decision's own noun is pinned only in its indefinite-articled form"
    )
    # … and the QUALIFIER form of the same true sentence stays green: the "never"
    # in *"which never loses the value"* is not a denial of the marker it happens
    # to precede in the comma-run.
    qualified_negation = (
        (
            "probe",
            "`sdk.supersede_point` is not a DECLARED false positive: it "
            "re-materializes the set by property MAP.",
        ),
    )
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(list(qualified_negation))
    ), (
        "a negated marker with a qualifier between ('is not a DECLARED false "
        "positive') is read as a denial, so TRUE prose reds"
    )
    # Probe 13: the VERB form of `carries no property`. The decision names
    # "carry no property"; the documents' spelling is articled, so the verb form
    # was the reachable green path.
    verb_form = (
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP but "
            "carries nothing on an about edge.",
        ),
        (
            "probe",
            "`sdk.supersede_point` re-materializes the set by property MAP and "
            "writes nothing on an about edge.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in verb_form
    ), (
        "a counted writer said to carry/write NOTHING on an about edge is not "
        "seen, so the decision's 'carry no property' is pinned only in its "
        "articled spelling"
    )
    # ⛔ Probe 14 (`#10200` review, cycle 5): the HISTORY escape is quantified per
    # OCCURRENCE, not per marker. One history-marked occurrence used to excuse a
    # second, present-tense denial of the SAME marker in the SAME sentence — the
    # exact `#10149` class (history kept, the denial NOT corrected), green.
    history_then_present_denial = (
        (
            "probe",
            "`sdk.supersede_point` was a declared false positive before `#9417`, "
            "and the guard still declares it a false positive at HEAD.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in history_then_present_denial
    ), (
        "a history-marked occurrence excuses a SECOND, present-tense denial of the "
        "same marker in the same sentence, so keeping the `#9417` record while "
        "leaving the retracted claim standing is green"
    )
    # … and the same asymmetry on the OTHER escape: one NEGATED occurrence used to
    # cancel an un-negated denial of the same marker. The sibling EXEMPT branch has
    # always been fail-closed (`any(not ...)`); the counted branch had inverted it.
    # ⚠️ The marker must be one that genuinely occurs TWICE, one negated and one
    # not. `is exempt` does NOT qualify — *"is not exempt"* does not contain it —
    # so a probe written with it stays green under the per-marker mutation and
    # pins nothing (measured: cycle 5's first draft of this probe did exactly that).
    # `false positive` does, which is why it is the one used here.
    negated_then_real_denial = (
        (
            "probe",
            "`sdk.supersede_point` is not a false positive, and it is a false "
            "positive.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in negated_then_real_denial
    ), (
        "one negated occurrence cancels a second, un-negated denial of the same "
        "marker, so 'it is not a false positive …, and it is a false positive' is "
        "green"
    )
    # ⛔ Probe 15 (`#10200` review, cycle 5): a QUANTIFIER negation AFFIRMS the
    # class, so it must not read as a class negation. This is the round-2 rule that
    # lived only in dead code (`_QUANTIFIER_NEGATION_RE` inside the unreferenced
    # `_has_class_negator`), so the fix was recorded and the false red was live on
    # BOTH legs. The probe REDS if the exclusion stops being reached.
    not_only_affirms = (
        (
            "probe",
            "`cache_projection.relation_statement` is not only a MAP form writer "
            "but the canonical one.",
        ),
        (
            "probe",
            "`cache_projection.relation_statement` is not the only writer that "
            "carries the set by property MAP.",
        ),
    )
    assert not any(
        "relation_statement" in item
        for item in _denied_verdicts(list(not_only_affirms))
    ), (
        "a QUANTIFIER negation ('not only X but also Y', 'not the only writer that "
        "carries it by property MAP') is read as a class negation, so the "
        "documents' own affirmative idiom reds and the cheapest way out is to "
        "weaken true prose"
    )
    # ⛔ Probe 16 (`#10200` review, cycle 5): the documents' OWN sentence about the
    # exemptions is TRUE, and must stay green even when it names a counted writer.
    # The bare stem `exempt` red it (it matches `exemptions` / `exempting`), which
    # is why the denial vocabulary is the predicate and articled PHRASES.
    documents_own_exemption_prose = (
        (
            "probe",
            "`sdk.supersede_point` counts now, and the guard pins the shapes that "
            "make both exemptions true instead of merely exempting them.",
        ),
    )
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(list(documents_own_exemption_prose))
    ), (
        "the documents' own TRUE sentence about the exemptions reds when it names "
        "a counted writer, so the corpus's own wording is the trigger and the red "
        "can only be cleared by deleting the correction"
    )
    # ⛔ Probe 17 (`#10200` review, cycle 6): the NEGATED-AUXILIARY spellings of
    # the owner's denial. The tuple listed only the AFFIRMATIVE phrasings, so the
    # same sentence with the writer as SUBJECT escaped verbatim.
    negated_auxiliary = (
        (
            "probe",
            "`sdk.supersede_point` does not carry the binder's `confidence` on an "
            "about edge.",
        ),
        (
            "probe",
            "`hosted_backup.restore_graph` itself never writes the attribute set "
            "at all.",
        ),
    )
    assert all(
        any(symbol in item for item in _denied_verdicts([sentence]))
        for sentence, symbol in zip(
            negated_auxiliary, ("supersede_point", "restore_graph"), strict=True
        )
    ), (
        "a counted writer denied with a NEGATED AUXILIARY ('does not carry the",
        " …', 'never writes the …') is green, so the owner's decision is escaped "
        "by one word — the measured `#10200` cycle-6 finding"
    )
    # ⛔ Probe 18 (`#10200` review, cycle 6): the HISTORY escape's clause ended at
    # punctuation only, so a comma-less coordination put the history marker and a
    # present-tense denial in ONE clause and the denial was excused.
    comma_less_history = (
        (
            "probe",
            "`sdk.supersede_point` was a declared false positive before `#9417` "
            "and it still drops the property on an about edge.",
        ),
        (
            "probe",
            "`sdk.supersede_point` was a declared false positive before `#9417` "
            "but it carries no property on an about edge.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in comma_less_history
    ), (
        "the history escape spans a COMMA-LESS coordination, so the cycle-3 "
        "denial is one omitted comma away from green — reds on `_HISTORY_BREAKS`"
    )
    # … and the TRUE history sentence stays green under the same coordinator rule
    # (a fix that reds the correction record is worse than the fail-open).
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(
            [
                (
                    "probe",
                    "`sdk.supersede_point` was a declared false positive before "
                    "`#9417` but it is a counted MAP-form writer now.",
                )
            ]
        )
    ), (
        "the coordinator boundary red a TRUE history sentence, so the correction "
        "record cannot be written"
    )
    # ⛔ Probe 19 (`#10200` review, cycle 6): the EXEMPT row's VERB form. Only the
    # participle `counted` was listed, so *"counts as a writer of the attribute
    # set"* — the exemption asserted to be one of the counted rows — was green.
    assert any(
        "_a_declared_exemption" in item
        for item in _denied_verdicts(
            [
                (
                    "probe",
                    "`_a_declared_exemption` counts as a writer of the "
                    "attribute set.",
                )
            ],
            injected_exempt,
        )
    ), (
        "the VERB form of 'called counted' is not seen for the exemption row, so "
        "the contradiction ships in the documents' verb voice"
    )
    # ⛔ Probe 20 (`#10200` review, cycle 6): TRUE prose must stay green when a
    # NEIGHBOUR predicate carries the negator — `not merely` / `not just` are
    # QUANTIFIER negations, and the clause-wide post-negation scan used to reach
    # across a semicolon to a neighbour's `no longer`.
    true_prose_neighbours = (
        (
            "probe",
            "`sdk.supersede_point` is not merely a MAP form writer but the "
            "canonical one.",
        ),
        (
            "probe",
            "`sdk.supersede_point` is not just a MAP form writer but the "
            "canonical one.",
        ),
        (
            "probe",
            "An unconditional `SET nr += $attrs` no longer applies to `sdk."
            "supersede_point`; it uses `ON CREATE`.",
        ),
    )
    neighbour_reds = [
        offender
        for sentence in true_prose_neighbours
        for offender in _denied_verdicts([sentence])
    ]
    assert not neighbour_reds, (
        "TRUE prose reds because a quantifier negation or a NEIGHBOUR's `no "
        "longer` is read as a class denial — the failure mode that pushes an "
        "editor to weaken correct sentences: " + repr(neighbour_reds)
    )
    # ⛔ Probe 21 (`#10200` review, cycle 7): the DENIAL VOCABULARY pinned one
    # TENSE and one VERB of the transfer denial, so the other tense and the other
    # verb of the SAME claim were green. Measured: `no longer carries the` was a
    # member and `no longer writes the` was not (the absent-by-design entry even
    # claimed the determiner form was "recorded above" when the only determiner
    # form recorded was the CARRY verb), and `transferred bare` was a past-tense
    # literal while the PRESENT tense — the one a live denial is written in —
    # matched nothing.
    other_verb_and_tense = (
        (
            "probe",
            "`sdk.supersede_point` no longer writes the attribute set on an about "
            "edge.",
        ),
        (
            "probe",
            "`sdk.supersede_point` transfers the about edge bare.",
        ),
        (
            "probe",
            "`sdk.supersede_point` transfers it bare.",
        ),
    )
    assert all(
        any("supersede_point" in item for item in _denied_verdicts([sentence]))
        for sentence in other_verb_and_tense
    ), (
        "the transfer denial is pinned in ONE verb or ONE tense, so the same "
        "claim reds when written `carries` and is green when written `writes` "
        "(or reds in the past tense and is green in the present) — measured at "
        "the `#10200` cycle-7 head"
    )
    # … and the DOUBLE NEGATION of the frame is TRUE of a counted writer: it says
    # the edge is transferred WITH properties, which is what `#9417` made true.
    # `DENIAL_PATTERNS` is guarded by the same two escapes as the literals.
    negated_transfer_bare = (
        (
            "probe",
            "`sdk.supersede_point` does not transfer the about edge bare.",
        ),
    )
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(list(negated_transfer_bare))
    ), (
        "a NEGATED transfer-bare statement is read as the denial it denies, so "
        "TRUE prose about the `#9417` carry reds"
    )
    # ⛔ Probe 22 (`#10200` review, cycle 7): the denial is attributed to EVERY
    # census key named in the SENTENCE (the class-marker half is predicate-scoped,
    # the denial half is not). That is fail-CLOSED on purpose — see the STATED
    # LIMITS in `_denied_verdicts` — and the measured retracted sentence is itself
    # multi-key, so a clause-scoped rule would first have to resolve its PLURAL
    # antecedent (`neither` / `both`) to keep it red. This probe pins BOTH ends:
    # the retracted multi-key shape stays red, and the documented resolution (one
    # writer's verdict per sentence) stays green, so the bound costs a split and
    # never a deleted contrast.
    retracted_multi_key = (
        (
            "probe",
            "`projection._rebuild_all_replay` and `sdk.supersede_point` are not "
            "one of them, so neither carries a property on an about edge, and the "
            "guard declares both census FALSE POSITIVES (`supersede_point` even "
            "DROPS the property, `#9417`).",
        ),
    )
    assert any(
        "supersede_point" in item for item in _denied_verdicts(list(retracted_multi_key))
    ), (
        "the retracted §13.8 q2 sentence is no longer read as a denial, so the "
        "sentence this whole leg exists for is green again"
    )
    contrast_split_into_sentences = (
        (
            "probe",
            "`sdk.supersede_point` is a counted MAP form writer. "
            "`projection._rebuild_all_replay` carries no property on an about "
            "edge.",
        ),
    )
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(list(contrast_split_into_sentences))
    ), (
        "stating the two verdicts in separate SENTENCES still reds the counted "
        "writer, so the documented resolution of the sentence-wide bound (split "
        "the contrast) does not work and an editor's only way out is to delete "
        "it — the failure mode this leg exists to prevent"
    )

    # ⛔ Probe 23 (`#10200` review, cycle 9): the OBJECT-NEGATION frame was bound
    # to ONE verb pair (`carry|carries`) while the file's own vocabulary used the
    # siblings — `writes no property` is how §13 states the EXEMPTION's verdict,
    # and `does not write the` / `never writes the` / `write nothing` were already
    # denials. Swap the subject of the documents' own sentence to a counted writer
    # and the whole module stayed GREEN, measured at `6d40e5732`: three reachable
    # green paths, one alternation away from the frame that already existed.
    other_object_negations = (
        (
            "probe",
            "`sdk.supersede_point` writes no property on an about edge.",
        ),
        (
            "probe",
            "`sdk.supersede_point` writes no `confidence` on an about edge.",
        ),
        (
            "probe",
            "`sdk.supersede_point` sets no property on an about edge.",
        ),
    )
    missed_object_negations = [
        sentence
        for sentence in other_object_negations
        if not any("supersede_point" in item for item in _denied_verdicts([sentence]))
    ]
    assert not missed_object_negations, (
        "the object-negation frame reads ONE verb of the construction, so the "
        "denial is green whenever it is written `writes` or `sets` instead of "
        "`carries` — and the EXEMPT row's own spelling (`writes no property`) is "
        "in that set: " + repr([s[:70] for _l, s in missed_object_negations])
    )
    # … and the widening must not read the bare prefix: the OBJECT is what
    # separates a denial from the record, so a verb plus `no` naming something
    # that is not a property/attribute is not this frame's business.
    assert not any(
        "supersede_point" in item
        for item in _denied_verdicts(
            [
                (
                    "probe",
                    "`sdk.supersede_point` writes no line numbers into §13.",
                )
            ]
        )
    ), (
        "the widened frame reads the bare `writes no` prefix, so TRUE prose that "
        "names a counted writer and negates something OTHER than a property reds "
        "and an editor's only way out is to weaken it"
    )
    # ⛔ Probe 24 (`#10200` review, cycle 9): "the exemption may not be called
    # counted" was pinned on the PRONOUN (`is one of them`) and the participle
    # (`counted`), so the same claim with the POPULATION SPELLED OUT was green —
    # in words AND in digits (`EXEMPT_COUNTED_PATTERNS` carries that frame).
    #: ⚠️ INJECTED row (same reason as probe 3): the real EXEMPT table is EMPTY
    #: since `#10139`, and this direction still has to be shown to work.
    population_spelled_out = (
        ("probe", "`_a_declared_exemption` is one of the six writers."),
        ("probe", "`_a_declared_exemption` is one of the 6 writers."),
    )
    missed_population = [
        sentence
        for sentence in population_spelled_out
        if not any(
            "_a_declared_exemption" in item
            for item in _denied_verdicts([sentence], injected_exempt)
        )
    ]
    assert not missed_population, (
        "the exemption asserted to be one of the six writers is green whenever the "
        "population is spelled out instead of pronominal, so the recorded "
        "decision's second direction is pinned only in one determiner: "
        + repr([s[:70] for _l, s in missed_population])
    )
    # … and the TRUE counted claim (pronominal, about a COUNTER writer) and the
    # NEGATED population claim stay green: polarity still decides, and a red on
    # the documents' own §13 sentence would force its deletion.
    population_true_prose = (
        ("probe", "`sdk.supersede_point` IS one of them since `#9417`."),
        (
            "probe",
            "`projection._rebuild_all_replay` is not one of the six writers.",
        ),
    )
    population_reds = [
        offender
        for sentence in population_true_prose
        for offender in _denied_verdicts([sentence])
    ]
    assert not population_reds, (
        "the population frame reds TRUE prose — the documents' own pronominal "
        "counted claim, or its negated form — so the correction record cannot be "
        "written: " + repr(population_reds)
    )

    # ⛔ Probe 25 (`#10200` review, cycle 13): the POST half of the polarity read
    # knew only the ADVERBIAL `no longer`, so the OBJECT voice of the same denial —
    # `re-materializes nothing`, `writes no property` — was read as an ASSERTED
    # class marker. That is the spelling this file's own `DENIAL_PATTERNS`
    # recognises for a counted writer, and the spelling the exemption row's verdict
    # is written in (*"This writer carries NO property on an about edge"*), so the
    # TRUE statement of the exemption red through `EXEMPT_COUNTED_MARKERS` (which
    # holds `re materializ` / `MAP form writer`). Measured at `3a20e63e9` by running
    # this leg's own functions: the first two sentences below RED there and stay
    # green now.
    # ⛔ The third is TRUE prose that must stay green — and it is NOT this leg's
    # WIDTH control, which is what it used to be labelled (`#10200` review, cycle
    # 14). It names the exemption but carries NO counted marker, and on an EXEMPT
    # row both hit sources guard with `not _marker_itself_negated(...)`, so the
    # frame is a SUPPRESSION only there — nothing reaches it on this sentence and
    # the sentence is green at EVERY width of it, measured at `fac4728cb` with the
    # frame as written AND with it widened to a bare `no \w+`. The width is
    # asserted against the frame itself, below, where it is actually consulted.
    #: ⚠️ INJECTED row (same reason as probe 3) — these are TRUE-prose pins and
    #: the sentence class they pin is the EXEMPT one, which the real table no
    #: longer has. A MAP row may not be described as an exemption; a declared row
    #: may, so the synthetic row is what keeps this direction honest.
    object_negated_true_prose = (
        (
            "probe",
            "`_a_declared_exemption` re-materializes nothing on an about "
            "edge.",
        ),
        (
            "probe",
            "`_a_declared_exemption` re-materializes nothing on the "
            "rebuild pass, so it carries no property on an about edge.",
        ),
        # … and §13's own `no recorded version read at all`: TRUE prose naming the
        # exemption and negating something that is NOT a property, which the frame
        # must stay out of. ⚠️ A TRUE-prose pin, NOT a width control — a widening
        # of the frame cannot red it (see the probe's own note above), so the
        # width is asserted on the frame itself in the leg below.
        (
            "probe",
            "`_a_declared_exemption` carries no recorded version read at "
            "all.",
        ),
    )
    object_negated_reds = [
        offender
        for sentence in object_negated_true_prose
        for offender in _denied_verdicts([sentence], injected_exempt)
    ]
    assert not object_negated_reds, (
        "an OBJECT negation (`re-materializes nothing`, `writes no property`) is "
        "read as an ASSERTED class, so TRUE prose reds — and it is the exemption's "
        "OWN stated wording, so an editor's only way out is to delete the record "
        "of why the row is exempt: " + repr(object_negated_reds)
    )
    # ⛔ THE WIDTH, asserted WHERE IT IS CONSULTED (`#10200` review, cycle 14).
    # The entry above carried a WIDTH-control label it could not earn: on the
    # EXEMPT branch the frame is a SUPPRESSION only (both hit sources guard with
    # `not _marker_itself_negated(...)`), so for a sentence like this one a
    # widening can never RED anything — and an EXEMPT-row sentence carrying no
    # counted marker never reaches the frame at all.
    # A control that cannot fire is the exact class this file exists to stop — a
    # written assurance that something is measured when nothing is — so the width
    # is pinned on `_PROPERTY_OBJECT_NEGATION_AFTER_RE` against a marker's OWN
    # tail, in BOTH directions, and the negative half is proved able to fail by
    # running that same tail through the bare-object widening a later lane would
    # otherwise adopt unmeasured.
    _width_inside = _flat(
        "`projection._rebuild_all_replay` re-materializes no property on an "
        "about edge."
    )
    _width_inside_low = _width_inside.lower()
    _width_inside_marker = re.search(r"re materializ", _width_inside_low)
    assert _width_inside_marker is not None, (
        "the width control's own class marker is gone, so it measures nothing: "
        + repr(_width_inside)
    )
    assert _object_negation_after(_width_inside_low, _width_inside_marker), (
        "the object-negation frame no longer reads `no property` behind a class "
        "marker, so the width control below is measuring a frame that reads "
        "nothing at all and its negative half is vacuous"
    )
    _width_outside = _flat(
        "`projection._rebuild_all_replay` re-materializes no recorded version "
        "read at all."
    )
    _width_outside_low = _width_outside.lower()
    _width_outside_marker = re.search(r"re materializ", _width_outside_low)
    assert _width_outside_marker is not None, (
        "the width control's own class marker is gone, so it measures nothing: "
        + repr(_width_outside)
    )
    assert not _object_negation_after(_width_outside_low, _width_outside_marker), (
        "the object-negation frame reads a `no <noun>` whose noun is OUTSIDE "
        "`_PROPERTY_NOUN`, so its width is `any trailing no` and TRUE prose that "
        "negates something other than a property is read as a denial — the "
        "exemption's own wording is then the only way out, and it is the record "
        "of why the row is exempt"
    )
    # The pattern is a PLAIN raw string (no `f` prefix, single braces). As an
    # f-string it carried a doubled `{{0,3}}` with no placeholder — `ruff` F541,
    # which is what `agent-infra-ci / lint` fails on. The un-doubling is
    # load-bearing, not tidy: with the `f` dropped and the braces left doubled,
    # `{{0,3}}` reads as a literal `{`+`}` and the assertion below fails on this
    # sentence — so the spelling is self-pinning.
    _bare_object_frame = re.compile(
        r"\A(?:\s*[\w'\u2019\-]+){0,3}\s+(?:nothing\b|no\s+\w+\b)", re.I
    )
    assert _bare_object_frame.match(_width_outside_low[_width_outside_marker.end():]), (
        "the width control above CANNOT FAIL: the tail it refuses matches the "
        "bare-object widening no better than the frame as written, so it pins "
        "the width in neither direction and a later lane that widens "
        "`_PROPERTY_OBJECT_NEGATION_AFTER_RE` is told a control will catch it "
        "when nothing will"
    )
    # … and the LEDGER must state the count this leg MEASURES, because the two
    # records in this head contradicted each other: round 12's changelog entry
    # claimed `three` TRUE-prose sentences red at `3a20e63e9` while this probe's
    # own comment, written one commit later, said the third is green there too.
    # The changelog was the false one — `3a20e63e9` carries no `re-materializes
    # nothing` probe at all (these sentences are `a6a241eb0`'s), and the third has
    # no marker — so `two` is the measured count. The count is DERIVED here (an
    # entry is a live control when it carries a marker the EXEMPT branch would
    # itself count) so a new entry has to update the ledger rather than quietly
    # falsify it (`#10200` review, cycle 14).
    _live_object_negation_probes = [
        sentence
        for _label, sentence in object_negated_true_prose
        if any(
            marker.lower() in _flat(sentence).lower()
            for marker in EXEMPT_COUNTED_MARKERS
        )
    ]
    _count_words = ("one", "two", "three", "four", "five", "six")
    assert 1 <= len(_live_object_negation_probes) <= len(_count_words), (
        "the live object-negation probes outgrew this pin's vocabulary "
        f"({len(_live_object_negation_probes)}) — extend `_count_words` rather "
        "than dropping the ledger check"
    )
    _measured_count_word = _count_words[len(_live_object_negation_probes) - 1]
    _ledger = _doc(STORAGE_CHANGELOG)
    _engine_entry_start = _ledger.find("`#10149` review round 12 (cycle 13)")
    assert _engine_entry_start >= 0, (
        "the round-12 (cycle 13) entry of `STORAGE-ARCHITECTURE-CHANGELOG.md` is "
        "gone — the count this leg pins moved with it, so it is UNMEASURED now"
    )
    _cycle14_start = _ledger.find(
        "`#10149` review round 13 (cycle 14)", _engine_entry_start
    )
    _engine_entry = _flat(
        _ledger[
            _engine_entry_start
            : _cycle14_start if _cycle14_start > 0 else len(_ledger)
        ]
    )
    _ledger_counts = [
        word
        for word in _count_words
        if f"{word} TRUE prose sentences" in _engine_entry
    ]
    assert _ledger_counts == [_measured_count_word], (
        "the cycle-13 changelog entry's count of probe 25's non-vacuous sentences "
        f"is FALSE: {len(_live_object_negation_probes)} of them carry a marker "
        "the EXEMPT branch counts and can therefore red, and the ledger says "
        f"{_ledger_counts!r} — a reader is told a control exists that does not. "
        "Entry: " + repr(_engine_entry[:400])
    )
    # … and the mirror, which is what the same widening must not cost: an object
    # negation about a COUNTED writer DENIES its class, so it must red. At
    # `3a20e63e9` this was GREEN — the post half could not see the negator, so
    # `re materializ` counted as STATED and `#10149`'s own claim restated as
    # *"it re-materializes nothing"* shipped unread.
    counted_object_negation = (
        (
            "probe",
            "`sdk.supersede_point` re-materializes nothing on an about edge.",
        ),
    )
    assert any(
        "supersede_point" in item
        for item in _denied_verdicts(list(counted_object_negation))
    ), (
        "an object negation AFTER a class marker is invisible, so the class counts "
        "as stated and `#10149`'s claim restated as 'it re-materializes nothing' "
        "is green on both legs"
    )
    # … and the negation must stay scoped to the marker's OWN predicate: a
    # coordinator ENDS it (`_PREDICATE_TERMINATORS`), so the next predicate's
    # object negation cannot suppress a real denial. ⛔ The sentences below were
    # chosen by MUTATION, not by eye: the obvious control (*"drops the property and
    # carries nothing"*) stays green either way, because `carries nothing` is a
    # `DENIAL_MARKERS` entry that reds on its own — it measures nothing about this
    # bound. These two go GREEN the moment the terminator check is removed, i.e.
    # they pin the bound; their trailing negation is no denial of its own.
    coordinated_object_negation = (
        (
            "probe",
            "`sdk.supersede_point` transfers the about edge bare and nothing "
            "else.",
        ),
        (
            "probe",
            "`sdk.supersede_point` transfers it bare but retains no property map.",
        ),
    )
    suppressed_denials = [
        sentence
        for sentence in coordinated_object_negation
        if not any("supersede_point" in item for item in _denied_verdicts([sentence]))
    ]
    assert not suppressed_denials, (
        "an object negation belonging to the NEXT predicate suppresses the denial "
        "before it, so a real denial of a counted writer is green one coordinator "
        "away: " + repr([s[:80] for _l, s in suppressed_denials])
    )

    offenders = _denied_verdicts(_census_sections())
    assert not offenders, (
        "a corrected passage contradicts a census table:\n  "
        + "\n  ".join(offenders)
        + "\nFix the SENTENCE to match the table (or, if the table is wrong, "
        "re-derive the table and every numeral derived from it) — a denial "
        "about history must say so (`before`/`until #9417`)"
    )
    # ⛔ The MODULE DOCSTRING is this file's own statement of what it reads and
    # what it does not, and the defect class lives IN it: `904c13141` fixed by
    # hand a paragraph that attached `supersede_point`'s mechanism to
    # `_rebuild_all_replay`, and NOTHING pinned it — restored verbatim, the suite
    # stayed 40/40 green (`#10200` review). The corrected documents are scoped to
    # `CORRECTED_SECTIONS`; this file's own record is scoped to ITSELF, which is
    # the same rule the comment layer got in round 11 and the assertion messages
    # got through that round's file-wide half.
    docstring_offenders = _denied_verdicts([("module docstring", __doc__ or "")])
    assert not docstring_offenders, (
        "this module's own docstring contradicts a census table:\n  "
        + "\n  ".join(docstring_offenders)
        + "\nThe docstring is the census's statement of what it reads, so a "
        "denial there is the same defect as one in a corrected passage"
    )
    # … and the docstring half must FIRE on the paragraph `904c13141` deleted, or
    # the assertion above is decoration. The pre-hand-fix sentence is kept here
    # verbatim (`2355c2e7f`, the head before it), which is this file's own
    # convention for a probe that must not stop matching.
    #: ⚠️ The SYMBOL is swapped for the injected exemption row (`#10139` emptied
    #: the real table); the SENTENCE is otherwise verbatim, because what this
    #: probe pins is the frame ("is NO longer an exemption"), not the site.
    pre_hand_fix = (
        "`_a_declared_exemption` — which earlier revisions of this "
        "file recorded as a re-materializer — is NOT: Its `SET r += $attrs` is "
        "on the 2a-DIRECT leg, whose rows are discovered by "
        "`MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t)`, so `rtype` is "
        "provably `IMPL|NAND`. The about edge is transferred by a separate "
        "`MERGE (new)-[nr:{rel}]->(t)` that **does** carry the old edge's "
        "properties (`SET nr += $attrs`, whitelisted to the "
        "`RelationUpsert.attrs` vocabulary), so since #9417 this writer DOES "
        "write `confidence` on an about edge and is no longer an exemption "
        "here."
    )
    assert any(
        "_a_declared_exemption" in item
        for item in _denied_verdicts(
            [("module docstring", pre_hand_fix)], injected_exempt
        )
    ), (
        "the docstring half cannot see the paragraph `904c13141` deleted — the "
        "sentence that carried `supersede_point`'s mechanism onto "
        "`_rebuild_all_replay` — so the hand fix it made is unpinned and the "
        "next editor can restore it green"
    )


def _declared_false_positive_counts(
    sections: list[tuple[str, str]],
) -> list[tuple[str, int]]:
    """Every place a passage STATES how many declared false positives there are.

    Split out of the leg so the probe can run the same rule on a synthetic
    passage (see `_unstated_scoped_facts` for why).
    """
    numbers = {word: number for number, word in NUMBER_WORDS.items()}
    numbers.update(_COUNT_WORD_VALUES)
    found: list[tuple[str, int]] = []
    for label, text in sections:
        flat = _flat(text)
        tokens = [
            (match.start(), match.group(1).lower())
            for match in _COUNT_NUMBER_TOKEN.finditer(flat)
        ]
        #: Every sentence end in this passage, in the SAME flattened coordinate
        #: space as the tokens and the phrases.
        sentence_ends = [match.start() for match in _SENTENCE_BOUNDARY.finditer(flat)]
        for phrase in _DECLARED_FALSE_POSITIVE_PHRASE.finditer(flat):
            before = [
                (position, token)
                for position, token in tokens
                if position < phrase.start()
                and phrase.start() - position <= _COUNT_WINDOW
                # ⛔ ONE CLAUSE, not just one sentence (`#10200` review, cycle 3):
                # `;`, `:`, `—` and `,` all separate the counts the guarded
                # paragraph states, and a numeral from an earlier clause was still
                # being attached to this phrase — *"6 writers, 3 by property MAP:
                # the declared false positive is X"* read THREE.
                and not any(
                    brk in flat[position : phrase.start()] for brk in _CLAUSE_BREAKS
                )
                # ⛔ … AND one SENTENCE (`#10200` review, executed both ways): a
                # `_CLAUSE_BREAKS` member is not the only boundary — it holds no
                # full stop, so the numeral in *"The census counts 6 writers. The
                # declared false positive is X"* was attached to a phrase in the
                # NEXT sentence and a true sentence pair red as a wrong count.
                and not _crosses_a_sentence_end(
                    sentence_ends, position, phrase.start()
                )
            ]
            if before:
                position, token = max(before)
                number = int(token) if token.isdigit() else numbers[token]
                label_span = flat[position:phrase.end()]
            else:
                # ⛔ A count stated AFTER the phrase is the same statement in the
                # documents' other voice, and the rule read only BACKWARD — *"The
                # declared false positives are two in total"* produced NOTHING, so
                # silence and an uncovered spelling were indistinguishable and a
                # stale count in that form shipped green (`#10200` review, cycle 6,
                # executed). Same clause and the same window, read forward.
                after = [
                    (position, token)
                    for position, token in tokens
                    if position >= phrase.end()
                    and position - phrase.end() <= _COUNT_WINDOW
                    and not any(
                        brk in flat[phrase.end() : position] for brk in _CLAUSE_BREAKS
                    )
                    # … and not across a sentence end in THIS direction either —
                    # the mirror of the `before` bound, which was missing for the
                    # same reason (`#10200` review, executed): *"The declared false
                    # positive is X. 6 writers are counted"* read SIX.
                    and not _crosses_a_sentence_end(
                        sentence_ends, phrase.end(), position
                    )
                ]
                # … and *"the ONLY declared false positive"* states a count of ONE
                # by construction. Absent from both documents today; it is read so
                # that writing it cannot silently state nothing.
                only = " only " in f" {_clause_at(flat.lower(), phrase.start())} "
                if after:
                    position, token = min(after)
                    number = int(token) if token.isdigit() else numbers[token]
                    label_span = flat[phrase.start() : position + len(token)]
                elif only:
                    position = phrase.start()
                    number = 1
                    label_span = flat[position:phrase.end()]
                else:
                    continue
            # A statement about HISTORY is TRUE and stays writable, the same escape
            # the denial leg carries (`#10200` review, cycle 3: the count leg had no
            # history notion, so *"before `#9417` it held two declared false
            # positives"* red).
            if any(
                history.lower() in _clause_at(flat.lower(), position)
                for history in HISTORY_MARKERS
            ):
                continue
            found.append((f"{label}: {label_span[:120]!r}", number))
    return found


def _invisible_site_ordinals(
    sections: list[tuple[str, str]],
) -> list[tuple[str, int]]:
    """Every place a passage states the ORDINAL of the site outside the census."""
    numbers = {word: number for number, word in ORDINAL_WORDS.items()}
    return [
        (f"{label}: {match.group(0)!r}", numbers[match.group(1).lower()])
        for label, text in sections
        for match in _INVISIBLE_SITE_ORDINAL.finditer(_flat(text))
    ]


def test_the_population_numerals_are_derived_from_the_census_tables():
    """The numerals that were TYPED rather than derived, now derived.

    `#10149` cycle 4 found two of these on a green suite. The declared
    false-positive count moved from two to one when `#9417` made
    `supersede_point` a counted writer, and a header still said *"two MORE … are
    the declared false positives"*; the invisible site's ordinal stayed at
    *"A sixth site is in the code today"* after the population BECAME six, so
    the site outside the census was the seventh. Neither was invisible to a
    human reader, only to CI — the digits elsewhere were right.

    Both are cheap to derive, so they are derived: the count from
    `NOT_AN_ABOUT_WRITER`, the ordinal from the population's width plus one.
    Covered spellings are stated rather than implied — the count as a cardinal
    word (or "both"), or a numeral, in the SAME CLAUSE and the SAME SENTENCE as
    "false positive(s)",
    matched to the token NEAREST the phrase; the ordinal only in the *"A <ordinal>
    site is in the code today"* sentence. The count is read in digits as well as
    in words because the guarded paragraph writes every other count as a numeral,
    and a count stated about HISTORY stays writable in the decision's own
    vocabulary (`HISTORY_MARKERS`: `before`/`until #9417`) — a past-tense phrasing
    outside that list (*"used to hold"*) is NOT covered, because widening it there
    would widen the denial escape too. A different spelling is outside this rule,
    which is why the rule says which one it reads.
    """
    sections = _census_sections()
    exemptions = len(NOT_AN_ABOUT_WRITER)
    population = len(RECORDED_WRITERS) + len(MAP_WRITERS)
    invisible = population + 1
    if invisible not in ORDINAL_WORDS or exemptions not in NUMBER_WORDS:
        pytest.fail(
            f"the population is {population} with {exemptions} declared false "
            "positive(s), and this leg's word tables stop at "
            f"{max(NUMBER_WORDS)}/{max(ORDINAL_WORDS)} — extend "
            "`NUMBER_WORDS`/`ORDINAL_WORDS` rather than dropping the derivation"
        )

    # Non-vacuity for the count rule: the stale spelling must be seen as TWO.
    probe = (("probe", "the guard declares both census FALSE POSITIVES"),)
    assert _declared_false_positive_counts(list(probe)) == [
        ("probe: 'both census FALSE POSITIVES'", 2)
    ], (
        "the count rule cannot read 'both census FALSE POSITIVES', so it would "
        "not have red on the stale header it exists for"
    )
    # … and the DIGIT spelling of the same count, which is how the guarded
    # paragraph writes every other count in it (`#10200` review: a word-only rule
    # left *"2 declared false positives"* green in the documents' own house
    # spelling).
    digit_probe = (("probe", "the guard declares 2 declared FALSE POSITIVES"),)
    assert _declared_false_positive_counts(list(digit_probe)) == [
        ("probe: '2 declared FALSE POSITIVES'", 2)
    ], (
        "the count rule cannot read a NUMERAL count ('2 declared false "
        "positives'), so the stale count it exists for stays green whenever it "
        "is written in digits"
    )
    # … and a count written in WORDS above SIX. The reader stopped at `six` while
    # `NUMBER_WORDS`/`ORDINAL_WORDS` reach twelve, so a population that grows to
    # seven exemptions would report *"no corrected passage states how many"* about
    # a passage that plainly does — and the width guard above would say the tables
    # already cover it (`#10200` review, cycle 7, executed: `seven` … `twelve`
    # all returned `[]`).
    wide_word_probe = (("probe", "the census holds seven declared FALSE POSITIVES"),)
    assert _declared_false_positive_counts(list(wide_word_probe)) == [
        ("probe: 'seven declared FALSE POSITIVES'", 7)
    ], (
        "the count rule stops at 'six' in words while the word tables reach "
        "twelve, so a count above six is silently unread and the failure message "
        "blames a passage that does state it"
    )
    # … and the count is the token NEAREST the phrase, not the leftmost number in
    # a window: the guarded paragraph states three OTHER counts as numerals before
    # this one, so a window rule reads the population (`#10200` review, cycle 2,
    # measured: it read 6 and, on the documents' own wording, 2).
    crowded = (
        (
            "probe",
            "The census counts 6 writers, 3 by property MAP, and 1 declared "
            "false positive.",
        ),
        (
            "probe",
            "The measured population is 6 writers — 2 ORIGINATING and 4 "
            "RE MATERIALIZING — with one declared false positive.",
        ),
    )
    assert [
        count for _where, count in _declared_false_positive_counts(list(crowded))
    ] == [1, 1], (
        "a sentence that states other counts as numerals is read as a false-"
        "positive count of one of THEM, so the correct count reds as wrong"
    )
    # … and the numeral must be in the PHRASE'S OWN CLAUSE, not merely its own
    # sentence: the guarded paragraph separates counts with `;`, `:` and `—` as
    # well as `.` (`#10200` review, cycle 3, measured: 4/4/3 were read out of the
    # clause before).
    other_clause = (
        (
            "probe",
            "6 writers, 3 by property MAP: the declared false positive is "
            "`projection._rebuild_all_replay`.",
        ),
    )
    assert _declared_false_positive_counts(list(other_clause)) == [], (
        "a numeral from an EARLIER clause is attached to `declared false "
        "positive`, so a sentence that states no count is read as stating one"
    )
    # ⛔ … and the boundary a `_CLAUSE_BREAKS` member does NOT provide: a SENTENCE
    # end. That tuple holds no full stop, and nothing else here split sentences
    # first, so the numeral in the sentence BEFORE the phrase was attached to it
    # — a false red on two true sentences, both directions (`#10200` review,
    # cycle 15, executed before the fix: `before` returned 6, `after` returned
    # 6). The comment above already claimed `.` was among the separators; it was
    # not.
    next_sentence = (
        (
            "probe",
            "The census counts 6 writers. The declared false positive is "
            "`projection._rebuild_all_replay`.",
        ),
    )
    assert _declared_false_positive_counts(list(next_sentence)) == [], (
        "a numeral from the PREVIOUS sentence is attached to `declared false "
        "positive`, so a passage that states no count for it reds as a wrong "
        f"count ({_declared_false_positive_counts(list(next_sentence))}) — the "
        "cheapest way to clear that red is to reword a TRUE sentence"
    )
    prior_sentence = (
        (
            "probe",
            "The declared false positive is `projection._rebuild_all_replay`. "
            "6 writers are counted.",
        ),
    )
    assert _declared_false_positive_counts(list(prior_sentence)) == [], (
        "a numeral from the FOLLOWING sentence is attached to `declared false "
        "positive` — the forward branch needs the same sentence bound, or a "
        "stale count in the next sentence reads as this sentence's own "
        f"({_declared_false_positive_counts(list(prior_sentence))})"
    )
    # … and the bound is the SENTENCE, not the full stop's position: a count and
    # its phrase separated by a dotted SYMBOL in the same sentence must still be
    # read, or the fix reds the documents' own house spelling.
    dotted_symbol = (
        (
            "probe",
            "the census holds one declared false positive at "
            "`projection._rebuild_all_replay`",
        ),
    )
    assert [
        count for _where, count in _declared_false_positive_counts(list(dotted_symbol))
    ] == [1], (
        "a dotted symbol inside the SAME sentence is read as a sentence end, so "
        "the count it belongs to is lost and true prose reds as silence"
    )
    # … and a HISTORY-marked count is TRUE and must stay writable, the same escape
    # the denial leg carries (`#10200` review, cycle 3: the count leg had none).
    history_count = (
        (
            "probe",
            "Before `#9417` the census held two declared false positives; it "
            "holds one declared false positive now.",
        ),
    )
    assert [
        count
        for _where, count in _declared_false_positive_counts(list(history_count))
    ] == [1], (
        "a history-marked count is read as a present-tense claim, so the "
        "correction record the decision blesses reds for being true"
    )
    # ⛔ Probe (`#10200` review, cycle 6): a count stated AFTER the phrase, and the
    # `only` form, were both read as NOTHING — so silence and an uncovered spelling
    # were indistinguishable and a stale count in either shape shipped green.
    after_phrase = (
        ("probe", "The declared false positives are two in total."),
        ("probe", "The declared FALSE POSITIVES number 2."),
    )
    assert [
        count for _where, count in _declared_false_positive_counts(list(after_phrase))
    ] == [2, 2], (
        "a count written AFTER the phrase is not read at all, so the rule is "
        "BACKWARD-only and a stale count in the forward voice pins nothing"
    )
    only_phrase = (
        (
            "probe",
            "the guard declares the only declared false positive as "
            "`projection._rebuild_all_replay`",
        ),
    )
    assert [
        count for _where, count in _declared_false_positive_counts(list(only_phrase))
    ] == [1], (
        "the `only …` form states a count of one by construction and is read as "
        "silence, so writing it would state nothing"
    )
    counts = _declared_false_positive_counts(sections)
    assert counts, (
        "no corrected passage states how many declared false positives there "
        "are — the count is a claim about `NOT_AN_ABOUT_WRITER`, and silence is "
        "not a match"
    )
    wrong_counts = [
        (where, stated) for where, stated in counts if stated != exemptions
    ]
    assert not wrong_counts, (
        f"a corrected passage states the declared false-positive count as "
        f"{wrong_counts}, but `NOT_AN_ABOUT_WRITER` holds {exemptions} — the "
        "count is derived, so write it that way"
    )

    # Non-vacuity for the ordinal rule: the MEASURED stale ordinal (six, at a
    # population of six) must be seen and must be the WRONG one.
    probe = (("probe", "A sixth site is in the code today and outside it"),)
    seen = _invisible_site_ordinals(list(probe))
    assert len(seen) == 1, (
        "the ordinal rule does not read 'A sixth site is in the code today' as "
        f"exactly one statement ({seen}) — the probe below would prove nothing"
    )
    stale = seen[0][1]
    assert stale != invisible, (
        "the ordinal rule reads the measured stale sentence as the CORRECT "
        f"ordinal ({stale}), so the leg below pins nothing"
    )
    ordinals = _invisible_site_ordinals(sections)
    assert len(ordinals) == 1, (
        "the invisible site's ordinal is stated "
        f"{len(ordinals)} times in the corrected passages ({ordinals}) — the "
        "sentence is how the census bounds itself, so it must appear exactly "
        "once, derived"
    )
    (where, stated), = ordinals
    assert stated == invisible, (
        f"{where} states the ordinal for a population of {stated - 1}, but the "
        f"census holds {population} writers, so the site OUTSIDE it is the "
        f"{ORDINAL_WORDS[invisible]} — derive the ordinal from the tables"
    )


#: A hyphen BETWEEN word characters. `_flat` normalises exactly this to a space,
#: so a marker containing one can NEVER match a flattened sentence — it is dead
#: on arrival while reading as if it matched. Four such markers shipped in this
#: PR's first three forms (`MAP-form`, `NAMED-form`, `re-materializ`,
#: `MAP-form writer`) and each was found by a review, not by the suite.
_HYPHENATED_MARKER = re.compile(r"\w-\w")

#: Markers that are DELIBERATELY absent from today's documents: a retraction
#: tripwire pins a sentence MEASURED false at some head, and a contradiction
#: vocabulary member exists to catch a sentence nobody has written yet. Being
#: absent is their job, so the liveness half below exempts them BY NAME — stated
#: rather than inferred, because "absent from the docs" is otherwise exactly the
#: dead-marker symptom.
MARKERS_ABSENT_BY_DESIGN: dict[str, str] = {
    "originates": "a future spelling of the NAMED class; live today as `originating`",
    "neither carries": "retraction tripwire — the `#9417`-era denial, absent by design",
    "transferred bare": (
        "retraction tripwire — the PAST-tense form of the pre-`#9417` denial, "
        "absent by design. ⚠️ It is a literal, so it pins ONE tense; the present "
        "tense (`transfers it bare`, `transfers the about edge bare`) is read by "
        "`DENIAL_PATTERNS`, which is a frame rather than a spelling list because "
        "the object between the verb and `bare` varies (`#10200` review, cycle 7: "
        "the present tense was GREEN while this literal was a member)"
    ),
  "writes no property": (
        "retraction tripwire — the EXEMPT class's positive denial. Absent by "
        "design since `#10139` emptied `NOT_AN_ABOUT_WRITER`: no census row is an "
        "exemption any more, so an honest document states the COUNT (zero) "
        "instead of calling a row property-free. The marker stays in "
        "`CLASS_VERDICT_MARKERS[\"EXEMPT\"]` and in `MARKERS_ABSENT_BY_DESIGN` "
        "because the first returning exemption must be nameable, and its "
        "liveness returns with that row — the DENIAL direction is still probed "
        "synthetically on every suite"
    ),
    #: `carries no property` is the SAME claim with the other verb; both left the "
    #: documents for the same reason (`#10139`), and the DENIAL frame over it is "
    #: still exercised by the probes in
    #: `test_no_corrected_passage_denies_a_verdict_the_census_counts`.
    "carries no property": (
        "the verb-swapped spelling of `writes no property` — absent for the SAME "
        "reason (`#10139` emptied the exemption table), and kept in "
        "`DENIAL_MARKERS`/`CLASS_VERDICT_MARKERS` so the frame still catches a "
        "counted writer described this way"
    ),
    "carries nothing": (
        "contradiction vocabulary — the verb form of `carries no property`, "
        "which is the spelling the documents use today"
    ),
    "drops confidence": (
        "contradiction vocabulary — the documents write the articled form "
        "(`drops the binder's confidence`), so the non-articled verb is absent "
        "by design until someone writes it"
    ),
    "false positive no longer": (
        "contradiction vocabulary — the post-negated form of 'called counted', "
        "absent until someone writes it"
    ),
    "no longer a declared false positive": (
        "contradiction vocabulary — the same claim in pre-negated form, absent "
        "until someone writes it"
    ),
    "no longer exempt": (
        "contradiction vocabulary — the exemption denied rather than replaced, "
        "absent until someone writes it"
    ),
    "is exempt": (
        "contradiction vocabulary — the predicate form of 'called an exemption'. "
        "Absent from both documents until someone writes it; it replaced the bare "
        "stem `exempt`, whose only actual `exempt*` matches were the NON-denial "
        "spellings `exempts` / `exemptions` / `exempting` (`#10200` review, "
        "cycle 5)"
    ),
    "is exempted": (
        "contradiction vocabulary — the same claim in its past-participle form. "
        "Absent until someone writes it, and NOT redundant with `is exempt`: the "
        "post-positioned-negation read anchors at the match END, so "
        "'is exempted from the census no longer' is only seen by this spelling"
    ),
    "an exemption": (
        "contradiction vocabulary — the indefinite-articled form the owner's "
        "decision quotes verbatim ('called an exemption'). Absent from both "
        "documents until someone writes it"
    ),
    "the exemption": (
        "contradiction vocabulary — the definite-articled form. Absent from both "
        "documents until someone writes it; the documents write `both exemptions` "
        "and `merely exempting` instead, neither of which is a denial"
    ),
    "counts as a writer": (
        "contradiction vocabulary — the VERB form of 'called counted', absent from "
        "both documents until someone writes it (`#10200` review, cycle 6)"
    ),
    "counts as one": (
        "contradiction vocabulary — the pronoun form of the same claim, absent "
        "until someone writes it (`#10200` review, cycle 6)"
    ),
    "does not carry the": (
        "contradiction vocabulary — the determiner form of 'carry no property', "
        "absent from both documents until someone writes it (the `#10149` denial "
        "is written affirmatively, `drops the`). Live in `DENIAL_MARKERS` so the "
        "paraphrase reds the moment it is written"
    ),
    "does not carry a": (
        "contradiction vocabulary — the indefinite-determiner form of the same "
        "denial, absent until someone writes it (`#10200` review, cycle 6)"
    ),
    "does not write the": (
        "contradiction vocabulary — the determiner form of 'writes nothing', "
        "absent until someone writes it (`#10200` review, cycle 6)"
    ),
    "never carries the": (
        "contradiction vocabulary — the NEVER-auxiliary determiner form, absent "
        "until someone writes it (`#10200` review, cycle 6)"
    ),
    "never writes the": (
        "contradiction vocabulary — the determiner form of the NEVER auxiliary, "
        "absent until someone writes it; it is the spelling that would have "
        "caught the measured `restore_graph` paraphrase (`#10200` review, cycle 6)"
    ),
    "no longer carries the": (
        "contradiction vocabulary — the post-negated auxiliary determiner form, "
        "absent until someone writes it (`#10200` review, cycle 6)"
    ),
    "no longer writes the": (
        "contradiction vocabulary — the WRITE-verb form of `no longer carries "
        "the`. ⚠️ Its absence was a recorded FALSE CLAIM until `#10200` review "
        "cycle 7: this entry's neighbour below says the bare form is covered \"by "
        "its determiner form recorded above\", and the only determiner form "
        "recorded was the CARRY verb — so the gate survived swapping `carries` "
        "for `writes`, measured green. Live in `DENIAL_MARKERS` now, so the "
        "paraphrase reds the moment it is written"
    ),
}


def test_no_marker_is_written_in_a_spelling_the_flattener_cannot_produce():
    """Every marker must be a spelling `_flat` can produce, and be pinned.

    `#10200` review: `EXEMPT_COUNTED_MARKERS` held `"MAP-form writer"`, matched
    with `marker.lower() in low` against a FLATTENED sentence, and `_flat` turns
    `-` between word characters into a SPACE — measured **0** occurrences in
    either flattened document, and 0 is what it could ever be. An exemption
    asserted to *"write the attribute set by property MAP"* was green, and the
    same was true of `"MAP-form"`, `"NAMED-form"` and `"re-materializ"`. A DEAD
    marker is worse than a missing one: it reads as coverage, and the sentence
    class it was supposed to catch stays green for ever.

    Two halves, because either alone is useless:
      1. NO marker may contain a hyphen between word characters (a spelling the
         flattener cannot emit), and
      2. every marker must otherwise be either PRESENT in the flattened documents
         or listed in `MARKERS_ABSENT_BY_DESIGN` with a stated reason — so a
         marker that dies by drift (a document reworded away from it) reds here
         instead of pinning nothing.
    """
    families: dict[str, tuple[str, ...]] = {
        "CLASS_VERDICT_MARKERS": tuple(
            marker for markers in CLASS_VERDICT_MARKERS.values() for marker in markers
        ),
        "DENIAL_MARKERS": DENIAL_MARKERS,
        "HISTORY_MARKERS": HISTORY_MARKERS,
        "EXEMPT_COUNTED_MARKERS": EXEMPT_COUNTED_MARKERS,
    }
    # Probe: the pattern must see the four spellings this leg exists for …
    unhittable = ("MAP-form", "NAMED-form", "re-materializ", "MAP-form writer")
    for spelling in unhittable:
        assert _HYPHENATED_MARKER.search(spelling), (
            f"the dead-marker pattern does not see {spelling!r}, so it cannot "
            "red on the defect it was written for"
        )
    # … and EVERY `DENIAL_PATTERNS` entry must SEE the denial it was written for,
    # or it is the same DEAD instrument as a hyphenated literal — plus one
    # sentence it must NOT read, because the REBUILD note (`#10139`) is TRUE prose
    # that a counted writer's sentence may legitimately carry.
    # ⛔ THE TABLE CARRIES ITS OWN PROBES, so a NEW frame cannot ship unmeasured:
    # each entry declares the denials it MUST see, and the TRUE prose it must stay
    # out of. Every `must_see` below is a sentence measured GREEN at `a03951455b`
    # (`#10200` review), and every `must_not_see` is a sentence from this record
    # that the same widening could have reddened — so the pair is the frame's own
    # falsifier in both directions.
    # ⚠️ THREE ASSERTIONS PER DIRECTION, because they prove different halves:
    #   * `pattern.search` on the FLATTENED spelling — the FRAME can see the
    #     spelling. Flattened because that is the text the RULE is handed
    #     (`_claim_sentences` → `_flat`): measured, the raw `carries no
    #     `confidence`` does not match the frame while its flattened form does, so
    #     probing the raw sentence tests the probe, not the frame;
    #   * `_denies_the_class_by_pattern` — the RULE (frame + the history and
    #     polarity escapes) reaches it. A must_see/must_not_see pair checked at the
    #     frame alone cannot express the escapes at all: *"does not drop the
    #     property"* MATCHES the drop frame and is still correct true prose,
    #     because `_class_marker_negated` suppresses it. Measured — the frame-only
    #     form of this probe failed on exactly that sentence.
    for pattern, must_see, must_not_see in DENIAL_PATTERNS:
        for denial in must_see:
            assert pattern.search(_flat(denial).lower()), (
                f"DENIAL_PATTERNS entry {pattern.pattern!r} cannot see the denial "
                f"it was written for: {denial!r}"
            )
            assert _denies_the_class_by_pattern(_flat(denial)), (
                f"DENIAL_PATTERNS entry {pattern.pattern!r} matches {denial!r} at "
                "the frame, but the RULE does not reach it — an escape is "
                "suppressing the denial it was written for"
            )
        assert not _denies_the_class_by_pattern(_flat(must_not_see)), (
            f"DENIAL_PATTERNS entry {pattern.pattern!r} reads TRUE prose it must "
            f"stay out of: {must_not_see!r} — clearing that red means weakening "
            "the record, which is the failure mode every round of this review "
            "has filed"
        )
    # ⛔ THE EXEMPT DIRECTION'S OWN FRAMES, probed the same way (`#10200` review,
    # cycle 9). A frame in `EXEMPT_COUNTED_PATTERNS` that cannot see the claim it
    # was written for is the same DEAD instrument as a hyphenated literal, and its
    # `must_not_see` is the documents' own COUNTED sentence — the one a widening to
    # `is one of …` in general would red.
    for pattern, must_see, must_not_see in EXEMPT_COUNTED_PATTERNS:
        for claim in must_see:
            assert pattern.search(_flat(claim).lower()), (
                f"EXEMPT_COUNTED_PATTERNS entry {pattern.pattern!r} cannot see the "
                f"claim it was written for: {claim!r}"
            )
            assert _exempt_counted_by_pattern(_flat(claim)), (
                f"EXEMPT_COUNTED_PATTERNS entry {pattern.pattern!r} matches "
                f"{claim!r} at the frame, but the RULE does not reach it — an "
                "escape is suppressing the claim it was written for"
            )
        assert not _exempt_counted_by_pattern(_flat(must_not_see)), (
            f"EXEMPT_COUNTED_PATTERNS entry {pattern.pattern!r} reads TRUE prose "
            f"it must stay out of: {must_not_see!r} — clearing that red means "
            "weakening the record"
        )
    # … and must NOT flag the live flattened spelling of the same words.
    for spelling in ("MAP form", "NAMED form", "re materializ", "pre-#9417"):
        assert not _HYPHENATED_MARKER.search(spelling), (
            f"the dead-marker pattern flags {spelling!r}, which `_flat` produces "
            "(or which has no hyphen between word characters), so it would red "
            "on a live marker"
        )
    dead = [
        f"{family}: {marker!r}"
        for family, markers in families.items()
        for marker in markers
        if _HYPHENATED_MARKER.search(marker)
    ]
    assert not dead, (
        "these markers contain a hyphen between word characters, and `_flat` "
        "normalises that to a SPACE — so they can never match a sentence and "
        f"pin nothing: {dead}. Write the FLATTENED spelling."
    )

    flat = (
        _flat(_doc(STORAGE)).lower(),
        _flat(_doc(EXTRACTOR)).lower(),
    )
    unpinned = [
        f"{family}: {marker!r}"
        for family, markers in families.items()
        for marker in markers
        if not any(marker.lower() in text for text in flat)
        and marker not in MARKERS_ABSENT_BY_DESIGN
    ]
    assert not unpinned, (
        "these markers appear in NEITHER flattened document and are not declared "
        f"absent-by-design: {unpinned} — either a document was reworded away from "
        "the marker (so it now pins nothing), or it needs a line in "
        "`MARKERS_ABSENT_BY_DESIGN` saying why its absence is intended"
    )
    stale = [
        marker
        for marker in MARKERS_ABSENT_BY_DESIGN
        if any(marker.lower() in text for text in flat)
    ]
    assert not stale, (
        f"these markers are declared absent-by-design but DO appear in the "
        f"documents: {stale} — the declaration has gone stale, and a stale "
        "declaration is how a live marker gets quietly exempted"
    )
    # ⛔ THE THIRD DIRECTION — DECLARATION → MARKER, which neither half above
    # checks (`#10200` review, cycle 9). `MARKERS_ABSENT_BY_DESIGN` is the file's
    # own coverage map: a reader takes every key as "this marker exists and its
    # absence is deliberate". A key that is a member of NO vocabulary tuple pins
    # nothing and is consulted by nothing, so it reads as coverage without being
    # any — the same doctrine the census enforces twice elsewhere (*"a marker that
    # matches nothing pins nothing"*). Measured at `6d40e5732`: `does not write`
    # and `no longer writes` were both orphans (their determiner forms, `does not
    # write the` / `no longer writes the`, are the live markers and carry the
    # reasons), so the entries were deleted rather than left as dead coverage —
    # and this assertion stops the next orphan from being written.
    declared_not_markers = sorted(
        set(MARKERS_ABSENT_BY_DESIGN)
        - {marker for markers in families.values() for marker in markers}
    )
    assert not declared_not_markers, (
        "these `MARKERS_ABSENT_BY_DESIGN` keys are not a member of any marker "
        f"tuple, so they exempt nothing and pin nothing: {declared_not_markers}. "
        "Delete the entry (if its reason belongs to a live neighbour) or add the "
        "marker to the family it belongs to"
    )


#: The `#9417` fix's review-cycle count — ONE value for the three statements
#: this file makes about it.
#:
#: `#10149`'s own record lists exactly four cycles on that five-line fix (1 and 2
#: returned findings, 3 and 4 were GREEN while §13 said the opposite of the
#: code), and this module states the count in three places: the PIN-INDEX entry
#: that introduces leg 33, leg 33's own block, and leg 33's docstring. Measured
#: at the `#10200` review, cycle 10: the pin index said FIVE where the other two
#: said four — the index disagreeing with the leg it indexes, i.e. exactly the
#: defect class this census exists to close, in the file that closes it.
_REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN = 4

#: A `<number> review cycles` statement — the shape all three use. Plural on
#: purpose: *"a third review cycle"* is a different claim (an ordinal about
#: `#9403`) and is not what this count is about.
#:
#: ⛔ `\w+` matches DIGITS as well as words, so the READER is where both
#: spellings are honoured — see `_review_cycle_counts`, which converts a digit
#: token instead of skipping it. The word-only consumer this replaced dropped a
#: DIGIT-stated count SILENTLY (`word not in words -> continue`), so the same
#: count could be typed twice in two spellings with the leg green in one of
#: them — the defect class this census exists to close, inside leg 33.
_REVIEW_CYCLE_COUNT_STATEMENT = re.compile(r"(\w+)\s+review cycles\b")


def _review_cycle_counts(source: str) -> list[tuple[int, str, int]]:
    r"""Every review-cycle count statement in `source`, with its 1-based line.

    The numeral is read in WORDS and in DIGITS. Digits are not a courtesy: `\w+`
    matches them, and a consumer that only consulted the word table dropped the
    digit spelling without saying so. A token that is NEITHER (`many review
    cycles`) is not a numeral claim and is skipped — that skip is the only one.

    Probe-checked by the leg that uses it, so a reader that stops seeing one of
    the two spellings reds there instead of quietly pinning half the shapes.
    """
    words = {word: number for number, word in NUMBER_WORDS.items()}
    out: list[tuple[int, str, int]] = []
    for match in _REVIEW_CYCLE_COUNT_STATEMENT.finditer(source):
        token = match.group(1).lower()
        value = int(token) if token.isdigit() else words.get(token)
        if value is None:
            continue
        out.append(
            (source[: match.start()].count("\n") + 1, match.group(0), value)
        )
    return out

#: The ledger's own statement of the same count, so the two artifacts are
#: compared against each other rather than each against a typed literal twice.
_LEDGER_CYCLE_COUNT_STATEMENT = re.compile(r"measured (\w+) times on the five-line\b")

#: The ledger records every `#10200` round and the cycle it closed, in the
#: heading shape `review round N (cycle M)`.
_LEDGER_ROUND = re.compile(r"review round (\d+) \(cycle (\d+)\)")

#: Every `cycle N` / `cycles N and M` inside a window after a `#10200` mention.
#: The window is bounded because the module mentions many reviews, and only the
#: `#10200` cycles are what the ledger is a record OF.
_DOCUMENTED_CYCLE = re.compile(r"cycle[s]?\s+(\d+)(?:\s+and\s+(\d+))?")
_TWO_THOUSAND_TWENTY_ONE = "#10200"
_CYCLE_WINDOW = 140

#: The `#:` block that justifies `DIRECT_EDGE_CREATED_ATTR_GUARDS` — the census's
#: evidence pointer for *"exactly two emitters"*, sliced by its own markers so a
#: block that moves fails by NAME instead of passing unread.
_PRODUCER_EVIDENCE_START = "#: ⛔ This map is not bookkeeping"
_PRODUCER_EVIDENCE_END = "DIRECT_EDGE_CREATED_ATTR_GUARDS: dict"

#: A backticked `file.py:<n>` / `file.sql:<n>` citation. The documents may carry
#: one only when it is qualified with the revision it was measured at (leg 6);
#: this file's RECORD may not carry one at all — a number here is the artifact a
#: lane audits when §13 and the code disagree, and it reads as CURRENT the moment
#: the symbol moves (the `#10200` review, cycle 10, followed the three numbers the
#: `DIRECT_EDGE_CREATED_ATTR_GUARDS` block used to carry into `poll_events`,
#: `list_batch` and `assess_source`).
_BARE_LINE_CITATION = re.compile(r"`[\w./-]+\.(?:py|sql):\d+`")


def test_the_producer_evidence_block_cites_the_emitters_by_symbol():
    """The block above `DIRECT_EDGE_CREATED_ATTR_GUARDS` is the census's evidence
    pointer for *"exactly two `DirectEdgeCreated` emitters"* — the paragraph a
    lane reads when §13 and the code disagree. It carried `sdk.py:8493`,
    `sdk.py:23117` and `sdk.py:41317`; at the reviewed head those three land in
    `poll_events`, `list_batch` and `assess_source`, while the emitters are the
    `_emit_event("DirectEdgeCreated", …)` calls in `_record_structural_edge` and
    `create_direct_edge`. A lane following the numbers must conclude either that
    the record is stale or that the producers are unenumerated.

    That is this file's own rule turned on itself — it asserts the documents cite
    by SYMBOL (*"the SYMBOL is authoritative and no line number is asserted"*)
    and enforces it on the corrected passages (leg 6) while its own evidence
    pointer carried bare numbers. So both halves are asserted here, and the
    emitter names are DERIVED from the AST rather than restated: a third producer
    reds through `test_every_direct_edge_created_producer_is_safe_for_an_about_edge`,
    and a re-typed number reds here.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    start = source.index(_PRODUCER_EVIDENCE_START)
    block = source[start : source.index(_PRODUCER_EVIDENCE_END, start)]

    numbered = _BARE_LINE_CITATION.findall(block)
    assert not numbered, (
        "the producer-evidence block cites line numbers as if they were "
        f"current: {numbered}. Name the SYMBOL instead — this file's own rule is "
        "*\"the SYMBOL is authoritative and no line number is asserted\"*, and it "
        "enforces exactly that on the corrected passages (leg 6). The emitters "
        "are `_record_structural_edge`'s `_emit_event(\"DirectEdgeCreated\", …)` "
        "and `create_direct_edge`'s"
    )

    emitters = {
        symbol
        for symbol, _lineno, _kwargs in _direct_edge_created_emissions(
            "tortoise/sdk.py"
        )
    }
    assert emitters, (
        "the AST found no `DirectEdgeCreated` emitter in `tortoise/sdk.py`, so "
        "this leg has lost its subject — re-derive the citation rule against the "
        "tree before trusting it"
    )
    unnamed = sorted(symbol for symbol in emitters if symbol not in block)
    assert not unnamed, (
        "the producer-evidence block does not name these emitters, so a reader "
        f"following it cannot find them: {unnamed}. It names them by SYMBOL, and "
        "the symbols come from the code — add the enclosing function to the "
        "block rather than a line number"
    )


def test_the_pin_index_states_the_9417_cycle_count_its_own_leg_does():
    """The pin index is a summary of each leg, so a NUMERAL in it that disagrees
    with the leg it indexes is the same defect this census exists to close — a
    count typed in two places instead of derived in one.

    Measured at the `#10200` review, cycle 10: entry 33 put that count at
    *five* while leg 33's own block says four, and `#10149`'s record lists
    exactly four (cycles 1 and 2 returned findings; 3 and 4 were green while §13
    said the opposite of the code). Nothing red on it, because
    `test_the_pin_index_names_every_leg` pins the entry SET and FILE ORDER and
    `test_the_pin_index_entry_for_the_9_6_retraction_states_no_measured_distance`
    pins one entry's CONTENT, at that entry's own subject.

    Three statements are compared, not one pair, and the ledger's own statement
    of the same count is the fourth: the numeral is typed ONCE, in
    `_REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN`, and every artifact that states it
    must agree with that value. Silence is not agreement — a passage that stops
    stating the count entirely reds rather than passing unmeasured.
    """
    words = {word: number for number, word in NUMBER_WORDS.items()}
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    # ⛔ The reader is PROBED in BOTH spellings before it is trusted, because the
    # word-only version of it was blind to digits and read as coverage. The
    # probe sentences carry their numeral through a VARIABLE so the file-wide
    # scan below does not count the probe itself as a statement about `#9417`.
    digit_token = "5"
    word_token = "four"
    probed_digit = _review_cycle_counts(f"gauged: {digit_token} review cycles on a fix")
    assert [value for _line, _text, value in probed_digit] == [5], (
        "the cycle-count reader does not see a count stated in DIGITS, so it "
        "pins only the word form and the same count can be typed twice — "
        "measured: rewriting the count in leg 33's own block from its number "
        f"word to digits left this leg green. Reader returned {probed_digit}"
    )
    probed_word = _review_cycle_counts(f"gauged: {word_token} review cycles on a fix")
    assert [value for _line, _text, value in probed_word] == [4], (
        "the cycle-count reader does not see a count stated as a NUMBER WORD, "
        f"which is the spelling every statement in this file uses. Reader "
        f"returned {probed_word}"
    )
    nonnumeral = "many"
    assert not _review_cycle_counts(f"gauged: {nonnumeral} review cycles on a fix"), (
        "the cycle-count reader treats a non-numeral token as a statement, so "
        "prose such as 'many review cycles' would be read as a measured count"
    )
    stated = _review_cycle_counts(source)
    assert stated, (
        "no `<number> review cycles` statement found in this file — the "
        "count this leg pins has moved or been reworded, so the leg would pass "
        "without measuring anything"
    )
    # ⛔ CARDINALITY, because this leg's own docstring claims it: *"Three
    # statements are compared, not one pair"* and *"Silence is not agreement — a
    # passage that stops stating the count entirely reds rather than passing
    # unmeasured"*. Asserting only that `stated` is NON-EMPTY pins nothing about a
    # passage that falls silent: measured (`#10200` review), deleting the count
    # from the PIN-INDEX statement — the artifact whose stale `five` opened this
    # whole class — left this leg GREEN while the docstring promised a red.
    expected_statements = 3
    assert len(stated) == expected_statements, (
        f"this file states the `#9417` review-cycle count in {len(stated)} "
        f"place(s), not {expected_statements} — the pin index that introduces "
        "leg 33, the `#10149` block below it, and leg 33's own docstring. A "
        "passage that stops stating it has silently left the record, and 'one "
        "value, stated once' does not make silence agreement. Found: "
        + repr([(line, text) for line, text, _v in stated])
    )
    # … and the count must DECREMENT when one statement falls silent, or the
    # cardinality above cannot notice one going. Removing only the FIRST
    # occurrence is what makes this a probe of the READER rather than of
    # `str.replace` — the two pin-index spellings share a matched text.
    thinner = source.replace(stated[0][1], "the", 1)
    assert len(_review_cycle_counts(thinner)) == len(stated) - 1, (
        "removing one cycle-count statement does not decrement the reader's "
        "output, so the cardinality assertion above cannot notice a passage "
        "that has stopped stating the count"
    )
    disagreeing = [
        f"module line {line} says {text!r} = {value}"
        for line, text, value in stated
        if value != _REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN
    ]

    ledger = (
        ROOT / "docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md"
    ).read_text(encoding="utf-8")
    recorded = _LEDGER_CYCLE_COUNT_STATEMENT.findall(ledger)
    assert recorded, (
        "the ledger no longer states how many review cycles the `#9417` fix "
        "took — re-derive the count from `#10149`'s own record and restate it "
        "there before trusting this leg"
    )
    ledger_values = sorted({words[word.lower()] for word in recorded if word.lower() in words})
    assert len(ledger_values) == 1, (
        "the ledger states the `#9417` cycle count more than one way "
        f"({recorded}) — one value, stated once"
    )
    if ledger_values[0] != _REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN:
        disagreeing.append(
            f"the ledger says {recorded[0]!r} = {ledger_values[0]}"
        )

    assert not disagreeing, (
        "the `#9417` review-cycle count is stated inconsistently — every place "
        "that states it must read the same value, because a count typed twice is "
        "the defect class this census exists to close:\n  "
        + "\n  ".join(disagreeing)
        + f"\n\nOne value, in `_REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN` "
        f"({_REVIEW_CYCLES_ON_NINE_FOUR_ONE_SEVEN}). Re-derive it from `#10149`'s "
        "record of the four cycles before changing it anywhere"
    )


def test_the_ledger_records_every_review_cycle_the_record_cites():
    """The correction ledger indexes this instrument: it is where a future lane
    learns what the census now pins and why, and its own doctrine is that a bound
    left out of it is a bound a lane will rely on.

    Measured at the `#10200` review, cycle 10: the ledger stopped at *round 6
    (cycle 7)* while this module cited a cycle 9 review in four places — so the
    rounds that introduced the widest denial frames and the newest vocabulary
    table had NO entry, while earlier instrument-only rounds were all recorded. A
    ledger that ends before the artifact it indexes is the *"a rule that only
    exists in dead code is not a rule"* failure in prose: the closures still exist
    in the code and nothing describes them.

    The invariant is one-sided on purpose — NEWER than the ledger is the defect
    (the ledger falling behind); older cycles than it records are fine, because
    rounds 1–3 predate the `(cycle N)` label convention. It reds on the current
    head's own gap, not on a re-derivation of history.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )
    cited: set[int] = set()
    for mention in re.finditer(re.escape(_TWO_THOUSAND_TWENTY_ONE), source):
        window = source[mention.end() : mention.end() + _CYCLE_WINDOW]
        for match in _DOCUMENTED_CYCLE.finditer(window):
            cited.update(int(group) for group in match.groups() if group)
    assert cited, (
        f"this module cites no `{_TWO_THOUSAND_TWENTY_ONE}` review cycle at all, "
        "so this leg measures nothing — re-derive it against the file's comment "
        "shape before trusting it"
    )

    ledger = (
        ROOT / "docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md"
    ).read_text(encoding="utf-8")
    rounds = [
        (int(round_number), int(cycle))
        for round_number, cycle in _LEDGER_ROUND.findall(ledger)
    ]
    assert rounds, (
        "the ledger records no `review round N (cycle M)` heading, so its own "
        "index shape has changed and this leg cannot read it"
    )
    newest_recorded = max(cycle for _round, cycle in rounds)
    newest_cited = max(cited)
    assert newest_cited <= newest_recorded, (
        f"this module cites the `{_TWO_THOUSAND_TWENTY_ONE}` review at cycle "
        f"{newest_cited}, but the correction ledger stops at cycle "
        f"{newest_recorded} (rounds recorded: "
        f"{', '.join(f'{r}/{c}' for r, c in rounds)}) — so the newest closures "
        "have no ledger entry and a lane reading the ledger does not learn what "
        "the instrument now pins. Add the missing round(s) to "
        "`docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md`"
    )


#: A COMMENT — this module's RECORD layer, as opposed to its test DATA. The
#: distinction is load-bearing rather than convenient: several legs carry a
#: PRE-FIX spelling as data (`_PIN_INDEX_PRE_FIX_PROBE`, the `security.py`
#: line-citation probe) precisely to prove a matcher can still fire on it, so a
#: blanket ban over the file would red on the probes that keep the rules honest.

#: The pointer this round removed from the record, assembled from two literals so
#: that THIS leg's own probe is not the last bare citation in the file — a
#: file-wide ban otherwise eats its own probe and cannot be written down at all.
_CACHE_PROJECTION_PRE_FIX_POINTER = "`cache_projection" ".py:717`"


def _comment_lines(source: str) -> list[tuple[int, str]]:
    """Every COMMENT in `source`, with the line number it starts on.

    `tokenize` rather than a line regex: a probe STRING may begin with `#:`
    (`_PRODUCER_EVIDENCE_START`, the pre-fix block below), and a line regex
    would read test data as record prose — the exact confusion this leg exists
    to keep apart.
    """
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]


def test_no_bare_line_citation_survives_in_this_files_record():
    """This module's own RECORD must cite by SYMBOL, and until this leg the rule
    was enforced over ONE block.

    `test_the_producer_evidence_block_cites_the_emitters_by_symbol` (leg 37) made
    the rule real for the `DIRECT_EDGE_CREATED_ATTR_GUARDS` evidence pointer, but
    it scans a single slice, so every other `#:` block in a file of this size was
    unscanned — and one of them carried the same defect. Measured at the
    `#10200` review, cycle 12: the `MAP_WRITERS` block justified the census's
    THIRD write form with a bare line number for `cache_projection`, which at
    that head is `if section is None:` in the row-matching loop, ~800 lines from
    the `{idx:$idx}` template the sentence quotes (`relation_statement`'s own
    `MERGE`); two diagnostic messages repeated the same number, so a lane
    auditing whether the `idx` row is still realised in code was sent to a
    branch that has nothing to do with it and could only conclude the record was
    stale — the `#9397` + `#10149` defect class, in the artifact that closes it.
    *(The number itself is not restated here: a leg whose docstring carried it
    would fail its own file-wide half, which is why the pre-fix spelling below
    is assembled from two literals.)*

    Two halves, because the record carried the number in two kinds of place:
    every COMMENT in this module must cite by symbol — not only the `#:` blocks
    leg 37 reads — and the one pointer the third row leans on (`cache_projection`)
    may carry no line number anywhere in the file at all, which is the half that
    reaches the assertion messages (where two of the three stale pointers lived)
    and the docstrings.
    """
    source = (ROOT / "tests/test_9397_about_edge_properties.py").read_text(
        encoding="utf-8"
    )

    #: The `MAP_WRITERS` justification block in its PRE-FIX shape — the only
    #: honest way to prove the scan below can red at all.
    probe = (
        "#: them ALSO writes a name in the INLINE pattern-map form\n"
        "#: (`cache_projection.relation_statement` stamps `{idx:$idx}`,\n"
        f"#: {_CACHE_PROJECTION_PRE_FIX_POINTER}), and that `idx` is read by the\n"
        "#: census's inline rule\n"
    )
    assert _BARE_LINE_CITATION.findall(probe) == [
        _CACHE_PROJECTION_PRE_FIX_POINTER
    ], (
        "the citation rule no longer matches the pre-fix spelling "
        f"{_CACHE_PROJECTION_PRE_FIX_POINTER!r}, so it cannot red on a "
        "re-introduction"
    )
    probed = _comment_lines(probe)
    assert [line for line, _text in probed] == [1, 2, 3, 4], (
        "the comment scan does not see the pre-fix block's `#:` lines, so it "
        f"would pass without measuring them: {probed}"
    )
    assert _BARE_LINE_CITATION.findall(probed[2][1]) == [
        _CACHE_PROJECTION_PRE_FIX_POINTER
    ], (
        "the comment scan does not hand its lines to the citation matcher — the "
        f"pre-fix citation sits on line 3 and it saw {probed[2][1]!r}"
    )

    record = _comment_lines(source)
    assert any("This map is not bookkeeping" in text for _line, text in record), (
        "the comment scan did not find this file's `DIRECT_EDGE_CREATED_ATTR_GUARDS` "
        "evidence block, so it is measuring nothing — re-derive it against the "
        "file's comment shape before trusting it"
    )
    numbered = [
        (line, found)
        for line, text in record
        for found in _BARE_LINE_CITATION.findall(text)
    ]
    assert not numbered, (
        "this module's COMMENTS cite line numbers as if they were current: "
        f"{numbered}. Name the SYMBOL instead — this file's own rule is *\"the "
        "SYMBOL is authoritative and no line number is asserted\"* (leg 6 "
        "enforces it on the documents, leg 37 on the producer-evidence block), "
        "and a number in the record is what a lane audits against the code when "
        "the two disagree"
    )

    pointer = re.compile(r"`cache_projection\.py:\d+`")
    assert pointer.findall(probe), (
        "the file-wide pointer rule does not match the pre-fix spelling "
        f"{_CACHE_PROJECTION_PRE_FIX_POINTER!r}, so it cannot red on a "
        "re-introduction"
    )
    everywhere = [
        (source[: match.start()].count("\n") + 1, match.group(0))
        for match in pointer.finditer(source)
    ]
    assert not everywhere, (
        "the census's third write form has exactly one pointer — "
        f"`cache_projection.relation_statement` — and a line number for it "
        f"survives at {everywhere}. The number is what rotted here: it named a "
        "row-matching branch ~800 lines from the `{idx:$idx}` template, in the "
        "RECORD and in two assertion messages, so a lane following it reads the "
        "row as stale. Name the symbol; the record may not carry the number at "
        "all"
    )


def test_the_rebuild_half_carries_what_the_live_half_carries():
    """`#10139`'s CODE half, asserted at the STATEMENT — the residual this file
    used to state instead.

    `MAP_WRITERS` records the replay row and names its residual in place: the
    census's whole-function union reaches that site through the 2a-DIRECT
    `SET r += $attrs` leg, because the structural transfer's own `SET` rides an
    interpolated `_carry` fragment (module-docstring gap 1). A `shape` needle and
    the two behavioural replay tests were what stood in for a statement-level
    pin. This leg is that pin, and it is cheap because the premises are literals:

      1. the descriptor JOURNALS the payload — `sdk.supersede_point` spreads
         `edge_props`, and `edge_props` is filtered by the ONE shared tuple
         (`DIRECT_EDGE_ATTR_KEYS`). A second hand-written key list here is the
         drift class `#10139` is about.
      2. the replay APPLIES it at the statement that re-creates the edge, so a
         rename cannot silently move the property off the transfer.
      3. the application is `ON CREATE`, not a bare `SET` — `#9417`'s precedence
         rule and NOT a formatting preference: the `EntityLinked` trailing sweep
         has already re-materialized the successor's OWN `confidence` by the time
         pass-2b runs, so an unconditional `SET nr += $attrs` overwrites it with
         the retired point's value (measured live at 0.42 replacing 0.91, and
         reproduced on the replay side by mutation). `_carry = " SET nr +=
         $attrs"` must red HERE, not only in the behaviour suites.

    ⚠️ The vocabulary is also pinned as a CEILING inside
    `FALSE_POSITIVE_ATTR_VOCAB`, the fixed set the neighbouring gated folds copy
    through: a carry that grew a key outside it would put that key on an
    interpolated `etype`, which is the `#9417` shape one constant away.
    """
    module = "tortoise/projection/__init__.py"
    symbol = "_rebuild_all_replay"
    body = _function_body(module, symbol)
    # 2. the statement that re-creates the transferred edge is still there …
    transfer = _query_call_containing(body, "MERGE (s)-[nr:{etype}]->(t)")
    assert transfer, (
        f"`{module}::{symbol}` no longer carries `MERGE (s)-[nr:{{etype}}]->(t)` "
        "— the about/structural transfer moved or was renamed, so the payload the "
        "descriptor journals lands nowhere (`#10139`)"
    )
    assert "{_carry}" in transfer, (
        "the transfer statement in "
        f"`{module}::{symbol}` does not interpolate the carry variable, so the "
        "payload is computed and then never applied: "
        + repr(transfer.strip()[:200])
    )
    # … 3. and the carry is the PRECEDENCE-PRESERVING form.
    assert "ON CREATE SET nr += $attrs" in body, (
        f"`{module}::{symbol}` no longer applies the descriptor's payload with "
        "`ON CREATE SET nr += $attrs` — `live == rebuild` is false again for an "
        "`about*` edge's `confidence` (`#10139`)"
    )
    assert '= " SET nr += $attrs"' not in body, (
        f"`{module}::{symbol}` applies the payload with a BARE `SET`. That "
        "clobbers the successor's OWN confidence, which the `EntityLinked` "
        "trailing sweep has already re-materialized by the time pass-2b runs: "
        "the live half's measured 0.91 -> 0.42 regression, on the replay side "
        "(`#9417`, `#10139`)"
    )
    # The gated `SET r += $attrs` folds beside it are still gated PER STATEMENT —
    # the protection that bounds what an INTERPOLATED `etype` can receive.
    setter_needle = "SET r += $attrs"
    setters = _query_calls_containing(body, setter_needle)
    assert setters, (
        f"`{module}::{symbol}` lost its `{setter_needle}` folds, so the census "
        "row this file records no longer describes the code"
    )
    gates = _setter_gate_premises(
        module,
        (ROOT / module).read_text(encoding="utf-8"),
        symbol,
        setter_needle,
    )
    assert len(gates) == len(setters), (
        f"`{module}::{symbol}` carries {len(setters)} `{setter_needle}` "
        f"statement(s) but {len(gates)} gate premise(s) — the two readings of "
        "the same function disagree, so this leg measures neither"
    )
    for _gate_test, vocabulary in gates:
        stray = sorted(set(vocabulary) - FALSE_POSITIVE_ATTR_VOCAB)
        assert not stray, (
            f"a gated attribute fold in `{module}::{symbol}` copies {stray}, "
            "which is outside the fixed vocabulary the interpolated `etype` is "
            f"bounded by ({sorted(FALSE_POSITIVE_ATTR_VOCAB)})"
        )
    # 1. ONE vocabulary, imported by BOTH halves.
    from tortoise.projection.edges import DIRECT_EDGE_ATTR_KEYS

    stray_keys = sorted(set(DIRECT_EDGE_ATTR_KEYS) - FALSE_POSITIVE_ATTR_VOCAB)
    assert not stray_keys, (
        "`DIRECT_EDGE_ATTR_KEYS` carries keys outside the fixed vocabulary the "
        f"neighbouring folds are bounded by: {stray_keys}"
    )
    emitter = (ROOT / "tortoise/sdk.py").read_text(encoding="utf-8")
    assert "if k in DIRECT_EDGE_ATTR_KEYS" in emitter, (
        "`sdk.supersede_point` builds its 2b payload from a key list of its own "
        "again — the live half and the replay half then disagree about which "
        "keys a supersede moves, which is what one shared constant prevents "
        "(`#10139`)"
    )
    assert "**edge_props," in emitter, (
        "the 2b `DirectEdgeRepoint` emission no longer journals `edge_props` — "
        "the descriptor is topology-only again, so the replay re-creates a bare "
        "edge and drops the property (`#10139`)"
    )
    # … and the two design documents that COUNT the row must say what it writes.
    for label, start, end, which in CORRECTED_SECTIONS:
        if label.endswith("§11.1"):
            continue
        section = _flat(
            _section(_doc(STORAGE if which == "STORAGE" else EXTRACTOR), start, end)
        )
        assert "ON CREATE SET nr += $attrs" in section, (
            f"{label} no longer states that the transfer carries the payload "
            "under `ON CREATE` — the row is counted there as an `about*` writer, "
            "so a reader cannot tell what it writes (`#10139`)"
        )
