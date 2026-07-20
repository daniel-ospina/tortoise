"""Projection — fold the event log into the current graph.

The log is the source of truth; a projection is a derived, rebuildable view.
`_apply_one` is the single source of fold semantics, shared by the pure `fold`
(batch) and every incremental backend, so an incrementally-updated projection and
`fold(read_all())` can never diverge.

Backends behind the `Projection` protocol:
  - InMemoryProjection — dict of points (statements AND operators).
  - FalkorProjection   — FalkorDBLite (embedded); same openCypher graph as server
                         FalkorDB, so it's portable to a server later.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

import numpy as np
from typing import Protocol, runtime_checkable


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


class FalkorProjection:
    """FalkorDB-backed projection. Supports embedded (FalkorDBLite) and Docker modes.

    Embedded:  FalkorProjection(path='/tmp/tortoise.db')
    Docker:    FalkorProjection(host='localhost', port=6379, password='...')
    URI:       FalkorProjection.from_uri('docker://:pass@host:6379/graph')

    Same API regardless of backend — constructor swap is the only difference.
    """

    def __init__(self, path: str | None = None, *,
                 host: str | None = None,
                 port: int = 6379,
                 password: str | None = None,
                 graph_name: str = "tortoise"):

        if path is not None:
            # Embedded FalkorDBLite (backward compatible)
            from redislite.falkordb_client import FalkorDB  # lazy: keep import optional
            self.db = FalkorDB(path)
        elif host is not None:
            # Docker FalkorDB
            from falkordb import FalkorDB  # ponytail: lazy import, only needed for Docker mode
            self.db = FalkorDB(host=host, port=port, password=password)
        else:
            raise ValueError("Either path or host must be provided")

        self.g = self.db.select_graph(graph_name)

    @classmethod
    def from_uri(cls, uri: str) -> "FalkorProjection":
        """Parse docker:// connection string.

        docker://:password@host:port/graph_name
        """
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        if parsed.scheme != "docker":
            raise ValueError(f"Unsupported scheme: {parsed.scheme} (expected docker://)")
        password = parsed.password or None
        graph_name = parsed.path.lstrip('/') or "tortoise"
        return cls(host=parsed.hostname or "localhost",
                   port=parsed.port or 6379,
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
            self.g.query(
                "MATCH (n:Point {id:$id}) "
                "SET n.content = coalesce($c, n.content), "
                "    n.context = coalesce($x, n.context), "
                "    n.updatedAt = $now",
                params={"id": ev["id"], "c": ev.get("new_content"),
                        "x": ev.get("new_context"), "now": _now_iso()},
            )
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

    def _upsert(self, p: dict) -> None:
        op = p.get("operator")
        prov = p.get("provenance", {})
        self.g.query(
            "MERGE (n:Point {id:$id}) "
            "SET n.content=$content, n.context=$context, "
            "    n.is_operator=$isop, n.op_type=$opt, "
            "    n.pointKind=coalesce($pk, n.pointKind), "
            "    n.status=coalesce($st, n.status, 'live'), "
            "    n.authoredBy=coalesce($ab, n.authoredBy), "

            "    n.confidence=coalesce($cf, n.confidence), "
            "    n.createdAt=coalesce($ca, n.createdAt, $now), "
            "    n.validFrom=coalesce($vf, n.validFrom), "
            "    n.validTo=coalesce($vt, n.validTo), "
            "    n.updatedAt=$now",
            params={"id": p["id"], "content": p["content"], "context": p["context"],
                    "isop": bool(op), "opt": op["op_type"] if op else None,
                    "pk": p.get("pointKind"),
                    "st": p.get("status"),
                    "ab": p.get("authoredBy"),

                    "cf": p.get("confidence"),
                    "ca": p.get("createdAt") or p.get("created_at"),
                    "vf": p.get("validFrom"), "vt": p.get("validTo"),
                    "now": _now_iso()},
        )
        # Ontology v2.1: link Point → Source via extractedFrom edge
        source_ref = p.get("extractedFrom")
        if source_ref:
            self._link_source(p["id"], source_ref)
        # aboutEntities → per-type about edges (Ontology v2.1 Phase 1)
        about = p.get("aboutEntities")
        if about and isinstance(about, list):
            for entity_name in about:
                self._create_about_edges(p["id"], str(entity_name))
        # P1-2: Temporal — also store provenance source_id
        if prov.get("source_id"):
            self.g.query(
                "MATCH (n:Point {id:$id}) SET n.provenanceSource=$sid",
                params={"id": p["id"], "sid": prov["source_id"]},
            )
        if op:
            self._create_edges(p)

    def _create_edges(self, p: dict) -> None:
        """Create typed edges for an operator Point. Auto-creates stub nodes
        for missing source Points referenced by short IDs (#6713)."""
        op = p["operator"]
        rel_type = {"NAND": "NAND", "IMPL": "IMPL",
                     "composedOf": "hasPart", "decomposesInto": "hasPart",
                     "contains": "hasPart", "wraps": "hasPart"}.get(op["op_type"])
        if rel_type is None:
            return  # unknown op_type — no edges to create
        for idx, src in enumerate(op["inputs"]):
            # ponytail: auto-create stub if source Point doesn't exist.
            # Short numeric IDs are orphan refs from cross-file wiring scripts.
            if len(src) < 20:  # short IDs (non-ULID) are suspect
                exists = self.g.query(
                    "MATCH (s:Point {id:$sid}) RETURN count(s) > 0",
                    params={"sid": src}
                ).result_set[0][0]
                if not exists:
                    self.g.query(
                        "CREATE (s:Point {id:$sid}) "
                        "SET s.content='[missing]', s.context='orphan-stub', "
                        "    s.is_operator=false",
                        params={"sid": src}
                    )
            self.g.query(
                f"MATCH (o:Point {{id:$oid}}) "
                f"MATCH (s:Point {{id:$sid}}) "
                f"MERGE (o)-[:{rel_type} {{idx:$idx}}]->(s)",
                params={"oid": p["id"], "sid": src, "idx": idx},
            )


    def _delete(self, pid: str) -> None:
        self.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": pid})

    # ── P1-1: Provenance chain ────────────────────────────────────

    def _link_source(self, point_id: str, source_ref: str) -> None:
        """Link Point → Source via extractedFrom edge (Ontology v2.1).

        Creates stub Source if missing, keyed on url. The references edge
        (Source → Document/Event/Object) is set by DocumentCreated events.
        """
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceType='document', s.title=$url, "
            "    s.contentHash='', s.ingestedAt=$now",
            params={"url": source_ref, "now": _now_iso()},
        )
        self.g.query(
            "MATCH (n:Point {id:$pid}), (s:Source {url:$url}) "
            "MERGE (n)-[:extractedFrom]->(s)",
            params={"pid": point_id, "url": source_ref},
        )

    # ponytail: SDK compat alias (Phase 1b will rename caller)
    _link_extracted_from = _link_source

    # ── Ontology v2.1: about edges ─────────────────────────────────

    def _create_about_edges(self, point_id: str, entity_name: str) -> None:
        """Link Point to named Subject or Object via aboutSubject/aboutObject edge.

        Tries Subject first, then Object, to auto-detect entity type from
        the legacy flat aboutEntities list. Creates stub if neither exists.
        """
        # Try Subject
        subj = self.g.query(
            "MATCH (s:Subject {name:$name}) RETURN s.name LIMIT 1",
            params={"name": entity_name},
        ).result_set
        if subj:
            self.g.query(
                "MATCH (n:Point {id:$pid}), (s:Subject {name:$name}) "
                "MERGE (n)-[:aboutSubject]->(s)",
                params={"pid": point_id, "name": entity_name},
            )
            return
        # Try Object
        obj = self.g.query(
            "MATCH (o:Object {name:$name}) RETURN o.name LIMIT 1",
            params={"name": entity_name},
        ).result_set
        if obj:
            self.g.query(
                "MATCH (n:Point {id:$pid}), (o:Object {name:$name}) "
                "MERGE (n)-[:aboutObject]->(o)",
                params={"pid": point_id, "name": entity_name},
            )
            return
        # Neither exists — default to Subject stub
        self.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$name, s.subjectKind='other'",
            params={"name": entity_name},
        )
        self.g.query(
            "MATCH (n:Point {id:$pid}), (s:Subject {name:$name}) "
            "MERGE (n)-[:aboutSubject]->(s)",
            params={"pid": point_id, "name": entity_name},
        )

    # ── P1-4: Entity linking (Subject / Object) ───────────────────

    def _upsert_subject(self, ev: dict) -> None:
        """MERGE Subject by name (content-hash dedup)."""
        sid = ev.get("id")
        name = ev.get("name", "")
        if not sid or not name:
            return
        self.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$id, s.subjectKind=$sk, s.createdAt=coalesce($ca, $now) "
            "ON MATCH SET s.subjectKind=coalesce($sk, s.subjectKind)",
            params={"id": sid, "name": name,
                    "sk": ev.get("subject_kind", "other"),
                    "ca": ev.get("createdAt"), "now": _now_iso()},
        )

    def _upsert_object(self, ev: dict) -> None:
        """MERGE Object by name (content-hash dedup)."""
        oid = ev.get("id")
        name = ev.get("name", "")
        if not oid or not name:
            return
        self.g.query(
            "MERGE (o:Object {name:$name}) "
            "ON CREATE SET o.id=$id, o.objectKind=$ok, o.createdAt=coalesce($ca, $now) "
            "ON MATCH SET o.objectKind=coalesce($ok, o.objectKind)",
            params={"id": oid, "name": name,
                    "ok": ev.get("object_kind", "other"),
                    "ca": ev.get("createdAt"), "now": _now_iso()},
        )

    def _upsert_document(self, ev: dict) -> None:
        """MERGE Document node."""
        did = ev.get("id")
        if not did:
            return
        self.g.query(
            "MERGE (d:Document {id:$id}) "
            "SET d.title=coalesce($title, d.title), "
            "    d.documentKind=coalesce($dk, d.documentKind), "
            "    d.format=coalesce($fmt, d.format), "
            "    d.updatedAt=$now",
            params={"id": did, "title": ev.get("title", did),
                    "dk": ev.get("document_kind", ""),
                    "fmt": ev.get("format", "markdown"),
                    "now": _now_iso()},
        )

    # ── P1-3: Staleness detection ─────────────────────────────────

    def stale_points(self, days: int = 30, limit: int = 50) -> list[dict]:
        """Find Points not updated in N days (older createdAt as fallback)."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "  AND coalesce(n.updatedAt, n.createdAt, '') < $cutoff "
            "RETURN n.id, n.content, n.pointKind, "
            "  coalesce(n.updatedAt, n.createdAt) as lastUpdate "
            "ORDER BY lastUpdate ASC LIMIT $limit",
            params={"cutoff": cutoff, "limit": limit},
        ).result_set
        return [{"id": r[0], "content": r[1], "pointKind": r[2],
                 "lastUpdate": r[3]} for r in rows]

    def _upsert_event(self, event: dict) -> None:
        """MERGE Event node with all ONTOLOGY §3.1 properties.

        Handles both nested ({type:EventRecorded, event:{eventId:...}}) and
        flat (eventId at top level) formats transparently.
        """
        inner = event.get("event", event)  # unwrap nested format
        eid = inner.get("id") or inner.get("eventId")
        if not eid:
            return
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) "
            "ON CREATE SET e += $props "
            "ON MATCH SET e += $props",
            params={"eid": eid, "props": {
                "eventKind": inner.get("eventKind", ""),
                "subject": inner.get("subject", ""),
                "object": inner.get("object", ""),
                "startedAt": inner.get("startedAt", ""),
                "endedAt": inner.get("endedAt"),
                "parentEvent": inner.get("parentEvent"),
                "participants": inner.get("participants", []),
                "classificationLevel": inner.get("classificationLevel", "internal"),
                "format": inner.get("format", "jsonl"),
            }}
        )

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
        from .log import EventLog
        self.g.query("MATCH (n) DETACH DELETE n")

        # Collect all events from all files
        events = []
        for fname in sorted(os.listdir(log_dir)):
            if fname.endswith('.jsonl'):
                events.extend(EventLog(os.path.join(log_dir, fname)).read_all())

        # Pass 1: create all Point/Operator nodes (skip edges) + non-edge events
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
                self._delete(ev.get("id") or ev["event_id"])
            elif t == "PointsMerged":
                for mid in ev.get("merge_ids", []):
                    self._delete(mid)
            elif t == "PointRevised":
                self.g.query(
                    "MATCH (n:Point {id:$id}) "
                    "SET n.content = coalesce($c, n.content), "
                    "    n.context = coalesce($x, n.context)",
                    params={"id": ev.get("id") or ev["event_id"],
                            "c": ev.get("new_content"),
                            "x": ev.get("new_context")},
                )
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

    def edge_stats(self) -> dict:
        """Return {operators, impl_edges, nand_edges, input_edges} for diagnostics."""
        ops = self.g.query(
            "MATCH (n:Point) WHERE n.is_operator = true RETURN count(n)"
        ).result_set[0][0]
        impl = self.g.query(
            "MATCH ()-[r:IMPL]->() RETURN count(r)"
        ).result_set[0][0]
        nand = self.g.query(
            "MATCH ()-[r:NAND]->() RETURN count(r)"
        ).result_set[0][0]
        inp = self.g.query(
            "MATCH ()-[r:INPUT]->() RETURN count(r)"
        ).result_set[0][0]
        return {"operators": ops, "impl_edges": impl, "nand_edges": nand,
                "input_edges": inp}

    def query(self, cypher: str, **params):
        return self.g.query(cypher, params=params or None)

    def compute_grounding(self, lam: float = 0.6) -> dict[str, float]:
        """Solve (I - lam*M)g = a and write n.grounding on every :Point.

        M is the row-normalized adjacency from :IMPL and :NAND edges (symmetric).
        a_i = 1.0 for resolution-event Points, 0 otherwise.

        Returns {point_id: grounding_value}."""
        # 1. Read all Point IDs → index mapping
        rows = self.g.query("MATCH (n:Point {status: 'live'}) RETURN n.id ORDER BY n.id").result_set
        ids = [r[0] for r in rows]
        n = len(ids)
        idx = {pid: i for i, pid in enumerate(ids)}
        if n == 0:
            return {}

        # 2. Build sparse symmetric adjacency from :IMPL and :NAND edges
        try:
            from scipy.sparse import coo_matrix
            from scipy.sparse.linalg import spsolve
            from scipy.sparse import eye as speye
        except ImportError:
            raise ImportError(
                "scipy required for compute_grounding; install with: pip install scipy"
            )
        rows, cols = [], []
        for rel in ("IMPL", "NAND"):
            edges = self.g.query(
                f"MATCH (a:Point {{status: 'live'}})-[:{rel}]->(b:Point {{status: 'live'}}) RETURN a.id, b.id"
            ).result_set
            for src, tgt in edges:
                if src in idx and tgt in idx:
                    i, j = idx[src], idx[tgt]
                    rows.extend([i, j])
                    cols.extend([j, i])  # symmetric: relevance is undirected

        # 3. Row-normalize → M (sparse)
        A = coo_matrix(([1.0] * len(rows), (rows, cols)), shape=(n, n)).tocsr()
        rowsum = np.array(A.sum(axis=1)).ravel()
        rowsum[rowsum == 0] = 1.0
        D_inv = coo_matrix(
            (1.0 / rowsum, (range(n), range(n))), shape=(n, n)
        ).tocsr()
        M = D_inv @ A

        # 4. Activity vector a from resolution events + resolution vectors.
        #    Exclude operator points — they propagate, they don't originate.
        #    ponytail: uniform activity (no timestamps to EWMA over); add
        #    EWMA decay (alpha=0.3) when resolution events carry timestamps.
        res = self.g.query(
            "MATCH (n:Point) WHERE n.context IN ['resolution-event','resolution-vector'] "
            "AND (n.is_operator IS NULL OR n.is_operator = false) RETURN n.id"
        ).result_set
        a = np.zeros(n)
        for (pid,) in res:
            if pid in idx:
                a[idx[pid]] = 1.0

        # 5. g = (I - lam*M)^-1 a  (sparse linear system)
        g = spsolve(speye(n, format='csr') - lam * M, a)

        # 6. Write back to each :Point node
        for pid, i in idx.items():
            self.g.query(
                "MATCH (n:Point {id:$id}) SET n.grounding=$g",
                params={"id": pid, "g": float(g[i])},
            )

        return {pid: float(g[idx[pid]]) for pid in ids}

    # ── Shock Propagation (#6707) ───────────────────────────────────

    def propagate_shock(self, epicenter_id: str, *, max_depth: int = 2,
                        damping: float = 0.5, threshold: float = 0.05
                        ) -> dict[str, tuple[float, float]]:
        """BFS shock propagation through IMPL (supports) and NAND (contradicts)
        edges.  Ported from /Users/home/eldato/operations/memory/epistemic.py.

        Each BFS step carries the parent's computed confidence into the child's
        _compute_confidence — the parent signal blends with local edge-ratio
        evidence BEFORE the inertia damping is applied.  Without this, _compute_
        confidence sees only static edge counts and the shock never flows."""
        changed: dict[str, tuple[float, float]] = {}
        visited: set[str] = set()
        
        # Skip if epicenter is deprecated/superseded
        status = self._node_status(epicenter_id)
        if status and status != 'live':
            return {}
        
        queue: deque[tuple[str, int, float | None]] = deque(
            [(epicenter_id, 0, None)]
        )

        while queue:
            node_id, depth, parent_conf = queue.popleft()
            if depth > max_depth or node_id in visited:
                continue
            visited.add(node_id)

            old = self._confidence(node_id)
            new = self._compute_confidence(node_id, parent_conf)
            if depth > 0:
                new = old * damping + new * (1 - damping)
            new = round(new, 4)

            if abs(new - old) > threshold:
                self.g.query(
                    "MATCH (n:Point {id:$id}) SET n.confidence=$v",
                    params={"id": node_id, "v": new},
                )
                changed[node_id] = (old, new)

            if depth < max_depth:
                for nid in self._neighbors(node_id):
                    if nid not in visited:
                        queue.append((nid, depth + 1, new))

        return changed

    def _compute_confidence(self, node_id: str,
                            parent_confidence: float | None = None) -> float:
        """Weighted confidence from neighbor beliefs, not just edge counts.

        Each IMPL edge contributes the neighbor's current confidence;
        each NAND edge contributes (1 - neighbor's confidence) — so a
        confident contradiction hurts more than a tentative one. Default 0.5
        when no edges or no neighbors have confidence set.
        """
        s_rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point) "
            "RETURN coalesce(m.confidence, 0.5)",
            params={"id": node_id},
        ).result_set
        c_rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:NAND]-(m:Point) "
            "RETURN coalesce(m.confidence, 0.5)",
            params={"id": node_id},
        ).result_set

        s_weighted = sum(r[0] for r in s_rows)
        c_weighted = sum(r[0] for r in c_rows)
        total = s_weighted + c_weighted
        base = s_weighted / total if total else 0.5
        if parent_confidence is None:
            return base
        return base * 0.5 + parent_confidence * 0.5

    def _confidence(self, node_id: str) -> float:
        r = self.g.query(
            "MATCH (n:Point {id:$id}) RETURN coalesce(n.confidence, 0.5)",
            params={"id": node_id},
        ).result_set
        return float(r[0][0]) if r else 0.5

    def _neighbors(self, node_id: str) -> list[str]:
        """All Points reachable via IMPL or NAND edges (both directions)."""
        rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point {status: 'live'}) RETURN DISTINCT m.id",
            params={"id": node_id},
        ).result_set
        neighbors = {r[0] for r in rows}
        rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:NAND]-(m:Point {status: 'live'}) RETURN DISTINCT m.id",
            params={"id": node_id},
        ).result_set
        neighbors.update(r[0] for r in rows)
        return list(neighbors)

    def close(self) -> None:
        self.db.close()

    # ── SVBP integration (Gate 4) ─────────────────────────────────

    def extract_svbp_factors(self):
        """Extract factor list for TortoiseSVBP from the graph.

        Returns (factors, evidence) where:
          factors = [(op_id, op_type, [input_ids], weight), ...]
          evidence = {claim_id: (alpha, beta), ...}
        """
        # Query all operator nodes
        rows = self.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true "
            "RETURN o.id, o.op_type"
        ).result_set

        factors = []
        for op_id, op_type in rows:
            # Get inputs for this operator
            input_rows = self.g.query(
                "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->(c:Point) "
                "RETURN c.id ORDER BY c.id",
                params={"oid": op_id},
            ).result_set
            input_ids = [r[0] for r in input_rows]
            if len(input_ids) >= 2:
                weight = 3.0 if op_type == "NAND" else 1.0
                factors.append((op_id, op_type, input_ids, weight))

        # Evidence: currently no per-claim evidence in the graph.
        # ponytail: evidence is set at SVBP construction time, not stored in graph.
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
