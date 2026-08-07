"""Backfill embeddings for nodes missing them (issue #160).

Computes 384-dim embeddings via all-MiniLM-L6-v2 (tortoise.embeddings) and
writes them as vecf32 so the HNSW vector index has data to query.

Idempotent: only nodes where `embedding IS NULL` are touched, so re-running
after a partial failure is safe and never recomputes existing vectors.

Usage:
    python3 scripts/backfill_embeddings.py [--dry-run] [--graph GRAPH]
        [--uri URI] [--limit N] [--batch-size N]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).

Requires the embeddings extra: pip install 'tortoise[embeddings]'
(or sentence-transformers + scikit-learn). --dry-run only reports counts and
does NOT require the model.
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_URI = "docker://:falkordb@localhost:16379/tortoise"
DEFAULT_BATCH = 500
LABELS = ("Point", "Label", "Source", "Object", "Subject")
PROP_MAP = {
    "Point": "content",
    "Label": "name",
    "Source": "name",
    "Object": "name",
    "Subject": "name",
}


def connect(uri: str, default_graph: str):
    """Connect to FalkorDB, returning (graph, resolved_graph_name)."""
    from falkordb import FalkorDB

    if uri.startswith("docker://"):
        rest = uri[len("docker://"):]
        creds, hostport = rest.split("@", 1)
        user, _, pw = creds.partition(":")
        host, _, portpath = hostport.rpartition(":")
        port, _, graph_from_path = portpath.partition("/")
        graph_name = graph_from_path or default_graph
        kwargs = {"host": host, "port": int(port)}
        if user:
            kwargs["username"] = user
        if pw:
            kwargs["password"] = pw
        db = FalkorDB(**kwargs)
    else:
        raise SystemExit(f"Unsupported URI: {uri}")
    return db.select_graph(graph_name), graph_name


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill embeddings for vector search")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise")
    ap.add_argument("--limit", type=int, default=0, help="0 = all (total cap)")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                    help="rows per query/compute batch (default: %(default)s)")
    args = ap.parse_args()

    g, graph_name = connect(args.uri, args.graph)
    print(f"Graph: {graph_name}")

    # ── Dry run — counts only, no model required ─────────────────────────
    if args.dry_run:
        total = 0
        for label in LABELS:
            rows = g.query(
                f"MATCH (n:{label}) WHERE n.embedding IS NULL "
                f"AND n.{PROP_MAP[label]} IS NOT NULL RETURN count(n)"
            ).result_set
            n = rows[0][0] if rows else 0
            if n:
                print(f"{label}: {n} missing embeddings")
                total += n
        print(f"\nDry run: {total} nodes would be embedded (no writes)")
        return 0

    from tortoise.embeddings import compute_embedding
    if compute_embedding("probe") is None:
        print("❌ Embeddings unavailable — install 'tortoise[embeddings]' "
              "(sentence-transformers + scikit-learn).", file=sys.stderr)
        return 1

    total = updated = skipped = 0
    for label in LABELS:
        prop = PROP_MAP[label]
        # Collect (nid, text) in bounded queries first — avoids re-pagination
        # drift from SET mutating the WHERE predicate mid-scan.
        rows_all: list = []
        offset = 0
        while True:
            rows = g.query(
                f"MATCH (n:{label}) WHERE n.embedding IS NULL "
                f"AND n.{prop} IS NOT NULL "
                f"RETURN n.id, n.{prop} SKIP $skip LIMIT $limit",
                params={"skip": offset, "limit": args.batch_size},
            ).result_set
            if not rows:
                break
            rows_all.extend(rows)
            offset += len(rows)
            if args.limit and offset >= args.limit:
                break
        if args.limit:
            rows_all = rows_all[: args.limit]
        if not rows_all:
            continue
        print(f"{label}: {len(rows_all)} missing embeddings")

        # Compute + write in batches (bounded memory, cheap queries).
        for start in range(0, len(rows_all), args.batch_size):
            for nid, text in rows_all[start:start + args.batch_size]:
                total += 1
                emb = compute_embedding(str(text))
                if emb is None:
                    skipped += 1
                    continue
                g.query(
                    f"MATCH (n:{label} {{id: $id}}) "
                    f"SET n.embedding = CASE WHEN $emb IS NOT NULL "
                    f"THEN vecf32($emb) ELSE n.embedding END",
                    params={"id": nid, "emb": emb},
                )
                updated += 1
                if total % 25 == 0:
                    print(f"  ... {total} processed")

    print(f"\nDone: {total} scanned, {updated} embedded, "
          f"{skipped} skipped (embedding unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
