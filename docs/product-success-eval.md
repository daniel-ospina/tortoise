# Tortoise — Product-Success Evaluation Spec
## "Did the memory matter?" — measurement framework for the compounding-memory claim

Status: draft v1 · Owner: founder (dogfood loop) + eval battery · Scope: product/memory success (L1+), NOT extraction plumbing (L0)

---

## 0. Evaluation philosophy

**The delta principle:** a memory feature has value only if its presence changes agent/user behavior. Every success metric is a delta (treatment vs control, or this-week vs baseline). Any metric without a defined counterfactual is decorative.

**Three metric layers:**
- **L0 Plumbing** (extraction worked): points created, sessions captured, consolidation ran. Necessary input — never success evidence.
- **L1 Behavioral delta** (did it matter): recall-before-rederive, repeat-work avoided, decision consistency, contradiction surfaced, time-to-answer. **The success layer.**
- **L2 Outcome** (user-visible value): trust, retention, activation, revenue-adjacent behavior. Pre-revenue proxies below.

**Vanity list (tracked for diagnostics only, excluded from launch gate):** point count, extraction volume, sessions captured, retrieval count, dashboard views, "memories created" counters.

**Battery architecture — three instruments, all required:**
1. **Synthetic battery** (§1–2): scripted, controlled, reproducible, statistical — proves the mechanism.
2. **In-product telemetry** (§3–5): instrumentation on real usage — proves it fires in the wild.
3. **Dogfood audit** (§6): founder's weekly human review — validates meaning and trust.

Any one alone can be gamed. Launch gate requires all three.

---

## 1. The "did it matter" battery

All three scenarios share a skeleton:
- **Setup:** S1 (decision/task session) → delay spanning ≥1 consolidation run → S2 (fresh context: new conversation, no transcript carryover, same model/temperature).
- **Design:** matched pairs, cross-over (each task run once with memory, once without, order counterbalanced), pre-registered rubric, grader blind to condition.
- **Rule:** no scenario is a success test unless the control's behavior is also measured.

### 1a. Prior-decision recall — "the agent recalls the prior decision instead of re-deriving it"
- **S1:** user+agent complete a structured decision (option/criterion/argument chain stored; decision point goes live).
- **S2 prompt:** follow-on task whose correct output REQUIRES the S1 decision, without restating it — "draft the LICENSE headers" after the licensing decision; "write the migration plan" after the schema decision.
- **Measured:** (1) re-derivation vs recall — re-research tool calls (re-fetch, re-compare options) before first correct answer; (2) time-to-first-correct-mention; (3) recall accuracy (output value == stored point, point ID citable); (4) user turns needed.
- **Targets:** recall-without-rederive ≥ 90% (treatment) · re-derivation calls ≥ 5× fewer than control · time-to-correct ≤ 40% of control · exact-match accuracy ≥ 90% · user turns ≤ half of control.

### 1b. Contradiction surfacing — "a new claim contradicts an old decision and the system surfaces it"
- **S1:** store decision D at live confidence.
- **S2 (scripted):** user introduces claim C contradicting D (or a finding undermining D's rationale). System must surface the conflict (NAND/conflict notice), not silently adopt C.
- **Measured:** detection (surfacing event within 1 turn of C) · resolution quality (supersede/mitigate with ledger entry vs silent flip-flop) · **false-positive rate** (run non-contradictory control claims through the same sessions).
- **Targets:** detection ≥ 90% within 1 turn · explicit resolution recorded ≥ 70% · **false positives ≤ 5%** (false alarms are trust-killers; gate this harder than detection).
- **Honesty:** a surfaced contradiction the user ignores is still correct behavior — "acted-on" is tracked separately (dogfood loop).

### 1c. Answer-from-memory — "the agent answers from memory what was decided weeks ago"
- **S1 at t0:** record D + full rationale. **t+21 days** (≥10 interleaved sessions, ≥5 consolidation runs): S_N asks a question answerable only from the graph — "why did we pick X over Y?"
- **Measured:** correct-with-provenance (answer cites stored points + confidence) · hallucination rate (fabricated rationale) · time.
- **Targets:** correct-with-provenance ≥ 80% · hallucinated rationale ≤ 10% (no-memory control fabricates ~100% — that's the delta) · time-to-answer ≤ 1 graph query / < 2 min.
- 1c is the load-bearing test for the longest-lived claim: "weeks-old memory still matters."

**Battery verdict:** PASS = all three scenarios meet targets at ≥ 20 runs each, p<0.05 vs control. The battery proves the mechanism; telemetry proves it fires on real work.

---

## 2. The 2-session continuity experiment (the compounding claim)

**Claim to prove:** a fresh-context agent with Tortoise memory performs measurably better on follow-on work than the same agent without — and the benefit grows with session count.

### Task suite
10 calibrated multi-step tasks, each split across 2 sessions: S1 = analysis/decision (licensing, auth design, region selection); S2 = follow-on requiring S1 output (docs, extension, interrogation). **Calibration floor:** in pilot runs, control must show ≥ 30% decision drift and ≥ 8 re-derivation tool calls — otherwise the metric can't discriminate.

### Design
- 20 runs (10 tasks × 2), matched pairs, cross-over, fresh context both sessions, 48h gap (≥1 consolidation run), same model/temp/seed.
- Fresh conversation per session; **the only difference between arms is the graph.**
- Pre-register per-task rubrics (what is "correct", which tool calls are "repeat work") before running.
- **Third arm** on 5 tasks: control-with-user — no memory, but agent may ask the user in S2. Measures where memory beats asking-the-user (autonomy), the real product question.

### Metrics and targets (paired, Wilcoxon signed-rank; report medians, not means)

| Metric | Definition | Treatment target | Control baseline |
|---|---|---|---|
| TCA | seconds S2 prompt → first rubric-correct output | ≤ 40% of control | 100% (re-derives) |
| Repeat-work calls | tool calls re-fetching info established in S1 | ≤ 1 | ≥ 8 (median) |
| Decision consistency | S2 decision == S1 recorded decision | ≥ 95% | ≤ 70% (drift floor) |
| Contradiction rate | S2 output contradicts S1, **unsurfaced** | ≤ 2% (surfaced don't count) | ≥ 20% |
| Question repetition | user re-asks questions answered in S1 | ≤ 5% of user turns | ≥ 25% |

### Compounding (the actual claim — not just recall)
Extend 5 tasks to 3-session chains (S3 follow-on after S2). **Compounding PASS:** benefit at S3 (TCA ratio vs control) ≥ benefit at S2 — the memory advantage is non-decreasing as the chain grows — AND save/recall telemetry rises week-over-week (§5). **A 2-session experiment proves recall, NOT compounding. Do not let it be marketed as compounding proof.**

### Go/no-go
PASS if treatment beats control on ≥ 4 of 5 primary metrics (p<0.05), median TCA advantage ≥ 2×, consistency ≥ 95%.
FAIL if recall is high but consistency isn't (memory retrieved but wrong) or repeat-work isn't reduced (retrieved but unused — dead weight, §7).

---

## 3. Recall quality & point-reuse

### Definitions (precise — these get gamed)
- **Retrieval:** a stored point is returned by a recall/query path in a later session (logged: query, rank, score, trigger — agent tool call / consolidation / user).
- **Reuse:** the retrieved point is **cited** in an agent message (product invariant: provenance everywhere → citations are parseable, attribution is free).
- **Effective reuse (the honest metric):** removing the point changes the output. Measured by **ablation** on a 5% sample of sessions (block top-ranked retrieval, diff output). Gold standard — sample it, don't run it everywhere.

### Instrumentation
- Retrieval log (above) — shipped with the recall path.
- Citation parser → **point-use ledger** (which stored points changed what got said).
- **Ablation sampler:** 5% of sessions run with top-1 retrieval blocked; output delta (edit distance / decision change) = effective-reuse signal.
- **Dismiss events:** agent surfaces memory (contradiction notice, "recalled:" block) and user ignores/overrides → the noise signal.
- **Poisoning probe:** in 2% of sessions, inject a plausible-but-wrong point into retrieval results; agent must reject it (§7, failure 2).

### Targets
- **State-layer reach:** ≥ 50% of decision-class points retrieved within 14 days of creation.
- **Epistemic reach:** ≥ 30% of all points retrieved within 30 days; never-retrieved-in-90d → flagged ROT (dashboard).
- **Reuse:** ≥ 50% of retrievals cited · ≥ 20% of sampled outputs change when top retrieval is ablated (effective-reuse floor).
- **Noise:** ≤ 30% of surface events dismissed · ≤ 10% of citations cosmetic (output identical when citation removed).
- **Staleness:** 0% of superseded points appear as live answers (must be flagged or excluded).

**Vanity warning:** retrieval rate is vanity by itself (a graph can retrieve everything and matter nothing). Only reuse-with-attribution and the ablation sample measure "did it matter." Publish vanity numbers as denominators, never headlines.

---

## 4. Memory-health dashboard (minimal honest version)

**Design rules:** every number maps to a behavior; rot is as visible as value; a green-only dashboard is a lie.

### The 5 numbers that matter (weekly)
1. **Answered from memory** — outputs citing stored points. "Your memory answered 14 questions this week."
2. **Repeat-work avoided** — sessions where recall replaced re-derivation (repeat-work heuristic: tool calls matching a previously-stored query pattern). "You didn't redo 6 decisions this week."
3. **Contradictions surfaced / resolved** — count + linked list (old claim, new claim, NAND, resolution state). "2 surfaced, 1 resolved, 1 open."
4. **Rot meter** — stale points (no retrieval in 90d) + draft points never promoted (below confidence gate 30d) + unresolved NAND edges. "41 points never touched, 12 drafts never confirmed, 1 contradiction open 3 weeks."
5. **Noise rate** — surfaced-but-dismissed / total surfaced. "3 of 12 memory surfaces were dismissed."

### Secondary row (context, not headlines)
Top entities (semantic-state layer's model of the user — "you care most about: licensing, pricing, auth") · open questions (claims with no resolution — drives the user to close them) · confidence distribution (live / draft / mitigated) · graph size (as denominator).

**The honest invariant:** the dashboard must render a negative week as clearly as a positive one — "Your memory answered 0 questions this week; 3 surfaces dismissed" is a first-class view, not an error state. **If the dashboard can't be red, it can't be trusted.**

---

## 5. Retention/engagement proxies (pre-revenue funnel)

**Causal funnel:** capture → query → recall → behavioral delta. Each stage is a leading indicator of the next.

| Proxy | Definition | Target |
|---|---|---|
| Capture rate | sessions captured / sessions run | ≥ 80% (≥10 of ~13/day) |
| Graph-query rate | recall reads per captured session | ≥ 1.0 (a write-only graph is a museum) |
| Recall rate | retrieval-into-output events per captured session | ≥ 0.3 by week 2 |
| **Save/recall ratio** | recall events / captures — must RISE | +25%/week for 4 weeks, plateau ≥ 0.5 |
| Continuity | sessions referencing a prior session via memory | ≥ 30% by week 4 |
| Contradiction surfacings | per week | ≥ 0.5/week sustained |
| Activation (public) | first capture → first answered-from-memory | ≤ 24h for a stranger |
| Point survival | points alive 30d without supersede/delete | ≥ 70% (extraction-quality proxy) |

**Honesty:** engagement ≠ value. The founder is engaged because it's his product — these proxies prove the loop is alive; only §1–2 prove it's valuable. **Save/recall ratio is the one headline-worthy proxy** — it's the leading indicator of compounding (memory's value should grow, not just exist).

---

## 6. The founder dogfood loop

**Cadence:** 30–45 min weekly, fixed checklist. Founder = power user + product owner; the dogfood audit is the trust layer.

### Weekly review (5 items)
1. **Extraction samples:** 15 random points (10 epistemic, 5 state-layer). Pass if value-first (decisions/options/criteria/findings, not trivia), confidence gates correct, claims true. **≥ 85% pass = extraction healthy.**
2. **Ledger explanations:** 10 random mutations (create/IMPL/NAND/mitigate/supersede). Check edge semantics — truth attacks on points, relevance attacks on operators, mitigations in 0.10–0.50, supersessions clean edges. **≥ 80% correctly typed.**
3. **Contradiction surfacings:** review every event (expected ≥ 2/week). Correct? Acted on? **Plus a missed-contradiction probe:** hand-check 2 sessions/week against the graph for conflicts NOT surfaced.
4. **Recall failures** (highest-value bug class): every instance where the agent re-derived something already in the graph, or surfaced noise. File P1/P2, count them.
5. **The 5 dashboard numbers** (§4) + save/recall trend (§5).

### Triage ladder
- **P0 (block):** trivia-heavy extraction OR false contradictions surfacing → confidence gate / expansion-pack tuning; feature is untrustworthy.
- **P1:** decision-class recall misses (state layer not retrieved) → retrieval threshold + reranking.
- **P2:** confidence mis-calibration (drafts that should be live) → threshold tuning.
- **P3:** noise (retrieved but dismissed) → reranking / surface formatting.

### Launch gates — two separate gates, don't conflate
- **Internal gate ("beta when X"):** 2 consecutive dogfood weeks with: ≥ 85% extraction pass · ≥ 5 real answered-from-memory events/week · ≥ 3 repeat-work-avoided/week · ≥ 2 surfacings/week with ≥ 1 acted-on and ≤ 1 false positive · stale-point rate < 20% · save/recall rising · AND synthetic battery (§1–2) green.
- **Public gate ("strangers when Y"):** internal gate green PLUS: stranger activation ≤ 24h · synthetic battery green on the public task set · noise ≤ 30% for new users · value visible without founder-level domain knowledge.

**The founder's green light is necessary but insufficient** — he can't feel a novice's onboarding, and solo-tier value isn't validated by a power user's sessions.

---

## 7. Adversarial checks (the honest failure modes)

### Failure 1 — Dead weight (technically correct, never used)
- **Symptom:** capture and retrieval climb; behavioral delta flat; save/recall flat; sessions still re-derive; users "have memory" and it changes nothing.
- **Caught by:** ablation sample (output delta ≈ 0 = dead weight regardless of retrieval counts) · repeat-work metric (still re-deriving = graph not consulted) · save/recall trend (flat = no compounding) · dogfood item "what did memory change this week?" — "nothing" is an answer.
- **Kill rule:** ablated-delta < 10% of sampled outputs for 2 consecutive weeks → the retrieval path is decorative; fix or cut before launch.

### Failure 2 — Recall noise (retrieved but unhelpful or misleading)
- **Symptom:** high retrieval AND high citation but low output delta; high dismiss rate; stale claims presented as current; cosmetic citations (true but irrelevant padding).
- **Caught by:** dismiss rate ≤ 30% (else surfaced memory is noise) · **poisoning probe** (inject plausible-but-wrong point into 2% of retrievals; agent must reject ≥ 80% at high confidence) · staleness check (0% superseded points in live answers) · cosmetic-citation check (citation present, output unchanged → noise, not reuse).
- **Kill rule:** dismiss > 40% or poison-acceptance > 20% → the surface path is lying; don't ship surfacing until fixed.

### Failure 3 — The black-box graph (can't trust what you can't see)
- **Symptom:** answers accepted on faith OR everything distrusted because nothing is verifiable; "memory said" without "here's why"; the graph so confident it stops showing its work.
- **Caught by:** provenance invariant (100% of memory-derived output carries point IDs + confidence — instrumented as a test, not a wish) · explainability spot-check (10 sampled answers: a reviewer can reconstruct the IMPL chain to source — else it's a black box) · trust telemetry (do users open provenance / edit surfaced points — edit-rate on surfaced points is a trust+engagement signal) · NAND transparency (100% of surfacings show both claims + resolution state).
- **Kill rule:** unattributed-memory rate > 0% in production output, or any surfacing without both sides shown → trust is gone; fix the invariant.

### Cross-cutting honesty rules
- **Pre-register** every test, metric, and target before data collection. Post-hoc target adjustment is disallowed (that's how vanity metrics are born).
- Every headline metric is a **delta** (vs control or baseline). Absolute counts are denominators.
- Vanity metrics are tracked for diagnostics and **explicitly excluded from both launch gates.**
- Any claim from the battery states which instrument produced it: synthetic (mechanism) / telemetry (fires in the wild) / dogfood (human-validated).

---

## 8. The launch decision in 12 numbers

| # | Metric | Gate value |
|---|---|---|
| 1 | Synthetic battery | 3/3 scenarios pass, p<0.05 |
| 2 | Continuity TCA ratio | treatment ≤ 40% of control |
| 3 | Continuity consistency | ≥ 95% same decision |
| 4 | Continuity contradiction (unsurfaced) | ≤ 2% |
| 5 | Effective reuse (ablation delta) | ≥ 20% of sampled outputs |
| 6 | Surface dismiss rate | ≤ 30% |
| 7 | Superseded-in-live-answers | 0% |
| 8 | Dogfood extraction pass | ≥ 85% for 2 weeks |
| 9 | Real answered-from-memory (dogfood) | ≥ 5/week for 2 weeks |
| 10 | Contradiction surfacings | ≥ 2/week, ≥ 1 acted on, ≤ 1 false positive |
| 11 | Save/recall ratio | rising week-over-week, ≥ 0.5 |
| 12 | Stranger activation | first value ≤ 24h |

All 12 green (internal gate = 1–11; public gate = 1–12) → launch the compounding claim with evidence. Any of 5, 6, 7, or 12 red → the product is not yet honest enough to sell.
