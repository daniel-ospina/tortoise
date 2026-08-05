"""Generic decision comparison script — file options/criteria/findings to the Tortoise graph
and rank options by EP confidence.

Two modes:
  1. --input <file.json|.yaml> — full decision definition
  2. --options/--criteria/--findings/--context --edges (JSON strings on CLI)

MITIGATION SEMANTICS (TRUTH vs RELEVANCE):
  - truth_edges: NAND directly on the target finding point (it's FALSE)
  - relevance_edges: mitigate the OPERATOR (it's TRUE but matters LESS)
    Uses mitigate_operator with strength in [0.10, 0.50] range.
  - Never NAND an option/criterion point for bad fit — express fit on the operator.

Input format (JSON):
{
  "context": "my-decision",
  "options": {"opt:a": "Option A desc", "opt:b": "Option B desc"},
  "criteria": {"crit:1": "Criterion 1 desc"},
  "findings": {"finding:1": "Finding 1 desc", "finding:2": "Finding 2 desc"},
  "edges": [
    ["crit:1", "IMPL", "opt:a"],
    ["crit:1", "NAND", "opt:b"],
    ["finding:1", "IMPL", "opt:a"],
    ["finding:2", "NAND", "opt:b"]
  ],
  "truth_edges": [
    {"source": "finding:1", "op_type": "NAND", "target": "finding:3"}
  ],
  "relevance_edges": [
    {"source": "crit:1", "op_type": "NAND", "target": "opt:b",
     "reason": "Not relevant for this option", "strength": 0.20}
  ]
}

Run:
  cd "$(dirname "$0")/.."
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/decide.py --input docs/examples/my-decision.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _load_input(args) -> dict:
    """Load decision data from --input file or inline JSON arguments."""
    if args.input:
        input_path = args.input
        raw = open(input_path, encoding="utf-8").read()
        suffix = os.path.splitext(input_path)[1]
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                print("Error: PyYAML is required for YAML input. Run: pip install PyYAML",
                      file=sys.stderr)
                sys.exit(1)
            return yaml.safe_load(raw)
        return json.loads(raw)

    # Inline mode: read from CLI arguments
    options = json.loads(args.options) if args.options else {}
    criteria = json.loads(args.criteria) if args.criteria else {}
    findings = json.loads(args.findings) if args.findings else {}
    edges = json.loads(args.edges) if args.edges else []
    truth_edges = json.loads(args.truth_edges) if args.truth_edges else []
    relevance_edges = json.loads(args.relevance_edges) if args.relevance_edges else []

    return {
        "context": args.context or "decide",
        "options": options,
        "criteria": criteria,
        "findings": findings,
        "edges": edges,
        "truth_edges": truth_edges,
        "relevance_edges": relevance_edges,
    }


def main():
    p = argparse.ArgumentParser(
        prog="decide.py",
        description="Compare options via EP belief propagation on the Tortoise graph",
    )
    p.add_argument("--input", "-i", type=str, default=None,
                   help="Path to JSON or YAML input file with full decision definition")
    p.add_argument("--options", type=str, default=None,
                   help='JSON dict of option names to descriptions, e.g. \'{"opt:a":"desc"}\'')
    p.add_argument("--criteria", type=str, default=None,
                   help='JSON dict of criterion names to descriptions')
    p.add_argument("--findings", type=str, default=None,
                   help='JSON dict of finding names to descriptions')
    p.add_argument("--context", "-c", type=str, default=None,
                   help="Domain context for the decision (default: 'decide')")
    p.add_argument("--edges", type=str, default=None,
                   help='JSON list of [source, op_type, target] tuples')
    p.add_argument("--truth-edges", type=str, default=None,
                   help='JSON list of truth challenge edges: [{source, op_type, target}]')
    p.add_argument("--relevance-edges", type=str, default=None,
                   help='JSON list of relevance mitigation edges: [{source, op_type, target, reason, strength}]')
    p.add_argument("--db", type=str, default=None,
                   help="Override TORTOISE_DB_URI (docker:// URI)")
    p.add_argument("--context-free", action="store_true",
                   help="Compute confidence via explicit operator factors instead of context isolation")

    args = p.parse_args()

    if not args.input and not args.options:
        p.print_help()
        print("\nError: --input or --options required", file=sys.stderr)
        sys.exit(1)

    data = _load_input(args)

    ctx = data.get("context", "decide")
    options = data.get("options", {})
    criteria = data.get("criteria", {})
    findings = data.get("findings", {})
    edges = data.get("edges", [])
    truth_edges = data.get("truth_edges", [])
    relevance_edges = data.get("relevance_edges", [])

    if not options:
        print("Error: at least one option required", file=sys.stderr)
        sys.exit(1)

    # Connect to graph
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(uri)

    # Track all operator IDs for --context-free mode
    all_operator_ids: list[str] = []

    try:
        # ── Create all points ──
        all_points: dict[str, str] = {}
        for pid, content in {**options, **criteria, **findings}.items():
            kind = (
                "option" if pid.startswith(("opt:", "option:")) else
                "criterion" if pid.startswith(("crit:", "criterion:")) else
                "evidence"
            )
            try:
                p = sdk.create_point(kind, content, context=ctx, dedup=True)
                all_points[pid] = p["id"]
                print(f"  ✓ {pid} → {p['id']}")
            except Exception as e:
                print(f"  ⚠ {pid}: {e}")

        def _resolve(name: str) -> str:
            """Resolve a key or point ID to a graph point ID."""
            if name in all_points:
                return all_points[name]
            return name

        # ── Regular edges (IMPL/NAND from criteria/findings → options) ──
        # Track created operators so relevance_edges can reuse them instead of
        # creating duplicates (same src/op_type/tgt in both sections).
        created_ops: dict[tuple[str, str, str], str] = {}
        for edge in edges:
            if isinstance(edge, list):
                src, op_type, tgt = edge[0], edge[1], edge[2]
                label = edge[3] if len(edge) > 3 else None
            elif isinstance(edge, dict):
                src = edge["source"]
                op_type = edge["op_type"]
                tgt = edge["target"]
                label = edge.get("label")
            else:
                print(f"  ⚠ Unknown edge format: {edge}")
                continue

            try:
                op = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)],
                                         context=ctx, label=label)
                created_ops[(src, op_type, tgt)] = op["id"]
                all_operator_ids.append(op["id"])
                print(f"  ✓ {src} --{op_type}--> {tgt}")
            except Exception as e:
                print(f"  ⚠ {src} --{op_type}--> {tgt}: {e}")

        # ── Truth edges: NAND the target finding POINT (it's FALSE) ──
        for te in truth_edges:
            src = te["source"]
            op_type = te.get("op_type", "NAND")
            tgt = te["target"]
            try:
                top = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)], context=ctx)
                all_operator_ids.append(top["id"])
                print(f"  ⚡ truth: {src} --{op_type}--> {tgt}")
            except Exception as e:
                print(f"  ⚠ truth {src} --{op_type}--> {tgt}: {e}")

        # ── Relevance edges: mitigate the OPERATOR (TRUE but matters LESS) ──
        for re in relevance_edges:
            src = re["source"]
            op_type = re.get("op_type", "NAND")
            tgt = re["target"]
            reason = re.get("reason", "Overstated relevance")
            strength = re.get("strength", 0.30)
            # Clamp to valid mitigation range [0.10, 0.50]
            strength = max(0.10, min(0.50, strength))
            try:
                # Reuse the operator if this edge was already created in `edges`
                # (prevents duplicate operators feeding EP twice).
                op_id = created_ops.get((src, op_type, tgt))
                if op_id is None:
                    op = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)], context=ctx)
                    op_id = op["id"]
                    all_operator_ids.append(op_id)
                sdk.mitigate_operator(op_id, reason, strength)
                print(f"  ⚖ relevance: {src} --{op_type}--> {tgt} "
                      f"(mitigated {strength:.2f}: {reason})")
            except Exception as e:
                print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {e}")

        # ── Compute confidence per option ──
        try:
            if args.context_free and all_operator_ids:
                print(f"  (context-free mode: {len(all_operator_ids)} operator factors)")
                result = sdk.compute_confidence(factors=all_operator_ids)
            else:
                result = sdk.compute_confidence(context=ctx)
            print(f"\n✓ EP computed: {result['iterations']} iterations, "
                  f"converged={result['converged']}")
            confs = result.get("confidences", {})

            opt_conf: dict[str, float] = {}
            for pid, cid in all_points.items():
                if pid.startswith(("opt:", "option:")):
                    mean = confs.get(cid, {}).get("mean")
                    if isinstance(mean, (int, float)):
                        opt_conf[pid] = float(mean)

            if opt_conf:
                print("\n=== OPTION CONFIDENCE (higher = more supported) ===")
                ranked = sorted(opt_conf.items(), key=lambda kv: kv[1], reverse=True)
                name_width = max(len(pid) for pid in opt_conf)
                for pid, c in ranked:
                    bar = "█" * int(c * 20) + "░" * (20 - int(c * 20))
                    print(f"  {pid:<{name_width}}  {c:.4f}  {bar}")
        except Exception as e:
            print(f"\n⚠ compute_confidence: {e}")

        # ── Verify structure ──
        try:
            result = sdk.check_structure()
            ctx_issues = [i for i in result
                          if ctx in str(i.get("id", "")) or ctx in str(i.get("message", ""))]
            print(f"\n✓ Structure check: {len(result)} issues total, "
                  f"{len(ctx_issues)} in context '{ctx}'")
        except Exception as e:
            print(f"⚠ check_structure: {e}")

    finally:
        if sdk._proj:
            sdk._proj.close()

    print(f"\nDone. Decision comparison filed to context='{ctx}'")


if __name__ == "__main__":
    main()
