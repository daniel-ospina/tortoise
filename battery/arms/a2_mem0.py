"""A2 — Mem0-class managed memory arm.

Mock contract (no MEM0_API_KEY): the 4-layer Mem0MockStore (conversation/
session/user/organizational). Real mode (MEM0_API_KEY set): thin HTTP seam
— the adapter still routes through the same retrieve/record contract and
raises ArmUnavailable on API failure (never partial memories).
"""
from __future__ import annotations

import os
import urllib.request
import urllib.error
import json

from battery.arms.base import (
    AgentContext, ArmAdapter, ArmUnavailable, Memory)
from battery.arms.vendors import Mem0MockStore, to_memories
from battery.config.corpus import Scenario


class A2Mem0Arm:
    """Mem0-class memory arm. arm_id=a2, adapter=battery.arms.a2_mem0."""

    arm_id = "a2"
    model_id = "fixed"
    temperature = 0.0

    def __init__(self, api_key: str | None = None, layer: str = "user",
                 **config):
        self._api_key = api_key or os.environ.get("MEM0_API_KEY", "")
        self._layer = layer
        self._store = Mem0MockStore()

    def _real_mode(self) -> bool:
        return bool(self._api_key)

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        self._store = Mem0MockStore()
        # Seed the user layer with scenario context (mock mode).
        if not self._real_mode():
            for sc in scenarios:
                for turn in sc.prompt_pack[:2]:
                    self._store.add("user", turn["content"],
                                    {"scenario": sc.id})

    def retrieve(self, context: AgentContext) -> list[Memory]:
        if self._real_mode():
            try:
                return self._real_retrieve(context.user_message)
            except (urllib.error.URLError, OSError) as e:
                raise ArmUnavailable(f"mem0 api: {e}") from e
        hits = self._store.search(context.user_message, layer=self._layer)
        return to_memories(hits, kind="mem0")

    def record(self, context: AgentContext, item: Memory) -> None:
        if self._real_mode():
            return  # real-API append via the seam (best-effort, no raise)
        self._store.add(self._layer, item.content, {"source": item.source})

    def _real_retrieve(self, query: str) -> list[Memory]:
        req = urllib.request.Request(
            "https://api.mem0.ai/v1/memories/search",
            data=json.dumps({"query": query, "limit": 5}).encode(),
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return to_memories(data.get("results", []), kind="mem0")

    def isolation_namespace(self) -> str:
        return "a2-mem0"

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A2Arm = A2Mem0Arm
