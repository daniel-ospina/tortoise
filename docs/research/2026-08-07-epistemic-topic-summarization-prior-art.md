---
title: "Research: Prior-art screening — epistemic topic summarization (settled vs contested retrieval)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise, search-engine, topic-summarization, ontology, falkordb
---

# Prior-Art Screening: Epistemic Topic Summarization (Settled vs Contested Retrieval)

**Date:** 2026-08-09
**Status:** Complete — screening memo; formal patent search still mandatory before filing
**Type:** Prior-art / novelty screening (patent-adjacent)
**Issue:** daniel-ospina/tortoise#598
**Screened invention:** tortoise#592 "Epistemic topic summarization — settled vs contested structure" (shipped, merged via PR #816)
**Sources:** 4 internal (search_engine.py, topic_summarization.py, ep.py, git history: patent drafts v1/v2) + 20 external (Perplexity ×12, Exa ×1 semantic sweep, arXiv/W3C direct)

---

## 1. Bottom Line

The shipped claim direction — **topic-scoped epistemic retrieval that classifies propositions as settled vs contested via EP-propagated Beta posterior variance through first-class typed operator entities** — has **narrow but defensible novelty**, contingent on claiming the **5-element combination**, not any element alone:

1. **Beta-distributed** (not scalar) confidence per claim, persisted as `ep_alpha`/`ep_beta`
2. **Expectation Propagation** (not contractive averaging or constraint solving) as the propagation engine
3. **First-class typed operator entities** (NAND/IMPL as graph nodes with lifecycle: `is_operator`, `status: retracted`)
4. **Variance-threshold classification** into settled / contested / disputed-pair zones (not parity-balance, not scalar cutoffs)
5. **Topic-neighborhood scoping + argument topology output** (retrieval-side epistemic structure, not just inference)

Every individual element has known prior art. The combination — specifically **second-order (variance) classification of a topic neighborhood over EP-propagated Beta posteriors in an operator-typed knowledge graph, exposed as a retrieval primitive** — was **not found in any single prior work** in this screening.

**Strongest overlaps to address in any filing:**
- **Hunter, Polberg, Thimm, Potyka — "Epistemic Graphs"** (AIJ 2020; KR 2018; IJAR 2019): degrees of belief over argument graphs with support+attack constraints — overlaps elements 2–3 semantically.
- **Nikooroo & Engel — "Belief Graphs with Reasoning Zones"** (arXiv 2510.10042): confidence-thresholded balanced subgraphs = a settled-zone concept — overlaps element 4 conceptually, different mechanism.
- **IRCDL 2022 "Expressing Without Asserting"**: explicit undisputed/disputed/settled claim tri-classification (static RDF) — overlaps the *terminology and task*, not the mechanism.

⚠️ **Formal patent search mandatory before filing** — USPTO / Google Patents / Espacenet with the CPC codes in §8. This screening covers the academic/industry literature landscape, not the patent register. Screening is not sufficient for claim drafting.

---

## 2. Reframed Problem Statement

> **Daniel (Premise Labs)** is trying to establish whether Tortoise's shipped topic-scoped settled/contested retrieval (#592) is novel enough to support follow-on patent claims, **but** the field spans multiple disconnected literatures (probabilistic argumentation, subjective logic, belief revision, KG reasoning, NLP controversy/stance detection, agent memory), which results in the risk of either filing claims that read on prior art or under-claiming the strongest position.

**Alternative framings considered:**
- *"How might we verify novelty of the mechanism (propagated variance) rather than the task (contested classification)?"* → The task framing is crowded (controversy detection, EWA, stance); the mechanism framing is where the white space lives.
- *"What if the defensible position is not the engine but the retrieval surface?"* → Topic-scoped epistemic structure as a query primitive (`GET /v1/topics/{topic}/summary`) is not found anywhere in the agent-memory product landscape (Mem0/Zep/Letta/Cognee) — a product-level differentiator even if mechanism claims are narrow.
- *Reverse:* "What if we assume prior art exists and identify the minimal claim that survives?" → Variance-threshold zone classification over EP Beta posteriors with has_ep gating survives (see §7).

**Domain classification:** **Complicated.** Multiple valid frameworks, synthesis across independent bodies of literature (formal argumentation, probabilistic reasoning, semantic web, NLP), bounded scope (screening a fixed claim vector), no emergent behavior. Not Clear (novelty is judgment, not fact), not Complex (no unfolding dynamics).

---

## 3. The Screened Invention (what is actually shipped, post-#592)

Verified by reading the shipped code on origin/main (bb585bb):

| Surface | Location | Notes |
|---|---|---|
| Core classifier | `tortoise/topic_summarization.py` | Topic neighborhood → settled / contested / disputed-pair zones + argument topology |
| Variance source | `tortoise/search_engine.py:654-714` | `_beta_variance(alpha, beta)` = αβ/((α+β)²(α+β+1)); `contested = has_ep AND variance > 0.04`; **has_ep gating**: claims without persisted EP evidence are never classified (uniform Beta(1,1) variance 1/12 ≈ 0.0833 is NOT contestation) |
| EP engine | `tortoise/ep.py` | Expectation Propagation, Beta messages, `get_contested_claims(variance_threshold=0.04)` (ep.py:475) |
| Operator entities | `tortoise/sdk.py:1358` `create_operator` | First-class `is_operator: true` Point nodes, IMPL/NAND, optional direction, lifecycle incl. retraction (#689 retracted filtering, search_engine.py:259,293) |
| Thresholds | `topic_summarization.py:38-45` | settled: mean ≥ 0.7 AND variance < 0.01; contested: variance > 0.04; disputed pair: NAND-connected, both variance > 0.02 |
| Neighborhood retrieval | `topic_summarization.py:136` | staged: topic → Subject/Object name match → incoming `about*` edges → Points → IMPL/NAND operator-chain expansion (max_hops) |
| SDK | `sdk.py:1891` `topic_summarize` | returns `{topic, total_points, significant, contested, disputed_pairs, argument_structure, meta}` |
| MCP tool | `mcp_server.py:909` `tortoise_topic_summarize` | tool registry |
| Hosted REST | `hosted_api.py:1223` `GET /v1/topics/{topic}/summary` | multi-tenant |
| Self-host REST | `selfhost_api.py:189` `GET /topics/{topic}/summary` | |

**Ancestry:** This is a direct descendant of the in-house provisional patent drafts (`git history: docs/patent/provisional.md` v1 2026-07-25, `provisional-v2.md` 2026-08-01 with 23 claims — files since moved to the premise-labs repo, commit `1673f65`). **Claim 11 of v2 — "Contradiction Detection via Variance" — is the direct ancestor of #592.** #592 adds the topic-scoped retrieval + zone classification + argument-topology output layer on top of that claim's mechanism.

---

## 4. Prior-Art Families Screened

### 4.1 Probabilistic abstract argumentation (Hunter, Thimm) — CLOSEST CONCEPTUAL OVERLAP **[HIGH — multiple independent sources: Perplexity + UCL/mthimm primary PDFs]**

- **Epistemic approach to probabilistic argumentation** (Hunter & Thimm, KR 2014 "Probabilistic Argumentation with Epistemic Extensions"): probabilities as *degrees of belief* in arguments; an epistemic extension = arguments with P(A) > 0.5; rationality constraints — e.g. if (a,b) is an attack and P(a) > 0.5 then P(b) ≤ 0.5 — a constraint semantically equivalent to a NAND factor.
- **Probabilities on extensions** (Thimm, JAIR 2017 survey): constellations vs epistemic families.
- **Overlap:** graph-propagated belief over attack/support relations; threshold-based classification (P > 0.5). 
- **Gap:** scalar probabilities (point beliefs), **no Beta distributions, no variance (second-order uncertainty), no EP**, no per-claim settled/contested *zones*, no retrieval/topic scoping.

### 4.2 Epistemic Graphs (Hunter, Polberg, Thimm, Potyka) — STRONGEST SINGLE-WORK OVERLAP **[HIGH — arXiv 1802.07489 + AIJ 2020 + KR 2018 + IJAR 2019, verified via Exa primary sources]**

- Generalizes the epistemic approach: degrees of belief in arguments with **support AND attack** expressed as epistemic constraints; belief *updates* over the graph (KR 2018 "Updating Belief in Arguments in Epistemic Graphs"; Potyka et al. polynomial-time updates in a fragment; IJAR 2019 "Delegated Updates in Epistemic Graphs for Opponent Modelling").
- **Overlap with elements 2–3:** belief propagation through typed support/attack relations over a graph of propositions; updates when new influence arrives.
- **Gap:** constraints are solved/aggregated (typically via non-linear constraint satisfaction or fixed-point iteration), **not EP message passing**; beliefs are scalars, **no Beta posteriors, no variance signal, no variance-threshold classification**; no topic-scoped retrieval primitive; no operator lifecycle.
- **Filing implication:** claims must not be drafted at "propagate belief through support/attack graph" — that reads on epistemic graphs. Anchor on EP + Beta variance + zone classification.

### 4.3 Nikooroo & Engel — "Belief Graphs with Reasoning Zones" — CLOSEST ON THE ZONE CONCEPT **[HIGH — arXiv 2510.10042 (Oct 2025) + 2508.03465 (Aug 2025), both fetched in full]**

- Reasoning zones = **confidence-thresholded, structurally balanced subgraphs** on which classical inference is "safe" despite global contradictions; built by seeded thresholding + Harary-style parity (signed 2-coloring) balance test + greedy repair; contractive damped propagation guarantees a unique fixed point; shock updates re-localize zones.
- Separates credibility (external trust) from confidence (structure-induced).
- **Overlap with element 4:** "confidence-thresholded zones where reasoning is safe" ≈ Tortoise's settled zone. Also uses typed edges (support/contradiction) and updates.
- **Gap (implementation-level):** contractive scalar averaging **vs EP with Beta posteriors**; parity/balance-test-based zone construction **vs per-claim Beta-variance thresholds**; zones are *subgraphs* (balanced regions) while Tortoise classifies *individual claims* + NAND-pair zones; no Beta uncertainty; no topic-scoped retrieval.
- **Filing implication:** the "reasoning zones" concept is published (Oct 2025) — avoid claiming "safe reasoning subgraphs." Differentiate: variance-of-Beta-posterior as the contested signal, claim-level classification, EP.

### 4.4 Jøsang subjective logic — BETA→EPISTEMIC-STATE ANCESTOR **[HIGH — multiple independent sources: UAI 2016 tutorial, Jøsang 2013 book PDF, belief calculus arXiv cs/0606029]**

- Bijective mapping between binomial opinions (b, d, u, a) and Beta PDFs: α = r + Wa, β = s + W(1−a); uncertainty u = 2/(r+s+2). The "vacuous" opinion ↔ Beta(1,1) — the same prior whose variance (1/12) is NOT contestation in Tortoise.
- **Overlap:** Beta-distributed belief with an explicit uncertainty channel (element 1's ancestor).
- **Gap:** no graph propagation mechanism for variance (opinion fusion operators are local algebraic rules); no classification of claims as settled/contested; no topic scoping. Subjective logic *fusion* (consensus, averaging) is a local operator, not a global EP solve.

### 4.5 Beta distributions in KG reasoning — BetaE + KG construction uncertainty **[HIGH — arXiv 2010.11465 + 2405.16929, verified via multiple independent hits]**

- **BetaE** (2010.11465, ICLR 2021): entities/queries embedded as **Beta distributions on [0,1]** with logical operators (AND/OR/NOT as set operations on Beta embeddings) for multi-hop KG reasoning. Proves "Beta in KGs" is known — element 1 alone is not novel in a KG context.
- **Uncertainty Management in the Construction of Knowledge Graphs: a Survey** (2405.16929): source quality + truth inference modeled with **Beta distributions** (sensitivity/specificity/prior truth), inferred via collapsed Gibbs sampling.
- **Gap:** both are *embedding/statistical-inference* approaches — no EP message passing over typed operator entities, no variance-threshold zone classification, no retrieval-side epistemic structure.

### 4.6 KG contradiction / fact verification — FactKG et al. **[HIGH — arXiv 2305.06590 + ACL anthology corroboration]**

- FactKG: 108K claims over DBpedia, fact verification via KG reasoning (one-hop, conjunction, existence, multi-hop, negation). Adjacent: KG contradiction *detection* as a verification task.
- **Gap:** binary verified/refuted labels per claim from a frozen KG — no propagated belief, no uncertainty, no settled/contested zones, no topic scoping.

### 4.7 ATMS / JTMS — SYMBOLIC CONTRADICTION ANCESTOR **[HIGH — Temple/Northwestern/Springer primary sources]**

- Assumption-based truth maintenance: contradiction = nogood (ATMS) or justification-based IN/OUT (JTMS); contexts excluded from inference. Contradiction as first-class is an old idea (Doyle 1979, de Kleer 1986 lineage).
- **Gap:** symbolic, all-or-nothing — no probabilistic beliefs, no variance, no classification.

### 4.8 RDF-star / RDF 1.2 — STATEMENT-LEVEL METADATA **[HIGH — W3C RDF 1.2 Concepts, WG charter]**

- Triple terms + annotations (rdf:reifies) standardize "statements about statements" (provenance, confidence, validity).
- **Gap:** annotation syntax only — no propagation semantics, no derived classification.

### 4.9 SUNAR — RETRIEVAL-SIDE UNCERTAINTY, CLOSEST ON NEIGHBORHOOD AXIS **[MEDIUM — arXiv 2503.17990, NAACL 2025; two independent hits]**

- "Semantic Uncertainty based Neighborhood Aware Retrieval for Complex QA": uses semantic uncertainty to guide neighborhood-aware retrieval.
- **Overlap with element 5:** uncertainty-aware, neighborhood-scoped retrieval.
- **Gap:** uncertainty is used to *filter/rank retrieval*, not to classify retrieved propositions into settled/contested epistemic zones; no belief propagation.

### 4.10 Stance detection (NLP) **[HIGH — survey arXiv 2006.03644 + multiple corroborations]**

- Text-level Favor/Against/Neither classification (surveys; Stance Reasoner 2024; stance-with-explanations 2024).
- **Gap:** surface text classification — no graph propagation, no uncertainty, no claim-zone semantics.

### 4.11 Wikipedia controversy detection — "CONTESTED" TASK IS KNOWN **[HIGH — Dori-Hacohen et al. SIGIR 2016, Bykau et al. CIKM 2015, Contropedia, WikiSym 2012]**

- Classifying articles/sections as controversial via **edit dynamics** (back-and-forth substitutions, dispute tags, talk pages); fine-grained (within-article) controversy localization exists.
- **Filing implication:** the mere *task* "classify claims/topics as contested" reads on this literature. Novelty must be anchored to the *mechanism*: propagated Beta posterior variance, not edit/revision statistics.

### 4.12 "Expressing Without Asserting" (IRCDL 2022) — SETTLED/DISPUTED/UNDISPUTED TRI-CLASSIFICATION **[⚠️ single-source — Exa full-text; verify before citing in a filing]**

- Cultural-heritage RDF: **undisputed / disputed / settled claims** as first-class classes; conjectural graphs for disputed, collapse graphs for settled disputes.
- **Overlap:** the exact settled/contested taxonomy as a data-modeling concept.
- **Gap:** static representation (assertion status), no propagation, no mechanism. Terminology overlap is real — good prior art to cite-and-distinguish in a claim.

### 4.13 Graph Uncertainty (NeurIPS 2024) — CLAIM-LEVEL UNCERTAINTY IN LLM OUTPUTS **[⚠️ single-source — NeurIPS proceedings PDF via Exa]**

- Claim-level uncertainty via graph centrality over response–claim entailment graphs (generalizes self-consistency); uncertainty-aware decoding.
- **Gap:** centrality/consensus statistics over sampled outputs — no belief propagation, no typed operators, no settled/contested zones.

### 4.14 Dynamic Epistemic Logic (DEL) **[HIGH — Stanford Encyclopedia of Philosophy, Internet Encyclopedia of Philosophy, van Ditmarsch et al. survey]**

- Formalizes knowledge/belief change, higher-order belief, plausibility models; adjacent to AGM belief revision.
- **Relationship:** DEL formalizes *belief dynamics* — it does **not** classify propositions as settled vs contested by propagated variance. The issue's characterization "formalizes settled vs contested across belief graph" is a loose mapping: DEL is background theory, not overlapping mechanism.

### 4.15 Markov Logic Networks / probabilistic logic programming (context) **[HIGH — established literature, not re-verified this session]**

- Weighted FOL clauses (can express soft NAND/IMPL), MAP inference / marginal inference via Gibbs or belief propagation over the ground Markov network.
- **Gap:** point-mass or joint-distribution inference over scalar weights — no per-claim Beta posteriors, no variance channel, no zone classification output, no topic-scoped retrieval primitive. Still: any filing that claims "soft logical constraints + approximate inference over a graph" without the Beta/variance/zone specifics would read on MLN.

### 4.16 Agent-memory products (Mem0, Zep/Graphiti, Letta, Cognee) — WHITE SPACE CONFIRMED **[HIGH — multiple independent 2026 comparisons]**

- All four treat memory as storage/retrieval (vectors, temporal KGs with validity windows, episode decomposition). **None** evaluate epistemic status (confidence, contradiction, uncertainty) of stored claims; none propagate belief; none expose settled/contested summaries. Consistent with the provisional patent's Background section (which predates #592).

### 4.17 Additional adjacent families found this session **[⚠️ single-source unless noted]**

- **ClaimFlow** (arXiv 2603.16073, 2026): expert-annotated claim-level epistemic relations (support/extend/qualify/refute/background) across ACL papers; claim lifecycle uncertainty analysis. Text-level annotation + analysis — no propagation. Relevant to future "argument topology" claims only as background. ⚠️ single-source.
- **Certainty classification of scholarly assertions** (Prieto et al., PeerJ 2020): data-driven 3-category certainty classification (high / non-high) via text cues. Task-level overlap with "classify claim certainty"; no graph mechanism. ⚠️ single-source.
- **Probabilistic logic programming / argument strength postulates** (surveyed inside Hunter 2020): postulates for argument weights/strengths — background only.

---

## 5. Element-by-Element Overlap Matrix

| # | Claim element | Closest prior art | Overlap | Differentiator |
|---|---|---|---|---|
| 1 | Beta-distributed confidence | Jøsang subjective logic; BetaE; 2405.16929 | High (individually known in KG/argumentation contexts) | Beta as the *persisted, propagated* object whose **variance is the classification signal** |
| 2 | EP propagation | Epistemic graphs (constraint solving); MLN (BP); Nikooroo (contractive averaging) | Medium | EP with Beta messages + Gauss-Jacobi quadrature moment projection (patent v1/v2); not constraint solving, not averaging |
| 3 | Typed operator entities (IMPL/NAND, lifecycle) | Epistemic graphs (support/attack); Dung-style attack graphs; ATMS nogoods | High (structurally) | First-class operator *nodes* with lifecycle (retraction, direction flag), N-ary — vs edges/constraints |
| 4 | Variance-threshold settled/contested classification | Nikooroo & Engel (confidence-thresholded balance zones); Hunter & Thimm (P>0.5); EWA tri-classification; controversy detection | Medium (concept), Low (mechanism) | **Second-order (variance) thresholding of EP posteriors with has_ep gating** — no work found classifying by posterior variance |
| 5 | Topic-neighborhood scoping + argument-topology output | SUNAR (uncertainty-aware neighborhood retrieval); FactKG (KG verification); Graph Uncertainty | Low-Medium | Epistemic *structure* (settled/contested/disputed + IMPL chains + NAND conflicts) as the retrieval output — not ranking, not verification |

---

## 6. Adversarial Review (disconfirmation-seeking)

**Queries run:** variance-threshold classification limitations; EP failure modes; "settled vs contested claims" academic search (Exa semantic); controversy-detection literature; posterior variance as uncertainty measure.

1. **⚠️ Variance-as-uncertainty is textbook Bayesian UQ** (posterior variance / posterior SD as standard error — ECB WP, Bayesian journals). A claim drafted as "classify by posterior variance" alone is not novel; the defensible specificity is *variance of EP-propagated Beta posteriors over typed operator graphs, at topic scope, with has_ep gating*.
2. **⚠️ The uniform-prior trap is a real weakness that the shipped code already defends**: Beta(1,1) variance (1/12 ≈ 0.0833) exceeds the 0.04 contested threshold yet represents *no evidence*, not contestation. The shipped `has_ep` gate (only claims with persisted EP evidence are classified; search_engine.py:713-714) is essential to the claim — **include it in any filing** or a rejector will run the trivial counterexample.
3. **⚠️ EP is not guaranteed to converge** (damping helps; double-loop guaranteed-convergent variant exists — MLR 2011 Seeger et al.; JMLR 2020 partitioned-data EP). A robustness claim must either avoid "always converges" language or bound the claim to the damped/double-loop variants.
4. **⚠️ The "contested claim classification" *task* is crowded**: Wikipedia controversy detection (edit dynamics), EWA settled/disputed tri-classification, stance detection, certainty classification in scholarly text. Drafting at task level is indefensible; drafting at mechanism level is the defensible position.
5. **⚠️ Scalar-belief propagation is published** (epistemic graphs, reasoning zones, argument strength postulates). The "variance channel" (second-order uncertainty) is the genuine differentiator — no work found that propagates a full Beta posterior and classifies on its variance at topic scope.
6. **⚠️ MLN/BP can encode soft NAND/IMPL semantics** — avoid claiming "soft logical constraints with approximate inference" in isolation; the Beta-posterior + variance-classification + operator-lifecycle specifics are what survive.

**Verdict after adversarial pass:** no disconfirming evidence found for the 5-element combination as a whole. Individual-element novelty: rejected. Combination novelty: **stands (narrow)**.

---

## 7. Novelty Position & Recommended Claim Strategy

**Defensible core (in order of strength):**

1. **Variance-threshold zone classification of EP-propagated Beta posteriors** with has_ep (measured-posterior) gating — settled (mean ≥ 0.7, variance < 0.01) / contested (variance > 0.04) / disputed NAND pairs (both > 0.02). *No prior art found on second-order classification of propagated beliefs.*
2. **Topic-scoped epistemic retrieval primitive** returning settled/contested/disputed structure + argument topology (`GET /v1/topics/{topic}/summary`, MCP `tortoise_topic_summarize`). *No agent-memory product exposes anything like this; SUNAR/Graph Uncertainty use uncertainty only to filter/rank.*
3. **First-class operator entities with lifecycle** (retraction, direction, N-ary) as the propagation substrate — distinguish from edge-based/constraint-based formalisms.
4. The full 5-element combination as a system claim (fallback position).

**Distinguish-in-filing (cite-and-distinguish):** epistemic graphs (Hunter et al.), reasoning zones (Nikooroo & Engel), subjective logic (Jøsang), EWA (IRCDL 2022), controversy detection (Dori-Hacohen et al.).

**Non-goals:** do NOT claim (a) Beta-in-KG (BetaE), (b) KG fact verification (FactKG), (c) "contested claim detection" at task level, (d) EP on factor graphs generically.

---

## 8. Formal Patent Search Protocol (NEXT STEP — mandatory before filing)

This screening is **not** a patent search. Run before drafting:

| Register | Query strategy |
|---|---|
| Google Patents | Combination: `(Beta OR "expectation propagation") AND ("knowledge graph" OR "argumentation") AND variance AND (contested OR settled OR dispute)`; also each element individually |
| USPTO (PatFT/AppFT + PatentsView) | CPC codes below + same keyword combos; review assignees: Microsoft (knowledge graphs + uncertainty), Bosch, IBM (argumentation/QA), Salesforce, Google |
| Espacenet | Same CPC codes; CL (classification) search with G06N5/022 AND G06N7/00 |

**CPC codes:**
- `G06N5/022` — knowledge representation / engineering of knowledge graphs
- `G06N7/00` (incl. `G06N7/01` probabilistic graphical models) — probabilistic computing
- `G06F16/36` — semantic graphs / KG querying
- `G06N5/04` — inference methods (argumentation, logic)
- `G06F16/35` / `G06F16/9032` — document classification / query formulation (retrieval side)
- `G06N20/00` — machine learning (if claiming learned thresholds)

**Review the in-house prior filings:** provisional v1 + v2 (23 claims) live in the premise-labs repo (removed from public repo in `1673f65`). Check for (a) any published versions of these, (b) family/priority options, (c) whether #592's claim layer should be a continuation or a new provisional.

---

## 9. Source Confidence Summary

| Claim | Sources | Tier |
|---|---|---|
| Epistemic approach to probabilistic argumentation (P>0.5, rationality constraints) | Perplexity + UCL/mthimm primary PDFs | High |
| Epistemic graphs (Hunter, Polberg, Thimm, Potyka; AIJ/KR/IJAR) | Exa full-text + mthimm PDFs | High |
| Reasoning zones (Nikooroo & Engel) | arXiv 2510.10042 + 2508.03465 full text (Perplexity + Exa) | High |
| Jøsang subjective logic Beta↔opinion bijection | 3 independent sources (UAI tutorial, book, belief calculus) | High |
| BetaE + KG construction uncertainty (2405.16929) | 2 independent sources + arXiv direct | High |
| FactKG | arXiv + ACL anthology | High |
| ATMS/JTMS contradiction semantics | 4 independent university/publisher sources | High |
| RDF-star / RDF 1.2 annotations | W3C primary docs | High |
| SUNAR (2503.17990) | Perplexity ×2 | Medium |
| Controversy detection (Wikipedia) | 5 independent sources | High |
| EWA undisputed/disputed/settled (IRCDL 2022) | Exa full-text only | ⚠️ single-source — verify before citing in filing |
| Graph Uncertainty (NeurIPS 2024) | Exa full-text only | ⚠️ single-source |
| ClaimFlow (2026), PeerJ certainty classification | Exa single hits | ⚠️ single-source |
| Agent-memory products lack epistemic evaluation | 5 independent 2026 comparisons | High |
| EP convergence caveats | 4 independent sources | High |
| Prior patent drafts v1/v2 (internal) | Repo git history | High (internal) |

**Adversarial queries included:** yes (variance-threshold limitations, EP failure modes, disconfirmation-focused Exa semantic sweep, controversy-detection crowding).

**Limitations:** (1) No patent-register search (deliberate — §8 is the formal protocol); (2) no legal opinion — this is a technical screening memo; (3) IRCDL 2022 and NeurIPS 2024 items rest on single-source retrieval (flagged); (4) some adjacent families (MLN, abstract argumentation classics) cited from established knowledge without fresh retrieval — re-verify exact citations before filing.

---

## 10. Recommendations

1. **Do not file without the formal patent search** (§8). This memo de-risks drafting, it does not clear claims.
2. **Draft claims around the variance-threshold zone classification + has_ep gating + topic-scoped retrieval primitive** — the narrow-but-defensible core. Cite-and-distinguish: epistemic graphs, reasoning zones, EWA, controversy detection.
3. **Preserve the has_ep gate and the documented threshold rationale in code/docs** — they are claim-supporting artifacts (and the uniform-prior trap is the likely first attack vector).
4. **Track the reasoning-zones line of work** (Nikooroo & Engel is Oct 2025; they have follow-ons) and re-screen before any continuation filing.
5. **Update the patent v2 claim set** with a topic-summarization claim layer reflecting #592 (settled/contested/disputed-pair zones, argument topology, MCP/REST surface), or spin a fresh provisional covering the retrieval layer.

---

## Appendix A — Internal Sources

- `tortoise/topic_summarization.py` (shipped #592) — classifier, thresholds, retrieval strategy
- `tortoise/search_engine.py:654-714` — `_beta_variance`, `has_ep` gating, `EpBreakdown.contested`
- `tortoise/ep.py:461-485` — Beta moments (`compute_confidence`, variance 1/12 uniform fallback), `get_contested_claims(0.04)`
- `tortoise/sdk.py:1891` — `topic_summarize` SDK surface
- `tortoise/mcp_server.py:909` — MCP tool
- `tortoise/hosted_api.py:1223` — `GET /v1/topics/{topic}/summary`
- `tortoise/selfhost_api.py:189` — self-host REST
- `docs/ONTOLOGY.md` §3.1-3.2 — operator model, EP confidence, Event→Point
- Git history: `0dca243` (patent v1), `89186dc` (figures/claims), `a58051b` (v2, 23 claims), `1673f65` (moved to premise-labs), `051797d`/`ea9cb64` (#592 implementation + review fixes)
