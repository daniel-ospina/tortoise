---
title: "Plan — #133 Proportional Extraction v1: explicit-signal upgrade"
type: plan
domain: engineering
doc_status: planned
created: 2026-08-07
updated: 2026-08-07
ownedBy: epistemic-team
governingAgreement: "#133"
---

# Plan — #133 Proportional Extraction v1 (explicit-signal upgrade)

## Context

- **Issue:** #133 — proportional extraction follow-up to #125 (metadata-only capture)
- **Scoping:** double-diamond complete 2026-08-06. Heuristic auto-classifier **REJECTED** (message-count/keywords measure effort, not epistemic value). v1 = **explicit-signal upgrade only**.
- **PO decisions:** Q1 `--upgrade-all` ✅ | Q2 capture-default ✅ | Q3 needs_extraction = **frontmatter** ✅ | Q4 lazy EP re-propagation ✅ | Q5 auto-classifier **DROPPED** (UX decision from usage; product may diverge) | Q6 doc_status backfill ✅
- **TIER:** standard
- **Research path:** none needed — all changes in-repo (ingest.py, sdk.py, extension), zero third-party deps. Research-intake gate skipped per writing-plans §02.
- **UX gate:** skipped — pure CLI + frontmatter, no UI, UX_RATING=low.

## Design

### Problem
Sessions captured via #125 are metadata-only (Document with topics/summary/sourcePath). Full extraction (LLM → Points into belief graph) requires manual `tortoise ingest`. Today there is **no state machine** to distinguish shallow (`captured`) from deep (`extracted`) Documents, and **no programmatic trigger** to upgrade. High-value sessions stay shallow forever.

### Solution (v1)
1. **`doc_status` state machine**: `captured` (metadata-only) → `extracted` (full analysis). **Mechanism:** `--capture-metadata` mode defaults `doc_status` to `captured` (not `draft`); full ingest + `--upgrade` flips to `extracted` via an **explicit post-extraction SET** (raw Cypher `SET d.doc_status='extracted'` on the Document id), NOT through `add_document()` — because `add_document` always passes a non-null `doc_status` (default `draft`), and `coalesce($ds, d.doc_status, 'draft')` would overwrite `captured` with the frontmatter value. The frontmatter value is only authoritative on Document **creation**; upgrade transitions are explicit SETs.
2. **`tortoise ingest --upgrade <file>`**: re-run full extraction on a captured Document, then SET `doc_status` captured→extracted. **Idempotency:** if already `extracted` → no-op with message "doc already extracted, skipped" (checked before extraction; also gated by existing `begin_ingest` content-hash key).
3. **`tortoise ingest --upgrade-all`**: query `MATCH (d:Document) WHERE d.doc_status = 'captured' OR d.needs_extraction = true RETURN d.id` (via `proj.g.query()` inline in ingest.py — the SDK has no document-status query), upgrade each. **Loop-safety:** each attempt gated by `begin_ingest` key (content-hash + extractor version) — identical content skips re-extraction; changed content re-extracts (correct, not a loop).
4. **`needs_extraction: true` frontmatter flag**: the explicit "this session matters" signal — read by `_parse_frontmatter` into the DocumentCreated event, stored as `d.needs_extraction` property, consumed by `--upgrade-all` discovery.
5. **Lazy EP re-propagation**: after upgrade lands new Points, run `compute_confidence` on the affected subgraph on-demand (not a global recompute).
6. **Backfill migration**: `graph-scripts/backfill_doc_status.py` stamps existing Documents (no `doc_status` property) to `captured`.

### Capture-default (Q2)
`--capture-metadata` mode defaults `doc_status` to `captured`. The extension sets `doc_status: captured` in frontmatter; the CLI default for `--capture-metadata` must ALSO be `captured` (not `draft`) so manual capture runs get the right status. Test: `--capture-metadata` with NO `doc_status` in frontmatter → Document has `doc_status: captured`.

## Integration Surface Map

| # | Surface | Boundary | Test layer | Failure mode |
|---|---------|----------|-----------|--------------|
| 1 | ingest CLI `--upgrade` | CLI → ingest → add_document → DocumentCreated | integration (CLI smoke) | flag not wired, wrong doc flipped |
| 2 | `--upgrade-all` discovery | raw Cypher query `doc_status='captured' OR needs_extraction=true` → ingest loop | integration | query misses Documents, infinite loop (gated by begin_ingest key) |
| 3 | frontmatter `needs_extraction` | `_parse_frontmatter` → DocumentCreated event → `d.needs_extraction` | unit + integration | flag not read, default wrong |
| 3b | **needs_extraction bridge** | frontmatter flag → discovery query → upgrade | integration (e2e) | flag set but `--upgrade-all` misses the Document |
| 4 | doc_status transition | post-extraction Cypher SET (NOT add_document) | integration (projection) | coalesce-null wipe: add_document non-null doc_status overwrites `captured` (P0 — must use explicit SET) |
| 5 | EP re-propagation | sdk `compute_confidence` after upgrade | integration | not run, or global recompute |
| 6 | backfill migration | graph-scripts script | smoke | idempotency, wrong docs stamped |

## Tasks

### Task 1: doc_status state machine + needs_extraction flag

**Intent:** Give the Document lifecycle explicit states so captured vs extracted is queryable.
**Acceptance:** `--capture-metadata` writes `doc_status: captured` (default, even with no frontmatter field); `needs_extraction` frontmatter read into DocumentCreated and stored as `d.needs_extraction`; full ingest does NOT overwrite `captured` (transition is explicit SET, not add_document).
**Files:**
- Modify: `tortoise/ingest.py` (`_parse_frontmatter` + DocumentCreated emission; `--capture-metadata` default `doc_status="captured"`)
- Modify: `tortoise/api.py` (`add_document` — add `needs_extraction` param)
- Modify: `tortoise/projection/entities.py` (store `d.needs_extraction`)
- Test: `tests/test_ingest.py` (capture default `captured` with no frontmatter field), `tests/test_projection.py` (coalesce-null: partial update preserves `captured`)

### Task 2: `--upgrade` and `--upgrade-all` CLI flags

**Intent:** Provide the explicit upgrade trigger (PO Q1).
**Acceptance:** `tortoise ingest --upgrade <file>` re-runs extraction + SET `doc_status='extracted'`; already-extracted → no-op "skipped"; `--upgrade-all` discovers `doc_status='captured' OR needs_extraction=true` via inline Cypher (`proj.g.query`, no SDK method needed), upgrades each; loop-safe via begin_ingest key; `--upgrade` on a non-Document transcript → graceful "not a Document" message.
**Dependencies:** `--upgrade-all` discovery of pre-#133 Documents depends on backfill (Task 4) — in dev, seed Documents with `doc_status: captured`; in production, run backfill before `--upgrade-all`.
**Files:**
- Modify: `tortoise/ingest.py` (argparse + upgrade loop + post-extraction SET)
- Test: `tests/test_ingest.py` (CLI smoke: upgrade, upgrade-all, no-op re-run, non-Document)

### Task 3: Lazy EP re-propagation

**Intent:** New Points from upgrade enter the belief graph with propagated confidence (PO Q4).
**Acceptance:** After upgrade, `compute_confidence` runs on affected subgraph on-demand; no global recompute.
**Files:**
- Modify: `tortoise/ingest.py` (post-upgrade hook)
- Test: `tests/test_ingest.py` (EP runs after upgrade)

### Task 4: Backfill migration

**Intent:** Existing Documents get `doc_status: captured` so the state machine is consistent (PO Q6).
**Acceptance:** One-time script stamps all Documents without doc_status as `captured`; idempotent; safe on live graph (test_guard, test-prefixed graphs). **Run BEFORE `--upgrade-all` in production** (Task 2 dependency).
**Files:**
- Create: `graph-scripts/backfill_doc_status.py`
- Test: manual smoke on test graph

## Test Strategy

- Integration: `tests/test_ingest.py` — CLI flags, upgrade loop, status transitions (live-DB pattern, `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_133*`, `test_guard()`)
- Unit: frontmatter parsing (`needs_extraction`), api param passthrough
- Projection: coalesce-null regression (partial update preserves doc_status — the #167 bug class)
- Smoke: backfill script on test graph

## Review Handoff

Plan is drafted. Hand off to `plan-review` skill (parallel reviewers) before execution.
