---
title: "Strategy Alignment Decision — Epic #1509: Extractor V3"
type: plan
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-20
aboutSubjects: tortoise
aboutObjects: extractor
---

# Epic #1509 — Extractor V3: Strategy Alignment (Stage 1)

*Generated via epic-align. Inputs: the approved scope (00-scope.md), the 12-report corpus (/tmp/v3-review, /tmp/v3-setup, /tmp/v3-synth), the v2 measurement reality.*

---

## Step 1 — Adversarial Strategy Test

### Alternatives considered

1. **Baseline-first (fix harness, re-run v2 as-is, then iterate).** — *Rejected by owner, and the rejection is sound:* a valid v2 baseline measures a design we already know is broken in known ways. The cost of one extra ~2.5-day run buys a comparison against an extractor we're about to replace. The V3 run becomes the baseline for V4 — the same learning loop, one run sooner, no throwaway.
2. **Buy/adapt Mem0 or Graphiti instead of evolving Tortoise.** — *Rejected:* the competitor stack (BM25+dense+rerank+MMR+temporal) informs the retrieval design and is consciously converged on, but Tortoise's differentiators — the epistemic layer (Points/EP confidence/NAND/MITIGATES/supersession), the Subject/Event/Object ontology, provenance — are the product's reason to exist. A bolt-on replaces the graph with a flat fact store and reproduces the same measurement problem. Adopting Graphiti's *data model ideas* (validity windows, entity resolution) ≠ adopting Graphiti.
3. **Deploy v2 as-is and iterate in production.** — *Rejected:* an unmeasured extractor cannot be steered. The v2 run proved we can't even trust a published number; product iteration on top of that is guessing. This is the core reason the measurement work is not optional overhead.
4. **Drop LongMemEval, evaluate on our own product tasks.** — *Considered, deferred:* LME is the standard that exposes the categories (KU, TR, Abstention) that matter for epistemic memory; product-only eval would hide the same failure modes behind task-specific noise. Keep LME as the objective yardstick; the harness fixes make it trustworthy.

### Anti-post-rationalization (argue AGAINST this epic)

- **The V3 bet may not pay:** most fixes (date anchoring, state-value tier, retrieval legs) have *code-verified mechanisms but unmeasured impact*. We could spend 4–6 weeks and see parity on a valid run. The honest expectation is that IE/KU/TR move, but nothing guarantees it.
- **Slow iteration loops:** each full run costs ~2.5 days + API spend. Even a perfect V3 build takes 3–4 run cycles to converge. If the team needs measurable memory gains this quarter, a faster (if cruder) path — e.g. shipping only the harness fixes + date anchoring, skipping the retrieval stack — would produce a measurement sooner.
- **Convergence risk:** the retrieval target (dense+sparse+rerank+MMR) is where every competitor already is. If the graph layer doesn't add measurable value on LME, we've spent weeks converging on parity with Mem0 while carrying more infrastructure.
- **Maintenance tax:** real-backend eval (real FalkorDB, embedder, FTS index) is more infra to run and keep healthy than the current embedded fallback. The user's "test the real tortoise" demand is correct, but it raises the operational bar.

### Opportunity cost

- **If we don't build V3:** we keep shipping on an unmeasured memory layer; production capture is *already* running v2, so the graph is accumulating extraction whose quality we cannot attest. The harness fixes (M1–M8) are needed regardless of any design direction — they're not throwaway even if the design were wrong.
- **What we'd build instead:** the same M1–M8 harness fixes (non-negotiable), then product features on top of unvalidated memory. The marginal cost of the E/R/A changes is design + one build; the marginal benefit is the first *measurement-driven* iteration loop the product has had.

---

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | **Do now — V3 core (harness M1–M8 + production P1–P4) + E/R/A bundled (run is the binding constraint, marginal build cost low)** | **Schedule — V4+ compounding (bi-temporal validity windows, cross-encoder rerank, calibration abstention)** |
| **Not Important** | Delegate (eval ops hygiene — folded into M6/M7, not a separate workstream) | Eliminate (nothing — the v2-baseline idea is already cut) |

**Placement:** Important + Urgent → **Do now.** Justification: production capture already runs v2 (the extractor is live); every session written today is written by a mechanism we cannot measure. That makes the *harness + reliability* core urgent (M1–M8, P1–P4). E/R/A (content/retrieval/reader) is Important + Not Urgent in the strict sense, **but ships in the same build** — the bundling argument is real: the run is the expensive asset (2.5 days + API cost), and shipping E/R/A in the same build costs only marginal build effort while avoiding a second run cycle. This is an explicit pull-forward of the Schedule cell, justified by the run constraint — not a contradiction of the matrix.

---

## Step 3 — Profit Growth Alignment

**Causal chain:** extractor+retrieval quality (facts retrievable, current, attributed, temporal) → epistemic memory answers correctly (LongMemEval categories: IE/KU/TR/MSR/Abstention) → agent/product conversations grounded in true memory → user trust in the memory layer (the product's differentiator) → retention/expansion of the memory product → revenue.

**Honest quantification:** this is foundational infra, not a direct revenue feature. Direct impact: low ($10s–100s/mo via API/reliability savings — DeepSeek-direct over OpenRouter). Indirect: high (unblocks measurement-driven iteration = option value; the memory product's credibility is the whole go-to-market). A *faster* path to the same profit outcome doesn't exist — the product IS the memory layer; there is no revenue without a trustworthy one.

---

## Step 4 — Decision Rationale

## Strategy Alignment Decision

**Feature:** Extractor V3 — fix the measurement, ship the vision (epic #1509)
**Decision:** PROCEED

**Alternatives considered:**
1. Baseline-first re-run — rejected by owner (no value in measuring a known-bad v2; V3 run = V4 baseline)
2. Buy/adapt Mem0/Graphiti — rejected (loses the epistemic/graph differentiator; same measurement problem)
3. Deploy v2 as-is — rejected (unmeasurable = unsteerable; production already runs v2 so the fix is urgent)
4. Drop LME for product-only eval — deferred (LME exposes the categories that matter; harness fixes make it trustworthy)

**Profit impact:** indirect (foundational). Causal chain: extraction+retrieval quality → correct epistemic memory → user trust → retention/revenue. Direct savings: DeepSeek-direct vs OpenRouter ($10s/mo). Option value: first measurement-driven iteration loop for the product (high).

**Eisenhower placement:** Important + Urgent → Do now (harness+reliability core is urgent because v2 is already in production; content/retrieval ships in the same build because the run is the expensive asset).

**Key assumptions:**
- The 12-report corpus is accurate (independently re-verified across 3 synthesis agents) — confidence: **high**
- Fixing the known issues yields measurable improvement on a valid run — confidence: **medium** (mechanisms code-verified, impact unmeasured)
- Real-backend eval (real FalkorDB + embedder + FTS index) is feasible in the eval env — confidence: **medium** (infra availability TBD)
- The ontology needs no changes (2.2 resolved as Points, owner-approved A) — confidence: **high**
- DeepSeek-direct is production-viable — confidence: **high** (adapter already proven in the eval)
- **Per-run API budget is secured and within cost envelope — confidence: medium.** The single demonstrated cause of the void v2 run was financial (21,342× HTTP 402 billing wall). Estimate: 500 questions × ~180 extractor calls + reader + judge ≈ $15–40/run on DeepSeek-direct (flash pricing) + judge gpt-4o (~$10–20) — must be confirmed with a pre-flight billing probe (scope M2) before each run. Budget failure = run void, so this is a gated assumption, not an afterthought.
- Team/agent capacity to land the full M+P+E/R/A build while production v2 stays live — confidence: **medium.** If capacity binds, cut order is explicit: R2/R5 (retrieval stack improvements) first, then E5 (supersession end-to-end) — harness (M) and production (P) are never cut.
- Iteration loop (run → learn → V4) stays affordable (each run ~2.5 days + API cost) — confidence: **medium**

**Recommendation:** PROCEED. Ship the harness+production core first (it's urgent regardless of design direction), then the E/R/A build, then ONE run that becomes the V4 baseline. The risk that the V3 bet doesn't pay is real but bounded — the harness fixes retain value in every world.

---

## Step 5 — Routing

**PROCEED** → hand off to epic-research (Stage 2).
