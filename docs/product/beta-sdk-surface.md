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

**Canonical terms used throughout:** an **organisation account** (the customer's account —
the billing and plan boundary) owns many **memory graphs** (the unit of memory).

## Why the set looks like this

The SDK serves **two audiences**, and only one of them is the agent:

- **The memory API** — an agent, or code acting for one, reading and writing *one memory
  graph*. This is what the MCP server exposes, and the SDK mirrors it name-for-name.
- **The tenancy API** — a **builder** running an app where every end-customer has an agent
  with private memory. They provision a memory graph per end-customer, issue a credential
  scoped to it, and destroy it on churn.

**The builder pays for their end-customers.** An end-customer never has an organisation
account — they are a memory graph inside the builder's account. Verified against
`tortoise/pricing.py`: `tier_limits()` exposes `max_graphs_per_team`, and its value is
**`None` = unlimited** on builder and team plans. So the shape is *one account, many memory
graphs*, with the plan deciding how many.

An agent never provisions. A builder does nothing else **in the tenancy block** — they also
call the memory API, because the memory API is what their product is built on.

## The recommended surface

| # | Name | Does | MCP twin | Who needs it |
|---|---|---|---|---|
| | **SETUP** | | | |
| 1 | `Tortoise(...)` | Open a connection to an organisation account and one of its memory graphs | — | everyone |
| 2 | `close()` | Release connections and background work | — | everyone |
| | **MEMORY — READ** | | | |
| 3 | `search_knowledge` | Find things by text — full-text and hybrid | `search_knowledge` | agent, builder |
| 4 | `list_knowledge` | Browse and filter without text search; page results | `list_knowledge` | agent, builder |
| 5 | `recall_confidence` | What is held and how confident it is, with the operators, NANDs and mitigations behind each item | `recall_confidence` | agent |
| 6 | `get_entity` | Fetch one node by id, any kind | `get_entity` | agent, builder |
| 7 | `explore_connections` | Walk outward from a node through its links | `explore_connections` | agent |
| 8 | `graph_overview` | What is in this memory graph and how it is shaped: taxonomy, structure, kinds, tags, namespaces, sources, graph counts | `graph_overview` | agent, builder |
| 9 | `list_sources` | Enumerate the sources this memory graph holds, **each with its credibility tier** | `list_sources` | agent, builder |
| 10 | `review_link_candidates` | Connections found but not made. Read-only | `review_link_candidates` | agent |
| 11 | `poll_events` | Read the event log since a point in time | `poll_events` | builder |
| 12 | `inspect_batch` | Look at a batch of items held for review | `inspect_batch` | agent |
| 13 | `read_journal` ⚠️ | Read an agent's own notes back | `read_journal` | agent |
| | **MEMORY — WRITE** | | | |
| 14 | `create_entity` | Add one node — a claim, or a referent (subject, object, event, document) | `create_entity` | agent, builder |
| 15 | `write_knowledge_batch` | Write many interconnected things in one call: points, entities, sources *and the links between them*, atomically | — | builder |
| 16 | `register_source` | Declare a source exists. The URL is its identity. **Trust defaults from the source kind**; `tier=` overrides | `register_source` | agent, builder |
| 17 | `index_sources` | Read files off disk and register each as a source with its content in memory. Batched, resumable | `index_sources` | builder |
| 18 | `ingest_session` | Turn a conversation into memory. **The backend is the target graph's configuration, not a call parameter** | `ingest_knowledge` | agent, builder |
| 19 | `write_journal` ⚠️ | Save an agent's own notes — batched, deduplicated | `write_journal` | agent |
| 20 | `manage_source_trust` | Set how reliable a source is; read the score back | `manage_source_trust` | agent |
| 21 | `link_entities` | Connect two nodes. An epistemic relation builds an operator, anything else a plain edge — the caller does not choose | `link_entities` | agent |
| 22 | `log_decision` ⚠️ | Record a decision quickly: the question, the options, the evidence, the choice — in one call | `log_decision` | agent, builder |
| | **MEMORY — REVISE** | | | |
| 23 | `update_knowledge` | Change what a stored node says | `revise_knowledge` | agent |
| 24 | `supersede_knowledge` | Replace a node with a successor, keeping the history | `revise_knowledge` | agent |
| 25 | `withdraw_knowledge` | Retract or invalidate — the claim no longer stands | `revise_knowledge` | agent |
| 26 | `delete_knowledge` | Remove knowledge — **a node or a link**. Deleting a link is how a wrong edge comes out | `delete_knowledge` | agent, builder |
| 27 | `adjust_relationship` | Change an existing link's strength, or annotate it | `adjust_relationship` | agent |
| 28 | `refresh_confidence` | Recompute confidence after changes. Scope is chosen for you — changed nodes by default, the whole graph on request | `refresh_confidence` | agent |
| 29 | `approve_merge` | Approve merging two things the system thinks are the same | `approve_merge` | agent |
| | **TENANCY — the builder's half** | | | |
| 30 | `get_organisation_account` | Read the account and the plan it is on | `manage_deployment` | builder, team |
| 31 | **`create_memory_graph`** | **Provision a memory graph — one per end-customer, or per agent** | `manage_deployment` | **builder** |
| 32 | `list_memory_graphs` | List the account's memory graphs | `manage_deployment` | builder, team |
| 33 | `count_memory_graphs` | Count them — against the plan's allowance | `manage_deployment` | builder |
| 34 | `delete_memory_graph` | Destroy a memory graph — churn, right-to-erasure | `manage_deployment` | builder |
| 35 | `restore_memory_graph` | Undo a delete | `manage_deployment` | builder |
| 36 | `set_memory_graph_name` | Label a memory graph | `manage_deployment` | builder, team |
| 37 | `set_memory_graph_backend` | Choose local or hosted **for that memory graph** | `manage_deployment` | builder |
| 38 | `create_key` | Mint a credential, **scoped to one memory graph** — the isolation boundary | `manage_deployment` | builder |
| 39 | `list_keys` | List a memory graph's credentials | `manage_deployment` | builder |
| 40 | `check_key` | **What does this credential reach?** Makes the isolation promise verifiable | `manage_deployment` | builder |
| 41 | `revoke_key` | Revoke a credential | `manage_deployment` | builder |
| 42 | `add_member` | Grant a person access to the account | `manage_deployment` | team |
| 43 | `list_members` | List who has access | `manage_deployment` | team |
| 44 | `remove_member` | Revoke a person's access | `manage_deployment` | team |
| | **PLATFORM** | | | |
| 45 | `run_onboarding` | Guided first-run: connect, verify, choose the backend, first write | `run_onboarding` | everyone |

**45 methods** against **150** today. ⚠️ marks the three rows still **pending a ruling** —
they are the shape to aim for, not settled.

**The backend is configuration, not a call parameter.** It is a property of each memory
graph, set at creation or during onboarding — and **one organisation account can hold both a
local and a hosted memory graph.** So `ingest_session` is one method that routes on the
target graph's configuration. An agent does not choose a backend; the connection knows.

## The decision-process question

Running a decision process should be **fast and smooth**. Here is the range of processes a
caller might run, and what each needs.

**What varies between decision processes is the rigor, not the data.** Every decision — cheap
or expensive — has the same four parts: the question, the options, the reasons, the choice.
Only the depth of the reasoning differs.

| Decision process | Question | Options | Reasons | Choice | Covered by |
|---|---|---|---|---|---|
| **Full epistemic** — the 7-step workflow | ✓ | options + criteria | findings, IMPL/NAND, mitigations, sub-mitigations | EP-ranked | `create_entity` + `link_entities` + `adjust_relationship` + `refresh_confidence` + `recall_confidence` |
| **Quick** — clear answer, low stakes | ✓ | ✓ | evidence only, no edges | the caller's | **`log_decision`** |
| **Pairwise / A-B** | ✓ | two | one criterion | the caller's | a case of Quick |
| **Multi-party** — several deciders | ✓ | ✓ | per-decider weights | combined | not covered — named, not solved |
| **Human-gated** — needs a sign-off | ✓ | ✓ | ✓ | an approval | `log_decision` + an approval record |
| **Retrospective** — logged after the fact | ✓ | ✓ | whatever exists | already made | the timeline shape |
| **Reversible / time-boxed** | ✓ | ✓ | ✓ | revisit date | not covered |

**The full epistemic path needs nothing new** — it is the five existing calls in the row
above. What is missing is the **cheap end**: one call that records a decision without the
ceremony. The cheap end is not missing from the code — `file_decision` already does it in one
call. **Its *shape* is what is wrong**, and that is the issue below.

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

**This needs a ruling before implementation**, because it changes what a decision *is* in the
graph, and the skill and the code currently disagree.

## Named but not solved

Recorded so they are not silently dropped. None is required for beta:

- **Multi-party decisions** — several deciders with weights.
- **Reversible / time-boxed decisions** — a revisit date, and what happens when it arrives.
- **Narrowing `graph_overview`.** The evidence says a tool whose discriminator selects
  *distinct questions* should split, and 4 of its 12 sections (`structure_check`, `health`,
  `status`, `stale`) have different consumers, return shapes and parameters from the 7
  orientation sections. **Held deliberately:** the split point is inference from a proxy, not
  measurement — no clean A/B of "N narrow tools" vs "1 tool with an N-way enum" exists — and a
  sectioned tool is forward-compatible, so splitting later loses nothing. The tiebreaker is a
  local eval with an agent in the loop; the battery cannot do it (it drives the SDK by method
  name, so no tool selection happens).

## Discarded — and why

### Removed

| Name (or group) | # | Rationale |
|---|---|---|
| `create_or_update_point`, `index_sessions` | 2 | Body is a single delegation to another **public** method. No added contract. |
| `_get_entity`, `_delete_entity` | 2 | Private impls that `get_entity`/`delete_entity` merely wrap. Merge the body up; the public name is right. |
| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. Run once, then dead code carrying a public promise. |
| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`. The ontology models all six as entities. |
| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |
| `update_point`, `update_entity` | 2 | → `update_knowledge`. |
| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |
| `retract_point`, `invalidate_point` | 2 | → `withdraw_knowledge`. Type-suffixed names imply a `retract_object`/`retract_event` family that should not exist. |
| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence wrangling — reachable through the canonical two. |
| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. Reification is our implementation detail. |
| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship`. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. |
| `file_human_approval` | 1 | → `log_decision` plus an approval record. |
| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |
| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` where they are orientation; the diagnostics are the open question under "Named but not solved". |
| `recall_gaps`, `recall_subgraph`, `recall_state`, `retrieval_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `recall_confidence`. **`recall_subgraph` is dropped, not folded** — `explore_connections` already answers that question. |
| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `recall_confidence` where they are confidence context; `poll_events` where they are a timeline. |
| `restore_point_at` | 1 | **A write, not a read** — it was wrongly grouped with the recall reads. Needs its own disposition; not folded. |
| `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. Paging is a parameter, not a promise. |
| `batch_create_points` | 1 | → `write_knowledge_batch`, not `create_entity` — batching is the whole point of that method. |
| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |
| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |
| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |
| `list_batch`, `list_batches` | 2 | → `inspect_batch`. |
| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources`. |
| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust`. |
| `complete_source` | 1 | **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes via its URL upsert — and it has **zero callers in the repo**. |
| `ulid` | 1 | A ULID generator. Not a memory operation. |
| `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` | 3 | Console diagnostics. `key_ids`/`active_key_count` fold into `list_keys`. |
| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. A builder embeds `add_member`, not email plumbing. |
| `signup_token_*` (3) | 3 | Operator-side agent self-signup. Real capability, but it is *our* provisioning. |
| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's organisation account. |
| `mine_corpus` | 1 | **Not folded.** Mining a session corpus is a different operation from indexing, and the only non-redundant part of `checkpoint`'s dedup is the other half of this question. Disposition open. |
| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface. |
| internal implementation (battery-driven) | ~80 | **Not deleted — becomes private.** Still works, still tested, no longer a promise. |

### Renamed, relocated, or re-homed — not discarded

| Name | Disposition |
|---|---|
| `events_poll` | Realigned to the MCP name → row 11 `poll_events`. |
| `test_guard` | **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK into test support. |
| `checkpoint`, `diary_write` | → row 19 `write_journal` ⚠️. Dedup-on-write is the non-redundant part and belongs in `write_knowledge_batch`. |
| `diary_read` | → row 13 `read_journal` ⚠️. It is **a read** and sat inside a write tool — a read/write guarantee violation. |
| `file_decision` | → row 22 `log_decision` ⚠️, with its shape under review. |
| `graph_delete`, `graph_restore`, `graph_list`, `graph_count`, `graph_set_name` | → rows 32–36 `*_memory_graph*`. Renamed to the canonical term. |
| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` | **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. This is noise elimination — the agreed list does not change. |
| `list_sources` | **Not discarded. Not folded.** Verified present at `sdk.py:6602` with an MCP tool, a CLI command and its own test file. It becomes row 9, and it gains the credibility tier. |

## Open items

### Needs the owner's ruling

1. **`log_decision`'s shape.** The skill says decisions are Events; `file_decision` writes
   Points. Which is right determines whether the cheap decision path is a rename or a rewrite.
   **Blocking for row 22.**
2. **The journal capability.** `checkpoint` and the diary pair arrived in the **initial
   codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters
   appear **nowhere in `docs/ONTOLOGY.md`** (the four `wing` matches in that file are the words
   *showing*, *following*, *narrowing*). They are nonetheless **live in the MCP server**,
   registered and quota-gated. Either the capability has a use case and needs an ontology home,
   or it comes out. **An issue will be drafted for review — not filed blind**, in case a use
   case exists that has not been found.
3. **`restore_point_at` and `mine_corpus`** — both currently have no home. Each needs either a
   method or an explicit cut.

### Approved and folded in

- `write_knowledge` → **`write_knowledge_batch`** (the batching was invisible).
- `index_files` → **`index_sources`** (verified: `file_indexer.py` derives a source url per
  file and writes `sourceKind` on the **Source** node).
- `capture_session_local` / `_hosted` → **`ingest_session`**, one method. The backend is the
  target memory graph's configuration; **one organisation account can hold both a local and a
  hosted graph**. Verified: `mcp_server.py:2986` shares `_capture_session_impl` with the
  hosted API *"so the two surfaces can never drift"* — the handler already routes internally.
- `stabilize_beliefs` → **`refresh_confidence`**, and `recall_beliefs` →
  **`recall_confidence`** — a belief is a Point; what a caller wants to know is confidence.
  Dropping the subgraph mode made the latter name honest.
- Isolation is now **`check_key`** in the key block, not a new capability — the read side of
  `create_key`/`revoke_key`.
- Edge removal folds into **`delete_knowledge`**, not `adjust_relationship`: removing a link
  and removing a node are the same operation over the id space, while adjusting strength is a
  different operation entirely.
- `register_source`'s `tier=` is **demoted to an override**, trust defaulting from the source
  kind. `SOURCE_KIND_DEFAULTS` already maps kind → tier, but the legacy kinds (`document`,
  `github_issue`, `github_pr`, `slack_message`, `linear_card`, `linear_cycle`) are
  deliberately `None` and need filling in. **This changes behaviour and needs explicit
  approval at implementation** — not a silent fill-in.
- **Fleet provisioning was dropped as a requirement.** `max_graphs_per_team` is `None`
  (unlimited) on builder and team plans, so there is no ceiling for a long onboarding loop to
  hit. Provisioning is a one-time per-customer setup, not a hot path.
- `complete_source` cut; `test_guard` relocated.

### MCP impact

**23 → 25.** Two forced changes, both correctness:
1. **Lift `read_journal` out of `capture_knowledge`** — **a read inside a write tool**, a
   read/write guarantee violation.
2. **`list_sources` stays its own tool**, not a `graph_overview` section. Verified: 7 of 7
   products whose ingested thing is a first-class object ship a dedicated listing; the generic
   route appears only where the source is not a real entity (Mem0 has no documents resource at
   all). **No product in the field returns a per-source trust tier in a listing** — so
   returning the tier is a differentiator, not parity.

The session split stays SDK-only. The tenancy block is **one** MCP tool, `manage_deployment`,
since an agent never provisions.

## Not in beta

The **eval harness**. No vendor in the category exposes its proprietary scenario suite; the
public harnesses that exist (Zep, Mem0, Supermemory, Basic Memory) reproduce *academic*
benchmarks and are a different artifact class. Worth revisiting post-beta — the category has
**no independently reproduced results at all**, and vendor numbers diverge by 8–45 points, so
a runnable, honest artifact is a genuine differentiator. It is cheap: every harness in the
field makes the customer supply their own model key.
