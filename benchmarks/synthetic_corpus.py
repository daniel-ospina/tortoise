"""synthetic_corpus — EP-structured corpus seeding for the #316 benchmark.

Generates a Point corpus with real graph shape: random 384-dim embeddings
(batch UNWIND + vecf32, matching sdk._upsert_point_props), posterior_alpha/
beta on a fraction (EP evidence), operator Points with IMPL edges, pointKind
spread over a small taxonomy, and a retracted fraction (#689). Content drawn
from a token pool so FTS queries grounded in `query_mix.json` tokens match.

Index policy (scoping task 3: "verify-not-create"):
    `_ensure_indexes` auto-creates the Point HNSW + FTS indexes at boot. The
    benchmark VERIFIES they exist (CALL db.indexes()) and records in
    provenance whether they pre-existed or were auto-created — never silently
    creates them itself.

Embedded note: FalkorDBLite (redislite) has no HNSW vector index — the vector
arm falls back to brute-force; the FULLTEXT index EXISTS on embedded
(verify_indexes reports fts=true; FTS populated 40/100 queries in the #1144
committed baseline), structural degrades to empty without a kind. Numbers
from embedded runs are NOT prod-parity (scoping: "embedded FalkorDBLite
excluded; numbers can reverse") — the Docker/HNSW path is the measurement
environment.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import dataclass, field
from typing import Any

EMBEDDING_DIM = 384          # all-MiniLM-L6-v2 (matches projection HNSW 384-d)

KIND_WEIGHTS: list[tuple[str, float]] = [
    ("claim", 0.35),
    ("decision", 0.20),
    ("term", 0.15),
    ("plan", 0.10),
    ("question", 0.10),
    ("event", 0.05),
    ("source", 0.05),
]

# Token pool — query_mix.json queries are grounded in these tokens so the
# FTS/vector/TF-IDF arms actually match corpus content.
TOKENS: list[str] = [
    "zero-cost", "onboarding", "pricing", "tiers", "enterprise", "plans",
    "memory", "retention", "sessions", "graph", "traversal", "operators",
    "edges", "embedding", "dimension", "vector", "index", "belief",
    "propagation", "posterior", "update", "full", "text", "search", "point",
    "content", "semantic", "similarity", "claims", "hybrid", "ranking",
    "fusion", "strategy", "degraded", "fallback", "database", "circuit",
    "breaker", "latency", "protection", "epistemic", "annotation",
    "confidence", "evidence", "relationships", "concepts", "agent", "storage",
    "cross", "entity", "retrieval", "source", "events", "contested",
    "variance", "threshold", "structured", "kind", "recall", "completeness",
    "full-scan", "score", "floor", "filter", "truncation", "batch",
    "efficiency", "traversal-predicate", "retracted", "is-operator", "flag",
    "expansion", "families", "posterior-stability", "uncalibrated",
    "uninformative", "prior", "migration", "legacy", "store",
    "reconciliation", "sweep", "canonical", "url", "identity", "decision",
    "rationale", "tradeoffs", "milestones", "timeline", "owners", "question",
    "evidence-links", "definition", "glossary", "vocabulary", "checklist",
    "activation", "experiment", "conversion", "metrics", "offline",
    "docker", "container", "resource", "limits", "warmup", "steady",
    "percentile", "distribution", "tail", "synthetic", "scaling", "arms",
    "right-censored", "elapsed", "timers", "elevated", "timeout",
    "uncensored", "completion", "cold-start", "boot", "verdict", "band",
    "cap-dominated", "provenance", "git", "sha", "fingerprint", "handoff",
]

CONTENT_LEN_RANGE = (8, 20)


def _content(rng: random.Random) -> str:
    return " ".join(rng.choices(TOKENS, k=rng.randint(*CONTENT_LEN_RANGE)))


def _vector(rng: random.Random) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]


@dataclass
class CorpusFingerprint:
    """Stable fingerprint of the seeded corpus (goes in report provenance)."""

    n_points: int
    kinds: dict[str, int] = field(default_factory=dict)
    n_operators: int = 0
    n_operator_edges: int = 0
    n_retracted: int = 0
    n_with_posterior: int = 0
    dim: int = EMBEDDING_DIM
    seed: int = 42
    batch_size: int = 500

    def to_dict(self) -> dict:
        return {
            "n_points": self.n_points,
            "kinds": self.kinds,
            "n_operators": self.n_operators,
            "n_operator_edges": self.n_operator_edges,
            "n_retracted": self.n_retracted,
            "n_with_posterior": self.n_with_posterior,
            "dim": self.dim,
            "seed": self.seed,
            "batch_size": self.batch_size,
            "generator": "benchmarks.synthetic_corpus",
        }


def generate_points(
    n: int, *, seed: int = 42, retracted_fraction: float = 0.02,
    posterior_fraction: float = 0.5, op_every: int = 50,
) -> tuple[list[dict], CorpusFingerprint]:
    """Generate `n` point dicts WITHOUT touching a graph (pure, testable).

    Each dict: {id, content, pointKind, is_operator, status, embedding,
    posterior_alpha, posterior_beta}. Structural non-empty is asserted by the
    caller after seeding (scoping: "structural non-empty asserted").
    """
    rng = random.Random(seed)
    points: list[dict] = []
    kind_counts: dict[str, int] = {}
    n_ops = 0
    n_retracted = 0
    n_posterior = 0
    for i in range(n):
        kind = rng.choices(
            [k for k, _ in KIND_WEIGHTS], weights=[w for _, w in KIND_WEIGHTS]
        )[0]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        is_op = (i % op_every == 0)  # deterministic operator cadence
        retracted = (not is_op) and rng.random() < retracted_fraction
        if is_op:
            n_ops += 1
        if retracted:
            n_retracted += 1
        with_post = (not is_op) and rng.random() < posterior_fraction
        if with_post:
            n_posterior += 1
        p = {
            "id": f"p-{i:07d}",
            "content": _content(rng),
            "pointKind": "operator" if is_op else kind,
            "is_operator": is_op,
            "status": "retracted" if retracted else "live",
            "embedding": _vector(rng),
        }
        if with_post:
            # Beta(α, β) posterior parameters — EP evidence shape.
            alpha = rng.randint(1, 40)
            p["posterior_alpha"] = float(alpha)
            p["posterior_beta"] = float(rng.randint(1, max(40 - alpha, 1)))
        points.append(p)
    fp = CorpusFingerprint(
        n_points=n, kinds=kind_counts, n_operators=n_ops,
        n_retracted=n_retracted, n_with_posterior=n_posterior, seed=seed,
    )
    return points, fp


def _point_exists(graph, pid: str) -> bool:
    rows = graph.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": pid}
    ).result_set
    return bool(rows and rows[0][0] > 0)


def seed_corpus(graph, points: list[dict], *, batch_size: int = 500) -> int:
    """Batch-insert points via UNWIND + vecf32 (idempotent per point id).

    Matches the production write path's vecf32 encoding (sdk.py point-write
    SET clause) so the stored vectors are byte-identical to real writes.
    Returns points created.

    #1348: confidence is written ONLY when the point dict carries it (the
    enhance_signals path) — the SET is CASE-gated on row.confidence so a
    plain corpus never gains (or loses) a written confidence value, and
    re-seeding over an existing graph preserves prior confidence.
    """
    created = 0
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        rows = graph.query(
            """
            UNWIND $batch AS row
            MERGE (n:Point {id: row.id})
            SET n.content = row.content, n.pointKind = row.pointKind,
                n.is_operator = row.is_operator, n.status = row.status,
                n.createdAt = timestamp(), n.updatedAt = timestamp()
            SET n.embedding = vecf32(row.embedding)
            SET n.posterior_alpha = row.posterior_alpha
            SET n.posterior_beta = row.posterior_beta
            SET n.confidence = CASE WHEN row.confidence IS NOT NULL
                                    THEN row.confidence ELSE n.confidence END
            RETURN count(n) AS created
            """,
            params={"batch": [
                {k: v for k, v in p.items() if k != "embedding"}
                | {"embedding": p["embedding"]}
                for p in batch
            ]},
        ).result_set
        if rows:
            created += int(rows[0][0])
    return created


def seed_operator_edges(graph, rng: random.Random, n_edges_per_op: int = 200,
                        max_total: int = 5000, *,
                        topic_correlated: bool = False,
                        oracle=None) -> int:
    """Wire operator→source IMPL edges (epistemic structure) in batches.

    Each operator gets up to `n_edges_per_op` IMPL edges to random non-operator
    source points, capped globally at `max_total` (scaling arms must not blow
    up graph size). Batched via UNWIND MERGE — one query per 500 edges.
    Returns edges created.

    #1348 topic_correlated=True: bias edge targets toward points whose HIDDEN
    topic matches the operator's topic — connectivity becomes topic-correlated
    (target-topic points accrue IMPL edges). No NEW nodes are created (edges
    connect existing operator/point nodes only), so FTS/vector inputs and
    oracle denominators (live_ids_by_topic, r@10 grade-2 sets) are unchanged.
    The edge COUNT may collide at max_total — the extended fingerprint hashes
    the (op,target) edge-pair LIST to distinguish enhanced vs plain.
    """
    ops = graph.query(
        "MATCH (op:Point {is_operator:true}) RETURN op.id, op.content"
    ).result_set
    sources = graph.query(
        "MATCH (n:Point) WHERE n.is_operator <> true RETURN n.id, n.content"
    ).result_set
    if not ops or not sources:
        return 0
    op_ids = [r[0] for r in ops]
    src_ids = [r[0] for r in sources]

    # #1348: for topic-correlated edges we need the hidden topic per point.
    # The graph does not store `topic` (seed_corpus SET is explicit-field), so
    # derive it from the content token overlap with each topic's vocab.
    src_topics: dict[str, int] = {}
    if topic_correlated and oracle is not None:
        # Operators are fetched WITH content too — their hidden topic is
        # derived the same way (code-review P2 fix: op_idx % oracle.n was a
        # permuted map; op content tokens are the robust source).
        for src_id, content in list(sources) + list(ops):
            best_t, best_n = 0, 0
            for t in oracle.core:
                vocab = oracle.vocab(t)
                n_hit = sum(1 for tok in (content or "").split() if tok in vocab)
                if n_hit > best_n:
                    best_t, best_n = t, n_hit
            src_topics[src_id] = best_t

    pairs: list[dict] = []
    remaining = max_total
    for op_idx, op_id in enumerate(op_ids):  # noqa: B007
        if remaining <= 0:
            break
        if topic_correlated and oracle is not None:
            # #1348 code-review P2 fix: derive the operator's topic from its
            # OWN content tokens (same method as src_topics) — NOT (op_idx %
            # oracle.n), which was a PERMUTED map when op_every doesn't divide
            # oracle.n, and NOT (op_idx * op_every) which hardcodes the
            # generate_oracle_points op spacing into this function.
            op_topic = src_topics.get(op_id, 0)
            # Bias: ~half the edges to same-topic sources, half random (the
            # docstring previously claimed 70/30 — corrected to match code).
            same = [s for s in src_ids if src_topics.get(s) == op_topic]
            other = [s for s in src_ids if src_topics.get(s) != op_topic]
            n_same = min(n_edges_per_op // 2, len(same), remaining)
            n_other = min(n_edges_per_op - n_same, len(other), remaining - n_same)
            targets = (rng.sample(same, n_same) if same else []) + \
                      (rng.sample(other, n_other) if other else [])
            rng.shuffle(targets)
        else:
            targets = rng.sample(src_ids, min(n_edges_per_op, len(src_ids), remaining))
        pairs.extend({"a": op_id, "b": t} for t in targets)
        remaining -= len(targets)
    created = 0
    for start in range(0, len(pairs), 500):
        batch = pairs[start : start + 500]
        graph.query(
            "UNWIND $pairs AS e "
            "MATCH (a:Point {id: e.a}), (b:Point {id: e.b}) "
            "MERGE (a)-[:IMPL {idx: 0}]->(b)",
            params={"pairs": batch},
        )
        created += len(batch)
    return created


def verify_indexes(proj) -> dict[str, bool]:
    """verify-not-create: check which retrieval indexes actually exist.

    Returns {"fts": bool, "vector": bool} by scanning CALL db.indexes().
    Auto-created-at-boot indexes (HNSW + FTS via _ensure_indexes) show up
    here on Docker FalkorDB ≥4.x. On embedded FalkorDBLite the FULLTEXT index
    EXISTS (fts=true — FTS populated 40/100 queries in the committed #1144
    baseline) while the HNSW vector index is absent (vector runs brute-force).
    """
    found: dict[str, bool] = {"fts": False, "vector": False}
    try:
        rows = proj.g.query("CALL db.indexes()").result_set
        for row in rows:
            info = " ".join(str(c) for c in row).lower()
            if "fulltext" in info or "text" in info:
                found["fts"] = True
            if "vector" in info or "hnsw" in info:
                found["vector"] = True
    except Exception:
        pass  # CALL db.indexes() unsupported (older embedded) → both False
    return found


def corpus_fingerprint_from_graph(graph) -> dict[str, Any]:
    """Compute the corpus fingerprint from a live graph (provenance).

    #1348 extended: adds n_with_confidence + embeddings sha256 + edge-pair
    hash so the enhancement state is DETECTABLE — embeddings + per-topic live
    counts are byte-identical by design across plain/enhanced, and the edge
    COUNT collides at max_total=5000 (only the edge-pair LIST differs).
    """
    rows = graph.query(
        "MATCH (n:Point) RETURN n.pointKind, count(n), "
        "sum(CASE WHEN n.is_operator = true THEN 1 ELSE 0 END), "
        "sum(CASE WHEN n.status = 'retracted' THEN 1 ELSE 0 END), "
        "sum(CASE WHEN n.posterior_alpha IS NOT NULL THEN 1 ELSE 0 END), "
        "sum(CASE WHEN n.confidence IS NOT NULL THEN 1 ELSE 0 END)"
    ).result_set
    kinds: dict[str, int] = {}
    total = 0
    n_ops = 0
    n_retracted = 0
    n_post = 0
    n_conf = 0
    for row in rows:
        kinds[str(row[0])] = int(row[1])
        total += int(row[1])
        n_ops += int(row[2] or 0)
        n_retracted += int(row[3] or 0)
        n_post += int(row[4] or 0)
        n_conf += int(row[5] or 0)
    n_edges = 0
    edge_pairs_hash = None
    try:
        erows = graph.query(
            "MATCH (:Point)-[r:IMPL]->(:Point) RETURN count(r)"
        ).result_set
        if erows:
            n_edges = int(erows[0][0])
        # #1348 edge-pair LIST hash (distinguishes enhanced vs plain when the
        # count collides at max_total). Deterministic: ORDER BY both endpoints.
        pr = graph.query(
            "MATCH (a:Point)-[r:IMPL]->(b:Point) "
            "RETURN a.id, b.id ORDER BY a.id, b.id"
        ).result_set
        if pr:
            import hashlib
            h = hashlib.sha256()
            for ra, rb in pr:
                h.update(f"{ra}|{rb}".encode())
            edge_pairs_hash = h.hexdigest()[:16]
    except Exception:
        pass
    # #1348 embeddings sha256 (ORDER BY id for determinism).
    embeddings_hash = None
    try:
        erows = graph.query(
            "MATCH (n:Point) WHERE n.embedding IS NOT NULL "
            "RETURN n.id, n.embedding ORDER BY n.id"
        ).result_set
        if erows:
            import hashlib
            h = hashlib.sha256()
            for _pid, vec in erows:
                try:
                    h.update(bytes(vec))
                except Exception:
                    h.update(repr(vec).encode())
            embeddings_hash = h.hexdigest()[:16]
    except Exception:
        pass
    return {
        "n_points": total,
        "kinds": kinds,
        "n_operators": n_ops,
        "n_retracted": n_retracted,
        "n_with_posterior": n_post,
        "n_with_confidence": n_conf,
        "n_operator_edges": n_edges,
        "edge_pairs_hash": edge_pairs_hash,
        "embeddings_hash": embeddings_hash,
    }


# ── Latent-topic oracle layer (issue #1144 retrieval eval) ────────────────────
#
# The plain token-pool corpus (#316) cannot measure retrieval QUALITY: every
# point is drawn from the SAME token pool, so any point containing query
# tokens counts as a match and P@K approaches 1.0 for every strategy (no
# signal). The oracle fixes this by giving the corpus a hidden latent-topic
# structure:
#
#   - TOKENS is partitioned into N_ORACLE_TOPICS sequential chunks (topics
#     are thematic — consecutive tokens are related, mirroring real docs).
#   - Each seeded point gets a HIDDEN topic; its content is drawn ONLY from
#     its topic's vocabulary and its embedding is clustered around the
#     topic's deterministic centroid (0.75 centroid + 0.25 noise). The topic
#     is NOT written to the graph — retrieval strategies never see it.
#   - Each topic boundary carries a BRIDGE token shared by the two adjacent
#     topics (NEAR neighbors). Bridge tokens create controlled distractors:
#     a point in a near topic that token-matches the query is graded 1
#     (partially relevant) and non-target topics are graded 0 — even when
#     their points contain query tokens (false friends from mixed-token
#     queries, which the old corpus could never express).
#   - Relevance for a query with target topic T is DERIVED deterministically:
#     grade 2 = live points in T, grade 1 = live points in NEAR(T),
#     grade 0 = everything else. No LLM, no human labels, fully repeatable.
#
# This makes strategies measurable: easy queries (all tokens in T's core)
# are near-perfect for token and cluster matchers; hard queries (bridge/
# neighbor-heavy "ambiguous near-miss" text) pull token matchers toward
# grade-1 distractors, so P@K < 1.0 and nDCG separates strategies.

N_ORACLE_TOPICS = 24                     # 131 pool tokens / ~5.5 tokens per topic
ORACLE_EMBED_ALPHA = 0.75                # centroid weight in point embeddings
ORACLE_CONTENT_LEN_RANGE = (4, 8)        # oracle content draws from a ~7-token vocab
ORACLE_QUERY_NOISE = 0.35                # noise on the synthetic query vector


def _unit(vec: list[float]) -> list[float]:
    """L2-normalize a vector (guard: zero vector stays zero)."""
    norm = sum(v * v for v in vec) ** 0.5
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


@dataclass
class TopicOracle:
    """Latent-topic structure of the oracle corpus (#1144).

    Deterministic from the seed: topics partition TOKENS into chunks; each
    topic has a core (exclusive tokens), one bridge token shared with the
    NEXT topic (mod N), and a unit-norm centroid used to cluster embeddings.
    """

    core: dict[int, list[str]]            # topic id -> exclusive core tokens
    bridges: dict[int, str]               # topic id -> token shared with (id+1) % n
    token_to_topics: dict[str, list[int]]  # token -> owning topic ids (1 or 2)
    centroids: dict[int, list[float]]     # topic id -> unit-norm 384-d centroid
    n: int = N_ORACLE_TOPICS

    def vocab(self, topic: int) -> list[str]:
        """Tokens a point in `topic` may contain: core + own bridge + the
        previous topic's bridge (so NEAR neighbors share surface tokens)."""
        return self.core[topic] + [self.bridges[topic], self.bridges[(topic - 1) % self.n]]

    def near(self, topic: int) -> list[int]:
        """NEAR neighbors of `topic`: the two topics sharing a bridge token.
        Points here are PARTIALLY relevant (grade 1) to a topic-`topic` query."""
        return [(topic - 1) % self.n, (topic + 1) % self.n]

    def centroid_of_token(self, token: str) -> list[float] | None:
        """Vector for one token: its owning topic's centroid (bridge tokens →
        the midpoint of the two owning centroids, unit-normalized). This is
        the synthetic stand-in for a real embedding model (see
        `query_vector_for`); it encodes token semantics, never oracle labels."""
        owners = self.token_to_topics.get(token)
        if not owners:
            return None
        if len(owners) == 1:
            return self.centroids[owners[0]]
        a, b = self.centroids[owners[0]], self.centroids[owners[1]]
        return _unit([a[i] + b[i] for i in range(len(a))])

    def query_vector_for(self, query: str, seed: int, noise: float = ORACLE_QUERY_NOISE) -> list[float]:
        """Synthetic query embedding for `query` (embedded-mode stand-in).

        Sums the token centroids of the query's tokens (bridge tokens pull
        toward both owning topics — exactly the ambiguity a real model would
        encode), adds seeded Gaussian noise, normalizes. Deterministic per
        (query, seed). Queries with no known token fall back to the #316
        deterministic pseudo-random vector (run_report._query_vec_for
        pattern) so the vector arm always has a vector to run.
        """
        import re as _re
        tokens = [t for t in _re.split(r"[^a-z0-9]+", query.lower()) if t]
        vecs = [self.centroid_of_token(t) for t in tokens if t in self.token_to_topics]
        if not vecs:
            rng = random.Random(f"{seed}:{query}")
            return [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
        dim = len(vecs[0])
        rng = random.Random(f"{seed}:oracle-vec:{query}")
        # Unit-normalized noise: a raw 384-d gaussian has norm ~sqrt(384) ~ 20
        # and would drown the centroid signal (verified: dist(qv, c0) ~ 1.15
        # instead of ~0.12 without normalization).
        noise_vec = _unit([rng.gauss(0.0, 1.0) for _ in range(dim)])
        vec = [0.0] * dim
        for v in vecs:
            for i in range(dim):
                vec[i] += v[i]
        for i in range(dim):
            vec[i] += noise * noise_vec[i]
        return _unit(vec)

    def token_topic_assignment(self, query: str) -> int | None:
        """Deterministic target topic for a query: argmax over topics of the
        number of query tokens in the topic's vocab; ties → lowest topic id.
        None when no query token is in any topic vocab."""
        import re as _re
        tokens = [t for t in _re.split(r"[^a-z0-9]+", query.lower()) if t]
        counts: dict[int, int] = {}
        for tok in tokens:
            for owner in self.token_to_topics.get(tok, []):
                counts[owner] = counts.get(owner, 0) + 1
        if not counts:
            return None
        best = max(counts.values())
        return min(k for k, v in counts.items() if v == best)

    def to_dict(self) -> dict:
        return {
            "n_topics": self.n,
            "core": {k: list(v) for k, v in self.core.items()},
            "bridges": dict(self.bridges),
            "near": {k: self.near(k) for k in self.core},
        }


def build_topic_oracle(seed: int = 42) -> TopicOracle:
    """Deterministically partition TOKENS into N_ORACLE_TOPICS topics.

    Sequential chunks keep the pool's thematic ordering (pricing terms stay
    together, retrieval terms stay together, ...). Each chunk's LAST token is
    its bridge — shared with the next topic (mod N). Leftover tokens (150 =
    24*6 + 6) are appended to the first 6 topics' cores.
    """
    n = N_ORACLE_TOPICS
    base = len(TOKENS) // n          # 6
    extra = len(TOKENS) - base * n   # 6
    chunks: dict[int, list[str]] = {}
    idx = 0
    for k in range(n):
        size = base + (1 if k < extra else 0)
        chunks[k] = TOKENS[idx:idx + size]
        idx += size
    core: dict[int, list[str]] = {}
    bridges: dict[int, str] = {}
    token_to_topics: dict[str, list[int]] = {}
    for k in range(n):
        chunk = chunks[k]
        bridge = chunk[-1]
        core[k] = chunk[:-1]
        bridges[k] = bridge
        token_to_topics[bridge] = [k, (k + 1) % n]
        for tok in chunk[:-1]:
            token_to_topics.setdefault(tok, []).append(k)
    rng = random.Random(seed * 7919)
    centroids: dict[int, list[float]] = {}
    for k in range(n):
        raw = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
        centroids[k] = _unit(raw)
    return TopicOracle(
        core=core, bridges=bridges,
        token_to_topics=token_to_topics, centroids=centroids, n=n,
    )


def generate_oracle_points(
    n: int, oracle: TopicOracle, *, seed: int = 42,
    retracted_fraction: float = 0.02, posterior_fraction: float = 0.5,
    op_every: int = 50, enhance_signals: bool = False,
) -> tuple[list[dict], dict[int, int]]:
    """Generate `n` oracle point dicts WITHOUT touching a graph.

    Same point shape as `generate_points` (id/content/pointKind/is_operator/
    status/embedding/posterior_*) plus a HIDDEN `topic` int. Topics are
    round-robin (topic = i % n_topics) so every topic gets a balanced slice;
    content draws from the topic vocab; embeddings cluster around the topic
    centroid (ORACLE_EMBED_ALPHA). The `topic` key is intentionally NOT
    written by seed_corpus (its SET clause is explicit-field) — the oracle
    never leaks into the graph.

    #1348 enhance_signals=True: adds topic-correlated `confidence` for the
    GraphRanker verdict. RNG CONTRACT (byte-identity): posterior alpha/beta
    stay on the MAIN seeded rng stream in both paths (identical per-iteration
    draw counts); confidence is DERIVED from those same drawn values mapped
    through topic membership (noisy per-topic spread with overlap — NOT a
    clean per-topic constant, which would be tautological since topic
    membership defines oracle grades). Posterior-less points get coalesce-0.5
    (the GraphRanker default — simplest rng contract; coverage dilution is a
    report field). Isolated Random(seed ^ salt) is used ONLY for extra draws
    (topic-bias noise here; edge selection in seed_operator_edges) — never the
    main stream.

    Returns (points, topic_counts) where topic_counts maps topic id → number
    of LIVE (non-retracted, non-operator) points, the oracle's grade-2
    denominator for that topic."""
    rng = random.Random(seed)
    # #1348 isolated stream for ENHANCEMENT-ONLY extra draws (never touches
    # the main stream — byte-identity holds for content/embeddings/posteriors).
    enh_rng = random.Random(seed ^ 0x1348) if enhance_signals else None
    topic_biases = {}
    if enhance_signals:
        for t in oracle.core:
            # Noisy per-topic mean shift in [-0.3, +0.3] — overlapping ranges.
            topic_biases[t] = round(enh_rng.uniform(-0.3, 0.3), 4)
    points: list[dict] = []
    topic_counts: dict[int, int] = {k: 0 for k in oracle.core}
    for i in range(n):
        topic = i % oracle.n
        is_op = (i % op_every == 0)
        retracted = (not is_op) and rng.random() < retracted_fraction
        with_post = (not is_op) and rng.random() < posterior_fraction
        vocab = oracle.vocab(topic)
        content = " ".join(rng.sample(vocab, min(rng.randint(*ORACLE_CONTENT_LEN_RANGE), len(vocab))))
        emb = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
        centroid = oracle.centroids[topic]
        # Unit-normalized noise: a raw 384-d gaussian has norm ~sqrt(384) ~ 20
        # and would dominate the 0.75-weight centroid (verified: point
        # embeddings were noise-dominated, collapsing all topical signal).
        noise_vec = _unit(emb)
        embedding = _unit([
            ORACLE_EMBED_ALPHA * centroid[j] + (1.0 - ORACLE_EMBED_ALPHA) * noise_vec[j]
            for j in range(EMBEDDING_DIM)
        ])
        kind = rng.choices(
            [k for k, _ in KIND_WEIGHTS], weights=[w for _, w in KIND_WEIGHTS]
        )[0]
        p = {
            "id": f"p-{i:07d}",
            "content": content,
            "pointKind": "operator" if is_op else kind,
            "is_operator": is_op,
            "status": "retracted" if retracted else "live",
            "embedding": embedding,
            "topic": topic,
        }
        if with_post:
            alpha = rng.randint(1, 40)
            p["posterior_alpha"] = float(alpha)
            p["posterior_beta"] = float(rng.randint(1, max(40 - alpha, 1)))
            if enhance_signals and not is_op:
                # #1348 pinned construction: noisy topic bias + alpha-derived
                # noise — overlapping ranges, NOT recoverable from topic alone.
                conf = 0.5 + topic_biases[topic] + ((alpha - 20) / 40) * 0.2
                p["confidence"] = round(max(0.0, min(1.0, conf)), 4)
        elif enhance_signals and not is_op:
            # Posterior-less: coalesce-0.5 fallback (simplest rng contract).
            p["confidence"] = 0.5
        points.append(p)
        if not is_op and not retracted:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    return points, topic_counts


def oracle_grades_for_query(
    oracle: TopicOracle, target_topic: int,
    live_ids_by_topic: dict[int, list[str]],
) -> dict[str, int]:
    """Deterministic graded relevance (issue #1144): grade 2 for target-topic
    points, grade 1 for NEAR-topic points, grade 0 for everything else.
    `live_ids_by_topic` must contain only live (non-retracted) point ids.
    Returns {point_id: 0|1|2} for every live point."""
    labels: dict[str, int] = {}
    for topic, ids in live_ids_by_topic.items():
        grade = 2 if topic == target_topic else (1 if topic in oracle.near(target_topic) else 0)
        for pid in ids:
            labels[pid] = grade
    return labels


def default_corpus_path(workdir: str | None = None) -> str:
    """Corpus db path: a temp-file embedded DB (or a named file under workdir)."""
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        return os.path.join(workdir, "bench-corpus.db")
    return os.path.join(tempfile.mkdtemp(prefix="tortoise-bench_"), "corpus.db")


def load_query_mix(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


__all__ = [  # noqa: RUF022
    "EMBEDDING_DIM", "KIND_WEIGHTS", "TOKENS", "CorpusFingerprint",
    "generate_points", "seed_corpus", "seed_operator_edges", "verify_indexes",
    "corpus_fingerprint_from_graph", "default_corpus_path", "load_query_mix",
    # #1144 latent-topic oracle layer
    "N_ORACLE_TOPICS", "ORACLE_EMBED_ALPHA", "TopicOracle",
    "build_topic_oracle", "generate_oracle_points", "oracle_grades_for_query",
]
