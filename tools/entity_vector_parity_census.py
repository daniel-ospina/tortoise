#!/usr/bin/env python3
"""#13629 step 1 — the per-label parity census that gates the graph-side vector retirement.

THE QUESTION THIS ANSWERS, and it is the whole gate on #13629's destructive half:

    For every graph node of `:Object` / `:Subject` / `:Event` / `:Source` that
    carries a stored `embedding`, does the POSTGRES RECORD already hold that
    node's `embedding_vec` — so that removing the graph copy destroys nothing?

`#11395`'s 2026-11-17 supersession (comment `6956793300`) authorises exactly that
removal and it authorises it **only second**: *"record first, graph removal second
… Do not do (2) in the same PR as (1). The original ruling existed because the graph
was the only copy; removing it before the record demonstrably serves it repeats the
failure the ruling was written to prevent."* The record's half has landed (#13220,
#13430, #13466) and the deployed build advertises all seven labels through
`record_vector.LABEL_SERVED`. That is the SERVING claim. This is the COPY claim, per
label, counted rather than advertised — and the two are not the same fact: a record
that serves a label can still be missing the rows it serves, and a `:Object` whose
record row predates the writer (or was NULLed by the width converter) has the graph
as its ONLY copy.

⛔ **READ-ONLY ON BOTH STORES.** It never removes a property, never writes a record
row, and issues no `DELETE`/`REMOVE`/`SET`/`CREATE` of any kind. Its output is one
row per label and a verdict. The destructive act is `tools/strip_graph_entity_vectors.py`,
which imports THIS module's reads and comparison rather than restating either.

## The three numbers per label, and why all three are printed

| column | meaning | what a reader does with it |
|---|---|---|
| `graph_vectors` | graph nodes of this label whose `embedding` is present | the population the strip would touch |
| `record_vectors` | record rows for this tenant whose `embedding_vec` is present | the copy that must already exist |
| **`graph_only`** | graph nodes carrying a vector whose record row has **no `embedding_vec`** | ⛔ **the refusal.** Each one is a vector whose removal destroys the only readable copy |

`graph_only` is the number this whole instrument exists for, and **it is printed
including when it is zero** — an absent column is how a gate reports a pass without
having looked, and this epic has already paid for that class of report once
(`#11112`'s graph that a census CREATED while certifying it).

Two further buckets are printed because they turn a non-zero `graph_only` into a
diagnosis instead of a mystery:

* `unkeyed_graph_nodes` — graph nodes carrying a vector but no key property
  (`id` / `eventId` / `url`). They can never be matched to a record row, so they are
  **graph-only by construction** and are counted INTO `graph_only` as well as being
  named separately. Silently skipping them would make a strip delete them.
* `record_unreadable_vector` — record rows whose `embedding` payload is present but
  whose GENERATED `embedding_vec` is NULL (the `public.record_embedding_vector`
  converter refuses anything that is not a 384-long all-numeric array). These are the
  rows a naive `embedding IS NOT NULL` comparison would call "the record has it" —
  they answer no dense query, so on the graph side they are graph-only too.

`record_only` (record rows with a vector and no graph node) is printed last and
**blocks nothing**: the record is the surviving copy, so extra rows there are the
record being ahead, which is the direction the cutover wants.

## Why the comparison is keyed the way it is

Both sides are keyed by the node's IDENTITY, and neither key is spelled here — each
comes from the component that owns it:

* **graph side** — the property the vector leg itself returns as the node's id
  (`tortoise/search_engine.run_vector_query`'s `id_field`: `url` for
  `source`/`document`, `eventId` for `event`, `id` otherwise). Restating that map
  here would be a second declaration of it, and `#9553` records what a second
  declaration of a key column costs: an Event arm that matched `event.id`, the one
  column its dominant producers leave NULL, and silently filed no row.
* **record side** — `node_key`, the record's one identity column across every table
  (`NodeSnapshot`'s key, `node_identity`'s PK component, and the column the
  `chk_*_node_key_is_contract_key` constraints pin). Since `#9881` it IS the id for
  `object`/`subject`, so one column serves all four labels.

## What a refusal looks like, and why it is not a failure of the instrument

Exit `0` means the instrument MEASURED — a `GRAPH-ONLY(n)` verdict is a successful
measurement that says *do not strip this label*. Exit `3` means a side could not be
read: no DSN, no graph, no `embedding_vec` column, a truncated page read, an
unscoped (untenanted) record read. **A census that cannot be run is a refusal, not a
pass** — the same posture `tools/turn_parity_census.py` takes for the turn purge, and
for the same reason: a vacuous parity report licenses an irreversible delete.

Usage::

    # the whole population, both stores
    uv run python tools/entity_vector_parity_census.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph>

    # machine-readable, and non-zero if any label is not at parity
    uv run python tools/entity_vector_parity_census.py --json out.json --require-parity

Exit status: ``0`` measured · ``3`` refused · ``4`` measured and `--require-parity`
was passed and at least one label is not at parity · ``1`` usage.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first, and the operator would get an unattributed
# traceback instead of this sentence (the `tools/` entry-point contract;
# `tests/test_entry_point_python_guard.py` pins it for the corpus). This guard is
# PROTECTIVE, not documentary: nothing below it may use 3.12-only SYNTAX, or the file
# would die in the tokenizer and never reach this statement.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/entity_vector_parity_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/entity_vector_parity_census.py`"
    )

import argparse
import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: The turn purge's census module — imported for the REFUSALS it already makes, never
#: re-implemented here: `empty_graph_target_flag` (an empty `--graph-name` is not an
#: absent one) and `resolve_graph_name` (the name this run actually addressed, so the
#: artefact cannot name a graph the run never opened). `purge_legacy_turns` couples to
#: it the same way for the same reason.
import turn_parity_census as census_mod

CensusError = census_mod.CensusError

#: The labels this issue retires, in the order they are reported, with the graph
#: property that IDENTIFIES a node of that label.
#:
#: ⛔ `:Source` is ONE label serving TWO caller-facing `entity_type`s (`source` and
#: `document`, `tortoise/security.ENTITY_TYPE_LABELS`), and `:Point` is deliberately
#: ABSENT: the armed cutover and `#13330` own it, and `#13629`'s brief forbids
#: touching it. Adding it here would silently widen a destructive tool's population —
#: which is why the population is a module-level constant a test iterates rather than
#: a list assembled at a call site.
RETIRED: tuple[tuple[str, str, str], ...] = (
    # (record table, graph label, graph identity property)
    ("object", "Object", "id"),
    ("subject", "Subject", "id"),
    ("event", "Event", "eventId"),
    ("source", "Source", "url"),
)

#: `:Point` — named so `test_the_population_excludes_point` can assert its absence by
#: name instead of by count. `#13629`'s scope boundary, in one place.
NOT_IN_SCOPE: tuple[str, ...] = ("Point",)

#: Keyset page size for the graph leg. Must sit well UNDER FalkorDB's
#: `RESULTSET_SIZE` (default 10_000) so a page is never truncated, and the store's own
#: aggregate count is still compared against what the pages returned — a silently
#: short page set is not an error to FalkorDB, so without that cross-check a truncated
#: read would report a graph-only node as absent from the graph and licence its strip
#: (the `#5062`/`#4233` truncation class `turn_parity_census` already guards).
GRAPH_PAGE_SIZE = 1000

#: The graph leg's count query — ONE row, so `RESULTSET_SIZE` cannot truncate it.
_GRAPH_COUNT_CYPHER = "MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(n)"

#: The graph leg's paged read. `ORDER BY id(n)` + the `>= $cursor` / `last + 1` keyset
#: idiom of `hosted_backup._paged_rows`: a stable engine-internal total order that no
#: concurrent write can renumber, unlike a `SKIP`/`LIMIT` offset walk.
_GRAPH_PAGE_CYPHER = (
    "MATCH (n:{label}) WHERE n.embedding IS NOT NULL AND id(n) >= $cursor "
    "RETURN id(n), n.{key_prop} AS node_key ORDER BY id(n) LIMIT $batch"
)


def _quote_identifier(name: str) -> str:
    """``name`` as a Cypher identifier, by the product's OWN rule.

    A label and a property name cannot be query parameters, so both reach Cypher by
    interpolation — and the label comes from the graph's own schema, i.e. from
    whatever a customer wrote there. `tortoise.graph_key_restore._quote_identifier` is
    the repo's one implementation of that escaping; restating a sanitizer here would be
    the second declaration this file refuses everywhere else.
    """
    from tortoise.graph_key_restore import _quote_identifier as quote

    return quote(name)


def _read(g: Any, cypher: str, params: dict | None = None) -> Any:
    """Issue a census READ through the engine's read-only verb.

    `graph.query` is the write-CAPABLE verb, so "this module stores nothing" would be
    this file's memory rather than the engine's rule. `GRAPH.RO_QUERY` is rejected
    server-side for a write (measured on falkordb-server 4.20.6 for `count(n)`,
    `CALL db.labels()` and the keyset page — identical answers through `ro_query`).
    """
    if params is None:
        return g.ro_query(cypher)
    return g.ro_query(cypher, params)


@dataclass
class LabelCensus:
    """One label's three numbers plus the buckets that explain a non-zero `graph_only`."""

    label: str
    table: str
    entity_types: list[str]
    graph_vectors: int = 0
    record_vectors: int = 0
    graph_only: int = 0
    graph_only_sample: list[str] = field(default_factory=list)
    unkeyed_graph_nodes: int = 0
    record_unreadable_vector: int = 0
    record_only: int = 0
    #: Filled by `census()` — the live reason a strip of THIS label is refused, if any.
    blocked_reason: str | None = None

    @property
    def at_parity(self) -> bool:
        """Graph ⊆ record: every vector the graph holds is readable in the record."""
        return self.graph_only == 0 and self.unkeyed_graph_nodes == 0

    @property
    def verdict(self) -> str:
        if self.at_parity:
            return "PARITY"
        return f"GRAPH-ONLY({self.graph_only})"

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "record_table": self.table,
            "entity_types": list(self.entity_types),
            "graph_vectors": self.graph_vectors,
            "record_vectors": self.record_vectors,
            "graph_only": self.graph_only,
            "graph_only_sample": list(self.graph_only_sample),
            "unkeyed_graph_nodes": self.unkeyed_graph_nodes,
            "record_unreadable_vector": self.record_unreadable_vector,
            "record_only": self.record_only,
            "verdict": self.verdict,
            "at_parity": self.at_parity,
            "blocked_reason": self.blocked_reason,
        }


def affected_entity_types(label: str) -> list[str]:
    """The caller-facing `entity_type` values that address ``label``.

    DERIVED from `tortoise.security.ENTITY_TYPE_LABELS` — the one label↔type mapping
    the query legs use — never listed here. `:Source` answers both `source` and
    `document`; spelling the map out in this file is how a census comes to state a
    population the product does not have (`#11405`'s two-of-five table cell).
    """
    from tortoise.security import ENTITY_TYPE_LABELS

    return sorted(t for t, lbl in ENTITY_TYPE_LABELS.items() if lbl == label)


def read_graph_vector_keys(g: Any, label: str, key_prop: str) -> dict:
    """The graph leg for ONE label: every vector-bearing node's identity key.

    Returns ``{"count", "keys", "unkeyed"}``. Raises :class:`CensusError` when the
    count cannot be taken, when the paged read comes back SHORT of it (truncation), or
    when a page cannot be read — an absence is never established from a failed read.
    """
    quoted, quoted_key = _quote_identifier(label), _quote_identifier(key_prop)
    try:
        rows = _read(g, _GRAPH_COUNT_CYPHER.format(label=quoted)).result_set
        total = int(rows[0][0]) if rows else 0
    except Exception as exc:
        raise CensusError(
            f"the graph's `{label}` vector count could not be read "
            f"({type(exc).__name__}: {exc}) — an absence is NOT established, so no "
            "label is certified") from exc

    keys: list[str] = []
    unkeyed = 0
    cursor = 0
    while True:
        try:
            page = _read(
                g,
                _GRAPH_PAGE_CYPHER.format(label=quoted, key_prop=quoted_key),
                {"cursor": cursor, "batch": GRAPH_PAGE_SIZE},
            ).result_set
        except Exception as exc:
            raise CensusError(
                f"the graph's `{label}` paged read failed at cursor {cursor} "
                f"({type(exc).__name__}: {exc}) — the population is incomplete, so "
                "an absence is NOT established") from exc
        if not page:
            break
        last: int | None = None
        for row in page:
            try:
                node_id, node_key = int(row[0]), row[1]
            except (IndexError, TypeError, ValueError) as exc:
                raise CensusError(
                    f"the graph's `{label}` page returned a row that is not "
                    f"`(id, key)` ({row!r}) — the shape this census compares on has "
                    "moved, so no label is certified") from exc
            last = node_id
            # ⛔ An UNKEYED vector-bearing node is not "absent from the record" —
            # it is UNMATCHABLE. Counted separately AND into graph_only by the
            # caller, because a strip that skipped it would delete the only copy
            # while reporting a clean parity table.
            if not isinstance(node_key, str) or not node_key:
                unkeyed += 1
                continue
            keys.append(node_key)
        if last is None:  # pragma: no cover - a page exists, so it has rows
            break
        cursor = last + 1

    if len(keys) + unkeyed < total:
        raise CensusError(
            f"the graph's `{label}` paged read saw {len(keys) + unkeyed} of {total} "
            "vector-bearing nodes — a SHORT read, which FalkorDB produces SILENTLY "
            "when a result set exceeds RESULTSET_SIZE. A truncated population would "
            "report graph-only nodes as absent from the graph and licence their "
            "strip, so nothing is certified")
    return {"count": total, "keys": keys, "unkeyed": unkeyed}


#: The record leg for one table. `node_key` is the record's one identity column
#: (`NodeSnapshot`'s key, and the column the `chk_*_node_key_is_contract_key`
#: constraints pin), so one statement serves all four labels.
_RECORD_SQL = (
    "SELECT node_key, (embedding_vec IS NOT NULL) AS readable, "
    "       (embedding IS NOT NULL) AS has_payload "
    "FROM public.{table} WHERE org_id = %s AND graph_id = %s"
)

#: The columns the record read depends on, asserted from the cursor's own description
#: so a renamed/dropped column is a REFUSAL rather than an empty comparison that
#: certifies parity vacuously (the `#10024` shape arriving through the schema).
_RECORD_COLUMNS = ("node_key", "readable", "has_payload")


def read_record_vector_keys(record_dsn: str, org_id: str, graph_id: str,
                           table: str) -> dict:
    """The record leg for ONE label: keyed by `node_key`, read under tenant scope.

    The connection goes through the repo's own doors — `record_serve.open_record`
    (bounded, `statement_timeout`) then `record_projection.scope_connection` (the R6
    tenant prologue that verifies the GUC stuck and REFUSES a role that can bypass
    RLS). An unscoped read returns ZERO rows and would certify vacuously; this leg
    cannot take that shape, because the scope either holds or the census exits 3.
    """
    try:
        import psycopg2  # noqa: F401 - availability probe, the record's own driver
    except ImportError as exc:
        raise CensusError(
            f"psycopg2 is not installed ({exc}) — install the 'postgres' extra; the "
            "record cannot be read, so an absence is NOT established") from exc
    try:
        from tortoise import record_projection, record_serve
    except Exception as exc:
        raise CensusError(
            f"the record's modules could not be imported ({type(exc).__name__}: "
            f"{exc}) — the record cannot be read, so an absence is NOT established"
        ) from exc
    try:
        conn = record_serve.open_record(record_dsn)
    except Exception as exc:
        raise CensusError(
            f"the record could not be opened ({type(exc).__name__}: {exc}) — an "
            "absence is NOT established") from exc
    try:
        cursor = record_projection.scope_connection(conn, org_id)
        cursor.execute(_RECORD_SQL.format(table=_quote_record_table(table)),
                       (org_id, graph_id))
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    except CensusError:
        raise
    except Exception as exc:
        raise CensusError(
            f"`public.{table}` could not be read ({type(exc).__name__}: {exc}) — an "
            "absence is NOT established") from exc
    finally:
        with contextlib.suppress(Exception):  # best-effort close
            conn.close()

    missing = [c for c in _RECORD_COLUMNS if c not in columns]
    if missing:
        raise CensusError(
            f"the `public.{table}` read did not return {', '.join(missing)} — the "
            "record schema this census depends on has moved, so an absence is NOT "
            f"established (got: {', '.join(str(c) for c in columns)})")

    readable: set[str] = set()
    unreadable = 0
    for row in rows:
        rec = dict(zip(columns, row, strict=False))
        key = rec.get("node_key")
        if not isinstance(key, str) or not key:
            # A row with no identity cannot be joined to the graph, so it can never
            # be the copy a graph node is compared against. Counted, not skipped.
            continue
        if rec.get("readable"):
            readable.add(key)
        elif rec.get("has_payload"):
            unreadable += 1
    return {"keys": readable, "unreadable": unreadable, "rows": len(rows)}


def _quote_record_table(table: str) -> str:
    """``table`` as a Postgres identifier, refused unless it is one of the four.

    The table name is interpolated into SQL, so it is validated against the closed
    population the module declares rather than escaped: an allowlist is what
    `tortoise/security.py` answers the same shape with, and the population is already
    the module's own constant.
    """
    allowed = {t for t, _, _ in RETIRED}
    if table not in allowed:
        raise CensusError(
            f"{table!r} is not one of this census's record tables ({sorted(allowed)}) "
            "— refusing rather than interpolating an unvetted identifier into SQL")
    return table


def census(*, g: Any, record_dsn: str, org_id: str, graph_id: str,
           labels: list[str] | None = None,
           strip_readiness: Any = None) -> dict:
    """Both legs, per label, joined on the node's identity.

    ``g`` is the bound graph handle (so a destructive caller compares the store it is
    about to write to, rather than a second connection it hopes addresses the same
    one); ``strip_readiness`` is an optional ``label -> (ready, reason)`` callable the
    STRIPPER supplies so its write-gate precondition is recorded in the very plan a
    human approves. The census itself never consults it for a verdict.
    """
    wanted = [row for row in RETIRED
              if labels is None or row[1] in set(labels)]
    if labels is not None and len(wanted) != len(set(labels)):
        raise CensusError(
            f"unknown --label value(s): {sorted(set(labels) - {r[1] for r in RETIRED})} "
            f"— this census covers {[r[1] for r in RETIRED]}")
    rows: list[LabelCensus] = []
    for table, label, key_prop in wanted:
        graph = read_graph_vector_keys(g, label, key_prop)
        record = read_record_vector_keys(record_dsn, org_id, graph_id, table)
        row = LabelCensus(
            label=label,
            table=table,
            entity_types=affected_entity_types(label),
            graph_vectors=graph["count"],
            record_vectors=len(record["keys"]),
            unkeyed_graph_nodes=graph["unkeyed"],
            record_unreadable_vector=record["unreadable"],
            record_only=len(record["keys"] - set(graph["keys"])),
        )
        keyed_graph_only = sorted(set(graph["keys"]) - record["keys"])
        row.graph_only = len(keyed_graph_only) + graph["unkeyed"]
        row.graph_only_sample = keyed_graph_only[:5]
        if strip_readiness is not None:
            ready, reason = strip_readiness(label)
            row.blocked_reason = None if ready else reason
        if not row.at_parity and row.blocked_reason is None:
            row.blocked_reason = (
                f"{row.graph_only} graph node(s) carry a vector the record cannot "
                "answer with (graph-only) — the graph is the ONLY readable copy for "
                "them. Backfill the record (#13430's repair path), never a delete")
        rows.append(row)
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "labels": [r.as_dict() for r in rows],
        "ready": all(r.at_parity and r.blocked_reason is None for r in rows),
        "population": [r[1] for r in RETIRED],
        "graph_page_size": GRAPH_PAGE_SIZE,
    }


def render(report: dict, graph_name: str) -> str:
    """The human table. Every column printed for every label, zeros included."""
    lines = [
        f"entity-vector parity census — graph {graph_name!r} at {report['at']}",
        "",
        f"{'label':10} {'graph':>9} {'record':>9} {'graph_only':>11} "
        f"{'unkeyed':>8} {'rec_unread':>11} {'rec_only':>9}  verdict",
    ]
    for row in report["labels"]:
        lines.append(
            f"{row['label']:10} {row['graph_vectors']:>9} {row['record_vectors']:>9} "
            f"{row['graph_only']:>11} {row['unkeyed_graph_nodes']:>8} "
            f"{row['record_unreadable_vector']:>11} {row['record_only']:>9}  "
            f"{row['verdict']}")
    lines.append("")
    for row in report["labels"]:
        if row["blocked_reason"]:
            lines.append(f"⛔ {row['label']}: {row['blocked_reason']}")
        if row["graph_only_sample"]:
            lines.append(
                f"   {row['label']} graph-only sample: "
                f"{', '.join(row['graph_only_sample'])}")
    lines.append("")
    lines.append(
        "VERDICT: PARITY on every label — the record holds a readable vector for "
        "every graph node's vector" if report["ready"] else
        "VERDICT: NOT AT PARITY — do NOT strip. A graph-only vector is the only "
        "readable copy of that fingerprint; the answer is a backfill, never a delete")
    lines.append(
        "(`record_only` is informational: the record being AHEAD is the direction the "
        "cutover wants and blocks nothing.)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the target graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph NAME to census (a target selector on BOTH "
                             "legs' reach; never scopes the record read — that is "
                             "--org-id/--graph-id)")
    parser.add_argument("--record-dsn", default=None,
                        help="hosted record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get("TORTOISE_RECORD_ORG_ID", ""),
                        help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get("TORTOISE_RECORD_GRAPH_ID", ""),
                        help="record tenant graph")
    parser.add_argument("--label", action="append", default=None,
                        choices=[label for _, label, _ in RETIRED],
                        help="census only this label (repeatable); default: all four")
    parser.add_argument("--json", default=None, help="write the full report here")
    parser.add_argument("--require-parity", action="store_true",
                        help="exit 4 when any label is not at parity, so a caller "
                             "gates on the verdict instead of parsing text")
    args = parser.parse_args(argv)

    empty_flag = census_mod.empty_graph_target_flag(args)
    if empty_flag:
        sys.stderr.write(f"entity_vector_parity_census: {empty_flag}\n")
        return 1
    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        sys.stderr.write(
            "entity_vector_parity_census: no record leg — pass --record-dsn or set "
            "TORTOISE_RECORD_DSN. One store alone cannot establish parity, and a "
            "census that cannot be run is a REFUSAL, not a pass. Nothing was read "
            "from the graph either (the graph leg is not taken without its "
            "counterpart).\n")
        return 3
    if not args.org_id or not args.graph_id:
        sys.stderr.write(
            "entity_vector_parity_census: the record tenant is unnamed — pass "
            "--org-id and --graph-id (or TORTOISE_RECORD_ORG_ID / "
            "TORTOISE_RECORD_GRAPH_ID). An UNTENANTED record read returns ZERO rows "
            "under RLS, which would certify parity vacuously (#10024).\n")
        return 3

    from tortoise.projection import FalkorProjection

    try:
        graph_name = census_mod.resolve_graph_name(
            args.uri, args.db_path, args.graph_name)
        if args.uri:
            proj = FalkorProjection.from_uri(
                args.uri, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        else:
            proj = FalkorProjection(
                args.db_path, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        census_mod._refuse_unless_graph_present(proj, graph_name)
        report = census(
            g=proj.g, record_dsn=record_dsn,
            org_id=args.org_id, graph_id=args.graph_id, labels=args.label)
        report["graph"] = graph_name
        report["graph_uri"] = args.uri or args.db_path
    except CensusError as exc:
        sys.stderr.write(f"entity_vector_parity_census: REFUSED — {exc}\n")
        return 3
    except Exception as exc:  # an unreadable side is a refusal
        sys.stderr.write(
            f"entity_vector_parity_census: REFUSED — the run could not complete "
            f"({type(exc).__name__}: {exc}). A census that cannot be run is a "
            "refusal, not a pass.\n")
        return 3

    print(render(report, graph_name))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nreport written to {args.json}")
    if args.require_parity and not report["ready"]:
        return 4
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
