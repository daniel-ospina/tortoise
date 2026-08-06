"""Projection — fold the event log into the current graph.

The log is the source of truth; a projection is a derived, rebuildable view.
`_apply_one` is the single source of fold semantics, shared by the pure `fold`
(batch) and every incremental backend, so an incrementally-updated projection and
`fold(read_all())` can never diverge.

Backends behind the `Projection` protocol:
  - InMemoryProjection — dict of points (statements AND operators).
  - FalkorProjection   — FalkorDB (Docker/server by default, embedded via path=);
                         same openCypher graph so portable between modes
"""
from __future__ import annotations

import re
import os
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

# P0 guard (#99): bulk graph-wipe classifier. Restored here in #49 Phase 2 —
# the guard was lost from this (live) module during the v3.0 ontology rewrite
# (0f9e6a2) and only survived in the legacy standalone projection.py, which
# Phase 2 deletes. This is the ACTIVE wipe-protection: it must live in the
# module the SDK actually imports.
# A query is a bulk wipe when it contains DETACH DELETE but has NO property map
# ({...} — e.g. MATCH (n:Label {id:$id})) and NO real WHERE clause.
# A WHERE clause is "real" only if it references a property (n.xxx) or a
# parameter ($id) or CONTAINS/IN — tautologies (WHERE true, WHERE 1=1) don't count.
_WHERE_REAL_RE = re.compile(
    r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*|\$[a-zA-Z_]|CONTAINS|IN\s*\(",
    re.IGNORECASE,
)


def _is_bulk_wipe(cypher: str) -> bool:
    up = cypher.upper()
    if "DETACH" not in up or "DELETE" not in up:
        return False
    # Property map in MATCH => targeted (e.g. {id:$id}, {team_id:$id})
    if "{" in cypher:
        return False
    # Real WHERE clause (property/param/CONTAINS/IN reference) => targeted
    m = re.search(r"WHERE\s+(.+) ", cypher + " ", re.IGNORECASE)
    if m and _WHERE_REAL_RE.search(m.group(1)):
        return False
    return True


class _GuardedGraph:
    """Wrapper around the FalkorDB Graph handle that guards bulk graph-wipe queries.

    The SDK calls proj.g.query() (raw handle) throughout — the guard must wrap
    self.g itself, not FalkorProjection.query(). Restored in #49 Phase 2 after
    the v3.0 rewrite (0f9e6a2) dropped it from this live module.

    Intercepts bulk DETACH DELETE (no property map, no real WHERE) and asserts
    the graph is a test graph before allowing execution. Targeted deletes
    (MATCH (n:Label {id:$id}) ...) pass through unchanged.
    """

    __slots__ = ("_g", "_proj")

    def __init__(self, g, projection):
        self._g = g
        self._proj = projection

    def query(self, cypher: str, params=None):
        if _is_bulk_wipe(cypher) and not getattr(self._proj, "_skip_guard", False):
            self._proj._assert_test_graph(
                "REFUSING to run bulk DETACH DELETE on non-test graph"
            )
        return self._g.query(cypher, params=params)

    def __getattr__(self, name):
        return getattr(self._g, name)

from tortoise.config import RELATIVE_PATH_ERROR

# ── Mixins ────────────────────────────────────────────────────────────────
from tortoise.projection.entities import _EntityHandlers
from tortoise.projection.edges import _EdgeHandlers
from tortoise.projection.grounding import _GroundingMixin
from tortoise.projection.propagation import _PropagationMixin

# ── Module-level helpers ──────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(ev: dict) -> dict:
    """Normalize event shape — tolerates API (flat) and script (nested point)."""
    if ev.get("point"):
        return {**ev, **ev["point"]}
    return ev


def _apply_one(points: dict[str, dict], ev: dict) -> None:
    ev = _norm(ev)
    t = ev["type"]
    if t in ("PointAdded", "OperatorAdded"):
        p = ev["point"]
        # Phase 1 stop-writes: strip context from v2+ events (#49)
        if ev.get("projection_version", 0) >= 2:
            p.pop("context", None)
        points[p["id"]] = p
        # Flatten speaker from point's provenance into node data
        prov = p.get("provenance", {})
        if prov.get("speaker"):
            p["speaker"] = prov["speaker"]
    elif t == "PointRevised":
        p = points.get(ev["id"])
        if p:
            if ev.get("new_content") is not None:
                p["content"] = ev["new_content"]
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("new_context") is not None and ev.get("projection_version", 0) < 2:
                p["context"] = ev["new_context"]
    elif t == "PointRetracted":
        points.pop(ev["id"], None)
    elif t == "PointsMerged":
        for mid in ev.get("merge_ids", []):
            points.pop(mid, None)
    # IngestStarted: no graph effect


def fold(events: list[dict]) -> dict[str, dict]:
    points: dict[str, dict] = {}
    for ev in events:
        _apply_one(points, ev)
    return points


def split(points: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Partition into (statement points, operator points)."""
    statements, operators = [], []
    for p in points.values():
        (operators if p.get("operator") else statements).append(p)
    return statements, operators


# ── Protocol + InMemory ───────────────────────────────────────────────────


@runtime_checkable
class Projection(Protocol):
    def apply(self, event: dict) -> None: ...
    def rebuild(self, log) -> None: ...


class InMemoryProjection:
    def __init__(self):
        self.points: dict[str, dict] = {}

    def apply(self, event: dict) -> None:
        _apply_one(self.points, event)

    def rebuild(self, log) -> None:
        self.points = fold(log.read_all())


_SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")


def _validate_uri_scheme(scheme: str) -> str:
    """Accept docker:// (local) and redis:// / rediss:// (FalkorDB Cloud) URIs.

    Raises ValueError for anything else, mirroring the historical docker://-only
    contract while making managed-instance URIs first-class.
    """
    if scheme not in _SUPPORTED_URI_SCHEMES:
        raise ValueError(
            f"Unsupported scheme: {scheme} "
            f"(expected docker://, redis://, or rediss://). "
            f"Example: docker://:password@localhost:6379/tortoise"
        )
    return scheme


# ── FalkorProjection ──────────────────────────────────────────────────────


class FalkorProjection(
    _EntityHandlers,
    _EdgeHandlers,
    _GroundingMixin,
    _PropagationMixin,
):
    """FalkorDB-backed projection. Supports Docker/server (default) and embedded modes.

    Embedded:  FalkorProjection(path='/tmp/tortoise.db')
    Docker:    FalkorProjection(host='localhost', port=16379, password='...')
    URI:       FalkorProjection.from_uri('docker://:pass@host:6379/graph')

    Same API regardless of backend — constructor swap is the only difference.
    """

    def __init__(self, path: str | None = None, *,
                 host: str | None = None,
                 port: int = 16379,
                 username: str | None = None,
                 password: str | None = None,
                 graph_name: str = "tortoise",
                 ssl: bool = False,
                 allow_nonstandard_path: bool = False):

        if path is not None:
            # Hard-reject relative paths (plan Task 7, issue #176): a relative
            # path like 'tortoise.db' resolves per-CWD and silently creates a
            # per-directory redislite server (Category-3 leak). Relative is
            # ALWAYS rejected — the escape hatch only permits absolute
            # non-canonical paths. Env TORTOISE_ALLOW_NONSTANDARD_PATH=1
            # enables the same escape hatch without the kwarg.
            if not allow_nonstandard_path and \
                    os.environ.get("TORTOISE_ALLOW_NONSTANDARD_PATH") == "1":
                allow_nonstandard_path = True
            if not os.path.isabs(path) and not path.startswith("~"):
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))
            if path.startswith("~") and not allow_nonstandard_path:
                # tilde is only valid if expanded to absolute via env;
                # unexpanded it is relative-like -> reject with hint
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))

            # Embedded mode (opt-in via path=). Use redislite's FalkorDB client —
            # the plain falkordb.FalkorDB treats a positional path arg as a HOST
            # (IDNA crash: redis tries to resolve the file path as a hostname, #82).
            from redislite.falkordb_client import FalkorDB  # lazy: keep import optional
            self.db = FalkorDB(path)
        elif host is not None:
            # Docker FalkorDB
            from falkordb import FalkorDB  # ponytail: lazy import, only needed for Docker mode
            self.db = FalkorDB(host=host, port=port, username=username, password=password,
                               socket_connect_timeout=5, socket_timeout=10, ssl=ssl)
        else:
            raise ValueError("Either path or host must be provided")

        self.g = _GuardedGraph(self.db.select_graph(graph_name), self)
        self.graph_name = graph_name
        self._graph_name = graph_name
        self._skip_guard = False
        self._is_embedded = (path is not None)
        self._falkordb_version = self._get_falkordb_version()
        if self._falkordb_version is not None and self._falkordb_version[0] < 4:
            import logging
            logging.getLogger(__name__).warning(
                "FalkorDB version %s is below minimum 4.x. FTS and vector indexes will be skipped.",
                '.'.join(map(str, self._falkordb_version)))
        elif self._falkordb_version is None:
            import logging
            logging.getLogger(__name__).warning(
                "Could not determine FalkorDB version. FTS and vector indexes may fail.")
        self._ensure_indexes()

        # Lifecycle hardening (plan Task 4):
        # - _closed flag for idempotent close()
        # - weakref.finalize so GC cleans up without explicit close
        # - atexit so normal process exit never orphans the server
        # - NO per-instance signal handlers (atexit suffices; avoids leaks)
        import atexit as _atexit
        import weakref as _weakref
        self._closed = False
        self._finalizer = _weakref.finalize(self, self.close)
        _atexit.register(self.close)

    @classmethod
    def from_uri(cls, uri: str, graph_name: str | None = None) -> "FalkorProjection":
        """Parse a connection URI into a projection.

        Supported schemes (all treated as docker://):
          docker://:password@host:port/graph_name   — canonical local form
          redis:// or rediss://                     — FalkorDB Cloud / managed
                                                     instances use the redis
                                                     scheme; accept as aliases.

        graph_name overrides the URI path (multi-tenant isolation, #7886) —
        each tenant SDK selects its own graph instead of the URI default.

        Unsupported schemes raise ValueError with an actionable message.
        """
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        _validate_uri_scheme(parsed.scheme)
        username = parsed.username or None
        password = parsed.password or None
        if graph_name is None:
            graph_name = parsed.path.lstrip('/') or "tortoise"
        return cls(host=parsed.hostname or "localhost",
                   port=parsed.port or 16379,
                   username=username,
                   password=password,
                   graph_name=graph_name,
                   ssl=(parsed.scheme == "rediss"))

    def _norm(self, ev: dict) -> dict:
        """Normalize event shape — tolerates API (flat) and script (nested point)."""
        if ev.get("point"):
            # Script format: id lives inside point
            return {**ev, **ev["point"]}
        return ev

    def apply(self, event: dict) -> None:
        ev = self._norm(event)
        t = ev["type"]
        if t in ("PointAdded", "OperatorAdded"):
            p = ev["point"]
            # Phase 1 stop-writes: strip context from v2+ events (#49)
            if ev.get("projection_version", 0) >= 2:
                p.pop("context", None)
            self._upsert(p)
        elif t == "PointRevised":
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("projection_version", 0) >= 2:
                ev.pop("new_context", None)
            self._revise_point(ev, set_updated_at=True)
        elif t == "PointRetracted":
            self._delete(ev["id"])
        elif t == "PointsMerged":
            for mid in event.get("merge_ids", []):
                self._delete(mid)
        elif t == "EventRecorded":
            self._upsert_event(ev)
        elif t == "SubjectAdded":
            self._upsert_subject(ev)
        elif t == "ObjectRegistered":
            self._upsert_object(ev)
        elif t == "DocumentCreated":
            self._upsert_document(ev)
        elif t == "SourceCreated":
            self._upsert_source(ev)

    def rebuild(self, log) -> None:
        self.g.query("MATCH (n) DETACH DELETE n")
        for ev in log.read_all():
            self.apply(ev)

    def rebuild_all(self, log_dir: str) -> dict:
        """Rebuild from all .jsonl files in a directory. Returns counts.

        Two-pass: creates all Point nodes first (pass 1), then operator edges
        and all other event types second (pass 2), so cross-file operator→Point
        references always resolve regardless of filename sort order.

        GAP-19: Full event-type coverage — replays all EventRecorded, SubjectAdded,
        ObjectRegistered, DocumentCreated, ConfidenceChanged, PointRevised events,
        not just PointAdded/OperatorAdded/PointRetracted/PointsMerged.
        """
        import os
        from tortoise.log import EventLog
        self.g.query("MATCH (n) DETACH DELETE n")

        # Collect all events from all files
        events = []
        for fname in sorted(os.listdir(log_dir)):
            if fname.endswith('.jsonl'):
                events.extend(EventLog(os.path.join(log_dir, fname)).read_all())

        # Pass 1: create all Point/Operator nodes (skip edges) + non-edge events
        # Pass 1a: create all Point/Operator nodes first
        # (skip edges), so cross-file PointRevised always has a node to revise (#21).
        for ev in events:
            t = ev["type"]
            if t in ("PointAdded", "OperatorAdded"):
                p = ev["point"]
                # Phase 1 stop-writes: strip context from v2+ events (#49)
                if ev.get("projection_version", 0) >= 2:
                    p.pop("context", None)
                op = p.get("operator")
                prov = p.get("provenance", {})
                # Build SET clauses + params; context is optional (#49)
                set_clauses = [
                    "n.content=$content",
                    "n.is_operator=$isop",
                    "n.op_type=$opt",
                    "n.pointKind=coalesce($pk, n.pointKind)",
                    "n.status=coalesce($st, n.status, 'live')",
                    "n.confidence=coalesce($cf, n.confidence)",
                    "n.createdAt=coalesce($ca, n.createdAt, $now)",
                    "n.updatedAt=$now",
                ]
                params = {
                    "id": p["id"], "content": p["content"],
                    "isop": bool(op),
                    "opt": op["op_type"] if op else None,
                    "pk": p.get("pointKind"),
                    "st": p.get("status"),
                    "cf": p.get("confidence"),
                    "ca": p.get("createdAt") or p.get("created_at"),
                    "now": _now_iso(),
                }
                self.g.query(
                    "MERGE (n:Point {id:$id}) SET " + ", ".join(set_clauses),
                    params=params,
                )

        # Pass 1b: apply revisions + other non-edge events AFTER all nodes exist
        for ev in events:
            t = ev["type"]
            if t in ("PointAdded", "OperatorAdded"):
                continue  # already handled in pass 1a
            elif t == "PointRetracted":
                self._delete(ev.get("id") or ev["event_id"])
            elif t == "PointsMerged":
                for mid in ev.get("merge_ids", []):
                    self._delete(mid)
            elif t == "PointRevised":
                # Phase 1: discard new_context for v2+ events (#49)
                if ev.get("projection_version", 0) >= 2:
                    ev.pop("new_context", None)
                self._revise_point(ev)
            elif t == "EventRecorded":
                self._upsert_event(ev)
            elif t == "SubjectAdded":
                self._upsert_subject(ev)
            elif t == "ObjectRegistered":
                self._upsert_object(ev)
            elif t == "DocumentCreated":
                self._upsert_document(ev)
            # ConfidenceChanged: no graph effect (audit-only event)

        # Pass 2: create edges for all operators
        for ev in events:
            if ev["type"] == "OperatorAdded":
                self._create_edges(ev["point"])

        node_count = self.g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
        edge_count = self.g.query(
            "MATCH ()-[r]->() RETURN count(r)"
        ).result_set[0][0]
        return {"events": len(events), "nodes": node_count, "edges": edge_count}

    def query(self, cypher: str, **params):
        # P0 guard (#99): refuse bulk graph-wipe on non-test graphs.
        # Respect _skip_guard (consistent with _GuardedGraph.query) so a
        # legitimate maintenance bypass works through either call path.
        if _is_bulk_wipe(cypher) and not self._skip_guard:
            self._assert_test_graph(
                f"REFUSING to run bulk DETACH DELETE on non-test graph "
                f"'{self._graph_name}'"
            )
        return self.g.query(cypher, params=params or None)

    def _assert_test_graph(self, reason: str = "") -> None:
        """Raise RuntimeError if the active graph is not a test graph.

        Test graphs must start with 'test_' or 'tortoise_test'.
        Embedded mode (path=) is inherently isolated (per-instance temp DB) —
        the guard does NOT apply to it. Only server mode (docker) needs the
        graph-name check, protecting the shared real graph (#99).
        """
        if getattr(self, "_is_embedded", False):
            return
        if not self._graph_name.startswith(("test_", "tortoise_test")):
            msg = (
                f"Graph guard: operation blocked on non-test graph "
                f"'{self._graph_name}'. "
                f"{reason} — destructive bulk ops require a graph name starting "
                f"with 'test_' or 'tortoise_test'."
            ).rstrip()
            raise RuntimeError(msg)

    def _get_falkordb_version(self):
        """Parse FalkorDB version from db.info().

        Returns (major, minor, patch) tuple or None if undetermined.
        """
        import re
        try:
            info = self.db.info()
        except Exception:
            return None

        info_str = info if isinstance(info, str) else str(info)

        # Module format: module:name=falkordb,ver=40000 (4.0.0)
        m = re.search(r'module:name=(?:falkordb|graph),ver=(\d+)', info_str, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            return (v // 10000, (v // 100) % 100, v % 100)

        # Server section: falkordb_version:4.0
        try:
            server = self.db.info('server')
            server_str = server if isinstance(server, str) else str(server)
            m = re.search(r'falkordb_version:(\d+)\.(\d+)', server_str)
            if m:
                return (int(m.group(1)), int(m.group(2)), 0)
        except Exception:
            pass

        return None

    def _ensure_indexes(self) -> None:
        """Create indexes on frequently-filtered Point properties.

        Wraps CREATE INDEX in try/except because FalkorDB raises
        "Attribute 'X' is already indexed" rather than silently no-opping.
        On subsequent startups, each CREATE INDEX errors immediately
        (O(1) check), so there is no startup penalty on large graphs.

        FTS and vector indexes are gated on FalkorDB >= 4.x.
        """
        # ── Range indexes (always safe, pre-4.x compatible) ──
        for prop in ("id", "pointKind", "content_hash", "is_operator"):
            try:
                self.g.query(f"CREATE INDEX FOR (n:Point) ON (n.{prop})")
            except Exception as e:
                msg = str(e).lower()
                if "already indexed" in msg or "already exists" in msg:
                    pass  # expected — index exists from prior startup
                else:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Failed to create index on n.%s: %s", prop, e)

        # ── Document range indexes (#125 — structural queries filter by kind) ──
        for prop in ("id", "documentKind"):
            try:
                self.g.query(f"CREATE INDEX FOR (n:Document) ON (n.{prop})")
            except Exception as e:
                msg = str(e).lower()
                if "already indexed" in msg or "already exists" in msg:
                    pass
                else:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Failed to create index on Document.%s: %s", prop, e)

        # ── Full-text & vector indexes require FalkorDB 4.x+ (#7779) ──
        _ver = getattr(self, '_falkordb_version', None)
        if _ver is None or _ver[0] >= 4:
            # ── Full-text indexes ──
            for label, field in [("Point", "content"), ("Event", "subject"), ("Subject", "name"),
                                 ("Document", "_searchText")]:  # #125 Document FTS
                try:
                    self.g.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', '{field}')")
                except Exception as e:
                    msg = str(e).lower()
                    if "already" in msg:
                        pass
                    else:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to create fulltext index on %s.%s: %s", label, field, e)

            # ── Vector index (HNSW) — Docker/server FalkorDB only (#7764) ──
            # Embedded mode (redislite) uses brute-force vec.euclideanDistance instead.
            # HNSW requires RediSearch module, not bundled with redislite.
            if not getattr(self, '_is_embedded', False):
                try:
                    self.g.query(
                        "CALL db.idx.vector.createNodeIndex('Point', 'embedding', 384, 'HNSW')"
                    )
                except Exception as e:
                    msg = str(e).lower()
                    if "already" in msg:
                        pass
                    else:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to create vector index on Point.embedding: %s", e)
        else:
            import logging
            logging.getLogger(__name__).info(
                "Skipping FTS and vector indexes: FalkorDB %s < 4.x",
                '.'.join(map(str, _ver)))

    def backfill_document_search_text(self) -> int:
        """#125: set _searchText=title on Documents missing it (idempotent).

        Covers pre-existing Documents created before the capture-fields change.
        Returns the number of Documents backfilled.
        """
        rows = self.g.query(
            "MATCH (d:Document) WHERE d._searchText IS NULL "
            "SET d._searchText = coalesce(d.title, '') "
            "RETURN count(d)"
        ).result_set
        return rows[0][0] if rows else 0

    def close(self) -> None:
        """Close the underlying DB connection idempotently.

        Lifecycle hardening (plan Task 4): idempotent (2nd call no-op),
        registered via weakref.finalize so GC cleans up without explicit
        close, and atexit-registered so normal process exit never orphans.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.db.close()
        except Exception:
            pass

    def __enter__(self) -> "FalkorProjection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _revise_point(self, ev: dict, set_updated_at: bool = False) -> None:
        """Apply PointRevised event — update content, context, and re-compute embedding."""
        new_content = ev.get("new_content")
        new_context = ev.get("new_context")
        pid = ev.get("id") or ev["event_id"]
        params: dict = {"id": pid, "c": new_content}

        # Re-compute embedding when content changes (even to empty — wipe stale).
        # Always set params["embedding"] so SET overwrites any stale value;
        # on compute failure, set to None rather than preserving old embedding (#19).
        if new_content is not None:
            try:
                from tortoise.embeddings import compute_embedding
                emb = compute_embedding(new_content) if new_content else None
                params["embedding"] = emb  # None = wipe stale embedding for empty content
            except Exception:
                params["embedding"] = None  # wipe stale embedding on failure (#19)

        set_clauses = ["n.content = coalesce($c, n.content)"]
        # Phase 2 #49: context removed — new_context no longer written
        if "embedding" in params:
            set_clauses.append("n.embedding = $embedding")
        if set_updated_at:
            set_clauses.append("n.updatedAt = $now")
            params["now"] = _now_iso()

        self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(set_clauses)}",
            params=params,
        )

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self.db.list_graphs()

    # ── SVBP integration (Gate 4) ─────────────────────────────────

    def extract_svbp_factors(self):
        """Extract factor list for TortoiseSVBP from the graph.

        Returns (factors, evidence) where:
          factors = [(op_id, op_type, [input_ids], weight), ...]
          evidence = {claim_id: (alpha, beta), ...}
        """
        rows = self.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true "
            "RETURN o.id, o.op_type"
        ).result_set

        factors = []
        for op_id, op_type in rows:
            input_rows = self.g.query(
                "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->(c:Point) "
                "RETURN c.id ORDER BY c.id",
                params={"oid": op_id},
            ).result_set
            input_ids = [r[0] for r in input_rows]
            if len(input_ids) >= 2:
                weight = 3.0 if op_type == "NAND" else 1.0
                factors.append((op_id, op_type, input_ids, weight))

        return factors, {}

    def get_svbp(self, **svbp_kwargs):
        """Create and run TortoiseSVBP on the current graph.

        Returns a TortoiseSVBP instance with converged beliefs.
        """
        from tortoise.svbp import TortoiseSVBP
        factors, evidence = self.extract_svbp_factors()
        if not factors:
            return None
        svbp = TortoiseSVBP(**svbp_kwargs)
        svbp.run(factors, evidence=evidence)
        return svbp
