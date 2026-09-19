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

**Result: ~40 methods, each traceable to a named user doing a named thing.**

## The recommended surface

| # | Name | Does | MCP twin | Who needs it |
|---|---|---|---|---|
| | **SETUP** | | | |
| 1 | `Tortoise(...)` | Open a connection: endpoint, credentials, target graph | — | everyone |
| 2 | `close()` | Release connections and background work | — | everyone |
| | **MEMORY — READ** | | | |
| 3 | `search_knowledge` | Find things by text — full-text and hybrid | `search_knowledge` | agent, builder |
| 4 | `list_knowledge` | Browse and filter without text search; page results | `list_knowledge` | agent, builder |
| 5 | `recall_beliefs` | What the system believes, how strongly, the gaps, provenance, how a belief changed | `recall_beliefs` | agent |
| 6 | `get_entity` | Fetch one node by id, any kind | `get_entity` | agent, builder |
| 7 | `explore_connections` | Walk outward from a node through its links | `explore_connections` | agent |
| 8 | `graph_overview` | The state of the graph: taxonomy, counts, topics, stale items, integrity, engine health | `graph_overview` | agent, builder |
| 9 | `review_link_candidates` | Connections the system found but has not made. Read-only | `review_link_candidates` | agent |
| 10 | `poll_events` | Read the event log since a point in time | `poll_events` | builder |
| 11 | `inspect_batch` | Look at a batch of items held for review | `inspect_batch` | agent |
| | **MEMORY — WRITE** | | | |
| 12 | `create_entity` | Add one node — a claim, or a referent (subject, object, event, document) | `create_entity` | agent, builder |
| 13 | `write_knowledge` | Add many interconnected things in one call: points, entities, sources *and the links between them* | — ⚠️ *add to MCP* | builder |
| 14 | `register_source` | Declare a source exists and how much to trust it. URL is the identity. Reads no content | `register_source` | agent, builder |
| 15 | `index_files` | Read files off disk and turn their content into memory. Batched, resumable | `index_files` | builder |
| 16 | `capture_session_local` | Turn a conversation into memory in your own graph | `capture_knowledge` ⚠️ | agent, builder |
| 17 | `capture_session_hosted` | The same, against the hosted service | `capture_knowledge` ⚠️ | agent |
| 18 | `write_journal` | Save an agent's own notes — batched, deduplicated | `capture_knowledge` ⚠️ | agent |
| 19 | `read_journal` | Read an agent's own notes back | `capture_knowledge` ⚠️ | agent |
| 20 | `manage_source_trust` | Score and set how reliable a source is; read the cached score | `manage_source_trust` | agent |
| 21 | `link_entities` | Connect two nodes — an epistemic relation builds an operator, anything else a plain edge. The caller does not choose | `link_entities` | agent |
| 22 | `record_decision` | Record a human decision or approval as a node retrieval can find | `record_decision` | agent, builder |
| | **MEMORY — REVISE** | | | |
| 23 | `update_knowledge` | Change what a stored node says | `revise_knowledge` ⚠️ | agent |
| 24 | `supersede_knowledge` | Replace a node with a successor, keeping the history | `revise_knowledge` ⚠️ | agent |
| 25 | `withdraw_knowledge` | Retract or invalidate — the claim no longer stands | `revise_knowledge` ⚠️ | agent |
| 26 | `delete_knowledge` | Remove a node | `delete_knowledge` | agent, builder |
| 27 | `adjust_relationship` | Change an existing epistemic link's strength, or annotate it | `adjust_relationship` | agent |
| 28 | `stabilize_beliefs` | Recompute confidence so beliefs settle after a change | `stabilize_beliefs` | agent |
| 29 | `approve_merge` | Approve merging two things the system thinks are the same | `approve_merge` | agent |
| | **TENANCY — the builder's half** | | | |
| 30 | `create_org` | Create a workspace with its own graph namespace | `manage_deployment` | builder, team |
| 31 | `get_org` | Read a workspace | `manage_deployment` | builder, team |
| 32 | `list_orgs` | List workspaces you can reach | `manage_deployment` | team |
| 33 | **`create_graph`** | **Provision a graph — one per end-customer, or per agent.** ⚠️ **DOES NOT EXIST TODAY** | `manage_deployment` | **builder** |
| 34 | `list_graphs` | List graphs in a workspace | `manage_deployment` | builder, team |
| 35 | `count_graphs` | Count graphs — for quota and billing | `manage_deployment` | builder |
| 36 | `delete_graph` | Destroy a graph — churn, right-to-erasure | `manage_deployment` | builder |
| 37 | `restore_graph` | Undo a delete | `manage_deployment` | builder |
| 38 | `set_graph_name` | Label a graph | `manage_deployment` | builder, team |
| 39 | `create_key` | Issue a credential. **Scoped to one graph** — this is the isolation boundary | `manage_deployment` | builder |
| 40 | `list_keys` | List a graph's credentials | `manage_deployment` | builder |
| 41 | `revoke_key` | Revoke a credential | `manage_deployment` | builder |
| 42 | `add_member` | Grant a person access to a workspace | `manage_deployment` | team |
| 43 | `list_members` | List who has access | `manage_deployment` | team |
| 44 | `remove_member` | Revoke a person's access | `manage_deployment` | team |
| | **PLATFORM** | | | |
| 45 | `run_onboarding` | Guided first-run: connect, verify, first write | `run_onboarding` | everyone |
| 46 | `close()` | *(see 2)* | — | — |

⚠️ = the split is **owed to the MCP list** — see "Open decisions".

## Discarded — and why

| Name (or group) | Count | Rationale |
|---|---|---|
| `create_or_update_point` | 1 | Body is a single delegation to `create_point`. Adds no contract. |
| `index_sessions` | 1 | Body is a single delegation to `ingest_corpus`. Adds no contract. |
| `_get_entity`, `_delete_entity` | 2 | Private implementations that `get_entity`/`delete_entity` merely wrap. Merge the private body up; the public name is correct. |
| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. They run once, then they are dead code with a public promise attached. |
| `create_subject`, `create_object`, `create_event`, `create_document` | 4 | Collapsed into `create_entity(type=)`. The ontology already models all of them as entities. |
| `create_point` | 1 | Same — `create_entity(type='point')`. Kept only as a warning alias because the eval harness calls it by name. |
| `delete_point`, `delete_point_wrapped` | 2 | Collapsed into `delete_knowledge`. |
| `update_point`, `update_entity` | 2 | Collapsed into `update_knowledge`. |
| `supersede`, `supersede_point` | 2 | Collapsed into `supersede_knowledge`. They also disagree — `supersede_point` carries a `valid_from` the other silently drops. |
| `retract_point`, `invalidate_point` | 2 | Collapsed into `withdraw_knowledge`. Type-suffixed names imply `retract_object`, `retract_event` — a family that should not exist. |
| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence wrangling. Reachable through `revise_knowledge` and `stabilize_beliefs`; not separate promises. |
| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | Collapsed into `link_entities`, which dispatches on the relation. Reification is our implementation detail, not the caller's problem. |
| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | Collapsed into `adjust_relationship`. `operator_action(**kwargs)` currently *accepts and silently ignores* `credibility` — a bug, not a feature. |
| `file_human_approval` | 1 | Collapsed into `record_decision`. |
| `tortoise_get_point`-style narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | Collapsed into `get_entity` except where a distinct shape is genuinely returned. |
| `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_topics`, `list_graphs`→no, `stale_points`, `status`, `check_structure`, `validate_domain`, `audit`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~14 | Collapsed into `graph_overview`. The MCP list already made this decision by merging 14 narrow aliases into one sectioned tool. |
| `recall_gaps`, `recall_subgraph`, `recall_state`, `retrieval_legs`, `calibrate_summary`, `calibration_passed`, `provenance`, `belief_timeline`, `restore_point_at`, `session_context`, `volunteer_context` | ~11 | Collapsed into `recall_beliefs`, which returns confidence, gaps, subgraph and provenance together. |
| `query`, `paginated_query`, `query_points_by_tag`, `batch_create_points` | 4 | Collapsed into `list_knowledge` / `create_entity`. Paging is a parameter, not a promise. |
| `traverse`, `expand_relationships`, `get_org_structure` | 3 | Collapsed into `explore_connections`. |
| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | Collapsed into `search_knowledge`. |
| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | Collapsed into `review_link_candidates`. |
| `list_batch`, `list_batches` | 2 | Collapsed into `inspect_batch`. |
| `events_poll` | 1 | Renamed `poll_events` to match the MCP tool. Not discarded — realigned. |
| `checkpoint`, `diary_write` | 2 | Renamed `write_journal`. They are the agent's own notes, not knowledge capture. |
| `diary_read` | 1 | Renamed `read_journal`. **It is a read** and was sitting inside a write tool — a read/write violation. |
| `ingest_corpus`, `index_file`, `mine_corpus`, `session_index_health` | 4 | Collapsed into `index_files`. |
| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | Collapsed into `manage_source_trust`. |
| `complete_source` | 1 | **Zero callers in the entire repo.** It has been public and unused the whole time. |
| `test_guard` | 1 | Guards the production-wipe incident and must stay — but it is *test infrastructure*, not product surface. Belongs on an internal testing helper, not the SDK. |
| `ulid` | 1 | A ULID generator. Not a memory operation; a two-line utility anyone can vendor. |
| `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` | 3 | Console diagnostics. `key_ids` and `active_key_count` fold into `list_keys`; recording is a console toggle. |
| `invitation_create`, `invitation_list`, `invitation_accept`, `invitation_get_by_id`, `invitation_get_by_token`, `invitation_revoke` | 6 | The invitation **UX** belongs to the console, which is where a human clicks it. A builder embeds `add_member`; they do not reimplement email invites. |
| `signup_token_lookup`, `signup_token_recover`, `signup_token_revoke` | 3 | Operator-side agent self-signup. Real capability, but not the builder's surface — it is our provisioning. |
| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. Reachable through the console; not separate promises. |
| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface — these exist because we operate the service. |
| Internal implementation — everything the battery drives directly | ~80 | Not deleted. Becomes private (`_`). Still works, still tested, no longer a promise. |

## Open decisions

1. **`create_graph` is missing and is the builder's keystone.** Provisioning a graph per
   end-customer is only possible today by creating an **org** per end-customer. That is a
   tenant per user. This is the single highest-value addition to the surface.
2. **The MCP list must take the same three splits**, or the two layers diverge — the exact
   problem naming rule 1 exists to prevent. `capture_knowledge` becomes
   `capture_session_local` / `capture_session_hosted` / `write_journal` / `read_journal`; and
   `revise_knowledge` becomes `update_knowledge` / `supersede_knowledge` / `withdraw_knowledge`.
   That takes the MCP list from 23 to 29 — still inside the 25–30 band.
3. **The `revise_knowledge` split is owed to the flag rule.** A parameter that selects which
   operation runs is two operations. `update`, `supersede` and `withdraw` have different side
   effects, so they are three. The earlier merge was wrong and is corrected here.
4. **`graph_set_recording`** — keep as a console-only toggle, or expose? Leaning console-only.

## Not in beta

The **eval harness**. No vendor in the category exposes its proprietary scenario suite; the
public harnesses that exist (Zep, Mem0, Supermemory, Basic Memory) reproduce *academic*
benchmarks and are a different artifact class. Worth revisiting post-beta — the category has
**no independently reproduced results at all**, and vendor numbers diverge by 8–45 points, so
a runnable, honest artifact is a genuine differentiator. It is cheap: every harness in the
field makes the customer supply their own model key.
