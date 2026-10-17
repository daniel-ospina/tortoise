---
title: "The completeness marker and the second divergence surface — `not-hot`, never empty (#7952)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-12
updated: 2026-10-12
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7952, epic #7879, interfaces B and D, completeness marker, reconciliation, known-hot set, Postgres record, FalkorDB cache"
---

# The completeness marker and the second divergence surface

**Issue:** **#7952** (P0-blocker, lane `c6-durability`) — epic **#7879**, §17.2's two requirements.
**Ruling:** `STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the system of record; FalkorDB is a derived hot cache.** §17.2 and §17.3 state the consequences; **neither is a new decision.**
**Interfaces:** **B** (Postgres → FalkorDB, the declared hot set) · **D** (the read path, part 1 — the marker).
**Test-design consumers:** `#7967` surfaces **S3** (reconciliation) and **S4** (the marker).

> ⚠️ **This is the MARKER half of #7952.** The residency **router** that acts on
> a `not-hot` verdict is **`#7953`** — it consumes this marker and is not built
> here (the §17.3 ordering edge `#7952` → `#7953`). The cache projection is
> **`#7971`**; the record read path is **`#7970`**.

## 1. What was wrong

**§3 covers ONE divergence surface** — journal ↔ projection. The hybrid ruling adds
a **SECOND**: Postgres (truth) ↔ FalkorDB (cache), **with no mechanism**. Two
requirements follow, and both fail silently without them:

1. **`truth == cache` was not checkable at all.** A chokepoint violation (§17.1 /
   `#7951`) is invisible, and an invisible violation is indistinguishable from
   success.
2. **A partial cache cannot reveal its own coherency bugs.** *"No such node"* and
   *"not in the cache"* are the **same wire answer** — `[]` — so the cache
   silently answers *"nothing"* to a question it cannot see. A wrong answer and
   a right answer are both `200`.

## 2. What this change builds

| artifact | what it is | the property it makes checkable |
|---|---|---|
| `tortoise/residency.py` | the marker's **ONE home** — the two verdicts, the classifier, §17.3's status rule | a read outside the known-hot set says **`not-hot`**, never `empty` |
| `tortoise/reconciliation.py` | the second divergence surface's check | divergence is a **NUMBER**, split by direction |
| `tortoise/sdk.py` (read path) | the marker on the **shipped** read path, behind its own flag | the field is on the response the caller RECEIVES |
| `tortoise/hosted_api.py` | the `residency` field on **`GET /v1/search`** | the marker is **customer-callable**, not internal |
| `config/surface-manifest.yml` | the `response_fields` record | the #3863 rule: an emission is recorded |

### 2.1 The marker is a directive, not a fifth term

`hot` / `not-hot`. It is **its own response field** (`residency`), never a term
of `available | empty | degraded | unconfigured` — that vocabulary's ONE home
(`tortoise/status_vocabulary.py`) forbids a fifth term, and **a residency
verdict answers a different question**: not *"what is the status of this read"*
but *"could the store I consulted SEE the answer"*.

**The polarity, stated once and not softened:**

* **COMPLETE** declaration → `hot` (everything is hot; a miss IS a finding).
* **PARTIAL** + `hits > 0` → `hot` (something the caller asked for came back).
* **PARTIAL** + a named key **inside** the declared set → `hot` (a miss here is
  a finding *within* the store).
* **PARTIAL** + zero hits, key outside/absent → **`not-hot`**.

### 2.2 §17.3's consequence, in one place

Under a PARTIAL declaration **`hits == 0` no longer establishes `empty`**. A
zero-hit read of a partially-covered store is **`degraded`** when the truth
fetch could not be made — `tortoise/residency.py::apply_residency`. When the
record *was* consulted and held nothing, the read is **`empty`**: a real
finding. Nothing else moves — `available` / `degraded` / `unconfigured` pass
through untouched.

### 2.3 Reconciliation: a number, split by direction

`reconcile_views(truth, cache)` returns integer counts —
`checked` · `divergent` · `missing_in_cache` · `extra_in_cache` ·
`field_mismatches` — plus a bounded key sample. Two decisions are load-bearing:

* **the two directions are counted APART.** `missing_in_cache` is a projection
  lag; **`extra_in_cache` is the second-writer signature** — something wrote the
  cache the record does not have, i.e. the chokepoint is being violated. A
  single boolean (or a single count) cannot express that difference, which is
  why there is no `coherent`/`ok` accessor.
* **an unreadable side REFUSES.** `reconcile()` raises
  `ReconciliationUnavailable` rather than returning a zero-divergence report: a
  check that cannot read its evidence must not report success (the #1382 class).

## 3. The declaration — declared, not implicit (interface B / §2.6)

`ResidencyDeclaration` carries `coverage` (`complete` | `partial`), an optional
enumerated `hot_keys`, and a `source` naming where it was read.

* **The default is `complete`, and that is the §2.0 posture**: the FalkorDB
  limit is held high precisely so partiality is *deferred*, and **with a
  complete cache the marker cannot fire** (§17.3, *deferred, not absent*).
* **The interim home is the graph's keyed `:Meta` marker.** The plan names the
  *authoritative* home as the control-plane residency metadata (§2.6, written
  by `#7971`); until it exists, the reader takes `:Meta{key:'residency'}`
  (the `config_reset` idiom) and **fails SAFE** — a missing, malformed or
  unreadable declaration is `complete` with `source` naming what happened. The
  failure direction is *stay quiet*, never *fire falsely*.
* **What `#7971`/`#7970` must do:** populate the declaration and supply the two
  readers; they must **not** re-implement the classifier (`mark_residency`) or
  the status rule (`apply_residency`).

## 4. Alternatives rejected

| alternative | why not |
|---|---|
| **`not-hot` as a fifth status term** | §17.3 forbids it: the vocabulary is RECORDED and its own text refuses a fifth term. It would also collapse *"where to look"* into *"what happened"*, which is exactly the distinction the router (`#7953`) needs. |
| **A boolean coherence verdict** | A boolean cannot separate a projection lag from a second writer, and cannot distinguish either from *"nothing was compared"*. The issue asks for a number for this reason. |
| **Returning `[]` plus a log line** | Invisible to the caller. The whole defect is that the caller cannot tell *"nothing"* from *"I could not see"*. |
| **Firing `not-hot` on a DOWN store** | An outage is not a residency miss — a cache that is down would be reported as *"outside the known-hot set"*, which is false and misroutes the repair. The field stays **absent** and `status=degraded` names the impairment (test-design S5's own failure mode). |
| **Deriving the marker from the cache alone** | Circular: it is the cache's *inability to see* that the marker reports. The verdict must come from the declaration, not from a probe of the store it describes. |
| **Silently reporting zero divergence when a side is unreadable** | Fail-open. Covered above. |

## 5. Wiring check

| touch point | type | covered by | status |
|---|---|---|---|
| `tortoise/residency.py` | new module (marker) | this issue | ✅ |
| `tortoise/reconciliation.py` | new module (the second surface) | this issue | ✅ |
| the read path's status sink | `tortoise/sdk.py` | this issue (flag-gated, byte-identical when off) | ✅ |
| `GET /v1/search` response field | `tortoise/hosted_api.py` | this issue (own flag) | ✅ |
| the field's record | `config/surface-manifest.yml` → `response_fields` | this issue | ✅ |
| the declaration's authoritative home | control-plane residency metadata | ⚠️ **`#7971`** (interim `:Meta` reader here) | ⚠️ |
| the two production readers | record (`#7970`) · cache (`#7971`) | ⚠️ not built; `reconcile(readers)` is the seam | ⚠️ |
| the residency router | `#7953` | ⚠️ out of scope — consumes this marker | ⚠️ |

## 6. Verification

`Verification: see test-design #7967 — surfaces S3, S4.`

* **S4 · the marker** — `tests/test_residency_marker.py`: the two verdicts; the
  four polarity cases; §17.3's status rule; the declaration reader's fail-safe
  cases; the presence set (`not-hot` is not `[]` / `None` / `{}`); the marker
  **end-to-end on a real embedded store** (a partial declaration returns
  `not-hot` and the status is `degraded`, never `empty`; the same read with a
  complete declaration is `hot` + `empty`); the marker's **absence** on a store
  that will not open; the composite coalescing (`recall_state`); and the
  **hosted `/v1/search` route** — absent by default, each field gated by its own
  flag.
* **S3 · reconciliation** — `tests/test_reconciliation.py`: divergence is an
  `int` (and not a `bool`); the two directions are separately counted; a
  bounded sample with exact counts; no boolean verdict on the report; and both
  refusal paths (`ReconciliationUnavailable`), including a reader that returns
  a non-keyed view.
* **Off by default** — with `TORTOISE_RESIDENCY` unset the read path never
  reads a declaration and the response is byte-identical; the `residency` key
  is absent (`tests/test_residency_marker.py::test_the_marker_is_absent_with_the_flag_off`,
  `TestHostedSearchRoute`).

## 7. Out of scope (stated, not absorbed)

* the **residency router** (`#7953`) — fetch-from-truth, and the boundary
  against the sufficiency router;
* the **cache projection** (`#7971`) — the record materialised to hot;
* the **record read path** (`#7970`) — which reads serve Postgres directly;
* the **partiality trigger** — with a complete cache the marker cannot fire,
  and *becoming* partial (a quota cap, the `#4333` ceiling) is what turns this
  from designed-and-deployed to load-bearing.
