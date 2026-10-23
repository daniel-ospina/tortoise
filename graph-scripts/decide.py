"""Generic decision comparison script — file options/criteria/findings to the Tortoise graph
and rank options by EP confidence.

Two modes:
  1. --input <file.json|.yaml> — full decision definition
  2. --options/--criteria/--findings/--context --edges (JSON strings on CLI)

MITIGATION SEMANTICS (TRUTH vs RELEVANCE):
  - truth_edges: NAND directly on the target finding point (it's FALSE)
  - relevance_edges: mitigate the OPERATOR (it's TRUE but matters LESS)
    Uses mitigate_operator with strength in [0.10, 0.50] range (clamped
    below). Single source of the strength semantics + dampening formula
    (w_eff = w * (1 - strength)): tortoise/weights.py module docstring
    (#2315) — 0.50 = strongest sanctioned mitigation.
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
  TORTOISE_DB_URI=docker://:@127.0.0.1:16379/tortoise uv run python graph-scripts/decide.py --input docs/examples/my-decision.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/decide.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/decide.py`"
    )

import argparse
import json
import os


def _load_input(args) -> dict:
    """Load decision data from --input file or inline JSON arguments."""
    if args.input:
        input_path = args.input
        raw = open(input_path, encoding="utf-8").read()  # noqa: SIM115
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


# ─────────────────────────────────────────────────────────────────────────────
# #6671 / #9631 — IDEMPOTENT OPERATOR WIRING, ONE IMPLEMENTATION
#
# The lookup lives in `tortoise/decide_wiring.py` and BOTH entry points call it:
# this script and `tortoise decide` (`tortoise/__main__.py::_cmd_decide`). The
# CLI kept the per-run-only cache after #6671 fixed it here, so re-running a
# decision through `tortoise decide` still duplicated every operator (#9631,
# measured: 3 → 6 operators, 1 → 2 mitigations, opt:a 0.6592 → 0.6756). A third
# hand-maintained copy is how that happened — there is now one writer.
# ─────────────────────────────────────────────────────────────────────────────
# The repo root is importable whether this file is run as a script or loaded
# from the test suite (`--db`, `main()`'s own insert, comes too late for a
# module-level import).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tortoise.decide_wiring import (  # noqa: E402
    damped_factor_ids,
    resolve_or_create_operator,
    select_damp_targets,
)


def file_decision(sdk, data: dict, *, context_free: bool = False) -> dict:
    """File a decision definition to the graph and rank its options by EP.

    Idempotent in its WRITES (#6671): filing the same decision twice reuses the
    existing points (content-hash dedup) and the existing operators, so the
    ranking no longer depends on how many times the script was run.

    Returns a dict with ``operators`` (one row per edge, with ``reused``),
    ``confidences`` (option id → EP mean), ``iterations`` and ``converged``.
    """
    ctx = data.get("context", "decide")
    options = data.get("options", {})
    criteria = data.get("criteria", {})
    findings = data.get("findings", {})
    edges = data.get("edges", [])
    truth_edges = data.get("truth_edges", [])
    relevance_edges = data.get("relevance_edges", [])
    proj = sdk._get_proj()

    # Track all operator IDs for --context-free mode (a factor list must not
    # carry the same operator twice — EP would count one bridge as two).
    all_operator_ids: list[str] = []
    operator_rows: list[dict] = []

    # ── Create all points ──
    all_points: dict[str, str] = {}
    for pid, content in {**options, **criteria, **findings}.items():
        kind = (
            "option" if pid.startswith(("opt:", "option:")) else
            "criterion" if pid.startswith(("crit:", "criterion:")) else
            "evidence"
        )
        try:
            # #1643 (D3): `context=` was removed from create_point in #49 —
            # passing it raised TypeError, every point soft-failed, and
            # operator resolution fell back to stub nodes (#334 class).
            # dedup=True keeps the write idempotent by content hash.
            p = sdk.create_point(kind, content, dedup=True)
            all_points[pid] = p["id"]
            print(f"  ✓ {pid} → {p['id']}")
        except Exception as e:
            print(f"  ⚠ {pid}: {e}")

    def _resolve(name: str) -> str:
        """Resolve a key or point ID to a graph point ID."""
        if name in all_points:
            return all_points[name]
        return name

    # Reuse map keyed on the RESOLVED ids (so `--edges` names and raw ids that
    # denote the same points converge on one operator).
    created_ops: dict[tuple[str, str, str, str], str] = {}

    def _elect(src_key: str, op_type: str, tgt_key: str, label: str | None,
               *, any_label: bool = False) -> tuple[str | None, bool, str | None]:
        """Resolve-or-create the bridge and record it. Never creates a twin."""
        src, tgt = _resolve(src_key), _resolve(tgt_key)
        try:
            op_id, reused, note = resolve_or_create_operator(
                sdk, proj, op_type, src, tgt, label, created_ops,
                any_label=any_label)
        except Exception as e:
            return None, False, str(e)
        if op_id not in all_operator_ids:
            all_operator_ids.append(op_id)
        operator_rows.append({
            "source": src_key, "op_type": op_type, "target": tgt_key,
            "label": label, "id": op_id, "reused": reused,
        })
        return op_id, reused, note

    def _wire(src_key: str, op_type: str, tgt_key: str, label: str | None,
              tick: str, *, any_label: bool = False) -> str | None:
        op_id, reused, note = _elect(src_key, op_type, tgt_key, label,
                                     any_label=any_label)
        if op_id is None:
            print(f"  ⚠ {src_key} --{op_type}--> {tgt_key}: {note}")
            return None
        suffix = f" (reused {op_id})" if reused else ""
        print(f"  {tick} {src_key} --{op_type}--> {tgt_key}{suffix}")
        if note:
            print(f"  ⚠ {src_key} --{op_type}--> {tgt_key}: {note}")
        return op_id

    # ── Regular edges (IMPL/NAND from criteria/findings → options) ──
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
        _wire(src, op_type, tgt, label, "✓")

    # ── Truth edges: NAND the target finding POINT (it's FALSE) ──
    for te in truth_edges:
        _wire(te["source"], te.get("op_type", "NAND"), te["target"],
              te.get("label"), "⚡ truth:")

    # ── Relevance edges: mitigate the OPERATOR (TRUE but matters LESS) ──
    for re in relevance_edges:
        src = re["source"]
        op_type = re.get("op_type", "NAND")
        tgt = re["target"]
        reason = re.get("reason", "Overstated relevance")
        strength = re.get("strength", 0.30)
        # Clamp to the sanctioned [0.10, 0.50] band (single source:
        # tortoise/weights.py module docstring, #2315).
        strength = max(0.10, min(0.50, strength))
        label = re.get("label")
        # A relevance edge is a MITIGATION, never a claim: the selection rule
        # (one writer — `tortoise.decide_wiring.select_damp_targets`) damps every
        # single-pair bridge on the triple, refuses the ones that also claim
        # other pairs, and scopes by label when the caller names one. This script
        # never mints a claim of its own here.
        pick = select_damp_targets(proj, op_type, _resolve(src), _resolve(tgt),
                                   label)
        for note in pick["notes"]:
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {note}")
        targets = pick["targets"]
        if not targets:
            continue
        # Record the damped bridges as this run's edges too (they are the
        # bridges the ranking reads). EVERY damped copy, via the rule both entry
        # points share — the CLI appends all of them too (#9631 review: the two
        # callers had forked this leg, so a polluted triple produced different
        # factor lists per entry point).
        for damped_id in damped_factor_ids(targets):
            if damped_id not in all_operator_ids:
                all_operator_ids.append(damped_id)
        operator_rows.append({
            "source": src, "op_type": op_type, "target": tgt,
            "label": label, "id": targets[0], "reused": True,
        })
        try:
            for target_id in targets:
                sdk.mitigate_operator(target_id, reason, strength)
            extra = "" if len(targets) == 1 else f" on {len(targets)} operators"
            # ONE line, the shape every other decide writer prints: the
            # mitigation detail belongs to the relevance tick, not to a second
            # line (the pre-#6671 script printed it inline, and
            # `tortoise/__main__.py::_cmd_decide` used to).
            named = ", ".join(str(t) for t in targets)
            print(f"  ⚖ relevance: {src} --{op_type}--> {tgt} "
                  f"(mitigated {strength:.2f}{extra}: {reason})")
            if len(targets) > 1:
                print(f"     damped: {named}")
        except Exception as e:
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {e}")

    # ── Compute confidence per option ──
    result: dict = {"context": ctx, "operators": operator_rows,
                    "confidences": {}, "iterations": 0, "converged": None}

    def _option_confidences(ep: dict) -> dict[str, float]:
        confs = ep.get("confidences") or {}
        out: dict[str, float] = {}
        for pid, cid in all_points.items():
            if pid.startswith(("opt:", "option:")):
                mean = confs.get(cid, {}).get("mean")
                if isinstance(mean, (int, float)):
                    out[pid] = float(mean)
        return out

    try:
        if context_free and all_operator_ids:
            print(f"  (context-free mode: {len(all_operator_ids)} operator factors)")
            ep = sdk.compute_confidence(factors=all_operator_ids)
        else:
            try:
                ep = sdk.compute_confidence()
            except Exception as wide_err:
                # The graph-wide pass can refuse on state that is not this
                # decision's (an uncalibrated foreign claim, a fail-closed
                # calibration posture); that must not cost us OUR ranking.
                print(f"  ⚠ graph-wide EP pass refused: {wide_err}")
                ep = {"confidences": {}, "iterations": 0, "converged": None}
        opt_conf = _option_confidences(ep)
        if not opt_conf and all_operator_ids and not context_free:
            # #6671: the writes are idempotent now, so a re-run dirties nothing
            # and the incremental no-arg pass can return a "successful" pass
            # with NO posterior for THIS decision's options — the ranking would
            # silently vanish while the run still reports success (the
            # `[ep-ran]` fail-open shape). The graph may also carry another
            # writer's dirty roots whose posteriors are not ours, so the test
            # is "did OUR options rank", never "was the result non-empty".
            print("  (no posterior for this decision's options — ranking this "
                  "decision's operator factors)")
            ep = sdk.compute_confidence(factors=all_operator_ids)
            opt_conf = _option_confidences(ep)

        if opt_conf:
            print(f"\n✓ EP computed: {ep['iterations']} iterations, "
                  f"converged={ep['converged']}")
            print("\n=== OPTION CONFIDENCE (higher = more supported) ===")
            ranked = sorted(opt_conf.items(), key=lambda kv: kv[1], reverse=True)
            name_width = max(len(pid) for pid in opt_conf)
            for pid, c in ranked:
                bar = "█" * int(c * 20) + "░" * (20 - int(c * 20))
                print(f"  {pid:<{name_width}}  {c:.4f}  {bar}")
        else:
            # A pass over zero posteriors is not a ranking, and must not be
            # reported as converged.
            print(f"\n⚠ EP produced NO posterior for this decision's options "
                  f"({ep['iterations']} iterations) — the ranking is EMPTY, "
                  "not converged")
        result.update(confidences=opt_conf, iterations=ep["iterations"],
                      converged=ep["converged"])
    except Exception as e:
        print(f"\n⚠ compute_confidence: {e}")

    # ── Verify structure ──
    try:
        struct = sdk.check_structure()
        ctx_issues = [i for i in struct
                      if ctx in str(i.get("id", "")) or ctx in str(i.get("message", ""))]
        print(f"\n✓ Structure check: {len(struct)} issues total, "
              f"{len(ctx_issues)} in context '{ctx}'")
        result["structure_issues"] = len(ctx_issues)
    except Exception as e:
        print(f"⚠ check_structure: {e}")
    return result


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
    if not data.get("options"):
        print("Error: at least one option required", file=sys.stderr)
        sys.exit(1)

    # Connect to graph
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tortoise.sdk import TortoiseSDK  # noqa: I001
    from tortoise.projection import FalkorProjection

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/tortoise")
    # Pass the resolved URI through the env so TortoiseSDK() never constructs
    # the DEFAULT embedded store (resolve_db_path) — otherwise the constructor's
    # cross-process busy probe throws EmbeddedStoreBusyError whenever another
    # process already holds ~/.tortoise/tortoise.db (order-dependent CI flake in
    # the decide smoke test; the script only uses the URI projection below).
    if args.db:
        os.environ["TORTOISE_DB_URI"] = uri
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(uri)

    try:
        file_decision(sdk, data, context_free=args.context_free)
    finally:
        if sdk._proj:
            sdk._proj.close()

    print(f"\nDone. Decision comparison filed to context='{ctx}'")


if __name__ == "__main__":
    main()
