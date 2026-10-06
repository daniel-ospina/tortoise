---
title: "How agent-memory systems organise their agent-facing operations"
type: synthesis
domain: capability
doc_status: live
created: 2026-09-17
ownedBy: epistemic-team
---

# How agent-memory systems organise their agent-facing operations — and where we sit

**Date:** 2026-09-17
**Issue:** #3863 (MCP/SDK surface curation)
**Scope:** the *agent memory* category only. Graph databases (Neo4j, FalkorDB, Memgraph, ArangoDB, Neptune, Kùzu, GraphDB, TerminusDB) and vector stores/RAG frameworks are **out of scope** — they are our storage layer, not our peers. This replaces an earlier pass that studied them.

**Method.** Tool counts were obtained by **fetching the server source on `main` and counting the registration decorators**, not by trusting a summariser. Every count below says which of these it is: `counted from source` / `read from docs` / `inferred`. Where a third-party source disagrees with the source, the disagreement is stated rather than smoothed.

---

## 1. Comparison table

| Comparable | What it is | Operations exposed to an agent (names) | Organisation pattern | Structure exposed or hidden | Profile / tier switch | Counted from |
|---|---|---|---|---|---|---|
| **Graphiti / Zep** | Temporal knowledge graph for agent memory; contradiction handling via bi-temporal edges | **13** `counted from source`: `add_memory`, `search_nodes`, `search_memory_facts`, `add_triplet`, `get_entity_edge`, `get_episode_entities`, `get_episodes`, `delete_entity_edge`, `delete_episode`, `summarize_saga`, `build_communities`, `clear_graph`, `get_status` | **Domain verbs, not CRUD.** Verb + domain noun: `add_/search_/get_/delete_` × `node / memory_fact / entity_edge / episode`, plus three *job* tools that are not CRUD at all (`build_communities`, `summarize_saga`, `get_episode_entities`). No prefix convention, no read/write marker in the names | **EXPOSED.** Entity types are a filter argument (`entity_types`) on `search_nodes`; relationships are separately searchable (`search_memory_facts`); the agent can write a raw edge (`add_triplet`), fetch one edge (`get_entity_edge`), run a graph algorithm (`build_communities`), and see which episode produced an entity (`get_episode_entities`) | **No.** No read-only or tier switch found — searched `read_only`/`readonly` in `graphiti_mcp_server.py` and `config/schema.py`, no match. All 13 are always advertised | `https://github.com/getzep/graphiti/blob/main/mcp_server/src/graphiti_mcp_server.py` |
| **Mem0** (three distinct surfaces; all three counted) | Memory layer for agents, optional graph mode | **9** OSS MCP `counted from source`: `add_memory`, `search_memories`, `get_memories`, `get_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `list_entities`, `delete_entities` (+1 prompt, `memory_assistant`, not a tool)<br>**11** hosted platform MCP `read from docs` (adds `list_events`, `get_event_status`)<br>**1** coding-agent plugin core `counted from source`: `search_memories` | **CRUD on a memory object.** `add / search / get / update / delete` + entity-management verbs (`list_entities`, `delete_entities`). The plugin variant is **read-only one-tool**: writes arrive through host lifecycle hooks | **HIDDEN.** Graph is a **boolean argument** — `enable_graph: bool` on `add_memory`, `search_memories`, `get_memories`. No entity, relation, or traversal tool exists; `list_entities` enumerates *owners* (user/agent/app/run), not graph nodes | **No switch**, but two radically different shippings: 9–11 tools (server) vs **1 tool** (plugin). `graph is disabled by default` per source comment | `https://github.com/mem0ai/mem0-mcp/blob/main/src/mem0_mcp_server/server.py`; `https://github.com/mem0ai/mem0/blob/main/integrations/agent-plugin-core/python/mcp_server.py`; `https://docs.mem0.ai/platform/mem0-mcp` |
| **Cognee** | Graph memory / ingestion pipeline for AI agents | **4** `counted from source`: `remember`, `recall`, `forget`, `cognify_status` | **Three domain verbs + one status.** The entire memory API is `remember / recall / forget`; `cognify_status` polls background ingestion. Every CRUD shape is collapsed into one verb | **HIDDEN.** No entity, relation, or traversal tool. The graph is inside `recall`'s results, not a surface the agent drives | **YES — the clearest in the set.** `COGNEE_MCP_TOOL_MODE` = `default` \| `minimal` \| `all`. `default`/`minimal` pin the three memory tools in `tools/list` and expose everything else through `search_tools` / `call_tool` (FastMCP `BM25SearchTransform`, ≤10 results); `all` = "pre-3.x behavior". Registration is tag-driven (`default`/`memory`/`status`) so the advertised set is derived, not hand-maintained | `https://github.com/topoteretes/cognee/blob/main/cognee-mcp/src/server.py` + `.../src/tool_registry.py`; corroborated `https://docs.cognee.ai/cognee-mcp/mcp-tools` |
| **Letta** (formerly MemGPT) | Agent runtime with self-editing memory blocks | **18** always loaded, `counted from source` (`LETTA_TOOLS`): `memory`, `memory_apply_patch`, `Read`, `Edit`, `Write`, `exec_command`, `write_stdin`, `ViewImage`, `UpdatePlan`, `Task`, `TaskOutput`, `TaskStop`, `Monitor`, `SendAgentMessage`, `Skill`, `AskUserQuestion`, `EnterWorktree`, `ExitWorktree`<br>**66** total defined (`toolDefinitions`), of which 48 are model-specific aliases (Claude/Codex/Gemini variants of the same job) | **One tool per job.** Memory is **one** tool named `memory` driven by a `command` argument (`create` / `str_replace` / `insert` / `delete` / `rename`) — counted from the tool docstring in the archive branch. Legacy individual memory tools also exist there: `core_memory_append`, `core_memory_replace`, `memory_replace`, `memory_insert`, `memory_apply_patch`, `memory_rethink`, `rethink_memory`, `memory_finish_edits`, `archival_memory_insert`, `archival_memory_search`, `conversation_search`, `send_message` | **PARTIALLY exposed.** Memory is *blocks* — labeled, editable, size-limited context sections, each with `read_only` / `hidden` flags — addressed by label (`human`, `persona`). The agent edits its own memory directly. There is no entity/relation/traversal tool: blocks plus archival search, not a graph | **YES — model-profile driven.** `StartupToolsetPreference = auto \| codex \| default \| gemini \| letta`; the switch picks the tool *names and schemas* that match the model family (codex → `apply_patch`/`shell`, gemini → `read_file_gemini`/`list_directory`, letta → the model-independent set above). An `exclude` list is also supported | `https://github.com/letta-ai/letta-code/blob/main/src/tools/letta-toolset.ts` + `.../src/tools/tool-definitions.ts`; `https://github.com/letta-ai/letta/blob/archive/letta/functions/function_sets/base.py` |

**Discrepancies worth stating (not smoothed):**
- Graphiti: third-party references report **9 tools** (`https://policylayer.com/tools/graphiti`) and a different naming set (`add_episode`/`search_facts`, `https://getzep-graphiti.mintlify.app/advanced/mcp-server`). The source on `main` has 13. The surface is a moving target — pin the commit before comparing. ⚠️ *unverified which third-party set matches any released tag.*
- Mem0: the OSS MCP server (9 tools) does **not** contain `list_events` / `get_event_status`; the docs list them. So the docs describe the **hosted** server. Two products, two counts — do not merge them.
- Cognee: earlier `cognee-mcp` versions exposed far more tools (`cognify`, `search`, `prune`, …). On `main` today there are 4. ⚠️ *unverified count for older versions.*

### 1a. Contradiction, supersession and forgetting — and whether the agent can steer it

| Comparable | Mechanism | Agent-visible / steerable? | Source |
|---|---|---|---|
| Graphiti / Zep | Bi-temporal model: each edge records when a fact was true and when it was learned; a contradicting fact **invalidates** the old edge (`invalid_at`) rather than deleting it, so point-in-time queries still work | **Partly.** The agent can see provenance (`get_episode_entities`) and can delete an edge (`delete_entity_edge`) — but **invalidation is not a named tool**. The agent cannot say "supersede this"; it says "delete this" | `https://blog.getzep.com/how-zep-tracks-provenance-in-agent-memory/`, `https://www.getzep.com/ai-agents/temporal-knowledge-graph/` |
| Mem0 | LLM-side ADD / UPDATE / DELETE / NOOP resolution at write time; `update_memory` overwrites text after the agent confirms the `memory_id` | **Partly.** Hosted only: `list_events` + `get_event_status` let the agent see what operations happened and whether an async one finished | `https://github.com/mem0ai/mem0-mcp/blob/main/src/mem0_mcp_server/server.py` (source), `https://docs.mem0.ai/platform/mem0-mcp` (docs) |
| Cognee | `forget` is an explicit, first-class agent tool. No invalidation or supersession semantics exposed | **Yes for delete, no for supersession** | source (above) |
| Letta | Self-editing: `memory.str_replace` for targeted edits, `memory_rethink` / `rethink_memory` for wholesale block replacement. Supersession happens by overwrite | **Yes — maximally.** Steering the agent's own memory *is* the design. No history tool is exposed, so the previous value is gone | `https://github.com/letta-ai/letta/blob/archive/letta/functions/function_sets/base.py`; `https://www.letta.com/blog/memory-blocks/` |

### 1b. How many tools is too many — what is actually published

- **Anthropic (primary, official).** A five-server setup = **58 tools ≈ 55K tokens before the conversation starts**; "we've seen tool definitions consume **134K tokens** before optimization". Recommended trigger for on-demand tool discovery: *"Tool definitions consuming >10K tokens"*, *"Building MCP-powered systems with multiple servers"*, *"**10+ tools available**"*. Stated failure mode: *"wrong tool selection and incorrect parameters, especially when tools have similar names like notification-send-user vs. notification-send-channel."* With tool search: Opus 4 accuracy **49% → 74%**, Opus 4.5 **79.5% → 88.1%**; ~85% token reduction. — `https://www.anthropic.com/engineering/advanced-tool-use` **[HIGH]**
- **MCP spec itself is silent.** The server-concepts page defines `tools/list` and `tools/call` and says tools are *model-controlled*, but publishes **no** count or budget guidance. — `https://modelcontextprotocol.io/docs/learn/server-concepts` **[HIGH]** (checked directly; the finding is the absence)
- **Practitioner consensus on the number** converges on *"degradation starts somewhere in the tens"*, with specific figures ranging **20–30**, **30–50**, and a recommended budget of **15–20 per agent** — across four independent blogs (`toolrouter.com/blog/too-many-mcp-tools`, `getunblocked.com/blog/mcp-tool-overload`, `channel.tel/blog/mcp-server-monolith-fix-tool-scoping`, `eclipsesource.com/blogs/2026/01/22/mcp-context-overload`). **No vendor publishes a hard number.** Treat the *direction* as solid and the *number* as unverified. ⚠️ **[LOW] single-source per figure**

---

## 2. Where we sit against them

Our measured surface (from `config/surface-manifest.yml`, `cut_at_commit: 4488de7d`, and the #3863 brief):

- **99** registered MCP tools across **11** groups — memory 32 · graph 16 · reasoning 14 · sessions 9 · admin 7 · onboarding 7 · journal 5 · sources 4 · review 3 · ask 1 · mining 1
- **152** public SDK methods: **89 agent-reachable** (82 through a tool's declared `sdk_method` binding,
  2 through a registered tool's handler — `recall_gaps`, `recall_subgraph` — and 5 through a CLI verb:
  `apikey_create`, `close`, `reconcile_sessions`, `session_index_health`, `volunteer_context`),
  **20 called only from our own engine, tooling or the tenant REST surface**, **41 reachable by no agent
  path**, and 2 `control-plane` methods (`apikey_revoke`, `graph_delete`) that **neither an MCP tool nor
  a CLI verb calls** — they are reached only from tenant REST in `tortoise/hosted_api.py` —
  89 + 20 + 41 + 2 = 152
- **64 of 99** never called in our own telemetry; **35** have (`used_by: agents` = 35 rows — verified in the manifest)
- **4** deprecated aliases (`lifecycle: "deprecated alias"` = 4 rows — verified; the 247 other rows are `active`)
- **5** tools declare an SDK method that does not exist (the tools still work via handlers — verified)
- **7** declared duplicate clusters in the baseline (`SEED_CLUSTERS` in `tools/surface_manifest.py`) — see §5. *(The brief said 6; the manifest declares 7. Flagged, not reconciled.)*

| Surface | Always advertised | Ratio vs ours |
|---|---|---|
| Cognee | 4 | ours is **24.8×** |
| Mem0 (OSS MCP) | 9 | **11.0×** |
| Graphiti / Zep | 13 | **7.6×** |
| Letta (always loaded) | 18 | **5.5×** |
| **Tortoise** | **99** | — |

**Verdict: yes — 99 is an outlier among agent-memory systems, and not marginally so.** The four comparables' always-loaded surfaces sum to **44 tools**; ours is more than **twice all four combined**. Letta — the largest of them — deliberately holds 18 ("one preferred tool for each job") and still ships a toolset switch on top; Cognee pins **3** and defers the rest. There is no memory system in this comparison that advertises anything close to 99.

Two honest qualifications:
1. **We are not like-for-like bigger.** Ours is a graph-memory *and* reasoning engine with sessions, sources and mining; Graphiti is a memory store. Part of the gap is genuine product scope.
2. **But the gap is not explained by scope alone.** 64 of our 99 tools have never been called in our own telemetry, and 63 of our 152 SDK methods have no agent path at all (41 with no caller found, 20 called only from our own engine, tooling or tenant REST, plus the 2 `control-plane` methods no MCP tool and no CLI verb calls). Roughly two-thirds of the advertised surface is unproven, and it is advertised by default.

---

## 3. Organisation patterns they use that we do not

**Pattern 1 — Pin a small verb-set; defer the rest behind tool search.** *(Cognee, and Anthropic's platform-level equivalent.)*
Evidence: `COGNEE_MCP_TOOL_MODE=default|minimal` pins `remember`/`recall`/`forget` in `tools/list` and routes the rest through `search_tools`/`call_tool`; `all` is explicitly labelled "pre-3.x behavior". Anthropic formalises the same idea with `defer_loading: true` and reports 49% → 74% selection accuracy on Opus 4.
**Fits us:** yes, directly. We already ship a `mode` argument on `tortoise_recall`; the deferral is the same idea applied at the `tools/list` layer instead of inside one tool, and it needs no new concepts.

**Pattern 2 — Collapse a whole domain to three verbs.** *(Cognee: `remember`/`recall`/`forget`. Letta: one `memory` tool with `command: create|str_replace|insert|delete|rename`.)*
Evidence: Cognee's entire memory API is 3 tools; Letta's is 1 tool plus 1 patch tool. Our own precedents are identical in shape — `tortoise_recall` (`mode` = state|gaps|subgraph|custom) and `tortoise_get` (absorbed six getters behind `type`).
**Fits us:** yes — this is the pattern we already invented twice, applied to 32 memory tools instead of to a handful.

**Pattern 3 — Name tools after jobs, not after table operations.** *(Graphiti.)*
Evidence: `search_memory_facts`, `build_communities`, `summarize_saga`, `get_episode_entities`, `add_triplet` — none of these is a CRUD mirror; each names something an agent would *want*. Graphiti still has `add_memory`/`search_nodes` for the ordinary path, so it keeps CRUD *and* adds job tools, without a CRUD mirror per entity type.
**Fits us:** yes — our `tortoise_query_points_by_tag`, `tortoise_paginated_query` and the three indexing tools are table operations wearing tool names.

**Pattern 4 — Expose structure only where the agent can act on it.** *(Graphiti exposes it; Mem0 hides it behind a boolean.)*
Evidence: Graphiti gives `entity_types` filters, relationship search, one-edge fetch, edge write, communities and provenance. Mem0 reduces the graph to `enable_graph: bool` on three tools and has no entity/relation tool at all.
**Fits us:** yes, with a caveat — we are graph-first, so hiding structure (Mem0's choice) would erase the differentiator. The lesson is *selectivity*: Graphiti exposes structure through a **small set of high-value verbs**, not one tool per node label.

**Pattern 5 — Writes happen in the background; tools are for reading.** *(Mem0's plugin core; Graphiti's async ingest.)*
Evidence: Mem0's coding-agent plugin ships exactly **one** tool, `search_memories`, and captures conversations through host lifecycle hooks, not through tools. Graphiti's `add_memory` "returns immediately and processes the episode addition in the background".
**Fits us:** yes — our never-called write-heavy tools (journal 5, sources 4, mining 1, the three indexing tools) are exactly this shape: ingest work the agent should not have to drive turn-by-turn.

**Pattern 6 — Behaviour lives in skills/prompts; the tool stays thin.** *(Mem0: six generated skills + a `memory_assistant` prompt. Cognee: descriptions authored for BM25 search.)*
Evidence: Mem0's core README: "the same `search_memories` MCP tool and six skill templates". Cognee's source comment tells tool authors to write descriptions "the words an agent would actually use — in both singular and plural" because "misses come from vocabulary, not from k".
**Fits us:** yes — much of what our 64 never-called tools encode is procedural knowledge (which sequence to call, in what order) that belongs in a skill, not in a tool schema. **Do not** copy the second half of this pattern (see §4).

---

## 4. What NOT to copy

| Do not copy | Whose | Why |
|---|---|---|
| Hiding all graph structure behind a boolean (`enable_graph`) | Mem0 | It is the right call for a vector-first memory store and the wrong call for a graph-memory product. It would delete our differentiator to win a token argument |
| A one-tool surface (`search_memories` only) as a product design | Mem0 plugin core | It works **only** because the host fires lifecycle hooks that capture every turn. A hosted memory API with no hook has no way to record anything — the agent would be unable to write at all |
| `all` mode as a default posture | Cognee | Its own source frames `all` as the pre-3.x escape hatch, not the target. It exists for compatibility, not as an aspiration |
| Top-level destructive tools with no gate: `clear_graph`, `delete_all_memories`, `delete_entities` | Graphiti, Mem0 | Irreversible, high-blast-radius operations sitting in the same flat list as `get_status`. Our `graph_delete`/`apikey_revoke` are already classified control-plane; keep the destructive class out of the agent-advertised default |
| Long-running graph algorithms as conversational tools (`build_communities`, `summarize_saga`) | Graphiti | These are maintenance jobs with unbounded latency and per-call cost. Graphiti gets away with it because it is a single-purpose server; for us they belong behind `mode`/admin, not in the default list |
| "Just write better descriptions" as the fix for findability | Cognee's BM25 note | Their own comment admits vocabulary misses survive good descriptions. Institutionalising a wording workaround instead of cutting the surface trades one problem for a permanent one |

---

## 5. The single strongest recommendation

**Make the group the tier: pin one always-advertised tool per group (plus the small graph-structure set that is our differentiator), defer the remainder behind tool search, and collapse the duplicate clusters into the canonical member that the baseline already names.**

Target shape — **99 advertised → ~14 advertised, all 99 still callable**:
- **11 pinned**, one per group, each with a `mode`/`type` argument in the shape we already ship (`tortoise_recall`, `tortoise_get`)
- **~3 graph-structure tools pinned deliberately** (`search` nodes, `search` relations, fetch-one-edge) — the Graphiti lesson that structure deserves a *small set of high-value verbs*
- **Everything else deferred** (`admin`, `onboarding` not advertised by default at all; `journal`, `sources`, `mining` moved behind background ingest)
- 14 ≤ Letta's 18, the largest always-loaded set in the comparison

**Collapse the 5 split clusters** (canonical member first, as the baseline already declares it):

| Cluster | Members → groups they currently sit in | Canonical |
|---|---|---|
| `fetch-by-id` | `tortoise_get` (graph, canonical) + `tortoise_get_entity`, `tortoise_get_operator`, `tortoise_get_events` (graph) + `tortoise_get_point` (memory) + `tortoise_get_session` (sessions) + `tortoise_get_governance` (admin) | `tortoise_get` |
| `create` | `tortoise_create_entity`, `tortoise_create_subject`, `tortoise_create_object`, `tortoise_create_event` (graph) + `tortoise_create_document` (sources) | `tortoise_create_entity` |
| `delete` | `tortoise_delete`, `tortoise_delete_point` (memory) + `tortoise_delete_entity` (graph) | `tortoise_delete` |
| `update` | `tortoise_update`, `tortoise_update_point` (memory) + `tortoise_update_entity` (graph) | `tortoise_update` |
| `deprecated-index` | `tortoise_index_files` (memory) + `tortoise_ingest_corpus` (sources) + `tortoise_index_sessions` (sessions) | `tortoise_index_files` |

(The other two declared clusters, `query` and `operator-action`, are *not* split across groups — they are intra-group duplicates and are handled by the same collapse, not by the cross-group argument.)

**The evidence behind it:**
1. **Every comparable stays small.** Always-loaded counts: Cognee 4 (3 pinned), Mem0 9 (1 in plugin form), Graphiti 13, Letta 18. Ours is 99. Nothing in the category validates 99.
2. **The platform-level guidance says defer at 10+ tools, and reports measured accuracy gains** (49% → 74% on Opus 4; wrong *tool selection*, not vendor opinion, is the named failure mode) — `https://www.anthropic.com/engineering/advanced-tool-use`.
3. **We already shipped this pattern twice** — `tortoise_recall` (`mode`) and `tortoise_get` (`type`, absorbing six getters). This is not a new architecture; it is the existing one applied to 11 groups instead of 2 tools.
4. **Two-thirds of the surface is unproven** — 64 of 99 never called in our own telemetry, 63 of 152 SDK methods with no agent path. Deferral costs nothing we can demonstrate we need, and the 5-method declaration mismatch is a symptom of the same drift.
5. **The pinned set is derivable, not authored** — Cognee derives its advertised set from per-tool tags rather than a hand-maintained list. Our baseline already carries `family`, `cluster`, `canonical` and `recommendation` per row, so the pinned/deferred split and the cluster collapse can be generated from the manifest instead of maintained by hand.

**Sequenced:** collapse the 5 clusters and cut the 64 never-called first (each is a mechanical, baseline-declared change), *then* enable deferral — otherwise the deferred tail still contains duplicates that tool search will happily surface.

---

## 6. Sources and confidence

**Counted from source (fetched raw on 2026-09-17)**

| # | URL | What it established |
|---|---|---|
| 1 | `https://github.com/getzep/graphiti/blob/main/mcp_server/src/graphiti_mcp_server.py` | Graphiti = **13** tools + names + `source` mode arg + no read-only switch |
| 2 | `https://github.com/getzep/graphiti/blob/main/mcp_server/src/config/schema.py` | no `read_only` config (absence check) |
| 3 | `https://github.com/mem0ai/mem0-mcp/blob/main/src/mem0_mcp_server/server.py` | Mem0 OSS MCP = **9** tools + names + `enable_graph` boolean |
| 4 | `https://github.com/mem0ai/mem0/blob/main/integrations/agent-plugin-core/python/mcp_server.py` | Mem0 plugin core = **1** tool (`TOOL_NAME = "search_memories"`) |
| 5 | `https://github.com/mem0ai/mem0/blob/main/integrations/agent-plugin-core/README.md` | capture via lifecycle hooks; six generated skills; `repo/dir/mine` scopes |
| 6 | `https://github.com/topoteretes/cognee/blob/main/cognee-mcp/src/server.py` | Cognee = **4** tools + `COGNEE_MCP_TOOL_MODE` + `BM25SearchTransform` |
| 7 | `https://github.com/topoteretes/cognee/blob/main/cognee-mcp/src/tool_registry.py` | tag-driven registration (`default`/`memory`/`status`); derived advertised set |
| 8 | `https://github.com/letta-ai/letta-code/blob/main/src/tools/letta-toolset.ts` | Letta always-loaded = **18**; `StartupToolsetPreference` switch |
| 9 | `https://github.com/letta-ai/letta-code/blob/main/src/tools/tool-definitions.ts` | **66** total tool definitions (model-specific aliases) |
| 10 | `https://github.com/letta-ai/letta/blob/archive/letta/functions/function_sets/base.py` | one `memory` tool with `command` sub-commands; the 13 legacy memory/archival functions |
| 11 | `config/surface-manifest.yml`, `tools/surface_manifest.py`, `config/surface-order.yml` (this repo, `cut_at_commit 4488de7d`) | our 99/152 counts, 35 `used_by: agents`, 4 deprecated aliases, 5 broken SDK declarations, the 7 declared clusters and their members |

**Documentation / secondary sources**

| # | URL | What it established | Confidence |
|---|---|---|---|
| 12 | `https://www.anthropic.com/engineering/advanced-tool-use` | 58 tools ≈ 55K tokens; 134K observed; defer at 10+ tools; 49% → 74% accuracy; wrong-tool-selection failure mode | **[HIGH]** primary vendor |
| 13 | `https://modelcontextprotocol.io/docs/learn/server-concepts` | the spec defines `tools/list` and is **silent** on tool count | **[HIGH]** checked directly |
| 14 | `https://docs.mem0.ai/platform/mem0-mcp` | hosted MCP = **11** tools (adds `list_events`, `get_event_status`) | **[MEDIUM]** ⚠️ docs only — server is hosted, could not count from source |
| 15 | `https://docs.cognee.ai/cognee-mcp/mcp-tools` + `.../mcp-local-setup` + `.../mcp-overview` | corroborates the 3 memory verbs + `cognify_status` and the three modes | **[HIGH]** independent of #6/#7 |
| 16 | `https://blog.getzep.com/how-zep-tracks-provenance-in-agent-memory/`, `https://www.getzep.com/ai-agents/temporal-knowledge-graph/` | invalidation-not-deletion; `invalid_at`; provenance from episodes | **[MEDIUM]** ⚠️ emerging — vendor-authored, two pages of one source |
| 17 | `https://www.letta.com/blog/memory-blocks/`, `https://docs.letta.com/letta_memgpt` | memory blocks labelled/editable/`read_only`; self-editing memory via tool calls | **[MEDIUM]** ⚠️ emerging — vendor-authored |
| 18 | `https://policylayer.com/tools/graphiti` | claims Graphiti exposes **9** tools (conflicts with 13 counted in #1) | **[LOW]** ⚠️ single-source, contradicts source |
| 19 | `https://toolrouter.com/blog/too-many-mcp-tools`, `https://getunblocked.com/blog/mcp-tool-overload/`, `https://www.channel.tel/blog/mcp-server-monolith-fix-tool-scoping`, `https://eclipsesource.com/blogs/2026/01/22/mcp-context-overload/` | degradation thresholds 20–30 / 30–50 / budget 15–20 per agent | **[LOW]** ⚠️ single-source per figure — treat the direction as solid, the numbers as unverified |

**Explicitly not established here**
- ⚠️ No comparable publishes a hard "N tools is too many" number. Every specific figure is practitioner opinion.
- ⚠️ Mem0's hosted 11-tool surface is read from docs, not counted — the server is hosted and closed.
- ⚠️ Cognee's historical tool count (pre-3.x) is unverified; only today's `main` was counted.
- ⚠️ Whether Graphiti's 13 differ by release tag from the third-party "9 tools" figure is unverified.
- The brief's "6 groups of near-duplicate tools" vs the manifest's **7** declared `SEED_CLUSTERS` is unresolved; the manifest was treated as authoritative and the discrepancy is flagged in §2 and §5.
- This study covers the **agent-facing operation surface** only. It does not evaluate memory *quality*, ranking, latency, or cost per call, and no system was executed.
