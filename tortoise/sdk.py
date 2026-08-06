"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (Docker/server FalkorDB by default, embedded via path argument).
Lazy-opens on first call. Returns structured dicts, never raw FalkorDB result sets.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from .domain_loader import known_kinds, register_kind
from .ids import ulid
from . import monitoring
from .projection import FalkorProjection
import threading

# P0 Group 3: register custom kinds for diary + checkpoint
register_kind("diary")
register_kind("checkpoint-item")
register_kind("option")    # used by file_decision (#133)
register_kind("evidence")  # used by file_decision (#133)

# Valid status values for Point nodes (used by update_point status validation)
POINT_STATUS_VALUES = frozenset({'live', 'draft', 'outdated', 'archived'})

_logger = logging.getLogger(__name__)


# ── ULID validation (Issue #52) ──
# Canonical format (from tortoise/ids.py): <timestamp-hex>-<uuid12>
_ULID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")
# Standard Crockford base32 ULID (26 chars) — recognized as valid
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)


def _is_ulid(s: str) -> bool:
    """Return True if *s* matches a valid ULID format (canonical or Crockford)."""
    return bool(_ULID_RE.match(s) or _CROCKFORD_ULID_RE.match(s))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Module-level cached registry for kind expansion
_registry_cache: "PackRegistry | None" = None
_registry_lock = threading.Lock()


def _get_kind_expander():
    """Return cached PackRegistry with pre-computed expansion table."""
    global _registry_cache
    if _registry_cache is None:
        with _registry_lock:
            if _registry_cache is None:
                from .pack_registry import PackRegistry
                from pathlib import Path as _Path
                packs_dir = _Path(__file__).resolve().parent.parent / "packs"
                _registry_cache = PackRegistry(packs_dir)
                _registry_cache.load_all()
    return _registry_cache


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Optional path to FalkorDBLite database file (None = use TORTOISE_DB_URI env var).
        namespace: Optional namespace for graph-name isolation.

    Precedence: an explicitly-provided db_path wins over the TORTOISE_DB_URI
    env var. This lets tests/fixtures force a temp embedded DB even when a
    shared test URI is set in the environment (#139).
    """

    def __init__(self, db_path: str | None = None, *, namespace: str | None = None):
        import os, re
        db_uri = os.environ.get("TORTOISE_DB_URI")
        if db_uri and db_path is None:
            self._db_path = None
            self._db_uri = db_uri
        else:
            self._db_path = db_path
            self._db_uri = None
        # P0: Crash early if running in production with no database configured.
        # Embedded redislite has no persistent volume → all data lost on deploy.
        if not db_uri and not db_path:
            if os.environ.get("FLY_APP_NAME"):
                raise RuntimeError(
                    "TORTOISE_DB_URI is empty in production. "
                    "Set FALKORDB_PASSWORD (recommended: entrypoint.sh auto-constructs the URI) "
                    "or set TORTOISE_DB_URI directly. "
                    "See docs/infra-runbook.md §1."
                )
            # Dev/CI: proceed, will use embedded redislite (tests set their own URI)
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
        self._registry_g = None
        self._audit_logger = None

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

    def test_guard(self) -> None:
        """Assert the connected graph is safe for destructive test teardowns.

        Raises RuntimeError if the graph appears to be a production graph
        (named ``tortoise`` or ``tortoise_restored_*``).  Test fixtures
        should call this before any ``MATCH (n) DETACH DELETE n``.

        Override with ``TORTOISE_ALLOW_PRODUCTION=1``.
        """
        import os
        if os.environ.get("TORTOISE_ALLOW_PRODUCTION") == "1":
            return

        proj = self._get_proj()
        graph_name = getattr(proj, "graph_name", None)
        if graph_name is None:
            graph_name = getattr(proj.g, "name", "unknown")

        # Block destructive ops on production graphs:
        #   tortoise             — the real graph
        #   tortoise_restored_*  — restored snapshots (precious)
        blocked = (
            graph_name == "tortoise"
            or graph_name.startswith("tortoise_restored")
        )
        if blocked:
            raise RuntimeError(
                f"SAFETY GUARD: Destructive operation blocked on graph "
                f"'{graph_name}'. This appears to be a production graph. "
                f"Use an isolated test graph (e.g. "
                f"'tortoise_test_calibration') instead. "
                f"Override with TORTOISE_ALLOW_PRODUCTION=1."
            )

    def _get_registry(self):
        """Return the control_plane registry graph handle (cached).

        Uses the existing db connection — no second FalkorDB connection.
        Registry graph name is namespace-scoped (``{ns}_control_plane``) so
        different namespaces never share registry state, and test graphs get
        an isolated name (``{ns}_{test_graph}_control_plane``) so parallel
        test runs stay independent (#135, #139).
        """
        if self._registry_g is None:
            proj = self._get_proj()
            graph_name = getattr(proj, "graph_name", None)
            ns = self._namespace or ""
            if graph_name and graph_name.startswith(("tortoise_test_", "test_")):
                # Keep the test prefix so test-graph guards still apply.
                registry_name = f"{ns}_{graph_name}_control_plane" if ns else f"{graph_name}_control_plane"
            elif ns:
                registry_name = f"{ns}_control_plane"
            else:
                registry_name = "control_plane"
            self._registry_g = proj.db.select_graph(registry_name)
            self._ensure_registry_indexes()
        return self._registry_g

    def _ensure_registry_indexes(self) -> None:
        """Create indexes on registry graph labels (idempotent)."""
        g = self._registry_g
        if g is None:
            return
        indexes = [
            ("Team", "name"),
            ("Membership", "team_id"),
            ("Membership", "user_id"),
            ("APIKey", "team_id"),
            ("APIKey", "key_hash"),
            ("Invitation", "team_id"),
            ("Invitation", "token_hash"),
        ]
        for label, prop in indexes:
            try:
                g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                _logger.debug("Index may already exist: %s.%s", label, prop)

    # ── Core CRUD ─────────────────────────────────────────────────

    def create_point(self, kind: str, content: str, **props) -> dict:
        """Create a new Point node. Raises ValueError if kind is invalid.

        Set dedup=True for idempotent creation (matches by content hash).
        """
        self._validate_kind(kind)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "create_point() got unexpected keyword argument 'context'. "
                "Context has been removed. Use pointKind for filtering, "
                "anchors for EP scoping, extractedFrom for provenance. See #49."
            )

        # Calibration: pop credibility before storing as node property
        credibility = props.pop("credibility", None)
        # Always compute and store content hash — dedup flag only gates the
        # existing-point lookup, not hash persistence (fix #80).
        ch = _content_hash(content)
        props["content_hash"] = ch
        # Idempotency guard: dedup by content hash when requested
        dedup = props.pop("dedup", False)
        if dedup:
            ch = _content_hash(content)
            # P1 #49: dedup by content_hash + pointKind (NOT context, which is no longer written)
            existing = proj.g.query(
                "MATCH (n:Point {content_hash:$ch}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND n.pointKind = $kind "
                "RETURN n.id",
                params={"ch": ch, "kind": kind},
            ).result_set
            if existing:
                pid = existing[0][0]
                props["updatedAt"] = now
                # Existing point already stores content_hash — don't re-write it
                # (would make the `if props:` guard always truthy and bump
                # updatedAt on every dedup hit, #80 review).
                props.pop("content_hash", None)
                if credibility is not None:
                    _logger.warning(
                        "credibility=%r ignored — point %s already exists and dedup=True",
                        credibility, pid)
                if props:
                    self.update_point(pid, **props)
                return self.get_point(pid)

        # Issue #52 — warn when caller passes an explicit non-ULID id
        explicit_id = props.pop("id", None)
        if explicit_id is not None:
            if not _is_ulid(explicit_id):
                _logger.warning(
                    "create_point received non-ULID id=%r — canonical format is "
                    "<timestamp-hex>-<uuid12>. This will override the auto-generated ULID. "
                    "Prefer omitting 'id' to use auto-generated ULID.",
                    explicit_id,
                )
            pid = explicit_id
        else:
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
            "SET n.embedding = vecf32($embedding)",
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

    # ── Resolution helper (Issue #52) ──

    def resolve_id(self, id_str: str) -> dict | None:
        """Resolve any Point ID (legacy / numeric / ULID) to the canonical point.

        Returns the Point dict if found, None otherwise.

        Strategy:
        1. Exact match on Point.id
        2. If the id looks like a numeric reference, search by content/properties
           (best-effort — legacy numeric IDs may not have explicit mappings yet)

        Non-destructive — read-only operation.

        Limitations:
        - For legacy prefix IDs (letta-*, op-*, etc.) with no exact match,
          there is currently no migration mapping to a canonical ULID.
          This is a known gap covered by docs/migrations/id-normalization-plan.md.
        - The resolution is exact-id-first; fuzzy matching is future work.
        """
        proj = self._get_proj()

        # 1. Exact match
        rows = proj.g.query(
            "MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.status",
            params={"id": id_str},
        ).result_set
        if rows:
            return self.get_point(rows[0][0])

        # 2. If numeric, try finding a point whose properties reference it
        #    (best-effort — many numeric IDs are native node IDs and would have
        #     matched in step 1; this handles edge cases like internal refs)
        if id_str.isdigit():
            # Search for points whose content or any property contains the numeric ID
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.content CONTAINS $id_str "
                "RETURN n.id, n.content LIMIT 5",
                params={"id_str": id_str},
            ).result_set
            if rows:
                _logger.info(
                    "resolve_id: numeric %r not found as direct id; "
                    "returning best-match point %r", id_str, rows[0][0]
                )
                return self.get_point(rows[0][0])

        return None

    def update_point(self, id: str, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict.
        
        For :Object-labeled nodes, version is auto-incremented on every update.
        Status changes are validated against POINT_STATUS_VALUES.
        """
        proj = self._get_proj()

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "update_point() got unexpected keyword argument 'context'. "
                "Context has been removed. See #49."
            )

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
        """Atomically replace old Point with new — CORRECTS edge + outdated flag + edge transfer.

        Transfers all edges from the old point to the new point:
          - Operator edges (IMPL, NAND, hasPart) with idx
          - Plain structural edges (aboutSubject, aboutObject, aboutAction,
            aboutEvent, aboutPoint, aboutDocument, extractedFrom, etc.)
        Preserves edge type and idx (source vs target position).
        Leaves the old point outdated with only the CORRECTS edge from the new point.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # 1. Mark old outdated + create CORRECTS edge (same as invalidate)
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now",
            params={"id": old_id, "now": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "CREATE (a)-[:CORRECTS]->(b)",
            params={"new_id": new_id, "old_id": old_id},
        )

        # 2a. Transfer operator edges (IMPL, NAND, hasPart) — preserve provenance
        edges_result = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r]->(old:Point {id:$old_id}) "
            "RETURN op.id, type(r), r.idx",
            params={"old_id": old_id},
        )

        transferred = 0
        for row in edges_result.result_set:
            op_id, edge_type, idx = row[0], row[1], row[2]
            # Create new edge: operator → new point (same idx preserves source/target position)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$new_id}}) "
                f"CREATE (op)-[:{edge_type} {{idx:$idx}}]->(new)",
                params={"op_id": op_id, "new_id": new_id, "idx": idx},
            )
            # Delete old edge (match by idx for precision)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}})-[r:{edge_type} {{idx:$idx}}]->(old:Point {{id:$old_id}}) "
                f"DELETE r",
                params={"op_id": op_id, "idx": idx, "old_id": old_id},
            )
            transferred += 1

        # 2b. Transfer plain structural edges (#122) — about*, extractedFrom, wasDerivedFrom, etc.
        # These edges connect the Point to entities (Subject, Object, Source, etc.)
        structural_rels = [
            'aboutSubject', 'aboutObject', 'aboutAction', 'aboutEvent',
            'aboutPoint', 'aboutDocument', 'extractedFrom', 'wasDerivedFrom'
        ]
        for rel in structural_rels:
            struct_rows = proj.g.query(
                f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(target) "
                f"RETURN id(target), target.id, labels(target)",
                params={"old_id": old_id},
            ).result_set
            for row in struct_rows:
                target_graph_id = row[0]  # FalkorDB internal node id — exact match
                # Create new edge: new point → same target (MERGE = idempotent, no dupes)
                proj.g.query(
                    f"MATCH (new:Point {{id:$new_id}}), (t) WHERE id(t) = $tid "
                    f"MERGE (new)-[:{rel}]->(t)",
                    params={"new_id": new_id, "tid": target_graph_id},
                )
                # Delete old edge (match by exact internal node id)
                proj.g.query(
                    f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(t) WHERE id(t) = $tid "
                    f"DELETE r",
                    params={"old_id": old_id, "tid": target_graph_id},
                )
                transferred += 1

        return {
            "invalidated": True,
            "id": old_id,
            "corrected_by": new_id,
            "edges_transferred": transferred,
        }

    # ── Operators ─────────────────────────────────────────────────

    def create_operator(self, op_type: str, source_id: str, target_ids: list[str],
                        label: str | None = None) -> dict:
        """Create an operator Point with optional semantic label.

        Semantic-epistemic edge model (#7801):
          - op_type: IMPL or NAND (epistemic mechanism)
          - label: domain verb — "addresses", "hasPart", "opposes" (semantic layer)
          - Operator carries the label; IMPL/NAND edges carry confidence via EP.
        """
        if op_type not in ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', or a part/whole type, got {op_type!r}"
            )
        pid = ulid()
        inputs = [source_id] + list(target_ids)
        proj = self._get_proj()

        # Validate all source/target Points exist (fail loudly, not silently)
        existing = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id",
            params={"ids": inputs},
        ).result_set
        existing_ids = {row[0] for row in existing}
        missing = [i for i in inputs if i not in existing_ids]
        if missing:
            raise ValueError(f"Cannot create operator: Points {missing} do not exist")

        # Build operator node with label property (context is NOT written — P1 #49)
        extra_props = []
        params = {"id": pid, "op": op_type}
        if label:
            extra_props.append("label:$label")
            params["label"] = label
        props_clause = ", " + ", ".join(extra_props) if extra_props else ""
        proj.g.query(
            f"CREATE (o:Point {{id:$id, is_operator:true, op_type:$op{props_clause}}})",
            params=params,
        )
        # Ontology v2.1: map part/whole ops to hasPart, remove INPUT edges
        edge_type = "hasPart" if op_type not in ("IMPL", "NAND") else op_type
        for i, inp_id in enumerate(inputs):
            proj.g.query(
                f"MATCH (o:Point {{id:$oid}}), (s:Point {{id:$sid}}) "
                f"CREATE (o)-[:{edge_type} {{idx:$i}}]->(s)",
                params={"oid": pid, "sid": inp_id, "i": i},
            )
        # Draft → live lifecycle (#131): source point goes live when first edge created
        proj.g.query(
            "MATCH (s:Point {id:$sid}) SET s.status = 'live'",
            params={"sid": source_id},
        )
        result = self.get_point(pid)
        return result

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

    def query(self, kind: str | None = None,
              **filters) -> list[dict]:
        """Query points by pointKind and/or custom property filters.

        For confidence-aware queries, use tortoise_fts_query() with query=None
        for full-scan mode with EP annotation.
        """
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
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

    def paginated_query(self, kind: str | None = None,
                         skip: int = 0, limit: int = 20, **filters) -> dict:
        """Query points with pagination. Returns {results, total, hasMore}.
        """
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
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
            f"MATCH {pat} RETURN m.id, m.content, m.pointKind",
            params={"id": id},
        ).result_set
        return [
            {"id": r[0], "content": r[1], "pointKind": r[2]}
            for r in rows
        ]

    # ── Chain Integrity ───────────────────────────────────────────

    def check_structure(self) -> list[dict]:
        """Check Gate 0→4 chain integrity. Uses pack-aware kind expansion."""
        proj = self._get_proj()
        violations: list[dict] = []

        # Resolve kinds via pack registry (handles namespace prefixes)
        uc_kind = self._expand_kind("useCase")
        jtbd_kind = self._expand_kind("jobToBeDone")
        uj_kind = self._expand_kind("userJourney")
        wf_kind = self._expand_kind("workflow")
        req_kind = self._expand_kind("requirement")

        # Build IN clauses
        def kind_in(kinds):
            return ", ".join(f"'{k}'" for k in kinds)

        # useCase without parent JTBD
        ucs = proj.g.query(
            f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] RETURN uc.id, uc.uc_id"
        ).result_set
        for uc_id, uc_ref in ucs:
            parents = proj.g.query(
                f"MATCH (op:Point {{is_operator:true, op_type:'composedOf'}})"
                f"-[:hasPart]->(uc:Point {{id:$id}}), "
                f"(op)-[:hasPart]->(jtbd:Point) WHERE jtbd.pointKind IN [{kind_in(jtbd_kind)}] "
                f"RETURN jtbd.id",
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
            f"MATCH (uj:Point) WHERE uj.pointKind IN [{kind_in(uj_kind)}] RETURN uj.id, uj.covered_use_cases"
        ).result_set:
            if not covered:
                continue
            for uc_ref in covered.split(","):
                uc_ref = uc_ref.strip()
                if not proj.g.query(
                    f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] AND uc.uc_id=$ref RETURN count(uc) > 0",
                    params={"ref": uc_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_use_case_ref",
                        "id": uj_id,
                        "message": f"userJourney {uj_id} refs non-existent useCase {uc_ref}",
                    })

        # Workflow dangling JTBD refs
        for wf_id, enables in proj.g.query(
            f"MATCH (wf:Point) WHERE wf.pointKind IN [{kind_in(wf_kind)}] RETURN wf.id, wf.enables_jtbd"
        ).result_set:
            if not enables:
                continue
            for jtbd_ref in enables.split(","):
                jtbd_ref = jtbd_ref.strip()
                if not proj.g.query(
                    f"MATCH (j:Point) WHERE j.pointKind IN [{kind_in(jtbd_kind)}] AND j.jtbd_id=$ref RETURN count(j) > 0",
                    params={"ref": jtbd_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_jtbd_ref",
                        "id": wf_id,
                        "message": f"workflow {wf_id} refs non-existent JTBD {jtbd_ref}",
                    })

        # Requirement dangling Workflow refs
        for req_id, wf_ref in proj.g.query(
            f"MATCH (req:Point) WHERE req.pointKind IN [{kind_in(req_kind)}] RETURN req.id, req.enabled_workflow"
        ).result_set:
            if not wf_ref or wf_ref == "ALL":
                continue
            if not proj.g.query(
                f"MATCH (w:Point) WHERE w.pointKind IN [{kind_in(wf_kind)}] AND w.wf_id=$ref RETURN count(w) > 0",
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
            "RETURN n.id, n.content, n.pointKind, n.createdAt "
            "ORDER BY n.createdAt"
        ).result_set:
            violations.append({
                "type": "orphaned_draft",
                "id": row[0],
                "message": (
                    f"Draft point '{row[1][:80] if row[1] else ''}' "
                    f"of kind '{row[2] or 'unknown'}' has no edges "
                    f"(created {row[3] or 'unknown'})"
                ),
            })

        return violations

    def summarize_structure(self) -> dict:
        """Count points per Gate (by pointKind). Returns {gate: count, ..., total}.

        P1 #49: re-keyed from context strings (tortoise-wf-gate0..4) to pointKind
        (jobToBeDone, useCase, userJourney, workflow, requirement). Pre-existing
        experimental points that had context but no matching pointKind may show 0
        — expected under the #49 re-home (pointKind is the target vocabulary).
        """
        proj = self._get_proj()
        gates = [
            ("gate0_jtbds", "jobToBeDone"),
            ("gate1_use_cases", "useCase"),
            ("gate2_user_journeys", "userJourney"),
            ("gate3_workflows", "workflow"),
            ("gate4_requirements", "requirement"),
        ]
        result: dict[str, int] = {}
        for key, kind in gates:
            result[key] = proj.g.query(
                "MATCH (n:Point {pointKind:$k}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN count(n)",
                params={"k": kind},
            ).result_set[0][0]
        result["total"] = sum(result.values())
        return result

    # ── Taxonomy ─────────────────────────────────────────────────

    def taxonomy(self) -> dict[str, int]:
        """Count entities by node label. Returns {Point: N, Event: N, ...}."""
        from .taxonomy import taxonomy as _taxonomy
        return _taxonomy(self._get_proj())

    def list_pointkinds(self) -> list[dict]:
        """All pointKinds present in the graph with counts. Returns [{kind, count, pack}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND n.pointKind IS NOT NULL "
            "RETURN n.pointKind, count(n) ORDER BY count(n) DESC"
        ).result_set
        result: list[dict] = []
        for row in rows:
            kind = row[0]
            count = row[1]
            pack = kind.split(":", 1)[0] if ":" in kind else ""
            result.append({"kind": kind, "count": count, "pack": pack})
        return result

    def list_sources(self) -> list[dict]:
        """All Sources with point counts. Returns [{url, sourceKind, points}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source) "
            "OPTIONAL MATCH (p:Point)-[:extractedFrom]->(s) "
            "RETURN s.url, s.sourceKind, count(p) AS points "
            "ORDER BY points DESC"
        ).result_set
        return [
            {"url": row[0], "sourceKind": row[1], "points": row[2]}
            for row in rows
        ]

    def list_namespaces(self) -> list[dict]:
        """Installed pack namespaces with kind counts. Returns [{namespace, name, kind_count}]."""
        registry = _get_kind_expander()
        packs = registry.list_packs()
        return [
            {
                "namespace": p["namespace"],
                "name": p["name"],
                "kind_count": sum(p["kind_counts"].values()),
            }
            for p in packs
        ]

    def list_topics(self, entity_id: str) -> dict:
        """entityProfile lite for an entity. Returns {id, pointKind, context, neighbors, neighborCounts}."""
        from .taxonomy import list_topics as _list_topics
        return _list_topics(self._get_proj(), entity_id)

    # ── Bulk ──────────────────────────────────────────────────────

    def batch_create_points(self, points_list: list[dict]) -> list[dict]:
        """Create multiple points. Each dict needs {kind, content, **props}."""
        return [self.create_point(**p) for p in points_list]

    def file_decision(self, options: list[str], evidence: list[str],
                      choice: int) -> dict:
        """File a simple decision directly to the graph — no EP, no calibration,
        no research cycles. Creates decision + options + evidence + IMPL edges
        atomically. For low-stakes decisions where the answer is clear (#133).

        Args:
            options: list of option descriptions (e.g. ["JSON", "YAML"])
            evidence: list of evidence statements supporting the choice
            choice: 0-indexed index into options (the chosen option)

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
            status="live",
        )
        decision_id = decision["id"]

        # 2. Create option points + IMPL edges from decision
        option_ids = []
        for i, opt in enumerate(options):
            opt_point = self.create_point(
                "option",
                f"Option {i+1}: {opt}",
                status="live",  # options are targets, not sources — explicit live
            )
            option_ids.append(opt_point["id"])
            # IMPL edge: decision -> option ("decision considers option")
            self.create_operator("IMPL", decision_id, [opt_point["id"]])

        # 3. Create evidence points + IMPL edges to the chosen option
        evidence_ids = []
        chosen_id = option_ids[choice]
        for ev in evidence:
            ev_point = self.create_point(
                "evidence",
                ev,
            )
            evidence_ids.append(ev_point["id"])
            # IMPL edge: evidence -> chosen option ("evidence supports choice")
            self.create_operator("IMPL", ev_point["id"], [chosen_id])

        return {
            "decision_id": decision_id,
            "option_ids": option_ids,
            "evidence_ids": evidence_ids,
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self._get_proj().list_graphs()

    def _audit(self, team_id: str, actor_user_id: str | None,
                operation: str, **kwargs) -> None:
        """Log an audit event. No-op if audit logger not initialized."""
        if self._audit_logger is None:
            from .audit_events import AuditLogger
            self._audit_logger = AuditLogger()
        self._audit_logger.append(
            team_id=team_id,
            actor_user_id=actor_user_id,
            operation=operation,
            **kwargs,
        )

    def list_relations(self) -> list[dict]:
        """List all relation declarations across installed packs.

        Returns [{"pack": ..., "predicate": ..., "fromKind": ..., "toKind": ...,
        "mechanism": ...}]. Pack relations describe valid edge types between
        entity kinds — use for schema discovery.
        """
        return _get_kind_expander().list_relations()

    def close(self) -> None:
        """Close the underlying database connection and audit logger."""
        if self._audit_logger is not None:
            self._audit_logger.close()
            self._audit_logger = None
        if self._proj is not None:
            self._proj.close()
            self._proj = None
        self._registry_g = None

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

    def _select_subgraph(self, anchors: list[str], max_hops: int = 1,
                         rel_filter: str = "IMPL|NAND",
                         direction: str = "both") -> list[str]:
        """BFS subgraph selection from anchor Points to collect operator IDs.

        Delegates to the shared _bfs_select_operators in tortoise.analyze.
        """
        from .analyze import _bfs_select_operators
        proj = self._get_proj()
        result = _bfs_select_operators(proj, anchors, max_hops=max_hops,
                                        rel_filter=rel_filter, direction=direction)
        return list(result)

    def compute_confidence(self, factors=None, evidence=None,
                           anchors: list[str] | None = None,
                           max_hops: int = 1,
                           rel_filter: str = "IMPL|NAND",
                           direction: str = "both",
                           require_calibration: bool = False,
                           recency_decay: float | None = None) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

        Args:
            factors: operator IDs (list[str]) or factor tuples. If None, auto-extracts.
            evidence: optional {claim_id: (alpha, beta)} priors.
            anchors: list of Point IDs for BFS subgraph selection.
            max_hops: BFS expansion depth when using anchors (default 1).
            rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default).
            direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default).
            require_calibration: if True, raises CalibrationError when evidence points are uncalibrated.
            recency_decay: optional recency decay factor (default 0.95 from TORTOISE_EP_RECENCY_DECAY).
                T0 sources exempt; lower tiers get gentle decay. 1.0 = no decay.

        Precedence: factors > anchors > auto-extract-all.
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
        # Apply source-based credibility inheritance (with recency modulation #122)
        self._apply_source_inheritance(recency_decay=recency_decay)
        if evidence:
            self._evidence.update(evidence)
        # Calibration gate
        if require_calibration:
            from .exceptions import CalibrationError
            summary = self.calibrate_summary()
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
        elif anchors is not None:
            # BFS subgraph selection from anchor points
            operator_ids = self._select_subgraph(anchors, max_hops=max_hops,
                                                  rel_filter=rel_filter,
                                                  direction=direction)
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

    def _apply_source_inheritance(self, recency_decay: float | None = None):
        """Apply credibilityTier from Source nodes to Points via extractedFrom edge.
        
        Only activates when credibilityTier is explicitly set on the Source (NOT NULL).
        Sources without credibilityTier = no inheritance = neutral Beta(1,1).
        If a Point has multiple Sources, the highest tier (lowest number: T0 > T1 > ...) wins.

        Recency modulation (#122 Part 3): older sources get slightly reduced evidence
        weight via recency_decay (default 0.95 from TORTOISE_EP_RECENCY_DECAY env var).
        T0 sources (gold/meta-analysis) are exempt from decay. Lower tiers get gentle
        decay: effective_count *= recency_decay ** years_since_ingested.
        """
        import os
        from datetime import datetime, timezone
        if recency_decay is None:
            recency_decay = float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95"))
        proj = self._get_proj()
        tier_map = {"T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1)}
        tier_order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
        
        where = "WHERE s.credibilityTier IS NOT NULL AND (n.baseline_set IS NULL OR n.baseline_set = false)"
        params = {}
        
        rows = proj.g.query(
            f"MATCH (n:Point)-[:extractedFrom]->(s:Source) {where} "
            "RETURN n.id, s.credibilityTier, s.ingestedAt",
            params=params,
        ).result_set
        
        # Group by Point ID, select highest tier (lowest number) for each Point
        from collections import defaultdict
        point_data = defaultdict(list)
        for pid, tier, ingested in rows:
            point_data[pid].append((tier, ingested))

        now_ts = datetime.now(timezone.utc).timestamp()
        
        for pid, entries in point_data.items():
            tiers = [e[0] for e in entries]
            best_tier = min(tiers, key=lambda t: tier_order.get(t, 99))
            alpha, beta = tier_map.get(best_tier, (1, 1))

            # Recency modulation: T0 exempt, others decay gently (#122)
            if best_tier != "T0" and recency_decay < 1.0:
                # Use the most recent ingestedAt for this source tier
                ingested_ts = None
                for t, ingested in entries:
                    if t == best_tier and ingested:
                        try:
                            dt = datetime.fromisoformat(ingested.replace("Z", "+00:00"))
                            ts = dt.timestamp()
                            if ingested_ts is None or ts > ingested_ts:
                                ingested_ts = ts
                        except (ValueError, TypeError):
                            pass
                if ingested_ts is not None:
                    years = max(0, (now_ts - ingested_ts) / (365.25 * 86400))
                    decay = recency_decay ** years
                    # Decay pulls toward Beta(1,1): alpha' = 1 + (alpha-1)*decay
                    # Stable facts (high alpha from multiple sources) stay strong
                    alpha = 1.0 + (alpha - 1.0) * decay
                    beta = 1.0 + (beta - 1.0) * decay

            self.set_point_baseline(pid, alpha, beta)

    def calibrate_summary(self) -> list[dict]:
        """Audit graph calibration state. Returns per-point guidance.
        
        Checks baseline_set flag on non-operator Points. For uncalibrated
        points, traverses extractedFrom→Source to check for inherited credibilityTier.
        """
        proj = self._get_proj()
        where = "WHERE (n.is_operator IS NULL OR n.is_operator = false)"
        params = {}
        
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
        # P1 #49: use wing property only — context is deprecated
        if wing:
            props["wing"] = wing
        return self.create_point("diary", entry, **props)

    def diary_read(self, agent_name: str, last_n: int = 10,
                   wing: str | None = None) -> list[dict]:
        """Read recent diary entries for an agent, newest first."""
        proj = self._get_proj()
        if wing:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "wing": wing, "lim": last_n},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "lim": last_n},
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
        kind_filter filters by n.pointKind.
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
            clauses.append("n.pointKind = $kf")
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
            # Confidence formula (#22): exact match → 1.0, partial match →
            # [0.5, 1.0) via length ratio, smoothed to avoid scale collapse.
            # len(q)/len(content) alone would give 0.001 for 1-char in 1000-char
            # doc and 0.5 for 5-char in 10-char — not comparable. The 0.5 offset
            # ensures all substring matches score ≥ 0.5, reserving [0, 0.5) for
            # the hybrid fallback path (which has no substring match at all).
            if content.lower() == q_lower:
                confidence = 1.0
            else:
                ratio = len(q) / len(content)
                confidence = round(0.5 + 0.5 * ratio, 4)
            results.append({"id": pid, "name": content, "kind": kind or "", "confidence": confidence})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:limit]

        # Hybrid fallback if no string matches (Phase 0, #7748)
        if not results:
            fts_results = self.tortoise_fts_query(q, limit=limit)
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
        *,
        entity_type: str = "point",
        min_confidence: float = 0.0,
        order_by: str = "relevance",
        limit: int = 10,
        threshold: float = 0.0,
        relationship_filter: str | None = None,
        traversal_path: str | None = None,
    ) -> list[dict]:
        """Hybrid search with RRF fusion + EP annotation.

        entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
        Full-scan mode: omit query, set kind → all Points of that kind.
        Best-match mode: provide query → RRF fusion of FTS + vector + structural.

        Point results annotated with EP breakdown (confidence_mean + evidence + contention).
        Non-Point entities skip EP annotation.
        min_confidence defaults to 0.0 (no filter).

        relationship_filter: 'predicate:target_id' — only return points connected to
            target_id via an operator with label=predicate (e.g., 'addresses:customerSegment-1').
        traversal_path: 'FromKind→ToKind' — only return points that participate in a
            pack-declared relation chain (e.g., 'Product→Feature'). Resolved via pack registry.
        """
        from .search_engine import (
            classify_query, degradation_chain, rrf_fusion,
            annotate_ep_batch, get_relationships, fallback_tfidf,
            SearchResult, SearchScores,
            filter_by_relationship, filter_by_traversal_predicate,
        )

        if entity_type not in ("point", "event", "subject", "document", "object", "operator", "source"):
            raise ValueError(f"entity_type must be 'point', 'event', 'subject', 'document', 'object', 'operator', or 'source', got {entity_type!r}")
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
        label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
        # Operator: Point nodes with is_operator=true, kind=op_type
        # Source: Source nodes, kind=sourceKind
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind", "document": "documentKind", "object": "objectKind", "operator": "op_type", "source": "sourceKind"}[entity_type]

        # 1. Classify query → determine active strategies
        strategies = classify_query(query, kind)
        is_full_scan = (query is None and kind is not None)

        # Expand kind early for pack-aware structural query + kind filter
        expanded_kinds = self._expand_kind(kind) if kind else None

        # 2. Get query vector if needed (all core entity types now have embeddings #7845)
        query_vec = None
        if strategies.get("vector") and query and query.strip():
            try:
                from .embeddings import EmbeddingModel
                model = EmbeddingModel.get()
                if model:
                    query_vec = model.encode([query])[0].tolist()
            except Exception:
                pass  # Graceful — vector strategy will degrade

        # 3. Run retrieval with degradation
        is_embedded = getattr(proj, '_is_embedded', True)
        # Full-scan mode: no truncation — return ALL Points in context (#7811 completeness)
        str_limit = limit * 2 if not is_full_scan else 100000
        raw_results = degradation_chain(
            graph, query, kind, query_vec, strategies,
            entity_type=entity_type, limit=str_limit,
            is_embedded=is_embedded,
        )

        if not raw_results:
            # All strategies failed — fallback to in-memory TF-IDF (Point only)
            if query and entity_type == "point":
                points = self.query(kind=kind)
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

        # 5. Apply kind filter BEFORE truncating (skip if structural-only already filtered)
        result_ids = list(fused.keys())
        if entity_type == "source":
            id_field = "url"
        elif entity_type == "event":
            id_field = "eventId"
        else:
            id_field = "id"
        # Graph label for MATCH (operators are Point nodes with is_operator=true)
        graph_label = "Point" if entity_type == "operator" else label

        if kind and query is not None and result_ids:
            expanded = expanded_kinds
            kind_ids = set()
            extra_clause = "AND n.is_operator = true" if entity_type == "operator" else ""
            try:
                if len(expanded) == 1:
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} = $kind {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params={"kind": expanded[0], "ids": result_ids},
                    ).result_set
                else:
                    placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                    params_dict: dict[str, Any] = {"ids": result_ids}
                    for i, k in enumerate(expanded):
                        params_dict[f"kind_{i}"] = k
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} IN [{', '.join(placeholders)}] {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params=params_dict,
                    ).result_set
                kind_ids = {row[0] for row in kind_rows}
            except Exception:
                kind_ids = set(result_ids)  # Pass-through on error
            result_ids = [pid for pid in result_ids if pid in kind_ids]

        # 5b. Apply relationship_filter (predicate:target_id format)
        if relationship_filter and result_ids:
            parts = relationship_filter.split(":", 1)
            if len(parts) == 2:
                pred, tid = parts[0].strip(), parts[1].strip()
                if pred and tid:
                    result_ids = filter_by_relationship(
                        graph, result_ids, pred, tid,
                        entity_type=entity_type, id_field=id_field,
                    )
                else:
                    _logger.warning("Invalid relationship_filter format: %s", relationship_filter)
            else:
                _logger.warning(
                    "relationship_filter must be 'predicate:target_id', got: %s",
                    relationship_filter,
                )

        # 5c. Apply traversal_path (e.g., 'Product→Feature') — resolve via pack registry
        if traversal_path and result_ids:
            resolved = self._resolve_traversal_path(traversal_path)
            if resolved:
                pred = resolved["predicate"]
                result_ids = filter_by_traversal_predicate(
                    graph, result_ids, pred,
                    entity_type=entity_type, id_field=id_field,
                )
            else:
                _logger.warning(
                    "traversal_path %r could not be resolved to a pack relation",
                    traversal_path,
                )

        # Truncate AFTER filtering
        result_ids = result_ids[:limit]

        # 6. EP annotation (Point only)
        ep_breakdowns = annotate_ep_batch(graph, result_ids) if entity_type == "point" else {}

        # 7. Fetch entity content in BATCH (not N+1)
        entity_data: dict[str, dict] = {}
        try:
            if entity_type == "point":
                rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.content, n.pointKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    pid = row[0]
                    entity_data[pid] = {
                        "content": row[1],
                        "kind": row[2],
                    }
            elif entity_type == "event":
                rows = graph.query(
                    "MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject, n.eventKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    eid = row[0]
                    entity_data[eid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "subject":
                rows = graph.query(
                    "MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "document":
                rows = graph.query(
                    "MATCH (n:Document) WHERE n.id IN $ids "
                    "RETURN n.id, n.title, n.documentKind, n.topics, n.summary, "
                    "n.sessionId, n.eventId, n.sourcePath",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    did = row[0]
                    entity_data[did] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        "topics": row[3] or [],
                        "summary": row[4] or "",
                        "sessionId": row[5] or "",
                        "eventId": row[6] or "",
                        "sourcePath": row[7] or "" if len(row) > 7 else "",
                    }
            elif entity_type == "object":
                rows = graph.query(
                    "MATCH (n:Object) WHERE n.id IN $ids RETURN n.id, n.name, n.objectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "operator":
                rows = graph.query(
                    "MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, n.label, n.op_type",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",  # label is searchable text
                        "kind": row[2] or "",    # op_type is kind
                    }
            elif entity_type == "source":
                rows = graph.query(
                    "MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.sourceKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
        except Exception:
            _logger.warning("Batch content fetch failed — returning results with minimal metadata")
            for pid in result_ids:
                entity_data[pid] = {"content": "", "kind": ""}

        # 7.5. Fetch relationships for result Points (Point only)
        point_relationships = get_relationships(graph, result_ids) if entity_type == "point" else {}

        # 8. Build SearchResult objects, filter, and order
        results = []
        for pid in result_ids:
            pt = entity_data.get(pid)
            if not pt:
                continue
            content, pt_kind = pt["content"], pt["kind"]
            ep = ep_breakdowns.get(pid) if entity_type == "point" else None
            # #125 capture metadata (document entity_type)
            cap_topics = pt.get("topics", [])
            cap_summary = pt.get("summary", "")
            cap_session = pt.get("sessionId", "")
            cap_event = pt.get("eventId", "")
            cap_source_path = pt.get("sourcePath", "")  # #167

            # Apply min_confidence filter (Point only; non-Point always pass)
            if entity_type == "point" and ep and ep.confidence_mean < min_confidence:
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
                scores=scores,
                match_source=match_source,
                ep=ep,
                relationships=point_relationships.get(pid, []),
                topics=cap_topics,
                summary=cap_summary,
                session_id=cap_session,
                event_id=cap_event,
                source_path=cap_source_path,
            )
            results.append(result)

        # 9. Order results
        if order_by == "confidence":
            results.sort(
                key=lambda r: r.ep.confidence_mean if r.ep else 0.0,
                reverse=True,
            )
        # Default: RRF relevance order (already in fused order)

        return [r.to_dict() for r in results[:limit]]

    # ── Multi-tenancy (#7001) ─────────────────────────────────

    # ── Control Plane: Team CRUD ───────────────────────────────────

    def team_create(self, name: str, *, idempotency_key: str | None = None) -> dict:
        """Create a team with its own graph namespace.

        Writes to the control_plane registry graph. Creates a tenant
        graph (team_{name}) for Point/Operator storage.

        Returns {name, graph_name, api_key, id}.
        """
        import re, uuid
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        # Input validation
        if not name or not name.strip():
            raise ControlPlaneError("Team name must not be empty")
        if len(name) > 64:
            raise ControlPlaneError("Team name must be 64 characters or fewer")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
            raise ControlPlaneError(
                f"Invalid team name: {name!r}. Use alphanumeric, hyphens, underscores."
            )

        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        graph_name = f"team_{name}"
        proj = self._get_proj()
        reg = self._get_registry()
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency — check registry graph for existing team
        if idempotency_key:
            existing = reg.query(
                "MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name",
                params={"ik": idempotency_key},
            ).result_set
            if existing:
                row = existing[0]
                return {"name": name, "graph_name": graph_name,
                        "api_key": api_key, "id": row[0],
                        "existing": True}

        # Duplicate name check
        dup = reg.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": name},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(f"Team {name!r} already exists")

        tid = ulid()
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
            "graph_name:$gn, createdAt:$now, tier:'free'})",
            params={"id": tid, "name": name, "key": key_hash,
                    "gn": graph_name, "now": now},
        )
        if idempotency_key:
            reg.query(
                "MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik",
                params={"id": tid, "ik": idempotency_key},
            )
        try:
            team_graph = proj.db.select_graph(graph_name)
            team_graph.query(
                "CREATE (:TeamMeta {name:$name, created:$now})",
                params={"name": name, "now": now},
            )
        except Exception:
            try:
                reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t",
                          params={"id": tid})
            except Exception:
                pass
            raise

        self._audit(tid, None, "team_create", resource_type="team", resource_id=tid)
        return {"name": name, "graph_name": graph_name, "api_key": api_key, "id": tid}

    def team_get(self, team_id: str) -> dict | None:
        """Get a team by ID. Returns None if not found."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        return rows[0][0] if rows else None

    def team_list(self) -> list[dict]:
        """List all teams."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team) RETURN properties(t) ORDER BY t.createdAt"
        ).result_set
        return [r[0] for r in rows]

    def team_update(self, team_id: str, **fields) -> dict:
        """Update mutable team fields."""
        from .exceptions import ControlPlaneError
        allowed = {
            "name", "tier", "stripe_customer_id", "subscription_id",
            "backup_enabled", "max_users", "max_teams", "max_graphs",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ControlPlaneError(f"Invalid team fields: {invalid}")
        reg = self._get_registry()
        reg.query(
            "MATCH (t:Team {id:$id}) SET t += $fields",
            params={"id": team_id, "fields": fields},
        )
        self._audit(team_id, None, "team_update", resource_type="team",
                     resource_id=team_id)
        return self.team_get(team_id) or {}

    def team_delete(self, team_id: str, *, confirmation: str) -> dict:
        """Delete a team and all associated control-plane entities.

        Cascading: Membership, APIKey, Invitation nodes are deleted.
        Tenant graphs are dropped (best-effort — FalkorDBLite may skip).
        Postgres audit_events are preserved (immutable).

        Requires confirmation matching the team name.
        """
        from .exceptions import ControlPlaneError
        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if confirmation != team.get("name", ""):
            raise ControlPlaneError(
                "Confirmation must match team name exactly"
            )

        reg = self._get_registry()
        # Cascade delete: Membership, APIKey, Invitation
        reg.query(
            "MATCH (m:Membership {team_id:$tid}) DETACH DELETE m",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (k:APIKey {team_id:$tid}) DETACH DELETE k",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (i:Invitation {team_id:$tid}) DETACH DELETE i",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (t:Team {id:$id}) DETACH DELETE t",
            params={"id": team_id},
        )

        # Best-effort tenant graph deletion
        graph_name = team.get("graph_name", f"team_{team.get('name', '')}")
        proj = self._get_proj()
        try:
            if hasattr(proj.db, 'delete_graph'):
                proj.db.delete_graph(graph_name)
            else:
                _logger.debug("delete_graph not available (FalkorDBLite) — skipping")
        except Exception:
            _logger.debug("Failed to delete tenant graph %s — skipping", graph_name)

        self._audit(team_id, None, "team_delete", resource_type="team",
                     resource_id=team_id)
        return {"deleted": True, "team_id": team_id}

    def migrate_teams_to_registry(self) -> dict:
        """One-shot: move Team nodes from tortoise graph to control_plane graph.

        Idempotent — running twice produces the same state.
        Existing Team nodes in the tortoise graph are marked as outdated.
        """
        proj = self._get_proj()
        reg = self._get_registry()
        teams = proj.g.query("MATCH (t:Team) RETURN properties(t)").result_set
        migrated, skipped = 0, 0
        for row in teams:
            team = row[0]
            name = team.get("name", "")
            # Check if already in registry
            existing = reg.query(
                "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
                params={"name": name},
            ).result_set[0][0]
            if existing:
                skipped += 1
                continue
            reg.query(
                "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
                "graph_name:$gn, createdAt:$now})",
                params={
                    "id": team.get("id", ulid()),
                    "name": name,
                    "key": team.get("api_key", ""),
                    "gn": team.get("graph_name", f"team_{name}"),
                    "now": team.get("createdAt", ""),
                },
            )
            migrated += 1
        if migrated > 0:
            proj.g.query("MATCH (t:Team) SET t.status = 'outdated'")
        return {"migrated": migrated, "skipped": skipped}

    # ── Control Plane: Membership CRUD ─────────────────────────────

    def membership_create(self, team_id: str, user_id: str, role: str) -> dict:
        """Add a user to a team with a given role.

        Validates role, team existence, and max_users constraint.
        Creates BELONGS_TO edge to Team.
        """
        from datetime import datetime, timezone
        from .exceptions import ControlPlaneError

        if role not in ("owner", "admin"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner' or 'admin'."
            )

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        # Check max_users constraint
        max_users = team.get("max_users")
        if max_users is not None:
            reg = self._get_registry()
            count = reg.query(
                "MATCH (m:Membership {team_id:$tid}) RETURN count(m)",
                params={"tid": team_id},
            ).result_set[0][0]
            if count >= max_users:
                raise ControlPlaneError(
                    f"Team at max users ({max_users}). Upgrade to add more."
                )

        mid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        reg = self._get_registry()
        reg.query(
            "CREATE (m:Membership {id:$id, user_id:$uid, team_id:$tid, "
            "role:$role, joinedAt:$now})",
            params={"id": mid, "uid": user_id, "tid": team_id,
                    "role": role, "now": now},
        )
        # Create BELONGS_TO edge
        reg.query(
            "MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) "
            "CREATE (m)-[:BELONGS_TO]->(t)",
            params={"mid": mid, "tid": team_id},
        )

        self._audit(team_id, user_id, "membership_create",
                     resource_type="membership", resource_id=mid)
        return {"id": mid, "team_id": team_id, "user_id": user_id, "role": role}

    def membership_get(self, membership_id: str) -> dict | None:
        """Get a membership by ID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {id:$id}) RETURN properties(m)",
            params={"id": membership_id},
        ).result_set
        return rows[0][0] if rows else None

    def membership_list(self, team_id: str) -> list[dict]:
        """List all memberships for a team."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid}) RETURN properties(m)",
            params={"tid": team_id},
        ).result_set
        return [r[0] for r in rows]

    def membership_update_role(self, membership_id: str,
                                new_role: str) -> dict:
        """Update a membership's role."""
        from .exceptions import ControlPlaneError
        if new_role not in ("owner", "admin"):
            raise ControlPlaneError(
                f"Invalid role {new_role!r}. Must be 'owner' or 'admin'."
            )
        m = self.membership_get(membership_id)
        if m is None:
            raise ControlPlaneError(f"Membership {membership_id!r} not found")
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) SET m.role = $role",
            params={"id": membership_id, "role": new_role},
        )
        self._audit(m["team_id"], m["user_id"], "membership_update_role",
                     resource_type="membership", resource_id=membership_id)
        return self.membership_get(membership_id) or {}

    def membership_delete(self, membership_id: str) -> dict:
        """Delete a membership. Idempotent."""
        m = self.membership_get(membership_id)
        if m is None:
            return {"deleted": False, "reason": "not found"}
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) DETACH DELETE m",
            params={"id": membership_id},
        )
        self._audit(m["team_id"], m["user_id"], "membership_delete",
                     resource_type="membership", resource_id=membership_id)
        return {"deleted": True, "membership_id": membership_id}

    # ── Control Plane: APIKey CRUD ─────────────────────────────────

    def _verify_hashed_lookup(self, label: str, prop: str, plaintext: str) -> list[dict]:
        """Verify a plaintext secret against stored salted hashes in the registry.

        hash_api_key() embeds a per-key random salt ("salt:hash"), so we can
        NOT look up by exact hash match — the lookup hash would never equal the
        stored hash (same root cause as #130). Instead fetch all candidate
        rows of the label and verify each stored hash against the plaintext.
        The registry is small (teams × keys × invites), so a scan is fine.
        """
        from tortoise.auth import verify_api_key
        reg = self._get_registry()
        rows = reg.query(
            f"MATCH (n:{label}) RETURN n.{prop}, properties(n)"
        ).result_set
        out = []
        for stored_hash, props in rows:
            if verify_api_key(plaintext, stored_hash):
                out.append(props)
        return out

    def apikey_create(self, team_id: str, created_by: str) -> dict:
        """Generate an API key for a team.

        Stores SHA-256 hash (never plaintext). Plaintext returned once.
        """
        import uuid
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        key_prefix = api_key[:10]
        kid = ulid()
        now = datetime.now(timezone.utc).isoformat()

        reg = self._get_registry()
        reg.query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:$cb, created_at:$now})",
            params={"id": kid, "tid": team_id, "kh": key_hash,
                    "kp": key_prefix, "cb": created_by, "now": now},
        )
        # BELONGS_TO edge
        reg.query(
            "MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) "
            "CREATE (k)-[:BELONGS_TO]->(t)",
            params={"kid": kid, "tid": team_id},
        )

        self._audit(team_id, created_by, "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"id": kid, "key_prefix": key_prefix, "api_key": api_key,
                "team_id": team_id, "created_at": now}

    def apikey_list(self, team_id: str) -> list[dict]:
        """List API keys for a team (no plaintext or hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) "
            "RETURN k.id, k.key_prefix, k.created_by, k.created_at, "
            "k.last_used_at, k.revoked_at",
            params={"tid": team_id},
        ).result_set
        keys = []
        for r in rows:
            keys.append({
                "id": r[0], "key_prefix": r[1], "created_by": r[2],
                "created_at": r[3], "last_used_at": r[4], "revoked_at": r[5],
            })
        return keys

    def apikey_revoke(self, key_id: str) -> dict:
        """Revoke an API key (soft delete — sets revoked_at). Idempotent."""
        from datetime import datetime, timezone
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.team_id",
            params={"id": key_id},
        ).result_set
        if not rows:
            return {"revoked": False, "reason": "not found"}
        if rows[0][0] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        now = datetime.now(timezone.utc).isoformat()
        reg.query(
            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
        self._audit(rows[0][1], None, "apikey_revoke",
                     resource_type="apikey", resource_id=key_id)
        return {"revoked": True, "key_id": key_id, "revoked_at": now}

    def apikey_verify(self, key_plaintext: str) -> dict | None:
        """Verify an API key against stored hashes.

        Returns {team_id, key_id} if valid, None if not found or revoked.
        Uses salted-hash verification (per-key salt means exact-hash lookup
        never matches — see #130, #139).
        """
        matches = [
            p for p in self._verify_hashed_lookup("APIKey", "key_hash", key_plaintext)
            if p.get("revoked_at") is None
        ]
        if matches:
            return {"team_id": matches[0]["team_id"], "key_id": matches[0]["id"]}
        return None

    # ── Control Plane: Invitation CRUD ─────────────────────────────

    def invitation_create(self, team_id: str, email: str, role: str,
                          created_by: str) -> dict:
        """Create an invitation with 7-day expiry.

        Token is hashed for storage; plaintext returned once.
        """
        import uuid
        from datetime import datetime, timedelta, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if role not in ("owner", "admin"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner' or 'admin'."
            )

        # Reject duplicate pending invitations for same email+team
        reg = self._get_registry()
        dup = reg.query(
            "MATCH (i:Invitation {team_id:$tid, email:$email}) "
            "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status <> 'revoked') "
            "RETURN count(i) > 0",
            params={"tid": team_id, "email": email},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(
                f"Pending invitation already exists for {email} in this team"
            )

        token = str(uuid.uuid4())
        token_hash = hash_api_key(token)
        iid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        reg.query(
            "CREATE (i:Invitation {id:$id, team_id:$tid, email:$email, "
            "role:$role, token_hash:$th, created_by:$cb, "
            "created_at:$now, expires_at:$exp, accepted_at:null})",
            params={"id": iid, "tid": team_id, "email": email,
                    "role": role, "th": token_hash, "cb": created_by,
                    "now": now, "exp": expires_at},
        )
        # FOR_TEAM edge
        reg.query(
            "MATCH (i:Invitation {id:$iid}), (t:Team {id:$tid}) "
            "CREATE (i)-[:FOR_TEAM]->(t)",
            params={"iid": iid, "tid": team_id},
        )

        self._audit(team_id, created_by, "invitation_create",
                     resource_type="invitation", resource_id=iid)
        return {"id": iid, "email": email, "role": role,
                "expires_at": expires_at, "token": token}

    def invitation_list(self, team_id: str) -> list[dict]:
        """List invitations for a team (no token hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {team_id:$tid}) "
            "RETURN i.id, i.email, i.role, i.created_by, i.created_at, "
            "i.expires_at, i.accepted_at, i.status",
            params={"tid": team_id},
        ).result_set
        invs = []
        for r in rows:
            invs.append({
                "id": r[0], "email": r[1], "role": r[2],
                "created_by": r[3], "created_at": r[4],
                "expires_at": r[5], "accepted_at": r[6], "status": r[7],
            })
        return invs

    def invitation_get_by_token(self, token_plaintext: str) -> dict | None:
        """Look up an invitation by its plaintext token (salted-hash verify)."""
        matches = self._verify_hashed_lookup("Invitation", "token_hash", token_plaintext)
        return matches[0] if matches else None

    def invitation_accept(self, invitation_id: str, user_id: str) -> dict:
        """Accept an invitation and create a membership.

        Checks expiry and single-use (not already accepted).
        """
        from datetime import datetime, timezone
        from .exceptions import ControlPlaneError

        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            raise ControlPlaneError(f"Invitation {invitation_id!r} not found")

        expires_at = inv.get("expires_at", "")
        now = datetime.now(timezone.utc)
        if expires_at:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if now > exp:
                raise ControlPlaneError("Invitation has expired")

        if inv.get("accepted_at"):
            raise ControlPlaneError("Invitation already accepted")

        if inv.get("status") == "revoked":
            raise ControlPlaneError("Invitation has been revoked")

        # Accept: mark as accepted + create membership
        now_iso = now.isoformat()
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now",
            params={"id": invitation_id, "now": now_iso},
        )

        membership = self.membership_create(
            team_id=inv["team_id"],
            user_id=user_id,
            role=inv.get("role", "admin"),
        )

        self._audit(inv["team_id"], user_id, "invitation_accept",
                     resource_type="invitation", resource_id=invitation_id)
        return {"membership_id": membership["id"],
                "team_id": inv["team_id"], "accepted_at": now_iso}

    def invitation_get_by_id(self, invitation_id: str) -> dict | None:
        """Get an invitation by its ULID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN properties(i)",
            params={"id": invitation_id},
        ).result_set
        return rows[0][0] if rows else None

    def invitation_revoke(self, invitation_id: str) -> dict:
        """Revoke an invitation (soft delete). Idempotent."""
        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            return {"revoked": False, "reason": "not found"}
        if inv.get("status") == "revoked":
            return {"revoked": True, "already": True,
                    "invitation_id": invitation_id}
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'",
            params={"id": invitation_id},
        )
        self._audit(inv["team_id"], None, "invitation_revoke",
                     resource_type="invitation", resource_id=invitation_id)
        return {"revoked": True, "invitation_id": invitation_id}

    def cleanup_expired_invitations(self) -> dict:
        """Mark expired invitations as 'expired' status.

        Returns count of cleaned invitations.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation) "
            "WHERE i.expires_at < $now AND i.accepted_at IS NULL "
            "AND (i.status IS NULL OR i.status <> 'expired') "
            "SET i.status = 'expired' "
            "RETURN count(i)",
            params={"now": now},
        ).result_set
        count = rows[0][0] if rows else 0
        return {"cleaned": count}

    # ── Helpers ───────────────────────────────────────────────────

    def _expand_kind(self, kind: str) -> list[str]:
        """Expand kind via subclassOf + equivalentTo for Cypher IN clause.

        Uses PackRegistry.expand_kind(). Registry is loaded once and cached.
        Returns [kind] if no packs loaded or kind is unknown.
        """
        return _get_kind_expander().expand_kind(kind)

    def _resolve_traversal_path(self, path: str) -> dict | None:
        """Resolve 'Product→Feature' to {predicate, fromKind, toKind} from pack registry.

        Matches against pack-declared relations — fromKind/toKind suffixes
        (e.g., 'product-strategy:product' matches 'Product' via kind name 'product').
        Returns None if no matching relation found.
        """
        segments = [s.strip() for s in path.split("→")]
        if len(segments) < 2:
            # Hint: user may have used ASCII '->' instead of Unicode '→'
            if "->" in path:
                _logger.warning(
                    "traversal_path uses ASCII '->' — use Unicode '→' instead "
                    "(e.g., 'Product→Feature')"
                )
            return None

        registry = _get_kind_expander()
        relations = registry.list_relations()
        if not relations:
            return None

        from_name, to_name = segments[0].strip(), segments[1].strip()

        for rel in relations:
            if "fromKind" not in rel or "toKind" not in rel:
                continue
            fk = rel["fromKind"]
            tk = rel["toKind"]
            # Extract kind name after the namespace prefix
            fk_name = fk.split(":", 1)[-1] if ":" in fk else fk
            tk_name = tk.split(":", 1)[-1] if ":" in tk else tk
            # Match case-insensitively against path segments
            if fk_name.lower() == from_name.lower() and tk_name.lower() == to_name.lower():
                return {"predicate": rel["predicate"], "fromKind": fk, "toKind": tk}

        return None

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

    def create_event(self, name: str, eventKind: str, **props) -> dict:
        """Create an Event node.

        If aboutSubject or aboutObject are provided in **props, they are extracted
        and wired as graph edges (Event)-[:aboutSubject]->(Subject) and
        (Event)-[:aboutObject]->(Object), rather than stored as string properties.
        """
        eid = self.ulid()
        about_subject = props.pop("aboutSubject", None)
        about_object = props.pop("aboutObject", None)
        result = self._create_entity("Event", eid, {"eventId": eid, "name": name, "eventKind": eventKind, "eventStatus": "scheduled", **props}, "EventRecorded")
        proj = self._get_proj()
        if about_subject:
            proj.create_about_edge(eid, about_subject, "aboutSubject")
            # Only name-resolve if it looks like a plain name, not an ID
            if isinstance(about_subject, str) and not _is_ulid(about_subject):
                proj._create_about_edges(eid, about_subject)
        if about_object:
            proj.create_about_edge(eid, about_object, "aboutObject")
            if isinstance(about_object, str) and not _is_ulid(about_object):
                proj._create_about_edges(eid, about_object)
        return result

    def create_document(self, title: str, documentKind: str, **props) -> dict:
        did = self.ulid()
        return self._create_entity("Document", did, {"title": title, "documentKind": documentKind, "objectKind": "document", "status": "draft", **props}, "DocumentCreated")

    def create_source(self, url: str, sourceKind: str, **props) -> dict:
        return self._create_entity("Source", url, {"url": url, "sourceKind": sourceKind, "ingestedAt": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), **props}, "SourceCreated")

    # ── Entity Derivation (#122 Part 2) ──────────────────────────

    def create_derivation(self, src_id: str, dst_id: str) -> dict:
        """Create a wasDerivedFrom edge: (dst)-[:wasDerivedFrom]->(src).

        PROV-O entity derivation — dst was derived from src. Distinct from
        extractedFrom (claim provenance) — wasDerivedFrom is Object→Object
        entity derivation.
        """
        proj = self._get_proj()
        ok = proj.create_edge(dst_id, src_id, "wasDerivedFrom")
        return {"derived": ok, "src": src_id, "dst": dst_id}

    # ── Reputation (#122 Part 4) ─────────────────────────────────

    def compute_reputation(self, subject_id: str) -> dict:
        """Derive reputation score for a Subject from event outcomes.

        Traverses: Subject -[:performs]-> Event -[:IMPL|NAND]-> Point
        Aggregates success/failure from direct event outcomes.
        Returns derived score (NOT stored).

        Returns {mean, total_events, impl_count, nand_count, alpha, beta, outcomes}.
        """
        proj = self._get_proj()
        # Direct: Event connects directly to claim Points via IMPL/NAND
        # (Operators connect ONLY epistemic targets per ONTOLOGY: Event→Point, Point→Point)
        impl_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:IMPL]->(p:Point) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND (s.id = $sid OR s.name = $sid) "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set
        nand_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:NAND]->(p:Point) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND (s.id = $sid OR s.name = $sid) "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set

        # Collect outcomes
        outcomes: list[dict] = []
        for row in impl_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "IMPL"})
        for row in nand_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "NAND"})

        total = len(outcomes)
        impl_count = sum(1 for o in outcomes if o["outcome"] == "IMPL")
        nand_count = sum(1 for o in outcomes if o["outcome"] == "NAND")

        if total == 0:
            return {"mean": 0.5, "total_events": 0, "impl_count": 0, "nand_count": 0,
                    "alpha": 1.0, "beta": 1.0, "outcomes": []}

        # Simple Beta reputation: IMPL = success, NAND = failure
        # Prior: Beta(1, 1) uniform
        alpha = 1.0 + impl_count
        beta = 1.0 + nand_count
        mean = alpha / (alpha + beta)

        return {
            "mean": round(mean, 4),
            "total_events": total,
            "impl_count": impl_count,
            "nand_count": nand_count,
            "alpha": alpha,
            "beta": beta,
            "outcomes": outcomes[:20],  # cap for readability
        }

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

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str) -> None:
        """Create Source → Entity references edge (Ontology v3.0 §3.2-3.3).

        Args:
            source_url: the Source node's url (must exist — created by _link_source)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Document|Event|Object) for the MATCH

        Raises:
            ValueError: if entity_label is not one of Document, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        proj = self._get_proj()
        proj.link_source_to_entity(source_url, entity_id, entity_label)

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
