# Tortoise Event Catalog — graph/claim change events (#432)

> **Source of truth:** `tortoise/shared_state/events.py` — the EventCodec registry
> (`register_claim_event_types`). Keep this table in sync with it by type name.
> Cross-ref: `docs/ONTOLOGY.md` §4.1/§5 (status vocabulary, `:GraphEvent` label).

## Event types

| Type | Version | Emitted by | Payload fields | Producer surface |
|---|---|---|---|---|
| `PointAdded` | 1 | `TortoiseSDK.create_point` (new point only — dedup hits do NOT emit); SDK `capture_session` / `hosted_api` capture turn loop (#3947 — one per `{session_id}_t{i}` turn Point, `is_episodic=true`) | `id`, `kind`, `content_hash` (both producers put the hash on the PAYLOAD, matching `create_point`); the capture turn adds the **envelope** key `contains_session` (the session-container link the replay fold restores — ontology §4.5), plus a `point` snapshot carrying `content`/`pointKind`/`speaker`/`is_episodic`/`status`/`createdAt` and — since #5004 — `embedding` plus, on a creating record, its identity keys (see the JSONL shape notes below). `content_hash` is NOT in the `point` snapshot: `_emit_event` strips it (`content_hash` is derived — the replay recomputes it in `_upsert_point_props`, #2795) | SDK (MCP, REST, local) |
| `OperatorAdded` | 1 | `TortoiseSDK.create_operator` | `id`, `op_type`, `source_id`, `target_ids` | SDK |
| `PointRetracted` | 1 | `TortoiseSDK.retract_point` (**tombstone** — `status='retracted'`, node kept; the JSONL line is what makes it durable) AND `TortoiseSDK.delete_point` / `TortoiseSDK.delete` (**hard delete** — emitted as a **`:GraphEvent`-only subscriber row**, no JSONL line; the durable record for that delete is the `EntityMutated` row below, because one event type must not carry two live end-states, #3300) | `id` | SDK |
| `EntityMutated` | 1 | The ONE write-surface record for durable entity mutation — `TortoiseSDK._update_entity` (non-`Point` labels: `restatus` when `status` is written, else `revise`), `TortoiseSDK._delete_entity` and `delete_point` (`delete`). Designed on #3299; the op set was extended by #3312 (unjournaled update) and #3300 (Point delete). **`rename` is fold-supported but NOT yet produced** (and the fold applies `state`, so a rename record must carry the new name as `state["name"]` — the top-level `name` field is currently unread) — a `name`-bearing write withholds its record and warns, because journalling it drops a legacy name-keyed `ObjectSuperseded` on replay (#3377 returned to open; #4769 lands rename journalling together with the structural sweep-ordering fix). Fold: `projection._fold_entity_mutation`, dispatching on `op` | `id`, `op`, `label`, plus `state` (the mutation's OWN keys — never a `properties(n)` snapshot — carrying the values the graph STORED; **absent for `op="delete"`**) and `name` (rename only) | SDK — **JSONL ONLY**: deliberately NOT in `_GRAPH_EVENT_TYPES`, so it rides the rebuild journal and not the `:GraphEvent` store |
| `PointSuperseded` | 1 | `TortoiseSDK.supersede_point` | `id` (old), `new_id` | SDK |
| `PointInvalidated` | 1 | `TortoiseSDK.invalidate_point` (#2488) | `id`, `corrected_by` | SDK |
| `PointPromoted` | 1 | `TortoiseSDK.promote_point` (#785) | `point` (full snapshot) | SDK |
| `OperatorPromoted` | 1 | `TortoiseSDK.promote_point` R16 (#785) | `point` (full snapshot), `id` | SDK |
| `OperatorAnnotated` | 1 | `TortoiseSDK.annotate_operator` | `id`, `bias`, `precision`, `consistency`, `directness` | SDK |
| `ObjectSuperseded` | 1 | hosted commit endpoint (`hosted_api._execute_commit_writes` §6b) + capture (`sdk._extract_session_v2`) + eval (`tools/longmem_eval/ingest_v2`) — entity-level supersession records; ALL THREE emit via the shared `commit_ops.apply_supersessions` (id-style kwargs — #1350/#2164/#2193) | `id`, `name`, `supersedes_by`, `session_id`, `evidence` — id-style kwargs. The GraphEvent payload carries all five for every producer; the JSONL line carries them only when the producing SDK is built with an `event_log_path`. In-tree JSONL exercisers build `event_log_path`-configured SDKs and drive `commit_ops.apply_supersessions` (which routes through `sdk._emit_event`): `tests/test_capture_session.py` (`test_apply_supersessions_legacy_idless_object_reaches_jsonl`) plus the #2194 suite (`test_object_registered_journal.py`) — an in-tree exerciser therefore predates #2194. The other `event_log_path`-configured in-tree SDK — offline mining, `tortoise/mining.py` — never emits `ObjectSuperseded` (no supersession path: Object writes are `ObjectRegistered` via `api.add_object`) | Hosted commit endpoint / SDK capture / eval ingest (all via `commit_ops.apply_supersessions`) |

> ⛔ **`ClaimStateChanged` is NOT an event type** (plan-review P1). Every claim
> transition maps to one of the eleven concrete types above; **challenged is a
> DERIVED condition** (NAND-operator-edge presence on a live point), not a
> state or event. Content edits via `update_point` (non-status props) emit
> NOTHING.
>
> The EventAPI/CLI/ingest path emits its own legacy events (`PointAdded`,
> `PointRetracted`, `PointsMerged`, `IngestStarted`) to the EventLog JSONL —
> unchanged. Hosted/SDK tenants read the `:GraphEvent` stream below.

### JSONL rebuild-journal record shapes (durability, not the `:GraphEvent` stream)

The table above documents the `:GraphEvent` **payload**. The JSONL rebuild
journal that `rebuild_all` replays is a *second*, differently-shaped store:
`_emit_event` writes the envelope (`event_id`/`ts`/`type`/`initiated_by`/
`projection_version`) plus the record's own fields. Several folds carry props
that the payload does not name:

- **The Point-snapshot folds (`PointAdded`, `PointPromoted`) and the capture
  turn** (#5004) — the `point` snapshot now also carries the embedding, which
  is a *node* property and stays one: `embedding` (the vector as stored, or an
  explicit `null`), plus — on a **creating** record only — the identity the
  vector was computed under, `embedding_model` / `embedding_revision` /
  `embedding_text_hash`, and (turn records that preserved an older vector)
  `embedding_preserved`. The identity keys describe the record; they are never
  node properties (the fold's `_POINT_HANDLED` drops them). A re-emitted
  snapshot (`PointPromoted`) carries the vector with **no** identity, and the
  `embedding_verbatim` marker rides the snapshot — the replay SETs it as a
  node property to reproduce the graph-only restore. **Presence is
  ownership:** a record that owns the field writes the key (vector or explicit
  `null`), and the replay restores / clears / leaves — it never re-encodes; a
  record with the key ABSENT is legacy (strip-era) and recomputes. The paths
  still outside the rule are enumerated — and must stay enumerated — in
  `docs/durability-posture.md` → *Derived properties that are STORED, not
  recomputed*; do not restate the list here (it has drifted once already).

- **`OperatorAnnotated`** (#3689) — the JSONL line carries `id` plus the
  **canonical** `annotator_bias`/`annotator_precision`/`annotator_consistency`/
  `annotator_directness` (the payload above keeps the SHORT names
  `bias`/`precision`/`consistency`/`directness` for the `:GraphEvent`
  contract). The fold accepts either spelling (`_annotator_dims(aliases=True)`),
  but an SDK-produced record always carries the long names.
- **`PointRevised`** — `update_point(**props)` journals the caller's props
  VERBATIM as extras, so an `annotator_*` key here is a node property of that
  exact name (never aliased).
- **`EntityLinked`** (#3664) — the capture entity-attachment record, written by
  `session_link.link_entity` **only when the SDK has an `event_log_path`**
  (JSONL-only: it is NOT in `_GRAPH_EVENT_TYPES`). Fields: `id` (source id;
  also carried as `source_id`), `source_label`, `source_id`, `target_label`,
  `target_id`, `edge_type`. Folded by `FalkorProjection._fold_entity_linked`
  as an idempotent MERGE of the flat logical endpoints; `edge_type` and both
  labels are validated against a frozen vocabulary (an unknown/malformed value
  is a 0-row NO-OP, never interpolated into Cypher).
- **`SessionRecorded`** (#3664) — the `:Session` node's journal carrier (the
  live capture MERGE is a raw write). Four are emitted per capture, in this
  order: (1) the opening record — `{id, created_at, turn_count, is_episodic}`
  plus `harness` / `actor_user_id` when set; (2) a trailing record written by
  `sdk._write_capture_turns` right after its batched turn statement, carrying
  `capture_redactions` (#4911); (3) after the entity-linking pass, carrying
  `entity_links_attempted` / `entity_links_created`; (4) the final, trailing
  record written right after the live `SET s.capture_ok /
  s.capture_extractor`, carrying `capture_ok` / `capture_extractor`.
  Folded by
  `FalkorProjection._fold_session_recorded` as an idempotent MERGE keyed on
  `id` that always sets `is_episodic=true`, coalesce-preserving `created_at` /
  `actor_user_id` (first writer wins) and taking `turn_count`, `harness`,
  `capture_redactions`, `entity_links_attempted`, `entity_links_created`,
  `capture_ok` and
  `capture_extractor` from the latest record (last writer wins). Each later
  record exists so a field is durable on the `apply()`-based engines too (the
  opening record is emitted before any result is known and cannot carry them; a
  null `capture_ok` would otherwise read as the legacy "presumed captured"
  case at the #2335 retry gate).

**Replay ordering.** `EntityLinked` is deferred to a trailing sweep by all
four whole-journal replay engines (`rebuild_all`, and the `apply()`-based
`rebuild` / `recover_from_log` / `backup.restore`'s JSONL fallback), so a link
whose endpoint is created LATER in the journal still folds. A link whose
endpoint was HARD-DELETED after it is skipped instead (the record itself
carries no `seq`; each engine pairs it with its journal position — the
`(journal_seq, record)` index over its events list — and the hard-delete
boundary is compared against that position), so a same-id re-creation does
not resurrect the deleted link. On `rebuild_all` the
sweep runs AFTER pass 2, so a `:Session` source recreated from a
`contains_session` turn link exists before the fold.

**No down-version guarantee for new folded record types.** An older binary
rebuilding a journal written by a newer one warns `unrecognized event type 'X'
— skipped` for a new type it does not know, and silently drops unknown
`PointRevised` extras. The rebuild path has no pre-wipe allowlist analogous to
`_assert_episodic_points_recreatable`; forward-only evolution of the JSONL
record vocabulary is a known limitation, not a supported downgrade path.

## `:GraphEvent` node schema

Stored in the **team's own FalkorDB graph namespace** (the namespace IS the
team partition — **no `org_id` property**, plan-review P2). Nodes carry
**zero relationships** (graph islands — invisible to label-scoped queries,
traversals, EP propagation; see §5 guard).

| Property | Type | Notes |
|---|---|---|
| `seq` | int | Per-graph monotonic, atomic in-graph counter (`GraphEventMeta.last_seq`). Indexed. |
| `ts` | string | ISO8601 UTC (node-level canonical; NOT re-embedded in payload). |
| `type` | string | One of the eleven registered types above. |
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
