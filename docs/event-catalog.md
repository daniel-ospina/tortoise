# Tortoise Event Catalog — graph/claim change events (#432)

> **Source of truth:** `tortoise/shared_state/events.py` — the EventCodec registry
> (`register_claim_event_types`). Keep this table in sync with it by type name.
> Cross-ref: `docs/ONTOLOGY.md` §4.1/§5 (status vocabulary, `:GraphEvent` label).

## Event types

| Type | Version | Emitted by | Payload fields | Producer surface |
|---|---|---|---|---|
| `PointAdded` | 1 | `TortoiseSDK.create_point` (new point only — dedup hits do NOT emit) | `id`, `kind`, `content_hash` | SDK (MCP, REST, local) |
| `OperatorAdded` | 1 | `TortoiseSDK.create_operator` | `id`, `op_type`, `source_id`, `target_ids` | SDK |
| `PointRetracted` | 1 | `TortoiseSDK.retract_point` | `id` | SDK |
| `PointSuperseded` | 1 | `TortoiseSDK.supersede_point` | `id` (old), `new_id` | SDK |
| `PointPromoted` | 1 | `TortoiseSDK.promote_point` (#785) | `point` (full snapshot) | SDK |
| `OperatorPromoted` | 1 | `TortoiseSDK.promote_point` R16 (#785) | `point` (full snapshot), `id` | SDK |
| `OperatorAnnotated` | 1 | `TortoiseSDK.annotate_operator` | `id`, `bias`, `precision`, `consistency`, `directness` | SDK |
| `ObjectSuperseded` | 1 | hosted commit endpoint (`hosted_api._execute_commit_writes` §6b) + capture (`sdk._extract_session_v2`) + eval (`tools/longmem_eval/ingest_v2`) — entity-level supersession records; ALL THREE emit via the shared `commit_ops.apply_supersessions` (id-style kwargs — #1350/#2164/#2193) | `id`, `name`, `supersedes_by`, `session_id`, `evidence` — id-style kwargs. The GraphEvent payload carries all five for every producer; the JSONL line carries them only when the producing SDK is built with an `event_log_path` — no in-tree producer is: hosted (`_make_sdk`) and the eval harness (`tools/longmem_eval/run.py`) pass none, and the only `event_log_path`-configured SDK (offline mining, `tortoise/mining.py`) never emits `ObjectSuperseded` (no supersession path) — the JSONL shape is exercised by out-of-tree clients only | Hosted commit endpoint / SDK capture / eval ingest (all via `commit_ops.apply_supersessions`) |

> ⛔ **`ClaimStateChanged` is NOT an event type** (plan-review P1). Every claim
> transition maps to one of the five concrete types above; **challenged is a
> DERIVED condition** (NAND-operator-edge presence on a live point), not a
> state or event. Content edits via `update_point` (non-status props) emit
> NOTHING.
>
> The EventAPI/CLI/ingest path emits its own legacy events (`PointAdded`,
> `PointRetracted`, `PointsMerged`, `IngestStarted`) to the EventLog JSONL —
> unchanged. Hosted/SDK tenants read the `:GraphEvent` stream below.

## `:GraphEvent` node schema

Stored in the **team's own FalkorDB graph namespace** (the namespace IS the
team partition — **no `team_id` property**, plan-review P2). Nodes carry
**zero relationships** (graph islands — invisible to label-scoped queries,
traversals, EP propagation; see §5 guard).

| Property | Type | Notes |
|---|---|---|
| `seq` | int | Per-graph monotonic, atomic in-graph counter (`GraphEventMeta.last_seq`). Indexed. |
| `ts` | string | ISO8601 UTC (node-level canonical; NOT re-embedded in payload). |
| `type` | string | One of the five registered types above. |
| `payload` | string | JSON of the **bare domain payload** (codec encode/decode wiring deferred to the first upcaster task — node props are canonical for v1). |
| `event_id` | string | Server-side ULID; **unique** (app-side dedup + unique constraint on production FalkorDB). |

`GraphEventMeta` counter node: `{last_seq, first_seq}` — `first_seq` is the
purge watermark (any cursor below it is expired → 410).

## Delivery contract

- **At-least-once.** Clients must be idempotent on replay.
- **Dedup:** `event_id` dedup at the storage layer (app-side pre-check; unique
  constraint on production FalkorDB) + read-path dedup (defense in depth).
- **Cursor:** opaque token — base64url JSON `{v:1, seq:N}` (one format for
  every cursor, incl. the empty graph `{v:1, seq:0}`).
- **Expiry:** a cursor below the `first_seq` watermark → HTTP 410 / SDK
  `ValueError("cursor expired — replay from tail")`; replay by polling with
  `after` omitted.
- **Retention:** `TORTOISE_EVENT_RETENTION_DAYS` (default 30) + per-team size
  cap `TORTOISE_EVENT_MAX_PER_TEAM` (default 500k), purged at boot + interval
  (`TORTOISE_EVENT_RETENTION_INTERVAL`, default 3600s) + lazily gated in polls.

## Surfaces

| Surface | Endpoint / tool | Notes |
|---|---|---|
| REST | `GET /v1/events?after=<cursor>&types=a,b&limit=N` | Team-scoped via auth; 400 malformed/unknown type; 410 expired |
| MCP | `tortoise_events_poll(after, types, limit)` | `readOnly`; stdio + HTTP; maintenance purge gated by interval |

Related: `tortoise_retract_point` (MCP) / `TortoiseSDK.retract_point` — the
tombstone-retraction write that emits `PointRetracted`.
