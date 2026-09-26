---
title: "Canonical SDK method surface — the inventory, de-duplicated (NOT the target)"
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

# Canonical SDK method surface — an inventory, not the target

**Read this for coverage, not for names.** This document is two things: an **inventory of all 150
methods that exist today**, placed in exactly one group, duplicates collapsed and gaps named; and an
alongside that, an **earlier target sketch** (32 groups over 149 names).

> ⛔ **The SDK target is `docs/product/beta-sdk-surface.md` (40 methods), not this document.**
> Where the two disagree, that doc governs. They really do disagree: this sketch says
> `write_knowledge` and `stabilize_beliefs` where the current target says
> `write_knowledge_batch` and `refresh_confidence`. Note also that this file's second table
> ("What we have that competitors do not") reuses the R/W/N labels for *different* groups —
> cross-reference by method name, never by label.

**Status: pending owner approval on the inventory.** The MCP side of the surface is the
owner-approved `docs/product/canonical-mcp-tools.md`.

> This document changes no code. The generated view of the surface *as it is today* is
> `docs/product/mcp-sdk-surface.md`, rendered from `config/surface-manifest.yml`; the freeze from #3863 covers the tool
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

## The groups (the earlier sketch)

> ⛔ **This is the 32-group sketch, not the 40-method target.** It is kept for its coverage and its
> rationale. For the target names, read `docs/product/beta-sdk-surface.md`.

**The names are the approved MCP names.** A capability has one name across both layers —
the MCP tool and the SDK method behind it. An earlier revision of this document invented a
second vocabulary (`search`, `get`, `traverse`, `dream`, `create_source`), which meant the
same capability answered to two names depending on which layer you were reading. That is
corrected here: every group whose capability has an approved MCP tool **carries that tool's
name**, and only the SDK-only groups (no tool exposes them) have names of their own.

Members are the current method names — this is a collapse of names that already exist, not a
design of new ones. Names are verb-first, 2–3 words, no internal jargon.

### The naming rules

Three rules, each of which caught a real error in this document.

1. **One name per capability, across both layers.** Every group whose capability has an
   approved MCP tool carries that tool's name. An earlier revision invented a second SDK
   vocabulary (`search`, `get`, `traverse`, `dream`, `create_source`), so the same capability
   answered to two names depending on which layer you were reading — and the generic SDK
   `get`/`delete`/`update` collided with real methods of those names.
2. **No node types in method names.** A name says what is done, not what it is done to.
   `retract_point` implied `retract_object`, `retract_subject` and so on; it is folded into
   `revise_knowledge`. The same rule retired `create_point`, `delete_point`, `update_point`,
   `supersede_point` — if we later retract a Source, the name already works.
3. **Verb-first, 2–3 words, no internal jargon.** `stabilize_beliefs` not `dream`;
   `adjust_relationship` not `operator_action`; `write_knowledge` not `ingest`. Three words are
   fine when they remove an ambiguity — `capture_session_local` / `capture_session_hosted`
   are worth the extra length because the choice is then unmissable at the call site. The
   cost is a few tokens in a tool list; the prefix/suffix length effect on agent selection is
   measured as negligible and model-dependent.

### READ

| # | Canonical | MCP tool | Members (current names) | Action |
|---|---|---|---|---|
| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` | keep, collapse |
| R2 | `list_knowledge` | #2 | `query`, `paginated_query`, `query_points_by_tag` | keep, collapse |
| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` | keep, collapse — absorbs the confidence reads and both provenance methods |
| R4 | `get_entity` | #4 | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` | keep, collapse |
| R5 | `explore_connections` | #5 | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` | keep, collapse |
| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` | keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability |
| R7 | `review_link_candidates` | #7 | `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | keep, collapse |
| R8 | `poll_events` | #8 | `events_poll` | keep |
| R9 | `inspect_batch` | #9 | `list_batch`, `list_batches` | keep, collapse |

### WRITE

| # | Canonical | MCP tool | Members (current names) | Action |
|---|---|---|---|---|
| W1 | `create_entity` | #10 | `create_entity`, `create_point`, `create_subject`, `create_object`, `create_event`, `create_document`, `create_or_update_point`, `batch_create_points` | keep — `create_point` and `create_event` are **retired to a failing name that names `create_entity`**; there is no SDK alias layer (#3836 (c) ruling). The eval harness calls them by name, so its call sites migrate in Phase 2 |
| W2 | `write_knowledge` | — | `ingest` | keep — **SDK-only name.** The batch call: one bundle writes points + entities + sources + connections atomically, with local `ref` labels so connections can address nodes created in the same call. Not to be called `ingest_bundle` (jargon) or `write_graph` (collides with `graph_overview` and the graph admin namespace) |
| W3 | `register_source` | #11 | `create_source`, `complete_source` | keep — **not foldable**: the URL is the node identity |
| W4 | `index_files` | #12 | `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` | keep — **2 self-declared DEPRECATED** |
| W5 | `capture_knowledge` | #13 | `checkpoint`, `diary_write`, `diary_read` | keep — session-adjacent capture artifacts |
| W6 | `capture_session_local` | — | `capture_session` | keep — **SDK-only name.** Writes the session into your own graph |
| W7 | `capture_session_hosted` | — | `commit_session` | keep — **SDK-only name.** Extracts locally, validates, then POSTs to `/v1/sessions/commit`; needs a hosted endpoint and an API key |
| W8 | `manage_source_trust` | #14 | `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` | keep — **`get_source_reliability` writes** |
| W9 | `link_entities` | #15 | `create_edge`, `create_derivation`, `link_source_to_entity`, `create_operator`, `create_direct_edge` | keep, collapse — **one call, dispatching internally on `kind=`**: epistemic relations build a reified operator node, structural ones a bare edge. The caller never sees the split |
| W10 | `record_decision` | #16 | `file_decision`, `file_human_approval` | keep, collapse |
| W11 | `revise_knowledge` | #17 | `update`, `update_point`, `update_entity`, `supersede`, `supersede_point`, `invalidate_point`, `retract_point`, `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | keep, collapse — **the widest group; see Open items** |
| W12 | `delete_knowledge` | #18 | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | keep, collapse |
| W13 | `stabilize_beliefs` | #19 | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` | keep — **`compute_confidence` mislabelled read** |
| W14 | `approve_merge` | #20 | `approve_merge` | keep |
| W15 | `adjust_relationship` | #21 | `operator_action`, `mitigate_operator`, `annotate_operator` | keep, collapse |
| W16 | `manage_deployment` | #22 | `org_*`, `graph_*`, `membership_*`, `apikey_*`, `invitation_*`, `signup_token_*` | keep, **namespaced** |
| W17 | utilities | — | `ulid`, `close` | `close` is core lifecycle; **`ulid` is a removal candidate** |

### Control plane — the `manage_deployment` members, namespaced

Operator/admin surfaces. **Not** part of the agent-facing read/write guarantee.

| # | Namespace | Members |
|---|---|---|
| N1 | `org` | `org_create`, `org_get`, `org_list`, `org_update`, `org_delete`, `migrate_orgs_to_registry` |
| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` |
| N3 | `membership` | `membership_create`, `membership_get`, `membership_list`, `membership_update_role`, `membership_delete` |
| N4 | `apikey` | `apikey_create`, `apikey_list`, `apikey_revoke`, `apikey_verify` |
| N5 | `invitation` | `invitation_create`, `invitation_list`, `invitation_get_by_token`, `invitation_get_by_id`, `invitation_accept`, `invitation_revoke`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` |
| N6 | `signup_token` | `signup_token_lookup`, `signup_token_recover`, `signup_token_revoke` |

### Archived

| Method | Why it is not on the target surface |
|---|---|
| `backfill_v25` | A one-shot migration setting `status='live'` where NULL and backfilling `pointKind` — targeting `ONTOLOGY_v2.5` while the schema is **v3.13**. Zero customers, pre-beta. The approved MCP list proposes the same archive for its tool (`tortoise_backfill_v25`); like that one, this is a **proposal** — the baseline still records the method `lifecycle: active`. A migration written against a schema several versions old is a liability, not a capability. |

## What each group does

One line each, in plain language. Read/write is stated because it is a guarantee, not a
label — a read tool can be handed to an agent without handing it a way to destroy something.

### READ

| Canonical | What it does |
|---|---|
| `search_knowledge` | **Find things by text.** Full-text and hybrid search, plus suggesting where to start, searching past sessions, and summarizing a topic. |
| `list_knowledge` | **Browse and filter without searching by text** — enumerate by kind, tag, or page through results. |
| `recall_beliefs` | **Ask what the system currently believes, and how strongly.** Returns confidence, the gaps (what it does *not* know), a subgraph, provenance (who decided it, where it came from), and how a belief changed over time. |
| `get_entity` | **Fetch one node by id**, whichever kind it is — a claim, an entity, a session, an event. |
| `explore_connections` | **Walk outward from a node** — what it links to, and what those link to. Includes the governance views (what your org owns). |
| `graph_overview` | **The state of the whole graph** — taxonomy, counts, tags, sources, namespaces, topics, stale items — plus integrity checks, ontology validation, audit, and belief-engine health. |
| `review_link_candidates` | **Suggested connections the system found but has not made.** Read-only; nothing is merged. |
| `poll_events` | **Read the event log** since a point in time. |
| `inspect_batch` | **Look at a batch of items held for review.** |

### WRITE

| Canonical | What it does |
|---|---|
| `create_entity` | **Add one node** — a claim (Point), or a referent (subject, object, event, document). Creating a Point is the variant that affects beliefs; the rest do not. |
| `write_knowledge` | **Add many interconnected things in one call** — points, entities, sources, *and the connections between them*, written nodes-first so links can reference things created in the same call. |
| `register_source` | **Declare that a source exists and how much to trust it.** The URL *is* the node identity; carries a credibility tier and a date. **Reads no content.** |
| `index_files` | **Read files from disk and turn their content into memory** — points, entities, embeddings. Batched, with resume. |
| `capture_knowledge` | **An agent's own journal** — save a batch of session notes (deduped), write a diary entry, read entries back. ⚠️ **Misnamed, and it mixes a read with writes — see below.** |
| `capture_session_local` | **Turn a conversation into memory in your own graph.** Turns become points; an LLM extracts beliefs and the links between them. |
| `capture_session_hosted` | **The same, against the hosted service** — extracts locally, validates, then sends. Needs an API key and a reachable endpoint. |
| `manage_source_trust` | **Score and set how reliable a source is**, and read the cached reliability back. |
| `link_entities` | **Connect two nodes.** Epistemic relations (supports, contradicts) build an operator; everything else a plain edge. The caller does not need to know which. |
| `record_decision` | **Record a human decision or approval** as a first-class node that later retrieval can find. |
| `revise_knowledge` | **Change something already stored** — edit it, replace it with a successor, withdraw it, promote a draft, or set its starting confidence. |
| `delete_knowledge` | **Remove a node.** |
| `stabilize_beliefs` | **Recompute confidence across the graph** so beliefs settle after a change. |
| `approve_merge` | **Approve merging two things the system thinks are the same.** |
| `adjust_relationship` | **Change an existing epistemic link's strength, or annotate it.** |
| `manage_deployment` | **Administration** — orgs, graphs, members, API keys, invitations, agent signup. |
| utilities | `close` tears down connections; `ulid` generates an id. |

### Two problems this exposes

**1. `capture_knowledge` mixes a read with writes.** Its members are `checkpoint` and
`diary_write` (writes) and `diary_read` (**a read**). That breaks the rule the rest of this
list is built on: a tool on the customer-grantable surface is either read or write, never
both. It has to split.

**2. `capture_knowledge` does not describe what it does.** The members are an agent's own
journal, not knowledge capture. `capture` also now collides with
`capture_session_local` / `capture_session_hosted`, which *do* capture sessions.

**Proposed split** (owner decision):

| Was | Becomes | Kind |
|---|---|---|
| `checkpoint`, `diary_write` | `write_journal` | write |
| `diary_read` | `read_journal` | read |

**Also observed:** `checkpoint` and the diary methods take `wing` / `room` parameters.
That vocabulary appears nowhere in `docs/ONTOLOGY.md`, which models graphs, namespaces and
containment differently. These three methods may descend from an earlier memory model and
should be checked against the ontology before they are carried forward — flagged, not
concluded.

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
**collapse the aliases**. The sketch above does exactly that: **32 groups over 149
names** — 26 memory-facing (R1–R9, W1–W17) and 6 control-plane namespaces (N1–N6) —
reached by grouping and merging; the retired primitives stop resolving — a call raises with the
hint naming its replacement (#3836 (c)) — and `backfill_v25` is archived. The
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

## Callers that must be migrated first

The eval harness drives `TortoiseSDK` **by method name** — no eval or benchmark invokes an
MCP tool. So a *tool* rename is invisible to it and an *SDK* rename is not. Every name below
is **retired**, not aliased: after Phase 2 a call to it **fails and names its replacement**
(#3836 (c) ruling — no SDK alias layer, no warning shim, no call telemetry). The two names that
are themselves targets — `create_entity` and `close` — do **not** retire and need no migration;
they are listed because the harness calls them and their signature must not drift under it. So
this is not a set of names to preserve; it is a migration order, because these in-repo callers
are the only callers that exist:

`create_point` · `create_operator` · `create_event` · `create_entity` · `get_point` ·
`ingest` · `recall_state` · `promote_point` · `mitigate_operator` · `compute_confidence` ·
`retract_point` · `dream` · `tortoise_fts_query` · `close`

## What we have that competitors do not — and why

> **⚠️ This table uses its own W/N numbering, which is NOT the numbering of the 32-group target
> list above.** The labels `R1–R9`, `W1–W17`, `N1–N6` are reused here for different groups: above,
> `W2` is `write_knowledge`; here, `W2` is `create_source`/`complete_source`. This table also uses
> `W18`, `W19` and `N7`, which exist nowhere in the target list. **Cross-reference by method name,
> never by label.** Reconciling the two schemes is part of Phase 0.3b, which owns the per-name SDK map.

Every method outside the **consensus core** needs a justification strong enough to survive
review. The core, measured across 13 competitors, is only **six capability buckets** —
*add · search · get/list · delete · update · namespace-scoping* — each held by 10–13 of 13
products. Anything else on our surface is differential and must earn its place.

Justification strength is graded honestly, including where it is weak. "Nobody else does
this" is **not** by itself a strong justification, and several entries below fail that test.

| Group | Members | Nearest competitor capability | Justification | Strength |
|---|---|---|---|---|
| R1 | `tortoise_fts_query`, `query`, `paginated_query`, `query_points_by_tag`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` | search (13/13) | **Core — table stakes.** | — |
| R2 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context` | none for gaps | Belief-propagation recall. `recall_gaps` — *what the graph does not know* — has **no analogue in any surveyed product**. | **strong** |
| R3 | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` | get/list (12/13) | **Core.** | — |
| R4 | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` | Graphiti search, Neo4j retrievers | Graph-native traversal reachable *without* an LLM. Competitors fold traversal into search. The two governance members are ours alone. | adequate |
| R5 | `status`, `taxonomy`, `list_*`, `audit`, `check_structure`, `validate_domain`, `stale_points`, `summarize_structure`, `dream_health_check`, `dream_health_state`, **`test_guard`** | `cognee.validate` | Graph structural validation — **Cognee has this**, so the claim is *"we differ in kind"*: ours is ontology-aware (`docs/ONTOLOGY.md`) where Cognee checks orphaned edges and identity mismatch generically. **`test_guard` is kept — see the note below the table.** | adequate |
| R6 | `get_cross_lens_candidates`, `list_dedup_candidates` | `detect_contradictions` (Cognee, internal) | A **read-only** review queue for link candidates — surfaces the candidate, never auto-merges. No competitor exposes candidate review as public API. | adequate |
| R7 | `get_provenance_chain`, `belief_timeline`, `restore_point_at` | provenance graph (Cognee) | Cognee also has provenance, so ours must differ in kind: ours is **who decided** (Point→authoredBy→Subject→delegation) to their **where it came from**. `belief_timeline` — how a belief itself changed over time — has no analogue. | adequate / strong |
| R8 | `events_poll`, `list_batch`, `list_batches` | `history` (Mem0), `runs` (Letta) | A graph-native event log with batch containment. | adequate |
| R9 | `get_confidence`, `calibrate_summary`, `calibration_passed` | PSL, OpenCog PLN, NARS, Knowledge Vault | Belief propagation over a graph. **Not novel as a mechanism** — PSL, PLN and NARS all do BP with evidence revision, and Knowledge Vault fused probabilistic claims at web scale. **Novel only as a shipped product surface**: no commercial graph database found that ships belief propagation (Neo4j, Neptune, ArangoDB, TigerGraph, Memgraph, FalkorDB treat confidence as an ordinary property). | **strong as product, not as method** |
| W1 | `create_entity`, `create_point`, `create_subject`, `create_object`, `create_event`, `create_document`, `create_or_update_point`, `batch_create_points` | add (13/13) | **Core.** | — |
| W2 | `create_source`, `complete_source` | `write_note`, `add` | A **source with a credibility tier** whose URL *is* its identity. No competitor models source trust; credibility is not a field anywhere in the survey. | **strong** |
| W3 | `create_edge`, `create_derivation`, `link_source_to_entity` | `add_triplet` (Graphiti) | Typed edges constrained to an ontology allowlist, versus a free-form triplet insert. | adequate |
| W4 | `create_operator`, `create_direct_edge` | `rdfs:subClassOf`, `owl:disjointWith` | ⚠️ **Corrected.** Typed entailment AND incompatibility as first-class relationships are **standard RDF/OWL** (`subClassOf`, `disjointWith`, `complementOf`) — a novelty claim here would be **false**. What is *not* standard: the relation itself as a **weighted, mitigatable graph object with mutation history**. That narrower claim is the defensible one. | **narrow — see correction** |
| W5 | `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` | ingest (13/13) | **Core**, with file-hash resume — Cognee has `sync`, so this is table stakes rather than differential. | — |
| W6 | `ingest` | add (13/13) | **Core — and the single most capable call on the surface**: one bundle writes points + entities + sources + connections atomically, with local `ref` labels so edges can reference nodes created in the same call. It is currently folded into `capture_knowledge`; see Open items. | — |
| W7 | `capture_session`, `checkpoint`, `diary_write`, `diary_read` | Letta conversations, Cognee session | Turn a session into memory with LLM extraction. Episodic-memory bucket is real (6/13). | adequate |
| W8 | `commit_session` | same | Same user action, **different backend**: it extracts locally (5-stage v2 pipeline), Layer-1 validates, then POSTs to `/v1/sessions/commit`. `capture_session` writes to the local graph. The distinction is real — commit needs a hosted endpoint and an API key — but a caller thinks *"capture this session"* either way. | adequate, **badly named** |
| W9 | `update`, `update_point`, `update_entity` | update (11/13) | **Core.** | — |
| W10 | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | delete (12/13) | **Core.** | — |
| W11 | `supersede`, `supersede_point`, `invalidate_point` | Graphiti `invalid_at`/`expired_at` | Supersession is **implicit in theirs** (an ingest side effect they never expose) and **explicit in ours** — a first-class operation with edge transfer. | **strong** |
| W12 | `retract_point` | none | A claim is **withdrawn without a successor**. Every competitor either deletes or supersedes; none retracts. | **strong** |
| W13 | `promote_point`, `list_drafts`, `quarantine_batch` | Cognee write proposals | Draft→live promotion that also promotes incident operators, under review gating. Nobody else has a draft lifecycle for claims. | adequate |
| W14 | `operator_action`, `mitigate_operator`, `annotate_operator` | none | `mitigate_operator` dampens an operator's effective weight — **no surveyed system has a mitigation-bearing operator object**, in RDF/OWL, PSL, Cyc, PLN or any graph DB. This is the narrowest defensible novelty in the operator family. | **strong** |
| W15 | `set_point_baseline` | **none** | Declares a claim's **starting belief** (the Beta prior) with provenance on who set it. No product has a per-claim prior. | **strong** |
| W16 | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` | `mem_scheduler` (MemOS) | Ours recomputes **beliefs** over a graph; MemOS reschedules **storage**. Different in kind, not degree. | **strong** |
| W17 | `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` | none | Source credibility is a first-class, scored, cached property. Absent from every product surveyed. | **strong** |
| W18 | `approve_merge` | TerminusDB (claimed) | Every surveyed **memory** product resolves entities automatically. ⚠️ **Corrected:** TerminusDB *claims* change-request/approval on merge (vendor prose, **unverified** — API docs not read), and Cyc historically required human adjudication of contradictions. Our novelty is narrower: the gate is keyed to **epistemic state** (belief deltas, contested claims), not structural field conflicts. | **moderate — trigger differs, shape does not** |
| W19 | `file_decision`, `file_human_approval` | `propose_sql_write` (Cognee) | Decisions are **graph nodes** that participate in retrieval, not a pending queue beside the graph. | **strong** |
| N1–N6 | `org_*`, `graph_*`, `membership_*`, `apikey_*`, `invitation_*`, `signup_token_*` | Chroma AdminClient, Weaviate users/roles, Letta access_tokens | Multi-tenant SaaS control plane. **Two sub-blocks, and they are not the same thing.** `signup_token_*` (3) is the **agent self-signup path** — `agent_signup` (#1709) mints org + membership + API key in one transaction, and `signup_token_recover` is documented keyless recovery ("mint a NEW key on the token's org"). Since we intend agents to sign themselves up with an API key, **this is product capability, not admin** — justification upgraded to strong. `invitation_*` (8) is admin: inviting *humans* into an org, which no competitor models as API. | `signup_token_*` **strong**; `invitation_*` adequate / **weak on size** |
| N7 | `ulid`, `close`, **`test_guard`** | `close` (7/13) | `close` is core lifecycle. **`ulid` is an ID utility no competitor exposes; `test_guard` is a test helper.** | **weak** |

### What does not survive review

Evidence, not taste. These are the entries whose justification is weak on its own terms:

| Entry | Why it fails |
|---|---|
| **`ulid`** | An internal ID utility. Nothing competitor-side exposes one; callers can generate ULIDs. |
| **`invitation_*`** (8 methods) | Absent across the field because nobody *invites* — they create users directly. Eight public methods for an administrative flow no competitor models. |
| **`commit_session`** — *the name, not the method* | A distinct backend is a fine distinction to keep; the name does not convey it. A caller cannot tell it means "commit to the hosted service" rather than "finish capturing". |
| **`test_guard`** — *withdrawn, kept* | Its docstring is *"Assert the connected graph is safe for destructive test teardowns… Raises RuntimeError if the graph appears to be a production graph"* — it guards the exact incident this project already suffered (the FalkorDB production wipe). Nothing is wrong with the method; its **classification** is wrong: test infrastructure on the product surface, which should be documented as test-only rather than marketed as a capability. |
| **`signup_token_*`** — *withdrawn, kept* | Reclassified as the **agent self-signup path**, not admin CRUD: `agent_signup` (#1709) mints org + membership + API key in one transaction, and recovery is keyless by token. If agents are meant to sign themselves up with an API key, this is core product capability. |
| `complete_source` | Zero code callers. |
| `backfill_v25` | Already archived — targets a schema several versions old. |
| The five unresolvable `sdk_method` values | Declarations that do not resolve (`recommendation: fix-declaration` in the manifest). |

### The honest headline

⚠️ **A prior revision of this section was wrong, and the correction matters.** It compared us
against **memory** products and concluded that two capabilities — belief propagation and
epistemic operators — were "genuinely unprecedented". That comparison set cannot support the
claim. Against **graph, knowledge-graph and reasoning systems**, most of it does not survive:

- **Typed entailment and incompatibility as first-class relationships are standard RDF/OWL**
  (`rdfs:subClassOf`, `owl:disjointWith`, `owl:complementOf`), long before us. Claiming novelty
  there would be **false**.
- **Belief propagation over a graph is not novel as a mechanism** — PSL, OpenCog PLN and NARS
  all propagate belief with evidence revision; Knowledge Vault fused probabilistic claims at
  web scale; provenance semirings (PODS 2007) handled provenance *of a probability*.
- **Contradiction detection is built into production systems** — GraphDB (on the commit path),
  Stardog (with inconsistency proof trees), Cyc (a Contradiction Resolver).
- **Approval on merge is at least marketed** — TerminusDB claims change requests, though on
  **structural** conflicts rather than epistemic ones.

**What survives is narrower, and it is an integration claim, not a capability claim:**

> No documented knowledge system ships belief propagation whose factors are **user-authored
> epistemic operators**, surfacing contradiction as a **probabilistic contestedness signal**,
> gated by **human approval**, and driving **confidence-weighted retrieval** — in one product,
> applied to agent memory.

Each column exists somewhere — Cyc (entailment + contradiction resolver + TMS), PSL and PLN
(probabilistic BP over typed implication links), Stardog and GraphDB (production entailment,
inconsistency detection, proof trees). **None ships them together**, and none is a graph store
with an agent-memory API. Confidence-weighted retrieval is the least crowded of the six.

**The three closest systems**, and why they are not the same thing:

| System | Why it comes close | Why it is not us |
|---|---|---|
| **Cyc** | Entailment + disjointness, a Contradiction Resolver, truth maintenance, human adjudication | Its truth model is **five-valued and explicitly non-probabilistic** — Lenat's paper states Cyc "eschews numeric certainty factors" and reasons by argumentation |
| **PSL / OpenCog PLN** | Probabilistic inference over typed implication links; PLN's Revision merges conflicting evidence | **Frameworks, not stores** — rules live in a program file, not as graph objects; no approval gate, no retrieval layer, no API to a memory product |
| **Stardog + GraphDB** | Production entailment, built-in inconsistency detection on commit, proof trees showing rule and premises | **Monotonic, two-valued logic.** No belief, no propagation, no confidence-weighted ranking |

**And a gate on making the claim at all:** our own eval spec records that before #855
contradiction was **invisible** (variance 0.006 against a 0.04 threshold) and attacks *raised*
confidence. The epistemic differentiator is therefore true **only of the post-#855 engine with
the thresholds re-calibrated** — otherwise it describes a design aspiration the product does
not yet exhibit.

Everything else that looks differential is really an instance of the six core buckets with
our own semantics attached, which is defensible — but it is not a moat, and should not be
justified as one.

## Open items

| Item | Needs |
|---|---|
| The **four** new dispatchers (`search`, `get(type=)`, `overview(section=)`, `recall(mode=)`) | approval — additive, but they add names to a frozen surface |
| `invitation_get` | approval — a new name; the two lookup paths differ (salted-hash verify vs ULID match), so this is a design decision, not a rename |
| `supersede` / `operator_action` parameter forwarding | `valid_from` and `credibility` must be forwarded before those two collapses are safe — today each is either a `TypeError` or silently ignored |
| **`ingest` needs its own MCP tool name** | It is the only call that writes points + entities + sources + connections atomically — the batch-and-relationships upload — yet it is folded into `capture_knowledge` alongside two session workflows. Rename it out, or rename the container. |
| `test_guard`, `ulid` | No product justification exists for either. Candidates for removal from the public surface. |
| `commit_session` | Is it a distinct user action from `capture_session`, or an internal pipeline split? |
| `invitation_*` (8), `signup_token_*` (3) | 11 public methods for administrative flows no competitor models as product API. Keep as namespaced admin, or reduce. |
| `get_confidence` / `compute_confidence` / `get_source_reliability` | a decision: fix the declaration, or add a `write_back: bool` parameter — until then the read/write guarantee does not hold |
| The control-plane cold-handle write | approval of a fix, or an explicit exemption for operator-only surfaces |
| Five phantom `sdk_method` registry values | folded into #4035's class of defect |
| 26 memory-facing groups vs the approved 23 tools | whether the SDK mirrors the tool list 1:1 or stays a superset |
| `list_graphs` vs `graph_list` | a naming decision — raw DB names vs control-plane rows |
| `ulid`, `close`, `test_guard` | whether utilities belong on the public surface at all |

## Sequencing

1. **This list** → owner approval.
2. **The bridge table** (each tool's `type=`/`mode=` → the SDK method behind it) — the pre-flight.
3. **The SDK dispatchers**, which are additive and unblock the mirror.
4. **Collapse the aliases**, then **retire the old names to a failing name that names the
   replacement** (#3836 (c) ruling — no SDK warning shim). The in-repo callers listed above
   are the only callers, so migrating them is what makes this step safe.
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
