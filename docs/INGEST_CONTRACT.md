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
`promotion_policy` parameter, connection routing is **endpoint-kind aware**
(a plain IMPL/NAND between `:Point` endpoints is **operator-mediated**; the
operator-less direct edge is reserved for non-Point pairs), and validation
moves to a deterministic zero-mutation Phase-1 pass. Where a behavior is pinned in the epic plan but
not yet visible in the shipped code at the time of writing, this doc says
**planned:** explicitly. Everything else is shipped behavior you can rely on.

Related docs:

- [Hosted quickstart](quickstart-cloud.md) — sign up, API key, MCP registration.
- [Self-hosted quickstart](quickstart-selfhosted.md) — Docker, `TORTOISE_DB_URI`, transport modes. (Embedded is the eval substrate, not a deployment path.)
- [Ontology](ONTOLOGY.md) — point kinds, operator semantics, the edge vocabulary.
- [Tortoise skill — how to use Tortoise](https://raw.githubusercontent.com/daniel-ospina/agent-infra/main/skills/how-to-use-tortoise/SKILL.md) — agent-facing ingest guidance.

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
     "direction": "unidirectional"},
    {"from": "pA", "to": "src1", "relation": "extractedFrom"}
  ]
}
```

⚠️ **`confidence`/`weight` are not in that example on purpose (#9749).** They are attributes of the
**operator-less direct edge**, which exists only for a connection with no plain `:Point` endpoint —
a Point-bearing pair is operator-mediated ([§13](#13-acceptance-surface-breaks--migration-lines) row
4, endpoint-kind gate #8499), and the operator route has no edge attribute to carry them. Sending
them on a promoted Point↔Point connection is a Phase-1 violation, not a silent discard.

The `direction` values accepted are the model's four — `->` (source→target), `<-` (target→source),
`<->` (both ways), `-` (none: the connection exists and no confidence transfers) — plus the legacy
aliases `unidirectional` ≡ `->` and `bidirectional` ≡ `<->`. See the direction model and its
*Transition state* note in `docs/ONTOLOGY.md` (§8).

Rules that hold regardless of granularity or policy:

- `ref` is a bundle-local addressing label usable in any connection's
  `from`/`to` (and in entity props like `authoredBy`/`aboutPoint`, and point
  `extractedFrom`) instead of a created id. It must be unique within the
  bundle and is **never stored** as a node property.
- Connections carry **exactly one** of `relation` (plain structural edge) or
  `operator` (epistemic mechanism). `operator` is `IMPL` or `NAND`
  (plus part/whole types), optionally with `label`, `direction`,
  `confidence`, `weight`, `mitigation`, or `reify` (see [§13](#13-acceptance-surface-breaks--migration-lines)).
  ⚠️ **#9749 — `confidence`/`weight` are DIRECT-edge attributes:** they are written (and read back)
  on the operator-less edge, i.e. for a pair with **no** plain `:Point` endpoint
  (`Object↔Object`, `Subject↔Object`, `Event↔…`). A **Point-bearing** pair is reified into an
  operator (#8499), whose route has no edge attribute to carry them, so the connection is a Phase-1
  violation there rather than an accepted-and-discarded value (§13 row 4). The explicit
  operator-less spelling for Points remains available on the SDK primitive:
  `sdk.create_direct_edge(op_type, src, tgt, confidence=…, weight=…)`.
- A plain `operator` connection (no `mitigation`/`reify`) takes a **singular**
  `to`, and multi-item `to` on it is a Phase-1 violation — multi-input semantics
  stay reachable via the operator anchor (`mitigation`, `reify: true`, or the
  automatic promotion below). ⛔ **"multi-item" means multi-item DISTINCT**
  (#9750): `to: ["o2", "o2"]` is ONE target spelled twice — the same shape the
  singular spelling produces (`create_direct_edge` reads the first target either
  way) — so it is ACCEPTED and reported as `duplicate_target_deduped`
  ([§3.2](#32-warnings--the-thirteen-keys) key 13); only a SECOND DISTINCT
  target is the violation. ⚠️ **#9173 — the rule is DIRECT-edge-scoped:** it
  applies only where the connection is genuinely direct (`Object↔Object`, i.e.
  no plain `:Point` endpoint). A **Point-bearing** pair is reified into an
  operator by the endpoint-kind gate (#8499), that route fans out over all
  targets, and the multi-item spelling is **accepted and committed** — no
  `reify: true` needed. Same rule, same shape as #8623's label arm.
- A connection's `mitigation: {reason, strength}` is **applied**, not a bare
  routing anchor (#5244): the operator is created / deduped / absorbed first,
  then the mitigation is written through the single-writer gate that owns the
  predicate — a mitigation Point plus `(op:Point)-[:mitigated_by]->(m:Point)` —
  and stamped with the bundle's `batch_id` ([§16](#16-batch_id--audit)).
  Re-submission updates it **in place** (never a duplicate) and reports
  `mitigation_strength_change` ([§3.2](#32-warnings--the-thirteen-keys) key 6).
  ⛔ **A WITHDRAWN mitigation is not reinstated by a re-submission** (#9962):
  if every mitigation the operator carries is terminal (`retracted` /
  `superseded` / `outdated` / `archived`), the submission is decided on its
  **assertion** — the `(reason, strength)` pair, which is what a mitigation
  records ([§3.2](#32-warnings--the-thirteen-keys) key 6). The SAME pair the
  withdrawn Point already records is a **redelivery** (crash-retry, redelivery,
  re-extraction): it writes **nothing** — the withdrawn Point already records
  this bundle's mitigation, no duplicate is minted, and the silence is the same
  one an unchanged re-submission has. A **different** pair is *new evidence* and
  is written: `mitigate_operator` mints a live mitigation beside the withdrawn
  one and `mitigation_strength_change` is reported against the withdrawn row. A
  retraction is terminal everywhere, so the withdrawn Point is never revived —
  the direct-call route behaves the same way. The hosted commit route applies
  this one rule too (its `MITIGATES` MERGE key is satisfied by a current
  mitigation **or** by a withdrawn one recording the same pair), so an identical
  record re-sent by a re-extraction cannot reinstate a retraction there either.
  On a `relation` connection `mitigation` is a **Phase-1 violation** — a
  structural edge has no operator bridge to damp ([§13](#13-acceptance-surface-breaks--migration-lines) row 4).
- Created points default to `status: "draft"` under the gated policy (see
  [§11 Promotion & EP](#11-promotion--ep)). Pass `status: "live"` only with
  `promotion_policy: "auto"` — under gated, an explicit `status:"live"` is a
  violation (no bypass of the gated contract).
- ⛔ **A mitigation Point is the ONE exemption from that born-draft rule**
  (#9962): a connection's `mitigation: {reason, strength}` is written by the
  single-writer mitigation gate (`mitigate_operator`), not by the bundle's
  point policy, so the mitigation Point is born `status: "live"` under **both**
  policies — gated included. The reason is definitional: a mitigation that does
  not dampen is not an assertion, and a `draft` node is EP-inert
  (`live._live_only` excludes it from the dampener read), so staging one as a
  draft would silently discard the dampening this section promises was applied.
  It is not an extraction-staged claim awaiting promotion, and nothing else in a
  bundle is exempt — every other Point a gated bundle creates is `draft`.

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
      "01J8…",
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
- **`ids["connections"]`** — the connection descriptor contract. A connection
  routed to an **operator** appends the bare operator-id string:
  `mitigation`/`reify:true`, and — since #8499 — **any plain IMPL/NAND whose
  endpoints include a plain `:Point`**. A connection routed to an operator-less
  **direct edge** appends `{direct_edge, from, to}` (`deduped` is deliberately
  not part of it — reported in the aggregate — so identical resubmissions
  compare equal). A **relation** connection appends
  `{relation, from, to, deduped}`.

  ⛔ **Point↔Point connections are operator-mediated** (owner ruling
  2026-10-13, tortoise #8499): an operator-less edge carries no operator node,
  so it can be neither mitigated nor annotated, and it cannot carry the
  epistemic weighting a Point's relations claim. The direct shape is reserved
  for non-Point pairs (`:Object`/`:Subject`/`:Event` — `Object↔Object` is the
  common case), where no such weighting is claimed. Pass `reify: true`
  to state the operator route explicitly; a plain spelling that gets re-routed
  is reported as the `point_mediated_route` warning
  ([§3.2](#32-warnings--the-thirteen-keys)).
- `nudges` is advisory, never enforced; `warnings` names divergences the
  engine noticed (see [§3.2](#32-warnings--the-thirteen-keys)).

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
| Operator route (mitigation / `reify:true` / **plain IMPL/NAND with a plain `:Point` endpoint**) | `{"operator_id": "<id>", "deduped": bool}` |
| Direct-edge route (operator-less — **no `:Point` endpoint**) | `{"direct_edge": "IMPL", "from": "<pid>", "to": "<pid>", "deduped": bool}` |
| Relation route | `{"relation": "<pred>", "from": "<id>", "to": "<id>", "deduped": bool}` |

### 2.4 Both policies

`promotion_policy` is orthogonal to granularity (both modes honor the same
policy):

| Policy | Points | Connections |
|---|---|---|
| `"gated"` (default — shipped since A0, epic #902) | stay `draft`; ANY effective status other than `"draft"` on a point item is a violation (row 9 — case variants, nested `props={...}`, and terminal statuses included) | never promote. Direct edges: no promotion. Operator path: `promote_source=False` → operator created `draft`, source **not** auto-promoted. |
| `"auto"` (opt-in parity mode) | a no-status connection-source point is stated `live` at first write; an explicit/prior `draft` is never retro-promoted (#1088) | source of an operator-requiring connection auto-promotes to `live` on first edge creation — **null-status sources only** (an explicit or prior `draft` is never retro-promoted, #1088; terminal sources never resurrected). Operator node written `live` (#5089 — previously written without a status property; a status-less Point is excluded by the strict grounding selector while a rebuild produced `live`, so the rebuild alone changed the operator's grounding participation). Deduped connections never retro-promote — promotion fires on **first edge creation** only. |

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
| `warnings` | `["<key>:<ids>:<detail>", …]` | Divergences the engine noticed (never fatal). Each entry is a key-prefixed string — the key is the §3.2 closed-set token, then the affected ids, then any context (colon-delimited). A warning key outside the §3.2 table is a divergence. |
| `results` | per-item array | Granular mode only. |

`ids["connections"]` descriptor shapes:

| Route | Descriptor |
|---|---|
| Direct edge (operator-less — requires a non-`:Point` endpoint) | `{"direct_edge": op_type, "from": id, "to": id}` — stable across re-ingest |
| Operator | bare operator-id string (identical across created / dedup-hit / absorb) |
| Relation | `{"relation": pred, "from": id, "to": id, "deduped": bool}` |

### 3.2 Warnings — the THIRTEEN keys

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
| 6 | `mitigation_strength_change` | Re-submission changed a mitigation's reason/strength; the existing mitigation Point was updated in place (never duplicated). A mitigation the graph records as **withdrawn** is the exception (#9962): a re-submission of the SAME `(reason, strength)` neither updates nor duplicates it and emits no warning; a DIFFERENT `(reason, strength)` mints a live mitigation beside the withdrawn one and DOES report this key. | None — deliberate divergence is recorded. |
| 7 | `partial_operator_residue` | A crash left a partial (incomplete input set) operator; the retry declined to absorb it (or could not), leaving residue naming candidates. On a graph written since #5089 the residue carries whatever the door wrote — `'live'` on the promoting posture, `'draft'` on the extraction one. It can still be status-less on a LEGACY/pre-#5089 graph, which is precisely why the absorb predicate keeps its `status IS NULL` limb (and why `mining.verify_no_auto_wire` still refuses a raw NULL). | Leave it (inert until promoted) or clean up via supersede of its inputs. |
| 8 | `operator_absorb_completed` | A crash partial was absorbed — its input edges were completed to the full set (naming the operator + completed-edge count). | None — observability only. |
| 9 | `label_dropped_resubmit` | A resubmission omitted a label its run-1 operator carried; strict matching treated it as a NEW operator (naming both). | Re-add the label to dedup, or supersede the extra operator. |
| 10 | `direction_dropped_resubmit` | A resubmission omitted a direction its run-1 operator carried; strict matching created a second operator (naming both). | Re-add the direction, or accept the extraction default (see [§12](#12-authoring-guidance-cycle-25)). |
| 11 | `direction_changed_resubmit` | The declared direction changed vs the stored operator on the same pair — a distinct semantic, committed as separate. | Confirm intent; supersede the stale direction variant if not wanted. |
| 12 | `point_mediated_route` | A plain `IMPL`/`NAND` connection whose endpoints include a plain `:Point` was **reified into an operator** rather than written as an operator-less direct edge (#8499, owner ruling 2026-10-13: Point↔Point relations are operator-mediated; the direct shape is reserved for non-Point pairs — `Object↔Object` is the common case, not the only one, since only `:Point` triggers the gate). Names both endpoints and the operator type. | None if you wanted the contracted route. To say it explicitly, write `reify: true`. ⚠️ If you read `ids["connections"]` positionally, the entry is now the **operator-id string**, not the `{direct_edge, from, to}` descriptor — that descriptor is still what an `Object↔Object` direct edge returns. |
| 13 | `duplicate_target_deduped` | The connection's `to` list named the same **target** more than once (the same spelling twice, or two spellings that resolved to ONE node — a bundle-local ref and the id it minted, or two items the point writer's content dedup folded), so the repeated entry was **collapsed** before the write (#9750). Names the ids that collapsed and the operator type. **On the operator route** the input set is a SET: its typed and `INPUT` edges are positional (`idx` 0 = the source slot `ep.py` reads the forward message from, `idx >= 1` = the targets), so a repeated target used to be written as two edges over one claim — `ep.py` builds the factor from the edge list, so it counted that claim twice and defeated the "<2 inputs" unary path (measured before the fix: `to: ["p2", "p2"]` → 3 `IMPL` + 3 `INPUT` edges for a one-target operator, vs `to: ["p2"]` → 2/2). Modern graphs write ONE edge per DISTINCT target, order preserved — with ONE exception, the self-support slot: a target that resolves onto the SOURCE keeps a slot of its own, so `{from: q1, to: ["q2", "q2"]}` where `q2` folds onto `q1` writes TWO typed and TWO `INPUT` edges over that one id (`idx` 0 and 1), not one. **On the operator-less direct route** the write reads the first target and writes ONE direct edge (no operator, no `INPUT` mirror), so the repeat is simply ignored — the warning says so, and the result is identical to the singular spelling. | None — `to: ["x", "x"]` and `to: ["x"]` now produce identical graphs on BOTH routes (same edge counts, same factor arity) **except** when `x` resolves onto `from`'s own node, which keeps the self-support slot described above (2 typed + 2 `INPUT` over one id). If you meant TWO assertions about one target, that is two connections, not two list entries. ⚠️ **An operator whose typed-slot count exceeds its distinct endpoint count** is keyed on the **SHAPE**, and the dedup key is the endpoint SET **plus the request's ARITY** (#10305): the exact arm requires the operator's typed-slot count to equal the length of the slot list the operator LOOKUP is handed — `[from, *to]` **after** the target collapse and **before** the endpoint-set normalization, so a repeated SOURCE slot is a real slot (a target that resolves onto `from` keeps a slot of its own, `idx` 0 and 1 over one id) while a repeated TARGET is already gone; `ep.py` reads arity from the edge list, which is why the source repeat counts. A repeated-target RESUBMISSION therefore still exact-dedups: `to: ["x", "x"]` is collapsed to `[from, x]`, arity 2 against the 2-slot operator the first write made (pinned by `test_reingest_repeated_ref_is_an_exact_dedup`, which reds if the key is ever measured on the raw `to` list instead). ⛔ Two producers write more slots than distinct endpoints and they are deliberately NOT treated alike: **(a) a pre-#9750 duplicate-edge operator** (`idx` 1 and 2 over one id — a duplicate **TARGET**) is still adopted, because no request can spell that shape (the writer collapses repeated targets), so a retry EXACT-matches it instead of minting the duplicate operator #9750 is about; **(b) a self-support target list that also carries another target** — `create_operator("IMPL", a, [a, b])` writes slots `[a, a, b]` (3 slots, 2 distinct endpoints), because a target repeating the SOURCE is deliberately NOT collapsed (`§5.5` self-support, `idx` 0 and 1 over one id) — is **NOT** adopted by a 2-slot request: `{"from": a, "to": [b]}` MISSES and mints its own 2-slot operator, the arity the caller actually asserted. The discriminator is WHICH slot repeats: a legacy duplicate repeats a TARGET (unspellable), a self-support duplicate repeats the SOURCE (spellable — a target resolving onto the source plus another target, `[q1, q1, q3]`, exact-matches it) — **and the carve-out only applies to an operator that actually CARRIES an excess slot** (`typed slots > distinct endpoints`), because without that bound a CLEAN operator would adopt a request of a wider arity (measured: a 3-slot request `[q1, q1, q3]` matching a 2-slot operator over `{q1, q3}` and keeping the 2-slot factor — the same defect mirrored). The carve-out itself is **width-unbounded on purpose**: a pre-#9750 operator with SEVERAL excess duplicate-target slots (e.g. `to: [b, b, b]`) is adopted by any same-set request of ANY width, because no request can spell its widths and tightening that would re-mint the duplicate operator #9750 is about. ⚠️ **Residual, disclosed:** a pre-#9750 operator whose duplicated TARGET *is its own source* (3 slots all on one id, written by `to: [a, a]` before this fix) is shape-identical to the self-support case, so it is **not** adopted — the request mints its own arity and the retry then exact-matches THAT operator. Before #10305 the 2-slot request was answered `deduped: true` by the 3-slot operator and `ep.py` read the 3-slot factor, so EP propagated a model the caller never asserted. This key reports what THIS write collapsed in the REQUEST, never what the graph already holds. ⚠️ An arity mismatch is REFUSED (it mints its own operator) but still not REPORTED — whether an idempotency-fingerprint mismatch should be surfaced (or answered `422`) is the §3.2 contract question in #10320. |

> A12 note: this table is the conformance anchor for the warning contract —
> every key in the closed set must appear here, and no key outside it is
> emitted. The set was extended from ELEVEN to TWELVE keys by #8499 (the
> `point_mediated_route` row above) and to THIRTEEN by #9750 (the
> `duplicate_target_deduped` row above); both amendments are deliberate, and are
> recorded here and pinned in `tests/test_ingest_conformance.py`.
>
> **Which keys are emitted today.** Row 6 (`mitigation_strength_change`) is
> emitted by `ingest()`'s mitigation path since #5244. Rows 3 and 4
> (`mitigation_orphan_residue`, `mitigation_drift_duplicate`) describe
> crash-orphan re-link and post-re-key drift; **no write door emits them yet** —
> they are reserved vocabulary for those recovery flows, not observable today.

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
| 4 | Connection contract | `connections[1] must carry exactly one of 'relation' or 'operator'`; `connections[0] requires 'from' and 'to'`; `connections[2]: 'to' must be a list or string`; `connections[3]: multi-item 'to' on a plain IMPL/NAND connection` (**#9173: DIRECT-edge-scoped** — a Point-bearing pair is promoted to the operator route and fans out, so it is accepted; **#9750: judged on the DISTINCT ref list** — a repeated spelling of ONE target is not multi-item) | Exactly one of relation/operator; `from`+`to` present; singular `to` on plain direct edges, where "singular" is **one DISTINCT target** — `to: ["o2", "o2"]` is accepted and collapses to the singular shape (any non-Point-bearing pair; multi-input → `mitigation`/`reify:true`); self-edges rejected on IMPL/NAND. |
| 5 | Unknown types / fields | `connections[4] operator must be one of ['IMPL','NAND', …]`; `connections[5] unknown relation 'SUPPORTS'`; `connections[6] confidence is not allowed on the operator route (reify:true/mitigation)`; `connections[7] confidence cannot be stored on this connection: a Point-bearing pair is operator-mediated` (**#9749 — both spellings shipped; judged by the EFFECTIVE route**) | Operator ∈ IMPL/NAND; relation ∈ the structural predicate set ∪ `extractedFrom`; drop attributes that don't belong on the route. `confidence`/`weight` live on the **operator-less direct edge** only — on a Point-bearing pair use the primitive `sdk.create_direct_edge(...)` if an edge-carried value is genuinely needed. |
| 6 | Endpoint typing | `external endpoint 'ghost-id' does not exist`; `connection endpoint must be a Point/Subject/Object/Event endpoint, got a Source`; ``endpoint 'EVT-CORPUS-1' resolves to an Event node with no id … connection endpoints must address the node by its id`` | An operator connection endpoint must carry one of the four **epistemic** labels — `:Point`/`:Subject`/`:Object`/`:Event` — and be addressed **by node id**, on BOTH the operator route (reify:true / mitigation / part-whole → `create_operator`) and the direct-edge route (plain IMPL/NAND → `create_direct_edge`). That set is the one declaration `_INGEST_ENDPOINT_LABELS` (= `live.EPISTEMIC_LABELS`), read by both validation phases, and it is exactly what #7902 widened the two write primitives to (owner ruling #7813; `_find_operator` already collects the four, so Subject/Object endpoints dedup like Point/Event ones). A `:Source` (a document IS a `:Source`, D10 #5026), an operator-shaped Point, an operator-shaped **entity** item (top-level `is_operator` OR the `props={...}` spelling), a name whose bundle item is operator-shaped, a **merge target** that is operator-shaped (the node the write lands on: the sole name carrier, or — once a name has 2+ carriers, when the fold keys on the record's own `id` — the carrier that already holds the deterministic `(label, name)` id), and an Event that resolves only by `eventId` (not by node id) stay violations — the last because both primitives key on `id`, so accepting it would crash mid-write (partial mutation). ⚠️ The operator-shaped exclusion is an **ingest admission rule, not a `create_operator` contract**: `create_operator` accepts an operator-shaped endpoint and writes the edge, and `weights.py` deliberately prices operator-targeting edges (the doubled base weight for an operator whose target is an operator, and the NAND-against-an-operator case). `ingest()` is stricter than the primitive here ON PURPOSE — a bundle is a whole-graph write whose failure mode is a partial mutation, so the route refuses the shape rather than modelling it. Moving this rule into `create_operator` would remove a capability the engine prices: that is a decision to reopen, not a fixture to tighten. On the DIRECT route the same exclusion is the primitive's own contract (`create_direct_edge` refuses an operator endpoint outright), so nothing there is a candidate to reopen. Bundle-local refs to point items or to `type:"subject"`/`"object"`/`"event"` entity items are accepted, and the entity legs resolve against the **bundle** rather than one item, because step 3 writes the entity items in bundle order and the name merge applies `status` at mint and `SET n += $extra` (last declaration wins) for `is_operator`: a same-name item can carry the terminal `status` that mints the node, or an `is_operator` the merge writes onto the shared node — the last cleared by a later **persistable** `is_operator: false` under the same name (`null` and non-persistable values are dropped by the writer, so they clear nothing). The server-managed `outdated` flag is **not** a name-merge axis: it is refused on mere **presence**, on any point or entity item, referenced or not, on either route and in any item order, because both item writers (`create_point` for `points`, the entity writers for `entities`) reject it on presence and ingest writes every item before any connection. A `:Subject` item's `status` is the *other* writer refusal that is a property of the item alone: `create_entity(type='subject')` (reached through `create_subject`) validates it against `SUBJECT_STATUS_VALUES` for every item in bundle order before the name merge, so a value outside that set — or a non-string, or an explicit `null` — is refused bundle-wide on ANY entity item, referenced or not and on either route, while `:Object`/`:Event` statuses are stored verbatim and stay accepted — EXCEPT one that is **stored and cannot be evaluated** (an array): FalkorDB persists it and `create_direct_edge`'s `status in TERMINAL_EXCLUDED_STATUSES` frozenset test then raises `TypeError` **mid-write**, so an array status is refused bundle-wide at the `object`/`event` item whose write **creates** the node — an Event (append-only), or the FIRST item naming a `(label, name)` whose write is a pure MINT (no carrier at all, or 2+ carriers and none holds the canonical `(label, name)` id, since #8864/#8902 then key the merge on the record's own id). An item that MERGES is not refused: the SOLE name carrier (whatever id it currently holds — the name-keyed merge adopts it), a carrier holding the canonical id, or an earlier item in the same bundle that just created it. `status` is written `ON CREATE` only there, the declaration is dropped, and the holder's own stored array is what the direct-route leg below catches (`_entity_name_holders` returns the merge target's axes, a `merges` flag and the target's id), on both graph-read legs (an addressed node, or an existing name holder, carrying one), and — this is the scope limit — NOT on a `:Subject` item, where the vocabulary rule above already refuses every non-string and `create_subject` raises before anything is stored. A NON-persistable value (a dict, a set) is dropped by the writer and stays accepted; the refusal is scoped by `_status_is_stored_unhashable`, not by "not a string". Phase 1 reads each item's props at the depth its WRITE DOOR flattens (`_item_prop_at_depth`): TWICE for a `subject`/`object`/`event` item (step 3 relays `create_subject`/`create_object`/`create_event` → `create_entity`, each running `_coerce_props`), ONCE for a `document` item (`create_document` folds once and relays to the low-level `_create_entity`), and TWICE for a `points` item (`create_point` folds once, and its DEDUP relay into `update_point` folds again). The two `points` readers deliberately differ, because they decide different things: the gated check (row 9) is EAGER at the dedup depth (a refused declaration must be caught even when the second fold only happens on the relay, which over-rejects a double-wrapped `outdated`/non-draft status on an item that mints instead of relaying — fail-closed by design), while the Phase-2 draft default is at the MINT depth, because at the dedup depth it would skip the explicit `status="draft"` for an item `create_point` sees no status on and the point would be born **live** under the gate that promises draft. So `props: {"props": {...}}` is closed for **every** server-managed key this gate reads — `status`, `outdated`, `is_operator`, `is_episodic`, `sourceVersion*`, `__runId`/`_merge_run_id` — because each is read at its write door's own fold depth: `points` 2 and `subject`/`object`/`event` 2 are refused at level 2, while a `document`/`sources` item folds ONCE and therefore accepts a level-2 spelling (writer parity, not a hole). A third level is visible to neither reader nor writer. The residual is elsewhere and it is tracked: `_sanitize_props` refuses MORE than this gate owns — e.g. an ENTITY item's caller-supplied `id`, and belief props it rejects (`credibility`/`ep_alpha`/`ep_beta`/`baseline_set`/`baseline_source`) — so such an item passes Phase 1 and raises in step 3 with the earlier items committed. That list is EXAMPLES, not the closed set (#8755 carries it). A connection endpoint whose node id the bundle itself RE-ADDRESSES is refused: an entity item naming a holder ADOPTS it, and when that holder's stored id is not the deterministic `(label, name)` id the same write rewrites it, so any endpoint still naming the pre-write id would vanish mid-write (the refusal names both ids). A direct-route connection whose two endpoints resolve to the SAME node is a self-edge and is rejected — the comparison carries the label as well as the landing id, because a `:Subject` stub (`id=name`) and a legacy node on another label can share an id STRING while being two nodes. The comparison is on the landing id, not the name: a bundle-local entity ref lands on the node it merges onto — the MERGE TARGET's stored id when one exists (a name-keyed merge ADOPTS that node and REWRITES its id to `entity_name_id(label, name)`, so an external ref still naming the pre-write id is the same node), else the canonical id it mints — while an EXTERNAL ref lands on the id it names, so two carriers that share a name but hold different ids are two nodes and the edge between them is legal. **#7925 closed the #2062 asymmetry**: the direct route used to require plain Points while `create_direct_edge` itself admitted all four, so the same `(Subject)-[:IMPL]->(Object)` edge was writable through the SDK and refused by `ingest()`. Structure is unchanged by the widening — the endpoint-KIND gate (#8499) still reifies any pair with a plain `:Point` endpoint, so a Point-bearing plain IMPL becomes an operator (with a `point_mediated_route` warning) and an Object↔Object / Subject/Object / Event pair keeps the operator-less direct edge. Note: Event entity items are **append-only** — re-ingesting a full bundle mints a NEW Event occurrence (new id) and thus a NEW operator; dedup applies to id-stable external Event endpoints (re-ingest connection-only against the resolved id). |
| 7 | Terminal endpoints | `endpoint '01J8…' is superseded/retracted — new direct edges to terminal points are rejected` | Point the connection at a live/draft point; supersede transfers are the mechanism to repoint. ⚠️ **#9173 — this guard is DIRECT-edge-scoped too:** it predicts a raise from `create_direct_edge`, which a **Point-bearing** pair never reaches (the endpoint-kind gate reifies it, and `create_operator` does not evaluate endpoint status), so a plain IMPL/NAND to a terminal Point is **accepted and committed** — no `reify: true` needed. any non-Point-bearing pair (`:Object`/`:Subject`/`:Event`; only `:Point` triggers the gate) keeps the refusal — `Object↔Object` is the common case, not the only one. ⚠️ **The KEY-identity leg is NOT route-scoped and must not be made so (#8400).** A `points` item whose `key` a TERMINAL point holds is refused on EVERY route and whether or not any connection references it: `create_point`'s key-first dedup resolves that point whatever the content and FOLDS onto it, so the write MUTATES a retracted/superseded node rather than wiring an edge to it — which is the axis `#9173` scoped, and the reason this one is a *point-write* prediction, not a `create_direct_edge` one. Remediation is an OUT-OF-BUNDLE `create_point(..., key=<a FRESH key>, dedup=False)` — `dedup=False` with the SAME key mints a second CARRIER of one key, the state #6110 exists to prevent — and NOT a supersede-transfer: `supersede_point` transfers edges and stamps `status`/validity/decay, and never writes `key`, so the retired holder still holds the key and the key-first fold resolves it again (the dead clause this row used to offer as a remediation, #9819 review P2). `dedup` is not an ingest item field at all: ingest hard-codes `dedup=True`, so a top-level `dedup` on an item is **refused in Phase 1** (`dedup is not a bundle item field …`, a zero-mutation refusal; before that it collided as a duplicate keyword argument and raised a raw `TypeError` mid-write, after step 1 and any earlier point item had committed), and the nested `props:{"dedup":…}` spelling is shadowed by that top-level `True` under `_coerce_props`' presence precedence and then popped, so it is a silent no-op and must not be read as an in-bundle escape. A fresh key alone does not avoid the *content* fold, because the resolution is key-first-then-content. ⚠️ **The bundle's OWN item order is covered too:** a `points` item whose key an EARLIER item of the same bundle mints terminal (`status` in the terminal vocabulary, reachable only under `promotion_policy="auto"` — row 9 refuses it under `gated`) is refused by bundle-order simulation (`minted_terminal` in `_check_endpoints`), not by a graph read, because the key-first fold would land on a point this same bundle creates terminal and stamp it. ⚠️ **The bundle-order simulation is FIRST-WRITER-WINS on BOTH identities (#9819 review P1):** a repeated key is NOT a violation by itself (only a key minted TERMINAL is refused, above), so a bundle that re-runs one key with recomputed content — the #6110 path a timed-out write forces a caller down — is legal; `create_point` resolves the KEY first, so every later item under that key folds onto the first by key and writes NO content. `minted_keys` records each minting item's key so the simulation knows that, and an item whose key folds there records no content mint: without it the simulation recorded a CONTENT mint the writer never performs, that phantom record suppressed the terminal record of the item that DOES mint that content, and a following item folded onto a bundle-minted TERMINAL point with Phase 1 certifying the bundle — measured (`[{key: K, content: A}, {key: K, content: B}, {content: B, status: "retracted"}, {content: B, props: {confidence: 0.9}}]` committed and the minted retracted node came back with `batch_id` stamped and the prop written). The mirror was an over-reject from the same root: a key-folding item that declares a terminal status mints nothing terminal (`create_point` pops the declaration before its dedup branch, #1905), so recording it refused the next item under that key for a mint that does not happen. Both directions are pinned by `test_an_item_whose_KEY_folds_mints_nothing_on_the_content_axis` and `test_a_key_fold_declaring_a_terminal_status_is_not_a_terminal_mint`. ⛔ **…AND “THE KEY IS FREE” IS NOT “THIS ITEM WRITES IT” (#9819 review P1, round 9):** `minted_keys` is recorded only on POSITIVE evidence — the item MINTS (its CREATE stores the key), or it content-folds onto a node that holds no OTHER key (the fold applies the key through `update_point`, which is the #6110 “a content hit adopts the key” path, now *predicted* rather than assumed). `create_point` **pops** a supplied key when its content fold resolves a point that already holds a different one, so an item can leave the key unheld while a phantom record claimed it — and every later item keys its `_key_folds_here` decision off that record, so the phantom suppressed **all** of their records, the terminal mint of the item that really mints it included, and a third item key-folded onto that point with Phase 1 certifying the bundle. The fold target's stored keys are read off the SAME batched content read (`_terminal_content_candidates` projects `n.key`; `_content_candidate_resolution` answers `fold_target_keys`), and a fold onto a node an EARLIER item minted is answered from `minted_content_keys`, because that node is invisible to the graph read. ⛔ **…and a key-folded item is NOT judged on its CONTENT (#9819 review P2):** the writer resolves the key FIRST, so an item whose key an EARLIER item of this same bundle already holds folds by key and never reaches its content leg — the committed-holder arm one snapshot later, since the graph read predates every item's write. `_key_folds_here` is therefore settled in the KEY leg and the content leg is SKIPPED for it, exactly as the committed-holder arm skips it; only AFTER the bundle-order TERMINAL-key arm above, so a key an earlier item minted terminal stays refused. Judging its content instead refused bundles the writer COMMITS — `points[0]` mints `(K, X)`, `points[1]` re-uses `K` carrying content that matches a STANDING terminal point the fold never touches — with remediation ("mint … with DIFFERENT content") that cannot apply to the shape. Pinned by `test_an_item_that_key_folds_in_bundle_does_not_run_the_content_leg`. ⚠️ The TERMINAL record is now gated on the item actually minting (`_key_writes_here and _mints_here`), not on the key half alone: a terminal-declaring item that content-folds mints nothing, so the next item under its key was refused for a mint that never happens. ⚠️ Residual, disclosed: when SEVERAL live points share one content identity and DISAGREE about the key, the writer's pick among the duplicates carries no `ORDER BY`, so which node the fold lands on is undecidable at Phase-1 time and the guard answers the fail-closed one (“the key may be dropped”) — the same posture as the content leg's “ANY terminal holder” rule, and here it can refuse a bundle whose key would in fact have landed. Pinned by `TestAFreeKeyIsNotAKeyThisItemWrites` (both fail-open shapes and both over-reject controls). ⚠️ **…and that record asks the WRITER's question, not the RESOLVER's (#9819 review P2):** the two readers of an item's `key` use two different tests on purpose — `_item_dedup_key` returns a key only when it `.strip()`s to something (exactly what `create_point`'s `_has_key` hands `_resolve_dedup_hit`), while `_item_stored_key` answers what the minted NODE holds, which `create_point`'s fold tests for plain TRUTHINESS (`isinstance(_existing_key, str) and _existing_key`). Nothing on the write path strips or refuses a blank key, so `key="  "` IS stored and DOES pop a later item's supplied key; reading the stored side with the resolver's test recorded "the mint wrote no key", which made a later item's key look applied while the writer popped it — the same fail-open, re-entered through the mismatch, one function away from the record. Both readers take the value from one `_item_supplied_key`, so `_coerce_props`' PRESENCE precedence (a top-level `key: None` shadows a nested `props["key"]`) cannot fork, and the graph side (`keys_by_hash`) already answered with truthiness for the same reason. Pinned by `test_a_whitespace_only_STORED_key_is_not_a_free_key`, `test_create_point_stores_a_whitespace_only_key` and `test_the_stored_key_and_the_resolver_key_answer_different_questions`. ⚠️ The `strip()`-versus-truthiness gap at the WRITE boundary itself is NOT closed here and must not be closed by a lane: a padded-but-real identity is a RECORDED POSITION (#9599's body) whose trim/refuse/verbatim answer is an OPEN OWNER QUESTION (#9599 comment 6342343424), and the blank half was ruled for session ids and container ids only (`tortoise/ids.py::session_id_refusal`, `projection._writable_container_id`) — not for `Point.key`. #10481 carries that measurement (`key` is index-backed, so a padded key is invisible to `_find_points_by_key` and two `dedup=True` writes mint two carriers of one key). ⛔ **Every bundle-order record keys the MINTED node by its STORED `pointKind` and looks it up by the later item's POSITIONAL one (#9819 review P2):** `create_point`'s props passthrough can carry `pointKind` itself and it WINS over the positional kind on the node, while the writer's dedup QUERY scopes by the positional kind (`_resolve_dedup_hit` / `_find_points_by_key`) — so one kind used for both sides failed OPEN when the two differed, and over-rejected the mirror shape. `_item_stored_point_kind` is that second home; `_item_point_kind` keeps answering for the lookups. ⚠️ **The CONTENT-identity leg is now unconditional too (#9819), and this is NOT #9173's route-scoping reopened:** a `points` item whose CONTENT matches a terminal point (or whose content an EARLIER item of the same bundle mints terminal, `minted_terminal_content`) is refused on EVERY route and whether or not any connection references it — `create_point`'s key-first resolution falls through to CONTENT when the item carries no key or its key is a MISS (#6110), and that content fold reaches `_stamp_batch_id` on the operator route exactly as it does on the direct route, so scoping it by route scoped it out of existence. Before #9819 a fresh key (or none) with the same content as a retracted point folded onto it and stamped `batch_id` / moved `updatedAt`, with `status` left `retracted` — the #8400 "terminal point mutated" hazard, measured. **OVERRIDES:** the pre-#9819 content-fold-is-legal reading of #9173 — the `create_direct_edge` scoping governs the edge (wiring an operator to a terminal point stays legal), and the POINT WRITE is a separate axis #9173 never decided; closing it accepts the over-block of re-ingesting a bundle that legitimately includes an already-retracted point, which is the fail-closed direction. ⚠️ **That over-block is WIDER than "the bundle re-states the retracted point", and the width is deliberate (#9819 review P2):** when SEVERAL points share one content — a LIVE one and a terminal TWIN — the writer's pick among the duplicates is unspecified (its hash query carries no `ORDER BY`), so this leg refuses whenever ANY resolved holder is terminal rather than betting on which one the fold lands on. A bundle whose content would in fact have folded onto the LIVE twin is therefore refused, and the refusal names *a* terminal holder that need not be the node the writer would have used. Same conservative posture, same reason, as the key leg's "ANY terminal key holder" rule above. The caller's fix is the ordinary one: give the new claim DIFFERENT content, minted OUT-OF-BUNDLE with `dedup=False`. ⛔ **Retracting or superseding the collided row does NOT remedy it** (#9819 review P2): `supersede_point` transfers edges and stamps `status`/`outdated`/`validTo`/`expiredAt`/`decay` and never rewrites `content`, `content_hash` or `key` (its only mutation of the old node is that single `SET`), while the dedup predicate carries no status/validity conjunct — so the retired row stays the hash-present holder of that content and the very next ingest of it is refused again. Pinned by `test_retiring_the_collided_row_does_not_clear_the_content_identity`. |
| 8 | Conflicting duplicates | `same-pair IMPL connections with differing direction/confidence are ambiguous`; label / direction / mitigation-reason conflicts | Identical duplicates are cleanly deduped; **conflicting** duplicates are rejected fail-closed. Unify the conflicting attribute, or use label-differing pairs. ⚠️ **#8623 — the LABEL arm is route-scoped:** it applies only where the pair is genuinely direct (a same-pair direct edge MERGEs on `(src, tgt, type)`, so the second would silently overwrite the first's label). A **Point-bearing** pair takes the operator route (the endpoint-kind gate, #8499), whose dedup key INCLUDES `label`, so label-differing same-pair connections between Points are **accepted and commit as two operators** — no `reify: true` workaround needed. |
| 9 | any effective status other than `"draft"` under gated (case variants, `props:{...}` nesting, terminal statuses) | `status:'X' is not allowed under promotion_policy 'gated'` | Use `promotion_policy="auto"` for explicit live, or keep draft and promote via `tortoise_update_point(status="live")` (the interim promotion route — see [§11](#11-promotion--ep)). |
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
| A point's **content is wrong** and a corrected version exists | `supersede_point(old_id, new_id)` (SDK) / `tortoise_supersede` | Atomically replaces the old point: `CORRECTS` edge, `outdated:true`, **all edges transferred** to the new point (operator edges both directions, structural edges), preserving type/direction/confidence/weight/label/`batch_id`. The old point keeps only the CORRECTS edge as provenance. *(Current v1 behavior — the restatement-vs-correction policy (designed in `#2421`, CLOSED `COMPLETED`) will replace the universal transfer with a per-edge carry/drop/pend triage for semantic edges; that triage is not implemented and has no open tracker. See ONTOLOGY §3.1.)* **Rebuild parity (#2489):** the 2b structural-edge transfer (snapshot-derivable rel set — `extractedFrom` + `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutDocument`/`aboutPoint`) is journaled as flat `DirectEdgeRepoint` descriptors `{src=old_id, tgt=<replay key>, target_label, edge_type}` (no successor field) and replayed by pass-2b, so `rebuild_all` reproduces the live transfer state (successor keeps the edges, old does not resurrect them). **The postgres record applies the SAME descriptor as a MOVE (#9972, #9973, #10191):** `DELETE` at the superseded point, `INSERT` at the hop `superseded_by` names (one hop per descriptor, so a chain reaches its terminal row as each hop's descriptor runs), the target resolved by the label's replay key (and refused when a name is held by two carriers, #5509), both halves gated on the point being terminal with a reachable destination, and `ON CONFLICT … DO NOTHING` so a value the successor already owns is never overwritten — the graph's carry is `ON CREATE`, so an edge the successor already holds is left untouched (`STORAGE-ARCHITECTURE.md` §13, #9417). The no-self-edge guard (target node == successor) journals a `delete_only` descriptor instead. Descriptors exist only for post-deploy supersedes — a pre-fix journal rebuild does not repair old resurrections (see ONTOLOGY §3.1). |
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
| 2 | `ERR_INVALID` `{error, code}` | Pre-SDK param error (bad `granularity` / `promotion_policy`) and the row-9 guard (any effective status other than `'draft'` on a point item under gated — shipped as this shape until the Phase-1 `ERR_BUNDLE_INVALID`/`BundleValidationError` migration lands). Message names the valid values / the sanctioned routes. | **Fix the param; never re-send.** Deterministic. |
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
- **One bound IS rate-based — and backoff DOES clear it (429).** `POST
  /v1/sessions/commit` also carries a **per-team ingest budget**: a rolling
  60-second allowance of `10 MiB` of writes per team, per instance (the window
  is process state; today the service runs one worker, so per-instance and
  per-team are the same figure). A request is charged the larger of (a) the
  bytes of the re-serialized §6.1 **request body** — the whole validated
  payload, including `sources`, `provenance_refs` and `telemetry`, so it is
  never less than the canonical form used for `client_commit_id` — and (b) the
  nodes it will write × 1024, where (b) is a declared **upper bound** on the
  nodes the request leaves behind (a node plus its `:GraphEvent` record for
  every point/operator/supersession — three for a superseding point, three for
  a `MITIGATES` bridge-attack — plus the fixed record/session/source/event
  chain), never an under-count. Exceeding the allowance answers **429** with a
  `Retry-After` header and `detail.code = "ingest_rate_limited"`; `detail.used`
  is always **your** spend in the window, and `detail.scope` says which
  allowance refused you — `"org"` (your team's own budget) or `"process"` (the
  instance's shared fallback bucket, used only while its per-team store is
  saturated by a flood; there `used` can legitimately be `0`, because the
  refusal is about the instance and not about anything you wrote). This is a
  **throttle, not a cap**: it never refuses your data, the window self-heals
  within 60 s, and the correct action is **backoff-and-retry** — the opposite
  of the cumulative `ERR_QUOTA` advice above. A request refused by THIS throttle
  leaves no record of its own: the `:CommitRecord` it opened is removed when
  that request created it, so the retry cannot be judged against it. If that
  attempt was the first for this `client_commit_id`, the retry is therefore a
  **first** attempt — the same `held[]` adjudication a clean request would have
  received, not a second one. When an EARLIER attempt of the same id left a
  record — a `held` deferral, a crash mid-write, or a refusal by a gate that
  does not clean up after itself (see #9826) — that record is preserved and the
  retry is a re-submission: it is not re-adjudicated against the 25-item
  first-adjudication band, only against the 50-item ceiling. A `fully_written`
  record is not re-run at all: the replay check returns `duplicate: true` with
  zero writes before the org-cap and ingest gates are consulted (the per-key
  rate limiter and Layer-1 validation still run first, so a re-POST of a
  written id whose non-canonical fields changed can still be refused 429 or
  422).
  (The repo's bundled SDK does not
  yet auto-retry a 429 — see #9799 — so a client driving the endpoint directly
  should honour `Retry-After` itself.) It exists because the removal of the
  flat session cap (#4010 — sessions are unlimited for every tier, by owner
  ruling) left that lane total-unbounded: a commit writes `:Session`, a
  transcript `:Source` and an `:Event`, and none of those labels is in the
  points count, so the points gate cannot see it. Graph **bytes**, not nodes,
  are the unit because that is the ruled storage unit (#4495/#5331). Operators
  can retune the allowance with `TORTOISE_COMMIT_INGEST_BYTES_PER_MIN`. **The
  capture lane (`POST /v1/sessions`) is not covered by this budget** — its
  `count + estimate` points gate and its per-session turn cap are unchanged.
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
| **Self-host tenant** (`serve --http --auth tenant`) | Per-team `org_{id}` namespaces, `tt_` keys. Registry unavailable → **503 `ERR_REGISTRY` pre-write** (never a silent pass). | Enforced per team. |
| **Hosted** (`https://api.premiselabs.co/mcp/`) | streamable-http, `Bearer tt_<key>`. | Enforced. |

Additional posture rules:

- If `_transport_mode` is unset/misconfigured, **all** operations reject
  (fail-closed); it never depends on dev-mode alone.
- **Ingest works offline / degraded:** embeddings degrade to lexical/FTS
  search; structure (edges, `batch_id`, EP) is unaffected.
- HTTP tenant mode writes to a fresh `org_{id}` namespace — data written over
  stdio stays in the `tortoise` graph; they are separate namespaces.
- Cross-process embedded contention: **planned** — this release adds the fail-fast
  `EmbeddedStoreBusyError` contract ([§8 row 9](#8-when-your-ingest-fails-error-code--action));
  today, concurrent embedded writers are serialized by a blocking
  flock-with-timeout (no silent second daemon either way).

---

## 11. Promotion & EP

**Known non-gated promotion surfaces (A0 is ingest-scoped; tracked as follow-up):**
The Q2 gated default applies to the `ingest` surface (SDK + MCP `tortoise_ingest`).
Direct primitives remain explicit promotion routes and are NOT gated: `tortoise_create_point(
props={"status":"live"})`, `tortoise_create_operator` (calls `create_operator` with the
`promote_source=True` default — it fills in a never-set source status on first edge, never an
explicit draft, #1088), and the SDK promotion/commit primitives (`sdk.promote_point`,
`sdk.supersede_point`, `sdk.retract_point`;
MCP-layer promotion routes through `tortoise_update_point(status="live")`). Flipping `create_operator`'s default / exposing `promote_source` on
the MCP tool is tracked as a follow-up so the system-wide default matches the ingest contract.

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
  "Edge-carried" means the operator-less direct edge: an operator-routed
  connection takes neither `confidence` nor `weight` ([§13](#13-acceptance-surface-breaks--migration-lines)
  row 4, #9749), because `create_operator` has no edge attribute to store them on.
- **Promotion is explicit, never automatic under gated.** Draft→live happens
  only via promotion — today via `tortoise_update_point(status="live")`
  (the interim route; guarded draft→live), and via the dedicated promote
  tools when they ship.

  **Capture-surface exception (W5 Phase C, #2104):** the session-capture
  write path (`POST /v1/sessions` + `sdk.capture_session`) auto-promotes
  ITS extracted claim points and their capture operators draft→live as part
  of the EP-on-ingest contract (the ingested session is memory, not a
  pending-review draft — §2.5 measured-good; E2E-5). Scope is strictly the
  capture write path: the gated `ingest` surface and non-capture extraction
  (the #780 draft-operator shape) are untouched, and since #1088 the create
  default is live unless a caller explicitly asks for draft.

  Promotion is rebuild-durable (PointPromoted / OperatorPromoted events, #548
  replay parity) and NOT reviewer-gated — the auto-promoted snapshot never
  fabricates a `reviewed` flag.

  **Pre-#1088 graphs:** points created under the old implicit-`draft` default
  are now indistinguishable from explicit drafts, so they are no longer
  retro-promoted — they stay EP-inert until someone promotes them explicitly.
  Deliberate: nothing marked `draft` is ever silently promoted.

  **Interim-route caveat (Track A — no zombie-operator resolution):** the
  interim route promotes a single point; it does NOT resolve draft operator
  nodes whose endpoints are now all live (the "zombie operator" — a draft
  operator left inert by derived liveness). Zombie-operator resolution
  ("promotes incident draft operators once all their endpoints are live")
  ships with the dedicated promote tools (#785 Track B, `SENTINEL_785`);
  until then an operator-requiring gated bundle's draft operator stays
  inert until either its endpoints are live AND it is explicitly promoted,
  or a promote_batch-style tool lands.
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
live AND the operator itself has been promoted out of draft** — endpoint
promotion alone does NOT activate a draft operator (see the §11 interim-
route caveat; zombie-operator auto-resolution ships with the #785 promote
tools). "Promote a second endpoint and the operator activates" is the
#785-era post-resolution statement; pre-#785 the operator's OWN promotion
is the second required conjunct. Promote a second endpoint and the operator activates; until then it
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
| 1 | **Gated default flip** | ingest auto-promotes (#131) | default `promotion_policy="gated"` — points stay draft; connections never promote | Pass `promotion_policy="auto"` for the #131 promote-on-first-edge behaviour (null-status sources only — an explicit or prior `draft` is never retro-promoted, #1088; the graph is no longer byte-identical across policies) |
| 2 | **Multi-item `to` on plain IMPL/NAND rejected** | a multi-input operator was created via fan-out | Phase-1 violation (fail-closed) — **narrowed by #9173:** now only on the **direct-edge** route — any pair with no plain `:Point` endpoint (`Object↔Object`, `Subject↔Object`, `Event↔…`; only `:Point` triggers the endpoint-kind gate), because that is the only route that calls `create_direct_edge` (ONE target). **#9750:** it is judged on the DISTINCT ref list, so it fires only for a SECOND DISTINCT target — `to: ["o2", "o2"]` is one target spelled twice and is accepted as the singular shape, with `duplicate_target_deduped` ([§3.2](#32-warnings--the-thirteen-keys) key 13). A Point-bearing pair is operator-mediated (#8499) and that route fans out, so a multi-item `to` between Points is accepted and commits as one operator over both targets | Point-bearing pair: **none** — write the fan-out directly, no `reify: true` needed. Object↔Object pair: add `reify:true`/`mitigation` (operator route unchanged), or split into singular connections |
| 3 | **Label conflict on same-pair plain connections** | label-differing same-pair plain connections created two operators | Phase-1 violation — **narrowed by #8623:** now a violation only on the **direct-edge** route — any pair with no plain `:Point` endpoint (`Object↔Object`, `Subject↔Object`, `Event↔…`), where the second edge would MERGE onto `(src, tgt, type)` and overwrite the first's label. A Point-bearing pair is operator-mediated (#8499), and that route's dedup key includes `label` — so two label-differing connections between Points are accepted and commit as two distinct operators | Point-bearing pair: **none** — write the two labels directly, no `reify: true` needed. Object↔Object pair: `reify:true` ×2 (operator route keeps both labels), or unify the label |
| 4 | **Route-scoped attribute rejection** | `confidence`/`weight` on operator-routed connections and `direction`/`confidence`/`weight` on relations were silently dropped | Phase-1 violation — and since #5244 also `mitigation` on a relation (a structural edge has no operator bridge to damp). ⚠️ **#9749 — judged by the EFFECTIVE route:** a plain IMPL/NAND between Points is promoted to the operator route by the endpoint-kind gate (#8499), so its `confidence`/`weight` are a violation too (before #9749 they validated as direct-edge attributes and were then DROPPED by the operator write). On the route where the rule allows them — the operator-less direct edge — they were also being dropped: `ingest` never forwarded them to `create_direct_edge`. Both halves are fixed in the same release: the direct route now stores them (and replays them on rebuild), and the operator route refuses them | Drop the attributes (confidence/weight belong on the plain direct-edge path only — a pair with **no** plain `:Point` endpoint). For a Point-bearing pair there is no ingest route that carries them: drop the key, or write the operator-less edge explicitly with `sdk.create_direct_edge(op_type, src, tgt, confidence=…, weight=…)`. For a mitigation, express the connection as `operator` + `mitigation` or call `sdk.mitigate_operator` |
| 5 | **Mitigation-reason conflict** | same-pair same-label IMPL+mitigation with differing reasons committed with an order-dependent survivor | Phase-1 violation | Unify the reason, or use label-differing pairs (legal) |
| 6 | **Label-absent strict matching** | a no-label request was a wildcard matching labeled operators (order-dependent commit) | absent ≠ present; Phase-1 conflict for same-pair {labeled + absent}; a label-dropped resubmit creates a second operator + `label_dropped_resubmit` warning | Re-add the label, or use `reify` ×2 under auto |
| 7 | **Direction-absent strict matching (per-op-type)** | a direction-absent lookup was a wildcard | absent ≡ op-type default for dedup (IMPL→`bidirectional`, NAND→`unidirectional`); {IMPL absent, `unidirectional`} and {NAND absent, `bidirectional`} are conflicts; a direction-dropped resubmit creates a second operator + `direction_dropped_resubmit` warning | Declare the direction, or accept the extraction default — **NAND=unidirectional, IMPL=bidirectional** |
| 8 | **A connection's `mitigation` is applied** (#5244) | the field was validated and then DISCARDED: the operator was written, no mitigation Point, no `mitigated_by` edge, no warning | the operator is created/deduped/absorbed, then the mitigation Point + `(op)-[:mitigated_by]->(m)` edge are written and `batch_id`-stamped; a re-submission updates in place and reports `mitigation_strength_change` (row 6). #9962: a re-submission whose mitigation has been WITHDRAWN is decided on its assertion — the same `(reason, strength)` writes nothing (no revive, no duplicate), a different one mints a live mitigation | None — this is what [§3.2](#32-warnings--the-thirteen-keys) already promised. Expect one extra Point in the graph and in `list_batch` counts for a mitigation-bearing bundle |

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

**Behavior changes (this release):** the eight breaks in
[§13](#13-acceptance-surface-breaks--migration-lines), the default-policy
flip to `gated`, the response additions (`batch_id`, `warnings`), and
**endpoint-kind-aware connection routing** — a plain IMPL/NAND between
`:Point` endpoints is now **operator-mediated** (a plain spelling is reified
and reported as the `point_mediated_route` warning,
[§2.2](#22-bulk--gated-the-default--expected-response)); the operator-less
direct edge is reserved for non-Point pairs (`:Object`/`:Subject`/`:Event`;
`Object↔Object` is the common case). Operator Points are still
created for `mitigation`/`reify:true`/part-whole, and now also for any
Point-bearing pair.

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
| Quota | `tortoise/quota.py` (`enforce_org_limit`, `_count_resource`, `resolve_org_limits`) |
| Lifecycle (draft→live) | `tortoise/sdk.py` (`update_point`, `create_operator` `promote_source`) |
| Recovery (supersede / retract) | `tortoise/sdk.py` (`supersede_point`, `retract_point`) |
| Read-back | `tortoise/sdk.py` (`recall_subgraph`, `query`) |
