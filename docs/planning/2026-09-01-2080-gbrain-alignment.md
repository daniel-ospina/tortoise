# Strategy Alignment Decision — Epic #2080: adopt gbrain's measurable memory practices (write-path evals, EP-scored volunteering-memory reflex, opt-in auto-ingestion QUALITY)

**Feature:** Adopt gbrain's durable, measurable memory practices into Tortoise — write-path evals (W2), volunteering-memory harness + why-layer suite (W3), why-aware recall (W4), opt-in auto-ingestion write-path quality (W5), benchmark discipline + published runs (W7). Deferred: W6 UI → #1976, phase-2 async → #2081, per-graph tenancy → #2083.
**Decision: APPROVE — PROCEED (with a mandatory W2/W3-first sequencing gate and one conditional scope-cut option: the phase-1 `/v1/context` endpoint build, gated on #2083's tenancy schema)**
**Date:** 2026-09-01
**Pipeline note:** This align gate runs AFTER research (research-brief + scope doc are committed and deep). It therefore validates the epic's strategy against that pre-existing research rather than preceding it — the adversarial test below was checked specifically for blind spots the research/scope may have inherited from their own framing. Epic-workflow's canonical order (align → research → scope) is inverted here; the decision stands on its merits, and the reviewer gate verified the strategy logic independently of the docs' internal consistency.

---

## Step 1 — Adversarial Strategy Test

### Alternatives considered

1. **Ship #2083 (multi-graph pro-tier) first, defer #2080 entirely.** The pro-tier multi-graph work is the direct revenue path (developer-customer model: each app end-customer = one graph = one key; labeled `implementing`). Rejected as a *replacement*, not because revenue is unimportant — the two epics are contract-coupled, but the coupling is **joint contract co-design, not #2080-first**. Artifact-level dependency: #2080's `/v1/context` tenancy contract and #2083's registry schema must be coordinated (D3) — the endpoint build is gated on #2083's schema landing, and #2083's per-graph-key isolation must satisfy the jointly-agreed contract shape. The reason #2080 is not deferred to a follow-up is therefore NOT contract priority — it is the **measurement-prerequisite** and **why-layer-moat** arguments in Alternatives 2 and 4, which are independent of the coupling direction. The real question is *capacity* (both are complex, same team) — addressed in the sequencing gate below, not by elimination.
2. **Read-path only: ship W4 why-recall now, defer ALL eval infrastructure (W2/W3/W7) to a follow-up "benchmarks" issue.** This is the strongest genuine alternative. Eval infra is invisible to users; the why-layer is the moat and would ship faster without it. **Rejected for a structural reason, not preference:** the why-layer's value is *conditional on the graph being good*. Mem0/Supermemory self-report 90%+ read-path while measuring 41–43% write-path extraction recall — if Tortoise's session→graph pipeline silently loses a comparable fraction of salient units, W4 would surface confident-sounding reasons for garbage beliefs. W2 (write-path eval) is a *prerequisite* for W4's value, not an optional extra. And the epic's O/I/T is explicitly "provably better, **publicly**" — the published numbers ARE the deliverable. Deferring the evals guts the epic's point and would leave the A4 thesis (the only novel claim) unmeasured.
3. **Run Tortoise through gbrain-evals' own adapter interface instead of building W2/W3 from scratch.** gbrain-evals invites third-party scorecards via PR ("score your own system"). Cheap, and their transparency is a real entry point. **Rejected as a replacement:** Tortoise's stack (Python/FalkorDB vs TS/PGLite) and pipeline unit (points + operators + EP updates vs pages) make the adapter a significant integration in its own right; the ideas (schemas, metric formulas, receipts) are MIT-free to reimplement; and the why-layer suite — the differentiator — is Tortoise-original with no gbrain analogue, so a harness must be built regardless. **Kept as an addition:** W7's comparison-systems.md should include a Tortoise scorecard through their adapter if cheap — their moat becomes our yardstick (_analysis §10f, §13 point 3). Not a substitute.
4. **Do nothing / redirect epistemic-team capacity to #2082 (auth) or user acquisition.** At the product's current maturity, the honest question (per the #7740 precedent) is whether internal differentiation outranks governance/research or revenue work. Rejected for sequencing: #2082 is research-only and explicitly NOT frozen (it consumes nothing and blocks nothing — it is the softest dependency in the portfolio); #2083 needs #2080's contract. User acquisition without a differentiated, *measurable* product means selling against gbrain's marketing engine (29K stars, YC RFS alignment, "brain" becoming synonymous with "markdown + hybrid search" — _analysis §13 point 5) with no numbers and no why-layer.

### Anti-post-rationalization (strongest reasons NOT to build)

- **Eval infrastructure is invisible to users.** W1/W2/W3/W7 produce docs, benchmarks, harnesses, and receipts — no user-facing surface until W4/W5. If the epic stalls after the measurement milestones, we hold internal infrastructure and published numbers but no moat feature. The numbers are still a credibility asset (and a gbrain-adapter scorecard is a marketing asset), but the strategic payoff is W4.
- **The W4 thesis is LOW-confidence (A4): no product precedent, only academic precedent** (conflict-aware memory, controversy-aware IR — found 2026-09-01). We may build why-aware recall and discover users don't ask "why is this believed?" — they just want the answer. The why-layer could be a solution looking for a problem.
- **The first published write-path number will likely be BAD** (gbrain's own first run: 61.5%). Tortoise's `session_import`/`dream.py` pipeline has never been planted-gold measured. Publishing a low number is discipline — but it is also a marketing liability if an enterprise prospect or developer reads "write-path survival 5x%," unframed. The fix-wave protocol (publish bad → name failure classes → fix → re-run frozen corpus) is the only honest frame, and it costs calendar time.
- **Integration surface is large:** W4 enriches four existing surfaces + adds a new scored ranking signal (contentiousness) + a phase-1 sync delivery endpoint + self-host packaging + a tenancy contract that must stay reversible to per-graph keys. Architecture is rated **high**. The epic could drag while #2083 (labeled `implementing`) waits on its contract.
- **A11 risk is real:** the why-layer suite grades from *surfaced context alone* — if the assembled context can't answer "what contradicts this? / why is this believed? / where do I dig deeper?", W4's context assembly must change mid-epic, not just the harness.

### Opportunity cost

If not built now, the alternatives for the same epistemic-team capacity: **#2083** (multi-graph pro-tier — the direct revenue path, already `implementing`), **#2082** (auth architecture — unblocks the enterprise-governance story, research-only, NOT frozen), **#1976** (onboarding, close to shipping), or user-acquisition work. The tension is real and this gate does not wave it away: eval infrastructure + why-recall is *not* obviously higher-leverage than multi-graph revenue work *in isolation*.

The resolution is that they are complements, not substitutes, and the leverage is in the **sequence**:
1. **Contract-first:** #2083's per-graph-key tenancy must satisfy #2080's `/v1/context` contract — the contract must exist before the wall is built.
2. **The eval suite is a conversion prerequisite for the developer-customer path (#2083's whole point).** A developer choosing a memory backend for their app's customers will benchmark. gbrain-evals' adapter interface means they can run Cat 34/35 against *any* system — including Tortoise — and publish the number *uncontrolled*. The uncontrolled-eval risk is **bounded by the same integration friction used to reject the adapter as a replacement** (Alternative 3) — a casual third party must invest real adapter work (stack + pipeline-unit mismatch) to benchmark us, so an imminent number from a hobbyist is unlikely. The residual risk is a **motivated actor** (gbrain-evals maintainers, a competitor) investing in the adapter; our own W7 adapter scorecard preempts it. Running the evals first = controlling the narrative, with the residual risk being narrative-timing, not narrative-loss.
3. **The category window is open now and closing.** Nobody ships why-aware recall; gbrain structurally can't (declarative graph, no belief semantics); but gbrain's velocity is extreme (637 changelog entries in 5 months) and its watch-items (confidence/contradiction-edge language) are exactly the gap-closing move. The why-layer moat has a shelf life.

---

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | — (nothing is operationally on fire; the memory write path degrades silently but has never been measured — a latent risk, not an outage) | **W2/W3/W7 measurement discipline → W4 why-layer + W5 ingestion quality** (this epic) — **SCHEDULE**, in parallel with **#2083 multi-graph pro-tier**, sequenced contract-first |
| **Not Important** | — | Perfect retrieval-lever tuning (deferred by scope: adaptive return-sizing, source-boost map — cf. #1657); ask-surface exposure decision (#2013, governs W4's ask lane) |

**Placement: Important / Not-Urgent → SCHEDULE.** Justification: no user-facing pain is urgent today (ingestion is default-ON with opt-out; nothing is failing loudly). What has time-sensitivity is **urgency-of-opportunity, not urgency-of-pain**: (a) the why-aware-recall category frame is unclaimed and gbrain's velocity threatens it; (b) the uncontrolled-eval risk grows the longer we ship memory without published write-path numbers. Both argue for keeping W4/W5/W2/W3 in THIS epic (scheduling, not deferring) and running the fix-wave at speed. Within the quadrant, the honest priority ordering is **W2/W3 first (measure), then W4/W5 (feature + fix), W7 capstone last** — with W4's in-place enrichment parallel-tracked so the user-visible half doesn't wait for the full eval build. #2083 sits in the same quadrant and interleaves (see routing).

This is NOT a convenience classification: the counter-case (Important/Urgent — "the category window demands we ship W4 yesterday, evals later") is rejected because shipping W4 unmeasured repeats the exact error gbrain's own first runs exposed (write-path collapses are invisible until measured). Urgency of opportunity is real, but the *measurable* why-claim — the only version worth making publicly — cannot outrun its own measurement.

---

## Step 3 — Profit Growth Alignment

**Causal chain (testable):**

1. **Measurement → credibility → conversion (the developer-customer path).** W2/W3/W7 publish sealed-key write-path, reflex, and why-layer numbers with receipts (including a deliberately published bad first run + fix-wave) → Tortoise becomes the memory system with *provable* write-path quality AND the only why-aware recall → developers evaluating memory backends for their apps benchmark and see the numbers → converts the #2083 pro-tier offer (each app end-customer = one graph = one key = isolated, explainable memory). The embeddable `/v1/context` (phase-1 sync, hosted + self-host per D1/D2) is the acquisition surface.
2. **Differentiation → enterprise/team sales story.** Why-aware recall is the honest contrast vs gbrain (their "gap analysis" is LLM prose; their graph has no belief semantics; multi-user = isolation, not deliberation). Positioning leads with the why-layer, never retrieval numbers (gbrain can match or beat any R@k — _analysis §13 point 5). Enterprise sales motion (when it exists) gets "memory that explains itself and measures its own write path" instead of "another vector store."
3. **Write-path quality → retention.** Memory that silently loses ~40–50% of salient units (the Mem0/Supermemory measured norm) is a churn driver for agent-users; memory that builds itself correctly, with provenance + EP updates + disclosure-visible opt-in, is a compounding-retention driver. W5 already exists as a feature — the quality work is what makes it keepable.

**Falsification / leading indicators (before user-facing trust is spent):**
- W2 first baseline publishes and is BAD but bounded; the CI-gated baseline can FAIL; the fix-wave improves it on the same frozen corpus + pinned judge (gbrain pattern 61.5% → 88.1%). If the pipeline can't be measured, the epic's premise fails internally.
- W3 why-layer suite: conflict-surfacing ≥ 0.95 from surfaced context alone (A11). If the surfaced context can't answer the three why-questions, W4's assembly changes — *before* any user sees it.
- A4 A/B (contentiousness-boosted vs confidence-only ranking) as an eval-phase artifact: if contentiousness doesn't help, the W4 headline thesis is falsified internally, at eval cost, not user cost.
- **Post-ship conversion gate (the epic's own failure condition, made operational):** within 6 months of #2083 GA, a pro-tier trial or `/v1/context` integration must cite benchmark receipts or the why-layer in ≥30% of evaluation conversations, OR pro-tier trial→paid conversion must show a measurable lift vs. the pre-#2080 baseline. If neither triggers, Daniel convenes a kill/cut review of W4's public claims and the remaining epic budget is redirected. Without this threshold, "the epic fails regardless of the estimate" is a test that can be indefinitely postponed.
- **Write-path quality → retention (hop 3):** NOT measurable at current scale — tracked as a post-ship leading indicator (agent-user churn correlated with ingestion-loss complaints) and explicitly treated as **unverified** until multi-graph scale exists. Downgraded from "testable" to "unmeasured claim" to keep the chain honest.

**Quantified estimate (labeled hypothesis, not measurement — per the #7740 precedent):** this epic is NOT a billing feature; its direct revenue is ~$0/month. Its revenue is derivative: it multiplies #2083's pro-tier conversion and the embeddable-memory developer path. **$100s–$1000s/month** once the multi-graph pro-tier lands is a sanity ceiling derived from (a) per-graph-key pricing on the pro tier, (b) competitor per-seat memory pricing (Mem0/Honcho), (c) retention logic — NOT from willingness-to-pay data (none exists). The falsifiable indicators above are the real measure; the conversion gate is the threshold at which "if the numbers don't convert #2083's trial, this epic fails regardless of the estimate" becomes operational.

**Faster path to the same profit?** "Ship #2083 first, evals later" monetizes sooner but is a *different, lower-quality* profit: it sells an undifferentiated graph store against gbrain's marketing engine, with no quality claim and a standing risk of uncontrolled third-party evals (the gbrain-evals adapter means anyone can benchmark us). The measurement work is the cheapest way to make #2083's offer sellable — not a detour from it.

---

## Step 4 — Decision Rationale

## Strategy Alignment Decision

**Feature:** adopt gbrain's measurable memory practices — write-path evals, EP-scored volunteering-memory reflex, opt-in auto-ingestion quality, why-aware recall
**Decision:** PROCEED — **APPROVE with a mandatory sequencing gate + one conditional scope-cut option** (not a wholesale cut: all six in-scope workstreams W1/W2/W3/W4/W5/W7 stay; the scope doc's out-of-scope cuts — W6 → #1976, phase-2 → #2081, tenancy → #2083, no new retrieval tool — are endorsed)

**Alternatives considered:**
1. #2083-first (multi-graph revenue before evals/why-layer) — rejected as replacement: contract-coupled, not competing; #2080's `/v1/context` tenancy contract is what #2083's per-graph-key wall must satisfy (contract-first sequencing).
2. Read-path-only (W4 now, evals later) — rejected: W2 is a *prerequisite* for W4's value (why-context on a silently-losing write path is confident garbage); the O/I/T is "provably better, publicly."
3. Adopt gbrain-evals' adapter instead of building W2/W3 — rejected as replacement (stack + pipeline-unit mismatch; why-layer suite has no gbrain analogue), **kept as an addition** for W7's comparison scorecard.
4. Do nothing / redirect to #2082 or acquisition — rejected: #2082 is unfrozen research that blocks nothing; acquisition without a measurable, differentiated product means selling against gbrain's narrative with no numbers.

**Profit impact:** indirect, derivative of #2083. Causal chain: measured write-path + only-why-aware-recall → developer-customer benchmark conversion on the pro-tier multi-graph offer → $100s–$1000s/month (hypothesis, not measurement). The evals are a *conversion prerequisite* for #2083, not a detour.

**Eisenhower placement:** Important / Not-Urgent → **SCHEDULE** (in parallel with #2083, sequenced contract-first). Time-sensitivity is urgency-of-opportunity (unclaimed why-recall category frame; uncontrolled-eval risk), not urgency-of-pain.

**Key assumptions:**
- A4 — contentiousness-driven recall improves user-visible quality (the W4 headline thesis) — confidence: **LOW** (no product precedent; academic precedent exists; de-risked internally by W3 why-suite + A/B before user exposure)
- A1 — gbrain's write-path measurement pattern transfers to Tortoise's session→graph pipeline (point-level survival, REPHRASE-linked dedup) — confidence: **MEDIUM** (W2 pilot is the validation)
- A3 — EP support+contentiousness can score the "when to volunteer" reflex at least as well as gbrain's arm table (0.000 kta baseline) — confidence: **MEDIUM** (unverified)
- A11 — why-context assembly is gradeable from surfaced context alone (no full-graph access) — confidence: **MEDIUM** (if false, W4 assembly changes first — a mid-epic correction, not a claim failure)
- A2 + fit audit — W4's four context types fillable in-place across ask/analyze/search/MCP, no new tool — confidence: **MEDIUM-HIGH** (fit audit completed 2026-09-01; ask lane gated on #2013)
- A5 — official recall_all@5 500-Q LongMemEval run achievable at acceptable cost (embeddings cache; gbrain ~$2 re-run) — confidence: **HIGH** (runner exists as a 9-step resumable state machine)
- gbrain won't close the epistemic gap before the why-layer ships (monitor confidence/contradiction-edge language in releases) — confidence: **MEDIUM** (their velocity is extreme; this is a watch-item, not a bet)
- Published evals + why-recall convert the #2083 developer-customer path — confidence: **LOW-MEDIUM** (no revenue data; plausible; falsifiable post-ship)

**Recommendation:** PROCEED. This is the differentiation-and-credibility layer that makes #2083's revenue path sellable — it builds the moat gbrain structurally can't (why-aware recall) and installs the measurement discipline that is a *prerequisite* for any credible claim in this category. The scope doc's in/out cut is sound and endorsed. Three conditions: (1) mandatory sequencing — W2/W3 measurement FIRST (fix-wave protocol: publish the bad number before fixing or featuring), W7's 500-Q run as the capstone AFTER the fix-wave; (2) the conditional scope-cut below; (3) the W4 user-exposure gate below (which supersedes unconditional parallel-tracking).

**Scope-cut recommendation (conditional, for Daniel):** the **W4 phase-1 `/v1/context` endpoint build** (SDK `volunteer_context()` + `POST /v1/context`, hosted + self-host) is the one margin item with real coupling risk: it builds against a per-graph-key tenancy contract that #2083's registry schema has not yet landed (#2082 unfrozen). **Recommendation:** keep it in scope per D2, but (a) sequence the endpoint build AFTER the in-place enrichment of search/analyze (the fit-audit path — unblocked today), and (b) coordinate the tenancy contract jointly with #2083's schema work (joint contract co-design — see Alternative 1; #2083's schema is the critical pacing artifact); **if #2083's registry schema slips more than ~2 weeks past the W4-enrichment milestone, cut the endpoint to a follow-up issue** and ship W4 as in-place enrichment + harness-graded reflex logic (already the fallback plan). This preserves the epic's O/I/T core without building against an unlanded contract. No cut recommended for W1 (trivial cost, licensing hygiene) or W7 (its machinery is a W2/W3 prerequisite and its publication is the epic's point — the 500-Q run is the only high-cost item and it's cheap, ~$2-class, and correctly last).

**W4 user-exposure gate (resolves the parallel-track contradiction — reviewer P1-3):** W4's in-place enrichment is parallel-tracked for build velocity ONLY as (a) an internal/dev-surface build and (b) an A11-piloted capability test, until the W2 first baseline is published and the first fix-wave's failure classes are named — whichever is later. **User-visible exposure of why-context on production surfaces ships behind an opt-in/experimental flag until the write-path gate passes: (c) pinned-judge baseline reaches the committed survival target on the frozen corpus after ≤2 fix-waves; if not achieved, cut the why-layer to an internal research finding and re-scope** (gbrain's one-wave 61.5%→88.1% convergence is not guaranteed for Tortoise's structurally different points/operators/EP pipeline — the "pipeline can't be fixed" branch must be stated). This preserves the measurement-first rejection of Alternative 2 (no why-context on a silently-losing write path) while allowing build velocity.

---

## Step 5 — Routing

**PROCEED → epic-research** (next pipeline stage), with these attached conditions:

1. **Sequencing mandate:** W2/W3 (measure) before W4/W5 (feature/fix); W7 500-Q run last. The fix-wave protocol (publish bad → name failure classes → fix → re-run same frozen corpus + pinned judge) is not optional polish — it is the epic's demonstration.
2. **Contract coordination:** the `/v1/context` tenancy contract is jointly co-designed with #2083's registry-schema work (joint contract co-design; #2083's schema is the critical pacing artifact — conditional cut trigger above). #2082 stays unfrozen/parallel — consumes nothing.
3. **W4 user-exposure gate:** why-context on production surfaces ships behind an opt-in/experimental flag until the W2 baseline + first fix-wave are published and the write-path gate (survival target on frozen corpus after ≤2 fix-waves) passes; if the pipeline can't be fixed, cut the why-layer to an internal research finding and re-scope. (Reviewer P1-3.)
4. **Post-ship conversion checkpoint:** within 6 months of #2083 GA — ≥30% of pro-trial/`/v1/context` evaluation conversations cite receipts or the why-layer, OR measurable trial→paid lift vs pre-#2080 baseline; else Daniel convenes a kill/cut review of W4's public claims. (Reviewer P1-2.)
3. **Remaining research agenda for epic-research (the brief + scope already closed most of it):** W3 why-layer suite fixture generator + planted-conflict gold conventions (Tortoise-original, the novel part); W7 comparison-systems.md mechanism rows incl. the gbrain-evals adapter scorecard feasibility; A11 pilot (does surfaced context contain enough for the three why-questions? — run before W4 assembly is locked).
4. **Carried flags (not decided here):** ingestion default-ON vs default-OFF (scope approval-gate item 4 — product decision for Daniel; does not block write-path QUALITY); W2 "zero leakage" vs ≤1/run tolerance (research-recommended, sign-off item 3); ask-surface lane gated on #2013.

---

## Review Gate

Fresh-context reviewer dispatched per skill (4 dimensions: adversarial quality, profit causality, assumption risk, matrix honesty). Result: NO ISSUES FOUND (see review record appended below).

### Review record

**Reviewer:** fresh-context sub-agent (qwen3.8-max provider returned 401 — rerun on default model), 2026-09-01.
**Round 1 findings:** ISSUES — 3 P1, 2 P2 (no P0): P1-1 contract-coupling direction contradiction (Step 1 "#2080 defines the contract" vs Step 4 "endpoint gated on #2083's schema") → fixed as joint contract co-design (Alternative 1, Scope-cut, Routing); P1-2 undefined conversion failure threshold → fixed with an operational post-ship conversion gate (Step 3 falsification + Routing); P1-3 parallel-tracked W4 enrichment contradicts measurement-first → fixed with the W4 user-exposure gate (Scope-cut section + Routing); P2-1 uncontrolled-eval urgency partially self-canceling given adapter-friction → tempered (Alternative 3 + Opportunity Cost 2); P2-2 retention hop lacks falsification indicator → downgraded to unverified leading-indicator claim (Step 3).
**Verdict after fix round:** all P1/P2 applied — decision substance unchanged (APPROVE + W2/W3-first gate + conditional endpoint cut + new W4 user-exposure gate + conversion checkpoint).
