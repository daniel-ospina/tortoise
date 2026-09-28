#!/usr/bin/env python3
"""#1370 — attribution audit: bound / unbound / misattributed subject bindings.

Two honest reports, no invented denominators:

1. **Graph report** (``--db`` / ``--uri``): the bound fraction over the graph's
   Points. The *unbound* denominator (attempted-but-refused) lives in the JSONL
   journal, because the capture write path does not persist the extraction's
   ``slots`` — pass ``--journal`` to get it. Without a journal the report says
   so rather than fabricating a denominator.

2. **Quality gate** (``--gold``): the binding POLICY's misattribution and
   unbound rates against authored ground truth. ``--calibrate`` sweeps τ_hi so
   a threshold can be chosen from measured rates, not a vibe.

Honest limitation: the quality gate measures the policy (threshold +
fail-closed + resolution), not the LLM's extraction quality — the gold labels
are authored, not model-produced. Real per-model calibration (D4) needs model
calls against a ~100–500-fact gold set and is out of scope here.

Usage:
    python3 tools/subject_binding_audit.py --gold tests/fixtures/subject_binding_gold.jsonl
    python3 tools/subject_binding_audit.py --gold ... --calibrate
    python3 tools/subject_binding_audit.py --db /tmp/memory.db --journal /tmp/events/events.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_GOLD = _REPO / "tests" / "fixtures" / "subject_binding_gold.jsonl"


def _graph_report(args) -> dict:
    from tortoise.sdk import TortoiseSDK
    from tortoise.subject_binding import audit_subject_binding

    # F1: this tool only READS the graph. Construct the SDK through a supported
    # path — no `sdk.test_guard = lambda: None` monkey-patch (a non-test
    # assignment to a public SDK member from `tools/` flips the surface
    # manifest's caller category and reds CI). The audit needs no guard bypass.
    # F4: `--uri` must NOT pass a positional `db_path` — the SDK takes the
    # embedded branch when `db_path is not None` and silently ignores
    # `TORTOISE_DB_URI`, reporting on an empty local graph.
    if args.uri:
        if not os.environ.get("TORTOISE_DB_URI"):
            raise SystemExit(
                "--uri requires TORTOISE_DB_URI to be set in the environment")
        sdk = TortoiseSDK(namespace=args.namespace or None)
    else:
        sdk = TortoiseSDK(args.db)
    try:
        return audit_subject_binding(sdk._get_proj().g, journal_path=args.journal)
    finally:
        sdk.close()


def _print_quality(metrics: dict) -> None:
    print(f"tau_hi={metrics['tau_hi']} tau_lo={metrics['tau_lo']} "
          f"rows={metrics['rows']}")
    print(f"  bound={metrics['bound']} "
          f"misattributed={metrics['misattributed']} "
          f"misattribution_rate={metrics['misattribution_rate']:.3f}")
    print(f"  gold_rows={metrics['gold_rows']} unmet={metrics['unmet']} "
          f"unbound_rate={metrics['unbound_rate']:.3f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="path to a Tortoise SDK graph (embedded)")
    ap.add_argument("--uri", action="store_true",
                    help="connect via TORTOISE_DB_URI instead of --db")
    ap.add_argument("--namespace", default=None)
    ap.add_argument("--journal", default=None,
                    help="JSONL journal path (supplies the unbound denominator)")
    ap.add_argument("--gold", default=str(_DEFAULT_GOLD),
                    help="authored subject-binding gold fixture (JSONL)")
    ap.add_argument("--calibrate", action="store_true",
                    help="sweep tau_hi and print the rate at each")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    from tortoise.subject_binding import quality_gate_metrics

    out: dict = {}
    if args.gold and os.path.exists(args.gold):
        if args.calibrate:
            sweep = []
            for hi in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
                m = quality_gate_metrics(args.gold, tau_hi=hi, tau_lo=0.1)
                sweep.append(m)
            out["calibration"] = sweep
            if not args.json:
                print("tau_hi  bound  misattributed  misattribution  unbound")
                for m in sweep:
                    print(f"{m['tau_hi']:>5}  {m['bound']:>5}  "
                          f"{m['misattributed']:>13}  "
                          f"{m['misattribution_rate']:>14.3f}  "
                          f"{m['unbound_rate']:>7.3f}")
        else:
            metrics = quality_gate_metrics(args.gold)
            out["quality_gate"] = metrics
            if not args.json:
                _print_quality(metrics)

    if args.db or args.uri:
        report = _graph_report(args)
        out["graph"] = report
        if not args.json:
            print(f"points={report['points_total']} "
                  f"subjects={report['subjects_total']} "
                  f"bound={report['bound']} "
                  f"bound_points={report['bound_points']} "
                  f"with_confidence={report['edges_with_confidence']} "
                  f"bound_fraction={report['bound_fraction']:.3f}")
            # F14: guard on the metric being None, not merely on the path being
            # absent — a non-existent --journal path yielded a None metric and
            # crashed the f-string with TypeError.
            if report.get("unbound_fraction") is None:
                print("  unbound denominator: UNKNOWN — "
                      + ("no existing --journal given" if report["journal"] is None
                         else "journal unreadable")
                      + " (slots are not persisted on the node)")
            else:
                print(f"  attempted={report['attempted']} "
                      f"unbound={report['unbound']} "
                      f"suspected={report['suspected']} "
                      f"unbound_fraction={report['unbound_fraction']:.3f}")

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
