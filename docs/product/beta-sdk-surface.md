---
title: Beta SDK Surface — the recommended set
status: draft
products: [tortoise]
aboutObjects: [tortoise-sdk, mcp-server]
approval_status: proposed
---

# Beta SDK surface

**The recommended set a beta developer sees.** Designed from who the SDK is for and what
they do — not pruned from the existing 150.

## Why the set looks like this

The SDK serves **two audiences**, and only one of them is the agent:

- **The memory API** — an agent, or code acting for one, reading and writing *one graph*.
  This is what the MCP server exposes, and the SDK mirrors it name-for-name.
- **The tenancy API** — a **builder** running an app where every end-customer has an agent
  with private memory. They must provision a graph per end-customer, issue a credential
  scoped to it, prove isolation, and destroy it on churn. **An agent never does any of this,
  and a builder does nothing else.**

The MCP list alone is therefore the *agent's half*. The SDK is the builder's whole view.

**Result: 45 methods, each traceable to a named user doing a named thing.**

## The recommended surface

| # | Name | Does | MCP twin | Who needs it |
|---|---|---|---|---|
| | **SETUP** | | | |
| 1 | `Tortoise(...)` | Open a connection: endpoint, credentials, target graph | — | everyone |
| 2 | `close()` | Release connections and background work | — | everyone |
| | **MEMORY — READ** | | | |
| 3 | `search_knowledge` | Find things by text — full-text and hybrid | `search_knowledge` | agent, builder |
| 4 | `list_knowledge` | Browse and filter without text search; page results | `list_knowledge` | agent, builder |
| 5 | `recall_confidence` | What the system holds, how confident it is, what it does *not* know, and where it came from | `recall_confidence` | agent |
| 6 | `get_entity` | Fetch one node by id, any kind | `get_entity` | agent, builder |
| 7 | `explore_connections` | Walk outward from a node through its links | `explore_connections` | agent |
| 8 | `graph_overview` | The state of the graph: taxonomy, counts, topics, stale items, integrity, engine health | `graph_overview` | agent, builder |
| 9 | `review_link_candidates` | Connections found but not made. Read-only | `review_link_candidates` | agent |
| 10 | `poll_events` | Read the event log since a point in time | `poll_events` | builder |
| 11 | `inspect_batch` | Look at a batch of items held for review | `inspect_batch` | agent |
| 12 | `read_journal` ⚠️ | Read an agent's own notes back | `read_journal` | agent |
| | **MEMORY — WRITE** | | | |
| 13 | `create_entity` | Add one node — a claim, or a referent (subject, object, event, document) | `create_entity` | agent, builder |
| 14 | `write_knowledge_batch` | Write many interconnected things in one call: points, entities, sources *and the links between them*, atomically | — | builder |
| 15 | `register_source` | Declare a source exists. The URL is its identity. **Trust defaults from the source kind**; `tier=` overrides | `register_source` | agent, builder |
| 16 | `index_sources` | Read files off disk and register each as a Source with its content in memory. Batched, resumable | `index_sources` | builder |
| 17 | `ingest_session_local` | Turn a conversation into memory in your own graph | `ingest_knowledge` | agent, builder |
| 18 | `ingest_session_hosted` | The same, against the hosted service | `ingest_knowledge` | agent |
| 19 | `write_journal` ⚠️ | Save an agent's own notes — batched, deduplicated | `write_journal` | agent |
| 20 | `manage_source_trust` | Score and set how reliable a source is; read the cached score | `manage_source_trust` | agent |
| 21 | `link_entities` | Connect two nodes. An epistemic relation builds an operator, anything else a plain edge — the caller does not choose | `link_entities` | agent |
| 22 | `log_decision` ⚠️ | Record a decision quickly: the question, the options, the evidence, the choice — in one call | `log_decision` | agent, builder |
| | **MEMORY — REVISE** | | | |
| 23 | `update_knowledge` | Change what a stored node says | `revise_knowledge` | agent |
| 24 | `supersede_knowledge` | Replace a node with a successor, keeping the history | `revise_knowledge` | agent |
| 25 | `withdraw_knowledge` | Retract or invalidate — the claim no longer stands | `revise_knowledge` | agent |
| 26 | `delete_knowledge` | Remove a node | `delete_knowledge` | agent, builder |
| 27 | `adjust_relationship` | Change an existing epistemic link's strength, or annotate it | `adjust_relationship` | agent |
| 28 | `refresh_confidence` | Recompute confidence after changes. Scope is chosen for you — dirty nodes by default, whole graph on request | `refresh_confidence` | agent |
| 29 | `approve_merge` | Approve merging two things the system thinks are the same | `approve_merge` | agent |
| | **TENANCY — the builder's half** | | | |
| 30 | `create_org` | Create a workspace with its own graph namespace | `manage_deployment` | builder, team |
| 31 | `get_org` | Read a workspace | `manage_deployment` | builder, team |
| 32 | `list_orgs` | List workspaces you can reach | `manage_deployment` | team |
| 33 | **`create_graph`** | **Provision a graph — one per end-customer, or per agent** | `manage_deployment` | **builder** |
| 34 | `list_graphs` | List graphs in a workspace | `manage_deployment` | builder, team |
| 35 | `count_graphs` | Count graphs — for quota and billing | `manage_deployment` | builder |
| 36 | `delete_graph` | Destroy a graph — churn, right-to-erasure | `manage_deployment` | builder |
| 37 | `restore_graph` | Undo a delete | `manage_deployment` | builder |
| 38 | `set_graph_name` | Label a graph | `manage_deployment` | builder, team |
| 39 | `create_key` | Issue a credential, **scoped to one graph** — the isolation boundary | `manage_deployment` | builder |
| 40 | `list_keys` | List a graph's credentials | `manage_deployment` | builder |
| 41 | `revoke_key` | Revoke a credential | `manage_deployment` | builder |
| 42 | `add_member` | Grant a person access to a workspace | `manage_deployment` | team |
| 43 | `list_members` | List who has access | `manage_deployment` | team |
| 44 | `remove_member` | Revoke a person's access | `manage_deployment` | team |
| | **PLATFORM** | | | |
| 45 | `run_onboarding` | Guided first-run: connect, verify, first write | `run_onboarding` | everyone |

**45 methods** against **150** today. ⚠️ marks the three rows still **pending the owner's ruling** (items 1 and 2 under Open items) — they are on the list as the shape to aim for, not as settled.

The MCP list carries these capability-for-capability. **23 → 24**: the only forced change is lifting `read_journal` out of `capture_knowledge`, because a read must not sit inside a write tool. If the journal capability survives with both halves split, it is 25. The tenancy block (rows 30–44) is **one** MCP tool, `manage_deployment`, since an agent never provisions.

## The decision-process question

Decisions were raised as a case worth designing for directly, because **running a decision
process should be fast and smooth.** What follows is the range of processes a caller might
actually run, and what each needs.

The organising observation: **what varies between decision processes is the *rigor*, not the
*data*.** Every decision — cheap or expensive — has the same four parts: the question, the
options, the reasons, the choice. Only the depth of the reasoning differs.

| Decision process | Question | Options | Reasons | Choice | Covered by |
|---|---|---|---|---|---|
| **Full epistemic** — the 7-step workflow | ✓ | options + criteria | findings, IMPL/NAND, mitigations, sub-mitigations | EP-ranked | `create_entity` + `link_entities` + `refresh_confidence` |
| **Quick** — clear answer, low stakes | ✓ | ✓ | evidence only, no edges | the caller's | **`log_decision`** |
| **Pairwise / A-B** | ✓ | two | one criterion | the caller's | a case of Quick |
| **Multi-party** — several deciders | ✓ | ✓ | per-decider weights | combined | not covered — a gap to name, not solve |
| **Human-gated** — needs a sign-off | ✓ | ✓ | ✓ | an approval | `log_decision` + an approval record |
| **Retrospective** — logged after the fact | ✓ | ✓ | whatever exists | already made | the timeline shape |
| **Reversible / time-boxed** | ✓ | ✓ | ✓ | revisit date | not covered |

**So the full epistemic path needs nothing new** — it is already four existing calls. What
is missing is the **cheap end**: one call that records a decision without the ceremony.

### The defect in the current shape

`file_decision` writes the decision, its options and its evidence as **Points**:

```python
decision = self.create_point('decision', f'Decision: {options[choice]}', status='live')
opt_point = self.create_point('option', ...)
ev_point  = self.create_point('evidence', ...)
```

The `tortoise-decide` skill's own anti-pattern list forbids exactly this:

> ⛔ **Don't store decisions as first-class Points** — the graph says "this state is based on
> these reasons"; **the decision dimension is the decision-as-Event timeline.**

**So the shape may be wrong, not the name.** A decision is a thing that happened at a time —
an Event. Options and evidence are Points; the decision is a moment.

**Recommended shape** (`log_decision`): record the **decision as an Event** on the timeline,
create the options and evidence as Points, wire the IMPL edges, atomically. EP stays
optional — the point of the cheap path is that it is cheap.

**This needs the owner's ruling before it is implemented**, because it changes what a
decision *is* in the graph, and the skill and the code currently disagree.

## Discarded — and why

| Name (or group) | Count | Rationale |
|---|---|---|
| `create_or_update_point`, `index_sessions` | 2 | Body is a single delegation to another **public** method. No added contract. |
| `_get_entity`, `_delete_entity` | 2 | Private impls that `get_entity`/`delete_entity` merely wrap. Merge the body up; the public name is right. |
| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. Run once, then dead code carrying a public promise. |
| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`. The ontology already models all of them as entities. |
| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |
| `update_point`, `update_entity` | 2 | → `update_knowledge`. |
| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |
| `retract_point`, `invalidate_point` | 2 | → `withdraw_knowledge`. Type-suffixed names imply a `retract_object`/`retract_event` family that should not exist. |
| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence wrangling — reachable through the canonical two. Not separate promises. |
| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. Reification is our implementation detail. |
| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship`. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug, not a feature. |
| `file_human_approval` | 1 | → `log_decision` plus an approval record. |
| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |
| `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_topics`, `status`, `stale_points`, `check_structure`, `validate_domain`, `audit`, `summarize_structure`, `dream_health_*` | ~14 | → `graph_overview`. The MCP list already made this call by merging 14 narrow aliases into one sectioned tool. |
| `recall_gaps`, `recall_subgraph`, `recall_state`, `retrieval_legs`, `calibrate_summary`, `calibration_passed`, `provenance`, `belief_timeline`, `restore_point_at`, `session_context`, `volunteer_context` | ~11 | → `recall_confidence`, which returns confidence, gaps, subgraph and provenance together. |
| `query`, `paginated_query`, `query_points_by_tag`, `batch_create_points` | 4 | → `list_knowledge` / `create_entity`. Paging is a parameter, not a promise. |
| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |
| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |
| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |
| `list_batch`, `list_batches` | 2 | → `inspect_batch`. |
| `events_poll` | 1 | Realigned to the MCP name `poll_events`. Not discarded. |
| `checkpoint` | 1 | Dedup-on-write is the only non-redundant part; it belongs in `write_knowledge_batch`. The rest is unapproved vocabulary — see below. |
| `diary_write` / `diary_read` | 2 | Unapproved vocabulary — see below. If the capability survives, they become `write_journal` / `read_journal`. |
| `ingest_corpus`, `index_file`, `mine_corpus`, `session_index_health` | 4 | `ingest_corpus`/`index_file` → `index_sources`. **`mine_corpus` is different** — it *mines* a session corpus, which is not indexing, and must not be folded in. |
| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust`. |
| `complete_source` | 1 | **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes via its URL upsert — and it has **zero callers in the repo**. |
| `test_guard` | 1 | **Kept, relocated.** It guards the production-wipe incident and must survive, but it is *test infrastructure*, so it moves out of the product SDK into test support. |
| `ulid` | 1 | A ULID generator. Not a memory operation. |
| `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` | 3 | Console diagnostics. `key_ids`/`active_key_count` fold into `list_keys`. |
| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. A builder embeds `add_member`, not email plumbing. |
| `signup_token_*` (3) | 3 | Operator-side agent self-signup. Real capability, but it is *our* provisioning. |
| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. |
| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface. |
| internal implementation (battery-driven) | ~80 | **Not deleted — becomes private.** Still works, still tested, no longer a promise. |

## Open items

### Needs the owner's ruling

1. **`log_decision`'s shape.** The skill says decisions are Events; `file_decision` writes
   Points. Which is right determines whether the cheap decision path is a rename or a
   rewrite. **Blocking for #22.**
2. **The journal capability.** `checkpoint` and the diary pair arrived in the **initial
   codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room`
   parameters appear **nowhere in `docs/ONTOLOGY.md`** (the four `wing` matches in that file
   are the words *showing*, *following*, *narrowing*). They are nonetheless **live in the MCP
   server**, registered and quota-gated. Either the capability has a use case and needs an
   ontology home, or it comes out. **An issue will be drafted for review — not filed blind**,
   in case a use case exists that has not been found.
3. **`create_graph` does not exist.** Graphs are created implicitly inside `org_create`, one
   per org, so provisioning a graph per end-customer today requires **an org per
   end-customer** — a tenant per user. The delete path is real (`graph_delete`,
   `graph_restore`); only creation is missing. This is the keystone gap for the builder.

### Approved and folded in

- `write_knowledge` → **`write_knowledge_batch`** (the batching was invisible).
- `index_files` → **`index_sources`** (verified: `file_indexer.py` derives a Source url per
  file and writes `sourceKind` on the **Source** node).
- `capture_session_*` → **`ingest_session_*`** ("capture" described neither the input nor the
  output; it reads an artifact, extracts, and stores).
- `stabilize_beliefs` → **`refresh_confidence`**, and `recall_beliefs` →
  **`recall_confidence`** — a "belief" is a Point; what a caller wants to know is confidence.
- `register_source`'s `tier=` is **demoted to an override**, with trust defaulting from the
  source kind. `SOURCE_KIND_DEFAULTS` already maps kind → tier; the legacy kinds (`document`,
  `github_issue`, `github_pr`, `slack_message`, `linear_card`, `linear_cycle`) are
  deliberately `None` and need filling in. **This changes behaviour and needs explicit
  approval at implementation** — it is not a silent fill-in.
- `complete_source` cut; `test_guard` relocated.

### MCP impact

**23 → 24** (25 if the journal splits both ways). The only forced change is lifting
`read_journal` out of `capture_knowledge`, because it is **a read inside a write tool** — a
read/write guarantee violation. The session split stays SDK-only: `mcp_server.py:2986` shares
`_capture_session_impl` with the hosted API
*"so the two surfaces can never drift"*, meaning the handler already routes local-vs-hosted
internally. **An agent never chooses a backend** — it is a deployment fact.

## Not in beta

The **eval harness**. No vendor in the category exposes its proprietary scenario suite; the
public harnesses that exist (Zep, Mem0, Supermemory, Basic Memory) reproduce *academic*
benchmarks and are a different artifact class. Worth revisiting post-beta — the category has
**no independently reproduced results at all**, and vendor numbers diverge by 8–45 points, so
a runnable, honest artifact is a genuine differentiator. It is cheap: every harness in the
field makes the customer supply their own model key.
