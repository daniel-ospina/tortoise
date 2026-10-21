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

`sdk.supersede_point` and `projection._rebuild_all_replay` — which earlier
revisions of this file recorded as re-materializers — are NOT. Its `SET r += $attrs` is on the 2a-DIRECT leg,
whose rows are discovered by `MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t)`,
so `rtype` is provably `IMPL|NAND`; the about edge is transferred by a separate
BARE `MERGE (new)-[:{rel}]->(t)` with no `SET`. On an about edge it writes no
property — it DROPS `confidence` (filed as `#9417`). `_rebuild_all_replay`
carries TWO `SET r += $attrs` statements, and neither is protected the same way:
the `DirectEdgeCreated` fold's producer (`create_direct_edge`) refuses any
`op_type` outside `{IMPL, NAND}`; the structural-`DirectEdgeRepoint` fold's
`etype` IS allowlisted and can be `aboutObject`, so what carries THAT statement
is the map being built from a fixed attribute vocabulary — structural repoint
descriptors carry none of those keys, the map is empty, and nothing is written.
Its about/structural transfer is a separate BARE `MERGE` whose own comment says
so (*"Structural 2b transfers are bare: NO attr SET"*), and `create_about_edge`
journals no `DirectEdgeCreated` at all. Both
functions therefore carry NO property on an about edge. The census's whole-function
variable union cannot tell which edge the `SET` lands on, so BOTH rows are
declared FALSE POSITIVES in `NOT_AN_ABOUT_WRITER` rather than counted as
re-materializations — the false-POSITIVE version of this file's own defect.

⚠️ An EXEMPTION is a fail-open cell unless something compares against it, which
is why this row is NOT exempt from the name-set comparison: it declares
`EXPECTED_NAMES` like a recorded writer, and its premise (BARE about transfer,
`SET` on the `IMPL|NAND` rows) is asserted against the STATEMENT it names — not
against a function-wide text union, which is the very blindness the row exists
to explain.

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
(listed above) — two MORE use it on an `IMPL|NAND` leg and are the declared
false positives below, which is why the population it must NOT count is the
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
   property-free"* is the SAME unqualified absolute, seventy lines above the
   §11.1 correction, for a different link class (a record-projected `references`
   edge carries `idx` too). Scoped to the fact the paragraph establishes: those
   links carry **no `sourceVersion`**.
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
   (3 named + 2 map), which is why the wrong number read plausibly; the causal
   split is 2 + 3, and the legs red if the fold is filed as an originator again
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

NOT promised: this does not re-open stored-vs-derived (the ruling stands, and
the edge stays), and it does not decide §11.4 rule 1's tension with `#1370`
(that is a design question, recorded, not resolved here — what IS pinned is
that the rule keeps stating the measured set and keeps carrying the tension
record, test 5). Line numbers are not
asserted — the SYMBOL is authoritative, per the documents' own rule.

Static and stdlib-only, with one non-static corner: the provenance leg measures a
claim about HISTORY, so it shells out. Exactly what it runs, because an earlier
revision of this paragraph said "ONE exception: `git show` … no network" and was
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
import re
import subprocess
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
#: (`cache_projection.relation_statement` stamps `{idx:$idx}`,
#: `cache_projection.py:717`), and that `idx` is read by the census's inline
#: rule — so the documents' set is `confidence` (originated) plus `idx`
#: (re-materialized), not one name.
MAP_WRITERS: dict[tuple[str, str], tuple[str, ...]] = {
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
NOT_AN_ABOUT_WRITER: dict[tuple[str, str], str] = {
    ("tortoise/sdk.py", "supersede_point"): (
        "the `SET r += $attrs` is on the 2a-DIRECT leg, whose rows come from "
        "`MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t)` (rtype is provably "
        "IMPL|NAND); the about edge is transferred by a separate BARE "
        "`MERGE (new)-[:{rel}]->(t)` with no SET — so this writer carries NO "
        "property on an about edge (it drops `confidence`; #9417)"
    ),
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"): (
        "it carries TWO `SET r += $attrs` statements, and the exemption depends "
        "on each separately: the `DirectEdgeCreated` fold, whose ONLY producer "
        "(`create_direct_edge`) refuses any op_type outside {IMPL, NAND}; and "
        "the structural-`DirectEdgeRepoint` fold, whose interpolated `etype` "
        "IS allowlisted and CAN be `aboutObject`, so it is the `attrs` gate "
        "that carries it — structural repoint descriptors carry none of "
        "{direction, confidence, weight, label, batch_id}, the map is empty, "
        "and nothing is written. The about*/structural transfer is a separate "
        "BARE `MERGE (s)-[:{etype}]->(t)` (the code says so in place: "
        "'Structural 2b transfers are bare: NO attr SET'), and "
        "`create_about_edge` journals no `DirectEdgeCreated` at all — so this "
        "writer carries NO property on an about edge"
    ),
}

#: For each declared false positive: the STATEMENT the census attributes the
#: property to (needle 1), and the BARE about transfer it does NOT (needle 2).
#: Two per row because the exemptions are different code shapes — an `$attrs`
#: copy on a direct edge, and a journal-event fold — so a single module-wide
#: membership test would be satisfied while a `SET` sat on the about transfer,
#: which is exactly the false negative these rows must not hide.
FALSE_POSITIVE_STATEMENTS: dict[tuple[str, str], tuple[str, str]] = {
    ("tortoise/sdk.py", "supersede_point"): (
        "SET r += $attrs",
        "MERGE (new)-[:{rel}]->(t)",
    ),
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"): (
        "SET r += $attrs",
        "MERGE (s)-[:{etype}]->(t)",
    ),
}

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

#: The property. One ORIGINATED name; `idx` is the second recorded name, written
#: by the record-layer projection as an INLINE pattern map (see `INLINE_FORM`).
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
INLINE_FORM = "<inline-map>"
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
    #: The declared FALSE POSITIVES are compared like any other row. Both are in
    #: the census (with `MAP_FORM`, from an `IMPL|NAND`-scoped
    #: `SET r += $attrs`), and an exemption with no expected set is exactly where
    #: a new property would slip past `unexpected` — the comparison is keyed on
    #: `EXPECTED_NAMES`, so the rows have to be HERE to stay measured.
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


def _fixed_key_vocabulary(source: str) -> list[str]:
    """The literal key names a `{k: v for … if k in (…)}` map is built from.

    A fixed vocabulary is what makes the exemption hold where the predicate is
    INTERPOLATED: a descriptor carrying none of these keys yields an EMPTY map,
    and an empty map writes nothing on whatever `etype` resolves to.
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
            "(`cache_projection.py:717`), so the measured set is `confidence` "
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
    (`projection/edges.py`), and so are the containment writers
    (`projection/entities.py`, `hosted_api.py`'s session→external MERGE),
    `graph-scripts/backfill_references.py`, and the bare `create_edge` escape
    hatch (it takes no property parameter). `session_source_migration._move_out`
    re-points an existing edge with `ON CREATE SET r2 = properties(r)` — it
    PRESERVES attrs, never mints one.

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
        "derivation writers stamp `sourceVersion` by design). Scope it to the fact "
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
    that this exact convention was wrong three times (`cache_projection.py:717`).
    """
    cite = re.compile(r"`[\w./-]+\.(?:py|sql):\d+`")
    qualifier = re.compile(r"at HEAD|at the pin\b|at the pinned revision", re.I)
    out: list[str] = []
    matches = list(cite.finditer(text))
    for i, m in enumerate(matches):
        # The window STOPS at the next citation. Measured: a neighbour's
        # `— at HEAD` inside a flat 120-character window qualified a bare
        # citation beside it, so removing `(at HEAD)` from `security.py:85`
        # while the next clause carried one stayed GREEN (vacuous pin).
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
    # first) and 3 RE-MATERIALIZING (the replay fold, …)"
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
    # §11.1 states the same two numbers in a DIFFERENT spelling — "ORIGINATED by two
    # sites and written by three in the named form" — and that spelling was pinned by
    # nothing: measured at `#9403` review, editing it to "three sites" left the suite
    # green while the paragraph above contradicted it. Words, not digits, so both
    # spellings are checked against the tables.
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    assert f"ORIGINATED by {words[originating]} sites" in storage, (
        f"§11.1 no longer states 'ORIGINATED by {words[originating]} sites', which is the "
        f"ORIGINATING count {originating} the census split implies"
    )
    assert f"written by {words[named]} in the named form" in storage, (
        f"§11.1 no longer states 'written by {words[named]} in the named form', which is "
        f"what `RECORDED_WRITERS` holds ({named})"
    )
    ordinals = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
    assert f"sees {words[named]} of the {words[total]}" in storage, (
        f"§13's census sentence no longer matches the tables: it must say "
        f"'sees {words[named]} of the {words[total]}' (RECORDED_WRITERS={named}, total={total})"
    )
    # `_flat` normalises the hyphen out of "NAMED-form", and the sentence appears twice
    # (lowercase once, capitalised once), so match the space form and require BOTH
    # occurrences to carry the ordinal the table implies.
    ordinal_hits = re.findall(rf"\b{ordinals[named]} NAMED form writer\b", storage, re.I)
    assert len(ordinal_hits) == 2, (
        f"§11.1 must say '{ordinals[named]} NAMED form writer' in both places it names "
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
    # `ordinals` maps NUMBER -> word, so the wrong ordinals are its VALUES whose
    # number is not the table's; reading the keys instead is how a loop over
    # `ordinals.items()` can silently search for "2 NAMED form writer" and match
    # nothing — the same dead-pin shape this block exists to close.
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
    assert f"{words[len(NOT_AN_ABOUT_WRITER)]} declared false positives" in storage, (
        "§13's declared-false-positive count no longer matches `NOT_AN_ABOUT_WRITER` "
        f"({len(NOT_AN_ABOUT_WRITER)})"
    )
    assert (originating == 2 and "the two that ORIGINATE it" in storage) or (
        f"the {originating} that ORIGINATE it" in storage
    ), (
        f"§11.1 no longer states that {originating} writers ORIGINATE the property"
    )
    assert len(NOT_AN_ABOUT_WRITER) == 2, (
        "the exemption table changed size; §13's '5, not 7' arithmetic and the "
        "declared-false-positive prose were written for two exemptions"
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
    split really is 3 + 2. The CAUSAL split is 2 originating + 3
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
        "RE-MATERIALIZING; the causal split is 2 + 3"
    )
    assert _flat("2 originating + 3 re-materializing") in _flat(
        _doc(STORAGE_CHANGELOG)
    ), (
        "STORAGE-ARCHITECTURE-CHANGELOG.md no longer states the corrected "
        "causal composition (2 originating + 3 re-materializing) — the ledger "
        "is where the count's current form is recorded, so a ledger still "
        "carrying only the wrong composition is the history a reader mistakes "
        "for the claim"
    )
    # The correct split must be STATED, not merely absent: an absence-only pin
    # is satisfied by deleting the classification altogether.
    assert (
        _flat("2 ORIGINATING") in section_13
        and _flat("3 RE-MATERIALIZING") in section_13
    ), (
        "§13 no longer states the causal split (2 originating + 3 "
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
    used to fix three `cache_projection.py:717` citations (`#9403` review, P2).

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
    # (the `STALE_CITATION_PROBE` lesson): `security.py:85` was bare.
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
        "`cache_projection.py:717` citations for exactly this reason: a bare "
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
        "the map-form population shrank below the two shipped sites the "
        "documents now record (5 writers the census sees: 3 named-form + 2 "
        "map-form, i.e. 2 originating + 3 re-materializing; two further "
        "census hits are declared false positives) — if a writer was "
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

    The recorded premises, one per exemption: `supersede_point`'s
    `SET r += $attrs` is on the 2a-DIRECT leg (`rtype` is `type(r)` of the
    `IMPL|NAND` rows) while its about edge is transferred by a BARE `MERGE` —
    so on an about edge it writes nothing and DROPS `confidence` (`#9417`); and
    `_rebuild_all_replay` carries TWO `SET r += $attrs` statements — the
    `DirectEdgeCreated` fold, whose only producer (`create_direct_edge`)
    refuses any `op_type` outside `{IMPL, NAND}`, and the
    structural-`DirectEdgeRepoint` fold, whose interpolated `etype` IS
    allowlisted (`validate_rel_type`) and can be `aboutObject` — while its
    structural transfer is a BARE `MERGE`. Each premise is asserted at the
    STATEMENT, which is where the property would land: a function-wide
    membership test is satisfied by both literals while a `SET` sits on the
    about transfer, and then §13 / §13.8 q2's "it carries no property on an
    about edge" becomes false with every other leg green.

    ⚠️ **The repoint fold's protection is NOT the producer restriction** — that
    covers the creation fold only. It is the gate over a map built from a FIXED
    key vocabulary: a structural repoint descriptor carries none of
    `direction/confidence/weight/label/batch_id`, so the map is empty and
    nothing is written. `_setter_gate_premises` therefore walks EACH statement
    to its OWN enclosing `if` and to the assignment building its map — a
    function-wide `if attrs:` search was satisfied by the creation fold's gate
    and said nothing about the repoint fold (`#9403` review, P3).
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
        "{idx:$idx}]->(tgt) SET r += $props` (`cache_projection.py:717`) "
        "writes a SECOND property name on an allowlisted `aboutObject` edge, "
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
