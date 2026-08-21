"""R1 granularity micro-test — the run protocol's step-2 sweep (#1540, epic
#1509).

Runs the LongMemEval runner across the three sweep points
``chunk_turns ∈ {1, 2, 4}`` (one very low, two middle — the owner-specified
3-point sweep) with all other knobs fixed, and emits a comparison table + a
deterministic winner per the D2 selection rule.

**Selection rule (D2):** v2 mode — maximize ``evidence_recall@10`` (the
extracted-point evidence; the denominator is granularity-neutral per D5),
with ``chunk_evidence_recall@10`` and ``context_tokens_mean`` recorded
alongside, subject to ``context_tokens_mean <= context_token_cap``; tie-break
→ smaller ``chunk_turns`` (finer evidence localization, less bloat).
Deterministic mode: the selection metric is knob-insensitive (chunks
unmarked, D3 — turn recall over uncapped turn points), so the deterministic
cell is a context-token/underfill view only, NOT a granularity selector.

**Run-protocol tie-in:** the selected granularity feeds the pilot (step 3)
and the 500-Q run (step 5); the report's methodology records the chosen
value (D7 — ``chunk_turns`` in the report methodology).

``--mock`` implies deterministic ingest when ``--ingest-mode`` is unset — a
mock run must be fully offline; real v2 sweeps need ``--extractor-model`` /
provider keys.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dataset as ds
from .judge import MockJudge, build_judge
from .reader import MockReader, build_reader
from .retrieve import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_POOL_MULTIPLIER,
)
from .run import _positive_int, run_evaluation

DEFAULT_SWEEP_POINTS = (1, 2, 4)
DEFAULT_SWEEP_KS = (5, 10, 20)


def _selection_metric_label(ingest_mode: str) -> str:
    return ("evidence_recall@10" if ingest_mode == "v2"
            else "turn_recall@10 (view-only)")


def _select_winner(results: list[dict], *, ingest_mode: str,
                   context_cap: int) -> dict | None:
    """The D2 selection rule. ``None`` in deterministic mode (knob-
    insensitive view) or when no config is eligible."""
    if ingest_mode != "v2":
        return None
    eligible = [
        r for r in results
        if r["context_tokens_mean"] <= context_cap
        and r["evidence_recall@10"] is not None
    ]
    if not eligible:
        return None
    # maximize evidence_recall@10, tie-break → smaller chunk_turns
    return max(eligible, key=lambda r: (r["evidence_recall@10"], -r["chunk_turns"]))


def run_sweep(
    instances: list[dict],
    *,
    chunk_turns_values: tuple[int, ...] = DEFAULT_SWEEP_POINTS,
    context_cap: int = DEFAULT_CONTEXT_TOKEN_CAP,
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
    ingest_mode: str = "v2",
    extractor_model=None,
    reader=None,
    judge=None,
    ks: tuple[int, ...] = DEFAULT_SWEEP_KS,
    work_dir: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict], dict | None]:
    """Run the 3-point sweep over ``instances`` with fixed other knobs.

    Returns ``(results, winner)`` where each result records the per-config
    aggregates (evidence/chunk-evidence/session/turn recall@10,
    context_tokens_mean, context_point_count_mean) and the winner is the D2
    selection (None in deterministic mode or when no config is eligible).
    """
    if ingest_mode not in ("deterministic", "v2"):
        raise ValueError(f"ingest_mode must be deterministic|v2, got {ingest_mode!r}")
    subset = instances[:limit] if limit is not None else instances
    results: list[dict] = []
    for turns in chunk_turns_values:
        outcomes, report = run_evaluation(
            subset, reader=reader, judge=judge, ks=ks, top_k=20,
            split="s", work_dir=work_dir, ingest_mode=ingest_mode,
            extractor_model=extractor_model,
            chunk_turns=turns, max_context_tokens=context_cap,
            max_chunks_per_session=max_chunks_per_session,
        )
        ret = report["retrieval"]
        results.append({
            "chunk_turns": turns,
            "evidence_recall@10": (ret.get("evidence_recall@k") or {}).get("10"),
            "chunk_evidence_recall@10": (
                ret.get("chunk_evidence_recall@k") or {}).get("10"),
            "session_recall@10": (ret.get("session_recall@k") or {}).get("10"),
            "turn_recall@10": (ret.get("turn_recall@k") or {}).get("10"),
            "context_tokens_mean": ret.get("context_tokens_mean", 0.0),
            "context_point_count_mean": ret.get("context_point_count_mean", 0.0),
            "n_completed": len(outcomes),
        })
    return results, _select_winner(results, ingest_mode=ingest_mode,
                                   context_cap=context_cap)


def _print_table(results: list[dict], *, ingest_mode: str,
                 context_cap: int, max_chunks_per_session: int) -> None:
    metric = _selection_metric_label(ingest_mode)
    print("=" * 88)
    print("R1 granularity sweep (run protocol step 2) — "
          f"ingest_mode={ingest_mode}")
    print(f"context_token_cap={context_cap}  "
          f"max_chunks_per_session={max_chunks_per_session}  "
          f"pool_depth=max(k)*{DEFAULT_POOL_MULTIPLIER}")
    print("=" * 88)
    header = (f"{'chunk_turns':>11}  {'ev_recall@10':>13}  "
              f"{'chunk_ev@10':>12}  {'sess_rec@10':>12}  "
              f"{'turn_rec@10':>12}  {'ctx_tokens':>11}  "
              f"{'ctx_points':>10}")
    print(header)
    print("-" * 88)
    for r in results:
        ev = f"{r['evidence_recall@10']:.3f}" if r["evidence_recall@10"] is not None else "N/A"
        cev = (f"{r['chunk_evidence_recall@10']:.3f}"
               if r["chunk_evidence_recall@10"] is not None else "N/A")
        tr = f"{r['turn_recall@10']:.3f}" if r["turn_recall@10"] is not None else "N/A"
        print(f"{r['chunk_turns']:>11}  {ev:>13}  {cev:>12}  "
              f"{r['session_recall@10']:.3f}  {tr:>12}  "
              f"{r['context_tokens_mean']:>11.1f}  "
              f"{r['context_point_count_mean']:>10.2f}")
    print("-" * 88)
    print(f"selection metric: maximize {metric} subject to "
          f"context_tokens_mean <= {context_cap}; tie-break smaller "
          f"chunk_turns (D2 #1540)")


def _print_winner(winner: dict | None, *, ingest_mode: str,
                  context_cap: int) -> None:
    if ingest_mode != "v2":
        print("\nDeterministic mode: the selection metric is knob-insensitive "
              "(chunks unmarked, D3) — no winner; this cell is a "
              "context-token/underfill view only.")
        return
    if winner is None:
        print("\nNO ELIGIBLE WINNER: no config satisfied "
              "context_tokens_mean <= cap with a real evidence_recall@10. "
              "Raising — do not proceed to the pilot with an unvalidated "
              "granularity.", file=sys.stderr)
        return
    print(f"\nSELECTED granularity: chunk_turns = {winner['chunk_turns']} "
          f"(evidence_recall@10 = {winner['evidence_recall@10']:.3f}, "
          f"context_tokens_mean = {winner['context_tokens_mean']:.1f})")
    print("→ the pilot (run protocol step 3) and the 500-Q run (step 5) use "
          "this value; the report methodology records it (D7).")
    if winner["context_tokens_mean"] > context_cap:
        print("⛔ winner's context_tokens_mean EXCEEDS the cap — raising.",
              file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.sweep_granularity",
        description="R1 3-point granularity sweep (run protocol step 2): "
                    "selects chunk_turns ∈ {1,2,4} for the pilot + 500-Q run.")
    p.add_argument("--split", default=ds.DEFAULT_SPLIT,
                   choices=sorted(ds.SPLIT_FILES),
                   help="dataset split (default s)")
    p.add_argument("--limit", type=int, default=20,
                   help="questions per config (default 20)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--ingest-mode", default=None,
                   choices=["deterministic", "v2"],
                   help="ingestion mode (default v2 — the V3 primary path; "
                        "--mock implies deterministic when unset)")
    p.add_argument("--mock", action="store_true",
                   help="offline: MockReader + MockJudge (implies deterministic "
                        "ingest when --ingest-mode unset — a mock run must be "
                        "fully offline)")
    p.add_argument("--chunk-turns", default="1,2,4",
                   help="comma-separated sweep points (default 1,2,4)")
    p.add_argument("--context-cap", type=_positive_int,
                   default=DEFAULT_CONTEXT_TOKEN_CAP,
                   help=f"reader context token budget (default {DEFAULT_CONTEXT_TOKEN_CAP})")
    p.add_argument("--max-chunks-per-session", type=_positive_int,
                   default=DEFAULT_MAX_CHUNKS_PER_SESSION,
                   help="per-session raw-chunk cap (default 2)")
    p.add_argument("--extractor-model", default=None,
                   help="extractor model spec for v2 real runs "
                        "(default: the production router)")
    p.add_argument("--output", default=None,
                   help="JSON output path for the sweep results")
    p.add_argument("--work-dir", default=None,
                   help="temp dir for per-question graphs")
    args = p.parse_args(argv)

    # --mock implies deterministic ingest when the mode is unset (fully
    # offline; real v2 sweeps need --extractor-model/keys).
    ingest_mode = args.ingest_mode or ("deterministic" if args.mock else "v2")
    if args.mock and ingest_mode == "v2":
        raise SystemExit("--mock implies deterministic ingest; pass "
                         "--ingest-mode v2 without --mock for a real v2 sweep")
    chunk_points = tuple(int(x.strip()) for x in args.chunk_turns.split(",")
                         if x.strip())
    if not chunk_points or any(v < 1 for v in chunk_points):
        raise SystemExit(f"--chunk-turns must be >= 1 values, got {args.chunk_turns!r}")

    instances = ds.load_dataset(args.split, limit=None, data_path=args.data,
                                download=True)

    if args.mock:
        reader, judge = MockReader(), MockJudge()
    else:
        reader, judge = build_reader(), build_judge()
    extractor_model = None
    if ingest_mode == "v2" and not args.mock:
        from tests.model_adapters import build_extractor_model
        extractor_model = build_extractor_model(max_tokens=None,
                                                temperature=0.0)

    results, winner = run_sweep(
        instances, chunk_turns_values=chunk_points,
        context_cap=args.context_cap,
        max_chunks_per_session=args.max_chunks_per_session,
        ingest_mode=ingest_mode, extractor_model=extractor_model,
        reader=reader, judge=judge, work_dir=args.work_dir,
        limit=args.limit,
    )
    _print_table(results, ingest_mode=ingest_mode,
                 context_cap=args.context_cap,
                 max_chunks_per_session=args.max_chunks_per_session)
    _print_winner(winner, ingest_mode=ingest_mode,
                  context_cap=args.context_cap)

    payload = {
        "selection_rule": ("v2: maximize evidence_recall@10 subject to "
                           "context_tokens_mean <= context_token_cap; "
                           "tie-break smaller chunk_turns (D2 #1540); "
                           "deterministic cell is view-only (knob-insensitive)"),
        "ingest_mode": ingest_mode,
        "context_token_cap": args.context_cap,
        "max_chunks_per_session": args.max_chunks_per_session,
        "pool_depth_multiplier": DEFAULT_POOL_MULTIPLIER,
        "results": results,
        "winner": winner,
        "note": ("winner None in deterministic mode (D2: knob-insensitive) or "
                 "when no config is eligible — see selection_rule"),
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"\nsweep JSON saved to: {args.output}")

    # Gate: the run protocol step-2 gate exits non-zero when no granularity
    # can be selected under the cap (never silently proceed with an
    # unvalidated knob).
    if ingest_mode == "v2" and winner is None:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
