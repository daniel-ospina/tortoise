---
title: "The residency router — a cache miss is not an insufficiency (#7953)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-13
updated: 2026-10-13
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7953, epic #7879, interface D, residency router, sufficiency router, completeness marker, known-hot set, Postgres record, FalkorDB cache"
---

# W7 — the residency router (`#7953`, §17.3)

> **Interface D, part 2.** The marker (W6b, `#7952`) is the router's INPUT; this
> is the router. The cache projection (W10, `#7971`) is what makes the
> declaration partial. The record read path (W8, `#7970`) is the consumer that
> performs the fetch — **deferred by §2.0, P2**.
> **Architecture of record:** `docs/architecture/STORAGE-ARCHITECTURE.md` §17.3
> (the rule and the two-router table), §12.3 (the completeness assumption),
> §12.4 (the sufficiency router). **Surfaces:** S4, S5 (`01-test-design.md`).

## 0. The problem

§12.3's tier table assumes each tier is **complete**. Under §2.0's hybrid,
**FalkorDB is partial by design** — the limit is held high so a miss is
**deferred**, not absent. §12.4's sufficiency router routes on **answer depth**;
it is **not** a residency router. Collapsing the two events either escalates
unnecessarily or — worse — **treats a cache miss as a negative finding**, the one
wrong answer that is indistinguishable from a right one (§17.2).

| event | correct response |
|---|---|
| *"the evidence I hold does not answer this"* | **escalate a tier** (§12.4) |
| *"this node is not in the cache"* | **fetch from the truth store** (§17.3) |

## 1. The artifact

`tortoise/residency_router.py` is the router's **ONE home**. It is a **pure
decision** — no I/O. Its input is §17.2's marker (plus §12.4's sufficiency
verdict and the cache's reachability); its output is ONE canonical route. It
never fetches the record and never walks a tier, which is what keeps it a
*router* rather than a re-implementation of the read path or of §12.4.

| route | meaning |
|---|---|
| `serve` | the cache's own answer stands; no repair is warranted |
| `fetch-record` | the **residency** repair — fetch from the truth store (Postgres) |
| `escalate-tier` | the **sufficiency** repair — go deeper (§12.4) |

Each decision also carries a `reason` naming **which event fired** — `resident` ·
`residency-miss` · `cache-unreachable` · `insufficiency` — so the two events, and
the outage, are distinguishable *at the point where the decision is made*. It
carries the `coordinate` a `fetch-record` repair must be fetched by.

## 2. The precedence — and why it is the collapse guard

**Residency outranks sufficiency.** A node the cache cannot see makes every
sufficiency verdict undecidable: the evidence in hand is *necessarily*
incomplete, so *"insufficient"* has not been established — and walking to a
colder tier of the SAME store that never held the node cannot repair it. §17.3:
the miss is repaired at the record. The order is:

1. the cache did not answer → `fetch-record` / `cache-unreachable`
2. the marker says `not-hot` → `fetch-record` / `residency-miss`
3. resident (or unassessed) and the evidence is short → `escalate-tier`
4. otherwise → `serve`

## 3. The four branches, and the state each names

| event | evidence | route | reason |
|---|---|---|---|
| the node is outside the known-hot set | marker `not-hot` (§17.2) | `fetch-record` | `residency-miss` |
| the cache is DOWN | `cache_reachable=False` — an outage leaves the marker **absent** | `fetch-record` | `cache-unreachable` |
| resident, the evidence is short | marker `hot` + sufficiency `False` | `escalate-tier` | `insufficiency` |
| resident (or the marker never ran) | marker `hot` / `None` + sufficiency not `False` | `serve` | `resident` |

**The fourth branch — a down cache — is test-design `#7967` S5's own addition,
and §17.3 does not name it.** A store that cannot be reached is a store that
cannot see: routing it as *"no evidence"* is the same failure as routing a
residency miss that way. It is a **residency miss**, and its `reason` keeps it
from being confused with a declared `not-hot`.

**`marker=None` means NOT ASSESSED, never `hot`.** It is the marker's flag being
off (the §2.0 default, under which the cache is complete and the marker cannot
fire) or the store already having been shown unreachable (covered by branch 1).
The decision carries `marker=None` so a consumer can see residency was never
established. An unknown verdict is **refused** (`ValueError`), not guessed: a
typo'd verdict must not silently take a branch.

## 4. The per-key path — a PARTIAL declaration routes an UNWRITTEN key to the record

`route_for_coordinate(declaration, *, label, key, hits, scope="", …)` is the
entry for a read that **named a node**. It builds the coordinate with
`cache_projection.declared_key` (`#7971`, interface B) — the SAME function the
cache projection declares hot keys with — so the writer of the key and the
reader of the key speak one vocabulary rather than agreeing by accident. `label`
is part of the coordinate, never discarded: `:Subject` and `:Object` share the
`name` namespace, so a bare key would let a partial cache declare `complete`
(`#7971`'s P0).

The polarity, stated once and not softened:

* **COMPLETE** declaration → `hot` → `serve` (the §2.0 posture; the marker cannot fire).
* **PARTIAL** + `hits > 0` → `hot` → route on sufficiency.
* **PARTIAL** + a key the declaration **covers** + `hits == 0` → `hot` → `serve`.
  A miss *inside* the known-hot set **is** a finding within the store.
* **PARTIAL** + a key it does **not** enumerate + `hits == 0` → `not-hot` →
  `fetch-record`. This is the deliverable: an unwritten key routes to the record,
  **never** to an absence.

## 5. The reader change — the coordinate must be observable

The marker can only separate *"outside the hot set"* from *"inside it and absent"*
when the read **names a node**. Before this unit the shipped read path called
`mark_residency(declaration, hits=len(rows))` with **no key**, so the per-key
branch was exercised only under test — and the router cannot route on what the
reader does not report.

`tortoise/sdk.py::tortoise_fts_query` therefore takes a private
`_residency_key` (the `_elevated_timeout_ms` precedent for a private,
byte-identical-when-unused parameter) and hands it to `mark_residency`. It is
**opt-in**: a SET read names no node, leaves it `None`, and keeps today's
set-level polarity — so the default deployment is byte-identical.

⚠️ **Recorded, not hidden.** No *existing* production caller names a single node
coordinate on this read path: `tortoise_fts_query` and `recall_state` are set
reads, and `GET /v1/search` takes only `q`/`limit`. The node-naming read that
will drive the per-key branch in production is `#7970`'s (deferred by §2.0). The
seam and its test exist now so that path has one place to pass a coordinate and
one router to consume it.

## 6. Not a fifth status term, and not the sufficiency router

The three route names are a **directive about where to look**, exactly as
`hot`/`not-hot` is — deliberately disjoint from the RECORDED four-term read-status
vocabulary `available | empty | degraded | unconfigured`
(`tortoise/status_vocabulary.py` is its ONE home and forbids a fifth term). A
test pins the disjointness. The status consequence of a miss is already §17.2's,
in one place: `tortoise/residency.py::apply_residency` turns a `not-hot` `empty`
into `degraded` when the record was not consulted — **never `empty`**.

## 7. Verification

`tests/test_residency_router.py` — the surface S5 contract:

* the decision and its precedence, including **the collapse guard**;
* the four branches, boundary-tested on both sides (a guard that cannot fail is
  not a guard);
* the per-key path: a covered key `serve`s, an unwritten key `fetch-record`s, and
  the scope is part of the coordinate;
* the vocabulary boundary (a route is not a status term);
* end-to-end on a real store: a partial declaration's miss returns `not-hot`,
  its `status` is **never `empty`**, and the route is `fetch-record`.

**Mutation proofs** (revert → RED → restore; the verbatim output is in the PR
body): collapse the two events · route a down cache as no-evidence · report a
partial miss as an absence · drop the coordinate at the reader · mint a route as
a status term.

## 8. Out of scope (stated, not absorbed)

* **the record read path** (`#7970`) — it performs the `fetch-record` repair;
  **deferred by §2.0, P2**, and no P0 unit may wait on it;
* **the sufficiency router itself** (§12.4 §12.8 item 3) — a separate artifact;
  this router routes *to* it, and never makes the depth decision;
* **the declaration's authoritative home** — control-plane residency metadata
  (§2.6); the interim `:Meta` reader is `#7952`'s;
* **the write-back** (§12.5) — §12.8 item 4.
