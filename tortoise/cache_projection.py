"""Interface B — the cache projection: the record's STATE, loaded to hot, one way.

WHY THIS MODULE EXISTS
======================
``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record** and **FalkorDB a derived hot cache**, and the
owner's words are *"data goes to postgres and it's loaded to hot (falkor)"*.

``tortoise/projection/`` already is a replay — of the **journal**, into
FalkorDB. That is history's fold, and it has two consequences this module
exists to fix:

1. **A rebuild could only read history.** ``FalkorProjection.rebuild_all``
   takes a ``log_dir`` and re-reads the JSONL. So *"Postgres is the record and
   the cache is loaded from it"* had **no execution path**, and the ruling's
   second half had no carrier (#7971; #7967 S3's own failure mode — *"a rebuild
   that can only read the journal rather than the record"*).
2. **The fold's output is not confined to the record.** ``apply()`` writes the
   ``content_hash``/``confidence``/embedding it recomputes — names the schema
   **refuses as authority** (#7968's ``chk_*_props_no_derived``, the EP
   carve-out). A cache built that way carries fields with **no PRIMARY claim in
   the record**, which is exactly what S3's contract clause forbids.

So this module projects the record's **state** — the layer tables ``#7968``
built — and confines its output to the record's PRIMARY field set.

THE FOUR PROPERTIES, AND WHERE EACH LIVES
=========================================
1. **One-way by construction.** Nothing here reads the cache to decide what to
   project. The plan is built entirely from the record; the only read it
   contains is the endpoint ``MATCH`` inside a relation upsert, which resolves
   a **record** coordinate. ⛔ That is why the five graph-only classes
   ``rebuild_all`` currently rescues from the cache (``_SNAPSHOT_SECTIONS``:
   ``#548`` synthetic Points, ``#990`` ``:Batch`` markers, ``#2814`` config,
   ``#4641`` onboarding state, ``#4653`` ``:GraphEventMeta``) are **not carried
   here**. The issue's own words forbid exactly that — *"the cache is a
   destination, never an input"* — and the placement ruling says what they are:
   *"a field with no PRIMARY claim in the record that exists only here is a
   defect, not a shortcut."* A record-sourced rebuild **drops** them, and the
   drop is **named**, not silent — the five classes are listed right here and in
   ``06-cache-projection.md`` §0.5. It is deliberately **not counted at
   runtime**: counting them would require the pre-wipe cache read this path
   forbids, and `rebuild_from_record`'s report is about the RECORD's coverage,
   not a census of what the wipe destroyed.
2. **The cache is never authoritative.** A property is projected **iff** the
   schema declares a column for it and does not refuse it. The property→column
   decision is **read from disk** — the schema (#7968) and the placement table
   (#7955) — never restated here, so a new column is a loud failure rather than
   a silent drop.
3. **Rebuildable from the record alone.** The plan is a pure function of the
   record view: same view ⇒ same statements (idempotent), and the written field
   set equals the record's field set exactly (never a superset).
4. **A cold region reads ``not-hot``, never ``empty``.** ``declaration_for``
   writes the known-hot set **after** the writes and as a function of what was
   materialized, so a record key the projection did not write is not claimed
   hot; with that declaration in force ``residency.apply_residency`` turns an
   ``empty`` read over it into ``degraded`` — never the ``empty`` that is
   indistinguishable from *"no such node"* (#7952, §17.2/§17.3).

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the write chokepoint.** §2.3/Interface C is ``#7951``; this is the
  third step of that path and routes no live writes.
* **Not the read path** (``#7970``, deferred by §2.0) nor **the residency
  router** (``#7953``).
* **Not a second declaration of the placement.** The table is parsed, and
  ``tests/test_cache_projection.py`` pins the parse against the schema on disk.
* **No migration-safety apparatus.** Owner scope ruling 2026-10-10: the only
  customer is the dogfood instance. No dual-write window, no backfill, no
  cutover, no rollback-parity surface appears here.
* **No public surface change.** ``FalkorProjection`` is internal; no MCP tool
  and no public ``TortoiseSDK`` method is added, removed or renamed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import record_projection as _rp
from .residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    ResidencyDeclaration,
    declare_residency,
)
from .security import validate_rel_type

__all__ = [
    "ADDITIONAL_PRIMARY_COLUMNS",
    "KEY_PROPERTY",
    "NODE_TABLES",
    "RELATION_TABLE",
    "AmbiguousRecordColumn",
    "CacheProjectionError",
    "ProjectionPlan",
    "ProjectionReport",
    "RecordNode",
    "RecordRelation",
    "RecordView",
    "Statement",
    "UnknownRecordColumn",
    "UnknownRecordLabel",
    "UnprojectableRecord",
    "ambiguities",
    "bookkeeping_columns",
    "canonical_property",
    "declaration_for",
    "declaration_is_sound",
    "declared_key",
    "node_from_row",
    "node_statement",
    "placement_columns",
    "placement_spellings",
    "project_records",
    "projected_columns",
    "projected_properties",
    "read_record_view",
    "record_plan",
    "relation_from_row",
    "relation_statement",
]

#: label → record table (the layer tables `#7968` built).
NODE_TABLES: dict[str, str] = {
    "Point": "point",
    "Event": "event",
    "Subject": "subject",
    "Object": "object",
    "Source": "source",
    "Session": "session",
}

#: The relations table — every edge is a row (ONTOLOGY §8 reification).
RELATION_TABLE = "relation"

#: The record's own coordinates: supplied by the schema/projection, never a node
#: property. Kept in step with `record_projection._BOOKKEEPING_COLUMNS` by the
#: suite's unclassified-column pin.
RECORD_BOOKKEEPING_COLUMNS: frozenset[str] = frozenset({
    "org_id", "graph_id", "props", "node_label", "node_key",
    "rel_type", "src_label", "src_key", "tgt_label", "tgt_key",
})

#: Coordinates that are bookkeeping on ONE table only. `public.relation.id` is
#: the row's own identity column (`bigint GENERATED ALWAYS AS IDENTITY`), not an
#: edge property — while on `public.point`/`public.event`, `id` IS a property
#: and the Event's key lives in `event_id`.
_TABLE_BOOKKEEPING: dict[str, frozenset[str]] = {
    "relation": frozenset({"id"}),
}


def bookkeeping_columns(table: str) -> frozenset[str]:
    """The columns of `table` that are coordinates, never properties."""
    return RECORD_BOOKKEEPING_COLUMNS | _TABLE_BOOKKEEPING.get(table, frozenset())

#: Columns the placement table does not spell and the schema does not refuse.
#: Four, and each is named with its reason — because the alternative is a
#: SILENT DROP, which is the failure Interface A/B both forbid.
#:
#: * `point.baseline_alpha` / `point.baseline_beta` — §2.8's narrowed carve-out
#:   exception: the author-set prior and the α/β it names are a
#:   NON-RECONSTRUCTIBLE input, therefore PRIMARY. The table spells only
#:   `baseline_source` (§2.7), and the schema adds the pair it governs.
#: * `subject.superseded_by` / `subject.superseded_at` — the Object row's
#:   lifecycle fields (§2.6), which `public.subject` carries too. The table
#:   spells them on Object only; the graph property names are Object's
#:   (`supersededBy`/`supersededAt`), which is what the fold writes.
ADDITIONAL_PRIMARY_COLUMNS: dict[str, dict[str, str]] = {
    "point": {"baseline_alpha": "baseline_alpha", "baseline_beta": "baseline_beta"},
    "subject": {"superseded_by": "supersededBy", "superseded_at": "supersededAt"},
}

#: The placement table (#7955) — the field contract the schema is pinned to.
_PLACEMENT_DOC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "02-identity-and-placement.md"
)

#: section heading in the placement table → the physical table.
_PLACEMENT_SECTION_TABLE: dict[str, str] = {
    "Event": "event",
    "Source": "source",
    "Session": "session",
    "Object": "object",
    "Subject": "subject",
    "Point": "point",
    "Edges": "relation",
}

_SECTION_RE = re.compile(r"^## (Event|Source|Session|Object|Subject|Point) \(node\)")
_EDGES_RE = re.compile(r"^## Edges / operators")
_HEADING_RE = re.compile(r"^#{2,3} ")
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([PDU])\s*\|")

#: `public.node_identity`'s generated key per label — parsed, not restated.
_KEY_CASE_RE = re.compile(r"WHEN\s+'(\w+)'\s+THEN\s+'([^']+)'")
_KEY_DDL = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / (
    "20261012000001_record_event_stream.sql")


class CacheProjectionError(RuntimeError):
    """Base class for every refusal on this path."""


class UnknownRecordLabel(CacheProjectionError):
    """A label with no record table — the record cannot carry it."""


class UnknownRecordColumn(CacheProjectionError):
    """A record column the projection has not classified.

    Raised rather than dropped: a column nobody placed in PRIMARY, DERIVED or
    bookkeeping is a schema change the cache projection has not been taught,
    and silently skipping it is the "cache poorer than the record" failure.
    """


class AmbiguousRecordColumn(CacheProjectionError):
    """Two placement spellings collapse to one column — refusals are explicit."""


class UnprojectableRecord(CacheProjectionError):
    """A record row that cannot be projected (a missing contract key)."""


# ════════════════════════════════════════════════════════════════════════════
# The read shape's declarations, read from disk
# ════════════════════════════════════════════════════════════════════════════


def _read_key_property() -> dict[str, str]:
    """`{label: key property}` from `public.node_identity`'s generated CASE.

    Read, never restated: a store that disagrees with the W1 contract cannot
    write the row at all, so the DDL is the contract's one home (#7955).
    """
    try:
        sql = _KEY_DDL.read_text(encoding="utf-8")
    except OSError as e:  # pragma: no cover - deployment guard
        raise CacheProjectionError(
            f"the record schema is not on disk at {_KEY_DDL} — the cache "
            f"projection reads the key contract from it: {e}"
        ) from e
    block = sql.split("CREATE TABLE IF NOT EXISTS public.node_identity", 1)
    if len(block) != 2:  # pragma: no cover - deployment guard
        raise CacheProjectionError(
            "public.node_identity is not in the record migration — the cache "
            "projection's key contract has no home"
        )
    found = dict(_KEY_CASE_RE.findall(block[1].split(");", 1)[0]))
    missing = [label for label in NODE_TABLES if label not in found]
    if missing:  # pragma: no cover - deployment guard
        raise CacheProjectionError(
            f"public.node_identity declares no key_property for {missing}"
        )
    return found


#: `{label: the property that is the label's canonical key}` (Interface E).
KEY_PROPERTY: dict[str, str] = _read_key_property()


def _read_placement_rows(doc: Path | None = None) -> list[tuple[str, str]]:
    """`(table, property)` for every row the placement table declares.

    EVERY class is read (`P`, `D`, `U`) — the class decides whether a field is
    *authoritative*, and the schema decides that (#7968's pin), while the NAME
    is needed here to spell a column back into the graph's namespace. Reading
    the `D`/`U` rows too is what lets the suite assert that a DERIVED column is
    never projected: the name must be known to be refused.
    """
    path = doc or _PLACEMENT_DOC
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CacheProjectionError(
            f"the placement table is not on disk at {path} — the cache "
            f"projection reads the property namespace from it: {e}"
        ) from e
    rows: list[tuple[str, str]] = []
    section: str | None = None
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if _EDGES_RE.match(line):
            section = "Edges"
            continue
        if _HEADING_RE.match(line):
            section = None
            continue
        if section is None:
            continue
        r = _ROW_RE.match(line)
        if not r:
            continue
        prop = r.group(1)
        if prop.endswith(".*"):
            # A grouped PREDICATE row (`mitigated_by.*`) names edge predicates,
            # not properties; the edge's own properties are spelled by the
            # `IMPL.` / `NAND.` rows.
            continue
        if "." in prop:
            prop = prop.partition(".")[2]
            for part in prop.split("/"):
                rows.append((_PLACEMENT_SECTION_TABLE[section], part.strip()))
        else:
            rows.append((_PLACEMENT_SECTION_TABLE[section], prop))
    for table, extra in ADDITIONAL_PRIMARY_COLUMNS.items():
        rows.extend((table, prop) for prop in extra.values())
    return rows


#: NOTE on caching: the parsers below read FILES, and every accessor is memoized.
#: That is not premature — `_props_from_row` runs PER RECORD ROW, so an uncached
#: parse would read the placement doc once per row (a 45k-node rebuild: 45k file
#: reads). The cache is keyed on the path, so a test pointing at a fixture doc
#: gets its own entry.
@lru_cache(maxsize=8)
def placement_spellings(doc: Path | None = None) -> dict[str, dict[str, tuple[str, ...]]]:
    """`{table: {column: (spelling, ...)}}`, sorted — the raw namespace.

    ⚠️ **More than one spelling can collapse to one column, and on this schema
    five DO** — FOUR on `public.source` (`session_id`, `event_id`, `source_path`,
    `document_kind`) and ONE on `public.event` (`session_id`). Each appears in
    the placement table BOTH snake- and camel-cased, because the graph's
    document writer emits the snake forms while the session-capture writer mints
    the camel forms (the declaration `projection/entities.py::_SOURCE_NODE_PROPS`
    says so in its own comment). The record's `snake()` naming is therefore
    **not injective** over the graph's namespace: a single record column cannot
    distinguish the two,
    and `canonical_property` chooses deterministically. Reported, not hidden —
    a record-sourced cache cannot be byte-identical to a journal-sourced one
    for those five fields until one spelling wins (#7955/#7968).
    """
    out: dict[str, dict[str, list[str]]] = {}
    for table, prop in _read_placement_rows(doc):
        out.setdefault(table, {}).setdefault(_rp.snake(prop), []).append(prop)
    return {t: {c: tuple(sorted(set(v))) for c, v in cols.items()}
            for t, cols in out.items()}


def _prefer(spellings: Sequence[str]) -> str:
    """The canonical spelling for one column — deterministic, and pinned.

    camelCase wins over the bare column name when both exist: the graph's
    property namespace is camelCase-first (the fold's own clauses write
    `pointKind`, `contentHash`, `canonicalUrl`, …), and the snake duplicates in
    the table are the document writer's raw payload leaking through a passthrough.
    A snake-only column keeps its own name (`is_episodic`, `subject_kind`, …).
    """
    camel = [s for s in spellings if s != _rp.snake(s)]
    return camel[0] if camel else spellings[0]


@lru_cache(maxsize=8)
def placement_columns(doc: Path | None = None) -> dict[str, dict[str, str]]:
    """`{table: {column: the canonical graph property name}}`."""
    return {t: {c: _prefer(v) for c, v in cols.items()}
            for t, cols in placement_spellings(doc).items()}


def canonical_property(table: str, column: str) -> str:
    """The graph property name a record column projects to."""
    cols = placement_columns().get(table, {})
    if column in cols:
        return cols[column]
    extra = ADDITIONAL_PRIMARY_COLUMNS.get(table, {})
    if column in extra:
        return extra[column]
    if column in RECORD_BOOKKEEPING_COLUMNS:
        return column
    raise UnknownRecordColumn(
        f"{table}.{column} is neither a placed property nor record bookkeeping "
        f"— the cache projection has not been taught this column"
    )


@lru_cache(maxsize=32)
def projected_columns(table: str) -> frozenset[str]:
    """The record columns that carry AUTHORITY — i.e. that get projected.

    The set is decided by the SCHEMA, not by the placement table's class column:
    a column exists **iff** the placement table demands one (#7968's two-way
    pin), and a name the schema REFUSES as a `props` key is DERIVED/VOLATILE by
    that refusal. So:

        projected = schema columns  −  refused  −  bookkeeping

    with the placement table supplying only the SPELLING. That is what makes
    the eight `U` rows resolve correctly without a second copy of §2.7: the
    five that become PRIMARY have a column and are not refused (`updatedAt`,
    `baseline_source`, `temporal_reviewed`, `IMPL.confidence`, `IMPL.weight`),
    and the three that become DERIVED are refused by name (`version`,
    `inherited_at`, `baseline_set` — all in `chk_point_props_no_derived`).
    """
    from .record_projection import table_columns

    columns = table_columns().get(table, frozenset())
    refused = _refused(table)
    placed = placement_columns().get(table, {})
    extra = ADDITIONAL_PRIMARY_COLUMNS.get(table, {})
    return frozenset(
        c for c in columns
        if c not in bookkeeping_columns(table) and c not in refused
        and (c in placed or c in extra)
    )


def projected_properties(table: str) -> frozenset[str]:
    """The graph property names the projection writes for one table."""
    return frozenset(canonical_property(table, c) for c in projected_columns(table))


def ambiguities() -> dict[str, tuple[str, ...]]:
    """Columns the placement table spells more than one way — sorted columns."""
    return {
        f"{table}.{column}": spellings
        for table, cols in placement_spellings().items()
        for column, spellings in cols.items()
        if len(spellings) > 1
    }


@lru_cache(maxsize=32)
def _refused(table: str) -> frozenset[str]:
    """The names the schema (or the projection) refuses as a `props` key."""
    from .record_projection import refused_props_names

    return frozenset(refused_props_names().get(table, ()))


# ════════════════════════════════════════════════════════════════════════════
# The record read shape
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RecordNode:
    """One record row, as the cache must materialize it."""

    label: str
    key: str
    props: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordRelation:
    """One `public.relation` row, as an edge.

    Endpoints are addressed by `(label, key)` — Interface E's contract — never
    by a graph-resident id (`#2489`/`#5086`).
    """

    rel_type: str
    src_label: str
    src_key: str
    tgt_label: str
    tgt_key: str
    idx: int = 0
    props: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordView:
    """The record, read: the cache projection's ONLY input."""

    nodes: tuple[RecordNode, ...] = ()
    relations: tuple[RecordRelation, ...] = ()

    @property
    def record_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((n.label, n.key) for n in self.nodes)


@dataclass(frozen=True)
class Statement:
    """One parameterized Cypher statement."""

    cypher: str
    params: Mapping[str, Any]


def _scalar(value: Any) -> Any:
    """A value as the graph stores it. `jsonb` reads back as list/dict already."""
    if isinstance(value, (list, dict)):
        return value
    return value


def _props_from_row(table: str, row: Mapping[str, Any], *,
                    skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Project a record row's columns + `props` tail into graph properties.

    Three destinations, and no fourth:

    * a column the placement table spells → that property, under its name;
    * a column the schema REFUSES (the EP carve-out, the projection caches) →
      dropped, by name — never stored as authority (S3's contract clause);
    * the `props` tail → verbatim, unmapped (the open-set surface);
    * anything else → :class:`UnknownRecordColumn`, loudly.
    """
    placed = placement_columns().get(table, {})
    refused = _refused(table)
    projected = projected_columns(table)
    out: dict[str, Any] = {}
    for column, value in row.items():
        if column == "props":
            tail = value
            if isinstance(tail, str):
                tail = json.loads(tail) if tail else {}
            if tail:
                refused_tail = sorted(set(tail) & refused)
                if refused_tail:
                    raise CacheProjectionError(
                        f"{table}.props carries DERIVED/VOLATILE name(s) "
                        f"{refused_tail} — the record must not hold them as "
                        f"authority, so the cache must not either (the "
                        f"schema's own CHECK refuses this row)"
                    )
                out.update(dict(tail))
            continue
        if column in bookkeeping_columns(table) or column in skip:
            continue
        if column in refused:
            # The EP carve-out and the projection-owned caches. Dropped BY NAME
            # rather than by the CHECK's later refusal (S3's contract clause).
            continue
        if column in projected:
            if value is not None:
                out[canonical_property(table, column)] = _scalar(value)
            continue
        if column in placed:
            # A placed column the schema does NOT authorise: DERIVED/VOLATILE.
            continue
        raise UnknownRecordColumn(
            f"{table}.{column} is not classified by the placement table, not "
            f"refused by the schema, and not record bookkeeping — refusing to "
            f"drop it silently"
        )
    return out


def node_from_row(label: str, row: Mapping[str, Any]) -> RecordNode:
    """One record row → one `RecordNode`, keyed by the label's contract key."""
    table = NODE_TABLES.get(label)
    if table is None:
        raise UnknownRecordLabel(f"{label!r} has no record table")
    key_prop = KEY_PROPERTY[label]
    key_column = _rp.snake(key_prop)
    raw = row.get(key_column)
    if raw is None or not isinstance(raw, str) or not raw:
        raise UnprojectableRecord(
            f"{label}: no contract key at {table}.{key_column} — the schema's "
            f"own CHECK would refuse the row, and a NULL key cannot be merged"
        )
    props = _props_from_row(table, row)
    # The key column is a graph property too (`id`, `name`, `url`, `eventId`),
    # so it is projected under its own name as well as being the MERGE key.
    props.setdefault(key_prop, raw)
    return RecordNode(label=label, key=raw, props=props)


def relation_from_row(row: Mapping[str, Any]) -> RecordRelation:
    """One `public.relation` row → one `RecordRelation`."""
    rel_type = row.get("rel_type")
    if not isinstance(rel_type, str) or not rel_type:
        raise UnprojectableRecord("relation: no rel_type (the edge predicate)")
    src_label = row.get("src_label")
    tgt_label = row.get("tgt_label")
    if src_label not in NODE_TABLES or tgt_label not in NODE_TABLES:
        raise UnprojectableRecord(
            f"relation: endpoint labels {src_label!r}/{tgt_label!r} are not "
            f"record labels {sorted(NODE_TABLES)}"
        )
    src_key, tgt_key = row.get("src_key"), row.get("tgt_key")
    if not src_key or not tgt_key:
        raise UnprojectableRecord("relation: an endpoint key is empty")
    idx_raw = row.get("idx") or 0
    try:
        idx = int(idx_raw)
    except (TypeError, ValueError) as e:
        raise UnprojectableRecord(f"relation: idx {idx_raw!r} is not an int") from e
    props = _props_from_row(
        RELATION_TABLE, row,
        skip=frozenset({"rel_type", "src_label", "src_key", "tgt_label",
                        "tgt_key", "idx"}),
    )
    return RecordRelation(rel_type=rel_type, src_label=src_label, src_key=src_key,
                          tgt_label=tgt_label, tgt_key=tgt_key, idx=idx,
                          props=props)


def read_record_view(conn: Any, *, org_id: str, graph_id: str,
                     labels: Iterable[str] = tuple(NODE_TABLES)) -> RecordView:
    """Read the record's node and relation state for ONE scope.

    The scope predicate is not optional: a reader that forgot it would project
    every tenant into this graph. Rows come back `dict`-shaped (a `RealDict`
    cursor, or a plain cursor's `description`).
    """
    nodes: list[RecordNode] = []
    relations: list[RecordRelation] = []
    cur = conn.cursor()
    for label in labels:
        table = NODE_TABLES[label]
        for row in _rows(cur, f"SELECT * FROM public.{table} "
                              "WHERE org_id = %s AND graph_id = %s",
                         (org_id, graph_id)):
            nodes.append(node_from_row(label, row))
    for row in _rows(cur, f"SELECT * FROM public.{RELATION_TABLE} "
                          "WHERE org_id = %s AND graph_id = %s",
                     (org_id, graph_id)):
        relations.append(relation_from_row(row))
    return RecordView(nodes=tuple(nodes), relations=tuple(relations))


def _rows(cur: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    fetched = cur.fetchall()
    if not fetched:
        return []
    if isinstance(fetched[0], Mapping):
        return fetched
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in fetched]


# ════════════════════════════════════════════════════════════════════════════
# The materialization
# ════════════════════════════════════════════════════════════════════════════


def node_statement(node: RecordNode) -> Statement:
    """Materialize ONE node — `MERGE` by the contract key, `SET` the record.

    The embedding is written as its own clause because the graph stores a
    VECTOR (`vecf32`) while a caller-owned one is stored verbatim
    (`Point.embedding_verbatim`, both PRIMARY `#5004`).
    """
    key_prop = KEY_PROPERTY[node.label]
    props = dict(node.props)
    embedding = props.pop("embedding", None)
    verbatim = bool(props.pop("embedding_verbatim", False))
    cypher = f"MERGE (n:{node.label} {{{key_prop}:$key}}) SET n += $props"
    params: dict[str, Any] = {"key": node.key, "props": props}
    if embedding is not None:
        form = "$embedding" if verbatim else "vecf32($embedding)"
        cypher += f", n.embedding = {form}"
        params["embedding"] = embedding
    if verbatim:
        # The marker is PRIMARY and the graph writes it as its own clause
        # (`projection/entities.py`), so it is carried in BOTH branches — with
        # and without a vector. Dropping it here would make a record-sourced
        # cache POORER than the record, and the marker is the authority on the
        # storage FORM: a caller-owned float64 vector would lose its flag and
        # the next journaled re-emit would narrow it through `vecf32` (#5004).
        props["embedding_verbatim"] = True
    return Statement(cypher=cypher, params=params)


def relation_statement(rel: RecordRelation) -> Statement:
    """Materialize ONE edge, resolving both endpoints by `(label, key)`.

    The predicate is interpolated into the query STRUCTURE
    (`MERGE (src)-[r:TYPE]->(tgt)`), where parameterization is impossible — so
    it is allowlisted exactly as every other interpolation site in the tree
    (`projection/__init__.py`, `sdk.py`, `mcp_server.py`) does. `record_plan`
    builds this before the wipe, so a non-allowlisted predicate refuses the
    whole projection up front rather than raising mid-materialization.
    """
    try:
        validate_rel_type(rel.rel_type)
    except ValueError as e:
        raise UnprojectableRecord(
            f"relation: rel_type {rel.rel_type!r} is not an allowlisted edge "
            f"predicate — refusing to interpolate an unvalidated identifier "
            f"into Cypher"
        ) from e
    src_kp = KEY_PROPERTY[rel.src_label]
    tgt_kp = KEY_PROPERTY[rel.tgt_label]
    cypher = (
        f"MATCH (src:{rel.src_label} {{{src_kp}:$src_key}}), "
        f"(tgt:{rel.tgt_label} {{{tgt_kp}:$tgt_key}}) "
        f"MERGE (src)-[r:{rel.rel_type} {{idx:$idx}}]->(tgt) SET r += $props"
    )
    return Statement(cypher=cypher, params={
        "src_key": rel.src_key, "tgt_key": rel.tgt_key, "idx": rel.idx,
        "props": dict(rel.props),
    })


@dataclass(frozen=True)
class ProjectionPlan:
    """The statements a record view projects to — built before anything runs.

    Materializing the whole plan first is what makes "the projection cannot
    half-apply a view it refuses" true: `node_from_row`/`relation_from_row`
    raise while the plan is being built, so a refusal leaves the cache untouched
    (the same shape `record_projection.replay_plan` uses for Interface A).
    """

    statements: tuple[Statement, ...]
    nodes: tuple[RecordNode, ...]
    relations: tuple[RecordRelation, ...]

    def __iter__(self):
        return iter(self.statements)

    def __len__(self) -> int:
        return len(self.statements)

    def node_props(self) -> dict[tuple[str, str], Mapping[str, Any]]:
        """`{(label, key): props}` — the field set the cache would carry."""
        return {(n.label, n.key): n.props for n in self.nodes}

    def node_keys(self) -> set[tuple[str, str]]:
        return {(n.label, n.key) for n in self.nodes}


def record_plan(nodes: Iterable[RecordNode],
                relations: Iterable[RecordRelation] = ()) -> ProjectionPlan:
    """The projection plan for a record view — a PURE function of the record.

    Nodes first, then relations: an edge's endpoints are `MATCH`ed, so a
    relation written before its node would find nothing.
    """
    nodes_t = tuple(nodes)
    rels_t = tuple(relations)
    statements = [node_statement(n) for n in nodes_t]
    statements.extend(relation_statement(r) for r in rels_t)
    return ProjectionPlan(statements=tuple(statements), nodes=nodes_t,
                          relations=rels_t)


@dataclass(frozen=True)
class ProjectionReport:
    """What a projection did — COUNTS, and the declaration it wrote."""

    nodes_written: int = 0
    relations_written: int = 0
    coverage: str = COVERAGE_COMPLETE
    declaration_keys: int = 0
    scope: str = ""

    def render(self) -> str:
        return (
            f"cache projection: {self.nodes_written} node(s), "
            f"{self.relations_written} relation(s) written; coverage="
            f"{self.coverage} over {self.declaration_keys} declared key(s)"
            + (f" (scope {self.scope})" if self.scope else "")
        )


def project_records(g: Any, plan: ProjectionPlan) -> int:
    """Execute a plan against a graph handle. Returns the statements run.

    ⛔ **No read here decides what to project.** Every statement is a `MERGE`
    derived from the record; the `MATCH` inside a relation statement resolves a
    RECORD coordinate, not a cache fact.
    """
    for stmt in plan.statements:
        g.query(stmt.cypher, params=dict(stmt.params))
    return len(plan.statements)


# ════════════════════════════════════════════════════════════════════════════
# The declaration — the known-hot set, derived from what was materialized
# ════════════════════════════════════════════════════════════════════════════


def _declared_key(scope: str, key: str) -> str:
    return f"{scope}:{key}" if scope else key


def declared_key(label: str, key: str, *, scope: str = "") -> str:
    """The declared coordinate for ONE record node — ``label:key``, scoped.

    ⛔ **The LABEL is part of the coordinate, never discarded.** ``KEY_PROPERTY``
    gives ``Subject`` and ``Object`` the same key (``name``) and ``Point`` and
    ``Session`` the same key (``id``), so a bare key string is **not unique
    across labels**: a key-only declaration collapses ``(Object, "acme")`` and
    ``(Subject, "acme")`` into ONE hot key, and a projection that wrote only one
    of them then declares ``complete`` — the fail-OPEN polarity this module
    forbids (a miss on the omitted label stays ``empty`` instead of becoming
    ``not-hot``/``degraded``).

    It is the coordinate a reader must pass to
    ``residency.mark_residency(..., key=...)``, so the writer and the reader
    speak the same vocabulary rather than agreeing only by accident.
    """
    return _declared_key(scope, f"{label}:{key}")


def declaration_for(record_keys: Iterable[tuple[str, str]],
                    materialized_keys: Iterable[tuple[str, str]], *,
                    scope: str = "") -> ResidencyDeclaration:
    """The known-hot set, from the RECORD's keys and what the projection wrote.

    `complete` **iff every record key it read was materialized** — coverage is
    a fact about the projection's own work, never a constant. Otherwise
    `partial`, enumerating exactly the keys that WERE written.

    ⚠️ A `partial` declaration **claims only what it enumerates** (#7952's
    reader), so an unenumerated node is a residency MISS — `not-hot`, never an
    absence. That is the polarity this function exists to feed.
    """
    record = {declared_key(label, key, scope=scope)
              for label, key in record_keys}
    written = {declared_key(label, key, scope=scope)
               for label, key in materialized_keys}
    if record and written >= record:
        return ResidencyDeclaration(coverage=COVERAGE_COMPLETE,
                                    source="cache-projection")
    return ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                hot_keys=frozenset(written),
                                source="cache-projection")


def declaration_is_sound(record_keys: Iterable[tuple[str, str]],
                         materialized_keys: Iterable[tuple[str, str]],
                         declaration: ResidencyDeclaration, *,
                         scope: str = "") -> bool:
    """Does `declaration` claim only what was actually materialized?

    The check that makes "a projection cannot declare a key hot it did not
    write" enforceable rather than aspirational — a `complete` declaration over
    a partial write is the mutation this refuses.
    """
    if declaration.partial:
        return True
    written = {declared_key(label, key, scope=scope)
               for label, key in materialized_keys}
    record = {declared_key(label, key, scope=scope)
              for label, key in record_keys}
    return written >= record


def write_declaration(g: Any, declaration: ResidencyDeclaration) -> None:
    """Write the known-hot set — ONE declaration format (reused from #7952)."""
    declare_residency(g, declaration)
