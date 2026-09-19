---
title: "Canonical SDK method surface — the target list, de-duplicated"
type: synthesis
domain: capability
doc_status: live
created: 2026-09-19
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: sdk-method-surface
approval_status: pending-owner-approval
issue: 1521
related:
  - "#3863"
  - "#4120"
  - "#3994"
  - "#4035"
  - "#4114"
  - "#4176"
---

# Canonical SDK method surface

**Status: pending owner approval.** This is the target shape of `TortoiseSDK`'s public
surface — every method placed in exactly one group, the duplicates collapsed, and the
gaps named. It is the SDK counterpart to `docs/product/canonical-mcp-tools.md` (the
owner-approved tool list), and it was produced by the same process: cluster by user
action, collapse true duplicates, keep genuine variants, and check the result against
what comparable products converge on.

> This document is a **target**, not a record of what exists. It changes no code. The
> generated view of the surface *as it is today* is `docs/product/mcp-sdk-surface.md`,
> rendered from `config/surface-manifest.yml`; the freeze from #3863 covers the tool
> surface, and nothing here is implemented until the owner approves.

## The count

| | |
|---|---|
| Public methods on `TortoiseSDK` (parsed by `ast`, no leading underscore) | **150** |
| Rows in `config/surface-manifest.yml` (`counts.sdk_public_methods`) | **150** |

The two agree exactly. `TortoiseSDK.ask` and `TortoiseSDK.ask_assembled` were removed at
the SDK layer by #3849 (PR #3929) — the ask pipeline moved to `tortoise/ask_lane.py`, so
the SDK exposes **no** `ask` method. Nothing is unaccounted for in the manifest.

> A previous draft reported 152 and explained the two-method gap as the manifest excluding
the eval lane. That was wrong, and how it arose is worth naming: the method count was
extracted from the **hub working copy** of `tortoise/sdk.py`, stale at a pre-#3929 state,
while the manifest row was read from `origin/main`. Mixing two revisions produced a
discrepancy that does not exist. Every **method** figure here is now derived from
`origin/main` (the competitor figures are prior analysis — see Verification notes).

## The principle

Same as the tool list, and for the same reason — a caller should be able to hand
read access to an agent without handing it a way to destroy something:

> On the customer-grantable surface, a method is **either** read or write.
> Operator-only and eval-only methods are exempt.

That principle is **not yet honest on the SDK layer**, and this document records where.
Three read-named methods write the main graph, and the entire control plane writes on a
cold handle. Both are itemised under **Declaration defects**.

## The target list

Grouped by the user action each group serves. Members are the current method names —
this is a collapse of names that already exist, not a design of new ones.

### READ

| # | Canonical | Members (current names) | Action |
|---|---|---|---|
| R1 | `search` | `tortoise_fts_query`, `query`, `paginated_query`, `query_points_by_tag`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` | keep, collapse — **`search` is a new name** (no SDK `search` exists) |
| R2 | `recall(mode=)` | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context` | keep, **new dispatcher** |
| R3 | `get(type=)` | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` | keep, **new dispatcher** |
| R4 | `traverse` | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` | keep, collapse |
| R5 | `overview(section=)` | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` | keep, **new dispatcher** |
| R6 | `review_connections` | `get_cross_lens_candidates`, `list_dedup_candidates` | keep, collapse |
| R7 | `provenance` | `get_provenance_chain`, `belief_timeline`, `restore_point_at` | keep — **both provenance methods stay** |
| R8 | `events_poll`, `list_batches` | `events_poll`, `list_batch`, `list_batches` | keep |
| R9 | confidence **read** | `get_confidence`, `calibrate_summary`, `calibration_passed` | keep — see **Declaration defects** |

### WRITE

| # | Canonical | Members (current names) | Action |
|---|---|---|---|
| W1 | `create_point` | `create_point`, `create_or_update_point`, `batch_create_points` | keep + **2 aliases collapse** |
| W2 | `create_entity(type=)` | `create_entity`, `create_subject`, `create_object`, `create_event`, `create_document` | keep — **keep `create_event`** (eval calls it) |
| W3 | `create_source` | `create_source`, `complete_source` | keep — **not foldable** (below) |
| W4 | `create_edge(relation=)` | `create_edge`, `create_derivation`, `link_source_to_entity` | keep + collapse |
| W5 | `create_operator(op_type=)` | `create_operator`, `create_direct_edge` | keep — **not foldable** (below) |
| W6 | `index_directory` | `index_file`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` | keep — **2 self-declared DEPRECATED** |
| W7 | `ingest` | `ingest` | keep |
| W8 | `capture_session` | `capture_session`, `checkpoint`, `diary_write`, `diary_read` | keep |
| W9 | `commit_session` | `commit_session` | keep — **not foldable into W8** |
| W10 | `update` | `update`, `update_point`, `update_entity` | keep + collapse |
| W11 | `delete` | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | keep + collapse |
| W12 | `supersede(transfer_edges=)` | `supersede`, `supersede_point`, `invalidate_point` | keep + collapse |
| W13 | `retract_point` | `retract_point` | keep — **no successor, not a `supersede`** |
| W14 | `promote_point` | `promote_point`, `list_drafts`, `quarantine_batch` | keep |
| W15 | `operator_action(action=)` | `operator_action`, `mitigate_operator`, `annotate_operator` | keep + collapse |
| W16 | `set_point_baseline` | `set_point_baseline` | keep |
| W17 | `dream` | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` | keep — **`compute_confidence` mislabelled read** |
| W18 | `assess_source` | `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` | keep — **`get_source_reliability` writes** |
| W19 | `approve_merge` | `approve_merge` | keep |
| W20 | `file_decision`, `file_human_approval` | `file_decision`, `file_human_approval` | keep |

### Control plane — namespaced

These are operator/admin surfaces, not memory operations. They are **not** part of the
agent-facing read/write guarantee, and per the competitor evidence they belong behind a
namespace rather than flattened onto the same object as `search`.

| # | Namespace | Members |
|---|---|---|
| N1 | `org` | `org_create`, `org_get`, `org_list`, `org_update`, `org_delete`, `migrate_orgs_to_registry` |
| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` |
| N3 | `membership` | `membership_create`, `membership_get`, `membership_list`, `membership_update_role`, `membership_delete` |
| N4 | `apikey` | `apikey_create`, `apikey_list`, `apikey_revoke`, `apikey_verify` |
| N5 | `invitation` | `invitation_create`, `invitation_list`, `invitation_get_by_token`, `invitation_get_by_id`, `invitation_accept`, `invitation_revoke`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` |
| N6 | `signup_token` | `signup_token_lookup`, `signup_token_recover`, `signup_token_revoke` |
| N7 | utilities | `ulid`, `close` |

### Archived

| Method | Why it is not on the target surface |
|---|---|
| `backfill_v25` | A one-shot migration setting `status='live'` where NULL and backfilling `pointKind` — targeting `ONTOLOGY_v2.5` while the schema is **v3.13**. Zero customers, pre-beta. The approved MCP list proposes the same archive for its tool (`tortoise_backfill_v25`); like that one, this is a **proposal** — the baseline still records the method `lifecycle: active`. A migration written against a schema several versions old is a liability, not a capability. |

## What collapses — and what the discriminator is

Most rows below are genuine duplicates: the members differ by nothing, by a type, or by a
flag — rarely by capability. Where the discriminator **already exists** the row says so;
where it does not (a parameter that must first be forwarded, or a name that has to be
invented) the row says that instead, because those are not renames.

| Members | Discriminator | Evidence |
|---|---|---|
| `create_or_update_point` → `create_point` | `dedup` — a `**props` key popped with default **`False`** (not a declared parameter) | single-statement delegate |
| `batch_create_points` → `create_point` / `ingest` | — | comprehension, no batch semantics |
| `create_subject` / `create_object` / `create_document` → `create_entity` | `type ∈ {subject, object, event, document}` | explicit raise on anything else |
| `create_derivation` → `create_edge` | `relation = 'wasDerivedFrom'` | already in the edge allowlist |
| `delete_point_wrapped` → `delete` | return shape | MCP-layer adapter |
| `index_sessions` / `ingest_corpus` → `index_directory` | `file_type` | both self-declared DEPRECATED in their own docstrings |
| `supersede_point` / `invalidate_point` → `supersede` | `transfer_edges: bool` — **but `supersede_point` also carries `valid_from`** (kw-only, the bi-temporal window stamp) that `supersede` does not forward. The collapse is **lossy**: `supersede(old, new, valid_from=…)` raises `TypeError` | dispatcher exists; `valid_from` must be added to it |
| `mitigate_operator` / `annotate_operator` → `operator_action` | `action ∈ {mitigate, annotate}` — **but `mitigate_operator` takes `credibility`**, which drives the mitigation's Beta baseline and which `operator_action` does not forward. Because `operator_action` accepts `**kwargs`, passing it is **accepted and ignored** — a silent behaviour change | dispatcher exists; `credibility` must be forwarded first (the MCP handler has the same hole) |
| `update_point` / `update_entity` → `update` | node label, auto-resolved | dispatcher exists |
| `delete_point` / `delete_entity` → `delete` | node label, auto-resolved | dispatcher exists |
| `invitation_get_by_token` / `_by_id` → `invitation_get` | **no discriminator yet — `invitation_get` is a new name.** The two paths are not interchangeable: one verifies a salted hash, the other matches a ULID in the registry | the two lookups differ; merging is a design decision, not a rename |

**The highest-leverage item in this document.** Four of the group canonicals — `search`,
`get(type=)`, `overview(section=)`, `recall(mode=)` — are *already implemented in the MCP
handlers* (`mcp_server.py` dispatches `tortoise_search`, `tortoise_get` over 7 types,
`tortoise_overview` over 12 sections, and `tortoise_recall` over 4 modes). The SDK never
grew the mirror for any of them. Adding those four dispatchers to the SDK is **purely
additive** — it breaks no caller, notably not the eval harness, which drives `TortoiseSDK`
by method name. It presents the consolidated surface without touching a single existing
call site. All four are **new SDK names**, so all four need explicit approval under the
#3863 freeze.

## What does *not* merge

We have been wrong about this twice already, so the blockers are recorded explicitly.

| Pair | Blocker |
|---|---|
| `create_source` ↛ `create_entity` | the **URL is the node identity**; plus tier canonicalisation, conditional MERGE on `contentHash`, evidence-age clock, and its own journaling contract |
| `create_point` ↛ `create_entity` | 501 LOC: content-hash dedup, tag sync, EP dirty-marking, `PointAdded` journal |
| `create_operator` ↔ `create_direct_edge` | operator **node** vs bare edge — different EP factor extraction; a `reify` flag would silently change semantics |
| `retract_point` ↔ `supersede` | retract has **no successor** |
| `promote_point` ↔ `update_point(status='live')` | promote also promotes incident operators, under reviewer gating |
| `capture_session` ↔ `commit_session` | local extraction vs extractor-v2 + Layer-1 POST |
| `provenance` ↔ `get_provenance_chain` | different edge chains: who-decided vs extracted-from |
| `list_graphs` ↔ `graph_list` | raw DB graph names vs control-plane rows — **same problem as the tool layer's `get_entity`** |
| `compute_confidence` ↔ `get_confidence` | eager run vs lazy read — both write (below) |
| control-plane CRUD families | different labels, validation and audit per family |

## Competitor evidence

Fourteen comparable products were surveyed on one question: **how do they structure their
public SDK/API surface?** Counts were derived by parsing source with `ast`, not from prose.

| Product | Public methods | Structure | Read/write split |
|---|---|---|---|
| Graphiti | 13 | one flat class | by verb |
| Mem0 | 14 | one flat class | by verb |
| MemOS | 26 | flat class + facade | by verb |
| Pinecone `Index` | 27 | two flat objects by plane | by plane |
| Chroma | 17 + 17 | two flat objects | by object |
| Cognee | ~40 exports | free functions | by **phase** (`add` → `cognify`) |
| Neo4j GraphRAG | 6 retrievers × 1 | class-per-strategy | by class role |
| MS GraphRAG | 11 | free functions | by search strategy |
| Letta | 16 resources | 2–3 level resource tree | by resource |
| Supermemory | 36 across 5 resources | namespaced | by resource |
| Zep Cloud | 95 across 8 namespaces | 2-level tree | by namespace |
| Weaviate v4 | per-namespace | namespace per operation class | by namespace |
| Basic Memory | 29 MCP tools | tool-first, no client class | explicit `readOnlyHint` |
| LangMem | 9 exports | factory functions | by factory kind |

**Converged patterns** (counts are of the 14 surveyed):

1. **Small closed verb vocabulary — 14/14.** Nobody invents a domain verb for the happy path.
2. **Verb-first, snake_case — 13/14.** The exception is the tool-first product, because an LLM selects from its list.
3. **Above ~30 methods, the pattern is to namespace** — Zep (95), Letta (16 resources), Supermemory (36 across 5 resources) and Weaviate all use namespaces or resource trees. Cognee is the counterexample on this table's own evidence: ~40 exports exposed as **free functions**, flat and untenanted. This is a tendency, not a rule — and it is this document's reading of the table, not a finding of the source.
4. **Flat classes cluster at 13–27 — 5/5.** The largest flat *memory* client is MemOS at 26. (Pinecone's `Index` carries 27 names but is one of **two** flat objects by plane, so whether it counts as a single flat surface is ambiguous — **UNVERIFIED**.)
5. **One high-level call with many effects is the default; primitives stay public — 11/14. Nobody hides them.**
6. **Read/write separation is by naming or namespace — 14/14. Never separate client classes.**
7. **Every retirement observed involved a warning or a parallel surface — never a silent rename.**
8. **A raw escape hatch exists in the majority — 9/14**.

**Where this lands for us.** 150 public methods on one flat class is ~5.5× the largest flat
surface observed (Pinecone's `Index`, 27). But the
evidence does **not** say "delete 128 methods" — it says **namespace**, and it says
**collapse the aliases**. The target list above does exactly that: **36 groups over 149
names** — 29 memory-facing (R1–R9, W1–W20) and 7 control-plane namespaces (N1–N7) —
reached by grouping and merging, with the primitives still reachable and `backfill_v25`
archived. The
control plane is what moves behind namespaces; the memory surface
is what mirrors the approved 23-tool list.

## Declaration defects

Found while building this list. None is fixed here — this document changes no code.

1. **Three read-named methods write.** `get_confidence` is annotated read-only but its own
   docstring says the lazy-dream path "WRITES `n.confidence`", and it calls
   `dream(dirty_only=True, ...)`, which runs `SET n.confidence = p.c`. `compute_confidence`
   writes the same property eagerly. `get_source_reliability` writes a reliability cache.
   **This is the blocker on the read/write guarantee** — a read-only key can currently write.
2. **The control plane writes on first use.** Every registry-backed read (`org_list`,
   `graph_list`, `apikey_list`, `membership_list`, `invitation_list`, and others) calls into
   a registry helper that on a cold handle issues `CREATE INDEX` statements. Idempotent, but
   a read that writes.
3. **Five registry `sdk_method` values resolve to no method on `TortoiseSDK`** — their
   handlers reach the projection or a module function directly. Each is annotated in-source
   as deliberate (`# navigation.entityProfile — not a direct SDK method`, `# pack_state
   helper, not an SDK method`), and `tools/surface-guard.py` treats `sdk_method` as part of
   the frozen baseline. So the annotations and the defect coexist: the *comment* is
deliberate, the *unresolvable binding* is still wrong — the manifest itself carries
`recommendation: fix-declaration` on all five, and the open item below folds them into
#4035's class. Listed here so the bridge table knows those five bindings are by handler,
not by method name.
4. **Exactly one method has no code caller:** `complete_source` — no call site in
   `battery/`, `tools/`, `tortoise/`, or `tests/`. It is named in the generated manifest
   and in docs, but a mention is not a caller. Every other method on the surface is reached
   by the eval harness, tooling, the hosted REST layer, or tests.

## Callers that must not break

The eval harness drives `TortoiseSDK` **by method name** — no eval or benchmark invokes an
MCP tool. So a *tool* rename is invisible to it and an *SDK* rename is not. These names are
load-bearing and must survive any collapse, or be shimmed with a warning the way #3883 does
at the tool layer:

`create_point` · `create_operator` · `create_event` · `create_entity` · `get_point` ·
`ingest` · `recall_state` · `promote_point` · `mitigate_operator` · `compute_confidence` ·
`retract_point` · `dream` · `tortoise_fts_query` · `close`

## Open items

| Item | Needs |
|---|---|
| The **four** new dispatchers (`search`, `get(type=)`, `overview(section=)`, `recall(mode=)`) | approval — additive, but they add names to a frozen surface |
| `invitation_get` | approval — a new name; the two lookup paths differ (salted-hash verify vs ULID match), so this is a design decision, not a rename |
| `supersede` / `operator_action` parameter forwarding | `valid_from` and `credibility` must be forwarded before those two collapses are safe — today each is either a `TypeError` or silently ignored |
| `get_confidence` / `compute_confidence` / `get_source_reliability` | a decision: fix the declaration, or add a `write_back: bool` parameter — until then the read/write guarantee does not hold |
| The control-plane cold-handle write | approval of a fix, or an explicit exemption for operator-only surfaces |
| Five phantom `sdk_method` registry values | folded into #4035's class of defect |
| 29 memory-facing groups vs the approved 23 tools | whether the SDK mirrors the tool list 1:1 or stays a superset |
| `list_graphs` vs `graph_list` | a naming decision — raw DB names vs control-plane rows |
| `ulid`, `close`, `test_guard` | whether utilities belong on the public surface at all |

## Sequencing

1. **This list** → owner approval.
2. **The bridge table** (each tool's `type=`/`mode=` → the SDK method behind it) — the pre-flight.
3. **The SDK dispatchers**, which are additive and unblock the mirror.
4. **Collapse the aliases**, with warning shims (#3883's mechanism) for the load-bearing names.
5. **Namespace the control plane.**

## Verification notes

- Method count and membership were derived by parsing `tortoise/sdk.py` with `ast`, not
  from documentation or from the manifest's prose.
- The merge verdicts were checked against the method bodies, following delegation one level
  into helpers — not inferred from names. This is how `get_confidence`'s write was found.
- Caller evidence is a repo-wide search for each method name across every tracked file.
- Competitor counts come from a **prior external analysis pass** — an `ast` parse of each
  project's source on its default branch. They are **UNVERIFIED in this repo**: nothing here
  reproduces them, and the per-product figures should be treated as reported rather than
  confirmed. The *converged patterns* drawn from them are this document's inference, not the
  source's conclusion. Where a figure is approximate, or a namespace makes a flat integer
  misleading, the table says so.
