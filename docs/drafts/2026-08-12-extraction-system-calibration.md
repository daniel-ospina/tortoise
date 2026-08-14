---
title: "System design — the value-first extraction system + calibration loop (epic #909, bootstrap)"
type: design
domain: engineering
doc_status: draft
created: 2026-08-12
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
inputs: spec-classification-model.md, research-r6, R1-R9, window-1 loop artifacts, 04-plan.md §6.3
---

# The Extraction System — design + optimization loop

> Treating calibration as a proper optimization problem: the system is designed
> once (below), then run in loops — system generates → owner reviews the OUTPUT
> (the loss signal) → the system's parameters are updated → rerun → until
> calibrated. This is the window-1 loop (0→29→100% mitigation coverage),
> formalized. It is also the bootstrap of the planned modular pipeline (plan
> slices 6a-6c): the prompt-based system below IS the initial parameterization
> of the value brief + classifier + relation prompts.

## 1. System architecture (bootstrap version)

```
session transcript (utterance-tagged EDUs)
   ↓
[S0] boilerplate filter — dispatch/instruction text, tool dumps, paths,
     identifiers, HTTP codes → excluded from mining (dropped or nothing[])
   ↓
[Extractor] ONE rubric-driven LLM pass (prompt v0.2 — the parameterized system)
   ├─ classification axis: decision / event / claim / process / nothing
   │    (spec-classification-model.md cue tables + R1∧R3 conjunction)
   ├─ entity axis: closed ontology vocab (core + packs), near-miss flags,
   │    pack proposals — never minted kinds (R6)
   ├─ relations: IMPL / NAND (unidirectional) / MITIGATES (edge-targeted,
   │    bias 0.10-0.50, quotes) — support edges first (R9 deep-miss)
   ├─ atomicity: compound decisions split (R2)
   ├─ confidence rubric (0.9+ / 0.7-0.9) + source_ref on every item (R4/R7)
   └─ R3: process items listed with logged reasons — never graph points
   ↓
[stream] {decisions[], events[], claims[], process[], entities[],
          relations[], sources[], nothing[]}
   ↓
[enforcer (later slice 6c)] E1-E10 ladder — in bootstrap: the model's own
   closed-vocab adherence + the reviewer's flags
```

**Lineage:** the single-pass prompt is the fused form of the future
`value_brief` (vocab + semantics), the S2 classifier prompt, and the S3
relation prompt. When slices 6a-6c land, the parameters below split into
those modules without redesign — the stream schema and the vocabulary are
identical.

## 2. Parameters (the optimization variables)

| # | Parameter | Initial value (v0.2) | Update rule (from review) |
|---|---|---|---|
| P1 | S0 filter list | dispatch/instruction first-turn, file paths, `\w+\.py` modules, HTTP codes, git refs | add patterns the reviewer flags as noise |
| P2 | classification cue tables (decision/event/claim) | spec §2 tables verbatim | add/remove cues from misclassified items |
| P3 | R1∧R3 conjunction strictness | decision = commissive ∧ product-knowledge | tighten/loosen per false decision/process calls |
| P4 | R9 MITIGATES cue taxonomy | the 14-cue audit list | add cues from missed mitigations |
| P5 | entity vocab + near-miss conventions | compiled packs + core; ⚠-flag list | add kinds via pack proposals (validated) |
| P6 | relation extraction (IMPL/NAND depth, quotes) | support-edges-first convention | per missed/over-mined relations |
| P7 | output schema | window-1 schema (8 sections) | add fields only with a requirement |

## 3. The optimization loop (run protocol)

```
iterate k = 1, 2, 3, …:
  1. RUN: system v_k on the window → stream_k
  2. REVIEW: owner reviews stream_k against the session — marks:
       - missed items (false negatives)     → recall loss
       - wrong class/kind/relation (errors) → precision loss
       - noise (should not be extracted)    → precision loss
  3. DIAGNOSE: cluster the losses → map to parameters P1-P7
  4. UPDATE: one parameter change per iteration (smallest change that
     explains the loss cluster) → v_{k+1}
  5. CONVERGENCE CHECK: owner finds no mistakes on two consecutive runs
     (or eval metrics, once the harness is live: layer-correct ≥0.90 /
     kind-correctness ≥0.90 / mitigation recall ≥0.75 per §6.3)
```

**Discipline (from window-1):**
- ONE parameter change per iteration — isolates cause/effect (the loop
  converged in 3 iterations on window-1 because each change was atomic).
- The owner's review is the ORACLE (ground truth); the system never argues.
- The canonical test case (X IMPL A; Z MITIGATES [X→A]; Y IMPL Z) is checked
  every run — a regression guard on P4/P6.
- Outputs are versioned (stream_v{k}) so regressions are bisectable.

## 4. Convergence criteria (calibrated = )

1. Owner review: zero mistakes on two consecutive runs (different windows).
2. Quantitative (once the eval harness is live — slice 8): layer-correct
   ≥0.90, kind-correctness ≥0.90, citation-correctness ≥0.90, atomicity
   ≥0.85, mitigation recall ≥0.75, empty-rate 20-40% (band semantics).
3. The window-2 gate (DE2E-1) additionally requires the κ ≥0.60 rubric
   agreement — computed on the CLASSIFIED stream vs the owner's labels
   (the judge agreement is a secondary signal; the primary is the review).

## 5. Current state

- System v0.2 designed (this doc) + implemented as `tools/probe_extractor.py`
  (the window-1 rubric + full R9 taxonomy + compiled vocab + window-1 schema).
- Window #2 = the #953 commit-endpoint implementation session (25 EDUs,
  operational — the required different session type).
- **Iteration 1 in progress**: run v0.2 → present stream_1 for owner review.
