"""Source credibility derivation — pure functions for the discrete-tier model.

Canonical home of the validated source credibility model (issue #398, design doc
docs/ep-source-credibility-experiment.md):

  - TIER_PRIORS: credibilityTier T0-T4 → Beta(alpha, beta) priors.
  - pc_base(tier) := alpha - 1  (excess over neutral Beta(1,1) — "pseudo-count").
  - aggregate_prior: log-scale multi-source aggregation (anti-Sybil):
        pc_t = log2(N_t + 1) * decay_t * (sum_i base_pc(tier_i) * factor_i) / N_t
    where decay_t keys on the TIER's MOST-RECENT source (T0 exempt). N=1 degenerates
    to base_pc * decay (matches the pre-existing #122 recency modulation formula
    alpha' = 1 + (alpha - 1) * decay — test_event_provenance exactness preserved).
  - assessment_factor: per-source reliability modulation from agent assessments,
    k=1.0, clamped [0.1, 2.0]. Formula: 1 + k * sum_a (rep_a - 0.5) * (score_a - 0.5).
  - resolve_tier: precedence explicit credibilityTier > sourceKind tier-form > registry > None.
  - SOURCE_KIND_DEFAULTS: T0-T4 identity + explicit None for ALL legacy kinds —
    legacy type strings (github_issue, github_pr, slack_message, linear_card,
    linear_cycle, document) stay NEUTRAL until explicitly registered via
    register_source_kind_default().

Ontology alignment: reliability is DERIVED at query time (v3.1 §2/§11 — derived,
not stored; evaluations are Points with EP confidence). Decay is the §10-compliant
light recency modulation (0.95^years default, T0 exempt, never auto-deprecates);
per-field / per-sourceType decay curves are explicitly deferred (issue open question
answered: deferred — see docs/plans/2026-08-07-source-credibility.md).

This module is pure (no graph I/O) — the SDK adapter in sdk.py owns the queries.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable  # noqa: UP035

# ── Tier model (validated — experiment doc §1.1, test_ep_sources.py TIER_MAP) ──
TIER_PRIORS: dict[str, tuple[float, float]] = {
    "T0": (10.0, 1.0),   # Gold / meta-analysis      mean 0.9091, pc 9.0
    "T1": (5.0, 1.0),    # High / peer-reviewed      mean 0.8333, pc 4.0
    "T2": (3.0, 1.0),    # Medium / expert           mean 0.7500, pc 2.0
    "T3": (2.0, 1.0),    # Low / anecdotal           mean 0.6667, pc 1.0
    "T4": (1.1, 1.0),    # Unverified                mean 0.5238, pc 0.1
}
TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(["T0", "T1", "T2", "T3", "T4"])}
_TIER_FORM = frozenset(TIER_PRIORS)

# Legacy credibility aliases → canonical tier (create_point's credibility kwarg
# vocabulary; kept for create_source backward compat, #398).
TIER_ALIASES: dict = {
    "gold": "T0", "T0": "T0", 0: "T0",
    "high": "T1", "T1": "T1", 1: "T1",
    "medium": "T2", "T2": "T2", 2: "T2",
    "low": "T3", "T3": "T3", 3: "T3",
    "unverified": "T4", "T4": "T4", 4: "T4",
}

# Plain-language credibility ladder for HUMAN-AUTHORED points (decision parts,
# direct claims) — the middle rung ``medium`` is the #2199 decide-part default
# prior (Beta(3,1), mean 0.75): "reuse the ladder, do NOT invent a new number"
# (knock-on decision 2, issue #2199). The Beta numbers live in TIER_PRIORS
# only; the words live in TIER_ALIASES only — no parallel map to drift.
CREDIBILITY_LADDER: tuple[str, ...] = (
    "gold", "high", "medium", "low", "unverified",
)


def canonical_tier(value) -> str | None:
    """Map a tier value (T0-T4 or legacy alias gold/high/.../numeric) to canonical T0-T4."""
    return TIER_ALIASES.get(value)


def credibility_prior(value) -> tuple[float, float] | None:
    """Map a point-level credibility value to its Beta(alpha, beta) prior.

    Accepts the plain-language ladder words (gold/high/medium/low/unverified),
    the canonical T0-T4 forms, or the legacy numeric aliases 0-4 — the same
    vocabulary ``canonical_tier`` accepts. Single-sourced: the words resolve
    through TIER_ALIASES and the Beta tuples come from TIER_PRIORS, so a new
    rung can never land with a stale number. Returns None for values outside
    the ladder (callers decide whether to fail loud or default).
    """
    tier = canonical_tier(value)
    if tier is None:
        return None
    return TIER_PRIORS[tier]

# ── Source-kind registry (extensible vocabulary, issue O/I/T 4) ─────────────
# T0-T4 identity + explicit None for ALL legacy/generic kinds. Legacy type
# strings stay NEUTRAL (no inheritance) until registered — never auto-map a type
# to a tier (the forbidden static sourceType→reliability map, graph c=0.447).
SOURCE_KIND_DEFAULTS: dict[str, str | None] = {
    **{tier: tier for tier in _TIER_FORM},
    "document": None,
    "github_issue": None,
    "github_pr": None,      # #388: PR events carry their own kind (was mislabeled github_issue)
    "slack_message": None,
    "linear_card": None,
    "linear_cycle": None,   # #388: cycles are not cards — own kind, still neutral
}

# Assessment-factor constants (pinned in scoping resolution C / plan Task 5)
ASSESSMENT_K: float = 1.0
FACTOR_MIN: float = 0.1   # floor preserves >=10% evidence — never inverts the prior
FACTOR_MAX: float = 2.0   # ceiling preserves tier ordering + anti-Sybil


def pc_base(tier: str) -> float:
    """Excess over neutral Beta(1,1): pc := alpha - 1 (pseudo-count)."""
    alpha, _beta = TIER_PRIORS[tier]
    return alpha - 1.0


def mean_from_beta(alpha: float, beta: float) -> float:
    """Mean of Beta(alpha, beta)."""
    return alpha / (alpha + beta)


def register_source_kind_default(kind: str, tier: str | None) -> None:
    """Register (or override) the default tier hint for a source kind.

    The extension point for the extensible sourceType vocabulary. ``None``
    registers a kind as explicitly neutral (no inheritance). Invalid tier values
    raise ValueError so mis-registration fails fast at load/import time.
    """
    if tier is not None and tier not in _TIER_FORM:
        raise ValueError(
            f"Invalid tier {tier!r} — must be one of {sorted(_TIER_FORM)} or None"
        )
    SOURCE_KIND_DEFAULTS[kind] = tier


def resolve_source_tier(source_kind: str | None) -> str | None:
    """Registry lookup: default tier hint for a source kind (None = neutral)."""
    if not source_kind:
        return None
    return SOURCE_KIND_DEFAULTS.get(source_kind)


def resolve_tier(
    credibility_tier: str | None,
    source_kind: str | None = None,
    registry_defaults: dict[str, str | None] | None = None,
) -> str | None:
    """Resolve a source's effective tier.

    Precedence: explicit ``credibilityTier`` (must be a valid T0-T4 form) >
    ``sourceKind`` tier-form (T0-T4) > registry default > None (neutral).
    Malformed values ("T9", "t1", "T1 ") resolve to None — never crash.
    """
    for candidate in (credibility_tier, source_kind):
        if candidate in _TIER_FORM:
            return candidate
    if source_kind:
        table = registry_defaults if registry_defaults is not None else SOURCE_KIND_DEFAULTS
        hint = table.get(source_kind)
        if hint in _TIER_FORM:
            return hint
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp. None/malformed → None (no decay)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # naive → UTC (#153)  # noqa: UP017
        return dt
    except (ValueError, TypeError):
        return None


def decay_factor(
    source_date: str | None,
    ingested_at: str | None,
    now: datetime | None = None,
    recency_decay: float = 0.95,
    tier: str | None = None,
) -> float:
    """Recency decay factor in [0, 1] — 1.0 = no decay.

    Keys on ``sourceDate`` (evidence age) else ``ingestedAt`` (arrival — documented
    proxy). T0 sources are exempt (factor 1.0, per ontology §10 "T0 ages
    differently than T4" + #122). Future dates clamp to 1.0; pre-epoch dates clamp
    to 1.0 (max(0, years)); malformed/missing dates → 1.0 (no decay — safe).
    """
    if tier == "T0":
        return 1.0
    ts = _parse_timestamp(source_date) or _parse_timestamp(ingested_at)
    if ts is None:
        return 1.0
    now_ts = (now or datetime.now(timezone.utc)).timestamp()  # noqa: UP017
    years = max(0.0, (now_ts - ts.timestamp()) / (365.25 * 86400.0))
    return float(recency_decay ** years)


def aggregate_prior(
    groups: Iterable[tuple[str, float, float, int]],
    *,
    recency_decay: float = 0.95,
    now: datetime | None = None,
) -> tuple[float, float]:
    """Aggregate source evidence into a Beta prior (alpha, beta).

    ``groups``: iterable of (tier, source_date, ingested_at, count_effective) —
    one entry per DISTINCT SOURCE, where count_effective is the edge weight
    (default 1.0; a per-source assessment factor can be folded in by the caller
    as an extra multiplier — see ``assessment_factor``).

    Pinned formula (scoping resolution B, plan Task 1):
        pc_t = log2(N_t + 1) * decay_t * (sum_{i in t} base_pc(tier_i) * factor_i) / N_t
    where ``decay_t`` keys on the TIER's MOST-RECENT source (T0 exempt), and
    ``factor_i`` is the caller-provided per-source weight (assessment factor).

    Properties:
      - N=1 degenerates to base_pc * decay (matches #122 formula → recency tests green)
      - monotone in source addition when added sources are not materially
        lower-weighted than the tier mean (doc S3.1/S6 = uniform-weight addition)
      - anti-Sybil: 1000 x T4 pc ~= 0.997 < 1 x T3 pc = 1.0 (quality beats quantity)
    """
    # Group by tier, tracking most-recent date for decay_t
    from collections import defaultdict
    tier_data: dict[str, list[tuple[float, float]]] = defaultdict(list)  # tier -> [(factor, pc)]
    tier_dates: dict[str, list[datetime]] = defaultdict(list)
    for tier, source_date, ingested_at, factor in groups:
        if tier not in _TIER_FORM:
            continue
        tier_data[tier].append((float(factor), pc_base(tier)))
        ts = _parse_timestamp(source_date) or _parse_timestamp(ingested_at)
        if ts is not None:
            tier_dates[tier].append(ts)

    total_pc = 0.0
    for tier, entries in tier_data.items():
        n = len(entries)
        # decay_t from the tier's MOST-RECENT source date (anti-sybil preserving;
        # adding ancient sources cannot lower a tier's decay)
        most_recent = max(tier_dates[tier]) if tier_dates[tier] else None
        if tier != "T0" and most_recent is not None:
            now_ts = (now or datetime.now(timezone.utc)).timestamp()  # noqa: UP017
            years = max(0.0, (now_ts - most_recent.timestamp()) / (365.25 * 86400.0))
            decay_t = float(recency_decay ** years)
        else:
            decay_t = 1.0
        mean_weight = sum(w for w, _pc in entries) / n
        total_pc += math.log2(n + 1.0) * decay_t * mean_weight * pc_base(tier)

    return (1.0 + total_pc, 1.0)


def assessment_factor(
    assessments: Iterable[tuple[float, float]],
    *,
    k: float = ASSESSMENT_K,
) -> float:
    """Aggregate reputation-weighted assessments into a pc multiplier.

    ``assessments``: iterable of (assessor_reputation, score) — reputation is
    snapshotted at assessment write time (never live-read at aggregation).

    Formula: 1 + k * sum_a (rep_a - 0.5) * (score_a - 0.5), clamped [0.1, 2.0].
      - rep = 0.5 (zero track record) or score = 0.5 contributes 0 (neutral)
      - k = 1.0: single assessor swing is +-0.25; clamps need ~4 assessors
      - NaN / +/-inf inputs → 1.0 (defense-in-depth)
    """
    total = 0.0
    for rep, score in assessments:
        try:
            term = (float(rep) - 0.5) * (float(score) - 0.5)
        except (TypeError, ValueError):
            continue
        if math.isnan(term) or math.isinf(term):
            continue
        total += term
    factor = 1.0 + k * total
    return max(FACTOR_MIN, min(FACTOR_MAX, factor))


def derive_reliability(
    *,
    tier: str | None,
    source_date: str | None,
    ingested_at: str | None,
    recency_decay: float = 0.95,
    now: datetime | None = None,
    assessments: Iterable[tuple[float, float]] | None = None,
) -> tuple[float | None, dict]:
    """Derive a source's reliability (0-1) + component breakdown.

    Tiered: mean of the modulated prior — reliability = mean_from_beta(1 + pc_eff, 1)
    where pc_eff = pc_base(tier) * decay * factor. This is the SAME number the EP
    inheritance adapter uses as the source's base weight (consistency invariant).

    Untiered + no assessments: None (reason 'untiered').
    Untiered + assessments: reputation-weighted mean of scores (display-only —
    untiered sources never feed EP).

    Returns (reliability, components_dict). Never blends means in probability
    space for the EP-facing value.
    """
    components: dict = {
        "tier": tier,
        "decay": 1.0,
        "factor": 1.0,
        "pc_eff": 0.0,
        "assessment_count": 0,
        "assessment_weighted_mean": None,
        "reason": None,
    }
    assessments = list(assessments or [])
    if tier in _TIER_FORM:
        decay = decay_factor(source_date, ingested_at, now, recency_decay, tier=tier)
        factor = assessment_factor(assessments)
        pc = pc_base(tier) * decay * factor
        alpha, beta = 1.0 + pc, 1.0
        components.update({
            "decay": decay,
            "factor": factor,
            "pc_eff": pc,
            "assessment_count": len(assessments),
        })
        return mean_from_beta(alpha, beta), components
    # Untiered
    if assessments:
        rep_sum = sum(rep for rep, _score in assessments)
        weighted = sum(rep * score for rep, score in assessments) / rep_sum if rep_sum else 0.0
        components.update({
            "assessment_count": len(assessments),
            "assessment_weighted_mean": weighted,
            "reason": "untiered; assessment-only",
        })
        return max(0.0, min(1.0, weighted)), components
    components["reason"] = "untiered"
    return None, components
