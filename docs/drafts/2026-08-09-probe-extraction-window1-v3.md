---
title: "Probe Extraction v3 (R9 full cue taxonomy) — Window #1"
type: probe-extraction
domain: operations
doc_status: probe
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
extractor_version: "value@0.1.0-draft (probe run 3 — R1-R9 rubric + audit-expanded cue taxonomy)"
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
window: "full session — infra verification → issues/pipeline → NAND fix → design exploration → capture architecture"
purpose: "EXTRACTOR PROBE iteration 3 — re-extraction of the same window with the FULL R9 cue taxonomy (requirements doc + mitigation-audit findings). Iteration 2 emitted 4 MITIGATES (4/14 = 29% coverage); the audit (2026-08-09-mitigation-audit-window1.md) identified 10 missed (M1-M10) with target edges + biases. This run re-extracts applying the audit's cues ('the one swing variable is', 'the common case is', 'this is gated on', 'the caveat is', 'watch-gate, not a statistical test', 'only achievable because', 'the leading indicator is', 'the exception is', 'real but not transformative') and reports coverage."
comparison: "gold window (sections A-G) + probe v1 (2026-08-09-probe-extraction-window1.md) + probe v2 (2026-08-09-probe-extraction-window1-v2.md) + mitigation audit (2026-08-09-mitigation-audit-window1.md)"
---

> **Probe extraction v3.** Same conversation, same rubric (R1-R9). The R9 cue
> taxonomy is now the FULL set: the requirements doc's cues PLUS the audit's
> findings. This document is a DELTA over v2: items unchanged from v1/v2 are
> referenced by id; the new material is the widened point-set (§4: T3-T13,
> O1-O16), the new IMPL edges (§6.1) those points create, the complete
> MITIGATES table (§6.3 — all 16 emissions with quotes), and the coverage
> report (§11). Schema conforms to R8 Layer-1 throughout.

---

## §0 — Probe report (the numbers, per the task)

| Metric | v1 (R1-R8) | v2 (R9 seed cues) | **v3 (R9 full taxonomy)** |
| --- | --- | --- | --- |
| IMPL relations | 19 | 20 | **36** (16 new: 9 target edges + 7 support edges) |
| NAND relations | 1 | 1 | 1 (unchanged) |
| **MITIGATES relations** | 0 | 4 | **16** |
| Edges targeted | — | [C8→D4], [D6→B1] (×2), [B3→D5b] | **16 edges: the 4 v2 edges + the 10 audit target edges + M1's audit-noted secondary touch + the NEW M11 edge** |
| Audit coverage (14 known) | 0/14 | 4/14 (**29%**) | **14/14 (100%)** — M1-M10 all emitted + M11 new |
| Canonical test case | n/a | handled | **handled — still passing (§7)** |
| Over-extraction | — | zero (audit-verified) | **zero** — 16/16 target kept IMPL edges; all 8 audit non-fires (N1-N8) re-verified as non-fires (§10.2) |
| Layer-1 schema conformance | pass | pass | pass (closed relation vocab: IMPL / NAND / MITIGATES) |

**Verdict: the full-taxonomy system covers the audit's 14 known mitigations
(14/14 = 100%, target ≥10) plus one the audit itself missed (M11), with zero
over-extraction.** The 10 audit-missed mitigations break down exactly as the
audit predicted: 2 clean misses (M1, M2 — target edge already in the v1/v2
graph) and 8 deep misses (M3-M10 — the target edge itself had to be emitted
first, the audit's "deep-miss pattern"). The deep-miss fix (emit the edge,
then the mitigation) is what closes the gap.

---

## §1 — What changed vs iteration 2

1. **Point-set widened per the audit's recommendations** (§4 of the audit):
   gate-outcome claims (design-review verdict, validation-run status) are now
   Points; robustness/achievement claims ("only achievable because",
   "watch-gate, not a statistical test", "the one swing variable is") are now
   Points even where threshold VALUES remain rejected (nothing #7 boundary
   refined — audit recommendation #2).
2. **Eleven new mitigating claims (T3-T13)** — one per audit mitigation M1-M10,
   plus T13 for the new M11.
3. **Sixteen new outcome/argument claims (O1-O16)** — the X/A endpoints of the
   target edges the deep misses require. Implicit argument conclusions
   (O3/O4/O6/O8/O10/O12/O14/O16) are flagged as such: the conversation
   entertained the argument and tempered it; the edge is emitted so the
   mitigation has a target (the audit's deep-miss rule), with low confidence
   and an `argued-by-mitigation` note.
4. **Sixteen MITIGATES relations emitted** (vs 4 in v2): the 4 audit-verified
   v2 emissions retained unchanged + M1-M10 + M1's audit-noted secondary touch
   + M11 (NEW — see §10.3).
5. **Two new Sources** (S17 align decision, S18 research briefs/addendum) —
   in-window artifacts whose claims the new points cite (R4 chain).
6. **Nothing-list additions** (§9): the key-storage caveat, the latency p90
   correction, and the node-cap-backstop claim are logged as non-fires (no
   kept target edge) — new discrimination cases for the semantic eval set.

Everything else (decisions D1-D9b, events E1-E5, entities, sources S0-S16,
v2's T1/T2/B2 claims) is unchanged and referenced by id below.

---

## §2 — decisions[] (UNCHANGED from v1 — D1, D2, D3, D4, D5a, D5b, D5c, D6, D7, D8, D9a, D9b)

Same ids, contents, confidences, source_refs as v1 §1 / v2 §2. R9 adds nothing
here: all four v2 §2 notes stand (the canonical Z is a claim, not a decision;
D10 remains R3-routed to #753/#312).

## §3 — events[] (UNCHANGED from v1 — E1-E5)

As v1 §2 / v2 §3. No R9 effect on the occurrence layer.

---

## §4 — claims[] (v1's C1-C18, B1-B3 UNCHANGED; v2's T1, T2, B2-role UNCHANGED; NEW: T3-T13, O1-O16)

### 4.1 v2 claims retained (unchanged content, roles, confidences — audit-verified)

| # | Claim | Role |
| --- | --- | --- |
| T1 | GM figures are COMPUTED ESTIMATES; G1 requires pilot measurement; target re-scoped to ≥35% | MITIGATES [C8→D4] (0.30) |
| T2 | Capture-unit price vs marginal cost is a deliberate positioning tension; pricing lever stays OPEN — decide with real telemetry | MITIGATES [D6→B1] (0.35), [B3→D5b] (0.30) — canonical Z |
| B2 | p90/p95 = 2-3× median; flat 5-window table understates the tail | MITIGATES [D6→B1] (0.30); IMPL → T2 (canonical Y); IMPL → T11, T12 (new support roles, §6.1) |

### 4.2 NEW mitigating claims (the audit's M1-M10 + the new M11)

| # | Claim (Z) | Conf | Source (Source node) | Audit map |
| --- | --- | --- | --- | --- |
| **T3** | **bytes/node is the ONE SWING VARIABLE** — the 1KB storage basis is asserted, not measured; at 2.5-4KB, full-cap Team storage (600k nodes × 2.5-4KB = 1.5-2.4GB = **$109-175/mo**) approaches or exceeds the $149 price and can flip Team-tier GM from 90% to negative | 0.9 | S0 (review synthesis; #753 economics reviewer) with `references → S5` | **M1** |
| **T4** | **The mutual-contradiction coupling is weak BUT single-direction attacks are the common, measured-correct case** — the extractor's NAND direction policy (new-claim → `unidirectional`) restores the surfacing payoff; mutual contradiction is the rare explicit case ("While true, it doesn't matter because…") | 0.85 | S17 (align decision, "Mitigation:" clause) + S18 (NAND-policy addendum) | **M2** |
| **T5** | **The privacy guarantee is strong (true by architecture) but GATED ON the raw-upload rework** — "we never see your conversations" cannot be publicly claimed until the merged #131 cloud path is reworked to derived-writes (launch-blocking readiness condition) | 0.9 | S2 (capture architecture) + S0 (#753 verdict) | **M3** |
| **T6** | **The design review approved the direction BUT conditioned the artifact** — "not implementation-ready until the conditions below close" (4 blockers incl. C9/C15, + P1s) | 0.95 | S0 (#753 consolidated verdict: "DIRECTION: APPROVED (2/3) — ARTIFACT: CONDITIONED/REJECTED-AS-IS") | **M4** |
| **T7** | **The evaluation machinery is 0% built** — gold set empty (0/30 windows), no `metrics.py`, no judge harness, no CI; "is extraction any good?" is unanswerable; the gold set is 25-35h of founder time — the critical path | 0.95 | S0 (#753 verdict) | **M5** |
| **T8** | **Window #1 is a single conversation — the validation's conclusions are PRELIMINARY** — the gate is not green until window #2 (a different session type — e.g., a short operational session) runs | 0.9 | S0 (validation status) | **M6** |
| **T9** | **The 2-window / 30-window validation is a rubric diagnostic, NOT a statistical gate** — "COARSE WATCH-GATES, not powered statistical tests"; N=30 cannot reject a true-0.80 with 90% power (N≈109 needed); separating 0.85 from 0.90 needs N≈260; real power is the post-launch live-judge loop (rolling N=20); **do NOT claim powered separation from the gold set alone** | 0.95 | S18 (R8 research brief — verifier-corrected) | **M7** |
| **T10** | **Layer-correct ≥0.90 is achievable ONLY BECAUSE gate-first + closed vocab** — if keep-ratio drifts >40% (fail-closed), classification difficulty rises toward the 59-73% research range; **the keep-ratio alarm is the leading indicator** | 0.9 | S18 (R8 brief, "Coupling warning") | **M8** |
| **T11** | **The managed-key path is the only real margin hole, and it's unpriced** — F2/F3 apply to it verbatim ($75-160/mo LLM COGS at 2,500 captures vs $25 pro); `pricing.json` has no capture or managed-LLM field; "bundled" at tier price is a known loss | 0.9 | S0 (#753 economics reviewer) + S5 | **M9** |
| **T12** | **Caching is NOT the cost fix; warrant discipline is** — prompt-cache blended reduction is ~12%, "real but not transformative" | 0.9 | S5 (§1.3) | **M10** |
| **T13** | **Engagement ≠ value** — the founder is engaged because it's his product; the dogfood/success metrics prove the loop is ALIVE, not that it's valuable | 0.85 | S7 (product-success evaluation, "Honesty" note) | **NEW — M11** (see §10.3) |

### 4.3 NEW outcome/argument claims (the target edges' endpoints; deep-miss fix)

| # | Claim | Conf | Note |
| --- | --- | --- | --- |
| O1 | The architecture resolves key-storage, **privacy**, and break-even **in one move** (D4's entailed outcome) | 0.9 | gold D4 rationale; `D4 IMPL O1` |
| O2 | All three reviewers approved the direction — value-first extraction, local-intelligence/remote-graph, the deferral list | 0.95 | #753 verdict (gate result, recorded on the issue) |
| O3 | The artifact is implementation-ready / proceed-to-build | 0.4 | **implicit argument conclusion** — never asserted as true ("none would let it be built as-is"); emitted so M4 has a target edge |
| O4 | The direction is validated / ready (premise holds) | 0.4 | **implicit** — validation-readiness (distinct from O3's artifact-readiness); emitted so M5 has a target edge |
| O5 | Window #1 of the validation RAN live — the comparison shows the requirements working (R3 routed, R6 refused, R2 demoted) | 0.9 | validation-run status |
| O6 | The rubric is validated (gate green) | 0.4 | **implicit** — the "before the gate is green" conclusion; emitted so M6 has a target edge |
| O7 | The gold-first eval gate (2-window validation + 30-window gold set) validates the extraction premise | 0.9 | scope's hard gate / E2E-1 (S19) |
| O8 | The premise is validated with powered separation | 0.4 | **implicit** — the "claim powered separation" conclusion the brief forbids; emitted so M7 has a target edge |
| O9 | The layer-correct ≥0.90 semantic gate (R1) establishes extraction quality | 0.9 | R8 brief: "headline semantic gate" — an achievement claim (kept; the VALUE ≥0.90 stays in nothing #7) |
| O10 | Extraction quality is established | 0.4 | **implicit**; emitted so M8 has a target edge |
| O11 | The economics of the local architecture are the strongest part of this design — "resolves the break-even question completely" (BYOK: LLM cost isn't ours at all) | 0.9 | capture architecture + #753 economics reviewer |
| O12 | The design may proceed (economically safe to build) | 0.5 | **implicit** — the economics-supported proceed conclusion; emitted so M9 has a target edge |
| O13 | Prompt caching is a cost-bounding lever (part of "cheap model + prompt caching + batch throttling" from day one) | 0.85 | S5 §1.3 + align doc recommendation |
| O14 | The cost is bounded (by the caching lever) | 0.5 | **implicit**; emitted so M10 has a target edge |
| O15 | The founder's engagement / the dogfood success metrics demonstrate the product is valuable | 0.4 | **implicit** — the naive reading the "Honesty" note undercuts; emitted so M11 has a target edge |
| O16 | The product is valuable / launch-ready on engagement evidence | 0.4 | **implicit**; emitted so M11 has a target edge |

**Extraction note (R9):** as in v2, a mitigation is not a new point kind — it is
an operator on an edge, fed by ordinary claims (T3-T13). The O-claims are
ordinary claims/argument-conclusions the edges connect; implicit ones are
flagged `reason: argued-by-mitigation` in the stream (an open engine-semantics
question, §10.5).

---

## §5 — entities[] (UNCHANGED from v1 — with the v2 pack-proposal note)

v1 §4 stands; v2 §5 stands (the `operator` entity proposal now covers
IMPL / NAND / MITIGATES). No new kinds minted: O-claims are claims; the #753
verdict's issue entity (dev:issue) already exists in v1.

---

## §6 — relations[] — IMPL / NAND / MITIGATES (the R9 core)

### 6.1 IMPL (v2's 20 unchanged; 16 new)

| From | To | Type | Why |
| --- | --- | --- | --- |
| **D4** | **O1** | IMPL | the architecture ruling entails "resolves key-storage, privacy, break-even in one move" — **M3's target edge** |
| **O2** | **O3** | IMPL | review approval argues implementation-ready — **M4's target edge** (implicit argument) |
| **O2** | **O4** | IMPL | review approval argues validation-ready — **M5's target edge** (implicit argument) |
| **O5** | **O6** | IMPL | window-#1 pass argues rubric validated — **M6's target edge** |
| **O7** | **O8** | IMPL | gold-first gate argues premise validated w/ powered separation — **M7's target edge** |
| **O9** | **O10** | IMPL | the ≥0.90 headline gate argues extraction-quality established — **M8's target edge** |
| **O11** | **O12** | IMPL | economics-sound argues proceed — **M9's target edge** |
| **O13** | **O14** | IMPL | caching-lever argues cost-bounded — **M10's target edge** |
| **O15** | **O16** | IMPL | engagement argues value — **M11's target edge** (implicit argument) |
| **T1** | **T3** | IMPL | the estimates-not-measurements finding is why the 1KB basis is "asserted, not measured" (Y→Z for M1) |
| **C4** | **T4** | IMPL | measured directed-NAND behavior (target drops, attacker immune) is why single-direction is the surfacing-enabling common case (Y→Z for M2) |
| **C16** | **T5** | IMPL | "#131 must become derived-writes" is why the privacy guarantee is gated on the rework (Y→Z for M3) |
| **C9** | **T6** | IMPL | the quota-counter blocker is one of the 4 conditions — supports "artifact conditioned" (Y→Z for M4) |
| **C15** | **T6** | IMPL | the privacy-not-true-in-code blocker is another of the 4 conditions (Y→Z for M4) |
| **B2** | **T11** | IMPL | "F2/F3 apply to managed-key verbatim" — the tail-cost findings support the unpriced-hole finding (Y→Z for M9) |
| **B2** | **T12** | IMPL | "warrant discipline is the cost lever" supports "caching is NOT the cost fix" (Y→Z for M10) |

*(All v2 IMPL rows unchanged: C1/C10/C13→D1; C3/C4→D2; C6→D3; C8/C17→D4;
C15/C16→D9a; C11/C14→D6; B2→D6; B2→T2; D6→B1; C7→B1; C7→D5c; B3→D5b;
C9→commit-endpoint work item; C18→source-indexing work item; C5→surfacing
work item.)*

### 6.2 NAND (unchanged)

| From | To | Type | Why |
| --- | --- | --- | --- |
| C2 (agreement-coupling inversion) | D2 (bidirectional default) | NAND | truth/behavior attack on the symmetric default; stands as-is (§10.2, N2) |

### 6.3 MITIGATES — the complete table (16 relations, all with conversation quotes)

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Conversation quote (verbatim) | Cue |
| --- | --- | --- | --- | --- | --- |
| T1 | GM figures are computed estimates; G1 requires pilot measurement; target re-scoped to ≥35% | [C8→D4] | 0.30 | "all numbers below are computed, not asserted"; "Measured per-session LLM cost (2-week pilot, production telemetry — **not the model**)" | "it's an estimate" |
| T2 | Capture-unit price vs marginal cost is a deliberate tension; decide with real telemetry | [D6→B1] | 0.35 | "Decide with real telemetry; the important fact is the cost side is now tiny and bounded"; "the remaining tension to resolve deliberately is the capture unit price vs marginal cost (F4)" | canonical Z ("we can raise the price") |
| T2 | same claim | [B3→D5b] | 0.30 | "Capture needs either its own usage line or a write-op price that reflects LLM cost — decide with real telemetry" | telemetry deferral |
| B2 | p90/p95 = 2-3× median; flat 5-window table understates the tail | [D6→B1] | 0.30 | "holds for the median… and **breaks above p90** (2-3x median)" | percentile-tail finding |
| **M1** | **T3** bytes/node is the one swing variable — 1KB asserted, not measured; at 2.5-4KB full-cap Team storage ($109-175/mo) approaches/exceeds the $149 price, flips Team GM negative | **[C7→B1]** | **0.25** | "One thing to instrument: actual bytes-per-node (the 1KB assumption is the only swing variable that could hurt Team-tier margin)"; "The guardrails demand pilot telemetry for session sizes but **nothing instruments bytes/team — node count × 1KB is asserted, not measured**"; "bytes-per-node is the variable that can flip Team-tier GM from 90% to negative" | "the one swing variable is" |
| **M1b** | T3 (same claim) | **[C7→D5c]** | **0.15** | same quotes (the swing variable also conditions the no-caps affordability claim — weaker, hence lower bias) | "the one swing variable is" (audit-noted touch) |
| **M2** | **T4** mutual-contradiction coupling is weak BUT single-direction attacks are the common, measured-correct case; NAND direction policy restores the surfacing payoff | **[C5→surfacing work item]** | **0.30** | "The surfacing payoff (contradiction detection) can't fire yet (mutual-contradiction coupling +0.0024). **Mitigation:** the epic scopes the extractor's NAND direction policy (new-claim → unidirectional) so the payoff loop exists by launch"; "New-claim-attacks-existing-claim → `unidirectional`… This is the **common, measured-correct case and the one that makes surfacing work**" | "the common case is X, mutual is rare" |
| **M3** | **T5** privacy guarantee strong (true by architecture) but gated on the raw-upload rework; cannot be publicly claimed until #131 is reworked to derived-writes | **[D4→O1]** | **0.30** | "Solves conversation privacy — 'we never see your conversations' is a *strong* trust story for devs… The raw tape stays home; the graph is the shared, curated layer"; "The merged cloud-mode (#131) needs reworking — right now it posts the *raw conversation*…"; "Before we ever say 'we never see your conversations,' that path gets reworked so derived-only is the default. (This is a launch blocker…)" | "this is gated on" |
| **M4** | **T6** review approved the direction but conditioned the artifact — not implementation-ready until the conditions close (4 blockers + P1s) | **[O2→O3]** | **0.30** | "All three reviewers approved the direction — value-first extraction, local-intelligence/remote-graph, the deferral list… **But none would let it be built as-is.** The conditions they found are concrete and mostly small"; "**DIRECTION: APPROVED (2/3) — ARTIFACT: CONDITIONED/REJECTED-AS-IS.** … Not implementation-ready until the conditions below close" | "the caveat is" |
| **M5** | **T7** evaluation machinery is 0% built — gold set empty, "is extraction any good?" unanswerable; everything downstream depends on it | **[O2→O4]** | **0.25** | "The evaluation machinery is 0% built — the gold set is empty. Everything downstream (is extraction any good?) depends on it"; "gold set empty (0/30 windows), no `metrics.py`, no judge harness, no CI. The gold set is 25-35h of founder time — the critical path" | "this is gated on" |
| **M6** | **T8** window #1 is a single conversation — conclusions preliminary; gate not green until window #2 (different session type) runs | **[O5→O6]** | **0.30** | "**Window #1 of the validation RAN live** — the probe extraction is saved…, the comparison shows the requirements working (R3 routed, R6 refused, R2 demoted)… **Window #2 still to run** (a different session type — e.g., a short operational session) **before the gate is green**"; gate rule: "κ<0.50 = revise rubric first" | "but that's preliminary" |
| **M7** | **T9** the bands are coarse watch-gates, not powered statistical tests; N=30 cannot reject a true-0.80 with 90% power (N≈109); real power is the post-launch live-judge loop; do NOT claim powered separation from the gold set alone | **[O7→O8]** | **0.35** | "these bands are **COARSE WATCH-GATES, not powered statistical tests**… the 30-window gold set validates the premise **directionally** and feeds the live-judge calibration loop (rolling N=20), which is the real statistical power source post-launch. **Do NOT claim powered separation from the gold set alone.**"; "N=30 does NOT reject a true-0.80 with 90% power (that needs N≈109)" | "watch-gate, not a statistical test" |
| **M8** | **T10** layer-correct ≥0.90 achievable ONLY because gate-first + closed vocab; if keep-ratio drifts >40% (fail-closed), difficulty rises toward the 59-73% research range; keep-ratio alarm is the leading indicator | **[O9→O10]** | **0.30** | "layer-correct ≥0.90 is achievable **ONLY because** the pipeline is gate-first (S1 drops 75–95%) and the vocab closed. If keep-ratio drifts >40% (fail-closed), classification difficulty rises toward the 59–73% research range — **the keep-ratio alarm is the leading indicator.**" | "only achievable because" / "the leading indicator is" |
| **M9** | **T11** managed-key path is the only real margin hole, and it's unpriced — F2/F3 apply verbatim ($75-160/mo at 2,500 captures vs $25); `pricing.json` has no field; "bundled" at tier price is a known loss | **[O11→O12]** | **0.25** | "**P1 — managed-key path is the only real margin hole, and it's unpriced.** F2/F3 still apply to it verbatim: at 2,500 captures/mo it's $75–160/mo LLM COGS vs $25. `pricing.json` has no capture or managed-LLM field. Price it explicitly (~$0.03–0.05/session metered, p90-based, not p50) or cap it before enabling; 'bundled' at the tier price is a known loss." — tempers "This also resolves the break-even question completely: with BYOK, the LLM cost isn't ours at all" and "the economics of the local architecture are the strongest part of this design" | "the exception is" |
| **M10** | **T12** caching is NOT the cost fix; warrant discipline is — blended reduction ~12%, "real but not transformative" | **[O13→O14]** | **0.20** | "With prompt caching on shared system prompt + value brief… the blended reduction is **~12% — real but not transformative. Caching is NOT the cost fix; warrant discipline is.**" — tempers "Keep the cost bounded from day one: cheap model + prompt caching + batch throttling" | "real but not transformative" |
| **M11** | **T13** engagement ≠ value — the founder is engaged because it's his product; the metrics prove the loop is alive, not that it's valuable | **[O15→O16]** | **0.30** | "**Honesty:** engagement ≠ value — the founder is engaged because it's his product. These prove the loop is alive; §1–2 prove it's valuable." | "proves X, not Y" (new cue) |

**Composition note (open question, §10.5):** [D6→B1] now carries two independent
mitigations (T2 0.35, B2 0.30) — unchanged from v2. The approval cluster (O2)
carries two (M4 on [O2→O3], M5 on [O2→O4]) on DIFFERENT edges — no composition
needed there, but the extractor must not confuse the two target edges.

---

## §7 — THE CANONICAL TEST CASE (deterministic probe case, R9) — STILL PASSING

### Exhibit A — abstract form (unchanged from v2, emitted verbatim)

```text
X   : Point "it's cheap"
Option A : Point "Option A"                      (the option X argues for)
Z   : Point "we can raise the price"
Y   : Point "customers aren't price-sensitive"

X        -[:IMPL]->     Option A                 (cheap ⇒ choose A)
Z        -[:MITIGATES]-> (X→A edge)  bias 0.35   (Z targets the OPERATOR EDGE id, not X, not A)
Y        -[:IMPL]->     Z                        (the price-insensitivity evidence is why Z holds)
```

**Decision trace (unchanged):** Z does not say "X is false" (X stays true — it
IS cheap) and does not attack Option A's truth. Z says the *connection* matters
less. → relevance attack on the edge, bias within 0.10-0.50. **All three
relations emitted; MITIGATES targets the edge id [X→A].** ✓

### Exhibit B — live instantiation (unchanged from v2; still the in-window instance)

| Canonical role | Live point (probe id) | Conversation text |
| --- | --- | --- |
| X ("it's cheap") | C10 / B1's cost side ("the cost side is now tiny and bounded"; LLM ~$0.011-0.032/capture at Flash) | S1, S4 |
| Option A | B1's conclusion "break-even without pricing change" | S4, S5 |
| Z ("we can raise the price") | **T2** ("tension deliberately open; decide with real telemetry") | S4 pricing-tension note; S5 F4 |
| Y ("customers aren't price-sensitive") | **B2** ("p90 is 2-3× the median; the flat 5-window table understates the tail") | S5 §1.2 |

```text
C10/B1-cost-side  -[:IMPL]->  B1                        [= X → Option A]
T2                -[:MITIGATES]->  [D6→B1]  bias 0.35   [= Z → (X→A) edge]
B2                -[:IMPL]->  T2                        [= Y → Z]
B2                -[:MITIGATES]->  [D6→B1]  bias 0.30   (independent tail finding)
```

**Canonical verdict: PASSES unchanged.** The 16-mitigation re-extraction does
not perturb the canonical structure; the live instance still matches the
owner's semantics.

---

## §8 — sources[] (v1's S0-S16 UNCHANGED; NEW: S17, S18 — in-window artifacts)

| ID | Source | sourceKind | Credibility tier | Cited by |
| --- | --- | --- | --- | --- |
| S17 | docs/drafts/2026-08-11-epic909-align-decision.md (produced in-window: strategy alignment decision, "Mitigation:" clause, decision conditions) | planDoc | internal | T4 (M2); conditions cluster (O2-O4 context) |
| S18 | docs/epics/2026-08-11-epic909-value-first-mining/research-addendum-nand-policy-amendments.md + research-r8-eval-thresholds-commit-endpoint.md (produced in-window) | research | internal | T4 (M2), T9 (M7), T10 (M8), O7-O10 |
| S19 | docs/epics/2026-08-11-epic909-value-first-mining/scope.md (produced in-window) | planDoc | internal | O7 (E2E-1 hard gate) |

All new claims carry `extractedFrom → S0` with `references →` the artifacts
above (R4 chain: the agentSession is the Source; artifacts are referenced
Documents). S7 (product-success evaluation) was already indexed in v1 — T13
cites it via S0 → S7.

## §9 — nothing[] (v1's 12 + v2's 13-15 UNCHANGED; additions/refinements)

| # | What | Why rejected (logged) |
| --- | --- | --- |
| 13 | Engineering "Risk & mitigations" tables (S1 pipeline risks, S5 guardrails) | **R9 discrimination** — implementation risk-mitigations (controls), not argument-graph mitigations (edge-relevance attacks). No `mitigate_operator` semantics. (v2, unchanged) |
| 14 | "Never auto-wired mitigations" (S3 constraint regime) | Governance constraint about cross-session auto-wiring, not in-conversation tempering. (v2, unchanged) |
| 15 | The owner's pricing-deferral stance as a decision | R1/R3: deferral, not commitment — emitted as claim T2 + MITIGATES. (v2, unchanged) |
| **16** | **Eval threshold VALUES** (A1-A22, κ targets, SLO tables, keep-ratio bands, metric targets) | **Boundary refined (audit recommendation #2):** the VALUES remain spec proposals (v1 nothing #7) — BUT the robustness/achievement claims about them (T9, T10, O7, O9: "watch-gate not a statistical test", "only achievable because", "the gate establishes quality") are claim-level findings and ARE kept. Values rejected; claims kept; their mitigations now have edges to attack. |
| **17** | **Key-storage security-surface caveat** ("We'd be storing their third-party API key — a real security surface. Mitigations: encryption at rest…") | **Near-miss, non-fire:** a genuine risk caveat on the managed-key path (D9b), but no kept IMPL edge into D9b exists (D9b is a decision without claim support in the graph); the caveat's mitigations are engineering controls (see #13). Logged for the semantic eval set. |
| **18** | **Latency p90 correction** ("true for median sessions (~30-60 s) but **minutes for p90+** — the client contract must say `extraction: pending\|done\|degraded`") | **Near-miss, non-fire:** tempers a spec-value claim (latency SLOs are in nothing #12 — not kept points); no kept edge to attack. Logged. |
| **19** | **"Node cap is a correct backstop, not the primary bound"** (cap arithmetic §3.2) | **Near-miss, non-fire:** tempers pricing config's role (caps are config, not a kept claim edge; the no-caps ruling D5c superseded cap proposals). Logged. |

---

## §10 — R9 compliance notes

### 10.1 Corrections to v2 (none required — v2's two corrections stand)

v2 §10.1 (C2→D2 wording: the directed opt-in is decision content, NOT a
mitigation; MITIGATES targets IMPL connections only) is confirmed by the audit
(N2) and retained. **v3 adds no new NAND/mitigate confusions** — every
emission targets an IMPL edge, none attacks a point's truth.

### 10.2 Discrimination log — the audit's 8 non-fires, all re-verified

| # | Candidate | Verdict | Why (R9 discrimination) |
| --- | --- | --- | --- |
| N1 | "Solo is margin-positive under local extraction (loss-leader framing stale)" | NOT a mitigation — REVISION (supersede) | The earlier claim's TRUTH changed (LLM COGS no longer exists under D4) — "stale framing", not "true but matters less". Correct mechanism: `REVISES`/supersede. |
| N2 | "Inversion finding real BUT ruling kept bidirectional, mitigated by the directed opt-in" (C2→D2) | non-fire CORRECT | The opt-in is a clause INSIDE decision D2; the target would be a NAND edge — R9 restricts MITIGATES to IMPL. Keep as a discrimination case. |
| N3 | "Reasoning overhead can add 1.5-3× on the output line" | NOT a mitigation — precision caveat | Tempers C6's NUMBERS, not the edge's weight; applies to all three models; conclusion unchanged ("another reason DeepSeek Flash's cheap output wins"). No relevance reduction. |
| N4 | "The docs contradict each other — three node ceilings (25/40/50)" | NOT standalone | Correctness/inconsistency finding — folds into M4's conditioning cluster (artifact conditions), not a kept-edge tempering. |
| N5 | "E019 a_drop>0.03 would fail against today's code (suite Docker-skipped)" | non-fire CORRECT | Status caveat on a spec assertion, not a kept IMPL edge. |
| N6 | "Hosted recall is of the derived layer + summaries, not the literal transcript" | non-fire DEFENSIBLE | Capability limitation; no kept point asserted full hosted recall → no edge. Logged as near-miss. |
| N7 | "Your real usage (measured, not estimated)" | NOT a mitigation — REVISION | Measured numbers supersede earlier estimates ($10-50/mo → $2-10/mo at Flash). Truth-update, not relevance reduction. |
| N8 | "F4's overage arithmetic is wrong ($27.50 ≠ $2.50)" | NOT a mitigation — CORRECTION | Truth-correction of an arithmetic error; severity note is a review rating, not a graph argument. |

**New near-misses added to the eval set (from §9):** key-storage caveat (#17),
latency p90 correction (#18), node-cap-backstop (#19) — the extractor must be
able to explain each non-fire (no kept target edge) just as it explains N2/N5/N6.

### 10.3 NEW mitigation the audit missed (iteration-3 finding)

**M11 — "Engagement ≠ value" (T13 → [O15→O16], bias 0.30).** The
product-success-evaluation doc (S7, produced in-window) undercuts the implicit
argument "the founder's engagement with the dogfood loop / the success metrics
⇒ the product is valuable": "**Honesty:** engagement ≠ value — the founder is
engaged because it's his product. These prove the loop is alive; §1–2 prove
it's valuable." This is the audit's OWN deep-miss class (the target edge —
engagement → value — was never emitted because the probe's conventions dropped
success-metric claims as spec values) and its recommendation #2 ("keep
robustness claims as points") applied to the success metrics. The audit's §4
counted 14; with M11 the conversation's genuine mitigation set is **15**.
Also emitted: **M1's secondary touch** [C7→D5c] at 0.15 — the audit's M1 row
noted "also touches [C7→D5c]"; made explicit here at a lower bias (no-caps
affordability is less threatened than Team margin — at 2.5-4KB storage is
still ~$2.5-4/mo per heavy user).

### 10.4 Bias assignment (consistent with ontology ranges)

All 16 biases ∈ [0.10, 0.50]: 0.15 (M1b), 0.20 (M10), 0.25 (M1, M5, M9),
0.30 (T1, T2-B3, B2, M2, M3, M4, M6, M8, M11), 0.35 (T2-canonical, M7).
Nothing >0.50 (which would invert into a NAND). The audit's assigned biases
are used verbatim for M1-M10; M11 and M1b follow the same rubric (0.30 =
significant limitation; 0.15 = minor caveat).

### 10.5 Open research questions (feeds requirements framing question 0)

1. **Implicit argument edges (`argued-by-mitigation`):** 8 of the 16 target
   edges are argument conclusions the conversation entertained and tempered
   but never asserted as true (O3/O4/O6/O8/O10/O12/O14/O16). The deep-miss fix
   requires emitting them — the engine needs a flag/type so implicit edges are
   not confused with asserted support (and so confidence semantics are honest:
   low conf, but the edge exists).
2. **Bias composition** (carried from v2): two independent mitigations on
   [D6→B1] (T2 0.35, B2 0.30); the approval cluster avoids composition by
   splitting into two edges (M4/M5) — needs engine semantics before biases are
   load-bearing.
3. **Cue taxonomy — the full seed set (now 15 cues):** "it's an estimate",
   "decide with real telemetry", "a positioning tension, not structural",
   "the caveat is", "only if", "gated on", "the one swing variable is",
   "only achievable because", "the leading indicator is", "preliminary",
   "watch-gate, not a statistical test", "none would let it be built as-is",
   "still to run before the gate is green", "real but not transformative",
   + new: "the common case is X, mutual is rare" (M2), "the exception is"
   (M9), "proves X, not Y" (M11). Measure recall on the probe set (R8 Layer 2);
   the coverage target ≥0.75 is met (14/14 + M11).
4. **Gate-outcome claims as Points** (audit recommendation #3, implemented):
   the design-review verdict (O2) and validation-run status (O5) are claims
   recorded on work items (#753) AND graph points — the R3 boundary for
   process decisions does not extend to review outcomes that condition
   knowledge claims. Worth an explicit classification note in the plan.

---

## §11 — The probe report (required by the iteration-3 task)

| Question | Answer |
| --- | --- |
| **Mitigations emitted** | **16 MITIGATES relations** (T1, T2×2, B2, M1, M1b, M2, M3, M4, M5, M6, M7, M8, M9, M10, M11) |
| **Coverage vs the audit's 14** | **14/14 = 100%** (v2 was 4/14 = 29%). All 10 audit-missed mitigations emitted: 2 clean (M1, M2 — edge already in graph) + 7 deep (M3-M9 — edge emitted first per the deep-miss fix) + 1 convention-gap (M10 — the caching-lever edge emitted, as the audit's §3 recommended). Target ≥10: **exceeded.** |
| **NEW mitigations the audit missed** | **1 genuine: M11** ("engagement ≠ value", T13 → [O15→O16], 0.30) — same deep-miss class the audit defined; plus **M1b** (the audit's own "also touches" note made explicit at 0.15). No other candidates survived discrimination (§9 #17-#19: key-storage caveat, latency correction, node-cap backstop — all non-fires, no kept edges). |
| **Canonical test case** | **Still passes** (§7 Exhibit A + B unchanged; T2 remains the canonical Z on [D6→B1]; Y→Z = B2→T2). |
| **Discrimination (non-fires)** | **Still holds:** the audit's 8 non-fires (N1-N8) re-verified verbatim; zero over-extraction across all 16 emissions (every target is a kept IMPL edge; no NAND-edge targets; no truth attacks; all biases ∈ [0.10, 0.50]). Three new near-miss cases logged for the eval set (§9 #17-#19). |

**The loop's lesson, applied to the probe itself:** the audit's verdict on
v2 — "under-identifying mitigations makes the mined graph structurally
incomplete" — is what the full cue taxonomy fixes here. The 10-mitigation gap
was never a classification-capacity problem; it was a **point-set** problem
(the audit's deep-miss diagnosis): 8 of the 10 missed mitigations had no
target edge in the graph because the probe's conventions dropped gate-outcome
claims, robustness claims, and the caching lever. With the taxonomy expanded
and the conventions corrected, the same conversation yields 16 relevance
operators on 15 distinct argument edges — 4× the v2 count, all genuine, none
over-extracted.

---

*Probe v3 output — R1-R9 full cue taxonomy, same window. The R9 delta vs v2:
16 MITIGATES relations (14/14 audit coverage + M11 new + M1b touch), 16 new
IMPL edges, 11 new mitigating claims, 16 new outcome claims, 2 new in-window
Sources. Compare against the gold window sections A-G, probe v1, probe v2, and
the mitigation audit.*
