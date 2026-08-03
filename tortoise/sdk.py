"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (Docker/server FalkorDB by default, embedded via path argument).
Lazy-opens on first call. Returns structured dicts, never raw FalkorDB result sets.
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
register_kind("option")    # used by file_decision (#133)
register_kind("evidence")  # used by file_decision (#133)

# Valid status values for Point nodes (used by update_point status validation)
POINT_STATUS_VALUES = frozenset({'live', 'draft', 'outdated', 'archived'})

_logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Optional path to FalkorDBLite database file (None = must use TORTOISE_DB_URI env var).
    """

    def __init__(self, db_path: str | None = None, *, namespace: str | None = None):
        import os, re
        db_uri = os.environ.get("TORTOISE_DB_URI")
        if db_uri:
            self._db_path = None
            self._db_uri = db_uri
        else:
            self._db_path = db_path
            self._db_uri = None
        # Namespace isolation: prefix graph name to segregate data
        if namespace is not None:
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$', namespace):
                raise ValueError(
                    f"Invalid namespace {namespace!r}. "
                    "Use alphanumeric, hyphens, underscores; max 64 chars."
                )
        self._namespace = namespace
        self._proj: FalkorProjection | None = None
        self._ep = None  # lazy-init TortoiseEP
        self._evidence: dict[str, tuple[float, float]] = {}

    def _get_proj(self) -> FalkorProjection:
        if self._proj is None:
            graph_name = "tortoise"
            if self._namespace:
                graph_name = f"{self._namespace}_{graph_name}"
            if self._db_uri is not None:
                self._proj = FalkorProjection.from_uri(self._db_uri)
            else:
                self._proj = FalkorProjection(self._db_path, graph_name=graph_name)
        return self._proj

    # ── Core CRUD ─────────────────────────────────────────────────

    def create_point(self, kind: str, content: str, **props) -> dict:
        """Create a new Point node. Raises ValueError if kind is invalid.

        Set dedup=True for idempotent creation (matches by content hash).
        """
        self._validate_kind(kind)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()

        # Calibration: pop credibility before storing as node property
        credibility = props.pop("credibility", None)
        # Idempotency guard: dedup by content hash when requested
        dedup = props.pop("dedup", False)
        if dedup:
            ch = _content_hash(content)
            existing = proj.g.query(
                "MATCH (n:Point {content_hash:$ch}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN n.id",
                params={"ch": ch},
            ).result_set
            if existing:
                pid = existing[0][0]
                props["updatedAt"] = now
                if credibility is not None:
                    _logger.warning(
                        "credibility=%r ignored — point %s already exists and dedup=True",
                        credibility, pid)
                if props:
                    self.update_point(pid, **props)
                return self.get_point(pid)
            props["content_hash"] = ch

        pid = ulid()
        # Points enter as draft, go live when first edge is created (#131)
        status = props.pop("status", "draft")

        # Compute embedding (Phase 1A, #7698) — stored as Point property
        embedding = None
        try:
            from .embeddings import compute_embedding
            embedding = compute_embedding(content)
        except Exception:
            pass  # Graceful — embedding is optional

        proj.g.query(
            "CREATE (n:Point {id:$id, content:$c, pointKind:$k, "
            "is_operator:false, status:$st, createdAt:$now, updatedAt:$now}) "
            "SET n.embedding = $embedding",
            params={"id": pid, "c": content, "k": kind, "st": status, "now": now,
                    "embedding": embedding},
        )
        for key, val in props.items():
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n += $props",
                params={"id": pid, "props": {key: val}},
            )
        # P1-1: Ontology v2.1 — link Point → Source via extractedFrom
        if props.get("extractedFrom"):
            proj._link_source(pid, props["extractedFrom"])
        # Apply credibility baseline (only on new creation, not dedup)
        if credibility is not None:
            tier_map = {
                "gold": (10, 1), "T0": (10, 1), 0: (10, 1),
                "high": (5, 1), "T1": (5, 1), 1: (5, 1),
                "medium": (3, 1), "T2": (3, 1), 2: (3, 1),
                "low": (2, 1), "T3": (2, 1), 3: (2, 1),
                "unverified": (1.1, 1), "T4": (1.1, 1), 4: (1.1, 1),
            }
            alpha, beta = tier_map.get(credibility, (1, 1))
            self.set_point_baseline(pid, alpha, beta)
        return self.get_point(pid)

    def create_or_update_point(self, kind: str, content: str, **props) -> dict:
        """Idempotent create/update — matches by content hash."""
        return self.create_point(kind, content, dedup=True, **props)

    def update_point(self, id: str, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict.
        
        For :Object-labeled nodes, version is auto-incremented on every update.
        Status changes are validated against POINT_STATUS_VALUES.
        """
        proj = self._get_proj()

        # Validate status if present
        if 'status' in props and props['status'] not in POINT_STATUS_VALUES:
            raise ValueError(
                f"Invalid status {props['status']!r}. "
                f"Must be one of: {', '.join(sorted(POINT_STATUS_VALUES))}"
            )

        # Check if node carries :Object label (entity node with version tracking)
        has_object = proj.g.query(
            "MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]

        if has_object:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            proj.g.query(
                "MATCH (n:Point:Object {id:$id}) "
                "SET n += $props, n.version = coalesce(n.version, 0) + 1, n.updatedAt = $now",
                params={"id": id, "props": props, "now": now},
            )
        else:
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

    def create_operator(self, op_type: str, source_id: str, target_ids: list[str],
                        *, context: str = "sdk") -> dict:
        """Create an operator Point via the projection's event-sourced path.

        Routes through projection.apply() so operators get the full schema
        (context, createdAt, content, etc.) — not just id/is_operator/op_type.
        Uses MERGE for edges (not CREATE) so source/target stubs are
        auto-created rather than silently failing (#130).

        Ontology v2.1: part/whole types (composedOf/decomposesInto/contains)
        → hasPart edge. Edge creation is handled by projection._create_edges.
        """
        if op_type not in ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', or a part/whole type, got {op_type!r}"
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        pid = ulid()
        inputs = [source_id] + list(target_ids)
        proj = self._get_proj()

        # Build event dict and route through projection for full schema (#130)
        event = {
            "type": "OperatorAdded",
            "point": {
                "id": pid,
                "content": f"{op_type}: {source_id} -> {target_ids}",
                "context": context,
                "pointKind": "operator",
                "operator": {"op_type": op_type, "inputs": inputs},
                "status": "live",
                "createdAt": now,
                "updatedAt": now,
            },
        }
        proj.apply(event)
        # Create edges — MERGE avoids silent failures on missing source/target
        proj._create_edges(event["point"])
        # Mark source point as live now that it has its first edge (#131)
        proj.g.query(
            "MATCH (s:Point {id:$sid}) SET s.status = 'live'",
            params={"sid": source_id},
        )
        return self.get_point(pid)

    def annotate_operator(self, id: str, bias: float, precision: float,
                          consistency: float, directness: float) -> dict:
        """Annotate an operator Point with structured epistemic dimensions.

        Args:
            id: Operator Point ID (must have is_operator=true).
            bias: 0-1, how much hidden stake/additional interest beyond stated position.
            precision: 0-1, how narrow/well-defined the relevance claim is.
            consistency: 0-1, how stable this relevance is across contexts.
            directness: 0-1, how directly the source bears on the target.

        Raises ValueError if id not found, not an operator, or dims out of [0,1].
        """
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        for name, val in (("bias", bias), ("precision", precision),
                          ("consistency", consistency), ("directness", directness)):
            if not 0 <= val <= 1:
                raise ValueError(f"{name} must be 0-1, got {val}")
        return self.update_point(id,
            annotator_bias=bias, annotator_precision=precision,
            annotator_consistency=consistency, annotator_directness=directness)

    def mitigate_operator(self, id: str, reason: str, strength: float = 0.5) -> dict:
        """Create a mitigation Point that modulates an operator's edge strength.

        Args:
            id: Operator Point ID to mitigate.
            reason: Why the edge is weaker than it appears.
            strength: 0-1, 0=fully neutralized, 1=fully intact (default 0.5).

        Raises ValueError if id not found or not an operator.
        Idempotent: second call updates existing mitigation (reason + strength),
        does not create a duplicate.
        """
        if not 0 <= strength <= 1:
            raise ValueError(f"strength must be 0-1, got {strength}")
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        # Idempotency: check for existing mitigation
        proj = self._get_proj()
        existing = proj.g.query(
            "MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) RETURN m.id",
            params={"id": id},
        ).result_set
        if existing:
            mid = existing[0][0]
            return self.update_point(mid, content=f"[MITIGATION] {reason}",
                                     mitigation_strength=strength)
        # Create new mitigation Point
        mid = ulid()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        proj.g.query(
            "CREATE (m:Point {id:$id, content:$c, pointKind:'statement', "
            "mitigation_strength:$s, is_operator:false, createdAt:$now, updatedAt:$now})",
            params={"id": mid, "c": f"[MITIGATION] {reason}", "s": strength, "now": now},
        )
        # Bidirectional link: mitigation Point -[:IMPL]-> operator, operator <-[:mitigated_by]- mitigation
        proj.g.query(
            "MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) "
            "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
            params={"mid": mid, "oid": id},
        )
        return self.get_point(mid)

    # ── Query ─────────────────────────────────────────────────────

    def query(self, kind: str | None = None, context: str | None = None,
              **filters) -> list[dict]:
        """Query points by pointKind, context, and/or custom property filters.

        For confidence-aware queries, use tortoise_fts_query() with query=None
        for full-scan mode with EP annotation.
        """
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

        # Orphaned draft points — created but never wired (#131)
        for row in proj.g.query(
            "MATCH (n:Point {status:'draft'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND NOT (n)--() "
            "RETURN n.id, n.content, n.context, n.createdAt "
            "ORDER BY n.createdAt"
        ).result_set:
            violations.append({
                "type": "orphaned_draft",
                "id": row[0],
                "message": (
                    f"Draft point '{row[1][:80] if row[1] else ''}' "
                    f"in context '{row[2] or 'none'}' has no edges "
                    f"(created {row[3] or 'unknown'})"
                ),
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

    def file_decision(self, options: list[str], evidence: list[str],
                      choice: int, context: str) -> dict:
        """File a simple decision directly to the graph — no EP, no calibration,
        no research cycles. Creates decision + options + evidence + IMPL edges
        atomically. For low-stakes decisions where the answer is clear (#133).

        Args:
            options: list of option descriptions (e.g. ["JSON", "YAML"])
            evidence: list of evidence statements supporting the choice
            choice: 0-indexed index into options (the chosen option)
            context: domain context for the decision

        Returns {decision_id, option_ids: [...], evidence_ids: [...]}.
        """
        if not options:
            raise ValueError("At least one option required")
        if choice < 0 or choice >= len(options):
            raise ValueError(f"choice={choice} out of range [0, {len(options)-1}]")

        # 1. Create decision point
        decision = self.create_point(
            "decision",
            f"Decision: {options[choice]}",
            context=context,
            status="live",
        )
        decision_id = decision["id"]

        # 2. Create option points + IMPL edges from decision
        option_ids = []
        for i, opt in enumerate(options):
            opt_point = self.create_point(
                "option",
                f"Option {i+1}: {opt}",
                context=context,
                status="live",  # options are targets, not sources — explicit live
            )
            option_ids.append(opt_point["id"])
            # IMPL edge: decision -> option ("decision considers option")
            self.create_operator("IMPL", decision_id, [opt_point["id"]], context=context)

        # 3. Create evidence points + IMPL edges to the chosen option
        evidence_ids = []
        chosen_id = option_ids[choice]
        for ev in evidence:
            ev_point = self.create_point(
                "evidence",
                ev,
                context=context,
            )
            evidence_ids.append(ev_point["id"])
            # IMPL edge: evidence -> chosen option ("evidence supports choice")
            self.create_operator("IMPL", ev_point["id"], [chosen_id], context=context)

        return {
            "decision_id": decision_id,
            "option_ids": option_ids,
            "evidence_ids": evidence_ids,
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self._get_proj().list_graphs()

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

    def compute_confidence(self, factors=None, evidence=None, context=None,
                           require_calibration: bool = False) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

        Args:
            factors: operator IDs (list[str]) or factor tuples. If None, auto-extracts.
            evidence: optional {claim_id: (alpha, beta)} priors.
            context: if set, scopes auto-extraction to operators connecting Points in this context.
            require_calibration: if True, raises CalibrationError when evidence points are uncalibrated.
        """
        proj = self._get_proj()
        ep = self._get_ep()
        # Hydrate evidence from graph-persisted baselines (survives SDK restarts)
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
            "RETURN n.id, n.ep_alpha, n.ep_beta"
        ).result_set
        for pid, alpha, beta in rows:
            if pid not in self._evidence:
                self._evidence[pid] = (alpha, beta)
        # Apply source-based credibility inheritance
        self._apply_source_inheritance(context=context)
        if evidence:
            self._evidence.update(evidence)
        # Calibration gate
        if require_calibration:
            from .exceptions import CalibrationError
            summary = self.calibrate_summary(context=context)
            evidence_kinds = {"statement", "observation", "hypothesis"}
            uncalibrated = [
                s for s in summary
                if not s["calibrated"] and s.get("pointKind") in evidence_kinds
            ]
            if uncalibrated:
                ids = [s["id"] for s in uncalibrated[:10]]
                msg = (
                    f"{len(uncalibrated)} uncalibrated evidence points. "
                    f"First 10: {ids}. Run calibrate_summary() for full guidance."
                )
                raise CalibrationError(msg)
        if factors is not None:
            operator_ids = [f if isinstance(f, str) else f[0] for f in factors]
        elif context is not None:
            # Scoped extraction: only operators connecting nodes in this context
            rows = proj.g.query(
                "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point {context:$ctx}) "
                "RETURN distinct op.id",
                params={"ctx": context},
            ).result_set
            operator_ids = [r[0] for r in rows]
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
        """Set Beta prior evidence for a claim. Persists to graph immediately."""
        self._evidence[claim_id] = (alpha, beta)
        # Persist to graph so baselines survive SDK restarts
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point {id: $id}) SET n.ep_alpha = $a, n.ep_beta = $b, n.baseline_set = true",
            params={"id": claim_id, "a": alpha, "b": beta},
        )
        return {"claim_id": claim_id, "alpha": alpha, "beta": beta}

    def get_confidence(self, claim_id: str) -> dict:
        """Get EP confidence for a claim: {mean, variance, alpha, beta}."""
        return self._get_ep().compute_confidence(claim_id)

    def _apply_source_inheritance(self, context: str | None = None):
        """Apply credibilityTier from Source nodes to Points via extractedFrom edge.
        
        Only activates when credibilityTier is explicitly set on the Source (NOT NULL).
        Sources without credibilityTier = no inheritance = neutral Beta(1,1).
        If a Point has multiple Sources, the highest tier (lowest number: T0 > T1 > ...) wins.
        """
        proj = self._get_proj()
        tier_map = {"T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1)}
        tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
        
        where = "WHERE s.credibilityTier IS NOT NULL AND (n.baseline_set IS NULL OR n.baseline_set = false)"
        params = {}
        if context:
            where += " AND n.context = $ctx"
            params["ctx"] = context
        
        rows = proj.g.query(
            f"MATCH (n:Point)-[:extractedFrom]->(s:Source) {where} "
            "RETURN n.id, s.credibilityTier",
            params=params,
        ).result_set
        
        # Group by Point ID, select highest tier (lowest number) for each Point
        from collections import defaultdict
        point_tiers = defaultdict(list)
        for pid, tier in rows:
            point_tiers[pid].append(tier)
        
        for pid, tiers in point_tiers.items():
            best_tier = min(tiers, key=lambda t: tier_order.get(t, 99))
            alpha, beta = tier_map.get(best_tier, (1, 1))
            self.set_point_baseline(pid, alpha, beta)

    def calibrate_summary(self, context: str | None = None) -> list[dict]:
        """Audit graph calibration state. Returns per-point guidance.
        
        Checks baseline_set flag on non-operator Points. For uncalibrated
        points, traverses extractedFrom→Source to check for inherited credibilityTier.
        """
        proj = self._get_proj()
        where = "WHERE (n.is_operator IS NULL OR n.is_operator = false)"
        params = {}
        if context:
            where += " AND n.context = $ctx"
            params["ctx"] = context
        
        rows = proj.g.query(
            f"MATCH (n:Point) {where} "
            "OPTIONAL MATCH (n)-[:extractedFrom]->(s:Source) "
            "RETURN n.id, n.content, n.pointKind, "
            "coalesce(n.baseline_set, false) AS calibrated, "
            "s.credibilityTier, s.url AS src_url",
            params=params,
        ).result_set
        
        results = []
        for row in rows:
            pid, content, pk, calibrated, tier, src_url = row
            item = {"id": pid, "content": content, "pointKind": pk, "calibrated": calibrated}
            
            if not calibrated:
                if src_url and tier:
                    item["suggestion"] = f"Inherited {tier} from Source {src_url}"
                elif src_url and not tier:
                    item["suggestion"] = (
                        f"Set credibilityTier on Source {src_url} "
                        f"(covers all points from this source)"
                    )
                else:
                    item["suggestion"] = (
                        f"Call set_point_baseline('{pid}', alpha, beta) "
                        f"or recreate with credibility kwarg"
                    )
            results.append(item)
        
        # Deduplicate: keep one entry per Point ID, prefer Source-based suggestions
        seen = {}
        deduped = []
        for item in results:
            pid = item["id"]
            if pid not in seen:
                seen[pid] = item
                deduped.append(item)
            elif "Source" in str(item.get("suggestion", "")) and "Source" not in str(seen[pid].get("suggestion", "")):
                # Replace in deduped list with the better Source-aware suggestion
                for i, d in enumerate(deduped):
                    if d["id"] == pid:
                        deduped[i] = item
                        break
                seen[pid] = item
        return deduped


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
        result = {"connected": connected, "counts": counts, "total_entities": total}
        if self._namespace:
            result["namespace"] = self._namespace
        return result

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
                "createdAt": frontmatter.get("created", now),
                "updatedAt": frontmatter.get("updated", now),
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

        # Hybrid fallback if no string matches (Phase 0, #7748)
        if not results:
            fts_results = self.tortoise_fts_query(q, kind=kind_filter, limit=limit)
            results = [{"id": r["id"], "name": r.get("content", ""), "kind": r.get("point_kind", ""),
                        "confidence": round(r.get("scores", {}).get("rrf", 0.0) * 0.5, 4)}
                       for r in fts_results]

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

    # ── Hybrid Search (Phase 0, #7748) ───────────────────────────

    def tortoise_fts_query(
        self,
        query: str | None = None,
        kind: str | None = None,
        context: str | None = None,
        *,
        min_confidence: float = 0.0,
        order_by: str = "relevance",
        limit: int = 10,
        threshold: float = 0.0,
    ) -> list[dict]:
        """Hybrid search with RRF fusion + EP annotation.

        Full-scan mode: omit query, set context → all Points in context.
        Best-match mode: provide query → RRF fusion of FTS + vector + structural.

        All results annotated with EP breakdown (confidence_mean + evidence + contention).
        min_confidence defaults to 0.0 (no filter).
        """
        from .search_engine import (
            classify_query, degradation_chain, rrf_fusion,
            annotate_ep_batch, fallback_tfidf, SearchResult, SearchScores,
        )

        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if order_by not in ("relevance", "confidence"):
            raise ValueError(f"order_by must be 'relevance' or 'confidence', got {order_by!r}")

        proj = self._get_proj()
        graph = proj.g

        # 1. Classify query → determine active strategies
        strategies = classify_query(query, kind, context)
        is_full_scan = (query is None and context is not None and not kind)

        # 2. Get query vector if needed
        query_vec = None
        if strategies.get("vector") and query:
            try:
                from .embeddings import EmbeddingModel
                model = EmbeddingModel.get()
                if model:
                    query_vec = model.encode([query])[0].tolist()
            except Exception:
                pass  # Graceful — vector strategy will degrade

        # 3. Run retrieval with degradation
        raw_results = degradation_chain(
            graph, query, kind, context, query_vec, strategies,
            limit=limit * 2,
        )

        if not raw_results:
            # All strategies failed — fallback to in-memory TF-IDF
            if query:
                points = self.query(kind=kind, context=context)
                return fallback_tfidf(query, points, limit=limit)
            return []

        # 4. Fuse via RRF (skip if single strategy or full-scan)
        if is_full_scan or len(raw_results) == 1:
            strat_name, ranked = next(iter(raw_results.items()))
            # Apply threshold filter (score floor)
            fused = {pid: score for pid, score in ranked if score >= threshold}
            match_source = strat_name
        else:
            ranked_lists = list(raw_results.values())
            fused = rrf_fusion(ranked_lists)
            # Apply threshold filter to RRF scores
            if threshold > 0:
                fused = {pid: score for pid, score in fused.items() if score >= threshold}
            match_source = "rrf"

        # 5. Fetch EP breakdowns (batch — single Cypher query)
        result_ids = list(fused.keys())[:limit]

        # Apply kind filter post-retrieval (FTS/vector don't filter by kind natively)
        if kind:
            kind_ids = set()
            try:
                kind_rows = graph.query(
                    "MATCH (n:Point) WHERE n.pointKind = $kind AND n.id IN $ids RETURN n.id",
                    params={"kind": kind, "ids": result_ids},
                ).result_set
                kind_ids = {row[0] for row in kind_rows}
            except Exception:
                pass
            result_ids = [pid for pid in result_ids if pid in kind_ids]

        ep_breakdowns = annotate_ep_batch(graph, result_ids)

        # 6. Build SearchResult objects, filter, and order
        results = []
        for pid in result_ids[:limit]:
            # Fetch Point content
            try:
                rows = graph.query(
                    "MATCH (n:Point {id: $id}) RETURN n.content, n.pointKind, n.context",
                    params={"id": pid},
                ).result_set
                if not rows:
                    continue
                content, pt_kind, pt_context = rows[0][0], rows[0][1], rows[0][2] if len(rows[0]) > 2 else None
            except Exception:
                continue

            ep = ep_breakdowns.get(pid)

            # Apply min_confidence filter (default 0.0 = no filter)
            if ep and ep.confidence_mean < min_confidence:
                continue

            # Build scores
            scores = SearchScores(rrf=fused.get(pid, 0.0))
            if "fts" in raw_results:
                for fid, fscore in raw_results["fts"]:
                    if fid == pid:
                        scores.fts = fscore
                        break
            if "vector" in raw_results:
                for vid, vscore in raw_results["vector"]:
                    if vid == pid:
                        scores.vector = vscore
                        break
            if "structural" in raw_results:
                for sid, sscore in raw_results["structural"]:
                    if sid == pid:
                        scores.structural = sscore
                        break

            result = SearchResult(
                id=pid,
                content=content,
                point_kind=pt_kind,
                context=pt_context,
                scores=scores,
                match_source=match_source,
                ep=ep,
            )
            results.append(result)

        # 7. Order results
        if order_by == "confidence":
            results.sort(
                key=lambda r: r.ep.confidence_mean if r.ep else 0.0,
                reverse=True,
            )
        # Default: RRF relevance order (already in fused order)

        return [r.to_dict() for r in results[:limit]]

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
        # #7395: hash before storing — graph dump won't reveal plaintext keys
        from tortoise.auth import hash_api_key
        key_hash = hash_api_key(api_key)
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
            params={"id": tid, "name": name, "key": key_hash,
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


    # ── Entity CRUD (ONTOLOGY v2.5 §3, all 7 types) ──────────────────

    def _create_entity(self, label: str, id_val: str, props: dict, event_type: str) -> dict:
        """Generic entity creation. Applies to graph via projection (event log + FalkorDB)."""
        proj = self._get_proj()
        # Build event dict
        event = {"type": event_type, "id": id_val, **props}
        # Normalize field names for projection compatibility
        if label == "Subject" and "subjectKind" in props:
            event["subject_kind"] = props["subjectKind"]
        if label == "Object" and "objectKind" in props:
            event["object_kind"] = props["objectKind"]
        if label == "Document" and "documentKind" in props:
            event["document_kind"] = props["documentKind"]
        if label == "Event" and "eventKind" in props:
            event["eventKind"] = event.get("eventKind", props.get("eventKind"))
            if "eventId" not in event:
                event["eventId"] = id_val
        if label == "Source":
            event["url"] = id_val
        # Apply through projection (writes to JSONL + FalkorDB)
        proj.apply(event)
        # Wire edges after entity exists in graph
        if props.get("authoredBy"):
            proj.create_authored_by(id_val, props["authoredBy"])
        if props.get("ownedBy"):
            proj.create_owned_by(id_val, props["ownedBy"])
        if props.get("managedBy"):
            proj.create_managed_by(id_val, props["managedBy"])
        return self._get_entity(id_val)

    def _get_entity(self, id_val: str) -> dict:
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (n) WHERE n.id = $id OR n.eventId = $id OR n.url = $id RETURN properties(n) LIMIT 1",
            params={"id": id_val},
        )
        return dict(r.result_set[0][0]) if r.result_set else {}

    def _update_entity(self, id_val: str, **props) -> dict:
        proj = self._get_proj()
        for key, val in props.items():
            proj.g.query(
                "MATCH (n) WHERE n.id = $id OR n.eventId = $id SET n += $p",
                params={"id": id_val, "p": {key: val}},
            )
        return self._get_entity(id_val)

    def _delete_entity(self, id_val: str) -> bool:
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (n) WHERE n.id = $id OR n.eventId = $id DETACH DELETE n RETURN count(n)",
            params={"id": id_val},
        )
        return bool(r.result_set[0][0]) if r.result_set else False

    def create_subject(self, name: str, subjectKind: str = "other", **props) -> dict:
        return self._create_entity("Subject", self.ulid(), {"name": name, "subjectKind": subjectKind, "status": "live", **props}, "SubjectAdded")

    def create_object(self, name: str, objectKind: str = "other", **props) -> dict:
        return self._create_entity("Object", self.ulid(), {"name": name, "objectKind": objectKind, "status": "live", **props}, "ObjectRegistered")

    def create_action(self, name: str, actionKind: str, **props) -> dict:
        return self._create_entity("Action", self.ulid(), {"name": name, "actionKind": actionKind, "actionStatus": "pending", **props}, "ActionCreated")

    def create_event(self, name: str, eventKind: str, **props) -> dict:
        eid = self.ulid()
        return self._create_entity("Event", eid, {"eventId": eid, "name": name, "eventKind": eventKind, "eventStatus": "scheduled", **props}, "EventRecorded")

    def create_document(self, title: str, documentKind: str, **props) -> dict:
        did = self.ulid()
        return self._create_entity("Document", did, {"title": title, "documentKind": documentKind, "objectKind": "document", "status": "draft", **props}, "DocumentCreated")

    def create_source(self, url: str, sourceKind: str, **props) -> dict:
        return self._create_entity("Source", url, {"url": url, "sourceKind": sourceKind, "ingestedAt": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), **props}, None)

    def get_entity(self, id_val: str) -> dict:
        return self._get_entity(id_val)

    def update_entity(self, id_val: str, **props) -> dict:
        return self._update_entity(id_val, **props)

    def delete_entity(self, id_val: str) -> bool:
        return self._delete_entity(id_val)

    # ── Query Helpers ─────────────────────────────────────────────

    def get_owned_entities(self, subject_id: str) -> list:
        """Return all entities owned by a Subject (governance query)."""
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (e)-[:ownedBy]->(s) WHERE s.id = $sid OR s.name = $sid RETURN properties(e) LIMIT 100",
            params={"sid": subject_id},
        )
        return [dict(row[0]) for row in r.result_set]

    def get_provenance_chain(self, point_id: str) -> list:
        """Return full provenance chain for a Point."""
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(src:Source)-[:references]->(entity) "
            "RETURN properties(src) as source, properties(entity) as entity, labels(entity) as labels LIMIT 1",
            params={"pid": point_id},
        )
        return [{"source": dict(row[0]), "entity": dict(row[1]), "labels": list(row[2])} for row in r.result_set]

    def get_org_structure(self, subject_id: str) -> dict:
        """Return organisational structure: members, roles, sub-teams."""
        proj = self._get_proj()
        members = proj.g.query(
            "MATCH (s)-[:hasMember]->(p:Subject) WHERE s.id = $sid OR s.name = $sid RETURN properties(p)",
            params={"sid": subject_id},
        )
        roles = proj.g.query(
            "MATCH (p:Subject)-[:holdsRole]->(r:Subject) WHERE p.id = $sid OR p.name = $sid RETURN properties(r)",
            params={"sid": subject_id},
        )
        return {
            "members": [dict(row[0]) for row in members.result_set],
            "roles": [dict(row[0]) for row in roles.result_set],
        }

    def ulid(self) -> str:
        from .ids import ulid as _ulid
        return _ulid()

    # ── Source Node Completion ────────────────────────────────────

    def complete_source(self, url: str, content: str = None, external_id: str = None) -> dict:
        """Populate Source node fields: contentHash, version, externalId."""
        import hashlib
        proj = self._get_proj()
        updates = {}
        if content is not None:
            updates["contentHash"] = hashlib.sha256(content.encode()).hexdigest()
        if external_id is not None:
            updates["externalId"] = external_id
        # Increment version
        r = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "SET s.version = coalesce(s.version, 0) + 1, s.updatedAt = $now "
            "SET s += $updates "
            "RETURN properties(s)",
            params={"url": url, "updates": updates, "now": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()},
        )
        return dict(r.result_set[0][0]) if r.result_set else {}

    # ── Backfill Migration ────────────────────────────────────────

    def backfill_v25(self, dry_run: bool = False) -> dict:
        """Backfill existing tortoise.db to ONTOLOGY v2.5 schema."""
        proj = self._get_proj()
        report = {"dry_run": dry_run, "actions": []}

        # 1. Backfill status on Points
        r = proj.g.query("MATCH (n:Point) WHERE n.status IS NULL RETURN count(n)")
        missing_status = r.result_set[0][0]
        if missing_status > 0:
            report["actions"].append(f"status_backfill: {missing_status} Points")
            if not dry_run:
                proj.g.query(f"MATCH (n:Point) WHERE n.status IS NULL SET n.status = 'live'")

        # 2. Backfill pointKind
        r = proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL RETURN count(n)")
        missing_kind = r.result_set[0][0]
        if missing_kind > 0:
            report["actions"].append(f"pointKind_backfill: {missing_kind} Points")
            if not dry_run:
                proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL SET n.pointKind = 'statement'")

        # 3. Count existing edges
        r = proj.g.query("MATCH ()-[r]->() RETURN count(r)")
        report["edge_count"] = r.result_set[0][0]

        # 4. Verify Point count unchanged
        r = proj.g.query("MATCH (n:Point) RETURN count(n)")
        report["point_count"] = r.result_set[0][0]

        return report
