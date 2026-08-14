"""Entity CRUD handlers for FalkorProjection — Point, Subject, Object, Document, Event, Source."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# #388: connector sourceKinds eligible for choke-point Source materialization in
# _upsert_event. Pinned explicitly (NOT SOURCE_KIND_DEFAULTS registry membership,
# which also contains 'document' + T0-T4 tier forms) so the mining.py exclusion is
# precise: mining events carry bare `source` without sourceKind/sourceUrl and must
# never materialize a Source node.
_CONNECTOR_SOURCE_KINDS: frozenset = frozenset({
    "github_issue", "github_pr", "linear_card", "slack_message",
    # #388: linear_cycle rides BOTH legs — the explicit container-level
    # fallback sourceUrl (linear.py) and the kind leg (belt-and-suspenders,
    # mirroring the github.py wiring comment): a cycle event missing
    # sourceUrl must still materialize via its registered kind.
    "linear_cycle",
})


def _is_real_source_url(url: str) -> bool:
    """#388 conf-60: a REAL web URL (http(s) permalink / resource) vs a
    container-level fallback key (`slack:{channel}`, `linear:{team_key}`,
    bare `source` string). The stale-sweep direction guard keys off this:
    fallback keys are never authoritative over a real URL (see
    ``_materialize_connector_source``)."""
    return url.startswith(("http://", "https://"))


def _build_search_text(title, summary=None, topics=None) -> str:
    """#125: compute the Document FTS search surface.

    Concatenates title + summary + topics (None-safe). Always includes title
    so every Document has a search floor.
    """
    parts = [title, summary] + list(topics or [])
    return " ".join(filter(None, parts))


class _EntityHandlers:
    """Mixin: entity upsert/delete methods for FalkorProjection."""

    # Event dict keys that are never stored as node properties (#228).
    _META_KEYS: frozenset = frozenset({
        "type",              # event type
        "projection_version",# internal version tracking
        "version",           # event format version
        "about_entities",    # handled as graph edges
        "authoredBy",        # handled as authoredBy edge
        "ownedBy",           # handled as ownedBy edge
        "managedBy",         # handled as managedBy edge
        "aboutSubject",      # handled as aboutSubject edge
        "aboutObject",       # handled as aboutObject edge
        "aboutEvent",        # handled as aboutEvent edge
        "aboutPoint",        # handled as aboutPoint edge
        "aboutDocument",     # handled as aboutDocument edge
        # journal meta keys (epic #900 T3, §4.2 cycle-16/17): the SDK's
        # _emit_event style-3 lines carry event_id/ts/initiated_by (+agent_id
        # on api._emit) + corrects — structural, never node properties. One
        # global skip-set keeps live/replay consistent across entity types.
        "event_id",
        "ts",
        "initiated_by",
        "agent_id",
        "corrects",
    })

    # Keys explicitly handled by each _upsert_* method.
    _SUBJECT_HANDLED: frozenset = frozenset({
        "id", "name", "subject_kind", "subjectKind", "createdAt", "embedding",
    })
    _OBJECT_HANDLED: frozenset = frozenset({
        "id", "name", "object_kind", "objectKind", "createdAt", "title", "embedding",
    })
    _DOCUMENT_HANDLED: frozenset = frozenset({
        "id", "title", "document_kind", "documentKind", "content",
        "topics", "summary", "session_id", "event_id", "doc_status",
        "source_path", "format", "embedding", "updatedAt",
        "about_entities", "objectKind", "status",
        # epic #900 T3 (§4.1 route pin): the indexer's source_url override
        # (→ the #205 auto-wire target) and the embedding-suppression flag
        # ride the journaled DocumentCreated event but must NEVER persist as
        # node props (_persist_extra_props skip-set membership).
        "source_url",
        "suppress_embedding",
    })
    _EVENT_HANDLED: frozenset = frozenset({
        "id", "eventId", "eventKind", "event",
        "subject", "object", "startedAt", "endedAt",
        "parentEvent", "participants", "classificationLevel",
        "format", "embedding",
        "aboutSubject", "aboutObject", "objectType", "uses",
        "childEvents", "scopedFacts",
        # name / eventStatus / createdAt are intentionally NOT here —
        # they were historically dropped by the fixed-field MERGE and
        # are now persisted as arbitrary props via _persist_extra_props.
    })
    _SOURCE_HANDLED: frozenset = frozenset({
        "id", "url", "sourceKind", "contentHash",
        "title", "ingestedAt", "version", "externalId", "updatedAt",
        # epic #900 T3 (§4.1): the ev keys `source_path` (→ s.sourcePath via
        # the MERGE clause, never persisted verbatim snake_case) and
        # `_searchText` (set by the write path, coalesce-on-create /
        # overwrite-on-hash-diff — §4.1 cycle-4 merge semantics).
        "source_path",
        "_searchText",
    })

    def _persist_extra_props(self, match_clause: str, match_params: dict,
                              ev: dict, handled_keys: frozenset) -> None:
        """Persist arbitrary caller-supplied props not explicitly handled.

        Computes the set difference between event dict keys and the union of
        _META_KEYS + handled_keys, then applies SET n += $extra on the
        matched node.  Skips the query entirely when there are no extra props.

        None values are excluded — Cypher null semantics in SET maps are
        unreliable (coalesce-based updates use explicit per-field clauses).
        """
        skip = self._META_KEYS | handled_keys
        extra = {k: v for k, v in ev.items() if k not in skip and v is not None}
        if extra:
            self.g.query(
                match_clause + " SET n += $extra",
                params={**match_params, "extra": extra},
            )

    def _upsert_point_props(self, p: dict) -> None:
        """Write all Point node properties (no edges).

        Single source of truth for Point property parity between apply() and
        rebuild_all() (#330): rebuild pass 1a calls this so a rebuilt graph can
        never drift from the incrementally-applied graph on node properties.
        """
        op = p.get("operator")
        if not isinstance(op, dict):
            # #331 (review r5): parity with _create_edges' r4 guard — a
            # truthy non-dict operator (e.g. a bare string) must degrade
            # to no-operator, not AttributeError in op.get("op_type").
            op = None
        prov = p.get("provenance")
        if not isinstance(prov, dict):
            # #331 (review r3): explicit null / string provenance must not
            # crash the Falkor path (parity with _apply_one's guard).
            prov = {}

        # Compute embedding for non-operator Points (#7778)
        embedding = None
        # #331 (review r4): only embed real content — an empty string
        # produced a junk vector in the HNSW index.
        if not op and p.get("content"):
            try:
                from tortoise.embeddings import compute_embedding
                embedding = compute_embedding(p.get("content", ""))
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
            "id": p["id"], "content": p.get("content", ""),
            "isop": bool(op), "opt": op.get("op_type") if op else None,
            "pk": p.get("pointKind"),
            "st": p.get("status"),
            "ab": p.get("authoredBy"),
            "embedding": embedding,
            "cf": p.get("confidence"),
            "ca": p.get("createdAt") or p.get("created_at"),
            "vf": p.get("validFrom"), "vt": p.get("validTo"),
            "now": _now_iso(),
        }
        # A10 operator-scoped replay extension (cycle-22/23): the OperatorAdded
        # point snapshot carries `direction` (stored ALWAYS) + `label` (stored
        # when truthy) on the node — the fixed SET list above drops them,
        # which post-rebuild (a) leaves direction=NULL (a direction-omitting
        # bundle's resubmission MISSES its run-1 operator → duplicate +
        # exactly-once violated), (b) re-opens the label cross-absorption
        # class (a label-absent retry matches rebuilt label-NULL operators),
        # and (c) flips every unidirectional operator to bidirectional in EP
        # (ep.py coalesce default). Write them from the payload (ZERO new
        # record fields — the carrier already exists).
        if op:
            set_clauses.append("n.direction=$dir")
            params["dir"] = p.get("direction")
            if p.get("label") is not None:
                set_clauses.append("n.label=$label")
                params["label"] = p["label"]
        # Phase 2 #49: context removed — never written
        self.g.query(
            "MERGE (n:Point {id:$id}) SET " + ", ".join(set_clauses),
            params=params,
        )
        # Ontology v2.1: also store extractedFrom as property for query convenience
        source_ref = p.get("extractedFrom")
        if source_ref:
            self.g.query("MATCH (n:Point {id:$id}) SET n.extractedFrom = $ref", params={"id": p["id"], "ref": source_ref})
        # P1-2: Temporal — also store provenance source_id
        if prov.get("source_id"):
            self.g.query(
                "MATCH (n:Point {id:$id}) SET n.provenanceSource=$sid",
                params={"id": p["id"], "sid": prov["source_id"]},
            )

    def _upsert_point_edges(self, p: dict) -> None:
        """Wire all Point edges (provenance + about + operator).

        Single source of truth for Point edge parity between apply() and
        rebuild_all() pass 2 (#330) — same role as _upsert_point_props for
        node properties.
        """
        # Ontology v2.1: link Point → Source via extractedFrom edge
        source_ref = p.get("extractedFrom")
        if source_ref:
            self._link_source(p["id"], source_ref)
        # aboutEntities → per-type about edges (Ontology v2.1 Phase 1)
        about = p.get("aboutEntities")
        if about and isinstance(about, list):
            for entity_name in about:
                self._create_about_edges(p["id"], str(entity_name))
        if p.get("operator"):
            self._create_edges(p)

    def _upsert(self, p: dict) -> None:
        """Upsert a Point: node properties via _upsert_point_props, then edges."""
        self._upsert_point_props(p)
        self._upsert_point_edges(p)

    def _delete(self, pid: str) -> None:
        self.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": pid})

    def _retract(self, pid: str) -> None:
        """Mark a Point as retracted instead of hard-deleting (#689).

        Retracted points are hidden from normal reads (get_point, query,
        paginated_query all filter status='retracted') but remain queryable via
        raw Cypher. This preserves data integrity — retraction is reversible.

        Historical note: prior to #689, retraction hard-deleted points via
        DETACH DELETE. Points retracted before this change are irrecoverably
        lost (the content existed only in the projection, and the projection
        deleted it). Future retractions leave this tombstone.
        """
        self.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'retracted', n.updatedAt = $now",
            params={"id": pid, "now": _now_iso()},
        )

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
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Subject {name: $name})", {"name": name},
            ev, self._SUBJECT_HANDLED,
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
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Object {name: $name})", {"name": name},
            ev, self._OBJECT_HANDLED,
        )

    def _upsert_document(self, ev: dict) -> None:
        """MERGE Document node."""
        did = ev.get("id")
        if not did:
            return
        # Compute embedding from title+content for semantic search (#7845).
        # Epic #900 T3 cycle-19: the NEW index path carries a suppress flag in
        # the ev dict (suppress_embedding) — the doc path computes the call
        # UNCONDITIONALLY today (title is always present on new-path docs), an
        # undeclared prop + unbounded network call on every new-path write,
        # repair, and DocumentCreated REPLAY. The legacy branch is unchanged
        # (flag absent → compute as today, SC4).
        embedding = None
        if not ev.get("suppress_embedding"):
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
        # #133: needs_extraction — explicit signal for --upgrade-all discovery.
        # coalesce-null sentinel: None default so partial updates preserve.
        nx = ev.get("needs_extraction")
        # _searchText computed only when the event carries meaningful text
        has_text = bool(ev.get("title") or summary or topics)
        st = (_build_search_text(ev.get("title", ""), summary, topics)
              if has_text else None)
        # #167: sourcePath — coalesce-null sentinel (no "" default) so
        # partial updates preserve existing value
        sp = ev.get("source_path")
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
            "    d.needs_extraction=coalesce($nx, d.needs_extraction, false), "
            "    d.sourcePath=coalesce($sp, d.sourcePath), "
            "    d._searchText=coalesce($st, d._searchText, d.title), "
            "    d.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE d.embedding END, "
            "    d.updatedAt=$now",
            params={"id": did, "title": ev.get("title", did),
                    "dk": ev.get("document_kind", ""),
                    "fmt": ev.get("format", "markdown"),
                    "content": ev.get("content"),
                    "topics": topics, "summary": summary, "sid": sid,
                    "eid": eid, "ds": ds, "nx": nx, "st": st, "sp": sp,
                    "embedding": embedding,
                    "now": _now_iso()},
        )
        # #228: persist arbitrary caller-supplied props (before edge wiring
        # so that extra props land on the Document node regardless of edge success)
        self._persist_extra_props(
            "MATCH (n:Document {id: $id})", {"id": did},
            ev, self._DOCUMENT_HANDLED,
        )
        # #205 — wire references edge (Source → Document) for provenance chain.
        # Epic #900 T3 (§4.1 route pin): under OQ-6 doc ids are `doc_<rel-path>`
        # ≠ the corpus:// Source url, so the hard-coded did==did auto-wire would
        # MERGE a PHANTOM Source (url=doc_<rel>, empty contentHash). The
        # optional `source_url` ev-key override (default falls back to did —
        # legacy ingest flow byte-identical) routes the #205 link onto the real
        # Source the indexer created first. The override rides the journaled
        # DocumentCreated event, so replay re-creates the edge onto the real
        # Source (S13/T12 split: doc-unit references edges SURVIVE rebuild).
        self.link_source_to_entity(ev.get("source_url") or did, did, "Document")
        # #125 — aboutSubject edges when about_entities present (Task 1
        # self-contained: label-agnostic generalization lives in edges.py)
        about = ev.get("about_entities") or []
        if about:
            for ent in about:
                self._create_about_edges(did, ent)

    def _upsert_event(self, event: dict, *, guard: bool = False,
                      guard_source_file: str | None = None) -> "tuple[str, bool] | None":
        """MERGE Event node with all ONTOLOGY §3.1 properties.

        Handles both nested ({type:EventRecorded, event:{eventId:...}}) and
        flat (eventId at top level) formats transparently.

        Auto-creates structural edges:
          - (Subject)-[:performs]->(Event) from event.subject
          - (Event)-[:produces]->(Object) from event.object
          - (Event)-[:uses]->(Object) from event.uses (list or single)
          - (Subject)-[:participatesIn]->(Event) from event.participants,
            falling back to event.subject when no explicit participants (#212)

        Epic #900 T3 extension (§4.2 meeting collision rule, cycle-12/13/
        17/18): when ``guard=True`` (the NEW meeting branch's call), the
        eventId write implements the dialect-verified THREE-STATEMENT
        construction — (1) MERGE candidate ON CREATE SET (creates if absent,
        no-op if present); (2) guarded classification ``MATCH ... WHERE
        e.eventKind='meeting' AND e.source_file = $sf`` — HIT = the eventId
        is OURS → in-place update; MISS = taken by a DIFFERENT-source
        meeting → GUARD-REJECTED → (3) suffix follow-up MERGE on
        ``<candidate>-<sha256(source_file)[:8]>`` (per-file-deterministic ⇒
        concurrent writers converge; [:12]/[:16] capped escalation on a
        suffixed-id collision — never a silent clobber). Returns
        ``(resolved_event_id, guard_rejected)``; the caller's wiring, journal
        emission and counter attribution bind to the RESOLVED id.

        Unparameterized calls (legacy ingest_corpus, rebuild replay) execute
        the plain MERGE path byte-identically (SC4) and return None.
        """
        inner = event.get("event", event)  # unwrap nested format
        eid = inner.get("id") or inner.get("eventId")
        if not eid:
            return
        # Embedding: the journaled EventRecorded payload carries the live
        # value (epic #900 cycle-18/19 — the sanctioned replay carrier for the
        # session heal); consume inner["embedding"] when present. Otherwise
        # compute from event content (legacy path, #7845) — EXCEPT meetings
        # (cycle-16: the meeting branch suppresses the unconditional
        # computation — no subject/object ⇒ a junk vector + an undeclared
        # network call on every meeting write/repair/replay; e.embedding stays
        # NULL).
        embedding = inner.get("embedding")
        if embedding is None and inner.get("eventKind") != "meeting":
            # Stored vecf32 (#244): vec.euclideanDistance rejects plain-list
            # vectors — a single List-typed embedding poisons brute-force vector
            # search for the whole Event label. Align with the Point pattern.
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
        if not guard:
            # ── PLAIN path — legacy/replay calls, byte-identical (SC4) ──
            self._event_plain_merge(eid, props, embedding, inner)
            # #388: connector Source materialization at the choke point (all
            # connector events flow here via proj.apply; the guarded meeting
            # path never carries connector metadata, so the gate is plain-path
            # scoped). Fire only on a registered connector sourceKind or an
            # explicit sourceUrl — never on bare `source`.
            self._materialize_connector_source(inner, eid)
            return None
        # ── MEETING GUARD path (cycle-12/13/17/18; the ONLY guard consumer) ──
        return self._event_guarded_merge(inner, eid, props, guard_source_file)

    def _event_plain_merge(self, eid: str, props: dict, embedding,
                           inner: dict) -> None:
        """The legacy single-statement Event MERGE (+ edges + extra props)."""
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) "
            "ON CREATE SET e += $props, e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) END "
            "ON MATCH SET e += $props, e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
            params={"eid": eid, "props": props, "embedding": embedding},
        )
        # ── Auto-create structural edges (#122) ──
        # Subject -[:performs]-> Event
        subj = inner.get("subject", "")
        if subj:
            from tortoise.ids import ulid
            stub_id = ulid()
            self.g.query(
                "MERGE (s:Subject {name:$name}) "
                "ON CREATE SET s.id=$id, s.subjectKind='other'",
                params={"name": subj, "id": stub_id},
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
                # #329: the minted Document id is tenant-influenced (event
                # props passthrough) — validate it so it can never be a host
                # path (the read side also fails closed via resolve_under_base).
                from tortoise.security import validate_document_id
                validate_document_id(str(obj))
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
                from tortoise.ids import ulid
                stub_id = ulid()
                self.g.query(
                    "MERGE (o:Object {name:$name}) "
                    "ON CREATE SET o.id=$id, o.objectKind='other'",
                    params={"name": obj, "id": stub_id},
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
                    from tortoise.ids import ulid
                    stub_id = ulid()
                    self.g.query(
                        "MERGE (o:Object {name:$name}) "
                        "ON CREATE SET o.id=$id, o.objectKind=$kind "
                        "ON MATCH SET o.objectKind=$kind",
                        params={"name": use_name, "kind": use_kind, "id": stub_id},
                    )
                    self.g.query(
                        "MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "
                        "MERGE (e)-[:uses]->(o)",
                        params={"name": use_name, "eid": eid},
                    )
        # ── Auto-create participatesIn edges (#212) ──
        # (Subject)-[:participatesIn]->(Event) for each participant id,
        # falling back to the event.subject field when no explicit participants.
        participants = inner.get("participants") or []
        if isinstance(participants, str):
            participants = [participants]
        if participants:
            for pid in participants:
                self.g.query(
                    "MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) "
                    "MERGE (s)-[:participatesIn]->(e)",
                    params={"sid": pid, "eid": eid},
                )
        elif subj:
            # Fallback: performer is an implicit participant
            self.g.query(
                "MATCH (s:Subject {name: $name}), (e:Event {eventId: $eid}) "
                "MERGE (s)-[:participatesIn]->(e)",
                params={"name": subj, "eid": eid},
            )

        # #228: persist arbitrary caller-supplied props (iterate inner dict
        # so nested {event:{...}} and flat formats both work)
        self._persist_extra_props(
            "MATCH (n:Event {eventId: $eid})", {"eid": eid},
            inner, self._EVENT_HANDLED,
        )

    def _materialize_connector_source(self, inner: dict, eid: str) -> None:
        """#388: materialize a Source node from connector event metadata.

        The single choke point through which 100% of connector events flow
        (github/linear/slack poll + webhook + entity paths all converge on
        ``proj.apply`` → ``_upsert_event``). Connectors already emit
        ``source``/``sourceKind`` (and now ``sourceUrl``) on every EventRecorded;
        this materializes ``(Source)-[:references]->(Event)`` so the
        ``(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)``
        provenance chain (ONTOLOGY §3.4) resolves for connector entities.

        Gate: fires ONLY on a registered connector ``sourceKind`` or an
        explicit ``sourceUrl`` field — never on bare ``source``. The second
        choke-point producer ``mining.py`` emits EventRecorded with ``source``
        but NO sourceKind/sourceUrl (mining.py:417-440) and must stay excluded
        (spurious non-URL Source nodes otherwise).

        Merge semantics (idempotent, no churn on re-poll):
          - ``sourceKind`` is set ONLY on CREATE — a pre-existing Source's kind
            is authoritative (#398 never-overwrite contract; ON MATCH uses
            coalesce so an existing kind is never re-stamped, and a stub
            without kind still gets one);
          - no version bump on re-materialization (unlike ``_upsert_source``,
            which bumps on hash-diff — TypeGraph churn pitfall).

        ``(Source)-[:references]->(Object {id})`` is wired ONLY when the event
        carries an explicit ``sourceObjectId`` (set exclusively by the github
        entity path). ``event.object`` is NEVER used as an Object key — on
        poll/webhook paths it is the entity TITLE string and
        ``_event_plain_merge`` already stubs ``Object {name: title}`` with a
        random ulid; using it would wire references to the wrong stub.
        """
        sk = inner.get("sourceKind")
        source_url = inner.get("sourceUrl")
        if sk not in _CONNECTOR_SOURCE_KINDS and not source_url:
            return
        url = source_url or inner.get("source", "")
        if not url:
            return
        # #388 conf-60 direction guard: NEVER let a fallback key displace a
        # real URL. chat_getPermalink returns None on ANY exception (rate
        # limits, transient outages), so a failed permalink poll emits the
        # container-level fallback (`slack:{channel}`) for an event that
        # already carries a real-permalink Source. Without this guard the
        # sweep below would DELETE the real-URL Source (and its accumulated
        # credibilityTier / sourcePath / title) + references edge — churning
        # provenance, and the next successful poll re-creating it →
        # oscillation. When the incoming url is a fallback key and the event
        # already has a real-URL Source, skip materialization entirely (the
        # fallback adds nothing; the real URL stays authoritative).
        if not _is_real_source_url(url):
            existing = self.g.query(
                "MATCH (s:Source)-[:references]->(e:Event {eventId: $eid}) "
                "RETURN s.url",
                params={"eid": eid},
            ).result_set
            if any(_is_real_source_url(row[0]) for row in existing):
                return
        self.g.query(
            "MERGE (s:Source {url: $url}) "
            "ON CREATE SET s.sourceKind = $sk, s.title = $url, "
            "    s.contentHash = '', s.ingestedAt = $now "
            "ON MATCH SET s.sourceKind = coalesce(s.sourceKind, $sk)",
            params={"url": url, "sk": sk or "document", "now": _now_iso()},
        )
        # (Source)-[:references]->(Event) — always, when the event exists.
        self.g.query(
            "MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) "
            "MERGE (s)-[:references]->(e)",
            params={"url": url, "eid": eid},
        )
        # #388 conf-62/conf-60: a fallback-key materialization (`slack:{channel}` /
        # `linear:{team_key}` / bare `source`) can predate the real URL (a
        # permalink becomes available later, or a later poll resolves the
        # container key). The new materialization wires a SECOND Source to the
        # same event → duplicated provenance entries. Clean up: drop every
        # `(Source)-[:references]->(Event)` edge whose url differs from the
        # now-authoritative url, and delete the Source node outright if that
        # edge was its ONLY relationship (deg=1 → orphaned). Shared Sources
        # (still referencing other events / extractedFrom by Points) keep the
        # node — only the superseded edge goes. EP-neutral: connector kinds
        # are neutral on both sides of the swap. Direction-guarded by conf-60
        # above: this sweep runs only when the incoming url is a real URL (or
        # no real-URL Source references the event), so a fallback key can
        # never supersede a real permalink Source.
        self.g.query(
            "MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) "
            "WHERE old.url <> $url "
            "WITH old, r, size([(old)-[x]-(y) | x]) AS deg "
            "DELETE r "
            "WITH old, deg "
            "WHERE deg = 1 "
            "DELETE old",
            params={"url": url, "eid": eid},
        )
        # (Source)-[:references]->(Object {id}) — only on explicit
        # sourceObjectId (github entity path; event.object is never an Object
        # key — see docstring).
        obj_id = inner.get("sourceObjectId")
        if obj_id:
            self.g.query(
                "MATCH (s:Source {url: $url}), (o:Object {id: $oid}) "
                "MERGE (s)-[:references]->(o)",
                params={"url": url, "oid": obj_id},
            )

    def _event_guarded_merge(self, inner: dict, candidate: str, props: dict,
                             sf: str | None) -> "tuple[str, bool]":
        """The meeting-scoped source_file-aware guard (§4.2 cycle-12/13/17/18).

        THREE-statement construction (dialect-supported — no ``ON MATCH
        WHERE`` in the FalkorDB dialect): (1) MERGE candidate ON CREATE SET;
        (2) guarded classification WHERE eventKind='meeting' AND
        source_file=$sf — HIT ⇒ in-place update, MISS ⇒ guard-rejected;
        (3) suffix follow-up MERGE on the deterministic suffixed id
        (``sha256(source_file)[:8]``) with the same classification guard and a
        capped [:12]/[:16] escalation on suffixed-id collisions (E2E-12 grind).

        Statement 1 carries the FULL prop set — including ``source_file``, the
        guard's comparison property (cycle-18 threading pin): a concurrent
        same-file writer must HIT its own classification (source_file present)
        instead of suffix-forking on a mid-write window.
        """
        import hashlib
        # FULL prop set: the fixed keys + the extras (title/topics/
        # content_metadata/file_hash/source_file/eventStatus/…) — the same
        # surface the plain path persists via _persist_extra_props.
        full = dict(props)
        skip = self._META_KEYS | self._EVENT_HANDLED
        for k, v in inner.items():
            if k not in skip and v is not None and k not in full:
                full[k] = v
        # (1) MERGE candidate — creates if absent, no-op if present (ON CREATE
        # SET is the supported directive; the props NEVER land on a colliding
        # node).
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props",
            params={"eid": candidate, "props": full},
        )
        # (2) guarded classification — is the candidate OURS?
        hit = self.g.query(
            "MATCH (e:Event {eventId: $eid}) "
            "WHERE e.eventKind = 'meeting' AND e.source_file = $sf "
            "RETURN e.id",
            params={"eid": candidate, "sf": sf},
        ).result_set
        if hit:
            self.g.query(
                "MATCH (e:Event {eventId: $eid}) SET e += $props",
                params={"eid": candidate, "props": full},
            )
            return (candidate, False)
        # (3) GUARD-REJECTED → suffix follow-up (per-file-deterministic ⇒
        # concurrent writers converge on the same id; first creates, second
        # no-ops).
        for width in (8, 12, 16):
            suffixed = f"{candidate}-{hashlib.sha256((sf or '').encode('utf-8')).hexdigest()[:width]}"
            held = self.g.query(
                "MATCH (e:Event {eventId: $eid}) RETURN e.source_file",
                params={"eid": suffixed},
            ).result_set
            if held and held[0][0] != sf:
                continue  # suffixed id taken by a DIFFERENT source — escalate
            hit2 = self.g.query(
                "MATCH (e:Event {eventId: $eid}) "
                "WHERE e.eventKind = 'meeting' AND e.source_file = $sf "
                "RETURN e.id",
                params={"eid": suffixed, "sf": sf},
            ).result_set
            if hit2:
                self.g.query(
                    "MATCH (e:Event {eventId: $eid}) SET e += $props",
                    params={"eid": suffixed, "props": full},
                )
            else:
                self.g.query(
                    "MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props",
                    params={"eid": suffixed, "props": full},
                )
            # extra-props tail re-targets to the RESOLVED id (never the
            # candidate when it exists as the colliding file's Event).
            self._persist_extra_props(
                "MATCH (n:Event {eventId: $eid})", {"eid": suffixed},
                inner, self._EVENT_HANDLED,
            )
            return (suffixed, True)
        # [:16] also taken — never a silent clobber; return the candidate with
        # the rejected flag so the caller records an errors[] warning (the
        # MERGE above is a no-op and no props were written anywhere).
        return (candidate, True)

    def _upsert_source(self, ev: dict, *, merge_run_id: str | None = None) -> "QueryResult | None":
        """MERGE Source node for layered provenance (Ontology v2.1).

        Source properties: url (permalink), sourceType, contentHash, title,
        ingestedAt, version, externalId. Creates stub if missing.

        Epic #900 T3 extensions (all inside the single statement — pin d,
        write-path containment):
          - the ON MATCH version bump is CONDITIONAL on a stored-hash
            difference (§5.1 pin b — ON MATCH bumps version/updatedAt/
            contentHash/title/_searchText ONLY when ``s.contentHash`` differs
            from the incoming ``$hash``; a stub Source with NULL contentHash
            is completed — the JOINT-E2E sweep's stub-handling);
          - ``s.sourcePath = coalesce($sp, s.sourcePath)`` (§4.1 — the
            sanctioned source_path route maps to camelCase on the node);
          - ``s._searchText`` — coalesce ON CREATE, OVERWRITE on hash-diff
            MERGE (§4.1 cycle-4 merge semantics; E2E-5 retitle refresh);
          - ``s.__runId = $rid`` on the ON CREATE branch ONLY when
            ``merge_run_id`` is given — the creator's per-run token. The
            embedded FalkorDBLite reports ``Nodes created: 1`` for BOTH of two
            concurrent same-key MERGEs (server-side parallel-executor quirk),
            so ``nodes_created`` is NOT a race-safe creator discriminator on
            the embedded backend: the index path detects the CREATE by
            re-reading ``__runId`` (== its own token ⇒ IT created) and removes
            it immediately. Non-index callers (legacy create_source paths)
            never pass a run id → the clause is omitted entirely (no prop).

        Returns the QueryResult (the caller uses ``nodes_created`` for the
        counter-authority outcome); ``proj.apply`` threads it for
        ``create_source``'s index-path consumers.
        """
        sid = ev.get("id")
        url = ev.get("url", "")
        if not sid and not url:
            return None
        key = url or sid
        search_text = ev.get("_searchText") or ev.get("title")
        run_clause = ", s.__runId = $rid" if merge_run_id is not None else ""
        r = self.g.query(
            "MERGE (s:Source {url: $url}) "
            "ON CREATE SET s.id = coalesce($id, $url), "
            "              s.sourceKind = $sk, "
            "              s.contentHash = $hash, "
            "              s.title = $title, "
            "              s.ingestedAt = $now, "
            "              s.version = 1, "
            "              s.externalId = $ext, "
            "              s.sourcePath = coalesce($sp, s.sourcePath), "
            "              s._searchText = $st" + run_clause + " "
            "ON MATCH SET s.contentHash = CASE WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                          THEN $hash ELSE s.contentHash END, "
            "           s.title = CASE WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                   THEN coalesce($title, s.title) ELSE s.title END, "
            "           s.version = CASE WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                    THEN s.version + 1 ELSE s.version END, "
            "           s.updatedAt = CASE WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                     THEN $now ELSE s.updatedAt END, "
            "           s.sourcePath = coalesce($sp, s.sourcePath), "
            "           s._searchText = CASE WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                        THEN $st ELSE s._searchText END",
            params={
                "url": key, "id": sid or key,
                "sk": ev.get("sourceKind", "document"),
                "hash": ev.get("contentHash", ""),
                "title": ev.get("title", key),
                "now": _now_iso(),
                "ext": ev.get("externalId", ""),
                "sp": ev.get("source_path"),
                "st": search_text,
                **({"rid": merge_run_id} if merge_run_id is not None else {}),
            },
        )
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Source {url: $url})", {"url": key},
            ev, self._SOURCE_HANDLED,
        )
        return r
