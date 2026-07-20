"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (FalkorDBLite/redislite). Lazy-opens on first call.
Returns structured dicts, never raw FalkorDB result sets.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from .domain_loader import known_kinds, register_kind
from .ids import ulid
from . import monitoring
from .projection import FalkorProjection

# P0 Group 3: register custom kinds for diary + checkpoint
register_kind("diary")
register_kind("checkpoint-item")

_logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Path to the FalkorDBLite database file (default: 'tortoise.db').
    """

    def __init__(self, db_path: str = "tortoise.db"):
        self._db_path = db_path
        self._proj: FalkorProjection | None = None
        self._ep = None  # lazy-init TortoiseEP
        self._evidence: dict[str, tuple[float, float]] = {}

    def _get_proj(self) -> FalkorProjection:
        if self._proj is None:
            self._proj = FalkorProjection(self._db_path)
        return self._proj

    # ── Core CRUD ─────────────────────────────────────────────────

    def create_point(self, kind: str, content: str, **props) -> dict:
        """Create a new Point node. Raises ValueError if kind is invalid."""
        self._validate_kind(kind)
        from datetime import datetime, timezone
        pid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()
        proj.g.query(
            "CREATE (n:Point {id:$id, content:$c, pointKind:$k, "
            "is_operator:false, createdAt:$now, updatedAt:$now})",
            params={"id": pid, "c": content, "k": kind, "now": now},
        )
        for key, val in props.items():
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n += $props",
                params={"id": pid, "props": {key: val}},
            )
        # P1-1: Ontology v2.1 — link Point → Source via extractedFrom
        if props.get("extractedFrom"):
            proj._link_source(pid, props["extractedFrom"])
        return self.get_point(pid)

    def create_or_update_point(self, kind: str, content: str, **props) -> dict:
        """Idempotent create/update — matches by content hash."""
        self._validate_kind(kind)
        from datetime import datetime, timezone
        ch = _content_hash(content)
        proj = self._get_proj()
        existing = proj.g.query(
            "MATCH (n:Point {content_hash:$ch}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id",
            params={"ch": ch},
        ).result_set
        if existing:
            pid = existing[0][0]
            props["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self.update_point(pid, **props)
            return self.get_point(pid)
        return self.create_point(kind, content, content_hash=ch, **props)

    def update_point(self, id: str, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict."""
        proj = self._get_proj()
        for key, val in props.items():
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n += $props",
                params={"id": id, "props": {key: val}},
            )
        return self.get_point(id)

    def delete_point(self, id: str) -> bool:
        """Delete a Point and its relationships. Returns True if found."""
        proj = self._get_proj()
        exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]
        if not exists:
            return False
        proj.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": id})
        return True

    def delete_point_wrapped(self, id: str) -> dict:
        """Delete a Point. Returns dict for MCP tool consumption."""
        found = self.delete_point(id)
        return {"deleted": found, "id": id}

    # ── Invalidate / Supersede (#6999 GAP-12) ────────────────────

    def invalidate_point(self, id: str, corrected_by_id: str) -> dict:
        """Mark a Point outdated, linked to its replacement via CORRECTS edge."""
        from datetime import datetime, timezone
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now",
            params={"id": id, "now": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "CREATE (a)-[:CORRECTS]->(b)",
            params={"new_id": corrected_by_id, "old_id": id},
        )
        return {"invalidated": True, "id": id, "corrected_by": corrected_by_id}

    def supersede_point(self, old_id: str, new_id: str) -> dict:
        """Atomically replace old Point with new — CORRECTS edge + outdated flag."""
        return self.invalidate_point(old_id, new_id)

    # ── Operators ─────────────────────────────────────────────────

    def create_operator(self, op_type: str, source_id: str, target_ids: list[str]) -> dict:
        """Create an operator Point. Edges follow projection convention:
        operator -(op_type)-> inputs. Ontology v2.1: INPUT edges removed,
        part/whole types (composedOf/decomposesInto/contains) → hasPart."""
        if op_type not in ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', or a part/whole type, got {op_type!r}"
            )
        pid = ulid()
        inputs = [source_id] + list(target_ids)
        proj = self._get_proj()
        proj.g.query(
            "CREATE (o:Point {id:$id, is_operator:true, op_type:$op})",
            params={"id": pid, "op": op_type},
        )
        # Ontology v2.1: map part/whole ops to hasPart, remove INPUT edges
        edge_type = "hasPart" if op_type not in ("IMPL", "NAND") else op_type
        for i, inp_id in enumerate(inputs):
            proj.g.query(
                f"MATCH (o:Point {{id:$oid}}), (s:Point {{id:$sid}}) "
                f"CREATE (o)-[:{edge_type} {{idx:$i}}]->(s)",
                params={"oid": pid, "sid": inp_id, "i": i},
            )
        return self.get_point(pid)

    # ── Query ─────────────────────────────────────────────────────

    def query(self, kind: str | None = None, context: str | None = None, **filters) -> list[dict]:
        """Query points by pointKind, context, and/or custom property filters."""
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        if kind:
            clauses.append("n.pointKind = $kind")
            params["kind"] = kind
        if context:
            clauses.append("n.context = $ctx")
            params["ctx"] = context
        for key, val in filters.items():
            if not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid filter key: {key!r}")
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)",
            params=params,
        ).result_set
        return [r[0] for r in rows]

    def paginated_query(self, kind: str | None = None, context: str | None = None,
                         skip: int = 0, limit: int = 20, **filters) -> dict:
        """Query points with pagination. Returns {results, total, hasMore}."""
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        if kind:
            clauses.append("n.pointKind = $kind")
            params["kind"] = kind
        if context:
            clauses.append("n.context = $ctx")
            params["ctx"] = context
        for key, val in filters.items():
            if not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid filter key: {key!r}")
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        total = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN count(n)",
            params=params,
        ).result_set[0][0]
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)"
            f" ORDER BY n.createdAt DESC SKIP $skip LIMIT $limit",
            params={**params, "skip": skip, "limit": limit},
        ).result_set
        results = [r[0] for r in rows]
        return {"results": results, "total": total, "hasMore": skip + limit < total}

    def get_point(self, id: str) -> dict:
        """Get a Point by ID. Returns dict of all properties, or {} if not found."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": id},
        ).result_set
        return rows[0][0] if rows else {}

    def traverse(self, id: str, relationship_type: str, direction: str = "outgoing") -> list[dict]:
        """Traverse relationships from a Point. Returns connected point dicts."""
        proj = self._get_proj()
        pat = (f"(n:Point {{id:$id}})-[:{relationship_type}]->(m:Point)"
               if direction == "outgoing" else
               f"(n:Point {{id:$id}})<-[:{relationship_type}]-(m:Point)")
        rows = proj.g.query(
            f"MATCH {pat} RETURN m.id, m.content, m.pointKind, m.context",
            params={"id": id},
        ).result_set
        return [
            {"id": r[0], "content": r[1], "pointKind": r[2], "context": r[3]}
            for r in rows
        ]

    # ── Chain Integrity ───────────────────────────────────────────

    def check_structure(self) -> list[dict]:
        """Check Gate 0→4 chain integrity. Returns list of violation dicts."""
        proj = self._get_proj()
        violations: list[dict] = []

        # useCase without parent JTBD
        ucs = proj.g.query(
            "MATCH (uc:Point {pointKind:'useCase'}) RETURN uc.id, uc.uc_id"
        ).result_set
        for uc_id, uc_ref in ucs:
            parents = proj.g.query(
                "MATCH (op:Point {is_operator:true, op_type:'composedOf'})"
                "-[:hasPart]->(uc:Point {id:$id}), "
                "(op)-[:hasPart]->(jtbd:Point {pointKind:'jobToBeDone'}) "
                "RETURN jtbd.id",
                params={"id": uc_id},
            ).result_set
            if not parents:
                violations.append({
                    "type": "orphan_use_case",
                    "id": uc_id,
                    "message": f"useCase {uc_ref or uc_id} has no parent JTBD",
                })

        # userJourney dangling UC refs
        for uj_id, covered in proj.g.query(
            "MATCH (uj:Point {pointKind:'userJourney'}) RETURN uj.id, uj.covered_use_cases"
        ).result_set:
            if not covered:
                continue
            for uc_ref in covered.split(","):
                uc_ref = uc_ref.strip()
                if not proj.g.query(
                    "MATCH (uc:Point {pointKind:'useCase', uc_id:$ref}) RETURN count(uc) > 0",
                    params={"ref": uc_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_use_case_ref",
                        "id": uj_id,
                        "message": f"userJourney {uj_id} refs non-existent useCase {uc_ref}",
                    })

        # Workflow dangling JTBD refs
        for wf_id, enables in proj.g.query(
            "MATCH (wf:Point {pointKind:'workflow'}) RETURN wf.id, wf.enables_jtbd"
        ).result_set:
            if not enables:
                continue
            for jtbd_ref in enables.split(","):
                jtbd_ref = jtbd_ref.strip()
                if not proj.g.query(
                    "MATCH (j:Point {pointKind:'jobToBeDone', jtbd_id:$ref}) RETURN count(j) > 0",
                    params={"ref": jtbd_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_jtbd_ref",
                        "id": wf_id,
                        "message": f"workflow {wf_id} refs non-existent JTBD {jtbd_ref}",
                    })

        # Requirement dangling Workflow refs
        for req_id, wf_ref in proj.g.query(
            "MATCH (req:Point {pointKind:'requirement'}) RETURN req.id, req.enabled_workflow"
        ).result_set:
            if not wf_ref or wf_ref == "ALL":
                continue
            if not proj.g.query(
                "MATCH (w:Point {pointKind:'workflow', wf_id:$ref}) RETURN count(w) > 0",
                params={"ref": wf_ref},
            ).result_set[0][0]:
                violations.append({
                    "type": "dangling_workflow_ref",
                    "id": req_id,
                    "message": f"requirement {req_id} refs non-existent workflow {wf_ref}",
                })

        return violations

    def summarize_structure(self) -> dict:
        """Count points per Gate (by context). Returns {gate: count, ..., total}."""
        proj = self._get_proj()
        gates = [
            ("gate0_jtbds", "tortoise-wf-gate0"),
            ("gate1_use_cases", "tortoise-wf-gate1"),
            ("gate2_user_journeys", "tortoise-wf-gate2"),
            ("gate3_workflows", "tortoise-wf-gate3"),
            ("gate4_requirements", "tortoise-wf-gate4"),
        ]
        result: dict[str, int] = {}
        for key, ctx in gates:
            result[key] = proj.g.query(
                "MATCH (n:Point {context:$c}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN count(n)",
                params={"c": ctx},
            ).result_set[0][0]
        result["total"] = sum(result.values())
        return result

    # ── Taxonomy ─────────────────────────────────────────────────

    def taxonomy(self) -> dict[str, int]:
        """Count entities by node label. Returns {Point: N, Event: N, ...}."""
        from .taxonomy import taxonomy as _taxonomy
        return _taxonomy(self._get_proj())

    def list_domains(self) -> list[dict]:
        """Active domains with entity counts. Returns [{context, count}] ordered by count DESC."""
        from .taxonomy import list_domains as _list_domains
        return _list_domains(self._get_proj())

    def list_topics(self, entity_id: str) -> dict:
        """entityProfile lite for an entity. Returns {id, pointKind, context, neighbors, neighborCounts}."""
        from .taxonomy import list_topics as _list_topics
        return _list_topics(self._get_proj(), entity_id)

    # ── Bulk ──────────────────────────────────────────────────────

    def batch_create_points(self, points_list: list[dict]) -> list[dict]:
        """Create multiple points. Each dict needs {kind, content, **props}."""
        return [self.create_point(**p) for p in points_list]

    # ── Lifecycle ─────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying database connection."""
        if self._proj is not None:
            self._proj.close()
            self._proj = None

    # ── P1-4: Entity Linking ────────────────────────────────────

    def provenance(self, point_id: str) -> dict:
        """Provenance chain — "Who decided this?" Point → Subject → delegation."""
        point = self.get_point(point_id)
        if not point:
            return {"error": f"Point {point_id} not found"}
        author = point.get("authoredBy", "")
        chain = {"point": {"id": point_id, "content": (point.get("content") or "")[:200],
                           "authoredBy": author}}
        if not author:
            return chain
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Subject) WHERE toLower(s.name) = toLower($n) RETURN properties(s)",
            params={"n": author},
        ).result_set
        if not rows:
            return {**chain, "subject": None}
        sub = rows[0][0]
        chain["subject"] = {"id": sub.get("id"), "name": sub.get("name"),
                             "kind": sub.get("subjectKind", "")}
        # ponytail: follow outgoing rels for Role → Team delegation
        rels = proj.g.query(
            "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], properties(n)",
            params={"sid": sub["id"]},
        ).result_set
        chain["delegation"] = [{"via": r[0], "node_type": r[1], "props": r[2]} for r in rels]
        return chain

    def create_subject(self, name: str, subject_kind: str = "other") -> dict:
        """Create or MERGE a Subject node. Deduplicates by name."""
        from datetime import datetime, timezone
        proj = self._get_proj()
        sid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        proj.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$id, s.subjectKind=$sk, s.createdAt=$now "
            "ON MATCH SET s.subjectKind=coalesce($sk, s.subjectKind) "
            "RETURN s.id, s.name, s.subjectKind",
            params={"id": sid, "name": name, "sk": subject_kind, "now": now},
        )
        rows = proj.g.query(
            "MATCH (s:Subject {name:$name}) RETURN properties(s)",
            params={"name": name},
        ).result_set
        return rows[0][0] if rows else {"id": sid, "name": name, "subjectKind": subject_kind}

    def create_object(self, name: str, object_kind: str = "other") -> dict:
        """Create or MERGE an Object node. Deduplicates by name."""
        from datetime import datetime, timezone
        proj = self._get_proj()
        oid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        proj.g.query(
            "MERGE (o:Object {name:$name}) "
            "ON CREATE SET o.id=$id, o.objectKind=$ok, o.createdAt=$now "
            "ON MATCH SET o.objectKind=coalesce($ok, o.objectKind) "
            "RETURN o.id, o.name, o.objectKind",
            params={"id": oid, "name": name, "ok": object_kind, "now": now},
        )
        rows = proj.g.query(
            "MATCH (o:Object {name:$name}) RETURN properties(o)",
            params={"name": name},
        ).result_set
        return rows[0][0] if rows else {"id": oid, "name": name, "objectKind": object_kind}

    # ── #7045: about edges backfill (Ontology v2.1) ──────────

    def backfill_about_entities(self) -> dict:
        """Keyword-match Points against Subject/Object names → about edges.

        For each Point (non-operator), checks if its content contains any Subject
        or Object name. If yes, creates aboutSubject or aboutObject edge.
        Idempotent — MERGE prevents duplicates.

        Returns {scanned, updated, entities_matched}.
        """
        proj = self._get_proj()
        # Load all entity names → ids
        entities: dict[str, str] = {}
        for label, key in [("Subject", "subjectKind"), ("Object", "objectKind")]:
            rows = proj.g.query(
                f"MATCH (e:{label}) RETURN e.name, e.id"
            ).result_set
            for name, eid in rows:
                if name:
                    entities[name.lower()] = eid

        # Ontology v2.1: use aboutSubject/aboutObject edges instead of property
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id, n.content"
        ).result_set

        scanned, updated, matched = 0, 0, 0
        for pid, content in rows:
            scanned += 1
            if not content:
                continue
            content_lower = content.lower()
            for name, eid in entities.items():
                if name in content_lower:
                    proj._create_about_edges(pid, name)
                    matched += 1
            if matched > 0:
                updated += 1

        return {"scanned": scanned, "updated": updated, "entities_matched": matched}

    # ── P1-3: Staleness ─────────────────────────────────────────

    def stale_points(self, days: int = 30, limit: int = 50) -> dict:
        """Return Points not updated in N days. Returns {stale: [...], count: N, cutoff: '...'}."""
        proj = self._get_proj()
        stale = proj.stale_points(days=days, limit=limit)
        return {"stale": stale, "count": len(stale),
                "cutoff": f"{days} days", "limit": limit}

    # ── EP Belief Propagation (#6908) ────────────────────────────

    def _get_ep(self):
        if self._ep is None:
            from .ep import TortoiseEP
            self._ep = TortoiseEP(self._get_proj())
        return self._ep

    def compute_confidence(self, factors=None, evidence=None) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}."""
        proj = self._get_proj()
        ep = self._get_ep()
        if evidence:
            self._evidence.update(evidence)
        if factors is not None:
            operator_ids = [f[0] for f in factors]
        else:
            factors_data, _ = proj.extract_svbp_factors()
            operator_ids = [f[0] for f in factors_data]
        if not operator_ids:
            return {"iterations": 0, "converged": True, "confidences": {}}
        iterations, converged = ep.run(operator_ids, evidence=self._evidence)
        confidences = {}
        proj = self._get_proj()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for claim_id in ep._affected_claims(operator_ids):
            conf = ep.compute_confidence(claim_id)
            confidences[claim_id] = conf
            # Write back mean confidence to node property
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.confidence = $c, n.updatedAt = $now",
                params={"id": claim_id, "c": conf["mean"], "now": now},
            )
        return {"iterations": iterations, "converged": converged, "confidences": confidences}

    def set_point_baseline(self, claim_id: str, alpha: float, beta: float) -> dict:
        """Set Beta prior evidence for a claim."""
        self._evidence[claim_id] = (alpha, beta)
        return {"claim_id": claim_id, "alpha": alpha, "beta": beta}

    def get_confidence(self, claim_id: str) -> dict:
        """Get EP confidence for a claim: {mean, variance, alpha, beta}."""
        return self._get_ep().compute_confidence(claim_id)


    # ── P0 Group 3: Checkpoint, Diary, Status, Analyze, Ingest ────

    def _content_exists(self, content: str) -> str | None:
        """Return point ID if a point with this content hash exists, else None."""
        ch = _content_hash(content)
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {content_hash:$ch}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id",
            params={"ch": ch},
        ).result_set
        return rows[0][0] if rows else None

    def checkpoint(self, items: list[dict], agent_name: str = "checkpoint",
                   threshold: float = 0.95) -> dict:
        """Session batch save — two-tier dedup (content hash + embedding similarity).

        Each item: {wing, room, content}. Returns {filed: N, duplicates: M}.
        threshold: cosine similarity for semantic dedup (0.0-1.0).
                   Set to 1.0 to disable semantic dedup (hash-only).
        """
        from datetime import datetime, timezone
        filed, duplicates = 0, 0
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # Tier 1: content hash dedup
        to_check: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for item in items:
            content = item["content"]
            ch = _content_hash(content)
            if ch in seen or self._content_exists(content):
                duplicates += 1
                continue
            seen.add(ch)
            to_check.append((item, ch))

        if not to_check:
            return {"filed": 0, "duplicates": duplicates}

        # Tier 2: embedding similarity dedup (GAP-08)
        to_file = to_check
        if threshold < 1.0:
            try:
                to_file = self._semantic_dedup(to_check, threshold)
            except Exception:
                pass  # ponytail: embeddings unavailable → hash-only fallback

        duplicates += len(to_check) - len(to_file)

        for item, ch in to_file:
            p = self.create_point(
                "checkpoint-item", item["content"],
                wing=item.get("wing", ""),
                room=item.get("room", ""),
                content_hash=ch,
            )
            # GAP-07: emit EventRecorded for provenance
            try:
                proj.apply({
                    "type": "EventRecorded",
                    "id": ulid(),
                    "eventKind": "pointAdded",
                    "subject": agent_name,
                    "object": p["id"],
                    "startedAt": now,
                })
            except Exception:
                _logger.warning("Failed to emit provenance event for point %s", p["id"])
            filed += 1

        monitoring.record_ingest()
        return {"filed": filed, "duplicates": duplicates}

    def _semantic_dedup(self, candidates: list[tuple[dict, str]],
                        threshold: float) -> list[tuple[dict, str]]:
        """Filter candidates by embedding similarity against existing checkpoint items."""
        import numpy as np
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {pointKind:'checkpoint-item'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.content"
        ).result_set
        existing = [r[0] for r in rows if r[0]]
        if not existing:
            return candidates

        new_texts = [item["content"] for item, _ch in candidates]
        # ponytail: prefer sentence_transformers; TF-IDF fallback for zero-dependency path
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vecs = model.encode(existing + new_texts, show_progress_bar=False)
        except ImportError:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vecs = TfidfVectorizer().fit_transform(existing + new_texts).toarray()

        e_vecs, n_vecs = vecs[:len(existing)], vecs[len(existing):]

        def _norm(v):
            n = np.linalg.norm(v, axis=1, keepdims=True)
            n[n == 0] = 1
            return v / n

        max_sims = (_norm(n_vecs) @ _norm(e_vecs).T).max(axis=1)
        return [(item, ch) for i, (item, ch) in enumerate(candidates)
                if max_sims[i] < threshold]

    def diary_write(self, agent_name: str, entry: str,
                    topic: str | None = None, wing: str | None = None) -> dict:
        """Write an agent diary entry. Returns the created Point."""
        from datetime import datetime, timezone
        props: dict[str, Any] = {"authoredBy": agent_name}
        if topic:
            props["topic"] = topic
        context = wing or f"diary_{agent_name}"
        return self.create_point("diary", entry, context=context, **props)

    def diary_read(self, agent_name: str, last_n: int = 10,
                   wing: str | None = None) -> list[dict]:
        """Read recent diary entries for an agent, newest first."""
        context = wing or f"diary_{agent_name}"
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {pointKind:'diary', authoredBy:$agent, context:$ctx}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
            params={"agent": agent_name, "ctx": context, "lim": last_n},
        ).result_set
        return [r[0] for r in rows]

    def status(self) -> dict:
        """Graph health + entity counts + FalkorDB connectivity.

        Returns {connected, counts: {Point, Event, ...}, total_entities}.
        """
        proj = self._get_proj()
        connected = False
        try:
            proj.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            connected = True
        except Exception:
            pass
        counts = self.taxonomy()
        total = sum(counts.values())
        return {"connected": connected, "counts": counts, "total_entities": total}

    def ingest_corpus(self, directory: str) -> dict:
        """Batch document ingestion — walk directory, parse YAML frontmatter,
        create/update Document nodes. Returns {ingested, updated, skipped}."""
        import re
        from pathlib import Path
        from datetime import datetime, timezone

        _FM_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)
        ingested, updated, skipped = 0, 0, 0
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()

        for filepath in Path(directory).rglob("*.md"):
            text = filepath.read_text(encoding="utf-8")
            m = _FM_RE.match(text)
            frontmatter: dict[str, str] = {}
            if m:
                for line in m.group(1).split('\n'):
                    kv = line.split(':', 1)
                    if len(kv) != 2:
                        continue
                    k, v = kv[0].strip(), kv[1].strip().strip('"').strip("'")
                    if k and v:
                        frontmatter[k] = v

            doc_id = str(filepath.relative_to(directory))
            title = frontmatter.get("title", filepath.stem)
            doc_kind = frontmatter.get("type", frontmatter.get("document_kind", ""))
            domain = frontmatter.get("domain", frontmatter.get("documentKnowledgeDomain", ""))

            # Check if document exists
            exists = proj.g.query(
                "MATCH (e:Event {eventId:$eid}) RETURN count(e) > 0",
                params={"eid": doc_id},
            ).result_set[0][0]

            props = {
                "title": title,
                "document_kind": doc_kind,
                "document_knowledge_domain": domain,
                "authored_by": frontmatter.get("authoredBy", ""),
                "owned_by": frontmatter.get("ownedBy", ""),
                "managed_by": frontmatter.get("managedBy", ""),
                "governing_agreement": frontmatter.get("governedBy", frontmatter.get("governingAgreement", "")),
                "doc_status": frontmatter.get("doc_status", "draft"),
                "format": "markdown",
                "version": frontmatter.get("version", ""),
                "created_at": frontmatter.get("created", now),
                "updated_at": frontmatter.get("updated", now),
                "eventKind": "DocumentCreated",
                "classificationLevel": "internal",
            }

            if exists:
                proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) SET e += $props",
                    params={"eid": doc_id, "props": props},
                )
                updated += 1
            else:
                proj.g.query(
                    "CREATE (e:Event {eventId:$eid}) SET e += $props",
                    params={"eid": doc_id, "props": props},
                )
                ingested += 1

        monitoring.record_ingest()
        return {"ingested": ingested, "updated": updated, "skipped": skipped}

    # ── Entity Resolution (GAP-01 #6987) ──────────────────────

    def suggest_entry_points(self, query: str, *, limit: int = 5,
                             kind_filter: str | None = None) -> list[dict]:
        """Entity resolution — NL query → matching entities from the graph.

        String match on content (Cypher CONTAINS) + embedding fallback.
        Returns [{id, name, kind, confidence}] sorted by confidence DESC.
        kind_filter filters by n.context (namespace), not pointKind.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        q = query.strip()[:500]  # ponytail: bound to 500 chars; embedding APIs have token limits
        if not q:
            return []

        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)",
                   "toLower(n.content) CONTAINS toLower($q)"]
        params = {"q": q}
        if kind_filter:
            clauses.append("n.context = $kf")
            params["kf"] = kind_filter

        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} "
            "RETURN n.id, n.content, n.pointKind",
            params=params,
        ).result_set

        results = []
        q_lower = q.lower()
        for pid, content, kind in rows:
            # ponytail: guard empty content (stub nodes may have '')
            if not content:
                continue
            confidence = 1.0 if content.lower() == q_lower else round(len(q) / len(content), 4)
            results.append({"id": pid, "name": content, "kind": kind or "", "confidence": confidence})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:limit]

        # Embedding fallback if no string matches (respects kind_filter as context)
        if not results:
            semantic = self.search(q, context=kind_filter, threshold=0.3, limit=limit)
            results = [{"id": r["id"], "name": r["content"], "kind": "",
                        "confidence": round(r["similarity"] * 0.5, 4)} for r in semantic]

        return results

    # ── Session Context (#6989) ──────────────────────────────

    def session_context(self) -> dict:
        """Return 'what happened last session' — diary entries, Points, Events, confidence changes.
        Returns structured dict with explicit 'no_prior_sessions' when graph is empty."""
        proj = self._get_proj()
        diary_entries = [r[0] for r in proj.g.query(
            "MATCH (n:Point {pointKind:'diary'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 10"
        ).result_set]
        recent_points = [r[0] for r in proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 20"
        ).result_set]
        recent_events = [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT 20"
        ).result_set]
        confidence_changes = [
            {"id": r[0], "content": r[1], "pointKind": r[2],
             "confidence": r[3], "updatedAt": r[4]}
            for r in proj.g.query(
                "MATCH (n:Point) WHERE n.confidence IS NOT NULL "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN n.id, n.content, n.pointKind, n.confidence, n.updatedAt "
                "ORDER BY n.updatedAt DESC LIMIT 20"
            ).result_set
        ]
        no_prior = not diary_entries and not recent_points and not recent_events
        return {
            "no_prior_sessions": no_prior,
            "diary_entries": diary_entries,
            "recent_points": recent_points,
            "recent_events": recent_events,
            "confidence_changes": confidence_changes,
        }

    # ── Semantic Search (#6990) ─────────────────────────────────

    def search(self, query: str, kind: str | None = None,
               context: str | None = None, *,
               threshold: float = 0.3, limit: int = 10) -> list[dict]:
        """Semantic/vector search over Points. Returns ranked [{id, content, similarity, snippet}, ...]."""
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
        from .embeddings import search_points
        points = self.query(kind=kind, context=context)
        return search_points(query, points, threshold=threshold, limit=limit)

    # ── Multi-tenancy (#7001) ─────────────────────────────────

    def team_create(self, name: str) -> dict:
        """Create isolated team graph via FalkorDB select_graph.
        Returns {name, graph_name, api_key, id}.

        Per-team API key enforcement is Phase 2 — this is infrastructure creation.
        Caller must store the returned api_key securely; it is not retrievable later.
        """
        import re, uuid
        from datetime import datetime, timezone

        # Input validation
        if not name or not name.strip():
            raise ValueError("Team name must not be empty")
        if len(name) > 64:
            raise ValueError("Team name must be 64 characters or fewer")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
            raise ValueError(
                f"Invalid team name: {name!r}. Use alphanumeric, hyphens, underscores."
            )

        api_key = f"tt_{uuid.uuid4().hex}"
        graph_name = f"team_{name}"
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency check — prevent duplicate teams
        existing = proj.g.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": name},
        ).result_set[0][0]
        if existing:
            raise ValueError(f"Team {name!r} already exists")

        # Write registry entry first (source of truth), then create team graph.
        # On team-graph failure we can clean up the registry entry.
        tid = ulid()
        proj.g.query(
            "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
            "graph_name:$gn, createdAt:$now})",
            params={"id": tid, "name": name, "key": api_key,
                    "gn": graph_name, "now": now},
        )
        try:
            team_graph = proj.db.select_graph(graph_name)
            team_graph.query(
                "CREATE (:TeamMeta {name:$name, created:$now})",
                params={"name": name, "now": now},
            )
        except Exception:
            # Roll back registry entry; don't mask the original error
            try:
                proj.g.query(
                    "MATCH (t:Team {id:$id}) DETACH DELETE t",
                    params={"id": tid},
                )
            except Exception:
                pass  # ponytail: best-effort rollback; if DB is dead, nothing to do
            raise

        return {"name": name, "graph_name": graph_name, "api_key": api_key, "id": tid}

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _validate_kind(kind: str) -> None:
        # ponytail: open-ended kind vocabularies — any string accepted.
        # Warning for unrecognized values; domain_loader.register_kind() can suppress.
        if kind not in known_kinds():
            _logger.warning(
                "Unrecognized pointKind %r. Known values: %s. "
                "Use tortoise.domain_loader.register_kind(%r) to register it.",
                kind, sorted(known_kinds()), kind,
            )
