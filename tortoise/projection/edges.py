"""Edge creation and linking methods for FalkorProjection."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


# Canonical structural-edge predicate vocabulary (ONTOLOGY §3.2/§3.3 + #391).
# Hoisted from create_edge so SDK surfaces (e.g. TortoiseSDK.ingest, epic #888
# W4) can validate relation names WITHOUT string-duplicating the set.
_VALID_EDGE_PREDICATES = frozenset({
    'performs', 'produces', 'uses', 'authoredBy', 'ownedBy', 'managedBy',
    'hasMember', 'holdsRole', 'memberOf', 'reportsTo',
    'participatesIn', 'hasPart', 'related', 'dependsOn', 'references',
    'wasDerivedFrom',
    # #391: about* edges (ONTOLOGY §3.2/§3.3) were only creatable via
    # create_about_edge — the generic create_edge set missed them.
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutDocument',
    'aboutSource', 'aboutAction',
})


class _EdgeHandlers:
    """Mixin: edge creation, about edges, source linking, edge stats."""

    def _create_edges(self, p: dict) -> None:
        """Create typed edges for an operator Point. Auto-creates stub nodes
        for missing source Points referenced by short IDs (#6713)."""
        op = p.get("operator")
        if not isinstance(op, dict):
            # #331 (review r4): a truthy non-dict operator value must
            # degrade to no edges, not AttributeError in op.get().
            return
        # #331 (review r3): .get() — a malformed operator dict without
        # op_type/inputs must degrade to no typed edges, not KeyError.
        rel_type = {"NAND": "NAND", "IMPL": "IMPL",
                     "composedOf": "hasPart", "decomposesInto": "hasPart",
                     "contains": "hasPart", "wraps": "hasPart"}.get(op.get("op_type"))
        import logging as _logging
        _log = _logging.getLogger(__name__)
        for idx, src in enumerate(op.get("inputs") or []):
            # #331 (review r4): non-string inputs members are malformed —
            # skip (len()/Cypher param would raise).
            if not isinstance(src, str):
                continue
            # ponytail: auto-create stub if source Point doesn't exist.
            # Short numeric IDs are orphan refs from cross-file wiring scripts.
            if len(src) < 20:  # short IDs (non-ULID) are suspect
                exists = self.g.query(
                    "MATCH (s:Point {id:$sid}) RETURN count(s) > 0",
                    params={"sid": src}
                ).result_set[0][0]
                if not exists:
                    # #329: bounded stub auto-creation — at the per-instance cap
                    # we STOP creating stubs and SKIP the edge to the missing
                    # node (fail-safe: no partial edge, no crash, warning logged).
                    if getattr(self, "_autocreated_stubs", 0) >= getattr(
                            self, "_max_autocreated_stubs", 500):
                        _log.warning(
                            "stub auto-creation cap reached (%d) — skipping "
                            "missing source %r (edge not created)",
                            getattr(self, "_max_autocreated_stubs", 500), src,
                        )
                        continue
                    self.g.query(
                        "CREATE (s:Point {id:$sid}) "
                        "SET s.content='[missing]', "
                        "    s.is_operator=false",
                        params={"sid": src}
                    )
                    self._autocreated_stubs = getattr(self, "_autocreated_stubs", 0) + 1
            if rel_type is not None:
                # Known op_type → typed edge + reverse INPUT
                self.g.query(
                    f"MATCH (o:Point {{id:$oid}}) "
                    f"MATCH (s:Point {{id:$sid}}) "
                    f"MERGE (o)-[:{rel_type} {{idx:$idx}}]->(s) "
                    f"MERGE (s)-[:INPUT {{idx:$idx}}]->(o)",
                    params={"oid": p["id"], "sid": src, "idx": idx},
                )
            else:
                # Unknown op_type → INPUT edge only
                self.g.query(
                    "MATCH (o:Point {id:$oid}) "
                    "MATCH (s:Point {id:$sid}) "
                    "MERGE (o)-[:INPUT {idx:$idx}]->(s) "
                    "MERGE (s)-[:INPUT {idx:$idx}]->(o)",
                    params={"oid": p["id"], "sid": src, "idx": idx},
                )

    def _create_about_edges(self, source_id: str, entity_name: str) -> None:
        """Link entity (Point, Document, Event, or Object) to a named entity.

        Auto-detects entity type from the legacy flat aboutEntities list.
        Tries Subject → Object → Action → Event → Document, creates stub if none found.
        ONTOLOGY v2.5 §2.2: Point/Doc/Event → Subject/Object/Action; Point/Doc → Event; Event → Point/Document.
        #125: source MATCH is label-agnostic so Document/Event sources work too.
        """
        # Try Subject
        if self._try_about_edge(source_id, entity_name, 'Subject', 'aboutSubject', 'subjectKind', 'other'):
            return
        # Try Object
        if self._try_about_edge(source_id, entity_name, 'Object', 'aboutObject', 'objectKind', 'other'):
            return
        # Try Event
        if self._try_about_edge(source_id, entity_name, 'Event', 'aboutEvent', 'eventKind', 'other'):
            return
        # Try Document
        if self._try_about_edge(source_id, entity_name, 'Document', 'aboutDocument', 'documentKind', 'other'):
            return
        # Try Point (for Event→Point reverse direction)
        if self._try_about_edge(source_id, entity_name, 'Point', 'aboutPoint', 'pointKind', 'statement'):
            return
        # Neither exists — default to Subject stub (label-agnostic source)
        self.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$name, s.subjectKind='other'",
            params={"name": entity_name},
        )
        srcs = self._resolve_entity(source_id, by_id=True)
        for n in srcs:
            self.g.query(
                f"MATCH (n:{n['label']} {{{n['key']}:$pid}}), (s:Subject {{name:$name}}) "
                f"MERGE (n)-[:aboutSubject]->(s)",
                params={"pid": n["value"], "name": entity_name},
            )

    def _try_about_edge(self, source_id: str, target_name: str, 
                        label: str, edge_type: str, kind_field: str, kind_default: str) -> bool:
        """Try to create an about* edge to a named entity. Returns True if found.

        For Document nodes, matches against both ``name`` and ``title`` properties
        (Documents store their display name in ``title`` per _upsert_document).
        """
        # Documents use 'title' as their display name (#211)
        if label == 'Document':
            r = self.g.query(
                f"MATCH (e:{label}) WHERE e.name = $name OR e.title = $name "
                "RETURN coalesce(e.title, e.name, $name) LIMIT 1",
                params={"name": target_name},
            ).result_set
        else:
            r = self.g.query(
                f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",
                params={"name": target_name},
            ).result_set
        if r:
            for n in self._resolve_entity(source_id, by_id=True):
                if label == 'Document':
                    self.g.query(
                        f"MATCH (n:{n['label']} {{{n['key']}:$sid}}), (e:{label}) "
                        f"WHERE e.name = $name OR e.title = $name "
                        f"MERGE (n)-[:{edge_type}]->(e)",
                        params={"sid": n["value"], "name": target_name},
                    )
                else:
                    self.g.query(
                        f"MATCH (n:{n['label']} {{{n['key']}:$sid}}), (e:{label} {{name:$name}}) "
                        f"MERGE (n)-[:{edge_type}]->(e)",
                        params={"sid": n["value"], "name": target_name},
                    )
            return True
        return False

    def create_about_edge(self, source_id: str, target_id: str, edge_type: str) -> bool:
        """Create a specific about* edge by source/target IDs. Validates edge type.
        
        Args:
            source_id: ID of source node (Point, Document, or Event)
            target_id: ID of target node
            edge_type: one of aboutSubject, aboutObject, aboutEvent, aboutPoint, aboutDocument
        
        Returns True if edge was created.
        """
        valid = {'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint', 'aboutDocument'}
        if edge_type not in valid:
            raise ValueError(f"Invalid about edge type: {edge_type}. Must be one of {valid}")
        
        # Resolve endpoints via index-backed labeled lookups (issue #327).
        # Source predicate is id-only; target is id OR eventId (legacy OR-set).
        sources = self._resolve_entity(source_id, by_id=True)
        targets = self._resolve_entity(target_id, by_id=True, by_eventId=True)
        if not sources or not targets:
            return False
        created = False
        for s in sources:
            for t in targets:
                r = self.g.query(
                    f"MATCH (s:{s['label']} {{{s['key']}:$sv}}) "
                    f"MATCH (t:{t['label']} {{{t['key']}:$tv}}) "
                    f"MERGE (s)-[:{edge_type}]->(t) "
                    f"RETURN count(*) > 0",
                    params={"sv": s["value"], "tv": t["value"]},
                )
                if r.result_set and r.result_set[0][0]:
                    created = True
        return created

    def _link_source(self, point_id: str, source_ref: str, source_kind: str = "document", *, label: str = "Point") -> None:
        """Link entity → Source via extractedFrom edge (Ontology v3.3).

        Creates stub Source if missing, keyed on url. sourceKind defaults to 'document'
        but connectors pass specific values (github_issue, slack_message, linear_card, etc.).
        ``label`` selects the source-side entity label — Point (default) or Document
        (create_document provenance, #394).

        Session-provenance refs (`session:<id>`, written by the capture
        extractors) stamp `is_episodic=true` ON CREATE — the backfill's
        condition 4 (graph-scripts/backfill_is_episodic.py) treats
        Session-linked Sources as episodic; a new capture creating a
        flag-less Source would otherwise keep matching the one-time backfill
        (issue #1486). Non-session Sources (documents, connectors) are
        untouched.
        """
        params = {"url": source_ref, "sk": source_kind, "now": _now_iso()}
        ep_clause = ""
        if str(source_ref).startswith("session:"):
            params["ep"] = True
            ep_clause = ", s.is_episodic=$ep"
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
            f"    s.contentHash='', s.ingestedAt=$now{ep_clause}",
            params=params,
        )
        self.g.query(
            f"MATCH (n:{label} {{id:$pid}}), (s:Source {{url:$url}}) "
            "MERGE (n)-[:extractedFrom]->(s)",
            params={"pid": point_id, "url": source_ref},
        )

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Document|Event|Object) for the MATCH
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Raises:
            ValueError: if entity_label is not one of Document, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        valid = {"Document", "Event", "Object"}
        if entity_label not in valid:
            raise ValueError(
                f"Invalid entity_label: {entity_label}. Must be one of {valid} "
                f"(Action was dissolved in Ontology v3.0)."
            )
        # MERGE Source with auto-create (mirrors _link_source) — #205
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
            "    s.contentHash='', s.ingestedAt=$now",
            params={"url": source_url, "sk": source_kind, "now": _now_iso()},
        )
        self.g.query(
            f"MATCH (s:Source {{url:$url}}), (e:{entity_label} {{id:$eid}}) "
            f"MERGE (s)-[:references]->(e)",
            params={"url": source_url, "eid": entity_id},
        )

    # ponytail: SDK compat alias (Phase 1b will rename caller)
    _link_extracted_from = _link_source

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

    def create_edge(self, source_id: str, target_id: str, predicate: str) -> bool:
        """Create a named edge between two entities by their IDs.
        Matches target by id OR eventId (Event nodes use eventId as key)."""
        valid_predicates = _VALID_EDGE_PREDICATES
        if predicate not in valid_predicates:
            raise ValueError(f"Unknown predicate: {predicate}")
        # Resolve endpoints via index-backed labeled lookups (issue #327).
        # Source OR-set: id | eventId | url ; target OR-set: id | eventId —
        # matching the legacy predicates exactly (a url-only stub Source is
        # therefore NOT a valid target, preserving prior behavior).
        sources = self._resolve_entity(source_id, by_id=True, by_eventId=True, by_url=True)
        targets = self._resolve_entity(target_id, by_id=True, by_eventId=True)
        if not sources or not targets:
            return False
        # #390: mirror create_owned_by's circular-DAG guard for ownedBy — the
        # generic create_edge path must not bypass it. The new edge is
        # source -[:ownedBy]-> target; a cycle would close iff target already
        # (transitively) owns source. Same varlen 1..10 traversal + all
        # resolved (target, source) pairs as create_owned_by.
        if predicate == 'ownedBy':
            for t in targets:
                for s in sources:
                    cycle = self.g.query(
                        f"MATCH (t:{t['label']} {{{t['key']}:$tid}}) "
                        f"MATCH (s:{s['label']} {{{s['key']}:$sid}}) "
                        f"MATCH path = (t)-[:ownedBy*1..10]->(s) RETURN count(path) > 0",
                        params={"tid": t["value"], "sid": s["value"]},
                    )
                    if cycle.result_set and cycle.result_set[0][0]:
                        raise ValueError(
                            f"Circular ownership: {target_id} already owned by {source_id}")
        created = False
        for s in sources:
            for t in targets:
                r = self.g.query(
                    f"MATCH (s:{s['label']} {{{s['key']}:$sv}}) "
                    f"MATCH (t:{t['label']} {{{t['key']}:$tv}}) "
                    f"MERGE (s)-[:{predicate}]->(t) RETURN count(*) > 0",
                    params={"sv": s["value"], "tv": t["value"]},
                )
                if r.result_set and r.result_set[0][0]:
                    created = True
        return created

    def create_owned_by(self, entity_id: str, subject_id: str) -> bool:
        """Create ownedBy edge with circular ownership DAG check."""
        # Resolve endpoints by id (index-backed, issue #327); the varlen
        # traversal itself has no index in FalkorDB and is accepted.
        # All resolved (s, t) pairs are checked (original cartesian semantics).
        sources = self._resolve_entity(subject_id, by_id=True)
        targets = self._resolve_entity(entity_id, by_id=True)
        found_cycle = False
        for s in sources:
            for t in targets:
                cycle = self.g.query(
                    f"MATCH (s:{s['label']} {{{s['key']}:$sid}}) "
                    f"MATCH (t:{t['label']} {{{t['key']}:$tid}}) "
                    f"MATCH path = (s)-[:ownedBy*1..10]->(t) RETURN count(path) > 0",
                    params={"sid": s["value"], "tid": t["value"]},
                )
                if cycle.result_set and cycle.result_set[0][0]:
                    found_cycle = True
                    break
            if found_cycle:
                break
        if found_cycle:
            raise ValueError(f"Circular ownership: {subject_id} already owned by {entity_id}")
        return self.create_edge(entity_id, subject_id, 'ownedBy')

    def create_managed_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'managedBy')

    def create_authored_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'authoredBy')
