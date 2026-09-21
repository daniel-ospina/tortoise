---
title: "Canonical MCP tool list — the owner-approved target surface"
type: synthesis
domain: capability
doc_status: live
created: 2026-09-18
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: mcp-tool-surface
approval_status: approved
approved: 2026-09-18
approvedBy: daniel-ospina
approval_pr: 4120
issue: 3863
related:
  - "#3994"
  - "#3883"
  - "#4113"
  - "#4114"
---

# Canonical MCP tool list — 23 tools

**Owner-approved 2026-09-18.** This is the target MCP surface. Everything not on this
list is retired, folded into a member below, or archived. The surface is frozen in both
directions until this list is executed — see `docs/product/mcp-sdk-surface.md` (the
generated view of the current baseline) and the gate in `tools/surface-guard.py`.

> This document is the **approval record**, not the generated baseline. The generated
> list of what exists *today* is `docs/product/mcp-sdk-surface.md`, rendered from
> `config/surface-manifest.yml`. This file records what the surface is *approved to become*.

## Principle: reads and writes never share a tool

On the customer-grantable surface, a tool is either **read** or **write** — never both, so
read permission can be granted generously with no path to a destructive action.

**Operator-only tools are exempt.** They are never handed to a tenant, so they may mix
reads and writes — `index_files` is the clean example: both of its members (`index_files`,
`mine_conversations`) are excluded from tenant HTTP.

**Caveat:** `manage_deployment` was intended as a second example, but two of its three
members are tenant-served today (`packs_list` is a read, `pack_install` is a write), so its
placement is still open. See **Open items**.

## READ — 9 tools

Grantable freely **once the one blocking item below is fixed** — `recall_beliefs`
currently absorbs `get_confidence`, which is read-labelled but writes. See **Open items**.

| # | Tool | Absorbs |
|---|---|---|
| 1 | `search_knowledge` | search, suggest_entry_points, search_sessions |
| 2 | `list_knowledge` | query |
| 3 | `recall_beliefs` | recall, belief_timeline, provenance, session_context, get_confidence, calibrate_summary |
| 4 | `get_entity` | get, get_session, get_entity |
| 5 | `explore_connections` | expand_relationships, entity_profile, traverse |
| 6 | `graph_overview` | overview, check_structure, validate_domain, audit, issue_insight, dream_health, summarize_structure |
| 7 | `review_link_candidates` | review_connections, find_cross_lens_candidates, list_dedup_candidates |
| 8 | `poll_events` | events_poll |
| 9 | `inspect_batch` | list_batch, list_batches |

## WRITE — 14 tools

| # | Tool | Absorbs |
|---|---|---|
| 10 | `create_entity` | create_point, create_entity, create_event, create_object, create_subject, create_document, diary_write |
| 11 | `register_source` | create_source |
| 12 | `index_files` | index_files, mine_conversations *(operator-only)* |
| 13 | `capture_knowledge` | session_capture, ingest, checkpoint |
| 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |
| 15 | `link_entities` | create_edge, create_operator |
| 16 | `record_decision` | file_decision, file_human_approval |
| 17 | `revise_knowledge` | update, update_point, update_entity, supersede, invalidate, retract_point, promote_point, set_point_baseline |
| 18 | `delete_knowledge` | delete, delete_point, delete_entity |
| 19 | `stabilize_beliefs` | dream, compute_confidence |
| 20 | `approve_merge` | approve_merge |
| 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |
| 22 | `manage_deployment` | org_create, packs_list, pack_install — **placement OPEN**, see Open items |
| 23 | `run_onboarding` | the seven `onboarding_*` |

## Removed entirely

| Name | Why |
|---|---|
| `analyze` | spends LLM tokens at read time. Write-time LLM cost is ours; read-time LLM is not offered |
| `diary_read` | dissolves into `list_knowledge(kind="diary")` |
| `backfill_v25` | proposed for archive — a one-shot migration to ONTOLOGY **v2.5**; the ontology is now **v3.13** (`docs/ONTOLOGY.md:2`, which itself supersedes v2.5). Zero customers, pre-beta: no legacy graphs exist to migrate. The baseline records it `lifecycle: active` with `recommendation: review`, so archiving is a proposal here, not a recorded status. Rebuild against the then-current schema if ever needed |

**Already gone before this list:** `ask` — eval-only, removed by issue #3849 / PR #3929.
It is not a row above because this table lists tools *this change* removes.

**Reading the "Absorbs" columns:** some baseline tools are not named individually
because a container already absorbs them transitively — `get_point`, `get_events`,
`get_operator`, `get_governance` sit inside `get`; `health`, `status`, `stale`,
`taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`,
`list_graphs`, `list_topics` sit inside `overview`; `paginated_query` and
`query_points_by_tag` sit inside `query`; `index_sessions` and `ingest_corpus` sit
inside `index_files`.

Two names that appear in earlier drafts of this list — `index_source` and
`manage_confidence` — were **proposed container names that never shipped**. Neither has
ever existed as a tool in this repo, so neither is a row above. `index_source`'s job is
done by `register_source`; the confidence tools are split across `recall_beliefs`,
`stabilize_beliefs` and `revise_knowledge`.

## Design decisions

1. **Cost policy:** write-time LLM cost is ours; **read-time LLM is not offered**. If a
   question needs an LLM, it is answered from data (search / list / recall), never by
   spending tokens at the customer's request.
2. **Operator-only tools stay separate** where merging would force a choice between
   exposing a privileged capability and losing a customer-visible one.
3. **A writer never shares a read-only name.** `approve_merge` is split out of
   `review_link_candidates` for exactly this reason.
4. **Points, Events, Sources, Subjects, Objects and Documents are all entities**
   (ontology §1), so one `create_entity` with `type=` covers the whole creator family
   with the right fields per type. `create_source` is the exception: a Source is a
   provenance anchor whose **URL is its node identity**, with tier canonicalisation,
   upsert-by-content-hash and its own journaling — so it stays `register_source`.
5. **Naming:** verb + object, 2–3 words, no internal jargon. `get_entity` not `get`;
   `explore_connections` not `explore_graph`; `adjust_relationship` not `operator_action`.
6. **Two shapes for ingesting files, deliberately:** `index_files(dir)` for a
   self-hoster with files on the server's disk; `capture_knowledge` / `ingest` for a
   hosted customer sending content. Both are needed; neither is redundant.

## Open items

| Item | Status |
|---|---|
| **Confidence reads currently WRITE.** `get_confidence` is annotated `readOnlyHint=True` (the `tortoise_get_confidence` entry in `tortoise/tool_registry.py`, `annotations=_ro()`) but, for a dirty root, calls `self.dream(dirty_only=True, ...)` inside `TortoiseSDK.get_confidence` (`tortoise/sdk.py`), which writes `SET n.confidence = p.c` in `tortoise/dream.py` (`_ep_run_batch`). Until that read path stops writing, `recall_beliefs` cannot honestly be called read-only. *(Cited by symbol, not line: line numbers go stale on every rebase.)* | **BLOCKING for the read/write guarantee** |
| **`tortoise_get_entity` must NOT be retired** by the #3883 warning shim. The map must retire `tortoise_get` → `tortoise_get_entity(id, type=...)` instead, because `list_tools` strips retired names *by string* — so retiring `get_entity` while re-declaring it would hide it from agents entirely. | **✅ RESOLVED — PR #4031 retires `tortoise_get` into `tortoise_get_entity` and keeps `get_entity` live; the tool gained the `type=` dispatch (`point`\|`entity`\|`operator`\|`events`\|`governance`), so the named replacement call resolves, and `TortoiseSDK.get_entity` is untouched. The earlier inverse (retiring `get_entity` into `tortoise_get`) was the must-fix this row raised.** |
| **`compute_confidence` is mislabelled in the same way.** Annotated read-only, but it persists: `TortoiseSDK.compute_confidence` (`tortoise/sdk.py`) runs `MATCH (n:Point {id: p.id}) SET n.confidence = p.c`. `stabilize_beliefs` (a write tool) is therefore its correct home — confirmed. | resolved — moves to `stabilize_beliefs` |
| **`packs_list` is tenant-facing, not operator-only.** The registry has it `_ro()`, `http_policy=True`, described as *"List this team's active packs … another tenant's packs are never observable"* (`tortoise/tool_registry.py`). Merging it into an operator-only `manage_deployment` would **remove a tenant-visible read**. Owner call: does a tenant need to list its own packs? If yes, `packs_list` belongs in a customer-readable tool. | **OPEN — owner decision** |
| **`pack_install` is tenant-facing too — an unflagged write.** It has `http_policy=True` and is described as *"Install a custom expansion pack on the **HOSTED surface** … stores the manifest in the tenant graph and activates it"* (`tortoise/tool_registry.py`); the baseline records it `served: http`. So folding it into an operator-only `manage_deployment` would **remove a tenant-visible write**, the same defect as `packs_list` but for a write. (Its `hosted_only=True` flag is separately dead — see #4114.) Owner call: does a tenant install its own packs? | **OPEN — owner decision** |
| **`run_onboarding` absorbs two read-only tenant-served tools** — `onboarding_state` and `onboarding_github_status` are `_ro()` and `http_policy=True`. So it is both read and write on the customer-grantable surface, which the principle above forbids. Either mark it operator-only (losing the tenant reads) or accept a read-inside-write and state the exemption. | **OPEN — decision** |
| **`graph_set_recording` — dropping it removes an agent's self-heal path.** The registry says the capture `409` (*"Session recording is disabled for this graph"*) *"routes agents here; call this tool to turn recording back on, then retry the capture"* (the `tortoise_graph_set_recording` entry in `tortoise/tool_registry.py`). REST exposes the same operation at `PATCH /v1/graphs/{graph_id}`, but **an MCP client cannot reach REST** — so dropping it would leave an agent that hits a 409 with no recovery path inside MCP. Owner call: **keep it as a 24th tool** (a write; needs a `team:manage`-scoped key), or accept the loss. | **OPEN — owner decision; keeping it makes the count 24** |
| **SDK method `TortoiseSDK.get_entity`** keeps the narrow meaning while this tool takes the broad one — same name, two meanings, one layer apart. | resolve in the SDK pass |
| **Bridge table** — every `type=`/`mode=` value in these 23 mapped to the SDK method behind it, before implementation starts. | pre-flight |

## Sequencing

1. Approve this list. *(done)*
2. **Publish the bridge table** (MCP `type=`/`mode=` → SDK method).
3. Implement the MCP list with the **SDK frozen** (method names + capabilities unchanged)
   so the handlers are written once.
4. Then the **SDK canonical list** (`config/surface-manifest.yml` declares **150** public
   methods; the scoping doc says 152 — reconcile when that pass starts). Renames there are mechanical for the
   MCP handlers; only a *capability* removal can force an MCP change — which the bridge
   table will have flagged.

## Verification that produced this list

Checked against `origin/main`: **every baseline tool but one is absorbed by exactly one target
or explicitly removed**, **zero double-absorption**, **no collisions with REST routes**,
and **17 of the 23 names appear nowhere else in the repo** — the six that do are
`get_entity`, `create_entity`, `register_source`, `index_files`, `approve_merge` and
`run_onboarding`, and in every case the match is a different identifier or the existing
tool of the same name. The one unplaced tool is `graph_set_recording`, whose disposition is
still open (see Open items) — so "zero orphans" holds only once that decision is made.
Six breakages were found and
folded into the list above; two were filed as issues: **#4113** (surface guards assert tool
names by string, so they go vacuous on any rename) and **#4114** (`hosted_only` is declared
in the registry and never read).
