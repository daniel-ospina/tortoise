# Scoping — #2514: planted-operator (layer-2) corpus + grading

> **Date:** 2026-09-07 · **Branch:** `opt/2514-ops-corpus` · **Scope:** measurement
> only — a planted-OPERATOR extension of the W2 write-path corpus
> (`tests/eval/write_path/`) that grades whether the extractor wires the
> **right operator EDGE** between the right points, instead of only grading
> content survival (`planted_units` macro/strict) + leakage. No write-path
> product change; ontology findings are flagged, NOT resolved here.

## Problem (issue #2514)

The write-path bench grades CONTENT survival + leakage only — zero gold
assertions exist for the operator edges the product ontology says the
extractor must wire (SUPERSEDE, NEGATE/counter-claim, MITIGATES, SUPPORTS,
+ object/subject structure). `runner.snapshot_session` already computes
`operator_counts` per session, but they are **empty on every real run**
(receipts w2b-phaseg-llm-*, w2b-m2-lane-* all show `{}`): reified operator
nodes are `:Point {is_operator:true}` WITHOUT `eventId`, so the
eventId-keyed `MEMORY_ROW_QUERY` never admits them into the graded set and
`OPERATOR_EDGE_QUERY` (both endpoints in the session's seen ids) never
matches the `(op)-[:IMPL|NAND]->(endpoint)` fan-out. Nothing measures
whether the RIGHT edge materialized.

## Scope decisions (this branch)

1. **2 new fictional engineering-agent sessions** (codex harness, Peregrine
   world continuity), `wp06_quarry_rollout` + `wp07_bluepeak_followup`,
   authored with the existing `generate_corpus.py` discipline (units,
   distractors, hazards) PLUS a new sealed gold section `planted_operators`.
   Sessions auto-register (fixtures are discovered by glob; no
   `ci-surfaces.yml` change needed — the m2 gate + corpus tests key off
   `corpus.session_ids()`).
2. **New gold section** `planted_operators` — sealed answer key, lives only
   in `gold/` (fixtures stay adapter-visible). Each entry:
   `{id, expected_kind ∈ {SUPERSEDE, NEGATE, MITIGATES, SUPPORTS},
   from: {verbatim_anchor, planted_turn, session_id?},
   to: {verbatim_anchor, planted_turn, session_id?},
   relation_turn, reason}`. `session_id` is optional and defaults to the
   gold's own session — **SUPERSEDE is planted CROSS-SESSION** (wp07 → wp06)
   because that is the ONLY mechanism by which today's v2 extractor can form
   a point-level supersession (CORRECTS) in one sequential corpus capture:
   the extractor's S3 search must resolve the superseded claim already in
   the graph (real backend; sdk.py `capture_session` note — embedded lanes
   structurally produce zero supersession records).
3. **Grading = mechanical, hermetic, additive audit dimension** — NOT added
   to `schema.METRIC_VALUES`. Rationale: the blessed baselines
   (`main.json`/`m2.json`) snapshot the full 6-metric vocabulary and
   `validate_baseline` rejects published baselines missing any member;
   growing the vocabulary would force re-measurement of BOTH lanes. The m2
   echo lane has no relation extraction by construction (structural 0), so
   the operator bar is inherently a PRODUCT (llm) lane bar — same posture
   split as the standing leakage bar (PR #2183 finding 1). The operator-edge
   number is computed on every run (m2 and llm), carried in the report +
   receipt, and unit-tested — the corpus measures the current write path
   first (benchmark-first: first number is expected-bad).
4. **Both committed baselines are re-pinned to the new corpus hash**
   (`fixtures_hash` covers every fixture + gold file, so a gold edit
   invalidates both — README E2E-2 doctrine). m2.json is re-blessed from a
   REAL deterministic m2 replay (free, no model); main.json (llm posture)
   is corpus-blessed with its published numbers carried forward and an
   explicit justification that the llm lane was NOT re-measured on the
   extended corpus (see "Sealed run required to activate", below). The
   corpus validator requires both baselines to pin the current hash, so this
   carry-forward is the only non-API-money path that keeps the committed
   corpus valid; the v1→v2 judge-pin staleness on main.json (deferred Phase
   G) keeps every future llm run `inconclusive` until the sealed run lands.

## Ontology semantics table (per planted edge)

Citations: `docs/ONTOLOGY.md` §3.1 (Point↔Point operator vocabulary), §5
(Point kinds / Problem-family note), §8 (semantic-epistemic edge model),
§10.5 (supersession mechanics); `docs/EXPANSION_PACKS.md` (relations =
IMPL/NAND mechanisms); extractor rules live in `tortoise/extractor_v2.py`
(S2/S4 OUTPUT_CONTRACT + TRUTH-vs-WEIGHT rules — the extractor-facing
semantics the llm lane will follow).

| # | Session (edge owner) | Transcript relation sentences (exact planting intent) | from → to (anchors) | Expected graph edge | Ontology basis | Ambiguity flag |
|---|---|---|---|---|---|---|
| op_01 | wp06 `quarry_rollout` | H (turn ~9): "the race can only re-enter when two workers can claim one batch, and the lease makes that impossible regardless of rollout scope". E (turn ~13): "the chaos run just finished — a killed lease-holding worker was recovered and every batch was claimed exactly once". Connective (asst, relation_turn): "so the chaos run supports the claim that the race cannot re-enter once the lease is held." | SUPPORTS: E (observation) → H (hypothesis/claim) | `IMPL` operator edge E→H (reified op node `op_type: IMPL`, source = evidence, target = claim) | ONTOLOGY §3.1 `IMPL` "A supports/implies B"; §8 semantic type `supports \| IMPL` "Evidence supports Claim"; extractor S2 "IMPL = supports/implies". | **CLEAR** — no flag. |
| op_02 | wp07 `bluepeak_followup` | H2 (turn ~5): "the rollout flag is what caused the duplicate claims — the flip raced the lease renewal". Counter (turn ~11): "the flag did not cause it — it had been stable for two hours before the duplicates; the timestamps point at region clock skew" (negation marker "did not cause it"). | NEGATE: counter-claim (¬H2) → H2 | `NAND` operator edge counter→H2 (extraction-emitted NANDs default `unidirectional` — new-claim-attacks-existing) | ONTOLOGY §3.1 `NAND` "A contradicts B"; §3.1 extraction NAND direction policy (#909: new claim attacks existing ⇒ unidirectional); extractor S2 "NAND = contradicts / truth attack on the Point". | **CLEAR** — no flag (marker: explicit negation). |
| op_03 | wp07 `bluepeak_followup` | R (turn ~9): "the real risk is that clock skew between regions makes lease-expiry claims unsafe". M (turn ~17): "the skew-tolerant lease grace period is now in place — that risk is covered" (a risk-reduction action). | MITIGATES: action M → risk claim R | `MITIGATES` mitigation structure on the claim/edge touching R | ONTOLOGY §3.1/§3.9 register `mitigated_by` only as **operator relevance modulation**: `(op)-[:mitigated_by]->(m)` + `(m)-[:IMPL]->(op)`; §8 "an edge carries an operator iff it needs mitigation"; extractor S2 MITIGATES = "claim TRUE but matters LESS than it seems" (relevance attack on an IMPL edge, strength ≤ 0.50 — above 0.50 the extractor is told to use NAND). | **FLAGGED (ontology finding):** ONTOLOGY §3.1's point→point vocabulary is IMPL/NAND/hasPart/CORRECTS only — there is NO first-class point→point "action reduces risk" operator, and the registered MITIGATES semantics is relevance-on-an-operator-edge, not action-reduces-risk. When the action falsifies the risk, §5 (Problem family: "the claim point is superseded/retracted when the problem materializes") points at NAND/supersede instead. Gold asserts MITIGATES (the issue's vocabulary + the write path's `op_type: MITIGATES` / mitigation-Point shape); a NAND emission is graded as `wrong_edge` and the ambiguity is reported to the ontology owner (#2514 finding). |
| op_04 | wp07 → wp06 (cross-session) | D1 (wp06 turn ~15): "we ship the lease fix all-at-once behind a single global kill flag" (decision). D2 (wp07 turn ~19): "reverse that call — per-service rollout flags instead of one global flag" (overturns D1). | SUPERSEDE: D2 (new) → D1 (superseded) | `CORRECTS` edge D2→D1 (old marked outdated/superseded) via the supersessions channel → `sdk.supersede` | ONTOLOGY §3.1 `CORRECTS` "New point corrects/replaces an outdated point … Created by `supersede_point` / `invalidate_point`" + supersession semantics; §10.5 cascading invalidation (`supersede_point` → CORRECTS → dirty → EP). | **FLAGGED (ontology finding):** the point-level CORRECTS path exists (§3.1) but the v2 EXTRACTOR's supersession model is STATE/ENTITY-centric (S2 rules: emit NEW entity + ONE statement point "B supersedes A"; ObjectSuperseded event + Object fold — §2 "decisions are NOT first-class Points"); within a single fresh session a decision reversal does NOT today produce a point→point CORRECTS — the extractor forms pt-level supersessions only when its S3 search resolves the superseded claim already in-graph (cross-capture, real backend). Planted cross-session so the current mechanism CAN produce the edge on the real lane; whether the ontology REQUIRES point-level CORRECTS for in-session reversals is left to the ontology owner. |

**Findings for the ontology owner (#2514):**
- F1 (op_03): no point→point "action reduces/mitigates risk" operator in the
  core vocabulary — only operator-edge relevance modulation (`mitigated_by`).
  "Risk reduced by an action" currently has no unambiguous core edge.
- F2 (op_04): state-centric §2/§5 vs §3.1 CORRECTS — when a DECISION overturns
  a prior decision inside one conversation, the ontology does not clearly
  require a point→point CORRECTS (extractor default is entity supersession +
  a merged "B supersedes A" statement point). Decision reversal is not a truth
  attack (NAND) and not pure restatement.

## Graded semantics (mechanical, per planted edge)

Per planted edge, the grader (hermetic, over the session snapshot surface)
reports a verdict + failure class:

| Verdict | Failure class | Meaning |
|---|---|---|
| content ok | `from_content_missing` / `to_content_missing` | no memory Point of the anchor's session carries the anchor text (bare-point failure starts here) |
| edge correct | — | the expected graph edge (IMPL/NAND/CORRECTS/MITIGATES form per table) connects the from-point → to-point |
| edge wrong | `edge_missing` / `edge_kind_mismatch` / `edge_direction_mismatch` | both points survive but the right operator edge between them does not |

Aggregate: `planted_operator_edges_correct` (correct/total) carried as a
report + receipt audit field on every run (both lanes). NOT a `METRIC_VALUES`
member in this change (see Scope decision 3) — promotion to a gated metric
goes with the sealed llm run.

## Sealed run required to activate (do NOT spend API money in this branch)

1. **m2 deterministic replay + corpus-bless** (executed in this branch, free):
   `TORTOISE_SESSION_EXTRACTOR=m2 TORTOISE_SESSION_LLM_MOCK=1 .venv/bin/python -m tests.eval.write_path.runner run --out <receipt>` over the extended corpus,
   then `bless --receipt … --corpus-bless --write` → re-pins `baselines/m2.json`
   to the new corpus hash with REAL deterministic numbers (the m2 CI gate:
   verdict PASS on clean replay). Operator-edge audit on m2 = structural 0
   (echo lane has no relation extraction) — expected and noted, never a bar.
2. **LLM product-lane sealed run** (REQUIRED later; NOT executed here):
   `TORTOISE_DB_URI='docker://:falkordb@…' .venv/bin/python -m tests.eval.write_path.runner run` on the extended corpus, 5/5+ sessions emitting, then
   `bless --receipt … --corpus-bless --protocol-bless --write` to re-pin
   `baselines/main.json` (hash re-pin + mechanical v1→v2 protocol re-pin in
   ONE deliberate step, resolving the deferred Phase G pin staleness) with the
   fresh llm numbers INCLUDING the new `planted_operators` audit (first real
   layer-2 measurement — expected-bad per benchmark-first). Until then the
   llm lane is `inconclusive` (judge-pin mismatch v1 vs v2) by design.
3. Optional follow-up issue: promote `planted_operator_edges_correct` into
   `schema.METRIC_VALUES` + posture baselines once a real llm number exists
   and the ontology findings F1/F2 have an owner decision (would be a
   protocol-bless on both lanes).

## Files touched

`docs/scoping/2026-09-07-2514-operator-corpus.md` (this note) ·
`tests/eval/write_path/{generate_corpus,schema,grading,runner}.py` ·
`tests/eval/write_path/gold/wp06_*.gold.json` + `gold/wp07_*.gold.json` +
`fixtures/wp06_*.json` + `fixtures/wp07_*.json` ·
`tests/eval/write_path/_manifest.json` · `baselines/{main,m2}.json` ·
`tests/eval/write_path/test_write_path_corpus.py` + `test_write_path_grading.py`
(+ additive runner coverage) · `tests/eval/write_path/README.md`
