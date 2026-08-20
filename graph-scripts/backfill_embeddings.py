"""Backfill embeddings for nodes missing them (issue #160).

Computes 384-dim embeddings via all-MiniLM-L6-v2 (tortoise.embeddings) and
writes them as vecf32 so the HNSW vector index has data to query.

Idempotent: only nodes where `embedding IS NULL` are touched, so re-running
after a partial failure is safe and never recomputes existing vectors.

URI support: docker://, redis://, rediss:// (FalkorDB Cloud) via
FalkorProjection.from_uri().

Multi-tenant: --all-tenants queries the registry graph for team IDs and
iterates team_{team_id} graphs. Per-tenant backfill, one team at a time.

Usage:
    python3 graph-scripts/backfill_embeddings.py [--dry-run] [--graph GRAPH]
        [--uri URI] [--all-tenants] [--limit N] [--batch-size N]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).

Requires the embeddings extra: pip install 'tortoise-graph[embeddings]'
(or sentence-transformers + scikit-learn). --dry-run only reports counts and
does NOT require the model.
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_URI = "docker://:falkordb@localhost:16379/tortoise"
DEFAULT_BATCH = 500

# Entity types to embed + their text property (what gets vectorized) and
# id property (what the MATCH/SET Cypher uses as the node key).
# #448: Source canonical key is url, not id.
LABEL_CONFIG: dict[str, dict[str, str]] = {
    "Point":    {"text_prop": "content", "id_prop": "id"},
    "Event":    {"text_prop": "subject", "id_prop": "eventId"},
    "Document": {"text_prop": "title",   "id_prop": "id"},
    "Source":   {"text_prop": "url",     "id_prop": "url"},
    "Object":   {"text_prop": "name",    "id_prop": "id"},
    "Subject":  {"text_prop": "name",    "id_prop": "id"},
}


def _connect_falkordb(uri: str):
    """Connect to FalkorDB via FalkorProjection.from_uri().

    Supports docker:// (local), redis:// and rediss:// (FalkorDB Cloud).
    Returns (FalkorDB client, resolved_graph_name).
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(uri)
    # proj.db (public FalkorDB client) + proj.graph_name (public) — NOT the
    # private _db/_graph_name (code-review P0 fix: _db doesn't exist).
    return proj.db, proj.graph_name


def _dry_run(db, graph_name: str, labels: list[str]) -> int:
    """Count nodes missing embeddings. Returns total count."""
    g = db.select_graph(graph_name)
    total = 0
    for label in labels:
        cfg = LABEL_CONFIG[label]
        rows = g.query(
            f"MATCH (n:{label}) WHERE n.embedding IS NULL "
            f"AND n.{cfg['text_prop']} IS NOT NULL RETURN count(n)"
        ).result_set
        n = rows[0][0] if rows else 0
        if n:
            print(f"  {label}: {n} missing embeddings")
            total += n
    return total


def _backfill_graph(db, graph_name: str, labels: list[str],
                    limit: int, batch_size: int) -> tuple[int, int, int]:
    """Backfill one graph. Returns (scanned, updated, skipped)."""
    g = db.select_graph(graph_name)

    from tortoise.embeddings import compute_embedding

    total = updated = skipped = 0
    for label in labels:
        cfg = LABEL_CONFIG[label]
        text_prop = cfg["text_prop"]
        id_prop = cfg["id_prop"]

        # Collect (node_id, text) in bounded queries — avoids re-pagination
        # drift from SET mutating the WHERE predicate mid-scan.
        rows_all: list[tuple] = []
        offset = 0
        while True:
            if label == "Event":
                # #244: AgentSession events carry name/keywords/topics (not
                # subject) — embed the session surface (same composition as the
                # index-time path in session_indexer.py). Other event kinds
                # still embed via subject below (same loop iteration).
                from tortoise.session_indexer import session_embedding_text
                rows = g.query(
                    "MATCH (n:Event) WHERE n.eventKind = 'AgentSession' "
                    "AND n.embedding IS NULL AND n.name IS NOT NULL "
                    "RETURN n.eventId, n.name, n.keywords, n.topics, n.content_metadata "
                    "SKIP $skip LIMIT $limit",
                    params={"skip": offset, "limit": batch_size},
                ).result_set
                if not rows:
                    rows = g.query(
                        f"MATCH (n:Event) WHERE n.embedding IS NULL "
                        f"AND n.{text_prop} IS NOT NULL AND NOT (n.eventKind = 'AgentSession') "
                        f"RETURN n.{id_prop}, n.{text_prop} "
                        f"SKIP $skip LIMIT $limit",
                        params={"skip": offset, "limit": batch_size},
                    ).result_set
                rows_all.extend(
                    (r[0], session_embedding_text(r[1], "", r[2] or [], r[3] or []))
                    if r[1] is not None and len(r) > 2
                    else (r[0], r[1])
                    for r in rows
                )
            else:
                rows = g.query(
                    f"MATCH (n:{label}) WHERE n.embedding IS NULL "
                    f"AND n.{text_prop} IS NOT NULL "
                    f"RETURN n.{id_prop}, n.{text_prop} "
                    f"SKIP $skip LIMIT $limit",
                    params={"skip": offset, "limit": batch_size},
                ).result_set
                rows_all.extend(rows)
            if not rows:
                break
            offset += len(rows)
            if limit and offset >= limit:
                break
        if limit:
            rows_all = rows_all[:limit]
        if not rows_all:
            continue
        print(f"  {label}: {len(rows_all)} missing embeddings")

        # Compute + write in batches. UNWIND avoids N+1 SET queries.
        for start in range(0, len(rows_all), batch_size):
            chunk = rows_all[start:start + batch_size]
            batch: list[dict] = []
            for nid, text in chunk:
                emb = compute_embedding(str(text))
                if emb is None:
                    skipped += 1
                    continue
                batch.append({"id": nid, "emb": emb})
                total += 1

            if batch:
                g.query(
                    f"UNWIND $batch AS row "
                    f"MATCH (n:{label} {{{id_prop}: row.id}}) "
                    f"SET n.embedding = vecf32(row.emb)",
                    params={"batch": batch},
                )
                updated += len(batch)

            if total % 25 == 0:
                print(f"    ... {total} processed")

    return total, updated, skipped



def _repair_legacy_event_embeddings(g, *, dry_run: bool, limit: int) -> int:
    """Rewrite plain-list Event embeddings into vecf32 (per-node, idempotent).

    Pre-#244 _upsert_event stored embeddings as plain Python lists;
    vec.euclideanDistance then fails the whole MATCH ("expected Null or
    Vectorf32 but was List") — one legacy node poisons Event vector search.
    vecf32() on an already-vecf32 node errors, so each node is rewritten in a
    try/except and failures are counted (expected on fully-migrated DBs).
    """
    rows = g.query(
        "MATCH (n:Event) WHERE n.embedding IS NOT NULL "
        "RETURN n.eventId"
    ).result_set
    if not rows:
        print("repair: no Events with embeddings to check")
        return 0
    targets = rows if not limit else rows[:limit]
    print(f"repair: checking {len(targets)} Event embedding(s)")
    rewritten = already = 0
    for (eid,) in targets:
        if dry_run:
            continue
        try:
            g.query(
                "MATCH (n:Event {eventId:$eid}) SET n.embedding = vecf32(n.embedding)",
                params={"eid": eid},
            )
            rewritten += 1
        except Exception:
            already += 1  # already vecf32 (or unrepairable) — expected on clean DBs
    print(f"repair: rewritten={rewritten} already_vecf32/skipped={already}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill embeddings for vector search")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise")
    ap.add_argument("--all-tenants", action="store_true",
                    help="iterate all team_* graphs from the registry")
    ap.add_argument("--limit", type=int, default=0, help="0 = all (per-graph cap)")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                    help="rows per query/compute batch (default: %(default)s)")
    ap.add_argument(
        "--repair-embeddings", action="store_true",
        help="Rewrite Event embeddings stored as plain lists into vecf32 "
        "(pre-#244 _upsert_event wrote plain lists; a single such node poisons "
        "brute-force vector search for the whole Event label).",
    )
    args = ap.parse_args()

    db, default_graph = _connect_falkordb(args.uri)  # noqa: RUF059

    # Resolve which labels to embed — all entity types with embedding support.
    labels = list(LABEL_CONFIG)

    # ── Dry run ──────────────────────────────────────────────────────
    if args.dry_run:
        grand_total = 0
        if args.all_tenants:
            print("Dry run: all tenants")
            reg = db.select_graph("registry")
            team_rows = reg.query(
                "MATCH (n:Team) RETURN n.id"
            ).result_set
            if not team_rows:
                print("  No teams found in registry")
            for row in team_rows:
                team_id = row[0]
                graph_name = f"team_{team_id}"
                print(f"\nTeam: {team_id} → {graph_name}")
                grand_total += _dry_run(db, graph_name, labels)
        else:
            graph_name = args.graph
            print(f"Dry run: {graph_name}")
            grand_total += _dry_run(db, graph_name, labels)
        print(f"\nDry run: {grand_total} nodes would be embedded (no writes)")
        return 0

    # ── Repair mode (#244) ───────────────────────────────────────────
    if args.repair_embeddings:
        g = db.select_graph(args.graph)
        return _repair_legacy_event_embeddings(g, dry_run=args.dry_run, limit=args.limit)

    # ── Live run ─────────────────────────────────────────────────────
    from tortoise.embeddings import compute_embedding
    if compute_embedding("probe") is None:
        print("❌ Embeddings unavailable — install 'tortoise-graph[embeddings]' "
              "(sentence-transformers + scikit-learn).", file=sys.stderr)
        return 1

    grand_scanned = grand_updated = grand_skipped = 0

    if args.all_tenants:
        print("Backfill: all tenants")
        reg = db.select_graph("registry")
        team_rows = reg.query(
            "MATCH (n:Team) RETURN n.id"
        ).result_set
        if not team_rows:
            print("  No teams found in registry")
        for row in team_rows:
            team_id = row[0]
            graph_name = f"team_{team_id}"
            print(f"\n── Team: {team_id} → {graph_name} ──")
            scanned, updated, skipped = _backfill_graph(
                db, graph_name, labels, args.limit, args.batch_size,
            )
            grand_scanned += scanned
            grand_updated += updated
            grand_skipped += skipped
    else:
        graph_name = args.graph
        print(f"Backfill: {graph_name}")
        scanned, updated, skipped = _backfill_graph(
            db, graph_name, labels, args.limit, args.batch_size,
        )
        grand_scanned += scanned
        grand_updated += updated
        grand_skipped += skipped

    print(f"\nDone: {grand_scanned} scanned, {grand_updated} embedded, "
          f"{grand_skipped} skipped (embedding unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
