# Tortoise Event Catalog — graph/claim change events (#432)

> **Source of truth:** `tortoise/shared_state/events.py` — the EventCodec registry
> (`register_claim_event_types`). Keep this table in sync with it by type name.
> Cross-ref: `docs/ONTOLOGY.md` §4.1/§5 (status vocabulary, `:GraphEvent` label).

## Event types

| Type | Version | Emitted by | Payload fields | Producer surface |
|---|---|---|---|---|
| `PointAdded` | 1 | `TortoiseSDK.create_point` (new point only — dedup hits do NOT emit); SDK `capture_session` / `hosted_api` capture turn loop (#3947 — one per `{session_id}_t{i}` turn Point, `is_episodic=true`) | `id`, `kind`, `content_hash` (both producers put the hash on the PAYLOAD, matching `create_point`); the capture turn adds the **envelope** key `contains_session` (the session-container link the replay fold restores — ontology §4.5), plus a `point` snapshot carrying `content`/`pointKind`/`speaker`/`is_episodic`/`status`/`createdAt` and — since #5004 — `embedding` plus, on a creating record, its identity keys (see the JSONL shape notes below). `content_hash` is NOT in the `point` snapshot: `_emit_event` strips it (`content_hash` is derived — the replay recomputes it in `_upsert_point_props`, #2795) | SDK (MCP, REST, local) |
| `OperatorAdded` | 1 | `TortoiseSDK.create_operator` | `id`, `op_type`, `source_id`, `target_ids` | SDK |
| `PointRetracted` | 1 | `TortoiseSDK.retract_point` (**tombstone** — `status='retracted'`, node kept; the JSONL line is what makes it durable) AND `TortoiseSDK.delete_point` / `TortoiseSDK.delete` (**hard delete** — emitted as a **`:GraphEvent`-only subscriber row**, no JSONL line; the durable record for that delete is the `EntityMutated` row below, because one event type must not carry two live end-states, #3300) | `id`, plus `deleted: true` on the HARD-delete producer only (#2353 — written UNCONDITIONALLY, because the marker records what the write path did and a reader-gated one mislabels every unswitched writer's deletes) | SDK |
| `EntityMutated` | 1 | The ONE write-surface record for durable entity mutation — `TortoiseSDK._update_entity` (`restatus` when `status` is written — on a `:Point`-carrying node the value is first validated against `POINT_STATUS_VALUES` and the record is written against the `:Point` label so it folds, #3311; otherwise `revise`), `TortoiseSDK._delete_entity` and `delete_point` (`delete`). #1423 adds the Subject lane's producer — `commit_ops._apply_subject_supersession` (`supersede`: `status`/`supersededBy`/`supersededAt` on a `:Subject`; a Subject `status` written through the generic surface is validated against `SUBJECT_STATUS_VALUES`, ONTOLOGY §4.2, and refused otherwise). Designed on #3299; the op set was extended by #3312 (unjournaled update), #3300 (Point delete) and #1423 (Subject supersede). **`rename` is fold-supported but NOT yet produced** (and the fold applies `state`, so a rename record must carry the new name as `state["name"]` — the top-level `name` field is currently unread) — a `name`-bearing write withholds its record and warns, because journalling it drops a legacy name-keyed `ObjectSuperseded` on replay (#3377 returned to open; #4769 lands rename journalling together with the structural sweep-ordering fix). Fold: `projection._fold_entity_mutation`, dispatching on `op` | `id`, `op`, `label`, plus `state` (the mutation's OWN keys — never a `properties(n)` snapshot — carrying the values the graph STORED; **absent for `op="delete"`**) and `name` (rename only) | SDK — **JSONL ONLY**: deliberately NOT in `_GRAPH_EVENT_TYPES`, so it rides the rebuild journal and not the `:GraphEvent` store |
| `PointSuperseded` | 1 | `TortoiseSDK.supersede_point` | `id` (old), `new_id` | SDK |
| `PointInvalidated` | 1 | `TortoiseSDK.invalidate_point` (#2488) | `id`, `corrected_by` | SDK |
| `PointPromoted` | 1 | `TortoiseSDK.promote_point` (#785) | `point` (full snapshot) | SDK |
| `OperatorPromoted` | 1 | `TortoiseSDK.promote_point` R16 (#785) | `point` (full snapshot), `id` | SDK |
| `OperatorAnnotated` | 1 | `TortoiseSDK.annotate_operator` | `id`, `bias`, `precision`, `consistency`, `directness` | SDK |
| `PointRevised` | 1 | `TortoiseSDK.update_point(**props)` — the JSONL journal for every content/validity/status revision (#548). **This is the ONE registered type whose emit path is conditional:** it reaches the `:GraphEvent` store only while `TORTOISE_CHANGE_FEED` is truthy (#2353), because that store's default emit set is the claim-TRANSITION contract (`sdk._GRAPH_EVENT_TYPES`, which is NOT widened by this type — see the ⚠️ note below) | payload = the caller's props VERBATIM plus `id` (a store revision carries the same bare domain payload the JSONL line does: `new_content`, `validTo`, `status`, `annotator_*`, caller extras…) | SDK — JSONL always; `:GraphEvent` only under the switch |
| `ObjectSuperseded` | 1 | hosted commit endpoint (`hosted_api._execute_commit_writes` §6b) + capture (`sdk._extract_session_v2`) + eval (`tools/longmem_eval/ingest_v2`) — entity-level supersession records; ALL THREE emit via the shared `commit_ops.apply_supersessions` (id-style kwargs — #1350/#2164/#2193) | `id`, `name`, `supersedes_by`, `session_id`, `evidence` — id-style kwargs. The GraphEvent payload carries all five for every producer; the JSONL line carries them only when the producing SDK is built with an `event_log_path`. In-tree JSONL exercisers build `event_log_path`-configured SDKs and drive `commit_ops.apply_supersessions` (which routes through `sdk._emit_event`): `tests/test_capture_session.py` (`test_apply_supersessions_legacy_idless_object_reaches_jsonl`) plus the #2194 suite (`test_object_registered_journal.py`) — an in-tree exerciser therefore predates #2194. The other `event_log_path`-configured in-tree SDK — offline mining, `tortoise/mining.py` — never emits `ObjectSuperseded` (no supersession path: Object writes are `ObjectRegistered` via `api.add_object`) | Hosted commit endpoint / SDK capture / eval ingest (all via `commit_ops.apply_supersessions`) |

> ⛔ **`ClaimStateChanged` is NOT an event type** (plan-review P1). Every claim
> transition maps to one of the eleven concrete types above; **challenged is a
> DERIVED condition** (NAND-operator-edge presence on a live point), not a
> state or event. Content edits via `update_point` (non-status props) emit
> NOTHING to the `:GraphEvent` store **by default**.
>
> ⚠️ **`PointRevised` is a JSONL-only record BY DEFAULT, and that is a recorded
> decision, not an accident** — `docs/plans/2026-08-08-432-subscriptions-plan.md`
> §Task 3 ("Content edits (`update_point` non-status props) emit NOTHING
> (plan-review P1) — the emit set is exactly these five hooks"), pinned by
> `tests/test_event_store.py::test_content_edit_emits_nothing`. The same holds
> for `EntityMutated` (above), `SourceVersioned`, `EntityLinked`,
> `SessionRecorded`/`SessionDeleted` and the other journal-carrier records.
> **#2353 is the one exception and it is OPT-IN:** with `TORTOISE_CHANGE_FEED`
> truthy (1/true/yes/on; unset or 0 means OFF) `PointRevised` ALSO reaches the
> `:GraphEvent` store, so `poll_events` can report before→after for content,
> validity and status via the semantic change feed (`tortoise/change_feed.py`,
> an off-by-default `changes` response field — see
> `docs/product/mcp-sdk-surface.md` → *Recorded response fields*). Off, the
> emitted set above is unchanged. Widening the DEFAULT emit set would reverse the
> plan-review ruling; that is an owner decision, not a lane's.
>
> Registration is the other half of that exception, and it is NOT conditional:
> `PointRevised` is the twelfth member of `CLAIM_EVENT_TYPES`, which is the
> READ-side catalog (`poll_events`' `types=` filter and
> `webhook_delivery._validated_types` consult it — never `sdk._GRAPH_EVENT_TYPES`).
> Registration is what makes `types=["PointRevised"]` and a `PointRevised` webhook
> subscription legal instead of a hard `ValueError`, so the store can never hold a
> row a caller cannot name (#2353 review P2). The eleven unconditional members are
> exactly `sdk._GRAPH_EVENT_TYPES`.
>
> **Two more notes on what the feed reports, both under the same switch.**
> (1) `PointRetracted` has TWO producers with different end-states (#3300):
> `retract_point`'s tombstone (`status='retracted'`, terminal-guarded) and
> `delete_point` / the capture stale-turn sweep's HARD delete (node destroyed,
> no guard). Their payloads were identical, so the feed reported the tombstone
> reading for a destroyed node. The hard-delete producer adds `deleted: true`
> to its payload, and the fold reports that producer as `change: "deleted"`
> with NO `status` and NO precondition; without the marker the row is the
> tombstone, the type's remaining producer. The marker is written
> UNCONDITIONALLY (#2353 review P1): gating it on the READING
> process's switch folded an unswitched writer's hard deletes as tombstones.
> Rows written before the marker existed carry nothing that separates the two
> producers and are read as tombstones — a stated residual.
> (2) `PointPromoted`'s reported `precondition` is the UNION both producers
> enforce — `status` in `[draft, null]` — because capture auto-promotion admits
> an UNSET status (`status IS NULL OR status = 'draft'`) and emits the same type,
> so `draft` alone would overstate the capture leg (#2353 review P2).
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

- **The seal annotation (`__TornTailSealed__`, #5917) — an annotation, not a
  record.** When `EventLog.append` finds the journal ending on a fragment — an
  unterminated line that is not valid JSON (a writer killed mid-append), or a
  terminated line that is not valid JSON — it terminates and/or marks the
  fragment before writing the new record, so the new record starts on a line of
  its own while the fragment still reaches the torn-tail classifier. `read_all`
  never returns it and it shifts no record index. It is deliberately **not
  JSON**: every record begins `{"`, so a sentinel no record can begin with keeps
  a *torn seal* unambiguous — a torn record fragment is still counted and
  classified rather than being mistaken for an annotation. The journal's grammar
  already admits non-JSON lines (a torn fragment is one).

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

- **`SourceVersioned`** (#5024, T6; the WINDOW half in #3644) — the `:Source` **version transition**. A
  re-fetched source whose `contentHash` differs is a *new version of the same
  identity*, never an in-place edit (`ONTOLOGY.md` v3.15 §4.6 *Versioning*;
  `STORAGE-ARCHITECTURE.md` §9.6). Fields: `id` and `url` (the identity),
  `contentHash` (the NEW version), **`previousContentHash`** (the superseded
  one — this is what makes the prior version addressable after the single
  `:Source` node has moved on), **`validFrom`** (the new version's valid-time
  start, and the node's new window start) and **`previousValidTo`** (the prior
  version's window close — equal to `validFrom` by construction: the same
  instant mints both, which is §4.6's *"the journal record that opens a new
  version closes the old one in the same act"*), plus the usual
  `sourceKind`/`title`/`ingestedAt`/`updatedAt`/extras. There is ONE node per
  `url`, so the prior version's window is **a journal fact**, never a second
  node: `previousValidTo` is therefore a record-only key (`_SOURCE_HANDLED`
  keeps it off the node).
  The transition instant is **not** a separate key: the
  payload's own `updatedAt` (minted once by `create_source`, so the live node
  and the record cannot disagree) is it, and the fold reads exactly that —
  `validFrom` is the SAME minted instant, i.e. one clock read under two names,
  not a second derivation. The
  record carries **no** recomputed ordinal — `version` is reproduced on replay
  through the same hash-diff gate the live write used, so recording it too
  would be a second derivation that can disagree. Folded by
  `FalkorProjection._fold_source_versioned`, which **delegates to
  `_upsert_source`** — the LIVE writer — so apply/replay parity holds by
  construction rather than by a second, hand-maintained clause list (the first
  cut kept its own clauses and drifted in `urlAliases`/`sourcePath`/
  `canonicalUrl`, each a live != replay divergence). **JSONL-only** (not
  in `_GRAPH_EVENT_TYPES`), in `_NO_POINT_FOLD`, and folded in **both**
  `apply()` and `rebuild_all` pass 1b. `create_source`'s cadence is
  per-**outcome**: this record for a real hash transition, `SourceCreated` for
  a create or a JOINT-E2E stub completion, and **nothing at all** for a
  re-check that found what we already hold (§9.6's cost bound — a version is
  "three timestamps and a hash", so a no-op re-fetch must not be one). A
  hashless re-check counts: a hashless create stores `contentHash = ''`, so
  "no stored hash" alone must not select the create arm, or every re-ingest of
  a hashless source would append a record.
- **The `sourceVersionTransit` carrier on a Point snapshot** (#5256) — a Point created against a
  `:Source` with a non-blank `contentHash` records the version it was read from as a list of
  `[<raw extractedFrom ref>, <contentHash>]` pairs. It is the *transit* for the edge-authoritative
  `r.sourceVersion` (stamped `ON CREATE` only — a re-link never advances it), so the replay re-stamps
  the edge from the snapshot instead of re-reading the Source: that read is unjournalled (#5024) and
  would record a version the Point was never read from. The key is the **raw** ref — the
  journal-stable spelling — never a live-time resolution of it. **Honest-absent:** no Source, or a
  Source whose hash is blank (empty or whitespace-only) or non-string, writes **no key at all** (never `''`, never `[]`).

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
  plus `harness` / `actor_user_id` when set, and `capture_lane` when a
  journaling producer sets it (`_fold_session_recorded` already coalesces it;
  no caller passes `capture_lane` to a journaling writer today, so nothing
  emits it yet);
  (2) a trailing record written by
  `sdk._write_capture_turns` right after its batched turn statement, carrying
  `capture_redactions` (#4911); (3) after the entity-linking pass, carrying
  `entity_links_attempted` / `entity_links_created`; (4) the final, trailing
  record written right after the live `SET s.capture_ok /
  s.capture_extractor`, carrying `capture_ok` / `capture_extractor`.
  Folded by
  `FalkorProjection._fold_session_recorded` as an idempotent MERGE keyed on
  `id` that always sets `is_episodic=true`, coalesce-preserving `created_at` /
  `actor_user_id` / `capture_lane` (first writer wins) and taking `turn_count`, `harness`,
  `capture_redactions`, `entity_links_attempted`, `entity_links_created`,
  `capture_ok` and
  `capture_extractor` from the latest record (last writer wins). Each later
  record exists so a field is durable on the `apply()`-based engines too (the
  opening record is emitted before any result is known and cannot carry them; a
  null `capture_ok` would otherwise read as the legacy "presumed captured"
  case at the #2335 retry gate).
- **`SessionDeleted`** (#7951) — the `:Session` node's hard-delete carrier,
  emitted by `hosted_api`'s `DELETE /v1/sessions/{id}`
  (`_delete_session_sync`). JSONL-only (NOT in `_GRAPH_EVENT_TYPES`): fields
  are `id` (the session id). The session delete is a raw `DETACH DELETE` and
  `Session` is not a canonical entity label (`_CANONICAL_ENTITY_ID_PROPS`),
  so no `EntityMutated` record can express the removal — that fold would fall
  back to the id-wide canonical delete and leave the Session standing while
  `SessionRecorded` re-materialised it. Folded idempotently by
  `FalkorProjection._fold_session_deleted` (a 0-row `DETACH DELETE` is the
  record's END STATE, never a fold-miss). The fold removes the session's
  agentSession `:Source` stub (`url = session:{id}`) in the SAME step, because
  that node has TWO journal re-creation paths the container anchor does not:
  a later `SessionRecorded` re-creates the container, and `_link_source`
  mints the Source from ANY `PointAdded` carrying `extractedFrom=session:{id}`
  (which is why a post-delete write naming the session re-mints it live).
  Only `rebuild_all` DEFERS this fold: its pass 2 re-derives edges from every
  `PointAdded` — including ones a later record hard-deletes — so its
  `_link_source` / `_link_session` side effects MERGE back what pass 1b
  removed, and the deferred re-apply runs after every pass. The `apply()`-based
  engines (`rebuild` / `recover_from_log` / `backup.restore`) fold the record
  INLINE at its own journal seq, so a later record's own fold re-creates what
  the delete removed. The deferred re-apply is therefore gated PER NODE on the
  journal's own re-materialisation anchors — the last `SessionRecorded` **or**
  `contains_session`-carrying `PointAdded` for the container, and the last
  `extractedFrom=session:{id}` `PointAdded` / `SourceCreated` / `SourceVersioned`
  for the Source — so the two nodes can neither drift from each other nor be
  deleted after a later record re-created them, and the two engines reach ONE
  verdict on the same journal (#7951 P1-a). The session's `CONTAINS` Points
  and its `sessionCaptured` Event(s) are journaled separately as
  `EntityMutated` op=delete records by the same delete site and by the
  delete-during-capture sweep (`_sweep_orphaned_writes`).

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
`contains_session` turn link exists before the fold. `SessionDeleted` is
carried with its journal seq into a deferred list and re-applied AFTER every
replay pass, gated on a later same-id `SessionRecorded` (a re-capture
supersedes the delete, so its container and Source must survive): pass 2
re-derives edges from every `PointAdded`, and its `_link_source` /
`_link_session` side effects MERGE the deleted session's `:Source` /
`:Session` back — undoing pass 1b's `SessionDeleted` fold for the deleted
Points' payloads (#7951).

**No down-version guarantee for new folded record types.** A binary that
predates #3585, rebuilding a journal written by a newer one, warns that the
record type is unrecognized and skips it, and silently drops unknown
`PointRevised` extras. A binary carrying #3585 or later instead fails the
replay loudly (R8) on the graph-backed rebuild/recover engines: the type is
recorded as a non-folded event and the run refuses rather than returning an
incomplete graph. The three documented `EXEMPT_SHAPES` are the only non-folded
records that are recorded WITHOUT refusing the run. The in-memory index alone
opens no run boundary and warns
without refusing. The
rebuild path has no pre-wipe allowlist analogous to
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
| `type` | string | One of the eleven `sdk._GRAPH_EVENT_TYPES` members above — plus `PointRevised` while `TORTOISE_CHANGE_FEED` is truthy (the twelfth registered `CLAIM_EVENT_TYPES` member; the emit gate is `sdk.py::_emit_event`). |
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
