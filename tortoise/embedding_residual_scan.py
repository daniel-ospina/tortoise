"""Erasure attestation across BOTH vector stores — the residual scan (#11642).

#4051 V1 moved the vector INDEX to Postgres and left the payload where it always
was: in the record's `embedding` column AND on the graph node, because writes
stay in the graph (§4 of `docs/architecture/SEARCH-ARCHITECTURE.md`). So from
this head on there are **two copies of every vector**, and "erased" is a claim
about both. This module is the instrument that settles it, because a return code
cannot: a delete that ran is not a delete that reached every store, and the RLS
policies on the record turn an UNSCOPED read into zero rows with no error — so
the shape that looks cleanest is the one that proves nothing.

⛔ IT IS A **RESIDUAL SCAN**, NOT A RETURN CODE. The question is not "did the
delete succeed" (the callers of an erasure already have an answer to that, and
it is the answer that has been wrong before) but "is a vector still readable
anywhere in this scope". So the verdict comes from counting, in both stores,
and the counts are the evidence.

The three outcomes, and why ``2`` exists
----------------------------------------
* ``CLEAN`` (exit 0) — both stores were READ, in scope, and neither holds a
  vector for the scope.
* ``RESIDUE`` (exit 3) — a store answered and still holds a vector. A FINDING:
  the erasure did not reach it. The counts say which store and how many.
* ``UNAVAILABLE`` (exit 2) — NO VERDICT. No DSN, no driver, an unreadable store,
  a connection the tenant prologue refuses, or a graph that could not be
  reached. ⚠️ This is the outcome that makes the tool honest: a scan that could
  not read a store must never be reported as "no residue found", because the
  whole failure class is a delete that silently reached one store and not the
  other.

⚠️ ``CLEAN`` is scoped and says so. Both counts are tenant-scoped (the record's
RLS via `record_projection.scope_connection`, the graph by its own name), so a
wrong `--org`/`--graph` reads a tenant that holds nothing and reports `CLEAN`
about **that scope** — which is a true statement about a scope nobody asked
about, not a false one about the one they did. The outcome names the scope it
measured for exactly that reason.

Read-only. It writes nothing to either store, creates no index and deletes no
row: an instrument that erases while attesting cannot attest.
"""
from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CLEAN",
    "EXIT_CLEAN",
    "EXIT_RESIDUE",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "RESIDUE",
    "UNAVAILABLE",
    "ResidualScan",
    "scan",
]

LOG_NAME = "tortoise.embedding_residual_scan"

CLEAN = "CLEAN"
RESIDUE = "RESIDUE"
UNAVAILABLE = "UNAVAILABLE"

EXIT_CLEAN = 0
#: A FINDING — a store still holds a vector for a scope that was erased.
EXIT_RESIDUE = 3
#: NO VERDICT — never reported as CLEAN. See the module docstring.
EXIT_UNAVAILABLE = 2

#: The record tables that can hold a vector. All three carry `embedding` (jsonb)
#: and the GENERATED `embedding_vec` the #11642 index is built on, so a residual
#: can survive in the payload OR in the indexed projection, and the scan counts
#: EITHER (`embedding IS NOT NULL OR embedding_vec IS NOT NULL`).
#:
#: ⛔ The OR is the point, not a belt-and-braces (PR #11660 review F13: this
#: comment used to say the scan counts "the PAYLOAD, because that is the one that
#: existed before the move", which the code has never done). A GENERATED column
#: cannot disagree with its payload TODAY — but the erasure this tool attests is
#: not written by this tool, and a `DELETE`/`UPDATE` that reaches one column and
#: not the other is exactly the shape a payload-only count reports CLEAN about.
#: Counting only the payload would make the scan's verdict a statement about a
#: column, when the customer's question is whether the vector is still readable
#: — and it is readable through `embedding_vec`.
_RECORD_TABLES = ("point", "source", "event")

#: Graph labels that can hold an `embedding` property. `Point` carries claims AND
#: operators, `Source` carries documents, `Event` the episodic rows. `Object` and
#: `Subject` are listed because the graph really does embed them (#7845) even
#: though the record has no column — they are the reason the graph half of this
#: scan cannot be replaced by the record half.
_GRAPH_LABELS = ("Point", "Source", "Event", "Object", "Subject")


@dataclass
class ResidualScan:
    """The measurement: one row per store, and a verdict derived from the rows.

    Nothing here is inferred. ``record_vectors`` / ``graph_vectors`` are ``None``
    when that store could not be read — and ``None`` is NOT zero: it is the
    difference between "the store holds nothing" and "nobody looked", which is
    the one distinction this whole tool exists to keep.
    """

    org_id: str
    graph_id: str
    record_vectors: int | None = None
    record_by_table: dict[str, int] = field(default_factory=dict)
    graph_vectors: int | None = None
    graph_by_label: dict[str, int] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.refusals:
            return UNAVAILABLE
        if (self.record_vectors or 0) > 0 or (self.graph_vectors or 0) > 0:
            return RESIDUE
        return CLEAN

    @property
    def exit_code(self) -> int:
        return {CLEAN: EXIT_CLEAN, RESIDUE: EXIT_RESIDUE,
                UNAVAILABLE: EXIT_UNAVAILABLE}[self.verdict]

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "verdict": self.verdict,
            "record_vectors": self.record_vectors,
            "record_by_table": dict(sorted(self.record_by_table.items())),
            "graph_vectors": self.graph_vectors,
            "graph_by_label": dict(sorted(self.graph_by_label.items())),
            "refusals": list(self.refusals),
            # Stated on every outcome, not only the failure ones: a reader who
            # quotes a CLEAN must be able to see that it is a claim about ONE
            # scope in TWO stores.
            # ⛔ TWO SCOPES, TWO IDENTIFIERS: the record's `graph_id` is a
            # `public.graphs.id`; the graph's name is that row's `namespace`.
            # They are NOT interchangeable, and the CLI takes them separately
            # (#11660 review round 3, P1).
            "scope": "org_id+graph_id on the record, graph NAME on the graph",
        }


def _count_record(scan: ResidualScan, record_dsn: str | None) -> None:
    """Count the payload vectors this tenant still holds, or refuse.

    ⛔ ``record_serve.open_record`` + ``record_projection.scope_connection`` are
    REUSED rather than re-derived: they are the one bounded-connect + tenant
    prologue pair both record doors use, and a second copy here is how an audit
    tool starts measuring a different database than the product.
    """
    from .record_projection import scope_connection
    from .record_serve import open_record

    tallies: dict[str, int] = {}
    try:
        conn = open_record(record_dsn)
    except Exception as e:  # noqa: BLE001, RUF100
        scan.refusals.append(
            f"record: could not be opened ({type(e).__name__}) — no verdict")
        return
    try:
        cur = scope_connection(conn, scan.org_id)
        try:
            for table in _RECORD_TABLES:
                cur.execute(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE org_id = %s AND graph_id = %s "
                    "AND (embedding IS NOT NULL OR embedding_vec IS NOT NULL)",
                    (scan.org_id, scan.graph_id),
                )
                row = cur.fetchone()
                tallies[table] = int(row[0]) if row else 0
        finally:
            # `contextlib.suppress`, not `try/except/pass`: a close that failed
            # would mask the read error this leg is REPORTING (a bare except
            # swallows it, and the refusal line then names the wrong failure).
            with contextlib.suppress(Exception):
                cur.close()
    except Exception as e:  # noqa: BLE001, RUF100
        scan.refusals.append(
            f"record: could not be read in scope ({type(e).__name__}) — "
            "no verdict")
        return
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    scan.record_by_table = tallies
    scan.record_vectors = sum(tallies.values())


def _count_graph(scan: ResidualScan, graph: Any) -> None:
    """Count the graph's embedding-bearing nodes for this graph, or refuse.

    The graph is addressed BY NAME, so "the scope" is the graph itself — there is
    no second axis to get wrong, and that is why `--graph` names the graph in
    both stores rather than only the record.
    """
    tallies: dict[str, int] = {}
    try:
        for label in _GRAPH_LABELS:
            rows = graph.query(
                f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL "
                "RETURN count(n)"
            ).result_set
            if not rows:
                scan.refusals.append(
                    f"graph: no row for label {label} — an aggregate returns "
                    "exactly one, so this is a read that answered nothing; "
                    "no verdict")
                return
            tallies[label] = int(rows[0][0] or 0)
    except Exception as e:  # noqa: BLE001, RUF100
        scan.refusals.append(
            f"graph: could not be read ({type(e).__name__}) — no verdict")
        return
    scan.graph_by_label = tallies
    scan.graph_vectors = sum(tallies.values())


def scan(
    *,
    org_id: str,
    graph_id: str,
    record_dsn: str | None = None,
    graph: Any | None = None,
) -> ResidualScan:
    """Measure both stores for one scope. Never raises for an unreadable store.

    ``graph`` is the handle to scan; when it is ``None`` the caller has not
    provided one and the graph half is REFUSED rather than assumed clean — a
    scan of one store is not a scan of two, and reporting it as ``CLEAN`` would
    be the exact false assurance this tool exists to remove.
    """
    out = ResidualScan(org_id=org_id, graph_id=graph_id)
    _count_record(out, record_dsn)
    if graph is None:
        out.refusals.append(
            "graph: no handle supplied — a one-store scan cannot attest an "
            "erasure that has to reach both, so it is no verdict, not CLEAN")
    else:
        _count_graph(out, graph)
    logging.getLogger(LOG_NAME).info(
        "embedding-residual-scan %s",
        json.dumps(out.as_dict(), sort_keys=True, default=str))
    return out
