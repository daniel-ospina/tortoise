"""Edge creation and linking methods for FalkorProjection."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _EdgeHandlers:
    """Mixin: edge creation, about edges, source linking, edge stats."""

    def _create_edges(self, p: dict) -> None:
        """Create typed edges for an operator Point. Auto-creates stub nodes
        for missing source Points referenced by short IDs (#6713)."""
        op = p["operator"]
        rel_type = {"NAND": "NAND", "IMPL": "IMPL",
                     "composedOf": "hasPart", "decomposesInto": "hasPart",
                     "contains": "hasPart", "wraps": "hasPart"}.get(op["op_type"])
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
                        "SET s.content='[missing]', "
                        "    s.is_operator=false",
                        params={"sid": src}
                    )
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
        self.g.query(
            "MATCH (n {id:$pid}), (s:Subject {name:$name}) "
            "MERGE (n)-[:aboutSubject]->(s)",
            params={"pid": source_id, "name": entity_name},
        )

    def _try_about_edge(self, source_id: str, target_name: str, 
                        label: str, edge_type: str, kind_field: str, kind_default: str) -> bool:
        """Try to create an about* edge to a named entity. Returns True if found."""
        r = self.g.query(
            f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",
            params={"name": target_name},
        ).result_set
        if r:
            self.g.query(
                f"MATCH (n {{id:$sid}}), (e:{label} {{name:$name}}) "
                f"MERGE (n)-[:{edge_type}]->(e)",
                params={"sid": source_id, "name": target_name},
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
        
        # Match target by id OR eventId (Event nodes use eventId as key)
        r = self.g.query(
            f"MATCH (s {{id:$sid}}) "
            f"MATCH (t) WHERE t.id = $tid OR t.eventId = $tid "
            f"MERGE (s)-[:{edge_type}]->(t) "
            f"RETURN count(*) > 0",
            params={"sid": source_id, "tid": target_id},
        )
        return bool(r.result_set[0][0]) if r.result_set else False

    def _link_source(self, point_id: str, source_ref: str, source_kind: str = "document") -> None:
        """Link Point → Source via extractedFrom edge (Ontology v2.5).

        Creates stub Source if missing, keyed on url. sourceKind defaults to 'document'
        but connectors pass specific values (github_issue, slack_message, linear_card, etc.).
        """
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
            "    s.contentHash='', s.ingestedAt=$now",
            params={"url": source_ref, "sk": source_kind, "now": _now_iso()},
        )
        self.g.query(
            "MATCH (n:Point {id:$pid}), (s:Source {url:$url}) "
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
        valid_predicates = {
            'performs', 'produces', 'uses', 'authoredBy', 'ownedBy', 'managedBy',
            'partOf', 'hasMember', 'holdsRole', 'reportsTo',
            'instantiates', 'participatesIn', 'hasPart', 'related', 'dependsOn', 'references',
            'wasDerivedFrom'
        }
        if predicate not in valid_predicates:
            raise ValueError(f"Unknown predicate: {predicate}")
        r = self.g.query(
            f"MATCH (s) WHERE s.id = $sid OR s.eventId = $sid OR s.url = $sid "
            f"MATCH (t) WHERE t.id = $tid OR t.eventId = $tid "
            f"MERGE (s)-[:{predicate}]->(t) RETURN count(*) > 0",
            params={"sid": source_id, "tid": target_id},
        )
        return bool(r.result_set[0][0]) if r.result_set else False

    def create_owned_by(self, entity_id: str, subject_id: str) -> bool:
        """Create ownedBy edge with circular ownership DAG check."""
        cycle = self.g.query(
            "MATCH (s {id:$sid}) MATCH (t {id:$tid}) MATCH path = (s)-[:ownedBy*1..10]->(t) RETURN count(path) > 0",
            params={"sid": subject_id, "tid": entity_id},
        )
        if cycle.result_set and cycle.result_set[0][0]:
            raise ValueError(f"Circular ownership: {subject_id} already owned by {entity_id}")
        return self.create_edge(entity_id, subject_id, 'ownedBy')

    def create_managed_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'managedBy')

    def create_authored_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'authoredBy')
