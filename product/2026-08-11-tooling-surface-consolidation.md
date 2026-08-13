---
title: Tortoise tooling surface — consolidation to 19 primitives
type: design
domain: engineering
status: live
created: 2026-08-11
updated: 2026-08-11
related: ["epic #888 (MCP/SDK surface design v2)", "epic #898 (recall)", "ontology v3.5 reification rule (PR #910)", "product/2026-08-11-workflows-skills-usecases.md (PR #899)"]
---

# Tortoise tooling surface — consolidation to 19 primitives

Consolidates the current **69 MCP tools** into **19 primitives**, organized around the
workflows in `product/2026-08-11-workflows-skills-usecases.md`. Principles:

1. **Tools = primitives** the agent must invoke distinctly, or that need nudging.
   **Workflows = things the skill explains** using primitives (not separate tools).
2. **Reification rule** (ontology v3.5 §8): an edge carries an operator iff it needs
   mitigation (or is a Point↔Point support/contradict). Structural edges stay plain +
   carry confidence as an edge attribute.
3. **Intuitive user-facing names** — no internal names (e.g. `compute_confidence`, not `ep`).
4. **Nudge, don't enforce** — write responses surface connection candidates; the agent
   decides whether to connect.

## The 19 tools

| Group | Tools |
|---|---|
| **Recall** (1) | `recall(mode=state\|gaps\|subgraph\|custom)` |
| **Write** (5) | `create_point`, `create_entity`, `create_source`, `create_edge`, `operator_action(action=mitigate\|annotate)` |
| **Update/Delete** (2) | `update`, `delete` |
| **Node lifecycle** (2) | `supersede(transfer_edges=)`, `retract` |
| **Confidence** (1) | `compute_confidence(scope=subgraph, set_baseline)` |
| **Bulk** (1) | `ingest(bundle, granularity=granular\|bulk)` |
| **Review** (1) | `review_connections(mode=add\|prune\|both)` |
| **Workflows** (2) | `index`, `mine(serendipity=0..1)` |
| **Orient** (2) | `overview(section=)`, `events_poll` |
| **Direct** (1) | `get(id, type=)` |

## Cross-cutting design decisions

### Reification (ontology v3.5 §8)
- `operator_action(action=mitigate|annotate)` is the mitigation substrate. Operator iff mitigation
  (or Point↔Point support/contradict). Structural edges stay plain.
- `create_edge` creates typed structural edges (performs/produces/uses, memberOf/ownedBy, about*),
  operator-less per the reification rule. Lazy promotion: add an operator when mitigation becomes needed.
- EP propagation over operator-less IMPL/NAND edges requires an EP update (edge-level direction +
  message init) — tracked as EP follow-up.

### Node lifecycle (not point lifecycle)
- All nodes (Points + entities) carry the lifecycle (`draft/live/retracted/superseded/outdated/archived`).
- Points additionally carry EP confidence (the Point-specific epistemic dimension).
- `supersede` / `retract` / `update` / `delete` are node-lifecycle ops (apply to Points AND entities).

### Write nudges (nudge, don't enforce)
- Write actions create the node and return `{node, nudges:[{candidate, suggested_relation}]}`.
- Nudges suggest IMPL/NAND/mitigate connections to related candidates. Not enforced — the agent
  acts on them via `operator_action`/`create_edge` if it wants. Drives connectivity without forcing it.

### `update` / `delete` unify point + entity
- One `update(id, props)`: detects node type; if a Point applies point-lifecycle semantics
  (draft→live promote, version increment for Point:Object, status validation), else plain update.
- One `delete(id)` (destructive, human-confirm), point or entity.

### `dream` is NOT a tool
- Dreaming (whole-graph/expanding EP to keep the graph fresh) is **maintenance, not an agent action**.
- Runs as a scheduled/internal operation: hosted = server-side schedule; enterprise self-hosted =
  customer-configured. Removed from the MCP surface. `compute_confidence(scope=subgraph)` stays
  (agent-triggered, targeted EP after connecting).

### `compute_confidence` naming
- The EP-running tool is `compute_confidence` (intuitive), not `ep` (internal). `set_point_baseline`
  folds in as `compute_confidence(set_baseline=...)`.

## Old tool → new surface mapping

**Folded into the 19:**
| Old | New |
|---|---|
| search, query, paginated_query, query_points_by_tag, entity_profile, traverse, list_topics, suggest_entry_points, search_sessions, session_context, get_session, get_events, get_point, get_entity, get_operator | `recall(mode)` + `get(id)` |
| create_subject/object/event/document | `create_entity(type=)` |
| create_operator, mitigate_operator, annotate_operator | `operator_action(action=mitigate\|annotate)` |
| update_point, update_entity | `update` |
| delete_point, delete_entity | `delete` |
| invalidate | `supersede(transfer_edges=false)` |
| check_structure, summarize_structure, list_pointkinds, list_sources, list_namespaces, list_tags, list_graphs, status, health, taxonomy, stale | `overview(section=)` |
| compute_confidence, set_point_baseline | `compute_confidence(scope=, set_baseline=)` |
| dream | config/internal (not a tool) |
| index_sessions, ingest_corpus | `index` + `ingest(bundle)` |
| assess_source, set_source_tier, get_source_reliability | folded into `create_source` credibility params |
| get_governance | `get`/`overview` |
| get_confidence | `recall` (confidence surfaced per-result) |
| checkpoint | `ingest(bundle)` (batch-save-with-dedup) |
| calibrate_summary | `compute_confidence` calibration mode |
| events_poll | `events_poll` (kept) |
| onboarding_* (6) | retired post-completion (per #896) |

**Moved to skill-guided workflows (not tools):** `file_decision`, `file_human_approval`,
`analyze`, `provenance` — explained in how-to-use-tortoise, orchestrated from primitives.

**SDK-only (pro builders, not MCP):** `team_create` (provisioning belongs to /internal/provision).

**Dropped:** `diary_write`, `diary_read` (diary is a code-registered custom kind, not canonical
in ontology §5; overlaps with sessions/create_point).

## Worker plan (deepseek v4 flash, parallel where independent)

- **W1 — recall modes:** `mode=gaps` (UC2) + `mode=subgraph` (UC3), builds on #907 (Wave A state).
- **W2 — write/revise consolidation:** `create_entity`, `update`, `delete`, `supersede(transfer_edges)`,
  `operator_action`, `create_edge` (reification rule) + write nudges. Deterministic AND LLM-judged tests.
- **W3 — orient/get:** `overview`, `get`; retire the list_* zoo + status/health.
- **W4 — bulk:** `ingest(bundle, granularity=)`.
- **W5 — EP update:** edge-level direction + message init for operator-less IMPL/NAND propagation.
- **W6 — review_connections:** `review_connections(mode=add|prune|both)` (needs epic filed).

Note: W2's semantic merges (create_entity about* edges, supersede/invalidate) need LLM-judged tests,
not just deterministic metadata-completion tests.
