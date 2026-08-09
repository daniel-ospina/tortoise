---
title: "Strategy Alignment Decision — Epic #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Strategy Alignment Decision — Epic #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4

**Feature:** Entity extraction + cross-session dedup (Phase 2) and cross-ontology integration (Phase 4) on top of the Phase-1 ConversationMiner pipeline.
**Decision: CONDITIONAL PROCEED — planning proceeds now; execution gated on #320 (session indexing) reaching STABLE and a calibration gate on Phase 1 extraction precision (Gate B — see instrumentation note below).**

> **Routing-change flag (reviewer P1, accepted):** This re-scope routes PROCEED → epic-research while the original 7740 align ruled "do NOT advance to epic-research yet" (Gate A not met). This is a deliberate reversal, not continuity: (a) ONTOLOGY v3.2 settles the design space (about* edges, no Action layer) independent of #320's completion; (b) research + planning are cheap, non-graph-writing artifacts; (c) execution stays hard-gated on Gates A+B. Child issues are therefore created in a **blocked-on-gates state** — each carries `Depends on: #320 + #416(+calibration)` — so issue-workflow/epic-executor cannot pick them up prematurely.

> **Gate B instrumentation gap (reviewer P1, accepted):** #416's actual gate criteria are **volume-based** ("≥3 events/session from ≥7 of 10 transcripts, <$1 LLM cost") — NOT precision-based. The original 7740 align's "≥70% human-reviewed precision" was never written into #416. Passing #416 does not establish ≥70% precision (a noisy high-volume extractor clears it trivially). Fix: a new **calibration milestone issue** (created in Decompose) carries the real Gate B: ≥70% human-reviewed precision on a 50-session sample + EP-grounding before/after measurement, per the concrete metric defined below. #416's outcome is a dependency input to that calibration, not a substitute for it.

## Review-gate fixes applied (2026-08-09, fresh-context reviewer)

1. **P1 — Gate B instrumented:** precision gate moved off #416 into a dedicated calibration milestone issue (see Decompose stage). #416's volume gate remains a dependency input, not the precision gate.
2. **P1 — Routing reversal flagged:** planning-now/execute-later is a deliberate reversal of the 7740 "do not advance" ruling, justified in the header; child issues are gate-blocked.
3. **P2 — draft-status hole closed:** SDK auto-promotes Points to `live` on first edge creation (#131) — the draft-only-until-review mitigation therefore fails at the exact moment Phase 4 wires edges. Plan pins: EP queries exclude `status: draft`; extraction-created Points do not auto-promote; `status: draft` is an explicit output contract of mining.py, not the SDK default. **Clarified in plan §8.4 (coherence review): "no auto-mitigation wiring" means no wiring to LIVE Points; draft-to-draft links are permitted with draft operator nodes (EP-inert under the draft filter) so contradiction/duplicate links surface for review without propagating pollution.** (SDK #131 behavior verified during re-scope review; encoded in Plan.)
4. **P2 — EP regression made measurable:** concrete metric: snapshot mean grounding across all `live` epistemic Points pre/post extraction batch, threshold ≤2% mean absolute change, sample = full live Point set. No regression tooling exists today — calibration milestone includes building the before/after snapshot query.
5. **P2 — Reuse claim scoped:** Phase 1 pattern reuse is HIGH for pipeline scaffolding (EventRecorded provenance, MCP plumbing, extractor harness) and MEDIUM for extraction-task transfer (entity/Object extraction is a different task family than Point/operator extraction).
6. **P2 — Profit falsification metric sharpened:** "query" = user-initiated search/traverse API call (excludes internal pipeline reads), plus absolute floor of ≥3 distinct querying users.

## Status of prior decision (7740-insight-mining-align.md)

The original epic (#7740 → consolidated into #264) was decided **CONDITIONAL PROCEED** with two hard gates:
1. **Gate A (prereq):** #7708 (now #320) reaches STABLE — sessions demonstrably indexed and searchable.
2. **Gate B (calibration):** Phase 1 extraction on 20–50 sessions, ≥70% human-reviewed precision, no EP grounding regression.

**Re-scope facts (2026-08-09):**
- **Phase 1 IS DELIVERED and in production pilot:** `tortoise/mining.py` (ConversationMiner — transcript → Events + Points + IMPL/NAND with provenance); `tortoise/session_indexer.py` (`extract_metadata_with_llm`); MCP `tortoise_index_sessions`. Pilot tracked by #416 (OPEN, gated).
- **Phase 3 is DELETED** per ONTOLOGY v3.2 (Action dissolved in v3.0; `instantiates` removed by #214; procedural layer = "status derived from event stream, not stored" §2). Replaced by Event→Object `aboutObject`/`uses`/`produces` wiring (§3.5).
- **Remaining scope = Phase 2 (entity extraction & cross-session dedup) + Phase 4 (about*/structural wiring, EP propagation on extracted Points, temporal belief tracking).**
- **Dependencies:** #320 (session indexing — I3: 4,190 conversations) still OPEN; #416 pilot gate outcome pending.

## Adversarial Test (Step 1)

**Alternatives considered:**

1. **Do nothing more — let the graph grow organically from manual capture** — Rejected: manual capture does not scale (4,190 conversations cannot be hand-mined); the compounding value of the graph ("we already decided this" recall) requires automated cross-session entity/insight consolidation. Phase 1 proved extraction is feasible; stopping now leaves Point soup with no entity backbone.
2. **Ship only Phase 2 (entity dedup), defer Phase 4 (EP propagation + temporal)** — Partially valid sequencing: Phase 4's EP propagation is the highest-risk operation (see nuclear risk below). **But** cross-ontology wiring is what makes extracted Points *usable* (searchable via about* edges, temporal tracking makes beliefs actionable). Splitting phases 2/4 into separate epics would fork the ontology work mid-flight. Deferral of *execution* of Phase 4's riskiest sub-items (EP propagation) is handled inside scope, not by epic deletion.
3. **Merge #264 Phases 2–4 into #438 (automated cross-domain connection discovery)** — **Rejected — boundary violation.** #438 is graph-driven connection discovery (existing graph → new connections); #264 is *conversation-driven* extraction (unstructured text → new graph content). Different data sources, different pipelines. Keeping them separate preserves traceability. (Boundary documented in Scope.)
4. **Rule-based entity extraction (regex/keyword) instead of LLM** — Partially adopted: `session_indexer.py` already has keyword fallback; rules are good for *known* entity classes (issue/PR refs `#NNN`, tools). LLM remains the differentiator for *open* entities (concepts, domain objects) and content dedup semantics. Hybrid: rules-first for reference entities, LLM for open entities — reduces cost and pollution.
5. **Re-run full Phase 1** — Rejected: Phase 1 is done and piloted (#416). Re-planning it wastes pipeline capacity.

**Anti-post-rationalization (strongest reasons NOT to build Phases 2–4 now):**
- **Gate A is NOT met:** #320 is still OPEN (I3: 4,190 conversations indexed is not complete). Building cross-session dedup on top of an incomplete event index means dedup runs over a partial universe — entity resolution quality degrades and re-runs are needed. This is a genuine hard gate for *execution*, not a sequencing caveat.
- **Gate B is NOT closed:** the calibration milestone (which carries the ≥70% precision criterion) has not passed — it awaits #416's pilot data as input. If Phase 1 extraction is imprecise, Phase 2 dedups *noise*, and Phase 4 propagates *noise* through EP — amplifying pollution.
- **Nuclear risk (unchanged from original align):** EP propagation on low-quality extracted Points wired through mitigation edges can nuke EP weights (the exact failure mode AGENTS.md hard-rule warns about). Phase 4's EP step must be gated: extracted Points start `status: draft`; no extraction-created Point auto-wires mitigation edges; calibration before full batch.
- **Entity dedup is genuinely hard:** cross-session resolution errors create duplicate Objects that poison semantic search forever. v1 fuzzy matching is explicitly a non-goal-adjacent risk (semantic dedup is v2) — must be scoped tightly.
- **LLM cost:** batch extraction over 4,190 conversations × (entity + dedup + temporal) passes is non-trivial. Throttled batch processing is a stated non-goal (real-time), cost must be bounded.
- **Opportunity cost:** while #320 is OPEN, the highest-leverage work is finishing session indexing + search gaps; Phases 2–4 are compounding differentiators that land *after* the event index is stable.

## Eisenhower Matrix (Step 2)

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | #320 session indexing completion (Gate A) | **#264 Phases 2–4** (entity dedup + cross-ontology integration) |
| **Not Important** | — | Perfect entity resolution (v2, semantic dedup) |

**Placement: Important / Not-Urgent → SCHEDULE**, with explicit ordering: #320 first (Gate A), then #416 pilot (volume gate — dependency input), then calibration milestone (Gate B — precision + EP regression), then Phases 2–4 execution. This is unchanged from the original decision — the re-scope does not change urgency, it narrows scope (Phase 3 deleted). The honest strategic question remains user-acquisition vs product-depth; this epic assumes product-depth is the path, consistent with the hosted platform bet.

## Profit Growth Alignment (Step 3)

**Causal chain (testable):** Sessions mined (Phase 1 ✓) → entities deduplicated + connected via about* edges (Phase 2) → extracted Points get EP confidence + temporal tracking (Phase 4) → graph answers "we already decided this" + "what changed" → users return to query → retention → conversion.

**Falsification criteria (leading indicators, inherited + updated):**
- Within 60 days of Phases 2–4 shipping: ≥10% of mined sessions generate ≥1 **user-initiated** graph query (search/traverse API call, excluding internal pipeline reads) per week, AND ≥3 distinct querying users.
- Extraction precision ≥70% on a 50-session human review sample (Gate B — carried by the calibration milestone issue, not #416).
- EP health preserved: snapshot mean grounding across all `live` epistemic Points changes ≤2% (mean absolute) after extraction batches; `status: draft` Points excluded from EP propagation entirely (guard: draft status is an explicit mining.py output contract; no auto-mitigation wiring from extraction; no auto-promotion on edge creation).
- Entity dedup precision ≥70% on a cross-session sample (new for Phase 2): duplicate Object creation rate below threshold.

**Revenue estimate methodology:** Placeholder hypothesis (unchanged from original): $10s–$100s/month upper-bound sanity ceiling based on per-seat memory-feature pricing (Mem0/Honcho) and retention logic. NOT a target. The falsifiable indicators above are the real measure.

**Faster path?** Yes — Phase 2 alone (entity dedup + about* wiring) delivers most of the cross-session value with lowest EP risk; Phase 4's EP propagation is the last sub-item to execute, behind the calibration gate. Sequencing inside the plan reflects this.

## Key Assumptions (confidence updated for re-scope)

- #320 reaches STABLE before Phases 2–4 execution — confidence: **MEDIUM** (epic OPEN with defined I3 target; no known blockers, but not yet complete).
- #416 pilot passes its volume gate (≥3 events/session) providing extraction data — confidence: **MEDIUM** (Phase 1 code ships and is being validated; volume at scale is unproven). The calibration milestone (Gate B: ≥70% precision, no EP regression) then gates Phases 2–4 execution — confidence: **MEDIUM** (precision is the open risk; metric + tooling defined above).
- Phase 1 pipeline scaffolding (ConversationMiner, extractor harness, EventRecorded provenance, MCP plumbing) is reusable for entity extraction — confidence: **HIGH**. Extraction-task transfer (Point/operator → entity/Object recognition, content-dedup semantics) is a different task family — confidence: **MEDIUM**, validated by a spike during epic-research.
- ONTOLOGY v3.2 about* edge model (aboutObject/uses/produces) is the correct replacement for the deleted Action layer — confidence: **HIGH** (v3.2 canonical; #214 confirms instantiates removal; §3.5 subject→event→object is explicit).
- LLM entity extraction + fuzzy dedup is sufficient for v1 without polluting the graph — confidence: **LOW-MEDIUM** (fuzzy matching is v1 by stated non-goals; dedup precision is the main open risk).
- **Graph pollution from low-quality extractions won't degrade EP belief propagation — confidence: LOW (unchanged — the nuclear risk).** Mitigation unchanged: `status: draft` Points, no auto-mitigation wiring from extraction, calibration gate before full batch.
- Users value mined/consolidated insights over raw search — confidence: **LOW** (zero evidence at ~0 users; falsification criteria test this).

## Recommendation

**CONDITIONAL PROCEED** — plan Phases 2–4 now (planning produces the design + MECE child issues, each created **blocked on Gates A+B**), with two execution gates:
1. **Gate A:** #320 reaches STABLE (sessions indexed per I3, searchable).
2. **Gate B:** calibration milestone passes — ≥70% human-reviewed extraction precision on a 50-session sample AND EP-grounding regression ≤2% mean absolute (metric + tooling defined in this align; calibration issue created in Decompose; #416's volume-gate outcome is an input to calibration, not the gate itself).

Phases 2–4 execution (via child issues) starts only after both gates. This is the honest update of the original decision: the re-scope removed the deleted Phase 3 and narrowed the plan to the two phases whose value is proven by Phase 1's delivery, while preserving the two gates that protect the graph from pollution.

## Routing

**PROCEED → epic-research** (Stage 2). Execution of child issues remains gated on Gates A+B per this decision.
