---
title: "A/B/C context-assembly pre-registration — verbatim source vs epistemic subgraph vs union"
type: data
domain: data
status: live
created: 2026-09-11
updated: 2026-09-11
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: Point, Operator, Session
---

# A/B/C context-assembly pre-registration

**Status:** ⛔ **Pre-registration — written BEFORE data collection.** Metrics, arms,
falsification criteria and the analysis plan below are frozen. Any change after the first
run is a new experiment revision, recorded as a dated amendment, never a silent edit.
**Created:** 2026-09-11
**Related:** #2976 (oracle ceiling = 81%) · #2978 (content-less slots) · #2683 (`[evidence-assembly]` Slice A) · #2820 (memory-substrate map) · #2578 (the measurement)
**Workflow:** `experiment-workflow` (9 stages) — this doc covers stages 1–5; stage 6 is the validation gate.

---

## 1. Why — the question this settles

Two designs are in direct conflict and both have evidence:

| Position | Source | Claim |
|---|---|---|
| **Serve more, structured** | Owner's subgraph thesis; Kumiho/Atlas research | The graph's reasoning state (claims + typed relations + confidence) is *leaner and higher quality* than raw source |
| **Serve less, verbatim** | `[evidence-assembly]` wave §4 (#2080/#2683) | "Adding more relevant evidence to a fixed window DEGRADES answers"; rank-then-admit ≈3–6 items |
| **Verbatim beats derived** | arXiv 2601.00821v3 (controlled ablation) | Verbatim source beats extracted artifacts by **15.9 pts** (LoCoMo) / **22.0 pts** (LongMemEval-S); **union matches chunks; artifacts alone forfeit the gap** |

Our own data sits between them: extracted fragments score **0/52**, raw gold sessions score
**42/52 (81%)** (#2976). The evidence-assembly wave's flooding result was measured **while 61% of
admitted slots were empty operator nodes** (#2978) — so its "less is more" doctrine rests on
contaminated data.

**This experiment is the first measurement that separates the three.** It is also the experiment
nobody has published: no controlled A/B at matched token budgets of a *reasoned* subgraph
(claims + typed IMPL/NAND + propagated credence + supersession) versus raw passages.

## 2. Hypotheses (frozen)

| # | Hypothesis | Directional prediction |
|---|---|---|
| **H1** | **Union beats subgraph-alone** — the epistemic subgraph needs verbatim source to be answerable | C > B, ≥15 pts |
| **H2** | **Verbatim beats subgraph-alone** — the 2601.00821 result, reproduced on our stack | A > B, ≥10 pts |
| **H3** | **Union matches or beats verbatim alone at matched tokens** — the union thesis | C ≥ A − 5 pts |
| **H4** | **Subgraph-alone is sufficient** — would falsify the union requirement | B ≥ A − 5 pts |
| **H5** | **The renderer is the binding constraint, not the graph** — subgraph arm fails for fixable presentation reasons | B's failures are dominated by render/format issues on hand inspection, not by absent claims |

H4 and H5 are the **falsification hypotheses** — the outcomes that would kill or reshape the
thesis. They are stated here so a null result is a finding, not a disappointment.

## 3. Arms (frozen)

All arms answer the **same 52 answerable questions** with the **same frozen reader** and the
**same judge**. Only the context package differs.

| Arm | What the reader receives | Built from |
|---|---|---|
| **A — verbatim** | The gold sessions rendered verbatim (`render_context`), turn text intact | `answer_session_ids` (gold) |
| **B — subgraph** | The epistemic subgraph relevant to the question: claims, rendered **with typed relations** (`C1 IMPLIES C3`, `C4 CONTRADICTS C1`), propagated confidence, entity links (`C2 is about Rovo`), and dates. **No raw turn text.** | Graph traversal from matched points |
| **C — union** | Arm B's subgraph **plus** the verbatim source turns each subgraph claim derives from, linked in place (`C1 ⟵ session 12, turn 4`) | B + provenance edges |

**Arm A is already measured** (#2976: 42/52) and must be **re-run in the same session** as B and C
so that reader/judge/environment are identical. The prior number is a reference, not the
comparison baseline.

**Labeling rule (hard):** a condition that leaks the gold annotation into the context (e.g. the
`has_answer` flag rendered) invalidates that arm. The subgraph must be built from graph content
and the question only — never from `answer_session_ids`.

## 4. Metrics (frozen)

**Primary:** *answerable-correct* — count of correct answers among the 52 non-abstention
questions, judged by the frozen judge.

**Secondary (all pre-registered):**
1. Reader refusal rate per arm (judge-independent signal).
2. Context tokens per arm (mean, median, distinct-value count).
3. Token-normalized accuracy: correct per 1,000 context tokens.
4. Per-class accuracy: interval (n=19), ordering/compare (n=32).
5. **Extraction-presence rate** — for each question, whether the gold evidence has a
   corresponding claim in the graph. Separates "claim absent" from "claim not retrieved".
6. Subgraph size: claims admitted, relations rendered, mean claims/question.

**Judge note:** the official `gpt-4o-2024-08-06` judge routes through the OpenRouter key, which
is currently exhausted (`limit_remaining=0`). If still exhausted at run time, use the
**deterministic variant-aware containment** judge (#2976) applied **identically to all arms and
both runs**, and label every number as judge-substituted. Report strict-containment alongside it
(the multi-variant gold format makes strict containment an undercount: 25/52 vs 42/52).

## 5. Controls (frozen)

| Control | Requirement |
|---|---|
| **Reader constancy** | One pinned reader for all arms and both runs; `assert_reader_constancy` must not abort |
| **Judge constancy** | One judge implementation for all arms |
| **Matched token budget** | All arms capped at the **same** `max_context_tokens`. The union must not win by being handed more. Report the **actual** mean tokens per arm to prove the match |
| **Environment constancy** | Fresh graph namespace per question; same machine, sequential (never two memory-heavy evals at once) |
| **Independence** | No arm's context may be constructed using the gold annotation |
| **Frozen question set** | The #2578 55-Q subset (52 answerable) — not re-selected after results |

## 6. Power analysis (honest)

n = **52** answerable questions, **paired** (same questions across arms) → the correct test is
**McNemar** on discordant pairs, not a two-proportion test.

| Effect to detect | Verdict at n=52 |
|---|---|
| ≥25 pts (e.g. 40% → 65%) | ✅ Adequately powered |
| ~15–20 pts | ⚠️ Borderline — directionally usable, will be reported with CIs and as *inconclusive* if the interval crosses zero |
| <15 pts | ❌ **Underpowered. Will be reported as inconclusive, never as a win.** |

**Pre-registered commitment:** a difference smaller than 15 points will **not** be claimed as a
result in either direction. This is stated now so the analysis cannot reverse it later.

## 7. Falsification criteria (frozen — mandate per `experiment-workflow`)

| # | Condition | Conclusion forced |
|---|---|---|
| **F1** | C ≤ A − 10 pts | **Union thesis rejected.** Verbatim alone is better; the subgraph layer does not earn its tokens. |
| **F2** | B ≥ A − 5 pts | **Subgraph-alone sufficient.** Union is unnecessary complexity; simplify to B. |
| **F3** | All arms ≤ 10/52 | **Void for the assembly question.** The failure is upstream (retrieval/extraction) — the experiment says nothing about assembly. |
| **F4** | C ≥ A + 5 pts | **Union supported.** Proceed to scale on the 133-question census. |
| **F5** | C > B by ≥15 pts **and** C ≥ A | **H1 supported** — the union is the product shape. |
| **F6** | F3 holds **and** extraction-presence (metric 5) is high | The graph contains the claims but retrieval does not surface them → the defect is **query→subgraph selection**, not assembly. |

## 8. Confounds & pre-mortem

**What could make this experiment lie — written before running it:**

1. **A weak renderer makes B lose for fixable reasons.** The subgraph serializer is new code; a
   poor format would reject the thesis wrongly. → *Mitigation:* the stage-6 validation gate
   includes hand inspection of B's rendered context on 5 questions. If the render is malformed,
   **fix it and restart** — never scale a broken design (the E013 anti-pattern).
2. **Token-budget creep.** Union naturally has more material; if it is not capped it "wins" by
   volume. → *Mitigation:* matched cap + report actual per-arm tokens.
3. **Judge strictness masquerading as arm quality.** The variant-aware judge already
   under-counted once (25 vs 42). → *Mitigation:* hand-inspect every disagreement between arms
   on the same question; report strict + variant-aware side by side.
4. **Extraction loss read as assembly failure.** If the wedding claim was never extracted, B
   cannot win regardless of presentation. → *Mitigation:* metric 5 (extraction-presence) is
   measured per question and reported alongside.
5. **Prior-knowledge contamination.** LongMemEval questions can sometimes be answered without
   evidence (the benchmark's "Best Guess" baseline scores 18.8%). → *Mitigation:* a **no-context
   control arm** (question only) on the same 52 questions, to establish the floor. If the floor
   is high, all arms are inflated and the result must be discounted accordingly.
6. **Prerequisite contamination (#2978).** Running before PR #3000 lands means every arm's
   window is ~61% empty slots. → **Hard prerequisite, see §9.**

## 9. Prerequisites (hard gates)

1. **#2978 must be fixed first** — PR **#3000** (`fix/2978-empty-slots`, open) skips content-less
   hits in `assemble_context`. Until it lands, all arms are measured through a window where
   ~61% of slots are empty operator nodes. **Do not run this experiment before it merges.**
2. Decide the OpenRouter budget question (official judge) or accept the labelled deterministic
   judge.
3. The subgraph serializer (arm B) and the union packer (arm C) must exist and pass the
   validation gate.

## 10. Validation gate — stage 6 (1 question, before scaling)

Run `gpt4_4929293a` (known oracle-correct, wedding question) through **all four** arms
(A/B/C/no-context). **STOP and fix if any of:**
- B or C produces an empty or content-less context,
- C's token count is not ≥ B's (union must contain B),
- B's rendered context contains no typed relation line (`IMPLIES`/`CONTRADICTS`),
- C's rendered context contains no provenance link back to a source turn,
- any arm's context contains a leaked gold artifact (`has_answer`, `answer_session_ids`).

Then hand-inspect B's rendered subgraph and confirm it reads as *knowledge*, not as a bag of
sentences. Only then scale to 52.

## 11. Analysis plan (frozen)

1. Paired McNemar per hypothesis (B vs A, C vs A, C vs B), exact test, two-sided, α = 0.05.
2. Report absolute counts, not only percentages: `C 31/52` is a result; `59.6%` invites
   over-reading.
3. Report 95% CIs on every difference. If the CI crosses zero → **inconclusive**.
4. Report the per-class breakdown for interval (n=19) and ordering/compare (n=32) — the classes
   behave differently (#2578: interval 0/19 vs the oracle's 18/19) and an aggregate would hide it.
5. Run the falsification criteria (§7) **before** writing any conclusion. A hypothesis that
   fails is reported as failed.
6. Stage the runs: **52 → (if F4/F5) the 133-question census → (if confirmed) 500**. Never
   skip the intermediate step.

## 12. Limitations (written BEFORE analysis — mandate per `experiment-workflow`)

- **n=52 is small.** Adequately powered only for ≥25-point effects (§6).
- **Judge substitution** if OpenRouter stays exhausted: the deterministic judge is an
  approximation of the official one; absolute numbers are not comparable to published
  LongMemEval results.
- **Oracle-gold construction.** Arm A uses the *gold* sessions, which no real system has. A is a
  ceiling, not an achievable configuration — C vs A therefore understates C's real-world margin.
- **Single corpus, single domain.** LongMemEval-S conversational memory. Conclusions do not
  transfer to document QA without a second corpus.
- **The subgraph serializer is new code.** A first implementation may not represent the
  architecture's best form; a null result measures *this renderer*, not the thesis in principle.
  This is the single largest threat to validity and is why §8.1 exists.
- **Deterministic extraction.** The graph's claim quality is capped by the extractor; arm B
  cannot exceed the knowledge that was actually written.

## 13. Resource estimate

| Item | Cost |
|---|---|
| Reader calls | 52 questions × 4 arms = **208** (+ retries) |
| Judge calls | 208 (0 if deterministic judge) |
| Ingestion | **0** — reuses the #2578 probe graphs on the eval container (`falkordb-eval`, :6380) |
| Wall clock | ~30–60 min for 52×4 at ~2–5 s/reader call, sequential |
| Implementation | subgraph serializer + union packer + 4-arm runner (the real work) |

## 14. What each outcome means for the product

| Outcome | Product decision |
|---|---|
| **F4/F5 (union wins)** | Context = subgraph (reasoning) + verbatim (evidence). Build the union packer as the assembly default. |
| **F2 (subgraph alone wins)** | Simplify — the graph *is* the package; source is only a link. |
| **F1 (verbatim wins)** | The epistemic layer's value is not retrieval-time. Keep it for decisions/auditing, and serve verbatim source to readers. |
| **F3 (all ~0)** | Nothing about assembly matters yet; go fix retrieval (#2976 trace, #2992 lane defects) first. |
| **F6 (claims present, not surfaced)** | The defect is query→subgraph selection — adopt HippoRAG's query→triple linking (+12.5 Recall@5 measured). |

---

*Pre-registration frozen: 2026-09-11. Amendments must be dated and appended, never edited in place.*
