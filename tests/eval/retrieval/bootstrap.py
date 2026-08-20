"""Paired bootstrap CIs + the #1144 quality gate.

Gate semantics (locked design, issue comment 2026-08-17): every retrieval
optimization cycle ships only if speed improved AND quality did not regress.
The quality gate compares a NEW run against the BASELINE run, paired on
identical query ids:

    ΔnDCG@10 (points) = (new nDCG − baseline nDCG) × 100, per query

    90% paired bootstrap CI on the mean Δ:
        CI.lower ≥ −2 points            → SHIP
        −4 ≤ CI.lower < −2 points       → WARN
        CI.lower < −4 points            → BLOCK
        BLOCK also on a SIGNIFICANT P@5 drop: the paired 90% CI on mean
        ΔP@5 (points) lies entirely below 0 (excludes 0).

Bootstrap: resample QUERY INDICES with replacement (n_resamples), compute
the resampled mean delta, percentile CI at alpha/2 .. 1−alpha/2 (alpha =
0.10 → 90% CI). Pairing is on identical queries — the same query id must
appear in both runs; deltas are computed per paired query (unpaired
queries are dropped and counted).

Deterministic: `rng` seeds the resampling so gate results reproduce.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence  # noqa: UP035

ALPHA = 0.10                       # 90% CI
DEFAULT_N_RESAMPLES = 2000
GATE_WARN_LOWER = -2.0             # points; CI.lower >= -2 → SHIP
GATE_BLOCK_LOWER = -4.0            # points; CI.lower < -4 → BLOCK


@dataclass
class ConfidenceInterval:
    lower: float
    upper: float
    mean: float
    n: int                 # number of paired observations

    def to_dict(self) -> dict:
        return {
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "mean": round(self.mean, 4),
            "n": self.n,
            "level": f"{int((1 - ALPHA) * 100)}%",
        }


def paired_bootstrap_ci(
    deltas: Sequence[float],
    n_resamples: int = DEFAULT_N_RESAMPLES,
    alpha: float = ALPHA,
    rng: random.Random | None = None,
) -> ConfidenceInterval:
    """90% percentile bootstrap CI on the MEAN of per-query deltas.

    Resamples the observation indices with replacement. Edge cases:
    empty deltas → (0, 0) CI with n=0; constant deltas → CI collapses to
    that constant (a deterministic delta has zero sampling variance).
    """
    deltas = list(deltas)
    if not deltas:
        return ConfidenceInterval(0.0, 0.0, 0.0, 0)
    rng = rng or random.Random(1144)
    n = len(deltas)
    means: list[float] = []
    for _ in range(n_resamples):
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * (n_resamples - 1))]
    hi = means[int((1 - alpha / 2) * (n_resamples - 1))]
    return ConfidenceInterval(
        lower=lo, upper=hi, mean=sum(deltas) / n, n=n,
    )


def one_sample_ci(
    values: Sequence[float],
    n_resamples: int = DEFAULT_N_RESAMPLES,
    alpha: float = ALPHA,
    rng: random.Random | None = None,
) -> ConfidenceInterval:
    """90% percentile bootstrap CI on the MEAN of one sample (the per-run
    uncertainty of a single strategy's metric)."""
    return paired_bootstrap_ci(values, n_resamples, alpha, rng)


def paired_deltas(
    new: dict[str, float],
    baseline: dict[str, float],
    scale: float = 100.0,
) -> tuple[list[float], int]:
    """Per-query deltas (new − baseline) paired on identical query ids.

    Returns (deltas, dropped) — `dropped` counts queries in one run but
    not the other (they cannot be paired). Values are scaled to "points"
    (nDCG 0..1 → 0..100) by default.
    """
    common = sorted(set(new) & set(baseline))
    dropped = len(set(new) ^ set(baseline))
    deltas = [
        (new[qid] - baseline[qid]) * scale
        for qid in common
        if isinstance(new[qid], (int, float)) and isinstance(baseline[qid], (int, float))
    ]
    return deltas, dropped


@dataclass
class GateDecision:
    verdict: str     # SHIP | WARN | BLOCK
    reason: str
    ndcg_ci: ConfidenceInterval
    p5_ci: ConfidenceInterval | None = None

    def to_dict(self) -> dict:
        d = {
            "verdict": self.verdict,
            "reason": self.reason,
            "ndcg_delta_points": self.ndcg_ci.to_dict(),
        }
        if self.p5_ci is not None:
            d["p5_delta_points"] = self.p5_ci.to_dict()
        return d


def quality_gate(
    ndcg_deltas: Sequence[float],
    p5_deltas: Sequence[float],
    *,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    alpha: float = ALPHA,
    rng: random.Random | None = None,
) -> GateDecision:
    """Apply the #1144 gate semantics to paired per-query deltas.

    ndcg_deltas / p5_deltas are in POINTS (×100) already. The verdict:

        SHIP  iff CI.lower(ΔnDCG) ≥ −2 AND no significant P@5 drop
        WARN  iff −4 ≤ CI.lower(ΔnDCG) < −2
        BLOCK iff CI.lower(ΔnDCG) < −4 OR significant P@5 drop
                (paired 90% CI on ΔP@5 entirely below 0)
    """
    ndcg_ci = paired_bootstrap_ci(ndcg_deltas, n_resamples, alpha, rng)
    p5_ci = paired_bootstrap_ci(p5_deltas, n_resamples, alpha, rng)
    p5_drop = p5_ci.upper < 0.0  # 90% CI excludes 0 below → significant drop
    if ndcg_ci.lower < GATE_BLOCK_LOWER:
        verdict = "BLOCK"
        reason = (
            f"ΔnDCG@10 90% CI lower {ndcg_ci.lower:.1f} points < "
            f"{GATE_BLOCK_LOWER} — quality regression beyond the block band"
        )
    elif p5_drop:
        verdict = "BLOCK"
        reason = (
            f"significant P@5 drop: paired 90% CI on ΔP@5 "
            f"[{p5_ci.lower:.2f}, {p5_ci.upper:.2f}] points excludes 0 below"
        )
    elif ndcg_ci.lower >= GATE_WARN_LOWER:
        verdict = "SHIP"
        reason = (
            f"ΔnDCG@10 90% CI [{ndcg_ci.lower:.1f}, {ndcg_ci.upper:.1f}] "
            f"does not exclude −{abs(GATE_WARN_LOWER):.0f} points — ship"
        )
    else:
        verdict = "WARN"
        reason = (
            f"ΔnDCG@10 90% CI lower {ndcg_ci.lower:.1f} points in the warn "
            f"band [{GATE_BLOCK_LOWER:.0f}, {GATE_WARN_LOWER:.0f}) — proceed "
            "with caution"
        )
    return GateDecision(verdict, reason, ndcg_ci, p5_ci)
