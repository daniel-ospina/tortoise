"""Battery arm adapters (issue #1408): sealed per-arm memory backends.

A0 plain (no memory) · A1 long-context stuffing · A2 Mem0-class · A2b
Zep/Graphiti-class (strongest comparator) · A3 recall-RAG (flat store) ·
A4 Tortoise epistemic graph (the treatment). All implement the
ArmAdapter protocol (battery.arms.base); vendors use mock-contract stores
when API keys are absent, real HTTP seams when present, and raise
ArmUnavailable on failure (never partial memories).
"""
from __future__ import annotations

from battery.arms.a0_plain import A0PlainArm
from battery.arms.a1_longctx import A1LongctxArm
from battery.arms.a2_mem0 import A2Mem0Arm
from battery.arms.a2b_zep import A2bZepArm
from battery.arms.a3_rag import A3RagArm
from battery.arms.a4_tortoise import A4TortoiseArm
from battery.arms.base import (
    AgentContext,
    ArmAdapter,
    ArmUnavailable,
    Memory,
)

__all__ = [
    "A0PlainArm", "A1LongctxArm", "A2Mem0Arm", "A2bZepArm", "A3RagArm",
    "A4TortoiseArm", "AgentContext", "ArmAdapter", "ArmUnavailable",
    "Memory",
]
