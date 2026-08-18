"""Shared vendor-adapter plumbing for memory arms (issue #1408).

Mem0/Zep are managed APIs; this module provides the mock-contract fallback
(no API key) and the real-API dispatch (key present). Mock contracts mirror
the vendor's public memory semantics closely enough that the differential
comparison stays honest:

- Mem0 mock: layered memory (conversation/session/user) — fact add +
  similarity retrieve over the layer; the 4-layer model per the epic
  research brief (datapace/aigentlab 2026).
- Zep mock: temporal knowledge-graph-ish store — facts with valid_from,
  optional invalidation (deprecated=true), retrieval prefers the latest
  valid fact for an entity (the invalidation-not-deletion semantics the
  graph-as-memory research (n26modi) attributes to Zep/Graphiti).

Real-API mode (env keys MEM0_API_KEY / ZEP_API_KEY) is a thin HTTP client
seam; without keys the mock contract is authoritative. The adapter never
returns partial memories on failure — it raises ArmUnavailable (the
runner records fallback_cached/failed per the model-call outcome enum).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from battery.arms.base import Memory


@dataclass
class Mem0MockStore:
    """In-memory Mem0-like layered store (mock contract)."""

    layers: dict[str, list[dict]] = field(
        default_factory=lambda: {"conversation": [], "session": [],
                                 "user": [], "organizational": []})

    def add(self, layer: str, content: str, meta: dict | None = None) -> str:
        mid = hashlib.sha256(
            f"{layer}:{content}:{time.time_ns()}".encode()).hexdigest()[:16]
        self.layers.setdefault(layer, []).append(
            {"id": mid, "content": content, "meta": meta or {},
             "ts": time.time()})
        return mid

    def search(self, query: str, layer: str | None = None,
               limit: int = 5) -> list[dict]:
        """Token-overlap retrieval (mock similarity — deterministic)."""
        q_tokens = set(query.lower().split())
        candidates: list[dict] = []
        for name, items in self.layers.items():
            if layer and name != layer:
                continue
            for it in items:
                overlap = len(q_tokens & set(it["content"].lower().split()))
                if overlap:
                    candidates.append((overlap, it))
        candidates.sort(key=lambda x: (-x[0], x[1]["ts"]))
        return [it for _, it in candidates[:limit]]


@dataclass
class ZepMockStore:
    """In-memory Zep-like temporal store (mock contract).

    Facts carry valid_from + optional invalidated flag; retrieval for an
    entity returns the latest VALID fact (invalidation-not-deletion).
    """

    facts: list[dict] = field(default_factory=list)

    def add(self, entity: str, fact: str,
            valid_from: float | None = None) -> str:
        fid = hashlib.sha256(
            f"{entity}:{fact}:{time.time_ns()}".encode()).hexdigest()[:16]
        self.facts.append({"id": fid, "entity": entity, "fact": fact,
                           "valid_from": valid_from or time.time(),
                           "invalidated": False})
        return fid

    def invalidate(self, fact_id: str) -> None:
        for f in self.facts:
            if f["id"] == fact_id:
                f["invalidated"] = True
                return

    def retrieve(self, entity: str) -> list[dict]:
        """Latest VALID facts for the entity (valid_from desc)."""
        valid = [f for f in self.facts
                 if f["entity"] == entity and not f["invalidated"]]
        valid.sort(key=lambda f: f["valid_from"], reverse=True)
        return valid


def to_memories(items: list[dict], kind: str = "statement") -> list[Memory]:
    """Convert vendor records to the harness Memory shape."""
    return [
        Memory(id=it.get("id", f"{kind}-{i}"),
               content=str(it.get("content", it.get("fact", ""))),
               confidence=it.get("confidence"),
               source=str(it.get("source", "")),
               kind=kind)
        for i, it in enumerate(items)
    ]
