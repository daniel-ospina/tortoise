---
title: "Integration surface map — E3, the D10 fold"
type: test-design
domain: engineering
doc_status: draft
created: 2026-09-24
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5088, integration surface map, D10 fold"
---

# Integration surface map — E3 (#5088), the D10 fold

**Skill:** `test-design` (planning-time). **Produces:** the surface map consumed by `writing-plans` / `executing-plans`.
**Epic:** #5088 — *the D10 fold: a document is a `:Source`, and sources are versioned.*
**Why this epic is the right first one:** it is **designed** (the rulings Q1–Q5 are recorded on #5013), **scoped**, and its window closes the moment the commit lane first runs.

> ⚠️ **This map is an intermediate artifact.** It feeds the epic's plan. It is not a plan.

---

## Step 1–2 — Surfaces and their layers

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| **1** | **the rule itself** — *what is a `:Source` and what is an entity* | **Semantic** | Both | **⭐ LABELLED DATASET** | the owner's ruling per real row (`label` / `band` / `source`) | prose drifts from practice; a clean sample hides the boundary by construction |
| **2** | `projection/edges.py` → `stub_key` · `STRUCTURAL_REL_LABELS` · `resolve_structural_target` | State (identity) | Both | **Round-trip** (build → rebuild → diff) | `aboutDocument` → label `Source`, key `url`; `extractedFrom` → `url`; **never `target.id`** | ⚠️ **silent mis-point**: label moved, key not ⇒ resolves to NOTHING ⇒ pass-2b mis-points the rebuilt edge **in silence** |
| **3** | `projection/entities.py` → `_upsert_source` · `_upsert_document` · `_SOURCE_HANDLED` | DB write | Out | **Integration** (real FalkorDB) | `_SOURCE_HANDLED` = the property allowlist; a field written but **not allowlisted is DROPPED** | ⚠️ **silent property drop** — the write appears to succeed |
| **4** | the **journal → replay** path (`rebuild_all`, pass-2b) | Event / State | Both | **Integration** — replay, then **diff the graph** | `derived = replay(journal)`; `DERIVABLE_STRUCTURAL_RELS` decides resurrection | a label change moves an edge class OUT of the derivable set ⇒ **durability regression, invisible until a rebuild is needed** |
| **5** | `quota.py` → `_count_resource('documents')` | DB read | In | **Integration** — real data, real predicate | ⭐ **`sourceKind = 'document'`** (corrected by the Q-S2 protocol, #5013) | ⚠️ **over-count**: `COALESCE(...)<>'transcript'` meters every session source (measured: **2,208**) — a price change disguised as cleanup. ⚠️ and the `documentKind IS NOT NULL` form **leaks**: a frontmatter-less doc has a NULL genre, becomes unmetered, and reopens **#1726** |
| **6** | `/v1/index/docs` (the gated endpoint) | External API | In | **E2E** | the `documents` resource still gates the endpoint | ⛔ **ungated**: retiring the resource instead of re-pointing opens exactly the hole #1726 closed |
| **7** | `projection/__init__.py` → `_ensure_indexes` · replay entrypoint | DB schema | Both | **Integration** | dropping `:Document` indexes, **not** adding a `:Source(documentKind)` index, is DELIBERATE (kind-field indexes cost a 3.15× write slowdown, #522) | an index added "for safety" silently costs write throughput |
| **8** | readers — `ingest.py` · `hosted_api.py` · `memory_orchestrator.py` (4 sites) + the transcript MERGE | DB read | In | **Integration** | every reader that asked for `:Document` must ask for `:Source` | a stale reader returns **empty, not an error** |
| **9** | `#5038` — `sourceVersion` on the `extractedFrom` link | State | Both | **Integration** + round-trip | version = `contentHash`; **stale is DERIVED**, never stored | a stored `stale` flag that is never cleared; an interval that is never **closed** |
| **10** | concurrent writers on the same graph (the capture lane is LIVE) | Concurrent | Contested | **Integration** | MERGE on `url` is the dedup mechanism | two writers creating two `:Source` nodes for one `url` |

---

## Step 3 — The integration checklist, applied

**Contract & data shape**
- [ ] The `:Source` property set is **explicit** (`_SOURCE_HANDLED`) and tested as an equality, not a sample — a field added to the writer but not the allowlist is dropped **silently**.
- [ ] `url` present on every `:Source` written — **a row with no `url` cannot be the identity** and must be an explicit, tested refusal rather than an empty key.
- [ ] Empty vs null: `documentKind` absent vs `''` vs `'transcript'` — each must land on a decided side of the count predicate.
- [ ] Boundary: the quota **at** the cap, at cap−1, and at cap+1 — the predicate change must not move the number that gates `/v1/index/docs`.

**Failure modes (per surface, enumerated)**
- [ ] #2 **silent mis-point** — the highest-severity mode in the epic. Needs a test that asserts the **resolved node**, not that the query returned.
- [ ] #3 **silent property drop** — assert the persisted property set, not the return value.
- [ ] #4 **replay divergence** — replay a captured journal and **diff the graph node-by-node, edge-by-edge**.
- [ ] #5 **predicate over-count** — run the old and new predicates over the *same* real corpus and diff the counts; the delta must be explainable row by row.
- [ ] #8 **stale reader** — returns empty, never errors.
- [ ] #10 **duplicate identity** — two concurrent writes on one `url` yield one node.

**Data integrity**
- [ ] **Idempotency** — a re-index of unchanged content must report *unchanged*, not *updated* (`test_idempotent_rerun`). ⚠️ Lane D's plan found the document→corpus collapse **breaks this**: the `#205 Source→Document` edge becomes a self-loop, is dropped, and the index-completeness gate then reports every unit *incomplete* forever.
- [ ] **Atomicity** — the label+key move and the quota re-point land together.
- [ ] **Ordering** — **#5026 before #5025** (both edit the same resolver). This is enforced by sequence, not by the tests.

---

## Step 4 — Bug-pattern flags

| Bug pattern | Detected? | Required verification |
|---|---|---|
| **Silent function skips** | ⚠️ **YES — the epic's core risk.** A retargeted label with an unmoved key resolves to nothing and pass-2b mis-points silently. | Assert the **resolved target node identity**; a passing query is not evidence. |
| **Conditional guards** | ⚠️ **YES.** `_SOURCE_HANDLED` (the allowlist) + the counts predicate (**`sourceKind = 'document'`** — corrected by the Q-S2 protocol; see surface #5) | Boundary tests on **both sides of each guard**; assert the persisted set. |
| **Race conditions** | ⚠️ **YES.** The capture lane writes concurrently; MERGE-on-`url` is the dedup. | Two concurrent writes, one `url` → one node. |
| **N+1 queries** | ⚠️ Possible in the 4 reader sites. | Flag: batch the reader queries; test with N > 1. |
| **Stale closures** | — n/a (Python, no React) | — |
| **SQL business logic** | — n/a (Cypher + Python, no Postgres functions) | — |

## Step 5 — Property-based testing

The surfaces here are **not** PBT-appropriate in the vitest/fast-check sense — the invariants are graph-level, not value-level. But two properties **are** worth stating as executable invariants in the integration suite:

- **Idempotency:** `ingest(x); ingest(x)` ≡ `ingest(x)` — same graph, same counts, no second version bump.
- **Round-trip:** `rebuild(replay(journal)) ≡ graph` — for the full node and edge sets, not a sample.

---

## ⭐ The layer this system has been missing — surface #1

Every surface above can be green while the **system is wrong**. Nothing here can check *"should this claim have been extracted at all?"* — that is a judgement, and a judgement needs **labelled rows**.

**⇒ Surface #1 is tested by the labelled dataset, and the dataset is built by the owner-in-the-loop loop** (`METHODOLOGY 2`): sample real rows → apply the rule by hand → show input + output → the owner rules → log the ruling as a labelled row → the taxonomy of rulings sharpens the next sample.

The dataset schema follows the repo's existing precedent, `tests/fixtures/labeled_pairs.jsonl`:

```json
{"input": "<the real row>", "output": "<what the rule produced>",
 "label": "<the owner's verdict>", "reason": "<why — so a later reader can disagree with the rule, not the row>",
 "band": "<coverage category, so the dataset can be audited for what it lacks>",
 "source": "<provenance>"}
```

**`band` is what makes METHODOLOGY 1's "include the borderline cases" auditable rather than a promise**, and the `reason` is what makes the owner's ruling survive the session that produced it.

---

## Honest boundaries of this map

- **Not verified:** that the 4 reader sites are exhaustive (counted from the epic, not by an exhaustive call-graph walk) · that no other module reads `:Document` · the exact current `_ensure_indexes` body.
- **A surface map is a claim about where bugs will be** — it is falsifiable by the implementation, and should be revisited if the first integration tests pass trivially.

## Handoff

This map is the **integration-surface map** gate for **E3 (#5088)**, sitting between the epic's *Scope* and *Plan* stages. Continue to the epic's plan with this map as input.
