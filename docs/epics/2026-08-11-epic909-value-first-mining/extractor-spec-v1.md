---
title: "Extractor spec v1 — the buildable contract for slices 6a-6c (epic #909)"
type: spec
domain: engineering
doc_status: draft — calibration-derived, owner-reviewed direction
created: 2026-08-12
ownedBy: epistemic-team
governingAgreement: "#909, #1013, #1026"
inputs: extraction-criteria-v1.md, extraction-harness-design-space.md, calibration experiments ledger (tools/experiments.jsonl)
---

# Extractor Spec v1 — the measured design

> What the calibration loop (12+ iterations, 3 windows, 2 models, 5 methods,
> all logged in `tools/experiments.jsonl`) says the production extractor
> should be. This is the buildable contract for #954 (value brief), #955
> (extractor pipeline), #956 (enforcer).

## The measured decisions

| # | Decision | Evidence (ledger + research) |
|---|---|---|
| D1 | **Staged pipeline** — pass 1 extracts items, pass 2 emits relations | document-level pipeline ≥ joint (AACL'22, ar5iv 2310.00696); our matrix: staged relations 28–65 vs single 30–37 |
| D2 | **Embedded-relations schema** — items carry `supports`/`attacks`/`criteria`/`tempered_by` inline (the tandem requirement) | embedded = 10 decision-events on flash vs 4 staged (the weak model learns the structure when it's inline) |
| D3 | **statement-only point kind** — the logic layer is one kind; hypothesis folded into confidence (owner, option B) | #1022 |
| D4 | **Decision class → Event node** (eventKind `decision`, aboutObject → options) — never a decision Point; decision-as-event on the timeline | #1013, state-centric model |
| D5 | **EventKind semantics** — occurrence (default), deployment (shipped to production ONLY), review, extraction, decision, turn (capture only) | iteration-4 fix (deployment mislabeling eliminated) |
| D6 | ~~**core:concept kind** for meta-domain concepts (points, decisions, options, criteria, lifecycle, models)~~ — **SUPERSEDED 2026-10-21 (#2786, PR #9395):** `concept` is not an `objectKind` in ONTOLOGY §5 (never was; the extractor brief dropped it in T12/#1272), and the canonical 16 (`pack_registry.CANONICAL_OBJECT_KINDS`) declares no `core:concept`, so the kind this decision routes to resolves nowhere. An abstract model with no fitting kind now routes to the vocabulary's **explicit `other` bucket** (`core:other`). See the note below the table: the routing is the shipped prompt's, and the effect D6 was protecting against is now a calibration measurement, not a prompt-rubric decision. | iteration-2 fix (design sessions: 63/99 entities → concept; `other` dump eliminated) · D6's own evidence argues *against* `other`, so this is **superseded by §5 / T12 (#1272), not refuted** — the question it raised is now measured, see below |
| D7 | **S0 reference masking** — regex pre-filter (`PR #N`, `issue N`, `epic N`, `vX.Y`, `#N`) before the LLM sees the transcript | iteration-3 fix (PR refs as dev:issue eliminated) |
| D8 | **Glue gate** — conversation-about-conversation (acknowledgments, recaps, meta) → nothing/process; facts about the WORK stay claims | iteration-6 correction (the gate must not eat operational content) |
| D9 | **Chunked extraction** — 6-EDU chunks with id prefixes + per-chunk retry/skip | measured truncation at ~18.6K chars on full-window outputs; chunk=6 fixed all methods |
| D10 | **Wall-clock deadline** (600s) per LLM call — no token caps (the model decides; flash is cheap) | the "stall" was unbounded streaming generation; bounded calls + cheap retries fixed it |
| D11 | **Deterministic enforcer rules** (from `validate_stream`): minted kinds, bad eventKinds, claim kind = statement, source_ref present (R4), quote ≤200, MITIGATES targets an IMPL edge + strength band, referential integrity | real-stream audit: 29 missing source_ref, 36 quote violations, 17 kind drifts — the deterministic layer catches what the LLM drifts on |
| D12 | **Distribution guards**: eventKind collapse (>80% one kind), 0-relations-with->5-claims, keep-ratio fail-closed (>40%), block-rate fail-closed (>15%) | guard fires on flash's all-occurrence streams; keep-ratio/block-rate from the plan |
| D13 | **Experiments ledger** — every extraction run logged (window, method, model, config, metrics, duration, status) | the calibration loop's memory; `tools/experiments.jsonl` + `--report` |

### D6 is superseded, and that is recorded here on purpose

D6 is struck through rather than deleted because it was a **real measured finding**
(iteration 2: routing meta-domain concepts to `other` produced a 63/99-entity dump), and the
only thing that ended it is a **later vocabulary decision**, not a counter-measurement:
ONTOLOGY §5 declares no `concept` object kind (it never did; the extractor brief dropped it in T12/#1272). Two consequences this record must
carry, so a lane reading a decision table does not re-decide the routing the other way:

- **The routing is settled by §5, not by D6.** The extraction prompt's confusable-kind
  paragraph and `tools/probe_extractor.py`'s `CORE_SEMANTICS` route an abstract model with no
  fitting kind to `core:other`, and `tests/test_kind_vocabulary_consistency.py`
  (`::test_extraction_prompt_advertises_only_canonical_core_kinds`) reds if a retired
  `core:<kind>` re-enters the built prompt. D6's *intent* — semantics-first extraction with an
  explicit no-fit bucket, never a minted kind — survives; only its chosen kind does not.
- **D6's fear of `other` is a measurement now, not a rubric argument.** Whether `core:other`
  behaves as a dump bucket again is settled by the calibration lanes (#9075–#9077) on
  `tools/experiments.jsonl`, which is where a change to the routing belongs if the measurement
  says so. A vocabulary-maintenance change is not the place to re-decide it, and neither is
  this record: reopening D6 means bringing that measurement here.

> **OVERRIDES:** D6's `core:concept` routing — superseded by ONTOLOGY §5 / T12 (#1272) (the canonical
> object-kind set declares no `concept`), so an abstract model with no fitting kind is emitted as
> the explicit `core:other` rather than as a retired kind no read path can resolve.

## The pipeline (target state — what #954/#955/#956 build)

```
transcript (utterance-tagged EDUs)
  ↓
[S0] deterministic pre-filter (D7, D8 glue gate)
  ↓
[S1] value gate — keep/drop; extract-nothing first-class; keep-ratio >40% → fail-closed (D12)
  ↓
[S2] classifier (pass 1) — per-EDU class:
     decision → EVENT (eventKind decision) + about_entities = options (D4, D5)
     claim    → Point (pointKind statement) (D3)
     event    → Event node (eventKind per D5)
     process  → drop with logged reason (R3)
     nothing  → rejected with reason (D8)
     + entity extraction (semantics-first, D6, near-miss flags, never mint)
  ↓
[S3] relations (pass 2 — the EMBEDDED schema, D2):
     items re-emitted with supports/attacks/criteria/tempered_by inline;
     deep-miss convention (support edge first, then the mitigation);
     canonical case checked every run
  ↓
[S5] grounding — entity frequency gate, dedup (pt_<sha>), supersede_point
  ↓
[S6] serializer — the derived-commit payload (events[] per #1013, no decision points)
  ↓
[enforcer — deterministic, zero-LLM] (D11, D12):
     E1 minted kind → block item · E8 missing source_ref → block ·
     quote >200 / kind drift → reject with reason · MITIGATES shape ·
     guards: collapse / relations / keep-ratio / block-rate → fail-closed
  ↓
[ledger] every run logged (D13)
```

## Interfaces (what each slice owns)

| Slice | Owns | Consumes |
|---|---|---|
| #954 value brief | the compiled vocab + semantics (kindDefs descriptions/nearMisses), the per-pack extraction config (#1026), the S0 mask + glue-gate rules | packs (v3 + #1026 slots), criteria v1 |
| #955 extractor | S0-S3/S5/S6 stages, the chunked runner (chunk=6, id prefixes, per-chunk retry/skip), the deadline wrapper (600s, no token cap), the embedded-relations pass 2 | value brief, commit_schema (#952 merged), criteria v1 |
| #956 enforcer | the E-ladder over the deterministic rules (D11), the guards (D12), the collapse/relations alarms, violation events | the extractor's stream, the compiled vocab |

## Verification (maps to the model-free suite)

`tests/test_calibration_tools.py` (25 tests, no LLM) is the acceptance suite for the deterministic layer: the S0 mask, the guards, `validate_stream`, the source-event connector, the vocab↔ontology alignment. The experiments ledger is the acceptance record for the stochastic layer (band semantics per plan §6.3).

## Known remaining work

- Owner labels for the window-2 gate (DE2E-1 κ) — the rubric has now been exercised on 3 real windows; the formal κ gate is the remaining owner step.
- Connection strategies (embedding/LLM/hybrid) for source events — designed (#1026-adjacent), not yet run.
- The staged-embedded combination needs ≥2 more rolls for a median (the fix is validated: 57 relations vs 0 broken).
