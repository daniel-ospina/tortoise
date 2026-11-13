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

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.12-only import would fail first, and the failure would not say what to do.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"curate-graph: requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/curate_graph.py`",
        file=sys.stderr,
    )
    raise SystemExit(3)  # usage, not a data error

import argparse
import json
import os
from collections.abc import Mapping
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
        for k in _ROW_KEYS:
            v = res.get(k)
            if isinstance(v, list):
                return v
    return res if isinstance(res, list) else []


#: The keys a row LISTING may arrive under. `_rows_from_tool` reads only these, so a
#: payload carrying none of them answered in a shape this reader does not understand.
_ROW_KEYS = ("results", "result", "points")


def _is_row_listing(res: Any) -> bool:
    """Did this payload arrive in a shape that can be READ as a row listing?

    The difference that matters is `[]` vs `unreadable`: an empty list (or an empty
    `result` key, or an explicit `total: 0`) is a graph with no rows, while a dict
    whose keys the reader does not recognise is a surface that drifted. Both go to
    `[]` through `_rows_from_tool`, and only this predicate can tell them apart —
    which is what makes "no silent zero" enforceable for the source section, not
    just for points.
    """
    res = C.normalise_tool_result(res)
    if isinstance(res, list):
        return True
    if not isinstance(res, dict):
        return False
    if any(isinstance(res.get(k), list) for k in _ROW_KEYS):
        return True
    # A COUNT-ONLY payload is readable only when it says ZERO. `{"total": 1578}` with
    # no rows means the LISTING did not arrive, and accepting it reported
    # `sources_read: 0` with exit 0 and only a gap to notice — the shape this guard
    # exists for (round-37 review, P3-3).
    counts = [res.get(k) for k in ("total", "count", "totalSources")]
    return any(isinstance(c, int) and not isinstance(c, bool) and c == 0 for c in counts)


class MCPReader:
    """Paginated reads of one graph, read-only by construction (no write verb)."""

    def __init__(self, call_tool, graph: str | None = None):
        self._call = call_tool
        self.graph = graph
        self.note: str | None = None
        self.notes: list[str] = []

    def _q(self, **args: Any) -> dict[str, Any]:
        if self.graph:
            args["graph"] = self.graph
        return self._call("tortoise_query", args)

    def _fingerprint_walk(self, *, page: int = PAGE) -> list[str]:
        """The id+content sequence of one full walk — used to VERIFY a census.

        Deliberately minimal (no Row construction, no guards): its job is to be the
        cheapest possible second opinion on "did the population hold still".
        """
        out: list[str] = []
        offset = 0
        while True:
            res = C.normalise_tool_result(self._q(offset=offset, limit=page))
            err = _error_of(res)
            if err:
                raise RuntimeError(f"verification pass was refused: {err[:300]}")
            batch = _rows_from_tool(res)
            if not batch:
                break
            out.extend(f"{r.get('id') or ''}\x00{r.get('content') or ''}" for r in batch)
            offset += len(batch)
            if len(batch) < page:
                break
        return out

    def read_points(self, *, page: int = PAGE, verify: bool = True) -> tuple[list[C.Row], int]:
        rows: list[C.Row] = []
        offset = 0
        total = None
        # id -> (first offset, content fingerprint). Point ids are NOT unique (colliding
        # ids are a tested, legal shape), so an id alone cannot tell "the same node
        # served twice" from "two nodes sharing an id" — the CONTENT can.
        first_page_fp: tuple[str, ...] | None = None
        last_page_fp: tuple[str, ...] | None = None
        last_offsets: tuple[int, int] | None = None
        seen: dict[str, tuple[int, str]] = {}
        totals: set[int] = set()
        while True:
            res = C.normalise_tool_result(self._q(offset=offset, limit=page))
            # A refusal on this surface is a `_safe`-answered payload with
            # `is_error=False`, so `normalise_tool_result` does NOT raise for it and it
            # would read as "0 rows". It is TERMINAL wherever it lands: on a LATER page
            # it used to reach `if not batch: break` with rows already collected, and
            # when the surface reports no `total` the short walk was then returned as a
            # complete census — the shortfall guard has no total to compare against and
            # the page re-check re-reads the last SUCCESSFUL page, so neither can see it.
            # That is the silent partial census R7 forbids (round-34 finding 1).
            page_refusal = _error_of(res)
            if page_refusal:
                raise RuntimeError(
                    f"read refused at offset {offset} after {len(rows)} rows: "
                    f"{page_refusal[:300]} — refusing a partial census")
            batch = _rows_from_tool(res)
            if isinstance(res, dict) and res.get("total") is not None:
                total = int(res["total"])
                totals.add(total)
            if len(totals) > 1:
                # An offset walk over a population that CHANGED between pages does
                # not describe one graph state: a row retracted mid-walk shifts every
                # later row up, so one row is skipped and a tombstone is re-read, and
                # the counts still match.
                raise RuntimeError(
                    f"pagination shifted mid-walk (reported total changed {sorted(totals)}) — "
                    "refusing a census that mixes two graph states")
            if not batch:
                break
            for r in batch:
                rid = str(r.get("id") or "")
                content = str(r.get("content") or "")
                fingerprint = f"{r.get('kind') or r.get('pointKind') or ''}\x00{content}"
                prior = seen.get(rid)
                if prior is not None:
                    first_offset, first_fp = prior
                    if first_offset != offset and first_fp == fingerprint:
                        # The SAME node, with the SAME content, served by an EARLIER
                        # page: the walk repeated rather than advanced, so it also
                        # skipped one. Two DIFFERENT nodes that merely share an id are
                        # a legal shape (create_point(id=X) is not unique) and repeat
                        # the id with different content.
                        raise RuntimeError(
                            f"row {rid!r} returned in an earlier page (offset {first_offset}) with "
                            f"identical content and again at offset {offset} — the population "
                            "shifted mid-walk; refusing a census that both skips and repeats rows")
                else:
                    seen[rid] = (offset, fingerprint)
                rows.append(C.Row(
                    node_id=str(r.get("id") or ""),
                    node_type="operator" if r.get("is_operator") else "point",
                    kind=r.get("kind") or r.get("pointKind"),
                    content=content,
                    content_hash=r.get("content_hash"),
                    props={k: v for k, v in r.items() if k not in ("embedding",)},
                ))
            fp = tuple(f"{r.get('id') or ''}\x00{r.get('content') or ''}" for r in batch)
            if first_page_fp is None:
                first_page_fp = fp
            last_page_fp, last_offsets = fp, (offset, len(batch))
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
            # `total` is not `structure.total`, and it does not count tombstones: the
            # paginated read answers it from `sdk.paginated_query`, which computes
            # `count(n)` with the SAME WHERE clause (same terminal-status exclusion,
            # #1391) as the page it serves. So a correct full walk cannot red here
            # because of retracted rows — the two numbers describe ONE population,
            # which is what makes a shortfall a failed read rather than a filter
            # disagreement between two surfaces.
            raise RuntimeError(f"incomplete read: {len(rows)} of {total} points — refusing a partial census")
        # A mid-walk mutation that keeps the TOTAL stable and repeats no row (one
        # retracted, one appended) is invisible to the guards above; the census then
        # contains a tombstone and omits a live row while looking complete. Re-reading
        # the LAST page at the end does not help (its snapshot is taken after the
        # churn), so a SECOND FULL WALK is the only sound check: it costs a second pass
        # (~2x calls) and is on by default, because a wrong census is worse than a slow
        # one. `--no-verify-read` opts out.
        if first_page_fp is not None:
            for label, want, args in (
                ("first", first_page_fp, {"offset": 0, "limit": page}),
                ("last", last_page_fp, {"offset": last_offsets[0], "limit": last_offsets[1]}),
            ):
                res2 = C.normalise_tool_result(self._q(**args))
                err2 = _error_of(res2)
                if err2:
                    raise RuntimeError(f"verification read of the {label} page was refused: "
                                       f"{err2[:300]}")
                got = tuple(f"{r.get('id') or ''}\x00{r.get('content') or ''}"
                            for r in _rows_from_tool(res2))
                if got != want:
                    raise RuntimeError(
                        f"the {label} page changed during the walk — the population was mutated "
                        "mid-read (a retraction and an addition of equal size look identical to "
                        "the totals); refusing a census that may hold a tombstone and omit a row")
        if verify:
            second = tuple(self._fingerprint_walk(page=page))
            # `rows` are C.Row dataclasses, not dicts — reading them with `.get`
            # raised AttributeError on EVERY successful walk, so the second pass
            # aborted every run instead of comparing anything.
            first = tuple(f"{r.node_id}\x00{r.content}" for r in rows)
            if second != first:
                raise RuntimeError(
                    "a second read of the same graph returned a DIFFERENT row sequence — the "
                    "population was mutated mid-walk (a retraction plus an addition leaves the "
                    "total unchanged and repeats no row, so only a full second pass can see it); "
                    "refusing a census that may hold a tombstone and omit a live row")
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
        except Exception as exc:
            # ONLY a recognised graph-refusal may fall back. A timeout/429 says
            # nothing about the binding, and re-reading a DIFFERENT graph under the
            # requested graph's identity is how a census mixes two graphs.
            reason = str(exc) or type(exc).__name__
            if not _looks_like_graph_refusal(reason):
                raise
            res, reason = None, reason
        else:
            reason = _error_of(res)
            if reason and not _looks_like_graph_refusal(reason):
                # An error envelope is the NORMAL delivery shape on this surface, so
                # the gate has to be here too: a quota/DB error must not re-read a
                # different graph and label the artifact with the requested one.
                raise RuntimeError(f"read of section {section!r} failed: {reason[:300]}")
        if reason:
            self._note(f"section {section!r}: the explicit graph argument was refused "
                       f"({reason[:160]}); read from the credential's own graph instead")
            return self._checked(self._call("tortoise_graph_overview", args), section)
        # An EMPTY result is a legitimate answer (a graph with no sources), so it is
        # returned as-is rather than re-read from a different graph.
        return res

    def _note(self, text: str) -> None:
        """Notes ACCUMULATE: a single slot kept only the last falling section, so a
        report could carry one fallback while another section silently used it."""
        if text not in self.notes:
            self.notes.append(text)
        self.note = "; ".join(self.notes)

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
        if not _is_row_listing(res):
            # R7's other half. `_section` fails closed on an error ENVELOPE, but a
            # refusal that arrives as an unrecognised DATA shape (`{"unexpected": "shape"}`)
            # carries no `error` key and unwrapped to `[]` — reported as a graph with no
            # sources, which on this tenant silently dropped 1,578 of 17,420 rows.
            raise RuntimeError(
                "read of section 'sources' answered in an unrecognised shape "
                f"({type(res).__name__} with keys "
                f"{sorted(res)[:6] if isinstance(res, dict) else 'n/a'}) — refusing to "
                "report an unreadable source listing as an empty one")
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

    def read_operators(
        self, structure: Mapping[str, Any] | None = None,
    ) -> tuple[list[C.Row], int | None]:
        """The reasoning layer — readable, and never read until this change.

        ``tortoise_search(entity_type="operator")`` is the ONLY bulk listing of operator
        Points on this surface. `tortoise_query` has no arm that reaches them: operator
        nodes carry ``op_type``/``label`` rather than ``pointKind``, so no kind filter
        enumerates them (``kind="operator"`` and ``kind="IMPL"`` both answer
        ``total: 0`` on live-v4), while the search path scopes its Cypher with
        ``n.is_operator = true`` and returns the layer. Measured 2026-11-13 on live-v4:
        **3,727 rows in one call**, exact match with ``structure.operators``.

        Two things the projection does NOT carry, and they decide the disposition:
        ``content`` is the operator's ``label`` ("label is searchable text"), and the
        endpoints are absent from the ENUMERATION — ``relationships`` is only decorated
        for ``entity_type="point"`` (``sdk.py``'s step 7.5: ``… if entity_type ==
        "point" else {}``), so it is empty here by construction rather than by
        measurement. The endpoints ARE readable one operator at a time
        (``tortoise_expand_relationships``), which is 3,727 calls this pass does not
        fan out. See ``classify_point``'s operator branch: a carried operator would be
        a floating node.

        The read is ROUTED like every other one: ``graph`` is the credential's
        per-call routing argument (``mcp_server._take_graph_argument``), and
        ``tortoise_search`` takes it, so omitting it would read the operator layer
        from the DEFAULT graph while every other section came from ``--graph`` — a
        census mixing two graphs, which is what the graph-refusal fallback below
        exists to make impossible.

        Fail-closed rules, each one an observed failure mode one node type over:

        * a row with no ``id`` is REFUSED — the class's identity key is its id (its text
          is a label, routinely empty), so two id-less rows are indistinguishable and
          would fold onto one;
        * a read that fills the requested ``limit`` exactly is REFUSED when no reported
          count says that is the whole layer — there is no offset walk here, so "a full
          page" and "a truncated layer" are the same bytes;
        * a read whose row count disagrees with ``structure.operators`` is REFUSED. The
          reconciliation is the same discipline the source listing gets, and for the same
          reason: two surfaces describing one population must agree, or one of them did
          not read it.

          ⚠️ They describe it with DIFFERENT predicates, so equality is a measurement,
          not a guarantee: ``structure.operators`` counts ``(n.is_operator = true OR
          n.op_type IS NOT NULL)`` with NO status filter (``sdk.summarize_structure``),
          while this read is the search path's structural leg — ``n.is_operator = true``
          PLUS the terminal-status/``outdated`` exclusion every Point scan applies
          (``search_engine``, #1391). So a legacy ``op_type``-only operator (a shape this
          repo backfills: ``graph-scripts/backfill_is_operator.py``) is counted and not
          served, and a terminal operator is counted and excluded — either makes a
          CORRECT read refuse. That direction is deliberate (a refusal is
          diagnosable; a silent short census is not), and the refusal message names both
          causes so the difference can be attributed instead of re-derived.

        An UNREPORTED count (a ``structure`` section with no ``operators`` key) does not
        refuse a NON-EMPTY read — the rows came from their own read, so it is a lost
        cross-check, not a lost population, and it is recorded on the report. A
        ZERO-ROW read with no count to reconcile it does refuse: "this graph has no
        operator layer" and "the layer was not read" are otherwise the same bytes,
        which is the silent-zero class `read_points` and `_unreconciled_sources`
        already fail closed on.
        """
        reported: int | None = None
        if isinstance(structure, Mapping):
            value = structure.get("operators")
            if isinstance(value, int) and not isinstance(value, bool):
                reported = value
        if reported is not None and reported > OPERATOR_READ_CEILING:
            raise C.CurationRefused(
                f"the operator layer holds {reported} rows, above the "
                f"{OPERATOR_READ_CEILING}-row single-call ceiling — that ceiling IS the "
                "surface's maximum `limit` (the SDK refuses a larger one), so there is no "
                "wider read to raise it to, only a `limit` and NO `offset`: no page walk, "
                "and a truncated layer entering the census is indistinguishable from a "
                "complete one; refusing rather than reporting a partial reasoning layer")
        res = self._search_operators()
        err = _error_of(res)
        if err:
            raise RuntimeError(f"read of the operator layer was refused: {err[:300]}")
        if not _is_row_listing(res):
            raise RuntimeError(
                "read of the operator layer answered in an unrecognised shape "
                f"({type(res).__name__} with keys "
                f"{sorted(res)[:6] if isinstance(res, dict) else 'n/a'}) — refusing to "
                "report an unreadable layer as an empty one")
        raw = _rows_from_tool(res)
        if reported is None and not raw:
            raise C.CurationRefused(
                "the operator read returned no rows AND the structure section reported no "
                "`operators` count, so an empty layer cannot be told from an unread one — "
                "refusing a census that would report the reasoning layer as empty")
        if len(raw) == OPERATOR_READ_CEILING and reported != OPERATOR_READ_CEILING:
            raise C.CurationRefused(
                f"the operator read returned exactly its {OPERATOR_READ_CEILING}-row limit and "
                f"no reported count says that is the whole layer (structure.operators "
                f"reported {reported!r}) — the response may be truncated, and `tortoise_search` "
                "has no offset walk to continue from; refusing a partial reasoning layer")
        if reported is not None and len(raw) != reported:
            raise C.CurationRefused(
                f"the operator layer read {len(raw)} rows but structure.operators reports "
                f"{reported} — refusing a layer two surfaces disagree about. The two "
                "count it with DIFFERENT predicates, so the difference is one of: a "
                "legacy `op_type`-only operator (`structure` counts `is_operator = true "
                "OR op_type IS NOT NULL`, this read serves `is_operator = true` only — "
                "`graph-scripts/backfill_is_operator.py` exists for that shape); a "
                "terminal/`outdated` operator (counted, excluded from the read by the "
                "#1391 status clause); a genuinely partial read; or the layer CHANGED "
                "between the `structure` count and this read (a concurrent create — "
                "`structure` is read immediately before this call to keep that window "
                "to two requests, but it is still two requests)")
        if reported is None:
            self._note("the structure section reported no `operators` count, so the operator "
                       "layer read is unreconciled (it is still the read's rows, not a count)")
        out: list[C.Row] = []
        for r in raw:
            if not isinstance(r, dict):
                # `_is_row_listing` accepts any list, so shape drift here would otherwise
                # surface as an AttributeError on `.get` (rc 1 traceback) instead of this
                # module's designed refusal — the same guard `graphs()` and
                # `read_sources` already apply to their rows.
                raise C.CurationRefused(
                    f"an operator row arrived as {type(r).__name__}, not an object — "
                    "refusing the layer rather than guessing which field carries its id")
            node_id = str(r.get("id") or "")
            if not node_id:
                raise C.CurationRefused(
                    "an operator row arrived with no `id` — the class is identified by its id "
                    "(its text is a label, routinely empty), so two id-less rows would fold "
                    "onto one; refusing the layer")
            out.append(C.Row(
                node_id=node_id,
                node_type="operator",
                # The search projection spells it `point_kind`; the structural readers
                # spell it `kind`/`pointKind`. Read all three rather than assume one.
                kind=r.get("point_kind") or r.get("kind") or r.get("pointKind"),
                content=str(r.get("content") or ""),
                content_hash=r.get("content_hash"),
                props={k: v for k, v in r.items() if k not in ("embedding",)},
            ))
        return out, reported

    def _search_operators(self) -> Any:
        """The operator enumeration, ROUTED to `self.graph` like every other read.

        ``graph`` is the credential's per-call routing argument (#9340,
        ``mcp_server._take_graph_argument``) and ``tortoise_search`` is not in
        ``GRAPH_ARGUMENT_EXCLUDED``, so it accepts and honours it. Omitting it was a
        real defect: on a workspace with two namespaces, every other section came from
        ``--graph`` while the operator layer came from the DEFAULT graph, and the
        reconciliation then compared one graph's count against another graph's rows —
        a confusing refusal on a correct run, and a silently mixed census when the two
        counts coincided.

        The fallback is `_section`'s, for `_section`'s reason: a graph-BOUND credential
        cannot be re-targeted, so a refusal ABOUT the graph argument is answered by
        re-reading WITHOUT it (omission means "this credential's own graph", a legal
        read) and recording the fact on the report. Any other refusal — a quota, a
        timeout, a DB fault — raises: re-reading a different graph under the requested
        graph's identity is how a census mixes two graphs.
        """
        args: dict[str, Any] = {"entity_type": "operator", "limit": OPERATOR_READ_CEILING}
        if not self.graph:
            return C.normalise_tool_result(self._call("tortoise_search", dict(args)))
        try:
            res = C.normalise_tool_result(self._call("tortoise_search", {**args, "graph": self.graph}))
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            if not _looks_like_graph_refusal(reason):
                raise
            self._note(f"the operator read: the explicit graph argument was refused "
                       f"({reason[:160]}); read from the credential's own graph instead")
            return C.normalise_tool_result(self._call("tortoise_search", dict(args)))
        reason = _error_of(res)
        if reason and _looks_like_graph_refusal(reason):
            self._note(f"the operator read: the explicit graph argument was refused "
                       f"({reason[:160]}); read from the credential's own graph instead")
            return C.normalise_tool_result(self._call("tortoise_search", dict(args)))
        return res

    def read_events(
        self, counts: Mapping[str, Any] | None = None,
    ) -> tuple[list[C.Row], int | None]:
        """The episodic layer — readable in one call, and never read until this change.

        `tortoise_get_entity(type="events", limit=N)` is the ADVERTISED path (the older
        `tortoise_get_events` name is retired at the server, #3883) and it forwards to
        `get_events`, which is `MATCH (e:Event) … ORDER BY e.startedAt DESC LIMIT $lim`.
        Measured on live-v4, 2026-11-13, read-only: **4,670 rows in one call**, exactly
        `counts.Event`. The harness recorded that layer as *"READABLE but NOT read by
        this pass"* (`NOT_ENUMERABLE["event"]`) — a scope decision it then reported as
        a complete census, which is the omission #12378 names.

        Reading it moves NO row into `carry`: `classify_event` answers `undecidable` on
        both of its arms (an agent-session container, whose home is #10000's open D1
        question; a domain Event, whose write path has no `events` section), so this
        buys per-kind census resolution and per-row reasons, nothing more.

        The read is ROUTED like every other one (`graph` is the credential's per-call
        routing argument and `tortoise_get_entity` is NOT in `GRAPH_ARGUMENT_EXCLUDED`),
        for the reason the operator read is: omitting it reads the DEFAULT graph while
        every other section came from `--graph`, and the reconciliation then compares
        one graph's count against another graph's rows.

        Fail-closed rules, each mirroring the operator read:

        * a row with neither `id` nor `eventId` is REFUSED — two id-less Events are
          indistinguishable and would fold onto one; `_upsert_event` writes `id` equal
          to the MERGE key `eventId`, and by-url/legacy rows carry the `eventId`
          spelling, so both are read;
        * a population AT OR ABOVE `EVENT_READ_CEILING` is REFUSED — there is no offset
          walk to page through it, AND FalkorDB silently caps any non-aggregate read at its
          server-global `RESULTSET_SIZE` (default 10 000 — measured on docker 4.20.4:
          10 050 Points read back as 10 000 rows, #4515/#4233/#5062), so a layer at the cap
          and a layer truncated BY the cap return the same bytes;
        * a read that FILLS the request is REFUSED, because there is no offset walk, so "a
          full page" and "a truncated layer" are the same bytes. The request is sized to
          `EVENT_READ_CEILING` — NOT to `EVENT_READ_CEILING + 1` and NOT to
          `counts.Event + 1`. The `+1` looks like extra safety and is the opposite: the
          engine's cap sits AT the bound, so a request of `bound + 1` can never be filled,
          a capped response always lands one row UNDER the request, and the fill refusal
          becomes an inert arm that can only fire on inputs no server can return. Sizing
          the request TO the bound makes a capped response (== the bound) fill it and
          refuse — and a response strictly below the bound is uncapped, hence exactly the
          layer;
        * a read whose row count disagrees with `counts.Event` is re-counted ONCE and then
          either ACCEPTED or REFUSED. Both surfaces describe `:Event` with the same
          predicate (unlike the operator pair), so equality was expected — but the count is
          read moments earlier, and `counts.Event` moves in BOTH directions on a live graph
          (`index_sessions` creates; the hosted entity-delete path deletes). Completeness
          rests on `len(raw) < limit_sent`, NOT on the count: a response below the request
          is the exact layer — because the engine cannot have capped it below the bound —
          so a disagreement whose fresh count equals the rows is ACCEPTED (the disagreement
          was a stale count) and anything else is REFUSED. The refusal still names
          `counts.Event` and what the re-read reported.

        ⛔ RESIDUAL, stated rather than hidden: on the UNREPORTED arm completeness rests
        only on the response having stayed below the bound. A server tuned with
        `RESULTSET_SIZE` BELOW `EVENT_READ_CEILING` would cap such a read invisibly, and
        nothing in this tool can see that tuning. Where a `counts.Event` exists the
        disagreement arm catches it (a capped response never equals a larger count), which
        is why the count is preferred and why `events_total_reported` is on the artifact.
        A deployment that tunes the cap below the bound must lower `EVENT_READ_CEILING`
        with it.

        An UNREPORTED count over a NON-EMPTY read is a lost cross-check, not a lost
        population: the rows came from their own read, so it is recorded on the report.
        A ZERO-ROW read with no count to reconcile it REFUSES — "this graph has no
        Events" and "the layer was not read" are otherwise the same bytes, which is the
        silent-zero class `read_points` and `_unreconciled_sources` already fail closed
        on.
        """
        reported = _node_count(counts, "Event")
        # ⚠️ KNOWN RESIDUAL, stated so the comment below is not read as claiming more than
        # the code delivers: this gate refuses WITHOUT reading, so a count that is
        # stale-HIGH across the bound refuses un-read even when the layer has since shrunk
        # BELOW the bound — where a read would have returned `len(raw) < limit_sent`, which
        # the arms below ACCEPT. Narrow (a graph sitting exactly AT the bound with a delete
        # inside the count->read window) and it clears on a re-run, so it is fail-CLOSED
        # and filed rather than fixed here.
        #
        # ⛔ Why it is not a one-line fix: moving the refusal after the read is only safe if
        # the fill check ALSO moves onto the EQUALITY path. The fill arm below lives INSIDE
        # the `len(raw) != reported` block, so `len(raw) == reported == EVENT_READ_CEILING`
        # reaches no fill check at all — measured, disabling THIS gate reds
        # `test_a_layer_at_the_bound_refuses_because_the_server_caps_there` and
        # `test_an_event_layer_above_the_ceiling_is_refused`, i.e. removing it alone is a
        # FAIL-OPEN. It is a two-part change with its own mutation proofs.
        if reported is not None and reported >= EVENT_READ_CEILING:
            raise C.CurationRefused(
                f"the Event layer holds {reported} rows, at or above this read's declared "
                f"{EVENT_READ_CEILING}-row RESPONSE BOUND. ⛔ TWO limits meet at this "
                "number: `sdk.get_events` validates no upper `limit` and passes it straight "
                "into the Cypher `LIMIT` (so the bound is OURS, a size/memory bound on ONE "
                "response), and FalkorDB silently truncates any non-aggregate read at its "
                "server-global `RESULTSET_SIZE`, which DEFAULTS TO 10 000 — the same "
                "number. A layer at the bound is therefore indistinguishable from a layer "
                "the server capped there, and there is a `limit` and NO `offset` "
                "(`MATCH (e:Event) … LIMIT $lim`), so it cannot be read whole at all. "
                "Raising `EVENT_READ_CEILING` alone does NOT fix this: it widens the gap "
                "between the request and the server's cap, which makes a capped response "
                "look MORE like a whole one. Raise the server's `RESULTSET_SIZE` with it "
                "and verify it (`config_get`), or add an offset walk; "
                "do not let a partial episodic layer be reported as complete")
        # ⛔ Ask for EXACTLY the DECLARED BOUND — never `bound + 1`, never `reported + 1`.
        # The response size is what decides whether a read is provably whole: `limit` goes
        # straight into the Cypher `LIMIT`, so a response SHORTER than the request is the
        # exact layer, and a response that FILLS the request is "at least this many" and
        # cannot be distinguished from a truncated one.
        #
        # Sizing the request to `reported + 1` (what this code did until the fourth review
        # cycle) gives exactly ONE row of headroom, so a layer that grew by even one row
        # FILLED the request — the very case the re-count below was meant to accept became
        # the truncation-capable case instead, and a grow-then-shrink race was accepted as
        # `events_reconciled: True` over unread rows (reproduced by the reviewer: 4 live
        # rows unread, 4 deleted rows reported).
        #
        # `bound + 1` was the fifth cycle's answer and it fails for a different reason: the
        # engine caps a non-aggregate read at `RESULTSET_SIZE`, DEFAULTS TO 10 000, i.e. AT
        # this bound. A request of 10 001 can then never be filled, so the fill refusal
        # below is inert — and a layer of 10 001 (one Event created after the count said
        # 10 000) comes back as 10 000 rows == the count and was ACCEPTED with a row
        # unread. Requesting the bound itself closes it: a capped response EQUALS the bound
        # and refuses; a response below the bound was not capped.
        #
        # ⛔ NOT `min(reported + 1, EVENT_READ_CEILING)` either: that clamp ate the headroom
        # exactly at the bound (a layer reporting 10,000 sent `limit=10,000`, so one that
        # had grown to 10,050 came back as 10,000 rows == the count and was filed
        # `events_reconciled: True` with 50 rows unread — the first reviewer round).
        # `reported` is already bounded by the refusal above, and `get_events` validates no
        # upper `limit`, so this is always a legal request.
        limit_sent = EVENT_READ_CEILING
        res = self._get_entities(limit_sent)
        err = _error_of(res)
        if err:
            raise RuntimeError(f"read of the Event layer was refused: {err[:300]}")
        if not _is_row_listing(res):
            raise RuntimeError(
                "read of the Event layer answered in an unrecognised shape "
                f"({type(res).__name__} with keys "
                f"{sorted(res)[:6] if isinstance(res, dict) else 'n/a'}) — refusing to "
                "report an unreadable layer as an empty one")
        raw = _rows_from_tool(res)
        if reported is None and not raw:
            raise C.CurationRefused(
                "the Event read returned no rows AND the status section reported no "
                "`Event` count, so an empty layer cannot be told from an unread one — "
                "refusing a census that would report the episodic layer as empty")
        if reported is None and len(raw) >= limit_sent:
            raise C.CurationRefused(
                f"the Event read returned {len(raw)} rows, filling the "
                f"{limit_sent}-row request, and "
                "no reported count says that "
                "is the whole layer — the response may be truncated, and this read has no "
                "offset walk to continue from; refusing a partial episodic layer")
        if reported is not None and len(raw) != reported:
            # ⛔ BOTH directions are re-counted, not only growth. A layer that GREW or
            # SHRANK between the count and this read is a CORRECT read being refused, and
            # both are reachable on a live graph: `counts.Event` is fed by
            # `index_sessions`, and `:Event` nodes are deleted by the hosted entity-delete
            # path. Equality with a fresh count accepts, in either direction.
            #
            # ⚠️ WHY ACCEPTING HERE IS SOUND. Completeness rests on ONE fact, and the
            # request is sized to establish it: `len(raw) < limit_sent`, i.e. the response
            # did not fill the request, so the engine cannot have capped it at
            # `RESULTSET_SIZE` (which defaults to the bound) and it is the exact layer.
            # The
            # re-count then only answers a different question: is the number this read
            # disagrees with simply stale? (A `:Event` created by `index_sessions` or
            # deleted by the hosted entity-delete path between the two requests.)
            #
            # ⛔ Do NOT weaken that to "the re-count proves the read is whole": it does not,
            # and an earlier revision said so. Equality plus a FILLED request is
            # satisfiable by a layer that grew past the request and then shrank back to
            # exactly its size before the re-read — the read truncated, the census filed
            # `events_reconciled: True` over unread rows (reproduced by the reviewer). The
            # arm below therefore refuses whenever the response filled the request, in
            # either direction.
            fresh = _node_count(self.counts(), "Event")
            if fresh is not None and fresh == len(raw) and len(raw) >= limit_sent:
                raise C.CurationRefused(
                    f"the Event layer read {len(raw)} rows, filling the "
                    f"{limit_sent}-row request, and a re-read of "
                    f"counts.Event now also reports {fresh} — but this layer is above what "
                    "ONE response may accept, and a filled response may also have been CUT, "
                    "so the rows cannot be shown to be the whole layer. Refusing rather "
                    "than filing a possibly-short layer")
            if fresh is not None and fresh == len(raw):
                self._note(
                    "the Event layer CHANGED between the status count and this read "
                    f"({reported} -> {len(raw)} rows); the count was re-read and now "
                    "agrees with the rows, so this is a complete read of the newer layer")
                reported = fresh
            else:
                raise C.CurationRefused(
                    f"the Event layer read {len(raw)} rows but counts.Event reports {reported} "
                    "— refusing a layer two surfaces disagree about. Both count `:Event` with "
                    "the same predicate, so the difference is one of: a genuinely partial / "
                    "clamped read; a change between the status read and this read, "
                    "repeated before the re-read (a DISAGREEMENT is re-counted once "
                    f"before refusing — the re-read reported {fresh!r}, not "
                    f"{len(raw)}); or a `counts` section that is itself stale, or scoped "
                    "to a different graph")
        empty_text = 0
        out: list[C.Row] = []
        for r in raw:
            if not isinstance(r, dict):
                # `_is_row_listing` accepts any list, so shape drift here would otherwise
                # surface as an AttributeError (rc 1 traceback) instead of this module's
                # designed refusal — the guard `read_operators` and `read_sources` apply.
                raise C.CurationRefused(
                    f"an Event row arrived as {type(r).__name__}, not an object — "
                    "refusing the layer rather than guessing which field carries its id")
            node_id = str(r.get("id") or r.get("eventId") or "")
            if not node_id:
                raise C.CurationRefused(
                    "an Event row arrived with neither `id` nor `eventId` — the class is "
                    "identified by its key (`_upsert_event` writes `id` equal to the "
                    "MERGE key `eventId`), so two id-less rows would fold onto one; "
                    "refusing the layer")
            content = _event_text(r)
            if not content:
                empty_text += 1
            out.append(C.Row(
                node_id=node_id,
                node_type="event",
                # The Event surface spells the kind `eventKind`; read the curation
                # spellings too rather than assume one (the operator read's lesson).
                kind=r.get("eventKind") or r.get("kind") or r.get("pointKind"),
                content=content,
                content_hash=r.get("contentHash") or r.get("content_hash"),
                props={k: v for k, v in r.items() if k not in ("embedding",)},
            ))
        if reported is None:
            self._note("the status section reported no `Event` count, so the Event layer "
                       "read is unreconciled (it is still the read's rows, not a count)")
        if empty_text:
            # ⛔ Disclosed, not hidden — and stated EXACTLY as far as it goes. An Event with
            # no `name`/`title`/`subject` has an EMPTY text, and this pass keys Events by
            # CONTENT (`Row.stable_key`), so those rows share one key: `--rows` shows the
            # same `stable_key` string for each of them. What does NOT happen is a duplicate
            # CLAIM — `classify_event` takes no `duplicate_of`/`keeper_id`, and every Event
            # arm is `undecidable`, so the row count and the per-row reasons are unaffected.
            # (An earlier draft of this note asserted a fold outcome that never reaches an
            # Event decision; round-2 review measured it and it was wrong.) The count is
            # still the difference between a reader that understood the layer and one that
            # silently collapsed it, so it travels on the artifact.
            self._note(f"{empty_text} of {len(raw)} Event rows carry no name/title/subject "
                       "and therefore an empty text: rows of the SAME KIND among them share "
                       "one `stable_key` in `--rows` WHEN they carry no `contentHash` of "
                       "their own (the key is `curate-11469:event:<kind>:<contentHash or "
                       "sha256(text)>`, so the sharing is per-kind and only for the rows "
                       "with neither; no duplicate claim is made for them — every Event "
                       "decision is `undecidable`, and nothing is carried)")
        return out, reported

    def _get_entities(self, limit: int) -> Any:
        """The Event enumeration, ROUTED to `self.graph` like every other read.

        `graph` is the credential's per-call routing argument (#9340,
        `mcp_server._take_graph_argument`) and `tortoise_get_entity` honours it: every tool
        advertises `graph` unless it is in `GRAPH_ARGUMENT_EXCLUDED`
        (`_with_graph_argument`), and `tortoise_get_entity` is not in that set — so
        omitting it would read the Event layer from the DEFAULT graph while every other
        section came from `--graph` — a census mixing two graphs.

        The fallback is `_search_operators`' and `_section`'s, for their reason: a
        graph-BOUND credential cannot be re-targeted, so a refusal ABOUT the graph
        argument is answered by re-reading WITHOUT it (omission means "this credential's
        own graph", a legal read) and recording the fact on the report. Any other
        refusal — a quota, a timeout, a DB fault — raises: re-reading a different graph
        under the requested graph's identity is how a census mixes two graphs.

        `type="events"` with no `id` is the whole-layer listing (`tortoise_get` treats
        `id` as an optional eventKind FILTER on this arm), and `limit` is the response the
        truncation check in `read_events` is sized against — ALWAYS exactly
        `EVENT_READ_CEILING`, never the reported count and never the bound plus one:
        `reported + 1` leaves exactly one row of headroom, so a single Event created between
        the count and the read fills the request and a truncated response becomes
        indistinguishable from a whole one; `min(reported + 1, EVENT_READ_CEILING)` loses
        the headroom entirely at the bound; and `bound + 1` can never be filled at all,
        because FalkorDB caps a non-aggregate read at its `RESULTSET_SIZE` (default 10 000,
        i.e. the bound), which makes the fill refusal inert. Sizing to the bound makes a
        capped response EQUAL it and refuse, and a response strictly below it uncapped,
        hence the whole layer.
        """
        args: dict[str, Any] = {"type": "events", "limit": limit}
        if not self.graph:
            return C.normalise_tool_result(self._call("tortoise_get_entity", dict(args)))
        try:
            res = C.normalise_tool_result(
                self._call("tortoise_get_entity", {**args, "graph": self.graph}))
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            if not _looks_like_graph_refusal(reason):
                raise
            self._note(f"the Event read: the explicit graph argument was refused "
                       f"({reason[:160]}); read from the credential's own graph instead")
            return C.normalise_tool_result(self._call("tortoise_get_entity", dict(args)))
        reason = _error_of(res)
        if reason and _looks_like_graph_refusal(reason):
            self._note(f"the Event read: the explicit graph argument was refused "
                       f"({reason[:160]}); read from the credential's own graph instead")
            return C.normalise_tool_result(self._call("tortoise_get_entity", dict(args)))
        return res

    def audit(self) -> dict[str, Any]:
        res = self._section("audit")
        return res if isinstance(res, dict) else {}


def _live_call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    from tortoise import mcp_client

    return mcp_client.call_tool(name, args or {})


#: A fallback to the credential's own graph is only legitimate when the refusal is
#: ABOUT the graph argument. The vocabulary is DERIVED from the producers, not
#: invented: `tortoise/mcp_auth.py::_retarget_refusal` ("This credential is bound to
#: one graph and cannot be re-targeted by a `graph` argument (requested …)") and
#: `tortoise/mcp_server.py` ("A graph-bound credential cannot be re-targeted"), plus
#: the resolver's "No graph '…' is reachable from this workspace". A quota/budget/DB
#: error matches none of these and RAISES instead of reading a different graph under
#: the requested graph's identity.
_GRAPH_REFUSAL_PHRASES = (
    "cannot be re-targeted",
    "cannot be retargeted",
    "bound to one graph",
    "is reachable from this workspace",
    "graph-bound credential",
)


def _looks_like_graph_refusal(reason: str) -> bool:
    low = (reason or "").lower()
    if any(phrase in low for phrase in _GRAPH_REFUSAL_PHRASES):
        return True
    # Otherwise the message must QUOTE the graph argument ("unknown graph 'x'",
    # 'graph "x" does not exist'). A bare mention of the word is not enough:
    # "FalkorDB: graph not found" and "unknown error while reading the graph" are
    # DB faults, and "permission denied: scope graph read" is a key restriction —
    # none of them says the ARGUMENT was refused.
    if "graph argument" in low or "graph-bound" in low:
        return True
    return ("graph '" in low or 'graph "' in low) and (
        "refus" in low or "unknown" in low or "does not exist" in low
        or "not found" in low or "not reachable" in low or "cannot" in low)


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


def _node_count(counts: Any, node_type: str) -> int | None:
    """`counts.<node_type>` from the status section, or None when it reports none.

    The status section nests its node counts under `counts` on some builds and
    carries them at the top level on others (`_source_gap` reads both spellings), and
    a missing or non-int value is None — NEVER 0, because "the surface reports no
    Event count" and "the graph holds no Events" are different facts and the
    reconciliation must be able to tell them apart (the silent-zero class).
    """
    if not isinstance(counts, dict):
        return None
    node_counts = counts.get("counts") if isinstance(counts.get("counts"), dict) else counts
    value = node_counts.get(node_type) if isinstance(node_counts, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _event_text(row: Mapping[str, Any]) -> str:
    """An Event's text, in the order the write path actually stores it.

    `name` is the Event's text and IS persisted on the node — it is deliberately
    absent from `_EVENT_HANDLED`, so `_persist_extra_props` carries it through
    (`projection/entities.py`, the #2788 note). Two narrower carriers exist and are
    declared here rather than discovered later: a MEETING Event persists its `title`
    through the guarded merge (and `backfill_sources` reads it back), and a domain
    Event always has a `subject`. An Event with none of the three keeps an empty text
    — `read_events` counts those on the report rather than inventing content for them.
    """
    for key in ("name", "title", "subject"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _unreconciled_sources(read: int, gap: int | None) -> str | None:
    """The refusal for a ZERO-source census that nothing can reconcile, else None.

    A non-zero gap is EXPECTED and named (`list_sources` filters `documentKind IS NULL`),
    so it is not a refusal. The refusal is the other case: 0 sources read AND no
    `counts.Source` to compare against, where "this graph has no sources" and "the
    listing was not read" are indistinguishable — the silent-zero R7 forbids, which
    for points is already a hard raise (`read_points`). A genuine empty listing still
    passes: the surface reports `counts.Source == 0`, so the gap is 0, not None.
    """
    if read or gap is not None:
        return None
    return ("read 0 sources and the status section reported no `Source` count, so an empty "
            "listing cannot be told from an unread one — refusing a census that would report "
            "the source population as empty with nothing to reconcile it against")


NOT_ENUMERABLE = {
    "object": "no listing surface on the MCP tool set — count from counts.Object",
    "subject": "no listing surface on the MCP tool set — count from counts.Subject",
    "source": "tortoise_list_sources matches `documentKind IS NULL` (sdk.list_sources), "
              "so a Document-kind Source is NOT listed — reconcile sources_read against "
              "counts.Source rather than reading the source count as complete",
}
#: ⛔ `event` is deliberately NOT in the table either, and its entry used to say
#: "READABLE but NOT read by this pass" — a scope decision written where a
#: REACHABILITY fact belongs, which made the census look complete over a layer of
#: **4,670 rows** it had never read (#12378). The layer is enumerated by
#: `read_events`: `tortoise_get_entity(type="events", limit=N)` forwards to
#: `get_events` (`MATCH (e:Event) … LIMIT $lim`, a bare list, no total) and answers the
#: whole layer in ONE call, reconciled against `counts.Event`.
#: ⚠️ Reading it moves NO row into `carry` — `classify_event` answers `undecidable` on
#: both arms — so the omission was never costing dispositions. What it cost was the
#: census's per-kind resolution (an undifferentiated episodic tier) and #11469's
#: Target 2, "every dropped row is named with its reason", for rows never enumerated.
#: The episodic tier's DESTINATION is still #10000's open D1 question; that is the part
#: that stayed a decision after the read landed.

#: ⛔ `operator` is deliberately NOT in the table above. It used to be, with the
#: reason "tortoise_query excludes operator Points unless an operator kind is
#: requested" — and that is false twice over, measured on live-v4 2026-11-13:
#: `tortoise_query(kind="operator")` and `tortoise_query(kind="IMPL")` both answer
#: `total: 0`, so the named escape hatch yields zero rows; and
#: `tortoise_search(entity_type="operator", limit=5000)` answers **3,727 rows in one
#: call**, an exact match with `structure.operators`, `match_source=structural`, no
#: vector leg. A readable layer recorded as unreadable is how 3,727 rows — the whole
#: reasoning layer — stayed out of a census that then reported itself complete.

#: The operator layer is read in ONE call: `tortoise_search` takes a `limit` and has no
#: `offset`, so there is no page walk to fall back on. A population above this ceiling
#: is therefore REFUSED rather than truncated — a partial layer entering the census
#: looks exactly like a complete one.
#:
#: ⛔ 10,000 is NOT a chosen trade-off, it is the SURFACE'S hard maximum: the SDK refuses
#: `limit > 10000` outright (`tortoise_fts_query`, `sdk.py:40575`), so this constant is
#: also the `limit` every operator read sends. There is no headroom to spend — raising
#: it does not read a wider layer, it makes every operator read raise `ValueError` out
#: of the SDK, which is NOT a `CurationRefused`: an ARMED run reaches rc 1 through the
#: generic `except Exception` arm, and a DISARMED run reports it on the artifact and
#: exits 0 — that arm exists for exactly this. A population above 10,000 has no
#: single-call read on this surface at all, which is what the refusal says.
OPERATOR_READ_CEILING = 10_000


#: The Event layer is read in ONE `tortoise_get_entity(type="events", limit=…)` call,
#: which forwards to `tortoise_get_events` (`mcp_server.py`) — a
#: `MATCH (e:Event) … ORDER BY e.startedAt DESC LIMIT $lim` with NO offset and a BARE
#: LIST return (`sdk.py`). A response that FILLS the request is therefore
#: indistinguishable from a truncated layer, while a response STRICTLY BELOW it is the
#: exact layer — which is what `read_events` rests completeness on. Same hazard as the
#: operator read, but the Event read refuses STRICTLY MORE, and the difference is the
#: engine cap: a count AT the bound refuses before the read, and a FILLED response refuses
#: even when a fresh count agrees. The operator read (#12380) refuses only a count ABOVE
#: `OPERATOR_READ_CEILING` and a fill whose count disagrees, so a 10,000-row operator layer
#: capped at 10,000 is accepted — the same fail-open, latent there (3,727 of 10,000 used)
#: and tracked separately (#12440); do not read this constant as evidence that arm needs no
#: audit.
#:
#: ⛔ 10,000 is TWO bounds that happen to coincide, and the coincidence is the whole
#: hazard. Ours: the SDK refuses `tortoise_search(limit > 10000)` outright, which makes
#: `OPERATOR_READ_CEILING` a wall, while `get_events` validates no upper bound and passes
#: `limit` straight into the Cypher `LIMIT` — so for Events this is a DECLARED
#: size/memory bound on ONE response, not a surface maximum. Theirs: FalkorDB silently
#: truncates any non-aggregate read at its server-global `RESULTSET_SIZE`, which DEFAULTS
#: to 10,000, with no error and no marker (measured on docker 4.20.4: 10 050 Points read
#: back as 10 000 rows — #4515/#4233/#5062). A `MATCH (e:Event) … LIMIT $lim` IS that
#: shape. So the read must size its request TO the bound, not above it: at `bound + 1` a
#: capped response always lands one row under the request, the fill refusal below can
#: never fire, and a layer that grew by one row past the cap is filed as complete. Sizing
#: to the bound makes a capped response equal it and refuse.
#:
#: The residual is a server tuned BELOW this bound, which caps the read invisibly. On the
#: UNREPORTED arm nothing can see it. On the reported arm only a count DIFFERING from the
#: cap sees it — a count that EQUALS the cap cannot, because it agrees with the capped
#: response (`len(raw) == reported` short-circuits the whole re-count path). Nothing fires
#: at the shipped default, where the cap is the bound; a deployment that lowers the cap
#: must lower this constant with it.
#:
#: The bound exists because this layer can only be read in ONE response (a `limit` and no
#: `offset`), so "the whole layer" needs a stated size a refusal can name — and it is a
#: size/memory bound, not an enforcement.
EVENT_READ_CEILING = 10_000


def cmd_inventory(args: argparse.Namespace) -> int:
    reader = MCPReader(_live_call_tool, args.graph)
    # ⛔ ONE handler for EVERY census read, and the SAME one `plan` has. The refusal type
    # also covers a tool payload that answered with an error (`normalise_tool_result`
    # raises `CurationRefused` for an `is_error` result), and a driver must not see
    # `inventory` exit 1 for the very failure `plan` reports as a designed rc 3 — two
    # codes for one failure was the round-3 finding, and the fix is agreement, not a
    # second vocabulary.
    try:
        counts = reader.counts()
        graphs = reader.graphs()
        points, total = reader.read_points(page=args.page, verify=args.verify_read)
        sources, source_total = reader.read_sources()
        # ⛔ The reasoning layer: readable, and omitted from every census before this
        # change. `structure` is handed over so the read reconciles against its count, and
        # a refusal here is the DESIGNED kind (rc 3), not a crash: it says two surfaces
        # disagree about one population, or that the layer cannot be read whole.
        # ⛔ `structure` is read IMMEDIATELY before the layer HERE TOO, not at the top: the
        # refusal's fourth cause is a concurrent create between the count and the read, and
        # its message states the window is "two requests". Reading the count before the
        # point walk (`read_points` is 34+ calls on a real graph) makes that message false
        # for the verb a driver runs FIRST — the window would be the whole walk.
        structure = reader.structure()
        operators, operators_reported = reader.read_operators(structure)
        # ⛔ The episodic layer — readable in one call and omitted from every census
        # before this change (#12378). ⛔ Its count is re-read HERE, immediately before the
        # read, rather than reusing `counts` from the top: that one is read before the
        # point walk (34+ calls on a real graph), and reconciling this layer against it
        # would make the refusal's "two requests" window claim false and turn a correct
        # read into a refusal the moment an Event was created mid-walk.
        events, events_reported = reader.read_events(reader.counts())
        # ⛔ The audit section is a census read too (it is in the `used` set the tests
        # assert, and it feeds the report), so it belongs INSIDE this handler: outside
        # it, an `is_error` payload here raised as rc 1 while the other four answered
        # rc 3 — the two-vocabularies defect round 3 removed, one read over (round-4
        # review measured it).
        audit = reader.audit()
    except C.CurationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    gap = _source_gap(counts, len(sources))
    unreconciled = _unreconciled_sources(len(sources), gap)
    if unreconciled:
        print(f"REFUSED: {unreconciled}", file=sys.stderr)
        return 3
    plan = C.build_plan(points + sources + operators + events,
                        source_graph=args.graph or "(credential-bound)",
                        target_graph=args.target or "(none: inventory only)")
    if args.rows:
        # inventory builds the plan (it IS the census), so the row artifact is
        # available here too — silently dropping the flag is how an operator reviews
        # a STALE file believing it is this run's.
        with open(args.rows, "w", encoding="utf-8") as fh:
            for d in plan.decisions:
                fh.write(json.dumps(d.as_dict(), sort_keys=True) + "\n")
    report = {
        # NEVER "default": an omitted `graph` reads the CREDENTIAL's own graph
        # (#10084), so a field saying "default" would state the opposite.
        "graph": args.graph or "(credential-bound)",
        "node_counts": counts.get("counts") if isinstance(counts.get("counts"), dict) else counts,
        "points_read": len(points),
        "points_total_reported": total,
        "sources_read": len(sources),
        "sources_total_reported": source_total,
        # The layer the census used to omit. `operators_read` is the READ's own count;
        # `operators_total_reported` is `structure.operators`, and the reader refuses
        # when they disagree, so a reader of this artifact can see both.
        "operators_read": len(operators),
        "operators_total_reported": operators_reported,
        # ⛔ `operators_total_reported is None` means NO cross-check was available, and
        # this layer's completeness then rests on `tortoise_search` having no `offset`
        # (a short response is indistinguishable from a whole layer). A driver must be
        # able to answer that from a FIELD rather than from prose — `census_measured`
        # describes the point/source census and knows nothing about this layer, so
        # leaving it at `True` was the machine-readable form of "this layer was read
        # whole", which the read cannot claim when nothing vouched for the count.
        "operators_reconciled": operators_reported is not None,
        # The episodic layer this census used to omit entirely (#12378). Same two facts
        # as the operator pair, for the same reason: `events_read` is the READ's own
        # count, and `events_total_reported` is the count THIS READ WAS RECONCILED AGAINST
        # — `counts.Event` normally, or the FRESH RE-READ when the two disagreed and the
        # re-count resolved it (so on a staleness accept this field is the newer number,
        # not the status count the run started with). A disagreement is re-counted once
        # and then accepted or refused, so a reader of this artifact can see both.
        "events_read": len(events),
        "events_total_reported": events_reported,
        # `events_total_reported is None` means NO cross-check was available: the layer's
        # completeness then rests on the response having stayed STRICTLY BELOW the
        # `EVENT_READ_CEILING` request, because a response that FILLS it cannot be told
        # from a truncation — FalkorDB caps a non-aggregate read at `RESULTSET_SIZE`,
        # which defaults to exactly this bound. A driver must be able to
        # answer that from a FIELD rather than from prose.
        "events_reconciled": events_reported is not None,
        "source_count_gap": gap,
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
        # Uniform with `plan`'s payload: this report's census is measured too.
        "census_measured": True,
        "rows_written": (len(plan.decisions) if args.rows else None),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        # ⛔ EVERY read layer is named, because this line is the ONE place an operator
        # sees the population without opening the JSON. It said "points + sources +
        # operators" and omitted the 4,670 Events the census had just classified — a
        # line stating 21,147 as the census over a run that measured 25,817, which is
        # the same "presents itself as the census over a population it did not count"
        # defect #12378 is filed about, one output over (PR review round 1).
        print(f"inventory: {len(points)} points + {len(sources)} sources + "
              f"{len(operators)} operators + {len(events)} events -> {args.out}")
        print(json.dumps(report["census"]["totals"], indent=1))
    else:
        print(text)
    return 0


def _disarmed_artifact(
    args: argparse.Namespace,
    reason: str,
    *,
    census: Any = None,
    read_failure: str | None = None,
    pending: int = 0,
    refused: str = "off-switch active",
    census_measured: bool | None = None,
    read_note: str | None = None,
    operators: tuple[int, int | None] | None = None,
    events: tuple[int, int | None] | None = None,
) -> int:
    """A DISARMED run that stopped early: exit 0, the reason on the artifact.

    The off switch exists to make pipeline noise stop during an incident — exactly when
    the reads are failing — so a disarmed run must not become a non-zero exit just
    because the census it was not going to write from could not be read (round-22
    contract: "a driver paged on a run that did exactly what it was told"). It must not
    SWALLOW the failure either.

    ⛔ `census_measured` is present on BOTH arms of THIS helper, so a driver answers
    "was it measured?" uniformly instead of inferring it from a missing key (round-38
    P3 — and the claim is scoped to this helper on purpose: a FULL disarmed run never
    reaches here and carries the same KEY from `cmd_plan`'s plan payload, where it is
    `unreconciled is None` and not a blanket `True` — an unreconcilable source listing
    travels through that arm, so the source population was not measured (round-40).
    The key is therefore on every disarmed artifact; its VALUE is the honest one). When
    `census` is given it is the census that WAS read, stopped by the ledger, and it
    travels here in full: a disarmed run over a damaged LEDGER still read the graph
    successfully, so the two losses are not the same fact and must not be reported as
    one — `census_measured` still says whether its SOURCE population could be
    reconciled, which is a different fact again.

    ⛔ `census_measured` IS NOT `census is not None`, and the LABEL at the end of this
    helper was the last place that still derived it from presence (round-42 review). A
    caller that knows the source population was unreconcilable passes
    `census_measured=False` while still handing over the census it did measure, because
    "the points were read" and "the census is complete" are different facts — and the
    operator-facing line must agree with the artifact it accompanies, or the incident
    log and the JSON tell two stories about one run.

    ⛔ `pending` is the plan's CARRY COUNT on that second arm, not a hardcoded 0: with
    the census measured and a carry set on the artifact, `pending: 0` says "nothing is
    owed" about a run whose ledger is UNREADABLE — a driver's `pending == 0 ⇒ done`
    check would read a refused migration as finished (round-38 review P2). The ledger
    could not be read, so the split is unknown and `pending` is the upper bound: the
    three counters still SPAN the carry set (`0 + 0 + carried == carried`), the
    documented invariant, and nothing was written.
    """
    # Resolved ONCE: the artifact and the operator-facing line must not disagree.
    measured = (census is not None) if census_measured is None else census_measured
    # ⛔ The reasoning layer's three fields travel HERE too, and they are TRI-STATE for a
    # reason: `None` means NO COMPLETED LAYER READ — the run stopped at or before that
    # read, so no census was produced and `read_failure` names the refusing read
    # (whatever rows it had already seen before it refused). It does NOT mean "this graph
    # has no operator layer"; `(n, m)` means a completed read, and `(n, None)` a
    # completed read with no count to reconcile it. The distinction is the whole point:
    # "not read" and "read but unreconciled" must not collapse into one value
    # (round-2/3 review).
    # ⚠️ The CONVERSE does not hold, and reading it into this field re-creates the very
    # defect it closes: `graphs()` is read AFTER the layer, so `census: null` also occurs
    # WITH a completed read (`(n, m)` on the two `after_read_*` arms). These fields report
    # the layer's state at the point the run stopped and are NEVER derived from `census`
    # (round-5 review). The ledger-stopped arm DID complete the read and passes
    # the real pair. Without this, a driver doing the round-7-mandated switch on a ledger
    # stop found `census_measured: true` — the strongest completeness claim the artifact
    # makes — with the only contrary fact in `read_note` prose, which is the defect the
    # field exists to close, one artifact over.
    operators_read, operators_reported = (None, None) if operators is None else operators
    #: The episodic layer's own tri-state, resolved ONCE for the same reason.
    events_read, events_reported = (None, None) if events is None else events
    payload = {
        # `null` when the census could not be measured — never a zeroed census, which
        # is indistinguishable from "the graph is empty" (the silent-zero class R7
        # forbids). When `census` is given it is the census that WAS read, stopped by
        # the ledger; `census_measured` says whether its SOURCE population could be
        # reconciled, which is a different fact.
        "census": census,
        # The CALLER supplies the fact where it knows better than `census is not None`:
        # a measured-but-unreconciled census exists AND has an unmeasured source
        # population, and deriving the flag from presence alone reported it as measured
        # (round-41 review). `read_note` carries the reason when it is not in
        # `read_failure` (that field is for the read that RAISED).
        "census_measured": measured,
        "disarmed_reason": reason,
        "read_failure": read_failure,
        "read_note": read_note,
        "operators_read": operators_read,
        "operators_total_reported": operators_reported,
        "operators_reconciled": (None if operators is None
                                 else operators_reported is not None),
        # ⛔ The episodic layer's TRI-STATE travels on the disarmed arms too, and for the
        # reason the operator pair does: `None` means NO COMPLETED LAYER READ, not "this
        # graph has no Events"; `(n, m)` is a completed read, `(n, None)` a completed read
        # with no count to reconcile it. A disarmed payload that dropped these fields made
        # the layer's state unknowable exactly when the run was reduced to reporting it
        # (the round-2/3 defect class on the operator layer, one layer over).
        "events_read": events_read,
        "events_total_reported": events_reported,
        "events_reconciled": (None if events is None
                              else events_reported is not None),
        "run": {
            "applied": False, "off": True, "skipped_known": 0, "written": 0,
            "target_graph": args.target, "digest": None, "refused": refused,
            "stopped": None, "identity": None, "duplicate_rows": 0, "pending": pending,
        },
        "rows_written": None,
        "not_enumerable_by_this_read_path": dict(NOT_ENUMERABLE),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    label = "census measured" if measured else "census NOT measured"
    print(f"disarmed ({label}: {reason[:200]}): nothing written, nothing attempted",
          file=sys.stderr)
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
    disarmed = C.off_switch_active(args.off)
    #: The reasoning layer as READ so far — `None` until the operator read completes, then
    #: `(rows, reported)`. Declared before the `try` so every disarmed arm reports the
    #: truth for its own point in the sequence (see the assignment below).
    layer: tuple[int, int | None] | None = None   # set only when the read COMPLETES
    #: The episodic layer's own tri-state — `None` until the Event read completes, then
    #: `(rows, reported)`. A SECOND field, not a reuse of `layer`: "the reasoning layer was
    #: read" says nothing about the episodic one. ⛔ Declared before the `try` for `layer`'s
    #: reason and one more: the refusal handler below reports it, so a refusal raised by
    #: any read at or before the Event read would leave a later assignment unreached — an
    #: UnboundLocalError inside the handler, turning a designed rc 3 into a traceback
    #: (measured: the disarmed-Event-read-failure test caught exactly that).
    episodic: tuple[int, int | None] | None = None
    try:
        counts = reader.counts()
        points, points_total = reader.read_points(page=args.page, verify=args.verify_read)
        sources, source_total = reader.read_sources()
        # ⛔ The reasoning layer, read and reconciled — see `read_operators`, and `structure`
        # read IMMEDIATELY before it so the count and the rows describe one instant: a full
        # point walk between them lets a concurrent create make `read > reported`, a
        # direction the refusal did NOT name before this change (it names it now, as its
        # fourth cause — see `read_operators`). `cmd_inventory` reads its count the same way,
        # for the same reason: the message is shared, so the window it states must be true
        # for every caller.
        structure = reader.structure()
        operators, operators_reported = reader.read_operators(structure)
        # ⛔ Bound the moment the read COMPLETES, and bound before the `try` so every
        # disarmed arm can report it truthfully: `graphs()` below is a LATER read, so a
        # failure there leaves the layer read and reconciled, and an arm that reported
        # `None` ("never read") would state the opposite of what happened (round-3
        # review). `None` now means only what it says.
        layer = (len(operators), operators_reported)
        # ⛔ The episodic layer (#12378): count FRESH, immediately before the read, for the
        # reason `cmd_inventory` states — the `counts` above predates the point walk, and
        # the refusal claims a two-request window.
        events, events_reported = reader.read_events(reader.counts())
        episodic = (len(events), events_reported)
        # INSIDE the guard too: `graphs()` feeds the identity guard and the ledger
        # scope, so a failure there is the same census class.
        graphs = reader.graphs()
    except C.CurationRefused as exc:
        # A DESIGNED refusal of ANY census read — the operator layer (two surfaces
        # disagreeing about one population, or a layer that cannot be read whole), or a
        # read whose payload came back as a tool error. rc 3, like every other refusal
        # this tool reports (`cmd_inventory` shares this answer), not the
        # unhandled-failure rc 1. Disarmed keeps the round-22 contract: nothing was going
        # to be written, so it is still exit 0 and the refusal travels on the artifact.
        if not disarmed:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3
        # `read_note` travels too: a section that fell back to the credential's own graph
        # is disclosed THERE, and this arm is one of the two places a disarmed run can
        # end — dropping it would lose the only record that part of this census came from
        # a different graph than `--graph` names.
        return _disarmed_artifact(args, str(exc), refused=str(exc), read_failure=str(exc),
                                  read_note=reader.note, operators=layer, events=episodic)
    except Exception as exc:
        # ⛔ THE SWITCH IS RESOLVED BEFORE THE CENSUS, for EVERY read — not only the
        # source section. An armed run still raises (a failed read must be loud); a
        # disarmed run reports the failure on the artifact and exits 0 (round-37 P3).
        if not disarmed:
            raise
        # ⛔ `refused` names the CAUSE on every disarmed failure arm, which is the
        # convention the ledger arm below already follows and the one the sibling
        # `CurationRefused` arm above follows too — leaving this arm on the helper's
        # `"off-switch active"` default made ONE state (a census read failed on a
        # disarmed run) read two ways in one field, and the off switch is not what
        # refused it here.
        # ⛔ `read_note` travels here too, for the SAME reason the sibling
        # `CurationRefused` arm passes it: a section that fell back to the credential's
        # own graph is disclosed only there, and this arm is one of the two places a
        # disarmed run can end. One state, one spelling — the asymmetry was this
        # commit's own `run.refused` defect, one field over (round-2 review).
        return _disarmed_artifact(args, f"{type(exc).__name__}: {exc}",
                                  refused=f"{type(exc).__name__}: {exc}",
                                  read_note=reader.note, operators=layer, events=episodic,
                                  read_failure=f"{type(exc).__name__}: {exc}")
    gap = _source_gap(counts, len(sources))
    unreconciled = _unreconciled_sources(len(sources), gap)
    if unreconciled and not C.off_switch_active(args.off):
        print(f"REFUSED: {unreconciled}", file=sys.stderr)
        return 3
    if unreconciled:
        # ⛔ A DISARMED run stays a success (exit 0): the operator asked for nothing to
        # be written, and reporting a REFUSAL for it is the round-22 defect (a driver
        # pages on a run that did exactly what it was told). The unmeasurable census
        # still travels on the artifact rather than being swallowed.
        reader._note(unreconciled)
    if args.apply and not graphs and not disarmed:
        # ⛔ An EMPTY `graphs` section silently disabled the rows-authority guard: with no
        # identity rows the guard falls back to shape, which accepts any id-shaped ref, so
        # `--target g_new` was written while the ledger filed the key under a SECOND scope
        # for one graph (round-38 P3). An apply with no identity rows is an unproven
        # target, and that is a refusal — the same answer as any other unproven identity.
        # A disarmed apply writes nothing, so it is not refused (the switch wins).
        print("REFUSED: the `graphs` section returned no identity rows, so the graph "
              "`--target` names cannot be proven — an applying run is refused rather "
              "than resolved by shape", file=sys.stderr)
        return 3
    plan = C.build_plan(points + sources + operators + events,
                        source_graph=args.graph or "(credential-bound)",
                        target_graph=args.target)
    try:
        result = C.run_plan(
            plan,
            writer=C.IngestWriter(_live_call_tool),
            ledger=C.Ledger(
                args.ledger or os.path.expanduser("~/.tortoise/curate-11469-ledger.jsonl"),
                target=args.target,
                # CANONICAL scope: `--target g_new` today and `--target org_x_g_new`
                # yesterday are ONE graph, and a spelling-keyed ledger re-sent every
                # row into it (round-36 review, P3-5).
                graph_rows=graphs,
            ),
            apply=args.apply,
            off=args.off,
            graph_rows=graphs,
        )
    except C.CurationRefused as exc:
        if disarmed:
            # ⛔ The switch is TOTAL: a disarmed run over a damaged/unscopable LEDGER is
            # still exit 0 (nothing was going to be written), and the census it DID
            # measure travels on the artifact. Pre-fix this arm exited 3 while the failed
            # -read arm exited 0 — two disarmed runs in one incident, opposite contracts
            # (round-38 P3). ⛔ `pending` is the carry count, NOT 0: the ledger is what
            # could not be read, so its holdings are unknown and the rows it might have
            # held are still owed (round-38 review P2). ⛔ `census_measured` is the
            # CALLER's fact, not `census is not None`: this arm can ALSO carry an
            # unreconcilable source listing, so the points were measured and the source
            # population was not — presence alone reported that as measured, and dropped
            # the reason entirely (round-41 review P3).
            return _disarmed_artifact(args, str(exc), census=plan.census(),
                                      pending=len(plan.carry_rows()), refused=str(exc),
                                      census_measured=unreconciled is None,
                                      operators=layer,
                                      events=episodic,
                                      read_note=reader.note)
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    if args.rows:
        # The census is aggregate by design; the ROW-LEVEL reasons are what a review
        # of "which 127 rows, and why" needs, so they get their own artifact rather
        # than an implied claim that the report carries them.
        with open(args.rows, "w", encoding="utf-8") as fh:
            for d in plan.decisions:
                fh.write(json.dumps(d.as_dict(), sort_keys=True) + "\n")
        payload_rows = len(plan.decisions)
    else:
        payload_rows = None
    payload = {"census": plan.census(), "run": result.as_dict(),
               # ⛔ PRESENT ON EVERY PATH, so `payload["census_measured"]` answers "was the
               # census measured?" instead of KeyError-ing on the most common disarmed run
               # (the healthy one) — the field was added for that uniformity and only the
               # two failure arms carried it (round-39 P3).
               # NOT unconditionally True: the disarmed arm carries an
               # UNRECONCILABLE source listing through to here (the armed one returns 3
               # above), and on that arm the source population was not measured — the
               # driver must be able to read that from this field alone, with the reason
               # in `read_note` (round-40 review, P3-2).
               "census_measured": unreconciled is None,
               "rows_written": payload_rows,
               "points_read": len(points), "points_total_reported": points_total,
               "sources_read": len(sources), "sources_total_reported": source_total,
               "operators_read": len(operators),
               "operators_total_reported": operators_reported,
               # See `cmd_inventory`: the layer's completeness is a field, not a note.
               "operators_reconciled": operators_reported is not None,
               # The episodic layer (#12378), same three facts as the reasoning one.
               "events_read": len(events),
               "events_total_reported": events_reported,
               "events_reconciled": events_reported is not None,
               "source_count_gap": gap,
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
    if result.off and not result.applied:
        # The operator ASKED for this (`--off` / TORTOISE_CURATE_OFF): nothing was
        # written because nothing was meant to be. Checked BEFORE the generic refusal
        # branch, which would otherwise read the off-switch as a failure and exit 3.
        print(f"disarmed ({result.refused}): nothing written, nothing attempted", file=sys.stderr)
        return 0
    if result.refused and not result.applied and args.apply:
        # A refused WRITE (or a refused identity) is a failure, not a successful
        # plan: a scripted driver must not read exit 0 as "migrated".
        print(f"REFUSED: {result.refused}", file=sys.stderr)
        return 3
    # Keyed on `stopped`, NOT on `applied`. A run stopped before its first batch
    # accepted anything reports `applied: False` (nothing landed) — an `applied and
    # stopped` test would fall straight through to exit 0, reading a stopped migration
    # as a finished one. `stopped` is the fact; `applied` is a derived field.
    if result.stopped:
        print(f"STOPPED mid-run: {result.stopped} (accepted={result.written})", file=sys.stderr)
        return 3
    if result.applied and result.refused:
        print(f"REFUSED mid-run: {result.refused} (accepted={result.written})", file=sys.stderr)
        return 3
    if args.apply and not result.applied:
        # The apply leg ran, nothing was accepted, and the writer neither refused nor
        # stopped. `IngestWriter` cannot produce this (it stops or refuses rather than
        # reporting a success that accepted nothing), so a CUSTOM `PlanWriter` is the
        # only source — but `applied: false` over a plan that still owes rows must not
        # exit 0, or a driver reads a migration that moved nothing as finished.
        print(f"NO-OP: nothing accepted, {result.pending} row(s) still owed", file=sys.stderr)
        return 3
    return 0


def _page_size(text: str) -> int:
    """`--page-size` must be a USAGE error when it is not a legal size.

    Unvalidated, `--page-size 0` reached the surface, came back as
    `{"error": "limit must be >= 1"}`, and surfaced as "refusing to report an empty
    graph" with a traceback (exit 1) — a usage mistake reported as a data verdict.
    """
    import argparse as _argparse
    try:
        val = int(text)
    except ValueError as exc:
        raise _argparse.ArgumentTypeError(f"not an integer: {text!r}") from exc
    if val < 1:
        raise _argparse.ArgumentTypeError(f"must be >= 1 (got {val})")
    return val


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Curation pass for our own memory (#11469)")
    ap.add_argument("action", choices=["inventory", "plan"])
    ap.add_argument("--graph", help="source graph (default: the credential's own graph)")
    ap.add_argument("--target", help="target graph — must differ from the source")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--rows", help="also write the ROW-LEVEL decisions here (JSON Lines: "
                                  "node id, disposition, the rule's reason, pack) — the artifact "
                                  "a row-by-row review of the plan actually needs")
    ap.add_argument("--ledger", help="ledger path (default ~/.tortoise/curate-11469-ledger.jsonl)")
    ap.add_argument("--page-size", "--page", dest="page", type=_page_size, default=PAGE,
                    help=f"rows per read (default {PAGE}). This is a page SIZE, not a "
                         "1-based page number: a small value makes the read take many calls")
    ap.add_argument("--apply", action="store_true", help="actually write (default: plan only)")
    ap.add_argument("--off", action="store_true", help="off-switch: write nothing")
    ap.add_argument("--no-verify-read", dest="verify_read", action="store_false",
                    help="skip the second verification walk. The walk assumes an unchanging "
                         "population; a same-size churn (one retraction + one addition) is "
                         "invisible without the second pass, so skipping it is a speed trade")
    args = ap.parse_args(argv)
    if args.action == "inventory":
        ignored = [f for f, v in (("--apply", args.apply), ("--ledger", args.ledger),
                                  ("--target", args.target), ("--off", args.off))
                   if v]
        if ignored:
            # Silently ignoring a flag is how an operator believes an artifact exists.
            print(f"inventory does not write: {', '.join(ignored)} apply only to `plan`",
                  file=sys.stderr)
            return 2
    return cmd_inventory(args) if args.action == "inventory" else cmd_plan(args)


if __name__ == "__main__":
    raise SystemExit(main())
