"""A2b — Zep/Graphiti-class temporal knowledge-graph arm (strongest comparator).

Architecturally closest to Tortoise (bi-temporal facts, invalidation-not-
deletion) — the research brief mandates it as an arm ("if Tortoise only
beats Mem0 but not Zep, the 'unique' claim is weak"). Mock contract
(ZEP_API_KEY absent): ZepMockStore with valid_from + invalidation; retrieve
returns the latest VALID fact per entity. Real mode: HTTP seam, ArmUnavailable
on failure.

#2633 real-mode limitation (the differential verdict must know): a2b real
runs exercise a READ-ONLY live store — setup seeds ONLY in mock mode, and
record() is a best-effort no-op in real mode (the write path is a Task-9
seam TODO). The real-run pre-flight asserts ``required_env_keys`` before
any episode so a real request without ZEP_API_KEY never silently runs
real-model spend against the seeded in-process mock store.
"""
from __future__ import annotations  # noqa: I001

import os
import urllib.request
import urllib.error
import json

from battery.arms.base import (
    AgentContext, ArmAdapter, ArmUnavailable, Memory)  # noqa: F401
from battery.arms.vendors import ZepMockStore, to_memories
from battery.config.corpus import Scenario


class A2bZepArm:
    """Zep-class temporal arm. arm_id=a2b, adapter=battery.arms.a2b_zep.

    Required env keys: ``ZEP_API_KEY`` (real mode). Without it the arm
    serves the seeded in-process mock store — the real-run gate (run.py)
    refuses a2b under executor=real when the key is absent.
    """

    arm_id = "a2b"
    model_id = "fixed"
    temperature = 0.0
    #: Real-run credential surface (#2633): the env key whose absence makes
    #: ``_real_mode()`` False — a real request without it would silently
    #: exercise the in-process MOCK store under run_mode=real. Declared on
    #: the ADAPTER (the surface owner), read by the runner pre-flight
    #: (battery/runner/run.py); never hardcoded there or in arms.yaml.
    required_env_keys: tuple[str, ...] = ("ZEP_API_KEY",)

    def __init__(self, api_key: str | None = None, **config):
        self._api_key = api_key or os.environ.get("ZEP_API_KEY", "")
        self._store = ZepMockStore()

    def _real_mode(self) -> bool:
        return bool(self._api_key)

    def setup_scenarios(self, scenarios: list[Scenario]) -> None:
        self._store = ZepMockStore()
        if not self._real_mode():
            # Seed facts about the scenario's subject entities.
            for sc in scenarios:
                for turn in sc.prompt_pack:
                    words = [w for w in turn["content"].split()
                             if w[0].isupper()][:2]
                    for ent in words:
                        self._store.add(ent.strip(".,"), turn["content"][:80])

    def retrieve(self, context: AgentContext) -> list[Memory]:
        if self._real_mode():
            try:
                return self._real_retrieve(context.user_message)
            except (urllib.error.URLError, OSError) as e:
                raise ArmUnavailable(f"zep api: {e}") from e
        # Retrieve latest valid facts for entities mentioned in the query.
        entities = [w.strip(".,") for w in context.user_message.split()
                    if w[0].isupper()]
        out: list[Memory] = []
        for ent in dict.fromkeys(entities):
            out.extend(to_memories(self._store.retrieve(ent), kind="zep"))
        return out[:10]

    def record(self, context: AgentContext, item: Memory) -> None:
        if self._real_mode():
            return
        self._store.add(item.source or "agent", item.content)

    def _real_retrieve(self, query: str) -> list[Memory]:
        req = urllib.request.Request(
            "https://api.getzep.com/api/v2/search",
            data=json.dumps({"text": query, "limit": 5}).encode(),
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return to_memories(data.get("results", []), kind="zep")

    def isolation_namespace(self) -> str:
        return "a2b-zep"

# Resolver-compatible alias (runner `arm_id_to_cls` convention).
A2bArm = A2bZepArm
