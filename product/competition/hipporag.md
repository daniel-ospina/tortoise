# HippoRAG / HippoRAG 2

> Academic graph-retrieval framework that builds an OpenIE knowledge graph from a corpus and selects the retrieval subgraph with **Personalized PageRank (PPR)**. The leading academic design for single-step multi-hop retrieval. Research system, not a product — MIT-licensed OSS from Ohio State.

---

## 1. Overview

| Field | Value |
|---|---|
| Organization | The Ohio State University (OSU NLP Group) + Stanford + UIUC (HippoRAG 2) |
| Lead authors | Bernal Jiménez Gutiérrez, Yiheng Shu, Yu Su (OSU). HippoRAG 1 also: Yu Gu, Michihiro Yasunaga (Stanford) |
| HippoRAG 1 | NeurIPS 2024 — arXiv:2405.14831 (v1 May 2024, v3 Jan 2025) |
| HippoRAG 2 | ICML 2025 — arXiv:2502.14802 (v1 Feb 2025, v2 Jun 2025) |
| GitHub | `OSU-NLP-Group/HippoRAG` — MIT, created 2024-05-23, active (last push 2026-09-03) |
| Commercial entity | ⚠️ **None found.** No company, product, pricing page, or commercial offering surfaced in any search or repo artifact |
| Status | Research/OSS, actively maintained by a small team (15 contributors total) |

[arXiv:2405.14831](https://arxiv.org/abs/2405.14831), [arXiv:2502.14802](https://arxiv.org/abs/2502.14802), [GitHub](https://github.com/OSU-NLP-Group/HippoRAG) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 2. Product Type

**Academic research system + reference OSS implementation.** Two papers, one codebase (`pip install hipporag`). Python 3.10+, dual-mode:

- **Offline indexing** — LLM (OpenIE) converts a passage corpus into a schema-less open knowledge graph.
- **Online retrieval** — a single PPR run over that graph ranks passages for a downstream LLM reader.

Not a platform, not a managed service, not agent-memory middleware in the Zep/Mem0 sense. There is **no API product, no dashboard, no multi-tenancy, no user model** — the library takes `docs` and `queries` and returns passages. HippoRAG 2 positions it as a "non-parametric continual learning" framework for LLMs (a RAG replacement), rather than as a memory product.

Repo layout (from README): `src/hipporag/` package with `HippoRAG.py` (top-level class), `embedding_store.py`, `rerank.py`; `examples/` with provider-specific demos; `reproduce/dataset/` with paper eval data; `main.py` as the unified experiment entry point.

[GitHub README](https://github.com/OSU-NLP-Group/HippoRAG/blob/main/README.md) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

**Tagline (repo description, verbatim):** "[NeurIPS'24] HippoRAG is a novel RAG framework inspired by human long-term memory that enables LLMs to continuously integrate knowledge across external documents. RAG + Knowledge Graphs + Personalized PageRank."

**Value proposition — HippoRAG 2 (verbatim, repo README):** "HippoRAG 2 is a memory framework for LLMs that recognizes and uses connections in new knowledge, mirroring a key function of human long-term memory. Our experiments show that HippoRAG 2 improves associativity (multi-hop retrieval) and sense-making (the process of integrating large and complex contexts) in even the most advanced RAG systems, without sacrificing their performance on simpler tasks."

**Efficiency claim (verbatim):** "Like its predecessor, HippoRAG 2 remains cost and latency efficient in online processes, while using significantly fewer resources for offline indexing compared to other graph-based solutions such as GraphRAG, RAPTOR, and LightRAG."

**Paper framing (verbatim, HippoRAG 1 abstract):** "HippoRAG synergistically orchestrates LLMs, knowledge graphs, and the Personalized PageRank algorithm to mimic the different roles of neocortex and hippocampus in human memory."

**Brand voice:** Academic. Neurobiological analogy is the marketing surface — hippocampal indexing theory, pattern separation/completion, neocortex/hippocampus/parahippocampal regions mapping. Every design choice is justified by a cognitive-science reference. Results framing is comparative-benchmark ("beats NV-Embed-v2"), not product-positioning.

**Comparative posture:** Positions against *other RAG methods*, not against commercial memory products. Baselines named in HippoRAG 2: BM25, Contriever, GTR, GTE-Qwen2-7B, GritLM-7B, NV-Embed-v2, RAPTOR, GraphRAG, LightRAG, and its own predecessor HippoRAG 1. No "vs Mem0" / "vs Zep" pages exist — no GTM layer.

[GitHub README](https://github.com/OSU-NLP-Group/HippoRAG/blob/main/README.md), [arXiv:2405.14831](https://arxiv.org/abs/2405.14831) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

**Primary:** NLP/IR researchers and graduate students working on multi-hop QA, graph-augmented RAG, and long-term memory for LLMs. Secondary: engineers evaluating graph retrieval as a drop-in RAG upgrade.

| Segment | Details |
|---|---|
| Personas | RAG researchers, IR/QA academic groups, LLM memory researchers, ML engineers scoring graph-RAG approaches |
| Use cases | Multi-hop QA over a document corpus (MuSiQue/2Wiki/HotpotQA-shaped), long-discourse QA (NarrativeQA), continual-learning simulation (corpus expansion), memory benchmarking |
| Stack assumptions | Owns an LLM (openai SDK 3.x, Azure OpenAI, Amazon Bedrock, OrcaRouter, or local vLLM), an embedding model (NV-Embed-v2, GritLM, Contriever), and Python 3.10 |
| Not designed for | Consumer chat memory, multi-tenant SaaS, real-time agent turn-by-turn memory, any workflow needing per-fact provenance or claim-level truth maintenance |

**Evidence of audience shift toward memory benchmarking:** HippoRAG 2 is now used as a *standing baseline* in agent-memory papers — e.g., SEEM (arXiv:2601.06411v2, Feb 2026) benchmarks against it on LoCoMo/LongMemEval; MemoryAgentBench (arXiv:2507.05257) includes "HippoRAG-v2" rows. This means the audience is expanding from "multi-hop QA" into "agent memory" — a space it was not built for.

[arXiv:2601.06411v2](https://arxiv.org/html/2601.06411v2) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

**N/A — academic OSS, no commercial offering.** MIT license, `pip install hipporag`, no paid tier, no hosted service, no support contract. No pricing page or company entity exists to fetch (⚠️ gap: verified absent, not merely unfound — repo README has no commercial links, no homepage beyond the arXiv paper).

**The real cost surface is compute, not license fees.** Because that is the only "price" a user pays, the paper's own cost table is the closest thing to a pricing page:

*Offline indexing + QA cost on the MuSiQue corpus (11,656 passages), Llama-3.3-70B-Instruct, 4×H100. Percentages are relative to HippoRAG 2 = 100%.*

| Metric | NV-Embed-v2 (dense RAG) | HippoRAG 1 | HippoRAG 2 | GraphRAG | LightRAG |
|---|---|---|---|---|---|
| Input tokens | – | 9.2M (100%) | 9.2M (100%) | 115.5M (1255%) | 68.5M (745%) |
| Output tokens | – | 3.0M (100%) | 3.0M (100%) | 36.1M (1203%) | 18.3M (610%) |
| Indexing time | 12.1 min (12%) | 57.5 min (58%) | 99.5 min (100%) | 277 min (278%) | 235 min (236%) |
| QA time / query | 0.3 s (25%) | 0.9 s (75%) | 1.2 s (100%) | 10.7 s (892%) | 13.3 s (1008%) |
| QA GPU memory | 1.7 GB (17%) | 6.0 GB (61%) | 9.9 GB (100%) | 3.7 GB (37%) | 4.5 GB (46%) |

Paper's own reading (verbatim): "HippoRAG 2 not only outperforms these RAG methods in QA and retrieval performance but also uses much fewer tokens compared to LightRAG and GraphRAG… HippoRAG 2's use of fact embeddings does increase its memory requirements compared to our baselines, however, we believe that this is an acceptable tradeoff."

⚠️ HippoRAG 2 needs **5.8× the QA GPU memory of dense RAG** (9.9 GB vs 1.7 GB) and **8.3× the indexing time** — the cost claim is relative to *other graph methods*, not to plain vector RAG.

**Packaging friction (verified):** PyPI `hipporag` latest is **2.0.0a4** (2025-06-24), while the repo README documents **2.0.0a5**. The repo has 162 commits with a last push of 2026-09-03, but only **1 GitHub release (v1.0.0, 2025-02-27)** and no release since. Installed-package users are ~14 months behind `main`.

[arXiv:2502.14802v2 Appendix F](https://arxiv.org/html/2502.14802v2), [PyPI](https://pypi.org/pypi/hipporag/json), [GitHub API](https://api.github.com/repos/OSU-NLP-Group/HippoRAG) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 6. Product & Features

### 6.1 The mechanism, in the papers' own words

**HippoRAG 1 — graph construction.** "Our offline indexing phase, analogous to memory encoding, starts by leveraging a strong instruction-tuned LLM, our artificial neocortex, to extract knowledge graph (KG) triples. The KG is schemaless and this process is known as open information extraction (OpenIE)." Two-step prompting: named entities first, then those entities are fed into a second prompt to extract triples (which include non-named-entity concepts). Then: "we use *M* to add the extra set of synonymy relations *E′* when the cosine similarity between two entity representations in *N* is above a threshold τ."

**HippoRAG 1 — subgraph selection.** "After the query nodes *R_q* are found, we run the PPR algorithm over the hippocampal index, i.e., a KG with |N| nodes and |E|+|E′| edges (triple-based and synonymy-based), using a personalized probability distribution … in which each query node has equal probability and all other nodes have a probability of zero." Then: "we aggregate the output PPR node probability over the previously indexed passages and use that to rank them for retrieval."

**Node specificity (HippoRAG 1).** An IDF-like per-node weight: `s_i = |P_i|^-1`, where `P_i` is the set of passages node *i* was extracted from. Applied by "multiplying each query node probability with s_i before PPR."

**HippoRAG 2 — three additions.** (1) *Dense-sparse integration*: "each passage in the corpus is treated as a passage node, with the context edge labeled 'contains' connecting the passage to all phrases derived from this passage." (2) *Deeper contextualization*: query links to **triples**, not nodes — "By default, HippoRAG 2 adopts the query-to-triple approach." (3) *Recognition memory*: "We use LLMs to filter retrieved T and generate triples T′ ⊆ T" — an LLM binary relevance filter on the retrieved triples before PPR seeding.

**HippoRAG 2 — the PPR run.** "All passage nodes are also taken as seed nodes, as broader activation improves multi-hop reasoning. Reset probabilities are assigned based on ranking scores for phrase nodes, while passage nodes receive scores proportional to their embedding similarity, adjusted by a weight factor." The weight factor is **0.05** — passage-node reset probability is scaled down 20×, because "concept-level signals are far more crucial."

### 6.2 What the subgraph actually is (the part that matters for us)

| Element | HippoRAG 1 | HippoRAG 2 |
|---|---|---|
| Nodes | phrase nodes (noun phrases from OpenIE) | phrase nodes **+ passage nodes** |
| Relation edges | OpenIE `(s, r, o)` triples, undirected, weight 1 | same |
| Synonym edges | embedding cosine ≥ τ=0.8, weight = similarity | same |
| Context edges | none | `passage —contains→ phrase`, weight 1 |
| Seed selection | query NER → top-similarity nodes | query → top-k **triples**, LLM-filtered → up to k phrase nodes + **all** passage nodes |
| Ranking signal | PPR over phrase graph, aggregated to passages via the `\|N\|×\|P\|` occurrence matrix **P** | PPR over phrase+passage graph, passage nodes ranked directly |
| Returned to reader | **top-ranked passages** (text), not triples, not nodes | **top-ranked passages** |
| Serialization | retrieved passages concatenated before the query for the LLM reader | same |

**Answer to the prompt question:** HippoRAG returns **passages** — not nodes, not triples. Triples are an internal seeding/filtering device. This is the key architectural fact: the reasoning structure (the graph) is a *scoring instrument*, and the object delivered to the LLM is unstructured text.

### 6.3 Hyperparameters

HippoRAG 1: synonymy threshold τ = 0.8, PPR damping factor 0.5 (tuned on 100 MuSiQue training examples). HippoRAG 2: passage-node reset weight factor 0.05; recognition-memory filter prompt auto-tuned with DSPy MIPROv2.

### 6.4 Infrastructure surface

- **LLM providers:** OpenAI, Azure OpenAI, Amazon Bedrock (LiteLLM route + Bedrock Mantle), OrcaRouter gateway, local vLLM.
- **Vector stores:** Parquet (default), Qdrant, ChromaDB, Milvus.
- **Index integrity (2.0.0a5):** persisted vectors and OpenIE state are bound to endpoint/deployment/model/normalization identity via `index_manifest.json`; mismatched indexes are **rejected rather than mixed silently**. Explicit "do not copy or fabricate only the manifest" warning.

### 6.5 Reported results — HippoRAG 1 (single-step retrieval, R@2 / R@5)

| Retriever | MuSiQue | 2Wiki | HotpotQA | Avg |
|---|---|---|---|---|
| ColBERTv2 (best baseline) | 37.9 / 49.2 | 59.2 / 68.2 | **64.7 / 79.3** | 53.9 / 65.6 |
| HippoRAG (Contriever) | **41.0 / 52.1** | **71.5 / 89.5** | 59.0 / 76.2 | **57.2 / 72.6** |
| HippoRAG (ColBERTv2) | 40.9 / 51.9 | 70.7 / 89.1 | 60.5 / 77.7 | 57.4 / 72.9 |

QA (F1, ColBERTv2 reader): MuSiQue HippoRAG 29.8 vs IRCoT 30.5 vs dense 26.4 — **HippoRAG loses to IRCoT on MuSiQue**; 2Wiki 59.5 vs 45.1; HotpotQA 55.0 vs 58.4. Note HippoRAG **underperforms the dense baseline on HotpotQA retrieval** and the authors attribute this to HotpotQA's "lower knowledge integration requirements." Abstract claims: "outperforms the state-of-the-art methods remarkably, by up to 20%"; "10-30 times cheaper and 6-13 times faster" than IRCoT.

### 6.6 Reported results — HippoRAG 2 (F1, Llama-3.3-70B reader, NV-Embed-v2 retriever)

| Metric | HippoRAG 2 | NV-Embed-v2 (best baseline) | Δ |
|---|---|---|---|
| NQ (factual) | 63.3 | 61.9 | +1.4 |
| PopQA (factual) | 56.2 | 55.7 | +0.5 |
| MuSiQue (multi-hop) | 48.6 | 45.7 | +2.9 |
| 2Wiki (multi-hop) | 71.0 | 61.5 | +9.5 |
| HotpotQA (multi-hop) | 75.5 | 75.3 | +0.2 |
| LV-Eval (multi-hop) | 12.9 | 9.8 | +3.1 |
| NarrativeQA (sense-making) | 25.9 | 25.7 | +0.2 |
| **Average** (as printed) | **59.8** | **57.0** | **+2.8** |

⚠️ **The paper's "Avg" column does not reconcile with its own per-dataset values** — do not recompute or lean on it. Verified against the raw table markup (9 cells = label + 7 datasets + Avg, so this is not an extraction misalignment): the mean of HippoRAG 2's 7 displayed F1 values is **50.5**, but the table prints **59.8**; NV-Embed-v2's displayed values mean **47.9**, but the table prints **57.0**. The gap is ~9 points and roughly systematic across rows, which suggests the Avg is computed over a wider dataset set than Table 2 displays (the paper defers detailed results to Appendix C). The **per-dataset numbers above are the reliable figures**; the averages are quoted because the paper prints them, not because they are internally consistent. Note also that the paper's headline "7 point improvement in associativity" does not equal the mean of its own four multi-hop deltas (+2.9, +9.5, +0.2, +3.1 → mean +3.9).

Recall@5 averages (Table 3 — these *do* reconcile cleanly): HippoRAG 2 **78.2** | NV-Embed-v2 73.4 | **HippoRAG 1 (reproduced) 63.8**. (HippoRAG 1 is *9.6 points worse* than plain dense retrieval on this broader suite — the deterioration HippoRAG 2 was built to fix.) 2Wiki Recall@5: 90.4 vs 76.5 (+13.9). MuSiQue Recall@5: 74.7 vs 69.7 (+5.0).

### 6.7 Ablations (HippoRAG 2, avg Recall@5 over MuSiQue/2Wiki/HotpotQA)

| Config | Avg R@5 | Reading |
|---|---|---|
| Full HippoRAG 2 | **87.1** | — |
| w/ NER-to-Node (HippoRAG 1 style) | 74.6 | **−12.5** — query→triple is the single largest design win |
| w/ Query-to-Node matching | 59.6 | −27.5 — granularity mismatch is catastrophic ("queries and KG nodes operate at different levels of granularity") |
| w/o passage nodes | 81.0 | −6.1 |
| w/o LLM triple filter | 86.4 | −0.7 |

**Dense-retriever flexibility** (MuSiQue R@5, Table 7): GTE-Qwen2-7B 63.6→68.8, GritLM-7B 66.0→71.6, NV-Embed-v2 69.7→74.7. The gain is a property of the graph layer, not the encoder.

⚠️ **One honest counterpoint in the ablation:** on 2Wiki specifically, the *HippoRAG 1 style* NER-to-Node linking scores **91.2 vs the full HippoRAG 2's 90.4** — it beats the final design on that dataset. The +12.5 average gain is driven almost entirely by MuSiQue (53.8 → 74.7) and HotpotQA (78.8 → 96.3). HippoRAG 2's query→triple linking is therefore not universally better; it is a large win on questions whose semantics don't reduce to entity matching, and marginally negative on 2Wiki's entity-centric design — the same entity-centricity the HippoRAG 1 paper credited for its own 2Wiki strength.

### 6.8 Third-party memory benchmarks (not run by the authors)

From SEEM (arXiv:2601.06411v2, Feb 2026), Table 1:

| System | LoCoMo BLEU-1 | LoCoMo F1 | LoCoMo J | LongMemEval Acc |
|---|---|---|---|---|
| NV-Embed-v2 (dense) | 53.0 | 57.9 | 74.7 | 58.4 |
| Mem0 | 34.2 | 43.3 | 54.1 | 56.7 |
| **HippoRAG 2** | **53.8** | **58.3** | **76.2** | **60.6** |
| SEEM (published later) | 56.1 | 61.1 | 78.0 | 65.0 |

⚠️ These are **third-party numbers under SEEM's harness**, not the authors' own, and SEEM's own paper is motivated by beating HippoRAG 2 ("surpasses HippoRAG 2 by an absolute margin of 4.4% on LongMemEval"). Treat as directionally informative only. HippoRAG 2 barely clears dense retrieval on LoCoMo (+0.4 F1) — a meaningful signal about how thin the graph advantage is outside Wikipedia-style multi-hop QA.

### 6.9 Standout features vs notable gaps

**Standout:** single-step multi-hop retrieval (no iterative LLM loop online); the query→triple linking trick (+12.5 R@5 over NER→node); offline cost far below GraphRAG/LightRAG; retriever-agnostic.

**Gaps (as a memory system):** no per-claim confidence or credence; no typed logical relations (no implication, no contradiction); no temporal validity (temporal triples are documented as *missed* by OpenIE); no claim-level provenance to a source turn (only passage containment); no multi-tenant/user model; no update/supersede semantics — the index is bound to its build-time configuration and rejected on mismatch, requiring a full re-index; no online write path in the OSS library.

[arXiv:2405.14831v2](https://arxiv.org/html/2405.14831v2), [arXiv:2502.14802v2](https://arxiv.org/html/2502.14802v2), [GitHub README](https://github.com/OSU-NLP-Group/HippoRAG/blob/main/README.md), [arXiv:2601.06411v2](https://arxiv.org/html/2601.06411v2) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

**Academic distribution, not a GTM motion.** There is no funnel to analyze in commercial terms. The actual acquisition surfaces:

| Channel | Signal |
|---|---|
| Conference publication | NeurIPS 2024 + ICML 2025 — the acceptance *is* the distribution event |
| Open-source repo | MIT license, `pip install hipporag`, README-first docs with runnable `examples/` per provider |
| Paper-driven adoption | Used as a standing baseline in follow-up memory papers (SEEM, MemoryAgentBench) — researchers must run it to publish against it |
| Cloud-vendor reference architecture | **AWS published a first-party implementation guide (2026-07-01)** — HippoRAG on Bedrock + Neptune + Neptune Analytics PPR + Titan Embeddings, framed as "enterprise-scale applications" |
| Unsolicited ecosystem contributions | Third parties build on it unprompted (issue #191 attaches a "Memory Safety Net" persistence-hardening module; issue #178 attaches a competing multi-component scoring ablation) |

**Sales motion:** none. **Partnerships:** none formal — the AWS blog is a technical how-to by AWS staff (Tanay Chowdhury, Saeideh Shahrokh, Yingwei Yu), not a partnership announcement. ⚠️ No evidence AWS supports or maintains the HippoRAG repo.

**The AWS artifact is the single most commercially interesting signal in this profile** — it is a hyperscaler showing enterprises how to run HippoRAG's PPR pattern on managed graph infrastructure (Neptune Analytics executes Personalized PageRank natively). That is the closest thing to productization, and it was done *to* HippoRAG, not *by* it.

[AWS ML Blog, 2026-07-01](https://aws.amazon.com/blogs/machine-learning/hipporag-neurobiologically-inspired-rag-using-amazon-bedrock-amazon-neptune-and-personalized-pagerank/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| GitHub stars | **3,999** (repo page renders "4k") |
| GitHub forks | 427 |
| Contributors | 15 (top: yhshu 60 commits, bernaljg 47, YiboZhao624 19) |
| Watchers | 28 |
| Total commits | 162 |
| Open issues | 8 |
| Last push | 2026-09-03 (active — commits within 8 days of this check) |
| Created | 2024-05-23 |
| Releases | 1 (v1.0.0, 2025-02-27) — tag list contains only `v1.0.0` |
| PyPI latest | `2.0.0a4`, 2025-06-24 (⚠️ README documents 2.0.0a5; PyPI lags `main` by ~14 months) |
| ⚠️ PyPI downloads | Not retrieved — pypistats.org returned HTTP 429 on two attempts |
| Citations — HippoRAG 1 | **387 (Google Scholar)**, 320 (Semantic Scholar), 90 (OpenAlex, primary record) |
| Citations — HippoRAG 2 | ⚠️ **Unverified.** A Perplexity snippet claimed "cited by 427" but that number is identical to the repo's fork count and arXiv does not publish citation counts — treated as unreliable and not reported as fact |
| Enterprise deployment | AWS first-party implementation guide (2026-07-01), AWS Bedrock + Neptune + Neptune Analytics |
| Production customers | ⚠️ None named anywhere. No case studies, no logos, no "used by" section |
| Commercial product | None |

**Reading the traction honestly:** ~4K stars and ~390 citations (HippoRAG 1) is strong for a 2024 academic system — top-decile research visibility. But the *shape* is academic: citation velocity without revenue, deployment guides without deployments, stars without a release cadence. Two years post-NeurIPS there is one tag and one PyPI line that is 14 months stale.

[GitHub API](https://api.github.com/repos/OSU-NLP-Group/HippoRAG), [PyPI](https://pypi.org/pypi/hipporag/json), [AWS ML Blog](https://aws.amazon.com/blogs/machine-learning/hipporag-neurobiologically-inspired-rag-using-amazon-bedrock-amazon-neptune-and-personalized-pagerank/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 9. Online Presence & Content

**Primary online channels:** GitHub repo (the de-facto homepage — the API `homepage` field points back at the arXiv paper), the two arXiv papers, Hugging Face paper pages (`huggingface.co/papers/2502.14802`), OpenReview (NeurIPS entry `hkujvAPVsg`), ICML virtual poster page, ACM DL entry (ICML proceedings, `10.5555/3780338.3781176`).

**Content strategy:** README-as-documentation. It is unusually thorough for an academic repo — per-provider quickstarts (OpenAI, Azure, Bedrock, Bedrock Mantle, OrcaRouter, vLLM), vector-store backends, index-migration warnings, optional extras (`.[vllm]`, `.[gritlm]`, `.[milvus]`), and an OpenAI SDK compatibility test module. This is *engineer-grade* documentation — the content effort goes into making the library runnable, not into marketing.

**SEO / organic presence:** No commercial SEO surface — no landing page, no comparison pages, no blog. Third-party content dominates the search surface (Emergent Mind topic pages, Paper Notes summaries, Medium explainers, `clawbot.ai` wiki, mirror repos). ⚠️ Domain-authority and traffic metrics are N/A for an academic project with no domain.

⚠️ Third-party mirror/fork repos (`qiweijian/HippoRAG`, `markmbain/OSU-NLP-Group-HippoRAG`) surface in search and carry LangChain-integration notes that the canonical repo does not — legacy HippoRAG 1 forks. Do not mistake these for the maintained project.

[GitHub README](https://github.com/OSU-NLP-Group/HippoRAG/blob/main/README.md), [Hugging Face](https://huggingface.co/papers/2502.14802) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 10. Community & Ecosystem

| Channel | Size | Engagement notes |
|---|---|---|
| GitHub stars | 3,999 | 427 forks — active forking is the strongest community signal |
| GitHub contributors | 15 | Two core maintainers (yhshu, bernaljg) account for 107 of 162 commits; long tail of 13 |
| Watchers | 28 | Very low — 0.7% of stargazers. Reads, not follows |
| Open issues | 8 | Low volume; issues are substantive (see below), not support noise |
| Discord / Slack | ⚠️ None found | GitHub Issues is the only community channel. Issue #191 notes "Discussions are disabled on this repo" |
| Mailing list / forum | None | Direct author email in README |

**Community mechanics:** Fork-and-extend, not ask-and-answer. The issue list is dominated by third parties proposing *capabilities* rather than reporting bugs:

- **#154 (2025-08-09, open)** — "HippoRAG Overweights Triple Similarity, Retrieving Evidence-Poor Chunks": a user reports HippoRAG performing "even worse than standard vector retrieval" on unevenly-dense corpora.
- **#178 (2026-05-10, open)** — third-party ablation of PPR-only ranking (same KG, ranking formula from the paper) reporting **recall@10 = 0.565 for PPR-only vs 0.819 for naive cosine** on HotpotQA dev (N=500); the poster's cosine×PPR hybrid scored 0.856.
- **#181 (2026-06-11, open)** — RFC: "Optional Structured Relation Dynamics for HippoRAG Retrieval," asking for a layer that exposes "how retrieval signals support, conflict, weaken, or require downstream verification."
- **#191 (2026-08-06, open)** — unsolicited persistence-hardening module ("crash-proof recovery & EDR-safe atomic writes") contributed as a linked repo, framed as hardening HippoRAG's "persistence layer for real deployments."

⚠️ #178 is a self-reported third-party ablation, not peer-reviewed and not run on the authors' harness — cite it as a directional signal about PPR-only ranking, not as a validated result.

[GitHub Issues API](https://api.github.com/repos/OSU-NLP-Group/HippoRAG/issues?state=all) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

**Sources checked:** GitHub open/closed issues (all, via API), repo README, the two papers' own error-analysis appendices, third-party papers that benchmark against HippoRAG 2, Perplexity-surfaced discussion. ⚠️ No review-site presence (no G2/Capterra/Product Hunt) — research OSS, so none expected.

**What users praise:**
- The PPR-over-open-KG formulation itself — clean enough that competitors cite it as "elegant" (issue #178: "The clean PPR-over-KG formulation is elegant, and the OpenIE pipeline + index-build flow has been very useful as a reference").
- Offline cost discipline vs GraphRAG/LightRAG (9.2M input tokens vs 68.5M/115.5M on the same corpus).
- Runnable docs — multiple providers, vector stores, local vLLM paths out of the box.
- Benchmark credibility — it is the baseline others must beat in the memory space.

**What users complain about:**
- **PPR-only ranking underperforms plain cosine in realistic settings.** Issue #154: "Since HippoRAG relies mainly on triple-level similarity, it tends to retrieve these conceptually close but evidence-poor passages… Overall, its performance is even worse than standard vector retrieval." Issue #178's ablation quantifies it (0.565 PPR-only vs 0.819 cosine, HotpotQA dev).
- **Triple filter removes the right answer.** The authors' own error analysis: after LLM triple filtering, 26% of failed samples matched no phrase from the supporting documents; 18% were left with **zero** triples; in 8% the matched-phrase proportion *decreased* after filtering. One worked example in the paper shows the filter returning an **empty** list for a query whose five retrieved triples all contained supporting-passage phrases.
- **Corpus-density sensitivity** — HippoRAG assumes roughly uniform information density across passages; it does not handle "some passages are complete, others just list entity names."
- **Ecosystem friction** — PyPI 14 months behind `main`, one release ever, index-config binding forces full re-index on any model change, and the 2.0.0a5 manifest rule means "copying or fabricating only the manifest is not a safe migration."
- **Memory cost** — 9.9 GB QA GPU memory, 5.8× dense RAG.

**Overall sentiment: Positive academically, mixed in practice.** Strong scholarly reception (NeurIPS + ICML, ~390 citations on the first paper) and repeated citation as a reference design. But the practitioner signal surfaced in its own issue tracker is a consistent theme of *ranking quality degradation outside Wikipedia-style multi-hop QA* — three independent reports (#154 user report, #178 third-party ablation, and the authors' own error analysis — in 50% of failures at least half the linked phrase nodes were in the supporting passages, yet graph search still failed) converge on the same weakness.

[GitHub Issues API](https://api.github.com/repos/OSU-NLP-Group/HippoRAG/issues?state=all), [arXiv:2502.14802v2 Appendix E](https://arxiv.org/html/2502.14802v2) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## Architectural Contrast — HippoRAG vs. Tortoise (for the subgraph-competitor analysis)

The prompt asked to confirm or correct four claims. Two confirmed, one corrected, one partial:

| Claim | Verdict | Evidence |
|---|---|---|
| Ranks a subgraph by graph centrality (PPR) | ✅ **CONFIRMED** | "we run the PPR algorithm over the hippocampal index… we aggregate the output PPR node probability over the previously indexed passages and use that to rank them for retrieval." The ranking signal is *structural centrality*, not truth, not relevance-to-truth. |
| No confidence / credence weighting | ✅ **CONFIRMED** | PPR yields a probability distribution over nodes — a *ranking score*, not a calibrated belief. Node specificity (`s_i = \|P_i\|^-1`) is IDF-flavoured term weighting. The LLM "recognition memory" filter is a **binary** keep/drop, not a confidence. Nothing anywhere attaches a credence to a claim being *true*. |
| No logical relations (IMPL / NAND) | ✅ **CONFIRMED** | Relations are OpenIE strings with "no constraints or schema." Relation edges are **undirected, weight 1** — direction and semantics are discarded at graph-build time. There is no implication typing, no contradiction/negation edge, and no mechanism to represent "these two claims conflict." |
| Edges are corpus co-occurrence | ❌ **CORRECTED** | Three edge types, none of them co-occurrence counts: (1) **relation edges** from OpenIE triples, weight 1, undirected; (2) **synonymy edges** when embedding cosine ≥ τ=0.8, weight = similarity; (3) **"contains" context edges** passage→phrase, weight 1 (HippoRAG 2). "Co-occurrence" undersells it — these are LLM-extracted semantic triples. The accurate critique is not *how* the edges are built but that **the relations are untyped and semantically inert**: `(A, r, B)` supports graph traversal identically regardless of whether `r` means "is the father of," "contradicts," or "was mentioned near." |

**Two corrections in HippoRAG's favour — be precise about these:**

1. **HippoRAG 2 does have passage-level provenance.** The `contains` edge links every phrase node to the passage it was extracted from, and HippoRAG 1's `|N|×|P|` occurrence matrix **P** serves the same role. So "graph holds claims traceable to a source document" is something HippoRAG *does* have. What it lacks is claim-level provenance in the agent-memory sense (which conversation turn, which speaker) and any provenance on the *relation*.
2. **HippoRAG 2 does have a context signal separate from concept signal** — dense-sparse integration is precisely its answer to the concept-context tradeoff, and it works (ablation: −6.1 R@5 without passage nodes). The gap is not "no context," it is "no truth structure."

**The clean one-line contrast:**

| | HippoRAG | Tortoise |
|---|---|---|
| Graph edge semantics | OpenIE triple, untyped, undirected, weight 1 | typed epistemic operators (IMPL, NAND) |
| Subgraph selection | PPR centrality from query-linked seeds | (our equivalent) |
| Weight on a claim | PPR rank — how *reachable*, not how *true* | EP belief propagation — a credence |
| Conflict | Not representable | NAND |
| Time | Not representable (OpenIE demonstrably drops temporal triples) | temporal validity + supersession |
| Delivered to LLM | **passages** (graph is a scoring instrument) | claim-level state with provenance |
| Provenance | passage containment | claim → source conversation turn |

**The single most useful fact in this profile for our positioning:** in HippoRAG, the graph is *never shown to the model*. It is a retrieval scorer; the reader gets concatenated text. Tortoise's graph **is** the payload — the reasoning state itself is what the agent consumes. HippoRAG answers "which passages should I read?"; Tortoise answers "what do I currently believe, and why?"

**And the most useful fact about where the field is heading:** issue #181 asks HippoRAG for exactly the layer Tortoise already has — a hook exposing "how retrieval signals support, conflict, weaken, or require downstream verification" plus "retrieval relation dynamics" with "support, indeterminacy, conflict" metadata. That request was filed by an outsider against a system that has no epistemics. The demand for typed epistemic structure on top of graph retrieval is being expressed in HippoRAG's own issue tracker.

*Last checked: 2026-09-11*

---

## Notes & Sources

**Primary sources (fetched 2026-09-11):**

- **HippoRAG 1 paper** — [arXiv:2405.14831](https://arxiv.org/abs/2405.14831) (abs) and [HTML v2](https://arxiv.org/html/2405.14831v2). NeurIPS 2024. Full text including §2 methodology, §4 results tables, §5 discussions, Appendix F error analysis (NER 48% / OpenIE 28% / PPR 24% on 100 MuSiQue errors), Appendix G cost comparison.
- **HippoRAG 2 paper** — [arXiv:2502.14802](https://arxiv.org/abs/2502.14802) (abs) and [HTML v2](https://arxiv.org/html/2502.14802v2). ICML 2025. Full text including §3 methodology, §5 results, §6 discussions/ablations, Appendix E error analysis, Appendix F cost table, Appendix G hyperparameters.
- **GitHub repo** — [OSU-NLP-Group/HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) + [raw README](https://raw.githubusercontent.com/OSU-NLP-Group/HippoRAG/main/README.md) + [GitHub API](https://api.github.com/repos/OSU-NLP-Group/HippoRAG). Metrics: 3,999 stars / 427 forks / 15 contributors / 162 commits / 8 open issues / MIT / created 2024-05-23 / last push 2026-09-03.
- **PyPI** — [pypi.org/pypi/hipporag/json](https://pypi.org/pypi/hipporag/json). Three releases only: 2.0.0a2 (2025-02-27), 2.0.0a3 (2025-04-12), 2.0.0a4 (2025-06-24). README documents 2.0.0a5.
- **AWS ML Blog** — [HippoRAG: Neurobiologically inspired RAG using Amazon Bedrock, Amazon Neptune, and personalized PageRank](https://aws.amazon.com/blogs/machine-learning/hipporag-neurobiologically-inspired-rag-using-amazon-bedrock-amazon-neptune-and-personalized-pagerank/), published 2026-07-01 by Tanay Chowdhury, Saeideh Shahrokh, Yingwei Yu. Bedrock (extraction/QA/NER) + Neptune (graph store) + Neptune Analytics (PPR) + Titan Embeddings, demonstrated on HotpotQA.

**Secondary sources (fetched 2026-09-11):**

- **SEEM paper** — [arXiv:2601.06411v2](https://arxiv.org/html/2601.06411v2) (Feb 2026). Source of the third-party LoCoMo/LongMemEval table in §6.8. Note: SEEM's stated goal is to beat HippoRAG 2, so treat its harness as adversarial-but-informative.
- **Paper Notes summary** — [en.papernotes.org ICML2025](https://en.papernotes.org/ICML2025/graph_learning/from_rag_to_memory_non-parametric_continual_learning_for_large_language_models/) — used to cross-check the HippoRAG 2 F1/Recall@5 and ablation tables against the paper HTML. **All reported numbers were independently confirmed against the arXiv HTML.**
- **Emergent Mind topic page** — [emergentmind.com/topics/hipporag-2](https://www.emergentmind.com/topics/hipporag-2) — secondary; source of the "37% miss rate" framing for the triple filter and the "9M vs 115M tokens" framing. Note the 9M/115M claim is consistent with the paper's Appendix F table (9.2M vs 115.5M).
- **Hugging Face paper page** — [huggingface.co/papers/2502.14802](https://huggingface.co/papers/2502.14802).

**Citations — sourced but low-confidence markers noted:**

| Paper | Google Scholar | Semantic Scholar | OpenAlex |
|---|---|---|---|
| HippoRAG 1 (2405.14831) | 387 | 320 | 90 (primary record) + 21 (arXiv record) |
| HippoRAG 2 (2502.14802) | ⚠️ not retrieved | ⚠️ not retrieved (Semantic Scholar API returned HTTP 429 on 3 attempts) | 2 (arXiv record) |

⚠️ The three indexes disagree by up to 4× for the same paper (387 vs 90 for HippoRAG 1). The Google Scholar and Semantic Scholar figures come from Perplexity-surfaced snippets, **not** from a direct fetch of the citation pages — treat them as approximate. OpenAlex figures are from a direct API call but OpenAlex undercounts recent AI/ML papers substantially. **No citation number in this profile should be quoted without its index named.**

⚠️ **Explicitly rejected claim:** a Perplexity result stated arXiv "lists the paper as cited by 427." arXiv does not publish citation counts, and 427 is exactly the repo's fork count. This is treated as a tool artifact and is **not** reported as the HippoRAG 2 citation count anywhere in this profile.

**Documented gaps:**

- ⚠️ **No commercial product exists** — verified absent across the repo, both papers, PyPI, and four web searches. §5 is "N/A" by fact, not by omission.
- ⚠️ **No production deployments named** anywhere. The AWS blog is a how-to, not a customer story. "Enterprise-scale" in that post is aspirational framing by AWS staff.
- ⚠️ **PyPI download counts not retrieved** — pypistats.org returned HTTP 429 on two attempts. Would be the best available proxy for real-world usage vs. academic citation.
- ⚠️ **No HippoRAG 2 citation count obtained.** Semantic Scholar API rate-limited (3 attempts); the two open-access mirrors (semanticscholar.org, OpenAlex) either blocked JS-less fetching or undercount.
- ⚠️ **Issue #178's ablation is third-party and unreviewed.** The poster's own `hippo_style.py` reimplementation, not the authors' code, on a 500-question HotpotQA dev subset. Directional only.
- ⚠️ **The HippoRAG 2 paper's own "Avg" column is internally inconsistent** (see §6.6). Verified directly against the raw table markup: the printed averages (59.8 / 57.0) do not equal the means of the printed per-dataset values (50.5 / 47.9). The paper does not explain the aggregation. The per-dataset figures are reliable; the averages should not be recomputed or cited as a computed quantity. This is a gap in the source, not in this profile.
- ⚠️ **No G2/Capterra/Product Hunt/review-site data** — none exists for research OSS, so sentiment in §11 rests entirely on GitHub issues plus the authors' own error analyses.
- ⚠️ **LongMemEval/LoCoMo numbers are third-party only.** The authors never benchmark on agent-memory benchmarks; §6.8 is SEEM's harness, and no second independent source was found to cross-check it.

*Last updated: 2026-09-11*
