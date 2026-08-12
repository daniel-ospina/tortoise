---
title: "Mitigation Audit — Window #1 (probe v2, R9 rubric)"
type: probe-audit
domain: operations
doc_status: audit
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
auditor: "independent mitigation audit (R9 lens)"
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
audited: "docs/drafts/2026-08-09-probe-extraction-window1-v2.md (probe iteration 2, 4 MITIGATES)"
inputs: "gold window v2, mining-system-requirements R9, align decision, epic #909 scope + research briefs, raw session slices (2026-08-09/10/11)"
---

> **Independent audit of the probe's R9 output.** Iteration 2 emitted 4 MITIGATES
> relations. This audit hunts for the ones the probe MISSED and sanity-checks the 4
> emitted, against the R9 definition (relevance reduction on an IMPL **edge**;
> NAND ≠ MITIGATE; bias 0.10–0.50; canonical X-IMPL-A / Z-MITIGATES-[X→A] / Y-IMPL-Z).
> Every candidate below was verified against the actual conversation content (raw
> session + the in-window artifacts that record its claims: gold window, guardrails,
> mvp-scope economics, capture architecture, 753 review verdicts, align decision,
> scope, research briefs — the last three were produced IN the window session).
> Note: the request for this audit was itself dispatched inside the window session
> (line ~1249), so this is the loop's iteration 3.

---

## §1 — MISSED mitigations (the probe's coverage gap)

### A. Clean misses — target edge ALREADY in the probe's graph (emit immediately, no new points needed)

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M1** | **bytes/node is the one swing variable** — the 1KB storage basis is asserted, not measured; at 2.5–4KB full-cap Team storage ($109–175/mo) approaches/exceeds the $149 price and flips Team GM negative | **[C7 → B1]** ("storage collapses ~$730→~$1/mo" IMPL "break-even") — also touches **[C7 → D5c]** (no-caps affordability) | **0.25** | **GENUINE — MISSED.** C7 stays true at the 1KB assumption; its robustness as economic support is conditioned on an unmeasured variable. R9 cue: *"the one swing variable is"*. |

> **Quotes (S0 session, review synthesis; #753 economics reviewer):**
> "One thing to instrument: actual bytes-per-node (the 1KB assumption is the only
> swing variable that could hurt Team-tier margin)."
> "Residual risk on the storage line: the 1KB/node basis is the fragile assumption…
> At a 2–4KB average, the heavy-user figure is $2–3/mo and full-cap Team storage
> (600k nodes × 2.5–4KB = 1.5–2.4GB = **$109–175/mo**) approaches or exceeds the
> $149 price. The guardrails demand pilot telemetry for session sizes but
> **nothing instruments bytes/team — node count × 1KB is asserted, not measured**."
> "bytes-per-node is the variable that can flip Team-tier GM from 90% to negative."

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M2** | **The mutual-contradiction coupling is weak BUT single-direction attacks are the common case** — the extractor's NAND direction policy (new-claim → `unidirectional`) restores the surfacing payoff; mutual contradiction is the rare explicit case | **[C5 → surfacing-feature work item]** ("mutual-contradiction coupling +0.0024 → surfacing gated") — the probe's OWN claims table carries this edge | **0.30** | **GENUINE — MISSED.** C5 stays true (the contested detector still can't fire on mutual cases); its weight as "the flagship feature is blind" is reduced because the common case is directed and directed is measured-correct. The align doc literally labels this a "Mitigation". R9 cue: *"while true, it doesn't matter because"*. |

> **Quotes (align decision; NAND-policy addendum; 753 verdict):**
> "The surfacing payoff (contradiction detection) can't fire yet (mutual-contradiction
> coupling +0.0024). **Mitigation:** the epic scopes the extractor's NAND direction
> policy (new-claim → unidirectional) so the payoff loop exists by launch."
> "New-claim-attacks-existing-claim → `unidirectional` (directed)… This is the
> **common, measured-correct case and the one that makes surfacing work**."
> "The contradiction-surfacing feature can't fire yet — the extractor needs to write
> new-claim-attacks as *directed* (which we now know works)… Without that policy,
> the flagship feature is blind."

### B. Deep misses — target edge exists in the conversation but the probe omitted the edge too (fix = emit edge + mitigation)

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M3** | **The privacy guarantee is strong (true by architecture) but gated on the raw-upload rework** — "we never see your conversations" is D4's trust story; it cannot be publicly claimed until the merged #131 cloud path is reworked to derived-writes | **[D4 → privacy-resolved]** ("local intelligence, remote graph… resolves key-storage, **privacy**, and break-even in one move" — gold D4 rationale) | **0.30** | **GENUINE — MISSED.** The architecture property is true but its present-tense launch weight is reduced — a launch-blocking readiness condition. R9 cue: *"this is gated on"*. Complements (does not duplicate) the probe's C15, which is the NAND-side finding (claim false in today's code); C15 and M3 coexist. |

> **Quotes (capture architecture doc / session; 753 verdict):**
> "Solves conversation privacy — 'we never see your conversations' is a *strong* trust
> story for devs… The raw tape stays home; the graph is the shared, curated layer."
> "The merged cloud-mode (#131) needs reworking — right now it posts the *raw
> conversation*… Worth deciding before #131 becomes default-on."
> "The privacy claim isn't true in the code yet — the merged capture mode posts raw
> conversations. **Before we ever say 'we never see your conversations,' that path
> gets reworked** so derived-only is the default. (This is a launch blocker…)"

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M4** | **The design review approved the direction but conditioned the artifact** — "not implementation-ready until the conditions below close" (4 blockers + 6 P1s) | **[review approval → implementation-ready / proceed-to-build]** ("All three reviewers approved the direction… But none would let it be built as-is") | **0.30** | **GENUINE — MISSED.** The approval is real; its weight as a build-go is reduced — the artifact is conditioned. R9 cue: *"the caveat is"* / *"that's a risk note, not a change"*. The review outcome is a claim-level gate result (recorded on #753) the probe never emitted as a point. |

> **Quotes (session synthesis; 753 consolidated verdict):**
> "All three reviewers approved the direction — value-first extraction,
> local-intelligence/remote-graph, the deferral list… **But none would let it be
> built as-is.** The conditions they found are concrete and mostly small."
> "**DIRECTION: APPROVED (2/3) — ARTIFACT: CONDITIONED/REJECTED-AS-IS.** … Not
> implementation-ready until the conditions below close."

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M5** | **The evaluation machinery is 0% built** — the gold set is empty; "is extraction any good?" is unanswerable; everything downstream depends on it | **[direction-approved → validated/ready]** (readiness claim within M4's cluster; distinct content: validation-readiness, not artifact-readiness) | **0.25** | **GENUINE — MISSED** (as a conditioning claim on the same approval edge as M4, or on the readiness edge). A gap-fact that plays a tempering role: the direction's readiness weight drops because the falsification machinery doesn't exist. R9 cue: *"this is gated on"*. |

> **Quote (session synthesis, 753 verdict):**
> "The evaluation machinery is 0% built — the gold set is empty. Everything
> downstream (is extraction any good?) depends on it."
> "Evaluation machinery is 0% built — gold set empty (0/30 windows), no `metrics.py`,
> no judge harness, no CI. The gold set is 25-35h of founder time — the critical path."

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M6** | **Window #1 is a single conversation — the validation's conclusions are preliminary** — the gate is not green until window #2 (a different session type) runs | **[window-#1 probe pass → rubric validated]** ("the comparison shows the requirements working (R3 routed, R6 refused, R2 demoted)") | **0.30** | **GENUINE — MISSED.** The pass is real; its evidentiary weight is reduced: one window, one session type, κ gate + second window pending. R9 cue: *"but that's preliminary"* / *"this is gated on"*. |

> **Quote (session, validation status):**
> "**Window #1 of the validation RAN live** — the probe extraction is saved…, the
> comparison shows the requirements working (R3 routed, R6 refused, R2 demoted)…
> **Window #2 still to run** (a different session type — e.g., a short operational
> session) **before the gate is green.**"
> (And the gate's own rule: "κ<0.50 = revise rubric first. If the rubric can't
> reproduce on your own workload, everything downstream changes." — 753 verdict.)

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M7** | **The 2-window / 30-window validation is a rubric diagnostic, not a statistical gate** — "coarse watch-gates, not powered tests"; N=30 cannot reject a true-0.80 with 90% power (N≈109 needed); real power is the post-launch live-judge loop; "Do NOT claim powered separation from the gold set alone" | **[eval gate / gold set → premise validated]** (the gold-first gate's evidentiary role — the scope's hard gate / E2E-1) | **0.35** | **GENUINE — MISSED.** The gate's power claim is true but its statistical weight is substantially reduced — a "watch-gate, not a statistical test" is an R9 cue verbatim. |

> **Quotes (R8 research brief, in-window; session summary):**
> "these bands are **COARSE WATCH-GATES, not powered statistical tests**… the
> 30-window gold set validates the premise **directionally** and feeds the live-judge
> calibration loop (rolling N=20), which is the real statistical power source
> post-launch. **Do NOT claim powered separation from the gold set alone.**"
> "The verifier caught that the statistical power claims were wrong — N=30 does NOT
> reject a true-0.80 with 90% power (that needs N≈109); the bands are honest
> *watch-gates*, not powered tests."

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M8** | **Layer-correct ≥0.90 is achievable ONLY because gate-first + closed vocab** — if keep-ratio drifts >40% (fail-closed), classification difficulty rises toward the 59–73% research range; the keep-ratio alarm is the leading indicator | **[R1 layer-correct ≥0.90 gate → extraction-quality established]** (the headline semantic gate's robustness) | **0.30** | **GENUINE — MISSED.** The ≥0.90 target is true but its robustness/independence is conditioned on a monitored precondition — a coupling warning, not a threshold value. R9 cues: *"only achievable because"* / *"the leading indicator"*. |

> **Quote (R8 research brief, "Coupling warning"):**
> "layer-correct ≥0.90 is achievable **ONLY because** the pipeline is gate-first
> (S1 drops 75–95%) and the vocab closed. If keep-ratio drifts >40% (fail-closed),
> classification difficulty rises toward the 59–73% research range — **the keep-ratio
> alarm is the leading indicator.**"

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M9** | **The managed-key path is the only real margin hole, and it's unpriced** — F2/F3 apply to it verbatim ($75–160/mo LLM COGS at 2,500 captures vs $25 pro); `pricing.json` has no field; "bundled" at tier price is a known loss | **[economics-sound → proceed]** ("This also resolves the break-even question completely" / "the economics of the local architecture are the strongest part of this design") | **0.25** | **GENUINE — MISSED.** The economics-sound claim is true for the default (BYOK) path; its universality is reduced — the kept opt-in (D9b) is the exception, deferred out of v1 rather than resolved. R9 cue: *"the exception is"*. |

> **Quotes (#753 economics reviewer; session):**
> "**P1 — managed-key path is the only real margin hole, and it's unpriced.** F2/F3
> still apply to it verbatim: at 2,500 captures/mo it's $75–160/mo LLM COGS vs $25.
> `pricing.json` has no capture or managed-LLM field. Price it explicitly
> (~$0.03–0.05/session metered, p90-based, not p50) or cap it before enabling;
> 'bundled' at the tier price is a known loss."
> "This also resolves the break-even question completely: with BYOK, the LLM cost
> isn't ours at all." (the claim being tempered — the resolution is complete for
> BYOK only)

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Verdict |
| --- | --- | --- | --- | --- |
| **M10** | **Caching is NOT the cost fix; warrant discipline is** — prompt-cache blended reduction is ~12%, "real but not transformative" | **[caching-lever → cost-bounded]** ("cheap model + prompt caching + batch throttling" as the cost-bound recommendation) | **0.20** | **GENUINE content — probe non-fire defensible but incomplete.** The tempering is real (12% is true but minor); its target edge was never emitted because the probe's guardrail-detail convention (nothing #7/#8) dropped the caching-lever claim. The probe DID log this as a near-miss (§10.2) — partial credit — but the positive mirror ("warrant discipline is the cost lever", B2→D6) WAS emitted, so the negative mirror should be too. |

> **Quotes (economics guardrails §1.3; session cost analysis):**
> "the blended reduction is **~12% — real but not transformative. Caching is NOT the
> cost fix; warrant discipline is.**"
> "Keep the cost bounded from day one: cheap model + prompt caching + batch
> throttling (all flagged as needed in the align doc)."

---

## §2 — Candidates verified as NOT mitigations

| # | Candidate | Verdict | Why (R9 discrimination) |
| --- | --- | --- | --- |
| **N1** | "Solo is margin-positive under local extraction (the loss-leader framing is stale)" | **NOT a mitigation — a REVISION (supersede)** | The earlier claim's TRUTH changed (its premise — our LLM COGS — no longer exists under D4), so it is not "true but matters less"; it is stale. Correct mechanism: S5 `REVISES`/`supersede`. Quote: "The 'solo = bounded loss leader' verdict (COGS table §4: −37%) is computed on LLM cost that no longer exists on our side. Under local extraction solo COGS ≈ $1–2 storage → **solo is margin-positive. Stale framing, not harmful**…" (The "not harmful" clause is a review severity rating, not a graph argument.) |
| **N2** | "Inversion finding real BUT ruling kept bidirectional anyway, mitigated by the directed opt-in" (C2→D2) | **Probe's exclusion CORRECT per R9** | The opt-in is a clause INSIDE decision D2 (the ruling's escape hatch), and the target would be a NAND edge — R9 restricts MITIGATES to IMPL connections. The pre-R9 loop discussion called it a mitigation; R9's final form resolved it as decision content. Keep as a discrimination case for the semantic eval set (the extractor must be able to explain the non-fire). |
| **N3** | "Reasoning overhead can add 1.5–3× on the output line" (cost caveat on the model comparison) | **NOT a mitigation — a precision caveat** | It tempers C6's NUMBERS but not the edge's weight: the overhead applies to all three models and the note concludes "another reason DeepSeek Flash's cheap output wins" — the support for D3 is unchanged or reinforced. No relevance reduction. |
| **N4** | "The docs contradict each other — three node ceilings (25/40/50)" | **NOT a standalone mitigation** | A correctness/inconsistency finding (NAND-flavored) about the docs' coherence — content that conditions the artifact, so it folds into M4's conditioning cluster rather than attacking a kept IMPL edge. |
| **N5** | "E019 a_drop>0.03 would fail against today's code (suite Docker-skipped)" | **Probe's non-fire CORRECT** | Status caveat on a spec's assertion (pre-NAND-fix applicability), not a reduction of a kept IMPL edge. |
| **N6** | "Hosted recall is of the derived layer + summaries, not the literal transcript" | **Probe's non-fire DEFENSIBLE** | A capability limitation; no kept point asserted full hosted recall, so no edge to attack. Correctly logged as a near-miss. |
| **N7** | "Your real usage (measured, not estimated)" (real session data vs rough math) | **NOT a mitigation — a REVISION** | The measured numbers supersede the earlier estimates ($10–50/mo estimate → measured $2–10/mo at Flash rates). Truth-update, not relevance reduction. |
| **N8** | "F4's overage arithmetic is wrong ($27.50 ≠ $2.50)" | **NOT a mitigation — a CORRECTION** | A truth-correction of an arithmetic error (the claim was false as computed), plus a severity note ("immaterial under local") that is a review rating, not a graph argument. |

---

## §3 — Sanity-check of the probe's 4 emissions

| Probe emission | Target | Bias | Verdict |
| --- | --- | --- | --- |
| **T1** "85–96% GM figures are computed estimates; G1 requires pilot measurement; target re-scoped to ≥35%" | [C8→D4] | 0.30 | **PASS — genuine.** C8 stays true (local extraction flips the margin sign); its weight as the architecture's economic support is tempered — "all numbers below are computed, not asserted" + G1 ("Measured per-session LLM cost (2-week pilot, production telemetry — **not the model**)"). Correct edge, correct semantics, bias in range. Minor impurity: the "target re-scoped to ≥35%" clause is a revision folded into the mitigation's content — harmless, but note it. |
| **T2** "pricing tension deliberately open; decide with real telemetry" | [D6→B1] | 0.35 | **PASS — genuine (canonical Z, live).** "Decide with real telemetry; the important fact is the cost side is now tiny and bounded" (S4) tempers B1's "break-even **without pricing change**": true at the median, does not settle the capture-unit pricing question. Correct mapping X=C10/B1-cost-side, A=B1, Z=T2, Y=B2. |
| **T2** (same claim) | [B3→D5b] | 0.30 | **PASS with note — weakest of the 4, defensible.** B3 stays true ($5/10k ≈ $0.0275/capture ≈ marginal cost — honest unit); "capture needs either its own usage line or a write-op price that reflects LLM cost — decide with real telemetry" (S4) tempers how much the unit-integrity finding settles the metering/pricing design. Risk: T2's topic overlaps B3's own content (both are the F4 tension); the genuinely mitigating clause is the deferral. Not over-extraction, but the nearest-to-borderline of the four. |
| **B2** "p90/p95 = 2–3× median; flat 5-window table understates the tail" | [D6→B1] | 0.30 | **PASS — genuine.** F2's verdict verbatim: "holds for the median… and **breaks above p90** (2–3x median)". Independent second relevance finding on the same edge (composition semantics is the probe's own flagged open question — fine). Note: B2's p90/p95 figures are warrant-inclusive; post-D6 (warrant budget 1/3) the tail still breaks the $0.02 premise via window-count scaling, so the mitigation stands. |

**Over-extraction check: none.** All 4 target kept IMPL edges, none attack a point's truth, all biases ∈ [0.10, 0.50]. The T1/T2-as-claims-playing-a-mitigating-role design (mitigation = operator fed by claims, per R9) is conformant.

**Non-fire check (§10.2):** C2→D2 exclusion correct (N2); E019 correct (N5); hosted-recall defensible (N6); **caching non-fire now flagged as M10** (the underlying claim WAS asserted in the conversation — the near-miss log was right that no edge existed *in the probe's graph*, but the fix is to emit the edge, not to stay silent).

---

## §4 — Verdict: the coverage gap

| Metric | Count |
| --- | --- |
| Genuine MITIGATES in the conversation's argumentation | **14** |
| — probe emitted | **4** (T1→[C8→D4]; T2→[D6→B1]; T2→[B3→D5b]; B2→[D6→B1]) |
| — MISSED, target edge already in probe graph (clean misses) | **2** (M1, M2) |
| — MISSED, target edge omitted by probe too (deep misses) | **7** (M3, M4, M5, M6, M7, M8, M9) |
| — MISSED, edge absent by probe convention (logged near-miss) | **1** (M10) |
| **Coverage: 4/14 ≈ 29%. Gap: 10 missed (71%).** | |

**The probe still under-identifies mitigations — the exact failure R9 was created to fix.** The 4 it emitted are all correct (no over-extraction, no NAND-confusion, canonical case handled). But 10 genuine relevance-temperings in the same conversation were not extracted:

- **2 are immediate fixes** (M1 bytes/node → [C7→B1]; M2 single-direction-common-case → [C5→surfacing]): the target edges are already in the probe's own graph — no new points needed, only the R9 classifier firing on "the one swing variable is" and "the common case is…" language.
- **8 require widening the point-set** the probe's conventions dropped: the review outcome + its conditions (M3/M4/M5), the validation-run claims (M6/M7/M8), the economics-sound conclusion (M9), and the caching lever (M10). The probe's "nothing #7" rejection (eval thresholds as spec values) is correct for *values* (A1–A22, κ targets) but wrong for *robustness/achievement claims* ("only achievable because", "watch-gate not a statistical test") — those are claim-level findings and must be kept so their mitigations have edges to attack.

**Recommendations for the extractor (feed iteration 3):**

1. Add to the MITIGATES few-shot set: assumption-swing language ("the only swing variable is", "asserted, not measured"), common-case rebuttals ("the common case is X, mutual is rare"), gate-condition language ("before the gate is green", "only achievable because", "gated on"), and exception language ("the only real hole", "the exception is").
2. Revisit nothing #7's boundary: keep robustness/achievability claims as points even when threshold *values* are rejected.
3. Emit gate-outcome claims (design-review verdict, validation-run status) as points so their conditioning claims (M3–M6) have target edges.
4. Retain N2 (C2→D2), N6 (hosted-recall), and M10 (caching) in the semantic eval set as discrimination probes — the system must be able to *explain* each non-fire/fire decision.
5. The open question from probe v2 §10.4 stands: two mitigations now land on [D6→B1] (T2 0.35, B2 0.30) and M2's edge carries the align doc's explicit "Mitigation:" label — bias composition and the "Z = policy decision" shape need engine semantics before biases are load-bearing.

---

*Audit of probe iteration 2 (R1–R9). Result: 4/4 emitted relations verified genuine; 10 missed (2 clean, 8 deep) — coverage 29%. The conversation's own R9 lesson ("under-identifying them makes the mined graph structurally incomplete") applies to the probe itself.*
