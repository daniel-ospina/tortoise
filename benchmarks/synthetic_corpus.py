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

Embedded note: FalkorDBLite (redislite) has no FTS/HNSW — the vector arm
falls back to brute-force and FTS degrades. Numbers from embedded runs are
NOT prod-parity (scoping: "embedded FalkorDBLite excluded; numbers can
reverse") — the Docker/HNSW path is the measurement environment.
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

    Matches the production write path's vecf32 encoding (sdk.py:980) so the
    stored vectors are byte-identical to real writes. Returns points created.
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
                        max_total: int = 5000) -> int:
    """Wire operator→source IMPL edges (epistemic structure) in batches.

    Each operator gets up to `n_edges_per_op` IMPL edges to random non-operator
    source points, capped globally at `max_total` (scaling arms must not blow
    up graph size). Batched via UNWIND MERGE — one query per 500 edges.
    Returns edges created."""
    ops = graph.query(
        "MATCH (op:Point {is_operator:true}) RETURN op.id"
    ).result_set
    sources = graph.query(
        "MATCH (n:Point) WHERE n.is_operator <> true RETURN n.id"
    ).result_set
    if not ops or not sources:
        return 0
    op_ids = [r[0] for r in ops]
    src_ids = [r[0] for r in sources]
    pairs: list[dict] = []
    remaining = max_total
    for op_id in op_ids:
        if remaining <= 0:
            break
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
    here on Docker FalkorDB ≥4.x; embedded reports neither (expected).
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
    """Compute the corpus fingerprint from a live graph (provenance)."""
    rows = graph.query(
        "MATCH (n:Point) RETURN n.pointKind, count(n), "
        "sum(CASE WHEN n.is_operator = true THEN 1 ELSE 0 END), "
        "sum(CASE WHEN n.status = 'retracted' THEN 1 ELSE 0 END), "
        "sum(CASE WHEN n.posterior_alpha IS NOT NULL THEN 1 ELSE 0 END)"
    ).result_set
    kinds: dict[str, int] = {}
    total = 0
    n_ops = 0
    n_retracted = 0
    n_post = 0
    for row in rows:
        kinds[str(row[0])] = int(row[1])
        total += int(row[1])
        n_ops += int(row[2] or 0)
        n_retracted += int(row[3] or 0)
        n_post += int(row[4] or 0)
    n_edges = 0
    try:
        erows = graph.query(
            "MATCH (:Point)-[r:IMPL]->(:Point) RETURN count(r)"
        ).result_set
        if erows:
            n_edges = int(erows[0][0])
    except Exception:
        pass
    return {
        "n_points": total,
        "kinds": kinds,
        "n_operators": n_ops,
        "n_retracted": n_retracted,
        "n_with_posterior": n_post,
        "n_operator_edges": n_edges,
    }


def default_corpus_path(workdir: str | None = None) -> str:
    """Corpus db path: a temp-file embedded DB (or a named file under workdir)."""
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        return os.path.join(workdir, "bench-corpus.db")
    return os.path.join(tempfile.mkdtemp(prefix="tortoise-bench_"), "corpus.db")


def load_query_mix(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


__all__ = [
    "EMBEDDING_DIM", "KIND_WEIGHTS", "TOKENS", "CorpusFingerprint",
    "generate_points", "seed_corpus", "seed_operator_edges", "verify_indexes",
    "corpus_fingerprint_from_graph", "default_corpus_path", "load_query_mix",
]
