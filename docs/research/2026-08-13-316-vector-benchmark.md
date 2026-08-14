---
title: "Research Brief"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Research Brief

## Raw Notes

### Axis Research

- **2026-08-13T10:06:33** [canonical] FalkorDB vector index (official docs): scales to millions of vectors with sub-linear query time via db.idx.vector.queryNodes. Official benchmark tool exists: github.com/FalkorDB/benchmark — reports p50/p90/p99 latency and compares against Neo4j; live results at benchmark.falkordb.com. FalkorDB blog claims sub-ms 3-hop traversals on tens of millions of edges.
- **2026-08-13T10:06:33** [pitfalls] HNSW vs brute-force: arXiv 2409.06464 — for corpora <100K docs, flat vs HNSW QPS differences are negligible; gap grows at 100K-1M (flat 2-3x slower). FLAT preferred for tiny datasets (<5K 512-d vectors / <10MB index) — index overhead not worth it. Implication for Tortoise: agent-memory scale (low thousands of Points) likely trivially meets vector<100ms via brute force; HNSW acceleration matters only at 100K-1M. Benchmark must NOT run on embedded FalkorDBLite (brute-force) as a proxy for prod Docker/HNSW — numbers can REVERSE.
- **2026-08-13T10:06:34** [canonical] IR evaluation conventions: classic IR (Manning IR book) uses ~50 information needs as sufficient minimum for averaging performance; RAG practice builds golden test sets of 50-200 query-doc pairs; metrics precision@k, recall@k, nDCG. Relevance judgments are expensive; average over a large query set. Warning (Stanford IR book): tuning on the same collection overstates performance — quasi-gold self-retrieval (corpus items as queries) leaks and overestimates precision.
- **2026-08-13T10:06:34** [canonical] Latency measurement protocol: p50/p95/p99 with warm-up to stable baseline; enough samples for stable p99; p95 for regression gates, p99 tracks tail; instrument every request; sequential AND concurrent sweeps; record full-chain completion times (DigitalOcean LLM-inference guide, gatling.io, loadtester.org). Python: timeit or pytest suffices for percentile collection — pytest-benchmark optional, not required.
- **2026-08-13T10:06:34** [competitor] Competitive reference numbers (all external, not on our data): Neo4j hybrid top-10 on 10M graph: p50 ~250ms, p95 ~340ms (markaicode, single-source, EC2 G4dn.xlarge); Neo4j vector latency 23.7ms@k=10 → 44.3ms@k=100 vs FAISS-HNSW 4.5-4.7ms (KTH thesis); Supermemory vendor claims: sub-300ms recall, 85.4% LongMemEval accuracy, hybrid vector+keyword two-stage retrieval, 40-80ms some paths, sub-400ms at scale (marketing, single-source). Honcho: D8 research covers patterns (BM25+vector over messages) — no public latency numbers (dedup to docs/research/2026-07-18-conversation-indexing-search.md). Cross-vendor P/R@K not comparable: different indexed units (Points+operators vs messages vs conclusions).
- **2026-08-13T10:06:34** [canonical] RRF tuning: k=60 is the industry-standard default (Cormack 2009; Elasticsearch + Azure AI Search both default 60). Higher k increases influence of lower-ranked documents; k in 20-100 behaves similarly; MariaDB recommends higher k for keyword+vector fusion. Tune only when labeled eval data shows a consistent benefit. Validates Tortoise rrf_fusion k=60 default; optional sensitivity sweep k in {20,60,100}.
