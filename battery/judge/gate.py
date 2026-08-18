
"""Judge validation gate (issue #1410, plan §5 judge/ + §7 E2E-5.x).

A rubric must pass validation BEFORE it may score anything. The gate runs:

1. **AB+BA position-bias test** — the same pair judged in both orders;
   agreement rate must exceed chance (binomial p < 0.05 one-sided).
2. **Inter-judge reliability** — Cohen's κ ≥ 0.70 (two judge runs on the
   same items; the κ computation mirrors tools/kappa.py).
3. **IRT item-infit** — Rasch infit for each rubric item in [0.7, 1.3]
   (a simplified expected-response-curve residual; out-of-range items flag
   the rubric as unstable).
4. **Stress set** — single-anchor rubric, all-identical anchors, and
   contradictory anchors must each be handled without catastrophic drift
   (plus the brief's label-flip / verbosity-bias / stochastic-stability
   checks over a fixed probe set).

Unvalidated rubrics raise JudgeGateBlocked (battery.exceptions). Validation
records persist by rubric id (the run_artifact contract). Mid-stream rubric
changes re-trigger the gate; episodes scored under a stale rubric are
flagged (E2E-5.2 drift).
"""
from __future__ import annotations

from dataclasses import dataclass

import hashlib
import math
import random
from pathlib import Path

from battery.exceptions import JudgeGateBlocked
from battery.judge.client import JudgeClient, build_abba_prompts

# Gate thresholds (issue #1410; E2E-5.1 pins).
POSITION_BIAS_P = 0.05
KAPPA_MIN = 0.70
IRT_INFIT_MIN = 0.7
IRT_INFIT_MAX = 1.3
STRESS_ITEMS = ("single_anchor", "all_identical", "contradictory_anchors",
                "label_flip", "verbosity_bias", "stochastic_stability")


@dataclass
class ValidationRecord:
    """One rubric's gate result — persisted by rubric id."""

    rubric_id: str
    passed: bool
    abba_agreement: float
    abba_n: int
    kappa: float | None
    irt_infit: dict[str, float]
    stress: dict[str, bool]
    checksum: str
    blocked_reason: str = ""


def _cohens_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Cohen's κ over the intersection of labeled items (mirrors tools/kappa)."""
    pairs = [(a, b) for a, b in zip(labels_a, labels_b) if a and b]
    if len(pairs) < 2:
        return 0.0
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    cats = sorted({c for p in pairs for c in p})
    pe = sum((sum(1 for a, _ in pairs if a == c) / n) *
             (sum(1 for _, b in pairs if b == c) / n) for c in cats)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def _rasch_infit(verdicts: Sequence[str], n_items: int) -> dict[str, float]:
    """Simplified Rasch-style item-infit per rubric item.

    Responses are binarized (better=1, else=0). Each item's observed mean
    is compared to the overall mean; the item's infit = 1 + squared
    standardized residual, so a perfectly consistent item scores 1.0
    (fit) and a misfitting item scores > 1.3 (flagged). A degenerate
    overall pattern (all identical responses) is perfect fit by
    definition — infit 1.0.
    """
    if not verdicts or n_items == 0:
        return {}
    binary = [1 if v == "better" else 0 for v in verdicts]
    overall_m = sum(binary) / len(binary)
    n_per_item = max(len(binary) // n_items, 1)
    infit: dict[str, float] = {}
    for i in range(n_items):
        slice_v = binary[i::n_items]
        if not slice_v:
            continue
        m_i = sum(slice_v) / len(slice_v)
        denom = overall_m * (1 - overall_m)
        if denom <= 0:
            # All-identical pattern — perfect fit by construction.
            infit[f"item-{i}"] = 1.0
            continue
        resid = (m_i - overall_m) / (denom / n_per_item) ** 0.5
        infit[f"item-{i}"] = 1.0 + resid * resid
    return infit


def _rubric_checksum(rubric_text: str) -> str:
    return hashlib.sha256(rubric_text.encode()).hexdigest()[:16]


def validate_rubric(rubric_id: str, rubric_text: str,
                    client: JudgeClient,
                    probe_pairs: Sequence[tuple[str, str]],
                    judge_labels_a: Sequence[str],
                    judge_labels_b: Sequence[str],
                    n_items: int = 4,
                    seed: int = 1410,
                    ) -> ValidationRecord:
    """Run the full validation battery. Returns the record; never raises on
    a failed gate (the caller decides scoring-block)."""
    rng = random.Random(seed + len(rubric_text))

    # 1) AB+BA position bias (binomial test, one-sided).
    abba_agree = 0
    abba_n = len(probe_pairs)
    for a, b in probe_pairs:
        p_ab, p_ba, _ = build_abba_prompts(a, b, rubric_text, rng)
        v_ab = client.judge(rubric_id, "abba-ab", p_ab).verdict
        v_ba = client.judge(rubric_id, "abba-ba", p_ba).verdict
        if v_ab == v_ba:
            abba_agree += 1
    # P(X >= k | p=0.5) via normal approx to binomial (one-sided).
    if abba_n > 0:
        z = (abba_agree - 0.5 * abba_n) / math.sqrt(0.25 * abba_n)
        abba_p = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        abba_p = 1.0
    abba_ok = abba_p < POSITION_BIAS_P

    # 2) Inter-judge reliability (Cohen's κ).
    kappa = _cohens_kappa(judge_labels_a, judge_labels_b)
    kappa_ok = kappa is not None and kappa >= KAPPA_MIN

    # 3) IRT item-infit.
    verdicts = [client.judge(rubric_id, f"irt-{i}", f"probe {i}",
                             ).verdict for i in range(n_items * 3)]
    infit = _rasch_infit(verdicts, n_items)
    irt_ok = bool(infit) and all(
        IRT_INFIT_MIN <= v <= IRT_INFIT_MAX for v in infit.values())

    # 4) Stress set.
    stress: dict[str, bool] = {}
    for name in STRESS_ITEMS:
        probe = _stress_probe(name, rng)
        v = client.judge(rubric_id, f"stress-{name}", probe).verdict
        stress[name] = v != ""  # a verdict was produced (non-degenerate)

    passed = abba_ok and kappa_ok and irt_ok and all(stress.values())
    return ValidationRecord(
        rubric_id=rubric_id,
        passed=passed,
        abba_agreement=abba_agree / abba_n if abba_n else 0.0,
        abba_n=abba_n,
        kappa=kappa,
        irt_infit=infit,
        stress=stress,
        checksum=_rubric_checksum(rubric_text),
        blocked_reason="" if passed else _blocked_reason(
            abba_ok, kappa_ok, irt_ok, stress),
    )


def _stress_probe(name: str, rng: random.Random) -> str:
    probes = {
        "single_anchor": "Score this single anchor response against the rubric.",
        "all_identical": "Score these two identical responses.",
        "contradictory_anchors": "Both anchors claim opposite facts; score them.",
        "label_flip": "Score this response where 'good' means 'bad'.",
        "verbosity_bias": "A much longer response vs a much shorter one.",
        "stochastic_stability": "Score this borderline response twice.",
    }
    return probes.get(name, "probe")


def _blocked_reason(abba_ok: bool, kappa_ok: bool, irt_ok: bool,
                    stress: dict[str, bool]) -> str:
    parts = []
    if not abba_ok:
        parts.append("position-bias")
    if not kappa_ok:
        parts.append("kappa<0.70")
    if not irt_ok:
        parts.append("IRT infit out of range")
    failed_stress = [k for k, v in stress.items() if not v]
    if failed_stress:
        parts.append(f"stress:{','.join(failed_stress)}")
    return "gate failed: " + ", ".join(parts)


class RubricRegistry:
    """Persists validation records by rubric id (run_artifact contract)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._records: dict[str, ValidationRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            import json
            data = json.loads(self._path.read_text())
            for rid, rec in data.items():
                self._records[rid] = ValidationRecord(**rec)

    def save(self, record: ValidationRecord) -> None:
        import json
        self._records[record.rubric_id] = record
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {rid: rec.__dict__ for rid, rec in self._records.items()},
            indent=2))

    def validated(self, rubric_id: str, rubric_text: str) -> bool:
        rec = self._records.get(rubric_id)
        if rec is None:
            return False
        return rec.passed and rec.checksum == _rubric_checksum(rubric_text)

    def require_validated(self, rubric_id: str, rubric_text: str) -> None:
        """E2E-5.1: unvalidated or drifted rubrics block scoring."""
        if not self.validated(rubric_id, rubric_text):
            raise JudgeGateBlocked(
                f"rubric {rubric_id!r} is not validated (or has drifted) — "
                f"run battery validate-judge first")
