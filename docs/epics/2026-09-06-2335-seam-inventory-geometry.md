# WI-3 memo — segmentation seams + geometry options (write path)

> **Issue:** #2335 (write-path telemetry) Task 6 · **Consumer:** #2281 WS2
> (ingest-side coverage eval: planted-gold extraction recall on multi-session
> haystacks, gating the ask surface) · **Date:** 2026-09-06
> **Status label: UNVALIDATED-DRAFT-DISCARD-ON-FIRST-DATA.**
> The seam anchors below are from code reading + the #2408 census — they are
> design constraints for the segmentation GEOMETRY question, NOT a
> destination. First measured data that contradicts an anchor supersedes it;
> each anchor carries a shelf-life note.

## 0. What this memo is for

#2281's WS2 needs to evaluate extraction recall over **multi-session
haystacks** (the #2134 truncation fix's real geometry: N sessions × M turns).
The extractor today is **one-shot per capture** — a whole conversation
(possibly spanning many sessions of context) is one `extract_session_v2`
call. The open question WS2's eval will inform: **do we need to split the
write path into per-segment extractions (segmentation), and if so, on which
seams?** This memo inventories the seams that exist in the current pipeline,
the orchestration gap a segmented product lane would have to fill, and the
geometry options — with cost reasoning and a probe spec. It does NOT decide;
it equips the decision with the measured #2408 evidence.

## 1. Seam inventory (current pipeline)

The v2 extractor's own pass structure (S1–S4) is the natural seam atlas.
Each seam below is where a "segment" could start/end, with what it would
carry or break.

| # | Seam | Where | What rides it | Shelf-life note |
|---|---|---|---|---|
| 1 | **Cross-segment references** | S1/S3 extraction: a later turn references a decision/entity minted from an earlier turn | `link_before_create`, `supersessions`, `about_entities` (entity reification dedupes against the graph, not the segment) | Re-verify against planted-gold cross-session recall data (WS2) |
| 2 | **Within-session REVISES** | S4 gap review + supersede: a user updates a prior stance inside one long conversation ("not Chicago — the suburbs") | `validFrom`/supersede wiring, tier-A re-emission | The #2408 census's `unchanged_share` 0.9683 / `verbatim_reemissions_total` 1710 — VERBATIM re-emission is the norm, not the exception; a revise seam MUST NOT re-emit the whole prior point |
| 3 | **Byte-identity / eval parity** | `tools/longmem_eval` (ingest_v2) reads the SAME `extract_session_v2` | planted-gold eval geometry must match product geometry or the gate is measuring a different machine | Pinned: any segmentation must keep byte-parity between eval lane and product lane (the #2335 Task 1 lesson: hosted mock lane runs the real extractor — parity is observable) |
| 4 | **S4 gap semantics** | S4 merges S2 verbatim-identical + corrected items | `s4_merge.verbatim_reemissions` (the #2408 accumulator) | 82.1% of S4 output tokens redundant on real LongMemEval geometry (234,569 / 285,814); corrections_share only 0.023 — the S4 merge is doing almost pure verbatim relay, which segmentation must not duplicate |

## 2. Greenfield product-lane orchestration gap

A segmented product lane does NOT exist today. Building it means solving
these (none of which a per-segment extractor call alone answers):

1. **N-extraction capture.** One capture call → one extraction. Segmented =
   N extractions per capture, each with its own stats/errors/observation
   line. The capture_ok / TRUE-retry state (WI-2b, this issue) is per-Session,
   not per-segment — a segmented retry needs per-segment outcome state or the
   retry semantics go coarse again.
2. **#1727 replay-skip / provenance.** The replay no-op + the deterministic
   `_session_capture_event_id` provenance assume one Event per Session.
   Segmented extractions either share that one Event (segment ids converge on
   it) or mint N events — the #1727 zero-new-node invariant pins the replay
   shape, and the mint-refresh-cost note (ON MATCH SET refreshes
   startedAt/endedAt) applies per re-run.
3. **Ceilings do NOT lift.** Corrected anchors (review PR #2473 — the
   soft-15/hard-25/ceiling-50→402 ladder is the COMMIT lane's
   `MAX_VALUE_POINTS_PER_SESSION` (quota.py), while `MAX_PAYLOAD_POINTS = 50`
   is the deliberately separate Layer-1 raw payload cap → 422 (commit_schema
   — the two 50s must never be wired together), and the capture path's hard
   bound is the team points-quota 402 gate, NOT either 50). Segmentation does
   not raise ANY of these. `MAX_EXTRACTIONS_PER_TURN = 200` gates BOTH
   estimate lanes (the shared v2 estimate is 3 × Σ min(sentences, cap) —
   it is not M2-only). Each segment's output still needs the escalation net
   (`escalated_*` counters, #2408 Task-4 shape) so no per-capture write gets
   clipped mid-stream.
4. **Escalation is the real cost surface.** #2408 measured **zero escalations
   across 155 sessions / 466 calls** — the escalation net never fired on
   real LongMemEval geometry. The cost-materiality readout is escalation
   TOKEN spend, not call count; segmentation multiplies calls but the
   expensive failure (escalation) is what the quarterly readout watches.

## 3. Geometry options (for the WS2 eval to discriminate)

| Option | Shape | Wins if | Loses if |
|---|---|---|---|
| **A. One-shot (status quo)** | whole conversation → one extraction | recall already high on ≤51-turn single sessions (the #2280 spot-check geometry) | the #2134 truncation gap reappears past the stored window; per-seam max out-token blows a cap |
| **B. Fixed-turn chunks** | conversation sliced at N-turn boundaries, each chunk extracted | bounded per-call cost; simplest orchestration | slices mid-REVISE / mid-decision; cross-chunk references need re-wiring (seam 1); chunk-local S4 loses the gap semantics (seam 4) |
| **C. Semantic segments** | slice on the S1/S2 seams (topic / decision-boundary detection) | segments align with real epistemic units | needs a segmentation model/pass = new architecture (out of scope here); risk of the classifier being the new #2134 |
| **D. Continuation-carry** | chunked extraction where each chunk carries the prior chunks' distilled points as context (re-extract, not re-emit) | preserves cross-segment refs without N-mint orchestration | re-extraction cost ≈ the 82.1% redundancy the census found — pays for the verbatim relay twice |

The memo does not pick. WS2's planted-gold recall across synthetic ≥51-turn
multi-session haystacks is designed to make A vs B measurable (below).

## 4. The 2-chunk S3 FTS recall-ceiling probe spec (design-constraint)

A **design constraint, NOT a destination falsifier** — it bounds what any
geometry must achieve, it does not pick the geometry.

- **Data:** synthetic ≥51-turn multi-session conversations (the stored-window
  boundary at 50 turns + the #2134 truncation regime; 51 forces a 2-chunk
  shape under option B).
- **Method:** split the SAME conversation at 25/26 turns → 2 `extract_session_v2`
  calls → S3 FTS recall over the union of chunks vs the one-shot recall over
  the same conversation. Recall ceiling = does 2-chunk recall ≥ one-shot
  recall on planted gold? (If 2-chunk < one-shot, chunking LOSES context the
  FTS/reader relied on — a hard no on B for the FTS lane.)
- **Instrument:** the per-seam max out-token telemetry (Task 4 observation
  leg: `max(sX_out_tokens, truncation_completion_tokens_sX)`) is the
  truncation sensor — a chunk whose seam-max approaches the completion cap is
  a chunk the windowing will clip.
- **Gate:** this is WS2's number. #2335's GO/NO-GO only fires on PRODUCT-lane
  loss events (Task 7 record), which this probe anticipates.

## 5. Cost reasoning (stated, no cost-based GO)

- **No cost-based GO.** The GO/NO-GO gate (#2335 Task 7) fires on LOSS events
  (chunk-aware: ≥2-chunk + double-residual vs the effective escalation
  ceiling, config-normalized), never on token cost alone.
- **Escalation-token spend = the quarterly cost-materiality readout.**
  #2408: zero escalations / 466 calls on real geometry — the escalation path
  is rare on LongMemEval-shape traffic; the readout re-confirms quarterly
  (event-driven cadence, NOT a monthly read — D4 observation-leg record).
- **Segmentation cost floor:** option B/D both re-extract shared context; at
  the census's 82.1% verbatim-relay rate the re-extraction duplicates ~80%
  of the relayed tokens. Cost reasoning must state this floor BEFORE any
  geometry commit; the WS2 recall delta is what justifies paying it.

## 6. Discard clause + refresh

- This memo is **UNVALIDATED-DRAFT-DISCARD-ON-FIRST-DATA**: the first
  WS2/measurement datum that contradicts a seam anchor supersedes it
  (each row's shelf-life note says what to re-verify).
- Anchors expire: seam 1/2 against WS2 planted-gold data; seam 4 against the
  next census-style run on product traffic; §4's probe against its own
  result. Refresh = re-run the noted measurement, then update or strike.
