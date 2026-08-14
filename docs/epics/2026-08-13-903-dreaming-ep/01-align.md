---
title: "Strategy Alignment Decision — Epic #903: Dreaming (EP across the whole/expanding graph)"
type: decisions
domain: strategy
doc_status: live
subjects.team: epistemic-team
created: 2026-08-13
---

# Epic #903 — Dreaming: EP across the whole/expanding graph — Align Decision

**Date:** 2026-08-13
**Pipeline:** epic-workflow Stage 1 (Align) — `epic-align`
**Decision:** **PROCEED**

---

## Grounding facts (from codebase)

- `tortoise/dream.py` already implements two tiers (#85):
  - `Dreamer.dream(anchors, max_hops=2)` — incremental BFS EP over the affected subgraph (reuses `_bfs_select_operators` capped at 200 ops + `TortoiseEP.run` with batch I/O). This is the "connect/EP-subgraph" workflow, effectively #395.
  - `Dreamer.dream_all(max_hops=2, batch_size=2000, max_total_operators=200_000)` — full-graph stabilization from all non-operator Points, batched, with a DoS cap. **Blunt instrument:** each batch re-runs BFS on its own anchor chunk (overlapping subgraphs recompute repeatedly, no operator dedup across batches); no freshness metadata; no window/expansion modes; no consolidation (dedup/merge) interplay.
- Hosted tier (`hosted_api.py`): per-team debounced dream queue (`_enqueue_dream` → `_dream_worker`), hourly budget on `/v1/dream?full=true` (#329). Embedded tier: in-band with ≤500ms latency budget, fallback to lazy-read + scheduled `dream_all` (#85).
- The `dreaming` workflow is a listed roadmap item in `product/2026-08-11-workflows-skills-usecases.md` (workflow tooling: index, mine, connect, ingest, EP-subgraph, dreaming).
- Two CRITICAL issue comments (2026-08-12) tie dreaming directly to the core product hypothesis: *the graph is the memory, not the summaries* — dreaming/EP is the computational means of keeping that memory alive; lifecycle events (supersede/deprecate) are inputs that change what propagates. State-centric model: confidence is derived (not stored) from points; dreaming refreshes state confidence.

**Gap vs. the epic's O/I/T (what #903 actually adds):**

| #903 wants | Existing | Gap |
|---|---|---|
| Full-graph EP pass selectable | `dream_all` exists | Mode is implicit, not a first-class selectable mode |
| Expanding-window mode | — | **Missing entirely** (only whole-graph or 2-hop incremental) |
| Incremental/bounded recompute (not always O(whole graph)) | batch cap exists | No freshness-aware scheduling; overlapping recompute; no operator dedup across batches |
| Freshness tracking (which regions recomputed when) | — | **Missing entirely** (no per-region `lastDreamedAt` / staleness query) |
| Consolidation interplay (dedup/merge) | `handover/merge-consolidation` doc exists (product/2026-08-11-handover-merge-consolidation.md) | Not wired into dream scheduling (merges create new dirty regions that dreaming must absorb) |

So this is a real extension of existing machinery — not greenfield, not redundant.

---

## Step 1 — Adversarial Strategy Test

**Alternatives considered:**

1. **Do nothing — rely on per-query fast-path EP (`compute_confidence(anchors=...)`).**
   Rejected. Fast-path only refreshes the last-touched subgraph on demand. Regions beyond the write-hops horizon (hosted tier does refresh ~2 hops of the write via the debounced dirty-root dream; the rest of the graph is only refreshed by rare `dream_all`) and unqueried regions rot. With the state-centric model, stale confidence on options = stale derived state = degraded historical-belief retrieval ("what was believed at t1" needs fresh-enough current beliefs).
2. **Crank up `dream_all` frequency / raise caps (same machinery, run more often).**
   Rejected. This is O(whole graph) every time — exactly the unbounded cost the epic forbids ("incremental expansion, not always full recompute"). Hosted per-tenant hourly budget (#329) would be exhausted; embedded 500ms budget blows immediately. It also doesn't deliver Indicator 2 (selectable modes) or Indicator 3 (freshness tracking).
3. **Streaming/event-driven EP (propagate on each write, no background passes).**
   Partially valid as a complementary mechanism. But loopy belief propagation is an iterative fixed-point computation — per-edge single updates don't converge to a global fixed point without a schedule. Dreaming (background passes over regions) IS the schedule. This alternative complements rather than replaces the epic.
4. **Lazy-only refresh (recompute confidence at read time for the queried region).**
   Rejected. That's the existing fast path. Dreaming exists specifically to take this cost OUT of the hot path and keep regions warm *before* they're queried.
5. **Deepen the fast path (raise `max_hops` 2→4+, per-query propagation over larger subgraphs) and skip background dreaming.**
   Rejected. Pushing cost into the query path makes the hot path slower and still never refreshes regions nobody queries — the rot is unchanged, it's just delayed. Dreaming's whole design point (#85) is the off-hot-path tier. Deepening the fast path complements (deeper fast path = smaller freshness debt), but does not replace the scheduler.

**Anti-post-rationalization — strongest reasons NOT to build this:**
- **Sparse-connectivity waste:** if the graph is mostly disconnected regions, most of the graph doesn't change when one region changes — full-graph dreaming recomputes unaffected regions. Counter: the epic's freshness tracking + expanding window is precisely the mechanism that limits recompute to regions that are *stale* (not merely *changed*).
- **Convergence risk:** loopy BP on a growing graph can oscillate; more regions recomputed = more chances for non-convergence. Existing `TortoiseEP.run` has convergence handling; expanding windows need convergence-aware stopping — a real engineering cost, not free.
- **Complexity budget:** freshness tracking + selectable modes + consolidation interplay is significant machinery for a non-urgent improvement. Counter: it is on the roadmap (workflows doc), is directly load-bearing for the core product hypothesis, and the O/I/T is measurable — this is not scope creep.
- **"Nice engine purity" trap:** is this solving a user-visible problem or just making the engine feel cleaner? Counter: staleness is THE documented failure mode of memory systems (n26modi head-to-head: staleness error 87%→20% with temporal-KG; Graphiti arXiv:2501.13956). Confidence freshness is user-visible via decisions/retrieval. Real, not cosmetic.

**Opportunity cost:** if we didn't build this, the alternative spend is the other listed roadmap workflows (mine, connect, ingest) and MCP/SDK consolidation (#888). Those matter too, but dreaming is the only one that keeps the whole graph's *beliefs* trustworthy over time — without it, value of everything else decays with staleness.

**Urgency tension (explicit):** two CRITICAL product-hypothesis comments on #903 argue this is load-bearing for the core value (memory freshness = trust), which could argue "Do now." But nothing is on fire: the hot path works, write-proximate regions are refreshed by the hosted dirty-root dream, and rot is slow (regions only degrade as the world moves, not as compute idles). Slow-moving rot + no active incident + full-depth planning value → resolves to **Schedule**, not Do Now. The "Important" half of the quadrant is what justifies the full pipeline (research + gates), not an expedited build.

---

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | — | **→ #903: Schedule** |
| **Not Important** | — | — |

**Placement: Important / Not Urgent → Schedule.**
- Important: core product hypothesis (graph as memory; confidence engine maintains the record); explicit roadmap item; without it, graph-wide beliefs rot.
- Not urgent: no active incident; fast-path EP keeps the hot path working; this is a quality/consistency improvement to schedule deliberately.
- Not a "Do now" because nothing is on fire and full depth (this epic = full pipeline with review gates) benefits from being scheduled, not rushed.

---

## Step 3 — Profit Growth Alignment

**Causal chain:** freshness-aware dreaming → graph-wide confidence stays current → historical-belief retrieval & decisions are trustworthy → users rely on the memory as the record (retention driver) → expansion of usage across teams → revenue.

**Rough impact:** infrastructure lever, order-of-magnitude $100s–$1000s/month via retention/trust rather than direct billing. The alternative (stale beliefs) is the documented #1 memory-system failure mode — its *cost* is the avoided downside. Testable: Indicator 3 (confidence recomputed/fresh after a pass) is measurable; note the eval spec (`docs/epistemic-layer-eval-spec.md`) has a graph-wide grounding gate (G7, |Δconf| ≤ 0.01 pre/post) but **no staleness-error evaluation** — defining one (e.g. point-in-time retrieval error against ground truth) is an explicit Research deliverable feeding Indicator 3.

**Faster path to same profit?** No — the faster path (fast-path-only) is what this epic exists to fix. Streaming EP is complementary, not a substitute (fixed-point schedule requirement).

---

## Step 4 — Decision Rationale

## Strategy Alignment Decision

**Feature:** Epic #903 — Dreaming: run EP across the whole/expanding graph to keep it fresh
**Decision:** PROCEED

**Alternatives considered:**
1. Do nothing (fast-path only) — rejected: rest of graph rots; the epic's entire premise.
2. Run `dream_all` more often — rejected: O(whole graph) cost, fails Indicators 2–3, blows #329 budget.
3. Streaming/event-driven EP — complementary, not a substitute (BP needs a fixed-point schedule).
4. Lazy-only refresh — rejected: that IS the fast path; dreaming is the off-hot-path tier.

**Profit impact:** freshness → trustworthy retrieval/decisions → retention → $100s–$1000s/month (infra lever; measurable via Indicator 3 + epistemic-layer eval spec).

**Eisenhower placement:** Important / Not Urgent → **Schedule** (roadmap item; no active incident; benefits from full-depth pipeline).

**Key assumptions:**
- Graph regions are meaningfully separable for windowed recompute (influence boundary can be approximated) — confidence: **medium** (depends on graph density; sparse = easy, dense = window ≈ whole graph).
- EP convergence handling scales to windowed recompute without oscillation — confidence: **medium** (existing run() converges on subgraphs today).
- Freshness metadata (region → lastDreamedAt) is queryable without schema-breaking changes — confidence: **high** (node property pattern already used: `confidence`, `updatedAt`).
- Staleness is the dominant failure mode worth paying for — confidence: **medium** (see Step 3; direction consistent across Graphiti + n26modi, magnitude unestablished).
- Production graph size/density makes windowed incremental recompute meaningfully cheaper than full passes — confidence: **low/unknown** (if graphs are small, the machinery is over-engineering; if dense, window ≈ whole graph). Collect real graph stats (node/edge counts, operator fan-out, region connectivity) in Research before Scope commits.
- Merged/deduped regions produce detectable dirty regions that freshness tracking can schedule — confidence: **medium** (merges change topology + region boundaries, which can invalidate freshness metadata and window boundaries; de-risk the interaction in Research).

**Recommendation:** PROCEED into Research. Deliver the epic in the order its own O/I/T implies: (1) selectable modes (full-graph vs expanding-window), (2) freshness tracking as the scheduler input, (3) bounded/incremental recompute with operator dedup, (4) consolidation interplay. Research should specifically de-risk the window/region-boundary question (assumption confidence: medium) before Scope commits to an architecture.

---

## Step 5 — Routing

**PROCEED** → hand off to `epic-research` (Stage 2).

<!-- review-gate-status: CLEAN (6 P2 issues fixed) -->
