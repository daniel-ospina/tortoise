# Project Alexandria (Microsoft Research)

> Probabilistic knowledge base construction from unstructured documents. Decommissioned — product (Viva Topics) retired Feb 2025.

---

## 1. Overview

| Field | Value |
|---|---|
| Organization | Microsoft Research Cambridge |
| Started | 2014 |
| Key paper | AKBC 2019 — Best Research Paper Award |
| Lead researcher | John Winn (MSR Cambridge) |
| Papers | ~650 citations |
| Status | **⚠️ Decommissioned** — Viva Topics retired Feb 2025, MSR project page removed (404) |

*Last checked: 2026-07-06*

---

## 2. Product Type

Research project → productized as Viva Topics engine → decommissioned. Probabilistic knowledge base construction: extract entities, facts, and schemas from text without labeled training data using Infer.NET (Bayesian inference). Never a standalone product — internal MS Graph pipeline.

*Last checked: 2026-07-06*

---

## 3. Positioning & Messaging

"From big data to big knowledge" — transform raw enterprise documents into structured, queryable knowledge without human labeling. 97%+ precision claimed. "Eyes-off" privacy-preserving processing.

⚠️ MSR project page removed (404). Data from AKBC papers + MSR blog.

*Last checked: 2026-07-06*

---

## 4. Target Audience

Internal Microsoft — Viva Topics enterprise customers (orgs with 20K+ documents in Microsoft Graph). Academic KB construction community. Never developer-facing.

*Last checked: 2026-07-06*

---

## 5. Business Model & Pricing

Not a standalone product. Priced through Viva Topics ($5/user/month) — now retired. Underlying Infer.NET framework (MIT, open source) still maintained in ML.NET.

*Last checked: 2026-07-06*

---

## 6. Product & Features

3-stage pipeline:
1. **Query Engine** — extract high-probability knowledge snippets from billions of documents
2. **Probabilistic Parser** — unsupervised template matching (thousands of templates)
3. **Probabilistic Inversion** — run generative model backwards via Infer.NET to extract facts

Two Viva Topics roles: Topic Mining (discovery + maintenance) and Topic Linking (cross-source unification).

**Tech stack:** Infer.NET, Microsoft Graph (18T+ resources), Satori reference KG. No public API/SDK.

⚠️ All architecture details from MSR blog post (April 2021). MSR project page removed.

*Last checked: 2026-07-06*

---

## 7. Go-to-Market & Acquisition

Product-led through Viva Topics. AKBC 2019 + 2021 papers. No open-source community, no developer GTM.

*Last checked: 2026-07-06*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| Citations | ~650 (Google Scholar) |
| GitHub | No repo |
| Viva Topics adoption | ⚠️ Unknown — retired before broad adoption data |
| Microsoft Graph scale | 18T+ resources processed |

*Last checked: 2026-07-06*

---

## 9-11. (Condensed — decommissioned project)

**Online presence:** MSR project page 404. MSR blog post live. AKBC papers on OpenReview. No GitHub repo. Infer.NET at `github.com/dotnet/infer` (still maintained).

**Community:** Academic only — 650 citations, no developer community.

**Reception:** Positive academically (Best Paper Award). Negative in retrospect — LLM-based approaches (Copilot, GraphRAG) superseded the probabilistic approach for the same enterprise knowledge use case.

---

## Why This Matters (despite being decommissioned)

Alexandria represents Microsoft's **pre-LLM approach to automated knowledge extraction.** It was elegant — probabilistic inversion of a generative model, 97%+ precision, fully unsupervised, no labeled data. But LLMs made it obsolete. The same pipeline could now be replaced with: `GPT → extract entities and facts from documents → store in graph.`

**Lesson for our epistemic graph:** Probabilistic approaches work for high-precision, narrow-domain extraction. LLMs work for broad, flexible extraction. Our system needs both — LLMs for broad claim extraction (Stream C), probabilistic/Bayesian for confidence propagation (Stream D).

---

## Notes & Sources

- **MSR Blog:** [April 2021](https://www.microsoft.com/en-us/research/blog/alexandria-in-microsoft-viva-topics-from-big-data-to-big-knowledge/) — live
- **⚠️ MSR project page:** 404 — deliberately removed
- **AKBC 2019 paper:** OpenReview `rJgHCgc6pX` — Best Research Paper Award
- **Infer.NET:** `github.com/dotnet/infer` — MIT, still maintained

*Last updated: 2026-07-06*
