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

Every row-bearing read is keyset-PAGED and cross-checked against the server's
own aggregate for the same predicate, and a disagreement is a REFUSAL: FalkorDB
truncates a non-aggregate result at `RESULTSET_SIZE` (default 10,000) without an
error, and the population this exists for is larger than that, so a clipped read
would otherwise hand `verify` a prefix of the graph as the whole of it.

Why `node_key` is not read: the legacy turn points on the production graphs have
`node_key = NULL`. Their identity is carried in `id` (``<session_id>_t<index>``),
which is what the record's `(org_id, graph_id, node_key)` key will be rebuilt
from — so the census validates THAT shape rather than assuming it. A point
attached to a session whose id does not key as one of ITS turns is NOT counted
as a turn: it is counted apart (`non_turn_members`), because folding it in is
how a session that lost turns reads as complete.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import would fail first, and an UNATTRIBUTED failure is the harm
# #5128 was filed for. Same shape as the sibling census (tools/edge_census.py);
# the guard must be the first statement after the docstring and `import sys`,
# which `tests/test_entry_point_python_guard.py` pins for every tools/ entry
# point.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/backfill_graph_turns.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/backfill_graph_turns.py`"
    )

import argparse
import json
import os
import re
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

#: What ``--uri-env`` accepts: an environment VARIABLE NAME, nothing else.
_URI_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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



#: Rows per keyset page. Deliberately BELOW FalkorDB's server-global
#: ``RESULTSET_SIZE`` (default 10,000), which truncates a non-aggregate read
#: SILENTLY — no error, no marker (#4515/#4233, ``tortoise/hosted_backup.py``).
#: The population this census exists for is ~37,571 episodic points, so a single
#: unpaged ``RETURN`` would measure the first 10,000 of them and report that as
#: the whole graph: a partial baseline for `verify`, on the phase that gates an
#: irreversible delete.
_PAGE_SIZE = 5000

_SESSIONS_PAGE_QUERY = (
    "MATCH (s:Session) WHERE id(s) >= $cursor "
    "RETURN id(s), s.id, s.turn_count, s.turn_max_index "
    "ORDER BY id(s) LIMIT $batch"
)
_SESSIONS_COUNT_QUERY = "MATCH (s:Session) RETURN count(s)"
_TURNS_PAGE_QUERY = (
    "MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE p.is_episodic = true "
    "AND id(p) >= $cursor "
    "WITH p, collect(s.id) AS sids "
    "RETURN id(p), p.id, size(coalesce(p.content,'')), "
    "       (p.embedding IS NOT NULL), p.pointKind, sids "
    "ORDER BY id(p) LIMIT $batch"
)
#: One row, two aggregates: ``count(p)`` counts the (session, point) PAIRS the
#: census must account for, ``count(DISTINCT p)`` the POINTS the paged read
#: returns one row for. Both are needed — the paged leg groups sessions per
#: point, so a point is one row and N session memberships.
_TURNS_COUNT_QUERY = (
    "MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE p.is_episodic = true "
    "RETURN count(p), count(DISTINCT p)"
)
_ORPHANS_PAGE_QUERY = (
    "MATCH (p:Point) WHERE p.is_episodic = true "
    "AND NOT (:Session)-[:CONTAINS]->(p) AND id(p) >= $cursor "
    "RETURN id(p), p.id ORDER BY id(p) LIMIT $batch"
)
_ORPHANS_COUNT_QUERY = (
    "MATCH (p:Point) WHERE p.is_episodic = true "
    "AND NOT (:Session)-[:CONTAINS]->(p) RETURN count(p)"
)


class CensusError(RuntimeError):
    """A census that cannot be trusted — refused, never written.

    Raised instead of returning a report whose numbers are a prefix of the
    graph's: the artifact is what `verify` compares the record against before a
    delete, and a silently-clipped read is indistinguishable from a small graph.
    """


def _paged_rows(graph, query: str, batch: int):
    """Every row of ``query``, keyset-paged on its ``id()`` column.

    The cursor advances to ``last_id + 1``, which requires the query to return
    ONE ROW PER NODE: a ``RETURN`` that emits one row per (session, point) PAIR
    repeats a point's internal id, and ``last + 1`` would then step over that
    point's remaining rows at a page boundary — a silent drop of exactly the
    turns this census exists to count. That is why the turn leg groups its
    sessions per point (``WITH p, collect(s.id)``) rather than paging raw pairs;
    a live FalkorDB measurement of both shapes is what settled it.

    The lower bound is ``id(n) >= 0``, never negative: a negative comparison
    against a NODE id answers 0 rows on the v6 Rust FalkorDB core (#9226,
    ``tortoise/hosted_backup.py::_paged_rows``).
    """
    cursor = 0
    while True:
        rows = graph.query(query, params={"cursor": cursor,
                                          "batch": batch}).result_set
        if not rows:
            return
        yield from rows
        if len(rows) < batch:
            return
        cursor = int(rows[-1][0]) + 1


def _count(graph, query: str) -> tuple[int, ...]:
    """A server-side aggregate — ONE row, so RESULTSET_SIZE cannot clip it."""
    rows = graph.query(query).result_set
    return tuple(int(v) for v in rows[0]) if rows else ()


def _cross_check(leg: str, measured: int, aggregate: int) -> None:
    """Refuse a census whose paged read disagrees with the server's own count.

    A clipped read is silent by construction (the engine truncates, it does not
    error), so the artifact would state a PREFIX of the graph as the graph — and
    `verify` would then compare the record against a partial baseline while the
    delete sweep is broader than any count in it. Fail closed.
    """
    if measured != aggregate:
        raise CensusError(
            f"census: the {leg} read returned {measured} row(s) but the server "
            f"counts {aggregate} — the read was TRUNCATED (RESULTSET_SIZE) or "
            "the graph changed under the census. A partial read would become a "
            "partial parity baseline, so this census is refused, not written.")


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


def _int_or_none(value):
    """``int(value)`` or ``None`` — never a bare raise.

    Two traps live here, both documented by the readers this file follows:
    a graph property may be a string, and a digit run may exceed CPython's
    integer-conversion limit (``sdk._capture_turn_ids`` records the same one).
    A single malformed row must not abort a census of a production graph.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _empty_bucket() -> dict:
    return {"turns": 0, "chars": 0, "embedded": 0, "indexes_seen": 0,
            "first_index": None, "first_key": None,
            "last_index": None, "last_key": None,
            "non_turn_members": 0, "turns_not_point_kind_event": 0}


def census(graph, graph_name: str, page_size: int | None = None) -> dict:
    """Per-session turn census for ONE graph. Reads only.

    A "turn" here is a ``:Point`` wired ``[:CONTAINS]`` to a ``:Session`` whose
    id keys as THAT session's turn (``<session_id>_t<ascii digits>``) — the same
    identity the record rebuilds from. Everything else attached to the session
    is reported apart, never folded into ``turns``.

    Every row-bearing read is keyset-PAGED and cross-checked against the
    server's own aggregate for the same predicate; a disagreement raises
    ``CensusError`` rather than returning a truncated census.
    """
    if page_size is None:
        # Read at CALL time, never bound as a def-time default: the module
        # constant is what a test retunes to make the census take >1 page.
        page_size = _PAGE_SIZE
    # `turn_max_index` is the ID-SPACE high-water mark the writer records beside
    # the window length (`sdk.py`, post-#10000: "`turn_count` is a WINDOW LENGTH,
    # not a max id index"), so the census can report the two declared numbers
    # separately from the two measured ones. `created_at` is deliberately not
    # read: nothing here consumes it.
    sessions = list(_paged_rows(graph, _SESSIONS_PAGE_QUERY, page_size))
    _cross_check(":Session", len(sessions),
                 _count(graph, _SESSIONS_COUNT_QUERY)[0])

    # One pass over the turns: their id IS the key (`<session>_t<index>`), and
    # the `CONTAINS` edge is the session linkage the record copy will carry.
    # `pointKind` rides along because the product's turn readers disagree about
    # it (`sdk._capture_turn_ids` and `session_schema.audit_session_retrofit`
    # require `pointKind = 'event'`; the record's own turn read does not): this
    # census keeps the id-shape definition — which is what the record keys on —
    # and MEASURES the divergence instead of silently picking one.
    rows = list(_paged_rows(graph, _TURNS_PAGE_QUERY, page_size))
    pairs_expected, points_expected = _count(graph, _TURNS_COUNT_QUERY)
    _cross_check("turn", len(rows), points_expected)
    _cross_check("turn-to-session edge",
                 sum(len(r[5] or ()) for r in rows), pairs_expected)

    per_session: dict[str, dict] = {}
    off_shape: list[str] = []
    other_session: list[str] = []
    for _internal_id, pid, chars, has_emb, kind, sids in rows:
        pid = "" if pid is None else str(pid)
        sids = [str(s) for s in (sids or ())]
        parsed = _TURN_ID_RE.match(pid)
        index = _int_or_none(parsed.group("index")) if parsed is not None else None
        if parsed is None or index is None:
            # An off-shape id (or an index CPython cannot convert) cannot be
            # keyed into the record at all — a copy-blocking finding, distinct
            # from the one below. Named ONCE per point, counted per membership.
            for sid in sids or [""]:
                per_session.setdefault(sid, _empty_bucket())[
                    "non_turn_members"] += 1
            off_shape.append(pid)
            continue
        owner = parsed.group("session")
        if owner not in sids:
            # A well-formed turn id belonging to a DIFFERENT session, wired
            # here. It IS on the shape; it simply is not these sessions' turn,
            # and the copy would rebuild it under its own session.
            for sid in sids:
                per_session.setdefault(sid, _empty_bucket())[
                    "non_turn_members"] += 1
            other_session.append(pid)
            continue
        for sid in sids:
            bucket = per_session.setdefault(sid, _empty_bucket())
            if sid != owner:
                # ATTACHED, on-shape, but not this session's: a multi-wired
                # point is one turn, not one per session.
                bucket["non_turn_members"] += 1
                continue
            bucket["turns"] += 1
            bucket["chars"] += _int_or_none(chars) or 0
            bucket["embedded"] += 1 if has_emb else 0
            if kind != "event":
                bucket["turns_not_point_kind_event"] += 1
            bucket["indexes_seen"] = max(bucket["indexes_seen"], index)
            # `first_key`/`last_key` are the ids at the LOWEST/HIGHEST INDEX, not
            # the lexicographically-first/last id (`s1_t9` sorts after `s1_t10`,
            # so the ordering the query returns is not the id order).
            if bucket["first_index"] is None or index < bucket["first_index"]:
                bucket["first_index"] = index
                bucket["first_key"] = pid
            if bucket["last_index"] is None or index > bucket["last_index"]:
                bucket["last_index"] = index
                bucket["last_key"] = pid

    # Turns with NO session edge would be copied under no session — that is a
    # silent orphan line in the parity check, so the ids are MEASURED, not
    # merely counted. A hard-coded empty list beside a non-zero count would read
    # as "no orphan ids", which is the opposite of the finding.
    orphan_ids = [str(r[1]) for r in
                  _paged_rows(graph, _ORPHANS_PAGE_QUERY, page_size)]
    _cross_check("orphan", len(orphan_ids),
                 _count(graph, _ORPHANS_COUNT_QUERY)[0])

    declared = {str(sid): _int_or_none(tc)
                for _nid, sid, tc, _tmi in sessions}
    declared_max = {str(sid): _int_or_none(tmi)
                    for _nid, sid, _tc, tmi in sessions}

    # Counted BEFORE declared-only sessions are folded in, so a declared session
    # with no turns cannot inflate "sessions with turns" — and by the TURN count,
    # not by bucket existence, because a session holding only non-turn members
    # has a bucket and zero turns.
    everywhere = set(declared) | set(per_session)
    sessions_with_turns = sum(1 for b in per_session.values() if b["turns"] > 0)
    sessions_without_turns = sorted(
        s for s in everywhere if per_session.get(s, {}).get("turns", 0) == 0)

    # Every declared session carries BOTH numbers into the artifact, including
    # one with no turns at all — that is the "872 of 873" case, and `verify`
    # cannot compare a session the census never mentioned.
    for sid in sessions_without_turns:
        # setdefault, never assignment: a session that has a bucket because it
        # holds NON-turn members must keep that count — overwriting it here is
        # how a real finding would be erased by the very step that reports it.
        per_session.setdefault(sid, _empty_bucket())
    for sid, bucket in per_session.items():
        bucket["declared"] = declared.get(sid)
        bucket["declared_max_index"] = declared_max.get(sid)
        # `turn_count` is a WINDOW LENGTH; the indices are the ID SPACE. A
        # session whose ids run 0,1,5,6 has 4 turns and a hole, and only the
        # second number can show it.
        #
        # The test is over the session's OWN window (`last - first + 1`), never
        # anchored at index 0. `max + 1 == count` is a contiguity test only when
        # the window begins at 0, and the writer deliberately starts it anywhere:
        # `_capture_turn_id(session_id, i, turn_offset)` keys `s_<i+offset>` and
        # `turn_max_index = turn_offset + turn_count - 1` (`tortoise/sdk.py`), so
        # a 2-turn window at offset 2 holds `_t2`,`_t3` with `turn_count = 2`
        # (the shape `tests/test_10000_turns_record_only.py` writes) — for which
        # `indexes_seen + 1 != turns` is arithmetically ALWAYS true. That flagged
        # a phantom hole on a contiguous id space, in the derived judgement this
        # pre-delete artifact exists to make trustworthy.
        bucket["id_space_hole"] = (
            bucket["turns"] > 0
            and bucket["last_index"] - bucket["first_index"] + 1
            != bucket["turns"])

    # NO CAUSE IS NAMED HERE. A disagreement between the declared window length
    # and the attached turns is a MEASUREMENT; on a journal+record deployment
    # (`sdk._record_holds_turns`) the writer sets `turn_count` and deliberately
    # puts no turns in the graph, which is a correct state that looks identical
    # to a loss. Attributing it to "legacy captures without turn_count
    # following" would be a cause this census never measured — `verify` decides,
    # and it has both numbers for every session.
    count_mismatch = {
        sid: {"declared": declared[sid],
              "attached": per_session.get(sid, {}).get("turns", 0),
              "declared_max_index": declared_max.get(sid),
              "max_index_seen": per_session.get(sid, {}).get("indexes_seen")}
        for sid in sorted(everywhere)
        if declared.get(sid) is not None
        and declared[sid] != per_session.get(sid, {}).get("turns", 0)
    }
    declared_and_empty = sorted(
        s for s in sessions_without_turns
        if (declared.get(s) or 0) > 0)
    id_space_holes = sorted(
        s for s, b in per_session.items() if b["id_space_hole"])

    totals = {
        "sessions_with_turns": sessions_with_turns,
        "sessions_declared": len(declared),
        "sessions_without_turns": len(sessions_without_turns),
        "sessions_declared_and_empty": len(declared_and_empty),
        "sessions_in_count_mismatch": len(count_mismatch),
        "sessions_with_id_space_holes": len(id_space_holes),
        "turns": sum(b["turns"] for b in per_session.values()),
        "chars": sum(b["chars"] for b in per_session.values()),
        "embedded": sum(b["embedded"] for b in per_session.values()),
        "turns_not_point_kind_event": sum(
            b["turns_not_point_kind_event"] for b in per_session.values()),
        "non_turn_members": sum(b["non_turn_members"]
                                for b in per_session.values()),
        "orphan_turns": len(orphan_ids),
        "turn_ids_off_shape": len(off_shape),
        "turn_ids_of_another_session": len(other_session),
    }
    return {
        "graph": graph_name,
        "totals": totals,
        "count_mismatch": count_mismatch,
        "per_session": per_session,
        "sessions_without_turns": sessions_without_turns,
        "sessions_declared_and_empty": declared_and_empty,
        "sessions_with_id_space_holes": id_space_holes,
        "orphan_turn_ids": orphan_ids,
        "turn_ids_off_shape": off_shape,
        "turn_ids_of_another_session": other_session,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=["census"])
    ap.add_argument("--graph", required=True,
                    help="the graph NAME (e.g. team_<org_id>) — not an id")
    ap.add_argument("--uri-env", default="FALKORDB_CLOUD_URI",
                    help="the NAME of the environment variable holding the "
                         "graph URI (default FALKORDB_CLOUD_URI) — never the URI")
    ap.add_argument("--out", help="write the census JSON here")
    ap.add_argument("--create-if-missing", action="store_true",
                    help="accept that a census of a non-existent graph CREATES "
                         "its keyspace (refused by default)")
    args = ap.parse_args(argv)

    # ⛔ Decided from the argument's SHAPE, never from a substring: the VALUE is
    # never interpolated, because `--uri-env` is the natural place to paste a URI
    # by mistake and echoing it would print the password this file promises never
    # to print. A substring test for "://" missed the SCHEMELESS spelling
    # (`:SUPERSECRETPW@db.example.com:6379`), which then reached the "not set"
    # message verbatim — a real leak through the guard that exists to prevent it.
    if not _URI_ENV_NAME_RE.fullmatch(args.uri_env):
        raise SystemExit(
            "census: --uri-env takes the NAME of an environment variable "
            "([A-Za-z_][A-Za-z0-9_]*), not the URI itself. Refusing to echo "
            "what was passed (a pasted URI carries a credential) — export it "
            "and pass the variable name, e.g. FALKORDB_CLOUD_URI.")
    uri = os.environ.get(args.uri_env)
    if not uri:
        raise SystemExit(
            f"census: ${args.uri_env} is not set — export the graph URI in that "
            "variable (its value is never echoed).")
    db, shown = _connect(uri)
    _assert_graph_exists(db, args.graph, args.create_if_missing)
    graph = db.select_graph(args.graph)
    try:
        report = census(graph, args.graph)
    except CensusError as exc:
        raise SystemExit(str(exc)) from None

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
          f"ids off the '<session>_t<i>' shape: {t['turn_ids_off_shape']}   "
          f"turn ids of another session: {t['turn_ids_of_another_session']}")
    print(f"count mismatch: {t['sessions_in_count_mismatch']} session(s) whose "
          f"declared turn_count != attached turns (cause NOT measured)   "
          f"[{t['sessions_declared_and_empty']} declare turns and hold none]")
    print(f"id-space holes: {t['sessions_with_id_space_holes']} session(s) "
          f"whose turn ids are not contiguous over their OWN window "
          f"(last index - first index + 1 != attached turns)")
    print(f"non-'event'    : {t['turns_not_point_kind_event']} keyed turn(s) "
          f"whose pointKind is not 'event' (the product readers do not count those)")
    if t["sessions_in_count_mismatch"]:
        for sid, d in list(report["count_mismatch"].items())[:5]:
            print(f"   {sid}: declared={d['declared']} attached={d['attached']}"
                  f"  declared_max_index={d['declared_max_index']}"
                  f" max_index_seen={d['max_index_seen']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        print(f"census written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
