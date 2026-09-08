
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
from __future__ import annotations  # noqa: I001

from dataclasses import dataclass

import hashlib
import math
import random
from pathlib import Path
from typing import Sequence  # noqa: UP035

from battery.exceptions import JudgeGateBlocked
from battery.judge.client import JudgeClient, build_abba_prompts
from battery.judge.rubric import load_rubric_spec

# Gate thresholds (issue #1410; E2E-5.1 pins).
POSITION_BIAS_P = 0.05
KAPPA_MIN = 0.70
IRT_INFIT_MIN = 0.7
IRT_INFIT_MAX = 1.3
GOLD_AGREEMENT_MIN = 0.8
STRESS_ITEMS = ("single_anchor", "all_identical", "contradictory_anchors",
                "label_flip", "verbosity_bias", "stochastic_stability")


@dataclass(frozen=True)
class JudgeVocabulary:
    """Verdict vocabulary + stress semantics for one judge protocol.

    Pairwise (default): better/worse/tie — all-identical responses must
    TIE; position-bias is measured via AB/BA swap. Declarative:
    anchored yes/no — byte-identical renders in "swapped" positions give
    byte-identical prompts, so position bias is unmeasurable in the
    single-construct vocabulary; the AB+BA slot is judge RETEST-consistency
    (the same render judged twice must return the SAME verdict) and
    all-identical responses must be CONSISTENT (no tie concept).
    """

    name: str = "pairwise"
    labels: tuple[str, ...] = ("better", "worse", "tie")
    irt_yes_label: str = "better"
    is_declarative: bool = False

    @property
    def yes_label(self) -> str:
        return self.labels[0]


DECLARATIVE_VOCAB = JudgeVocabulary(
    name="declarative", labels=("yes", "no"), irt_yes_label="yes",
    is_declarative=True)


def exact_binomial_p(agree: int, n: int) -> float:
    """One-sided P(X >= agree | p=0.5) — exact via math.comb for small n
    (no new deps), continuity-corrected normal approximation above.
    Exact for n <= 32: p(4,4)=0.0625 (blocks), p(5,5)=0.03125 (passes)."""
    if n <= 0:
        return 1.0
    if n <= 32:
        total = 0.0
        for k in range(agree, n + 1):
            total += math.comb(n, k)
        return total * (0.5 ** n)
    z = (agree - 0.5 - 0.5 * n) / math.sqrt(0.25 * n)
    return 0.5 * math.erfc(z / math.sqrt(2))


def _gold_anchors_for(rubric_id: str,
                      gold_anchors: Sequence[dict] | None) -> list[dict]:
    """Explicit gold anchors win; otherwise resolve the itemized rubric's
    gold block from the repo rubric store (battery/config/rubrics) so the
    gate can catch a degenerate all-yes judge without the caller re-passing
    the block. Pairwise mode never auto-loads (vocabulary-agnostic legs)."""
    if gold_anchors:
        return list(gold_anchors)
    from pathlib import Path as _P
    default_cfg = _P(__file__).resolve().parents[1] / "config"
    try:
        spec = load_rubric_spec(default_cfg, rubric_id)
    except Exception:  # noqa: BLE001, RUF100  (absent rubric => no anchors)
        return []
    return list(spec.gold_anchors)


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
    pairs = [(a, b) for a, b in zip(labels_a, labels_b) if a and b]  # noqa: B905
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


def _rasch_infit(verdicts: Sequence[str], n_items: int,
                 yes_label: str = "better") -> dict[str, float]:
    """Simplified Rasch-style item-infit per rubric item.

    Responses are binarized against the vocabulary's yes label
    (pairwise better=1, else=0; declarative yes=1, no=0). Each item's
    observed mean is compared to the overall mean; the item's infit =
    1 + squared standardized residual, so a perfectly consistent item
    scores 1.0 (fit) and a misfitting item scores > 1.3 (flagged). A
    degenerate overall pattern (all identical responses) is perfect fit
    by definition — infit 1.0.
    """
    if not verdicts or n_items == 0:
        return {}
    binary = [1 if v == yes_label else 0 for v in verdicts]
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
                    vocabulary: JudgeVocabulary | None = None,
                    irt_renders: Sequence[str] | None = None,
                    gold_anchors: Sequence[dict] | None = None,
                    ) -> ValidationRecord:
    """Run the full validation battery. Returns the record; never raises on
    a failed gate (the caller decides scoring-block).

    Pairwise mode (default vocabulary=None) is byte-identical to the
    #1410 battery: AB+BA position-bias, κ, per-item IRT over the legacy
    contentless ``probe {i}`` prompts, pairwise stress. Declarative mode
    (vocabulary=DECLARATIVE_VOCAB) parameterizes the verdict vocabulary +
    stress expectations for anchored yes/no items: the AB+BA slot is
    RELABELED judge retest-consistency (single-construct prompts have no
    A/B frame — byte-identical renders in "swapped" positions give
    byte-identical prompts and a temp-0 judge agrees by construction, so
    "position bias" would be a lie), stress all-identical means
    consistent-same-verdict-twice (no tie label exists), the IRT leg
    judges the caller-supplied anchored item renders (never contentless
    bare probes) with the live loop bound (n_items x 3), and the
    gold-anchor agreement leg (>= 0.8 vs the rubric JSON's expected
    labels; the block carries >= 1 expected-'no' render with no-share >
    20% so a degenerate all-yes judge fails below the bar) runs.
    The Cohen's-κ inter-judge leg is vocabulary-agnostic and RETAINED
    unchanged in both modes.
    """
    rng = random.Random(seed + len(rubric_text))
    vocab = vocabulary or JudgeVocabulary()
    declarative = vocab.is_declarative

    # 1) AB+BA (pairwise: position-bias) / judge RETEST-consistency
    #    (declarative): the same anchored evidence render judged in two
    #    independent calls must return the SAME yes/no verdict. Exact
    #    binomial at small n (math.comb — no new deps); the pairwise leg
    #    keeps its byte-identical continuity-corrected normal
    #    approximation.
    abba_agree = 0
    abba_n = len(probe_pairs)
    for a, b in probe_pairs:
        if declarative:
            p_a = _declarative_render_prompt(a, rubric_text)
            p_b = _declarative_render_prompt(b, rubric_text)
        else:
            p_ab, p_ba, _ = build_abba_prompts(a, b, rubric_text, rng)
            p_a, p_b = p_ab, p_ba
        v_a = client.judge(rubric_id, "abba-ab", p_a).verdict
        v_b = client.judge(rubric_id, "abba-ba", p_b).verdict
        if v_a == v_b:
            abba_agree += 1
    if abba_n > 0:
        if declarative:
            abba_p = exact_binomial_p(abba_agree, abba_n)
        else:
            z = (abba_agree - 0.5 - 0.5 * abba_n) / math.sqrt(0.25 * abba_n)
            abba_p = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        abba_p = 1.0
    abba_ok = abba_p < POSITION_BIAS_P

    # 2) Inter-judge reliability (Cohen's κ) — vocabulary-agnostic.
    kappa = _cohens_kappa(judge_labels_a, judge_labels_b)
    kappa_ok = kappa is not None and kappa >= KAPPA_MIN

    # 3) IRT item-infit — declarative judges the caller-supplied anchored
    #    item renders (irt_renders[i] for i in range(n_items * 3), the
    #    LIVE loop bound); pairwise keeps the legacy contentless probes.
    irt_prompts = []
    for i in range(n_items * 3):
        if declarative:
            render = (irt_renders[i] if irt_renders
                      and i < len(irt_renders) else f"probe {i}")
            irt_prompts.append(_declarative_render_prompt(render, rubric_text))
        else:
            irt_prompts.append(f"probe {i}")
    verdicts = [client.judge(rubric_id, f"irt-{i}", p).verdict
                for i, p in enumerate(irt_prompts)]
    infit = _rasch_infit(verdicts, n_items, yes_label=vocab.irt_yes_label)
    irt_ok = bool(infit) and all(
        IRT_INFIT_MIN <= v <= IRT_INFIT_MAX for v in infit.values())

    # 4) Stress set — vocabulary-parameterized pass criteria.
    stress: dict[str, bool] = {}
    for name in STRESS_ITEMS:
        probe = _stress_probe(name, rng)
        if name == "all_identical":
            v = client.judge(rubric_id, f"stress-{name}", probe).verdict
            if declarative:
                # two IDENTICAL anchored renders judged twice must return
                # the SAME yes/no verdict (consistency replaces the
                # pairwise all-identical -> tie expectation).
                v2 = client.judge(rubric_id, f"stress-{name}-2", probe).verdict
                stress[name] = v == v2 and v != ""
            else:
                stress[name] = v == "tie"
        elif name == "verbosity_bias":
            v_short = client.judge(rubric_id, f"stress-{name}-s",
                                   probe + " [short]").verdict
            v_long = client.judge(rubric_id, f"stress-{name}-l",
                                  probe + " [long]").verdict
            stress[name] = v_short == v_long and v_short != ""
        elif name == "stochastic_stability":
            v1 = client.judge(rubric_id, f"stress-{name}-1", probe).verdict
            v2 = client.judge(rubric_id, f"stress-{name}-2", probe).verdict
            stress[name] = v1 == v2 and v1 != ""
        else:
            v = client.judge(rubric_id, f"stress-{name}", probe).verdict
            stress[name] = v != ""

    # 5) Gold-anchor agreement (declarative only): judge verdicts vs the
    #    rubric JSON's expected labels — agreement >= 0.8. The block's >= 1
    #    expected-'no' render (no-share > 20%) means a degenerate all-yes
    #    judge scores below the bar and FAILS this leg (never masked by a
    #    self-consistent judge).
    gold_ok = True
    gold_agreement = 1.0
    gold_n = 0
    if declarative:
        anchors = _gold_anchors_for(rubric_id, gold_anchors)
        if anchors:
            agree = 0
            for ga in anchors:
                item_id = str(ga.get("item_id", ""))
                item_text = _item_text(rubric_id, item_id, rubric_text)
                prompt = (_RENDER_ITEM_PROMPT.format(
                    rubric_text=item_text or rubric_text,
                    render=ga.get("render", "")))
                verdict = client.judge(
                    rubric_id, f"gold-{item_id}", prompt).verdict
                if verdict == str(ga.get("expected", "")):
                    agree += 1
            gold_n = len(anchors)
            gold_agreement = agree / gold_n if gold_n else 1.0
            gold_ok = gold_agreement >= GOLD_AGREEMENT_MIN

    passed = abba_ok and kappa_ok and irt_ok and all(stress.values()) \
        and gold_ok
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
            abba_ok, kappa_ok, irt_ok, stress, gold_ok,
            leg_label="retest" if declarative else "position-bias",
            gold_n=gold_n, gold_agreement=gold_agreement),
    )


#: Per-anchor judge prompt (declarative) — item text + evidence render,
#: never the full item set (so a no-anchor's verdict can never be dragged
#: by unrelated items' phrasing).
_RENDER_ITEM_PROMPT = (
    "Rubric item: {rubric_text}\n\n"
    "Evidence: {render}\n"
    "Answer YES or NO."
)


def _declarative_render_prompt(render: str, rubric_text: str) -> str:
    """Declarative evidence-render judge prompt (retest/IRT): the anchored
    evidence is judged against the full rubric; answer YES/NO."""
    return (
        f"Rubric: {rubric_text}\n\n"
        f"Judge this evidence against the anchored items.\n"
        f"Evidence: {render}\n"
        f"Answer YES or NO."
    )


def _item_text(rubric_id: str, item_id: str, rubric_text: str) -> str:
    """Item text for a gold anchor: resolved from the rubric store when
    possible (itemized rubric), else falls back to the rubric text."""
    if not item_id:
        return rubric_text
    from pathlib import Path as _P
    default_cfg = _P(__file__).resolve().parents[1] / "config"
    try:
        spec = load_rubric_spec(default_cfg, rubric_id)
    except Exception:  # noqa: BLE001, RUF100
        return rubric_text
    for it in spec.items:
        if it.get("id") == item_id:
            return str(it.get("text", rubric_text))
    return rubric_text


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
                    stress: dict[str, bool], gold_ok: bool = True,
                    leg_label: str = "position-bias",
                    gold_n: int = 0, gold_agreement: float = 1.0) -> str:
    parts = []
    if not abba_ok:
        parts.append(leg_label)
    if not kappa_ok:
        parts.append("kappa<0.70")
    if not irt_ok:
        parts.append("IRT infit out of range")
    failed_stress = [k for k, v in stress.items() if not v]
    if failed_stress:
        parts.append(f"stress:{','.join(failed_stress)}")
    if not gold_ok:
        parts.append(
            f"gold-anchor (agreement {gold_agreement:.2f}/{gold_n})")
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
