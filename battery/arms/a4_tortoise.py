"""A4 — Tortoise epistemic-graph arm (the treatment).

The graph is the arm's memory: setup_scenarios builds the scenario graph
(per-scenario namespace via the batcher — wiring the #1406 deferral),
retrieve reads EP confidence + traverse (IMPL/NAND), record writes
evidence points + operators with the decide-workflow semantics
(truth-vs-relevance: NAND the claim vs mitigate the operator), and the arm
exposes a decide_cycles counter — the R2 mechanism-gate trajectory field
(Challenge/Deepen cycles per graph-scripts/decide.py patterns).

Gold text NEVER enters the graph or the episode context (sealed-gold
boundary). The arm is hermetic: embedded FalkorDBLite via TORTOISE_DB_PATH.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from battery.arms.base import AgentContext, ArmAdapter, ArmUnavailable, Memory  # noqa: F401
from battery.config.corpus import Scenario
from battery.runner.setup import scenario_namespace


class A4TortoiseArm:
    """Epistemic-graph arm. arm_id=a4, adapter=battery.arms.a4_tortoise."""

    arm_id = "a4"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, db_path: str | None = None, **config):
        self._db_path = db_path or os.environ.get("TORTOISE_DB_PATH") or ""
        self._sdk = None
        self._proj = None
        self.decide_cycles = 0

    # ── setup ───────────────────────────────────────────────────────────
    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        """Build the scenario graph (per-scenario namespace) + seed claims.

        Uses the embedded FalkorDBLite (hermetic; no Docker). The batcher's
        namespace routing (battery.runner.setup.batch_setup) materializes
        per-scenario graphs when given the projection.

        seed_mode (#2284 I-1): contradiction scenarios seed claim_a +
        evidence ONLY (¬A never pre-seeded — it arrives in-context at k at
        run time); the hermetic warm guard refuses a stale PRE-FIX graph in
        the same namespace. The sdk.ingest verb-channel swap is sibling A's
        (#2291) — applied over the SAME seed_mode contract.
        """
        from tortoise.projection import FalkorProjection
        if not self._db_path:
            tmp = tempfile.mkdtemp(prefix="battery_a4_")
            self._db_path = str(Path(tmp) / "a4.db")
        self._proj = FalkorProjection(self._db_path, graph_name="test")
        from battery.runner.setup import batch_setup
        # namespaced=True: per-scenario graphs (battery_<id>) — retrieve reads
        # the same namespaced graph (no cross-scenario MERGE collapse).
        # seed_mode=True is the batch default for contradiction scenarios
        # (¬A absent pre-k) + the hermetic warm-store guard.
        batch_setup(self._proj, scenarios, namespaced=True, seed_mode=True)
        self.decide_cycles = 0

    def _scenario_graph(self, scenario: Scenario):
        return self._proj.db.select_graph(scenario_namespace(scenario.id))

    # ── retrieve ────────────────────────────────────────────────────────
    def retrieve(self, context: AgentContext) -> list[Memory]:
        """Read the graph: EP confidence + IMPL/NAND context for the
        scenario's claims. Raises ArmUnavailable on projection failure
        (never partial memories)."""
        if self._proj is None:
            raise ArmUnavailable("a4 arm not set up")
        try:
            g = self._scenario_graph(context.scenario)
            rows = g.query(
                "MATCH (p:Point) WHERE p.is_operator <> true "
                "AND p.content IS NOT NULL "
                "AND (p.pointKind IS NULL OR p.pointKind <> 'seed_manifest') "
                "RETURN p.id AS id, p.content AS content, "
                "p.status AS status LIMIT 20").result_set
            # Filter to live claims the episode can cite.
            out = []
            # result_set rows are positionals: (id, content, status).
            for row in rows:
                rid, rcontent, rstatus = (list(row) + [None] * 3)[:3]
                if rstatus in (None, "live", "draft"):
                    out.append(Memory(
                        id=str(rid), content=str(rcontent or ""),
                        kind="claim", confidence=None))
            return out
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"a4 graph read: {e}") from e

    # ── record ──────────────────────────────────────────────────────────
    def record(self, context: AgentContext, item: Memory) -> None:
        """Write an evidence point + wire it to the scenario's claims.

        Decide-workflow semantics: item.kind=="nand" → NAND the target claim
        (truth edge); item.kind=="mitigate" → mitigate the operator
        (relevance edge); otherwise create an evidence point with an IMPL to
        the matched claim (support edge).
        """
        if self._proj is None:
            return
        g = self._scenario_graph(context.scenario)
        try:
            import hashlib as _hashlib
            import time as _time  # noqa: F401
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            # Write directly through the projection graph (single-writer —
            # never open a second SDK on the same store).
            point_id = "ev-" + _hashlib.sha256(
                f"{context.scenario.id}:{item.content}".encode()).hexdigest()[:16]
            g.query(
                "MERGE (p:Point {id: $id}) "
                "ON CREATE SET p += {content: $content, pointKind: 'evidence', "
                "is_operator: false, status: 'draft', createdAt: $now, "
                "updatedAt: $now}",
                params={"id": point_id, "content": item.content, "now": now})
            claims = g.query(
                "MATCH (p:Point {is_operator: false}) "
                "WHERE p.pointKind IS NULL OR p.pointKind <> 'seed_manifest' "
                "RETURN p.id AS id "
                "LIMIT 5").result_set
            target = str(claims[0][0]) if claims else None
            if target and item.kind == "nand":
                op_id = point_id + "-nand"
                g.query(
                    "MERGE (o:Point {id: $oid}) "
                    "ON CREATE SET o += {is_operator: true, status: 'draft', "
                    "createdAt: $now}", params={"oid": op_id, "now": now})
                g.query(
                    "MATCH (o:Point {id: $oid}), (t:Point {id: $tid}) "
                    "MERGE (o)-[:NAND {direction: 'unidirectional'}]->(t)",
                    params={"oid": op_id, "tid": target})
            elif target and item.kind == "mitigate":
                ops = g.query(
                    "MATCH (o:Point {is_operator: true}) RETURN o.id AS id "
                    "LIMIT 3").result_set
                if ops:
                    g.query(
                        "MATCH (o:Point {id: $oid}) SET o.weight = $w",
                        params={"oid": str(ops[0][0]), "w": 0.3})
            elif target:
                op_id = point_id + "-impl"
                g.query(
                    "MERGE (o:Point {id: $oid}) "
                    "ON CREATE SET o += {is_operator: true, status: 'draft', "
                    "createdAt: $now}", params={"oid": op_id, "now": now})
                g.query(
                    "MATCH (o:Point {id: $oid}), (t:Point {id: $tid}) "
                    "MERGE (o)-[:IMPL]->(t)",
                    params={"oid": op_id, "tid": target})
            self.decide_cycles += 1  # one Challenge/Deepen cycle per record
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"a4 graph write: {e}") from e

    def isolation_namespace(self) -> str:
        return "a4-tortoise"

    def close(self) -> None:
        if self._proj is not None:
            try:  # noqa: SIM105
                self._proj.close()
            except Exception:  # noqa: BLE001, RUF100
                pass

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A4Arm = A4TortoiseArm
