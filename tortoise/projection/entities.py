"""Entity CRUD handlers for FalkorProjection — Point, Subject, Object, Document, Event, Source."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_search_text(title, summary=None, topics=None) -> str:
    """#125: compute the Document FTS search surface.

    Concatenates title + summary + topics (None-safe). Always includes title
    so every Document has a search floor.
    """
    parts = [title, summary] + list(topics or [])
    return " ".join(filter(None, parts))


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

        # Build SET clauses + params; context is optional (Phase 1 stop-writes, #49)
        set_clauses = [
            "n.content=$content",
            "n.is_operator=$isop",
            "n.op_type=$opt",
            "n.pointKind=coalesce($pk, n.pointKind)",
            "n.status=coalesce($st, n.status, 'live')",
            "n.authoredBy=coalesce($ab, n.authoredBy)",
            "n.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE n.embedding END",
            "n.confidence=coalesce($cf, n.confidence)",
            "n.createdAt=coalesce($ca, n.createdAt, $now)",
            "n.validFrom=coalesce($vf, n.validFrom)",
            "n.validTo=coalesce($vt, n.validTo)",
            "n.updatedAt=$now",
        ]
        params = {
            "id": p["id"], "content": p["content"],
            "isop": bool(op), "opt": op["op_type"] if op else None,
            "pk": p.get("pointKind"),
            "st": p.get("status"),
            "ab": p.get("authoredBy"),
            "embedding": embedding,
            "cf": p.get("confidence"),
            "ca": p.get("createdAt") or p.get("created_at"),
            "vf": p.get("validFrom"), "vt": p.get("validTo"),
            "now": _now_iso(),
        }
        # Phase 1 backward compat: only write context when present in point dict (#49)
        if "context" in p and p["context"] is not None:
            set_clauses.insert(1, "n.context=$context")
            params["context"] = p["context"]

        self.g.query(
            "MERGE (n:Point {id:$id}) SET " + ", ".join(set_clauses),
            params=params,
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
        # Compute embedding for Subject name (#7845)
        embedding = None
        try:
            from tortoise.embeddings import compute_embedding
            embedding = compute_embedding(name)
        except Exception:
            pass
        self.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$id, s.subjectKind=$sk, s.createdAt=coalesce($ca, $now), "
            "            s.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE s.embedding END "
            "ON MATCH SET s.subjectKind=coalesce($sk, s.subjectKind), "
            "            s.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE s.embedding END",
            params={"id": sid, "name": name,
                    "sk": ev.get("subject_kind", "other"),
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "embedding": embedding},
        )

    def _upsert_object(self, ev: dict) -> None:
        """MERGE Object by name (content-hash dedup).

        Objects are encoded via the Source→references→Object chain —
        embedding from name provides direct vector search capability
        while the provenance chain traces back to source content (#7845).
        """
        oid = ev.get("id")
        name = ev.get("name", "")
        if not oid or not name:
            return
        title = ev.get("title")  # None default — coalesce needs NULL, not ""
        ok = ev.get("object_kind")  # None default — same issue
        # Compute embedding from name (#7845)
        embedding = None
        try:
            from tortoise.embeddings import compute_embedding
            embedding = compute_embedding(name)
        except Exception:
            pass
        self.g.query(
            "MERGE (o:Object {name:$name}) "
            "ON CREATE SET o.id=$id, o.objectKind=coalesce($ok, 'other'), o.createdAt=coalesce($ca, $now), o.title=coalesce($title, ''), "
            "            o.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END "
            "ON MATCH SET o.objectKind=coalesce($ok, o.objectKind), "
            "            o.title=coalesce($title, o.title), "
            "            o.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END",
            params={"id": oid, "name": name,
                    "ok": ok,
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "title": title,
                    "embedding": embedding},
        )

    def _upsert_document(self, ev: dict) -> None:
        """MERGE Document node."""
        did = ev.get("id")
        if not did:
            return
        # Compute embedding from title+content for semantic search (#7845)
        embedding = None
        doc_content = " ".join(filter(None, [
            ev.get("title", ""),
            ev.get("content", ""),
        ]))
        if doc_content.strip():
            try:
                from tortoise.embeddings import compute_embedding
                embedding = compute_embedding(doc_content)
            except Exception:
                pass
        # #125 capture fields — use ev.get(field) with NO default so None →
        # Cypher null, letting coalesce fall through to existing on partial
        # updates. CRITICAL: "" is non-null in Cypher — coalesce("", d.f, ...)
        # returns "" and WIPES existing. Never use "" defaults in SET clauses.
        topics = ev.get("topics")
        summary = ev.get("summary")
        sid = ev.get("session_id")
        eid = ev.get("event_id")
        ds = ev.get("doc_status")
        # _searchText computed only when the event carries meaningful text
        has_text = bool(ev.get("title") or summary or topics)
        st = (_build_search_text(ev.get("title", ""), summary, topics)
              if has_text else None)
        self.g.query(
            "MERGE (d:Document {id:$id}) "
            "SET d.title=coalesce($title, d.title), "
            "    d.documentKind=coalesce($dk, d.documentKind), "
            "    d.format=coalesce($fmt, d.format), "
            "    d.content=coalesce($content, d.content), "
            "    d.topics=coalesce($topics, d.topics, []), "
            "    d.summary=coalesce($summary, d.summary, ''), "
            "    d.sessionId=coalesce($sid, d.sessionId, ''), "
            "    d.eventId=coalesce($eid, d.eventId, ''), "
            "    d.doc_status=coalesce($ds, d.doc_status, 'draft'), "
            "    d._searchText=coalesce($st, d._searchText, d.title), "
            "    d.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE d.embedding END, "
            "    d.updatedAt=$now",
            params={"id": did, "title": ev.get("title", did),
                    "dk": ev.get("document_kind", ""),
                    "fmt": ev.get("format", "markdown"),
                    "content": ev.get("content"),
                    "topics": topics, "summary": summary, "sid": sid,
                    "eid": eid, "ds": ds, "st": st,
                    "embedding": embedding,
                    "now": _now_iso()},
        )
        # #125 — aboutSubject edges when about_entities present (Task 1
        # self-contained: label-agnostic generalization lives in edges.py)
        about = ev.get("about_entities") or []
        if about:
            for ent in about:
                self._create_about_edges(did, ent)

    def _upsert_event(self, event: dict) -> None:
        """MERGE Event node with all ONTOLOGY §3.1 properties.

        Handles both nested ({type:EventRecorded, event:{eventId:...}}) and
        flat (eventId at top level) formats transparently.

        Auto-creates structural edges:
          - (Subject)-[:performs]->(Event) from event.subject
          - (Event)-[:produces]->(Object) from event.object
          - (Event)-[:uses]->(Object) from event.uses (list or single)
        """
        inner = event.get("event", event)  # unwrap nested format
        eid = inner.get("id") or inner.get("eventId")
        if not eid:
            return
        # Compute embedding from event content/description (#7845)
        embedding = None
        event_content = " ".join(filter(None, [
            inner.get("subject", ""),
            inner.get("eventKind", ""),
            inner.get("object", ""),
        ]))
        if event_content.strip():
            try:
                from tortoise.embeddings import compute_embedding
                embedding = compute_embedding(event_content)
            except Exception:
                pass
        props = {
            "id": eid,  # ensure Event node has id for edge matching (#122)
            "eventKind": inner.get("eventKind", ""),
            "subject": inner.get("subject", ""),
            "object": inner.get("object", ""),
            "startedAt": inner.get("startedAt", ""),
            "endedAt": inner.get("endedAt"),
            "parentEvent": inner.get("parentEvent"),
            "participants": inner.get("participants", []),
            "classificationLevel": inner.get("classificationLevel", "internal"),
            "format": inner.get("format", "jsonl"),
        }
        if embedding is not None:
            props["embedding"] = embedding
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) "
            "ON CREATE SET e += $props "
            "ON MATCH SET e += $props",
            params={"eid": eid, "props": props},
        )
        # ── Auto-create structural edges (#122) ──
        # Subject -[:performs]-> Event
        subj = inner.get("subject", "")
        if subj:
            self.g.query(
                "MERGE (s:Subject {name:$name}) "
                "ON CREATE SET s.id=$name, s.subjectKind='other'",
                params={"name": subj},
            )
            self.g.query(
                "MATCH (s:Subject {name:$name}), (e:Event {eventId:$eid}) "
                "MERGE (s)-[:performs]->(e)",
                params={"name": subj, "eid": eid},
            )
        # Event -[:produces]-> Object (or Document when objectType='document', #125)
        obj = inner.get("object", "")
        object_type = inner.get("objectType", "")  # 'Document' | 'Object' | '' (legacy)
        if obj:
            if object_type == "Document":
                self.g.query(
                    "MERGE (d:Document {id:$id}) "
                    "ON CREATE SET d.title=$id, d.documentKind='transcript'",
                    params={"id": obj},
                )
                self.g.query(
                    "MATCH (d:Document {id:$id}), (e:Event {eventId:$eid}) "
                    "MERGE (e)-[:produces]->(d)",
                    params={"id": obj, "eid": eid},
                )
            else:
                self.g.query(
                    "MERGE (o:Object {name:$name}) "
                    "ON CREATE SET o.id=$name, o.objectKind='other'",
                    params={"name": obj},
                )
                self.g.query(
                    "MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "
                    "MERGE (e)-[:produces]->(o)",
                    params={"name": obj, "eid": eid},
                )
        # Event -[:uses]-> Object (input entities, #122; #125 structured dicts)
        uses = inner.get("uses")
        if uses:
            if isinstance(uses, str):
                uses = [uses]
            elif isinstance(uses, dict):
                uses = [uses]  # bare dict → normalize to list
            for use_item in uses:
                if isinstance(use_item, dict):
                    # #125 structured uses: {name, kind} → objectKind from kind
                    use_name = use_item.get("name", "")
                    use_kind = use_item.get("kind", "other")
                else:
                    # legacy string uses → default objectKind='other'
                    use_name = str(use_item)
                    use_kind = "other"
                if use_name:
                    self.g.query(
                        "MERGE (o:Object {name:$name}) "
                        "ON CREATE SET o.id=$name, o.objectKind=$kind "
                        "ON MATCH SET o.objectKind=$kind",
                        params={"name": use_name, "kind": use_kind},
                    )
                    self.g.query(
                        "MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "
                        "MERGE (e)-[:uses]->(o)",
                        params={"name": use_name, "eid": eid},
                    )

    def _upsert_source(self, ev: dict) -> None:
        """MERGE Source node for layered provenance (Ontology v2.1).

        Source properties: url (permalink), sourceType, contentHash, title,
        ingestedAt, version, externalId. Creates stub if missing.
        """
        sid = ev.get("id")
        url = ev.get("url", "")
        if not sid and not url:
            return
        key = url or sid
        self.g.query(
            "MERGE (s:Source {url: $url}) "
            "ON CREATE SET s.id = coalesce($id, $url), "
            "              s.sourceKind = $sk, "
            "              s.contentHash = $hash, "
            "              s.title = $title, "
            "              s.ingestedAt = $now, "
            "              s.version = 1, "
            "              s.externalId = $ext "
            "ON MATCH SET s.contentHash = $hash, "
            "           s.title = coalesce($title, s.title), "
            "           s.version = s.version + 1, "
            "           s.updatedAt = $now",
            params={
                "url": key, "id": sid or key,
                "sk": ev.get("sourceKind", "document"),
                "hash": ev.get("contentHash", ""),
                "title": ev.get("title", key),
                "now": _now_iso(),
                "ext": ev.get("externalId", ""),
            },
        )
