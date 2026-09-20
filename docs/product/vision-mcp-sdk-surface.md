---
title: Vision — the MCP and SDK surface
status: approved-scope
products: [tortoise]
aboutObjects: [tortoise-mcp, tortoise-sdk]
---

# Vision — the MCP and SDK surface

## The vision, in one paragraph

**A developer should be able to read our entire surface in a sitting and know what to call.**
Today they cannot: the MCP server exposes **98 tools** and the SDK **150 methods**, with five
overlapping ways to search, four ways to revise, seven ways to create a node, and a
lifecycle nobody owns. The target is a surface small enough to hold in your head, where every
name says what it does, every operation says what it will do to your data, and

> **anything that changes what happens to the caller's data is legible at the call site.**

That is the single principle the whole list is derived from — not a size target. Size is the
measurement, not the goal.

**Two audiences, two halves:**

| | Who | What it is |
|---|---|---|
| **The memory API** | An agent, or code acting for one | Read and write **one memory graph**. This is the MCP surface, mirrored in the SDK. |
| **The tenancy API** | A **builder** — code running an app where every end-customer has private memory | Provision a memory graph per end-customer, scope a key to it, destroy it on churn. **Not on the MCP.** |

**Canonical terms:** an **organisation account** (the billing and plan boundary) owns many
**memory graphs** (the unit of memory). An end-customer is a memory graph inside the builder's
account — the builder pays; an end-customer never has an organisation account of their own.

## The two canonical documents

| Document | What it is |
|---|---|
| **`docs/product/canonical-mcp-tools.md`** | **The approved MCP list — 23 tools**, 9 READ / 14 WRITE. Merged as `8375c7921`. This is the **naming authority** for the SDK. |
| **`docs/product/beta-sdk-surface.md`** | **The recommended SDK surface — 41 methods** over 150, from the builder's and the agent's actual work. Includes the discarded-name ledger and the rationale for each. |
| `docs/product/canonical-sdk-methods.md` | The full inventory of all 150 with descriptions — the *from* state. |

**These documents are the vision.** This file is the bridge from them to implementation.

## Scope of the freeze

The freeze is on **tools and endpoints** — nothing is added, removed, renamed or deprecated on
either surface without explicit human approval. A **response field** that is off by default and
leaves the response unchanged is **not** a gate failure, but must be recorded.

## The reconciliation — MCP 98 → 23

Every group of current tools reaches one of the destinations below. **This is a map of where
the groups go, not an enumeration of all 98** — a name can legitimately sit in two buckets when
it is reachable from two call sites, and this list was reconstructed by hand. **The authoritative
per-name bridge table is Phase 0.1.**

| Destination | Current tools that reach it |
|---|---|
| **→ `create_entity`** | `create_point` `create_event` `create_subject` `create_object` `create_document` `create_operator` `create_edge` `create_source` |
| **→ `search_knowledge`** | `search` `query` `paginated_query` `query_points_by_tag` |
| **→ `list_knowledge`** | `get` `list_pointkinds` `list_tags` `list_namespaces` `list_sources` |
| **→ `get_entity`** | `get_point` `get_events` `get_operator` `get_source_reliability` `entity_profile` `get_governance` `get_session` |
| **→ `check_confidence`** | `get_confidence` `compute_confidence` `recall` `calibrate_summary` `belief_timeline` `provenance` |
| **→ `explore_connections`** | `traverse` `expand_relationships` `session_context` |
| **→ `graph_overview`** | `overview` `status` `stale` `check_structure` `audit` `taxonomy` `summarize_structure` `validate_domain` `dream_health` |
| **→ `poll_events`** | `events_poll` |
| **→ `refresh_confidence`** | `dream` `promote_point` |
| **→ `review_link_candidates`** | `review_connections` `find_cross_lens_candidates` `list_dedup_candidates` |
| **→ `manage_source_trust`** | `set_source_tier` `assess_source` |
| **→ `index_sources_from_directory`** | `index_files` `index_sessions` `ingest_corpus` `mine_conversations` |
| **→ `adjust_relationship`** | `mitigate_operator` `operator_action` `annotate_operator` |
| **→ `update_knowledge`** | `update` `update_point` `update_entity` |
| **→ `supersede_knowledge`** | `supersede` `invalidate` |
| **→ `delete_knowledge`** | `delete` `delete_point` `delete_entity` |
| **→ `record_decision`** | `file_decision` `file_human_approval` |
| **→ `run_onboarding`** | `onboarding_state` `onboarding_seed` `onboarding_demo_create` `onboarding_github_connect` `onboarding_github_index` `onboarding_github_status` `onboarding_session_recording` `pack_install` |
| **→ tenancy (not on the MCP)** | `list_graphs` `org_create` |
| **→ removed, no destination** | `ask` (retired #3929) · `checkpoint` `diary_read` `diary_write` (journal, post-beta) · `backfill_v25` `ingest` `session_capture` `packs_list` `graph_set_recording` `set_point_baseline` `retract_point` |

**The authoritative per-name bridge table — every current tool, its single destination, and the
SDK method behind each discriminator — is Phase 0.1.** It is a deliverable precisely because this
document must not claim a reconciliation it has not computed. The hand-built map above shows the
shape; it is not the proof.

## The reconciliation — SDK 150 → 41

The ledger of the departing names — grouped, with the rationale for each group — is the
**"Discarded — and why"** section of `docs/product/beta-sdk-surface.md`. **The five current
names the target reuses verbatim are `create_entity`, `get_entity`, `approve_merge`, `close`
and `list_sources`** (the last as `list_knowledge(kind='source')`). The ledger is written per
group, not per name, so it names the departures rather than summing them to a number —
**a group-level ledger is the honest form, and the per-name completeness check is deferred to
Phase 0.1.**

**41 rows = 41 entries**, of which the table's rows 1–2 are the `Tortoise(...)` constructor and
`close()`.

**The SDK count is 41 and the MCP count is 25.** The approved canonical MCP list is **23**; the
beta surface adds four, drops three and splits one:

| | |
|---|---|
| **+4** | `get_historical_knowledge` · `mine_knowledge_from_directory` · `write_question` · `verify_connection` |
| **−3** | `inspect_batch` → `list_knowledge(kind='batch')` · `manage_deployment` (tenancy is not on the MCP) · `run_onboarding` → `verify_connection` |
| **±1** | `revise_knowledge` splits into `update_knowledge` + `supersede_knowledge` |
| **renames** | `recall_beliefs`→`check_confidence` · `stabilize_beliefs`→`refresh_confidence` · `capture_knowledge`→`mine_knowledge_from_session` · `index_files`→`index_sources_from_directory` · `manage_source_trust` unchanged |

23 + 4 − 3 + 1 = **25**. ✓

## Implementation plan

Phases are ordered so nothing is written twice. **The SDK is frozen while the MCP list is
built**, because the MCP handlers call the SDK — building them in the other order writes each
handler twice.

### Phase 0 — the pre-flight (blocks everything)

| | Deliverable | Why |
|---|---|---|
| **0.1** | **The bridge table.** Every MCP `type=` / `mode=` / `section=` discriminator → the exact SDK method and argument it resolves to. | The merged tools dispatch internally. Until that mapping is written down, nobody knows whether the 23 names can be implemented on the frozen SDK — or whether a `type=` value has no SDK method to call. **This is the check that prevents a rewrite.** |
| **0.2** | **Name the target.** A short note in both canonical docs stating that **every name is a target, not a description of today.** | The review found the MCP column reads as present tense. A developer following it today calls tools that do not exist. |
| **0.3** | **The MCP rename table.** old name → new name, per tool. | 24 of the 25 names are not live today. Without this, implementation silently renames the whole MCP surface with no migration note. |
| **0.4** | **`__all__` in `tortoise/__init__.py`.** | The root cause of 150. The public surface is declared by *convention*; every design test presupposes a declaration. Without this the surface drifts back. |

### Phase 1 — the declaration and the gate

| | Deliverable | Depends on |
|---|---|---|
| **1.1** | `tortoise/__all__` listing the 41 approved methods | 0.4 |
| **1.2** | The approved-surface manifest, cut **once** from that declaration, frozen | 1.1 |
| **1.3** | The gate: unfiltered `pull_request` check, **fail-closed** on any add/remove/rename | 1.2 |
| **1.4** | **#3883** — retired names must **WARN** when called, naming the replacement | — |

**1.4 gates every removal.** Nothing is removed until a caller of a retired name gets a warning
that names its replacement.

### Phase 2 — the SDK

| | Deliverable | Depends on |
|---|---|---|
| **2.1** | The 41 methods under their canonical names | 1.1, 1.4 |
| **2.2** | The 4 merges (`create_entity`, `link_entities`, `delete_knowledge`, `update_knowledge`) dispatching internally | 2.1 |
| **2.3** | `update_memory_graph` — **the rename path that is currently missing** | 2.1 |
| **2.4** | The Contracts section enforced: pagination cursors, truncation notice, typed errors | 2.1 |
| **2.5** | 145 retired names → warning aliases | 1.4 |
| **2.6** | `check_connection` — see *Unsure* below | — |

### Phase 3 — the MCP server

| | Deliverable | Depends on |
|---|---|---|
| **3.1** | The 25 tools implemented **on the frozen SDK** | Phase 2 |
| **3.2** | The 73 retired tools removed; `tortoise_ask` stays retired | 3.1 |
| **3.3** | The `co_firstlineno` guard defect fixed — see *Coordination* | — |

### Phase 4 — verification

| | Deliverable |
|---|---|
| **4.1** | Every MCP tool exercised end-to-end against a live graph |
| **4.2** | The isolation proof: `create_key` → `check_connection` → a graph it cannot reach |
| **4.3** | The retired-name warning proven by execution, not grep |

## Unsure — awaiting the owner

Two items are **not settled** and are deliberately not implemented. They are small and local;
everything else in the plan proceeds without them.

### U1 — the revise triangle (#6)

`update_knowledge` (which now carries retraction), `supersede_knowledge` and
`delete_knowledge` each end a claim's life, and no row says when to pick which. Mis-choosing is
unrecoverable — `delete_knowledge` has no undo.

**Proposed resolution — the rows carry a decision rule:**

| | When | What survives |
|---|---|---|
| **Update + retract** | The claim was true and no longer is, and **nothing replaced it** | the claim, bounded — "true until March, then not" |
| **Supersede** | The claim was replaced **by a specific successor** | both, chained — the old one points forward |
| **Delete** | The claim **must not be retained** — never true, or an erasure request | nothing |

The distinction is decidable because retract and supersede differ by exactly one thing: **whether
there is a successor.** And the destructive one is made unmistakable by requiring an explicit
`purge=True` for hard removal, matching `delete_memory_graph`.

### U2 — `check_key` vs `verify_connection` (#7)

Both answer "what does this credential reach," with no stated difference. **Proposed
resolution: collapse to one method, `check_connection(key_id=None)`** — omit the id to check your
own connection, pass one to inspect a specific credential. `key_id` selects *the thing being
asked about*, not the operation, so this is one question with one answer. Placed in SETUP, on
both surfaces. `check_key` and `verify_connection` both disappear.

## Coordination — four lanes are blocked on this

| Lane | Blocked on | Unblocks with |
|---|---|---|
| **B2c** (`01a07161`) | *"I'm out of executable work."* The whole lane waits on the approved list. #3898 is **red by design**, its `xfail` bound to this work. | The approved list (now available) + Phase 1 |
| **B3** (`01a093d5`) | *"The list can only be re-approved all at once"* — it cannot re-cut the surface manifest per-PR without rubber-stamping. | Phase 1.2 — the manifest cut once |
| **B4** (`01a0a7f1`) | Told by the owner to build *under* the redesign, not alongside it. PR #4020 superseded twice. | Phase 0.1, so the route work binds to the new surface |
| **B5** (`01a0b558`) | Found a **defect in `tools/surface-guard.py`** — `_fingerprint` uses `co_firstlineno`, which is *positional, not identity*: 3 comment lines near line 713 shift every baseline row while the digest is byte-identical. | Phase 3.3 |

**B5's finding is a defect in a merged artifact from this lane and is treated as blocking.**

## Not in beta

- **The eval harness.** No vendor exposes its proprietary scenario suite, and the category has
  **no independently reproduced results** — vendor numbers diverge by 8–45 points. Revisit
  post-beta; it is cheap (every harness in the field makes the customer supply their model key).
- **The journal capability** (`checkpoint`, `diary_write`, `diary_read`). Live on the MCP today,
  from the initial commit, with a `wing`/`room` vocabulary that appears nowhere in
  `docs/ONTOLOGY.md`. Filed separately; unlisted until then.
