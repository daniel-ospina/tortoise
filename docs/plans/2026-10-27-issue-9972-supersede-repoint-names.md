<!-- research-path: the issue comment carrying `<!-- research: v1 -->` (https://github.com/daniel-ospina/tortoise/issues/9972#issuecomment-6452195621) -->
<!-- plan-review: cycles=0, status=pending-harness-review, version=2.3.0 -->

# #9972 + #9973 — a supersede's structural repoint descriptor is a MOVE (record side)

**Level:** task · **complexity:** standard · **class:** code
**Branch:** `fix/9972-supersede-repoint-names` · **Paths:** `tortoise/record_projection.py`,
`tests/test_record_projection.py`, `supabase/tests/pglite/validate.mjs`

## 1. The problem, stated as a falsifiable claim

`DirectEdgeRepoint` carries two shapes. The **2a direct** shape names ids and its `src` is
already the successor (`sdk.py`: `src=new_id`), so an upsert at `src` is correct. The **2b
structural** shape names its target by the node's **replay key** (`stub_key`: a `name` for
`aboutSubject`/`aboutObject`/`aboutEvent`/`aboutPoint`, a `url` for `aboutDocument`/
`extractedFrom`) and its `src` is the point being **superseded** (`sdk.py`: `src=old_id`), so it
means **MOVE**. The record applied one rule to both — `RelationUpsert()` at `src`, resolving the
endpoint through `_ENTITY_MATCH_COLUMNS` (object/subject → `("id",)`).

Measured on a real Postgres (PGlite, every migration on disk applied, statements executed one by
one with row counts captured):

| journal | HEAD | after this change |
|---|---|---|
| `EntityLinked(P1 -aboutObject-> obj)` + `supersede(P1→P2)` | **0 rows** planned-and-dropped for the descriptor; the only row sits at `P1` | **1 row**, at `P2`, `tgt_key = obj` |
| 3-link chain `c1→c2→c3` | rows accumulate at each hop, never at the terminal | **1 row**, at `c3` |
| a name held by two live Objects | fan-out onto an arbitrary carrier | **0 rows** (refused, as the graph refuses) |
| `aboutDocument` onto a provenance `:Source` | resolves (no kind test) | **0 rows** (the kind test is load-bearing) |
| a descriptor whose `src` has no supersede | — | **no-op, row byte-identical** (`confidence` kept) |

**Confidence:** high — the first four are measured end-to-end against the shipped schema, and the
falsifier (a write-time move that churns an un-moved row) is the fifth.

## 2. Alternatives considered

| option | why not |
|---|---|
| **Producer** (`sdk.py` 2b: emit `tgt = target.id`) | Rejected. The graph's own replay is the other consumer of this descriptor and resolves by name/url; an id-keyed `tgt` makes `rebuild_all` resolve nothing, and it contradicts `docs/ONTOLOGY.md` §4.4's "never `target.id`". |
| **Widen `_ENTITY_MATCH_COLUMNS`** (`object`/`subject` → `("id","name")`) | Rejected, and named in the code so nobody re-tries it: the table is shared with `EntityMutated`/`EntityLinked`, whose payloads ARE ids — an unranked `id = %s OR name = %s` is a UNION with no precedence and can pick a different row than the fold's `MATCH {id:$id}`. |
| **Target only** (`tgt_match=("name",)` at the call site) | Measured a REGRESSION by the prior lane: same wrong node, `confidence` erased by the conflict path; and it resurrects an edge at a dead point when the journal has no separate carrier. |
| **Read-time filtering** (Zep/Graphiti's invalidation, mem0's ADD-only) | Contradicts a recorded decision — see §5. |
| **Delete at every chain hop, insert at the terminal** (chosen shape) | Mirrors the fold exactly (`_final(src)`), is idempotent, and its no-move case is a no-op rather than churn. |

## 3. Complexity + domains

Standard: one module, two new resolvers, one dispatch branch, one gap in a family the record
already claimed to carry. Domain: `code` (record projection) — no surface change, no migration
(the schema already holds every value the fix needs).

## 4. Integration Surface Map

| surface | layer | proof |
|---|---|---|
| `record_projection.replay_plan` / `mutations_for` | unit (exact SQL) | `tests/test_record_projection.py` (8 new tests) |
| the plan executed against the shipped schema | integration (real Postgres) | `supabase/tests/pglite/validate.mjs` (9 cases) |
| `cache_projection.relation_statement` (record → graph) | mechanical MERGE of stored endpoints | follows from the rows; `cache_projection`'s own suite |
| the graph's own replay (oracle) | docker lane (unavailable on this cell) | `tests/test_pointsuperseded_rebuild.py` — unchanged, still the oracle |

## 5. Research

The `<!-- research: v1 -->` comment (linked above) × this plan: it settles (a) the descriptor's key
is the node's replay key, not its id (`projection/edges.py:92`), (b) the graph's transfer is a bare
`MERGE` at `_final(src)` with no attribute SET (`projection/__init__.py:16868`), and (c) the record
must be derivable from the journal alone (`STORAGE-ARCHITECTURE` §3).

**Contradiction test.** Two recorded decisions cover this work and both are *satisfied*, not
reopened: `docs/ONTOLOGY.md` §4.4 (keys are label-scoped and resolved through the shared
`stub_key`/`resolve_structural_target` resolver, never `target.id`) and `sdk.py`'s `E2E-11.6` (zero
direct edges incident to a superseded point, live AND post-rebuild). The convergent industry answer
(invalidate-and-filter, or accumulate-and-rank) is a *retrieval-scoped* one and contradicts both,
so it is **evidence, not a candidate** — the survey is recorded in the research comment rather than
adopted over a decision.

## 6. Wiring

| call site / consumer | what it does there |
|---|---|
| `_declared_mutations` (`RelationUpsert` arm) | NEW branch: `record_type == "DirectEdgeRepoint"` → `_structural_repoint_statements`; `None` falls through to the old upsert |
| `_structural_repoint_statements` (new) | routes on `edge_type` membership in `_STRUCTURAL_REL_LABELS`; emits `[DELETE at src, INSERT at terminal]` |
| `_supersede_chain_cte` (new) | `WITH RECURSIVE succ…` — `_final(src)` as a `superseded_by` walk, `path`-guarded, depth-bounded, carrying `terminal` |
| `_structural_target_resolution` (new) | `resolve_structural_target` in SQL: `name` (Subject/Object/Event), `props->>'name'` (Point), `url` + `document_kind IS NOT NULL` (aboutDocument), the canonical triple (extractedFrom); `HAVING count(*) = 1` on those FIVE arms, and the resolver's own `prefer`/byte-order `LIMIT 1` on `extractedFrom` — a deliberate single-carrier PICK (the fold's `resolve_source_key` parity), NOT an ambiguity refusal |
| `_terminal_point_sql` (new) + `_TERMINAL_POINT_STATUSES` | `live.is_terminal_status` for the `_src_is_terminal` gate, restated for the STANDALONE load |
| `_relation_delete_statement` | unchanged — the `delete_only` guard keeps its own leg |
| `_relation_upsert_statement` | unchanged — 2a direct + `DirectEdgeCreated` + every other family keep byte-identical SQL |
| `_ENTITY_MATCH_COLUMNS` | **unchanged**, and now carries the comment saying why widening it is wrong |
| `_LABEL_TABLES`, `_endpoint_resolution` | read-only reuse |
| `supabase/tests/pglite/validate.mjs` | new suite; runs `replay_plan` from the module by file path (never a copy) |
| `tests/test_record_projection.py` | 8 new tests; two of them pin the restated constants to `live.py` / `projection/edges.py` |

## 7. Implementation steps

1. `_STRUCTURAL_REL_LABELS` + `_TERMINAL_POINT_STATUSES` + the two pin tests. Prove: the pins red
   if either restatement drifts.
2. `_supersede_chain_cte` + `test_the_structural_move_reads_the_records_own_successor_chain`.
3. `_structural_target_resolution` + the name/ambiguity/`props->>'name'`/document tests.
4. `_structural_repoint_statements` + the dispatch branch + the two acceptance tests (this issue's
   and #9973's, adapted: the move is two statements in one plan).
5. The PGlite leg: 6 measured cases, including the no-supersede no-op and a CYCLIC chain
   (no destination ⇒ nothing moves).
6. Mutation proof: neuter the routing branch → the two acceptance tests go red (measured).

## 8. Rounds

**Rounds:** 0 pre-code verifier rounds (this lane has no `task` tool; the harness reviewer at the
exact PR head is the review gate, and self-review is not a review).

**Review round 2** (fresh-context reviewer at `baddcd57f`): 3 findings, all verified against the
primary source before being applied, all fixed here:

- **the DELETE and the INSERT were gated on different conditions.** `moved` required "terminal src +
  HAS a successor", which a CYCLIC (or >`_SUPERSEDE_CHAIN_DEPTH`) chain satisfies while the walk
  reaches no `next_id IS NULL` row — so the DELETE ran and the INSERT wrote nothing, contradicting
  this module's own "a cycle ⇒ no destination ⇒ nothing moves". Fixed by making the destination a
  third conjunct of `moved`; mutation-proved (drop it → PGlite case 6 measures `rows = []`).
- **two PGlite negative cases asserted only `!rows.some(<wrong row>)`**, which `[]` satisfies, so a
  delete-without-insert passed them. Both now assert the EXACT surviving row, as case 5 already did.
- **the terminal predicate's flag leg was dead.** It read `props ->> 'outdated'`, but the record
  writes `outdated` as a COLUMN (`PointSuperseded`/`PointInvalidated` literals; `_split_properties`
  routes a payload key to the column when the table has one — measured:
  `INSERT … (…, status, outdated, props) VALUES (…, 'live', true, '{}')`), so a `PointInvalidated`
  point read as LIVE here and terminal in the fold. The set was also pinned to
  `TERMINAL_STATUS_VALUES`, one member narrower than the graph's `_terminal_excluded`
  (`| {deprecated}`), so the pin certified the drift it existed to catch. Both fixed; the pin now
  compares against `TERMINAL_EXCLUDED_STATUSES` and asserts the column form AND the absence of the
  dead `props` form.

Also fixed while touching `tests/test_record_projection.py`: the import block ruff flagged (`I001`,
pre-existing on this branch).

## Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 **an un-moved edge is churned** (delete + re-insert on a point that has no successor, erasing
  `confidence` — the measured failure of the rejected target-only fix) → PGlite case 5 asserts the
  row is byte-identical (`confidence = 0.9`) and the plan-seam test asserts BOTH statements carry
  the terminal + successor + destination gate; deleting that gate reds
  `test_a_structural_repoint_moves_its_edge_to_the_final_successor`.
- R2 **a move lands on an arbitrary carrier** when a name is held twice (#5509) → `HAVING count(*) =
  1` on the four name-keyed labels and on `aboutDocument`; PGlite case 3 asserts the ONE row
  survives untouched at the superseded point;
  `test_a_name_held_by_two_carriers_resolves_to_nothing`.
  ⚠️ The `extractedFrom` arm is the exception, stated rather than implied: it resolves through
  `_endpoint_resolution`'s `ORDER BY … LIMIT 1`, i.e. it deliberately PICKS one carrier among
  Sources sharing a canonical form — exactly what the fold's `resolve_source_key` does
  (`ORDER BY s.url LIMIT 1`), because two Sources with the same canonical identity are the same
  identity, not an ambiguity. The refusal discipline belongs to the name-keyed arms, where two
  carriers holding one `name` really are two different nodes.
- R3 **the 2a direct family regresses** (its `src` is the successor; a delete there removes the edge
  the descriptor exists to re-create) → `test_a_direct_edge_repoint_is_still_a_plain_upsert` +
  PGlite cases 1–2 exercising the structural route only.
- R4 **the record is looser than the fold** on the Source arms (`aboutDocument` on a provenance
  `:Source`; an `extractedFrom` fan-out) → `test_aboutdocument_stays_resolve_only_and_document_only` +
  PGlite case 4 (the exact surviving row, not merely the absence of a row at the successor).
- R5 **the restated constants drift** from `live.py` / `projection/edges.py` → the two pin tests,
  against `TERMINAL_EXCLUDED_STATUSES` (not `TERMINAL_STATUS_VALUES`: the graph's gate is
  `_terminal_excluded`, which also excludes the legacy `deprecated`).
- R6 **an id-reuse point loses its own edges** (`_src_is_terminal`) → the terminal gate is asserted
  present in both statements; the #2488 survivor-filter asymmetry is NAMED in the code and in §Gaps
  of the research block rather than silently assumed.

**Out of scope — must NOT be raised** (note, file, do not chase): the 2a direct family's `_final`
chain accumulation (its descriptor carries no old endpoint — filed separately); `#10139` (the
descriptor carries no `attrs`, so a moved row lands with `props='{}'`); any pre-existing
`_ENTITY_MATCH_COLUMNS["source"]` fan-out outside the repoint family; the graph-side docker lane,
which is unavailable on this cell.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared
surface.
