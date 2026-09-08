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

import math
import os
import tempfile
from pathlib import Path

from battery.arms.base import AgentContext, ArmAdapter, ArmUnavailable, Memory  # noqa: F401
from battery.config.corpus import Scenario
from battery.runner.setup import scenario_namespace

#: Evidence-point kind used for agent writes (decision-part semantics: live
#: with a stamped starting belief — the product's own write surface).
_EVIDENCE_KIND = "evidence"
_CLAIM_MEMORY_KIND = "claim"
#: Per-episode Challenge/Deepen cycle cap (#2291 I-3 / Task 4 ep_outcome):
#: cap-hit ⇒ non_converged/undec, never forced CONVERGED.
DECIDE_CYCLES_CAP = 8
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
        self._active_scenario: str | None = None
        #: per-scenario memo of evidence content already filed this setup
        #: (idempotency: an identical re-file is a true no-op — no duplicate
        #: operator, no double cycle; product content-hash keeps one point).
        self._filed_content: dict[str, set[str]] = {}

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

        # Close any prior handles (accumulate re-setup lifecycle — never
        # leak opened projections/atexit registrations across sessions).
        self.close()

        ev_dir = Path(db_file).parent / "events"
        self._filed_content = {}
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
        proj = self._sdk(scenario)._get_proj()
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
        Memory.confidence = the claim's EP posterior mean (row.ep.
        confidence_mean) — never None on the real path (uncalibrated rows
        fall back to the product's documented neutral 0.5). Operator ids
        surfaced by the state read's nands/arguments attachments are
        emitted as operator-kind Memories (content = the attached edge
        label when given, else "") so the WRITE closed set can carry
        operators for #901 mitigate routing. Raises ArmUnavailable on
        failure (never partial memories). The per-episode decide counter
        resets when the episode MOVES to a different scenario (episodes are
        per-scenario sequential — a late scenario must not inherit an early
        one's cycle count toward the cap).
        """
        # Episode boundary: reset decide_cycles when the scenario changes.
        sid = context.scenario.id
        if self._active_scenario is not None and self._active_scenario != sid:
            self.decide_cycles = 0
        self._active_scenario = sid
        sdk = self._sdk(context.scenario)
        try:
            query = (context.user_message or "").strip()
            if not query:
                probe = _scenario_probe_query(context.scenario)
                query = probe or ""
            results = sdk.recall_state(
                query=query or None, kind=None, limit=20)
            out: list[Memory] = []
            seen_op_ids: set[str] = set()
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
                ep = row.get("ep") if isinstance(row.get("ep"), dict) else {}
                mean = ep.get("confidence_mean")
                confidence = (float(mean) if isinstance(mean, (int, float))
                              else 0.5)
                confidence = max(0.0, min(1.0, confidence))
                out.append(Memory(
                    id=rid, content=content, confidence=confidence,
                    kind=_CLAIM_MEMORY_KIND))
                # Operator attachments (contested/high-contention rows carry
                # them) → the write closed set.
                for key in ("nands", "arguments"):
                    for att in (row.get(key) or []):
                        oid = att.get("id") if isinstance(att, dict) else None
                        if not oid or oid in seen_op_ids:
                            continue
                        seen_op_ids.add(oid)
                        out.append(Memory(
                            id=str(oid), content="", confidence=None,
                            kind="operator"))
            return out
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"a4 graph read: {e}") from e

    # ── ep_outcome terminal table (#2291 I-4) ───────────────────────────
    def ep_terminal_outcome(self, scenario: Scenario, *,
                            variance_threshold: float = 0.04) -> dict:
        """Honest episode-end terminal outcome over the product EP engine.

        Derivation rules (plan §1; review P1-4/P2-5):
        - decisive = compute_confidence returns a NON-EMPTY affected set AND
          no vacuous diagnostic — an empty affected set is NEVER decisive
          (derived from the public return, never private state).
        - contested = MAX per-claim posterior variance over the affected set
          EXCEEDS ``variance_threshold`` ([cal] ep-variance row passed
          explicitly) — NON-DECISIVE BY CONSTRUCTION even when the engine
          converged (mechanism convergence != decisiveness); numeric
          converged + variance retained.
        - decide cap (DECIDE_CYCLES_CAP) reached ⇒ the process stopped —
          never forced CONVERGED: outcome undec (loopy scenario) or
          non_converged, unless contested (state persists, capped flag set).
        - engine non-convergence ⇒ undec (loopy/graph_script scenario, per
          task_type tie-break) or non_converged. NOTE (probe 2026-09-07):
          the damped embedded engine converges the authored lp NAND
          triangles (iterations 13, converged=true) — the engine-diagnostic
          undec leg is currently UNREACHABLE on the committed corpus; undec
          is reached honestly via the decide-cap path. Never forced.

        Returns {outcome, converged, iterations, max_variance,
        affected_count, decide_cycles, capped, anchors,
        anchor_shortfall}. Consumers (Task-8 liveness, verify-at-scope, R3
        scorer) read DECISIVE within-row outcomes only. anchor_shortfall is
        set when decide cycles happened but the probe returned no live
        anchors (vacuous-EP diagnostic — never a silent non_converged).
        """
        sdk = self._sdk(scenario)
        anchors = _live_claim_ids(sdk, scenario)
        cc = sdk.compute_confidence(
            factors=None, anchors=anchors)
        conf = cc.get("confidences") or {}
        diagnostic = cc.get("diagnostic")
        converged = bool(cc.get("converged"))
        iterations = int(cc.get("iterations") or 0)
        vacuous = (not conf) or bool(diagnostic)
        decide = self.decide_cycles
        anchor_shortfall = decide > 0 and not anchors
        variances = {}
        for cid, entry in conf.items():
            if isinstance(entry, dict) and entry.get("variance") is not None:
                try:
                    variances[cid] = float(entry["variance"])
                except (TypeError, ValueError):
                    continue
        max_var = max(variances.values(), default=0.0)
        affected = len(conf)
        capped = decide >= DECIDE_CYCLES_CAP and decide > 0
        if decide == 0 and affected == 0:
            outcome = "no-op"
        elif max_var > float(variance_threshold):
            outcome = "contested"  # non-decisive BY CONSTRUCTION
        elif capped or not converged or anchor_shortfall:
            outcome = "undec" if _is_loopy(scenario) else "non_converged"
        elif vacuous:
            # converged but no decisive affected set — mechanism convergence
            # with no epistemic movement is never CONVERGED.
            outcome = "undec" if _is_loopy(scenario) else "non_converged"
        else:
            outcome = "converged"
        return {
            "outcome": outcome, "converged": converged,
            "iterations": iterations, "max_variance": max_var,
            "affected_count": affected, "decide_cycles": decide,
            "capped": capped, "anchors": len(anchors),
            "anchor_shortfall": anchor_shortfall,
        }

    # ── record ──────────────────────────────────────────────────────────
    def record(self, context: AgentContext, item: Memory) -> None:
        """Write through the product verb surface (#901 routing).

        #2291 Task 3 semantics:
        - Targets come ONLY from the retrieved closed set
          (context.prior_memories); an EMPTY closed set (or no claim member)
          ⇒ zero writes + honest no-op (locked: ``empty_set_never_writes``).
        - kind=evidence content is filed once per scenario per setup: a
          re-file of IDENTICAL content is a TRUE no-op (no duplicate
          evidence point, no duplicate operator, no double cycle — the
          product content-hash keeps the point singular either way).
        - kind="nand" (truth edge): create_operator NAND, unidirectional,
          promote_source=True, targeting a closed-set CLAIM.
        - kind="mitigate" (relevance edge): mitigate_operator on a
          closed-set OPERATOR-kind memory (surfaced by the Task-4 read
          mapping); strength = clamp(item.confidence, 0.10, 0.50) when a
          confidence is given, else the decide-tooling default 0.3 (both
          inside the product's [0.10, 0.50] convention); reason = content.
          Mitigation strength is advisory metadata (#2315) — asserted on
          engine-honored surfaces (call/idempotency/observability), NEVER on
          claim-EP deltas.
        - kind default (support edge): create_operator IMPL.
        - Evidence is created STATUS DRAFT; it goes LIVE only when the
          operator edge succeeds (create_operator promote_source) — an
          operator write failure leaves an INERT draft orphan (never a live
          orphan in state reads/EP on accumulating graphs).
        - decide_cycles increments per successful NEW record; the
          per-episode cap (DECIDE_CYCLES_CAP, default 8) makes further
          records honest no-ops (cap-hit semantics surface in Task 4's
          ep_outcome table — never forced CONVERGED).
        - Mid-run failure (CalibrationError/product error) ⇒ ArmUnavailable
          (never swallow + fabricate from an uncalibrated store).
        """
        if self._db_path is None:
            return
        sdk = self._sdk(context.scenario)
        try:
            if self.decide_cycles >= DECIDE_CYCLES_CAP:
                return  # cap-hit: honest no-op (never forced CONVERGED)
            closed = [m for m in (context.prior_memories or ())
                      if m.id and not _is_seed_manifest(m.content)]
            if item.kind == "mitigate":
                ops = [m for m in closed if m.kind == "operator"]
                if not ops:
                    return  # unresolved operator target ⇒ honest no-op
                c = item.confidence
                if isinstance(c, float) and math.isfinite(c):
                    # clamp raw numeric confidence into [0.10, 0.50]
                    # (out-of-range high ⇒ capped at the 0.50 convention).
                    strength = max(0.10, min(0.50, c))
                else:
                    strength = 0.3  # decide-tooling default, in-range
                sdk.mitigate_operator(
                    ops[0].id, reason=item.content or "", strength=strength)
                self.decide_cycles += 1
                return
            claims = [m for m in closed if m.kind == _CLAIM_MEMORY_KIND]
            if not claims:
                return  # empty/claim-less closed set ⇒ zero writes (no-op)
            sid = context.scenario.id
            filed = self._filed_content.setdefault(sid, set())
            if item.content in filed:
                return  # identical re-file this setup: TRUE no-op
            target = claims[0].id
            created = sdk.create_point(kind=_EVIDENCE_KIND, content=item.content,
                                       dedup=True, status="draft",
                                       source_harness="battery",
                                       source_session=sid)
            ev_id = created.get("id") if isinstance(created, dict) else None
            if not ev_id:
                raise ArmUnavailable("a4 create_point returned no id")
            try:
                if item.kind == "nand":
                    sdk.create_operator(
                        "NAND", ev_id, [target], direction="unidirectional")
                else:
                    sdk.create_operator("IMPL", ev_id, [target])
            except Exception as e:  # noqa: BLE001, RUF100
                # Evidence stays DRAFT (promote_source fires only on operator
                # success) ⇒ inert residue, never a live orphan.
                raise ArmUnavailable(f"a4 operator write failed: {e}") from e
            filed.add(item.content)
            self.decide_cycles += 1  # one cycle per NEW record
        except ArmUnavailable:
            raise
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


def _live_claim_ids(sdk, scenario: Scenario) -> list[str]:
    """Live claim ids for the terminal-table EP run (the decide anchors).
    Read via the product state surface (recall_state), never raw queries."""
    try:
        probe = _scenario_probe_query(scenario) or None
        rows = sdk.recall_state(query=probe, kind=None, limit=50)
        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            if r.get("entity_type") != "point" or r.get("is_operator"):
                continue
            c = str(r.get("content") or "")
            if not c or _is_seed_manifest(c) or c.startswith("[MITIGATION]"):
                continue
            rid = str(r.get("id") or "")
            if rid:
                out.append(rid)
        return out
    except Exception as e:  # noqa: BLE001, RUF100
        # Never swallow: a failed state read must surface as ArmUnavailable,
        # not as an empty anchor set that silently relabels a converged
        # store non_converged/undec with no diagnostic.
        raise ArmUnavailable(f"a4 live-claim state read failed: {e}") from e


def _is_loopy(scenario: Scenario) -> bool:
    """task_type tie-break: loopy/graph_script scenarios classify
    non-convergence as undec (never forced CONVERGED); other scenarios as
    non_converged."""
    tt = getattr(scenario, "task_type", None)
    return bool(tt == "loopy" or getattr(scenario, "graph_script", None))


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
