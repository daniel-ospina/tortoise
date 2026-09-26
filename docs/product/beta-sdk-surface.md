---
title: Beta SDK Surface — the recommended set
type: synthesis
domain: capability
doc_status: live
created: 2026-09-18
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: [tortoise-sdk, mcp-server]
approval_status: approved
approved: 2026-09-21
approvedBy: daniel-ospina
---

# Beta SDK surface

**The recommended set a beta developer sees.** Designed from who the SDK is for and what
they do — not pruned from the existing 150.

> **Scope of this approval.** The owner settled the **target surface** on 2026-09-21, including the
> ruling that `graph_set_recording` is kept — which is what sets the MCP count at **26**. The
> **four** placement questions that were left open in `canonical-mcp-tools.md` (`packs_list`,
> `pack_install`, `run_onboarding`, `manage_deployment`) were **settled on 2026-09-22** (recorded on
> **#4282**) — see
> §"The four open placements — settled". None of the four changes the count: they are all **off the
> MCP target**, which is why the MCP stays at **26**. See `vision-mcp-sdk-surface.md`
> §"Cross-artifact tensions".

**Canonical terms:** an **organisation account** (the customer's account — the billing and
plan boundary) owns many **memory graphs** (the unit of memory).

## Why the set looks like this

The SDK serves **two audiences**:

- **The memory API** — an agent, or code acting for one, reading and writing *one memory
  graph*. This is what the MCP server exposes, and the SDK mirrors it name-for-name.
- **The tenancy API** — a **builder** running an app where every end-customer has an agent
  with private memory. They provision a memory graph per end-customer, issue a credential
  scoped to it, and destroy it on churn.

**The builder pays for their end-customers.** An end-customer never has an organisation
account — they are a memory graph inside the builder's account. Verified against
`tortoise/pricing.py`: `tier_limits()` exposes `max_graphs_per_team`, whose value is
**`None` = unlimited** on the **pro** and **team** plans (the tiers are free, solo,
pro, team, anon — there is no "builder" plan; a builder buys pro or team).

**The audience labels used below:** *agent* (a model using the MCP surface — it never constructs a
client and never touches tenancy), *builder* (code integrating the SDK for an app), *admin* (a
person managing their own account).

## The recommended surface

| # | Name | Does | MCP twin | Who needs it |
|---|---|---|---|---|
| | **SETUP** | | | |
| 1 | `Tortoise(...)` | Open a connection — the credential determines the account and the memory graph | — | builder |
| 2 | `close()` | Release connections and background work | — | builder |
| | **MEMORY — READ** | | | |
| 3 | `search_knowledge` | Find things by text — full-text and hybrid. **Paged: returns a cursor** | `search_knowledge` | agent, builder |
| 4 | `list_knowledge` | **One list method; `kind=` selects the kind** — claims, sources, questions, review batches. Browse and filter without text search. **Paged: cursor + limit** | `list_knowledge` | agent, builder |
| 5 | `check_confidence` | How confident the graph is in each item, with the operators and mitigations behind it. **Returns the confidence view only** | `check_confidence` | agent |
| 6 | `get_entity` | Fetch one node by id, any kind | `get_entity` | agent, builder |
| 7 | `get_historical_knowledge` | **What a claim said on a given date.** Walks the revision chain to the version valid then | `get_historical_knowledge` | agent |
| 8 | `explore_connections` | Walk outward from a node through its links. **Paged** | `explore_connections` | agent |
| 9 | `graph_overview` | What is in this memory graph and how it is shaped. **Returns a named-section envelope**, not a flat dict | `graph_overview` | agent, builder |
| 10 | `review_link_candidates` | Connections found but not made. Read-only. **Accept one by calling `link_entities` with its endpoints** | `review_link_candidates` | agent |
| 11 | `poll_events` | Read the event log since a point in time. **Paged** | `poll_events` | agent, builder |
| | **MEMORY — WRITE** | | | |
| 12 | `create_entity` | Add one node — a claim, or a referent (subject, object, event, document) | `create_entity` | agent, builder |
| 13 | `write_knowledge_batch` | Write many interconnected things in one call — **claims, entities, sources and the links between them** — atomically | — | builder |
| 14 | `register_source` | Declare a source exists. The URL is its identity. **Trust defaults from the source kind**; `tier=` overrides | `register_source` | agent, builder |
| 15 | `index_sources_from_directory` | Read files off disk and register each as a source with its content in memory. **Nothing is extracted.** Batched, resumable | `index_sources_from_directory` | builder |
| 16 | `mine_knowledge_from_session` | Extract claims, operators and entities from **one conversation**. The backend is the target graph's configuration | `mine_knowledge_from_session` | agent, builder |
| 17 | `mine_knowledge_from_directory` | The same for a **folder of files**, each mined by its own content type | `mine_knowledge_from_directory` | builder |
| 18 | `manage_source_trust` | Set how reliable a source is, and return the new score. **Reads come from `list_knowledge(kind='source')`** | `manage_source_trust` | agent |
| 19 | `link_entities` | Connect two nodes. An epistemic relation builds an operator, anything else a plain edge — the caller does not choose | `link_entities` | agent |
| 20 | `write_question` | **File a question with its options and the evidence behind them.** Not a decision — a decision happens later, or not at all | `write_question` | agent, builder |
| 21 | `record_decision` | **Record that a question was resolved**, by this choice, at this time. Accepts a full question inline for a one-call shortcut; **re-recording the same question is rejected, and the inline form on an already-filed question resolves it rather than filing a second** | `record_decision` | agent, builder |
| | **MEMORY — REVISE** | | | |
| 22 | `update_knowledge` | Change what a stored thing says — **a node or a link** — **or retract it.** Carries `valid_until` / `invalid_at`: retraction is a temporal field on update, not a separate verb | `update_knowledge` | agent |
| 23 | `supersede_knowledge` | Replace a node with a successor, keeping the history. **Carries an explicit link policy** — whether the old node's links follow the successor | `supersede_knowledge` | agent |
| 24 | `delete_knowledge` | Remove knowledge — **a node or a link**. Hard removal; there is no undo. **Choose by the rule: retract when nothing replaced the claim, supersede when a specific successor did, delete when it must not be retained** | `delete_knowledge` | agent, builder |
| 25 | `adjust_relationship` | Change an existing epistemic link's strength. **Annotating a link is `update_knowledge` on it** | `adjust_relationship` | agent |
| 26 | `refresh_confidence` | Recompute confidence after changes. **Two scopes: the changed nodes, or the whole graph** — stated as a parameter, not chosen silently | `refresh_confidence` | agent |
| 27 | `approve_merge` | Approve merging two things the system thinks are the same. **`reject_merge` is not covered — see below** | `approve_merge` | agent |
| | **TENANCY — SDK and REST only. NOT on the MCP** | | | |
| 28 | `get_organisation_account` | Read the account and the plan it is on | — | admin |
| 29 | `create_memory_graph` | **Provision a memory graph — one per end-customer, or per agent.** Takes `name` and `backend` at creation | — | builder |
| 30 | `update_memory_graph` | **The one partial update a memory graph has** — rename it, or set/clear its session-recording override. A graph is created with its fields; this is the single place they change | `graph_set_recording` (recording only) | builder |
| 31 | `list_memory_graphs` | List the account's memory graphs | — | admin, builder |
| 32 | `delete_memory_graph` | Destroy a memory graph. **`purge=True` for irreversible erasure; the default is a recoverable delete** | — | builder |
| 33 | `restore_memory_graph` | Undo a recoverable delete. **Refuses on a purged graph** | — | builder |
| 34 | `create_key` | Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id | — | builder |
| 35 | `list_keys` | List a memory graph's credentials | — | builder |
| 36 | `revoke_key` | Revoke a credential. **Rotation is mint-then-revoke — the new key is live before the old dies** | — | builder |
| 37 | `add_member` | Grant a person access to the account | — | admin |
| 38 | `list_members` | List who has access | — | admin |
| 39 | `remove_member` | Revoke a person's access | — | admin |
| | **PLATFORM** | | | |
| 40 | `check_connection` | **What does this credential reach?** Omit `key_id` to check your own connection; pass one to inspect a specific credential. Makes the isolation promise verifiable. **Programmatic, returns a result — not a wizard** | `check_connection` | builder |

**40 methods** against **150** today. **MCP: 26 tools** — every row except the tenancy block (28–39), `write_knowledge_batch`
(builder-only, 13) and the constructor/`close` (1–2) — **plus row 30**, `update_memory_graph`,
which is inside the tenancy block but is the one tenancy method with an MCP twin, because the
owner's 2026-09-21 ruling kept the `graph_set_recording` tool. 40 − 12 − 1 − 2 + 1 = 26.

> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the
> current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix.
> The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it.
Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every
*current* tool to its destination but does not name the target's replacing name.


## Tenancy is not on the MCP


> **On the competitor evidence below:** these figures come from a competitive research pass and
> are **not independently verified in this repo**. Treat them as *reported*, not *confirmed* —
> the same caveat `docs/product/canonical-sdk-methods.md` carries in its Verification notes.

The tenancy block (rows 28–39) is **SDK and REST only.** This follows the competitors: Zep and
Mem0 both put their admin surface on a **separate URL prefix** (`graph/*` vs `user/*`;
`/api/v1/orgs/…` vs `/v1/memories/`) — it is a different API area, not a set of agent tools.
MCP is the model-controlled surface by definition. A solo user has one memory graph and needs
none of it; a team member occasionally reads members; **the builder is a code integration, not
an agent.**

This also settles the split question without needing to answer it — there is no merged
`manage_deployment` to split.

**And it makes the RBAC / ABAC cut cleanly:**

| | Who | Where |
|---|---|---|
| **Member management** — people, roles, access | A human admin | SDK / REST / console |
| **Key scoping** — what an agent's credential reaches | The builder's code | SDK / REST |
| **Anything tenancy-related** | An agent | **Not on the MCP** |

## The four open placements — settled

`canonical-mcp-tools.md` carried four items marked **OPEN**. All four were resolved on
**2026-09-22**, and none of them moves the MCP count — every one is **off the MCP target**:

| Item | Placement | Why |
|---|---|---|
| `manage_deployment` | **Off the agent-facing MCP surface** — the tenancy block (SDK / REST / console) | Putting it on the MCP would **contradict a recorded owner ruling**: this doc states *"Tenancy is not on the MCP"*. A container that mixed organisation creation with tenant pack writes is exactly the account-tenancy shape the ruling excludes. |
| `run_onboarding` | **Dropped**, in favour of `check_connection` | It **violates the recorded read/write principle** (`canonical-mcp-tools.md`: *reads and writes never share a tool*): as designed it absorbed two read-only tenant tools **plus** writes. |
| `packs_list`, `pack_install` | **SDK / REST**, post-beta | No memory/KG product in the comparison set exposes pack *installation* on its agent-facing API. See §"Packs" below. |

The fifth item that was open — `graph_set_recording` — was already settled on **2026-09-21** and is
**kept**, which is the *one deliberate exception* to the rule below (an agent's only in-MCP
recovery from the capture 409). It is on the MCP, unlike the four above.

### The general rule

> **Containers may be on the MCP; account tenancy is not.**

A merged *container* over memory operations (`create_entity(type=)`, `list_knowledge(kind=)`) is a
legitimate agent tool. An **account-tenancy** operation — provisioning, people, plans, keys,
organisation lifecycle — is not: it is the builder's or an admin's code, so it lives in the SDK,
REST or console. `manage_deployment` was the shape that blurred the two, which is why it is off the MCP.

### The two reads `run_onboarding` absorbed — rehomed, not lost

`run_onboarding` also absorbed two **read-only, tenant-visible** tools. Dropping it would have
destroyed two live reads, so both are **rehomed to the tenancy block (SDK / REST)** — they are
**tenant/account-level** reads, not memory-graph reads, and the general rule above puts account
tenancy off the MCP:

| Absorbed read | What it answers | New home |
|---|---|---|
| `onboarding_state` | This team's onboarding progress | Tenancy block — SDK/REST; already served at `GET /v1/onboarding/state` |
| `onboarding_github_status` | This team's GitHub connection status | Tenancy block — SDK/REST; already served at `GET /v1/onboarding/github/status` |

**What is lost, stated exactly:** the **MCP** surface loses both reads. They are *not* rehomed onto
an MCP tool — `check_connection` is *"programmatic, returns a result — not a wizard"*, and
onboarding-progress state is wizard state, so folding it there would contradict this doc's own
description of the row. Both reads remain reachable at the REST endpoints above (their `rest_spec`
is unchanged in `tortoise/tool_registry.py`), so no capability is deleted; what changes is *which
surface* serves it.

## Provisioning: primitives, and the credential carries the tenant

Two decisions, applied together.

**Primitives, not a composite.** `create_memory_graph` and `create_key` stay separate, each
with its own retry semantics. No competitor exposes a task-shaped provisioning API — zero of
eight — so a composite would be a deliberate bet, not a convergence.

**The credential carries the tenant.** This is Mem0's shape: the organisation and project are
resolved from the API key, and `MemoryClient` accepts no org or project parameter. So
`create_key` mints a credential scoped to one memory graph, and a client constructed with it
is already addressed to that graph. The builder's code never passes a graph id around.

**Three methods were deleted to make this work:**

| Deleted | Why |
|---|---|
| `set_memory_graph_name` | Single-column setter. AIP-133: the create method sets the resource's fields. `name` moved onto `create_memory_graph`. Supported by **7 of 8** competitors, who use create-with-fields plus one partial update and no per-field setters. |
| `set_memory_graph_backend` | Worse than a setter — it hides a data migration behind a property write. Graphiti makes the backend **immutable at construction** (the driver is chosen in the constructor). Backend is now fixed at creation. |
| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |

## Questions and decisions

**A question and a decision are different things.** You do not file a decision — you file a
**question**, and a decision is the **event of resolving it** by picking an option. This is
what the `tortoise-decide` skill means by *"the decision dimension is the decision-as-Event
timeline"*: the decision is a moment, not an object.

That explains the defect in the current code. `file_decision` writes:

```python
decision = self.create_point('decision', f'Decision: {options[choice]}', status='live')
opt_point = self.create_point('option', ...)
ev_point  = self.create_point('evidence', ...)
```

It stores the **question** and calls it a decision. The fix is to split, not rename.

| Method | Creates | Answers |
|---|---|---|
| `write_question(question, options, evidence)` | **Points** — the question, its options, its evidence | "what are we deciding, and what is the case for each option?" |
| `record_decision(question_id, choice)` | **An Event** — the moment of resolution | "what did we choose, and when?" |

`write_question` leaves the question open until something resolves it, which makes the
retrospective case natural. `record_decision` takes either an existing `question_id` **or** the
full question inline. **`list_knowledge(kind='question')` (row 4) is what makes that reachable** —
without it a filed question is invisible unless the caller kept the id out of band.

### The full range of decision processes

| Process | Call sequence |
|---|---|
| **Full epistemic** — options, criteria, findings, IMPL/NAND, mitigations | `write_question` → `create_entity` → `link_entities` → `adjust_relationship` → `refresh_confidence` → `check_confidence` → `record_decision` |
| **Quick** — answer already known | `record_decision(question, options, evidence, choice)` — one call |
| **Question now, decide later** | `write_question` → `list_knowledge(kind='question')` → `record_decision(question_id, choice)` |
| **Pairwise / A-B** | a case of Quick |
| **Human-gated** | `write_question` → `record_decision` → an **approval record** ⚠️ *no approval method exists — see below* |
| **Retrospective** | `record_decision` on an already-answered question |

## Packs — post-beta, with per-graph curation

**`packs_list` and `pack_install` are not in beta, and not on the MCP.** No memory/KG product in
the comparison set (Zep, Mem0, Letta, Cognee, Supertmemory, LangMem, Basic Memory, MemOS) exposes
knowledge-pack *installation* on its **agent-facing** API. The analogues all live in a marketplace /
CLI / content layer outside the product API: Letta's Agent Skills, Zep's plugin marketplace,
Cognee's config-supplied ontology. Pack curation is a **user/builder** concern, not an agent tool.

**But packs are not dropped, and per-graph curation is not foreclosed.** The owner's ruling:

> packs curation doesn't need to be beta, but we do need to enable users to curate the packs they
> have on each graph later on. so they can add their own packs if they want

**Checked: nothing in this design forecloses per-graph pack curation.** The evidence:

- **Activation is already per-graph state, not global config.** `tortoise_pack_install`
  (*"stores the manifest in the tenant graph and activates it"*) writes the tenant graph's
  `PackInstall` records; `tortoise_packs_list` reads *"the shared pack catalog joined with the
  tenant graph's `PackInstall` activation records"* and masks other tenants. So "the packs on
  **each graph**" is already the storage shape — the curation surface is a control over existing
  per-graph state, not a new model.
- **The tenancy block has room.** It has one partial update (`update_memory_graph`) and **no**
  per-graph pack operation. Nothing about the frozen target precludes adding one post-beta; the
  surface is frozen for beta, not permanently closed.
- **The SDK/REST surface is the natural home.** The pack catalog is *content*, not agent memory, so
  a per-graph pack operation belongs with the tenancy/pack operations (SDK + REST), not the 26 MCP
  tools.

**Where a post-beta per-graph pack operation would slot in:** an SDK/REST operation addressed to
**one memory graph** — list / install / remove on that graph, plus authoring a user-supplied pack
(validation of a user-authored manifest is the open design question). Whether it ever earns an MCP
tool is tested against the recorded rule: **containers may be on MCP; account tenancy is not.**

**Tracked in #4663** — "Post-beta: per-graph pack curation — users curate the packs on each graph
and add their own".

## Named but not solved

Recorded so they are not silently dropped. None is required for beta:

- **`reject_merge` / `unmerge`.** `approve_merge` exists with no counterpart. Either the review
  queue is read-only for its own sake, or the reject path is missing.
- **An approval record.** The human-gated row above depends on one and no method provides it.
  Either a row is added or that process is not covered.
- **Multi-party decisions** — several deciders with weights.
- **Reversible / time-boxed decisions** — a revisit date.
- **Narrowing `graph_overview`.** The evidence says a tool whose discriminator selects *distinct
  questions* should split, and 4 of its 12 sections (`structure_check`, `health`, `status`,
  `stale`) have different consumers, return shapes and parameters from the other 8. **Held
  deliberately:** the split point is inference from a proxy, not measurement — no clean A/B of
  "N narrow tools" vs "1 tool with an N-way enum" exists — and a sectioned tool is
  forward-compatible. The tiebreaker is a local eval with an agent in the loop.
- **`check_confidence` may absorb too much.** `recall_gaps`, `calibration_passed` and
  `retrieval_legs` are different questions from "how confident is this" — different consumers
  and return shapes. The row now states it returns the confidence view only; whether gaps
  deserve their own method is the same held question as `graph_overview`, and it gets the same
  treatment rather than being folded silently.
- **Per-source deletion.** You can register a source, index sources and score their trust, but
  nothing removes one. For a builder whose upstream revokes a feed, or an erasure request
  scoped to one source, there is no operation. `delete_knowledge` removes nodes and links, not
  sources.
- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the
  **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room`
  parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server**
  today. **Filed post-beta** (issue to be created) and **unlisted** until then.

## Discarded — and why

### Removed

| Name (or group) | # | Rationale |
|---|---|---|
| `create_or_update_point`, `index_sessions` | 2 | Body is a single delegation to another **public** method. No added contract. |
| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. Run once, then dead code carrying a public promise. |
| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`. The ontology models all of them as entities. |
| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |
| `update_point`, `update_entity` | 2 | → `update_knowledge`. |
| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |
| `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. |
| `withdraw_knowledge` | 1 | **Never existed** — removed from the plan. Retraction is a field on `update_knowledge`. |
| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. |
| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. |
| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. |
| `file_human_approval` | 1 | → `record_decision`. |
| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources_from_directory`. |
| `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. |
| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |
| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` where they are orientation. The diagnostics are the held question above. |
| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |
| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. |
| `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |
| `batch_create_points` | 1 | → `write_knowledge_batch`. |
| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |
| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |
| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |
| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. |
| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |
| `complete_source` | 1 | **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes — and it has **zero callers in the repo**. |
| `ulid` | 1 | A ULID generator. Not a memory operation. |
| `graph_key_ids`, `graph_active_key_count` | 2 | Console diagnostics. Both fold into `list_keys`. |
| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. |
| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` | 3 | See "Provisioning" above. |
| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. |
| `signup_token_*` (3) | 3 | Operator-side agent self-signup — our provisioning, not product surface. |
| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. |
| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface. |
| `index_sources` (bare) | 1 | Renamed → `index_sources_from_directory`, so the index/mine distinction is unmissable. |

### Renamed, relocated, or re-homed — or deleted as a duplicate alias

| Name | Disposition |
|---|---|
| `events_poll` | → row 11 `poll_events`. |
| `test_guard` | **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK. |
| `restore_point_at` | → row 7 **`get_historical_knowledge`**. A **read**, not a write — it returns the version of a claim valid on a date and mutates nothing. |
| `file_decision` | → rows 20/21 **`write_question`** + **`record_decision`**. It was filing a *question* and calling it a decision. |
| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 `*_memory_graph*`. |
| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` | **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. |
| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |
| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. |
| `file_decision`'s `options`/`evidence`/`choice` | Preserved in `record_decision`'s inline shortcut form. |

## Contracts

Every method on this surface must state these. They are currently unstated for most rows.

- **Pagination.** Every list and search returns a cursor and a bounded page. The competitor norm
  is explicit: Zep's directory takes `page_size` 1–100 and returns `total_count`; Letta's
  archival-memory list takes `after`/`before`/`limit`; Supermemory and Mem0 paginate too.
- **Truncation.** Any response that hits a cap says so and returns a cursor — never a silently
  short list.
- **Errors.** A typed error, not a string. A caller must be able to distinguish "not found",
  "denied", "invalid", and "temporarily unavailable" without parsing prose.

**Pagination is a requirement, not an observation.** Every row in this list is a target: rows 3, 4,
8 and 11 have no `def` on `TortoiseSDK` today, so there is no signature to confirm anything in.
These four *must* state pagination when implemented. **Rows 31, 35 and 38 are lists that must state it too before implementation closes.** Truncation and typed errors are stated here as requirements and are not yet in any signature — that is Phase 2.4 of the plan.

## Not in beta

The **eval harness**. No vendor exposes its proprietary scenario suite; the public harnesses
that exist reproduce *academic* benchmarks and are a different artifact class. Worth revisiting
post-beta — the category has **no independently reproduced results at all**, and vendor numbers
diverge by 8–45 points, so a runnable, honest artifact is a genuine differentiator. It is cheap:
every harness in the field makes the customer supply their own model key.
