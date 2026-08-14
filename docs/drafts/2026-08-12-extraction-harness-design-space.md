---
title: "Extraction harness — the design space: improvements and alternatives to test (epic #909)"
type: research
domain: engineering
doc_status: draft
created: 2026-08-12
ownedBy: epistemic-team
governingAgreement: "#909"
inputs: calibration loop iterations 1-7, extraction research (12+14 sources), pipeline-vs-joint literature
---

# The design space — what the harness should test and compare

> Framing (owner, 2026-08-12): the matrix compares HARNESS DESIGN ELEMENTS —
> stage architecture, prompts, schemas, connection strategies — NOT models.
> The user brings the model (BYOK); we optimize the design. Models are held
> constant per comparison run (a control, not a variable).

## Dimension 1 — Stage architecture (the headline variable)

| Variant | Description | Evidence | Harness status |
|---|---|---|---|
| **single (joint)** | one pass: items + relations + kinds | sentence-level joint > pipeline (AACL'22); joint advantage DROPS/REVERSES at document level (ar5iv 2310.00696) | implemented — baseline |
| **staged (pipeline)** | pass 1 items → pass 2 relations (fixed ids) | "frustratingly easy" pipeline +1.7–2.8% F1 over joint (ACE04/05, SciERC); clinical +5.5% F1; **our measured win: 26–42 relations + decision-events recovered** | implemented — current best |
| **iterative (self-correct)** | run → validate (deterministic layer) → re-prompt with the errors → rerun once | self-correction loops (research: iterative refinement improves constrained tasks); the deterministic validator gives the error signal for free | **to wire** |
| **extract-then-connect** | conversation items + SOURCE events (PR/issue metadata), then connect | the owner's architecture: events captured deterministically from sources; connection is the critical step | connector built; connection variants below |

## Dimension 2 — Prompt design

| Variant | Description | Evidence | Status |
|---|---|---|---|
| schema-first | the full stream schema in the prompt (current) | baseline; over-constrained prompts degrade (iteration-5 collapse) | current |
| JSON-Schema / constrained decoding | provider-supported structured output (JSON schema param) | Neo4j KG builder: "structured output enforcement where the provider supports it — R8 layer-1 done right" (research-r6 §2.2) | **to wire** (model-dependent) |
| example-anchored (few-shot) | 2-3 worked examples per class + the canonical MITIGATES case | DSPy: example/demo tuning overfits; instruction tuning generalizes — use FEW examples, not many | **to wire** |
| negative-example focus | the glue/process counter-examples (iteration-6 lesson) | measured: the glue gate fixed design-window over-extraction but broke operational — negatives must be scoped (conversation-glue only) | partially in prompt |
| instruction-level only | no in-prompt examples; rules only | DSPy: instruction rewrites generalize better than example-level | current |

## Dimension 3 — Output schema design

| Variant | Description | Evidence | Status |
|---|---|---|---|
| separate arrays (current) | events/claims/entities/relations/sources/nothing | baseline | current |
| **relations embedded in items** | each claim carries its IMPL/NAND edges inline; decision-events carry criteria ids | the tandem requirement — coupling relations to their items may beat a separate array (the relation↔item referential link is the failure point) | **to wire** |
| required-fields-in-schema vs post-validate | quote ≤200 + source_ref enforced by the prompt vs caught by the validator | validator catches 29 missing-source_ref + 36 quote violations on real streams — post-validation is necessary regardless (R8 layer-1) | both — validator is the safety net |

## Dimension 4 — Entity typing strategy

| Variant | Description | Evidence | Status |
|---|---|---|---|
| semantics-first | kindDefs descriptions + nearMisses in the prompt (current) | descriptions + confusable siblings = proven near-miss mechanism (EMNLP'21); our measured fix (tortoise → product) | current |
| name-then-type (two-stage) | extract entity names → second pass types them against the vocab | separation of concerns; the typing pass gets a closed set — may beat in-pass typing on entity quality | **to wire** (cheap: reuse staged infra) |
| coarse-first | emit coarse kind, refine only on confidence | coarse > fine on unseen types (EACL'21); our `concept` catch-all is the coarse home | partial (core:concept) |

## Dimension 5 — Connection strategies (source events ↔ conversation items)

| Variant | Description | Evidence | Status |
|---|---|---|---|
| token/name resolution | deterministic: event title tokens vs session entities (≥4 chars) | current — deterministic, cheap; the "PR merged" ↔ README link | implemented (fixed: ≥4-char tokens) |
| embedding similarity | embeddings of event text vs entity names | catches paraphrase links the token matcher misses | **to wire** (embeddings exist in repo) |
| LLM resolution | a prompt pass: "which session items does this event connect to?" | most flexible; LLM cost per event | **to wire** |
| hybrid | deterministic exact → LLM only on the residual | cost-discipline (TierMem-style sufficiency routing) | **to wire** |

## Dimension 6 — Guards & config (all deterministic — testable model-free)

- collapse guard (eventKind >80% one kind) — fires on flash runs ✓
- 0-relations-with->5-claims guard ✓
- keep-ratio fail-closed (>40% → empty) — the S1 value gate (plan) — **wire as a harness config**
- block-rate fail-closed (>15%) — the enforcer's E-ladder — **wire as a config**
- fail-closed vs warn posture per class — config matrix

## The comparison protocol (design-variable discipline)

1. Hold the model CONSTANT per comparison (BYOK control) — e.g., all comparisons on v4-pro; re-check on a weaker model only for robustness.
2. Compare ONE design dimension at a time (stage architecture → then prompt design → then schema → then typing → then connection), on the SAME 3 windows.
3. Deterministic layer (validate_stream + guards) is the primary metric; relations + decision-events are the headline outcomes; minted/badEvk are hard gates.
4. Every candidate runs ≥3× (stochasticity); report best/median, not a single roll.

## Immediate next experiments (highest leverage)

1. **iterative (self-correct)** vs staged — the deterministic validator gives the error signal; likely the biggest remaining win (recover missing source_ref, over-200 quotes, missed relations).
2. **relations-embedded** schema vs separate-array — tests the tandem requirement directly.
3. **name-then-type** vs in-pass typing — entity quality is the state-first critical path.
4. **example-anchored** (2-3 worked examples incl. the canonical case) vs instruction-only.
5. **connection hybrid** (deterministic + LLM residual) on a real PR↔session pair.
