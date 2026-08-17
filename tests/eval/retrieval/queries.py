"""Query sets for the #1144 retrieval quality eval.

Two sets, both committed as JSON under tests/eval/retrieval/queries/:

1. **Oracle queries (100)** — every query carries a deterministic oracle
   target topic; graded relevance is derived by
   `benchmarks.synthetic_corpus.oracle_grades_for_query` (grade 2 = target
   topic, grade 1 = NEAR topics, grade 0 = rest). No LLM, no human labels.
   Composition: the 53 vocabulary-targetable queries from the #316 latency
   mix (benchmarks/query_mix.json) + 47 new queries written for the eval.
   Excluded from the oracle set: q052/q055/q056 — their tokens have ZERO
   overlap with the corpus token pool, so no oracle target exists (empty
   relevant set → degenerate labels). They stay in the latency mix where
   they belong (q055/q056 are the no-match degrade probes).

   Tiers (spec: 50 easy / 30 medium / 20 hard):
   - easy   — query tokens all inside the target topic's exclusive core
   - medium — query tokens mostly in the target core, one bridge token that
              also matches a NEAR topic (mild ambiguity)
   - hard   — "ambiguous near-miss": the query text is dominated by a NEAR
              topic's core + bridge tokens, so surface token matching pulls
              grade-1 distractors ABOVE the grade-2 target (latent intent
              vs surface vocabulary mismatch)

2. **Authored queries (50)** — authored over the real internal graph domain
   (anchor slice): pricing/product decisions, the hybrid search engine, EP
   belief propagation, ops/runbooks, ontology/registry, benchmarks, memory.
   Relevance is SUBJECTIVE → judged by two cross-vendor LLM judges + owner
   adjudication (tests/eval/retrieval/judge.py). No logs required — authored
   from the repository's documented domain structure (docs/, product/,
   tortoise/). The baseline run reports these as `labels_pending` until the
   judge pool (top-50/strategy/query) is labeled and merged via
   `--judge-labels`.

The committed JSONs are the source of truth; the builder below is the
deterministic generator used to produce them (`--rebuild-queries` on
tests/eval/retrieval/run.py). A test asserts generator output == committed
JSON so the files cannot drift.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from benchmarks.synthetic_corpus import (
    TopicOracle,
    build_topic_oracle,
)

QUERIES_DIR = Path(__file__).resolve().parent / "queries"
ORACLE_QUERIES_PATH = QUERIES_DIR / "oracle_queries.json"
AUTHORED_QUERIES_PATH = QUERIES_DIR / "authored_queries.json"

ORACLE_TIER_TARGETS = {"easy": 50, "medium": 30, "hard": 20}
ORACLE_QUERY_COUNT = 100
ORACLE_QUERY_MIX_COUNT = 53  # targetable query_mix queries (see module docstring)

# ── Oracle query construction ───────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query.lower()) if t]


def _assign_query_mix_tier(oracle: TopicOracle, target: int, query_tokens: list[str]) -> str:
    """Deterministic tier for a query_mix-derived oracle query.

    easy: ≥2 target-vocab tokens and none in a NEAR topic's vocab;
    medium: ≥1 target-vocab token (with any NEAR spillover);
    hard: everything else (no distinctive target signal).
    """
    vocab_t = set(oracle.vocab(target))
    near_vocab = {t for n in oracle.near(target) for t in oracle.vocab(n)}
    in_t = sum(1 for tok in query_tokens if tok in vocab_t)
    in_near = sum(1 for tok in query_tokens if tok in near_vocab)
    if in_t >= 2 and in_near == 0:
        return "easy"
    if in_t >= 1:
        return "medium"
    return "hard"


def _rebalance_tiers(
    queries: list[dict],
    targets: dict[str, int],
    initial: dict[str, int] | None = None,
) -> list[dict]:
    """Deterministically demote over-subscribed query_mix queries.

    Only demotes (easy→medium, medium→hard) — never promotes, so a
    query_mix query can only become more honest, not flattered. A tier
    over its target demotes its most-ambiguous members to the next tier,
    capped at the next tier's free slots (the remainder is filled by the
    new-query generator). Within a tier: easy demotes least
    target-distinctive first (fewest target tokens); medium demotes most
    NEAR-vocab spillover first.
    """
    from collections import defaultdict
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for q in queries:
        by_tier[q["tier"]].append(q)

    def _demote(tier: str, next_tier: str, key) -> None:
        free = targets[next_tier] - len(by_tier[next_tier])
        excess = len(by_tier[tier]) - targets[tier]
        move = min(max(excess, 0), max(free, 0))
        if move <= 0:
            return
        by_tier[tier].sort(key=key)
        moved = by_tier[tier][:move]
        by_tier[tier] = by_tier[tier][move:]
        for q in moved:
            q["tier"] = next_tier
            q.setdefault("tier_rebalanced", []).append(f"{tier}->{next_tier}")
            by_tier[next_tier].append(q)

    # easy -> medium: fewest target tokens first (least distinctive).
    _demote("easy", "medium", key=lambda q: (q["_target_count"], q["id"]))
    # medium -> hard: most NEAR-vocab spillover first.
    _demote("medium", "hard", key=lambda q: (-q["_near_count"], q["id"]))
    result = by_tier["easy"] + by_tier["medium"] + by_tier["hard"]
    for q in result:
        q.pop("_target_count", None)
        q.pop("_near_count", None)
    return result


def _new_queries_for(
    oracle: TopicOracle,
    needed: dict[str, int],
    seed: int = 42,
) -> list[dict]:
    """Generate `needed[tier]` new oracle queries (deterministic).

    easy:   3 core tokens of the target topic (exclusive — no bridges).
    medium: 2 core tokens + 1 bridge token (bridge matches target + NEAR).
    hard:   1 core token of the target + 2 core tokens of a NEAR topic + 2
            bridge tokens — the surface text describes the NEAR topic more
            than the target (ambiguous near-miss; latent intent = target).
    Topics cycle round-robin so every topic hosts queries.
    """
    rng = random.Random(f"oracle-new-queries:{seed}")
    n = oracle.n
    out: list[dict] = []
    counters = {"easy": 0, "medium": 0, "hard": 0}
    topic_idx = 0
    while sum(counters.values()) < sum(needed.values()):
        k = topic_idx % n
        topic_idx += 1
        core = oracle.core[k]
        bridge = oracle.bridges[k]
        for tier in ("easy", "medium", "hard"):
            if counters[tier] >= needed[tier]:
                continue
            if tier == "easy":
                toks = rng.sample(core, 3)
            elif tier == "medium":
                toks = rng.sample(core, 2) + [bridge]
            else:
                nbr = oracle.near(k)[1]  # the +1 neighbor
                nbr_core = oracle.core[nbr]
                toks = rng.sample(core, 1) + rng.sample(nbr_core, 2) + [
                    oracle.bridges[(k - 1) % n], bridge,
                ]
            rng.shuffle(toks)
            counters[tier] += 1
            out.append({
                "id": f"nrq{counters[tier]:03d}-{tier[:1]}",
                "query": " ".join(toks),
                "tier": tier,
                "source": "oracle_new",
                "oracle_target": k,
            })
    return out


def build_oracle_query_set(
    oracle: TopicOracle | None = None,
    query_mix_path: str | None = None,
    seed: int = 42,
) -> dict:
    """Build the full 100-query oracle set deterministically.

    Returns {"meta": ..., "queries": [...]}. Raises ValueError when the
    composition cannot hit the pre-registered 50/30/20 tier split.
    """
    oracle = oracle or build_topic_oracle(seed)
    if query_mix_path is None:
        query_mix_path = str(Path(__file__).resolve().parent.parent.parent.parent
                             / "benchmarks" / "query_mix.json")
    mix = json.loads(Path(query_mix_path).read_text())

    queries: list[dict] = []
    skipped: list[str] = []
    for q in mix["queries"]:
        target = oracle.token_topic_assignment(q["query"])
        if target is None:
            skipped.append(q["id"])
            continue
        q_tokens = _tokens(q["query"])
        near_vocab = {t for n in oracle.near(target) for t in oracle.vocab(n)}
        queries.append({
            "id": q["id"],
            "query": q["query"],
            "tier": _assign_query_mix_tier(oracle, target, q_tokens),
            "source": "query_mix",
            "oracle_target": target,
            "kind": q.get("kind"),
            "_target_count": sum(1 for t in q_tokens if t in oracle.vocab(target)),
            "_near_count": sum(1 for t in q_tokens if t in near_vocab),
        })

    if len(queries) + len(skipped) != len(mix["queries"]):
        raise ValueError("query_mix iteration mismatch")

    have = {"easy": 0, "medium": 0, "hard": 0}
    for q in queries:
        have[q["tier"]] += 1
    needed = {t: ORACLE_TIER_TARGETS[t] - have[t] for t in ("easy", "medium", "hard")}
    # Deterministic rebalance pass: when a tier is over-subscribed by
    # query_mix, demote its most ambiguous members (most NEAR-vocab
    # spillover) to the next-harder tier so the pre-registered 50/30/20
    # split is always achievable. Semantics stay honest: more near-vocab
    # spillover = genuinely harder.
    queries = _rebalance_tiers(queries, ORACLE_TIER_TARGETS, have)
    have = {"easy": 0, "medium": 0, "hard": 0}
    for q in queries:
        have[q["tier"]] += 1
    needed = {t: ORACLE_TIER_TARGETS[t] - have[t] for t in ("easy", "medium", "hard")}
    if any(v < 0 for v in needed.values()):
        raise ValueError(
            f"query_mix tier distribution already exceeds targets after "
            f"rebalance: {have} — pre-registered 50/30/20 split is not "
            "achievable; revise the oracle tier assignment"
        )
    queries.extend(_new_queries_for(oracle, needed, seed))
    queries.sort(key=lambda q: q["id"])

    if len(queries) != ORACLE_QUERY_COUNT:
        raise ValueError(f"expected {ORACLE_QUERY_COUNT} oracle queries, got {len(queries)}")
    final = {"easy": 0, "medium": 0, "hard": 0}
    for q in queries:
        final[q["tier"]] += 1
    if final != ORACLE_TIER_TARGETS:
        raise ValueError(f"tier split {final} != {ORACLE_TIER_TARGETS}")

    return {
        "meta": {
            "issue": "1144",
            "name": "oracle retrieval queries (synthetic, deterministic labels)",
            "n_queries": len(queries),
            "tiers": final,
            "composition": {
                "query_mix": sum(1 for q in queries if q["source"] == "query_mix"),
                "oracle_new": sum(1 for q in queries if q["source"] == "oracle_new"),
            },
            "query_mix_excluded": {
                "ids": skipped,
                "reason": "tokens have zero overlap with the corpus token pool "
                          "→ no oracle target (empty relevant set); they remain "
                          "latency-mix probes in #316",
            },
            "relevance": (
                "deterministic 3-level graded labels via "
                "synthetic_corpus.oracle_grades_for_query (grade 2 = oracle_target "
                "topic, grade 1 = NEAR topics, grade 0 = rest)"
            ),
        },
        "queries": queries,
    }


def load_oracle_queries() -> dict:
    """Load the committed oracle query set (source of truth)."""
    return json.loads(ORACLE_QUERIES_PATH.read_text())


# ── Authored queries (50, over the real internal graph domain) ──────────────

AUTHORED_QUERIES: list[dict] = [
    # Pricing / product (product/ strategy docs + pricing.json)
    {"id": "aq001", "query": "pricing tiers enterprise plans", "domain": "pricing", "rationale": "product/pricing.json + strategy docs"},
    {"id": "aq002", "query": "unit economics per message cost", "domain": "pricing", "rationale": "pricing research, cost-per-token economics"},
    {"id": "aq003", "query": "free tier limits graph nodes", "domain": "pricing", "rationale": "tier limits / max_graph_nodes"},
    {"id": "aq004", "query": "launch roadmap milestones phase", "domain": "product", "rationale": "product/2026-08-10-launch-roadmap.md"},
    {"id": "aq005", "query": "beta feedback funnel onboarding", "domain": "product", "rationale": "beta feedback docs (#1199)"},
    {"id": "aq006", "query": "competitor comparison positioning", "domain": "product", "rationale": "product/competition/"},
    {"id": "aq007", "query": "three layer memory model", "domain": "product", "rationale": "product/2026-07-31-three-layer-memory-model.md"},
    {"id": "aq008", "query": "agent coordination layer plan", "domain": "product", "rationale": "product/2026-07-10-agent-coordination-layer-plan.md"},
    # Hybrid search engine (tortoise/search_engine.py)
    {"id": "aq009", "query": "hybrid rrf fusion ranking", "domain": "search", "rationale": "search_engine.rrf_fusion"},
    {"id": "aq010", "query": "circuit breaker latency protection", "domain": "search", "rationale": "search_engine _CircuitBreaker (#249)"},
    {"id": "aq011", "query": "degraded fallback tfidf", "domain": "search", "rationale": "search_engine.fallback_tfidf"},
    {"id": "aq012", "query": "query classification kind filter", "domain": "search", "rationale": "search_engine.classify_query"},
    {"id": "aq013", "query": "vector index hnsw embedding", "domain": "search", "rationale": "run_vector_query HNSW path (#7777)"},
    {"id": "aq014", "query": "retracted points excluded results", "domain": "search", "rationale": "#689 status filter"},
    {"id": "aq015", "query": "structural kind query pointKind", "domain": "search", "rationale": "run_structural_query"},
    {"id": "aq016", "query": "full text search index", "domain": "search", "rationale": "run_fts_query / db.idx.fulltext"},
    {"id": "aq017", "query": "relationship traversal predicate filter", "domain": "search", "rationale": "filter_by_traversal_predicate (#7846)"},
    {"id": "aq018", "query": "ep annotation confidence breakdown", "domain": "search", "rationale": "annotate_ep_batch"},
    # EP / epistemic engine (tortoise/ep.py)
    {"id": "aq019", "query": "belief propagation posterior update", "domain": "ep", "rationale": "EP belief propagation"},
    {"id": "aq020", "query": "contested claims variance threshold", "domain": "ep", "rationale": "posterior variance contestation"},
    {"id": "aq021", "query": "nand contradiction evidence", "domain": "ep", "rationale": "NAND operators = contradiction evidence"},
    {"id": "aq022", "query": "ep convergence non convergence retention", "domain": "ep", "rationale": "epic 903-C5 retention"},
    {"id": "aq023", "query": "posterior stability uncalibrated prior", "domain": "ep", "rationale": "posterior_alpha/beta stability"},
    {"id": "aq024", "query": "epistemic annotation confidence", "domain": "ep", "rationale": "EpBreakdown confidence_mean"},
    # Ops / infrastructure (docs/ops + infra)
    {"id": "aq025", "query": "backup restore runbook", "domain": "ops", "rationale": "docs/ops/registry-backup-dr.md"},
    {"id": "aq026", "query": "event log rebuild recovery", "domain": "ops", "rationale": "JSONL event-log rebuild (#548)"},
    {"id": "aq027", "query": "docker deploy environment variables", "domain": "ops", "rationale": "docker compose / env config"},
    {"id": "aq028", "query": "migration legacy store", "domain": "ops", "rationale": "tortoise/migrate_kinds.py"},
    {"id": "aq029", "query": "namespace isolation graph", "domain": "ops", "rationale": "SDK namespace prefixing"},
    {"id": "aq030", "query": "data safety encryption at rest", "domain": "ops", "rationale": "docs/data-safety.md"},
    {"id": "aq031", "query": "client server package split", "domain": "ops", "rationale": "docs/client-server-split.md (#526)"},
    {"id": "aq032", "query": "quickstart self hosted", "domain": "ops", "rationale": "docs/quickstart-selfhosted.md"},
    # Ontology / registry (docs/ONTOLOGY.md + registry schema)
    {"id": "aq033", "query": "point kind taxonomy", "domain": "ontology", "rationale": "KIND_WEIGHTS / pointKind"},
    {"id": "aq034", "query": "operator impl edges structure", "domain": "ontology", "rationale": "IMPL edges + operators"},
    {"id": "aq035", "query": "registry graph schema entity", "domain": "ontology", "rationale": "docs/registry-graph-schema.md"},
    {"id": "aq036", "query": "pack installs backfill", "domain": "ontology", "rationale": "backfill_pack_installs.py"},
    {"id": "aq037", "query": "source url canonical identity", "domain": "ontology", "rationale": "#448/#149 source url key"},
    # Benchmarks (benchmarks/ + #316)
    {"id": "aq038", "query": "latency p95 target arms", "domain": "benchmark", "rationale": "bench_core pre-registered targets"},
    {"id": "aq039", "query": "warmup steady state protocol", "domain": "benchmark", "rationale": "WarmupProtocol CV target"},
    {"id": "aq040", "query": "right censored capped samples", "domain": "benchmark", "rationale": "failure taxonomy capped/capped-tail"},
    {"id": "aq041", "query": "e2e verdict band achieved", "domain": "benchmark", "rationale": "E2E_TARGET_MS 300ms band"},
    {"id": "aq042", "query": "elevated timeout uncensored", "domain": "benchmark", "rationale": "ELEVATED_TIMEOUT_MS (#317)"},
    # Memory / sessions (tortoise/session_*)
    {"id": "aq043", "query": "session memory reconciliation", "domain": "memory", "rationale": "session_continuity + reconciliation"},
    {"id": "aq044", "query": "semantic search sessions", "domain": "memory", "rationale": "test_session_semantic_search"},
    {"id": "aq045", "query": "event store append schema", "domain": "memory", "rationale": "event_store GraphEvent schema"},
    # Auth / teams / hosted
    {"id": "aq046", "query": "api key team membership", "domain": "auth", "rationale": "membership_create / api_key"},
    {"id": "aq047", "query": "signup rate limit buckets", "domain": "auth", "rationale": "tortoise/abuse.py signup limiters"},
    {"id": "aq048", "query": "hosted quickstart deployment", "domain": "auth", "rationale": "docs/quickstart-cloud.md"},
    # Strategy / research / governance
    {"id": "aq049", "query": "gtm pricing decision rationale", "domain": "strategy", "rationale": "graph-scripts pricing decisions"},
    {"id": "aq050", "query": "org attribution research", "domain": "strategy", "rationale": "product/2026-07-08-org-attribution-research.md"},
]


def load_authored_queries() -> dict:
    """Load the committed authored query set (source of truth)."""
    return json.loads(AUTHORED_QUERIES_PATH.read_text())


def load_query_sets() -> tuple[dict, dict]:
    """(oracle_set, authored_set) from the committed JSONs."""
    return load_oracle_queries(), load_authored_queries()
