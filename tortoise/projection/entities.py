"""Entity CRUD handlers for FalkorProjection — Point, Subject, Object, Document, Event, Source."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _EntityHandlers:
    """Mixin: entity upsert/delete methods for FalkorProjection."""

    def _upsert(self, p: dict) -> None:
        op = p.get("operator")
        prov = p.get("provenance", {})

        # Compute embedding for non-operator Points (#7778)
        embedding = None
        if not op:
            try:
                from tortoise.embeddings import compute_embedding
                embedding = compute_embedding(p["content"])
            except Exception:
                pass

        self.g.query(
            "MERGE (n:Point {id:$id}) "
            "SET n.content=$content, n.context=$context, "
            "    n.is_operator=$isop, n.op_type=$opt, "
            "    n.pointKind=coalesce($pk, n.pointKind), "
            "    n.status=coalesce($st, n.status, 'live'), "
            "    n.authoredBy=coalesce($ab, n.authoredBy), "
            "    n.embedding=coalesce($embedding, n.embedding), "

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
                    "embedding": embedding,

                    "cf": p.get("confidence"),
                    "ca": p.get("createdAt") or p.get("created_at"),
                    "vf": p.get("validFrom"), "vt": p.get("validTo"),
                    "now": _now_iso()},
        )
        # Ontology v2.1: link Point → Source via extractedFrom edge
        source_ref = p.get("extractedFrom")
        if source_ref:
            self._link_source(p["id"], source_ref)
            # Also store as property for query convenience
            self.g.query("MATCH (n:Point {id:$id}) SET n.extractedFrom = $ref", params={"id": p["id"], "ref": source_ref})
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

    def _delete(self, pid: str) -> None:
        self.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": pid})

    # ── Entity nodes ───────────────────────────────────────────────

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
        title = ev.get("title")  # None default — coalesce needs NULL, not ""
        ok = ev.get("object_kind")  # None default — same issue
        self.g.query(
            "MERGE (o:Object {name:$name}) "
            "ON CREATE SET o.id=$id, o.objectKind=coalesce($ok, 'other'), o.createdAt=coalesce($ca, $now), o.title=coalesce($title, '') "
            "ON MATCH SET o.objectKind=coalesce($ok, o.objectKind), "
            "            o.title=coalesce($title, o.title)",
            params={"id": oid, "name": name,
                    "ok": ok,
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "title": title},
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
