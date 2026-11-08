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


def _error_of(res: Any) -> str | None:
    """The reason a tool payload is a REFUSAL, or None when it is data."""
    if isinstance(res, dict):
        for key in ("error", "detail", "violations"):
            val = res.get(key)
            if val:
                return str(val)
    return None


def _rows_from_tool(res: Any) -> list[dict[str, Any]]:
    res = C.normalise_tool_result(res)
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
            res = C.normalise_tool_result(self._q(offset=offset, limit=page))
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
        if not self.graph:
            return self._checked(self._call("tortoise_graph_overview", args), section)
        try:
            res = C.normalise_tool_result(self._call("tortoise_graph_overview", {**args, "graph": self.graph}))
        except Exception as exc:  # a refusal, not an empty section
            self.note = (f"credential-bound read for {section!r}: the explicit graph argument "
                         f"was refused ({str(exc)[:160]}); used the credential's own graph")
            return self._checked(self._call("tortoise_graph_overview", args), section)
        err = _error_of(res)
        if err:
            # An error envelope is truthy and does NOT raise; returning it as a
            # section would present a refusal as census data.
            self.note = (f"credential-bound read for {section!r}: the explicit graph argument "
                         f"was refused ({err[:160]}); used the credential's own graph")
            return self._checked(self._call("tortoise_graph_overview", args), section)
        # An EMPTY result is a legitimate answer (a graph with no sources), so it is
        # returned as-is rather than re-read from a different graph.
        return res

    @staticmethod
    def _checked(raw: Any, section: str) -> Any:
        """Unwrap, then FAIL CLOSED on an error envelope.

        A retry can refuse too, and an envelope falling through to `_rows_from_tool`
        reads as "0 rows" — a refusal presented as a census. Points fail closed for
        this reason; every section does.
        """
        res = C.normalise_tool_result(raw)
        err = _error_of(res)
        if err:
            raise RuntimeError(f"read of section {section!r} was refused: {err[:300]}")
        return res

    def read_sources(self) -> tuple[list[C.Row], int | None]:
        """Sources, plus the total the surface reported (None when it reports none).

        The surface's source listing matches `documentKind IS NULL`, so it is a
        SUBSET of `counts.Source`; the caller reconciles against that and names the
        gap (NOT_ENUMERABLE["source"]) rather than reporting a complete census. A
        row SECTION carries no `total` key, so None is the honest answer here — the
        previous code returned `len(out)`, which could never disagree with
        `sources_read` and so reconciled nothing.
        """
        res = self._section("sources")
        raw = _rows_from_tool(res)
        total = None
        if isinstance(res, dict):
            for key in ("total", "count", "totalSources"):
                if isinstance(res.get(key), int):
                    total = res[key]
                    break
        out = []
        for s in raw:
            url = str(s.get("url") or "")
            out.append(C.Row(
                node_id=url, node_type="source", kind=s.get("sourceKind"),
                content=url, content_hash=None, props=s,
            ))
        return out, total

    def counts(self) -> dict[str, Any]:
        res = self._section("status")
        return res if isinstance(res, dict) else {}

    def structure(self) -> dict[str, Any]:
        res = self._section("structure")
        return res if isinstance(res, dict) else {}

    def graphs(self) -> list[dict[str, Any]]:
        """The workspace's graph identities, in whatever shape this server offers.

        The tool set exposes NAMESPACES (``org_…_g_<id>``) here, and a namespace
        encodes its graph id — enough to tell two graphs apart. A graph NAME
        (``live-v4``) is not resolvable from this surface at all, which is why an
        unresolvable ref is a refusal in the guard rather than a pass.
        """
        out: list[dict[str, Any]] = []
        for row in _rows_from_tool(self._section("graphs")):
            if isinstance(row, dict):
                out.append(dict(row))
            elif isinstance(row, str) and row:
                out.append({"namespace": row})
        return out

    def audit(self) -> dict[str, Any]:
        res = self._section("audit")
        return res if isinstance(res, dict) else {}


def _live_call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    from tortoise import mcp_client

    return mcp_client.call_tool(name, args or {})


def _source_gap(counts: Any, read: int) -> int | None:
    """`counts.Source` minus what this read path enumerated (None if unreported).

    Non-zero is EXPECTED: `sdk.list_sources` matches `documentKind IS NULL`, so a
    Document-kind Source is never listed. Reported rather than explained away.
    """
    if not isinstance(counts, dict):
        return None
    node_counts = counts.get("counts") if isinstance(counts.get("counts"), dict) else counts
    total = node_counts.get("Source") if isinstance(node_counts, dict) else None
    return (total - read) if isinstance(total, int) else None


NOT_ENUMERABLE = {
    "operator": "tortoise_query excludes operator Points unless an operator kind is "
                "requested (sdk._operator_scope_clauses, #7108) — count from structure.operators",
    "object": "no listing surface on the MCP tool set — count from counts.Object",
    "event": "no listing surface on the MCP tool set — count from counts.Event",
    "subject": "no listing surface on the MCP tool set — count from counts.Subject",
    "source": "tortoise_list_sources matches `documentKind IS NULL` (sdk.list_sources), "
              "so a Document-kind Source is NOT listed — reconcile sources_read against "
              "counts.Source rather than reading the source count as complete",
}


def cmd_inventory(args: argparse.Namespace) -> int:
    reader = MCPReader(_live_call_tool, args.graph)
    counts = reader.counts()
    structure = reader.structure()
    graphs = reader.graphs()
    points, total = reader.read_points(page=args.page)
    sources, source_total = reader.read_sources()
    audit = reader.audit()
    plan = C.build_plan(points + sources, source_graph=args.graph or "default",
                        target_graph=args.target or "(none: inventory only)")
    report = {
        "graph": args.graph or "default",
        "node_counts": counts.get("counts") if isinstance(counts.get("counts"), dict) else counts,
        "points_read": len(points),
        "points_total_reported": total,
        "sources_read": len(sources),
        "sources_total_reported": source_total,
        "source_count_gap": _source_gap(counts, len(sources)),
        "structure": structure,
        "graphs": graphs,
        "not_enumerable_by_this_read_path": dict(NOT_ENUMERABLE),
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
    if args.apply and not args.graph:
        # A write must name the graph that was READ. Omitting --graph reads the
        # credential's own graph, and fabricating a name for it would have the
        # identity guard compare a graph nobody read.
        print("plan --apply: --graph <source graph id or namespace> is required — the source "
              "identity must be the graph that was actually read", file=sys.stderr)
        return 2
    reader = MCPReader(_live_call_tool, args.graph)
    counts = reader.counts()
    points, _ = reader.read_points(page=args.page)
    sources, source_total = reader.read_sources()
    graphs = reader.graphs()
    plan = C.build_plan(points + sources, source_graph=args.graph or "default", target_graph=args.target)
    try:
        result = C.run_plan(
            plan,
            writer=C.IngestWriter(_live_call_tool),
            ledger=C.Ledger(
                args.ledger or os.path.expanduser("~/.tortoise/curate-11469-ledger.jsonl"),
                target=args.target,
            ),
            apply=args.apply,
            off=args.off,
            graph_rows=graphs,
        )
    except C.CurationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    payload = {"census": plan.census(), "run": result.as_dict(),
               "sources_read": len(sources), "sources_total_reported": source_total,
               "source_count_gap": _source_gap(counts, len(sources)),
               # The plan is the artifact a driver writes; a census without the
               # classes this read path cannot enumerate reads as complete.
               "not_enumerable_by_this_read_path": dict(NOT_ENUMERABLE),
               # A credential-bound fallback read means the rows came from the
               # credential's own graph, not the one asked for — never silent.
               "read_note": reader.note}
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    if result.refused and not result.applied and args.apply:
        # A refused WRITE (or a refused identity) is a failure, not a successful
        # plan: a scripted driver must not read exit 0 as "migrated".
        print(f"REFUSED: {result.refused}", file=sys.stderr)
        return 3
    if result.applied and result.stopped:
        print(f"STOPPED mid-run: {result.stopped} (accepted={result.written})", file=sys.stderr)
        return 3
    if result.applied and result.refused:
        print(f"REFUSED mid-run: {result.refused} (accepted={result.written})", file=sys.stderr)
        return 3
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
