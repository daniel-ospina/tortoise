"""ArmAdapter protocol — the sealed-adapter contract (plan §6; scope DD8).

Real arms (A0–A4) implement this protocol in #1408; this slice ships the
protocol + dataclasses + ArmUnavailable so the runner and child issues
implement to a tested contract. The harness never reaches into an arm's
internals (isolation contract).
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401
from typing import Protocol

from battery.config.corpus import Scenario


class ArmUnavailable(Exception):
    """Arm API failure (timeout/429/503) — never partial-memory returns.

    Raised during retrieve/record → the runner serves a deterministic cached
    response and records ``fallback_cached`` (cache exists) or ``failed``
    (no cache). Raised during setup_scenarios / load → arm-init failure
    (skip arm, summary-only, exit 4).
    """


@dataclass(frozen=True)
class Memory:
    """One retrieved memory item."""

    id: str
    content: str
    confidence: float | None = None
    source: str = ""
    kind: str = "statement"
    #: Optional WRITE target (a closed-set member id). When an executor
    #: knows WHICH claim/operator a record is aimed at it sets this; the
    #: arm validates membership in the retrieved closed set and refuses
    #: (honest no-op) non-members — never a silent misroute (plan Task-3
    #: "item.target if present must be a closed-set member").
    target_id: str | None = None
    #: Optional author-stated SOURCE STRENGTH for a filed evidence point
    #: (ladder: gold/high/medium/low/unverified, T0-T4 or numeric — validated
    #: by the SDK). When the executor/agent knows the counter-source is
    #: strong it states it here; the arm maps it onto the created evidence.
    #: Absent (None) => the arm applies the SDK's documented decide default
    #: (medium, Beta(3,1)) — an agent-filed contradiction is NEVER weaker
    #: than the standard rung by omission (#2284 exposure finding: the
    #: draft-first path previously lost the default and behaved as
    #: unverified, ~3x weaker than intended).
    credibility: str | None = None


@dataclass(frozen=True)
class AgentContext:
    """Everything the harness hands to the arm for one episode.

    ``prior_memories`` are the arm's retrieved items; ``user_message`` is
    the current turn. Gold text is NEVER present here (sealed-gold
    boundary — scope DD2).
    """

    scenario: Scenario
    episode_seed: int
    prior_memories: tuple[Memory, ...] = ()
    user_message: str = ""


class ArmAdapter(Protocol):
    """Sealed arm adapter (supersedes plan §6's ArmAdapter row; the
    dataclasses above + setup_scenarios are added by this slice — scope DD8).

    Contract:
      - setup_scenarios(scenarios) — seed the arm's memory for the run
        (MockArm: no-op; harness batcher handles the scenario graph when
        --batch-setup).
      - retrieve(context) -> list[Memory] — raise ArmUnavailable on failure,
        never return partial memories silently.
      - record(context, item) — persist one memory item.
      - isolation_namespace() -> str — the arm's per-arm namespace; distinct
        from the harness's per-scenario setup namespace.

    Seed contract (#2284 I-1): contradiction scenarios seed in seed_mode by
    default — claim_a + evidence ONLY, never claim_b/k/NAND (¬A arrives
    in-context at turn k at run time). The verb channel itself (sdk.ingest)
    is sibling A's (#2291) — applied over the same seed_mode contract.
    """

    arm_id: str
    model_id: str
    temperature: float

    def setup_scenarios(self, scenarios: list[Scenario]) -> None: ...

    def retrieve(self, context: AgentContext) -> list[Memory]: ...

    def record(self, context: AgentContext, item: Memory) -> None: ...

    def isolation_namespace(self) -> str: ...
