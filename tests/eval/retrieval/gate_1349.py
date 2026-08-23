"""gate_1349.py — the #1349 embedder-selection gate (the locked decision rule).

The gate decides whether a candidate 384-dim embedding model swap ships —
implemented as committed, CI-tested code, NOT a judgment call at merge time.
It implements the pre-registered rule from
``docs/scoping/2026-08-17-1349-embedder-selection-scoping.md`` (§Pre-registered
Decision Rule) and ``docs/plans/2026-08-17-1349-embedder-selection.md`` (T4):

    Co-primary metrics: turn_recall@10 AND nDCG@10 (LongMemEval-S per-question
    outcomes from the T2 vector arm). Category filter: question_type starting
    ``single-session-`` OR in {temporal-reasoning, knowledge-update,
    multi-session}; ``_abs`` abstention questions are excluded.

    6 configs → 3 model families (MiniLM control + bge-small + arctic-xs ×2 +
    arctic-s ×2; arctic runs in BOTH a no-prefix config and the vendor
    ``prompt_name="query"`` config). The family delta per metric = max of its
    2 configs (pre-registered selection rule). m = 6 pairwise one-sided tests
    (3 families × 2 metrics) → BH-FDR at q=0.10 (top-rank p = q/m = 0.0167,
    z≈2.128). A family wins iff (a) its aggregate mean beats the MiniLM
    control by ≥ +5% relative AND (b) BH rejects its pairwise one-sided test,
    on EITHER co-primary metric.

    n-adaptive bar: bar ≈ z·(sd/√n)/control_mean derived from the ACTUAL
    per-question data (n=200/300/500 → ≈+15.1/+12.3/+9.5% at sd=0.40,
    control=0.40). Reported as evidence — the win gate is (a) AND (b) only.

    Outcomes: PASS(<family>) / NO-WINNER / INSUFFICIENT-POWER / BLOCKED.
      * INSUFFICIENT-POWER: paired n < 200 OR control turn_recall@10 < 0.05 →
        absolute-delta fallback (a candidate clearing absolute turn_recall@10
        ≥ 0.30 is surfaced) + the judgment tiebreak recommendation in pinned
        order (mini-BEIR OOD → labeled-pair calibration → nDCG@10) — the gate
        EMITS the recommendation; the actual judgment is human/ADR-009.
      * NO-WINNER: nothing clears on either metric with FDR-clean CIs.
      * Multi-winner: argmax combined rank (turn_recall@10 + nDCG@10) →
        lower E2E-8 latency → smaller image size → family-preserving
        (arctic-xs is fine-tuned FROM MiniLM — closest encoder space).
      * Escalation (fires when NEITHER metric clears for all families but a
        directional signal exists): OR-of-three — (i) ≥1 family delta
        positive at p<0.10 pre-FDR on EITHER co-primary metric, OR (ii)
        control turn_recall@10 ≥ 0.50 (ceiling-compressed), OR (iii) ≥1
        family with a per-category gain ≥ +5pp on any of the 4 paper
        categories on EITHER metric. If escalation fires → recommend an
        end-to-end judged run on the top-2 families + CONTROL (3 judged
        families, control always judged) on the production HNSW surface.
        Escalation PASS criterion: the judged winner clears end-to-end
        accuracy vs control by ≥+5% relative with one-sided paired p<0.10
        pre-FDR on the same category set (the FULL filtered question set —
        fingerprint-pinned against cherry-picking), else NO-WINNER. Judge
        unavailable/non-answers → NO-WINNER (pre-registered). For escalation
        winners the judged run (on HNSW) satisfies GATE (c) — the standard
        HNSW spot-check is WAIVED.
      * BLOCKED: any manifest validation or precondition failure — missing
        config (m≠6), report_sha mismatch vs on-disk, denominator mismatch
        (mixed-n/--limit, hybrid-fed-as-vector, asymmetric sets, dropped
        >5%), code_sha drift on an eval-critical path, resolved_revision
        (per-model), checkpoint_state, product-call.json, HNSW spot-check
        artifact, pre-swap E2E-8 ≤300ms, #265 non-384 status.

    P@10 (secondary) + P@5 (tertiary) are reported, not gated. Paired 90%
    CIs are reported as evidence, not a gate. Split-config tie rule: if
    different arctic configs win different metrics, the swap config = argmax
    on the metric that cleared, else combined rank.

    Denominator: ALL filtered-split questions — non-evidence 0/0 tied deltas
    (neither arm retrieved an evidence turn) dilute power and are included
    (documented; the n-adaptive bar accounts for the empirical sd).

Manifest schema (JSON):
    {
      "split": "s",
      "retriever": "vector",
      "probe_revisions": {model: resolved_revision},   # probe-recorded
      "configs": [                                     # EXACTLY 6
        {"name": ..., "model": ..., "prompt": null|"query",
         "resolved_revision": ..., "n": ..., "checkpoint_state": "complete",
         "report_sha": sha256(report file), "code_sha": ...,
         "report": "path (relative to the manifest)"},
        ...
      ],
      "preconditions": {
        "product_call": "path",                        # {decision, timestamp, recorder}
        "hnsw_spotcheck": "path",                      # {cleared, n, metric_deltas}
        "e2e8": {model: "path"},                       # benchmarks report (arms.e2e.censored_p95_ms)
        "issue_265": {"status": "not-landed"}          # or "landed-non-384"
      },
      "escalation_judged": {"path": ..., "sha": ...}   # optional; escalation winner
    }

Run: python -m tests.eval.retrieval.gate_1349 --manifest <path> [--out <path>]
Exit code 0 iff verdict PASS and not blocked (fail-closed otherwise).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.eval.retrieval.bootstrap import (
    DEFAULT_N_RESAMPLES,
    bh_fdr,
    one_sided_bootstrap_p,
    paired_bootstrap_ci,
)

# ── pinned decision-rule parameters (pre-registered; do NOT tune) ───────────
Q = 0.10                       # BH-FDR level
M = 6                          # pairwise tests: 3 families × 2 co-primary metrics
Z = 2.128                      # z for the top-rank one-sided p = q/m = 0.0167
NOMINAL_FLOOR = 0.05           # (a): ≥ +5% relative vs control
MIN_PAIRED_N = 200             # paired n below this → INSUFFICIENT-POWER
CONTROL_DEGENERATE = 0.05      # control turn_recall@10 below → INSUFFICIENT-POWER
ABSOLUTE_FALLBACK = 0.30       # absolute turn_recall@10 a candidate must clear
ESCALATION_P = 0.10            # trigger (i): pre-FDR one-sided p
ESCALATION_CEILING = 0.50      # trigger (ii): control turn_recall@10 ≥ this
ESCALATION_CATEGORY_PP = 0.05  # trigger (iii): per-category gain ≥ +5pp
DROPPED_FRACTION = 0.05        # dropped-question fail threshold (>5% fails)
E2E8_LIMIT_MS = 300.0          # pre-swap E2E-8 p95 limit
CO_PRIMARY = ("turn_recall@10", "ndcg@10")
FAMILY_ORDER = ("bge-small", "arctic-xs", "arctic-s")
CONTROL_MODEL = "minilm"
REQUIRED_CONFIGS = {"minilm": 1, "bge-small": 1, "arctic-xs": 2, "arctic-s": 2}
CHECKPOINT_STATES = ("complete",)
PRODUCT_CALL_ENUM = ("server-side", "selfhost-only", "reject-swap")
ISSUE265_SAFE = ("not-landed",)
JUDGMENT_TIEBREAK_ORDER = ("mini-beir-ood", "labeled-pair-calibration", "ndcg@10")

# Tiebreak: family-preserving = arctic-xs is fine-tuned FROM MiniLM (closest
# encoder space — hypothesis-level, final tiebreak only, per the scoping).
FAMILY_PRESERVING_ORDER = {"arctic-xs": 0, "arctic-s": 1, "bge-small": 2}

# Model info for the image-size tiebreak (approx baked sizes; T8 records the
# measured figures — only the ORDER matters for the tiebreak).
MODEL_INFO: dict[str, dict[str, Any]] = {
    "minilm":    {"hf_id": "sentence-transformers/all-MiniLM-L6-v2",
                  "approx_mb": 90,  "family": "minilm"},
    "bge-small": {"hf_id": "BAAI/bge-small-en-v1.5",
                  "approx_mb": 130, "family": "bge"},
    "arctic-xs": {"hf_id": "snowflake/snowflake-arctic-embed-xs",
                  "approx_mb": 90,  "family": "arctic"},
    "arctic-s":  {"hf_id": "snowflake/snowflake-arctic-embed-s",
                  "approx_mb": 440, "family": "arctic"},
}

# code_sha drift scope — the eval-critical paths. Any change on these between
# the burn sha and HEAD invalidates the evidence (the probe, benchmarks,
# calibration tools, labeled-pair fixture, AND the ingest write wrapper all
# produce gate evidence). NOT full-tree (PR1's own merge would HARD-FAIL a
# full-tree pin); drift on a non-scoped path (docs/) passes.
DRIFT_SCOPE = (
    "tools/longmem_eval/",
    "tools/embedder_probe.py",
    "tools/mini_beir/",
    "tools/calibrate_thresholds.py",
    "tools/pair_label_runner.py",
    "benchmarks/run_report.py",
    "benchmarks/synthetic_corpus.py",
    "tests/eval/retrieval/",
    "tests/fixtures/labeled_pairs.jsonl",
    "tortoise/embeddings.py",
    "tortoise/sdk.py",
    "tortoise/projection/",
    "tortoise/search_engine.py",
    "graph-scripts/backfill_embeddings.py",
)

# The 4 paper categories (LongMemEval abilities over the gate question set).
PAPER_CATEGORIES = ("Information Extraction", "Multi-Session Reasoning",
                    "Temporal Reasoning", "Knowledge Updates")


class GateError(ValueError):
    """A gate input is structurally invalid — HARD-FAIL (never guess)."""


# ── category filter (pinned) ────────────────────────────────────────────────

def is_gate_question(question_type: str | None, question_id: str | None) -> bool:
    """The pre-registered category filter: ``single-session-*`` OR in
    {temporal-reasoning, knowledge-update, multi-session}; ``_abs``
    abstention questions excluded (signalled on the question_id)."""
    if not question_type or not question_id:
        return False
    if "_abs" in question_id:
        return False
    if question_type.startswith("single-session-"):
        return True
    return question_type in {"temporal-reasoning", "knowledge-update",
                             "multi-session"}


def paper_category(question_type: str | None) -> str | None:
    """Map a gate question type to its paper category (report.py PAPER_CATEGORY
    semantics; kept local so the gate cannot drift with tools/ changes)."""
    qt = question_type or ""
    if qt.startswith("single-session-"):
        return "Information Extraction"
    return {
        "multi-session": "Multi-Session Reasoning",
        "temporal-reasoning": "Temporal Reasoning",
        "knowledge-update": "Knowledge Updates",
    }.get(qt)


# ── n-adaptive bar + provenance helpers (pure, unit-tested) ─────────────────

def n_adaptive_bar(sd: float, n: int, control_mean: float, *,
                   q: float = Q, m: int = M) -> float:
    """The effective bar: z(1−q/m)·(sd/√n)/control_mean.

    z is derived INSIDE from the pairwise-FDR level (z = Φ⁻¹(1−q/m); m=6,
    q=0.10 → z≈2.128 — the code-review P2: the old hardcoded z=2.128 baked
    in q/m=0.0167 and left the q/m params dead). Derived from the ACTUAL
    per-question data (empirical sd of the paired deltas and n). As n falls
    the bar rises (n=200/300/500 → ≈+15.1/+12.3/+9.5% at sd=0.40,
    control=0.40). Reported as evidence — the win gate is (a) nominal floor
    AND (b) BH rejection only.
    """
    if n <= 0 or control_mean <= 0:
        return float("inf")
    z = statistics.NormalDist().inv_cdf(1 - q / m)
    return z * (sd / math.sqrt(n)) / control_mean


def question_set_fingerprint(qids: Sequence[str]) -> str:
    """sha256 over the SORTED question ids — pins the judged-run question set
    to the FULL filtered split (a post-hoc n that shrinks until p<0.10 is a
    cherry-picking window, explicitly forbidden)."""
    return hashlib.sha256(
        "\n".join(sorted(qids)).encode("utf-8")).hexdigest()


def scoped_drift(changed: Sequence[str],
                 scope: Sequence[str] = DRIFT_SCOPE) -> list[str]:
    """Changed files that fall on eval-critical paths (exact file or path
    prefix); a ``docs/foo.md`` change passes."""
    return [f for f in changed
            if any(f == p or f.startswith(p.rstrip("/") + "/") for p in scope)]


def _git_head(repo_root: Path | None) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=str(repo_root) if repo_root else None)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


def _git_changed(code_sha: str, repo_root: Path | None) -> list[str] | None:
    """Files changed between ``code_sha`` and the WORKING TREE; None =
    unverifiable. ``git diff <sha>`` (no HEAD arg) also covers uncommitted
    worktree edits — a live mid-burn edit on a scoped path must drift the
    gate, not pass because HEAD still equals the burn sha."""
    try:
        out = subprocess.run(["git", "diff", "--name-only", code_sha],
                             capture_output=True, text=True, timeout=10,
                             cwd=str(repo_root) if repo_root else None)
        if out.returncode != 0:
            return None
        return [line for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return None


# ── report loading + extraction ─────────────────────────────────────────────

def _resolve(path: str | Path, manifest_dir: Path | None) -> Path:
    p = Path(path)
    if p.is_absolute() or manifest_dir is None:
        return p
    return manifest_dir / p


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_report(report: Mapping[str, Any]) -> tuple[dict[str, dict], int]:
    """Per-question gate metrics from a LongMemEval report.

    Returns ({qid: {turn_recall@10, ndcg@10, p@10, p@5, category}},
    dropped_count) over the FILTERED gate question set. Questions marked
    ``breaker_open`` are excluded (routed through the dropped accounting —
    never recall 0, never silently included). Missing k=10 / ndcg@10 on a
    gate question is a HARD-FAIL (data error, not a 0).
    """
    data: dict[str, dict] = {}
    dropped = 0
    for o in report.get("outcomes") or []:
        qid = o.get("question_id")
        if not qid:
            continue
        if o.get("breaker_open"):
            dropped += 1
            continue
        if not is_gate_question(o.get("question_type"), qid):
            continue
        trk = o.get("turn_recall@k") or {}
        if "10" not in trk:
            raise GateError(
                f"outcome {qid} lacks turn_recall@k['10'] — the gate needs "
                "k=10 (run with the default ks 5,10,20)")
        if o.get("ndcg@10") is None:
            raise GateError(f"outcome {qid} lacks ndcg@10")
        data[qid] = {
            "turn_recall@10": float(trk["10"]),
            "ndcg@10": float(o["ndcg@10"]),
            "p@10": float(o["p@10"]) if o.get("p@10") is not None else None,
            "p@5": float(o["p@5"]) if o.get("p@5") is not None else None,
            "category": paper_category(o.get("question_type")),
        }
    return data, dropped


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    return statistics.stdev(xs) if len(xs) >= 2 else 0.0


def _paired(cfg_data: Mapping[str, dict], ctrl_data: Mapping[str, dict],
            metric: str) -> tuple[list[float], list[str], int]:
    """Paired per-question deltas (config − control) on common qids.

    Returns (deltas, common_qids, dropped) — dropped = |Δ| (questions in one
    arm only: asymmetric sets, failed/dropped questions).
    """
    common = sorted(set(cfg_data) & set(ctrl_data))
    dropped = len(set(cfg_data) ^ set(ctrl_data))
    deltas = [cfg_data[q][metric] - ctrl_data[q][metric] for q in common]
    return deltas, common, dropped


# ── manifest validation (HARD-FAIL checks) ──────────────────────────────────

def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_dir: Path | None,
    repo_root: Path | None,
    head_sha_fn: Callable[[Path | None], str | None] | None,
    changed_files_fn: Callable[[str, Path | None], list[str] | None] | None,
) -> tuple[list[str], dict[str, dict]]:
    """Structural + provenance validation. Returns (errors, report_data).

    report_data = {config_name: {"data": {qid: metrics}, "dropped": int,
                                 "report": dict, "config": dict}} — populated
    only for configs whose reports loaded cleanly.
    """
    errors: list[str] = []
    report_data: dict[str, dict] = {}

    if manifest.get("retriever") != "vector":
        errors.append(f"(c) manifest retriever must be 'vector' (hybrid "
                      f"reports fed as the vector arm), got "
                      f"{manifest.get('retriever')!r}")

    configs = manifest.get("configs")
    if not isinstance(configs, list) or len(configs) != 6:
        errors.append(f"(a) expected exactly 6 configs (MiniLM control + "
                      f"bge-small + arctic-xs ×2 + arctic-s ×2), got "
                      f"{len(configs) if isinstance(configs, list) else 'non-list'}")
        return errors, report_data

    counts: dict[str, int] = {}
    for cfg in configs:
        if isinstance(cfg, dict) and "model" in cfg:
            counts[cfg["model"]] = counts.get(cfg["model"], 0) + 1
    for model, want in REQUIRED_CONFIGS.items():
        if counts.get(model) != want:
            errors.append(f"(a) model {model!r} must have exactly {want} "
                          f"config(s), got {counts.get(model, 0)}")

    # P1 fix (code-review): report_data is keyed by config NAME — two configs
    # sharing a name silently overwrite, dropping one run's evidence from the
    # family reduction while model counts and len(configs)==6 still pass.
    names = [c.get("name") for c in configs if isinstance(c, dict)]
    if len(set(names)) != len(names):
        dup = sorted(repr(n) for n in names if names.count(n) > 1)
        errors.append(f"(a) duplicate config names {dup} — report_data is "
                      "keyed by name; a duplicate silently drops one "
                      "config's evidence from family reduction")

    # P2 fix (code-review): each arctic family must run BOTH the no-prefix
    # config and the vendor prompt_name="query" config — the family delta
    # is the max of the pair; two no-prefix configs would silently drop the
    # vendor-prompt evidence.
    for model in ("arctic-xs", "arctic-s"):
        prompts = {c.get("prompt") for c in configs
                   if isinstance(c, dict) and c.get("model") == model}
        if prompts != {None, "query"}:
            errors.append(f"(a) {model} must run both the no-prefix config "
                          f"and the vendor prompt_name=\"query\" config — "
                          f"got prompts {sorted(str(p) for p in prompts)}")

    required_keys = ("name", "model", "prompt", "resolved_revision", "n",
                     "checkpoint_state", "report_sha", "code_sha", "report")
    for cfg in configs:
        name = cfg.get("name")
        missing = [k for k in required_keys if k not in cfg]
        if missing:
            errors.append(f"config {name!r} missing fields {missing}")
            continue
        if cfg["checkpoint_state"] not in CHECKPOINT_STATES:
            errors.append(f"(f) config {name!r} checkpoint_state "
                          f"{cfg['checkpoint_state']!r} not in "
                          f"{CHECKPOINT_STATES} — truncated/partial "
                          "checkpoints are rejected, never silently counted")
            continue
        path = _resolve(cfg["report"], manifest_dir)
        if not path.is_file():
            errors.append(f"config {name!r} report file missing: {path}")
            continue
        on_disk_sha = _sha256_file(path)
        if on_disk_sha != cfg["report_sha"]:
            errors.append(f"(b) config {name!r} report_sha mismatch: on-disk "
                          f"{on_disk_sha[:12]}… != manifest "
                          f"{cfg['report_sha'][:12]}… — mid-burn read or "
                          "tampered evidence")
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            errors.append(f"config {name!r} report unreadable: {e!r}")
            continue
        if report.get("methodology", {}).get("retriever") != "vector":
            errors.append(f"(c) config {name!r} report methodology "
                          f"retriever={report.get('methodology', {}).get('retriever')!r} "
                          "— hybrid reports cannot be fed as the vector arm")
        try:
            data, dropped = extract_report(report)
        except GateError as e:
            errors.append(f"config {name!r}: {e}")
            continue
        n_report = report.get("n_questions")
        if cfg["n"] != n_report:
            errors.append(f"(c) config {name!r} manifest n={cfg['n']} != "
                          f"report n_questions={n_report} — mixed-n/--limit "
                          "subsets must not be mixed into one gate run")
        report_data[name] = {"data": data, "dropped": dropped,
                             "report": report, "config": cfg}

    # denominator: per-pair dropped-question counts (asymmetric sets)
    if report_data:
        ctrl_name = next((c["name"] for c in configs
                          if c.get("model") == CONTROL_MODEL), None)
        if ctrl_name is None or ctrl_name not in report_data:
            errors.append("control config (minilm) missing from reports")
        else:
            ctrl_qids = set(report_data[ctrl_name]["data"])
            for cfg in configs:
                if cfg.get("model") == CONTROL_MODEL or cfg["name"] not in report_data:
                    continue
                cand_qids = set(report_data[cfg["name"]]["data"])
                paired = len(ctrl_qids & cand_qids)
                dropped = len(ctrl_qids ^ cand_qids)
                if paired == 0:
                    errors.append(f"(c) {cfg['name']} shares no gate questions "
                                  "with control — asymmetric question set")
                elif dropped > DROPPED_FRACTION * paired:
                    errors.append(
                        f"(c) {cfg['name']} vs control: {dropped} unpaired "
                        f"questions ({dropped / paired:.1%} of {paired} "
                        f"paired) — above the {DROPPED_FRACTION:.0%} "
                        "threshold; asymmetric question sets / dropped "
                        "questions break the paired comparison")

    # code_sha drift (scoped to the eval-critical paths)
    shas = {c.get("code_sha") for c in configs if isinstance(c, dict)}
    if len(shas) > 1:
        errors.append(f"(d) per-config code_sha differ "
                      f"{sorted(s for s in shas if s)} — a mid-burn merge "
                      "silently changed later configs' vectors")
    elif len(shas) == 1:
        code_sha = shas.pop()
        head = (head_sha_fn(repo_root) if head_sha_fn is not None
                else _git_head(repo_root))
        if head is None:
            errors.append("(d) cannot resolve repo HEAD — code provenance "
                          "unverifiable (fail closed)")
        else:
            # spec-review P2 fix: run the working-tree diff UNCONDITIONALLY —
            # ``git diff <code_sha>`` covers both commits-after-sha AND
            # uncommitted worktree edits, so a live mid-burn edit on a scoped
            # path drifts the gate even when HEAD still equals the burn sha.
            changed = (changed_files_fn(code_sha, repo_root)
                       if changed_files_fn is not None
                       else _git_changed(code_sha, repo_root))
            if changed is None:
                errors.append(f"(d) cannot diff {code_sha}..HEAD — code "
                              "provenance unverifiable (fail closed)")
            else:
                drifted = scoped_drift(changed)
                if drifted:
                    errors.append(f"(d) code_sha drift on eval-critical paths "
                                  f"between {code_sha} and HEAD: {drifted} — "
                                  "re-run the gate + spot-checks on drifted "
                                  "main (full re-burn only if eval code moved)")

    # resolved_revision — PER-MODEL semantics
    probe_revs = manifest.get("probe_revisions")
    if not isinstance(probe_revs, dict) or set(probe_revs) != set(REQUIRED_CONFIGS):
        errors.append("(e) probe_revisions must record one resolved revision "
                      "per model: " + ", ".join(sorted(REQUIRED_CONFIGS)))
    else:
        for model in REQUIRED_CONFIGS:
            revs = {c["resolved_revision"] for c in configs
                    if isinstance(c, dict) and c.get("model") == model}
            if len(revs) != 1:
                errors.append(f"(e) model {model!r} has differing "
                              f"resolved_revisions across its configs: "
                              f"{sorted(r for r in revs if r)} — same model "
                              "⇒ same revision")
            elif probe_revs.get(model) != revs.pop():
                errors.append(f"(e) model {model!r} manifest revision "
                              f"!= probe-recorded revision "
                              f"{probe_revs.get(model)!r}")

    return errors, report_data


# ── the decision rule ───────────────────────────────────────────────────────

def _aggregate_metric(data: Mapping[str, dict], metric: str) -> float:
    return _mean([m[metric] for m in data.values() if metric in m])


def _rank_map(values: Mapping[str, float]) -> dict[str, int]:
    """Competition rank (1,2,2,4): rank 1 = highest value; ties share."""
    order = sorted(values, key=lambda k: values[k], reverse=True)
    out: dict[str, int] = {}
    for i, k in enumerate(order, start=1):
        if i > 1 and values[k] == values[order[i - 2]]:
            out[k] = out[order[i - 2]]
        else:
            out[k] = i
    return out


def combined_rank(families: Sequence[str],
                  mean_deltas: Mapping[str, Mapping[str, float]]) -> dict[str, int]:
    """Combined rank = turn_recall@10 rank + nDCG@10 rank (lower = better)."""
    ranks = {m: _rank_map({f: mean_deltas[f][m] for f in families})
             for m in CO_PRIMARY}
    return {f: ranks["turn_recall@10"][f] + ranks["ndcg@10"][f]
            for f in families}


def multiwinner_pick(
    winners: Sequence[str],
    mean_deltas: Mapping[str, Mapping[str, float]],
    *,
    e2e8_p95: Mapping[str, float] | None = None,
    model_info: Mapping[str, Mapping[str, Any]] = MODEL_INFO,
    family_preserving: Mapping[str, int] = FAMILY_PRESERVING_ORDER,
) -> str:
    """Pre-registered multi-winner tiebreak: argmax combined rank → lower
    E2E-8 latency → smaller image size → family-preserving."""
    combined = combined_rank(winners, mean_deltas)
    e2e8 = e2e8_p95 or {}
    return min(
        winners,
        key=lambda f: (combined[f], e2e8.get(f, float("inf")),
                       model_info.get(f, {}).get("approx_mb", float("inf")),
                       family_preserving.get(f, 99)),
    )


def _read_e2e8_latencies(manifest: Mapping[str, Any],
                         manifest_dir: Path | None) -> dict[str, float]:
    """Best-effort per-model E2E-8 p95 for the multi-winner tiebreak."""
    e2e8_map = (manifest.get("preconditions") or {}).get("e2e8") or {}
    out: dict[str, float] = {}
    for model, path in e2e8_map.items():
        p = _resolve(path, manifest_dir)
        if not p.is_file():
            continue
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        arms = report.get("arms") or {}
        e2e = arms.get("e2e") or {}
        p95 = e2e.get("censored_p95_ms") or report.get("censored_p95_ms")
        if isinstance(p95, (int, float)):
            out[model] = float(p95)
    return out


def _swap_config_for(winner: str, cfg_names: Sequence[str],
                     cleared: Mapping[str, Mapping[str, bool]],
                     config_mean_deltas: Mapping[str, Mapping[str, float]],
                     family_rels: Mapping[str, Mapping[str, float]],
                     ) -> tuple[str, str]:
    """Split-config tie rule (pre-registered): if different arctic configs
    win different metrics, the swap config = argmax on the metric that
    cleared, else combined rank. On a combined-rank tie (the complementary
    case — each config wins one metric) the metric with the larger relative
    clear decides (deterministic). Returns (config_name, rule)."""
    cleared_metrics = [m for m in CO_PRIMARY if cleared[winner][m]]
    if len(cleared_metrics) == 1:
        m = cleared_metrics[0]
        best = max(cfg_names, key=lambda cn: config_mean_deltas[cn][m])
        return best, "argmax-on-cleared-metric"
    combined = combined_rank(cfg_names, config_mean_deltas)
    m_strong = max(CO_PRIMARY, key=lambda m: family_rels.get(winner, {}).get(m, 0.0))
    best = min(cfg_names, key=lambda cn: (combined[cn],
                                          -config_mean_deltas[cn][m_strong]))
    return best, "combined-rank"


def decision_rule(
    report_data: Mapping[str, dict],
    *,
    manifest: Mapping[str, Any],
    manifest_dir: Path | None,
    n_resamples: int,
) -> dict:
    """Compute the full pre-registered rule from the validated reports.

    Returns a verdict dict with ``statistical_verdict`` ∈ {PASS(<family>),
    NO-WINNER, INSUFFICIENT-POWER} plus every evidence block (family table,
    BH, bars, CIs, P@10/P@5, escalation, insufficient-power disposition).
    """
    configs = [rd["config"] for rd in report_data.values()]
    ctrl_name = next(c["name"] for c in configs if c["model"] == CONTROL_MODEL)
    ctrl_data = report_data[ctrl_name]["data"]
    family_configs: dict[str, list[str]] = {}
    for cfg in configs:
        family_configs.setdefault(cfg["model"], []).append(cfg["name"])

    # control aggregates (own filtered set — the control's absolute level)
    control = {
        "turn_recall@10": _aggregate_metric(ctrl_data, "turn_recall@10"),
        "ndcg@10": _aggregate_metric(ctrl_data, "ndcg@10"),
        "p@10": _aggregate_metric(ctrl_data, "p@10"),
        "p@5": _aggregate_metric(ctrl_data, "p@5"),
    }
    ctrl_tr10 = control["turn_recall@10"]

    # 6-config → 3-family reduction: the family delta per metric = max of
    # its configs (pre-registered selection rule); per-config mean deltas
    # are kept for the split-config tie rule.
    family_best: dict[tuple[str, str], dict] = {}
    config_mean_deltas: dict[str, dict[str, float]] = {}
    for cfg_name in [c["name"] for c in configs]:
        config_mean_deltas[cfg_name] = {}
        for metric in CO_PRIMARY:
            deltas, common, dropped = _paired(
                report_data[cfg_name]["data"], ctrl_data, metric)
            config_mean_deltas[cfg_name][metric] = _mean(deltas)
    for family in FAMILY_ORDER:
        for metric in CO_PRIMARY:
            best: dict | None = None
            for cfg_name in family_configs[family]:
                deltas, common, dropped = _paired(
                    report_data[cfg_name]["data"], ctrl_data, metric)
                md = config_mean_deltas[cfg_name][metric]
                cand = {"config": cfg_name, "deltas": deltas, "n": len(deltas),
                        "dropped": dropped, "mean_delta": md, "qids": common}
                if best is None or md > best["mean_delta"]:
                    best = cand
            family_best[(family, metric)] = best  # type: ignore[assignment]

    def _paired_ctrl_mean(key: tuple[str, str]) -> float:
        return _mean([ctrl_data[q][key[1]] for q in family_best[key]["qids"]])

    # one-sided bootstrap p per (family, metric); BH-FDR over m=6
    bh_order = [(f, m) for f in FAMILY_ORDER for m in CO_PRIMARY]
    pvals: dict[tuple[str, str], float] = {
        key: one_sided_bootstrap_p(family_best[key]["deltas"],
                                   n_resamples=n_resamples)
        for key in bh_order}
    rejected = bh_fdr([pvals[k] for k in bh_order], q=Q)
    bh_map = dict(zip(bh_order, rejected, strict=True))

    cleared: dict[str, dict[str, bool]] = {}
    mean_deltas: dict[str, dict[str, float]] = {}
    bars: dict[str, dict[str, float]] = {}
    ci90: dict[str, dict[str, dict]] = {}
    for family in FAMILY_ORDER:
        cleared[family] = {}
        mean_deltas[family] = {}
        bars[family] = {}
        ci90[family] = {}
        for metric in CO_PRIMARY:
            key = (family, metric)
            fb = family_best[key]
            ctrl_mean = _paired_ctrl_mean(key)
            rel = (fb["mean_delta"] / ctrl_mean if ctrl_mean > 0
                   else -float("inf"))
            floor_ok = rel >= NOMINAL_FLOOR
            bh_ok = bool(bh_map[key])
            mean_deltas[family][metric] = fb["mean_delta"]
            cleared[family][metric] = floor_ok and bh_ok
            ci = paired_bootstrap_ci(fb["deltas"], n_resamples=n_resamples)
            bars[family][metric] = n_adaptive_bar(
                _stdev(fb["deltas"]), fb["n"], ctrl_mean)
            ci90[family][metric] = {
                "lower": round(ci.lower, 4), "upper": round(ci.upper, 4),
                "mean": round(ci.mean, 4), "n": ci.n,
            }
    winners = [f for f in FAMILY_ORDER if any(cleared[f][m] for m in CO_PRIMARY)]
    family_rels = {f: {m: (mean_deltas[f][m] / _paired_ctrl_mean((f, m))
                           if _paired_ctrl_mean((f, m)) > 0 else -float("inf"))
                       for m in CO_PRIMARY} for f in FAMILY_ORDER}

    # per-family absolute turn_recall@10 (best tr10 config, paired set) —
    # the INSUFFICIENT-POWER absolute-delta fallback
    family_abs_tr10: dict[str, float] = {}
    for family in FAMILY_ORDER:
        fb = family_best[(family, "turn_recall@10")]
        cfg_data = report_data[fb["config"]]["data"]
        family_abs_tr10[family] = _mean(
            [cfg_data[q]["turn_recall@10"] for q in fb["qids"]])

    # P@10 (secondary) + P@5 (tertiary) — reported, not gated
    family_p10: dict[str, dict[str, float]] = {}
    family_p5: dict[str, dict[str, float]] = {}
    for family in FAMILY_ORDER:
        cfg_name = family_best[(family, "turn_recall@10")]["config"]
        cfg_data = report_data[cfg_name]["data"]
        for out, metric in (("p@10", "p@10"), ("p@5", "p@5")):
            fv = _mean([m[metric] for m in cfg_data.values()
                        if m.get(metric) is not None])
            cv = _mean([m[metric] for m in ctrl_data.values()
                        if m.get(metric) is not None])
            table = family_p10 if out == "p@10" else family_p5
            table[family] = {"control_mean": round(cv, 4),
                             "family_mean": round(fv, 4),
                             "mean_delta": round(fv - cv, 4)}

    # per-category gains (escalation trigger (iii)) — the 4 paper categories
    categories: dict[str, dict[str, dict[str, float]]] = {}
    for category in PAPER_CATEGORIES:
        categories[category] = {}
        for family in FAMILY_ORDER:
            categories[category][family] = {}
            for metric in CO_PRIMARY:
                key = (family, metric)
                fb = family_best[key]
                cfg_data = report_data[fb["config"]]["data"]
                cq = [q for q in fb["qids"]
                      if ctrl_data[q].get("category") == category]
                categories[category][family][metric] = _mean(
                    [cfg_data[q][metric] - ctrl_data[q][metric] for q in cq])

    # ── insufficient power ───────────────────────────────────────────────────
    n_paired_min = min(fb["n"] for fb in family_best.values())
    insufficient = (n_paired_min < MIN_PAIRED_N
                    or ctrl_tr10 < CONTROL_DEGENERATE)
    ip_block = None
    if insufficient:
        abs_clear = {f: round(family_abs_tr10[f], 4)
                     for f in FAMILY_ORDER
                     if family_abs_tr10[f] >= ABSOLUTE_FALLBACK}
        ip_block = {
            "n_paired_min": n_paired_min,
            "control_turn_recall@10": round(ctrl_tr10, 4),
            "absolute_fallback_clearances": abs_clear,
            "absolute_fallback_threshold": ABSOLUTE_FALLBACK,
            "judgment_tiebreak": list(JUDGMENT_TIEBREAK_ORDER),
            "note": ("INSUFFICIENT-POWER disposition: judgment tiebreak in "
                     "pinned order — mini-BEIR OOD datasets → labeled-pair "
                     "calibration → nDCG@10 — recorded as judgment-based in "
                     "ADR-009 (the gate EMITS this recommendation; the "
                     "actual judgment is human/ADR)."),
        }

    # ── escalation branch (fires when NEITHER metric clears) ────────────────
    escalation: dict[str, Any] = {"triggered": False}
    if not winners and not insufficient:
        triggers: list[str] = []
        if any(fb["mean_delta"] > 0 and pvals[key] < ESCALATION_P
               for key, fb in family_best.items()):
            triggers.append("positive-delta-p<0.10")
        if ctrl_tr10 >= ESCALATION_CEILING:
            triggers.append("control-ceiling>=0.50")
        if any(d >= ESCALATION_CATEGORY_PP
               for cat in categories.values()
               for fam in cat.values()
               for d in fam.values()):
            triggers.append("per-category-gain>=5pp")
        escalation = {"triggered": bool(triggers), "triggers": triggers}
        if triggers:
            combined = combined_rank(FAMILY_ORDER, mean_deltas)
            top2 = sorted(FAMILY_ORDER, key=lambda f: combined[f])[:2]
            escalation["top2"] = top2
            escalation["judged_families"] = [*top2, CONTROL_MODEL]
            escalation["judged"] = _evaluate_escalation(
                manifest, manifest_dir, top2, ctrl_data, n_resamples)

    # ── winner + swap config + verdict ──────────────────────────────────────
    swap_config: str | None = None
    split_rule: str | None = None
    if insufficient:
        stat_verdict = "INSUFFICIENT-POWER"
    elif winners:
        e2e8_p95 = _read_e2e8_latencies(manifest, manifest_dir)
        winner = multiwinner_pick(winners, mean_deltas, e2e8_p95=e2e8_p95)
        swap_config, split_rule = _swap_config_for(
            winner, family_configs[winner], cleared, config_mean_deltas,
            family_rels)
        stat_verdict = f"PASS({winner})"
    elif escalation.get("triggered") and escalation.get("judged", {}).get("pass"):
        winner = escalation["judged"]["winner"]
        # P2 fix (code-review): the judged run executed on HNSW — if the
        # artifact records which config ran there, THAT config is the swap
        # config (the vector-burn config deltas are from the embedded
        # surface, not the judged run); else fall back to the split rule.
        judged_cfg = escalation["judged"].get("config")
        if judged_cfg in family_configs.get(winner, []):
            swap_config, split_rule = judged_cfg, "judged-run-config"
        else:
            swap_config, split_rule = _swap_config_for(
                winner, family_configs[winner], cleared, config_mean_deltas,
                family_rels)
        stat_verdict = f"PASS({winner})"
    else:
        stat_verdict = "NO-WINNER"

    return {
        "statistical_verdict": stat_verdict,
        "winner": stat_verdict[5:-1] if stat_verdict.startswith("PASS(") else None,
        "burn_qids": sorted(ctrl_data),
        "swap_config": swap_config,
        "split_config_rule": split_rule,
        "clearing_families": list(winners),
        "split": manifest.get("split"),
        "retriever": manifest.get("retriever"),
        "control": control,
        "families": _family_table(
            FAMILY_ORDER, family_configs, family_best, cleared, pvals, bh_map,
            bars, ci90, family_p10, family_p5, family_abs_tr10,
            _paired_ctrl_mean),
        "bh": {"q": Q, "m": M, "z": Z,
               "pvals": {f"{f}__{m}": pvals[(f, m)]
                         for f in FAMILY_ORDER for m in CO_PRIMARY},
               "rejected": {f"{f}__{m}": bh_map[(f, m)]
                            for f in FAMILY_ORDER for m in CO_PRIMARY}},
        "n_paired_min": n_paired_min,
        "bars": {f: {m: round(bars[f][m], 4) for m in CO_PRIMARY}
                 for f in FAMILY_ORDER},
        "categories": categories,
        "escalation": escalation,
        "insufficient_power": ip_block,
        "tiebreak_evidence": {
            "combined_rank": combined_rank(FAMILY_ORDER, mean_deltas),
            "e2e8_p95_ms": _read_e2e8_latencies(manifest, manifest_dir),
        },
    }


def _family_table(families, family_configs, family_best, cleared, pvals,
                  bh_map, bars, ci90, family_p10, family_p5, family_abs_tr10,
                  paired_ctrl_mean) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for family in families:
        metrics = {}
        for metric in CO_PRIMARY:
            key = (family, metric)
            fb = family_best[key]
            ctrl_mean = paired_ctrl_mean(key)
            rel = (fb["mean_delta"] / ctrl_mean if ctrl_mean > 0
                   else -float("inf"))
            metrics[metric] = {
                "best_config": fb["config"],
                "n": fb["n"],
                "control_mean": round(ctrl_mean, 4),
                "family_mean": round(ctrl_mean + fb["mean_delta"], 4),
                "mean_delta": round(fb["mean_delta"], 4),
                "relative_delta": round(rel, 4),
                "p": round(pvals[key], 6),
                "bh_rejected": bool(bh_map[key]),
                "cleared": bool(cleared[family][metric]),
                "bar": round(bars[family][metric], 4),
                "ci90": ci90[family][metric],
            }
        out[family] = {
            "configs": list(family_configs[family]),
            "winner": bool(any(cleared[family][m] for m in CO_PRIMARY)),
            "cleared": {m: bool(cleared[family][m]) for m in CO_PRIMARY},
            "metrics": metrics,
            "p@10": family_p10[family],
            "p@5": family_p5[family],
            "absolute_turn_recall@10": round(family_abs_tr10[family], 4),
        }
    return out


# ── escalation judged run ───────────────────────────────────────────────────

def _evaluate_escalation(manifest, manifest_dir, top2, ctrl_data,
                         n_resamples) -> dict[str, Any]:
    """Evaluate the escalation judged run (end-to-end accuracy on HNSW).

    PASS criterion (pre-registered): the judged winner clears end-to-end
    accuracy vs control by ≥+5% relative with one-sided paired p<0.10
    pre-FDR on the same category set — the FULL filtered question set,
    fingerprint-pinned (a post-hoc n that shrinks until p<0.10 is a
    cherry-picking window, forbidden). Judge unavailable/non-answers →
    NO-WINNER with negative evidence.
    """
    def fail(reason):
        return {"pass": False, "winner": None, "reason": reason}
    entry = manifest.get("escalation_judged")
    if not isinstance(entry, dict) or not entry.get("path"):
        return fail("escalation fired but no escalation-judged artifact "
                    "recorded — pre-registered NO-WINNER (run the judged "
                    "top-2+control pass on the production HNSW surface)")
    path = _resolve(entry["path"], manifest_dir)
    if not path.is_file():
        return fail(f"escalation-judged artifact file missing: {path}")
    if entry.get("sha") and _sha256_file(path) != entry["sha"]:
        return fail("escalation-judged artifact sha mismatch (tampered)")
    try:
        art = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return fail(f"escalation-judged artifact unreadable: {e!r}")
    winner = art.get("winner")
    if winner not in top2:
        return fail(f"judged winner {winner!r} not in the pre-registered "
                    f"top-2 families {top2}")
    if art.get("judge_available") is False:
        return fail("judge unavailable/non-answers — pre-registered "
                    "NO-WINNER with the negative evidence attached")
    per_q = art.get("per_question") or {}
    if not per_q:
        return fail("judged run produced no per-question accuracy "
                    "(judge non-answers) — pre-registered NO-WINNER")
    burn_qids = sorted(ctrl_data)
    fp = art.get("question_set_fingerprint")
    if fp != question_set_fingerprint(burn_qids):
        return fail("question-set fingerprint mismatch: judged run covers a "
                    "different question set than the burn's FULL filtered "
                    "set — a post-hoc n cherry-pick is forbidden")
    # P0 fix (code-review): the fingerprint pins the question SET, not the
    # per-question coverage — an artifact declaring the full-set fingerprint
    # but carrying per_question for only a subset must not pass (with the
    # HNSW spot-check WAIVED on the escalation path, that subset would be
    # the ONLY gate on shipping).
    if set(per_q) != set(burn_qids):
        return fail(f"judged per_question keys ({len(per_q)}) do not cover "
                    f"the burn's FULL filtered question set "
                    f"({len(burn_qids)}) — the fingerprint alone cannot pin "
                    "per-question evidence; a post-hoc n that shrinks until "
                    "p<0.10 is a cherry-picking window (forbidden)")
    deltas = [per_q[q]["winner"] - per_q[q]["control"]
              for q in sorted(per_q)
              if per_q[q].get("winner") is not None
              and per_q[q].get("control") is not None]
    if not deltas:
        return fail("no usable judged pairs (non-answers) — pre-registered "
                    "NO-WINNER")
    ctrl_acc = _mean([per_q[q]["control"] for q in sorted(per_q)
                      if per_q[q].get("control") is not None])
    # P2 fix (code-review): mirror CONTROL_DEGENERATE — a near-zero judged
    # control accuracy makes the relative gain meaningless (denominator
    # → 0 inflates gain toward inf) → NO-WINNER.
    if ctrl_acc < CONTROL_DEGENERATE:
        return fail(f"judged control accuracy {ctrl_acc:.4f} below the "
                    f"{CONTROL_DEGENERATE:.0%} floor — a degenerate control "
                    "makes relative gain meaningless (pre-registered "
                    "NO-WINNER)")
    gain = _mean(deltas) / ctrl_acc
    p = one_sided_bootstrap_p(deltas, n_resamples=n_resamples)
    passed = gain >= NOMINAL_FLOOR and p < ESCALATION_P
    return {
        "pass": passed,
        "winner": winner,
        "config": art.get("config"),
        "relative_gain": round(gain, 4),
        "one_sided_p": round(p, 6),
        "n": len(deltas),
        "judge_id": art.get("judge_id"),
        "question_set_fingerprint": fp,
        "retrieval_metric_deltas": art.get("retrieval_metric_deltas") or {},
        "reason": ("judged winner clears ≥+5% relative with one-sided paired "
                   "p<0.10 pre-FDR on the full category set — the escalation "
                   "judged run executed on the production HNSW surface and "
                   "satisfies GATE (c)" if passed else
                   "judged winner does NOT clear ≥+5% relative with one-sided "
                   "paired p<0.10 pre-FDR — NO-WINNER"),
    }


# ── preconditions (HARD PR2 gates) ──────────────────────────────────────────

def _check_product_call(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"name": "product_call", "met": False,
                "detail": "product-call.json missing — the hosted-vs-local "
                          "decision must be recorded BEFORE the burn"}
    try:
        pc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {"name": "product_call", "met": False,
                "detail": "product-call.json unreadable"}
    decision = pc.get("decision")
    if decision not in PRODUCT_CALL_ENUM:
        return {"name": "product_call", "met": False,
                "detail": f"product-call decision {decision!r} not in "
                          f"{PRODUCT_CALL_ENUM}"}
    ts_raw = pc.get("timestamp")
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {"name": "product_call", "met": False,
                "detail": f"product-call timestamp {ts_raw!r} unparseable"}
    if ts > datetime.now(UTC):
        return {"name": "product_call", "met": False,
                "detail": f"product-call timestamp {ts_raw!r} is in the future"}
    if decision != "server-side":
        return {"name": "product_call", "met": False,
                "detail": f"product-call decision {decision!r} — GATE (b) "
                          "requires server-side default embedding"}
    return {"name": "product_call", "met": True, "detail": decision}


def _check_hnsw_spotcheck(manifest, manifest_dir, n_resamples,
                          burn_qids, winner) -> dict:
    """Standard winner-vs-control HNSW spot-check: artifact present + cleared.
    The gate RECOMPUTES the one-sided p from the artifact's per-question
    deltas (m=2: BH q=0.10 → min p ≤ 0.05) and requires the artifact's
    declared ``cleared`` to agree. The artifact is pinned to the winner and
    to its own n = the FULL filtered-split question set (a subset spot-check
    that shrinks until p clears is a cherry-picking window — forbidden);
    per metric the delta list must COVER the full set: with the dropped
    accounting shape, len(deltas) + len(dropped_qids) == the full set, where
    dropped_qids are breaker_open/absent questions recorded as None
    sentinels in the full-length deltas and skipped by the recomputed p. An
    artifact WITHOUT dropped_qids (old shape) falls back to the strict
    len(deltas) == full set check (fail-closed — a short delta list is an
    unverified subset).

    This check is only evaluated when there IS a winner: on NO-WINNER /
    INSUFFICIENT-POWER there is nothing to spot-check, so the precondition
    is WAIVED in :func:`check_preconditions` (a missing artifact must not
    BLOCK-mask an honest non-passing outcome)."""
    path = (manifest.get("preconditions") or {}).get("hnsw_spotcheck")
    if not path:
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": "hnsw_spotcheck path missing from the manifest"}
    p = _resolve(path, manifest_dir)
    if not p.is_file():
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": f"HNSW spot-check artifact missing: {p}"}
    try:
        art = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": "HNSW spot-check artifact unreadable"}
    # P1 fix (code-review): winner:null must NOT validate the gate's winner
    # — an unattributed artifact proves nothing about the shipped candidate.
    if winner is not None and art.get("winner") != winner:
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": f"HNSW spot-check artifact winner "
                          f"{art.get('winner')!r} != gate winner {winner!r} — "
                          "a null/unattributed artifact does not validate "
                          "the gate winner"}
    art_n = art.get("n")
    if burn_qids is not None and art_n != len(burn_qids):
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": f"HNSW spot-check n={art_n} != the FULL filtered-"
                          f"split question set ({len(burn_qids)}) — a subset "
                          "spot-check that shrinks until p clears is a "
                          "cherry-picking window (forbidden)"}
    md = art.get("metric_deltas") or {}
    dropped_qids = art.get("dropped_qids") or []
    new_shape = "dropped_qids" in art
    recomputed: list[float] = []
    for metric in CO_PRIMARY:
        entry = md.get(metric) or {}
        deltas = entry.get("deltas") or []
        if not deltas:
            return {"name": "hnsw_spotcheck", "met": False,
                    "detail": f"HNSW spot-check missing per-question deltas "
                              f"for {metric} (the gate recomputes the p)"}
        # P1 fix (code-review): the declared n only pins the artifact's OWN
        # count — the recomputed p must be over deltas COVERING the FULL
        # filtered-split set. New shape: dropped questions are listed in
        # dropped_qids and keep None sentinels in the full-length deltas, so
        # the PAIRED (non-None) delta count + len(dropped_qids) must equal the
        # burn set. Old shape (no dropped_qids field): strict len(deltas) ==
        # burn set, fail-closed (a subset of deltas that shrinks until p
        # clears is a cherry-picking window, forbidden).
        paired = [d for d in deltas if d is not None]
        covered = (len(paired) + len(dropped_qids) if new_shape
                   else len(deltas))
        if burn_qids is None or covered != len(burn_qids):
            expect = (f"{len(burn_qids)}" if burn_qids is not None
                      else "unknown (no burn set)")
            return {"name": "hnsw_spotcheck", "met": False,
                    "detail": f"HNSW spot-check {metric} covers {covered} "
                              f"questions ({len(paired)} paired per-question "
                              f"deltas + {len(dropped_qids)} dropped) != the "
                              f"FULL filtered-split question set ({expect}) — "
                              "recomputing p over an unverified subset is a "
                              "cherry-picking window (forbidden)"}
        # None sentinels are the dropped questions — skip them in the
        # recomputed p (the one-sided test runs on the paired non-dropped).
        if not paired:
            return {"name": "hnsw_spotcheck", "met": False,
                    "detail": f"HNSW spot-check {metric} carries no usable "
                              f"per-question deltas (all questions dropped) — "
                              "nothing to recompute"}
        recomputed.append(one_sided_bootstrap_p(paired,
                                                n_resamples=n_resamples))
    cleared_recomputed = min(recomputed) <= Q / 2  # m=2: q/2 = 0.05 (z≈1.645)
    declared = bool(art.get("cleared"))
    if declared != cleared_recomputed:
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": f"HNSW spot-check declared cleared={declared} but "
                          f"recomputation from the per-question deltas gives "
                          f"min p = {min(recomputed):.4f} — inconsistent "
                          "evidence"}
    if not cleared_recomputed:
        return {"name": "hnsw_spotcheck", "met": False,
                "detail": f"HNSW spot-check NOT cleared: min one-sided p = "
                          f"{min(recomputed):.4f} > 0.05 (m=2, q=0.10) — the "
                          "winner does not hold on the production HNSW "
                          "surface"}
    return {"name": "hnsw_spotcheck", "met": True,
            "detail": f"cleared (min one-sided p = {min(recomputed):.4f} ≤ "
                      "0.05, m=2)"}


def _check_e2e8(manifest, manifest_dir, required_models) -> dict:
    """Pre-swap E2E-8 ≤300ms p95 (censored arm, deployment VM class) for the
    winner + ALL gate-clearing candidates (the multi-winner tiebreak needs
    every clearing candidate's latency)."""
    e2e8_map = (manifest.get("preconditions") or {}).get("e2e8") or {}
    for model in sorted(required_models):
        path = e2e8_map.get(model)
        if not path:
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"missing E2E-8 report for {model} — winner + "
                              "all gate-clearing candidates must be measured "
                              "pre-swap on the deployment VM class (T3 "
                              "--model)"}
        p = _resolve(path, manifest_dir)
        if not p.is_file():
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"E2E-8 report missing for {model}: {p}"}
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"E2E-8 report unreadable for {model}"}
        arms = report.get("arms") or {}
        e2e = arms.get("e2e") or {}
        if e2e.get("verdict") == "INVALIDATED":
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"E2E-8 report INVALIDATED for {model}"}
        p95 = e2e.get("censored_p95_ms")
        if not isinstance(p95, (int, float)):
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"E2E-8 report for {model} lacks "
                              "arms.e2e.censored_p95_ms"}
        if float(p95) > E2E8_LIMIT_MS:
            return {"name": "e2e8_<=300ms", "met": False,
                    "detail": f"pre-swap E2E-8 p95 {p95}ms > "
                              f"{E2E8_LIMIT_MS:.0f}ms for {model} — the "
                              "latency band vetoes the swap"}
    return {"name": "e2e8_<=300ms", "met": True,
            "detail": f"all required models ≤ {E2E8_LIMIT_MS:.0f}ms p95"}


def _check_issue265(manifest) -> dict:
    """#265 merge-status check: no non-384 dimension may have landed before
    PR2 (if it did, the 768/1024 pool reopens — PR2 is not created as
    planned). Fail closed on anything but the recorded safe status."""
    raw = (manifest.get("preconditions") or {}).get("issue_265")
    status = raw.get("status") if isinstance(raw, dict) else None
    if status not in ISSUE265_SAFE:
        return {"name": "issue_265", "met": False,
                "detail": f"#265 status {status!r} — a non-384 dimension "
                          "landed (or is unknown); the 768/1024 pool reopens "
                          "and PR2 is not created as planned"}
    return {"name": "issue_265", "met": True, "detail": status}


def check_preconditions(manifest, manifest_dir, stats, n_resamples) -> tuple[list[dict], list[str]]:
    """Validate the HARD PR2 preconditions; returns (checks, errors).

    The HNSW spot-check is WAIVED whenever there is no winner to validate
    (escalation path — the judged run on HNSW supersedes it; and P2 fix:
    NO-WINNER / INSUFFICIENT-POWER — a missing artifact must not BLOCK-mask
    an honest non-passing outcome)."""
    checks: list[dict] = []
    errors: list[str] = []

    checks.append(_check_product_call(
        _resolve((manifest.get("preconditions") or {}).get("product_call"),
                 manifest_dir)
        if (manifest.get("preconditions") or {}).get("product_call") else None))

    if (stats.get("escalation") or {}).get("triggered"):
        stats["hnsw_waived"] = True
        checks.append({"name": "hnsw_spotcheck", "met": True,
                       "detail": "WAIVED — the escalation judged top-2+"
                                 "control run executed on the production HNSW "
                                 "surface supersedes the standard spot-check "
                                 "(GATE (c) is satisfied by the escalation "
                                 "run itself; the standard spot-check is "
                                 "waived)"})
    elif stats.get("winner") is None:
        # P2 fix (code-review): no winner → nothing to spot-check. The
        # precondition is WAIVED (the spot-check gates a SHIPPED winner
        # only); a missing/unreadable artifact must not turn an honest
        # NO-WINNER / INSUFFICIENT-POWER into a BLOCKED mask.
        stats["hnsw_waived"] = True
        checks.append({"name": "hnsw_spotcheck", "met": True,
                       "detail": "WAIVED — no winner to spot-check "
                                 "(NO-WINNER/INSUFFICIENT-POWER); the HNSW "
                                 "spot-check validates a shipped winner "
                                 "only"})
    else:
        stats["hnsw_waived"] = False
        checks.append(_check_hnsw_spotcheck(
            manifest, manifest_dir, n_resamples, stats.get("burn_qids"),
            stats.get("winner")))

    required_e2e8: set[str] = set(stats.get("clearing_families") or [])
    if stats.get("winner"):
        required_e2e8.add(stats["winner"])
    checks.append(_check_e2e8(manifest, manifest_dir, required_e2e8))
    checks.append(_check_issue265(manifest))

    for c in checks:
        if not c["met"]:
            errors.append(c["detail"])
    return checks, errors


# ── public entry ────────────────────────────────────────────────────────────

def run_gate(
    manifest: Mapping[str, Any],
    *,
    manifest_dir: Path | None = None,
    repo_root: Path | None = None,
    head_sha_fn: Callable[[Path | None], str | None] | None = None,
    changed_files_fn: Callable[[str, Path | None], list[str] | None] | None = None,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    out_path: str | Path | None = None,
) -> dict:
    """Run the full gate: manifest validation → decision rule → preconditions.

    Returns the verdict dict (machine-readable). ``head_sha_fn`` /
    ``changed_files_fn`` are injectable for tests; defaults run real git.
    """
    errors, report_data = validate_manifest(
        manifest, manifest_dir=manifest_dir, repo_root=repo_root,
        head_sha_fn=head_sha_fn, changed_files_fn=changed_files_fn)

    if errors:
        verdict = _blocked(errors)
    else:
        stats = decision_rule(report_data, manifest=manifest,
                              manifest_dir=manifest_dir,
                              n_resamples=n_resamples)
        pre_checks, pre_errors = check_preconditions(
            manifest, manifest_dir, stats, n_resamples)
        stats["preconditions"] = {"checks": pre_checks}
        if pre_errors:
            verdict = _blocked(pre_errors, stats=stats)
        else:
            verdict = {
                **stats,
                "verdict": stats["statistical_verdict"],
                "blocked": False,
                "blocking_reasons": [],
            }

    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    return verdict


def _blocked(reasons: Sequence[str], stats: dict | None = None) -> dict:
    return {
        **(stats or {}),
        "verdict": "BLOCKED",
        "statistical_verdict": (stats or {}).get("statistical_verdict"),
        "blocked": True,
        "blocking_reasons": list(reasons),
    }


def _print_verdict(verdict: dict) -> None:
    print("=" * 72)
    print(f"#1349 embedder-selection gate — verdict: {verdict['verdict']}")
    if verdict["blocked"]:
        print("BLOCKED — HARD-FAIL reasons:")
        for r in verdict["blocking_reasons"]:
            print(f"  ✗ {r}")
    if verdict.get("statistical_verdict"):
        print(f"statistical verdict: {verdict['statistical_verdict']}")
    print(f"split={verdict.get('split')} retriever={verdict.get('retriever')} "
          f"n_paired_min={verdict.get('n_paired_min')}")
    control = verdict.get("control") or {}
    print(f"control: turn_recall@10={control.get('turn_recall@10') or 0.0:.4f} "
          f"ndcg@10={control.get('ndcg@10') or 0.0:.4f} "
          f"p@10={control.get('p@10') or 0.0:.4f}")
    for family, entry in (verdict.get("families") or {}).items():
        tr = entry["metrics"]["turn_recall@10"]
        nd = entry["metrics"]["ndcg@10"]
        print(f"  {family:<10} winner={entry['winner']!s:<5} "
              f"trΔ={tr['mean_delta']:+.4f} (rel {tr['relative_delta']:+.1%}, "
              f"p={tr['p']:.4f}, BH={tr['bh_rejected']}, bar {tr['bar']:.1%}) "
              f"ndcgΔ={nd['mean_delta']:+.4f} (p={nd['p']:.4f}, BH={nd['bh_rejected']})")
    esc = verdict.get("escalation") or {}
    if esc.get("triggered"):
        print(f"escalation: TRIGGERED ({', '.join(esc['triggers'])}) top2={esc.get('top2')}")
        if esc.get("judged"):
            print(f"  judged: pass={esc['judged'].get('pass')} "
                  f"gain={esc['judged'].get('relative_gain')} "
                  f"p={esc['judged'].get('one_sided_p')}")
    ip = verdict.get("insufficient_power")
    if ip:
        print(f"insufficient power: n={ip['n_paired_min']} "
              f"control_tr10={ip['control_turn_recall@10']:.4f} "
              f"absolute clearances={ip['absolute_fallback_clearances']}")
        print(f"  judgment tiebreak (pinned order): "
              f"{' → '.join(ip['judgment_tiebreak'])}")
    if verdict.get("swap_config"):
        print(f"swap config: {verdict['swap_config']} "
              f"(rule: {verdict.get('split_config_rule')})")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tests.eval.retrieval.gate_1349",
        description="The #1349 embedder-selection gate — the pre-registered "
                    "decision rule as committed, CI-tested code.")
    ap.add_argument("--manifest", required=True,
                    help="gate input manifest JSON (per-config reports + "
                         "precondition artifact paths)")
    ap.add_argument("--out", default=None,
                    help="verdict JSON path (default: "
                         "<manifest>.verdict.json next to the manifest)")
    ap.add_argument("--repo", default=None,
                    help="repo root for the code_sha drift check "
                         "(default: this repo)")
    args = ap.parse_args(argv)

    manifest_path = Path(args.manifest)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # P2 fix (code-review): clean fail-closed message, not a traceback.
        print(f"ERROR: cannot load manifest {manifest_path}: {e!r}",
              file=sys.stderr)
        return 1
    manifest_dir = manifest_path.parent
    repo_root = Path(args.repo) if args.repo else \
        Path(__file__).resolve().parent.parent.parent.parent
    out_path = args.out or manifest_path.with_name(
        manifest_path.stem + ".verdict.json")
    verdict = run_gate(manifest, manifest_dir=manifest_dir,
                       repo_root=repo_root, out_path=out_path)
    _print_verdict(verdict)
    print(f"verdict JSON saved to: {out_path}")
    passed = verdict["verdict"].startswith("PASS(") and not verdict["blocked"]
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
