# Strategy Alignment Decision — Epic #7740: Mine Agent Conversations for Insights

**Feature:** LLM-driven extraction of decisions/entities/actions from indexed sessions into the epistemic graph
**Decision: CONDITIONAL PROCEED — gated on #7708 reaching STABLE state, Phase 1 only**

## Adversarial Test (Step 1)

**Alternatives considered:**
1. **Do nothing — rely on manual capture** — Rejected: doesn't scale to ~947 sessions; hosted product needs autonomous accumulation.
2. **Search-only (improve session search, no extraction)** — Partially valid and a REAL sequencing alternative: with ~0 paying users, the honest question is whether ANY graph enrichment is the right move before user acquisition. **"Search + acquire users first, then extract"** is a genuine alternative this decision must weigh. It is rejected ONLY for sequencing: search gaps (#7770/#7774) are the urgent prerequisite and must ship first, but extraction is the compounding differentiator that makes the product feel alive. The two are not substitutes — extraction is deferred, not eliminated, behind a #7708 stability gate.
3. **Hindsight-style flat consolidation, no cross-ontology wiring** — Rejected: flat summaries don't feed EP confidence or cross-lens discovery — the two differentiators that justify the graph.
4. **Rule-based extraction (regex/keyword) instead of LLM** — Cheap but low precision for decisions/hypotheses; LLM extraction is the differentiator. Rules can supplement entity extraction later.

**Anti-post-rationalization (strongest reasons NOT to build):**
- **Prereq #7708 is OPEN with unresolved search gaps (#7770/#7774) and the local container is currently down** — the foundation is demonstrably NOT stable. Extraction built on a broken foundation produces Points nobody can find. This is a hard gate, not a sequencing caveat.
- LLM extraction cost: ~947 sessions × API calls is non-trivial; batch throttling required.
- Extraction quality varies by model; low-confidence Points **pollute the graph and degrade EP belief propagation** — the graph's core differentiator. Bad Points wired through mitigation edges can nuke EP weights (the exact failure mode AGENTS.md hard-rule warns about).
- Entity dedup (Phase 2) is genuinely hard; cross-session resolution errors create duplicate Objects.
- With ~0 paying users, the opportunity cost of extraction vs. user acquisition is a real question.

**Opportunity cost:** If not built now, the alternatives are: finish #7708's search gaps (immediate user-facing pain), or invest in user acquisition. Both are valid higher-leverage uses of the next sprint.

## Eisenhower Matrix (Step 2)

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | — (with ~0 users, nothing is operationally urgent) | **Search gaps (#7770/#7774)** then **#7740 extraction** |
| **Not Important** | — | Perfect entity resolution (v2) |

**Placement: Important / Not-Urgent → SCHEDULE, with clear priority ordering.** With ~0 paying users, neither search nor extraction is operationally "urgent" — both belong in Important/Not-Urgent. Within that quadrant, **search ships first** (it unblocks the #7708 stability gate and gives users a reason to return), **extraction second** (compounding differentiator). Honest framing: the real strategic question is user acquisition vs. product depth — this decision assumes product depth is the path (the hosted product's differentiation bet), which should be revisited if acquisition stalls.

## Profit Growth Alignment (Step 3)

**Causal chain (testable):** Sessions mined → Points/Objects/Actions accumulate → graph becomes the "team brain" → users return to query it → retention → conversion.

**Falsification criteria (leading indicators):**
- Within 60 days of extraction shipping: ≥10% of mined sessions generate ≥1 graph query (search/traverse) per week
- Extraction precision ≥70% (human review of a 50-session sample): low-precision extractions are filtered, not merged
- EP health preserved: no regression in mean grounding across existing Points after extraction batch (guard against graph pollution)

**Revenue estimate methodology:** Labeled as **placeholder hypothesis**, not measurement. $10s–$100s/month is an upper bound guess based on: (a) competitor pricing (Mem0/Honcho charge per-seat for memory features), (b) retention logic (a graph that compounds is stickier), NOT on willingness-to-pay surveys (none exist at ~0 users). It is NOT a target; it is a sanity ceiling. The falsifiable indicators above are the real measure — if extraction doesn't drive graph queries within 60 days, the feature fails regardless of the revenue estimate.

**Faster path?** Yes — Phase 1 (Point extraction) alone delivers the O/I/T indicator #1 demo ("decisions become Points"). Phases 2-4 follow only after calibration proves precision.

## Key Assumptions (confidence downgraded to match evidence)

- Sessions are indexed and searchable (prereq #7708) — confidence: **LOW** (epic OPEN, search gaps unresolved, local container down; must reach STABLE state before extraction)
- LLM extraction quality sufficient for Point creation without polluting the graph — confidence: **LOW** (no calibration run exists yet; precision ≥70% is a hypothesis)
- **Graph pollution from low-quality extractions won't degrade EP belief propagation — confidence: LOW, and this is the nuclear risk.** Mitigation: extraction Points start as `status: draft` (not live) until reviewed; no extraction-created Point auto-wires mitigation edges; calibration gate before full batch.
- Extraction cost is acceptable (batch, throttled) — confidence: **high** (batch is a stated v1 non-goal; throttle is engineering)
- Users value mined insights over raw search — confidence: **LOW** (zero evidence at ~0 users; the falsification criteria above will test this)

## Recommendation
**CONDITIONAL PROCEED** with two hard gates:
1. **Gate A (prereq):** #7708 reaches STABLE — search gaps (#7770/#7774) merged, sessions demonstrably searchable. No extraction work before this.
2. **Gate B (calibration):** Phase 1 extraction runs on 20–50 sessions; human review confirms ≥70% precision; EP health check shows no grounding regression. Full-batch execution only after Gate B passes.

Phases 2–4 (entities/actions/cross-ontology) are deferred behind both gates. This is the honest decision: the epic is worth building (compounding differentiator), but its prerequisites are not yet met, and its core hypothesis (users value mined insights; extraction won't pollute the graph) is unproven at ~0 users.

## Routing
CONDITIONAL PROCEED → do NOT advance to epic-research yet. The epic's plan doc should be prepared, but implementation waits on Gate A (#7708 STABLE). Epic-align gate: reviewer issues addressed (confidence downgrades, EP-pollution assumption added, falsifiable profit chain, honest matrix).
