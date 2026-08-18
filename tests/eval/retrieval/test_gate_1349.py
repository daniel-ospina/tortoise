"""Task 4 (#1349) — gate_1349.py: the full pre-registered embedder-selection rule.

The gate decides whether a model swap ships — every branch is mechanically
tested on synthetic reports with KNOWN verdicts (the T4 checkpoint reviewed
BEFORE the T8 evidence burn uses the gate).

Coverage per the locked rule:
  * decision-rule outcomes: PASS / NO-WINNER / INSUFFICIENT-POWER,
    absolute-fallback, multi-winner tiebreak (combined rank → E2E-8 → size
    → family-preserving), escalation triggers (i)/(ii)/(iii), escalation
    PASS / judge-unavailable → NO-WINNER,
  * 6-config → 3-family reduction (all-6-configs input, m=6 bars),
  * manifest validation: missing-config, report_sha, denominator
    (mixed-n/--limit, hybrid-fed-as-vector, asymmetric sets, dropped >5%),
    code_sha drift (scoped-path fails, docs/ passes, per-config mismatch),
    resolved_revision (per-model), checkpoint_state,
  * precondition branches: product-call.json, HNSW spot-check (present +
    cleared, escalation waiver), E2E-8 ≤300ms, #265 non-384 status,
  * P@10/P@5 reporting, split-config tie rule, n-adaptive bar (n=200/300/500).
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

from tests.eval.retrieval import gate_1349
from tests.eval.retrieval.bootstrap import (
    bh_fdr,
    one_sided_bootstrap_p,
)
from tests.eval.retrieval.gate_1349 import (
    ABSOLUTE_FALLBACK,
    DROPPED_FRACTION,
    E2E8_LIMIT_MS,
    FAMILY_ORDER,
    MIN_PAIRED_N,
    Z,
    n_adaptive_bar,
    question_set_fingerprint,
)

GATE_N = 300
ABS_N = 10
TOTAL = GATE_N + ABS_N
CODE_SHA = "sha-burn-1"
REV = "rev-minilm"
PRE = {"product_call": "product-call.json",
       "hnsw_spotcheck": "hnsw-spotcheck.json",
       "e2e8": {"minilm": "e2e8-minilm.json",
                 "bge-small": "e2e8-bge-small.json",
                 "arctic-xs": "e2e8-arctic-xs.json",
                 "arctic-s": "e2e8-arctic-s.json"},
       "issue_265": {"status": "not-landed", "note": "verified on main"}}


def _qts() -> list[str]:
    return (["single-session-user"] * 75 + ["multi-session"] * 75
            + ["temporal-reasoning"] * 75 + ["knowledge-update"] * 75)


def _qids() -> list[str]:
    return [f"lme_{i:04d}" for i in range(GATE_N)]


def _abs_qids() -> list[str]:
    return [f"lme_{i:04d}_abs" for i in range(GATE_N, GATE_N + ABS_N)]


def _vals(mean: float, sd: float, n: int = GATE_N, seed: int = 1349) -> list[float]:
    rng = random.Random(seed)
    return [min(1.0, max(0.0, rng.gauss(mean, sd))) for _ in range(n)]


def _shift(vals: list[float], delta: float) -> list[float]:
    return [min(1.0, max(0.0, v + delta)) for v in vals]


def _full(gate_vals: list[float], abs_vals: list[float] | None = None) -> list[float]:
    return list(gate_vals) + list(abs_vals if abs_vals is not None
                                  else [0.30] * ABS_N)


def make_outcome(qid: str, qt: str, tr10: float, ndcg: float,
                 *, p10: float | None = None, p5: float | None = None,
                 breaker: bool = False) -> dict:
    if breaker:
        return {"question_id": qid, "question_type": qt, "label": None,
                "hypothesis": None, "retriever": "vector",
                "breaker_open": True, "dropped_reason": "breaker_open",
                "total_ms": 100.0}
    return {
        "question_id": qid, "question_type": qt, "label": None,
        "hypothesis": None, "retriever": "vector",
        "session_recall@k": {"5": tr10, "10": tr10, "20": tr10},
        "turn_recall@k": {"5": tr10, "10": tr10, "20": tr10},
        "ndcg@10": ndcg,
        "p@10": p10 if p10 is not None else tr10,
        "p@5": p5 if p5 is not None else tr10,
        "ranked_ids": [], "evidence_turn_matches": [],
        "context_tokens": 1000, "context_point_count": 20,
        "retrieval_latency_ms": 100.0, "reader_latency_ms": 0.0,
        "judge_latency_ms": 0.0, "total_ms": 100.0,
    }


def make_report(outcomes: list[dict], *, model: str, prompt: str | None,
                retriever: str = "vector", split: str = "s",
                surface: str = "embedded", git_sha: str = CODE_SHA) -> dict:
    done = [o for o in outcomes if not o.get("breaker_open")]
    trs = [o["turn_recall@k"]["10"] for o in done]
    ndcgs = [o["ndcg@10"] for o in done if o.get("ndcg@10") is not None]
    return {
        "benchmark": "LongMemEval",
        "dataset": "xiaowu0162/longmemeval-cleaned",
        "split": split,
        "n_questions": len(done),
        "accuracy": None,
        "retrieval": {
            "session_recall@k": {"5": sum(trs) / len(trs) if trs else 0.0,
                                 "10": sum(trs) / len(trs) if trs else 0.0,
                                 "20": sum(trs) / len(trs) if trs else 0.0},
            "turn_recall@k": {"5": sum(trs) / len(trs) if trs else 0.0,
                              "10": sum(trs) / len(trs) if trs else 0.0,
                              "20": sum(trs) / len(trs) if trs else 0.0},
            "ndcg@10": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
            "p@10": 0.0, "p@5": 0.0,
        },
        "dropped": {"n": sum(1 for o in outcomes if o.get("breaker_open")),
                    "breaker_open": sum(1 for o in outcomes
                                        if o.get("dropped_reason") == "breaker_open"),
                    "questions": [o["question_id"] for o in outcomes
                                  if o.get("breaker_open")]},
        "n_dropped": sum(1 for o in outcomes if o.get("breaker_open")),
        "n_failed": 0,
        "methodology": {"model": model, "query_prompt": prompt,
                        "retriever": retriever, "surface": surface,
                        "git_sha": git_sha, "checkpoint_key": "x"},
        "outcomes": outcomes,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_cfg(tmp_path: Path, name: str, model: str, prompt: str | None, *,
              tr10: list[float], ndcg: list[float], revision: str = REV,
              code_sha: str = CODE_SHA, n: int | None = None,
              checkpoint_state: str = "complete", retriever: str = "vector",
              dropped: tuple[str, ...] = (), split: str = "s",
              filename: str | None = None) -> dict:
    qids = _qids() + _abs_qids()
    qts = _qts() + ["single-session-user"] * ABS_N
    outcomes = [
        make_outcome(qid, qts[i], tr10[i], ndcg[i], breaker=qid in dropped)
        for i, qid in enumerate(qids)
    ]
    report = make_report(outcomes, model=model, prompt=prompt,
                         retriever=retriever, split=split)
    path = tmp_path / (filename or f"report-{name}.json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    done = len(outcomes) - len(dropped)
    return {"name": name, "model": model, "prompt": prompt,
            "resolved_revision": revision, "n": n if n is not None else done,
            "checkpoint_state": checkpoint_state, "report_sha": _sha(path),
            "code_sha": code_sha, "report": str(path)}


def base_values(ctrl_sd: float = 0.40) -> dict:
    """Control turn_recall@10 + nDCG@10 arrays (mean ≈ 0.40, sd = ctrl_sd)."""
    ctrl_tr = _vals(0.40, ctrl_sd)
    ctrl_ndcg = _vals(0.40, ctrl_sd, seed=1350)
    return {"ctrl_tr": ctrl_tr, "ctrl_ndcg": ctrl_ndcg}


def escalation_deltas(mean: float = 0.024, sd: float = 0.23,
                      seed: int = 7, n: int = GATE_N) -> list[float]:
    """Deterministic deltas with EXACT mean/sd (z-scored, then scaled). The
    empirical one-sided bootstrap p lands in (0.0167, 0.10) — escalation
    trigger (i) fires while BH-FDR does NOT reject (p > q/m). Values are
    written raw (no clipping) so the deltas stay exact."""
    import statistics as _st
    rng = random.Random(seed)
    z = [rng.gauss(0.0, 1.0) for _ in range(n)]
    m = sum(z) / len(z)
    s = _st.stdev(z)
    return [mean + sd * (zi - m) / s for zi in z]


def build_burn(
    tmp_path: Path,
    *,
    deltas: dict[str, dict[str, float]] | None = None,
    abs_deltas: dict[str, dict[str, float]] | None = None,
    code_sha: str = CODE_SHA,
    revisions: dict[str, str] | None = None,
    probe_revisions: dict[str, str] | None = None,
    checkpoint_states: dict[str, str] | None = None,
    dropped: dict[str, tuple[str, ...]] | None = None,
    preconditions: dict | None = None,
    escalation_judged: dict | None = None,
    retriever: str = "vector",
    n_overrides: dict[str, int] | None = None,
    report_retrievers: dict[str, str] | None = None,
    ctrl_sd: float = 0.40,
) -> dict:
    """Write a full 6-config synthetic burn into tmp_path; return the manifest.

    ``deltas[config] = {"turn_recall@10": d, "ndcg@10": d2}`` — the family
    values are control + d (per gate question; abstention questions shift by
    ``abs_deltas`` — default 0.0 so the _abs filter is value-neutral unless
    a test says otherwise).
    """
    bv = base_values(ctrl_sd=ctrl_sd)
    ctrl_tr, ctrl_ndcg = bv["ctrl_tr"], bv["ctrl_ndcg"]
    names = ("minilm", "bge-small", "arctic-xs", "arctic-xs-query",
             "arctic-s", "arctic-s-query")
    models = {"minilm": "minilm", "bge-small": "bge-small",
              "arctic-xs": "arctic-xs", "arctic-xs-query": "arctic-xs",
              "arctic-s": "arctic-s", "arctic-s-query": "arctic-s"}
    prompts = {"minilm": None, "bge-small": None, "arctic-xs": None,
               "arctic-xs-query": "query", "arctic-s": None,
               "arctic-s-query": "query"}
    deltas = deltas or {n: {"turn_recall@10": 0.0, "ndcg@10": 0.0} for n in names}
    abs_deltas = abs_deltas or {}
    revisions = revisions or {}
    checkpoint_states = checkpoint_states or {}
    dropped = dropped or {}
    report_retrievers = report_retrievers or {}
    n_overrides = n_overrides or {}

    configs = []
    for name in names:
        model = models[name]
        d = deltas.get(name, {"turn_recall@10": 0.0, "ndcg@10": 0.0})
        ad = abs_deltas.get(name, {"turn_recall@10": 0.0, "ndcg@10": 0.0})
        is_ctrl = name == "minilm"
        if is_ctrl:
            tr = list(ctrl_tr)
            ndcg = list(ctrl_ndcg)
        else:
            tr = _shift(ctrl_tr, d["turn_recall@10"])
            ndcg = _shift(ctrl_ndcg, d["ndcg@10"])
        tr = _full(tr, _shift([0.30] * ABS_N, ad["turn_recall@10"]))
        ndcg = _full(ndcg, _shift([0.30] * ABS_N, ad["ndcg@10"]))
        cfg = write_cfg(
            tmp_path, name, model, prompts[name], tr10=tr, ndcg=ndcg,
            revision=revisions.get(name, REV if is_ctrl else f"rev-{model}"),
            code_sha=code_sha,
            checkpoint_state=checkpoint_states.get(name, "complete"),
            retriever=report_retrievers.get(name, retriever),
            dropped=dropped.get(name, ()),
        )
        if name in n_overrides:
            cfg["n"] = n_overrides[name]
        configs.append(cfg)

    if preconditions is None:
        preconditions = dict(PRE)
        preconditions["e2e8"] = {m: f"e2e8-{m}.json"
                                 for m in ("minilm", "bge-small", "arctic-xs",
                                           "arctic-s")}
        _write_precondition_files(tmp_path, preconditions)
    manifest = {
        "split": "s",
        "retriever": retriever,
        "probe_revisions": probe_revisions or {
            "minilm": REV, "bge-small": "rev-bge-small",
            "arctic-xs": "rev-arctic-xs", "arctic-s": "rev-arctic-s"},
        "configs": configs,
        "preconditions": preconditions,
    }
    if escalation_judged is not None:
        manifest["escalation_judged"] = escalation_judged
    return manifest


def _write_precondition_files(tmp_path: Path, preconditions: dict) -> None:
    (tmp_path / "product-call.json").write_text(json.dumps({
        "decision": "server-side", "timestamp": "2026-08-17T12:00:00Z",
        "recorder": "t8"}), encoding="utf-8")
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps({
        "cleared": True, "n": GATE_N, "winner": "arctic-s", "control": "minilm",
        "metric_deltas": {
            "turn_recall@10": {"n": GATE_N, "mean_delta": 0.05,
                               "one_sided_p": 0.0, "deltas": [0.05] * GATE_N},
            "ndcg@10": {"n": GATE_N, "mean_delta": 0.0, "one_sided_p": 1.0,
                        "deltas": [0.0] * GATE_N},
        }}), encoding="utf-8")
    for m in ("minilm", "bge-small", "arctic-xs", "arctic-s"):
        (tmp_path / f"e2e8-{m}.json").write_text(json.dumps({
            "arms": {"e2e": {"censored_p95_ms": 250.0, "verdict": "achieved"}},
        }), encoding="utf-8")


def write_spotcheck(tmp_path: Path, *, winner: str = "arctic-s",
                    cleared: bool = True, n: int = GATE_N) -> None:
    """Overwrite the HNSW spot-check artifact with a winner-consistent one."""
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps({
        "cleared": cleared, "n": n, "winner": winner, "control": "minilm",
        "metric_deltas": {
            "turn_recall@10": {"n": n, "mean_delta": 0.05 if cleared else 0.0,
                               "one_sided_p": 0.0 if cleared else 0.5,
                               "deltas": ([0.05] * n if cleared else [0.0] * n)},
            "ndcg@10": {"n": n, "mean_delta": 0.0,
                        "one_sided_p": 1.0, "deltas": [0.0] * n},
        }}), encoding="utf-8")


def run_gate(tmp_path: Path, manifest: dict, **kw) -> dict:
    """Run the gate with fast bootstrap resamples + stubbed git (tests only)."""
    kw.setdefault("n_resamples", 150)
    kw.setdefault("manifest_dir", tmp_path)
    kw.setdefault("repo_root", tmp_path)
    kw.setdefault("head_sha_fn", lambda repo: CODE_SHA)
    kw.setdefault("changed_files_fn", lambda sha, repo: [])
    return gate_1349.run_gate(manifest, **kw)


def write_escalation_artifact(tmp_path: Path, *, winner: str = "arctic-s",
                              gain: float = 0.08, p_ok: bool = True,
                              judge_available: bool = True,
                              fingerprint: str | None = None,
                              per_question: dict | None = None,
                              config: str | None = None,
                              n: int = GATE_N) -> dict:
    bv = base_values()
    ctrl = bv["ctrl_tr"]
    if per_question is None:
        deltas = [gain] * GATE_N if p_ok else [gain * 0.3] * GATE_N
        per_question = {}
        for i, qid in enumerate(_qids()):
            c = ctrl[i]
            per_question[qid] = {"winner": min(1.0, c + deltas[i]),
                                 "control": c}
    if fingerprint is None:
        fingerprint = question_set_fingerprint(_qids())
    artifact = {
        "producer": "tools/longmem_eval/run.py (escalation judged run)",
        "surface": "hnsw", "winner": winner, "control": "minilm",
        "config": config,
        "judge_id": "openrouter:test", "n": n,
        "judge_available": judge_available,
        "question_set_fingerprint": fingerprint,
        "per_question": per_question,
    }
    path = tmp_path / f"escalation-judged-{winner}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return {"path": str(path), "sha": _sha(path)}


# ── n-adaptive bar (pure function, m=6 pins) ────────────────────────────────

def test_n_adaptive_bar_m6_pins():
    """Pre-registered pins: m=6, q=0.10, z=2.128, sd=0.40, control=0.40 →
    +15.1% / +12.3% / +9.5% at n=200/300/500 (the plan's ≈ values; exact
    computation: 15.05/12.28/9.51). As n falls the bar rises."""
    bars = {n: n_adaptive_bar(0.40, n, 0.40) for n in (200, 300, 500)}
    assert bars[200] == pytest.approx(0.151, abs=0.001)
    assert bars[300] == pytest.approx(0.123, abs=0.001)
    assert bars[500] == pytest.approx(0.095, abs=0.001)
    assert bars[200] > bars[300] > bars[500]
    # z pin: the m=6 top-rank one-sided threshold q/m = 0.0167 ↔ z≈2.128.
    assert Z == pytest.approx(2.128, abs=0.001)


def test_n_adaptive_bar_degenerate_control():
    assert n_adaptive_bar(0.40, 300, 0.0) == float("inf")


def test_n_adaptive_bar_computes_z_from_q_over_m():
    """P2 fix: z is derived from q/m INSIDE the bar (Φ⁻¹(1−q/m)) — the
    hardcoded z=2.128 baked in q/m=0.0167 and the q/m params were dead."""
    import statistics as _st
    assert _st.NormalDist().inv_cdf(1 - 0.10 / 6) == pytest.approx(
        2.128, abs=0.002)
    # m=6/q=0.10 pin is preserved.
    assert n_adaptive_bar(0.40, 300, 0.40) == pytest.approx(0.123, abs=0.001)
    # q and m are LIVE: a stricter FDR level raises the bar.
    assert n_adaptive_bar(0.40, 300, 0.40, q=0.05, m=6) > \
        n_adaptive_bar(0.40, 300, 0.40, q=0.10, m=6)


# ── decision-rule outcomes ──────────────────────────────────────────────────

def test_pass_single_family_clears(tmp_path):
    """arctic-s clears turn_recall@10 (constant +0.06 → p=0, +15% relative,
    BH-rejected at m=6) → PASS(arctic-s). The other families are ≈ control.
    This is also the dependent-deltas case (all family deltas derive from
    the same control noise — BH under PRDS):"""
    deltas = {"arctic-s": {"turn_recall@10": 0.06, "ndcg@10": 0.0},
              "arctic-s-query": {"turn_recall@10": 0.06, "ndcg@10": 0.0}}
    manifest = build_burn(tmp_path, deltas=deltas)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-s)"
    assert out["blocked"] is False
    fam = out["families"]["arctic-s"]
    assert fam["winner"] is True
    m = fam["metrics"]["turn_recall@10"]
    assert m["cleared"] is True
    assert m["bh_rejected"] is True
    assert m["p"] == 0.0
    assert m["relative_delta"] >= 0.05
    # ndcg did not clear for arctic-s; nothing cleared for the others.
    assert fam["metrics"]["ndcg@10"]["cleared"] is False
    assert out["families"]["bge-small"]["winner"] is False
    assert out["swap_config"] in ("arctic-s", "arctic-s-query")
    # m=6 p-values reported.
    assert len(out["bh"]["pvals"]) == 6
    # The n-adaptive bar is derived from the ACTUAL per-question data:
    # bar = z·(sd/√n)/control_mean with the empirical paired deltas.
    import statistics as _st
    ctrl = base_values()["ctrl_tr"]
    deltas = [min(1.0, max(0.0, v + 0.06)) - v for v in ctrl]
    bar = n_adaptive_bar(_st.stdev(deltas), len(deltas),
                         sum(ctrl) / len(ctrl))
    assert out["bars"]["arctic-s"]["turn_recall@10"] == pytest.approx(
        round(bar, 4), abs=1e-9)


def test_no_winner(tmp_path):
    manifest = build_burn(tmp_path)  # all families ≈ control
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert all(not f["winner"] for f in out["families"].values())
    # no escalation trigger fires (deltas ~0, control < 0.50, no category gain)
    assert out["escalation"]["triggered"] is False


def test_insufficient_power_small_n(tmp_path):
    """Paired n < 200 → INSUFFICIENT-POWER with the judgment-tiebreak
    disposition (mini-BEIR OOD → labeled-pair calibration → nDCG@10)."""
    n_small = 150
    deltas = {"arctic-s": {"turn_recall@10": 0.06, "ndcg@10": 0.0},
              "arctic-s-query": {"turn_recall@10": 0.06, "ndcg@10": 0.0}}
    manifest = build_burn(tmp_path, deltas=deltas)
    # --limit subset: only the first n_small questions are in every report.
    for cfg in manifest["configs"]:
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["outcomes"] = report["outcomes"][:n_small]
        report["n_questions"] = n_small
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["n"] = n_small
        cfg["report_sha"] = _sha(report_path)
    write_spotcheck(tmp_path, n=n_small)  # spot-check on the same subset
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "INSUFFICIENT-POWER"
    assert out["insufficient_power"]["n_paired_min"] < MIN_PAIRED_N
    assert out["insufficient_power"]["judgment_tiebreak"] == [
        "mini-beir-ood", "labeled-pair-calibration", "ndcg@10"]


def test_insufficient_power_degenerate_control_absolute_fallback(tmp_path):
    """Control turn_recall@10 < 0.05 → INSUFFICIENT-POWER; a candidate
    clearing absolute ≥ 0.30 is surfaced as the absolute-delta fallback."""
    # Control is near-zero recall; the candidate is strong in absolute terms.
    manifest = build_burn(tmp_path)
    ctrl_tr = _vals(0.02, 0.01)
    ctrl_ndcg = _vals(0.30, 0.10, seed=1350)
    for cfg in manifest["configs"]:
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        is_ctrl = cfg["model"] == "minilm"
        for i, o in enumerate(report["outcomes"]):
            if o.get("breaker_open"):
                continue
            if is_ctrl:
                tr = ctrl_tr[i] if i < GATE_N else 0.02
                nd = ctrl_ndcg[i] if i < GATE_N else 0.30
            elif i < GATE_N:  # candidate: absolute ≈ 0.42 on gate questions
                tr = 0.42
                nd = 0.42
            else:
                tr = 0.10
                nd = 0.10
            o["turn_recall@k"] = {"5": tr, "10": tr, "20": tr}
            o["session_recall@k"] = dict(o["turn_recall@k"])
            o["ndcg@10"] = nd
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["report_sha"] = _sha(report_path)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "INSUFFICIENT-POWER"
    ip = out["insufficient_power"]
    assert ip["control_turn_recall@10"] < 0.05
    assert set(ip["absolute_fallback_clearances"]) == {
        "bge-small", "arctic-xs", "arctic-s"}
    assert all(v >= ABSOLUTE_FALLBACK
               for v in ip["absolute_fallback_clearances"].values())


def test_absolute_fallback_does_not_fire_below_030(tmp_path):
    manifest = build_burn(tmp_path)
    ctrl_tr = _vals(0.02, 0.01)
    for cfg in manifest["configs"]:
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        is_ctrl = cfg["model"] == "minilm"
        for i, o in enumerate(report["outcomes"]):
            if o.get("breaker_open"):
                continue
            if is_ctrl:
                tr = ctrl_tr[i] if i < GATE_N else 0.02
                nd = 0.20
            elif i < GATE_N:
                tr = 0.20
                nd = 0.20
            else:
                tr = 0.20
                nd = 0.20
            o["turn_recall@k"] = {"5": tr, "10": tr, "20": tr}
            o["session_recall@k"] = dict(o["turn_recall@k"])
            o["ndcg@10"] = nd
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["report_sha"] = _sha(report_path)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "INSUFFICIENT-POWER"
    # 0.20 < 0.30 → no absolute clearances.
    assert out["insufficient_power"]["absolute_fallback_clearances"] == {}


def test_multiwinner_combined_rank(tmp_path):
    """Three families clear; the winner is the argmax of the combined rank
    (turn_recall@10 rank + nDCG@10 rank) — genuinely decisive here: tr ranks
    {arctic-xs 1, bge 2, arctic-s 3}, ndcg ranks {arctic-s 1, arctic-xs 2,
    bge 3} → combined {arctic-xs 3, arctic-s 4, bge 5} → arctic-xs."""
    deltas = {
        "bge-small": {"turn_recall@10": 0.06, "ndcg@10": 0.02},
        "arctic-xs": {"turn_recall@10": 0.09, "ndcg@10": 0.03},
        "arctic-xs-query": {"turn_recall@10": 0.09, "ndcg@10": 0.03},
        "arctic-s": {"turn_recall@10": 0.03, "ndcg@10": 0.09},
        "arctic-s-query": {"turn_recall@10": 0.03, "ndcg@10": 0.09},
    }
    manifest = build_burn(tmp_path, deltas=deltas)
    write_spotcheck(tmp_path, winner="arctic-xs")
    out = run_gate(tmp_path, manifest)
    # All three families clear both metrics (floors 15%/5%, 22.5%/7.5%,
    # 7.5%/22.5%; constant deltas → p=0 → BH rejects).
    assert all(f["winner"] for f in out["families"].values())
    assert out["verdict"] == "PASS(arctic-xs)"
    assert out["tiebreak_evidence"]["combined_rank"] == {
        "arctic-xs": 3, "arctic-s": 4, "bge-small": 5}


def test_multiwinner_e2e8_tiebreak(tmp_path):
    """Two winners tied on combined rank → lower E2E-8 latency decides."""
    deltas = {
        "bge-small": {"turn_recall@10": 0.08, "ndcg@10": 0.02},
        "arctic-xs": {"turn_recall@10": 0.01, "ndcg@10": 0.08},
        "arctic-xs-query": {"turn_recall@10": 0.01, "ndcg@10": 0.08},
        "arctic-s": {"turn_recall@10": 0.03, "ndcg@10": 0.03},
        "arctic-s-query": {"turn_recall@10": 0.03, "ndcg@10": 0.03},
    }
    # bge wins turn_recall@10 (rank 1), arctic-xs wins ndcg@10 (rank 1) →
    # combined 3 vs 3 → E2E-8: bge 260ms, arctic-xs 200ms → arctic-xs.
    pre = dict(PRE)
    pre["e2e8"] = {"minilm": "e2e8-minilm.json",
                   "bge-small": "e2e8-bge-small.json",
                   "arctic-xs": "e2e8-arctic-xs.json",
                   "arctic-s": "e2e8-arctic-s.json"}
    _write_precondition_files(tmp_path, pre)
    (tmp_path / "e2e8-bge-small.json").write_text(json.dumps(
        {"arms": {"e2e": {"censored_p95_ms": 260.0, "verdict": "achieved"}}}),
        encoding="utf-8")
    (tmp_path / "e2e8-arctic-xs.json").write_text(json.dumps(
        {"arms": {"e2e": {"censored_p95_ms": 200.0, "verdict": "achieved"}}}),
        encoding="utf-8")
    manifest = build_burn(tmp_path, deltas=deltas, preconditions=pre)
    write_spotcheck(tmp_path, winner="arctic-xs")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-xs)"
    assert out["tiebreak_evidence"]["e2e8_p95_ms"]["arctic-xs"] == 200.0


def test_family_reduction_all_six_configs(tmp_path):
    """6-config → 3-family: the arctic family delta per metric = max of its
    2 configs; m=6 p-values total."""
    deltas = {"arctic-xs": {"turn_recall@10": 0.02, "ndcg@10": 0.0},
              "arctic-xs-query": {"turn_recall@10": 0.08, "ndcg@10": 0.0}}
    manifest = build_burn(tmp_path, deltas=deltas)
    write_spotcheck(tmp_path, winner="arctic-xs")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-xs)"
    m = out["families"]["arctic-xs"]["metrics"]["turn_recall@10"]
    assert m["best_config"] == "arctic-xs-query"  # max of the 2 configs
    # expected delta = the clipped construction's actual delta (values near
    # 1.0 clip, slightly shrinking the constant delta)
    ctrl = base_values()["ctrl_tr"]
    expected = (sum(min(1.0, max(0.0, v + 0.08)) - v for v in ctrl)
                / len(ctrl))
    assert m["mean_delta"] == pytest.approx(round(expected, 4), abs=1e-9)
    assert out["swap_config"] == "arctic-xs-query"
    assert len(out["bh"]["pvals"]) == 6  # 3 families × 2 metrics


def test_split_config_tie_rule_single_metric(tmp_path):
    """The family clears ONLY turn_recall@10; arctic-xs-query is the argmax
    on that metric → swap config = arctic-xs-query (not combined rank)."""
    deltas = {"arctic-xs": {"turn_recall@10": 0.03, "ndcg@10": 0.0},
              "arctic-xs-query": {"turn_recall@10": 0.08, "ndcg@10": 0.0}}
    manifest = build_burn(tmp_path, deltas=deltas)
    write_spotcheck(tmp_path, winner="arctic-xs")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-xs)"
    assert out["families"]["arctic-xs"]["metrics"]["turn_recall@10"]["cleared"]
    assert out["swap_config"] == "arctic-xs-query"
    assert out["split_config_rule"] == "argmax-on-cleared-metric"


def test_split_config_tie_rule_combined_rank(tmp_path):
    """Different arctic configs win different metrics (both clear) → swap
    config = argmax combined rank."""
    deltas = {"arctic-s": {"turn_recall@10": 0.06, "ndcg@10": 0.02},
              "arctic-s-query": {"turn_recall@10": 0.02, "ndcg@10": 0.08},
              "arctic-xs": {"turn_recall@10": 0.03, "ndcg@10": 0.03},
              "arctic-xs-query": {"turn_recall@10": 0.03, "ndcg@10": 0.03},
              "bge-small": {"turn_recall@10": 0.01, "ndcg@10": 0.01}}
    manifest = build_burn(tmp_path, deltas=deltas)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-s)"
    fam = out["families"]["arctic-s"]
    assert fam["metrics"]["turn_recall@10"]["cleared"]
    assert fam["metrics"]["ndcg@10"]["cleared"]
    # no-prefix wins turn_recall@10, query wins ndcg@10; both cleared →
    # combined rank decides (query: 1+1=2 < 3).
    assert out["swap_config"] == "arctic-s-query"
    assert out["split_config_rule"] == "combined-rank"


def test_p10_p5_reported(tmp_path):
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert "p@10" in out["families"]["arctic-s"]
    assert "p@5" in out["families"]["arctic-s"]
    assert out["control"]["p@10"] > 0.0
    # p@10 mirrors turn_recall@10 in the fixture; expected = clipped delta
    ctrl = base_values()["ctrl_tr"]
    expected = (sum(min(1.0, max(0.0, v + 0.06)) for v in ctrl) - sum(ctrl)) \
        / len(ctrl)
    assert out["families"]["arctic-s"]["p@10"]["mean_delta"] == pytest.approx(
        round(expected, 4), abs=1e-9)


def test_paired_ci90_reported_not_gating(tmp_path):
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    ci = out["families"]["arctic-s"]["metrics"]["turn_recall@10"]["ci90"]
    assert set(ci) >= {"lower", "upper", "mean", "n"}
    assert ci["n"] == GATE_N


# ── escalation branch ───────────────────────────────────────────────────────

def _apply_escalation_deltas(manifest: dict) -> None:
    """Overwrite the arctic-s family reports with control + escalation
    deltas (mean +0.024, sd 0.23, EXACT — written raw, no clipping). The
    empirical one-sided p lands in (0.0167, 0.10): trigger (i) fires but
    BH-FDR does NOT reject (a burn winner would pre-empt escalation)."""
    deltas = escalation_deltas(0.024, 0.23, seed=7)
    bv = base_values(ctrl_sd=0.15)  # the burn control used by build_burn
    ctrl = bv["ctrl_tr"]
    for cfg in manifest["configs"]:
        if cfg["model"] != "arctic-s":
            continue
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for i, o in enumerate(report["outcomes"]):
            if o.get("breaker_open") or i >= GATE_N:
                continue
            v = ctrl[i] + deltas[i]
            o["turn_recall@k"] = {"5": v, "10": v, "20": v}
            o["session_recall@k"] = dict(o["turn_recall@k"])
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["report_sha"] = _sha(report_path)


def test_escalation_trigger_i_positive_delta(tmp_path):
    """Trigger (i): ≥1 family delta positive at p<0.10 pre-FDR on EITHER
    co-primary metric. With no judged artifact recorded → pre-registered
    NO-WINNER with the negative evidence (missing judged run)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    out = run_gate(tmp_path, manifest)
    esc = out["escalation"]
    assert esc["triggered"] is True
    assert "positive-delta-p<0.10" in esc["triggers"]
    assert out["verdict"] == "NO-WINNER"
    assert esc["judged"]["pass"] is False
    assert "artifact" in esc["judged"]["reason"].lower()


def test_escalation_trigger_ii_control_ceiling(tmp_path):
    """Control turn_recall@10 ≥ 0.50 (ceiling-compressed) → trigger (ii)."""
    manifest = build_burn(tmp_path)  # all deltas 0
    # Control at ceiling: rewrite every report's turn_recall@10 to ~0.55.
    for cfg in manifest["configs"]:
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for o in report["outcomes"]:
            if o.get("breaker_open"):
                continue
            o["turn_recall@k"] = {"5": 0.55, "10": 0.55, "20": 0.55}
            o["session_recall@k"] = dict(o["turn_recall@k"])
            o["ndcg@10"] = 0.55
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["report_sha"] = _sha(report_path)
    out = run_gate(tmp_path, manifest)
    assert out["escalation"]["triggered"] is True
    assert "control-ceiling>=0.50" in out["escalation"]["triggers"]
    assert out["control"]["turn_recall@10"] >= 0.50


def test_escalation_trigger_iii_category_gain(tmp_path):
    """Per-category gain ≥ +5pp on a paper category fires trigger (iii) even
    with a NEGATIVE overall delta (no trigger (i), control < 0.50)."""
    deltas = {"arctic-s": {"turn_recall@10": -0.06, "ndcg@10": -0.06},
              "arctic-s-query": {"turn_recall@10": -0.06, "ndcg@10": -0.06}}
    manifest = build_burn(tmp_path, deltas=deltas)
    # arctic-s gains ONLY on temporal-reasoning (questions 150-225): +0.12
    # boost on a −0.06 base → temporal delta +0.06 ≥ +5pp; everywhere else
    # −0.06 → overall mean = (75·0.06 − 225·0.06)/300 = −0.03 < 0.
    for cfg in manifest["configs"]:
        if cfg["model"] != "arctic-s":
            continue
        report_path = Path(cfg["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for i, o in enumerate(report["outcomes"]):
            if o.get("breaker_open") or i >= GATE_N:
                continue
            if 150 <= i < 225:  # temporal-reasoning block
                tr = o["turn_recall@k"]["10"] + 0.12
                nd = o["ndcg@10"] + 0.12
            else:
                tr = o["turn_recall@k"]["10"]
                nd = o["ndcg@10"]
            o["turn_recall@k"] = {"5": tr, "10": tr, "20": tr}
            o["session_recall@k"] = dict(o["turn_recall@k"])
            o["ndcg@10"] = nd
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        cfg["report_sha"] = _sha(report_path)
    out = run_gate(tmp_path, manifest)
    esc = out["escalation"]
    assert esc["triggered"] is True
    assert "per-category-gain>=5pp" in esc["triggers"]
    assert out["verdict"] == "NO-WINNER"  # no judged artifact provided


def test_escalation_pass_judged_artifact(tmp_path):
    """Escalation fires (trigger (i)); the judged artifact's winner clears
    ≥+5% relative with one-sided p<0.10 pre-FDR → PASS(winner). The HNSW
    spot-check is WAIVED for escalation winners (satisfied by the judged
    run itself on the production HNSW surface)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    esc = write_escalation_artifact(tmp_path, winner="arctic-s", gain=0.08)
    manifest["escalation_judged"] = esc
    # the standard HNSW spot-check is intentionally absent (waived).
    pre = dict(PRE)
    pre.pop("hnsw_spotcheck")
    manifest["preconditions"] = pre
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-s)"
    assert out["escalation"]["judged"]["pass"] is True
    assert out["escalation"]["judged"]["relative_gain"] >= 0.05
    assert out["escalation"]["judged"]["one_sided_p"] < 0.10
    assert out["hnsw_waived"] is True
    assert out["blocked"] is False


def test_escalation_judge_unavailable_no_winner(tmp_path):
    """Judge unavailable/non-answers → NO-WINNER with the negative evidence
    (pre-registered outcome). The standard HNSW spot-check is WAIVED — the
    escalation run supersedes it even when the judged pass fails."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    esc = write_escalation_artifact(tmp_path, winner="arctic-s",
                                    judge_available=False)
    manifest["escalation_judged"] = esc
    # the standard HNSW spot-check is absent (superseded by the escalation run)
    pre = dict(PRE)
    pre.pop("hnsw_spotcheck")
    manifest["preconditions"] = pre
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert out["hnsw_waived"] is True
    assert out["blocked"] is False
    assert out["escalation"]["judged"]["pass"] is False
    assert "judge" in out["escalation"]["judged"]["reason"].lower()


def test_escalation_judged_fails_criterion(tmp_path):
    """Judged artifact present but the winner does NOT clear ≥+5% relative →
    NO-WINNER (pre-registered)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    # judged gain +0.01 → +2.5% relative < 5% → NO-WINNER.
    esc = write_escalation_artifact(tmp_path, winner="arctic-s", gain=0.01)
    manifest["escalation_judged"] = esc
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert out["escalation"]["judged"]["pass"] is False
    assert out["escalation"]["judged"]["relative_gain"] < 0.05


def test_escalation_judged_question_set_fingerprint_pin(tmp_path):
    """A judged run on a cherry-picked question subset (fingerprint mismatch)
    is rejected — the FULL filtered question set is pinned."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    subset = _qids()[:150]  # cherry-picked half
    esc = write_escalation_artifact(
        tmp_path, winner="arctic-s", gain=0.08,
        fingerprint=question_set_fingerprint(subset))
    manifest["escalation_judged"] = esc
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert "fingerprint" in out["escalation"]["judged"]["reason"].lower()


def test_escalation_judged_per_q_must_cover_full_burn_set(tmp_path):
    """P0 fix: an artifact declaring the FULL-set fingerprint but carrying
    per_question for only HALF the burn questions must NOT pass — the
    fingerprint pins the question set, not the per-question coverage. With
    the HNSW spot-check WAIVED on the escalation path, that subset would be
    the ONLY gate on shipping (each subset question +0.08 → p=0)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    subset = _qids()[:150]  # 150/300 — declared fp covers the full set
    ctrl = base_values(ctrl_sd=0.15)["ctrl_tr"]
    per_q = {qid: {"winner": ctrl[i] + 0.08, "control": ctrl[i]}
             for i, qid in enumerate(subset)}
    esc = write_escalation_artifact(
        tmp_path, winner="arctic-s",
        fingerprint=question_set_fingerprint(_qids()),
        per_question=per_q)
    manifest["escalation_judged"] = esc
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert "per_question" in out["escalation"]["judged"]["reason"].lower()


def test_escalation_pass_uses_judged_config(tmp_path):
    """P2 fix: the escalation judged artifact records which config ran on
    HNSW; when present, THAT config is the swap config (the vector-burn
    config deltas are from the embedded surface, not the judged run)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    esc = write_escalation_artifact(tmp_path, winner="arctic-s", gain=0.08,
                                    config="arctic-s-query")
    manifest["escalation_judged"] = esc
    pre = dict(PRE)
    pre.pop("hnsw_spotcheck")
    manifest["preconditions"] = pre
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-s)"
    assert out["escalation"]["judged"]["pass"] is True
    assert out["swap_config"] == "arctic-s-query"
    assert out["split_config_rule"] == "judged-run-config"


def test_escalation_judged_degenerate_control(tmp_path):
    """P2 fix: judged control accuracy below the CONTROL_DEGENERATE floor
    makes the relative gain meaningless (a near-zero denominator inflates
    gain toward inf) → NO-WINNER, mirroring the main-rule floor."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    per_q = {qid: {"winner": 0.02, "control": 0.001} for qid in _qids()}
    esc = write_escalation_artifact(tmp_path, winner="arctic-s",
                                    per_question=per_q)
    manifest["escalation_judged"] = esc
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert "degenerate control" in out["escalation"]["judged"]["reason"]


def test_escalation_top2_selection(tmp_path):
    """The escalation block reports the top-2 families by combined rank
    (control always judged as the third)."""
    manifest = build_burn(tmp_path, ctrl_sd=0.15)
    _apply_escalation_deltas(manifest)
    out = run_gate(tmp_path, manifest)
    esc = out["escalation"]
    assert len(esc["top2"]) == 2
    assert set(esc["top2"]) <= set(FAMILY_ORDER)
    assert esc["judged_families"] == esc["top2"] + ["minilm"]  # control always


# ── manifest validation ─────────────────────────────────────────────────────

def test_missing_config_hard_fail(tmp_path):
    manifest = build_burn(tmp_path)
    manifest["configs"] = [c for c in manifest["configs"]
                           if c["name"] != "bge-small"]
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("config" in r.lower() for r in out["blocking_reasons"])


def test_duplicate_config_names_block(tmp_path):
    """P1 fix: report_data is keyed by config name — two configs sharing a
    name silently overwrite, dropping one run's evidence from family
    reduction (model counts and len(configs)==6 still pass). Must
    HARD-FAIL."""
    manifest = build_burn(tmp_path)
    manifest["configs"][3]["name"] = "arctic-xs"  # arctic-xs-query → dup
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("duplicate config names" in r for r in out["blocking_reasons"])


def test_arctic_family_requires_vendor_prompt_pair(tmp_path):
    """P2 fix: an arctic family must run BOTH the no-prefix config and the
    vendor prompt_name="query" config — two no-prefix arctic configs
    silently drop the vendor-prompt evidence from family reduction."""
    manifest = build_burn(tmp_path)
    manifest["configs"][3]["prompt"] = None  # arctic-xs-query → no-prefix
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("prompt_name" in r for r in out["blocking_reasons"])


def test_report_sha_mismatch_hard_fail(tmp_path):
    manifest = build_burn(tmp_path)
    path = Path(manifest["configs"][1]["report"])
    # tamper with the on-disk report AFTER the manifest was written
    report = json.loads(path.read_text(encoding="utf-8"))
    report["outcomes"][0]["ndcg@10"] = 0.99
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("report_sha" in r for r in out["blocking_reasons"])


def test_hybrid_report_fed_as_vector_hard_fail(tmp_path):
    manifest = build_burn(tmp_path, retriever="hybrid")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("retriever" in r for r in out["blocking_reasons"])


def test_report_retriever_mismatch_hard_fail(tmp_path):
    """A report whose methodology says hybrid inside a vector manifest."""
    manifest = build_burn(tmp_path,
                          report_retrievers={"arctic-s": "hybrid"})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("retriever" in r for r in out["blocking_reasons"])


def test_manifest_n_vs_report_n_mismatch_hard_fail(tmp_path):
    """Mixed-n (--limit subset): the manifest claims a full n but the report
    has fewer questions."""
    manifest = build_burn(tmp_path, n_overrides={"arctic-s": 150})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("n" in r and "report" in r for r in out["blocking_reasons"])


def test_asymmetric_question_sets_hard_fail(tmp_path):
    """One config's report is missing 10% of the gate questions (asymmetric
    set) → per-pair dropped > 5% → HARD-FAIL."""
    qids_all = _qids() + _abs_qids()
    qts_all = _qts() + ["single-session-user"] * ABS_N
    missing = set(_qids()[::10])  # 30 of 300 = 10%
    bv = base_values()
    tr = _full(bv["ctrl_tr"])
    ndcg = _full(bv["ctrl_ndcg"])
    outcomes = [make_outcome(q, qts_all[i], tr[i], ndcg[i])
                for i, q in enumerate(qids_all) if q not in missing]
    report = make_report(outcomes, model="bge-small", prompt=None)
    path = tmp_path / "report-asymmetric.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest = build_burn(tmp_path)
    cfg = next(c for c in manifest["configs"] if c["name"] == "bge-small")
    cfg.update({"n": len(outcomes), "report": str(path),
                "report_sha": _sha(path)})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("dropped" in r for r in out["blocking_reasons"])


def test_dropped_at_threshold_passes(tmp_path):
    """Dropped == 5% of paired questions is at the boundary — does NOT fail
    (the fail threshold is strictly above 5%). 14 missing of 300 →
    14/286 = 4.90% ≤ 5%."""
    qids_all = _qids() + _abs_qids()
    qts_all = _qts() + ["single-session-user"] * ABS_N
    missing = set(_qids()[:14])  # 14 of 300 → 14/286 = 4.9% ≤ 5%
    bv = base_values()
    tr = _full(bv["ctrl_tr"])
    ndcg = _full(bv["ctrl_ndcg"])
    outcomes = [make_outcome(q, qts_all[i], tr[i], ndcg[i])
                for i, q in enumerate(qids_all) if q not in missing]
    report = make_report(outcomes, model="bge-small", prompt=None)
    path = tmp_path / "report-5pct.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest = build_burn(tmp_path)
    cfg = next(c for c in manifest["configs"] if c["name"] == "bge-small")
    cfg.update({"n": len(outcomes), "report": str(path),
                "report_sha": _sha(path)})
    out = run_gate(tmp_path, manifest)
    assert out["blocked"] is False
    assert out["verdict"] == "NO-WINNER"


def test_code_sha_drift_scoped_path_fails(tmp_path):
    """Drift on an eval-critical path between the burn sha and HEAD → the
    gate refuses to trust the evidence."""
    manifest = build_burn(tmp_path, code_sha="sha-burn-1")
    out = run_gate(
        tmp_path, manifest,
        head_sha_fn=lambda repo: "sha-after",
        changed_files_fn=lambda sha, repo: ["tools/longmem_eval/run.py"])
    assert out["verdict"] == "BLOCKED"
    assert any("code_sha" in r for r in out["blocking_reasons"])


@pytest.mark.parametrize("path", [
    "tools/longmem_eval/retrieve.py",
    "tools/embedder_probe.py",
    "tools/mini_beir/run.py",
    "tools/calibrate_thresholds.py",
    "tools/pair_label_runner.py",
    "benchmarks/run_report.py",
    "benchmarks/synthetic_corpus.py",
    "tests/eval/retrieval/gate_1349.py",
    "tests/fixtures/labeled_pairs.jsonl",
    "tortoise/embeddings.py",
    "tortoise/sdk.py",
    "tortoise/projection/__init__.py",
    "tortoise/search_engine.py",
    "graph-scripts/backfill_embeddings.py",
])
def test_code_sha_drift_each_scoped_path_fails(tmp_path, path):
    """Every eval-critical path in the drift scope HARD-FAILS the gate —
    the probe, benchmarks, calibration tools, labeled-pair fixture, ingest
    write path, and eval code all produce gate evidence."""
    assert path in gate_1349.DRIFT_SCOPE or \
        any(path.startswith(p.rstrip("/") + "/") or path == p
            for p in gate_1349.DRIFT_SCOPE)
    manifest = build_burn(tmp_path, code_sha="sha-burn-1")
    out = run_gate(
        tmp_path, manifest,
        head_sha_fn=lambda repo: "sha-after",
        changed_files_fn=lambda sha, repo: [path])
    assert out["verdict"] == "BLOCKED"
    assert any("code_sha" in r for r in out["blocking_reasons"])


def test_code_sha_drift_scope_boundary(tmp_path):
    """A path that merely PREFIX-matches a scoped file (not the file or its
    dir) does not drift — e.g. tools/embedder_probe.py.bak is not the
    probe file."""
    assert gate_1349.scoped_drift(["tools/embedder_probe.py.bak"]) == []
    assert gate_1349.scoped_drift(["tortoise/embeddings.pyx"]) == []
    assert "tortoise/projection/__init__.py" in gate_1349.scoped_drift(
        ["tortoise/projection/__init__.py"])


def test_code_sha_drift_non_scoped_passes(tmp_path):
    """Drift on a non-scoped path (docs/) → the gate still runs."""
    manifest = build_burn(tmp_path, code_sha="sha-burn-1")
    out = run_gate(
        tmp_path, manifest,
        head_sha_fn=lambda repo: "sha-after",
        changed_files_fn=lambda sha, repo: ["docs/00_index.md"])
    assert out["blocked"] is False
    assert out["verdict"] == "NO-WINNER"


def test_code_sha_per_config_mismatch_fails(tmp_path):
    """Different configs burned against different code shas → HARD-FAIL (a
    mid-burn merge silently changed later configs' vectors)."""
    manifest = build_burn(tmp_path)
    manifest["configs"][3]["code_sha"] = "sha-burn-2"
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("code_sha" in r for r in out["blocking_reasons"])


def test_code_sha_unverifiable_fails(tmp_path):
    manifest = build_burn(tmp_path)
    out = run_gate(tmp_path, manifest, head_sha_fn=lambda repo: None)
    assert out["verdict"] == "BLOCKED"


def test_resolved_revision_same_model_diff_revision_fails(tmp_path):
    manifest = build_burn(tmp_path)
    # arctic-xs's two configs disagree on the resolved revision
    manifest["configs"][2]["resolved_revision"] = "rev-A"
    manifest["configs"][3]["resolved_revision"] = "rev-B"
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("revision" in r for r in out["blocking_reasons"])


def test_resolved_revision_manifest_vs_probe_fails(tmp_path):
    manifest = build_burn(tmp_path)
    manifest["configs"][0]["resolved_revision"] = "rev-not-probe"
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("revision" in r for r in out["blocking_reasons"])


def test_resolved_revision_distinct_models_passes(tmp_path):
    """Different models have their own revisions — passes."""
    manifest = build_burn(tmp_path)
    manifest["probe_revisions"] = {
        "minilm": "rev-minilm", "bge-small": "rev-bge-small",
        "arctic-xs": "rev-arctic-xs", "arctic-s": "rev-arctic-s"}
    out = run_gate(tmp_path, manifest)
    assert out["blocked"] is False


def test_checkpoint_state_partial_fails(tmp_path):
    manifest = build_burn(tmp_path,
                          checkpoint_states={"arctic-s": "partial"})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("checkpoint" in r for r in out["blocking_reasons"])


def test_checkpoint_state_truncated_fails(tmp_path):
    manifest = build_burn(tmp_path,
                          checkpoint_states={"arctic-s": "truncated"})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


# ── precondition branches ───────────────────────────────────────────────────

def test_product_call_missing_blocks(tmp_path):
    pre = dict(PRE)
    pre["product_call"] = "does-not-exist.json"
    manifest = build_burn(tmp_path, preconditions=pre)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("product-call.json missing" in r for r in out["blocking_reasons"])


def test_product_call_illegal_enum_blocks(tmp_path):
    pre = dict(PRE)
    (tmp_path / "product-call.json").write_text(json.dumps(
        {"decision": "customer-local", "timestamp": "2026-08-17T12:00:00Z",
         "recorder": "t8"}), encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


def test_product_call_future_timestamp_blocks(tmp_path):
    pre = dict(PRE)
    (tmp_path / "product-call.json").write_text(json.dumps(
        {"decision": "server-side", "timestamp": "2999-01-01T00:00:00Z",
         "recorder": "t8"}), encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


def test_product_call_not_server_side_blocks(tmp_path):
    """A legal product call that is NOT server-side blocks PR2 (GATE (b))."""
    pre = dict(PRE)
    (tmp_path / "product-call.json").write_text(json.dumps(
        {"decision": "reject-swap", "timestamp": "2026-08-17T12:00:00Z",
         "recorder": "t8"}), encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert out["statistical_verdict"] == "PASS(arctic-s)"
    assert any("server-side" in r for r in out["blocking_reasons"])


def test_hnsw_spotcheck_missing_blocks(tmp_path):
    pre = dict(PRE)
    pre["hnsw_spotcheck"] = "missing-spotcheck.json"
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("hnsw" in r for r in out["blocking_reasons"])


def test_hnsw_spotcheck_not_cleared_blocks(tmp_path):
    pre = dict(PRE)
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps(
        {"cleared": False, "n": GATE_N,
         "metric_deltas": {"turn_recall@10": {"n": GATE_N, "mean_delta": 0.0,
                                              "one_sided_p": 0.4,
                                              "deltas": [0.0] * GATE_N},
                           "ndcg@10": {"n": GATE_N, "mean_delta": 0.0,
                                       "one_sided_p": 0.4,
                                       "deltas": [0.0] * GATE_N}}}),
        encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("hnsw" in r for r in out["blocking_reasons"])


def test_hnsw_spotcheck_recomputed_mismatch_blocks(tmp_path):
    """The artifact's declared cleared field disagrees with the gate's
    recomputation from the per-question deltas → inconsistent evidence."""
    pre = dict(PRE)
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps(
        {"cleared": True, "n": GATE_N,
         "metric_deltas": {"turn_recall@10": {"n": GATE_N, "mean_delta": 0.0,
                                              "one_sided_p": 0.4,
                                              "deltas": [0.0] * GATE_N},
                           "ndcg@10": {"n": GATE_N, "mean_delta": 0.0,
                                       "one_sided_p": 0.4,
                                       "deltas": [0.0] * GATE_N}}}),
        encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


def test_hnsw_spotcheck_delta_count_must_match_burn(tmp_path):
    """P1 fix: the artifact declares n=300 + cleared=True but carries only
    100 per-question deltas (each +0.30 → recomputed p=0). The gate must
    NOT recompute p over an unverified subset — the delta count must equal
    the FULL filtered-split question set per metric."""
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    # Overwrite the default spot-check with the adversarial subset artifact.
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps(
        {"cleared": True, "n": GATE_N, "winner": "arctic-s",
         "control": "minilm",
         "metric_deltas": {
             "turn_recall@10": {"n": GATE_N, "mean_delta": 0.30,
                                "one_sided_p": 0.0,
                                "deltas": [0.30] * 100},
             "ndcg@10": {"n": GATE_N, "mean_delta": 0.0,
                          "one_sided_p": 1.0, "deltas": [0.0] * GATE_N}}}),
        encoding="utf-8")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("per-question deltas" in r for r in out["blocking_reasons"])


def test_hnsw_spotcheck_winner_null_blocks(tmp_path):
    """P1 fix: a spot-check artifact with winner:null must NOT validate the
    gate's winner — an unattributed artifact proves nothing about the
    shipped candidate."""
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    (tmp_path / "hnsw-spotcheck.json").write_text(json.dumps(
        {"cleared": True, "n": GATE_N, "winner": None, "control": "minilm",
         "metric_deltas": {
             "turn_recall@10": {"n": GATE_N, "mean_delta": 0.05,
                                "one_sided_p": 0.0,
                                "deltas": [0.05] * GATE_N},
             "ndcg@10": {"n": GATE_N, "mean_delta": 0.0,
                          "one_sided_p": 1.0, "deltas": [0.0] * GATE_N}}}),
        encoding="utf-8")
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("gate winner" in r for r in out["blocking_reasons"])


def test_no_winner_without_spotcheck_not_blocked(tmp_path):
    """P2 fix: a NO-WINNER burn with NO spot-check artifact must not be
    BLOCK-masked — there is no winner to spot-check, so the precondition is
    WAIVED (the spot-check gates a shipped winner only)."""
    manifest = build_burn(tmp_path)  # all families ≈ control → NO-WINNER
    pre = dict(PRE)
    pre.pop("hnsw_spotcheck")
    manifest["preconditions"] = pre
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "NO-WINNER"
    assert out["blocked"] is False
    assert out["hnsw_waived"] is True


def test_e2e8_over_300ms_blocks(tmp_path):
    pre = dict(PRE)
    (tmp_path / "e2e8-arctic-s.json").write_text(json.dumps(
        {"arms": {"e2e": {"censored_p95_ms": 350.0, "verdict": "achieved"}}}),
        encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("e2e8" in r or "E2E" in r for r in out["blocking_reasons"])


def test_e2e8_missing_for_clearing_family_blocks(tmp_path):
    pre = dict(PRE)
    pre["e2e8"] = {"minilm": "e2e8-minilm.json"}  # no arctic-s entry
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("e2e8" in r or "E2E" in r for r in out["blocking_reasons"])


def test_e2e8_invalidated_report_blocks(tmp_path):
    pre = dict(PRE)
    (tmp_path / "e2e8-arctic-s.json").write_text(json.dumps(
        {"arms": {"e2e": {"censored_p95_ms": 120.0, "verdict": "INVALIDATED"}}}),
        encoding="utf-8")
    manifest = build_burn(tmp_path, preconditions=pre,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


def test_issue265_landed_non384_blocks(tmp_path):
    pre = dict(PRE)
    pre["issue_265"] = {"status": "landed-non-384", "note": "768-dim landed"}
    manifest = build_burn(tmp_path, preconditions=pre)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"
    assert any("265" in r for r in out["blocking_reasons"])


def test_issue265_unknown_status_blocks(tmp_path):
    pre = dict(PRE)
    pre["issue_265"] = {"status": "maybe"}
    manifest = build_burn(tmp_path, preconditions=pre)
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "BLOCKED"


def test_full_pass_with_all_preconditions(tmp_path):
    """The happy path: PASS + all precondition artifacts present + valid."""
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    out = run_gate(tmp_path, manifest)
    assert out["verdict"] == "PASS(arctic-s)"
    assert out["blocked"] is False
    assert all(p["met"] for p in out["preconditions"]["checks"])


def _git_repo(tmp_path: Path) -> str:
    """Turn tmp_path into a git repo containing the burn; return HEAD."""
    import subprocess
    for args in (["git", "init", "-q"],
                 ["git", "config", "user.email", "gate@test"],
                 ["git", "config", "user.name", "gate"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-qm", "burn"]):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_cli_writes_verdict_json(tmp_path, capsys):
    """The CLI entrypoint prints a verdict and saves the verdict JSON."""
    manifest = build_burn(tmp_path)
    head = _git_repo(tmp_path)  # the burn now lives at a real repo HEAD
    for cfg in manifest["configs"]:
        cfg["code_sha"] = head
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    out_path = tmp_path / "verdict.json"
    rc = gate_1349.main(["--manifest", str(mpath), "--out", str(out_path),
                         "--repo", str(tmp_path)])
    assert rc == 1  # NO-WINNER → non-zero (fail-closed)
    assert out_path.is_file()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "NO-WINNER"
    printed = capsys.readouterr().out
    assert "NO-WINNER" in printed


def test_cli_pass_exit_zero(tmp_path):
    """The CLI wiring: PASS(winner) + all preconditions → exit 0."""
    manifest = build_burn(tmp_path,
                          deltas={"arctic-s": {"turn_recall@10": 0.06,
                                               "ndcg@10": 0.0},
                                  "arctic-s-query": {"turn_recall@10": 0.06,
                                                     "ndcg@10": 0.0}})
    head = _git_repo(tmp_path)
    for cfg in manifest["configs"]:
        cfg["code_sha"] = head
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    out_path = tmp_path / "verdict.json"
    rc = gate_1349.main(["--manifest", str(mpath), "--out", str(out_path),
                         "--repo", str(tmp_path)])
    assert rc == 0
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "PASS(arctic-s)"
    assert saved["blocked"] is False


def test_cli_python_dash_m_entrypoint(tmp_path):
    """The DOCUMENTED T8 invocation — ``python -m tests.eval.retrieval.
    gate_1349 --manifest ...`` — must run (catches a missing ``import sys``
    in the ``__main__`` block)."""
    import subprocess
    manifest = build_burn(tmp_path)
    head = _git_repo(tmp_path)
    for cfg in manifest["configs"]:
        cfg["code_sha"] = head
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    out_path = tmp_path / "verdict.json"
    proc = subprocess.run(
        ["uv", "run", "python", "-m", "tests.eval.retrieval.gate_1349",
         "--manifest", str(mpath), "--out", str(out_path),
         "--repo", str(tmp_path)],
        capture_output=True, text=True, timeout=180, cwd=Path(__file__).resolve().parent.parent.parent.parent)
    assert proc.returncode == 1, proc.stderr[-2000:]
    assert out_path.is_file()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "NO-WINNER"


def test_cli_manifest_parse_error_clean_message(tmp_path, capsys):
    """P2 fix: an unreadable/malformed manifest prints a clean error and
    exits non-zero (fail-closed) — no traceback."""
    mpath = tmp_path / "bad-manifest.json"
    mpath.write_text("{not json", encoding="utf-8")
    rc = gate_1349.main(["--manifest", str(mpath)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "manifest" in (captured.out + captured.err).lower()
    assert "Traceback" not in (captured.out + captured.err)


def test_code_sha_uncommitted_edit_drifts_when_head_equals_sha(tmp_path):
    """spec-review P2 fix: an UNCOMMITTED worktree edit on a scoped path
    must drift the gate even when HEAD == burn sha — the old `elif head !=
    code_sha` guard skipped the diff exactly in the standard post-burn
    state, letting a live mid-burn edit pass. `git diff <sha>` (working-tree
    diff) covers both committed-after-sha and uncommitted edits."""
    manifest = build_burn(tmp_path, code_sha="sha-burn-1")
    out = run_gate(
        tmp_path, manifest,
        head_sha_fn=lambda repo: "sha-burn-1",   # HEAD == burn sha
        changed_files_fn=lambda sha, repo: ["tools/longmem_eval/run.py"])
    assert out["verdict"] == "BLOCKED"
    assert any("code_sha" in r for r in out["blocking_reasons"])


def test_is_gate_question_filter_branches():
    """Direct pin of the category filter (spec-review P3): single-session-*
    prefix, in-set exact types, non-listed exclusion, and _abs exclusion."""
    assert gate_1349.is_gate_question("single-session-preference", "q1")
    assert gate_1349.is_gate_question("single-session-user", "q1")
    assert gate_1349.is_gate_question("temporal-reasoning", "q1")
    assert gate_1349.is_gate_question("knowledge-update", "q1")
    assert gate_1349.is_gate_question("multi-session", "q1")
    assert not gate_1349.is_gate_question("single-session-assistant_abs", "q_abs")
    assert not gate_1349.is_gate_question("some-other-type", "q1")
    assert not gate_1349.is_gate_question(None, "q1")
    assert not gate_1349.is_gate_question("temporal-reasoning", None)
