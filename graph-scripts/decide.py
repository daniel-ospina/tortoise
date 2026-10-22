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
# #6671 — IDEMPOTENT OPERATOR WIRING
#
# `create_point` below is written with `dedup=True` (content hash), so the
# POINTS converge across runs. `create_operator` is NOT idempotent — it mints a
# fresh operator Point on every call — and this script used to key its
# `created_ops` cache per run only, so a second run re-created the whole edge
# set. Because `mitigate_operator` is idempotent PER OPERATOR, the two runs'
# mitigations landed on different copies: run-1 edges damped, run-2 edges at
# full weight, both read by EP. Measured on #6671 with a 2-edge decision:
# 4 operators after two runs, option confidence 0.5736 → 0.5184 (14 → 19 EP
# iterations). There is NO edge-delete API, so that pollution is irreversible.
#
# The identity used here is the graph's own operator shape, not a second model:
# `create_operator` wires `(source)-[:INPUT {idx:0}]->(op)` and
# `(op)-[op_type|hasPart {idx:n>0}]->(target)`, for ANY accepted op_type
# (IMPL/NAND and the part/whole family, whose bridge is mirrored as `hasPart`).
# `label` is part of the identity — two edges with different Layer-3 predicates
# over the same triple are two claims. The endpoint labels are deliberately NOT
# in the identity: the write door admits :Point/:Subject/:Object/:Event
# endpoints (#7902), so anchoring on `:Point` would miss a Subject/Object
# endpoint and duplicate the very edge this guards.
# ─────────────────────────────────────────────────────────────────────────────


def _find_operators(proj, op_type: str, source_id: str, target_id: str,
                    label: str | None, *,
                    any_label: bool = False) -> list[tuple[str, str]]:
    """Existing ``(operator_id, status)`` pairs on this bridge.

    Returns ALL matches, not just the first: a graph already polluted by an
    earlier run of this script has duplicate copies of every edge, and a
    relevance edge must damp every copy it finds — damping one leaves the other
    at full weight, which is the asymmetry #6671 measured.

    ``any_label=True`` drops the label arm of the identity. That is the
    RELEVANCE-edge posture: a mitigation says "this criterion matters less for
    that option", so it damps every bridge the caller names by
    (source, op_type, target) — including a labeled one the same decision filed
    without repeating the label — instead of minting a parallel unlabeled
    operator to damp.

    The status is returned because a reused operator may be `draft`, and EP
    excludes draft operators (#780) — reusing one silently would drop the
    decision's own edge from the ranking (see `_resolve_or_create_operator`).
    A status-less operator reads as `live`, the graph's own default.
    """
    label_arm = "" if any_label else \
        "AND coalesce(op.label, '') = coalesce($label, '') "
    rows = proj.g.query(
        # Label-free endpoints: the write door admits :Point/:Subject/:Object/
        # :Event endpoints (#7902), so a `:Point` anchor would miss a
        # Subject/Object endpoint and duplicate the edge. The op node keeps
        # `:Point` (an operator IS a Point, is_operator:true).
        "MATCH (s {id:$sid})-[ri:INPUT]->(op:Point {is_operator:true})"
        "-[ro]->(t {id:$tid}) "
        "WHERE op.op_type = $op AND ri.idx = 0 AND ro.idx > 0 "
        + label_arm +
        "RETURN op.id, op.status",
        params={"sid": source_id, "tid": target_id, "op": op_type,
                "label": label},
    ).result_set
    return [(row[0], row[1] or "live") for row in rows]


def _resolve_or_create_operator(sdk, proj, op_type: str, source_id: str,
                                target_id: str, label: str | None,
                                cache: dict[tuple[str, str, str, str], str],
                                *, any_label: bool = False
                                ) -> tuple[str, bool, str | None]:
    """Write the edge idempotently.

    Returns ``(operator_id, reused, note)`` — ``note`` is a warning to print
    (or None). A match is elected only if it is LIVE: EP excludes draft
    operators (#780), so adopting a draft would silently drop this decision's
    edge from the ranking. When only draft copies exist the write creates the
    live operator the decision needs and says so; the next run then reuses it,
    so the run count still converges (#6671).
    """
    key = (source_id, op_type, target_id, "" if any_label else (label or ""))
    if key in cache:
        return cache[key], True, None
    existing = _find_operators(proj, op_type, source_id, target_id, label,
                               any_label=any_label)
    live = [op_id for op_id, status in existing if status == "live"]
    if live:
        cache[key] = live[0]
        return live[0], True, None
    note = None
    if existing:
        # Report the REAL statuses: a non-live operator is not necessarily a
        # draft — `retracted` is a stored status too (`POINT_STATUS_VALUES`), and
        # calling a retracted claim a "draft" would hide that the run just
        # re-asserted it at full EP weight with a live twin.
        named = ", ".join(f"{op_id} ({status})" for op_id, status in existing)
        note = (f"only NON-LIVE operator(s) {named} on this bridge — EP excludes "
                "draft operators (#780) and this decision needs a live bridge, "
                "so a LIVE operator is created; the non-live copy stays and does "
                "not participate")
    op = sdk.create_operator(op_type, source_id, [target_id], label=label)
    cache[key] = op["id"]
    return op["id"], False, note


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

    def _wire(src_key: str, op_type: str, tgt_key: str, label: str | None,
              tick: str, *, any_label: bool = False) -> str | None:
        src, tgt = _resolve(src_key), _resolve(tgt_key)
        try:
            op_id, reused, note = _resolve_or_create_operator(
                sdk, proj, op_type, src, tgt, label, created_ops,
                any_label=any_label)
        except Exception as e:
            print(f"  ⚠ {src_key} --{op_type}--> {tgt_key}: {e}")
            return None
        if op_id not in all_operator_ids:
            all_operator_ids.append(op_id)
        operator_rows.append({
            "source": src_key, "op_type": op_type, "target": tgt_key,
            "label": label, "id": op_id, "reused": reused,
        })
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
        # any_label: a relevance edge damps the bridge the caller names; it is
        # not a claim of its own, so it never mints a parallel operator just
        # because the decision did not repeat the Layer-3 label here.
        op_id = _wire(src, op_type, tgt, label, "⚖ relevance:",
                      any_label=True)
        if op_id is None:
            continue
        try:
            # Damp EVERY operator on this bridge (normally one). A graph
            # polluted by an earlier non-idempotent run carries duplicate
            # copies; mitigating only the first would leave the others at full
            # weight — the exact asymmetry #6671 measured.
            targets = [found_id for found_id, _st in _find_operators(
                proj, op_type, _resolve(src), _resolve(tgt), label,
                any_label=True)] or [op_id]
            for target_id in targets:
                sdk.mitigate_operator(target_id, reason, strength)
            extra = "" if len(targets) == 1 else f" on {len(targets)} operators"
            print(f"     (mitigated {strength:.2f}{extra}: {reason})")
        except Exception as e:
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {e}")

    # ── Compute confidence per option ──
    result: dict = {"context": ctx, "operators": operator_rows,
                    "confidences": {}, "iterations": 0, "converged": None}
    try:
        if context_free and all_operator_ids:
            print(f"  (context-free mode: {len(all_operator_ids)} operator factors)")
            ep = sdk.compute_confidence(factors=all_operator_ids)
        else:
            ep = sdk.compute_confidence()
        print(f"\n✓ EP computed: {ep['iterations']} iterations, "
              f"converged={ep['converged']}")
        confs = ep.get("confidences", {})

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
