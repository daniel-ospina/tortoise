---
title: "#11469 — curating our own memory: the inventory, and the migration harness"
type: design
domain: platform
doc_status: live
created: 2026-11-08
subjects.team: epistemic-team
aboutSubjects: Tortoise dogfood graph, curation pass
aboutObjects: "#11469, #10175, #5333, #10000"
---

# #11469 — curation: inventory first, migration second

The owner's ruling (2026-11-07, recorded on `#11469`) is the frame for everything below:

1. **keep** the old graph — a historic artifact; not pruned, not "cleaned", not backfilled;
2. **migrate the useful data out** of it into the clean pack-filed graph ← this issue;
3. optionally relocate/relabel it as an archive afterwards;
4. **delete** it only after the new extractor has been measured against it (`#10175` step 5).

Two measured facts govern the risk:

* the old graph is **at its node cap** (`25000 / 25000`) and therefore already refuses writes;
* **the Postgres record holds 0 rows for that tenant**, so the graph is the *only* copy —
  a migration here is moving the only copy of a tenant's data.

Code: `tortoise/curation.py` (classification + guards + additive writer) and
`tools/curate_graph.py` (the CLI). Tests: `tests/test_curation_11469.py` — ⛔ no case count is quoted
here on purpose: the file gains cases in every review round, so a hand-written count re-stales on the
next one. Not even as an illustration — an example count is still a count. `tools/ci_selection.py
--integrity` is the authority, matching the policy stated on this suite's own `durations` row in
`config/ci-surfaces.yml`.

## The inventory — measured 2026-11-08 against `live-v4`

`live-v4` is the graph the credential is bound to; it is the capped dogfood graph
(`org_3326a01ea34ae595d84de5d8f9_g_2b7e4c9d1a6f8035`). Every number below is a read
(`tortoise_graph_overview` `status`/`structure`/`sources`/`audit`, plus the per-kind totals of
`tortoise_query`), reproducible with `uv run python tools/curate_graph.py inventory --graph live-v4`.

### By node type

| node type | count | note |
|---|---:|---|
| **Point** | **19,595** | 15,868 non-operator + **3,727 operators** |
| · non-operator Point | 15,868 | `structure.total`; the paginated read returns 15,842 live/non-retracted rows |
| · operator (IMPL/NAND/…) | 3,727 | **count only** (`structure.operators`) — the read path excludes operator Points unless an operator kind is requested (`sdk._operator_scope_clauses`, #7108) |
| **Object** | **5,379** | **count only** (`counts.Object`) — no listing surface on the tool set |
| **Event** | **4,670** | **count only** — agent-session containers (episodic tier) |
| **Source** | **1,578** | **enumerated, but only the `documentKind IS NULL` population** (`sdk.list_sources`) — a Document-kind Source is not listed by this read path, so the report reconciles `sources_read` against `counts.Source` instead of calling it complete. **100% `agentSession`, `session:<uuid>` urls**, zero real artifacts |
| **Subject** | **26** | **count only** |
| edges | 7,981 | `audit.edge_count` |
| **total nodes** | **31,248** | non-episodic cap counter = 19,595 + 5,379 + 26 = **25,000 / 25,000** |

### By class, with a judgement

The judgement is produced by the rules in `tortoise/curation.py`; every rule is quotable, and a
row that no rule decides is `undecidable` rather than guessed. The JSON **report** is aggregate
(census by `node_type:kind` × disposition); the row-by-row reasons are a separate artifact,
`plan --rows <path>` (JSON Lines: node id, disposition, the rule's reason, pack, **and the row's
`content`** — so a decision can be re-derived from the artifact alone, not just looked up).

| class | count | judgement | reason |
|---|---:|---|---|
| `point:decision` | 7 | **carry** | owner-level decisions — the curated graph exists to keep them |
| `point:option` | 2 | **carry** | options hang off decisions |
| `point:finding` | 1 | **carry** | durable finding |
| `point:jobToBeDone` | 1 | **carry** | strategy artifact |
| `point:useCase` | 1 | **carry** | strategy artifact |
| `point:evidence` | 120 | **carry** (115) / not-carried (5) | evidence is signal; the 5 are exact duplicates of a carried evidence row (the keeper carries it) |
| `point:diary` | 2 | **undecidable** | agent diary: authored, but narrative-shaped |
| `point:statement` | 15,708 | **not-carried 58 / undecidable 15,650** | 58 match a turn-narration marker (`curation.NARRATIVE_MARKERS`, pinned by `test_narrative_statement_is_not_carried`); the other 15,650 are claims this rule set cannot judge |
| `source:agentSession` | 1,578 | **undecidable** | §9.1/D1 (`#10000`) says the raw tier lives in Postgres — but this tenant's record holds **0 rows**, so "do not carry it" would leave the transcript with no home |
| `object` (bare definite) | majority of 5,379 (**not enumerated** — sample only) | **not-carried** | `#11021`/`#11211`: a noun phrase that refers to nothing is not an entity. Biased 1,000-row sample: **96%** drop-shaped; `#5333`'s full census on the same graph: **83%** of 7,870 objects |
| `event` (session container) | 4,670 | **undecidable** | same only-copy problem as Sources |
| `operator` | 3,727 (**not enumerated** — count only) | **carry** (rule) | the rule carries the operator row as a claim; edges are the ingest contract's job (#888 W4), not built here |

The dispositions over the **17,420 enumerable rows** (15,842 points + 1,578 sources) must add up to
that population, and they do: **127 carry · 63 not-carried · 17,230 undecidable** (7+2+1+1+1+115
carry; 5 duplicate evidence rows + 58 narration statements not-carried; 15,650 statements + 2 diary
rows + 1,578 sources undecidable). These are the numbers the run reported and the ones posted on
`#11469`; the class table above is the same measurement broken out by kind, so the two must be read
as ONE number set — if they ever disagree, the run's report is authoritative and the table is stale.

### Defects already measured on the source (audit, read-only)

| check | count | severity |
|---|---:|---|
| `missing_sourceKind_source` | 5,073 | medium — every session-backed evidence path resolves to a neutral tier |
| `superseded_active_edges` | 5 | medium |
| `impl_instead_of_nand` | 2,603 | low — keyword heuristic |
| `missing_sourceKind` | 71 | low — legacy evidence |
| **total** | **7,752** | none `high` |

### ⚠️ The finding that decides the shape of this work

**A deterministic rule set cannot select the signal out of 15,708 statements.** Only 58 of them
match a narration marker — those are `not-carried` — and the remaining **15,650** match no rule at
all. The non-statement kind rules (decision 7, option 2, finding 1, jobToBeDone 1, useCase 1,
evidence 115) select the 127 carried rows. Operators (3,727) are carried by a rule too but are not
enumerated by this read path, so they are not part of that 127. The 15,650 unjudged statements are
claims, plausible and unclassified. Two options, and this issue does not choose between them:

1. a **semantic pass** (search/embedding-similarity clustering + a reviewer) that proposes a
   carry-set for review; or
2. **incremental curation**: migrate the decided slice now (the 127 carried rows + the operators the
   rule carries), and let the statement bulk be curated as the fleet writes new memory into the clean
   graph anyway. Subjects are NOT in that slice: the read path cannot enumerate them
   (`NOT_ENUMERABLE["subject"]`), so no Subject row ever enters a plan — the 26 in the graph are
   counted, never classified.

What the tool guarantees either way: a run only ever carries rows whose disposition is `carry`,
and every other row's reason is recorded per row in `plan --rows <path>` (the aggregate report
carries the counts, not the rows). Nothing is dropped from the old graph — it stays whole.

## What the harness guarantees (and how each is proven)

⚠️ **One consequence of the read surface, stated so the census is not over-read:** `tortoise_query`
returns non-operator Points only, and Objects/Events/Subjects have no listing tool. So the enumerable
population this pass classifies is **15,842 points + 1,578 sources**; operator/Object/Event/Subject
rows appear in the tables as **counts from `graph_overview`**, with the class rule stated, and are
flagged *not enumerated* rather than presented as measure-and-classify results.

| property | mechanism | test |
|---|---|---|
| the old graph is only read | the module has no destructive call at all; the reader exposes no write verb. The property is the WRITER's verb list — a bundle-text grep for "delete" cannot observe a destructive call | `test_writer_protocol_declares_no_destructive_verb` (property), `test_bundle_carries_no_deletion` (supplement) |
| a read that refuses is never census data | every `graph_overview` section read (including the credential-bound retry) is unwrapped and FAILS CLOSED on an error envelope — an envelope falling through `_rows_from_tool` reads as "0 rows"; a refusal on a `tortoise_query` PAGE is terminal wherever it lands, including on a later page with rows already collected (the case no total-shortfall check and no re-read of the last SUCCESSFUL page can see); a transport failure RAISES rather than re-reading a different graph (only a recognised graph refusal may fall back, and notes accumulate per section); `sources_read` is reconciled against `counts.Source` via a reported `source_count_gap`, never called complete | `test_an_error_envelope_is_never_reported_as_zero_rows`, `test_a_refusal_on_a_later_page_is_never_a_complete_census`, `test_a_transport_failure_does_not_fall_back_to_another_graph`, `test_a_graph_refusal_does_fall_back_and_says_so_once_per_section`, `test_the_inventory_report_names_the_source_filter` |
| the target is only added to | `PlanWriter` is a **single-verb** protocol (`write_sections`, which writes each bundle SECTION — `points` and `sources` — because a carried Source sent as a Point is re-typed, and a bundle that only ever sent `points` reported success while writing none) — additivity is a property of the type, not of the caller's discipline | `test_writer_protocol_declares_no_destructive_verb`, `test_the_wire_carries_the_sources_section_not_only_points` |
| **idempotent** | stable `migrationKey` per content hash → target-side dedup (`ingest` folds by content hash + kind) **and** a local ledger that skips a migrated key before it reaches the wire, **scoped to the target's resolved identity refs** — one graph has one scope for every spelling the `graphs` surface EXPOSES: the scope is the literal spelling plus the `graph_id`/`stable_id`/`namespace` of the row that spelling resolves through (`_ledger_row`, which matches EXACT identity fields only). ⛔ THREE values were each measured to invert this guard's error direction from *refuse* to *skip* (`applied: True, written: 0`, exit 0 — a target that owed every row reported as migrated), and none may enter the set or the recorded key: a `_tail`-derived identity (a bare tail is id-SHAPED but `mcp_auth` resolves a bare token by graph NAME, so graph A's tail `x` matched a request for a DIFFERENT graph named `x`); a `name` at all (a name is not an identity, and the shipped `graphs` shape `{"namespace": s}` loses `graph_id`/`stable_id`, so a name equal to a graph whose id is not exposed looked unique); and the `default` sentinel (workspace-universal, while the ledger file is per-user — a target identified only by it is REFUSED, like any unproven identity). A value two rows share is also excluded (`_refs_unique_to`). A spelling the surface cannot resolve is refused by the identity guard before any write, and an applying run whose `graphs` section is EMPTY is refused for the same reason — with no identity rows the guard falls back to shape and accepts any id-shaped ref. ⛔ One residual, in the SAFE direction and unreachable on today's `list[str]` surface: a target the surface resolves ONLY by graph NAME (possible only if the `graphs` section starts returning dict rows) is accepted by the guard but scoped literally, so the next run under another spelling RE-SENDS every row — never a skip. Resolving is `_ledger_row`'s job and it matches EXACT id fields only, deliberately: a name may name a graph without identifying it. A spelling-keyed ledger, by contrast, made `--target org_x_g_new` and `--target g_new` two targets, re-sending every row and leaving idempotency to a target that is not declared idempotent; the planner and the writer derive that key IDENTICALLY — stringified, so a hand-built row carrying `5` is the same key as the writer's `"5"` — because two derivations leave a row pending forever and re-send it on every run The **keeper** for a content hash is the row of record, so a row that HOLDS content displaces an incumbent that holds none — otherwise an empty row that merely arrived first would be reported as the row of record for text it does not contain, and the only copy would be left behind while the artifact said it was accounted for — and a hash that two rows share while their CONTENT differs establishes no duplicate at all, so the second text is `undecidable` (a per-row decision) rather than an "exact duplicate" whose keeper is claimed to be its row of record. The keeper is identified by its **position in the read**, never by `node_id` — point ids are not unique, so a row sharing the keeper's id was being appended the keeper's OWN decision and its text vanished with no `--rows` line at all | `test_second_run_writes_nothing`, `test_duplicate_follows_its_keeper`, `test_a_non_string_migration_key_is_stringified_on_both_sides`, `test_a_content_holding_row_displaces_an_empty_keeper`, `test_a_stale_hash_never_claims_a_row_of_record_for_another_text`, `test_two_rows_sharing_an_id_and_a_hash_cannot_hide_the_second_text`, `test_the_promotion_loop_identifies_the_keeper_by_position_not_by_node_id`, `test_a_re_run_under_another_spelling_of_the_target_still_skips`, `test_the_cli_hands_the_graph_rows_to_the_ledger_scope` (the shipped CLI wiring, not just the unit seam), `test_a_ledger_scoped_to_another_graph_is_still_refused`, `test_the_ledger_never_matches_a_different_graph_by_a_bare_tail`, `test_a_name_that_equals_another_graphs_id_is_not_a_match`, `test_the_ledger_never_records_the_default_sentinel`, `test_the_cli_refuses_a_target_spelling_the_graphs_surface_cannot_resolve`, `test_a_name_never_scopes_a_ledger_even_when_the_id_is_not_exposed`, `test_the_default_sentinel_cannot_scope_a_ledger`, `test_an_applying_run_with_no_identity_rows_is_refused`, `test_the_ledger_resolves_field_major_like_the_write_path`, `test_a_name_spelled_target_scopes_literally_and_never_skips` (a name never enters the match set; the disjoint scopes refuse rather than skip) |
| **pack-filed** | the destination is the pack-FILED graph, so pack loss must be observable: the catalog mapping is read from its manifests and a load failure degrades to an empty map *without* blocking the inventory, but the cause is carried on the plan (`pack_catalog_note`) and reported by the census over the rows that will be **written** (`carried_rows`, `rows_without_pack`). Counting every decision instead made the field a function of the population deliberately left behind: on the full census it read 17,418 unfiled of 17,420, so a total catalog loss moved it by 2 rows out of 17,420. Counted over the write set it reads what the carry set owes — **125 unfiled of 127**, because only the strategy kinds (`jobToBeDone`, `useCase`) have a pack owner — so catalog loss moves it by those 2 rows out of 127, which is the whole movement a healthy run can show. The field is a pack-loss detector, not a coverage metric: the 125 name a real gap in the packs (no pack declares `decision`, `option`, `finding` or `evidence`) and it is reported, not explained away | `test_a_total_pack_catalog_failure_is_visible_in_the_census`, `test_an_unknown_kind_prefix_is_not_pack_ownership` |
| **off-switch** | `TORTOISE_CURATE_OFF=1` / `--off`, checked before the run **and before each write batch**, through the SHIPPED default (`IngestWriter`'s own `armed`), not only through an injected callable; a run disarmed BEFORE it starts is a success (exit 0 — the operator asked for it, and the switch is consulted BEFORE the identity guard, so a disarmed run with an unresolvable target is still disarmed), a switch that flips MID-run is an exit 3. ⛔ The switch is resolved BEFORE the census and takes PRECEDENCE over EVERY read failure in `plan` (points, sources, status, graphs): a disarmed run is still exit 0 and the artifact says `census_measured: false` with the reason in `read_failure` (or, when the reads succeeded but the SOURCE population was unreconcilable, in `read_note` — `census_measured` is false there too, on every disarmed arm) — an absent census is never a zeroed one — while an ARMED run still raises | `test_explicit_off_switch_writes_nothing`, `test_env_off_switch_writes_nothing`, `test_the_shipped_off_switch_watch_disarms_a_live_run` (no injected `armed=`), `test_a_disarmed_run_is_not_reported_as_a_failure` (rc 0 + "disarmed"), `test_a_disarmed_run_over_an_unmeasurable_census_still_exits_zero` (rc 0, refusal in `read_note`), `test_a_disarmed_run_over_an_unmeasurable_points_read_exits_zero` (rc 0, `census_measured: false`), `test_an_armed_run_over_an_unmeasurable_points_read_still_fails` (the counter-arm), `test_a_disarmed_run_over_a_damaged_ledger_still_exits_zero` (rc 0, `census_measured: true` — the switch is total, and the two losses are not reported as one), `test_a_disarmed_run_over_a_damaged_ledger_reports_what_it_still_owes` (the three counters SPAN the carry set with `pending` = the carry count — never the 0 that reads as "nothing owed" beside a measured census), `test_a_disarmed_run_over_a_damaged_ledger_keeps_the_unmeasured_source_fact` (`census_measured: false` + the reason in `read_note` when that arm ALSO carries an unreconcilable source listing), `test_a_disarmed_run_with_an_unresolvable_target_is_still_disarmed` (the switch is consulted BEFORE the identity guard), `test_the_cli_exit_codes_pin_every_failure_arm` (a mid-run stop is exit 3) , `test_every_plan_payload_says_whether_the_census_was_measured` (`census_measured` on EVERY payload, not just the failure arms) |
| every carried row reaches the wire | `bundle()` REFUSES to build when a carried row has no wire section (point/operator → `points`, source → `sources`), and an unrepresentable row is `undecidable`, never `carry`; a result the pass cannot interpret (a `str`/`list`, which `normalise_tool_result` passes through) is a REFUSAL, not a write | `test_a_bundle_refuses_a_carried_row_with_no_section`, `test_a_carried_event_is_undecidable_not_silently_dropped`, `test_a_text_result_this_pass_cannot_interpret_is_a_refusal`, `test_the_wire_carries_the_sources_section_not_only_points`, `test_the_wire_never_carries_a_migration_key_on_a_source`, `test_a_source_is_written_once_because_the_ledger_key_is_the_same_one` |
| **source ≠ target** | compared on the CANONICAL id, where every accepted spelling (graph id, `stable_id`, namespace, case-insensitive NAME — the ID spellings are matched EXACTLY, as `mcp_auth` does, because casefolding them let `Default` be one row's graph id in the guard and another row's name on the write path — and the tenant graph's `org_<id>` / `team_<id>` pair) collapses to that graph's id. **The rows are the authority**: when graph rows are available, an id-shaped spelling that no row names is UNRESOLVED (a refusal), because the surface hands out namespace strings and `mcp_auth` accepts a `stable_id` that a namespace-only row does not expose — a shape-only comparison let `--graph g_<tenant-gid> --target team_<org>` pass the guard and write IN PLACE. An ambiguous name is also a refusal (fail closed), the `default` sentinel is a marker rather than an identity (it falls through to the row's real spelling), two rows that disagree about what one ref names are REFUSED rather than first-matched, and when the two refs resolve through rows exposing DIFFERENT identity fields the guard refuses too — the two canonicals could then differ for ONE graph (a tenant row carrying `stable_id` but no namespace, beside a row carrying only the namespace), which is the in-place fail-open — **and the SAME-field case is refused as well**: two rows that share an identity value while their canonicals differ spell one graph two ways, and neither the ambiguity check nor the shape check can see it (each ref matches exactly one row, and the shapes are identical), which measured 7 in-place fail-opens out of 2,162 oracle-resolved row sets; a row set that puts a BARE NAME in `namespace` is refused outright, because `MCPReader.graphs()` wraps every returned string as a namespace value, so `{namespace: org_o1_g_old}` beside `{namespace: live-v4}` is shape-identical, shares nothing, and names ONE graph. CI: the new test file is registered in `config/ci-surfaces.yml` (`tools/ci_selection.py --integrity`) — an unregistered test file reds the required aggregate | `test_source_equals_target_refused`, `test_source_equals_target_refused_across_spellings`, `test_the_graph_name_is_the_same_graph_as_its_namespace`, `test_namespace_and_gid_spellings_of_one_graph_are_refused`, `test_no_rows_does_not_fail_open`, `test_unresolvable_target_is_refused_fail_closed`, `test_ambiguous_name_is_refused`, `test_a_name_the_surface_cannot_resolve_is_refused`, `test_the_tenant_graphs_id_spelling_is_not_treated_as_a_distinct_graph`, `test_an_id_no_row_names_is_unresolved_when_rows_are_available`, `test_the_default_sentinel_is_not_an_identity`, `test_rows_that_disagree_about_one_ref_are_refused`, `test_one_graph_split_across_two_rows_with_different_fields_is_refused`, `test_a_self_contradictory_row_set_cannot_prove_two_graphs_distinct`, `test_a_row_set_that_puts_a_bare_name_in_namespace_cannot_prove_distinctness`, `test_the_graph_name_resolves_to_the_same_canonical_as_its_namespace`, `test_the_tenants_two_rows_cannot_hide_one_graph_behind_the_name_field`, `test_a_case_varied_ref_cannot_be_one_rows_id_and_anothers_name`, `test_a_matching_row_with_no_establishable_canonical_is_a_refusal_not_a_skip` |
| dry by default | `--apply` is required; the default run writes nothing, and it still reports `skipped_known`/`duplicate_rows`/`pending` from the ledger so `skipped + duplicates + pending` SPANS the **carry set** — `skipped + duplicates + pending == len(plan.carry_rows())`, which is the 127-row subset and NOT `census.total_rows` (17,420): a driver reconciling the three counters against the census in the same payload would read a 17,420-vs-127 gap as row loss. Those are **three** counters and they reconcile in every mode (dry, apply, re-run, partial refusal, mid-run stop); `written` is a **subset of `pending`** — the rows the target accepted — so it must never be added to them: an apply over 2 carry rows reports `written=2 / pending=2`, whose four-way sum is 4, not 2. The LEDGER decides the split even on a pre-write refusal | `test_dry_run_writes_nothing`, `test_a_dry_run_reports_what_the_ledger_already_holds`, `test_the_counters_span_the_plan_in_every_mode`, `test_a_refused_run_still_reports_what_the_plan_owes`, `test_a_refused_run_over_a_landed_plan_owes_nothing` |
| a refusal is never recorded as a write | the writer unwraps the tool result (a `CallToolResult` OBJECT, not a dict — a dict-only check never sees a refusal), raises on a refusal envelope, and the ledger is written from the ACCEPTED keys only, scoped per target graph; a partial run reports what already landed | `test_a_refused_write_is_never_recorded`, `test_ingest_refusal_envelope_is_not_a_success`, `test_a_calltoolresult_object_is_unwrapped_and_a_refusal_refuses`, `test_a_refused_second_batch_reports_the_first_batch_and_records_it`, `test_each_refusal_envelope_key_refuses_on_its_own`, `test_ledger_is_scoped_to_its_target` |
| the bundle is ingest-valid | every point item carries `content` (ingest Phase-1 refuses a bundle otherwise) **and** the bundle mints **no** bundle-local `ref` at all — a ref is expanded wherever it appears as a field value, so any ref can collide with a row's own content/id and make the whole bundle uncanonicalisable (`ref == migrationKey` did exactly that) | `test_bundle_items_carry_content`, `test_bundle_survives_the_ingest_canonicaliser`, `test_a_row_whose_content_looks_like_a_bundle_ref_cannot_cycle`, `test_the_bundle_passes_ingests_phase1_validator` (the last one calls the REAL `sdk._validate_bundle`) |
| no silent zero | a read that returns 0 rows, or fewer than the reported total, **fails** — an empty census must never look like "nothing to migrate" — for the SOURCE section too: an unreadable listing shape — including a COUNT-ONLY payload that reports rows (only a count of 0 is consistent with none) — and a 0-source census with no `counts.Source` to reconcile it are both refusals, while a graph that really has no sources (`counts.Source == 0` → gap 0) is allowed; a refusal delivered as an error ENVELOPE (this surface's normal shape) also fails and NAMES its cause, rather than re-reading a different graph; and a page walk whose population CHANGES mid-walk is refused: a total that moves, the SAME row — id AND content — served by an earlier page, or a first/last page that no longer matches when re-read — and, because a snapshot taken after the churn cannot see it, a **second full walk** whose row sequence must match (default on; `--no-verify-read` trades that away for speed), while two distinct nodes sharing one id are a legal shape and are not — unless the SAME row — id AND content — is served again at a LATER offset, which is a repeat rather than a second node and fails closed (an identical pair **within one page** is two nodes, not a shift: the repeat check is gated on the offset having moved), because an offset walk over a shrinking set both skips a live row and re-reads a tombstone while the counts still match | `test_the_entry_point_reader_refuses_a_zero_or_short_point_read`, `test_a_points_refusal_names_its_real_cause`, `test_an_error_envelope_is_never_reported_as_zero_rows`, `test_a_quota_envelope_does_not_re_read_a_different_graph`, `test_a_shifted_pagination_is_refused_not_silently_partial`, `test_a_row_served_twice_is_refused_even_when_the_total_is_stable`, `test_a_same_size_churn_mid_walk_is_refused`, `test_a_churn_midway_through_the_walk_is_caught_by_the_page_recheck`, `test_a_totals_move_on_a_SHRINKING_population_is_refused_without_the_verification_pass`, `test_a_content_change_only_the_second_pass_can_see_is_refused`, `test_two_nodes_sharing_an_id_in_one_page_are_not_a_pagination_shift`, `test_two_nodes_sharing_an_id_across_pages_are_not_a_pagination_shift`, `test_a_truncated_last_page_whose_total_moved_is_refused`, `test_an_unreadable_source_listing_is_a_refusal_not_an_empty_census`, `test_a_zero_source_census_with_nothing_to_reconcile_it_is_refused`, `test_a_genuinely_empty_source_population_still_passes`, `test_a_count_only_source_listing_with_no_rows_is_refused`, `test_a_count_only_listing_that_says_zero_is_a_readable_empty_listing` |

## How to run

```bash
# inventory (read-only) — the table above (a name is fine here: nothing is written)
uv run python tools/curate_graph.py inventory --graph live-v4 --out /tmp/inventory.json

# plan against a target (writes nothing without --apply)
uv run python tools/curate_graph.py plan \
    --graph org_<org>_g_<source-gid> --target org_<org>_g_<clean-gid> --out /tmp/plan.json

# apply (additive, idempotent, off-switchable). BOTH graphs must be given in a
# spelling a GRAPH ROW exposes — in practice the namespace (org_<org>_g_<gid>).
# A bare NAME (live-v4) and a bare `g_<gid>` are REFUSED: this surface returns
# namespace strings only, and the guard must not guess an identity it cannot see
# (an id it cannot resolve could be the source's OWN id — the fail-open that wrote
# in place). --graph is mandatory for --apply, so the source identity is always the
# graph that was actually read.
uv run python tools/curate_graph.py plan \
    --graph org_<org>_g_<source-gid> --target org_<org>_g_<clean-gid> --apply
```

Environment: `TORTOISE_MCP_URL` + `TORTOISE_MCP_API_KEY`; `TORTOISE_CURATE_OFF=1` disarms writes.

### Exit codes — the contract a driver may switch on

| rc | means |
|---:|---|
| **0** | the run did what was asked: `inventory`, a dry `plan`, an operator-DISARMED run (the off-switch, asked for on purpose), an apply that completed, or an apply that owed the target nothing (idempotent re-run) |
| **2** | a USAGE error, decided before any read: `--page-size 0`, `--apply` without `--graph`, `inventory` given a `plan`-only flag |
| **3** | a real failure: the identity guard refused (never an in-place write), the target refused the write, the off-switch flipped mid-run, or any stop/refusal/no-op — reached before OR after the first batch |
| **1** | ⚠️ **not part of the designed contract** — an UNHANDLED failure. A read the surface refuses raises out of `MCPReader`'s section gate (`_checked`, `read_points`) before any handler, so `inventory` and `plan` exit 1 with a traceback. A driver should treat 1 as "the run crashed" and 3 as the designed failure rc |

In the JSON report, `run.applied` follows what the target ACCEPTED: `true` means the apply leg
landed (or there was nothing left to write), `false` with `run.refused` **or** `run.stopped` set
means nothing was written — so a driver that reads the field instead of the exit code cannot read a
refused or stopped migration as a finished one. A partial run (some keys accepted, then a refusal or
a stop) keeps `applied: true` and reports the accepted keys in `run.written`, which is a subset of
`run.pending`, never a fourth counter to add in. The one remaining shape — a writer that accepts
nothing and neither refuses nor stops — is not producible by `IngestWriter`; it reports
`applied: false` and exits **3** under its own `NO-OP` message rather than 0.

### Recorded, not changed here (round-33 review follow-ups)

Three follow-ups from the review are deliberately left as they are. They are recorded here, next to
the guarantees, because a review comment is not an artifact the next lane reads:

* **a carried row's `kind` is emitted verbatim** — including a pack-namespaced spelling
  (`core:decision`) and kinds no pack declares (`finding`). Stripping the namespace here would
  silently re-type rows the source declared namespaced, on a write path whose contract is fidelity.
  Ingest does not silently drop either spelling, but by two DIFFERENT mechanisms: a bare
  unrecognised `pointKind` is **WARNED** about and stored, while a pack-namespaced kind the target
  has not installed is **REFUSED** loudly (`_refuse_unapproved_kind`). Either way no row is lost —
  but "refused" is not "warned", so a namespaced spelling is not free. Which spellings the clean
  target must accept is the clean graph's vocabulary decision (`#10175` step 2).
  ⚠️ **Classification is NOT spelling-invariant, despite the bare-kind rule.** `classify_point`
  compares `Row.bare_kind`, but `classify_source` and `classify_event` compare the EXACT
  `row.kind`: a `core:agentSession` source falls through `classify_source`'s session arm to
  `carry`, where a bare `agentSession` is `undecidable` (§9.1/D1). No live row moves today — the
  census's source rows are all bare — but a lane that namespaced a kind on the strength of "the
  rules use the bare kind" would turn a decision into a carry.
* **the `_g_` split is on the LAST occurrence, so a graph id that itself contains `_g_` is
  TRUNCATED** (`org_o1_g_x_g_y` and `g_x_g_y` both yield `y`). That truncation is fail-closed by
  construction, not by accident: it collapses every spelling of one id to the SAME tail, so the
  guard never splits one graph in two, and its only failure direction is that two DIFFERENT graphs
  whose ids share that tail compare equal — which refuses a correct run, and can never pass an
  in-place write. Narrowing it would have to know which ids may contain `_g_`, which no surface
  here guarantees. An **empty** tail (an id ending in `_g_`) is not a spelling of anything and
  canonicalises to `None`, so it refuses rather than falling back to the raw spelling — the
  fallback was the one way this guarantee could have been false.
* **two identity clauses are redundant** with `canonical_graph_id`'s `return None` catch-all ("the
  rows are the authority"): the `len(hits) > 1` refusal and the ambiguous-name one below it. They
  are deliberate defence in depth. The ambiguous-name clause is removable with no behaviour change
  at all; the `len(hits) > 1` one is neutral only where no single row's *name* also matches the
  ref — otherwise deleting it falls through to the name branch and resolves where it refused. That
  is why they need a comment rather than a pin. Their job is to keep refusing on the day that
  catch-all is refactored, when falling through would first-match and become the in-place
  fail-open.

⚠️ **Known operational limitation, measured 2026-11-08:** `tortoise.mcp_client.call_tool` opens a
fresh HTTP session per call (by design — the credential is resolved per request, #10605), so a full
`plan` read is ~32 page reads for the 15,842 points — and **~68 calls with the default
verification pass** (the second full walk plus the two page re-reads), which is the number to
schedule against: the hosted endpoint can answer `429 Too Many Requests` part-way. The run then fails loudly rather than reporting a partial census (the reader raises), and
nothing is written — but a production run should be scheduled rather than fired in parallel with a
busy fleet.

## Why this pass has not written anything yet (three blockers, all measured)

1. **There is no clean target graph.** `#10175` step 2 ("start a new graph") has not happened; the
   workspace's graphs today are `default`, `acl-verify-20261027` and `live-v4`. Creating the
   target is `#10175`'s step, not this issue's.
2. **The MCP credential is graph-bound to `live-v4`** — it cannot write to another graph, and
   `live-v4` is at its cap, so a write there is refused by the venue's node cap (the credential
   cannot be re-targeted: `tortoise/mcp_auth.py::_retarget_refusal`). A graph-bound
   credential refusing a re-target is a property of the venue, not a bug to route around.
3. **15,650 statements have no deciding rule** (above; the other 58 are not-carried by the
   narration rule) — carrying them un-reviewed would migrate the mess rather than curate it.

None of the three is worked around here: the harness is built and proven by tests, and the first
`--apply` is gated on (1) and on a decision about (3). A **plan-only** run does not need a resolvable
target identity (it writes nothing) — it reports the unproven identity instead of refusing, which is
what makes the `plan` half runnable today; the guard is strict exactly where a write can happen.

## Explicitly not in this change

* relocation/relabelling the old graph as an archive (ruling step 3 — optional, after the migration);
* deleting anything anywhere (ruling step 4 — gated on `#10175` step 5's measurements);
* near-duplicate MERGE (`#5006`);
* the report-only definite-description planner (`#5059`) — its census is cited, not re-implemented;
* any change to the MCP tool surface or the SDK's public methods (`#4282`).
