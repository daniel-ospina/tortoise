"""L2 — SEA-Eval sequential stream / pseudo-evolution gate (E2E-2.1).

Repeated task families; genuine evolution ⇒ token consumption converges
DOWNWARD as task frequency rises (strategy reuse replaces zero-shot
reasoning). Metrics: token-trajectory reduction ≥30% by rep 3 [cal];
strategy-reuse rate rising; held-out family SR ≥ prior-wave SR baseline.
Flat tokens while the graph grows = PSEUDO-EVOLUTION FAIL.

⚠️ PROVISIONAL: the trajectory-gate design is single-source (SEA-Eval
2604.08988) — the gate stays, labeled provisional.
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


class L2PseudoEvolutionStream:
    stream_id = "L2"
    metric = "token_reduction_pct"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        """sessions: per-repetition traces {family, rep, tokens}. Computes
        the token reduction from rep 1 to rep 3 within each family."""
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        families: dict[str, dict[int, float]] = {}
        for s in sessions:
            fam = s.get("family", "?")
            rep = int(s.get("rep", 1))
            families.setdefault(fam, {})[rep] = float(s.get("tokens", 0))
        reductions: list[float] = []
        for fam, reps in families.items():
            if 1 in reps and 3 in reps and reps[1] > 0:
                reductions.append(1 - reps[3] / reps[1])
        if not reductions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        mean = sum(reductions) / len(reductions)
        traj = tuple(reductions)
        return StreamResult(
            self.stream_id, self.metric, mean, mean >= threshold,
            threshold, trajectory=traj,
            evidence=(f"mean_reduction={mean:.2f} families={len(reductions)} "
                      f"⚠️ provisional gate (SEA-Eval single-source)",))

    def strategy_reuse_trend(self, sessions: list[dict[str, Any]]) -> bool:
        """Strategy-reuse rate must RISE across repetitions."""
        by_rep: dict[int, list[float]] = {}
        for s in sessions:
            by_rep.setdefault(int(s.get("rep", 1)), []).append(
                float(s.get("strategy_reuse_rate", 0.0)))
        reps = sorted(by_rep)
        if len(reps) < 2:
            return False
        return (sum(by_rep[reps[-1]]) / len(by_rep[reps[-1]])
                > sum(by_rep[reps[0]]) / len(by_rep[reps[0]]))

    def held_out_baseline(self, held_out_sr: float, prior_wave_sr: float) -> bool:
        """Contamination control: held-out family SR ≥ prior-wave SR."""
        return held_out_sr >= prior_wave_sr
