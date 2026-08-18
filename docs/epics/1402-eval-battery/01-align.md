---
title: "Strategy Alignment Decision — Epic #1402: Agent-Reasoning Eval Battery"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
---

# Strategy Alignment Decision — Epic #1402: Agent-Reasoning Eval Battery

**Feature:** Prove or falsify that the Tortoise epistemic graph improves an agent's *reasoning* — not just recall — via a three-tier eval battery (single-session reasoning probes, longitudinal learning streams, differential sweep vs alternative memory systems).
**Decision: PROCEED (conditional) — scheduled, not urgent; the verdict is a differentiation PROFILE (all metrics scored on all arms), and the "unique" claim ships when ≥1 empirically-won differentiator exists on a load-bearing axis AND no load-bearing weakness is serious (no mitigation path). Reclassification trigger: if launch messaging includes the reasoning-uniqueness positioning, this epic moves to Do-now (see Eisenhower).**

## Adversarial Test (Step 1)

**Alternatives considered:**

1. **Do nothing — rely on the existing eval lineage (#1144 retrieval quality, #1369 LongMemEval ingestion, #1366/#1367 weak-category fixes).** Rejected as sufficient: those measure *recall/retrieval parity*, exactly the axis where the literature says memory systems already converge (LongMemEval saturation). They cannot detect the *reasoning* delta — and cannot falsify the "just another memory system" null. They remain a required input (Tier-3 parity leg), not a substitute.
2. **Ad-hoc experiments instead of a formal epic (run probes opportunistically as research side-quests).** Rejected: the battery's validity depends on matched arms, pre-registered rubrics, calibration discipline, and trajectory metrics. Ad-hoc runs are unregistered → results can't support the product claim, and the pseudo-evolution gate requires a designed longitudinal stream, not opportunistic data.
3. **Skip validation — ship the "graph is a reasoning medium" claim on dogfood + 2604.20006's negative result for others.** Rejected: the product thesis (graph-as-memory-hypothesis, #909) IS the claim that we're different. Shipping an unvalidated uniqueness claim risks reputation, invites competitor counter-evidence, and forfeits the strongest sales asset (measured delta). The null is live and documented — ignoring it is the anti-post-rationalization failure this gate exists to prevent.
4. **Build only off-the-shelf reasoning benchmarks (τ-bench, GAIA, OSWorld) and assume the delta shows up.** Rejected: they are capability-level single-session evals with no memory axis; they measure the *model's* reasoning, not the *graph's* contribution. They are scenario material, not the battery.
5. **Build the battery as a public/commercial benchmark product.** Rejected for now — out of scope, high maintenance cost, no revenue path identified. The battery is internal validation tooling first; a public benchmark is a later packaging decision.

**Anti-post-rationalization — strongest reasons NOT to build:**
- **The claim may fail.** 2604.20006 found memory helps remembering most, reasoning least. Our own probes might show the graph does not move reasoning outcomes — falsifying the product thesis. That is precisely why we must build this before marketing the claim; a falsification is cheaper now than in the market.
- **Eval infra is expensive, ships no user-facing feature.** Harness + 5 arms + 3 benchmark integrations is real engineering that doesn't add a product surface. Opportunity cost against extraction quality (#1350), onboarding, and the launch roadmap.
- **Self-fulfilling test risk.** Probes built on our own primitives (NAND surfacing, EP calibration) could measure the engine's own API rather than agent-level reasoning outcomes. Mitigation: probes are agent-level behavioral scenarios with blind rubrics, scored by external judges — and the differential arms (non-Tortoise systems) run the SAME probes, so the design can't be Tortoise-shaped without being caught by the matched-recall control.
- **Launch dependency risk.** If treated as a launch blocker, the battery delays the roadmap. Mitigation: it is scheduled, not urgent — it gates the *claim*, not the *product*.

**Opportunity cost:** if we didn't build this, the next-best uses of the capacity are extraction-quality work (#1350) and the launch roadmap. Both are already in flight; the battery is the only item that de-risks the *thesis itself*, which every other epic depends on.

## Eisenhower Matrix (Step 2)

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | Do now — extraction v2 (#1350), launch blockers | **Schedule — THIS EPIC: the product-claim validator** |
| **Not Important** | Delegate — CI noise, cosmetic fixes | Eliminate — vanity metrics (point counts, retrieval stats) |

**Placement: Important / Not Urgent (Schedule).** Justification: the battery de-risks the core commercial claim and must complete *before* the "memory that reasons" positioning goes public — but it does not block shipping the product, and forcing it into the launch critical path would be convenience-classification in the other direction. Best action for profit growth *right now* is still extraction+launch; the battery is the scheduled gate that makes the launch message true.

## Profit Growth Alignment (Step 3)

**Causal chain (testable):**
1. Battery clears Tier-1 (capability) + Tier-2 (improvement) + Tier-3 (uniqueness at matched recall) → measured, published delta: "Tortoise improves agent reasoning outcomes; generic memory systems do not."
2. Measured delta → differentiated positioning vs generic memory products (Mem0/Zep-class) → the moat claim becomes evidence-backed rather than asserted.
3. Evidence-backed claim → conversion: eval-informed buyers (the agent-memory market buys on benchmarks) choose the product; retention: the longitudinal result ("agent gets better with live use") is the compounding-memory product promise.
4. **Falsification outcome is also a profit action:** if Tier-3 fails, we learn the moat isn't the graph's reasoning contribution — redirect before spending on a false claim.

**Quantify:** indirect, order-of-magnitude $1000s–$10,000s/month in the long run via conversion/retention on the differentiated claim; the *direct* value is risk reduction (avoid shipping an unvalidated uniqueness claim at an estimated reputation/conversion cost well above the battery's build cost). The faster path to the same profit outcome would be publishing the existing #1144/#1369 recall results — which we do, as the parity leg, alongside.

## Review-gate fixes applied (2026-08-14, fresh-context reviewer)

1. **P1 — Tier-3 gate replaced by owner override: differentiation PROFILE, not exclusion (2026-08-14).** The reviewer's de-tilt (exclude structural legs R1/R4 from the verdict) is REJECTED by the product owner: the battery must tell us where we're better and where we're not on EVERY metric — exclusions hide information. New verdict rule: (a) ALL probes (R1–R5; L1–L6; D2–D4) are scored on ALL arms and reported in a full delta profile; (b) each metric is classified STRONG (empirically won, delta ≥ [cal] threshold) / STRUCTURAL (won but by-construction — the graph's primitives firing, honest label, competitors could replicate) / PARITY / WEAK (comparator wins, delta ≥ [cal] threshold), with a load-bearing flag (is the axis customer-visible: contradiction, staleness, calibration, consistency, improvement-over-time); (c) the "unique" claim ships when ≥1 STRONG exists on a load-bearing axis (a TRUE DIFFERENTIATOR — empirically won, not structural) AND no load-bearing WEAK lacks a documented mitigation path (no SERIOUS WEAKNESS); (d) if only STRUCTURAL wins exist, the claim is "mechanism, not unique" — positioning as memory+reasoning aid, re-scope to strengthen. (e) WEAKs each carry a mitigation path and a re-run loop: the battery is re-runnable after mitigations so "improve enough that weaknesses are not serious" is testable. (Battery spec D1/AC-D1/Verdict rule + scope E2E-3 updated to match.)
2. **P2 — Matched-recall defined ex ante.** Matched recall = equal top-K factual retrieval F1 (K=5) on the scenario corpus, measured on a factual probe subset, before the reasoning battery runs. **Symmetric trigger:** if ANY arm falls ≥0.10 F1 short of the corpus-best factual retrieval (A4 included — graph retrieval losing to RAG on factual F1 is a live possibility), the comparison is rerun on a recall-matched balanced subset; if that subset is <50% of the corpus, the differential verdict is **INCONCLUSIVE** — pre-committed consequence: the uniqueness claim does not ship on an INCONCLUSIVE result; the epic re-scopes to a recall-capable comparator or reports "not demonstrated" and defers the positioning question. No post-hoc reinterpretation later.
3. **P2 — Falsification branch pre-committed (profile-based).** Verdict outcomes: (a) UNIQUE — ≥1 TRUE DIFFERENTIATOR (STRONG on a load-bearing axis) and no SERIOUS WEAKNESS → the "the graph improves reasoning; other memory systems don't" claim ships with the full profile as evidence; (b) MECHANISM-NOT-UNIQUE — only STRUCTURAL wins, no STRONG → uniqueness claim dropped from positioning until a new mechanism is built and re-validated; retention positioning (Tier-1/2 capability + improvement) survives independently; (c) WEAK-UNMITIGATED — a load-bearing WEAK lacks a mitigation path → claim gated until the weakness is fixed and the battery re-run shows it below the serious threshold (the improvement loop); (d) INCONCLUSIVE — matched-recall regime failed → claim does not ship; epic re-scopes the comparator. Artifacts that change on non-UNIQUE outcomes: positioning copy, product-success-eval claim section, epic #909 thesis annex — recorded in the verdict report. The full profile is reported in ALL outcomes — the diagnostic value is the profile itself, not the verdict.
4. **P2 — Eisenhower reclassification trigger.** Initial launch messaging MUST exclude the reasoning-uniqueness positioning (verify at plan stage; owner commits). If launch messaging includes it before the battery verdict, this epic reclassifies to Important/Urgent (Do-now) automatically — the gate moves into the critical path.
5. **P2 — Profit quantification grounded.** "The agent-memory market buys on benchmarks" is marked an assumption to test in epic-research (no competitor benchmark-led sales cases collected yet). Cost side added: order-of-magnitude budget = 2–4 engineer-weeks for the harness + arms, compute ≈ 20 runs × 5 arms × longitudinal streams ≈ 500–1,000 agent-eval episodes (within existing 1144 eval budget, no new infrastructure spend). Revenue range sanity: $1000s–$10,000s/month implies tens-to-hundreds of paid seats attributable to the claim on a pre-revenue product — flagged as speculative until pricing + segment data exist; treated as directional, not a target.
6. **P2 — Missing assumptions added.** (a) External validity: near-term customer workloads (#1350/#909: extraction, mining, retrieval) may not exercise the measured reasoning pathway — scenario-to-workload mapping is a research-stage task; conversion mechanism is empty if customers never hit the reasoning axis. (b) SEA-Eval single-source caveat carried forward: AC-L2's token-trajectory gate rests on one paper (2604.08988) — flagged ⚠️ single-source in the landscape doc; corroboration sought in research; gate stays, labeled provisional.

## Decision Rationale (Step 4)

**Feature:** Agent-Reasoning Eval Battery (#1402)
**Decision:** PROCEED — scheduled (Important/Not-Urgent), claim-gated not launch-gated

**Alternatives considered:** (1) rely on existing eval lineage — insufficient, recall-only; (2) ad-hoc experiments — invalid methodology; (3) skip validation — unacceptable claim risk; (4) off-the-shelf reasoning benchmarks — capability-level, no memory axis; (5) public benchmark product — out of scope.

**Profit impact:** evidence-backed differentiation → conversion/retention on the compounding-memory promise; $1000s–$10,000s/month indirect; direct value = falsification risk reduction on the core thesis.

**Eisenhower placement:** Important / Not Urgent — Schedule. Gates the claim, not the launch.

**Key assumptions:**
- The graph's reasoning contribution is measurable at agent level by our probes — confidence: **medium** (2604.20006's task-dependence finding says reasoning gains are the hardest axis; probes are designed to catch a null result, not guarantee one)
- Matched-recall is achievable at the reasoning-relevant level and operationally defined (top-K factual F1, K=5, partial-match regime pre-committed) — confidence: **medium** (recall backends are off-the-shelf and #1144 infra exists; the *meaningful* match — decision-relevant claims surfaced, not just facts — is the open question and is defined ex ante rather than assumed)
- The battery will complete before the claim goes public — confidence: **medium** (marketing timing is outside this epic's control; reclassification trigger in place: reasoning positioning in launch messaging ⇒ Do-now)
- Blind LLM-judge rubrics can score reasoning outcomes reliably — confidence: **medium** (SEAL meta-judge and AdaRubric results support judge-based protocols; pre-registration + calibration mitigates drift)
- Near-term customer workloads exercise the measured reasoning pathway — confidence: **low** (external-validity risk; scenario-to-workload mapping is a research-stage task, and a mismatch is a finding, not a failure)
- AC-L2's token-trajectory gate (SEA-Eval) holds beyond its single source — confidence: **low ⚠️ single-source** (corroboration sought in research; gate labeled provisional)

**Recommendation:** PROCEED as a scheduled epic — build the battery to produce a full differentiation profile (all metrics, all arms) and a falsification-accepting verdict (≥1 true differentiator, no serious weakness). Launch messaging excludes the uniqueness claim until the verdict (reclassification trigger otherwise). The battery is re-runnable so identified weaknesses can be improved and re-measured.

## Routing (Step 5)

PROCEED → hand off to `epic-research` (Stage 2). Gate: previous-stage review (below) must clear before research begins.
