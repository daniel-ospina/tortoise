"""Backfill embeddings for nodes missing them (issue #160) — plus the T12
``--force-re-embed`` backfill for the #1349 MiniLM→bge-small swap.

Computes 384-dim embeddings via BAAI/bge-small-en-v1.5 (tortoise.embeddings,
EMBEDDING_MODEL) and writes them as vecf32 so the HNSW vector index has data
to query.

Two operating modes:
- Default (NULL-only): only nodes where `embedding IS NULL` are touched, so
  re-running after a partial failure is safe and never recomputes existing
  vectors.
- ``--force-re-embed`` (#1349 T12): re-embeds ALL rows with the SAME
  index-time text composition so stored vectors equal what the live index
  path produces under bge. Flow per graph: NULL-only repair pass (meeting
  exclusion applied, runs BEFORE the purge leg) → purge leg (legacy #160
  meeting embeddings — subject-only MiniLM junk — are nulled, they must not
  persist in the winner's index) → all-rows pass. Idempotent: deterministic
  embedder + full overwrite ⇒ re-running rewrites identical vectors.

Meeting handling matches index-time Python ``eventKind != "meeting"``
(projection/entities.py:483 suppression) exactly: in Cypher ``<> 'meeting'``
is NULL-falsy for a NULL eventKind, so the force/repair predicates use
``(n.eventKind IS NULL OR n.eventKind <> 'meeting')`` — meetings never get
embeddings from ANY backfill path, and a repair-after-purge can never
re-embed just-purged meetings.

URI support: docker://, redis://, rediss:// (FalkorDB Cloud) via
FalkorProjection.from_uri().

Multi-tenant: --all-tenants queries the registry graph for team IDs and
iterates team_{team_id} graphs. Per-tenant backfill, one team at a time.

Usage:
    python3 graph-scripts/backfill_embeddings.py [--dry-run] [--graph GRAPH]
        [--uri URI] [--all-tenants] [--limit N] [--batch-size N]
        [--force-re-embed] [--repair-embeddings]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).

Requires the embeddings extra: pip install 'tortoise-graph[embeddings]'
(or sentence-transformers + scikit-learn). --dry-run only reports counts and
does NOT require the model.
"""
from __future__ import annotations

import argparse
import json
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


# ── Index-time text composition (composition parity, #1349 T12) ────

# The text vectorized at backfill MUST equal the index-time composition so a
# backfill-encoded vector equals an index-time vector for the same content.
# Event non-meeting = subject + eventKind + object (projection/entities.py
# :484-488); Document = title + content (:363); AgentSession =
# session_embedding_text(name, summary, keywords, topics) with the summary
# parsed from the stored content_metadata JSON.

# Index-time meeting suppression (projection/entities.py:483) — meetings
# never receive embeddings. Cypher `eventKind <> 'meeting'` is NULL-falsy for
# a NULL eventKind, so the IS NULL OR arm is required to match the Python
# `eventKind != "meeting"` semantics exactly.
_MEETING_EXCLUSION = "(n.eventKind IS NULL OR n.eventKind <> 'meeting')"


def _summary_from_metadata(content_metadata) -> str:
    """Extract the LLM-extracted summary from the stored content_metadata.

    Index-time session embeddings embed name + summary + keywords + topics
    (session_indexer.session_embedding_text), with summary sourced from the
    extracted metadata persisted as ``content_metadata`` JSON on the Event
    node. The pre-T12 backfill hardcoded summary="" here (silently
    downgrading session vectors) — parsing it restores composition parity.
    Malformed/unparseable metadata degrades to "" (never crashes).
    """
    if not content_metadata:
        return ""
    if isinstance(content_metadata, str):
        try:
            content_metadata = json.loads(content_metadata)
        except (TypeError, ValueError):
            return ""
    if not isinstance(content_metadata, dict):
        return ""
    s = content_metadata.get("summary")
    if not s:
        return ""
    return s if isinstance(s, str) else str(s)


def _row_text(label: str, branch: str, row: tuple) -> str:
    """Index-time-composition text for a fetched row.

    Byte-identical to the index-time ``" ".join(filter(None, [...]))`` joins
    (entities.py) — NULL/None parts drop exactly like ``""`` defaults there.
    """
    if label == "Event":
        if branch == "AgentSession":
            # row = (eventId, name, keywords, topics, content_metadata)
            from tortoise.session_indexer import session_embedding_text
            return session_embedding_text(
                row[1], _summary_from_metadata(row[4]),
                row[2] or [], row[3] or [],
            )
        # row = (eventId, subject, eventKind, object) — non-meeting only
        return " ".join(filter(None, [row[1], row[2], row[3]]))
    if label == "Document":
        # row = (id, title, content)
        return " ".join(filter(None, [row[1], row[2]]))
    # row = (id_prop, text_prop)
    return row[1]


def _label_branches(label: str, *, force: bool) -> list[tuple[str, str, str]]:
    """Per-label (branch, WHERE, RETURN) query specs for both modes.

    Event splits into AgentSession (session surface: name/keywords/topics/
    content_metadata) and other kinds (subject + eventKind + object). The
    meeting exclusion applies to the Event "other" branch in BOTH modes —
    the NULL-only repair pass must never re-embed a just-purged meeting.
    ``force=False`` adds ``n.embedding IS NULL`` (NULL-only repair);
    ``force=True`` drops it (all rows re-embedded). Index-time session
    embeddings never see a NULL name (the indexers default it from
    summary/filename), but session_embedding_text(None, ...) composes
    summary+keywords+topics safely — a name-less AgentSession is
    index-time-embeddable, so the session branch does not gate on name.
    """
    cfg = LABEL_CONFIG[label]
    null_pred = "" if force else "n.embedding IS NULL AND "
    if label == "Event":
        return [
            ("AgentSession",
             f"{null_pred}n.eventKind = 'AgentSession'",
             "n.eventId AS _id, n.name AS _name, n.keywords AS _keywords, "
             "n.topics AS _topics, n.content_metadata AS _cm"),
            ("other",
             f"{null_pred}{_MEETING_EXCLUSION} AND "
             "(n.eventKind IS NULL OR n.eventKind <> 'AgentSession') "
             f"AND n.{cfg['text_prop']} IS NOT NULL",
             "n.eventId AS _id, n.subject AS _subject, n.eventKind AS _kind, "
             "n.object AS _object"),
        ]
    if label == "Document":
        # text surface = title + content (index-time composition needs both)
        return [("all", f"{null_pred}n.title IS NOT NULL",
                 "n.id AS _id, n.title AS _title, n.content AS _content")]
    # Aliased columns: Source's id_prop == text_prop == url would otherwise
    # RETURN n.url, n.url ("Multiple result columns with the same name").
    return [("all", f"{null_pred}n.{cfg['text_prop']} IS NOT NULL",
             f"n.{cfg['id_prop']} AS _id, n.{cfg['text_prop']} AS _text")]


def _purge_meeting_embeddings(g, *, dry_run: bool = False) -> int:
    """Remove legacy #160 meeting embeddings (subject-only MiniLM junk).

    Index-time suppresses meeting embeddings entirely (entities.py:483) —
    the #160 backfill embedded them anyway; under bge they must not persist
    in the vector index. Returns the count removed (or to remove in dry-run).
    """
    rows = g.query(
        "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
        "RETURN count(n)"
    ).result_set
    n = rows[0][0] if rows else 0
    if n and not dry_run:
        g.query(
            "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
            "SET n.embedding = null",
        )
    return n


def _dry_run(db, graph_name: str, labels: list[str], *,
             force: bool = False) -> tuple[int, int]:
    """Count rows a run WOULD write. Returns (total, meeting_purge_count).

    NULL-only: rows missing embeddings. Force: ALL rows (meeting-excluded) —
    already-embedded rows ARE re-embedded by force, so they count; meetings
    and text-less rows are the unaffected set and are NOT counted. Dry-run
    never computes embeddings and never writes.
    """
    g = db.select_graph(graph_name)
    total = 0
    for label in labels:
        n = 0
        for _, where, _ in _label_branches(label, force=force):
            rows = g.query(
                f"MATCH (n:{label}) WHERE {where} RETURN count(n)"
            ).result_set
            n += rows[0][0] if rows else 0
        if n:
            verb = "rows would be re-embedded" if force else "missing embeddings"
            print(f"  {label}: {n} {verb}")
            total += n
    purged = 0
    if force:
        rows = g.query(
            "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
            "RETURN count(n)"
        ).result_set
        purged = rows[0][0] if rows else 0
        if purged:
            print(f"  Event: {purged} meeting embeddings would be purged")
    return total, purged


def _backfill_graph(db, graph_name: str, labels: list[str],
                    limit: int, batch_size: int, *,
                    force: bool = False) -> dict:
    """Backfill one graph.

    force=False: NULL-only repair (only ``embedding IS NULL`` rows).
    force=True: ALL rows re-embedded (T12) with index-time composition; the
    meeting exclusion applies in both modes.

    Returns stats: scanned/updated/skipped counts, per_label updated counts,
    agent_sessions sub-count (Event label only).
    """
    g = db.select_graph(graph_name)

    from tortoise.embeddings import compute_embedding

    total = updated = skipped = 0
    # Seed ALL 6 label keys at 0 so the completeness marker is unambiguous:
    # a zero-count label means "no rows re-embedded", not "never processed".
    per_label: dict[str, int] = {label: 0 for label in LABEL_CONFIG}
    agent_sessions = 0
    for label in labels:
        cfg = LABEL_CONFIG[label]
        label_updated = 0

        # Collect (branch, row) in bounded queries — avoids re-pagination
        # drift from SET mutating the WHERE predicate mid-scan.
        rows_all: list[tuple] = []
        for branch, where, returns in _label_branches(label, force=force):
            offset = 0
            while True:
                rows = g.query(
                    f"MATCH (n:{label}) WHERE {where} "
                    f"RETURN {returns} SKIP $skip LIMIT $limit",
                    params={"skip": offset, "limit": batch_size},
                ).result_set
                if not rows:
                    break
                rows_all.extend((branch, r) for r in rows)
                offset += len(rows)
                if limit and offset >= limit:
                    break
        if limit:
            rows_all = rows_all[:limit]
        if not rows_all:
            continue
        verb = "rows re-embedded" if force else "missing embeddings"
        print(f"  {label}: {len(rows_all)} {verb}")

        # Compute + write in batches. UNWIND avoids N+1 SET queries.
        for start in range(0, len(rows_all), batch_size):
            chunk = rows_all[start:start + batch_size]
            batch: list[dict] = []
            for branch, row in chunk:
                text = _row_text(label, branch, row)
                if not text.strip():
                    # Index-time guards composition with .strip() before
                    # computing (entities.py) — empty text never embeds.
                    skipped += 1
                    continue
                emb = compute_embedding(str(text))
                if emb is None:
                    skipped += 1
                    continue
                batch.append({"id": row[0], "emb": emb})
                if label == "Event" and branch == "AgentSession":
                    agent_sessions += 1
                total += 1

            if batch:
                g.query(
                    f"UNWIND $batch AS row "
                    f"MATCH (n:{label} {{{cfg['id_prop']}: row.id}}) "
                    "SET n.embedding = vecf32(row.emb)",
                    params={"batch": batch},
                )
                updated += len(batch)
                label_updated += len(batch)

            if total and total % 25 == 0:
                print(f"    ... {total} processed")

        per_label[label] = label_updated

    return {"scanned": total, "updated": updated, "skipped": skipped,
            "per_label": per_label, "agent_sessions": agent_sessions}


def _merge_stats(agg: dict, stats: dict) -> dict:
    """Accumulate per-graph stats into an aggregate (all-tenants)."""
    agg["scanned"] += stats["scanned"]
    agg["updated"] += stats["updated"]
    agg["skipped"] += stats["skipped"]
    agg["agent_sessions"] += stats["agent_sessions"]
    agg["meeting_purged"] += stats["meeting_purged"]
    agg["repair_skipped"] += stats["repair_skipped"]
    agg["repair_updated"] += stats["repair_updated"]
    for label, n in stats["per_label"].items():
        agg["per_label"][label] = agg["per_label"].get(label, 0) + n
    return agg


def _force_reembed_graph(db, graph_name: str, labels: list[str],
                         limit: int, batch_size: int) -> dict:
    """Force-mode flow for one graph: NULL-only repair → meeting purge →
    all-rows pass (#1349 T12).

    Ordering is load-bearing: the NULL-only repair pass applies the SAME
    meeting exclusion and runs BEFORE the purge leg, so a repair-after-purge
    can never re-embed just-purged meetings. Returns aggregate stats for the
    completeness marker (per-label re-embedded counts, purge count, repair
    skips).
    """
    repair = _backfill_graph(db, graph_name, labels, limit, batch_size,
                             force=False)
    # The repair leg re-embeds the NULL subset once, then the all-rows pass
    # re-embeds everything INCLUDING that subset. The overlap is intentional:
    # the plan (Task 12) mandates the NULL-only repair pass runs BEFORE the
    # purge leg with the same meeting exclusion, and its skips are recorded
    # separately (repair_skipped in the marker). Marker per_label counts come
    # from the force pass only, so "everything moved" per-label accounting is
    # unaffected by the redundant compute on the NULL subset.
    g = db.select_graph(graph_name)
    purged = _purge_meeting_embeddings(g)
    force = _backfill_graph(db, graph_name, labels, limit, batch_size,
                            force=True)
    return {
        "scanned": repair["scanned"] + force["scanned"],
        "updated": repair["updated"] + force["updated"],
        "skipped": repair["skipped"] + force["skipped"],
        "per_label": force["per_label"],
        "agent_sessions": force["agent_sessions"],
        "meeting_purged": purged,
        "repair_skipped": repair["skipped"],
        "repair_updated": repair["updated"],
    }


def _print_completeness(stats: dict) -> None:
    """Machine-verifiable "everything moved" marker (one JSON line).

    Per-label re-embedded counts (all 6 LABEL_CONFIG labels), AgentSession
    sub-breakdown, meeting purge count, and repair skips — parseable by ops
    tooling in the PR2 evidence.
    """
    marker = {
        "labels": stats.get("per_label", {}),
        "agent_sessions": stats.get("agent_sessions", 0),
        "meeting_purged": stats.get("meeting_purged", 0),
        "repair_skipped": stats.get("repair_skipped", 0),
        "repair_updated": stats.get("repair_updated", 0),
    }
    print("COMPLETENESS " + json.dumps(marker, sort_keys=True))


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


def main(argv: list[str] | None = None) -> int:
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
    ap.add_argument(
        "--force-re-embed", action="store_true",
        help="Re-embed ALL rows (not just embedding IS NULL) with the "
        "index-time text composition — the #1349 T12 backfill for the "
        "MiniLM→bge swap. Runs the NULL-only repair pass first, then purges "
        "legacy meeting embeddings, then re-embeds every row. Idempotent.",
    )
    args = ap.parse_args(argv)

    db, default_graph = _connect_falkordb(args.uri)

    # Resolve which labels to embed — all entity types with embedding support.
    labels = list(LABEL_CONFIG)

    # ── Dry run ──────────────────────────────────────────────────────
    if args.dry_run:
        grand_total = 0
        grand_purged = 0
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
                total, purged = _dry_run(db, graph_name, labels,
                                         force=args.force_re_embed)
                grand_total += total
                grand_purged += purged
        else:
            graph_name = args.graph
            header = ("Dry run (force-re-embed): " if args.force_re_embed
                      else "Dry run: ")
            print(header + graph_name)
            total, purged = _dry_run(db, graph_name, labels,
                                     force=args.force_re_embed)
            grand_total += total
            grand_purged += purged
        if args.force_re_embed:
            print(f"\nDry run: {grand_total} nodes would be re-embedded, "
                  f"{grand_purged} meeting embeddings purged (no writes)")
        else:
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

    if args.force_re_embed:
        stats = {"scanned": 0, "updated": 0, "skipped": 0, "per_label": {},
                 "agent_sessions": 0, "meeting_purged": 0,
                 "repair_skipped": 0, "repair_updated": 0}
        if args.all_tenants:
            print("Backfill (force-re-embed): all tenants")
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
                _merge_stats(
                    stats,
                    _force_reembed_graph(db, graph_name, labels,
                                         args.limit, args.batch_size),
                )
        else:
            graph_name = args.graph
            print(f"Backfill (force-re-embed): {graph_name}")
            _merge_stats(
                stats,
                _force_reembed_graph(db, graph_name, labels,
                                     args.limit, args.batch_size),
            )
        _print_completeness(stats)
        print(f"\nDone: {stats['scanned']} scanned, {stats['updated']} re-embedded, "
              f"{stats['skipped']} skipped (embedding unavailable), "
              f"{stats['meeting_purged']} meeting embeddings purged")
        return 0

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
            s = _backfill_graph(db, graph_name, labels,
                                args.limit, args.batch_size)
            grand_scanned += s["scanned"]
            grand_updated += s["updated"]
            grand_skipped += s["skipped"]
    else:
        graph_name = args.graph
        print(f"Backfill: {graph_name}")
        s = _backfill_graph(db, graph_name, labels,
                            args.limit, args.batch_size)
        grand_scanned += s["scanned"]
        grand_updated += s["updated"]
        grand_skipped += s["skipped"]

    print(f"\nDone: {grand_scanned} scanned, {grand_updated} embedded, "
          f"{grand_skipped} skipped (embedding unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
