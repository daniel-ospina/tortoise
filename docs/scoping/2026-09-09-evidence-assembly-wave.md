# Evidence-assembly wave — the reader is the lens, the package is the product

Date: 2026-09-09 · Epic #2080 · Parent: QA-loop → layer-3/4 stack

## 1. The measured phenomenon (why this wave exists)

Two 50-Q fast cycles on the hard slice s[150:199] (38 multi-session reasoning + 12
single-session preference), GPT-4o reader pinned, official judge:

| Cycle | Arms | Accuracy | evidence_recall@5 | Notes |
|---|---|---|---|---|
| C1 | all OFF | **0.780** | baseline | 39/50 |
| C2 | expansion + coverage-loop + evidence-boost ON | 0.740 | **+0.029** | 0 recoveries, 2 regressions |

Retrieval recall improved; end-to-end accuracy went DOWN. The regressions were
context flooding (the coverage loop merged **79** points into one question's
40-item window and broke a correct answer; evidence-boost re-ranked 11 items and
broke another). All 11 C1 failures stayed failed.

## 2. Failure taxonomy (11 failures, read against the gold)

- **6 preference questions** — gold = *"answer in a way that draws on the user's
  actual history"* (their cat Luna's shedding, their Suica+TripIt apps, their
  podcast tastes). The reader answered GENERICALLY ("in Tokyo use a Suica card")
  — the model's world-knowledge priors drowning the user's specific facts.
  Evidence was partially retrieved but unused.
- **5 multi-session questions** — arithmetic/enumeration errors: gold "100
  points" → reader "300"; gold "5 goals+assists" → "several goals and two
  assists"; gold "$12 per mug" → "$60 total" (wrong quantity). Two also had
  genuine retrieval gaps (evidence at @20 ≤ 0.03–0.29).
- The benchmark paper measures the SAME distribution: **40–50% of errors =
  correct retrieval + wrong generation**. Only 2/11 of ours were retrieval
  misses (evidence_coverage is already 0.98 → recall is near-saturated).

## 3. The line (hard rule for this wave)

- **The reader is a MEASUREMENT LENS, not the product.** GPT-4o + the official
  reader prompt + the official judge are FROZEN for every product measurement.
  No reader prompt engineering, no reader model swaps, no answer-format tricks
  count as fixes. (Reader experiments are allowed ONLY as diagnostics in a
  separately-labeled mode to learn what evidence package a consumer needs —
  never as a shipped change, never in a product-facing number.)
- **The product = extract → wire → recall → the EVIDENCE PACKAGE handed to the
  consumer.** Any optimization of what is retrieved, how it is selected,
  deduplicated, ordered, or value-annotated IS product work and is the target
  of this wave.
- The eval harness may change ONLY to render, byte-faithfully, whatever the
  product's assembly produces — never to add reasoning help of its own.

## 4. Why "more recall" is the wrong lever (research-grounded)

- Independent RAG literature: adding more relevant evidence to a fixed window
  DEGRADES answers (lost-in-the-middle; distractibility studies; DEG-RAG shows
  removing 40–50% duplicate evidence IMPROVES QA).
- Best practice: rank-then-admit ≈3–6 high-precision items; dedup so one fact
  appears once; compress to verbatim spans; order the decisive evidence first.
- Competitors (Zep closest): cross-scope search → **pack the best evidence into
  a small fixed budget**; a smaller, tighter block can outscore a larger one.
  None solves our problem; their top scores are self-reported on their own
  harnesses.
- Our 0.78 slice sits FAR above the official full-context baseline (GPT-4o with
  the whole 115k history scores 20% on preference / 44% on MR — we score 50% /
  87%) and close to the oracle ceiling (87–92%). The remaining ~10-point gap is
  evidence-assembly + value fidelity, not recall.

## 5. The fix wave (product slices, each cached-cycle-testable)

### Slice A — assembly: dedup + cap + order (start here; no extractor change)
Near-duplicate evidence fills the window (a distilled point + its source raw
chunks + its turns all restating the same fact). Product-side, in the retrieval
assembly:
1. Collapse a distilled point with its own source chunks/turns into ONE entry
   (keep the point + ONE verbatim source ref), so one fact occupies one slot.
2. Cross-item near-dupe dedup (embedding/similarity or shared-anchor overlap)
   before the window fill.
3. Order: exact-value/verbatim-marked items first; recency tiebreak.
The consumer (any downstream model) receives a deduped, tight, ordered package.
Measurement: same reader/judge/slice → the 2 flooding regressions should recover;
the burial class should not regress further. ~40-min cached cycle after a
single 4-h re-ingest ONLY if ingest output changes (Slice A does NOT touch the
extractor → cached ingest HITS → fast cycle immediately).

### Slice B — value fidelity: verbatim value-spans on value-bearing points
The aggregation errors (300-vs-100, $60-vs-$12) = the consumer summed wrong or
wrong-quantity numbers because the distilled point carried a paraphrase or lost
the exact value. Extend the extraction's value-fidelity discipline (#2542
family): value-bearing claims (numbers, dates, prices, counts, quantities) MUST
carry their exact value + the verbatim source span (source turn id + span), and
the assembly surfaces the span with the point. This makes "enumerate then
sum/count" reliable for ANY consumer. Requires an extractor change → ONE 4-h
re-ingest, then cached cycles.

### Slice C (diagnostic, labeled, never a product number) — reader probes
Use reader-prompt variants in the eval ONLY to answer: for preference questions,
does a "ground in the user's stated facts, name them, no generic advice"
instruction move 50% → X (i.e., is the residual assembly-shaped or
reader-capability-shaped)? For aggregation, does "list each evidence item then
sum" move it? The answers decide how much of the gap is closable by Slice A+B
(product) vs how much is a downstream-model limitation (out of scope; the
honest residual).

### Explicitly NOT in this wave
- More recall machinery, larger windows, retrieve-many-memories (Mem0-style):
  recall is saturated and flooding measurably hurts.
- Reader model/prompt changes as fixes (see §3).
- Knowledge-update / temporal categories: separate cycles on their own slices.

## 6. Acceptance + honest framing
- Slice A gate: C1's 0.780 reproduced (no regression) + the 2 C2 regressions
  recovered when the arms are re-enabled on top of the fixed assembly (the arms
  stay OFF-by-default; Slice A must make the product robust to them).
- Slice B gate: aggregation-class accuracy moves on the same slice with the
  SAME reader (no reader change); per-question: the value-bearing evidence is
  verbatim-present + deduped in the reader's package.
- Never claim a win from a reader change. Every product number = frozen reader +
  official judge + method line.

## 7. Evidence files
- docs/research/2026-09-09-context-window-best-practices.md
- docs/research/2026-09-09-competitor-memory-architecture.md
- docs/research/2026-09-09-longmemeval-internals.md
- Cycle receipts: .worktrees/opt/2080-measure/tools/longmem_eval/receipts/
  lme-p50-baseline-v3-20260908.json (C1), lme-p50-cycle2-20260908.json (C2).
