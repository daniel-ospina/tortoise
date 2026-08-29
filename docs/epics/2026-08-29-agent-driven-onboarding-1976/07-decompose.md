---
title: "Decomposition — Epic #1976: Agent-driven onboarding (child issues + wiring)"
type: synthesis
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Decomposition — Epic #1976 child issues

> **Gate:** epic-decompose — per-issue review (3 parallel batches) + MECE verification (3 cycles → CLEAN).
> **Capstone hook:** #2008 (capstone: clickthrough verification) — fired after Decompose, before Verify.
> **Source:** plan `06-plan.md` §8 decomposition plan + test-design #1992 surface map.

## Issue inventory (11 created + 1 deferred)

| Issue | Workstream | Complexity | Depends on | Surfaces (#1992) | DE2E targets |
|-------|-----------|-----------|------------|------------------|--------------|
| #1997 | W1 wizard shrink + 5 human steps + copy sweep | standard | W5 (checkpoint endpoint — catalog-presented mark) | 3 (form UX + 402 surface), 4 (shell), 16 | DE2E-1, 2, 3, 12 |
| #1998 | W2 agent install skill + universal command + fork card | standard | W5, W1 (shell) | 4 (semantics), 5, 6 | DE2E-1, 5, 8, 12 |
| #1999 | W3 seed + decide + fork-aware completion | standard | W2, W5 | 1 (step edges), 7, 8, 14 (W11-owned), 15 (seed half) | DE2E-4, 1, 12 |
| #2000 | W4 Settings tab (owner) + Overview de-toggling | standard | W5 | 9, 10 | DE2E-2, 6, 11 |
| #2001 | W5 graph-held OnboardingState + migration + cross-W E2E | complex | none (foundational) | 1, 2, 17 | DE2E-1, 6, 12 |
| #2002 | W6 session-capture disclosure | standard | W4, W5, W2 (copy contract) | 11 | DE2E-11 |
| #2003 | W7 invite-accept fusion + OTP + atomic accept | complex | W5, W1 (join leg) | 12 | DE2E-7, 8 |
| #2004 | W8 builder capability catalog | standard | W1 (placeholder owner), W5 (checkpoint), W2 | 13 | DE2E-9 |
| #2005 | W9 trigger/entry + compact multi-org + fork inheritance | standard | W1, W2, W3, W4, W5 | 1, 2, 3 (condition), 4 (door-skip), 9, 10 | DE2E-1, 3, 6, 12 |
| #2006 | W11 onboarding telemetry | standard | W3 (write paths), W5 (checkpoint endpoint) | 14 | DE2E-1, 12 |
| #2007 | W12 self-hosted onboarding | standard | W5, W3 | 15 | DE2E-10 |
| — | **W10 invitee post-accept mini-onboarding** | deferred | needs org RBAC/access-tier design first | — | — |

## Dependency graph (acyclic — MECE verified)

```
W1 ──┐
W5 ──┼──► W2 ──► W3 ──► W11
     │            │
     │            └──► W12
     ├──► W4 ──► W6 (W4→W5)
     ├──► W7 (W1 join leg + W5 member_progress)
     ├──► W8 (W1 placeholder owner + W5 checkpoint + W2)
     └──► W9 (W1, W2, W3, W4, W5)
```

Launch slice: **W1, W2, W3, W4, W5, W9, W11** (independently mergeable).
Follow-on waves: **W6, W7, W8, W12**.
Last: **W10** (explicitly deferred — RBAC first).

## Key ownership pins (MECE single-owner discipline)

- **Fork card:** W1 = shell + static build-branch placeholder (+ marks catalog-presented at render via W5 checkpoint); W2 = semantics + persistence; W9 = door-level skip verification only.
- **Catalog placeholder:** W1 renders at launch; W8 replaces the SOURCE (mechanism stays W5's checkpoint).
- **Capture announcement:** W2 owns copy contract; W6 implements trigger + Settings surface (consumes W4 tab).
- **Telemetry emission:** W11 owns emission + dedup (hooks W5 checkpoint edge-creation); W3 creates the step edges.
- **Step-edge writes:** W3 (seed/decide edges); W5 (schema + checkpoint endpoint + canonical enforcement).
- **Seed logic:** W3; full self-host path W12 (consumes W3 seed + W5 node).
- **402:** W9 raises the condition; W1 renders the upgrade surface in the org-create form.
- **Invites:** W7 extends existing /v1/invites* (M6) — never re-creates; W10 deferred.

## Wiring record

- **Per-issue review gate:** 3 parallel fresh-context reviewers (batch 1: W1-W4; batch 2: W5-W8; batch 3: W9/W11/W12) → 7 issues fixed (surface adoption gaps, dep corrections, ownership splits, plan-pin carry-omissions); 4 passed with minor annotation fixes applied in the same loop (W4, W6, W8, W12 — each had nits fixed in-doc before CLEAR).
- **MECE verification:** cycle 1 → 8 issues (overlap W3/W11, gap build-fork gate, 4 serialization, minor overlap W1/W9, cosmetic) → all fixed; cycle 2 → 3 minor residuals → all fixed; cycle 3 → **MECE CLEAN**.
- **Deferred artifacts:** W10 (invitee post-accept) — needs RBAC design; org RBAC/data-access tiers not in this epic.

## Review-gate record

- Per-issue reviews: 3 batches dispatched, fixes applied to 7 issues, re-verified.
- MECE: 3 cycles (cycle 1: 8 issues → fixed; cycle 2: 3 residuals → fixed; cycle 3: **MECE CLEAN**).
- Capstone: **#2008** created (E2E-1…E2E-12 walk-through + 17 surfaces + A0 gate data capture + proud-test pass — feeds epic-verify Stage 6).
- Launch-slice mergeability note (verify P2 fix): W5 (#2001) bundles Rail-1 plumbing + deferred parts in ONE issue — Rail 1's "migration after agent flow" is enforced by its pinned INTERNAL SUB-STEP ORDERING (plumbing first, migration/mirror/completion last) + the legacy-org create-on-write interim. The launch slice remains independently mergeable at the W-level; #2001's merge surface is the documented partial-merge gate.
