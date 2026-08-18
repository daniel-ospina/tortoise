"""A3 — recall-RAG arm (flat claim store, no propagation).

ChromaDB-class flat store over the scenario corpus: retrieved memories are
verbatim claim spans. No propagation, no contradiction detection, no
confidence — the 'recall without reasoning' control (plan §5; brief:
the flat-store failure mode is 0% responsive to evidence change).
"""
from __future__ import annotations

import hashlib

from battery.arms.base import AgentContext, ArmAdapter, Memory
from battery.config.corpus import Scenario


class A3RagArm:
    """Flat retrieval arm. arm_id=a3, adapter=battery.arms.a3_rag."""

    arm_id = "a3"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, top_k: int = 5, **config):
        self._top_k = top_k
        self._claims: list[dict] = []

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        # Seed the flat store from the scenario prompt turns.
        self._claims = []
        for sc in scenarios:
            for turn in sc.prompt_pack:
                self._claims.append({
                    "id": f"claim-{hashlib.sha256(turn['content'].encode()).hexdigest()[:12]}",
                    "content": turn["content"],
                    "scenario": sc.id,
                })

    def _overlap(self, a: str, b: str) -> int:
        return len(set(a.lower().split()) & set(b.lower().split()))

    def retrieve(self, context: AgentContext) -> list[Memory]:
        scored = sorted(
            ((self._overlap(context.user_message, c["content"]), c)
             for c in self._claims),
            key=lambda x: -x[0])
        return [Memory(id=c["id"], content=c["content"], kind="claim",
                       source=c.get("scenario", ""))
                for _, c in scored[: self._top_k] if _ > 0]

    def record(self, context: AgentContext, item: Memory) -> None:
        self._claims.append({"id": item.id, "content": item.content})

    def isolation_namespace(self) -> str:
        return "a3-rag"

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A3Arm = A3RagArm
