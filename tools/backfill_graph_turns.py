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

The phase is refused unless the graph EXISTS (`GRAPH.LIST`): a read against a
non-existent graph materialises its keyspace, which is a change — and this
phase's whole promise is that it makes none. It is also refused when the graph
holds no `:Session` at all, because an all-zero census is indistinguishable
from a wrong graph or a label/property shape that no longer matches, and a
zero that cannot be told apart from "nothing to migrate" is not evidence.

Why `node_key` is not read: the legacy turn points on the production graphs have
`node_key = NULL`. Their identity is carried in `id` (``<session_id>_t<index>``),
which is what the record's `(org_id, graph_id, node_key)` key will be rebuilt
from — so the census validates THAT shape rather than assuming it. A point
attached to a session whose id does not key as one of ITS turns is NOT counted
as a turn: it is counted apart (`non_turn_members`), because folding it in is
how a session that lost turns reads as complete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse as up

#: A turn id in the shape the SDK mints: ``<session_id>_t<index>``.
#:
#: ``[0-9]``, not ``\d``: the product's one definition of a turn index is ASCII
#: digits (``record_read._turn_index``, ``sdk._capture_turn_ids`` — whose
#: ``isdecimal()`` guard exists for the same reason), and ``\d`` also matches
#: Unicode digits. A key the record REFUSES must not be counted here as one that
#: rebuilds exactly.
#:
#: ``.+`` is deliberately GREEDY so the LAST ``_t<digits>`` wins: a session id
#: that itself ends in ``_t<i>`` (``sess_t3_t7``) must parse as session
#: ``sess_t3`` index 7, not as session ``sess`` — otherwise one session's turns
#: prefix-match another's, which is the trap `sdk._capture_turn_ids` documents.
_TURN_ID_RE = re.compile(r"^(?P<session>.+)_t(?P<index>[0-9]+)$")


def _assert_graph_exists(db, graph_name: str,
                         create_if_missing: bool = False) -> None:
    """Refuse to measure a graph that does not exist yet.

    ⛔ A read against a graph that does not exist still has a side effect:
    FalkorDB materialises the keyspace on the first query, so a bare ``MATCH``
    creates an empty graph of that name — measured on the engine while writing
    this. This phase's contract is "changes nothing", and on a production
    instance a typo'd graph name would otherwise be created by the very act of
    measuring it. ``GRAPH.LIST`` does not itself create anything, so this check
    is side-effect free — unlike the query it guards.
    """
    names = list(db.list_graphs() or [])
    if graph_name in names or create_if_missing:
        return
    raise SystemExit(
        f"census: graph {graph_name!r} does not exist on this instance "
        f"({len(names)} graph(s) there) — and reading a non-existent graph "
        "CREATES it (FalkorDB materialises the keyspace on the first query), "
        "so this tool will not be the thing that creates it. Check the name, "
        "or pass --create-if-missing to accept that.")



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
    non_turn_ids: list[str] = []
    for sid, pid, chars, has_emb in rows:
        sid = str(sid)
        parsed = _TURN_ID_RE.match(pid or "")
        own = parsed is not None and parsed.group("session") == sid
        bucket = per_session.setdefault(sid, {
            "turns": 0, "chars": 0, "embedded": 0, "indexes_seen": 0,
            "first_key": None, "last_key": None, "non_turn_members": 0})
        if not own:
            # An off-shape id cannot be keyed into the record at all, and a
            # POINT attached here under ANOTHER session's turn id is not this
            # session's turn — neither would be rebuilt by the copy. Counting
            # either as a turn is exactly how a session that lost turns reads
            # as complete, which is the failure this census exists to catch.
            bucket["non_turn_members"] += 1
            non_turn_ids.append(pid)
            continue
        bucket["turns"] += 1
        bucket["chars"] += int(chars or 0)
        bucket["embedded"] += 1 if has_emb else 0
        bucket["indexes_seen"] = max(bucket["indexes_seen"],
                                     int(parsed.group("index")))
        if bucket["first_key"] is None:
            bucket["first_key"] = pid
        bucket["last_key"] = pid

    # Turns with NO session edge would be copied under no session — that is a
    # silent orphan line in the parity check, so the ids are MEASURED, not
    # merely counted. A hard-coded empty list beside a non-zero count would read
    # as "no orphan ids", which is the opposite of the finding.
    orphan_ids = [r[0] for r in graph.query(
        "MATCH (p:Point) WHERE p.is_episodic = true "
        "AND NOT (:Session)-[:CONTAINS]->(p) RETURN p.id ORDER BY p.id"
    ).result_set]

    declared = {str(sid): (int(tc) if tc is not None else None)
                for sid, tc, _ in sessions}

    # Counted BEFORE declared-only sessions are folded in, so a declared session
    # with no turns cannot inflate "sessions with turns" — and by the TURN count,
    # not by bucket existence, because a session holding only non-turn members
    # has a bucket and zero turns.
    sessions_with_turns = sum(1 for b in per_session.values()
                             if b["turns"] > 0)
    sessions_without_turns = sorted(
        s for s in (set(declared) | set(per_session))
        if per_session.get(s, {}).get("turns", 0) == 0)

    # Every declared session carries BOTH numbers into the artifact, including
    # one with no turns at all — that is the "872 of 873" case, and `verify`
    # cannot compare a session the census never mentioned.
    for sid in sessions_without_turns:
        # setdefault, never assignment: a session that has a bucket because it
        # holds NON-turn members must keep that count — overwriting it here is
        # how a real finding would be erased by the very step that reports it.
        per_session.setdefault(sid, {
            "turns": 0, "chars": 0, "embedded": 0, "indexes_seen": 0,
            "first_key": None, "last_key": None, "non_turn_members": 0})
    for sid, bucket in per_session.items():
        bucket["declared"] = declared.get(sid)

    # A session whose declared turn_count disagrees with the number of turn
    # points attached to it is a source-side inconsistency: the copy must not
    # "fix" it silently, so it is reported here and the parity check compares
    # against BOTH numbers. Driven by the UNION of declared and attached — a
    # session that declares turns and holds none is the worst case of this
    # class, not an absent one.
    count_mismatch = {
        sid: {"declared": declared[sid],
              "attached": per_session.get(sid, {}).get("turns", 0)}
        for sid in sorted(set(declared) | set(per_session))
        if declared.get(sid) is not None
        and declared[sid] != per_session.get(sid, {}).get("turns", 0)
    }

    totals = {
        "sessions_with_turns": sessions_with_turns,
        "sessions_declared": len(declared),
        "sessions_without_turns": len(sessions_without_turns),
        "turns": sum(b["turns"] for b in per_session.values()),
        "chars": sum(b["chars"] for b in per_session.values()),
        "embedded": sum(b["embedded"] for b in per_session.values()),
        "orphan_turns": len(orphan_ids),
        "turn_ids_not_in_key_shape": len(non_turn_ids),
        "sessions_in_count_mismatch": len(count_mismatch),
    }
    return {
        "graph": graph_name,
        "totals": totals,
        "count_mismatch": count_mismatch,
        "per_session": per_session,
        "sessions_without_turns": sessions_without_turns,
        "orphan_turn_ids": orphan_ids,
        "turn_ids_not_in_key_shape": non_turn_ids,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=["census"])
    ap.add_argument("--graph", required=True,
                    help="the graph NAME (e.g. team_<org_id>) — not an id")
    ap.add_argument("--uri-env", default="FALKORDB_CLOUD_URI",
                    help="environment variable holding the graph URI")
    ap.add_argument("--out", help="write the census JSON here")
    ap.add_argument("--create-if-missing", action="store_true",
                    help="accept that a census of a non-existent graph CREATES "
                         "its keyspace (refused by default)")
    args = ap.parse_args(argv)

    uri = os.environ.get(args.uri_env)
    if not uri:
        raise SystemExit(f"census: ${args.uri_env} is not set")
    db, shown = _connect(uri)
    _assert_graph_exists(db, args.graph, args.create_if_missing)
    graph = db.select_graph(args.graph)
    report = census(graph, args.graph)

    t = report["totals"]
    if t["sessions_declared"] == 0:
        # A positive control: every total below is a zero, and a wrong graph (or
        # a `:Session` label / `is_episodic` shape that no longer matches) looks
        # exactly the same. An artifact that cannot be told apart from "nothing
        # to migrate" must not be handed to `verify` as evidence.
        raise SystemExit(
            f"census: graph {args.graph!r} holds no :Session nodes, so every "
            "total is a zero — which is what a wrong graph, or a label/property "
            "shape that no longer matches, looks like too. Refusing to write a "
            "census that cannot be told apart from 'nothing to migrate'.")
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
    if t["sessions_without_turns"]:
        print(f"  incl. {t['sessions_without_turns']} session(s) that declare "
              "turns and hold none")
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
