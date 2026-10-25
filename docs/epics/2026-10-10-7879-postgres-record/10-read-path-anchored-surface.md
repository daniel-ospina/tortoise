---
title: "The anchored read surface — which reads the record serves (#7970, interface D)"
type: plan
domain: engineering
doc_status: decided
created: 2026-10-24
updated: 2026-10-24
subjects.team: epistemic-team
aboutSubjects: Tortoise Postgres record
aboutObjects: "epic #7879, issue #7970, record_serve.py, STORAGE-ARCHITECTURE.md, hosted API"
---

# W8 — the read path: which anchored reads the record serves

**Decision record for [#7970](https://github.com/daniel-ospina/tortoise/issues/7970)** · epic
[#7879](https://github.com/daniel-ospina/tortoise/issues/7879) · scope item 4 · interface **D** ·
recorded **2026-10-24**.

`#7970` asks one question and this document answers it as a registry in code, not as prose:
*"which of the 697 anchored / 377 whole-graph Cypher sites are served by Postgres directly."*
The artifact is `tortoise/record_serve.py`; its `ANCHORED_READS` table is the answer, and
`tests/test_record_serve.py` pins the table (served reads carry a parity rule; exclusions carry a
reason — a served read without one, or an exclusion without one, is refused at construction).

## 1. The decision, and the three properties that make it an anchoring

**Served — the anchored class: the read NAMES one node, so the record can answer it by its own
interface-E coordinate** (`Point → id`, `Subject`/`Object → name`, `Source → url`, `Event → eventId`,
`Session → id`; `public.node_identity` is the contract):

| read | the cache-side shape it replaces | where it is reachable |
|---|---|---|
| `point-by-id` | `MATCH (n:Point {id:$id}) RETURN properties(n)` | `GET /v1/points/{id}` |
| `node-by-key` | `MATCH (n:<Label> {<key>:$key}) RETURN properties(n)` | **callable, no consumer wired yet** — it answers the coordinate `residency_router.route_for_coordinate` mints, but that router has no non-test caller today. It is a served *capability*, not a shipped route. ⚠️ **its parity claim is measured for `Point` only**: a record-sourced `:Event` loses seven fold `ON CREATE` defaults the record row cannot carry (including the ONTOLOGY §4.5-required `eventKind`), so `parity_difference(label='Event')` reports them as UNEXPLAINED — the open gap in **#9721**. "Served" must therefore not be read as "parity proven for every label"; a consumer wires a label only after the parity rule comes back clean on it. |

**Not served, each with its reason in the same table** — an exclusion that does not say why is
indistinguishable from an omission:

| excluded | why |
|---|---|
| `anchored-neighbours` | the cache's answer includes operator nodes and an `idx` convention a record row cannot reproduce without re-deciding it. Pinning parity to an answer this unit cannot show is the same one would freeze a decision it has not made. (**#6976** — an operator-hop requirement that returned `[]` for points that had edges, fixed by **#8991** — is the historical member of this defect family, and the reason the anchor must bind or fail.) |
| `anchored-ep-scalars` | `confidence` / `ep_*` / `posterior_*` are **recomputed from the structure** (§2.0 clause (a), §17.5) and the schema refuses them as columns AND as `props` keys. |
| `internal-accessor` (`TortoiseSDK.get_point`) | write paths call it **mid-write**; a record-served answer would read a store the same write has not finished projecting. Its caller-facing surface is `tortoise_get_point`, which is not wired because an MCP call carries no hosted org credential from which to resolve a record tenant. |
| `entity-accessor` (`TortoiseSDK.get_entity` → `tortoise_get_entity`) | a **second** internal accessor with the **same** mid-write hazard (`_get_entity` echoes a written entity back at three write sites). Excluded for the same reason as `get_point`, and named separately because it is a different accessor: `tortoise_get_entity` never reaches `get_point`. (With `type=`, that tool routes to `tortoise_get` — a LIST read, which has no coordinate and falls under `whole-graph`.) |
| `selfhost-endpoint` (`GET /points/{id}` in `selfhost_api.py`) | a **second** caller-facing anchored read. Not wired: the record tenant it needs is resolved from a hosted org credential, and the selfhost surface presents none — that needs its own tenant decision, not a copy of this one. |
| `whole-graph` (the 377 sites) | no coordinate ⇒ a record answer cannot be shown to be the same SET. Out of this unit's scope by the issue's own wording. |

The three properties that make "no silent fallback" structural rather than remembered:

1. **`read_node` takes no cache handle.** Its signature is `(conn, org_id, graph_id, label, key)`,
   and the module imports no graph driver (pinned by AST, not by grep over prose). ⚠️ The
   **endpoint** must hold the same property, and it did not at first: the route built its data SDK
   *before* reading the mode, and `_data_sdk` runs `_assert_graph_owned`, whose probe failure is a
   **403 GRAPH_NOT_FOUND** — an impairment answered as absence. `record` mode now constructs **no**
   handle at all, so it makes zero cache calls and the epic's *"answers from Postgres with the graph
   engine stopped"* is true of the shipped route and not only of `serve_point`. `off` and `prefer`
   still construct through the #3498 seam (`_data_sdk_offloaded` — pool slot, wait bound, fail-closed
   503); `prefer` additionally **captures** a construction failure and re-raises it only when the
   cache is actually needed, so a stopped graph cannot make the lenient mode answer 403 before the
   record is asked. Pinned by `test_record_mode_builds_no_cache_handle_at_all` and
   `test_prefer_answers_from_the_record_when_the_cache_cannot_be_opened`.
2. **The anchor must BIND.** The row query carries `org_id` + `graph_id` + `node_key` and asks for
   **two** rows; a second row means the predicate did not restrict and raises `AnchorDidNotBind`.
   The cache's own version of this class had no tell at all — #6976 returned an EMPTY list for
   points that had edges (an operator-hop requirement; fixed by #8991). Here the tell is an
   exception an operator sees, and it is deliberately not downgraded to a warning.
3. **A fallback is a named, counted event — on every answer, a 404 included.** `TORTOISE_RECORD_SERVE` has exactly three modes:
   `off` (default — the cache answers; this module is imported, because it owns the flag's parsing
and the caller must not re-parse it, but it is never *consulted*: no connection, no query, no
field),
   `record` (the record answers or the read RAISES; the cache callback is never invoked),
   `prefer` (record first; an outage or a miss falls back, and the answer carries
   `servedFrom` + `fallbackReason` while `fallback_counts()` counts it).

The attribution (`servedFrom` / `fallbackReason`) rides **every armed response**, including **both
404 arms** (no such point, and the retracted tombstone) — a 404 is an *answered* read, and a
fallback whose cache also held nothing was counted (`fallback_counts()`); hiding it there would be
the invisible fallback the design forbids. In `off` mode the detail is the plain pre-#7970
string — no wrapper, no keys — and a test pins that too.

**S4/S5, stated precisely — this unit covers the PARITY half only.** The issue's S4/S5 clause has
two halves: *"a read answered from the record must return the same answer as the cache path"*
(**this unit**, §2 below) and *"a read for a non-resident node must return `not-hot`, never
empty"* (**not this unit**): the `residency` marker field is emitted by the cache read path
(`GET /v1/search`, `#7952`/`#7953`) and this unit does not touch it. What the record path
contributes to that discipline is its own mirror, pinned by test: **an impaired record is 503,
never 404** — *"the record could not be asked"* is not "no such point".

`node_key` is the anchor column, not the property column: it is the primary key (indexed) and the
schema's own CHECK pins it to the label's key property (`chk_point_node_key_is_contract_key` and its
siblings). The record row becomes graph properties through **`cache_projection.node_from_row`** —
the cache projection's own row→property map — so the two directions cannot drift about what a column
is called.

## 2. Parity, measured on real stores

`record_serve.parity_difference(cache_props, record_props, label=…)` is the rule: a field the cache
answers and the record does not is **explained** only by the schema's refused set or by a declared
fold `ON CREATE` default; anything else reddens, and so does a field the record answers that the
cache does not. A field both sides carry is **always** compared, refused name or not — that is what
keeps the endpoint's own `content_hash` re-derivation honest.

Measured 2026-10-24 on Postgres 16 (record) + a live FalkorDB (cache), the point created by the
production SDK and written to the record by the production projection (`record_projection.replay`):

```
PARITY MEASUREMENT {"cache_fields": 11, "record_fields": 8,
  "diff": {"clean": true, "changed": [], "invented": [],
           "unexplained_missing": [],
           "explained_missing": ["content_hash", "ep_dirty", "ep_dirty_at"]}}
```

The three absences are exactly the refused names the live point carried; nothing is unexplained.
`S9` — the same read with the cache copy **destroyed** (`DETACH DELETE`, then a cache callback that
raises if touched at all):

```
S9 {"fields": ["content","createdAt","id","is_operator","key","pointKind","status","updatedAt"],
    "read": "point-by-id", "served_from": "record"}
```

**Two caller-visible differences, both deliberate**, on a record-served answer:
`content_hash` is **re-derived** at the endpoint with the writer's own function (#2795 D2 — the
schema refuses it as a column), and the EP dirty flags (`ep_dirty`, `ep_dirty_at`) are **absent**,
because they are the cache's own fold state and a record-served answer must not claim to know it.

## 3. Mutation arms — the guards, and the edits that turn them RED

Run 2026-10-24 against `tests/test_record_serve.py` (47 tests) and the live file (2 tests):

| edit | result |
|---|---|
| drop `graph_id` from the anchor predicate | 2 RED (`test_the_anchor_predicate_is_in_the_sql`, `…_table_and_key_come_from_the_schema…`) |
| drop the `LIMIT 2` bind refusal | 1 RED (`test_the_anchor_must_bind`) |
| let `record` mode fall back on **both** failure arms | 3 RED (`test_an_unreadable_record_is_not_an_absence[failure0/1]`, `test_the_endpoint_never_turns_an_impaired_record_into_404`) |
| endpoint answers **404** for an impaired record | 1 RED (same endpoint test) |
| `parity_difference` explains every absence | 1 RED (`test_parity_reddens_on_an_unexplained_absence`) |
| **LIVE**: the record answer silently drops `key` | 1 RED (live parity: `unexplained_missing: ["key"]`) |
| the endpoint builds a cache handle in `record` mode | 1 RED (`test_record_mode_builds_no_cache_handle_at_all`) |
| `open_record` raises with the connection open | 1 RED (`test_open_record_closes_the_connection_when_the_bound_fails`) |
| a misconfigured DSN is reported as an outage | 1 RED (`test_a_misconfigured_dsn_is_structural_not_an_outage`) |
| a projection refusal is downgraded to a fallback | 1 RED (`test_an_unprojectable_record_is_structural_not_a_fallback`) |
| the `:Event` parity bound is dropped from the registry | 1 RED (`test_node_by_key_parity_is_declared_measured_for_point_only`) |
| a tenancy misconfiguration is downgraded to a fallback | 1 RED (`test_a_misconfigured_tenancy_is_structural_not_a_fallback`) |
| a permanent missing tenant is answered 503 | 1 RED (`test_a_credential_without_a_record_tenant_is_a_fault_not_an_outage`) |
| `off` mode overwrites a stored `servedFrom` | 1 RED (`test_off_mode_leaks_no_attribution_and_keeps_a_stored_name`) |
| `prefer` builds the cache handle before asking the record | 1 RED (`test_prefer_answers_from_the_record_when_the_cache_cannot_be_opened`) |
| a permanent missing tenant is counted as an outage | 1 RED (`test_prefer_counts_an_unresolved_tenant_under_its_own_reason`) |
| a tenancy misconfiguration escapes the endpoint | 1 RED (`test_the_endpoint_answers_500_for_a_tenancy_misconfiguration`) |

## 4. What this does not decide, and where the decision could overturn it

* **§2.0 still governs the default.** `TORTOISE_RECORD_SERVE` is OFF, the FalkorDB limit stays high,
  and no query has to read both stores (§2.0: *"for now we keep the limit in falkor high so we don't
  need to rush"*). This unit builds the **capability** the epic's acceptance names — *"a read path
  answers from Postgres with the graph engine stopped"* — as an **opt-in arm**, not by re-plumbing the
  1,074 sites. Nothing here widens the read path beyond the two named reads.
* **A record miss is an absence; a record OUTAGE is not.** That asymmetry is the mirror of §17.2:
  the record is authoritative, so a miss there is a real absence — **but only where the record is
  complete**, which is a deployment fact, not a property of this code. `record` mode asserts it;
  `prefer` mode does not and pays for the doubt with a named, counted fallback. The endpoint answers
  **503** (never 404) for an impaired record, pinned by test. The one arm that is
  NOT an impairment is the tenant one: a credential that resolves no record tenant
  never becomes resolvable by retrying, so it is **500** (a permanent fault), and
  a role/GUC misconfiguration refuses **structurally** (500) instead of being
  counted as `record-unavailable` — an ops reason for a value problem.
* **`prefer` never lets the cache's availability decide whether the record is
  asked.** Its handle is constructed through the #3498 seam, but a construction
  failure is CAPTURED and re-raised only when the cache is actually needed —
  otherwise a stopped graph would answer `403 GRAPH_NOT_FOUND` before the record
  was consulted, making the lenient mode less able to answer than `record`. `off`
  keeps propagating the failure, exactly as before.
* **Not built here, named so the absence is visible:** a CLI verb (the two record verbs that exist,
  `record-reconcile` and `record-restore`, are the precedent); connection pooling for the endpoint's
  per-request record connection; `anchored-neighbours`; parity for any label other than `Point`
  (**#9721** — `:Event` loses the fold's `ON CREATE` defaults); a **cross-store CI lane** — the live
  parity and S9 legs need Postgres *and* FalkorDB, and no job provides both, so **both legs skip in
  every CI leg today** and the file is registered only so it is classified
  (`.github/actions/postgres-provision` exports no graph URI by design, #9551). It must be run by
  hand on a lane with both stores armed; do not read a green CI run as this file passing.
