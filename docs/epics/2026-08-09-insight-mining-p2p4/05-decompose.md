---
title: "Epic Decompose — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Decompose — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4

**Date:** 2026-08-09
**Status:** draft — 9 child issues created; per-issue review + MECE gate passed (round 2: reviewer defects fixed — dep shorthand collisions, missing #782/#783 deps, DE2E-N3 ownership, calibration_passed pin, Variant C split)
**Input:** 04-plan.md (8 substeps, coherence review passed); 03-scope.md (7 high-level E2E)

---

## 1. Work Units (from plan §5.1 components + §6.3 sequencing)

| # | Issue | Component (plan §5.1) | Gate | DE2E |
|---|---|---|---|---|
| 1 | #779 Gate B tooling + calibration milestone (incl. `calibration_passed()` marker + reader — owned here, reused by #787) | Gates (§5.1) | ungated tooling; milestone = Gate B | DE2E-4 setup, feeds DE2E-7 |
| 2 | #780 EP draft filter + draft operator nodes | EpSafeCommit (ep.py side) | ungated (§6.3 step 2; ordered after #779 — DE2E-4 needs mean_grounding helper) | DE2E-4 |
| 3 | #781 INSTANTIATES→aboutObject wiring + ranking (ingest-path variant) | Wiring fix | ungated (§6.3 step 3) | DE2E-5 ingest-path variant (mining variant re-verified in #782) |
| 4 | #782 Phase-2 entity extraction stage | EntityStage + pipeline entry points | #320 (Gate A) + Gate B | DE2E-1, N1/N2/N5/N7/N8/N12, DE2E-5 mining variant |
| 5 | #783 Phase-2 EntityResolver | EntityResolver | #320 + Gate B + #782 | DE2E-2, N3 |
| 6 | #784 Phase-2 content dedup | ContentDedup | #320 + Gate B + #782 + #783 + #780 | DE2E-3 (Variants A+B; Variant C in #785), N4/N11/N13 |
| 7 | #785 Phase-4 EP-safe batch commit + promote_point (+ list_quarantined) | EpSafeCommit (mining side) + Promotion | #320 + Gate B + #780 + #779 + #782 | DE2E-8, N6/N9, DE2E-3 Variant C |
| 8 | #786 Phase-4 temporal belief wiring | TemporalWire | #320 + Gate B + #782 + #785 | DE2E-6 |
| 9 | #787 Phase-4 MCP surface + workflow-layer check_gates (reuses #779's calibration_passed) | MCP surface + gates | #320 + Gate B + #782..#786 | DE2E-7, N10 |

## 2. Dependency Graph

```
ungated:   #779 ──► #780            #781 (parallel to the #779→#780 chain)
                │
gated:         ▼
           #782 (needs #320+GateB) ──┬──► #783 (needs #782)
                                     ├──► #784 (needs #782 + #783 + #780)
                                     └──► #785 (needs #780 + #779 + #782)
                                           └──► #786 (needs #785 + #782)
#782+#783+#784+#785+#786 ──► #787 (MCP wraps all; check_gates reads these links)
```

- **Ungated serial chain:** #779 → #780 (DE2E-4's grounding-delta assertion depends on #779's mean_grounding helper — code-ordering, not a human gate). #781 runs parallel to that chain.
- **Parallel tracks post-#782:** #783 and #784 and #785 fan out (with intra-deps: #784 needs #783's queue API; #785 needs #780/#779). Longest chain #779→#780→#785→#786→#787 (5 hops).
- **Acyclic:** yes.
- **Gates:** every gated issue (4–9) also depends on Gate A (#320 STABLE) + Gate B (calibration milestone #779 pass) — enforced by workflow-layer `check_gates` (#787 work item), not SDK.

## 3. MECE coverage walk (plan §5.1 components → issues)

| Plan component | Covered by |
|---|---|
| EntityStage (extractor.py) | #782 |
| EntityResolver (entity_resolver.py) | #783 |
| ContentDedup (sdk.py _semantic_dedup) | #784 |
| EpSafeCommit (mining.py + ep.py) | #780 (EP filter) + #785 (batch/promotion) |
| TemporalWire (mining.py post-pass) | #786 |
| Wiring fix (INSTANTIATES→aboutObject) | #781 (ingest path) + #782 (mining path re-verification) |
| Structural wiring produces/uses | #782 (W-1 emits edges) + #781 (predicate hygiene) |
| Promotion (promote_point) | #785 |
| Gates (workflow layer) | #779 (milestone + calibration_passed marker) + #787 (check_gates helper) |
| MCP surface | #787 |

## 4. Child issues

- **#779** chore: Gate B tooling + calibration milestone (mean_grounding + drift snapshot) — `complexity:standard`
- **#780** feat(ep): EP draft filter + draft operator nodes, 4 call sites — `complexity:complex`
- **#781** fix(graph): INSTANTIATES→aboutObject wiring + Event-anchored ranking — `complexity:standard`
- **#782** feat(mining): Phase-2 entity extraction stage + pipeline entry points — `complexity:standard`
- **#783** feat(mining): Phase-2 EntityResolver — exact→fuzzy→semantic dedup chain — `complexity:complex`
- **#784** feat(mining): Phase-2 content dedup — pointKind-scoped hash+embedding — `complexity:standard`
- **#785** feat(mining): Phase-4 EP-safe batch commit + promote_point gate — `complexity:complex`
- **#786** feat(mining): Phase-4 temporal belief wiring + belief_timeline — `complexity:standard`
- **#787** feat(mcp): Phase-4 MCP surface + check_gates/calibration_passed — `complexity:standard`

## 5. Not-in-scope (recorded, plan §8.4 NOT-NOW)

Autonomous high-confidence merges (post-calibration), alias/co-reference lexicon (v2), batch-optimized entity pre-filter, whole-session near-dup detection, reviewer UI for candidate queue, one-time backfill of 4,190 historical sessions, cross-Point discovery without conversation trigger (#438 boundary — see plan §2 W-4 carve-out).
