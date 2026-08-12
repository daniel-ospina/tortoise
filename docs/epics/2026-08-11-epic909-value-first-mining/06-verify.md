---
title: "Verify — Epic #909: Value-first mining system (v1)"
type: verify
domain: engineering
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
inputs: 01-align decision, research briefs, scope.md, 04-plan.md, 05-decompose.md
---

# Epic Verification Result

**Epic:** Value-first mining system — ontology-driven extraction with local-intelligence capture (R1-R9)
**Status:** READY

## Step 1 — Artifact Completeness: 11/11 present (on the docs branch, PR #854)

| Artifact | Path | Present |
| --- | --- | --- |
| Align decision | docs/drafts/2026-08-11-epic909-align-decision.md | ✅ |
| Research briefs (3 + addendum) | docs/epics/2026-08-11-epic909-value-first-mining/research-*.md | ✅ |
| Classification-cues research | docs/research/2026-08-11-classification-cues-decisions-events-claims.md | ✅ |
| Requirements contract | docs/drafts/2026-08-09-mining-system-requirements.md (R1-R9) | ✅ |
| Canonical design | docs/epics/2026-08-11-epic909-value-first-mining/DESIGN.md | ✅ |
| Classification spec | docs/epics/2026-08-11-epic909-value-first-mining/spec-classification-model.md | ✅ |
| Scope (boundaries + 10 high-level E2Es) | docs/epics/2026-08-11-epic909-value-first-mining/scope.md | ✅ |
| Plan (8 substeps) | docs/epics/2026-08-11-epic909-value-first-mining/04-plan.md | ✅ |
| Decompose (19 issues) | docs/epics/2026-08-11-epic909-value-first-mining/05-decompose.md | ✅ |
| Validation-loop artifacts | docs/drafts/2026-08-09-probe-extraction-window1{,-v2,-v3}.md, window2, mitigation-audit | ✅ |

## Step 2 — Cross-Phase Coherence: 0 issues

| Check | Source → Target | Result |
| --- | --- | --- |
| Strategy → Scope | PROCEED (conditioned: gold-first gate, R6 pack mapping in scope, #753 conditions, privacy rework, local-intelligence write path) → scope.md §1 | ✅ all 5 conditions reflected in the in-scope list (items 1-8) |
| Scope → Plan | scope §1 in-scope items 1-8 → plan sections | ✅ item1→§4/§5/W-5 · item2→W-1/§5.1 · item3→W-3/§6.1 · item4→§4.4/W-4 · item5→§6.2 · item6→W-6/§6.3 · item7→§4.3 · item8→W-7 |
| High-level → Detailed E2E | scope E2E-1..10 → plan DE2E-1..11 | ✅ 1:1 + DE2E-11 (spec §6 canonical probe) |
| Plan → Issues | plan §8.3 9 slices → 19 issues | ✅ MECE-verified (05-decompose.md) |
| Out-of-scope discipline | scope §1.2 deferrals (warrants, managed-key, raw upload, dashboard, v1.1 surfaces) | ✅ consistently deferred in plan + issues (S4 never runs, judge_summary cut, CONNECTS/RESOLVES cut, promote endpoint v1.1) |

## Step 3 — E2E Test Alignment: 0 gaps

```text
High-level E2E (scope) → Detailed E2E (plan §7) → Issue references (decompose)
```

| High-level | Detailed | Issues (from the issues' E2E fields — verified 2026-08-11) |
| --- | --- | --- |
| E2E-1 gate | DE2E-1 | 945, 946, 960, 961 |
| E2E-2 BYOK → graph | DE2E-2 | 948, 953, 955, 958, 959, 961, 962 |
| E2E-3 classification + R3 | DE2E-3 | 955, 960, 961, 962 |
| E2E-4 atomicity | DE2E-4 | 955, 961, 962 |
| E2E-5 source citation | DE2E-5 | 948, 953, 958, 961, 962 |
| E2E-6 NAND direction | DE2E-6 | 948, 953, 955, 957 |
| E2E-7 idempotency+budget+quota+Layer-1 | DE2E-7 | 947, 952, 953, 958 |
| E2E-8 extract-nothing | DE2E-8 | 955, 961, 962 |
| E2E-9 pack enforcement | DE2E-9 | 949, 950, 951, 954, 956 |
| E2E-10 privacy | DE2E-10 | 953, 959, 963 |
| (spec §6 probe) | DE2E-11 MITIGATES | 955 (probe); 953 (write mechanism via its DE2E-2 four-node row); 961/962 (Layer-2 recall band — E2E fields declare DE2E-11 legs) |

Wiring annotations: #956's E9 stream-shape assertions exercise DE2E-3/4 through #955's run-loop wiring (per #956's body — its E2E field is DE2E-9). Layer-2 legs (961/962) read the gold set per the per-class N rule.

Every high-level E2E → detailed counterpart ✅ · every DE2E referenced by ≥1 issue ✅ · every issue carrying E2E fields references the relevant DE2Es ✅

## Step 4 — Review Gate Audit: 13/13 passed

| Phase | Gate | Status | Evidence |
| --- | --- | --- | --- |
| epic-align | Strategy review | ✅ | align-decision.md (adversarial test, Eisenhower, profit alignment, 5 conditions) |
| epic-research | Research brief review | ✅ | handoff: verifier-corrected (addendum absorbed Research-Needed #4) |
| epic-scope | Scope review (human gate) | ✅ | 3 of 4 owner decisions resolved + 1 carried (window-#2 schedule → #946, owner availability); window-1 validation converged (0→29→100%) |
| epic-plan §1-8 | Per-substep reviews (×8) | ✅ | 6 reviewer rounds, ~116 findings fixed to convergence, 0 open P0/P1 (review record in 04-plan.md) |
| epic-decompose | Per-issue review | ✅ | 5 parallel batches, ~15 findings fixed (05-decompose.md) |
| epic-decompose | MECE verification | ✅ | 2 rounds (4 findings + 3 residuals) → MECE CLEAN |

## Step 5 — Final Decision

**Decision:** PROCEED to implementation.

**Carried dependencies (not gaps):**

- #946 (window #2 gate run) is owner-scheduled — the first dependency; slices 2-4 (#947/#948/#949) are gate-parallel and implementable immediately.
- Scope §6 open decision #3 (2-window validation schedule) = owner availability — operational scheduling item, not a coherence gap.
- PR #854 (docs home) remains OPEN by design; #870 decision deferred (not blocking).

**Implementation order:** #945 → #946 (gate) ∥ #947/#948/#949 (parallel) → #950/#951 → #952 → #953 → #954 → #956/#955 → #957 → #958 → #959 → #960 → #961 (owner) ∥ #962 → #963.

**Action items before implementation:** (1) #946 has no assignee/schedule — the gate is the entire critical path; owner to schedule window #2 before extractor work starts. (2) PR #854 (docs home) + #870 decisions remain open (non-blocking).
