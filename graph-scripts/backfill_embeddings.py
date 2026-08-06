"""Backfill embeddings for nodes missing them (issue #160).

Computes 384-dim embeddings via all-MiniLM-L6-v2 (tortoise.embeddings) and
writes them as vecf32 so the HNSW vector index has data to query.

Usage:
    python3 scripts/backfill_embeddings.py [--dry-run] [--graph GRAPH] [--uri URI]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).

Requires the embeddings extra: pip install 'tortoise[embeddings]'
(or sentence-transformers + scikit-learn).
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_URI = "docker://:falkordb@localhost:16379/tortoise"
LABELS = ("Point", "Label", "Source", "Object", "Subject")
PROP_MAP = {
    "Point": "content",
    "Label": "name",
    "Source": "name",
    "Object": "name",
    "Subject": "name",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill embeddings for vector search")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    from falkordb import FalkorDB
    # Parse docker://user:pass@host:port/graph
    uri = args.uri
    if uri.startswith("docker://"):
        rest = uri[len("docker://"):]
        creds, hostport = rest.split("@", 1)
        user, _, pw = creds.partition(":")
        host, _, portpath = hostport.rpartition(":")
        port, _, graph_from_path = portpath.partition("/")
        graph_name = graph_from_path or args.graph
        kwargs = {"host": host, "port": int(port)}
        if user:
            kwargs["username"] = user
        if pw:
            kwargs["password"] = pw
        db = FalkorDB(**kwargs)
    else:
        raise SystemExit(f"Unsupported URI: {uri}")

    from tortoise.embeddings import compute_embedding
    if compute_embedding("probe") is None:
        print("❌ Embeddings unavailable — install 'tortoise[embeddings]' "
              "(sentence-transformers + scikit-learn).", file=sys.stderr)
        return 1

    g = db.select_graph(graph_name)
    total = updated = skipped = 0
    for label in LABELS:
        prop = PROP_MAP[label]
        rows = g.query(
            f"MATCH (n:{label}) WHERE n.embedding IS NULL AND n.{prop} IS NOT NULL "
            f"RETURN n.id, n.{prop}"
        ).result_set
        if not rows:
            continue
        batch = rows if not args.limit else rows[: args.limit]
        print(f"{label}: {len(rows)} missing embeddings (processing {len(batch)})")
        for nid, text in batch:
            total += 1
            emb = compute_embedding(str(text))
            if emb is None:
                skipped += 1
                continue
            if args.dry_run:
                print(f"  [dry] {label} {nid}: would write {len(emb)}-dim")
                updated += 1
                continue
            g.query(
                f"MATCH (n:{label} {{id: $id}}) "
                f"SET n.embedding = CASE WHEN $emb IS NOT NULL THEN vecf32($emb) ELSE n.embedding END",
                params={"id": nid, "emb": emb},
            )
            updated += 1
            if total % 25 == 0:
                print(f"  ... {total} processed")

    print(f"\nDone: {total} scanned, {updated} embedded, {skipped} skipped (embedding unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
