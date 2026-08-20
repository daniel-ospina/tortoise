---
title: "Tortoise v2 extractor — LongMemEval full-run RETRIEVAL analysis"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Tortoise v2 extractor — LongMemEval full-run RETRIEVAL analysis

Date: 2026-08-20 · Baseline: `/tmp/lme-det-full-report.json` (485 Q, git 4a66fb7) · v2: `/tmp/lme-v2-full-report.json` (496 Q, git 75cd2816) · Raw v2 checkpoint: `/tmp/lme-v2-full.json` · Run log: `/tmp/lme-v2-direct16b.log`
Method: report diffing + full-checkpoint error-text analysis + empirical retrieval probes (FalkorDBLite, TF-IDF-forced and FTS paths, synthetic 30-session corpus) + competitor code (graphiti, mem0, letta).

---

## 0. Executive summary (the headline)

**The v2 full run is not a valid v2 measurement.** The extractor wrote **zero points in 444 of 496 questions (89.5%)** because the direct-DeepSeek extractor key returned **HTTP 402 Payment Required** on ~21,329 S1 calls (`S1 chunk failed: HTTPError: 402 … api.deepseek.com/v1/chat/completions`; 21,957 "no embed list produced" errors). The graph that retrieval measured contained **raw verbatim transcripts only** (one per session, ~48 sessions/question) — no v2-extracted points, no turn points, no evidence-marked points (**495/496 questions had 0 evidence points**).

Consequences:
- **`evidence_recall@5 = 0.0` is vacuous**, not a retrieval failure — there were no evidence points to retrieve (the metric reports 0.0 both for "evidence exists but never surfaces" and "no evidence exists"; only 1/496 questions had an evidence point, and its own run was broken: session_recall@5 = 0.0).
- **The session-recall drop (80.6% → 75.4%) measures raw-transcript-only retrieval**, i.e. condition (B) below — not v2 graph retrieval. The one question with an evidence point also had sr@5 = 0.0; the questions with the biggest drops (baseline 1.00 → v2 0.00, n=15+) all carry 40–186 ingest errors.

**However, the analysis surfaced a genuine retrieval-layer risk that survives the 402 failure** (probe C, §3): v2's points are *paraphrased* (S1 story-summarizes, S2 maps), and the eval's effective sparse leg — RediSearch-style FTS with AND-over-token semantics — **excludes a paraphrased evidence point that drops even one query token**, while the verbatim raw-transcript leg outranks it. Even a fully healthy v2 run would face a low evidence-recall ceiling under the current lexical stack. The baseline's 0.526 evidence recall itself is mediocre (0.62 IE / 0.61 TR / 0.45 MSR / 0.45 KU), so the evidence-retrieval problem is pre-existing and v2's paraphrasing makes it worse.

---

## 1. Retrieval verdict

### 1.1 What the report numbers actually mean

| metric | baseline (det) | v2 (reported) | v2 (true meaning) |
|---|---|---|---|
| session_recall@5 | 0.806 | 0.754 | raw-transcript-only recall (extractor produced nothing) |
| evidence_recall@5 | 0.526 | **0.000** | vacuous — 0 evidence points exist in 495/496 graphs |
| turn_recall@5 | 0.526 | 0.000 | same vacuity |
| context_tokens mean | 7,918 | **34,863 (4.4×)** | top-20 saturated with full-session verbatim transcripts |
| retrieval latency p50 | 181 ms | 40 ms | tiny graphs (raw-only) hit the fast FTS/index path |
| n_ingest_errors | — | 35–186 per Q (mean 134.6) | 21,329× HTTP 402 + 1,081× S2 timeouts + 903× S4 timeouts |

The report's own `_print_summary` warns "⚠ N v2-ingest error(s) … recall may be raw-transcript-only", but nothing gates the run or nullifies the published metrics — 21k errors is not a transient blip, it is a failed run that was published as a v2 measurement.

### 1.2 Where the session-recall "drop" happens (raw-only corpus, per category)

| category | n | baseline sr@5 | v2 sr@5 | Δ |
|---|---|---|---|---|
| Info Extraction | 149 | 0.926 | 0.826 | **−0.101** |
| Abstention | 28 | 0.717 | 0.661 | −0.057 |
| Temporal Reasoning | 123 | 0.786 | 0.746 | −0.040 |
| Multi-Session Reasoning | 109 | 0.679 | 0.665 | −0.014 |
| Knowledge Updates | 72 | 0.833 | 0.819 | −0.014 |

36 questions collapse from sr@5 > 0 to exactly 0.0 (their v2 context is still ~36k tokens — retrieval ran, it just ranked the wrong sessions' transcripts top-5). IE is hit hardest because single-session questions ask for one specific fact; with turn points (baseline) the answer turn is a compact, high-precision doc; with raw-only (v2) every session's transcript is a giant bag that also contains the question's tokens (topics repeat across sessions), so top-5 becomes a lottery. The multi-session/KU categories are least affected because their queries already scatter tokens broadly.

### 1.3 The evidence_recall=0.0 anomaly — attribution

The metric (`retrieve.py`) sets evidence_recall to 0.0 in two indistinguishable cases: (a) no `has_answer=true` points exist (extractor failure) or (b) they exist but never surface (retrieval failure). The v2 checkpoint proves (a): 495/496 questions have `evidence_points: 0`; the single question with 1 evidence point had a broken retrieval run too. **Conclusion: 0.0 is an ingest-failure artifact, not a retrieval regression — and the metric layer cannot tell the difference. That is a measurement design bug (#1369) in itself.**

### 1.4 The genuine retrieval finding (probe C)

Probe (30 sessions, distractor sessions that mention Ava/board games/Catan, answer session s7, evidence point paraphrased "Ava's go-to board game is Catan…" instead of "favorite"):

| query | FTS hits | evidence point surfaced? |
|---|---|---|
| "What is Ava's favorite board game?" (real Q) | 2: s7 raw (1.0), s7 *user-turn* statement (0.75) | **NO** — point says "go-to", FTS AND-match drops it |
| "Ava board game Catan" (subset) | 5: s7 raw (0.167), **evidence pt (0.125)**, 3 distractors (0.100) | YES, but ranked below the raw transcript |
| "Ava's favorite board game" | 2 | NO |
| "Ava go-to board game" (as the point phrases it) | **0 hits** | NO (hyphenated tokenization) |

Baseline on the same corpus: the verbatim evidence *turn* also failed the full-question FTS query (only the user's question turn + raw transcript matched — RediSearch stopword/possessive/score quirks), but the mini-fixture probe with the TF-IDF path surfaced the evidence turn at rank 1. So: (i) the sparse leg is brittle toward paraphrase in both modes, and (ii) the two legs (FTS vs TF-IDF) behave differently per graph — **the eval does not run a uniform retrieval stack** (FalkorDBLite materializes the FTS index for larger graphs but not small ones; match_source is never recorded per question).

---

## 2. Where we do well / where not

**Well:**
- **Verbatim session recall.** The raw-transcript leg reliably surfaces the *answer session* even when extraction is completely dead (session recall still 0.75 with 0 points written; probe B sr@5 = 1.0). The #1369 design decision to retain raw transcripts is validated.
- **Graceful degradation.** Circuit breakers, 500 ms caps, per-question error isolation, checkpoint/resume, snapshot TF-IDF fallback — the run completed 496/500 questions through a provider outage without crashing.
- **Attribution machinery exists.** Evidence marking + session/turn/evidence recall split is the right frame — it's just not *exercised* or *gated*.
- **Idempotent, content-addressed writes.** No duplicate points even across 40–60 sessions per question; operator/evidence OR-in collision handling is sound.

**Not well:**
1. **Run integrity gates.** A 21k-error, 0-point run was published as a v2 result. No abort/flag on `evidence_points == 0` or error-rate threshold.
2. **Evidence-retrieval ceiling (genuine).** Evidence-marked points are paraphrased; the sparse leg (FTS AND-match in this FalkorDBLite build; TF-IDF cosine in others) punishes paraphrase. Vector leg absent in the eval (no embedder) — the one leg that would rescue semantic paraphrase — and Tortoise never created an FTS index itself; availability is engine-dependent and inconsistent per graph.
3. **Structural leg is inert in the eval.** `run_structural_query` with `kind=None` returns `[]`; IMPL/NAND operator structure contributes nothing to retrieval. The eval's "graph retrieval" is text-over-points, not graph retrieval.
4. **Raw-transcript domination.** When only transcripts exist, top-20 is 4.4× the tokens (34.9k) with no diversity control (no MMR, no dedup by session, no snippet truncation) — worse reader answers (overall 0.678→0.661; IE 0.847→0.765) and 2.5× reader latency.
5. **Metric blindness.** evidence_recall can't distinguish "never written" from "never retrieved"; match_source/leg-mix per question is not recorded; k=5 pool (`limit*2` = 40) is the only recall lever.
6. **Inconsistency between eval legs.** FTS vs TF-IDF vs vector behave differently per graph size/build → per-question recall is not measured on a common stack.

---

## 3. Competitor retrieval mechanisms (step 3)

| system | retrieval surface | mechanisms | notes for us |
|---|---|---|---|
| **getzep/graphiti** | 4 parallel scopes: **edges, nodes, episodes (verbatim chunks), communities** | per-scope **fulltext (Lucene) + cosine (embeddings) + BFS graph traversal**, fused by **RRF**; rerankers: **cross-encoder, MMR (diversity), node-distance, episode-mentions**; MIN_SCORE 0.6 | episodes = our raw transcripts, but *chunked per turn* and used to **expand** to entities/communities via MENTIONS edges — the graph is a *recall amplifier*, not the only surface. No AND-only sparse leg (Lucene OR + sanitize). |
| **mem0ai/mem0** | memory rows + KG entities | **parallel scoring: semantic (vector) + BM25 + entity search + temporal reasoning**, fused with **entity boost weight** (`ENTITY_BOOST_WEIGHT`); optional **LLM/cross-encoder rerank**; BM25 over lemmatized text | sparse leg is **BM25** (idf + OR-tolerant, handles partial paraphrase — my subset query "Ava board game Catan" worked), not strict AND-match. Entity-centric query analysis is the graph-traversal substitute. |
| **letta-ai/letta** | archival passages (chunked verbatim memory) + tags | **vector search over passage embeddings**, metadata/tag/date filters, no graph traversal | validates the "index raw chunks + embeddings" baseline; retrieval is pure dense + filters. Shows the dense leg is the default backbone everyone else builds on. |

Common thread: **all three are dense-first (embeddings), sparse as BM25/Lucene complement, and treat graph structure as a recall-expansion/rerank signal — never as the only surface, and never with strict-AND lexical semantics.**

---

## 4. Proposed improvements (steps 2 + 3, ranked)

**P0 — measurement integrity (fix the run, not the retriever):**
1. **Gate v2 runs on extraction health**: abort/flag when `evidence_points == 0` or ingest-error rate > threshold (e.g. >20% of sessions); persist per-question `match_source`, evidence_point_count, and points-written in the outcome so 0.0 vs 0/5 is never ambiguous.
2. **Record the leg mix per question** (fts/vector/structural/tfidf + pool size + scores) in the report methodology — the current report cannot even say which stack a number came from.
3. **Re-run the v2 full-run with a working extractor key** before drawing any v2 conclusion. (The earlier 4–5-question v2 runs with working extraction showed evidence_recall@5 = 0.25–0.2 and sr@5 0.75–0.8 — evidence points *do* surface when they exist, confirming the 0.0 is a run artifact.)

**P1 — retrieval stack (the genuine fixes):**
4. **Make the sparse leg BM25/OR-tolerant instead of RediSearch strict-AND**: query expansion (add top-N extracted-point terms), or fall back to TF-IDF cosine over the full pool when FTS returns < k hits. (Probe: the evidence point surfaced at rank 3 on the OR-ish subset query; it was *zeroed* by the AND query.)
5. **Enable the dense leg in the eval**: give the runner an embedder (MiniLM local, no API key) so v2's paraphrased points get the semantic surface they were designed for; embed the raw transcripts too. This is the single biggest lever for v2 evidence recall — every competitor leads with it.
6. **Chunk the raw-transcript leg**: one point per turn (as baseline) or per ~3 turns instead of whole-session blobs — cuts context 4×, improves precision, and restores turn-level evidence attribution; add session-level dedup/MMR to keep ≤1 transcript per session in top-k.
7. **Wire the structural leg into retrieval**: at minimum, after a text match on an entity/point, expand via IMPL/NAND one hop and include connected points (graphiti's episode-mentions pattern) — this is the only way the graph adds recall over plain text.

**P2 — reranking + diversity:**
8. **Cross-encoder or LLM rerank** of the pooled candidates (graphiti/mem0 both do this) with a `rerank_min_score` floor.
9. **MMR/diversity in top-k** (graphiti) to stop one session's transcript family from monopolizing the context (directly addresses the 4.4× token blowup).
10. **Temporal/recency scoring** (mem0's temporal leg) for the Temporal-Reasoning category (currently the worst evidence recall 0.614).

---

## 5. Concrete recommendations

1. **Treat the v2 full-run as void.** Do not cite 75.4% / 0.0 as v2 numbers; the run is a raw-transcript-only control. Re-run with the extractor key funded or switched back to OpenRouter routing, plus the P0-1 gate.
2. **Fix the evidence-marking / raw-transcript balance — yes, it needs rework**, but not by removing the raw leg (it's the run's only working recall and the #1369 verbatim mitigation is correct). Rework = (a) chunk raw transcripts to turn-granularity, (b) dedup/MMR the fused top-k by session, (c) ensure evidence-marked points are *retrievable*, i.e. the marking threshold (≥0.4 stopword-stripped overlap with the answer turn) does not align with the lexical retriever's ability to find them by the *question's* phrasing — mark + densify, or mark + expand the point content with verbatim answer tokens.
3. **Prefer BM25 over strict-AND FTS for the sparse leg** (mem0 pattern) or blend: FTS pool ∪ TF-IDF pool before RRF; never let a single strategy's AND semantics zero out the answer.
4. **Add the local embedder to the eval** (and to the embedded search path generally) so the vector leg is real in every mode; v2's entire recall bet is on semantic extraction, and the current measurement never tests it.
5. **Make the structural leg contribute** (1-hop IMPL/NAND expansion on matched points) — otherwise "graph + raw sessions" hybrid is text-only and the graph is decorative in retrieval.
6. **Metric/UX**: expose `evidence_point_count` per outcome; change evidence_recall semantics so "no evidence written" is reported as `null`/`N/A`, not 0.0; add a run-level "measurement valid" flag.
7. **Ops**: the 402 run cost ~2.5 days of wall time (mean 823 s/question incl. 600 s S-stage deadlines). The extractor's `_complete` deadline (600 s) with retries means a dead key is only discovered after hours; add a cheap pre-flight API ping and fail-fast on 4xx auth/quota errors in `extract_session_v2` before running 500 questions.

**Bottom line:** the retrieval layer didn't regress — the measurement collapsed (402 outage → 0 points → vacuous evidence recall, raw-only session recall, 4.4× context). The real retrieval work is to give v2's paraphrased evidence points a retrieval surface they can actually win on: dense embeddings + OR-tolerant sparse + turn-granular transcripts + a structural expansion leg — the exact stack graphiti/mem0/letta all build on.
