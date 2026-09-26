"""Edge creation and linking methods for FalkorProjection."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from tortoise.source_identity import normalize_source_url, resolve_source_key

logger = logging.getLogger(__name__)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


# ── #2489 structural-edge transfer parity: shared rel→(label,key) resolver ──
# ONE home for the key map supersede_point 2b EMISSION (sdk.py), the pass-2b
# DirectEdgeRepoint structural REPLAY branch (projection/__init__.py), and the
# resurrection-DELETE leg. Two divergent copies would drift: _create_about_edges'
# auto-detect (Subject-first order) cannot mint a specific aboutObject/
# aboutEvent/aboutDocument edge for an absent node (it would attach the
# descriptor's rel to a wrong-label node), so replay resolution is LABEL-SCOPED
# and never auto-detects.

# Snapshot-derivable structural rel set — the rels supersede's 2b transfer
# journals as DirectEdgeRepoint descriptors because rebuild pass-2 re-creates
# them at the OLD point from its immutable snapshot (extractedFrom prop +
# aboutEntities list -> _upsert_point_edges / entities.py). They are the edges
# whose resurrection the replay delete-leg must clean. aboutAction
# (Action dissolved in Ontology v3.0), aboutSource, and wasDerivedFrom are NOT
# derivable — never snapshot-recreated, never resurrect at old — so they get no
# descriptor (emitting one would half-own the A10 raw-edge family by accident).
DERIVABLE_STRUCTURAL_RELS = frozenset({
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint',
    'aboutDocument', 'extractedFrom',
})

# rel -> entity label of the structural TARGET (label-scoped resolution). The
# source side of a supersede transfer is always a Point. Public — sdk.py's 2b
# emission and the projection replay both import it (no duplicate key map).
STRUCTURAL_REL_LABELS = {
    'aboutSubject': 'Subject',
    'aboutObject': 'Object',
    'aboutEvent': 'Event',
    'aboutPoint': 'Point',
    # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source, not a graph node.
    # The label and the replay key MUST move together (below) — a retargeted
    # label with the old key resolves to nothing and silently mis-points the
    # rebuilt edge. aboutDocument is NOT collapsed into aboutSource: it stays
    # in DERIVABLE_STRUCTURAL_RELS (above) and aboutSource stays out.
    'aboutDocument': 'Source',
    'extractedFrom': 'Source',
}


def stub_key(rel: str, target: dict):
    """Extract the replayable (label, key) for a structural edge's target node.

    ``target`` is the target node's property dict (the 2b SELECT returns
    ``properties(target)``). Key map: aboutSubject/aboutObject/aboutEvent/
    aboutPoint -> ``name``; aboutDocument/extractedFrom -> ``url`` (D10: a
    document is a :Source, so it resolves by ``url`` — the SAME key the label
    moved with. A title-keyed aboutDocument descriptor is no longer
    resolvable and must not be emitted). NEVER ``target.id`` — Subjects MERGE by
    name (id may be a webhook stub ulid, #1918), Sources by url. Returns None
    for a rel outside the derivable set or an unresolvable key (name-less Point
    from an id-targeted create_about_edge; a Source target lacking url) — such
    descriptors are un-replayable and the caller must SKIP emission (those
    edges die at rebuild today anyway).
    """
    label = STRUCTURAL_REL_LABELS.get(rel)
    if label is None:
        return None
    key = (target.get('url') if rel in ('aboutDocument', 'extractedFrom')
           else target.get('name'))
    if not isinstance(key, str) or not key:
        return None
    return (label, key)


def _mint_subject_stub(g, name: str) -> None:
    """MERGE the Subject stub live wiring's auto-detect fallback creates
    (edges.py _create_about_edges) — single create path for live + replay so a
    replayed descriptor mints a byte-identical stub to live wiring."""
    g.query(
        "MERGE (s:Subject {name:$name}) "
        "ON CREATE SET s.id=$name, s.subjectKind='other'",
        params={"name": name},
    )


def _mint_source_stub(g, url: str, source_kind: str | None = None) -> str:
    """MERGE the Source stub _link_source creates — single create path for live
    + replay (mirror _link_source's ON CREATE exactly: title=url, empty
    contentHash, ingestedAt now; session: refs carry is_episodic=true so the
    #1486 one-time episodic backfill does not re-match a replay-minted Source).

    Returns the resolved ``url`` key of the node the stub/registration
    addresses (S0b, #5012): a URL variant resolves to the node its canonical
    identity already names — via the SHARED ``resolve_source_key`` — so no
    write path can mint a second ``:Source``.  Callers MUST use the returned
    key for their subsequent MATCH/MERGE (the raw spelling may not be the
    node's stored ``url``).

    ``source_kind`` defaults to the ref-appropriate value. Ontology §4.6 +
    #909 §4.3 #6 register **agentSession** as the source-kind VALUE for session
    Sources (the four-node capture model's provenance bridge); everything else
    defaults to ``document``. Minting `session:` refs as ``document`` was a
    documented wart that `_materialize_session_source` had to upgrade IN PLACE
    (sdk.py) — resolving the default here means a replay-minted stub is already
    correct, and the eval ingest lane (which never materializes session sources)
    stops mislabelling every session Source as a document. Callers that know
    better still pass it explicitly.
    """
    if source_kind is None:
        source_kind = "agentSession" if str(url).startswith("session:") else "document"
    # S0b (#5012): resolve a URL variant to the node its canonical identity
    # already names, so the stub path cannot mint a second :Source either.
    key = resolve_source_key(g, url)
    canonical = normalize_source_url(key)
    params = {"url": key, "raw_url": url, "cu": canonical,
              "sk": source_kind, "now": _now_iso()}
    ep_clause = ""
    if str(key).startswith("session:"):
        params["ep"] = True
        ep_clause = ", s.is_episodic=$ep"
    g.query(
        "MERGE (s:Source {url:$url}) "
        "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
        "    s.canonicalUrl=$cu, s.urlAliases=[$raw_url], "
        f"    s.contentHash='', s.ingestedAt=$now{ep_clause} "
        "ON MATCH SET s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
        "    s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
        "        THEN s.urlAliases "
        "        ELSE coalesce(s.urlAliases, []) + [$raw_url] END",
        params=params,
    )
    return key


def resolve_structural_target(g, label: str, key: str, rel: str):
    """Resolve the label-scoped structural target node by its replay key.

    Create-if-missing semantics: Subject / Source stubs MERGE by key (the live
    wiring precedent — _create_about_edges' fallback / _link_source). NEVER
    auto-detect labels (Subject-first auto-detect would attach an aboutObject
    descriptor to a same-name Subject node) and NEVER mint Point stubs by name
    (an absent Point target => skip — absent Documents/Objects/Events return
    None too; those entities are journaled by their own creation events, and an
    absent one is an unjournaled-producer edge that dies at rebuild).

    Returns {"internal": <FalkorDB internal node id>, "logical": <node.id>}
    or None. ``g`` is the projection graph handle (``self.g`` on
    FalkorProjection / the guarded proxy). """
    if label not in STRUCTURAL_REL_LABELS.values():
        # label is interpolated into the query STRUCTURE — fail loudly on
        # anything outside the fixed constant set (parity with
        # _resolve_entity's runtime defense).
        raise RuntimeError(
            f"resolve_structural_target: unsafe label {label!r} "
            "(contract: STRUCTURAL_REL_LABELS values only)")
    if label == "Subject":
        _mint_subject_stub(g, key)
        rows = g.query(
            "MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 1",
            params={"name": key}).result_set
        if not rows:
            return None
        return {"internal": rows[0][0], "logical": rows[0][1]}
    if label == "Source":
        # D10 (ONTOLOGY §4.4): aboutDocument's target is a document :Source
        # keyed by ``url``. RESOLVE-ONLY — never mint: a miss must be logged
        # and skipped, never turned into a phantom Source that silently
        # mis-points the resurrected edge (adversarial class B1). By contrast,
        # extractedFrom keeps its create-if-missing stub (live _link_source
        # parity).
        if rel == "aboutDocument":
            # D10 B1 live-parity (adversarial): the LIVE auto-detect
            # (`_try_about_edge`) resolves an aboutDocument target only when
            # `e.documentKind IS NOT NULL`, so a session/connector/provenance
            # Source can never become an aboutDocument target. This replay
            # match MUST carry the same guard — without it a producer-created
            # edge (reachable through the public `sdk.create_edge`) to a
            # provenance Source is re-attached to that non-document Source on
            # rebuild, i.e. live and replay disagree (the #2489 "one create
            # path / byte-identical stubs" invariant). The guard is
            # DELIBERATE live-parity, not an accident: a wrong target must be
            # refused here, exactly as live refuses it.
            rows = g.query(
                "MATCH (s:Source {url:$url}) WHERE s.documentKind IS NOT NULL "
                "RETURN ID(s), s.id LIMIT 1",
                params={"url": key}).result_set
            if not rows:
                logger.warning(
                    "aboutDocument replay: no Source at url=%r — descriptor "
                    "skipped (never minted, never mis-pointed)", key)
                return None
            return {"internal": rows[0][0], "logical": rows[0][1]}
        # S0b (#5012): the stub helper returns the CANONICAL key — the raw
        # spelling may not be the node's stored ``url``, and the MATCH below
        # must address the node the stub actually merged onto.
        key = _mint_source_stub(g, key)
        rows = g.query(
            "MATCH (s:Source {url:$url}) RETURN ID(s), s.id LIMIT 1",
            params={"url": key}).result_set
        if not rows:
            return None
        return {"internal": rows[0][0], "logical": rows[0][1]}
    # Non-stubbable labels — resolve-only (never mint). Event nodes match by
    # name (set by create_event).
    q = (f"MATCH (x:{label} {{name:$key}}) "
         f"RETURN ID(x), x.id LIMIT 1")
    rows = g.query(q, params={"key": key}).result_set
    if not rows:
        return None
    return {"internal": rows[0][0], "logical": rows[0][1]}


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
        for missing source endpoints referenced by short IDs (#6713).
        Operator endpoints may be Point OR Event nodes (A1b #1272) — the
        existence checks and edge MERGEs match both (#1919 fold-parity: live
        create_operator writes the same typed + INPUT edges this replay does)."""
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
            # ponytail: auto-create stub if source endpoint doesn't exist.
            # Short numeric IDs are orphan refs from cross-file wiring scripts.
            if len(src) < 20:  # short IDs (non-ULID) are suspect
                exists = self.g.query(
                    "MATCH (s) WHERE (s:Point OR s:Event) "
                    "AND s.id = $sid RETURN count(s) > 0",
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
            else:
                # #1917: long IDs are presumed real ULIDs — never stub them.
                # A source that doesn't resolve would silently match nothing
                # in the MERGE below; warn instead of dropping the input.
                exists = self.g.query(
                    "MATCH (s) WHERE (s:Point OR s:Event) "
                    "AND s.id = $sid RETURN count(s) > 0",
                    params={"sid": src},
                ).result_set[0][0]
                if not exists:
                    _log.warning(
                        "input source %r (id length %d >= 20) does not "
                        "resolve to an existing Point or Event — INPUT edge "
                        "skipped",
                        src, len(src),
                    )
                    continue
            if rel_type is not None:
                # Known op_type → typed edge + reverse INPUT
                self.g.query(
                    f"MATCH (o:Point {{id:$oid}}), (s) "
                    f"WHERE (s:Point OR s:Event) AND s.id = $sid "
                    f"MERGE (o)-[:{rel_type} {{idx:$idx}}]->(s) "
                    f"MERGE (s)-[:INPUT {{idx:$idx}}]->(o)",
                    params={"oid": p["id"], "sid": src, "idx": idx},
                )
            else:
                # Unknown op_type → INPUT edge only (convention: source → operator)
                self.g.query(
                    "MATCH (o:Point {id:$oid}), (s) "
                    "WHERE (s:Point OR s:Event) AND s.id = $sid "
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
        # Try Source (D10: a document IS a :Source — aboutDocument's target)
        if self._try_about_edge(source_id, entity_name, 'Source', 'aboutDocument', 'documentKind', 'other'):
            return
        # Try Point (for Event→Point reverse direction)
        if self._try_about_edge(source_id, entity_name, 'Point', 'aboutPoint', 'pointKind', 'statement'):
            return
        # Neither exists — default to Subject stub (label-agnostic source).
        # #2489: stub mint routed through the SHARED resolver helper
        # (_mint_subject_stub) so live wiring and rebuild replay mint
        # byte-identical stubs (one create path).
        _mint_subject_stub(self.g, entity_name)
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

        D10 (ONTOLOGY v3.15 §4.4/§9.5 Q1): a document is a ``:Source``, so the
        ``aboutDocument`` target is a document-bearing Source matched on ``url``
        (its identity key — the document id) or ``title`` (its display name,
        the old Document convention). The ``documentKind IS NOT NULL`` guard
        keeps a session/connector/provenance Source from ever becoming an
        ``aboutDocument`` target.
        """
        if label == 'Source':
            r = self.g.query(
                "MATCH (e:Source) WHERE (e.url = $name OR e.title = $name) "
                "AND e.documentKind IS NOT NULL "
                "RETURN coalesce(e.title, e.url, $name) LIMIT 1",
                params={"name": target_name},
            ).result_set
        else:
            r = self.g.query(
                f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",
                params={"name": target_name},
            ).result_set
        if r:
            for n in self._resolve_entity(source_id, by_id=True):
                if label == 'Source':
                    self.g.query(
                        f"MATCH (n:{n['label']} {{{n['key']}:$sid}}), (e:Source) "
                        "WHERE (e.url = $name OR e.title = $name) "
                        "AND e.documentKind IS NOT NULL "
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

    def _link_source(self, point_id: str, source_ref: str | Sequence[str], source_kind: str | None = None, *, label: str = "Point") -> None:
        """Link entity → Source via extractedFrom edge (Ontology v3.3).

        Creates stub Source if missing, keyed on url. ``source_kind`` defaults
        per-ref (``agentSession`` for ``session:`` refs, else ``document``) —
        connectors and the Document path pass specific values (github_issue,
        slack_message, linear_card, ...). ``label`` selects the source-side
        entity label — Point (default) or Document (create_document provenance,
        #394).

        ``source_ref`` is **many-to-many** (ontology §3.3, amended #3263): a
        claim extracted from several sources carries one edge per source, so a
        list/tuple of refs is fanned out to N edges. Previously a list was not
        rejected but silently MERGEd a SINGLE Source whose ``url`` was an ARRAY
        (or, for the inferred path, stringified the list into a bogus
        ``session:['s1', 's2']`` ref) — a corrupt provenance node with no error.

        Session-provenance refs (`session:<id>`, written by the capture
        extractors) stamp `is_episodic=true` ON CREATE — the backfill's
        condition 4 (graph-scripts/backfill_is_episodic.py) treats
        Session-linked Sources as episodic; a new capture creating a
        flag-less Source would otherwise keep matching the one-time backfill
        (issue #1486). Non-session Sources (documents, connectors) are
        untouched.
        """
        refs = [source_ref] if isinstance(source_ref, str) else list(source_ref)
        for ref in refs:
            if not ref:
                continue
            # #2489: Source stub creation routed through the SHARED resolver helper
            # (_mint_source_stub — mirror query text, incl. the session: is_episodic
            # clause) so live wiring and rebuild replay mint byte-identical stubs
            # (one create path).
            ref = _mint_source_stub(self.g, ref, source_kind)
            # D10: a Source source-side entity resolves by ``url`` (its identity
            # key); Point/other labels keep the id key.
            key_clause = "{url:$pid}" if label == "Source" else "{id:$pid}"
            self.g.query(
                f"MATCH (n:{label} {key_clause}), (s:Source {{url:$url}}) "
                "MERGE (n)-[:extractedFrom]->(s)",
                params={"pid": point_id, "url": ref},
            )

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Source|Event|Object) for the MATCH.
                The retired ``"Document"`` is accepted as a DEPRECATED ALIAS and
                resolved to ``Source`` (D10, ONTOLOGY v3.15 §4.4).
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Raises:
            ValueError: if entity_label is not one of Source, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        if entity_label == "Document":
            # D10: :Document is retired — a document is a :Source. Kept as a
            # deprecated alias so existing callers/journal replay converge on
            # the same node instead of creating a second label.
            entity_label = "Source"
        valid = {"Source", "Event", "Object"}
        if entity_label not in valid:
            raise ValueError(
                f"Invalid entity_label: {entity_label}. Must be one of {valid} "
                f"(Action was dissolved in Ontology v3.0; 'Document' is a "
                f"deprecated alias for 'Source')."
            )
        # MERGE Source with auto-create (mirrors _link_source) — #205
        key = resolve_source_key(self.g, source_url)
        canonical = normalize_source_url(key)
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
            "    s.canonicalUrl=$cu, s.urlAliases=[$raw_url], "
            "    s.contentHash='', s.ingestedAt=$now "
            "ON MATCH SET s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
            "    s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
            "        THEN s.urlAliases "
            "        ELSE coalesce(s.urlAliases, []) + [$raw_url] END",
            params={"url": key, "raw_url": source_url, "cu": canonical,
                    "sk": source_kind, "now": _now_iso()},
        )
        # D10: a document is a :Source, and a Source resolves by ``url`` (not
        # ``id``) — the same identity key the label moved with.
        key_clause = "{url:$eid}" if entity_label == "Source" else "{id:$eid}"
        self.g.query(
            f"MATCH (s:Source {{url:$url}}), (e:{entity_label} {key_clause}) "
            f"MERGE (s)-[:references]->(e)",
            params={"url": key, "eid": entity_id},
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
