#!/usr/bin/env python3
"""#10017 — the pre-delete census for the legacy graph turns.

The copy-then-verify-then-delete migration the owner approved (2026-10-26) must
never delete on a total. A total is not evidence: `37,571 episodic points` can
be true while *every session* is mismatched, because the number carries no
per-tenant structure. So the census this tool produces is **per session**, and
it is the artifact the `verify` phase will compare the record against.

This phase is READ-ONLY, by construction: it opens the graph, queries, and
writes nothing — no journal, no record, no graph mutation. `copy` / `verify` /
`delete` arrive in their own change so this one can be run against production
without any of them existing yet.

    uv run python tools/backfill_graph_turns.py census \
        --graph team_7a3b5403935f865c27d3fb1751 \
        --uri-env FALKORDB_CLOUD_URI \
        --out /tmp/census-team_7a3b54.json

The URI is read from the named environment variable and never printed (it may
carry a credential); only its host/scheme are reported.

Why `node_key` is not read: the legacy turn points on the production graphs have
`node_key = NULL`. Their identity is carried in `id` (``<session_id>_t<index>``),
which is what the record's `(org_id, graph_id, node_key)` key will be rebuilt
from — so the census validates THAT shape rather than assuming it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse as up

#: A turn id in the shape the SDK mints: ``<session_id>_t<index>``.
_TURN_ID_RE = re.compile(r"^(?P<session>.+)_t(?P<index>\d+)$")


def _connect(uri: str):
    """Open the graph store from a redis/falkor URI (TLS when `rediss`)."""
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    u = up.urlparse(uri)
    if u.scheme not in ("redis", "rediss", "falkor", "falkors"):
        raise SystemExit(
            f"census: unsupported URI scheme {u.scheme!r} — expected redis(s)://")
    # #8118: the read bound comes from the product's ONE resolver, not from this
    # call site. ``falkordb.FalkorDB`` defaults ``socket_timeout=None`` and hands
    # it to redis-py explicitly, so an unbounded client parks a production census
    # in ``recv()`` forever against a stalled engine (#2969's defrag loop, #2838's
    # MERGE lock) — alive, 0% CPU, no error — which is the one outcome this tool's
    # "read a live graph and change nothing" contract cannot tolerate: the
    # operator sees a hang with no diagnosis, on the phase that gates a delete.
    return FalkorDB(
        host=u.hostname,
        port=u.port or 6379,
        username=up.unquote(u.username) if u.username else None,
        password=up.unquote(u.password) if u.password else None,
        ssl=(u.scheme in ("rediss", "falkors")),
        **_bounded_server_socket_kwargs(),
    ), f"{u.scheme}://{u.hostname}:{u.port or 6379}"


def census(graph, graph_name: str) -> dict:
    """Per-session turn census for ONE graph. Reads only."""
    sessions = graph.query(
        "MATCH (s:Session) RETURN s.id, s.turn_count, s.created_at"
    ).result_set

    # One pass over the turns: their id IS the key (`<session>_t<index>`), and
    # the `CONTAINS` edge is the session linkage the record copy will carry.
    rows = graph.query(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
        "WHERE p.is_episodic = true "
        "RETURN s.id, p.id, size(coalesce(p.content,'')), "
        "       (p.embedding IS NOT NULL) "
        "ORDER BY s.id, p.id"
    ).result_set

    per_session: dict[str, dict] = {}
    bad_key = 0
    for sid, pid, chars, has_emb in rows:
        parsed = _TURN_ID_RE.match(pid or "")
        if parsed is None or parsed.group("session") != sid:
            bad_key += 1
        bucket = per_session.setdefault(
            str(sid), {"turns": 0, "chars": 0, "embedded": 0,
                       "indexes_seen": 0, "first_key": None, "last_key": None})
        bucket["turns"] += 1
        bucket["chars"] += int(chars or 0)
        bucket["embedded"] += 1 if has_emb else 0
        if parsed is not None:
            bucket["indexes_seen"] = max(bucket["indexes_seen"],
                                         int(parsed.group("index")))
        if bucket["first_key"] is None:
            bucket["first_key"] = pid
        bucket["last_key"] = pid

    # Turns with NO session edge would be copied under no session — that is a
    # silent orphan line in the parity check, so count it explicitly.
    orphan = graph.query(
        "MATCH (p:Point) WHERE p.is_episodic = true "
        "AND NOT (:Session)-[:CONTAINS]->(p) RETURN count(p)"
    ).result_set[0][0]

    declared = {str(sid): (int(tc) if tc is not None else None)
                for sid, tc, _ in sessions}
    # A session whose declared turn_count disagrees with the number of turn
    # points attached to it is a source-side inconsistency: the copy must not
    # "fix" it silently, so it is reported here and the parity check compares
    # against BOTH numbers.
    count_mismatch = {
        sid: {"declared": declared.get(sid), "attached": b["turns"]}
        for sid, b in per_session.items()
        if declared.get(sid) is not None and declared[sid] != b["turns"]
    }

    totals = {
        "sessions_with_turns": len(per_session),
        "sessions_declared": len(declared),
        "turns": sum(b["turns"] for b in per_session.values()),
        "chars": sum(b["chars"] for b in per_session.values()),
        "embedded": sum(b["embedded"] for b in per_session.values()),
        "orphan_turns": int(orphan or 0),
        "turn_ids_not_in_key_shape": bad_key,
        "sessions_in_count_mismatch": len(count_mismatch),
    }
    return {
        "graph": graph_name,
        "totals": totals,
        "count_mismatch": count_mismatch,
        "per_session": per_session,
        "orphan_turn_ids": [],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=["census"])
    ap.add_argument("--graph", required=True,
                    help="the graph NAME (e.g. team_<org_id>) — not an id")
    ap.add_argument("--uri-env", default="FALKORDB_CLOUD_URI",
                    help="environment variable holding the graph URI")
    ap.add_argument("--out", help="write the census JSON here")
    args = ap.parse_args(argv)

    uri = os.environ.get(args.uri_env)
    if not uri:
        raise SystemExit(f"census: ${args.uri_env} is not set")
    db, shown = _connect(uri)
    graph = db.select_graph(args.graph)
    report = census(graph, args.graph)

    t = report["totals"]
    print(f"graph         : {report['graph']}")
    print(f"connected     : {shown}")
    print(f"sessions      : {t['sessions_with_turns']} with turns "
          f"({t['sessions_declared']} declared)")
    print(f"turns         : {t['turns']}  ({t['chars']:,} chars, "
          f"{t['embedded']} embedded)")
    print(f"orphan turns  : {t['orphan_turns']}   "
          f"ids off the '<session>_t<i>' shape: {t['turn_ids_not_in_key_shape']}")
    print(f"count mismatch: {t['sessions_in_count_mismatch']} session(s) whose "
          f"declared turn_count != attached turn points")
    if t["sessions_in_count_mismatch"]:
        for sid, d in list(report["count_mismatch"].items())[:5]:
            print(f"   {sid}: declared={d['declared']} attached={d['attached']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        print(f"census written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
