---
title: "Ingest Contract — tortoise_ingest / sdk.ingest"
type: engineering
domain: platform
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-ingest, tortoise-mcp
---

# Ingest Contract (`tortoise_ingest` / `sdk.ingest`)

The ingest surface writes a whole **bundle** — points, entities, sources, and
the connections between them — in one coherent, idempotent call. This document
is the granular customer contract for that surface: bundle shape, worked
examples with the exact response shape, the full error surface with the retry
action per error class, quota semantics, and what to do when you ingest the
wrong thing.

**How to read this doc.** The ingest surface is extended by epic #902 (this
release): the response gains `batch_id` and `warnings`, the tool gains the
`promotion_policy` parameter, plain IMPL/NAND connections become
**operator-less direct edges**, and validation moves to a deterministic
zero-mutation Phase-1 pass. Where a behavior is pinned in the epic plan but
not yet visible in the shipped code at the time of writing, this doc says
**planned:** explicitly. Everything else is shipped behavior you can rely on.

Related docs:

- [Hosted quickstart](quickstart-cloud.md) — sign up, API key, MCP registration.
- [Self-hosted quickstart](quickstart-selfhosted.md) — Docker / embedded, `TORTOISE_DB_URI`, transport modes.
- [Ontology](ONTOLOGY.md) — point kinds, operator semantics, the edge vocabulary.
- [Tortoise skill — how to use Tortoise](../skills/how-to-use-tortoise/SKILL.md) — agent-facing ingest guidance.

---

## 1. Quickstart

Get credentials and a working transport first:

- **Hosted (recommended):** follow [quickstart-cloud.md](quickstart-cloud.md).
  You get a `tt_` team API key, register the MCP server at
  `https://api.premiselabs.co/mcp/` with `Authorization: Bearer tt_<key>`
  (streamable-http), and the CLI via `tortoise init --api-key tt_<key>`.
- **Self-hosted:** follow [quickstart-selfhosted.md](quickstart-selfhosted.md).
  The daemon reads `TORTOISE_DB_URI` (e.g.
  `docker://:falkordb@localhost:6379/tortoise`) or `TORTOISE_DB_PATH`
  (embedded, single-writer eval only); MCP clients register a stdio or
  streamable-http server against `tortoise.mcp_server` / `tortoise serve`.

Environment variables that matter for ingest:

| Variable | Purpose |
|---|---|
| `TORTOISE_API_KEY` | Hosted/self-hosted auth. **Never set for stdio local dev** — stdio cannot carry auth tokens and fails closed (see [§10 Transport & auth modes](#10-transport--auth-modes)). |
| `tt_<key>` team keys | Created via `tortoise team keys create` / `POST /v1/team/keys`; the plaintext is shown once. |
| `TORTOISE_DB_URI` | Self-hosted DB target (`docker://` or `bolt://`). Required for `tortoise serve`; unset → server refuses to start. |

Once connected, you have `tortoise_ingest` (MCP) and `sdk.ingest` (Python)
with identical contracts.

---

## 2. Worked first bundle

### 2.1 The bundle shape

```json
{
  "points": [
    {"ref": "pA", "kind": "statement", "content": "A implies B"},
    {"ref": "pB", "kind": "statement", "content": "B"}
  ],
  "entities": [
    {"type": "subject", "name": "ACME Corp"},
    {"type": "event", "name": "Deploy #4812", "eventKind": "occurrence"}
  ],
  "sources": [
    {"ref": "src1", "url": "https://example.com/report.pdf", "sourceKind": "report"}
  ],
  "connections": [
    {"from": "pA", "to": "pB", "operator": "IMPL", "label": "supports",
     "confidence": 0.9, "direction": "unidirectional"},
    {"from": "pA", "to": "src1", "relation": "extractedFrom"}
  ]
}
```

Rules that hold regardless of granularity or policy:

- `ref` is a bundle-local addressing label usable in any connection's
  `from`/`to` (and in entity props like `authoredBy`/`aboutPoint`, and point
  `extractedFrom`) instead of a created id. It must be unique within the
  bundle and is **never stored** as a node property.
- Connections carry **exactly one** of `relation` (plain structural edge) or
  `operator` (epistemic mechanism). `operator` is `IMPL` or `NAND`
  (plus part/whole types), optionally with `label`, `direction`,
  `confidence`, `weight`, `mitigation`, or `reify` (see [§13](#13-acceptance-surface-breaks--migration-lines)).
- A plain `operator` connection (no `mitigation`/`reify`) takes a **singular**
  `to`. **planned:** multi-item `to` on a plain IMPL/NAND connection becomes a
  Phase-1 violation; multi-input semantics stay reachable via the operator
  anchor (`mitigation` or `reify: true`).
- Created points default to `status: "draft"` under the gated policy (see
  [§11 Promotion & EP](#11-promotion--ep)). Pass `status: "live"` only with
  `promotion_policy: "auto"` — under gated, an explicit `status:"live"` is a
  violation (no bypass of the gated contract).

### 2.2 Bulk + gated (the default) — expected response

```json
{
  "granularity": "bulk",
  "batch_id": "01J8…",
  "created": {"points": 2, "entities": 2, "sources": 1, "connections": 2},
  "deduped": {"points": 0, "entities": 0, "sources": 0, "connections": 0},
  "ids": {
    "points": ["01J8…", "01J8…"],
    "entities": ["…", "…"],
    "sources": ["…"],
    "connections": [
      {"direct_edge": "IMPL", "from": "01J8…", "to": "01J8…"},
      {"relation": "extractedFrom", "from": "01J8…", "to": "…"}
    ],
    "refs": {"pA": "01J8…", "pB": "01J8…", "src1": "…"}
  },
  "nudges": [],
  "warnings": []
}
```

Key points:

- **`batch_id`** (planned — lands with this release): a deterministic,
  content-derived ULID-shaped id for the whole logical bundle. Identical
  content ⇒ identical `batch_id` across retries. `batch_id` never appears in
  bundle items (it is server-managed — a bundle carrying it is a violation).
- **`created` / `deduped`** each count `{points, entities, sources, connections}`.
  Re-submitting the identical bundle returns the same ids with everything in
  `deduped`.
- **`ids["connections"]`** — the connection descriptor contract (shipped
  today: operator-requiring connections append the bare operator-id string;
  relation connections append `{relation, from, to, deduped}`). **This
  release** re-routes plain IMPL/NAND connections to operator-less **direct
  edges** with the stable descriptor `{direct_edge, from, to}` — `deduped` is
  deliberately not part of it (reported in the aggregate) so identical
  resubmissions compare equal. The operator path (`mitigation`/`reify:true`)
  keeps the bare operator-id string.
- `nudges` is advisory, never enforced; `warnings` names divergences the
  engine noticed (see [§3.2](#32-warnings--the-eleven-keys)).

### 2.3 Both granularities

`granularity="granular"` runs the same Phase-1 validation and the same
write passes, and adds a per-item `results` array — one entry per bundle item
with its primitive result and a `deduped` flag:

```json
{
  "granularity": "granular",
  "batch_id": "01J8…",
  "created": {"points": 1, "entities": 0, "sources": 0, "connections": 0},
  "deduped": {"points": 0, "entities": 0, "sources": 0, "connections": 0},
  "ids": {"points": ["01J8…"], "entities": [], "sources": [], "connections": [], "refs": {}},
  "nudges": [],
  "warnings": [],
  "results": [
    {"section": "points", "index": 0, "ref": "pA", "item": {"tags": ["x"]},
     "result": {"id": "01J8…", "pointKind": "statement", "status": "draft", "content": "A implies B"},
     "deduped": false}
  ]
}
```

Granular `results[].item` returns the item's remaining props after the
structural keys (`ref`/`kind`/`content`/`type`/`name`/`url`/`sourceKind`)
are consumed — empty for a bare statement point.

Granular `results[].result` shapes per connection route:

| Route | `results[].result` |
|---|---|
| Operator route (mitigation / `reify:true`) | `{"operator_id": "<id>", "deduped": bool}` |
| Direct-edge route (plain IMPL/NAND) | `{"direct_edge": "IMPL", "from": "<pid>", "to": "<pid>", "deduped": bool}` |
| Relation route | `{"relation": "<pred>", "from": "<id>", "to": "<id>", "deduped": bool}` |

### 2.4 Both policies

`promotion_policy` is orthogonal to granularity (both modes honor the same
policy):

| Policy | Points | Connections |
|---|---|---|
| `"gated"` (default — shipped since A0, epic #902) | stay `draft`; an explicit `status:"live"` item is a violation (row 9) | never promote. Direct edges: no promotion. Operator path: `promote_source=False` → operator created `draft`, source **not** auto-promoted. |
| `"auto"` (opt-in parity mode) | source points promote on write (#131 parity) | source of an operator-requiring connection is auto-promoted to `live` (draft/null-status sources only; terminal sources never resurrected). Operator node written without a status property (live by projection, the #780 asymmetry). |

`promotion_policy` is orthogonal to granularity — the same bundle via
`bulk` vs `granular` honors the same policy (E2E-5 proves graph parity).

---

## 3. Response field reference

### 3.1 Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `granularity` | `"bulk" \| "granular"` | Echo of the request param. |
| `batch_id` | string (ULID-shaped, content-derived) | Stable id for the logical bundle; identical across identical resubmissions (see [§16](#16-batch_id--audit)). **planned** (this release). |
| `created` | `{points, entities, sources, connections}` | Count of newly created items per section. |
| `deduped` | `{points, entities, sources, connections}` | Count of items that matched an existing node and were adopted instead. |
| `ids` | `{points[], entities[], sources[], connections[], refs{}}` | The ids/urls the bundle produced or matched, plus the ref table. |
| `nudges` | `[]` | Advisory only, never enforced. |
| `warnings` | `[{key, ...}…]` | Divergences the engine noticed (never fatal). **planned** (this release; closed-set enumeration below). |
| `results` | per-item array | Granular mode only. |

`ids["connections"]` descriptor shapes:

| Route | Descriptor |
|---|---|
| Direct edge (plain IMPL/NAND) | `{"direct_edge": op_type, "from": id, "to": id}` — stable across re-ingest |
| Operator | bare operator-id string (identical across created / dedup-hit / absorb) |
| Relation | `{"relation": pred, "from": id, "to": id, "deduped": bool}` |

### 3.2 Warnings — the ELEVEN keys

`warnings` entries carry a `key` (the table below), the affected ids, and any
context (old/new values, candidate lists). The key set is a **closed
enumeration** — a warning key outside this table is a divergence; report it.

| # | Key | What happened | What you should do |
|---|---|---|---|
| 1 | `append_only_items` | Event/Document entities are append-only occurrence records — re-ingesting them appended again (by design). | None — expected for events/documents. |
| 2 | `modified_item_residue` | A resubmission's item differed from what a crash left behind; the stale residue remains, the new version committed. | Audit the named residue; supersede/retract if it is wrong. |
| 3 | `mitigation_orphan_residue` | A mitigation Point orphaned by a crash was found; it was re-linked or left as residue (naming candidates). | None if re-linked; supersede the orphan if it should not exist. |
| 4 | `mitigation_drift_duplicate` | A second drift event after a re-key — the mitigation was duplicated rather than silently re-keyed. | Supersede the stale duplicate. |
| 5 | `nfc_straddle_duplicate` | An encoding-only difference (e.g. NFC vs NFD) split a dedup key; two points exist for one logical content. | Merge via supersede, or re-submit in one encoding. |
| 6 | `mitigation_strength_change` | Re-submission changed a mitigation's reason/strength; the existing mitigation Point was updated in place (never duplicated). | None — deliberate divergence is recorded. |
| 7 | `partial_operator_residue` | A crash left a partial (incomplete input set) operator; the retry declined to absorb it (or could not), leaving NULL-status residue naming candidates. | Leave it (inert until promoted) or clean up via supersede of its inputs. |
| 8 | `operator_absorb_completed` | A crash partial was absorbed — its input edges were completed to the full set (naming the operator + completed-edge count). | None — observability only. |
| 9 | `label_dropped_resubmit` | A resubmission omitted a label its run-1 operator carried; strict matching treated it as a NEW operator (naming both). | Re-add the label to dedup, or supersede the extra operator. |
| 10 | `direction_dropped_resubmit` | A resubmission omitted a direction its run-1 operator carried; strict matching created a second operator (naming both). | Re-add the direction, or accept the extraction default (see [§12](#12-authoring-guidance-cycle-25)). |
| 11 | `direction_changed_resubmit` | The declared direction changed vs the stored operator on the same pair — a distinct semantic, committed as separate. | Confirm intent; supersede the stale direction variant if not wanted. |

> A12 note: this table is the conformance anchor for the warning contract —
> every key in the closed set must appear here, and no key outside it is
> emitted.

---

## 4. Verify your ingest

After a successful ingest, query the graph to confirm what landed and that
the bundle is coherent.

**Recall the subgraph around a seed** (the completeness-optimized surface —
what you use before connecting more knowledge):

```python
sub = sdk.recall_subgraph("01J8…", depth=2, completeness="core")
# → {"nodes": […], "edges": […], "stats": {"node_count": …, "edge_count": …}}
```

MCP: `tortoise_recall(seed=<id or topic>, depth=2, completeness="core")`.

**Query points by kind / property:**

```python
sdk.query("statement")                       # all statement points
sdk.query(kind=None, status="draft")         # everything still gated
```

MCP: `tortoise_query(kind="statement")`, `tortoise_search(q="…")`.

**Audit what a bundle added:** `batch_id` round-trips through the whole
bundle's artifacts — see [§16](#16-batch_id--audit). **planned:** a batch
discovery/audit primitive (`list_batch`) lands with this release; until then
`query(status="draft")` + the `ids` from the response are the retrieval path.

**Check team usage:** `tortoise team info` → `GET /v1/team` (see [§9](#9-quota)).

---

## 5. Top violations → fix table

Phase-1 validation is deterministic and **zero-mutation**: when any violation
is found, nothing is written and the response is
`{error, code: "ERR_BUNDLE_INVALID", violations: [{section, index, message}, …]}`
with **all** violations listed at once (fail-fast is a regression). SDK
callers get a `ValueError` subclass (`BundleValidationError`) with
`.violations` and `.as_dict()`.

| # | Violation class | Example message | Fix |
|---|---|---|---|
| 1 | Item shape | `points[0] must be a dict` / `points[1] requires 'kind'` / `sources[2] requires 'sourceKind'` | Fix the item; shape is validated per section. |
| 2 | Point kind vocabulary | `pointKind 'event' is not a write kind` (**planned**) | Use `statement` (canonical) or a legacy write-compat kind; episodic records are **entity items** `type:"event"` with `eventKind` (`occurrence`/`turn`) — never a Point. |
| 3 | Ref misuse | `duplicate bundle ref 'pA'` (shipped); `refs shaped like real ULIDs are rejected` (**planned** — Phase 1) | Refs must be unique, bundle-local labels; don't address real ids through refs. |
| 4 | Connection contract | `connections[1] must carry exactly one of 'relation' or 'operator'`; `connections[0] requires 'from' and 'to'`; `connections[2]: 'to' must be a list or string`; `connections[3]: multi-item 'to' on a plain IMPL/NAND connection` (**planned**) | Exactly one of relation/operator; `from`+`to` present; singular `to` on plain direct edges (multi-input → `mitigation`/`reify:true`); self-edges rejected on IMPL/NAND. |
| 5 | Unknown types / fields | `connections[4] operator must be one of ['IMPL','NAND', …]`; `connections[5] unknown relation 'SUPPORTS'`; `reify:true connection carrying 'confidence' is rejected` (**planned** — route-scoped fields) | Operator ∈ IMPL/NAND; relation ∈ the structural predicate set ∪ `extractedFrom`; drop attributes that don't belong on the route. |
| 6 | Endpoint typing | `external endpoint 'ghost-id' does not exist`; `connection endpoint must be a plain Point, got a Source` | Direct-edge and operator-requiring connections need existing plain-Point endpoints (or bundle-local refs to point items). |
| 7 | Terminal endpoints | `endpoint '01J8…' is superseded/retracted — new direct edges to terminal points are rejected` | Point the connection at a live/draft point; supersede transfers are the mechanism to repoint. |
| 8 | Conflicting duplicates | `same-pair IMPL connections with differing direction/confidence are ambiguous`; label / direction / mitigation-reason conflicts | Identical duplicates are cleanly deduped; **conflicting** duplicates are rejected fail-closed. Unify the conflicting attribute, or use label-differing pairs (legal on the operator route). |
| 9 | `status:"live"` under gated | `status:'live' is not allowed under promotion_policy 'gated'` | Use `promotion_policy="auto"` for explicit live, or keep draft and promote via `tortoise_update_point(status="live")` (the interim promotion route — see [§11](#11-promotion--ep)). |
| 10 | `batch_id` in bundle | `batch_id is server-managed and cannot be set on bundle items` (**planned**) | Remove it; the server computes and stamps it. |
| 11 | `c_cal` on a bundle item | `c_cal is calibrated-pipeline-write-only` (**planned**) | Never send `c_cal` through ingest; calibrated confidence is written only by the calibration pipeline. |
| 12 | `quote` over cap | `quote exceeds 200 characters` (**planned**, Phase 1) | Truncate the provenance quote to ≤200 chars. |

> **CYCLE-26 derived-liveness replaces the old check-5.** There is no
> "operator-requiring connection rejected under gated" violation anymore:
> gated operator connections are **accepted**, and the operator activates
> only when two of its connected points are live (see
> [§11.1 Derived liveness](#111-derived-liveness)). The retained check-5
> remnant is only the explicit `status:"live"` bypass guard (row 9).

---

## 6. You ingested the wrong thing

The graph is append-only by default — nothing you ingest is silently deleted.
Three recovery tools, in order of preference:

| Situation | Tool | Semantics |
|---|---|---|
| A point's **content is wrong** and a corrected version exists | `supersede_point(old_id, new_id)` (SDK) / `tortoise_supersede` | Atomically replaces the old point: `CORRECTS` edge, `outdated:true`, **all edges transferred** to the new point (operator edges both directions, structural edges), preserving type/direction/confidence/weight/label/`batch_id`. The old point keeps only the CORRECTS edge as provenance. |
| A point should **not exist** | `retract_point(id)` (SDK) / `tortoise_retract_point` | Terminal `status="retracted"` tombstone; default query surfaces exclude it (`include_retracted=True` to see it). |
| An **operator / mitigation** was wrongly ingested | see operator disposition below | `supersede_point` rejects operators (supersession is for statement points) — disposition is explicit, never silent. |

**Operator disposition (E2E-11.8(c)) — two documented options, no silent ones:**

1. **Reify a corrected pair** — create the corrected statement points and a
   corrected operator (`mitigation`/`reify:true` connection), then supersede
   the wrong points. Supersede's edge transfer moves the wrong operator's
   input edges off the wrong points; the wrong operator remains as an inert
   residue with a fixed input-edge set.
2. **Leave the operator, supersede its outputs** — supersede the operator's
   output points; the operator stays, its input-edge set is fixed, and the
   superseded outputs stop contributing to EP (terminal-point handling).

Whatever you choose, **re-submitting the identical bundle after a supersede
does not resurrect the wrong artifact** — dedup is content-keyed, and the
corrected content is a different logical item.

---

## 7. Kill recovery

**Transport death → retry idempotency is the contract.**

If the ingest call dies mid-flight (client killed, network drop, server
SIGKILL), you get **no response** — but the write may have partially
committed. The recovery signal is a single rule:

> **Re-submit the identical bundle, unpatched.** Convergence is exactly-once:
> items that committed dedup, items that didn't commit are created, and the
> whole logical bundle carries **one `batch_id`** across kill + retry
> (`batch_id` is content-derived, not per-call — a retry mints the same id).

- The killed call returns no structured error; there is nothing to parse.
- **Metering note:** a transport-killed call commits its writes but is never
  metered (metering records after the call returns — the killed call never
  returns); the retry meters +1. Every kill = one free write op, by design —
  never "fix" this by retrying harder.
- Embedded self-hosted stores auto-rebuild from their JSONL event log when
  the live store is lost/corrupt; points, direct edges, `batch_id`s, and
  mitigation stamps survive with no manual rebuild (see
  [quickstart-selfhosted.md](quickstart-selfhosted.md) — the default embedded
  mode has no AOF window, so the JSONL journal is the durability contract).
- **Timeout ≠ failure — resubmission is idempotent.** A timeout is the
  same class as transport death: re-send the identical bundle; if it
  committed, you get an all-`deduped` response confirming presence.

---

## 8. When your ingest fails: error code → action

The MCP tool returns structured errors; SDK callers raise. All eight shapes,
with the exact retry action:

| # | Error shape | Meaning | Action |
|---|---|---|---|
| 1 | `ERR_BUNDLE_INVALID` `{error, code, violations[]}` | Phase-1 validation failed; zero mutations; deterministic. | **Fix the bundle; never re-send unchanged.** All violations are in the response. |
| 2 | `ERR_INVALID` `{error, code}` | Pre-SDK param error (bad `granularity` / `promotion_policy`). Message names the valid values. | **Fix the param; never re-send.** Deterministic. |
| 3 | `ERR_QUOTA` `{error, code}` | Team cap reached. The check is pre-write **count-then-act** — even a fully-deduped (zero-delta) call is rejected at cap. May arrive **after** Phase-2 commit (writes landed; the response carries the already-computed `batch_id` so you can verify what committed). Cap is **cumulative node count, not rate-based**. | **Stop retrying; escalate** ([§9 Quota](#9-quota)). Once headroom is granted, resubmit **once** — an all-`deduped` response confirms presence. |
| 4 | `ERR_QUOTA_SERVER` `{error, code}` | Transient quota-counting failure (fail-closed). | **Backoff-retry** (transient). |
| 5 | `ERR_UNAUTHORIZED` `{error, code}` | 401 — missing/invalid `Bearer tt_<key>`. | **Fix credentials; never retry.** |
| 6 | `ERR_REGISTRY` `{error, code}` | 503 — team registry (key→team resolution) unavailable. The `-32099` **static-auth-misconfigured** variant ("Static auth misconfigured: no API key set") is a server configuration problem. | Transient → **backoff-retry**. The `-32099` static-misconfig variant → **operator fix (config), don't retry**. |
| 7 | Phase-2 failure `{error}` — **no code** | A write failed mid-commit; partial state is committed. The error names the section/index/item; it carries the already-computed `batch_id`. | **Re-send converges** — the retry dedups what committed and writes the rest. Audit via `batch_id` first if you like. |
| 8 | Transport death — **no response** | The call may have committed anything. | **Re-send the identical bundle**; exactly-once convergence ([§7](#7-kill-recovery)). |
| 9 | `EmbeddedStoreBusyError` — named class prefix in `{error}` (**planned**) | Cross-process contention on an embedded store (another process holds the db). **planned:** fail-fast with a named error replaces today's blocking flock-with-timeout handling. | **planned:** do **NOT** retry until the holder exits. Unlike phase-2 partial-state errors, re-sending while the store is busy does **not** converge — it re-errors. (Fail-fast replaces the old silent second-daemon behavior.) |

**planned:** `ERR_BUNDLE_INVALID` is a new exported constant classified as a
client error (4xx-class) — the dedicated-branch mapping
(`BundleValidationError`/`QuotaExceededError`/`QuotaCheckError`) always
precedes the generic error branch, so a dedicated error never degrades into
the `{error}`-only shape whose retry action is different.

---

## 9. Quota

- **What counts against the cap:** all **non-episodic Points** in your team
  graph. Bundle-created points are non-episodic by construction (deliberate
  knowledge, never capture-turn records) — they count. The `is_episodic:true`
  turn Points and `:Session` nodes written by the **capture path** are
  exempt (that exemption ships with the capture work). Entities/sources/
  events/documents are counted separately per their own resource rules.
- **The cap is CUMULATIVE, not rate-based.** It is the team's total node
  count against the tier limit (`max_points` ← `graph_size_cap`). Backoff
  cannot clear it — only raising the limit (or pruning) does.
- **How to check usage:** `tortoise team info` → `GET /v1/team` returns
  `point_count` plus user/graph limits. **Known mismatch:** `GET /v1/team`
  exposes only `point_count` — there is **no points-limit field**, and the
  REST count is a raw `MATCH (n:Point)` (all Points, including episodic)
  while the enforced predicate counts non-episodic Points; the displayed
  number therefore **over-reports (or matches)** the enforced count, and the
  limit itself is not displayed at all. Treat `tortoise team info` as usage
  signal, not limit truth.
- **`ERR_QUOTA` action: stop retrying and escalate.** The quota message's
  "Upgrade your plan" wording references a billing path that does not exist
  yet; the real escalation is support / an operator raising the team's
  `max_points` (`team_update` / Supabase `graph_size_cap`). An admin raise
  propagates within the 60s auth-cache TTL and applies at the next call's
  pre-write check — an in-flight bundle is unaffected.

---

## 10. Transport & auth modes

| Mode | Posture | Quota |
|---|---|---|
| **stdio, non-dev** (`TORTOISE_API_KEY` set) | **Fail-closed** — every tool rejects with an auth-required message. stdio cannot carry auth tokens. | — |
| **stdio, dev mode** (no `TORTOISE_API_KEY`) | Local eval only. | N/A |
| **Self-host static / none** (`serve --http --auth static` or `none`) | Single-tenant `team_selfhost` namespace. | **Quota N/A** (selfhost has no billing; batch caps still apply). |
| **Self-host tenant** (`serve --http --auth tenant`) | Per-team `team_{id}` namespaces, `tt_` keys. Registry unavailable → **503 `ERR_REGISTRY` pre-write** (never a silent pass). | Enforced per team. |
| **Hosted** (`https://api.premiselabs.co/mcp/`) | streamable-http, `Bearer tt_<key>`. | Enforced. |

Additional posture rules:

- If `_transport_mode` is unset/misconfigured, **all** operations reject
  (fail-closed); it never depends on dev-mode alone.
- **Ingest works offline / degraded:** embeddings degrade to lexical/FTS
  search; structure (edges, `batch_id`, EP) is unaffected.
- HTTP tenant mode writes to a fresh `team_{id}` namespace — data written over
  stdio stays in the `tortoise` graph; they are separate namespaces.
- Cross-process embedded contention: **planned** — this release adds the fail-fast
  `EmbeddedStoreBusyError` contract ([§8 row 9](#8-when-your-ingest-fails-error-code--action));
  today, concurrent embedded writers are serialized by a blocking
  flock-with-timeout (no silent second daemon either way).

---

## 11. Promotion & EP

**Promotion-EP conditional line (operative — GATE-2 Q6 approved):**

> **Promoted points are live; direct-edge knowledge enters EP computation
> when the A9 traversal ships** — and A9's selector traversal for
> operator-less direct edges ships with this release (direction-respecting,
> `SENTINEL_A9` gate). Once a point is promoted to `live`, its direct-edge
> factors participate in EP belief propagation.

- **The graph records what the author believed; EP computes what the
  evidence supports.** Edge-carried `confidence` values are recorded for
  audit, provenance, and future calibration — EP never seeds messages from
  them. Belief comes from factor structure, not author-stated values.
- **Promotion is explicit, never automatic under gated.** Draft→live happens
  only via promotion — today via `tortoise_update_point(status="live")`
  (the interim route; guarded draft→live), and via the dedicated promote
  tools when they ship.
- **Promotion authority setting (per-team, alongside quota limits):**
  default `agent` — agents can promote. The alternate value is `reviewer`
  mode (the reviewer-gated flow). Not asked at onboarding; discoverable in
  settings. Under the default, agent promotion works out of the box.

### 11.1 Derived liveness

> **Gated operator connections are accepted; the operator activates only when
> two of its connected points are live.**

Under `promotion_policy="gated"`, operator-requiring connections (mitigation /
`reify:true`) are accepted — there is no fail-closed rejection. The operator
is created with `status:"draft"` (`promote_source=False`) and is **EP-inert by
construction**: it participates in EP **iff ≥2 of its connected points are
live**. Promote a second endpoint and the operator activates; until then it
cannot propagate belief. This is the derived-liveness rule (A9's selector
predicate for operator nodes).

---

## 12. Authoring guidance (CYCLE-25)

- **Statement-first authoring.** `kind: "statement"` is **the** extraction
  write kind. Legacy kinds (`decision`, `vision`, `strategy`, `plan`, `goal`,
  `target`, `humanApproval`, `observation`, `hypothesis`) remain write-compatible
  but are compat-only — don't use them for new extraction. **`kind: "event"`
  is REJECTED on point items** — episodic records are **entity items**
  `type:"event"` with `eventKind` (`occurrence`/`turn`). Decisions are
  decision-as-event (eventKind `decision` + lifecycle writes on objects),
  not first-class Points. **planned:** a point item without `kind` defaults
  to `statement`.
- **NAND direction default.** A direction-absent NAND connection commits as
  `unidirectional` — *new-claim-attacks-existing* is the common extracted
  case. Declare `direction: "bidirectional"` explicitly for mutual
  restatement. (IMPL direction-absent stays `bidirectional`.) Note the SDK
  primitive `create_operator` keeps its bidirectional default — the
  per-op-type extraction default applies on the **ingest/extraction** path
  and the direct-edge path.
- **`quote` field.** Point items may carry `quote` — a provenance quote
  **≤200 chars** (secret-scanned; the cap aligns with the message-excerpt
  limit). **planned:** Phase-1 enforces the 200-char cap; a longer quote is a
  violation.
- **`c_cal`** is calibrated-pipeline-write-only — never send it through
  ingest (a bundle carrying `c_cal` is a violation).

---

## 13. Acceptance-surface breaks & migration lines

These are behavior changes on the hosted surface in this release (plus the
default-policy flip in [§2.4](#24-both-policies)). All are validation-policy,
not schema — no version bump.

| # | Break | Pre-release behavior | Post-release | Migration |
|---|---|---|---|---|
| 1 | **Gated default flip** | ingest auto-promotes (#131) | default `promotion_policy="gated"` — points stay draft; connections never promote | Pass `promotion_policy="auto"` for parity (identical graph, E2E-8-proven) |
| 2 | **Multi-item `to` on plain IMPL/NAND rejected** | a multi-input operator was created via fan-out | Phase-1 violation (fail-closed) | Add `reify:true`/`mitigation` (operator route unchanged), or split into singular connections |
| 3 | **Label conflict on same-pair plain connections** | label-differing same-pair plain connections created two operators | Phase-1 violation | `reify:true` ×2 (operator route keeps both labels), or unify the label |
| 4 | **Route-scoped attribute rejection** | `confidence`/`weight` on operator-routed connections and `direction`/`confidence`/`weight` on relations were silently dropped | Phase-1 violation | Drop the attributes (confidence/weight belong on the plain direct-edge path only) |
| 5 | **Mitigation-reason conflict** | same-pair same-label IMPL+mitigation with differing reasons committed with an order-dependent survivor | Phase-1 violation | Unify the reason, or use label-differing pairs (legal) |
| 6 | **Label-absent strict matching** | a no-label request was a wildcard matching labeled operators (order-dependent commit) | absent ≠ present; Phase-1 conflict for same-pair {labeled + absent}; a label-dropped resubmit creates a second operator + `label_dropped_resubmit` warning | Re-add the label, or use `reify` ×2 under auto |
| 7 | **Direction-absent strict matching (per-op-type)** | a direction-absent lookup was a wildcard | absent ≡ op-type default for dedup (IMPL→`bidirectional`, NAND→`unidirectional`); {IMPL absent, `unidirectional`} and {NAND absent, `bidirectional`} are conflicts; a direction-dropped resubmit creates a second operator + `direction_dropped_resubmit` warning | Declare the direction, or accept the extraction default — **NAND=unidirectional, IMPL=bidirectional** |

---

## 14. Upgrading to this release

Combined cross-epic upgrade notes (epic #902 ingest + epic #900 index).

**Deprecated (announced, behavior unchanged during the window):**

- `tortoise_index_sessions` and `tortoise_ingest_corpus` are **deprecated**.
  They keep working (frozen semantics) until the removal window.
- The `tortoise index sessions` CLI is **removed** — replaced by
  `tortoise index directory <corpus-dir>` (`--metadata`, `--db`,
  `--corpus-name`). Session hooks fire the reconciliation sweep through the
  new command.
- **Path: `tortoise_ingest_corpus` → `tortoise_ingest`.** Corpus/file-driven
  ingestion moves to the bundle surface: convert your corpus walk into a
  bundle (points + entities + sources + connections) and call
  `tortoise_ingest` — you get the coherent multi-section write, idempotent
  retry, `batch_id` audit, and the full error contract of this document. The
  corpus tools remain for the transition window only.

**Behavior changes (this release):** the seven breaks in
[§13](#13-acceptance-surface-breaks--migration-lines), the default-policy
flip to `gated`, the response additions (`batch_id`, `warnings`), and the
direct-edge connection routing (plain IMPL/NAND connections become
operator-less direct edges — operator Points are created only for
`mitigation`/`reify:true`).

---

## 15. Timeout ≠ failure

> **Timeout ≠ failure — resubmission is idempotent.**

If an ingest call times out, you do not know whether it committed. Re-send
the identical bundle: committed items dedup (all-`deduped` counts confirm
presence), missed items are created, and one `batch_id` spans the whole
logical bundle. Never "fix" a timeout by editing the bundle before retrying
— a modified retry is a different logical bundle and can strand run-1
residue.

---

## 16. `batch_id` & audit

- **What `batch_id` is:** a deterministic, content-derived id for the logical
  bundle — identical bundles (including crash-retries) share one id. It is
  stamped on every **new** Point the bundle creates (plain points, operator
  Points, mitigation Points) and on direct edges. It is server-managed:
  a bundle item carrying `batch_id` is a violation.
- **Created-OR-adopted semantics:** a batch-less pre-existing point that
  dedup-hits a bundle item **acquires** the bundle's `batch_id` on its first
  dedup hit (the stamp-when-absent rule has no discriminator between a crash
  sibling and a pre-existing point — adoption is pinned). Audit therefore
  returns "artifacts stamped with the batch_id, **created OR adopted via
  dedup**".
- **Scope of audit:** the STAMPED classes — points + operator/mitigation
  Points + direct edges. Entities and sources are **not** stamped (they are
  url/name-keyed) and are unreachable by `batch_id` — documented as out of
  stamp scope.
- **Batch discovery:** **planned** — a batch discovery/audit primitive
  (`list_batch(batch_id)` or a batch_id filter on `query()`) ships with this
  release. It is what lets you find what committed when transport death ate
  the response (you never saw a `batch_id`). Until it ships, retrieve via
  the `ids` from the response + `query(status="draft")`.
- **Outside audit scope:** editorial supersede artifacts (the superseding
  point `p2`, created via `supersede_point` with no batch) are **not** in
  the batch's stamped set; repointed edges **remain** in their originating
  batch (`batch_id` preserved through supersede's edge transfer).
- **Rebuild durability:** `batch_id` survives `rebuild_all` (replayed from
  the JSONL journal), so post-rebuild audits are complete for everything the
  response claimed committed.

---

## Appendix A — Code locations (for operators)

| Contract piece | Where it lives |
|---|---|
| `sdk.ingest` orchestrator | `tortoise/sdk.py` (`ingest`, `_find_operator`) |
| Validation + `BundleValidationError` | `tortoise/sdk.py` (`_validate_bundle`, planned) |
| Direct-edge writer | `tortoise/sdk.py` (`create_direct_edge`, planned) |
| `tortoise_ingest` MCP tool | `tortoise/mcp_server.py` |
| Error constants | `tortoise/mcp_auth.py` (`ERR_UNAUTHORIZED`, `ERR_REGISTRY`, `-32099`), `tortoise/mcp_server.py` (`ERR_QUOTA`, `ERR_QUOTA_SERVER`, `ERR_INVALID`; `ERR_BUNDLE_INVALID` planned) |
| Quota | `tortoise/quota.py` (`enforce_team_limit`, `_count_resource`, `resolve_team_limits`) |
| Lifecycle (draft→live) | `tortoise/sdk.py` (`update_point`, `create_operator` `promote_source`) |
| Recovery (supersede / retract) | `tortoise/sdk.py` (`supersede_point`, `retract_point`) |
| Read-back | `tortoise/sdk.py` (`recall_subgraph`, `query`) |
