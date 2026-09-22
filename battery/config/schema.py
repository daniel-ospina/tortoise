"""Scenario corpus schema constants (issue #1407, epic #1402 plan §4).

Single source of truth for every enum/constant the corpus touches — modules
reference these by name, never literal strings (epic plan §6 discipline).

Packs (task_type) and their metric families:
    decision -> R2 (adversarial deliberation coverage) / R4 (defeat conditions)
    contradiction -> R1 (contradiction surfacing)
    calibration -> R3 (epistemic calibration)
    retraction -> R5 (belief-update responsiveness)
    loopy_contested -> R3 (honest UNDEC — E2E-1.3)
    adversarial -> D4 (robustness — attack_type field)
    family_rep -> L2 (pseudo-evolution stream)
    interdependent -> L1 (MemoryArena-style stream)
    wave_variant -> L3 (harder held-out variants)
    cross_session_contradiction -> L4 (A/¬A across sessions)
    decision_drift -> L5 (weeks-long consistency)
    feedback_loop -> D3 (iterative feedback integration)

Packless families (documented — no dedicated corpus pack): D1 (verdict-rule
profile assembly, #1415), D2 (longitudinal spread — reuses L1/L2 streams via
#1412), L6 (distillation fidelity — re-scores existing scenarios via #1411).
"""
from __future__ import annotations

# Tier: scenario SHAPE (probe = single-session; stream = multi-session script;
# differential = hostile/robustness). Tier-3 pack selection downstream must use
# family/task_type, never tier alone (feedback_loop is stream-shaped but
# Tier-3-scored).
TIERS: tuple[str, ...] = ("probe", "stream", "differential")

# Metric families (plan §4 `family`).
FAMILIES: tuple[str, ...] = (
    "R1", "R2", "R3", "R4", "R5",
    "L1", "L2", "L3", "L4", "L5", "L6",
    "D3", "D4",
)

TASK_TYPES: tuple[str, ...] = (
    "decision",
    "contradiction",
    "calibration",
    "retraction",
    "loopy_contested",
    "adversarial",
    "family_rep",
    "interdependent",
    "wave_variant",
    "cross_session_contradiction",
    "decision_drift",
    "feedback_loop",
)

# D4 hostile-input attack types (E2E-3.5). Only valid when task_type=adversarial.
ATTACK_TYPES: tuple[str, ...] = ("poisoned", "sybil", "echo_chamber", "flapping", "anchoring")

# Contamination-control splits (authored at creation time — never derived).
SPLITS: tuple[str, ...] = ("train", "wave-1", "wave-2", "wave-3", "held_out")

# R3 calibration evidence tiers.
EVIDENCE_TIERS: tuple[str, ...] = ("T1", "T2", "T3", "T4")

# D4 source-credibility tiers (epistemic-layer T0–T4; T0 highest credibility).
# SEPARATE from EVIDENCE_TIERS — T0 is a source-credibility tier, not an
# evidence tier (the sybil scenario's "100 T4 vs 1 T0" ordering lives here).
SOURCE_TIERS: tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")

# Evidence valence (calibration evidence_tiers + flapping flip new_valence).
VALENCES: tuple[str, ...] = ("supports", "undercuts")

# family_rep repetition index.
REP_VALUES: tuple[int, ...] = (1, 2, 3)

# Enum-shaped gold values that legitimately appear in scenario prose — the
# render-guard allowlist DERIVES from this constant (single source; extend
# here, never inline). Guard matching is exact-token for these.
GOLD_ENUMS: tuple[str, ...] = ("undecided",)

# Exact pack counts (v1.1 — growing a pack requires bumping CORPUS_VERSION;
# contradiction = 15 planted ct-* + 6 benign bct-* FP surface twins, #2284 T3).
PACK_COUNTS: dict[str, int] = {
    "decision": 20,
    "contradiction": 21,
    "calibration": 15,
    "retraction": 10,
    "loopy_contested": 12,
    "adversarial": 10,
    "family_rep": 18,
    "interdependent": 10,
    "wave_variant": 6,
    "cross_session_contradiction": 6,
    "decision_drift": 6,
    "feedback_loop": 6,
}

# Exact per-pack split distribution (contamination control pinned at creation).
PACK_SPLITS: dict[str, dict[str, int]] = {
    "decision": {"train": 14, "wave-1": 6},
    "contradiction": {"train": 21},  # 15 ct-* + 6 bct-* twins, all train
    "calibration": {"train": 12, "wave-2": 3},
    "retraction": {"train": 10},
    "loopy_contested": {"train": 8, "wave-2": 4},
    "adversarial": {"held_out": 4, "train": 6},
    "family_rep": {"wave-1": 5, "wave-2": 5, "wave-3": 5, "held_out": 3},
    "interdependent": {"wave-1": 5, "wave-2": 5},
    "wave_variant": {"held_out": 6},
    "cross_session_contradiction": {"wave-2": 3, "wave-3": 3},
    "decision_drift": {"wave-2": 3, "wave-3": 3},
    "feedback_loop": {"wave-3": 6},
}

# D4 attack-type distribution (2 per type = 10 adversarial scenarios).
ATTACK_DISTRIBUTION: dict[str, int] = {
    "poisoned": 2, "sybil": 2, "echo_chamber": 2, "flapping": 2, "anchoring": 2,
}

# L2 family_rep task-family names (6 authored templates; the held-out family's
# 3 reps are never shown in waves — E2E-2.1 contamination control).
FAMILY_REP_NAMES: tuple[str, ...] = (
    "incident-triage",
    "customer-churn-review",
    "vendor-selection",
    "feature-priority",
    "pricing-review",
    "compliance-assessment",
)
HELD_OUT_FAMILY = "compliance-assessment"

CORPUS_VERSION = "1.1"  # v1.1: bct-* benign FP surface twins (#2284 T3)

# Injection-turn pin for contradiction packs (R1 k=5; L4 cross-session k = the
# session index of the ¬A plant).
CONTRADICTION_K = 5

#: FP-surface control-set marker (bct-* benign twins, #2284 T3) — the ONLY
#: valid value for a contradiction scenario's ``control_set`` field. Single
#: source of truth: every predicate (validate exemption, population sweep)
#: keys on this constant, never a literal string — a typo (BCT/"bct "/
#: benign) must fail validation instead of silently disabling the
#: planted-contradiction bindings (PR #2341 review round 2, P2).
CONTROL_SET_BCT = "bct"
CONTROL_SET_VALUES: tuple[str, ...] = (CONTROL_SET_BCT,)
