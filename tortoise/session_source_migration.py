"""#4125 — migrate pre-#4005 basename-url session ``:Source`` nodes.

WHAT THIS EXISTS FOR
====================
#4005 replaced the hosted commit path's session-Source identity
(``os.path.basename(ref.path)`` — literally ``'session.md'``) with the canonical
``session:<session_id>`` ONTOLOGY §4.6 registers — the SAME identity the capture
path materializes (``sdk._materialize_session_source``), the projection stub
mints (``projection.edges._mint_source_stub``) and ``delete_session`` / the
capture orphan sweep delete. Nodes minted BEFORE that fix keep their bare
basename ``url`` and are never rewritten, so:

* a re-capture/re-commit of an existing session mints a SECOND ``agentSession``
  ``:Source`` at ``session:<id>`` while the legacy node survives with its old
  ``extractedFrom``/``references`` edges (the index entry is duplicated, not
  corrected) — measured in this module's suite; and
* the legacy node is unreachable by ``delete_session`` (which deletes only
  ``session:<id>``), so it LEAKS on delete.

This module is the migration for that population, AND the reconciler the
whole-journal replay engines call so the migration is REBUILD-DURABLE (the
#2296 write-surface invariant — a graph-only migration is undone by
``rebuild_all``; see REBUILD DURABILITY below).

A RECONCILIATION, NOT A ONE-SHOT SCRIPT
=======================================
``reconcile_legacy_session_sources`` is idempotent and re-runnable: it derives
everything it needs from the graph it is handed, so it can be run against a
live tenant (``graph-scripts/4125_migrate_legacy_session_sources.py``), against
a freshly replayed graph (the replay engines), or again after a partial run —
with the same result. ``dry_run=True`` performs no write of any kind and
returns the same report shape.

ATTRIBUTION — the authority for "which session is this node?"
=============================================================
A legacy node's canonical identity is DERIVED, never invented; where it cannot
be derived, the node is left in place and reported. Two surfaces carry the
session id, and they are not equally durable:

1. **``(Point)-[:extractedFrom]->(legacy)`` + the Point's own ``session_id``.**
   Journal-durable: the property and the edge are both replayed from the
   ``PointAdded`` record, so this attribution SURVIVES ``rebuild_all``.
2. **``(legacy)-[:references]->(doc)`` + ``doc.sessionId``.** LIVE-ONLY: the
   hosted commit path writes the Document with raw Cypher and emits no journal
   record, so a rebuild does not reproduce it (measured).

The union of the two is the attributed session set. A node whose set is EMPTY
is reported as ``unattributed`` and never rewritten — no canonical identity can
be derived for it, and inventing one is worse than a stranded node.

THE COLLISION CASE (one legacy node, several sessions)
=====================================================
The pre-fix identity COLLIDED two machines' ``session.md`` onto ONE node — the
defect #4005 removed. Such a node has no single canonical identity, so the
reconciler moves each *attributable* edge to the canonical node of the session
that edge names (a Point's ``session_id``; a Document target's ``sessionId``),
then deletes the node only once nothing is left on it. An edge no session can
be attributed to BLOCKS the delete (an unknown edge is never traded for a
guess), and the node is reported as ``ambiguous``/``blocked`` instead. The
node's own metadata is merged only when the attribution is unambiguous (exactly
one session): a mixed node's ``contentHash``/``provenance_spans`` describe no
single session.

THE PRE-FIX CONTENT HASH IS NOT AN ANCHOR
=========================================
Pre-#4005 the legacy node's ``contentHash`` was ``content_hash(url)`` — a hash
of the FILENAME, which "could not detect that the raw had changed, existed, or
was gone" (#4005). Carrying that value onto the canonical node would plant a
false current-version anchor (§4.6 / #3998), so it is REFUSED
(:func:`is_pre_fix_url_hash`) and only a non-``hash(url)`` value is ever
carried — and only into a canonical node that has no anchor of its own.

REBUILD DURABILITY
==================
The journal is APPEND-ONLY (``tortoise/log.py``: "sealing writes bytes, it
never rewrites"), so a migration cannot rewrite the legacy lines, and no mapping
for them exists in the journal to fold. What the journal DOES reproduce is
attribution #1: the replayed Points carry ``session_id`` and their
``extractedFrom`` edge still names the legacy node. So each whole-journal replay
engine runs this same reconciler at the end of its trailing sweep
(``rebuild_all``, ``rebuild``, ``recover_from_log`` and ``backup.restore``'s
JSONL fallback): the migrated state is RE-DERIVED from the journal on every
replay instead of being recorded in it. That is also why the migration cannot
drift from its own durability mechanism — they are one function.

Known residual, stated rather than implied: the single-record ``apply()`` path
does not run it (it has no journal-wide view), so a differential replay of ONLY
the legacy records re-materializes the legacy identity until the next
whole-journal replay or migration run. That is the same shape as the other
whole-journal-only sweeps (``fold_deferred_entity_links``, the #7951
hard-delete re-assert), and it is why the CLI stays runnable at any time.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CANONICAL_PREFIX",
    "MOVABLE_SOURCE_RELS",
    "find_legacy_session_sources",
    "is_pre_fix_url_hash",
    "reconcile_after_replay",
    "reconcile_legacy_session_sources",
]

#: The canonical session-Source prefix (ONTOLOGY §4.6, #4005). A legacy node is
#: an ``agentSession`` ``:Source`` whose url does NOT start with it.
CANONICAL_PREFIX = "session:"

#: Relationship types this reconciler will MOVE off a legacy node. A closed,
#: auditable set: a relationship type outside it BLOCKS the delete, so an
#: unrecognised edge is never traded for a rewrite, and the type is named in
#: the report. Source-side rels the ontology registers: outgoing
#: ``references``; incoming ``extractedFrom`` / ``aboutSource`` /
#: ``aboutDocument`` / ``wasDerivedFrom`` / ``produces``.
MOVABLE_SOURCE_RELS = frozenset({
    "extractedFrom", "references", "aboutSource", "aboutDocument",
    "wasDerivedFrom", "produces",
})

#: A relationship type is interpolated into the move statement's pattern (Cypher
#: cannot parameterise a rel type), so it must be a bare identifier. Anything
#: else is UNMOVABLE — it blocks the delete and is reported, exactly like an
#: unknown type, rather than being pasted into a query.
_REL_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_LEGACY_CANDIDATES = (
    "MATCH (old:Source) "
    "WHERE old.sourceKind = 'agentSession' "
    "  AND old.url IS NOT NULL "
    "  AND NOT old.url STARTS WITH $prefix "
    "RETURN old.url, properties(old) ORDER BY old.url"
)

# A Point's own session id. Both spellings: the in-tree write paths use
# `session_id`; `sessionId` is accepted so an out-of-tree producer that used the
# camelCase spelling is not silently unattributable.
_POINT_EDGE_SESSIONS = (
    "MATCH (p:Point)-[:extractedFrom]->(old:Source {url:$u}) "
    "RETURN p.id, coalesce(p.session_id, p.sessionId)"
)

# The targets of the node's own `references` edges, with the session each target
# names (a Document is a `:Source` carrying `sessionId` since D10; a
# non-document target names none and is reported as `None`).
_REF_TARGET_SESSIONS = (
    "MATCH (old:Source {url:$u})-[:references]->(t) "
    "RETURN coalesce(t.url, t.id, t.eventId), "
    "       coalesce(t.sessionId, t.session_id)"
)

_OUT_TYPES = (
    "MATCH (old:Source {url:$u})-[r]->(t) "
    "RETURN DISTINCT type(r) ORDER BY type(r)"
)
_IN_TYPES = (
    "MATCH (n)-[r]->(old:Source {url:$u}) "
    "RETURN DISTINCT type(r) ORDER BY type(r)"
)
_DEGREE = (
    "MATCH (old:Source {url:$u}) "
    "WITH old, size([(old)-[x]-(y) | x]) AS deg RETURN deg"
)


def is_pre_fix_url_hash(url: str, stored_hash: str | None) -> bool:
    """True when *stored_hash* is the pre-#4005 ``hash(url)`` placeholder.

    The pre-fix writer passed ``content_hash(url)`` — a hash of the basename,
    which says nothing about the raw (#4005). Such a value must never be
    carried onto the canonical node: it would read as a current-version anchor
    that cannot detect a changed, missing or absent raw (§4.6 / #3998).
    """
    if not stored_hash or not url:
        return False
    return stored_hash == hashlib.sha256(str(url).encode("utf-8")).hexdigest()


# ── graph helpers ─────────────────────────────────────────────────────────


def _rows(g, cypher: str, **params) -> list:
    res = g.query(cypher, params=params) if params else g.query(cypher)
    return list(res.result_set) if res is not None and res.result_set else []


def _scalar(g, cypher: str, **params) -> Any:
    rows = _rows(g, cypher, **params)
    return rows[0][0] if rows and rows[0] else None


def _clean_sid(value: Any) -> str | None:
    """A usable session id, or ``None`` — a blank/None id attributes nothing."""
    if not isinstance(value, str):
        return None
    sid = value.strip()
    return sid or None


def _degree(g, url: str) -> int:
    return int(_scalar(g, _DEGREE, u=url) or 0)


def _rel_types(g, url: str) -> tuple[list[str], list[str]]:
    """(outgoing, incoming) relationship type names on the legacy node."""
    out = [r[0] for r in _rows(g, _OUT_TYPES, u=url) if r and r[0]]
    inc = [r[0] for r in _rows(g, _IN_TYPES, u=url) if r and r[0]]
    return out, inc


def _canonical_exists(g, canonical_url: str) -> bool:
    return bool(_scalar(g, "MATCH (s:Source {url:$v}) RETURN s.url", v=canonical_url))


def _attribution(g, url: str) -> dict:
    """Attributed session set for one legacy url + the per-edge detail.

    ``point_sessions`` maps a Point id to the session it names (``None`` when
    the Point carries none); ``ref_sessions`` maps a ``references`` target key
    to the session IT names (``None`` for a non-document target).

    The carriers are exhaustive for the in-tree writers: the hosted commit path
    wires ``extractedFrom`` from POINTS only (``create_point(...
    extractedFrom=)``, hosted_api.py) and the connector/capture paths wire
    ``(Source)-[:references]->(Event)`` — an OUTGOING edge, whose target carries
    no session and so is treated as unattributable (moved only when the node's
    attribution is unambiguous, never silently discarded).
    """
    point_sessions: dict[str, str | None] = {}
    for row in _rows(g, _POINT_EDGE_SESSIONS, u=url):
        pid = row[0]
        if isinstance(pid, str):
            point_sessions[pid] = _clean_sid(row[1] if len(row) > 1 else None)
    ref_sessions: dict[str, str | None] = {}
    for row in _rows(g, _REF_TARGET_SESSIONS, u=url):
        key = row[0]
        if isinstance(key, str):
            ref_sessions[key] = _clean_sid(row[1] if len(row) > 1 else None)
    sessions = {s for s in point_sessions.values() if s}
    sessions |= {s for s in ref_sessions.values() if s}
    return {
        "sessions": sorted(sessions),
        "point_sessions": point_sessions,
        "ref_sessions": ref_sessions,
    }


# ── the move primitives ───────────────────────────────────────────────────


def _move_out(g, url: str, canonical_url: str, rel: str,
              target_key: str | None, dry_run: bool) -> int:
    """Move ``(legacy)-[rel]->(t)`` onto the canonical node (optionally one target).

    ``MERGE … ON CREATE SET r2 = properties(r)`` carries the edge's own attrs —
    including the #5199 ``sourceVersion`` anchor — so a re-point cannot lose the
    version the provenance edge recorded. An edge whose target IS the canonical
    node is skipped: re-pointing it would only make a self-loop.

    The self-loop guard is NULL-SAFE (``t.url IS NULL OR t.url <> $v``): a
    target that is not a ``:Source`` (an Event, an Object) has no ``url``, and a
    bare ``t.url <> $v`` evaluates to NULL there — which would silently SKIP
    every non-Source target, the exact class this writer is for (measured: a
    Point's ``extractedFrom`` edge was never moved).
    """
    where = "(t.url IS NULL OR t.url <> $v) "
    params = {"u": url, "v": canonical_url}
    if target_key is not None:
        where += "AND coalesce(t.url, t.id, t.eventId) = $k "
        params["k"] = target_key
    if dry_run:
        return int(_scalar(
            g,
            f"MATCH (old:Source {{url:$u}})-[r:`{rel}`]->(t) WHERE {where}"
            "RETURN count(r)", **params) or 0)
    rows = _rows(
        g,
        f"MATCH (old:Source {{url:$u}})-[r:`{rel}`]->(t) WHERE {where}"
        "MATCH (new:Source {url:$v}) "
        f"MERGE (new)-[r2:`{rel}`]->(t) ON CREATE SET r2 = properties(r) "
        "DELETE r RETURN count(r2)", **params)
    return int(rows[0][0]) if rows and rows[0] else 0


def _move_in(g, url: str, canonical_url: str, rel: str, *,
             point_sid: str | None, dry_run: bool) -> int:
    """Move ``(n)-[rel]->(legacy)`` onto the canonical node.

    With ``point_sid`` set, only ``:Point`` edges whose own ``session_id`` names
    that session move — the per-session split a collided node needs. Without it
    every edge of that type moves (the caller has established the node has one
    session, so the remainder belongs to it). The self-loop guard is NULL-SAFE
    for the same reason as :func:`_move_out`: the source side of an
    ``extractedFrom`` edge is a Point/Event, which has no ``url`` at all.
    """
    if point_sid is not None:
        match = (f"MATCH (p:Point)-[r:`{rel}`]->(old:Source {{url:$u}}) "
                 "WHERE coalesce(p.session_id, p.sessionId) = $sid ")
        src, params = "p", {"u": url, "v": canonical_url, "sid": point_sid}
    else:
        match = (f"MATCH (n)-[r:`{rel}`]->(old:Source {{url:$u}}) "
                 "WHERE (n.url IS NULL OR n.url <> $v) ")
        src, params = "n", {"u": url, "v": canonical_url}
    if dry_run:
        return int(_scalar(g, match + "RETURN count(r)", **params) or 0)
    rows = _rows(
        g,
        match + f"MATCH (new:Source {{url:$v}}) "
        f"MERGE ({src})-[r2:`{rel}`]->(new) ON CREATE SET r2 = properties(r) "
        "DELETE r RETURN count(r2)", **params)
    return int(rows[0][0]) if rows and rows[0] else 0


def _merge_metadata(g, url: str, canonical_url: str, props: dict, *,
                    carry: bool) -> bool:
    """MERGE the canonical ``session:<id>`` Source; True when it was CREATED.

    The create shape MIRRORS the #4005 commit path's node
    (``_upsert_source``'s ON CREATE — measured: ``id``/``canonicalUrl``/
    ``urlAliases``/``title``/``version=1``), plus the legacy spelling recorded as
    an alias (the ``urlAliases`` trace the #4005 write site calls "an alias rule
    for pre-fix basename nodes").

    Metadata is carried from the legacy node ONLY when ``carry`` (an
    unambiguous single-session attribution) and ONLY into a slot the canonical
    node does not already own:

    * ``provenance_spans`` / ``sourceDate`` / ``sourcePath`` / ``externalId`` —
      filled when empty (the spans describe the same raw; ``sourcePath`` is the
      W-7 basename the identity deliberately does not carry);
    * ``contentHash`` — filled only when the legacy value is NOT the pre-fix
      ``hash(url)`` placeholder (:func:`is_pre_fix_url_hash`);
    * the legacy node's ``validFrom``/``version`` are NOT carried: an absent
      window start is the honest open/unbounded value for a version whose real
      start belongs to a different identity's write clock (the #3644
      legacy-record rule), and merging an identity does not invent a version
      ordinal.

    ``updatedAt`` is deliberately NOT written: the reconciler must not invent
    state a writer did not produce (live-vs-replay divergence is the class this
    module's own durability argument rests on).
    """
    existed = _canonical_exists(g, canonical_url)
    spans = props.get("provenance_spans")
    spans = list(spans) if carry and isinstance(spans, (list, tuple)) else []
    stored_hash = props.get("contentHash")
    hash_ok = (
        carry and isinstance(stored_hash, str) and stored_hash
        and not is_pre_fix_url_hash(url, stored_hash)
    )
    g.query(
        "MERGE (new:Source {url:$v}) "
        "ON CREATE SET new.id = $v, new.canonicalUrl = $v, "
        "              new.urlAliases = [$v], new.sourceKind = 'agentSession', "
        "              new.title = $v, "
        "              new.version = 1, new.ingestedAt = $now "
        "SET new.sourceKind = coalesce(new.sourceKind, 'agentSession'), "
        "    new.is_episodic = coalesce(new.is_episodic, true), "
        "    new.sourcePath = coalesce(new.sourcePath, $u), "
        "    new.sourceDate = coalesce(new.sourceDate, $sd), "
        "    new.externalId = coalesce(new.externalId, $ext), "
        "    new.provenance_spans = CASE "
        "        WHEN coalesce(size(new.provenance_spans), 0) = 0 THEN $spans "
        "        ELSE new.provenance_spans END, "
        # An absent anchor STAYS absent (§4.6 / #3998): the CASE only fills a
        # hash we have (a non-`hash(url)` legacy value), never writes `''` or
        # clears a stored one.
        "    new.contentHash = CASE WHEN $hash IS NOT NULL "
        "        AND coalesce(new.contentHash, '') = '' "
        "        THEN $hash ELSE new.contentHash END, "
        "    new.urlAliases = CASE WHEN $u IN coalesce(new.urlAliases, []) "
        "        THEN coalesce(new.urlAliases, []) "
        "        ELSE coalesce(new.urlAliases, []) + [$u] END",
        params={
            "v": canonical_url, "u": url,
            "now": datetime.now(UTC).isoformat(),
            "spans": spans,
            "hash": stored_hash if hash_ok else None,
            "sd": props.get("sourceDate") if carry else None,
            "ext": props.get("externalId") if carry else None,
        },
    )
    return not existed


def reconcile_after_replay(projection, *, engine: str) -> dict | None:
    """End-of-replay-sweep entry point for the whole-journal replay engines.

    Re-derives the migrated state from the journal the replay just reconstructed
    (see the module docstring, REBUILD DURABILITY) so a rebuild cannot resurrect
    the legacy identity. NEVER raises: this runs AFTER the wipe, where an
    exception would leave an unproven graph (#2943 "no loss without proof") — a
    failure is reported and the operator can re-run the CLI, which is the same
    reconciliation. A projection without a graph handle (an injected fake in a
    test) is a silent no-op.
    """
    g = getattr(projection, "g", None)
    if g is None:
        return None
    try:
        report = reconcile_legacy_session_sources(g)
    except Exception:  # noqa: BLE001, RUF100
        logger.exception(
            "%s: legacy session-Source reconciliation FAILED — the graph is "
            "rebuilt but pre-#4005 basename session Sources were not migrated; "
            "re-run graph-scripts/4125_migrate_legacy_session_sources.py on the "
            "graph (#4125)", engine)
        return None
    if report["migrated"]:
        logger.info(
            "%s: migrated %d pre-#4005 basename session Source(s) onto "
            "session:<id> (%d edge(s) re-pointed, %d canonical node(s) minted) "
            "(#4125)", engine, len(report["migrated"]),
            report["edges_repointed"], len(report["canonical_created"]))
    if report["unattributed"] or report["ambiguous"] or report["blocked"]:
        logger.warning(
            "%s: %d legacy session Source(s) could NOT be migrated "
            "(%d unattributed, %d ambiguous%s) — no canonical identity is "
            "derivable for them from the journal; they stay in place and need "
            "an operator decision (#4125)",
            engine,
            len(report["unattributed"]) + len(report["ambiguous"])
            + len(report["blocked"]),
            len(report["unattributed"]), len(report["ambiguous"]),
            ", {} blocked by an unmovable relation".format(
                len(report["blocked"]))
            if report["blocked"] else "")
    return report


def _delete_if_orphan(g, url: str) -> bool:
    """DETACH DELETE the legacy node ONLY once nothing is left on it."""
    rows = _rows(
        g,
        "MATCH (old:Source {url:$u}) "
        "WITH old, size([(old)-[x]-(y) | x]) AS deg "
        "WHERE deg = 0 DETACH DELETE old RETURN old.url", u=url)
    return bool(rows)


def _blocking_rel_types(out_types, in_types) -> list[str]:
    """Present rel types this reconciler cannot move (unknown or not an identifier)."""
    return sorted({
        t for t in set(out_types) | set(in_types)
        if t not in MOVABLE_SOURCE_RELS or not _REL_TYPE_RE.match(t)
    })


# ── public surface ────────────────────────────────────────────────────────


def find_legacy_session_sources(g) -> list[dict]:
    """READ-ONLY: every legacy node + the attribution the migration would use.

    ``g`` is anything exposing ``.query(cypher, params=...)`` (a projection's
    ``g``). This is the dry-run/audit surface: it writes nothing of any kind.
    """
    out: list[dict] = []
    for row in _rows(g, _LEGACY_CANDIDATES, prefix=CANONICAL_PREFIX):
        url = row[0]
        if not isinstance(url, str) or not url:
            continue
        att = _attribution(g, url)
        out.append({
            "url": url,
            "sessions": att["sessions"],
            "points": dict(att["point_sessions"]),
            "references": dict(att["ref_sessions"]),
            "degree": _degree(g, url),
        })
    return out


def reconcile_legacy_session_sources(g, *, dry_run: bool = False) -> dict:
    """Migrate the graph's basename-url session Sources onto ``session:<id>``.

    Idempotent and re-runnable; ``dry_run=True`` writes nothing and returns the
    plan it would execute. Never raises for a candidate it cannot attribute —
    that candidate is reported and left untouched.

    Returns:
      ``candidates``         legacy ``agentSession`` urls found,
      ``migrated``           urls fully re-pointed and deleted,
      ``unattributed``       urls with no derivable session (left in place),
      ``ambiguous``          urls still holding edges afterwards (left in place),
      ``blocked``            url -> relationship types that blocked a delete,
      ``edges_repointed``    edges moved onto a canonical node,
      ``canonical_created``  canonical urls the run minted,
      ``dry_run``            the flag this call carried.
    """
    report: dict = {
        "candidates": [], "migrated": [], "unattributed": [], "ambiguous": [],
        "blocked": {}, "edges_repointed": 0, "canonical_created": [],
        "dry_run": bool(dry_run),
    }
    for row in _rows(g, _LEGACY_CANDIDATES, prefix=CANONICAL_PREFIX):
        url = row[0]
        if not isinstance(url, str) or not url:
            continue
        report["candidates"].append(url)
        props = row[1] if len(row) > 1 and isinstance(row[1], dict) else {}
        att = _attribution(g, url)
        sessions = att["sessions"]
        if not sessions:
            # No derivable canonical identity: never invent one.
            report["unattributed"].append(url)
            continue

        out_types, in_types = _rel_types(g, url)
        blocking = _blocking_rel_types(out_types, in_types)
        if blocking:
            report["blocked"][url] = blocking
        single = sessions[0] if len(sessions) == 1 else None
        moved = 0
        if single is not None:
            # Unambiguous: everything on the node belongs to this one session.
            canonical_url = f"{CANONICAL_PREFIX}{single}"
            created = (not _canonical_exists(g, canonical_url)) if dry_run else \
                _merge_metadata(g, url, canonical_url, props, carry=True)
            if created:
                report["canonical_created"].append(canonical_url)
            for rel in out_types:
                if rel in blocking:
                    continue
                moved += _move_out(g, url, canonical_url, rel, None, dry_run)
            for rel in in_types:
                if rel in blocking:
                    continue
                moved += _move_in(g, url, canonical_url, rel,
                                  point_sid=None, dry_run=dry_run)
        else:
            # The pre-fix collision: one node, several sessions. Move only the
            # edges that NAME a session; nothing else can be attributed.
            for sid in sessions:
                canonical_url = f"{CANONICAL_PREFIX}{sid}"
                created = (not _canonical_exists(g, canonical_url)) if dry_run \
                    else _merge_metadata(g, url, canonical_url, props, carry=False)
                if created:
                    report["canonical_created"].append(canonical_url)
                for rel in out_types:
                    if rel in blocking:
                        continue
                    for key, tsid in att["ref_sessions"].items():
                        if tsid == sid:
                            moved += _move_out(g, url, canonical_url, rel, key,
                                               dry_run)
                for rel in in_types:
                    if rel in blocking:
                        continue
                    moved += _move_in(g, url, canonical_url, rel,
                                      point_sid=sid, dry_run=dry_run)
        report["edges_repointed"] += moved

        # Report the OUTCOME the same way in both modes: a node is "migrated"
        # only when nothing is left holding it (an unmovable relation, or an
        # edge no session attributes, leaves it in place and is reported).
        if dry_run:
            would_orphan = not blocking and _degree(g, url) - moved <= 0
            (report["migrated"] if would_orphan
             else report["ambiguous"]).append(url)
        elif _delete_if_orphan(g, url):
            report["migrated"].append(url)
        else:
            report["ambiguous"].append(url)
    return report
