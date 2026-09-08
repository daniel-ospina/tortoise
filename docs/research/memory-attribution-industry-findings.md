---
title: "Research — Actor Attribution in Agent-Memory Knowledge Graphs (industry code audit)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-09-08
aboutSubjects: tortoise
aboutObjects: tortoise-agent
---

# Industry findings: actor attribution in agent-memory knowledge graphs

Question: should "which human filed this memory" be a property on every written memory node
(Option 2A) or ride at session level (Option 2B)? Findings below are from reading the actual
source of five leading products (all cloned 2026-09-08, depth 1, head-of-main). Repos:
`getzep/graphiti`, `mem0ai/mem0`, `letta-ai/letta-code` (note: `letta-ai/letta` main is now a
docs-only landing page whose AGENTS.md forbids using its retired V1 archive as evidence of
current behavior, so current code was read from `letta-code`), `langchain-ai/langmem`,
`basicmachines-co/basic-memory`.

## 1. Zep / Graphiti (graph-tit… graphiti_core)

**(a) Actor is NOT per-record.** Every node — entities/facts, episodes, communities, sagas —
shares one base class with exactly five fields: `uuid, name, group_id, labels, created_at`
(`graphiti_core/nodes.py:93-100`). EpisodicNode adds `source` (an EpisodeType enum), a
free-text `source_description`, `content`, `valid_at`, and a customer `episode_metadata` dict
(`nodes.py:318-330`). `user_id` appears nowhere in the data model (only in anonymous telemetry,
`graphiti_core/telemetry/telemetry.py:103`).

**(b)** N/A — no per-record actor property.

**(c) Coarse scoping via `group_id`.** `group_id` is a required partition property on every
node ("partition of the graph", `nodes.py:96`) and is the delete/search boundary
(`Node.delete_by_group_id`, `nodes.py:178`). Provenance to a source conversation rides the
graph: episodes connect to entities via `EpisodicEdge` (`edges.py:143`) and consecutive
episodes via `NextEpisodeEdge` (`edges.py:822`). "Whose memory" = the caller chooses one
`group_id` per user/group ("Manages vast numbers of per-user/entity context graphs",
`README.md:100`).

**(d)** `add_episode(...)` takes `name, episode_body, source_description, group_id, ...` — no
actor argument (`graphiti_core/graphiti.py:1043-1075`). The hosted REST API requires only
`group_id` (`server/graph_service/dto/ingest.py:6-13`). Identity is client-claimed (caller
picks the partition), not server-derived.

## 2. Mem0

**(a) Per-record, but as indexed scope fields — the exception to the rule.** `add(messages,
*, user_id=None, agent_id=None, run_id=None, metadata=None, ...)` (`mem0/memory/main.py:760-773`)
requires ≥1 of `user_id`/`agent_id`/`run_id` (docstring `:777`; `Mem0ValidationError`
`VALIDATION_001`, `:392-396`). Chosen ids are copied into every stored memory's payload via
`base_metadata_template["user_id"] = user_id` etc. (`main.py:376-383`), and caller `metadata`
is stripped of identity keys so freeform metadata can't change scope (`main.py:135-171`).

**(b) Indexed namespace-by-payload.** Qdrant store builds payload indexes on exactly
`["user_id", "agent_id", "run_id", "actor_id"]` at init (`mem0/vector_stores/qdrant.py:166-182`)
— i.e., the fields are the *retrieval key*, not an audit column. Additionally a SQLite history
table carries per-event `actor_id` + `role` columns (`mem0/memory/storage.py:68-118`), populated
per message from the message's `name` field (`main.py:899-910`).

**(c)** Queries (`search`, `get_all`) also require a filter containing ≥1 id
(`main.py:1268-1302`); retrieval is a payload-equality filter per user/agent/run.

**(d)** Ids are arguments to `add()`/`filters` — client-claimed, then locked server-side so
they can't be forged via metadata. There is no auth-derived actor; `actor_id` (the "who spoke
this message") comes from the message object.

## 3. Letta (letta-code)

**(a) Coarse, agent-scoped.** Agent memory is memory blocks/files owned by an agent; local
memfs is partitioned `.letta/agents/<agent_id>/memory` (`src/agent/memory-filesystem.ts:29-31`).
Blocks are labeled (e.g., persona/human content) and passed per-agent at creation
(`src/agent/create-agent-request.ts:73-89`; label→value merge in `create.ts:309`). No per-memory
author field. The human user is represented as memory *content* (identity blocks), not as a
field on memories.

**(b)** N/A per-record.

**(c)** Shared memory = shared blocks between agents (`blockProvenance … source: "shared"`,
`create.ts:327-332`). Memory commit history is attributed to the **agent identity** as the git
author (memfs repo sets local `user.name` to the agent display name, `src/agent/memory-git.ts:861-895`).

**(d)** Human attribution is a *request-layer* concern: cloud re-attributes calls to the
initiating human via an `X-Letta-Acting-User-Id` header that cloud-api validates against org
membership before honoring (`src/agent/acting-user.ts:1-29`). Server-validated, not client-claimed;
used for conversation attribution, not written onto memory nodes.

## 4. LangGraph LangMem

**(a) Coarse via namespace — no per-record actor.** A memory item is `{key, value, namespace}`
(`src/langmem/knowledge/extraction.py:45-86`); the Memory schema is just `content` (`:86-95`).

**(b)** N/A per-record.

**(c)** User identity is a *namespace level*: default namespace
`("memories", "{langgraph_user_id}")` (`extraction.py:699`), resolved at runtime from graph
config `{"configurable": {"langgraph_user_id": "user-123"}}` (`extraction.py:713-727`).
"Show me user X's memories" = search in namespace `("memories", "X")`. BaseStore keys partition
by namespace (deterministic id includes namespace, `extraction.py:938`).

**(d)** The manage/search tools are *bound* to a namespace template at construction
(`create_manage_memory_tool(namespace=("memories", "{langgraph_user_id}"), ...)`,
`src/langmem/knowledge/tools.py:25-50`); the tool's own signature is only
`(content, id, action)` (`tools.py:57-64`). The LLM can never name the user — identity is
injected from run config (client/run-scoped), not per-call.

## 5. basic-memory

**(a) Hybrid: per-entity, not per-fact.** The `entity` table has nullable
`created_by`/`last_updated_by` columns ("Who created this entity (cloud user_profile_id UUID,
null for local/CLI usage)", `src/basic_memory/models/knowledge.py:111-114`) — added by a later
migration (alembic `k4e5f6g7h8i9_add_created_by_and_last_updated_by_to_entity`). The atomic
`observation` table (the fact rows) has **no** author columns (`knowledge.py:426-486`) — the
"file"/entity/document is the attribution unit.

**(b)** Not indexed for retrieval (only `created_at`/`updated_at` indexed, `:49-50`); nullable;
audit-style metadata surfaced in API responses.

**(c)** No user-scoped query semantics found; attribution is metadata, not a partition key.
Local/CLI writes carry `None` (single-user by construction).

**(d)** Stamp flows from the request actor: `user_profile_value = str(request.actor.user_profile_id)`
(`src/basic_memory/indexing/accepted_note_mutation_runner.py:703-705`), applied to
`created_by`/`last_updated_by` on every accepted note write (`:430`; `services/note_preparation.py:150-172`).

## Synthesis

Four of five products attribute at a **coarse level**, not per memory record. The dominant
pattern for "which human does this memory belong to" is a **scope key stamped at the write
boundary and used as the partition/namespace/filter**: Graphiti stamps `group_id` on every node
but the caller supplies it once per ingestion unit; LangMem keeps `user_id` purely at namespace
level (the model never sees it); Letta partitions by `agent_id` and re-attributes human actors
server-side at the request layer only; basic-memory tracks "who created the entity/note" as a
nullable audit column (facts themselves carry nothing). Mem0 is the one true per-record model —
but its per-record `user_id/agent_id/run_id/actor_id` fields are explicitly **indexed payload
filters** and a write-time requirement (one id per `add`), i.e., a namespace implemented as an
indexed property rather than a graph path, plus an audit-style `actor_id` on history events.

Two conclusions for the Tortoise decision. First, **per-node actor stamping (2A) is not the
industry norm** — it exists (Mem0, basic-memory's entity columns) only where (i) the id is the
retrieval scope and is indexed for it (Mem0) or (ii) it's a nullable audit field with no query
role (basic-memory), and both store it as a single string property on an otherwise flat
record, not a graph hop. Second, the 2B concern (unattributed out-of-session writes) is handled
industry-wide by **defaulting an actor at the write boundary**: Mem0 *rejects* adds without an
id; Graphiti requires `group_id`; Letta validates a server-side actor override; basic-memory
defaults to `None` in local single-user mode. Nobody lets attribution silently vanish — they
make one of (require it, default it to the authenticated principal, or scope it so
single-user needs no stamp). A session→user link (2B) matches Graphiti/LangMem/Letta; where
per-node provenance is genuinely needed, Mem0 shows the cheap form: one indexed scope property
+ an actor field on the *event/history* log, not a stamp duplicated onto every derived memory
node. No product was found that shipped per-node actor stamps and later removed them; the
observed movement is the opposite direction (basic-memory added per-entity audit columns later).

*Method: repos cloned into /tmp/tortoise_research (shallow, main HEAD 2026-09-02..08); all
claims cite file:line from that snapshot. No closed-source behavior was inferred — Letta's
current code was read from letta-ai/letta-code because letta-ai/letta explicitly deprecated its
Python source; Zep cloud server-side auth resolution beyond the open server code was not
assessed.*
