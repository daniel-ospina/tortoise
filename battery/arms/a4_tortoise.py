"""A4 — Tortoise epistemic-graph arm (the treatment), SDK-verb channel.

The graph is the arm's memory: setup_scenarios builds the per-scenario
graphs (reference-lane hermetic batch seeding — raw-MERGE allowlisted via
``batch_setup`` UNTIL Task 2 swaps the channel to ``sdk.ingest``); the arm
then holds ONE ``TortoiseSDK`` handle per scenario (``graph_name`` bound at
construction, same store file, per-scenario event log) and every runtime
read/write flows through PRODUCT verbs on that handle — never raw Cypher.

Read surface (Task 1 minimal; Task 4 refines per-construct EP mapping):
  retrieve → ``recall_state`` (UC1 state read: live claims ranked by
  confidence, contested surfaced with counter-evidence, superseded
  excluded, draft-exclusion belt-and-braces). Memory.confidence stays None
  until Task 4 fills EP means from ``compute_confidence``.

Write surface (Task 1 minimal; Task 3 refines #901 routing):
  record → ``create_point`` (kind=evidence — decision-part semantics land
  LIVE with a stamped starting belief) + ``create_operator`` (IMPL/NAND)
  with targets from the retrieved closed set ONLY; an unresolved operator
  target is an honest no-op, never a fresh store probe.

The arm is hermetic: per-run embedded store (explicit db_path ignores
TORTOISE_DB_URI), per-scenario graphs isolated (EP caches cannot bleed).
Gold text NEVER enters the graph or the episode context (sealed-gold
boundary). Lane contract: docs/epics/1402-eval-battery/lane-matrix.md;
enforced source-level by tests/test_battery_lane_matrix.py.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from battery.arms.base import AgentContext, ArmAdapter, ArmUnavailable, Memory  # noqa: F401
from battery.config.corpus import Scenario
from battery.runner.setup import open_reference_projection, scenario_namespace

#: Evidence-point kind used for agent writes (decision-part semantics: live
#: with a stamped starting belief — the product's own write surface).
_EVIDENCE_KIND = "evidence"
_CLAIM_MEMORY_KIND = "claim"
#: seed-manifest marker content prefix — never surfaced as a memory.
_SEED_MANIFEST_PREFIX = "battery:seed_manifest:"


def _is_seed_manifest(content: str) -> bool:
    return (content or "").startswith(_SEED_MANIFEST_PREFIX)


class A4TortoiseArm:
    """Epistemic-graph arm. arm_id=a4, adapter=battery.arms.a4_tortoise."""

    arm_id = "a4"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, db_path: str | None = None, **config):
        self._db_path = db_path or os.environ.get("TORTOISE_DB_PATH") or ""
        self._sdk_by_id: dict[str, object] = {}
        self.decide_cycles = 0

    # ── setup ───────────────────────────────────────────────────────────
    def setup_scenarios(self, scenarios: list[Scenario], *, seed_lane: bool = True) -> None:
        """Build the per-scenario graphs + open one SDK handle per scenario.

        Content is seeded through the PRODUCT bulk surface —
        ``seed_scenario_via_ingest`` (sdk.ingest over each scenario's own
        handle: derive_scenario_graph content contract + credibility
        baselines + seed props; server batch_id; seed points promoted —
        operators ride the source promotion). The warm guard refuses a stale
        PRE-FIX graph in the same namespace BEFORE any batch_id is minted;
        a clean seed_mode graph re-setup accumulates (idempotent ingest).
        seed_mode default: contradiction scenarios seed claim_a + evidence
        ONLY (¬A never pre-seeded — it arrives in-context at k at run time).
        The reference-lane batch path stays for equivalence tests only
        (battery.testing.seeds raw helper) — never the real A4 path.

        ``seed_lane=False``: open the per-scenario handles only (no seeding)
        — used by the equivalence facade to READ a raw-lane-seeded store
        through the arm's product read surface.
        """
        if not self._db_path:
            tmp = tempfile.mkdtemp(prefix="battery_a4_")
            self._db_path = str(Path(tmp) / "a4.db")
        db_file = str(self._db_path)
        from tortoise.sdk import TortoiseSDK

        ev_dir = Path(db_file).parent / "events"
        for sc in scenarios:
            ns = scenario_namespace(sc.id)
            sdk = TortoiseSDK(
                db_path=db_file,
                graph_name=ns,
                event_log_path=str(ev_dir / f"{ns}.jsonl"),
            )
            self._sdk_by_id[sc.id] = sdk
            if seed_lane:
                from battery.runner.setup import seed_scenario_via_ingest
                seed_scenario_via_ingest(sdk, sc, seed_mode=True)
        self.decide_cycles = 0

    def _sdk(self, scenario: Scenario):
        sdk = self._sdk_by_id.get(scenario.id)
        if sdk is None:
            raise ArmUnavailable(
                f"a4 arm not set up for scenario {scenario.id}")
        return sdk

    def _scenario_graph(self, scenario: Scenario):
        """READ-ONLY test-support handle over the scenario graph (used by
        battery.testing.seeds.SeededStore.find_content). Never a runtime
        write path."""
        proj = self._sdk(scenario)._get_proj()  # noqa: SLF001  (test support)
        return proj.db.select_graph(scenario_namespace(scenario.id))

    # ── retrieve ────────────────────────────────────────────────────────
    def retrieve(self, context: AgentContext) -> list[Memory]:
        """Read the graph through the product UC1 state surface.

        recall_state over the scenario's SDK handle (same handle that
        wrote): live claims ranked by the multiplicative confidence gate;
        contested surfaced with counter_evidence; superseded excluded;
        draft/terminal filtering per product semantics. The probe query is
        the episode's own user message when present, else the scenario's
        primary planted claim (the everyday "what do I know about X" read).
        Memory.confidence stays None until Task 4 fills EP means. Raises
        ArmUnavailable on failure (never partial memories).
        """
        sdk = self._sdk(context.scenario)
        try:
            query = (context.user_message or "").strip()
            if not query:
                probe = _scenario_probe_query(context.scenario)
                query = probe or ""
            results = sdk.recall_state(
                query=query or None, kind=None, limit=20)
            out: list[Memory] = []
            for row in results or []:
                if not isinstance(row, dict):
                    continue
                if row.get("entity_type") != "point":
                    continue
                if row.get("is_operator"):
                    continue
                content = str(row.get("content") or "")
                if not content or _is_seed_manifest(content):
                    continue
                if content.startswith("[MITIGATION]"):
                    continue  # state-context attachment, not a standalone claim
                rid = str(row.get("id") or "")
                if not rid:
                    continue
                out.append(Memory(
                    id=rid, content=content, confidence=None,
                    kind=_CLAIM_MEMORY_KIND))
            return out
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"a4 graph read: {e}") from e

    # ── record ──────────────────────────────────────────────────────────
    def record(self, context: AgentContext, item: Memory) -> None:
        """Write through the product verb surface.

        #901 semantics (Task 1 minimal; Task 3 refines): an evidence point
        via create_point (kind=evidence → decision-part semantics: lands
        live with a stamped starting belief), then a NAND (truth edge) or
        IMPL (support edge) operator via create_operator with targets taken
        ONLY from the retrieved closed set (context.prior_memories). An
        unresolved target/operator is an honest no-op — never a fresh store
        probe, never a content-derived guess.
        """
        if self._db_path is None:
            return
        sdk = self._sdk(context.scenario)
        try:
            closed = [m for m in (context.prior_memories or ())
                      if m.id and not _is_seed_manifest(m.content)]
            if not closed:
                return  # empty closed set ⇒ zero writes (honest no-op)
            target = next((m.id for m in closed
                           if m.kind == _CLAIM_MEMORY_KIND), closed[0].id)
            created = sdk.create_point(kind=_EVIDENCE_KIND, content=item.content)
            ev_id = created.get("id") if isinstance(created, dict) else None
            if not ev_id:
                raise ArmUnavailable("a4 create_point returned no id")
            if item.kind == "nand":
                sdk.create_operator(
                    "NAND", ev_id, [target], direction="unidirectional")
            elif item.kind == "mitigate":
                # Operator targets require the closed set to carry the
                # operator (Task 3 read-mapping refinement) — until then an
                # unresolved operator target is an honest no-op.
                return
            else:
                sdk.create_operator("IMPL", ev_id, [target])
            self.decide_cycles += 1  # one Challenge/Deepen cycle per record
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"a4 graph write: {e}") from e

    def isolation_namespace(self) -> str:
        return "a4-tortoise"

    def close(self) -> None:
        for sdk in self._sdk_by_id.values():
            try:  # noqa: SIM105
                sdk.close()
            except Exception:  # noqa: BLE001, RUF100
                pass
        self._sdk_by_id = {}


def _scenario_probe_query(scenario: Scenario) -> str:
    """Deterministic probe text for the empty-context everyday read: the
    primary planted claim when the scenario plants one, else the first
    authored non-system turn."""
    pairs = getattr(scenario, "contradiction_pairs", None) or []
    if pairs:
        ca = getattr(pairs[0], "claim_a", None)
        if ca:
            return ca[:200]
    turns = [t for t in (scenario.prompt_pack or [])
             if t.get("role") != "system"]
    if turns:
        return str(turns[0].get("content", ""))[:200]
    return ""


# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A4Arm = A4TortoiseArm
