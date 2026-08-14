---
title: "Epic Decompose — #903: Dreaming (EP across the whole/expanding graph)"
type: decisions
domain: strategy
doc_status: live
subjects.team: epistemic-team
created: 2026-08-13
---

# Epic #903 — Decomposition (Decompose stage)

**Date:** 2026-08-13
**Pipeline:** epic-workflow Stage 5 (Decompose) — `epic-decompose`
**Gates:** per-issue review (4 parallel batches) + MECE verification — **CLEAN** (per-issue fixes: 15+ coordination patches; MECE: 8 ownership/precision fixes, zero cycles).

## Child issues (dependency-ordered)

| Issue | Title | Depends on | DE2E | Scope items |
|---|---|---|---|---|
| #1239 | C1 graph-scale diagnostics + stale-first-vs-full decision gate | C12 (F5), parallel C2 | 10 | 10 |
| #1240 | C2 freshness property + atomic write-back + index + promote/zero-affected fixes | C12 (F2) | 1, 4, 3-sub | 1 |
| #1241 | C3 stale-first window scheduler + dedup + per-pass budget | C2, C12; C1 gate | 2 | 3, 4 |
| #1242 | C4 warm-start via graph-persisted messages + invalidation (gated) | C3, C12 (F1) | 6a, 6b, 12abc | 5 |
| #1243 | C5 retention + attempt cap + stale_unresolved | C3, C2, C12 (F3) | 7, 12d | 6 |
| #1244 | C6 SDK dream() mode router + precedence + return shapes | C3 | 1, 3 | 2 |
| #1245 | C7 observability + zero-output alarm + dream_health_check + /v1/dream/health | C5, C3, C12 | 8 | 7 |
| #1246 | C8 hosted wiring + #329 budget + 429/Retry-After + selfhost | C6, C3 | 11 | 2(h), 3 |
| #1247 | C9 lifecycle absorption + transfer invalidation wiring | C4, C2 | 5 | 8 |
| #1248 | C10 staleness-error evaluation + eval-spec gate | C3, C2, C7, C12 (F4) | 9 | 9 |
| #1249 | C11 MCP dream mode + dream_health_check tool + error mapping | C6, C7 | 8 (MCP) | 2(MCP) |
| #1250 | C12 shared test fixtures F1–F5 + hermetic harness | none | all (substrate) | cross-cutting |

## Wave plan (parallelization)

- **Wave 0:** C12 (fixtures — substrate), C2 (production, parallel), C1 (serialized behind C12's F5 for its fixture, but its *script* work is parallel).
- **Wave 1:** C3 (needs C2 + C12).
- **Wave 2:** C4, C5, C6 (need C3 + C12 where applicable).
- **Wave 3:** C7, C8, C9, C10 (need wave-2 pieces + C12 fixtures).
- **Wave 4:** C11 (needs C6 + C7).
- **Capstone:** #1254 (clickthrough verification, after waves).

## Key ownership boundaries (fixed at gates — do not re-litigate)

- C4 owns ep.py invalidation helper + generic call sites; C9 owns the three lifecycle call sites (supersede L1777, invalidate L1475, approve_merge L2616).
- C11 owns MCP tool registration/params/error mapping wholesale; C7 owns SDK `dream_health_check()` + alarm logic.
- C5 owns sdk.py L5383 non-convergence retention; C2 owns L5380 zero-affected-converged retention + promote→dirty (L1835).
- C7 owns the staleness-error-curve + region_attempts + warm_start_savings SURFACING; C10/C5/C4 produce the underlying data.
- DB-down contract: C6 (embedded TortoiseError raise), C8 (hosted/selfhost 500/503 shape).
- G7 isolation: C2 owns the write-back regression guard; C6 authors the DE2E-3 precedence/isolation test.

<!-- decompose-gate-status: CLEAN -->
