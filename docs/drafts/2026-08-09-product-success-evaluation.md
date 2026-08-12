---
title: "DRAFT — Product-Success Evaluation (exploration, NOT approved design)"
type: draft
domain: product
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753 (review gate)"
---

> ⚠️ **EXPLORATION DRAFT.** Filed per product owner direction. See issue #753 for the review gate.

# Product-Success Evaluation — how we know memory matters

**Core philosophy (the delta principle):** memory has value only if its presence changes agent/user behavior. Every success metric is a delta (with-memory vs without, or vs baseline). Any metric without a counterfactual is decorative. Three layers: L0 plumbing (extraction worked — necessary, never success evidence), L1 behavioral delta (the success layer), L2 outcome (trust, retention, activation).

## 1. The "did it matter" battery (mechanism tests)

All scenarios: S1 (decision) → ≥1 consolidation run → S2 (fresh context, same model, no transcript carryover); matched pairs, cross-over, pre-registered rubric, blind grading. No scenario counts unless the control is measured.

| Scenario | Setup | Targets |
| --- | --- | --- |
| 1a. Prior-decision recall | S1 stores structured decision; S2 requires it without restating | Recall-without-rederive ≥90%; re-derivation calls ≥5× fewer than control; time ≤40% of control; exact-match ≥90% |
| 1b. Contradiction surfacing | S2 user introduces claim contradicting S1 decision | Detection ≥90% in 1 turn; explicit resolution ≥70%; **false positives ≤5%** (false alarms kill trust — gate harder than detection) |
| 1c. Answer-from-memory | S1 records D + rationale; t+21 days, ≥10 interleaved sessions | Correct-with-provenance ≥80%; hallucinated rationale ≤10% (no-memory control fabricates ~100%); <2 min |

## 2. The 2-session continuity experiment (compounding claim)

10 calibrated multi-step tasks (S1 analysis/decision, S2 follow-on). Calibration floor: control must show ≥30% decision drift and ≥8 re-derivation calls in pilot. 20 runs, matched pairs, cross-over, fresh context both sessions, 48h gap, same model/seed. **Only difference between arms is the graph.** Plus a third arm (5 tasks): memory vs asking-the-user (autonomy).

| Metric | Treatment | Control |
| --- | --- | --- |
| TCA (time to first rubric-correct output) | ≤40% of control | 100% (re-derives) |
| Repeat-work calls (re-fetching S1 info) | ≤1 | ≥8 median |
| Decision consistency (S2 == S1) | ≥95% | ≤70% (drift floor) |
| Unsurfaced contradiction | ≤2% | ≥20% |
| Question repetition | ≤5% of turns | ≥25% |

Analysis: paired Wilcoxon, medians. **Compounding caveat:** extend 5 tasks to 3-session chains — PASS requires benefit at S3 ≥ S2 (non-decreasing) + rising save/recall telemetry. A 2-session experiment proves recall, NOT compounding.

**Go/no-go:** PASS = ≥4 of 5 metrics at p<0.05, median TCA advantage ≥2×, consistency ≥95%. FAIL if recall is high but consistency isn't (retrieved but wrong) or repeat-work isn't reduced (retrieved but unused).

## 3. Recall quality & point-reuse

- **retrieval** = point returned by a query path (logged: query, rank, confidence) · **reuse** = retrieved point cited in output (provenance makes attribution free) · **effective reuse** = removing the point changes the output — measured by **ablation on a 5% sample** (block top-1 retrieval, diff output).
- Instrumentation: retrieval log · citation parser → point-use ledger · ablation sampler · dismiss events (surfaced memory ignored = noise signal) · **poisoning probe** (inject plausible-but-wrong point into 2% of retrievals).
- Targets: ≥50% of decision points retrieved in 14d; ≥30% of all points in 30d; never-retrieved-in-90d = ROT; reuse ≥50% of retrievals cited; ≥20% of ablated outputs change (effective-reuse floor); dismiss ≤30%; cosmetic citations ≤10%; **0% superseded points in live answers**.
- **Vanity warning:** retrieval rate alone is vanity — only cited-reuse + ablation measure "did it matter."

## 4. Memory-health dashboard (minimal honest version)

Every number maps to a behavior; rot as visible as value; a green-only dashboard is a lie. The 5 weekly numbers: (1) answered-from-memory (attributed outputs); (2) repeat-work avoided; (3) contradictions surfaced/resolved (linked list); (4) rot meter (stale points, drafts never promoted, unresolved NANDs); (5) noise rate (dismissed/total). **"Your memory answered 0 questions this week" is a first-class view, not an error.**

## 5. Retention/engagement proxies (pre-revenue funnel)

Capture ≥80% of sessions · graph-query ≥1.0 read per captured session (write-only graph = museum) · recall ≥0.3 retrieval-into-output per capture by week 2 · **save/recall ratio +25%/week for 4 weeks, plateau ≥0.5 (the compounding leading indicator)** · continuity ≥30% of sessions reference prior session by week 4 · contradiction surfacings ≥0.5/week · point survival ≥70% alive 30d without supersede.

## 6. Founder dogfood loop (weekly 30–45 min)

(1) 15 random extraction samples — ≥85% correct; (2) 10 random ledger mutations — ≥80% correctly typed; (3) every contradiction surfacing + missed-contradiction probe; (4) **recall failures** (re-derivations that shouldn't have happened — highest-value bug class); (5) the 5 dashboard numbers + save/recall trend. Triage: P0 = trivia extraction or false contradictions; P1 = decision-class recall misses; P2 = confidence mis-calibration; P3 = noise.

**Two launch gates:**

- Internal ("beta when X"): 2 consecutive weeks — extraction ≥85%, ≥5 answered-from-memory/wk, ≥3 repeat-work-avoided/wk, ≥2 surfacings with ≥1 acted-on and ≤1 false positive, stale <20%, save/recall rising, synthetic battery green.
- Public ("strangers when Y"): internal green PLUS stranger activation ≤24h, noise ≤30% for new users, value visible without founder domain knowledge. **The founder's green light is necessary but insufficient.**

## 7. Adversarial checks

| Failure | Symptom | Kill rule |
| --- | --- | --- |
| Dead weight (correct, never used) | Retrieval climbs, behavioral delta flat | Ablated-delta <10% for 2 weeks → retrieval path is decorative |
| Recall noise (retrieved but unhelpful) | High retrieval+citation, low delta, high dismiss | Dismiss >40% or poison-acceptance >20% → don't ship surfacing |
| Black-box graph | Answers on faith; "memory said" without "here's why" | Unattributed >0% or surfacing without both sides → fix the invariant |

## 8. Launch in 12 numbers

1–4: synthetic battery green · TCA ≤40% of control · consistency ≥95% · unsurfaced contradiction ≤2%
5–7: effective reuse ≥20% · dismiss ≤30% · 0% superseded in live answers
8–11: extraction ≥85% (2 wks) · ≥5 answered-from-memory/wk · ≥2 surfacings with ≥1 acted-on · save/recall rising ≥0.5
12: stranger activation ≤24h

Internal gate = 1–11; public gate = 1–12. **Any of 5, 6, 7, or 12 red → the product isn't honest enough to sell yet.**

Load-bearing choices: the ablation sampler is the single most honest metric; the false-positive/poisoning/dismiss gates are STRICTER than the positive gates (memory products die from untrusted recall, not missed recall); the two gates separate "proven to work for the builder" from "proven for strangers."
