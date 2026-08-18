# Scoping — #1163: graph-persisted dirty state + ep_version epoch for multi-process EP

Date: 2026-08-18 · Issue: #1163 · Related: #395 (local EP deferral), #6761 (batch I/O), #901 (epic)

## Problem

EP dirty state lives in-process (`TortoiseSDK._dirty_roots`, sdk.py:871). Every HTTP
request builds a fresh request-scoped SDK (`mcp_auth.py:69`), so dirty state never
survives request boundaries → the no-arg local-EP path over HTTP returns
`no_dirty_state_http`. The hosted dream path (hosted_api.py `_enqueue_dream` /
`_dream_worker`) transfers dirty roots through a process-local asyncio queue — lost on
restart, and invisible to any other process. Batch-I/O EP runs (#6761) additionally
have a load→flush race: two processes loading the same state, one flushing, the other
clobbering the first's writes.

## Design

### 1. Graph epoch node — `:EpMeta`
One `:EpMeta` node per graph (namespace-isolated by graph_name): `ep_version` int,
incremented by every `_mark_dirty` call (`MERGE (m:EpMeta) SET m.ep_version =
coalesce(m.ep_version,0)+1`). Non-`:Point` label → invisible to every Point-scoped
query (recall, dream BFS, coverage, fallback snapshot, `_affected_claims`).

### 2. Dirty flags on claims — `n.ep_dirty` + `n.ep_dirty_at`
`_mark_dirty` stamps the mutated points + the reverse-BFS affected claims
`ep_dirty=true, ep_dirty_at=<epoch>` (same queries it already runs). The in-memory
`_dirty_roots` set remains the hot-path mirror; the graph becomes the cross-process
source of truth.

### 3. Hydration on fresh SDK
`_hydrate_dirty_roots()` — `MATCH (n:Point {ep_dirty: true}) RETURN n.id` → union into
`_dirty_roots` when the in-memory set is empty. Called at the top of `dream()`
(before the W1 local-mode auto-select), `_dream_local`, and the `compute_confidence`
no-arg path. A fresh request-scoped SDK thus sees persisted dirty state → local EP
works across processes/requests (acceptance 1).

### 4. Sweep by the dreamer
Converged passes clear both in-memory roots and graph flags (`SET n.ep_dirty = null`
on the affected set). W4/#1243 retention preserved: non-converged runs keep flags
(retry); capped roots clear their flag alongside the in-memory discard.

### 5. ep_version guard (acceptance 2 — stale run cannot clobber)
`TortoiseEP.run` snapshots the graph's `ep_version` before `_load_cache`;
`_flush_cache` re-reads it and SKIPS the flush when it advanced (a concurrent
`_mark_dirty` happened mid-run → cached state is stale). Guarantees ep_alpha/msg_alpha
writes cannot interleave across processes. Single-process operation is unaffected
(no write between load and flush in one run).

### 6. HTTP no-arg gate
`mcp_server.tortoise_compute_confidence` no-arg over HTTP: return `no_dirty_state_http`
only when the graph has NO persisted dirty roots; otherwise fall through to the SDK
no-arg path (which hydrates + runs local EP over the affected closure, #395 AC8
semantics intact).

## Complexity & risk

- **Complexity: standard** — touches sdk.py (`_mark_dirty`, dream adapters, hydration),
  ep.py (flush guard), mcp_server.py (gate). No schema migration: `ep_dirty` /
  `ep_dirty_at` are optional properties; `:EpMeta` is MERGE-created lazily.
- **Risks**: extra queries per `_mark_dirty` (write-path cost, bounded: 2 existing +
  2 new batched queries); EpMeta node polluting unlabelled scans (audited — all hot
  paths use `:Point`); hydration changing dream mode auto-select (desired — dirty
  roots present → local, W1 rule).

## Tests

- New: `tests/test_ep_dirty_persist.py` — two-SDK multi-process simulation (write in
  A, dream/EP in B sees dirty state; epoch increments on writes; stale-run flush
  guard; sweep clears graph flags; hydration on fresh SDK).
- Regression: `tests/test_ep_local_395.py`, `tests/test_sdk_ep.py` (local-EP
  semantics intact).
