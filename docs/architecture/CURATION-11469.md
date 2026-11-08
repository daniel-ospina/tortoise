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
`tools/curate_graph.py` (the CLI). Tests: `tests/test_curation_11469.py` (105 tests).

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
`plan --rows <path>` (JSON Lines: node id, disposition, the rule's reason, pack).

| class | count | judgement | reason |
|---|---:|---|---|
| `point:decision` | 7 | **carry** | owner-level decisions — the curated graph exists to keep them |
| `point:option` | 2 | **carry** | options hang off decisions |
| `point:finding` | 1 | **carry** | durable finding |
| `point:jobToBeDone` | 1 | **carry** | strategy artifact |
| `point:useCase` | 1 | **carry** | strategy artifact |
| `point:evidence` | 120 | **carry** (115) / not-carried (5) | evidence is signal; the 5 are exact duplicates of a carried evidence row (the keeper carries it) |
| `point:diary` | 2 | **undecidable** | agent diary: authored, but narrative-shaped |
| `point:statement` | 15,708 | **not-carried 0 / undecidable 15,708** | **not one** matches a turn-narration marker; every one is a claim this rule set cannot judge |
| `source:agentSession` | 1,578 | **undecidable** | §9.1/D1 (`#10000`) says the raw tier lives in Postgres — but this tenant's record holds **0 rows**, so "do not carry it" would leave the transcript with no home |
| `object` (bare definite) | majority of 5,379 (**not enumerated** — sample only) | **not-carried** | `#11021`/`#11211`: a noun phrase that refers to nothing is not an entity. Biased 1,000-row sample: **96%** drop-shaped; `#5333`'s full census on the same graph: **83%** of 7,870 objects |
| `event` (session container) | 4,670 | **undecidable** | same only-copy problem as Sources |
| `operator` | 3,727 (**not enumerated** — count only) | **carry** (rule) | the rule carries the operator row as a claim; edges are the ingest contract's job (#888 W4), not built here |

### Defects already measured on the source (audit, read-only)

| check | count | severity |
|---|---:|---|
| `missing_sourceKind_source` | 5,073 | medium — every session-backed evidence path resolves to a neutral tier |
| `superseded_active_edges` | 5 | medium |
| `impl_instead_of_nand` | 2,603 | low — keyword heuristic |
| `missing_sourceKind` | 71 | low — legacy evidence |
| **total** | **7,752** | none `high` |

### ⚠️ The finding that decides the shape of this work

**A deterministic rule set cannot select the signal out of 15,708 statements.** Not one statement in
this graph matches a narration marker — the narration rule selects nothing at all — and the
non-statement kind rules (decision 7, option 2, finding 1, jobToBeDone 1, useCase 1, evidence 115)
select the 127 carried rows. Operators (3,727) are carried by a rule too but are not enumerated by
this read path, so they are not part of that 127. The 15,708 statements are claims, plausible and
unjudged. Two options, and this issue does not choose between them:

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
| a read that refuses is never census data | every `graph_overview` section read (including the credential-bound retry) is unwrapped and FAILS CLOSED on an error envelope — an envelope falling through `_rows_from_tool` reads as "0 rows"; a transport failure RAISES rather than re-reading a different graph (only a recognised graph refusal may fall back, and notes accumulate per section); `sources_read` is reconciled against `counts.Source` via a reported `source_count_gap`, never called complete | `test_an_error_envelope_is_never_reported_as_zero_rows`, `test_a_transport_failure_does_not_fall_back_to_another_graph`, `test_a_graph_refusal_does_fall_back_and_says_so_once_per_section`, `test_the_inventory_report_names_the_source_filter` |
| the target is only added to | `PlanWriter` is a **single-verb** protocol (`write_sections`, which writes each bundle SECTION — `points` and `sources` — because a carried Source sent as a Point is re-typed, and a bundle that only ever sent `points` reported success while writing none) — additivity is a property of the type, not of the caller's discipline | `test_writer_protocol_declares_no_destructive_verb`, `test_a_carried_source_is_reached_by_the_writer` |
| **idempotent** | stable `migrationKey` per content hash → target-side dedup (`ingest` folds by content hash + kind) **and** a local ledger that skips a migrated key before it reaches the wire | `test_second_run_writes_nothing`, `test_duplicate_follows_its_keeper` |
| **off-switch** | `TORTOISE_CURATE_OFF=1` / `--off`, checked before the run **and before each write batch**; a run disarmed BEFORE it starts is a success (exit 0 — the operator asked for it), a switch that flips MID-run is an exit 3 | `test_explicit_off_switch_writes_nothing`, `test_env_off_switch_writes_nothing` |
| every carried row reaches the wire | `bundle()` REFUSES to build when a carried row has no wire section (point/operator → `points`, source → `sources`), and an unrepresentable row is `undecidable`, never `carry`; a result the pass cannot interpret (a `str`/`list`, which `normalise_tool_result` passes through) is a REFUSAL, not a write | `test_a_bundle_refuses_a_carried_row_with_no_section`, `test_a_carried_event_is_undecidable_not_silently_dropped`, `test_a_text_result_this_pass_cannot_interpret_is_a_refusal`, `test_a_carried_source_is_reached_by_the_writer`, `test_the_wire_never_carries_a_migration_key_on_a_source`, `test_a_source_is_written_once_because_the_ledger_key_is_the_same_one` |
| **source ≠ target** | compared on the CANONICAL id, where every accepted spelling (graph id, `stable_id`, namespace, case-insensitive NAME, and the tenant graph's `org_<id>` / `team_<id>` pair) collapses to that graph's id. **The rows are the authority**: when graph rows are available, an id-shaped spelling that no row names is UNRESOLVED (a refusal), because the surface hands out namespace strings and `mcp_auth` accepts a `stable_id` that a namespace-only row does not expose — a shape-only comparison let `--graph g_<tenant-gid> --target team_<org>` pass the guard and write IN PLACE. An ambiguous name is also a refusal (fail closed), the `default` sentinel is a marker rather than an identity (it falls through to the row's real spelling), two rows that disagree about what one ref names are REFUSED rather than first-matched, and when the two refs resolve through rows exposing DIFFERENT identity fields the guard refuses too — the two canonicals could then differ for ONE graph (a tenant row carrying `stable_id` but no namespace, beside a row carrying only the namespace), which is the in-place fail-open. CI: the new test file is registered in `config/ci-surfaces.yml` (`tools/ci_selection.py --integrity`) — an unregistered test file reds the required aggregate | `test_source_equals_target_refused`, `test_source_equals_target_refused_across_spellings`, `test_the_graph_name_is_the_same_graph_as_its_namespace`, `test_namespace_and_gid_spellings_of_one_graph_are_refused`, `test_no_rows_does_not_fail_open`, `test_unresolvable_target_is_refused_fail_closed`, `test_ambiguous_name_is_refused`, `test_a_name_the_surface_cannot_resolve_is_refused`, `test_the_tenant_graphs_id_spelling_is_not_treated_as_a_distinct_graph`, `test_an_id_no_row_names_is_unresolved_when_rows_are_available`, `test_the_default_sentinel_is_not_an_identity`, `test_rows_that_disagree_about_one_ref_are_refused`, `test_one_graph_split_across_two_rows_with_different_fields_is_refused` |
| dry by default | `--apply` is required; the default run writes nothing, and it still reports `skipped_known`/`duplicate_rows`/`pending` from the ledger so `skipped + duplicates + pending` SPANS the census — the four counters reconcile in every mode (dry, apply, re-run, partial refusal, mid-run stop), and the LEDGER decides the split even on a pre-write refusal | `test_dry_run_writes_nothing`, `test_a_dry_run_reports_what_the_ledger_already_holds`, `test_the_counters_span_the_plan_in_every_mode`, `test_a_refused_run_still_reports_what_the_plan_owes`, `test_a_refused_run_over_a_landed_plan_owes_nothing` |
| a refusal is never recorded as a write | the writer unwraps the tool result (a `CallToolResult` OBJECT, not a dict — a dict-only check never sees a refusal), raises on a refusal envelope, and the ledger is written from the ACCEPTED keys only, scoped per target graph; a partial run reports what already landed | `test_a_refused_write_is_never_recorded`, `test_ingest_refusal_envelope_is_not_a_success`, `test_a_calltoolresult_object_is_unwrapped_and_a_refusal_refuses`, `test_a_refused_second_batch_reports_the_first_batch_and_records_it`, `test_ledger_is_scoped_to_its_target` |
| the bundle is ingest-valid | every point item carries `content` (ingest Phase-1 refuses a bundle otherwise) **and** the bundle mints **no** bundle-local `ref` at all — a ref is expanded wherever it appears as a field value, so any ref can collide with a row's own content/id and make the whole bundle uncanonicalisable (`ref == migrationKey` did exactly that) | `test_bundle_items_carry_content`, `test_bundle_survives_the_ingest_canonicaliser`, `test_a_row_whose_content_looks_like_a_bundle_ref_cannot_cycle`, `test_the_bundle_passes_ingests_phase1_validator` (the last one calls the REAL `sdk._validate_bundle`) |
| no silent zero | a read that returns 0 rows, or fewer than the reported total, **fails** — an empty census must never look like "nothing to migrate"; a refusal delivered as an error ENVELOPE (this surface's normal shape) also fails and NAMES its cause, rather than re-reading a different graph; and a page walk whose population CHANGES mid-walk is refused: a total that moves, the SAME row — id AND content — served by an earlier page, or a first/last page that no longer matches when re-read — and, because a snapshot taken after the churn cannot see it, a **second full walk** whose row sequence must match (default on; `--no-verify-read` trades that away for speed), while two distinct nodes sharing one id are a legal shape and are not — unless their content is IDENTICAL, in which case the walk cannot tell them from one node re-served and fails closed, because an offset walk over a shrinking set both skips a live row and re-reads a tombstone while the counts still match | `test_the_entry_point_reader_refuses_a_zero_or_short_point_read`, `test_a_points_refusal_names_its_real_cause`, `test_an_error_envelope_is_never_reported_as_zero_rows`, `test_a_quota_envelope_does_not_re_read_a_different_graph`, `test_a_shifted_pagination_is_refused_not_silently_partial`, `test_a_row_served_twice_is_refused_even_when_the_total_is_stable`, `test_a_same_size_churn_mid_walk_is_refused`, `test_two_nodes_sharing_an_id_in_one_page_are_not_a_pagination_shift`, `test_two_nodes_sharing_an_id_across_pages_are_not_a_pagination_shift`, `test_a_truncated_last_page_whose_total_moved_is_refused` |

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
3. **15,708 statements have no deciding rule** (above) — carrying them un-reviewed would migrate
   the mess rather than curate it.

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
