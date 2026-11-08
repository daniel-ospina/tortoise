#!/usr/bin/env python3
"""Curation pass for our own (dogfood) memory — `#11469`.

    uv run python tools/curate_graph.py inventory --out /tmp/inventory.json
    uv run python tools/curate_graph.py plan --target <graph> --out /tmp/plan.json
    uv run python tools/curate_graph.py plan --target <graph> --apply        # writes

Reads the OLD graph through the MCP surface it is bound to and never writes to
it; `--apply` is refused until a distinct target graph exists (the old graph is
at its node cap and is the only copy of that tenant's data — see #11469,
#10175 and the ruling recorded there).

Env:
    TORTOISE_MCP_URL / TORTOISE_MCP_API_KEY   the MCP endpoint + credential
    TORTOISE_CURATE_OFF=1                     disarm every write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import curation as C

PAGE = 500


def _normalise(res: Any) -> Any:
    """Turn an MCP tool result into plain JSON.

    The client hands back a ``CallToolResult``; reading only ``dict``/``list``
    made an ERROR look like an empty graph (0 rows, 0 census) — the silent-zero
    shape this repo refuses. So: unwrap, and treat ``isError`` as a failure.
    """
    if isinstance(res, (dict, list)):
        return res
    is_err = getattr(res, "isError", None)
    sc = getattr(res, "structuredContent", None)
    if sc:
        payload: Any = sc.get("result", sc) if isinstance(sc, dict) else sc
    else:
        parts = getattr(res, "content", None) or []
        text = "".join(getattr(p, "text", "") for p in parts)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
    if is_err:
        raise RuntimeError(f"MCP tool error: {str(payload)[:400]}")
    return payload


def _rows_from_tool(res: Any) -> list[dict[str, Any]]:
    res = _normalise(res)
    if isinstance(res, dict):
        for k in ("results", "result", "points"):
            v = res.get(k)
            if isinstance(v, list):
                return v
    return res if isinstance(res, list) else []


class MCPReader:
    """Paginated reads of one graph, read-only by construction (no write verb)."""

    def __init__(self, call_tool, graph: str | None = None):
        self._call = call_tool
        self.graph = graph
        self.note: str | None = None

    def _q(self, **args: Any) -> dict[str, Any]:
        if self.graph:
            args["graph"] = self.graph
        return self._call("tortoise_query", args)

    def read_points(self, *, page: int = PAGE) -> tuple[list[C.Row], int]:
        rows: list[C.Row] = []
        offset = 0
        total = None
        while True:
            res = _normalise(self._q(offset=offset, limit=page))
            batch = _rows_from_tool(res)
            if isinstance(res, dict) and res.get("total") is not None:
                total = int(res["total"])
            if not batch:
                break
            for r in batch:
                content = str(r.get("content") or "")
                rows.append(C.Row(
                    node_id=str(r.get("id") or ""),
                    node_type="operator" if r.get("is_operator") else "point",
                    kind=r.get("kind") or r.get("pointKind"),
                    content=content,
                    content_hash=r.get("content_hash"),
                    props={k: v for k, v in r.items() if k not in ("embedding",)},
                ))
            offset += len(batch)
            if total is not None and offset >= total:
                break
            if len(batch) < page:
                break
        if not rows:
            # An empty read is a FAILURE, not an empty graph: the source is known
            # to hold thousands of nodes, so 0 rows means the read was refused or
            # mis-targeted — and a silent zero would produce a clean-looking
            # census that claims there is nothing to migrate.
            raise RuntimeError(
                f"read returned 0 points (reported total={total}); refusing to report an "
                "empty graph — check the credential/graph binding"
            )
        if total is not None and len(rows) < total:
            raise RuntimeError(f"incomplete read: {len(rows)} of {total} points — refusing a partial census")
        return rows, (total if total is not None else len(rows))

    def _section(self, section: str) -> Any:
        """A graph_overview section, with the credential's binding made explicit.

        A graph-bound credential cannot be re-targeted by a `graph` argument; when
        it refuses, the retry is WITHOUT the argument (omission means "this
        credential's own graph" — a documented, legal read) and the fact that the
        read used the credential's binding is recorded on the report rather than
        silently absorbed.
        """
        args = {"section": section}
        if self.graph:
            try:
                res = _normalise(self._call("tortoise_graph_overview", {**args, "graph": self.graph}))
                if res:
                    return res
            except Exception:
                pass
            self.note = "credential-bound read: the explicit graph argument was refused; used the credential's own graph"
        return _normalise(self._call("tortoise_graph_overview", args))

    def read_sources(self) -> list[C.Row]:
        res = self._section("sources")
        out = []
        for s in _rows_from_tool(res):
            url = str(s.get("url") or "")
            out.append(C.Row(
                node_id=url, node_type="source", kind=s.get("sourceKind"),
                content=url, content_hash=None, props=s,
            ))
        return out

    def counts(self) -> dict[str, Any]:
        res = self._section("status")
        return res if isinstance(res, dict) else {}

    def structure(self) -> dict[str, Any]:
        res = self._section("structure")
        return res if isinstance(res, dict) else {}

    def audit(self) -> dict[str, Any]:
        res = self._section("audit")
        return res if isinstance(res, dict) else {}


def _live_call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    from tortoise import mcp_client

    return mcp_client.call_tool(name, args or {})


def cmd_inventory(args: argparse.Namespace) -> int:
    reader = MCPReader(_live_call_tool, args.graph)
    counts = reader.counts()
    structure = reader.structure()
    points, total = reader.read_points(page=args.page)
    sources = reader.read_sources()
    audit = reader.audit()
    plan = C.build_plan(points + sources, source_graph=args.graph or "default",
                        target_graph=args.target or "(none: inventory only)")
    report = {
        "graph": args.graph or "default",
        "node_counts": counts.get("counts") if isinstance(counts.get("counts"), dict) else counts,
        "points_read": len(points),
        "points_total_reported": total,
        "sources_read": len(sources),
        "structure": structure,
        "read_note": reader.note,
        "audit": {
            "node_count": audit.get("node_count"),
            "edge_count": audit.get("edge_count"),
            "summary": audit.get("summary"),
            "checks": [
                {"id": c.get("id"), "severity": c.get("severity"), "count": c.get("count")}
                for c in (audit.get("checks") or [])
            ],
        },
        "census": plan.census(),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"inventory: {len(points)} points + {len(sources)} sources -> {args.out}")
        print(json.dumps(report["census"]["totals"], indent=1))
    else:
        print(text)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    if not args.target:
        print("plan: --target <graph> is required (the clean, pack-filed graph)", file=sys.stderr)
        return 2
    reader = MCPReader(_live_call_tool, args.graph)
    points, _ = reader.read_points(page=args.page)
    sources = reader.read_sources()
    plan = C.build_plan(points + sources, source_graph=args.graph or "default", target_graph=args.target)
    try:
        result = C.run_plan(
            plan,
            writer=C.IngestWriter(_live_call_tool),
            ledger=C.Ledger(args.ledger or os.path.expanduser("~/.tortoise/curate-11469-ledger.jsonl")),
            apply=args.apply,
            off=args.off,
        )
    except C.CurationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    payload = {"census": plan.census(), "run": result.as_dict()}
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Curation pass for our own memory (#11469)")
    ap.add_argument("action", choices=["inventory", "plan"])
    ap.add_argument("--graph", help="source graph (default: the credential's own graph)")
    ap.add_argument("--target", help="target graph — must differ from the source")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--ledger", help="ledger path (default ~/.tortoise/curate-11469-ledger.jsonl)")
    ap.add_argument("--page", type=int, default=PAGE)
    ap.add_argument("--apply", action="store_true", help="actually write (default: plan only)")
    ap.add_argument("--off", action="store_true", help="off-switch: write nothing")
    args = ap.parse_args(argv)
    return cmd_inventory(args) if args.action == "inventory" else cmd_plan(args)


if __name__ == "__main__":
    raise SystemExit(main())
