---
title: Plan — #1370 write-time subject binding for points
type: engineering
domain: capability
doc_status: live
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-memory-capture, aboutSubject, subject-binding, write-path
created: 2026-09-24
---

<!-- research-path: none — codebase-first; owner-locked decisions; zero third-party deps -->

# Plan — #1370 write-time subject binding for points

> **Issue:** #1370 · **Scoping:** `docs/scoping/2026-09-24-1370-write-time-subject-binding.md`
> · **Tier:** Standard · **Branch:** `feat/1370-write-time-subject-binding`
> · **Research path:** codebase-first; owner-locked decisions; zero third-party deps → Phase-1.5
> external-research skip is justified (see scoping doc).
> **Class:** B (mechanical architecture conformance). Every test docstring answers
> (1) what value makes this test fail? (2) does the fixture contain a row where that value is reachable?

## Design decisions (locked after solution-verify + duplication review — see cycle log in scoping doc)

1. **The binder is the ONLY confidence-gated `aboutSubject` producer.** The legacy
   `about_entities` channel is topic annotation; it must **not** emit `aboutSubject`.
   Subject-kind names are therefore **skipped** by every `about_entities` resolver
   (local point loop, hosted point loop, hosted event loop). For the hosted **event**
   resolver this is a DELIBERATE drop, not a hand-off: that loop writes `aboutObject`
   only and the gated binder reads a Point's `slots`, so an Event's subject-kind
   attribution is intentionally not replaced — the only permitted Event→Subject writer
   would be an un-gated one. The skip is explicit, commented, and test-pinned.
2. **Kind routing is the root fix** (also #4934): entity kinds in the declared §5 Subject
   vocabulary are created as `:Subject` (`create_entity("subject", …)`), everything else
   stays `:Object`. No `:Subject` is minted by the binder (D8).
3. **Journaled carrier = existing `EntityLinked` + optional `confidence`.** No new
   graph-state event. Conditional `SET r.confidence` in BOTH the live MERGE and the replay
   fold ⇒ live==rebuild (for journal-configured SDKs; hosted SDKs are journal-less and
   live-only, as today). When the legacy `about_entities` channel already wrote the edge,
   `link_entity`'s pre-probe short-circuits — but `link_entity` returns 0 for that state AND
   for an ABSENT endpoint pair, so the binder RE-PROBES the edge and applies the confidence
   to the EXISTING edge (emitting the same journaled `EntityLinked` record) ONLY when the
   edge is genuinely present. An absent pair is refused (`unresolved`) and emits no record —
   a phantom `EntityLinked` would resurrect an edge the live graph never had. Refusals use
   a JSONL-only, non-folding `EntityBindingRefused` audit record, registered in
   `_NO_PROJECTION_FOLD`.
4. **Tri-state gate, fail-closed.** `bound ≥ τ_hi` / `suspected ∈ [τ_lo, τ_hi)` /
   `unbound < τ_lo`. Defaults `τ_hi=0.7`, `τ_lo=0.4`. Thresholds are validated
   fail-closed: finite with `0.0 < τ_hi ≤ 1.0` and `0.0 ≤ τ_lo ≤ τ_hi`; an out-of-range,
   inverted or ALL-ZERO (`0.0/0.0`) pair falls back to the shipped defaults (a `0.0`
   upper bound would make a zero-confidence slot `bound`). Non-numeric/None/NaN ⇒
   unbound. Unknown/non-subject kind ⇒ unbound (never invented). Unresolvable name ⇒
   unbound, no stub.
5. **Only `subject`→`aboutSubject` and `object`→`aboutObject` roles are bound.** The
   `event` slot is explicitly NOT bound here (#1417 content-edge contract).
6. **Subject kind classification is derived from `extractor_v2.SUBJECTS`** (bare names),
   with `other` deliberately excluded (fail-closed NIL bucket) and a drift test pinning
   the exclusion.

## Integration Surface Map (test-design)

| # | Surface | Type | Data Flow | Test Layer | Contract | Key failure modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `subject_binding.decide_binding` | Pure logic | Internal | Unit | `(confidence, tau_hi, tau_lo) → bound\|suspected\|unbound` | NaN/None/non-numeric; exactly-at-threshold; inverted thresholds |
| 2 | `subject_binding.is_subject_kind` | Pure logic | Internal | Unit | bare-name ∈ `SUBJECTS` compared as the RAW string (no whitespace normalization); `other` excluded | namespaced vs bare; unknown pack kind; whitespace variant; `other` |
| 3 | `link_entity` + `EntityLinked` | Event/journal + DB | Out | Integration (Falkor + JSONL) | record `{source_id, source_label, target_id, target_label, edge_type, confidence?}` | confidence dropped on replay; unconditional SET clobbers a later no-confidence record |
| 4 | `_fold_entity_linked_reason` | DB replay | In | Integration (rebuild parity) | edge props byte-identical after wipe+replay | live==rebuild divergence |
| 5 | Local capture seam (`_extract_session_v2`) | DB write | Out | Integration (Falkor) | Point→aboutSubject edge; Subject node created; no Object duplicate | un-gated edge from about_entities; stub minted; Object+Subject duplicate |
| 6 | Hosted commit seam (`_execute_commit_writes`) | DB write | Out | Integration (Falkor) | parity with #5 on the same payload | seam drift; raw name-MERGE stub |
| 7 | `about_entities` resolvers (local + hosted pt + hosted ev) | DB write | Out | Integration (Falkor) | subject-kind names produce NO edge | un-gated aboutSubject; id-less Object stub |
| 8 | Entity supersession (`apply_supersessions`) | DB write | Both | Integration (Falkor) | subject-kind successor ⇒ explicit warn+skip | misleading "dangling successor"; silent inversion |
| 9 | Read surface `fetch_point_epistemic_state` | DB read | In | Integration (Falkor) | `subject` appears on a bound point; `subject_unavailable` self-clears | chain-derived subject (must stay absent) |
| 10 | `audit_subject_binding` + CLI + fixture | Tooling | Both | Unit + Integration | `{points, bound, suspected, unbound, no_slot, unbound_fraction}`; misattribution over gold | denominator wrong; rate not reproducible |
| 11 | `config/ci-surfaces.yml` | Infra | — | integrity | new test file registered under `core` | `ci_selection --integrity` red |

### Bug pattern flags
- **Conditional guard**: every `if confidence >= tau_hi` branch needs both-side tests.
- **Silent function skips**: the "refused" path must be journaled, not dropped.
- **Ambiguous zero return**: `link_entity` returns 0 for an already-existing edge AND for an absent endpoint pair — never infer "already present" from 0; re-probe the edge.
- **N+1 queries**: `bind_point_subjects` bounds per-point work to the slot count (slots per point ≤ ~3) — one resolver Cypher per slot; there is no batch wrapper (each seam keeps its own create-gating and per-point isolation).

## Task 1 — decision core (`tortoise/subject_binding.py`)

**Intent:** the pure policy: subject-kind classification + the tri-state confidence gate.
**Acceptance:** `is_subject_kind` true for the 5 declared subject kinds (bare/formatted),
false for `other`/objects/unknown AND for whitespace variants (`' core:team '`,
`'core:team\n'`); `decide_binding` returns bound/suspected/unbound at the exact boundaries
and unbound for non-numeric/NaN; `resolve_thresholds` rejects an all-zero/inverted/out-of-range
pair to the shipped defaults.
**Files:** Create `tortoise/subject_binding.py`; Test `tests/test_subject_binding_1370.py`.

## Task 2 — journal carrier (`session_link.py`, `projection/entities.py`, `projection/__init__.py`)

**Intent:** bindings auditable + replayable with confidence; refusals tracked, not dropped.
**Acceptance:** `link_entity(confidence=0.8)` sets `r.confidence` live and journals it; a journal-only rebuild reproduces `r.confidence` byte-identically; a later `EntityLinked` without `confidence` does not clear it; `EntityBindingRefused` does not warn on replay.
**Files:** Modify `tortoise/session_link.py`, `tortoise/projection/entities.py`, `tortoise/projection/__init__.py`; Test same file.

## Task 3 — binder driver + local seam

**Intent:** write the gated Point→aboutSubject edge at capture time.
**Acceptance:** a point with a ≥τ_hi subject slot gets `(Point)-[:aboutSubject]->(:Subject)`; <τ_lo writes none; kind routing makes the entity a `:Subject` and no `:Object` duplicate; subject-kind names in `about_entities` write no edge and mint no stub; a `link_entity` 0 result is RE-PROBED — an absent Point/Subject pair (a nonexistent Point id, or an `eventId`-only Subject stub) is refused `unresolved` and emits no `EntityLinked`.
**Files:** Modify `tortoise/sdk.py`; Test same file.

## Task 4 — hosted seam parity

**Intent:** the hosted commit path binds identically.
**Acceptance:** the same slot payload driven through `_execute_commit_writes` yields the same binding records/edges as the local seam, bound on the id `create_point` resolved the write to (never the payload id; absent resolved id ⇒ no bind).
**Files:** Modify `tortoise/hosted_api.py`; Test same file.

## Task 5 — supersession guard

**Intent:** routing subject kinds to `:Subject` must not silently break entity supersession.
**Acceptance:** a subject-kind successor yields an explicit, accurate warned skip (not a misleading "dangling successor"); object-kind supersession is unchanged.
**Files:** Modify `tortoise/commit_ops.py`; Test same file.

## Task 6 — audit tooling + quality gate

**Intent:** indicators 3 + 4.
**Acceptance:** `audit_subject_binding(graph)` reports the fractions with a point-level
`bound_fraction` (`DISTINCT bound Points / all Points`, never > 1.0) and a Point-scoped
journal denominator; `tools/subject_binding_audit.py --gold` prints the misattribution +
unbound rates and a `--calibrate` sweep; the metric is **load-bearing** —
`quality_gate_metrics` seeds a throwaway graph from each row's `graph_nodes` and runs the
REAL `bind_point_subjects` / `_resolve_target` path, reading the resolved subject off the
graph; a test pins the default-threshold misattribution rate on the authored fixture at
≤10% (measured `1/13 ≈ 0.077`), and another pins that flipping a row's `graph_nodes`
changes the rate.
**Files:** Create `tools/subject_binding_audit.py`, `tests/fixtures/subject_binding_gold.jsonl`; Modify `tortoise/subject_binding.py` (audit + metrics live there, not in `tortoise/audit.py` — no shared-module change); Test same file.

## Task 7 — CI registration + surface checks

**Intent:** the tests actually run in CI; no accidental surface change.
**Acceptance:** `config/ci-surfaces.yml` lists the new test under `core`; `python3 tools/ci_selection.py --integrity` exits 0; `tools/surface-guard.py` + `tools/surface_manifest.py check` pass (no new public SDK/MCP member).
**Files:** Modify `config/ci-surfaces.yml`.

## Risks
- **`sdk.py` is a SHARED_MODULE → full CI matrix.** Expected; the lane is expensive but correct.
- **Hosted SDKs set no `event_log_path`** → binder edges there are live-only (pre-existing behavior). Documented, not fixed here.
- **Real LLM τ calibration (D4) is NOT covered** — the fixture measures policy only.
- **#4934's hosted 29-row backfill is NOT covered** — a data migration.
