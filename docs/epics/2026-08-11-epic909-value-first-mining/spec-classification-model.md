---
title: "Spec — Classification model (R1/R2/R3): decisions vs events vs claims, atomicity"
type: spec
domain: engineering
doc_status: design
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753"
inputs: research/2026-08-11-classification-cues-*.md + probe windows 1-2 + mitigation audit
---

# Classification Model Spec — decisions / events / claims, atomicity, process routing

The most implementation-ready piece of the mining system. Two-axis classifier + atomicity
validator + process-decision gate, with the failure modes observed across both probe windows.

## 1. The two-axis model

**Axis 1 — illocutionary type** (Searle): is the utterance a **commissive** (commits to
future action: decided/chose/agreed/we-will/I-will/committed) or an **assertive**
(reports the world: believe/think/shows/costs/failed)?

**Axis 2 — aspect (within assertives)**: past-perfective accomplishment ("fixed/repaired/
shipped/completed") → **EVENT**; stative/gnomic ("costs $0.60/M", "fails 40% on CI",
"is the unit-economics killer") → **CLAIM**.

| | Commissive | Assertive past-perfective | Assertive stative |
|---|---|---|---|
| **Class** | **DECISION** | **EVENT** | **CLAIM** |
| Test | "we will X / decided X / chose X" | "X happened / was fixed / shipped" | "X is / costs / fails / implies" |
| Ontology | Point (decision) | Event node | Point (claim) |

**R1∧R3 conjunction (window-2 finding — the real decision gate):** a commissive alone is
NOT a decision. "I'll fix both now" (≈15× in the operational session) is a process
commitment, not a product decision. **DECISION = commissive ∧ product-knowledge-bearing**
(asserts something durable about the domain: a choice of approach, a ruling, a commitment
with epistemic weight). Otherwise → event (or R3-routed if process).

## 2. Cue tables

**DECISION cues:** decided, chose, agreed to, we will, I will (with agentivity + the
conjunction), we're going with, the ruling is, default to, ship X first, reject Y.
- ⚠️ **"should" is a RECOMMENDATION, not a commitment** (measured 44× deliberation vs
  10× decisions in window-1's meta-discussion) — do NOT classify as decision.
- ⚠️ **"will" is ambiguous** (prediction vs commitment) — discriminator: subject
  agentivity + the R1∧R3 conjunction.

**EVENT cues:** repaired, fixed, shipped, completed, merged, deployed, closed, ran,
measured, filed, created. ("did X" = event.)

**CLAIM cues:** stative predicates — is, costs, fails, implies, means, shows, measured,
the cause is, the risk is, requires, depends on. Plus quantified facts and research
findings (with source_ref).

**MITIGATE cues (R9 — "true but matters less"):** it's an estimate, decide with real
telemetry, a positioning tension not structural, the caveat is, only if, gated on, the one
swing variable, only achievable because, the leading indicator is, preliminary, watch-gate
not a statistical test, none would let it be built as-is, still to run before the gate,
real but not transformative. Targets an IMPL edge (0.10-0.50), never a point. **Deep-miss
convention: extract the support edge first.**

## 3. Atomicity validator (R2)

- **Unit = EDU** (elementary discourse unit = minimal speech act). "A AND B AND C" = 3
  EDUs = 3 decisions.
- **Propositionize before classify:** split on coordination (and/but/or), serial lists,
  rationale subordination ("X because Y" → X = decision, Y = IMPL-linked claim).
- **Deterministic check:** any emitted decision with coordination cues or >1 commissive
  predicate → retry once with the error → split or fail. Doubles as the R8 Layer-1 gate.

## 4. Process-decision routing (R3)

Process/governance commitments ("validate on 2 windows first", "record this on the
issue") → **drop with logged reason** (violation event) in v1; work-item routing when an
integration exists. NOT graph points. Monitor-only eval (≥0.95 until n≥20).

## 5. Observed failure modes (from both windows — the eval harness must test these)

1. **Tool results = 53% of agent traffic** — S0 must filter; the biggest precision lever.
2. **Meta-discussion pollution** — the agent discussing the mining system itself ("we
   decided the extractor should...") explodes decision counts (78× in window-1's session).
   Fix: speaker-anchored commitment + the R1∧R3 conjunction.
3. **Trigger-less decisions** — real decisions often lack cues ("I'm leaning toward
   option B"). The value gate must keep these; cue-only extraction misses them.
4. **Event granularity** — per-PR vs per-task vs per-turn (window-2) — needs a rule
   (default: per-task, dedup narrated repeats 4-5×).
5. **Tool-quirk claims** — durable but environment-specific ("the bash tool needs X") —
   propose a `tooling-knowledge` tier or an S0 fuzzy band.
6. **Verification-edge mitigations** — status claims as X endpoints for MITIGATES
   (window-2 found 4) — codify X endpoints.
7. **Conditional commitments** ("don't delete YET") — decisions with validity windows.
8. **Degenerate-empty risk** — an operational session can pass every rubric check with a
   near-empty graph — **minimum-signal assertion per window type** required.
9. **"should" false positives** — the top misclassification (44× in window-1).
10. **Zero-NAND sessions** are normal (window-2) — don't bias toward NAND emission.

## 6. Eval integration

- Per-class rates (decisions/events/claims/mitigations separately — never a blended
  layer-correct number; base rates differ wildly).
- The R1∧R3 conjunction is THE decision-class test the evals must exercise.
- Window-type minimum-signal assertions (operational sessions must emit ≥N events, etc.)
  — prevents degenerate-empty.
- The canonical mitigation case (X IMPL A; Z MITIGATES [X→A]; Y IMPL Z) as a deterministic probe.

## 7. Inputs/lineage

research/2026-08-11-classification-cues-decisions-events-claims.md (the Searle model +
measurements); probe windows 1-2 (the failure modes); mitigation audit (the R9 cues);
requirements R1-R9 (the contract).
