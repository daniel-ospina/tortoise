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
                    any_label: bool = False) -> list[dict]:
    """Existing operators on this bridge, as dicts with the facts EP needs.

    Each row is ``{"id", "status", "outdated", "label", "targets"}``.

    Returns ALL matches, not just the first: a graph already polluted by an
    earlier run of this script has duplicate copies of every edge, and a
    relevance edge must damp every copy it finds — damping one leaves the other
    at full weight, which is the asymmetry #6671 measured.

    ``any_label=True`` drops the label arm of the identity (used to ask "what
    sits on this triple at all?", which the relevance arm needs before deciding
    what it may damp).

    ``status``, the legacy ``outdated`` flag and the Layer-3 ``label`` come back
    because the caller must know three things it cannot assume: whether EP reads
    the bridge (`tortoise.live._live_only` = not draft AND not
    terminal(status, outdated)), which claim it is (two labels over one triple
    are two claims), and whether the operator IS this bridge (``targets`` > 1
    means it also claims other pairs, so reusing it would widen this decision).
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
        "RETURN op.id, op.status, coalesce(op.outdated, false), "
        "coalesce(op.label, '')",
        params={"sid": source_id, "tid": target_id, "op": op_type,
                "label": label},
    ).result_set
    if not rows:
        return []
    counts = dict(proj.g.query(
        "MATCH (op:Point) WHERE op.id IN $ids MATCH (op)-[r]->() "
        "WHERE r.idx > 0 RETURN op.id, count(r)",
        params={"ids": [row[0] for row in rows]},
    ).result_set)
    return [{"id": row[0], "status": row[1] or "live",
             "outdated": bool(row[2]), "label": row[3] or "",
             "targets": int(counts.get(row[0], 0))} for row in rows]


def _is_ranked(row: dict) -> bool:
    """EP's own rule (`tortoise.live._live_only`): not draft, not terminal."""
    from tortoise.live import is_draft_status, is_terminal_status

    return (not is_draft_status(row["status"])
            and not is_terminal_status(row["status"], row["outdated"]))


def _status_note(row: dict) -> str | None:
    """A TRUE one-line note about why this bridge is (or is not) ranked."""
    from tortoise.live import is_draft_status, is_terminal_status

    if row["outdated"]:
        # `outdated=true` is server-managed (`invalidate_point`), and
        # `update_point` refuses to clear it — so no promotion remedy is
        # offered here, whatever the status says (#6699/#2422).
        return (f"elected operator {row['id']}, outdated=true "
                f"(status={row['status']}) — the legacy dead marker; EP "
                "excludes it and update_point cannot clear it, so this bridge "
                "stays out of the ranking; it is NOT duplicated (#6671)")
    if is_draft_status(row["status"]):
        return (f"elected operator {row['id']}, status=draft — EP excludes "
                "draft operators (#780), so this bridge does not participate in "
                "the ranking until it is promoted "
                "(update_point(id, status='live')); it is NOT duplicated "
                "(#6671)")
    if is_terminal_status(row["status"], row["outdated"]):
        return (f"elected operator {row['id']}, status={row['status']} — a "
                "TERMINAL status; EP excludes it (#2422) and update_point "
                "cannot resurrect it, so this bridge stays out of the ranking; "
                "it is NOT duplicated (#6671)")
    return None


def _resolve_or_create_operator(sdk, proj, op_type: str, source_id: str,
                                target_id: str, label: str | None,
                                cache: dict[tuple[str, str, str, str], str],
                                *, any_label: bool = False
                                ) -> tuple[str, bool, str | None]:
    """Write the edge idempotently — ONE operator per single-pair bridge.

    Returns ``(operator_id, reused, note)``; ``note`` is a warning to print.

    Idempotence is by the graph's own operator identity, and it is bounded by
    TWO facts read from the graph, not assumed:

    * a copy EP does not read (draft / terminal status / legacy
      ``outdated=true``) is never preferred over one it does — and it is never
      "fixed" by filing a live twin, which is the decision recorded on #6671's
      scoping ("Creating a second operator instead would reintroduce the exact
      duplication this issue is about"). A twin is not cosmetic:
      ``update_point(draft_id, status="live")`` is a legitimate call, and after
      a twin exists it leaves TWO live operators on one bridge (measured), which
      EP reads twice — with no edge-delete API.
    * an operator with ``targets > 1`` is NOT this bridge: it also claims other
      pairs, so reusing it would silently widen the decision's claim (and a
      relevance edge on the pair would damp the other pairs through it). A
      single-pair bridge is written instead, which the next run then reuses.
    """
    key = (source_id, op_type, target_id, "" if any_label else (label or ""))
    if key in cache:
        return cache[key], True, None
    matches = _find_operators(proj, op_type, source_id, target_id, label,
                              any_label=any_label)
    single = [row for row in matches if row["targets"] == 1]
    if single:
        # Prefer the copy EP reads; on a polluted bridge the copies are
        # identical, so storage order is arbitrary.
        elected = ([row for row in single if _is_ranked(row)] or single)[0]
        cache[key] = elected["id"]
        return elected["id"], True, _status_note(elected)
    note = None
    if matches:
        multi = ", ".join(f"{row['id']} ({row['targets']} targets)"
                          for row in matches)
        note = (f"existing operator(s) {multi} on this triple also claim other "
                "pairs — not reused (it would widen this decision's claim); a "
                "single-pair bridge is created")
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

    def _elect(src_key: str, op_type: str, tgt_key: str, label: str | None,
               *, any_label: bool = False) -> tuple[str | None, bool, str | None]:
        """Resolve-or-create the bridge and record it. Never creates a twin."""
        src, tgt = _resolve(src_key), _resolve(tgt_key)
        try:
            op_id, reused, note = _resolve_or_create_operator(
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
        # A relevance edge is a mitigation, not a claim of its own, so it never
        # mints a parallel operator. When the caller states a label, the label
        # SCOPES the dampening (two labels over one triple are two claims —
        # damping a `refutes` bridge on behalf of a `supports` mitigation would
        # silently weaken a claim the caller did not name). Without a label the
        # edge damps every bridge on the triple.
        # A relevance edge is a MITIGATION, never a claim: it never writes an
        # operator of its own. It damps the claim the caller named — the
        # labelled bridges when a label is given, the unlabelled ones
        # otherwise — and refuses to guess when the triple carries several
        # distinct labels and the edge names none (damping a `refutes` bridge
        # on behalf of an unlabelled mitigation would silently weaken a claim
        # the caller did not name).
        found = _find_operators(proj, op_type, _resolve(src), _resolve(tgt),
                                label, any_label=label is None)
        if not found:
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: no "
                  f"{label + ' ' if label else ''}bridge on this triple — "
                  "nothing damped (a relevance edge never mints a claim)")
            continue
        # #6671 review: a bridge with ``targets > 1`` also claims OTHER pairs,
        # so damping it here would weaken claims this edge never named — the
        # exact hazard ``_resolve_or_create_operator`` refuses to create. The
        # two arms must state ONE rule, so the exclusion is applied here too
        # instead of being asserted only in the ``edges`` arm's prose.
        shared = [row for row in found if row["targets"] != 1]
        if shared:
            named = ", ".join(
                f'{row["id"]} ({row["targets"]} targets)' for row in shared)
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {named} also "
                  "claim other pairs — not damped (it would weaken claims "
                  "this edge did not name)")
        found = [row for row in found if row["targets"] == 1]
        if not found:
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: every bridge "
                  "on this triple is shared with other pairs — nothing damped")
            continue
        if label is not None:
            targets = [row["id"] for row in found]
        elif any(row["label"] == "" for row in found):
            targets = [row["id"] for row in found if row["label"] == ""]
        elif len({row["label"] for row in found}) == 1:
            targets = [row["id"] for row in found]
        else:
            labels = sorted({row["label"] for row in found})
            print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: this triple "
                  f"carries {len(labels)} distinct labels {labels} and the "
                  "edge names none — nothing damped")
            continue
        # Record the damped bridges as this run's edges too (they are the
        # bridges the ranking reads).
        if targets[0] not in all_operator_ids:
            all_operator_ids.append(targets[0])
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
            # `tortoise/__main__.py::_cmd_decide` still does).
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
