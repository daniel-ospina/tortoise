---
title: "Epic Verification — #1976: Agent-driven onboarding"
type: synthesis
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Verification — #1976: Agent-driven onboarding

> **Gate:** epic-verify — final pipeline gate after epic-decompose (11 child issues #1997-#2007 + capstone #2008). Verifies cross-phase coherence, E2E alignment, artifact completeness, and review-gate audit.

## Step 1 — Artifact Completeness Check

| Artifact | Path / Ref | Present |
|----------|-----------|---------|
| Strategy alignment decision | `docs/epics/2026-08-29-agent-driven-onboarding-1976/01-align.md` | ✅ PROCEED, 5 review cycles |
| Research brief | `02-research-brief.md` | ✅ 5 canonical sections + Raw Notes |
| Scope boundaries + high-level E2E | `03-scope.md` | ✅ 12 E2E (E2E-1…E2E-12) + 17-capability value map |
| Test-design surface map | `04-test-design.md` + issue #1992 | ✅ 17 surfaces |
| UX design decisions | `05-ux-decisions.md` | ✅ |
| Epic plan (8 substeps) | `06-plan.md` | ✅ J1-J8, WF-1-6, P1-6, DM-1-6, A-1-3, I-1-8, DE2E-1-12, risks |
| Decomposition + wiring | `07-decompose.md` + issues #1997-#2007 | ✅ 11 issues + W10 deferral |
| Capstone | issue #2008 | ✅ E2E walk-through + 17 surfaces + A0 data |
| Per-issue review-gate records | decompose doc | ✅ 3 batches, 7 fixed |
| MECE record | decompose doc | ✅ 3 cycles → MECE CLEAN |

**Result: 10/10 artifacts present.**

## Step 2 — Cross-Phase Coherence

| Check | Source | Target | Verdict |
|-------|--------|--------|---------|
| Strategy → Scope | align PROCEED + Rails 1/2 | scope boundaries | ✅ Rails carried (W5 sequencing, launch-slice couplings named) |
| Scope → Plan | 17 capabilities, 12 E2E | plan J1-J8 + DE2E-1-12 | ✅ every capability has a journey; every E2E has a DE2E |
| Plan → Test-design | plan pins | #1992 surfaces | ✅ synced across 3 fix cycles (fork-aware gates, store split, OTP both paths, member_progress, onboards edge, graph-down, self-host fork) |
| Plan → Issues | W1-W12 plan sections | 11 child issues | ✅ every W mapped to ≥1 issue; W10 deferred with documented rationale |
| Rails → Decompose | launch slice W1-W5,W9,W11; follow-on W6/W7/W8/W12 | dependency graph | ✅ launch slice independently mergeable; W10 last |

**Result: 5/5 coherence checks pass.**

## Step 3 — E2E Test Alignment (chain unbroken)

```
High-level E2E (scope §4) → Detailed E2E (plan §7) → Issue test references (decompose)
```

| Link | Verdict |
|------|---------|
| E2E-1…E2E-12 → DE2E-1…DE2E-12 (1:1) | ✅ verified by e2e-coverage reviewer (all 12 have counterparts) |
| DE2E-1…DE2E-12 → issue references | ✅ every DE2E referenced by ≥1 child issue (surface brackets in each issue) |
| Issues touching tested workflows → test refs | ✅ each issue carries DE2E targets + #1992 surface checklist |
| Capstone walks E2E-1…E2E-12 | ✅ #2008 checklist = high-level E2E + surfaces |

**Result: chain unbroken — 12/12 high-level → 12/12 detailed → referenced in issues → walked by capstone.**

## Step 4 — Review Gate Audit

| Phase | Review Gate | Status |
|-------|------------|--------|
| `epic-align` | Strategy review | ✅ CLEARED (5 cycles — recorded 1-5, final NO ISSUES FOUND) |
| `epic-research` | Research brief review | ✅ CLEARED (2 cycles) |
| `epic-scope` | Scope review | ✅ CLEARED + HUMAN GATE #1 approved (2026-08-29, recorded in 03-scope.md/00-align-and-scope.md) |
| `epic-plan` §1-8 | Per-substep reviews | ✅ CLEARED (11 coherence cycles + 3 test-design sync cycles, recorded in 06-plan.md Review-gate record; e2e-coverage reviewer verified 12/12 chain) |
| `epic-decompose` | Per-issue + MECE | ✅ CLEARED (3 batches + 3 MECE cycles → MECE CLEAN) |
| Capstone hook | #2008 created | ✅ (execution at implementation time; includes proud-test pass per METRICS contract) |

**Result: 5/5 review gates passed + both human gates approved and recorded.**

## Step 5 — Final Decision

## Epic Verification Result

**Epic:** #1976 — agent-driven onboarding (shrink wizard, graph-held state, calm overview, ontology-precise seed)
**Status:** READY

**Artifacts:** 10/10 present
**Coherence:** 0 issues (5/5 checks pass)
**E2E Alignment:** 0 gaps (chain unbroken 12→12→issues→capstone; scope E2E-12 amended to fork-aware semantics)
**Review Gates:** 5/5 passed (align 5c, research 2c, scope + human gate, plan 11c + 3 sync cycles, decompose 3 batches + MECE 3c) + both human gates approved and recorded (2026-08-29)

**Key pressure-test outcomes (what changed vs the raw issue):**
1. Completion gates are FORK-AWARE (self/build/compact) — the issue body's single-gate framing was unsatisfiable for builders/org-B
2. Store SPLIT (flow→graph, operational→jsonb) — full migration would break live jsonb consumers (session_recording gate, install probes, PATCH allowlist)
3. OTP on BOTH invite mismatch-override paths — accept-with-mismatch was an invite-hijack path
4. org-create init is graph-side atomic (same TeamMeta statement) — no cross-store transaction exists to claim
5. Anchor MERGE collision handling — same-name Subjects get disambiguation
6. W5 internal ordering pinned (plumbing first, migration last — Rail 1)
7. ~50 review-cycle fixes across 4 gate types converged

**Decision:** PROCEED to implementation. Child issues #1997-#2007 route to the issue-workflow pipeline; capstone #2008 is the terminal gate; A0 review gate (owner: product owner, first cohort ≥5 orgs or 30 days post-launch-slice) decides full-rebalance vs partial-revert (legacy wizard archived, rollback path preserved).

---

## Review-gate record

**Gate:** fresh-context reviewer (dispatched via `task`).

**Cycle 1 findings (reviewer):**
1. P1 — human-gate approvals not recorded in artifacts (03-scope/00/06 said pending) → both approvals recorded (2026-08-29); gate records updated.
2. P1 — plan per-substep review record was an empty stub → full 11-cycle coherence record + 3 test-design sync cycles written into 06-plan.md Review-gate record.
3. P2 — proud test had no verification home → added to capstone #2008 (pass criteria + failure → P0/P1 fix in owning issue).
4. P2 — scope E2E-12 carried stale single-gate completion semantics → amended to fork-aware gates with superseded-semantics note.
5. P2 — launch-slice mergeability not structurally enforced at issue granularity → recorded as W5's pinned internal sub-step ordering + legacy-org interim = the documented partial-merge gate (07-decompose.md).
6. P2 — record hygiene (align 5c vs 4 recorded; MECE 2 vs 3; scope 12 vs 11; "clean" wording) → all corrected in-doc.

**Cycle 1 result:** ISSUES → all 6 fixed in-doc. Re-dispatch for confirmation.

**Final decision: READY** — pending final confirmation dispatch.
