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

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

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
            if ev.get("new_context") is not None:
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
                 graph_name: str = "tortoise"):

        if path is not None:
            # Embedded mode (opt-in via path=). Check for redislite .settings file
            from falkordb import FalkorDB  # lazy: keep import optional
            import json as _json
            from pathlib import Path as _Path
            settings_file = _Path(path).with_suffix('.db.settings')
            if settings_file.exists():
                settings = _json.loads(settings_file.read_text())
                socket = settings.get('unixsocket', '')
                if socket:
                    self.db = FalkorDB(unix_socket_path=socket)
                else:
                    self.db = FalkorDB(path)
            else:
                self.db = FalkorDB(path)
        elif host is not None:
            # Docker FalkorDB
            from falkordb import FalkorDB  # ponytail: lazy import, only needed for Docker mode
            self.db = FalkorDB(host=host, port=port, username=username, password=password, socket_connect_timeout=5, socket_timeout=10)
        else:
            raise ValueError("Either path or host must be provided")

        self.graph_name = graph_name
        self.g = self.db.select_graph(graph_name)
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

    @classmethod
    def from_uri(cls, uri: str) -> "FalkorProjection":
        """Parse docker:// connection string.

        docker://[user]:password@host:port/graph_name
        """
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        if parsed.scheme != "docker":
            raise ValueError(f"Unsupported scheme: {parsed.scheme} (expected docker://)")
        username = parsed.username or None
        password = parsed.password or None
        graph_name = parsed.path.lstrip('/') or "tortoise"
        return cls(host=parsed.hostname or "localhost",
                   port=parsed.port or 16379,
                   username=username,
                   password=password,
                   graph_name=graph_name)

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
            self._upsert(ev["point"])
        elif t == "PointRevised":
            self._revise_point(ev, set_updated_at=True)
        elif t == "PointRetracted":
            self._delete(ev["id"])
        elif t == "PointsMerged":
            for mid in ev.get("merge_ids", []):
                self._delete(mid)
        elif t == "EventRecorded":
            self._upsert_event(ev)
        elif t == "SubjectAdded":
            self._upsert_subject(ev)
        elif t == "ObjectRegistered":
            self._upsert_object(ev)
        elif t == "DocumentCreated":
            self._upsert_document(ev)

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
        try:
            self._rebuild_pass1(events)
        except Exception:
            # If pass 1 fails partway through, the graph is in an inconsistent
            # state. Wipe and re-raise so the caller can retry from clean.
            self.g.query("MATCH (n) DETACH DELETE n")
            raise

        # Pass 2: create edges for all operators
        try:
            self._rebuild_pass2(events)
        except Exception:
            self.g.query("MATCH (n) DETACH DELETE n")
            raise

        node_count = self.g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
        edge_count = self.g.query(
            "MATCH ()-[r]->() RETURN count(r)"
        ).result_set[0][0]
        return {"events": len(events), "nodes": node_count, "edges": edge_count}

    def _rebuild_pass1(self, events: list) -> None:
        for ev in events:
            t = ev["type"]
            if t in ("PointAdded", "OperatorAdded"):
                p = ev["point"]
                op = p.get("operator")
                prov = p.get("provenance", {})
                self.g.query(
                    "MERGE (n:Point {id:$id}) "
                    "SET n.content=$content, n.context=$context, "
                    "    n.is_operator=$isop, n.op_type=$opt, "
                    "    n.pointKind=coalesce($pk, n.pointKind), "
                    "    n.status=coalesce($st, n.status, 'live'), "
                    "    n.confidence=coalesce($cf, n.confidence), "
                    "    n.createdAt=coalesce($ca, n.createdAt, $now), "
                    "    n.updatedAt=$now",
                    params={"id": p["id"], "content": p["content"],
                            "context": p["context"],
                            "isop": bool(op),
                            "opt": op["op_type"] if op else None,
                            "pk": p.get("pointKind"),
                            "st": p.get("status"),
                            "cf": p.get("confidence"),
                            "ca": p.get("createdAt") or p.get("created_at"),
                            "now": _now_iso()},
                )
            elif t == "PointRetracted":
                self._delete(ev.get("id") or ev.get("event_id"))
            elif t == "PointsMerged":
                for mid in ev.get("merge_ids", []):
                    self._delete(mid)
            elif t == "PointRevised":
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

    def _rebuild_pass2(self, events: list) -> None:
        for ev in events:
            if ev["type"] == "OperatorAdded":
                self._create_edges(ev["point"])

    def query(self, cypher: str, **params):
        return self.g.query(cypher, params=params or None)

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
        for prop in ("id", "pointKind", "context", "content_hash", "is_operator"):
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

        # ── Full-text & vector indexes require FalkorDB 4.x+ (#7779) ──
        _ver = getattr(self, '_falkordb_version', None)
        if _ver is None or _ver[0] >= 4:
            # ── Full-text indexes ──
            for label, field in [("Point", "content"), ("Event", "subject"), ("Subject", "name"), ("Document", "title")]:
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
                # Core entity types with embeddings (#7845): Point, Event, Subject, Document, Object.
                # Action is skipped — procedural, not content-bearing.
                for label in ("Point", "Event", "Subject", "Document", "Object"):
                    try:
                        self.g.query(
                            f"CALL db.idx.vector.createNodeIndex('{label}', 'embedding', 384, 'HNSW')"
                        )
                    except Exception as e:
                        msg = str(e).lower()
                        if "already" in msg:
                            pass
                        else:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Failed to create vector index on %s.embedding: %s", label, e)
        else:
            import logging
            logging.getLogger(__name__).info(
                "Skipping FTS and vector indexes: FalkorDB %s < 4.x",
                '.'.join(map(str, _ver)))

    def close(self) -> None:
        self.db.close()

    def _revise_point(self, ev: dict, set_updated_at: bool = False) -> None:
        """Apply PointRevised event — update content, context, and re-compute embedding."""
        new_content = ev.get("new_content")
        pid = ev.get("id") or ev["event_id"]
        params: dict = {"id": pid, "c": new_content, "x": ev.get("new_context")}

        # Re-compute embedding when content changes (even to empty — wipe stale)
        if new_content is not None:
            try:
                from tortoise.embeddings import compute_embedding
                emb = compute_embedding(new_content) if new_content else None
                params["embedding"] = emb  # None = wipe stale embedding for empty content
            except Exception:
                pass

        set_clauses = ["n.content = coalesce($c, n.content)",
                       "n.context = coalesce($x, n.context)"]
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
