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
`tools/curate_graph.py` (the CLI). Tests: `tests/test_curation_11469.py` (49 tests).

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
| **Source** | **1,578** | **enumerated** — **100% `agentSession`, `session:<uuid>` urls**, zero real artifacts |
| **Subject** | **26** | **count only** |
| edges | 7,981 | `audit.edge_count` |
| **total nodes** | **31,248** | non-episodic cap counter = 19,595 + 5,379 + 26 = **25,000 / 25,000** |

### By class, with a judgement

The judgement is produced by the rules in `tortoise/curation.py`; every rule is quotable in the
report, and a row that no rule decides is `undecidable` rather than guessed.

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
2. **incremental curation**: migrate the decided slice now (the 127 carried rows + operators +
   Subjects), and let the statement bulk be curated as the fleet writes new memory into the clean
   graph anyway.

What the tool guarantees either way: a run only ever carries rows whose disposition is `carry`,
and every other row is reported with its reason. Nothing is dropped from the old graph — it stays
whole.

## What the harness guarantees (and how each is proven)

⚠️ **One consequence of the read surface, stated so the census is not over-read:** `tortoise_query`
returns non-operator Points only, and Objects/Events/Subjects have no listing tool. So the enumerable
population this pass classifies is **15,842 points + 1,578 sources**; operator/Object/Event/Subject
rows appear in the tables as **counts from `graph_overview`**, with the class rule stated, and are
flagged *not enumerated* rather than presented as measure-and-classify results.

| property | mechanism | test |
|---|---|---|
| the old graph is only read | the module has no destructive call at all; the reader exposes no write verb | `test_bundle_carries_no_deletion`, `test_writer_protocol_declares_no_destructive_verb` |
| the target is only added to | `PlanWriter` is a **single-verb** protocol (`write_points`) — additivity is a property of the type, not of the caller's discipline | `test_writer_protocol_declares_no_destructive_verb` |
| **idempotent** | stable `migrationKey` per content hash → target-side dedup (`ingest` folds by content hash + kind) **and** a local ledger that skips a migrated key before it reaches the wire | `test_second_run_writes_nothing`, `test_duplicate_follows_its_keeper` |
| **off-switch** | `TORTOISE_CURATE_OFF=1` / `--off`, checked before the run **and before each write batch** | `test_explicit_off_switch_writes_nothing`, `test_env_off_switch_writes_nothing` |
| **source ≠ target** | compared on the CANONICAL id, where every accepted spelling (graph id, `stable_id`, namespace, case-insensitive NAME, and the tenant graph's `org_<id>` / `team_<id>` pair, which are ONE graph) collapses to that graph's id tail — measured against the row shape this server actually returns (namespace strings); a spelling the surface cannot resolve, or an ambiguous name, is a REFUSAL (fail closed) | `test_source_equals_target_refused`, `test_source_equals_target_refused_across_spellings`, `test_the_graph_name_is_the_same_graph_as_its_namespace`, `test_namespace_and_gid_spellings_of_one_graph_are_refused`, `test_no_rows_does_not_fail_open`, `test_unresolvable_target_is_refused_fail_closed`, `test_ambiguous_name_is_refused`, `test_a_name_the_surface_cannot_resolve_is_refused` |
| dry by default | `--apply` is required; the default run writes nothing | `test_dry_run_writes_nothing` |
| a refusal is never recorded as a write | the writer unwraps the tool result (a `CallToolResult` OBJECT, not a dict — a dict-only check never sees a refusal), raises on a refusal envelope, and the ledger is written from the ACCEPTED keys only, scoped per target graph; a partial run reports what already landed | `test_a_refused_write_is_never_recorded`, `test_ingest_refusal_envelope_is_not_a_success`, `test_a_calltoolresult_object_is_unwrapped_and_a_refusal_refuses`, `test_a_refused_second_batch_keeps_the_first_batch_and_reports_zero_then_records_it`, `test_ledger_is_scoped_to_its_target` |
| the bundle is ingest-valid | every point item carries `content` (ingest Phase-1 refuses a bundle otherwise) **and** the bundle mints **no** bundle-local `ref` at all — a ref is expanded wherever it appears as a field value, so any ref can collide with a row's own content/id and make the whole bundle uncanonicalisable (`ref == migrationKey` did exactly that) | `test_bundle_items_carry_content`, `test_bundle_survives_the_ingest_canonicaliser`, `test_a_row_whose_content_looks_like_a_bundle_ref_cannot_cycle` |
| no silent zero | a read that returns 0 rows, or fewer than the reported total, **fails** — an empty census must never look like "nothing to migrate" | `MCPReader.read_points` raises |

## How to run

```bash
# inventory (read-only) — the table above (a name is fine here: nothing is written)
uv run python tools/curate_graph.py inventory --graph live-v4 --out /tmp/inventory.json

# plan against a target (writes nothing without --apply)
uv run python tools/curate_graph.py plan \
    --graph org_<org>_g_<source-gid> --target org_<org>_g_<clean-gid> --out /tmp/plan.json

# apply (additive, idempotent, off-switchable). BOTH graphs must be given in an
# IDENTITY spelling — a namespace (org_<org>_g_<gid>) or g_<gid>. A bare NAME
# (live-v4) is refused, because this surface returns namespaces only and the guard
# must not guess which graph a name means; --graph is mandatory for --apply, so the
# source identity is always the graph that was actually read.
uv run python tools/curate_graph.py plan \
    --graph org_<org>_g_<source-gid> --target org_<org>_g_<clean-gid> --apply
```

Environment: `TORTOISE_MCP_URL` + `TORTOISE_MCP_API_KEY`; `TORTOISE_CURATE_OFF=1` disarms writes.

⚠️ **Known operational limitation, measured 2026-11-08:** `tortoise.mcp_client.call_tool` opens a
fresh HTTP session per call (by design — the credential is resolved per request, #10605), so a full
`plan` read is ~32 sequential calls and the hosted endpoint can answer `429 Too Many Requests`
part-way. The run then fails loudly rather than reporting a partial census (the reader raises), and
nothing is written — but a production run should be scheduled rather than fired in parallel with a
busy fleet.

## Why this pass has not written anything yet (three blockers, all measured)

1. **There is no clean target graph.** `#10175` step 2 ("start a new graph") has not happened; the
   workspace's graphs today are `default`, `acl-verify-20261027` and `live-v4`. Creating the
   target is `#10175`'s step, not this issue's.
2. **The MCP credential is graph-bound to `live-v4`** — it cannot write to another graph, and
   `live-v4` is at its cap, so any write there is `GRAPH-WRITE-REFUSED` by design. A graph-bound
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
